"""Regression tests for the human-in-the-loop lifecycle (AUDIT finding 7) and the
verification gate (AUDIT finding 8).

Before this pass ``hitl_checkpoint_node`` only set flags, so ``route_after_hitl``
read a default-false ``hitl_approved`` and the graph went straight to ``END``:
the harness never actually suspended for a human. ``run_workflow`` also ignored
its ``session`` argument, so nothing was ever published mid-run.

Everything here runs fully offline: the NLI evaluator and the DSPy programs are
faked or rely on the existing offline fallback paths.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from lmeditor.core.exceptions import InvalidStateTransitionError
from lmeditor.core.schemas import Claim, SectionDraft, SectionStatus, SourceChunk, WorkflowEvent
from lmeditor.core.types import EntailmentStatus, EventType, WorkflowStep
from lmeditor.verification.citation_checker import (
    DEFAULT_F_BETA_THRESHOLD,
    CitationChecker,
)
from lmeditor.workflow.edges import MAX_SECTION_RETRIES, route_after_hitl
from lmeditor.workflow.graph import (
    AWAITING_HUMAN_REVIEW_STATUS,
    EventSink,
    registered_workflow_threads,
    release_workflow,
    resume_workflow,
    run_workflow,
)
from lmeditor.workflow.nodes import hitl_checkpoint_node
from lmeditor.workflow.state import HarnessState


@pytest.fixture(autouse=True)
def _installed_model() -> Any:
    """Install a grounded stub model for the duration of each test.

    The workflow drafts through a real language model. Without one, the
    drafting node leaves the section empty and says why -- which is the
    correct behaviour, but it means a test that asserts on *drafted text*
    would assert on nothing. The stub quotes the passage it was given and
    cites that passage's real chunk id, so the text the stream carries is
    genuinely grounded.
    """
    import dspy

    from .stub_lm import StubLM

    previous = dspy.settings.lm
    dspy.configure(lm=StubLM())
    yield
    dspy.configure(lm=previous)


class RecordingSink:
    """Fake ``EventSink`` that records every published event."""
    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    async def publish_event(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[EventType]:
        return [e.event_type for e in self.events]

    def of_type(self, event_type: EventType) -> list[WorkflowEvent]:
        return [e for e in self.events if e.event_type == event_type]

    @property
    def completed_steps(self) -> list[WorkflowStep]:
        return [e.step for e in self.of_type(EventType.NODE_COMPLETE)]

    @property
    def started_steps(self) -> list[WorkflowStep]:
        return [e.step for e in self.of_type(EventType.NODE_START)]


class _Scorecard:
    precision = 0.0
    recall = 0.0
    f_beta = 0.0
    total_claims = 1
    supported_claims = 0
    ungrounded_claims = 1
    contradicted_claims = 0
    neutral_claims = 0
    details: list[Any] = []


class FailingCitationChecker:
    """Offline stand-in that never verifies a section."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, _Scorecard]:
        claims = [
            Claim(
                claim_id=f"claim_{section.section_id}_0",
                section_id=section.section_id,
                text="A claim the fake checker refuses to ground.",
                cited_chunk_ids=[],
                entailment_status=EntailmentStatus.UNGROUNDED,
                entailment_score=0.0,
            )
        ]
        return (
            section.model_copy(
                update={
                    "is_verified": False,
                    "status": SectionStatus.NEEDS_REVISION,
                    "claims": claims,
                }
            ),
            _Scorecard(),
        )


