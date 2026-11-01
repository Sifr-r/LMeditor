"""Tests for the verification throughput work (contract §3).

Verification used to be fully serial: ``CitationChecker.audit_claims`` awaited
the NLI evaluator once per claim inside a ``for`` loop, and
``verify_citations_node`` awaited ``audit_section`` once per section inside a
second one. Verification wall time was therefore the *sum* of every model
inference. The three things fixed here are:

- per-claim and per-section evaluation now run concurrently, with the assembled
  result bit-identical to the serial version;
- the concurrency is capped by ``MAX_CONCURRENT_NLI_EVALUATIONS`` so a long
  document cannot open an unbounded fan-out of model calls;
- ``ResilientNLIEvaluator._cache`` is a bounded LRU rather than an unbounded
  dict on a process-lifetime singleton.

The concurrency proofs deliberately use in-flight counters and events rather
than wall-clock timings: a timing assertion on a loaded CI box is flaky, while
"these N calls were simultaneously inside the evaluator" is a fact.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.graph import END, StateGraph

from lmeditor.core.schemas import (
    Claim,
    SectionDraft,
    SectionStatus,
    SourceChunk,
    VerificationScorecard,
)
from lmeditor.core.types import EntailmentStatus
from lmeditor.verification import citation_checker as checker_module
from lmeditor.verification.citation_checker import (
    MAX_CONCURRENT_NLI_EVALUATIONS,
    CitationChecker,
)
from lmeditor.verification.nli_evaluator import (
    MAX_CACHE_ENTRIES,
    HeuristicNLIEvaluator,
    ResilientNLIEvaluator,
    default_nli_evaluator,
)
from lmeditor.workflow.nodes import verify_citations_node
from lmeditor.workflow.state import HarnessState, create_initial_harness_state

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class InFlightEvaluator:
    """Evaluator that records how many of its calls are inside it at once.

    The verdict is keyed off the *hypothesis* text rather than a call counter,
    so the result of a claim never depends on how many calls happened before it.
    That is what makes the serial-vs-concurrent comparison below meaningful: a
    call-order-dependent fake would make the two runs agree for the wrong reason.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.in_flight = 0
        self.peak_in_flight = 0
        self.calls = 0
        #: Premise text of every call, in completion order.
        self.seen: list[str] = []

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        self.calls += 1
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            self.seen.append(premise)
            # Yield control so concurrent callers actually interleave.
            for _ in range(3):
                await asyncio.sleep(0)
            if self.delay:
                await asyncio.sleep(self.delay)
            return _verdict_for(hypothesis)
        finally:
            self.in_flight -= 1


class BarrierEvaluator:
    """Evaluator that blocks until ``target`` calls are simultaneously inside.

    If evaluation were serial this would block forever, so every test using it
    wraps the call in ``asyncio.wait_for`` and a regression fails as a timeout
    instead of hanging the suite.
    """

    def __init__(self, target: int) -> None:
        self.target = target
        self.in_flight = 0
        self.peak_in_flight = 0
        self.reached = asyncio.Event()

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        if self.in_flight >= self.target:
            self.reached.set()
        try:
            await self.reached.wait()
            return _verdict_for(hypothesis)
        finally:
            self.in_flight -= 1


def _verdict_for(hypothesis: str) -> tuple[EntailmentStatus, float]:
    """Deterministic verdict derived from the claim's own text."""
    if "supported" in hypothesis:
        return EntailmentStatus.ENTAILED, 0.91
    if "neutral" in hypothesis:
        return EntailmentStatus.NEUTRAL, 0.44
    if "contradicted" in hypothesis:
        return EntailmentStatus.CONTRADICTED, 0.77
    return EntailmentStatus.UNGROUNDED, 0.12


#: Cycles the claim texts go through, so a section exercises every counter.
_PATTERNS = ("supported", "neutral", "contradicted", "ungrounded", None)

#: ``SourceChunk.chunk_id`` is constrained to a SHA-256 hex digest, so the
#: citation target has to be one.
_CHUNK_ID = "a" * 64


