"""The frozen harness state: the shape of a fresh run, as Python wrote it.

This is the original Python implementation of ``workflow.state``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The reducers live here for the same reason they live in the live module: they
are LangGraph's channel plumbing, not a domain rule, so they never crossed the
seam and are not the Kotlin tier's to change.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from lmeditor.core.schemas import (
    OutlineNode,
    OutlineNodeInput,
    Perspective,
    ResearchMemory,
    SectionDraft,
    Source,
    SourceChunk,
    VerificationReport,
)


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Pure dictionary reducer merging updates without side effects."""
    res = dict(a)
    res.update(b)
    return res


def append_lists(a: list[Any], b: list[Any]) -> list[Any]:
    """Pure list reducer concatenating elements."""
    return list(a) + list(b)


class HarnessState(TypedDict, total=False):
    """Central LangGraph state passed through the cyclic writing harness."""

    # Document metadata
    doc_id: str
    topic: str
    target_audience: str
    guidelines: list[str]

    # Layer 0: User-supplied Source artifacts (Phase P0.1)
    sources: list[Source]

    # Layer 1: Ingested & Indexed Source Chunks
    raw_texts: list[str]
    source_chunks: dict[str, SourceChunk]  # chunk_id -> SourceChunk

    # Layer 2: STORM Discovery & Outlining
    perspectives: list[Perspective]
    outline_root: OutlineNode | None
    outline_nodes: list[OutlineNode]  # Flat list of leaf sections

    # Guided research: the corpus questioned by the discovered perspectives
    # before the outline is written. ``research_memory`` is the consolidated
    # result of that inquiry and ``research_notes`` says what it found (or
    # why it did not run). ``guided_research`` is the toggle a run uses to
    # opt out of the stage; the default is on.
    guided_research: bool
    research_memory: ResearchMemory | None
    research_notes: list[str]

    # The outline the caller supplied, when the caller supplied one. This is
    # the operator's steer: the sections they named are the sections the
    # manuscript is written to. It carries no chunk assignments, because the
    # corpus still decides what may be said about each section.
    requested_outline: list[OutlineNodeInput] | None

    # Layer 3: DSPy Drafting
    section_drafts: Annotated[dict[str, SectionDraft], merge_dicts]  # section_id -> SectionDraft
    current_section_id: str | None

    # Layer 4: Verification & Citation Fidelity
    verification_reports: Annotated[dict[str, VerificationReport], merge_dicts]  # section_id -> report
    retry_counts: Annotated[dict[str, int], merge_dicts]  # section_id -> int
    critiques: Annotated[dict[str, str], merge_dicts]  # section_id -> critique string

    # Layer 5: Macro Cohesion & Polish
    full_manuscript: str
    status: str
    errors: Annotated[list[str], append_lists]

    # Human-in-the-Loop Review Gates
    requires_hitl: bool
    hitl_reason: str | None
    hitl_approved: bool

    # Caller-supplied verification options (AUDIT finding 12).
    # Declared on a ``total=False`` TypedDict and always read through
    # ``.get(key, <default>)``, so a checkpoint serialised before this change
    # -- which has neither key -- still loads and still routes on the defaults.
    max_retries: int
    threshold_f_beta: float

    #: Optional override for the NLI evaluator used by the verification node.
    #: Production never sets it (the default cross-encoder is used); the test
    #: suite sets it so a verification run does not need the model weights.
    nli_evaluator: Any


def create_initial_harness_state(
    doc_id: str,
    topic: str,
    target_audience: str = "Technical & Academic Readers",
    raw_texts: list[str] | None = None,
    sources: list[Source] | None = None,
    guidelines: list[str] | None = None,
    # The two defaults below are literal duplicates of
    # ``workflow.edges.MAX_SECTION_RETRIES`` and
    # ``verification.citation_checker.DEFAULT_F_BETA_THRESHOLD``. They are
    # written out rather than imported because this module is a leaf of the
    # package graph: importing ``edges`` here would make it depend on
    # ``core.schemas.SectionStatus``, and importing ``citation_checker`` would
    # drag the NLI stack (sentence-transformers, the resilient-evaluator
    # singleton) into every consumer of the state. Both import sites above
    # state.py, so the dependency can only ever point downwards; the
    # duplication is the price of keeping that direction.
    max_retries: int = 3,
    threshold_f_beta: float = 0.85,
) -> HarnessState:
    """Initialize a clean, deterministic HarnessState."""
    return HarnessState(
        doc_id=doc_id,
        topic=topic,
        target_audience=target_audience,
        guidelines=guidelines or [],
        raw_texts=raw_texts or [],
        sources=list(sources or []),
        source_chunks={},
        perspectives=[],
        outline_root=None,
        outline_nodes=[],
        guided_research=True,
        research_memory=None,
        research_notes=[],
        section_drafts={},
        current_section_id=None,
        verification_reports={},
        retry_counts={},
        critiques={},
        full_manuscript="",
        status="initialized",
        errors=[],
        requires_hitl=False,
        hitl_reason=None,
        hitl_approved=False,
        max_retries=max_retries,
        threshold_f_beta=threshold_f_beta,
    )


__all__ = [
    "HarnessState",
    "append_lists",
    "create_initial_harness_state",
    "merge_dicts",
]