class PassingCitationChecker:
    """Offline stand-in that verifies every section."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, _Scorecard]:
        return (
            section.model_copy(update={"is_verified": True, "status": SectionStatus.VERIFIED}),
            _Scorecard(),
        )


def test_recording_sink_satisfies_event_sink_protocol() -> None:
    """The fake sink satisfies the structurally declared protocol."""
    assert isinstance(RecordingSink(), EventSink)


# ---------------------------------------------------------------------------
# §4: the gate really suspends, and the decision routes the graph
# ---------------------------------------------------------------------------


def _build_gate_graph(sink: RecordingSink) -> Any:
    """Minimal graph: hitl_checkpoint -> router -> optional completion sink."""
    def _record_completion(state: HarnessState) -> dict[str, Any]:
        sink.events.append(
            WorkflowEvent(
                event_type=EventType.NODE_COMPLETE,
                step=WorkflowStep.MACRO_POLISH,
                doc_id=str(state.get("doc_id", "")),
                data={"node": "macro_completion"},
            )
        )
        return {"status": "completed"}

    workflow = StateGraph(HarnessState)
    workflow.set_entry_point("hitl_checkpoint")
    workflow.add_node("hitl_checkpoint", hitl_checkpoint_node)
    workflow.add_node("macro_completion", _record_completion)
    workflow.add_conditional_edges(
        "hitl_checkpoint",
        route_after_hitl,
        {"proceed_to_macro": "macro_completion", "terminate": END},
    )
    workflow.add_edge("macro_completion", END)
    return workflow.compile(checkpointer=MemorySaver())


def test_hitl_node_suspends_before_any_decision() -> None:
    """First invoke raises an interrupt; the gate has not decided anything yet."""
    sink = RecordingSink()
    graph = _build_gate_graph(sink)
    config = {"configurable": {"thread_id": "t1"}}

    result = graph.invoke({"doc_id": "t1"}, config=config)

    assert "__interrupt__" in result
    assert sink.events == []


def test_approving_resume_routes_to_macro() -> None:
    """``Command(resume=approved)`` yields ``hitl_approved`` and continues."""
    sink = RecordingSink()
    graph = _build_gate_graph(sink)
    config = {"configurable": {"thread_id": "t2"}}

    graph.invoke({"doc_id": "t2"}, config=config)
    final = graph.invoke(Command(resume=True), config=config)

    assert final["hitl_approved"] is True
    assert final["requires_hitl"] is False
    assert route_after_hitl(final) == "proceed_to_macro"
    assert [e.data["node"] for e in sink.events] == ["macro_completion"]


def test_rejecting_resume_terminates() -> None:
    """``Command(resume=rejected)`` terminates the graph without completion."""
    sink = RecordingSink()
    graph = _build_gate_graph(sink)
    config = {"configurable": {"thread_id": "t3"}}

    graph.invoke({"doc_id": "t3"}, config=config)
    final = graph.invoke(Command(resume=False), config=config)

    assert final["hitl_approved"] is False
    assert route_after_hitl(final) == "terminate"
    assert sink.events == []


# ---------------------------------------------------------------------------
# §4: run_workflow / resume_workflow pairing
# ---------------------------------------------------------------------------


def test_run_workflow_publishes_node_and_token_events(monkeypatch: Any) -> None:
    """run_workflow publishes node transitions and drafted text to the sink."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    sink = RecordingSink()
    res = _run(sink)

    assert res["requires_hitl"] is True
    assert res["status"] == AWAITING_HUMAN_REVIEW_STATUS

    assert sink.started_steps[:4] == [
        WorkflowStep.INGEST,
        WorkflowStep.DISCOVER,
        WorkflowStep.GUIDED_RESEARCH,
        WorkflowStep.OUTLINE,
    ]
    assert WorkflowStep.HITL_GATE in sink.started_steps
    # The gate suspends inside the node, so it never emits a NODE_COMPLETE.
    assert WorkflowStep.HITL_GATE not in sink.completed_steps

    hitl = sink.of_type(EventType.HITL_REQUEST)
    assert len(hitl) == 1
    assert hitl[0].data["requires_hitl"] is True
    assert hitl[0].data["reason"]

    # Drafted text reaches the stream as token deltas.
    deltas = sink.of_type(EventType.TOKEN_DELTA)
    assert deltas
    assert all(isinstance(e.data["delta"], str) and e.data["delta"] for e in deltas)
    assert all(e.data["section_id"] for e in deltas)

    # Verification results reach the stream as scorecard updates.
    scorecards = sink.of_type(EventType.SCORECARD_UPDATE)
    assert scorecards
    for event in scorecards:
        card = event.data["scorecard"]
        for key in (
            "precision",
            "recall",
            "f_beta",
            "total_claims",
            "supported_claims",
            "ungrounded_claims",
            "contradicted_claims",
            "neutral_claims",
        ):
            assert key in card


def test_run_workflow_never_publishes_completion_when_suspended(monkeypatch: Any) -> None:
    """Suspending is not completing: no terminal event may be published."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    sink = RecordingSink()

    res = _run(sink)

    assert res["requires_hitl"] is True
    assert res["status"] == AWAITING_HUMAN_REVIEW_STATUS
    assert EventType.WORKFLOW_COMPLETE not in sink.types
    assert EventType.WORKFLOW_ERROR not in sink.types
    # Sanity: the graph really did reach the gate, so the run was not vacuous.
    assert res.get("retry_counts", {})
    assert max(res["retry_counts"].values()) >= MAX_SECTION_RETRIES


def test_run_workflow_completes_when_sections_verify(monkeypatch: Any) -> None:
    """The non-suspending path still runs to completion after the astream rewrite."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", PassingCitationChecker, raising=True
    )
    sink = RecordingSink()

    res = _run(sink)

    assert res.get("requires_hitl") is not True
    assert res["status"] == "completed"
    assert res["full_manuscript"]
    assert res["sections"]
    assert WorkflowStep.MACRO_POLISH in sink.started_steps
    assert sink.of_type(EventType.HITL_REQUEST) == []


