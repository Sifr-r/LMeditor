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


class RevisionConflictError(LMeditorError):
    """A write's revision precondition does not match the stored revision."""

    def __init__(
        self, artifact_id: str, expected_revision: int, actual_revision: int, outcome: str
    ) -> None:
        super().__init__(
            f"Revision conflict for '{artifact_id}'.",
            details={
                "artifact_id": artifact_id,
                "expected_revision": expected_revision,
                "actual_revision": actual_revision,
                "outcome": outcome,
            },
        )


class StorageWriteError(LMeditorError):
    """A storage mutation failed, with a stable reason and affected path."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Could not write storage at '{path}': {reason}.",
            details={"path": path, "reason": reason},
        )


class IncompleteOperationError(LMeditorError):
    """An interrupted operation must be recovered before acknowledgement."""

    def __init__(
        self, operation_id: str, operation_kind: str, affected_paths: Sequence[str]
    ) -> None:
        super().__init__(
            f"Operation '{operation_id}' ({operation_kind}) is incomplete.",
            details={
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "affected_paths": list(affected_paths),
            },
        )


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


class ValidationError(LMeditorError, ValueError):
    """Raised when an input or boundary constraint fails validation.

    It is also a ``ValueError``, because that is what it is: a value that
    does not satisfy a constraint. The Python tier's own code raises plain
    ``ValueError`` for the same kind of refusal, and the Kotlin tier raises
    this one, so a caller that catches ``ValueError`` catches both.
    """

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


class WordProcessingError(LMeditorError):
    """Base exception for Word (OOXML) document processing errors."""


class WordParseError(WordProcessingError):
    """Raised when a .docx package cannot be parsed into the document model.

    The reader must surface *every* malformed-package condition through this
    error: a partially parsed document is worse than a refused one, because the
    caller cannot tell that content was dropped.
    """

    def __init__(self, origin: str, reason: str) -> None:
        self.origin = origin
        self.reason = reason
        super().__init__(
            f"Failed to parse Word document '{origin}': {reason}",
            details={"origin": origin, "reason": reason},
        )


class WordRenderError(WordProcessingError):
    """Raised when a document model cannot be rendered into a .docx package."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(
            f"Failed to render Word document: {reason}",
            details={"reason": reason},
        )


class BibliographyResolutionError(LMeditorError):
    """Raised when a bibliography resolver fails to resolve an origin."""

    def __init__(self, resolver: str, origin: str, reason: str) -> None:
        self.resolver = resolver
        self.origin = origin
        self.reason = reason
        super().__init__(f"Resolver '{resolver}' failed for '{origin}': {reason}")


class LMNotConfiguredError(LMeditorError):
    """Raised when a language-model operation is attempted with no LM loaded.

    LMeditor refuses to fabricate prose in place of a model call, so every
    generative path (drafting, outline synthesis, perspective generation)
    must fail loudly with this error when no language model is configured.

    Configure one with the ``LMEDITOR_LM_*`` environment variables; see
    ``lmeditor.llm.config.LMConfig``.
    """

    def __init__(self, operation: str = "", message: str | None = None) -> None:
        self.operation = operation
        msg = message or (
            f"No language model is configured, so '{operation or 'generation'}' cannot run. "
            "Set LMEDITOR_LM_PROVIDER, LMEDITOR_LM_MODEL and LMEDITOR_LM_API_KEY "
            "(or LMEDITOR_LM_BASE_URL for a local OpenAI-compatible server) and restart the app."
        )
        super().__init__(msg, details={"operation": operation})


class DraftingError(LMeditorError):
    """Raised when a section draft cannot be produced."""

    def __init__(self, section_id: str = "", reason: str = "", message: str | None = None) -> None:
        self.section_id = section_id
        self.reason = reason
        msg = message or f"Drafting failed for section '{section_id}': {reason}"
        super().__init__(msg, details={"section_id": section_id, "reason": reason})


class ExportError(LMeditorError):
    """Raised when a manuscript cannot be exported to the requested format."""

    def __init__(self, doc_id: str = "", reason: str = "", message: str | None = None) -> None:
        self.doc_id = doc_id
        self.reason = reason
        msg = message or f"Export failed for document '{doc_id}': {reason}"
        super().__init__(msg, details={"doc_id": doc_id, "reason": reason})


class SourceUploadTooLargeError(LMeditorError):
    """Raised when an uploaded artifact exceeds the accepted size.

    The upload is refused while it is being read, so an oversized file is
    never fully buffered in memory.
    """

    def __init__(self, message: str, *, limit_bytes: int = 0) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(message, details={"limit_bytes": limit_bytes})


class EditingError(LMeditorError):
    """Base exception for targeted-edit domain errors."""