def _chunk(chunk_id: str = _CHUNK_ID) -> SourceChunk:
    text = f"Grounded passage for {chunk_id}."
    return SourceChunk(
        chunk_id=chunk_id,
        document_id="doc_throughput",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _claims(count: int, section_id: str = "sec_mix") -> list[Claim]:
    """``count`` claims covering every verdict, one in five of them uncited."""
    claims: list[Claim] = []
    for idx in range(count):
        pattern = _PATTERNS[idx % len(_PATTERNS)]
        if pattern is None:
            # No inline citation: always UNGROUNDED, exercises the other branch.
            claims.append(
                Claim(
                    claim_id=f"c{idx}",
                    section_id=section_id,
                    text=f"Uncited assertion number {idx}.",
                    sentence_idx=idx,
                )
            )
            continue
        claims.append(
            Claim(
                claim_id=f"c{idx}",
                section_id=section_id,
                text=f"Assertion number {idx} is {pattern} by the passage.",
                sentence_idx=idx,
                cited_chunk_ids=[_CHUNK_ID],
            )
        )
    return claims


def _serial_gate() -> asyncio.Semaphore:
    """A one-permit stand-in for the real gate, forcing serial evaluation."""
    return asyncio.Semaphore(1)


# ---------------------------------------------------------------------------
# 1. Concurrency is order-stable: identical claims, identical counters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_claims_match_the_serial_result(monkeypatch: Any) -> None:
    """Same section, serial gate vs real gate -> bit-identical output.

    The serial run uses the *same* code with a one-permit gate, so the only
    variable under test is the concurrency itself.
    """
    chunk = _chunk()
    claims = _claims(25)
    chunks = {chunk.chunk_id: chunk}

    serial_evaluator = InFlightEvaluator()
    monkeypatch.setattr(checker_module, "_nli_gate", _serial_gate)
    serial_claims, serial_card = await CitationChecker(
        evaluator=serial_evaluator
    ).audit_claims(claims=claims, chunks_by_id=chunks, section_id="sec_mix")

    monkeypatch.undo()
    concurrent_evaluator = InFlightEvaluator()
    concurrent_claims, concurrent_card = await CitationChecker(
        evaluator=concurrent_evaluator
    ).audit_claims(claims=claims, chunks_by_id=chunks, section_id="sec_mix")

    # The serial baseline really was serial, and the concurrent one was not.
    assert serial_evaluator.peak_in_flight == 1
    assert concurrent_evaluator.peak_in_flight > 1

    assert [c.claim_id for c in concurrent_claims] == [
        c.claim_id for c in serial_claims
    ]
    assert [c.text for c in concurrent_claims] == [c.text for c in serial_claims]
    assert [c.entailment_status for c in concurrent_claims] == [
        c.entailment_status for c in serial_claims
    ]
    assert [c.entailment_score for c in concurrent_claims] == [
        c.entailment_score for c in serial_claims
    ]
    assert [c.premise_text for c in concurrent_claims] == [
        c.premise_text for c in serial_claims
    ]
    assert [c.reasoning for c in concurrent_claims] == [c.reasoning for c in serial_claims]

    assert concurrent_card.model_dump() == serial_card.model_dump()
    for field in (
        "precision",
        "recall",
        "f_beta",
        "beta",
        "total_claims",
        "supported_claims",
        "ungrounded_claims",
        "contradicted_claims",
        "neutral_claims",
    ):
        assert getattr(concurrent_card, field) == getattr(serial_card, field), field

    # The mixed section must really exercise every counter, or the comparison
    # above would be trivially true.
    assert serial_card.total_claims == 25
    assert serial_card.supported_claims == 5
    assert serial_card.neutral_claims == 5
    assert serial_card.contradicted_claims == 5
    assert serial_card.ungrounded_claims == 10


@pytest.mark.asyncio
async def test_completion_order_does_not_reach_the_result(monkeypatch: Any) -> None:
    """The evaluator is made to finish in reverse order; output is unchanged."""
    chunk = _chunk()
    claims = _claims(12)
    chunks = {chunk.chunk_id: chunk}

    async def slow_first(premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        # Earlier claims sleep longer, so completion order is the reverse of
        # submission order.
        idx = int(hypothesis.split("number ")[1].split(" ")[0])
        await asyncio.sleep((len(claims) - idx) * 0.002)
        return _verdict_for(hypothesis)

    class _Slow:
        async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
            return await slow_first(premise, hypothesis)

    monkeypatch.setattr(checker_module, "_nli_gate", _serial_gate)
    baseline, baseline_card = await CitationChecker(evaluator=_Slow()).audit_claims(
        claims=claims, chunks_by_id=chunks, section_id="sec_mix"
    )

    monkeypatch.undo()
    evaluated, card = await CitationChecker(evaluator=_Slow()).audit_claims(
        claims=claims, chunks_by_id=chunks, section_id="sec_mix"
    )

    assert [c.claim_id for c in evaluated] == [c.claim_id for c in baseline]
    assert [c.entailment_status for c in evaluated] == [
        c.entailment_status for c in baseline
    ]
    assert card.model_dump() == baseline_card.model_dump()


@pytest.mark.asyncio
async def test_strict_hallucination_check_still_raises_for_a_missing_chunk() -> None:
    """Concurrency did not weaken the guard: the first bad claim still raises."""
    from lmeditor.core.exceptions import CitationHallucinationError

    claims = [
        Claim(
            claim_id="good",
            section_id="sec_ghost",
            text="This one is supported by the passage.",
            cited_chunk_ids=[_CHUNK_ID],
        ),
        Claim(
            claim_id="ghost",
            section_id="sec_ghost",
            text="This one cites a chunk that does not exist.",
            cited_chunk_ids=["chunk_phantom"],
        ),
    ]
    checker = CitationChecker(evaluator=InFlightEvaluator())

    with pytest.raises(CitationHallucinationError) as exc_info:
        await checker.audit_claims(
            claims=claims,
            chunks_by_id={_CHUNK_ID: _chunk()},
            strict_hallucination_check=True,
        )

    assert "chunk_phantom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_uncited_claims_short_circuit_without_a_model_call() -> None:
    """A claim with no citation is decided locally, exactly as before."""
    evaluator = InFlightEvaluator()
    checker = CitationChecker(evaluator=evaluator)

    claims = _claims(5)
    evaluated, card = await checker.audit_claims(
        claims=claims, chunks_by_id={_CHUNK_ID: _chunk()}
    )

    # Five claims, four of them cited: only the cited ones reach the model.
    assert evaluator.calls == 4
    assert len(evaluated) == 5
    assert evaluated[4].entailment_status == EntailmentStatus.UNGROUNDED
    assert evaluated[4].premise_text is None
    assert evaluated[4].reasoning == "Claim has no inline citations."
    assert card.ungrounded_claims == 2


# ---------------------------------------------------------------------------
# 2. The semaphore bound is respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_concurrency_never_exceeds_the_cap() -> None:
    """A 4x-cap claim section is admitted exactly ``MAX_CONCURRENT`` at a time."""
    assert MAX_CONCURRENT_NLI_EVALUATIONS == 8

    evaluator = InFlightEvaluator()
    chunk = _chunk()
    claims = _claims(MAX_CONCURRENT_NLI_EVALUATIONS * 4)

    await CitationChecker(evaluator=evaluator).audit_claims(
        claims=claims, chunks_by_id={chunk.chunk_id: chunk}
    )

    assert evaluator.calls == sum(1 for c in claims if c.cited_chunk_ids)
    assert evaluator.peak_in_flight <= MAX_CONCURRENT_NLI_EVALUATIONS
    # ... and it really does saturate the gate, rather than quietly serialising.
    assert evaluator.peak_in_flight == MAX_CONCURRENT_NLI_EVALUATIONS


@pytest.mark.asyncio
async def test_section_fan_out_is_bounded_too() -> None:
    """``verify_citations_node`` audits sections concurrently but not unboundedly."""
    section_count = MAX_CONCURRENT_NLI_EVALUATIONS * 3
    state = _state_with_sections(section_count)
    checker = _InFlightChecker()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", lambda *a, **k: checker, raising=True
    )
    try:
        result = verify_citations_node(state)
    finally:
        monkeypatch.undo()

    assert checker.peak_in_flight <= MAX_CONCURRENT_NLI_EVALUATIONS
    assert checker.peak_in_flight > 1, "sections were not audited concurrently"
    assert len(result["verification_reports"]) == section_count


@pytest.mark.asyncio
async def test_calls_really_do_run_in_parallel() -> None:
    """Six calls block until all six are inside the evaluator at the same time.

    Serial evaluation would deadlock here, so the ``wait_for`` is what turns a
    regression into a failure rather than a hung test.
    """
    target = 6
    assert target <= MAX_CONCURRENT_NLI_EVALUATIONS

    evaluator = BarrierEvaluator(target=target)
    chunk = _chunk()
    claims = _claims(12)

    evaluated, card = await asyncio.wait_for(
        CitationChecker(evaluator=evaluator).audit_claims(
            claims=claims, chunks_by_id={chunk.chunk_id: chunk}
        ),
        timeout=10,
    )

    assert evaluator.peak_in_flight >= target
    assert len(evaluated) == 12
    assert card.total_claims == 12


# ---------------------------------------------------------------------------
# 3. The NLI cache is bounded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_is_bounded_after_exceeding_the_cap() -> None:
    """Filling the cache past ``MAX_CACHE_ENTRIES`` leaves exactly the cap behind."""
    assert MAX_CACHE_ENTRIES == 4096

    evaluator = ResilientNLIEvaluator(
        primary_evaluator=InFlightEvaluator(), fallback_evaluator=HeuristicNLIEvaluator()
    )
    overflow = 500

    for idx in range(MAX_CACHE_ENTRIES + overflow):
        status, _score = await evaluator.evaluate(
            f"premise {idx}", f"hypothesis {idx} is supported"
        )
        assert status == EntailmentStatus.ENTAILED

    assert evaluator.cache_size() == MAX_CACHE_ENTRIES


@pytest.mark.asyncio
async def test_cache_evicts_least_recently_used_first() -> None:
    """A hit refreshes recency, so the oldest *unused* entry is the one dropped."""
    evaluator = ResilientNLIEvaluator(
        primary_evaluator=InFlightEvaluator(), fallback_evaluator=HeuristicNLIEvaluator()
    )

    await evaluator.evaluate("keep me", "h0 is supported")
    for idx in range(1, MAX_CACHE_ENTRIES):
        await evaluator.evaluate(f"p{idx}", f"h{idx} is supported")

    # Exactly at the cap, so nothing has been dropped yet.
    assert evaluator.cache_size() == MAX_CACHE_ENTRIES
    assert evaluator._cache_get(("keep me", "h0 is supported")) is not None

    # Refresh the oldest entry, then overflow by one. The eviction must skip it.
    await evaluator.evaluate("keep me", "h0 is supported")
    await evaluator.evaluate("overflow", "overflow is supported")

    assert evaluator.cache_size() == MAX_CACHE_ENTRIES
    assert evaluator._cache_get(("keep me", "h0 is supported")) is not None
    assert evaluator._cache_get(("p1", "h1 is supported")) is None
    assert evaluator._cache_get(("overflow", "overflow is supported")) is not None


def test_the_process_wide_singleton_cache_is_bounded() -> None:
    """``default_nli_evaluator`` is shared by every document; its cache must not grow."""
    evaluator = default_nli_evaluator
    evaluator._cache.clear()

    overflow = 500
    for idx in range(MAX_CACHE_ENTRIES + overflow):
        evaluator._cache_put((f"premise {idx}", f"hypothesis {idx}"), (EntailmentStatus.ENTAILED, 0.9))

    assert evaluator.cache_size() == MAX_CACHE_ENTRIES
    evaluator._cache.clear()


# ---------------------------------------------------------------------------
# 4. The node no longer allocates a thread pool per call
# ---------------------------------------------------------------------------


def test_node_uses_no_thread_pool_when_no_loop_is_running() -> None:
    """The LangGraph dispatch path: a plain synchronous call, no pool, no join.

    The previous implementation opened a ``ThreadPoolExecutor`` per node call and
    blocked on ``.result()``; the replacement borrows a loop with ``asyncio.run``
    and allocates no thread at all, so the live thread count is unchanged.
    """
    import threading

    state = _state_with_sections(4)
    checker = _InFlightChecker()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", lambda *a, **k: checker, raising=True
    )
    before = threading.active_count()
    try:
        result = verify_citations_node(state)
    finally:
        monkeypatch.undo()

    assert len(result["verification_reports"]) == 4
    assert checker.peak_in_flight > 1
    assert threading.active_count() == before