def test_approving_resume_reenters_the_graph_past_the_gate(monkeypatch: Any) -> None:
    """An approving resume continues from the interrupt, not from the start.

    The evidence that the graph genuinely resumed rather than restarting: the
    gate node itself completes on resume, ingestion does *not* run again, and the
    post-gate macro node runs for the first time.
    """
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    doc_id = "doc_resume_ok"
    first = RecordingSink()

    suspended = _run(first, doc_id=doc_id)
    assert suspended["requires_hitl"] is True

    # The suspended run is retained so it can actually be resumed.
    assert doc_id in registered_workflow_threads()
    # Ingestion already happened during the first pass.
    assert WorkflowStep.INGEST in first.completed_steps
    assert WorkflowStep.MACRO_POLISH not in first.completed_steps

    second = RecordingSink()
    resumed = _resume(doc_id, True, second)

    # Re-entered at the gate and continued past it.
    assert WorkflowStep.HITL_GATE in second.completed_steps
    assert WorkflowStep.MACRO_POLISH in second.completed_steps
    # ... and did not restart from the beginning.
    assert WorkflowStep.INGEST not in second.completed_steps
    assert WorkflowStep.DISCOVER not in second.completed_steps
    assert WorkflowStep.VERIFY_CITATIONS not in second.completed_steps

    assert resumed["hitl_approved"] is True
    assert resumed["requires_hitl"] is False
    assert resumed["status"] == "completed"
    assert resumed["full_manuscript"]
    assert resumed["sections"]

    complete = second.of_type(EventType.WORKFLOW_COMPLETE)
    assert len(complete) == 1
    assert complete[0].step == WorkflowStep.COMPLETE
    assert complete[0].data["sections"]
    assert "scorecard" in complete[0].data


def test_the_terminal_frame_carries_a_real_document_scorecard(monkeypatch: Any) -> None:
    """``workflow_complete`` publishes the aggregate, not ``None``.

    The terminal frame used to publish ``scorecard: None``, so the client had
    no truthful measurement on the last event it received and fell back to
    displaying a fabricated perfect score.
    """
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    doc_id = "doc_terminal_scorecard"
    first = RecordingSink()
    suspended = _run(first, doc_id=doc_id)
    assert suspended["requires_hitl"] is True

    sink = RecordingSink()
    _resume(doc_id, True, sink)

    complete = sink.of_type(EventType.WORKFLOW_COMPLETE)
    assert len(complete) == 1

    scorecard = complete[0].data["scorecard"]
    assert scorecard is not None, "the terminal frame published no scorecard"
    assert isinstance(scorecard, dict)

    # The aggregate is computed from the sections the run actually audited.
    assert scorecard["total_sections"] == len(complete[0].data["sections"])
    assert scorecard["total_claims"] == scorecard["supported_claims"] + (
        scorecard["ungrounded_claims"]
        + scorecard["contradicted_claims"]
        + scorecard["neutral_claims"]
    )
    # A score, not a null and not a hard-coded 1.0.
    assert 0.0 <= scorecard["f_beta"] <= 1.0


def test_rejecting_resume_is_terminal_without_success(monkeypatch: Any) -> None:
    """A rejecting resume ends the run and is not reported as a success."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    doc_id = "doc_resume_no"
    _run(RecordingSink(), doc_id=doc_id)

    sink = RecordingSink()
    resumed = _resume(doc_id, False, sink)

    assert resumed["hitl_approved"] is False
    assert resumed["requires_hitl"] is False
    assert resumed["status"] == "human_review_rejected"
    assert not resumed.get("full_manuscript")
    # The gate ran, but the post-gate node did not.
    assert WorkflowStep.HITL_GATE in sink.completed_steps
    assert WorkflowStep.MACRO_POLISH not in sink.completed_steps
    # A rejection is not a completion.
    assert EventType.WORKFLOW_COMPLETE not in sink.types
    assert sink.of_type(EventType.HITL_REQUEST) == []


def test_resume_workflow_rejects_unknown_thread_id() -> None:
    """An unregistered thread raises a typed domain error, never a fresh run."""
    assert "doc_never_started" not in registered_workflow_threads()

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        _resume("doc_never_started", True, RecordingSink())

    assert "doc_never_started" in str(exc_info.value)


def test_registry_is_evicted_on_terminal_state(monkeypatch: Any) -> None:
    """The registry must not retain an entry once a run cannot be resumed."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )

    approved = "doc_evict_yes"
    _run(RecordingSink(), doc_id=approved)
    assert approved in registered_workflow_threads()
    _resume(approved, True, RecordingSink())
    assert approved not in registered_workflow_threads()

    rejected = "doc_evict_no"
    _run(RecordingSink(), doc_id=rejected)
    assert rejected in registered_workflow_threads()
    _resume(rejected, False, RecordingSink())
    assert rejected not in registered_workflow_threads()


