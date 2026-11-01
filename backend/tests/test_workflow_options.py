"""Regression tests for AUDIT finding 12: the API's ``max_retries`` and
``threshold_f_beta`` reached ``run_workflow`` and were then silently dropped.

Neither value entered ``HarnessState``, so ``route_after_verification``
compared retry counts against the hardcoded ``MAX_SECTION_RETRIES = 3`` and
``verify_citations_node`` built ``CitationChecker(f_beta_threshold=0.85)``
regardless of what the caller asked for. ``POST /api/v1/documents/generate``
documents both fields, so this is a broken contract, not an unused one.

Everything here runs fully offline: verification is done by a stand-in checker
and by the real heuristic NLI path, never by a model download.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from lmeditor.core.schemas import (
    Claim,
    SectionDraft,
    SectionStatus,
    SourceChunk,
    VerificationReport,
    WorkflowEvent,
)
from lmeditor.core.types import EntailmentStatus, EventType, WorkflowStep
from lmeditor.verification.citation_checker import (
    DEFAULT_F_BETA_THRESHOLD,
    CitationChecker,
)
from lmeditor.workflow.edges import MAX_SECTION_RETRIES, route_after_verification
from lmeditor.workflow.graph import (
    AWAITING_HUMAN_REVIEW_STATUS,
    run_workflow,
)
from lmeditor.workflow.nodes import verify_citations_node
from lmeditor.workflow.state import HarnessState, create_initial_harness_state

# ---------------------------------------------------------------------------
# Stand-ins
# ---------------------------------------------------------------------------


class RecordingSink:
    """Fake ``EventSink`` that records every published event."""

    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    async def publish_event(self, event: WorkflowEvent) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[WorkflowEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def node_completions(self, step: WorkflowStep) -> int:
        return sum(
            1
            for e in self.of_type(EventType.NODE_COMPLETE)
            if e.step == step and e.data.get("node")
        )


class _FixedScorecard:
    """Scorecard whose only interesting property is the F_beta it reports.

    The other three counters are held at zero so the node's four-condition gate
    reduces to the threshold comparison alone -- otherwise the test would pass
    for the wrong reason (any non-entailed claim would trip a counter and the
    threshold would never be reached).
    """

    def __init__(self, f_beta: float) -> None:
        self.precision = f_beta
        self.recall = f_beta
        self.f_beta = f_beta
        self.beta = 0.5
        self.total_claims = 10
        self.supported_claims = 10
        self.ungrounded_claims = 0
        self.contradicted_claims = 0
        self.neutral_claims = 0
        self.details: list[Any] = []


class RecordingChecker:
    """Stand-in checker that records the threshold it was constructed with."""

    #: Every instance built during a test, newest last.
    instances: list[RecordingChecker] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        self.f_beta_threshold = float(
            kwargs.get("f_beta_threshold", DEFAULT_F_BETA_THRESHOLD)
        )
        self.sections_audited: list[str] = []
        RecordingChecker.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    @classmethod
    def thresholds_seen(cls) -> list[float]:
        """The threshold every checker built during this test was given."""
        return [c.f_beta_threshold for c in cls.instances]

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, _FixedScorecard]:
        self.sections_audited.append(section.section_id)
        scorecard = _FixedScorecard(f_beta=0.9)
        return (
            section.model_copy(
                update={
                    "is_verified": False,
                    "status": SectionStatus.NEEDS_REVISION,
                    "claims": [],
                }
            ),
            scorecard,
        )


class NeverPassingChecker(RecordingChecker):
    """Stand-in that reports F_beta=0.0 so the run always hits the retry loop."""

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, _FixedScorecard]:
        await super().audit_section(section, chunks_by_id, strict_hallucination_check)
        return (
            section.model_copy(
                update={
                    "is_verified": False,
                    "status": SectionStatus.NEEDS_REVISION,
                    "claims": [],
                }
            ),
            _FixedScorecard(f_beta=0.0),
        )


def _seeded_state(**overrides: Any) -> HarnessState:
    """A state with one unverified section, ready for the verification node."""
    section = SectionDraft(
        section_id="sec_opts",
        title="Options",
        content="A drafted claim that cites its source passage.",
        claims=[
            Claim(
                claim_id="c0",
                section_id="sec_opts",
                text="A drafted claim.",
                cited_chunk_ids=["chunk_a"],
            )
        ],
    )
    state = create_initial_harness_state(doc_id="doc_opts", topic="Options")
    state["section_drafts"] = {"sec_opts": section}
    state["source_chunks"] = {
        "chunk_a": SourceChunk.create(
            document_id="doc_opts",
            text="A drafted claim.",
            char_start=0,
            char_end=16,
            byte_start=0,
            byte_end=16,
        )
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. max_retries=1 reaches the human gate after exactly one revision cycle
# ---------------------------------------------------------------------------


def _run_with_max_retries(max_retries: int, monkeypatch: Any, doc_id: str) -> dict[str, Any]:
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", NeverPassingChecker, raising=True
    )
    sink = RecordingSink()
    return asyncio.run(
        run_workflow(
            doc_id=doc_id,
            topic="Retry budget",
            raw_texts=[
                "Grounded writing requires verifiable citations attached to every "
                "assertion, and every claim must be traceable to an indexed passage."
            ],
            max_retries=max_retries,
            session=sink,
        )
    ), sink


def test_max_retries_one_reaches_hitl_after_a_single_revision_cycle(
    monkeypatch: Any,
) -> None:
    """The regression itself: one retry, then the gate -- not three retries.

    The loop count is read from the published ``critique_and_refine`` node
    completions, so this fails if the constant is consulted again, not merely
    if a default changed.
    """
    res, sink = _run_with_max_retries(1, monkeypatch, "doc_one_retry")

    assert res["requires_hitl"] is True
    assert res["status"] == AWAITING_HUMAN_REVIEW_STATUS

    revisions = sink.node_completions(WorkflowStep.CRITIQUE_REVISION)
    assert revisions == 1, (
        "max_retries=1 must allow exactly one critique/redraft cycle, "
        f"but the run performed {revisions}"
    )

    # Corroborated by the state the checkpoint actually carries.
    assert res["retry_counts"], "the run never reached the refinement loop"
    assert max(res["retry_counts"].values()) == 1
    assert res["max_retries"] == 1

    hitl = sink.of_type(EventType.HITL_REQUEST)
    assert len(hitl) == 1
    # The stream tells the operator the budget that was really enforced.
    assert hitl[0].data["max_section_retries"] == 1


def test_default_retry_budget_is_unchanged(monkeypatch: Any) -> None:
    """Omitting ``max_retries`` still buys three revision cycles."""
    assert MAX_SECTION_RETRIES == 3

    res, sink = _run_with_max_retries(3, monkeypatch, "doc_three_retry")

    assert res["requires_hitl"] is True
    assert sink.node_completions(WorkflowStep.CRITIQUE_REVISION) == MAX_SECTION_RETRIES
    assert max(res["retry_counts"].values()) == MAX_SECTION_RETRIES
    assert res["max_retries"] == MAX_SECTION_RETRIES

    hitl = sink.of_type(EventType.HITL_REQUEST)
    assert hitl[0].data["max_section_retries"] == MAX_SECTION_RETRIES


def test_omitting_max_retries_keeps_the_constant_default(monkeypatch: Any) -> None:
    """``run_workflow``'s own default reaches the state unchanged."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", NeverPassingChecker, raising=True
    )
    sink = RecordingSink()

    res = asyncio.run(
        run_workflow(
            doc_id="doc_default_retry",
            topic="Default retry budget",
            raw_texts=[
                "Grounded writing requires verifiable citations attached to every "
                "assertion, and every claim must be traceable to an indexed passage."
            ],
            session=sink,
        )
    )

    assert res["max_retries"] == MAX_SECTION_RETRIES
    assert sink.node_completions(WorkflowStep.CRITIQUE_REVISION) == MAX_SECTION_RETRIES
    assert sink.of_type(EventType.HITL_REQUEST)[0].data["max_section_retries"] == 3

    initial = create_initial_harness_state(doc_id="d", topic="t")
    assert initial["max_retries"] == 3