class InlineEditRejectedError(EditingError):
    """Raised when a proposed revision cannot be accepted.

    The rejection carries the reason, so the caller can show the user why
    the text was left alone instead of silently keeping a worse sentence.
    """

    def __init__(self, section_id: str = "", reason: str = "", message: str | None = None) -> None:
        self.section_id = section_id
        self.reason = reason
        msg = message or f"Inline edit rejected for section '{section_id}': {reason}"
        super().__init__(msg, details={"section_id": section_id, "reason": reason})


class EditTargetNotFoundError(EditingError):
    """Raised when the section or paragraph an edit names does not exist."""

    def __init__(self, section_id: str = "", paragraph_index: int | None = None, message: str | None = None) -> None:
        self.section_id = section_id
        self.paragraph_index = paragraph_index
        msg = message or (
            f"Edit target not found: section='{section_id}' paragraph={paragraph_index}"
        )
        super().__init__(
            msg,
            details={"section_id": section_id, "paragraph_index": paragraph_index},
        )


class FactCheckError(LMeditorError):
    """Base exception for fact-check domain errors."""


class WebEvidenceError(FactCheckError):
    """Raised when web evidence cannot be retrieved.

    A web failure never voids a check that the corpus can answer, so this
    error is caught at the evidence boundary and reported as a note on the
    report rather than propagated.
    """

    def __init__(self, reason: str = "", query: str = "", message: str | None = None) -> None:
        self.reason = reason
        self.query = query
        msg = message or f"Web evidence unavailable for query '{query}': {reason}"
        super().__init__(msg, details={"reason": reason, "query": query})


class LibraryError(LMeditorError):
    """Base exception for the book library domain."""


class BookNotFoundError(LibraryError):
    """Raised when a book id does not exist in the library."""

    def __init__(self, book_id: str = "", message: str | None = None) -> None:
        self.book_id = book_id
        msg = message or f"BookNotFoundError: book_id='{book_id}' was not found."
        super().__init__(msg, details={"book_id": book_id})


class SectionNotFoundError(LibraryError):
    """Raised when a section id does not exist inside a book."""

    def __init__(self, book_id: str = "", section_id: str = "", message: str | None = None) -> None:
        self.book_id = book_id
        self.section_id = section_id
        msg = message or (
            f"SectionNotFoundError: section_id='{section_id}' was not found in book '{book_id}'."
        )
        super().__init__(msg, details={"book_id": book_id, "section_id": section_id})


class VaultError(LMeditorError):
    """Base exception for the vault domain.

    A vault is a folder of markdown files on the operator's disk, and every
    rule that decides anything about one -- the path safety, the
    frontmatter, the link and tag grammar, the backlink index and the
    search order -- belongs to the Kotlin tier. This error is how the
    tier's refusal reaches the Python side: the message says what was
    wrong, and the details carry the context that produced it.
    """

    def __init__(
        self,
        message: str = "",
        reason: str = "",
        note_id: str = "",
        root: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})
        if reason:
            merged.setdefault("reason", reason)
        if note_id:
            merged.setdefault("note_id", note_id)
        if root:
            merged.setdefault("root", root)
        self.reason = reason
        self.note_id = note_id
        self.root = root
        super().__init__(message, details=merged)


class VaultNotFoundError(VaultError):
    """Raised when a note the request names is not in the vault."""


class NoVaultOpenError(VaultError):
    """Raised when a request arrives while no vault is open.

    The client has to open a vault before it can ask anything of one, so a
    request that arrives without an open vault conflicts with the server's
    state rather than carrying a bad input.
    """


class NotebookError(LMeditorError):
    """Base exception for the notebook domain.

    A notebook is a named collection of sources and notes on the operator's
    disk, and every rule that decides anything about one -- the identifiers
    that are safe to write, the shape of the manifest, the idempotence of a
    write, the membership of a source or a note -- belongs to the Kotlin tier.
    This error is how the tier's refusal reaches the Python side: the message
    says what was wrong, and the details carry the context that produced it.
    """

    def __init__(
        self,
        message: str = "",
        reason: str = "",
        notebook_id: str = "",
        root: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})
        if reason:
            merged.setdefault("reason", reason)
        if notebook_id:
            merged.setdefault("notebook_id", notebook_id)
        if root:
            merged.setdefault("root", root)
        self.reason = reason
        self.notebook_id = notebook_id
        self.root = root
        super().__init__(message, details=merged)


class NotebookNotFoundError(NotebookError):
    """Raised when the notebook the request names is not in the folder."""


class NotebookAskError(NotebookError):
    """Raised when a question cannot be asked of a notebook.

    An ask that cannot be made is a refusal of the request -- an empty
    question, a notebook that is not there -- and never a made-up answer.
    """