def test_registry_is_evicted_when_a_run_completes_without_suspending(
    monkeypatch: Any,
) -> None:
    """A run that never suspends is terminal immediately and is not retained."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", PassingCitationChecker, raising=True
    )
    doc_id = "doc_evict_direct"
    _run(RecordingSink(), doc_id=doc_id)

    assert doc_id not in registered_workflow_threads()
    with pytest.raises(InvalidStateTransitionError):
        _resume(doc_id, True, RecordingSink())


def test_release_workflow_drops_an_abandoned_document(monkeypatch: Any) -> None:
    """An abandoned suspended run can be dropped explicitly."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", FailingCitationChecker, raising=True
    )
    doc_id = "doc_abandoned"
    _run(RecordingSink(), doc_id=doc_id)
    assert doc_id in registered_workflow_threads()

    assert release_workflow(doc_id) is True
    assert doc_id not in registered_workflow_threads()
    assert release_workflow(doc_id) is False
    with pytest.raises(InvalidStateTransitionError):
        _resume(doc_id, True, RecordingSink())


def _run(sink: RecordingSink, doc_id: str = "doc_hitl") -> dict[str, Any]:
    import asyncio

    return asyncio.run(
        run_workflow(
            doc_id=doc_id,
            topic="Grounded Expository Writing",
            raw_texts=[
                "Grounded writing requires verifiable citations attached to every assertion. "
                "Each claim must be traceable to an indexed source passage."
            ],
            session=sink,
        )
    )


def _resume(thread_id: str, decision: Any, sink: RecordingSink) -> dict[str, Any]:
    import asyncio

    return asyncio.run(resume_workflow(thread_id, decision, session=sink))


# ---------------------------------------------------------------------------
# §3: NEUTRAL claims must not pass the verification gate
# ---------------------------------------------------------------------------


class MixedEntailmentEvaluator:
    """Deterministic evaluator: every claim is entailed except the last one."""

    def __init__(self, neutral_index: int) -> None:
        self.neutral_index = neutral_index
        self.seen = 0

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        idx = self.seen
        self.seen += 1
        if idx == self.neutral_index:
            return EntailmentStatus.NEUTRAL, 0.5
        return EntailmentStatus.ENTAILED, 0.95


def _chunk() -> SourceChunk:
    text = "Grounded writing requires verifiable citations."
    return SourceChunk.create(
        document_id="doc_neutral",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _section_with_ten_cited_claims(chunk: SourceChunk) -> SectionDraft:
    sentences = " ".join(
        f"Claim number {i} is fully supported by the passage [#{chunk.chunk_id}]." for i in range(10)
    )
    return SectionDraft(
        section_id="sec_neutral",
        title="Neutral case",
        content=sentences,
    )


@pytest.mark.asyncio
async def test_neutral_claim_blocks_verification_despite_high_f_beta() -> None:
    """Nine entailed + one neutral used to score F_beta=0.9 and VERIFIED=True."""
    chunk = _chunk()
    section = _section_with_ten_cited_claims(chunk)
    checker = CitationChecker(evaluator=MixedEntailmentEvaluator(neutral_index=9))

    audited, scorecard = await checker.audit_section(
        section, chunks_by_id={chunk.chunk_id: chunk}
    )

    assert scorecard.total_claims == 10
    assert scorecard.supported_claims == 9
    assert scorecard.neutral_claims == 1
    assert scorecard.contradicted_claims == 0
    assert scorecard.ungrounded_claims == 0
    # F_beta alone would have cleared the bar -- only the new neutral rule rejects it.
    assert scorecard.f_beta >= DEFAULT_F_BETA_THRESHOLD
    assert audited.is_verified is False
    assert audited.status == SectionStatus.NEEDS_REVISION
    assert audited.critique is not None
    assert "Neutral claims: 1" in audited.critique


@pytest.mark.asyncio
async def test_threshold_is_configurable_and_defaults_to_contract_value() -> None:
    """``f_beta_threshold`` is a real keyword parameter defaulting to 0.85."""
    assert DEFAULT_F_BETA_THRESHOLD == 0.85

    chunk = _chunk()
    section = _section_with_ten_cited_claims(chunk)

    strict = CitationChecker(evaluator=MixedEntailmentEvaluator(neutral_index=9))
    assert strict.f_beta_threshold == DEFAULT_F_BETA_THRESHOLD

    lenient = CitationChecker(evaluator=MixedEntailmentEvaluator(neutral_index=9), f_beta_threshold=0.99)
    _audited, scorecard = await lenient.audit_section(
        section, chunks_by_id={chunk.chunk_id: chunk}
    )
    assert scorecard.f_beta < lenient.f_beta_threshold