@pytest.mark.asyncio
async def test_node_works_when_called_from_inside_a_running_loop() -> None:
    """The other branch: the caller already has a loop, so ``asyncio.run`` is illegal."""
    state = _state_with_sections(4)
    checker = _InFlightChecker()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", lambda *a, **k: checker, raising=True
    )
    try:
        result = await asyncio.to_thread(verify_citations_node, state)
    finally:
        monkeypatch.undo()

    assert len(result["verification_reports"]) == 4
    assert checker.peak_in_flight > 1


def test_langgraph_accepts_a_coroutine_node() -> None:
    """Evidence for the async-node option in the contract.

    ``StateGraph.add_node`` does accept a coroutine function, so
    ``verify_citations_node`` could be made ``async def``. It is left
    synchronous on purpose: ``tests/test_workflow.py`` calls it directly as
    ``verify_citations_node(state)`` and ``state.update(result)``, which only
    works for a function returning a mapping. That test is outside this change's
    ownership, so converting the node would break the suite rather than speed it
    up.
    """
    from lmeditor.workflow.state import HarnessState as _State

    async def _async_node(state: _State) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"status": "async_node_ran"}

    workflow = StateGraph(_State)
    workflow.set_entry_point("async_node")
    workflow.add_node("async_node", _async_node)
    workflow.add_edge("async_node", END)
    graph = workflow.compile()

    result = asyncio.run(graph.ainvoke({"doc_id": "d", "topic": "t"}))

    assert result["status"] == "async_node_ran"