# ---------------------------------------------------------------------------
# 2. A custom threshold_f_beta changes the per-section pass/fail decision
# ---------------------------------------------------------------------------


def test_custom_threshold_changes_the_per_section_decision(monkeypatch: Any) -> None:
    """The same scorecard passes at 0.85 and fails at 0.95."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", RecordingChecker, raising=True
    )
    RecordingChecker.reset()

    lenient = verify_citations_node(_seeded_state(threshold_f_beta=0.85))
    strict = verify_citations_node(_seeded_state(threshold_f_beta=0.95))

    # The stand-in reports F_beta=0.9; only the threshold differs between runs.
    assert lenient["section_drafts"]["sec_opts"].is_verified is True
    assert strict["section_drafts"]["sec_opts"].is_verified is False

    assert lenient["verification_reports"]["sec_opts"].all_passed is True
    assert strict["verification_reports"]["sec_opts"].all_passed is False
    assert (
        lenient["verification_reports"]["sec_opts"].status == EntailmentStatus.ENTAILED
    )
    assert (
        strict["verification_reports"]["sec_opts"].status == EntailmentStatus.UNGROUNDED
    )


def test_threshold_reaches_the_citation_checker_it_builds(monkeypatch: Any) -> None:
    """The state value is handed to ``CitationChecker``, not just compared locally.

    Otherwise the node's own ``all_passed`` would use one threshold while the
    section status written by ``audit_section`` used another, and the two could
    disagree -- which is the incoherence the constant was originally there to
    prevent.
    """
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", RecordingChecker, raising=True
    )
    RecordingChecker.reset()

    verify_citations_node(_seeded_state(threshold_f_beta=0.42))

    assert RecordingChecker.thresholds_seen() == [0.42]
    assert CitationChecker(f_beta_threshold=0.42).f_beta_threshold == 0.42


def test_run_workflow_forwards_threshold_f_beta_to_the_graph(monkeypatch: Any) -> None:
    """The API parameter reaches the node, not just the state dictionary."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", RecordingChecker, raising=True
    )
    RecordingChecker.reset()

    res = asyncio.run(
        run_workflow(
            doc_id="doc_threshold",
            topic="Threshold propagation",
            raw_texts=["Grounded writing requires verifiable citations [chunk_a]."],
            threshold_f_beta=0.42,
        )
    )

    assert res["threshold_f_beta"] == 0.42
    assert RecordingChecker.thresholds_seen(), "the verification node never ran"
    assert set(RecordingChecker.thresholds_seen()) == {0.42}


