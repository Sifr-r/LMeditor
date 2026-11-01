"""LangGraph typed state definition and pure reducer operators for LMeditor.

The shape of a fresh run is a rule, and the rule is the Kotlin tier's: it is
implemented once, in ``lmeditor.workflow``, and the tier serves it across the
NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.state``, frozen, so the parity harness
can still ask both sides the same question.

The reducers stay in Python on purpose. LangGraph's channels are Python
objects, so they never crossed the seam; they are plumbing, not a domain rule,
and the tier has no version of them to answer with.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict, cast

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, ValidationError, WorkflowError
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
from lmeditor.tier import workflow as tier_workflow


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


def _require_str(value: Any, field: str) -> None:
    """Refuse a value that is not text.

    The seam coerces whatever it is given, so an integer document id would
    quietly become a string on the other side. Refusing it here says what was
    wrong instead of carrying it.
    """
    if not isinstance(value, str):
        raise ValidationError(
            f"Expected a string for '{field}', got {type(value).__name__}",
            field=field,
            value=value,
        )


def _require_str_list(value: Any, field: str) -> None:
    """Refuse a collection that is not a list of text."""
    if value is None:
        return
    if not isinstance(value, (list, tuple)):
        raise ValidationError(
            f"Expected a list of strings for '{field}', got {type(value).__name__}",
            field=field,
            value=value,
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValidationError(
                f"Expected a string at '{field}[{index}]', got {type(item).__name__}",
                field=field,
                value=item,
            )


def _require_sources(value: Any) -> None:
    """Refuse a source collection that is not a list of ``Source`` models."""
    if value is None:
        return
    if not isinstance(value, (list, tuple)):
        raise ValidationError(
            f"Expected a list of Source for 'sources', got {type(value).__name__}",
            field="sources",
            value=value,
        )
    for index, item in enumerate(value):
        if not isinstance(item, Source):
            raise ValidationError(
                f"Expected a Source at 'sources[{index}]', got {type(item).__name__}",
                field="sources",
                value=item,
            )


def _require_max_retries(value: Any) -> None:
    """Refuse a retry budget that is not a whole, non-negative number."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"Expected an integer for 'max_retries', got {type(value).__name__}",
            field="max_retries",
            value=value,
        )
    if value < 0:
        raise ValidationError(
            f"'max_retries' must not be negative, got {value}",
            field="max_retries",
            value=value,
        )


def _require_threshold(value: Any) -> None:
    """Refuse a groundedness threshold that is not a share."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"Expected a number for 'threshold_f_beta', got {type(value).__name__}",
            field="threshold_f_beta",
            value=value,
        )
    if not 0.0 <= float(value) <= 1.0:
        raise ValidationError(
            f"'threshold_f_beta' must be between 0.0 and 1.0, got {value}",
            field="threshold_f_beta",
            value=value,
        )


def create_initial_harness_state(
    doc_id: str,
    topic: str,
    target_audience: str | None = None,
    raw_texts: list[str] | None = None,
    sources: list[Source] | None = None,
    guidelines: list[str] | None = None,
    # The three defaults below -- the audience, the retry budget and the
    # groundedness threshold -- are owned by the Kotlin tier now. They are
    # ``None`` here rather than written out, because a second copy of a
    # default is a second rule that can drift; ``None`` means "the caller did
    # not say", and the tier supplies the value it always supplied.
    max_retries: int | None = None,
    threshold_f_beta: float | None = None,
) -> HarnessState:
    """Initialize a clean, deterministic HarnessState.

    The state a run starts from is a rule, and it is the Kotlin tier's: it is
    implemented once, in ``lmeditor.workflow`` on the Kotlin side, and this
    call asks for it across the NDJSON seam rather than assembling one here.

    Args:
        doc_id: The identifier the run is filed under.
        topic: The manuscript subject.
        target_audience: Who the manuscript is written for. ``None`` leaves
            the decision to the tier's own default.
        raw_texts: The passages the caller supplied.
        sources: The caller-supplied source artifacts (Layer 0).
        guidelines: The caller's writing guidelines.
        max_retries: The per-section revision budget. ``None`` leaves the
            decision to the tier's own default.
        threshold_f_beta: The groundedness threshold the verification step
            uses. ``None`` leaves the decision to the tier's own default.

    Returns:
        A fresh harness state: the same keys, the same defaults, the same
        ``"initialized"`` status and the same empty collections this function
        always returned, with every model in it rebuilt into the Python
        tier's own classes by the tier facade.

    Raises:
        ValidationError: An argument was not of the type the state accepts.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed: a silent fallback would hide exactly the drift the
            tier exists to remove.
    """
    _require_str(doc_id, "doc_id")
    _require_str(topic, "topic")
    if target_audience is not None:
        _require_str(target_audience, "target_audience")
    _require_str_list(raw_texts, "raw_texts")
    _require_str_list(guidelines, "guidelines")
    _require_sources(sources)
    _require_max_retries(max_retries)
    _require_threshold(threshold_f_beta)

    try:
        state = tier_workflow.create_initial_state(
            doc_id=doc_id,
            topic=topic,
            target_audience=target_audience,
            raw_texts=raw_texts,
            sources=sources,
            guidelines=guidelines,
            max_retries=max_retries,
            threshold_f_beta=threshold_f_beta,
        )
    except BridgeUnavailableError:
        # The tier is not there. That is an infrastructure failure, not a bad
        # input, and it must not be dressed up as one: the caller needs to
        # know that the rule could not be answered at all.
        raise
    except LMeditorError:
        # The tier's own refusal is already the Python counterpart of the
        # Kotlin error, carrying its field and its value.
        raise
    except Exception as exc:  # pragma: no cover - the tier's own failures
        raise WorkflowError(
            f"the Kotlin tier could not build the initial state for '{doc_id}': {exc}"
        ) from exc

    return cast(HarnessState, state)


__all__ = [
    "HarnessState",
    "append_lists",
    "create_initial_harness_state",
    "merge_dicts",
]
