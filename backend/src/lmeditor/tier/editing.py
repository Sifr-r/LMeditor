"""The editing rules, as the Kotlin tier implements them.

The *generation* of a revision is a model call and stays in Python. The
*acceptance* of one -- may this text stand, given the sources the document
has? -- is a rule, and the rule is Kotlin's.

The edit is therefore a conversation across the seam in two halves. The first
half is the plan: which span of the section the edit lands on, which passages
the model may be shown, and the instruction it is shown them with. The second
half is the acceptance: whether the revision the model produced may stand,
and what the section looks like when it does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lmeditor.bridge.errors import BridgeUnavailableError, KotlinDomainError
from lmeditor.core.schemas import (
    Claim,
    InlineEditRequest,
    InlineEditResult,
    SectionDraft,
    SourceChunk,
)
from lmeditor.tier.bridge import tier_bridge


def _call(method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Ask the tier for something, and say so plainly when it cannot answer.

    A tier that does not serve a method is a tier that is not there: the jar
    on disk predates the rule. That is an infrastructure failure and not a bad
    request, so it is reported as one -- it must never be turned into a
    domain error, and it must never be answered with a Python implementation
    instead.
    """
    try:
        return tier_bridge().call(method, params)
    except KotlinDomainError as exc:
        if exc.error_type == "BridgeError" and "unknown method" in str(exc):
            raise BridgeUnavailableError(
                f"the Kotlin tier does not serve '{method}'",
                f"the tier jar must be rebuilt: {exc}",
            ) from exc
        raise


def validate_revision(
    revised_text: str,
    section: SectionDraft,
    chunks: list[SourceChunk],
) -> tuple[bool, str, list[str], list[Claim]]:
    """Whether a proposed revision may stand.

    Returns whether it was accepted, why, the chunk ids it relies on, and the
    claims extracted from it. A revision that cites a chunk the document does
    not have is refused, and the refusal is a result rather than an exception.

    The claims carry no entailment verdict: the model that produces one lives
    in the Python tier, and its answer is handed back to Kotlin for the audit.
    """
    result = _call(
        "editing.validate_revision",
        {
            "revised_text": revised_text,
            "section": section.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        },
    )
    accepted = bool(result.get("accepted", False))
    reason = str(result.get("reason", ""))
    cited = [str(item) for item in result.get("cited_chunk_ids", [])]
    claims = [Claim.model_validate(item) for item in result.get("claims", [])]
    return accepted, reason, cited, claims


def plan_edit(
    request: InlineEditRequest,
    section: SectionDraft,
    chunks: Sequence[SourceChunk],
    *,
    max_passages: int = 6,
) -> tuple[str, str, list[SourceChunk]]:
    """The plan of an edit: the instruction, the text to revise and the passages.

    The span of the section the edit lands on and the instruction the model
    is given are rules, and the rules are Kotlin's. The rewrite itself is a
    model call and happens in Python, so the plan is fetched first and the
    model is called with it.

    The passages that come back are the ones that speak to the target: they
    are found by overlap, because the retrieval index belongs to the Python
    tier and the passages it ranked travel with the request instead.

    Raises:
        ValidationError: The request is empty or malformed.
        EditTargetNotFoundError: The section, the paragraph or the selected
            text does not exist.
    """
    result = _call(
        "editing.plan_edit",
        {
            "request": request.model_dump(mode="json"),
            "section": section.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "max_passages": max_passages,
        },
    )
    instruction = str(result.get("instruction", ""))
    original_text = str(result.get("original_text", ""))
    passages = [SourceChunk.model_validate(item) for item in result.get("passages", [])]
    return instruction, original_text, passages


def apply_edit(
    request: InlineEditRequest,
    section: SectionDraft,
    chunks: Sequence[SourceChunk],
    revised_text: str,
    evaluated_claims: Sequence[Claim] = (),
    *,
    index_chunks: Sequence[SourceChunk] = (),
    ranked_passages: Sequence[SourceChunk] = (),
    use_index: bool = False,
    max_passages: int = 6,
    refusal_reason: str = "",
    beta: float = 0.5,
    threshold_f_beta: float = 0.85,
    evaluator_name: str = "",
) -> InlineEditResult:
    """Whether the revision may stand, and the section that comes back when it does.

    The claims of the revision are checked against the chunks it cites, the
    scorecard is computed from the verdicts, and the section is written back
    only when the revision holds. A revision that does not hold comes back as
    a result with ``applied=False`` and the reason it was refused, never as
    an exception.

    ``evaluated_claims`` are the claims of the revision with their entailment
    verdicts already on them: the model that judges them lives in the Python
    tier, and its judgement travels back across the seam. ``refusal_reason``
    carries the reason the rewrite seam itself refused, when it did.

    Raises:
        ValidationError: The request is empty or malformed.
        EditTargetNotFoundError: The section, the paragraph or the selected
            text does not exist.
    """
    result = _call(
        "editing.apply_edit",
        {
            "request": request.model_dump(mode="json"),
            "section": section.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "index_chunks": [chunk.model_dump(mode="json") for chunk in index_chunks],
            "ranked_passages": [chunk.model_dump(mode="json") for chunk in ranked_passages],
            "use_index": use_index,
            "max_passages": max_passages,
            "revised_text": revised_text,
            "refusal_reason": refusal_reason,
            "evaluated_claims": [claim.model_dump(mode="json") for claim in evaluated_claims],
            "beta": beta,
            "threshold_f_beta": threshold_f_beta,
            "evaluator_name": evaluator_name,
        },
    )
    return InlineEditResult.model_validate(result["result"])


__all__ = ["apply_edit", "plan_edit", "validate_revision"]