def test_default_threshold_is_unchanged() -> None:
    """Omitting the threshold still means 0.85, in the state and in the checker."""
    initial = create_initial_harness_state(doc_id="d", topic="t")
    assert initial["threshold_f_beta"] == DEFAULT_F_BETA_THRESHOLD
    assert DEFAULT_F_BETA_THRESHOLD == 0.85
    assert CitationChecker().f_beta_threshold == 0.85


# ---------------------------------------------------------------------------
# 3. Defaults behave exactly as before
# ---------------------------------------------------------------------------


def test_initial_state_seeds_both_option_keys() -> None:
    """Both keys exist on a fresh state so a round-trip through a checkpoint keeps them."""
    initial = create_initial_harness_state(doc_id="d", topic="t")

    assert initial["max_retries"] == 3
    assert initial["threshold_f_beta"] == 0.85


def test_initial_state_accepts_overrides() -> None:
    """Explicit values are stored verbatim, not clamped or coerced."""
    initial = create_initial_harness_state(
        doc_id="d", topic="t", max_retries=7, threshold_f_beta=0.5
    )

    assert initial["max_retries"] == 7
    assert initial["threshold_f_beta"] == 0.5


def test_state_module_does_not_import_the_verification_stack() -> None:
    """``workflow.state`` must stay a leaf of the package graph.

    Importing the default threshold from ``verification.citation_checker`` would
    make every state consumer pay for the NLI stack, so the coupling is
    documented in the source instead of expressed as an edge.
    """
    import lmeditor.workflow.state as state_module

    source = state_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    assert "from lmeditor.verification" not in text
    assert "import lmeditor.verification" not in text


# ---------------------------------------------------------------------------
# 4. Backward compatibility with checkpoints written before the keys existed
# ---------------------------------------------------------------------------