def test_verify_citations_node_is_still_synchronous() -> None:
    """Guard the assumption above: if this ever changes, the reason must be revisited."""
    import inspect

    assert not inspect.iscoroutinefunction(verify_citations_node)
    assert inspect.iscoroutinefunction(CitationChecker.audit_claims)


# ---------------------------------------------------------------------------
# 5. The heuristic scoring semantics are untouched
# ---------------------------------------------------------------------------


def test_heuristic_evaluator_scores_are_unchanged() -> None:
    """The throughput change must not have moved a single verdict.

    One verdict *did* move, deliberately: a hypothesis that shares nothing
    with the premise is neither entailed nor contradicted, so the evaluator
    now reports ``NEUTRAL``. ``UNGROUNDED`` is reserved for a claim that has
    no premise to check at all (an uncited claim), which is the distinction
    the audit asked for.
    """
    evaluator = HeuristicNLIEvaluator()

    async def _check() -> list[tuple[EntailmentStatus, float]]:
        return [
            await evaluator.evaluate(
                "Grounded writing requires verifiable citations.",
                hypothesis,
            )
            for hypothesis in (
                "Grounded writing requires verifiable citations.",
                "The moon is made of green cheese.",
                "Grounded writing requires evidence.",
                "Grounded writing does not require verifiable citations.",
            )
        ]

    assert asyncio.run(_check()) == [
        (EntailmentStatus.ENTAILED, 1.0),
        (EntailmentStatus.NEUTRAL, 0.0),
        (EntailmentStatus.ENTAILED, 0.75),
        (EntailmentStatus.CONTRADICTED, 0.3333),
    ]


