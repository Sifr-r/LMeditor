"""Deterministic domain exceptions for LMeditor."""

from __future__ import annotations

from typing import Any, Sequence


class LMeditorError(Exception):
    """Base exception for all LMeditor domain errors."""

    def __init__(
        self,
        message: str = "",
        details: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if kwargs:
            self.details.update(kwargs)
            for k, v in kwargs.items():
                if not hasattr(self, k):
                    setattr(self, k, v)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class ProvenanceError(LMeditorError):
    """Base exception for chunk provenance, chunking, and lineage alignment errors."""


class ChunkNotFoundError(ProvenanceError):
    """Raised when an indexed source chunk cannot be located."""

    def __init__(self, chunk_id: str, message: str | None = None) -> None:
        msg = message or f"ChunkNotFoundError: chunk_id='{chunk_id}' was not found in index."
        super().__init__(msg, details={"chunk_id": chunk_id})
        self.chunk_id = chunk_id


class SpanAlignmentError(ProvenanceError):
    """Raised when character/byte offsets violate document boundary constraints."""

    def __init__(
        self,
        span_start: int,
        span_end: int,
        doc_length: int | None = None,
        message: str | None = None,
    ) -> None:
        msg = message or (
            f"SpanAlignmentError: span [{span_start}, {span_end}) out of bounds"
            + (f" for doc_length={doc_length}." if doc_length is not None else ".")
        )
        super().__init__(
            msg,
            details={"span_start": span_start, "span_end": span_end, "doc_length": doc_length},
        )
        self.span_start = span_start
        self.span_end = span_end
        self.doc_length = doc_length


class VerificationError(LMeditorError):
    """Base exception for verification domain errors."""


class CitationHallucinationError(VerificationError):
    """Raised when an inline citation refers to a non-existent chunk or ungrounded premise."""

    def __init__(
        self,
        chunk_id: str = "",
        quote: str | None = None,
        reason: str | None = None,
        section_id: str | None = None,
        message: str | None = None,
        hallucinated_ids: Sequence[str] | None = None,
    ) -> None:
        cid = chunk_id or (hallucinated_ids[0] if hallucinated_ids else "unknown")
        msg = message or (
            f"CitationHallucinationError: chunk_id='{cid}' in section='{section_id or 'unknown'}'. "
            f"Quote: '{quote or ''}'. Reason: '{reason or 'ungrounded'}'."
        )
        details: dict[str, Any] = {
            "chunk_id": cid,
            "section_id": section_id,
            "quote": quote,
            "reason": reason,
        }
        if hallucinated_ids:
            details["hallucinated_ids"] = list(hallucinated_ids)
        super().__init__(msg, details=details)
        self.chunk_id = cid
        self.section_id = section_id
        self.quote = quote
        self.reason = reason
        self.hallucinated_ids = list(hallucinated_ids) if hallucinated_ids else [cid]


class UngroundedClaimError(VerificationError):
    """Raised when a factual claim fails entailment or lacks valid supporting premises."""

    def __init__(
        self,
        claim_id: str,
        claim_text: str = "",
        score: float = 0.0,
        entailment_score: float | None = None,
        threshold: float | None = None,
        message: str | None = None,
    ) -> None:
        final_score = entailment_score if entailment_score is not None else score
        thresh_str = f" threshold={threshold:.4f}" if threshold is not None else ""
        msg = message or (
            f"UngroundedClaimError for claim_id='{claim_id}': score={final_score:.4f}{thresh_str}. "
            f"Claim: '{claim_text}'"
        )
        super().__init__(
            msg,
            details={
                "claim_id": claim_id,
                "claim_text": claim_text,
                "score": final_score,
                "threshold": threshold,
            },
        )
        self.claim_id = claim_id
        self.claim_text = claim_text
        self.score = final_score
        self.entailment_score = final_score
        self.threshold = threshold


class NLIModelError(VerificationError):
    """Raised when NLI evaluation fails across both primary and fallback evaluators."""


class ClaimExtractionError(VerificationError):
    """Raised when proposition-level claim extraction or sentence splitting fails."""


class WorkflowError(LMeditorError):
    """Base exception for workflow state machine errors."""


class WorkflowDeadlockError(WorkflowError):
    """Raised when cyclic retry count exceeds maximum bounds or encounters state deadlock."""

    def __init__(
        self,
        current_node: str = "",
        state_summary: str = "",
        doc_id: str = "",
        retry_count: int = 0,
        max_retries: int = 0,
        message: str | None = None,
    ) -> None:
        msg = message or (
            f"WorkflowDeadlockError in current_node='{current_node}': {state_summary}. "
            f"doc_id='{doc_id}' retries={retry_count}/{max_retries}"
        )
        super().__init__(
            msg,
            details={
                "current_node": current_node,
                "state_summary": state_summary,
                "doc_id": doc_id,
                "retry_count": retry_count,
                "max_retries": max_retries,
            },
        )
        self.current_node = current_node
        self.state_summary = state_summary
        self.doc_id = doc_id
        self.retry_count = retry_count
        self.max_retries = max_retries


StateGraphDeadlockError = WorkflowDeadlockError


class InvalidStateTransitionError(WorkflowError):
    """Raised when an illegal or invalid transition is attempted in the state machine."""


class ValidationError(LMeditorError):
    """Raised when an input or boundary constraint fails validation."""

    def __init__(self, message: str, field: str | None = None, value: Any = None) -> None:
        details: dict[str, Any] = {}
        if field is not None:
            details["field"] = field
        if value is not None:
            details["value"] = repr(value)
        super().__init__(message, details=details)
        self.field = field
        self.value = value


class PerspectiveGenerationError(LMeditorError):
    """Raised when multi-perspective persona discovery fails."""


class DialogueError(LMeditorError):
    """Raised during conversational research loop execution."""


class OutlineGenerationError(LMeditorError):
    """Raised when outline generation fails."""


class DSPyAssertionError(LMeditorError, AssertionError):
    """Raised when a DSPy runtime assertion constraint fails during generation."""

    def __init__(
        self,
        message: str = "",
        details: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        LMeditorError.__init__(self, message, details=details, **kwargs)
        AssertionError.__init__(self, message)


class PassageBoundsError(DSPyAssertionError):
    """Raised when generated text references chunk IDs outside assigned leaf bounds."""

    def __init__(
        self,
        message: str = "",
        section_id: str | None = None,
        details: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        merged_details = dict(details or {})
        if section_id is not None:
            merged_details["section_id"] = section_id
        super().__init__(message, details=merged_details, section_id=section_id, **kwargs)
        self.section_id = section_id


class SourceLoadError(LMeditorError):
    """Raised when a source artifact cannot be loaded by its loader."""

    def __init__(self, kind: str, origin: str, reason: str) -> None:
        self.kind = kind
        self.origin = origin
        self.reason = reason
        super().__init__(f"Failed to load {kind} source '{origin}': {reason}")


class BibliographyResolutionError(LMeditorError):
    """Raised when a bibliography resolver fails to resolve an origin."""

    def __init__(self, resolver: str, origin: str, reason: str) -> None:
        self.resolver = resolver
        self.origin = origin
        self.reason = reason
        super().__init__(f"Resolver '{resolver}' failed for '{origin}': {reason}")