def test_state_without_the_option_keys_routes_on_the_defaults() -> None:
    """A pre-existing checkpoint (neither key present) must still route."""
    unverified = SectionDraft(
        section_id="sec_legacy",
        title="Legacy",
        content="A section from a checkpoint written before the options existed.",
        status=SectionStatus.FLAGGED,
    )

    below_default: HarnessState = {
        "section_drafts": {"sec_legacy": unverified},
        "retry_counts": {"sec_legacy": MAX_SECTION_RETRIES - 1},
    }
    at_default: HarnessState = {
        "section_drafts": {"sec_legacy": unverified},
        "retry_counts": {"sec_legacy": MAX_SECTION_RETRIES},
    }

    assert "max_retries" not in below_default
    assert "threshold_f_beta" not in below_default
    assert route_after_verification(below_default) == "needs_refinement"
    assert route_after_verification(at_default) == "hitl_required"


def test_the_option_and_its_absence_disagree_on_the_same_state() -> None:
    """Isolates the propagation: only the presence of ``max_retries`` differs.

    Both dictionaries describe the identical run -- one unverified section with
    one recorded retry. With the key present the budget is 1 and the run is
    spent; without it the router falls back to ``MAX_SECTION_RETRIES``. Before the
    fix the key was ignored and both returned the same answer.
    """
    unverified = SectionDraft(
        section_id="sec_legacy",
        title="Legacy",
        content="A section that has already been revised once.",
        status=SectionStatus.FLAGGED,
    )
    shared: dict[str, Any] = {
        "section_drafts": {"sec_legacy": unverified},
        "retry_counts": {"sec_legacy": 1},
    }

    with_option = cast_state({**shared, "max_retries": 1})
    without_option = cast_state(dict(shared))

    assert route_after_verification(with_option) == "hitl_required"
    assert route_after_verification(without_option) == "needs_refinement"


def test_graph_routes_a_legacy_state_without_the_option_keys() -> None:
    """The same holds when LangGraph itself drives the router.

    LangGraph hands the conditional-edge function whatever the merged state
    happens to contain, so this exercises the real integration rather than a
    direct function call.
    """
    unverified = SectionDraft(
        section_id="sec_legacy",
        title="Legacy",
        content="A section from a checkpoint written before the options existed.",
        status=SectionStatus.FLAGGED,
    )
    reached: list[str] = []

    def _sink_node(state: HarnessState) -> dict[str, Any]:
        reached.append("critique_and_refine")
        return {}

    workflow = StateGraph(HarnessState)
    workflow.set_entry_point("verify_citations")
    workflow.add_node("verify_citations", lambda state: {"status": "verified"})
    workflow.add_node("critique_and_refine", _sink_node)
    workflow.add_node("hitl_checkpoint", lambda state: {"status": "awaiting"})
    workflow.add_conditional_edges(
        "verify_citations",
        route_after_verification,
        {
            "all_verified": END,
            "needs_refinement": "critique_and_refine",
            "hitl_required": "hitl_checkpoint",
        },
    )
    workflow.add_edge("critique_and_refine", END)
    workflow.add_edge("hitl_checkpoint", END)
    graph = workflow.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "legacy"}}

    # A checkpoint written before the keys existed: exactly two keys.
    graph.invoke(
        cast_state(
            {
                "section_drafts": {"sec_legacy": unverified},
                "retry_counts": {"sec_legacy": MAX_SECTION_RETRIES - 1},
            }
        ),
        config=config,
    )
    assert reached == ["critique_and_refine"]

    reached.clear()
    graph.invoke(
        cast_state(
            {
                "section_drafts": {"sec_legacy": unverified},
                "retry_counts": {"sec_legacy": MAX_SECTION_RETRIES},
            }
        ),
        config=config,
    )
    assert reached == []


def cast_state(payload: dict[str, Any]) -> HarnessState:
    """Narrow a plain dict literal to the TypedDict for LangGraph's ``invoke``."""
    return HarnessState(**payload)


@pytest.mark.parametrize("key", ["max_retries", "threshold_f_beta"])
def test_option_keys_are_optional_on_the_typed_dict(key: str) -> None:
    """``HarnessState`` is ``total=False``, so neither key is required."""
    assert HarnessState.__total__ is False
    assert key in HarnessState.__annotations__


def test_report_shape_is_unchanged_by_the_new_options() -> None:
    """Sanity: the node still returns the same three keys the graph depends on."""
    result = verify_citations_node(_seeded_state())

    assert set(result) == {"verification_reports", "section_drafts", "status"}
    assert result["status"] == "citations_verified"
    report = result["verification_reports"]["sec_opts"]
    assert isinstance(report, VerificationReport)