@pytest.mark.asyncio
async def test_metric_formulas_are_unchanged() -> None:
    """Precision / recall / F_beta still come from the same integer counters."""
    chunk = _chunk()
    claims = _claims(10)
    evaluated, card = await CitationChecker(evaluator=InFlightEvaluator()).audit_claims(
        claims=claims, chunks_by_id={chunk.chunk_id: chunk}
    )

    assert isinstance(card, VerificationScorecard)
    cited = [c for c in evaluated if c.cited_chunk_ids]
    supported_cited = sum(
        1 for c in cited if c.entailment_status == EntailmentStatus.ENTAILED
    )
    assert supported_cited == card.supported_claims
    assert card.precision == checker_module.compute_citation_precision(
        supported_cited, len(cited), card.total_claims
    )
    assert card.recall == checker_module.compute_citation_recall(
        card.supported_claims, card.total_claims
    )
    assert card.f_beta == checker_module.compute_groundedness_f_beta(
        card.precision, card.recall, beta=0.5
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _InFlightChecker:
    """Stand-in ``CitationChecker`` that reports its own section concurrency."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak_in_flight = 0
        # The real checker exposes the NLI evaluator it used; the workflow node
        # reads ``checker.evaluator.evaluator_name`` to label a report, so the
        # stand-in has to answer the same question.
        self.evaluator = HeuristicNLIEvaluator()

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, VerificationScorecard]:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            for _ in range(3):
                await asyncio.sleep(0)
            return (
                section.model_copy(
                    update={"is_verified": True, "status": SectionStatus.VERIFIED}
                ),
                VerificationScorecard(
                    precision=1.0,
                    recall=1.0,
                    f_beta=1.0,
                    beta=0.5,
                    total_claims=1,
                    supported_claims=1,
                    ungrounded_claims=0,
                    contradicted_claims=0,
                    neutral_claims=0,
                    details=[],
                ),
            )
        finally:
            self.in_flight -= 1


def _state_with_sections(count: int) -> HarnessState:
    """A state holding ``count`` drafted, unverified sections."""
    state = create_initial_harness_state(doc_id="doc_sections", topic="Throughput")
    state["source_chunks"] = {"chunk_a": _chunk()}
    state["section_drafts"] = {
        f"sec_{idx}": SectionDraft(
            section_id=f"sec_{idx}",
            title=f"Section {idx}",
            content=f"Body of section {idx} citing its passage [chunk_a].",
            order=idx,
            status=SectionStatus.DRAFTED,
        )
        for idx in range(count)
    }
    return state
