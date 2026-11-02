"""Server-Sent Events (SSE) and REST gateway routes for LMeditor."""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from lmeditor import notebooks
from lmeditor import vault
from lmeditor.bridge.errors import BridgeUnavailableError
from lmeditor.core.exceptions import (
    BookNotFoundError,
    ExportError,
    InvalidStateTransitionError,
    NotebookError,
    NotebookNotFoundError,
    NoVaultOpenError,
    SectionNotFoundError,
    SourceLoadError,
    SourceUploadTooLargeError,
    VaultError,
    VaultNotFoundError,
    WordParseError,
)
from lmeditor.core.schemas import (
    BookSectionEditRequest,
    BookSectionSaveRequest,
    BookSectionSaveResult,
    FactCheckRequest,
    HITLReviewPayload,
    InlineEditRequest,
    OutlineNodeInput,
    SectionDraft,
    SectionSaveRequest,
    SectionSaveResult,
    SourceChunk,
    VerificationScorecard,
    WorkflowEvent,
)
from lmeditor.core.sections import merge_operator_text
from lmeditor.core.types import EventType, SourceKind, WorkflowStep
from lmeditor.ingestion.url_policy import UnsafeSourceError, assert_public_url, validate_source_id
from lmeditor.server.library import (
    book_index,
    book_passages,
    book_store,
    invalidate_book_index,
)
from lmeditor.server.notebooks import open_notebooks_root, require_notebooks_root
from lmeditor.server.routes_helpers import (
    safe_detail,
    safe_detail_value,
    safe_message,
    safe_origin_reason,
    tier_unavailable_detail,
)
from lmeditor.server.session import (
    ActiveSessionExistsError,
    SubscriberOverrun,
    WorkflowSession,
    scorecard_payload,
    session_manager,
)
from lmeditor.server.sources import StoredSource, source_library
from lmeditor.server.vault import open_active_vault, require_vault_root

router = APIRouter(prefix="/api/v1", tags=["Workflow & Streaming"])

logger = logging.getLogger(__name__)

#: Reason used when a suspended run carries no ``hitl_reason``.
DEFAULT_HITL_REASON = "Human review is required before the manuscript can continue."

#: Key LangGraph uses for an interrupt chunk in its result mapping.
_INTERRUPT_KEY = "__interrupt__"

#: Source kinds that describe an on-disk artifact and therefore require an upload.
FILESYSTEM_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        SourceKind.PDF.value,
        SourceKind.DOCX.value,
        SourceKind.MARKDOWN.value,
        SourceKind.HTML.value,
        SourceKind.TEXT.value,
    }
)

#: The most source chunks one generation request may carry.
#:
#: A generation request is paid for twice over: once for the material it
#: carries, and once for every revision loop it asks for. These three
#: numbers are the server's answer to a request that would spend without
#: limit.
MAX_INLINE_SOURCE_CHUNKS: int = 64

#: The most characters of source text one generation request may carry.
MAX_INLINE_SOURCE_CHARS: int = 400_000

#: The most cyclic revision loops one generation request may ask for.
MAX_REQUEST_RETRIES: int = 5

#: The most characters of the operator's own text one request may carry.
#:
#: The text of a question, of a passage to check or of an instruction to
#: follow goes into the prompt of a paid call, so it is bounded as well.
MAX_REQUEST_TEXT_CHARS: int = 200_000


class GenerateDocumentRequest(BaseModel):
    """Payload for triggering an end-to-end manuscript generation workflow.

    The material to write from can be supplied either inline
    (``source_chunks``) or by reference to sources the server already holds
    (``source_ids``). Referencing them by id is what makes the source library
    useful: a client can ingest once and then generate any number of
    documents without shipping the same chunks again.
    """

    topic: str = Field(..., min_length=1, description="Manuscript subject or thesis statement")
    source_chunks: list[SourceChunk] = Field(
        default_factory=list,
        max_length=MAX_INLINE_SOURCE_CHUNKS,
        description="Candidate ground truth source chunks",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        description="Ids of sources the server already holds; their chunks are used",
    )
    doc_id: str | None = Field(default=None, description="Optional custom document identifier")
    max_retries: int = Field(default=3, ge=1, le=MAX_REQUEST_RETRIES, description="Maximum cyclic revision loops before HITL")
    threshold_f_beta: float = Field(default=0.85, ge=0.0, le=1.0, description="Minimum F_0.5 verification score required")
    outline: list[OutlineNodeInput] | None = Field(
        default=None,
        description=(
            "The outline the manuscript is written to, when the caller supplies one. "
            "This is the operator's steer: the sections named here are the sections "
            "the draft fills. The passages each section is grounded in are still "
            "assigned from the corpus, so a supplied outline decides what the "
            "document covers and never what it claims."
        ),
    )

    @model_validator(mode="after")
    def _require_material(self) -> "GenerateDocumentRequest":
        """Reject a request that names no material to write from."""
        if not self.source_chunks and not self.source_ids:
            raise ValueError(
                "A generation request needs source material: supply 'source_chunks' "
                "or the 'source_ids' of sources the server already holds."
            )
        return self

    @model_validator(mode="after")
    def _require_bounded_material(self) -> "GenerateDocumentRequest":
        """Reject a request that carries more material than one run may read.

        The request is refused, never trimmed: a run that quietly used half
        of what it was given would answer with a document about half the
        corpus, and the caller would never know.
        """
        characters = sum(len(chunk.text) for chunk in self.source_chunks)
        if characters > MAX_INLINE_SOURCE_CHARS:
            raise ValueError(
                f"The request carries {characters} characters of source material; "
                f"the limit is {MAX_INLINE_SOURCE_CHARS}. Send fewer chunks, or "
                "ingest the material as a source and name it in 'source_ids'."
            )
        return self


class GenerateDocumentResponse(BaseModel):
    """Initial response acknowledging workflow instantiation."""

    doc_id: str
    status: str
    stream_url: str


class SourceIngestRequest(BaseModel):
    """JSON fallback when the caller does not upload a file."""

    source_id: str = Field(..., min_length=1)
    kind: str = Field(..., description="One of SourceKind values")
    origin: str = Field(..., min_length=1, description="Absolute http(s) URL")
    title: str = Field(..., min_length=1)
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pdf_metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Bibliographic metadata for the source, overriding whatever the "
            "document itself declares (title, author, DOI, ...)"
        ),
    )


class SourceIngestResponse(BaseModel):
    """Response payload from the source ingestion endpoint."""

    source: dict[str, Any]
    chunks: list[dict[str, Any]]
    omissions: list[str] = Field(
        default_factory=list,
        description="Content the loader could not preserve (images, comments, endnotes, ...)",
    )


class WorkflowStatusResponse(BaseModel):
    """Snapshot status of an active or completed workflow."""

    doc_id: str
    topic: str
    current_step: WorkflowStep
    status: str = Field(..., description="running | awaiting_human_review | completed | failed | rejected")
    hitl_pending: bool
    is_completed: bool
    scorecard: dict[str, Any] | None = None
    error: str | None = None
    rejection_reason: str | None = None


# ---------------------------------------------------------------------------
# Ingestion trust boundary (AUDIT finding 2)
# ---------------------------------------------------------------------------


def _validated_source_id(source_id: str) -> str:
    """Return a path-safe ``source_id`` or refuse the request with 400."""
    try:
        return validate_source_id(source_id)
    except UnsafeSourceError as exc:
        # The tier's refusal may name the path it resolved the id to, which
        # is the server's own disk: the client is told the id was refused,
        # and the reason goes to the log.
        logger.warning("The source id was refused: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_detail(exc, "Invalid source_id"),
        ) from exc


def _assert_fetchable_url(origin: str, kind: str) -> str:
    """Apply the destination policy at the API boundary, before any fetch.

    Raises the domain error :class:`SourceLoadError` rather than letting the
    ``ValueError`` subclass :class:`UnsafeSourceError` escape as a 500.
    """
    try:
        return assert_public_url(origin)
    except UnsafeSourceError as exc:
        raise SourceLoadError(kind, origin, str(exc)) from exc


def _rejected_origin(exc: SourceLoadError) -> HTTPException:
    """Map a refused destination to a client error.

    The reason names the addresses the host resolved to and the address it
    actually connected to, and those are the server's own: the client is
    told the origin was refused and why it was refused, without the
    addresses, and the whole of it goes to the log.
    """
    logger.warning("The origin was refused: %s", exc)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Rejected origin: {safe_origin_reason(exc.reason)}",
    )


def _remote_ingest_fields(
    kind: str | None,
    origin: str | None,
    title: str,
) -> tuple[str, str, str]:
    """Validate the no-upload form of ``POST /sources/ingest``.

    A filesystem ``origin`` that did not come from
    :func:`lmeditor.server.routes_helpers.persist_source_to_disk` is refused:
    a client-supplied path is never an artifact this process stored, so reading
    it would expose arbitrary server files. Only a remote source may be
    ingested here, and its destination is checked before the fetch.
    """
    if kind is None or origin is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Provide a `file` upload, or `kind=url` with an absolute http(s) "
                "`origin` (see POST /api/v1/sources/url)."
            ),
        )
    if kind in FILESYSTEM_SOURCE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"kind={kind!r} requires a `file` upload: filesystem origins are only "
                "accepted when produced by this server from an upload."
            ),
        )
    if kind != SourceKind.URL.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported kind {kind!r} for this endpoint.",
        )
    try:
        checked = _assert_fetchable_url(origin, kind)
    except SourceLoadError as exc:
        raise _rejected_origin(exc) from exc
    return kind, checked, title


def _ingest_source(
    *,
    source_id: str,
    kind: str,
    origin: str,
    title: str,
    doi: str | None,
    arxiv_id: str | None,
    pmid: str | None,
    pdf_metadata: dict[str, Any] | None = None,
) -> SourceIngestResponse:
    """Run the ingestion pipeline, mapping every boundary rejection to 4xx."""
    from lmeditor.ingestion.service import IngestionService

    try:
        source_kind = SourceKind(kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported source kind {kind!r}.",
        ) from exc

    svc = IngestionService.default()
    try:
        result = svc.ingest(
            source_id=source_id,
            kind=source_kind,
            origin=origin,
            title=title,
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
            pdf_metadata=pdf_metadata,
        )
    except SourceLoadError as exc:
        # The origin of a filesystem source is the server's own path, and the
        # reason may carry one too: the client is told the source could not
        # be loaded, and the whole of it goes to the log.
        logger.warning("The source could not be loaded: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=safe_detail(exc, "Source could not be loaded"),
        ) from exc
    except UnsafeSourceError as exc:
        logger.warning("The source was refused: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_detail(exc, "Rejected source"),
        ) from exc
    except ValueError as exc:
        # Pydantic model validation on the submitted metadata.
        logger.warning("The source payload was refused: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_detail(exc, "Invalid source payload"),
        ) from exc

    # Remember what was ingested so a later generation request can refer to
    # this source by id instead of shipping its chunks back to the server.
    source_library.add(
        StoredSource.from_source(
            result.source,
            result.chunks,
            record=result.source.bibliography,
            omissions=result.omissions,
        )
    )

    return SourceIngestResponse(
        source=result.source.model_dump(mode="json"),
        chunks=[c.model_dump(mode="json") for c in result.chunks],
        omissions=list(result.omissions),
    )


# ---------------------------------------------------------------------------
# Workflow worker (AUDIT finding 7)
# ---------------------------------------------------------------------------


def _is_suspended(result: dict[str, Any]) -> bool:
    """Return True when the graph stopped at the human-in-the-loop gate.

    ``__interrupt__`` is LangGraph's own signal that the run stopped inside a
    node; a rebuilt graph handed ``Command(resume=...)`` on a thread it does not
    share re-enters the gate and returns exactly that, so it is checked here to
    keep a re-suspended run from ever being reported as a completion.
    """
    from lmeditor.workflow.graph import AWAITING_HUMAN_REVIEW_STATUS

    if _INTERRUPT_KEY in result:
        return True
    if result.get("status") == AWAITING_HUMAN_REVIEW_STATUS:
        return True
    return bool(result.get("requires_hitl"))


def _sections_dump(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a graph result into a JSON-serializable list of sections."""
    sections: Any = result.get("sections")
    if sections is None:
        drafts: Any = result.get("section_drafts")
        if isinstance(drafts, dict):
            sections = list(drafts.values())
        elif isinstance(drafts, list):
            sections = drafts
        else:
            sections = []
    dumped: list[dict[str, Any]] = []
    for section in sections:
        if isinstance(section, dict):
            dumped.append(section)
        elif hasattr(section, "model_dump"):
            dumped.append(section.model_dump())
    return dumped


def _apply_scorecard(session: WorkflowSession, result: dict[str, Any]) -> None:
    """Adopt the document-level verification scorecard produced by the run.

    The graph returns the *audited sections*, not a single scorecard, so the
    aggregate is computed here. Previously this only adopted a scorecard the
    graph never produced, which left the client with ``null`` and no truthful
    measurement to display.
    """
    from lmeditor.workflow.graph import build_document_scorecard

    card = result.get("scorecard")
    if isinstance(card, VerificationScorecard):
        session.scorecard = card.model_dump(mode="json")
        return
    if isinstance(card, dict) and card:
        session.scorecard = dict(card)
        return

    drafts = result.get("section_drafts")
    if isinstance(drafts, dict):
        sections = list(drafts.values())
    elif isinstance(drafts, list):
        sections = list(drafts)
    else:
        sections = []
    if sections:
        session.scorecard = build_document_scorecard(sections)


async def _invoke_workflow(session: WorkflowSession, request: GenerateDocumentRequest) -> dict[str, Any]:
    """Run the graph for the first time, publishing progress through the session."""
    from lmeditor.workflow.graph import run_workflow

    return await run_workflow(
        doc_id=session.doc_id,
        topic=request.topic,
        source_chunks=request.source_chunks,
        max_retries=request.max_retries,
        threshold_f_beta=request.threshold_f_beta,
        session=session,
        requested_outline=request.outline,
    )


async def _resume_suspended_workflow(
    session: WorkflowSession, decision: HITLReviewPayload
) -> dict[str, Any]:
    """Resume the run suspended for ``session.doc_id`` with the operator decision.

    ``workflow.graph`` retains the compiled graph (and therefore the checkpointer
    that holds the interrupt) under the ``thread_id``, which is the ``doc_id``,
    for exactly as long as the run can be resumed. Raises
    :class:`~lmeditor.core.exceptions.InvalidStateTransitionError` when no such
    run is retained.
    """
    from lmeditor.workflow.graph import resume_workflow

    return await resume_workflow(
        thread_id=session.doc_id,
        decision=decision.model_dump(mode="json"),
        session=session,
    )


def _release_retained_run(doc_id: str) -> None:
    """Drop the checkpointer of a run that can no longer be resumed.

    A no-op once the graph has evicted the entry itself (it does so on every
    terminal state), which is exactly the case for an abandoned document.
    """
    from lmeditor.workflow.graph import release_workflow

    release_workflow(doc_id)


async def _await_review_decision(
    session: WorkflowSession, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Open a review, wait for the operator decision, and resume the run.

    Returns the post-resume state — which may itself be a fresh suspension, so
    the caller loops — or ``None`` when the session has already recorded a
    terminal outcome (an operator rejection, or a run that could not be
    resumed).
    """
    reason = str(result.get("hitl_reason") or DEFAULT_HITL_REASON)
    session.open_hitl_review(reason)

    if session.hitl_requests_published == 0:
        # `workflow.graph` publishes this frame itself when it interrupts; publish
        # it here only if it did not, so one review yields one hitl_request.
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.HITL_REQUEST,
                step=WorkflowStep.HITL_GATE,
                doc_id=session.doc_id,
                data={
                    "step": WorkflowStep.HITL_GATE.value,
                    "reason": reason,
                    "requires_hitl": True,
                },
            )
        )

    decision = await session.await_hitl_decision()

    try:
        resumed = await _resume_suspended_workflow(session, decision)
    except InvalidStateTransitionError as exc:
        # Nothing is retained for this document, so there is no run to continue.
        if decision.approved:
            # The reason may carry the server's own layout; the client is
            # told which operation failed, and the whole of it goes to the log.
            logger.warning("The suspended workflow could not be resumed: %s", exc)
            await session.fail(safe_detail(exc, "cannot resume the suspended workflow"))
        else:
            # The verdict is still the outcome even though the run is already gone.
            await session.terminate_rejected(decision.feedback or "no feedback supplied")
        return None

    if not decision.approved:
        # The operator rejected: the graph ended at the gate, so the session is
        # terminal without ever being reported as a completion.
        await session.terminate_rejected(decision.feedback or "no feedback supplied")
        return None

    return resumed


async def _run_workflow_worker(session: WorkflowSession, request: GenerateDocumentRequest) -> None:
    """Background worker executing the LangGraph workflow and streaming events.

    ``session.complete`` is reached only when the graph actually finished. A run
    that suspends for review publishes ``hitl_request`` (the graph does this
    itself), waits for the operator decision, and resumes on the same
    ``thread_id``; each further suspension opens a new review. A rejection is
    terminal and is never reported as a completion.
    """
    try:
        result = await _invoke_workflow(session, request)

        while _is_suspended(result):
            resumed = await _await_review_decision(session, result)
            if resumed is None:
                return  # terminal outcome already published
            result = resumed

        _apply_scorecard(session, result)
        await session.complete(sections=_sections_dump(result))

    except Exception as exc:
        # The run died; nothing can be resumed from it, so free its checkpointer.
        # The reason may name the server's own disk or the JVM's internals:
        # the client is told what failed, and the whole of it goes to the log.
        logger.error("The workflow run failed: %s", exc, exc_info=exc)
        _release_retained_run(session.doc_id)
        await session.fail(safe_detail(exc))


def _refuse_oversized_text(*texts: str) -> None:
    """Refuse a request that carries more text than one paid call may read.

    The text of a question, of a passage to check or of an instruction to
    follow goes into the prompt of the call, so it is bounded like the
    corpus is. The request is refused, never trimmed.
    """
    total = sum(len(text) for text in texts)
    if total > MAX_REQUEST_TEXT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"The request carries {total} characters of text; the limit is "
                f"{MAX_REQUEST_TEXT_CHARS}. Send less text, or point the request "
                "at the passage it is about."
            ),
        )


def _refuse_oversized_material(chunks: list[SourceChunk]) -> None:
    """Refuse material that costs more than one request is allowed to cost.

    The material is the whole of what the run will read: the chunks sent
    inline and the chunks of the sources the request names. The request is
    refused, never trimmed, so a caller is never handed a document written
    from half of the corpus it asked for.
    """
    if len(chunks) > MAX_INLINE_SOURCE_CHUNKS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"The request names {len(chunks)} source chunks; the limit is "
                f"{MAX_INLINE_SOURCE_CHUNKS}. Send fewer chunks, or ingest the "
                "material as a source and name it in 'source_ids'."
            ),
        )
    characters = sum(len(chunk.text) for chunk in chunks)
    if characters > MAX_INLINE_SOURCE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"The request carries {characters} characters of source material; "
                f"the limit is {MAX_INLINE_SOURCE_CHARS}. Send fewer chunks, or "
                "ingest the material as a source and name it in 'source_ids'."
            ),
        )


@router.post(
    "/documents/generate",
    response_model=GenerateDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger new manuscript generation workflow",
)
async def generate_document(request: GenerateDocumentRequest) -> GenerateDocumentResponse:
    """Initialize a LangGraph manuscript generation workflow and return stream URL."""
    doc_id = request.doc_id or f"doc_{uuid.uuid4().hex[:12]}"

    # Material named by id is resolved from the server's source library; the
    # inline chunks, when given, are used as they are. This happens before the
    # session is created so a request that names no material leaves no live
    # session behind.
    source_chunks = list(request.source_chunks)
    if request.source_ids:
        source_chunks.extend(source_library.chunks_for(request.source_ids))
    if not source_chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "None of the requested sources are held by this server: "
                f"{request.source_ids!r}. Ingest them first, or send the chunks inline."
            ),
        )
    _refuse_oversized_material(source_chunks)
    request = request.model_copy(update={"source_chunks": source_chunks})

    try:
        session = await session_manager.create_session(doc_id=doc_id, topic=request.topic)
        # The document's own verification threshold is remembered so that a
        # later measurement of the operator's edited text is judged by the
        # same bar the document was written to, rather than by a default that
        # happens to be lying around.
        session.threshold_f_beta = request.threshold_f_beta
    except ActiveSessionExistsError as exc:
        # A live session already owns this doc_id. Replacing it would orphan
        # the running worker's session object, so the duplicate is refused
        # rather than silently superseding the in-flight run.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document '{doc_id}' already has an active workflow "
                f"(status: {exc.status})."
            ),
        ) from exc

    # Launch execution worker asynchronously
    asyncio.create_task(_run_workflow_worker(session, request))

    return GenerateDocumentResponse(
        doc_id=doc_id,
        status="started",
        stream_url=f"/api/v1/stream/{doc_id}",
    )


@router.get("/sources", summary="List the sources this server has ingested")
async def list_sources() -> dict[str, Any]:
    """Return the server's source library."""
    return {
        "sources": [stored.to_summary() for stored in source_library.list()],
        "count": len(source_library),
    }


@router.get(
    "/documents/{doc_id}",
    summary="Fetch the authoritative manuscript for a document",
)
async def get_document(doc_id: str) -> dict[str, Any]:
    """Return the manuscript, its sections and its verification scorecard.

    The client builds its editor view from this payload rather than from the
    concatenation of streaming deltas, so the document it shows is the one the
    backend actually produced.
    """
    session = await session_manager.get_session(doc_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No document '{doc_id}' is known to this server.",
        )

    card = session.scorecard
    if isinstance(card, VerificationScorecard):
        card_payload: dict[str, Any] = card.model_dump(mode="json")
    elif isinstance(card, dict):
        card_payload = dict(card)
    else:
        card_payload = {}

    return {
        "doc_id": doc_id,
        "topic": session.topic,
        "status": session.status,
        "is_completed": session.is_completed,
        "hitl_pending": session.hitl_pending,
        "error": session.error,
        "sections": session.final_sections,
        "scorecard": card_payload,
        "verified": bool(card_payload.get("all_passed")) if card_payload else False,
        "unverified_section_ids": list(card_payload.get("unverified_section_ids", [])),
    }


@router.get(
    "/documents/{doc_id}/export.docx",
    summary="Export the manuscript as a Microsoft Word document",
    response_class=Response,
)
async def export_document_docx(doc_id: str) -> Response:
    """Render the manuscript to a ``.docx`` package and return it.

    This is the only route that reaches the Word writer. Without it the
    OOXML writer was a library no user could reach: the document could be
    generated and verified, but never handed back in the format the tool
    promises to produce.
    """
    session = await session_manager.get_session(doc_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No document '{doc_id}' is known to this server.",
        )
    if not session.final_sections:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document '{doc_id}' has no manuscript to export yet "
                f"(status: {session.status})."
            ),
        )

    from lmeditor.core.schemas import DocumentDraft, SectionDraft
    from lmeditor.word.manuscript import build_manuscript_document
    from lmeditor.word.writer import WordWriter

    sections: list[SectionDraft] = []
    for raw in session.final_sections:
        try:
            sections.append(SectionDraft.model_validate(raw))
        except Exception as exc:  # noqa: BLE001 - one bad section must not 500
            logger.warning("Skipping unserialisable section in export: %s", exc)

    if not sections:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document '{doc_id}' has no exportable sections.",
        )

    draft = DocumentDraft(
        document_id=doc_id,
        title=session.topic or doc_id,
        sections=sections,
    )

    try:
        word_document = build_manuscript_document(draft)
        payload = WordWriter().write(word_document)
    except ExportError:
        raise
    except Exception as exc:  # noqa: BLE001 - map writer failures to 422
        raise ExportError(doc_id=doc_id, reason=str(exc)) from exc

    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{doc_id}.docx"',
        },
    )


@router.get(
    "/library/books/{book_id}/export.docx",
    summary="Export a book as a Microsoft Word document",
    response_class=Response,
)
async def export_book_docx(book_id: str) -> Response:
    """Render a book to a ``.docx`` package and return it.

    The book is read one section at a time and assembled through the same
    manuscript bridge a document uses, so the file carries the headings, the
    lists, the character formatting and the footnotes the editor's edits
    produced. Without this route a book could be edited but never handed back:
    the work would be trapped in the library.
    """
    from lmeditor.library import build_book_document
    from lmeditor.word.writer import WordWriter

    store = book_store()
    try:
        word_document = build_book_document(store, book_id)
    except BookNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except SectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc

    try:
        payload = WordWriter().write(word_document)
    except ExportError:
        raise
    except Exception as exc:  # noqa: BLE001 - map writer failures to 422
        raise ExportError(doc_id=book_id, reason=str(exc)) from exc

    filename = f"{book_id}.docx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post(
    "/sources/ingest",
    response_model=SourceIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a source artifact (file upload or URL)",
)
async def ingest_source(
    source_id: str = Form(...),
    title: str = Form(...),
    kind: str | None = Form(default=None),
    origin: str | None = Form(default=None),
    doi: str | None = Form(default=None),
    arxiv_id: str | None = Form(default=None),
    pmid: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> SourceIngestResponse:
    """Ingest a single source via file upload (multipart/form-data only).

    A filesystem ``origin`` is accepted **only** when it is the path this
    process just wrote from an uploaded ``file``; a client-supplied path is
    refused. For remote ingestion use ``POST /api/v1/sources/url`` (or send
    ``kind=url`` with an absolute http(s) ``origin`` here, which is checked
    before anything is fetched).
    """
    from lmeditor.server.routes_helpers import persist_source_to_disk

    validated_id = _validated_source_id(source_id)

    saved_path: Path | None = None
    resolved_kind: str
    resolved_origin: str
    resolved_title: str

    # AUDIT finding 15: the ingestion pipeline does disk writes, PDF/DOCX parsing
    # and a network fetch, all synchronous. On the event loop it stalls status,
    # SSE, health and HITL approval for every other client, so it runs on a
    # worker thread. Domain rejections raised here still reach the handler,
    # because the exception is re-raised in this coroutine.
    #
    # The ``finally`` clause removes the persisted upload whatever happens, so a
    # request rejected after the file was written does not leave it behind.
    try:
        if file is not None:
            try:
                saved = await persist_source_to_disk(file, source_id=validated_id)
            except UnsafeSourceError as exc:
                # The refusal may name the absolute path the upload resolved
                # to, which is the server's own disk: the client is told the
                # upload was rejected, and the reason goes to the log.
                logger.warning("The upload was rejected: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=safe_detail(exc, "Rejected upload"),
                ) from exc
            except SourceUploadTooLargeError as exc:
                # The refusal names the upload and the limit it passed. The
                # client is told the upload was too large, and the whole of
                # it goes to the log, so an operator can see which upload
                # it was.
                logger.warning("The upload was too large: %s", exc, exc_info=exc)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=safe_detail(exc),
                ) from exc
            saved_path = saved.path
            resolved_kind = _kind_from_extension(saved.path).value
            resolved_origin = str(saved.path)
            resolved_title = title or saved.path.name
        else:
            resolved_kind, resolved_origin, resolved_title = _remote_ingest_fields(
                kind, origin, title
            )

        return await asyncio.to_thread(
            _ingest_source,
            source_id=validated_id,
            kind=resolved_kind,
            origin=resolved_origin,
            title=resolved_title,
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
        )
    finally:
        # The uploaded artifact has served its purpose: its text and chunks
        # are in the source library. Leaving it on disk would grow the upload
        # directory without bound, one copy per upload, forever.
        if saved_path is not None:
            await asyncio.to_thread(_remove_persisted_source, validated_id)


def _remove_persisted_source(source_id: str) -> None:
    """Delete a persisted upload and the directory that held it.

    The whole per-source directory goes, not just the file: an empty directory
    left behind per upload is the same unbounded growth as the file was, one
    directory per request instead of one file. The store's own removal
    prunes the directories it empties.
    """
    try:
        from lmeditor.ingestion.source_store import SourceStore

        SourceStore.default().remove(source_id)
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail the request
        logger.warning("Could not remove persisted source '%s': %s", source_id, exc)


@router.post(
    "/sources/url",
    response_model=SourceIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a URL-referenced source via JSON body",
)
async def ingest_url_source(payload: SourceIngestRequest) -> SourceIngestResponse:
    """Ingest a single source referenced by URL using a JSON body.

    The body must match :class:`SourceIngestRequest`. Only ``kind="url"`` with
    an absolute http(s) ``origin`` is accepted, and the destination is validated
    before the fetch is attempted.
    """
    validated_id = _validated_source_id(payload.source_id)

    if payload.kind != SourceKind.URL.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"POST /api/v1/sources/url only accepts kind={SourceKind.URL.value!r}, "
                f"got {payload.kind!r}. Filesystem sources require a file upload on "
                "POST /api/v1/sources/ingest."
            ),
        )

    try:
        checked_origin = _assert_fetchable_url(payload.origin, SourceKind.URL.value)
    except SourceLoadError as exc:
        raise _rejected_origin(exc) from exc

    # A URL fetch carries a multi-second timeout, so it is offloaded for the
    # same reason as the upload path above (AUDIT finding 15).
    return await asyncio.to_thread(
        _ingest_source,
        source_id=validated_id,
        kind=SourceKind.URL.value,
        origin=checked_origin,
        title=payload.title,
        doi=payload.doi,
        arxiv_id=payload.arxiv_id,
        pmid=payload.pmid,
        pdf_metadata=payload.pdf_metadata,
    )


def _kind_from_extension(path: Path) -> SourceKind:
    """Map a file extension to the corresponding ``SourceKind``.

    A legacy binary ``.doc`` is a different format from a ``.docx`` -- an
    OLE2 compound file rather than an OOXML package -- and the reader only
    speaks OOXML. Mapping it to ``DOCX`` would produce a confusing parse
    error, so it is refused here with the reason the operator needs.

    The wording comes from :mod:`lmeditor.word.formats`, which is also what
    the reader uses when it meets the same bytes, so the route and the reader
    can never disagree about what a file is or what to do about it.
    """
    from lmeditor.word.formats import LEGACY_DOC_REASON

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return SourceKind.PDF
    if suffix == ".doc":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"{LEGACY_DOC_REASON}, and upload that instead",
        )
    if suffix == ".docx":
        return SourceKind.DOCX
    if suffix in {".md", ".markdown"}:
        return SourceKind.MARKDOWN
    if suffix in {".html", ".htm"}:
        return SourceKind.HTML
    return SourceKind.TEXT


@router.get(
    "/stream/{doc_id}",
    response_class=StreamingResponse,
    summary="Stream real-time workflow events via SSE",
)
async def stream_workflow_events(
    doc_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Connect to Server-Sent Events stream for a specific document workflow.

    Each frame is ``id: <n>`` plus ``event: <event_type>`` and a ``data:``
    JSON envelope of ``{event_type, step, doc_id, data}``. The terminal
    frames are exactly ``workflow_complete`` and ``workflow_error``, after
    which the stream closes.

    Every request gets its own subscription, so concurrent streams on one
    document each receive the full frame sequence instead of dividing it.

    A client that reconnects names the last frame it received in
    ``Last-Event-ID``, and is given the frames after it. A client that was
    away longer than the session remembers is told what it missed with a
    ``gap`` frame, rather than being left to assume it has everything.

    A subscriber that falls behind the session's history cannot be given the
    frames it has not read, because they have left it. Its stream ends with
    an ``overflow`` control frame instead, whose ``data:`` names what was
    missed, so the client knows the run is still going and reconnects.
    """
    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )

    resume_from: int | None = None
    if last_event_id is not None:
        try:
            resume_from = int(last_event_id)
        except ValueError as exc:
            # A header that is not a frame id is refused rather than ignored:
            # serving the wrong frames to a client that believes it has
            # resumed is worse than refusing it.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID is not a frame id.",
            ) from exc

    async def event_generator() -> AsyncGenerator[str, None]:
        subscription = await session.subscribe(resume_from)
        try:
            if resume_from is not None:
                gap = session.gap_before(resume_from)
                if gap is not None:
                    # The gap frame carries no id: a client's last-event-id
                    # buffer advances only on a frame that has one, so the
                    # client stays able to resume from where it was.
                    payload = json.dumps(gap)
                    yield f"event: gap\ndata: {payload}\n\n"
            while True:
                try:
                    frame = await session.next_frame(subscription)
                except SubscriberOverrun as overrun:
                    # The subscriber fell behind: the frames it has not read
                    # have left the session's history, so it can never be
                    # given them. It is ended with the reason, and it
                    # reconnects with the id of the last frame it received.
                    payload = json.dumps(
                        {
                            "reason": "the subscriber fell behind",
                            "behind": overrun.frames_missed,
                            "oldest_retained": overrun.oldest_retained,
                        }
                    )
                    yield f"event: overflow\ndata: {payload}\n\n"
                    break
                if frame is None:
                    # The session is finished and this subscriber has read
                    # the terminal frame.
                    break
                event_id, event = frame
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"id: {event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"
        finally:
            # A dropped client must not keep a subscription alive or keep a
            # position that nothing will ever read.
            await session.unsubscribe(subscription)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/workflow/{doc_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Submit Human-in-the-Loop review decision",
)
async def submit_hitl_approval(doc_id: str, payload: HITLReviewPayload) -> dict[str, str]:
    """Provide operator approval or critique feedback to resume gated workflow.

    The decision is recorded against the *pending* review. A decision for a
    review that has already been answered, or for a document whose run is no
    longer retained (never started, already terminal, or released), is a 409
    rather than a silent no-op.
    """
    from lmeditor.workflow.graph import registered_workflow_threads

    # The path names the document; the body may name one too, and the path
    # wins, so a decision cannot be redirected to another run.
    payload = payload.model_copy(update={"doc_id": doc_id})

    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )

    # Both guards run before the decision is recorded, and nothing awaits in
    # between, so a refused request never leaves a stale verdict on the session.
    if not session.hitl_pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document '{doc_id}' has no pending human review; the decision "
                "cannot be recorded."
            ),
        )

    if doc_id not in registered_workflow_threads():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No resumable workflow is registered for '{doc_id}': the run has "
                "already reached a terminal state or was released."
            ),
        )

    session.record_hitl_decision(payload)

    await session.publish_event(
        WorkflowEvent(
            event_type=EventType.NODE_COMPLETE,
            step=WorkflowStep.HITL_GATE,
            doc_id=doc_id,
            data={"approved": payload.approved, "feedback": payload.feedback},
        )
    )

    return {"doc_id": doc_id, "status": "decision_recorded"}


@router.get(
    "/workflow/{doc_id}/status",
    response_model=WorkflowStatusResponse,
    summary="Get current state snapshot of document workflow",
)
async def get_workflow_status(doc_id: str) -> WorkflowStatusResponse:
    """Retrieve the current execution status, scorecard, and HITL flags."""
    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )

    return WorkflowStatusResponse(
        doc_id=session.doc_id,
        topic=session.topic,
        current_step=session.current_step,
        status=session.status,
        hitl_pending=session.hitl_pending,
        is_completed=session.is_completed,
        scorecard=scorecard_payload(session.scorecard) or None,
        error=session.error,
        rejection_reason=session.rejection_reason,
    )


@router.get("/health", tags=["Health"], summary="Service health check")
async def health_check() -> dict[str, Any]:
    """Verify backend API readiness.

    The payload reports whether a language model is loaded. Drafting requires
    one, and LMeditor will not invent prose without it, so a client can tell
    "the gateway is up but cannot write" apart from "the gateway is broken".
    """
    from lmeditor.llm import status as lm_status

    return {
        "status": "healthy",
        "service": "lmeditor-api",
        "version": "0.1.0",
        "lm": lm_status(),
    }


# ---------------------------------------------------------------------------
# Targeted editing, fact checking, and the book library
# ---------------------------------------------------------------------------
#
# These three surfaces close the gaps between "write a document" and "work on
# a document": a small change to one passage, a verdict on one paragraph, and
# a book that is edited one section at a time without ever being loaded whole.


class BookSectionInput(BaseModel):
    """One section supplied when a book is created."""

    section_id: str = Field(..., min_length=1)
    title: str = Field(default="")
    content: str = Field(default="")


class CreateBookRequest(BaseModel):
    """Payload for creating a book in the library."""

    book_id: str | None = Field(
        default=None,
        description="Identifier for the new book; generated when omitted",
    )
    title: str = Field(default="", description="Title of the book")
    sections: list[BookSectionInput] = Field(
        default_factory=list,
        description="The sections of the book, in order",
    )


def _document_index(doc_id: str) -> Any | None:
    """The retrieval index held for a document, if one was built."""
    from lmeditor.workflow.nodes import provenance_index_for

    return provenance_index_for(doc_id)


def _document_chunks(doc_id: str) -> list[SourceChunk]:
    """Every source chunk the document's retrieval index holds.

    The chunks are read back out of the index rather than from the session,
    because the session keeps the *result* of a run while the index keeps the
    material the run was written from.

    ``chunk_ids`` is a property of the index, not a method: calling it raised
    a ``TypeError`` for every document that had an index, which took the
    targeted-edit and fact-check routes down with it.
    """
    index = _document_index(doc_id)
    if index is None:
        return []
    chunks: list[SourceChunk] = []
    for chunk_id in index.chunk_ids:
        chunk = index.get(chunk_id)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def _section_draft(raw: dict[str, Any]) -> SectionDraft | None:
    """Build a :class:`SectionDraft` from a serialised section, or ``None``.

    Unknown keys are dropped rather than rejected: the section payload
    travels through JSON and may carry fields a newer client added, and a
    section the user is editing must not become uneditable because of one.
    """
    fields = set(SectionDraft.model_fields)
    payload = {key: value for key, value in raw.items() if key in fields}
    try:
        return SectionDraft.model_validate(payload)
    except PydanticValidationError:
        return None


async def _load_section_draft(doc_id: str, section_id: str) -> SectionDraft:
    """The section of a document, or a 404 when it does not exist."""
    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )
    for raw in session.final_sections:
        if not isinstance(raw, dict):
            continue
        draft = _section_draft(raw)
        if draft is not None and draft.section_id == section_id:
            return draft
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Section '{section_id}' not found in document '{doc_id}'.",
    )


def _persist_section(session: WorkflowSession, section: SectionDraft) -> None:
    """Write an edited section back into the session's section list."""
    dumped = section.model_dump(mode="json")
    for index, raw in enumerate(session.final_sections):
        if not isinstance(raw, dict):
            continue
        if raw.get("section_id") == section.section_id:
            session.final_sections[index] = dumped
            return
    session.final_sections.append(dumped)


def _web_search_provider() -> Any:
    """The web evidence provider, honouring the ``LMEDITOR_WEB_SEARCH`` switch.

    Web evidence is on by default because "fact check from the web" is part of
    the product, but it must be switchable off for an air-gapped deployment
    or a test that must not touch the network.
    """
    import os

    from lmeditor.factcheck import DuckDuckGoSearchProvider, NullWebSearchProvider

    setting = os.environ.get("LMEDITOR_WEB_SEARCH", "on").strip().lower()
    if setting in {"off", "0", "false", "no"}:
        return NullWebSearchProvider()
    return DuckDuckGoSearchProvider()


@router.post(
    "/documents/{doc_id}/edits",
    status_code=status.HTTP_200_OK,
    summary="Apply a targeted inline edit to one section of a document",
)
async def apply_document_edit(doc_id: str, payload: InlineEditRequest) -> dict[str, Any]:
    """Rewrite one paragraph or one section, grounded in the document's sources.

    The edit is refused rather than applied when the revision cites a source
    that does not exist or when the revised claims cannot be supported. A
    rejected edit leaves the document untouched, and the response says why.
    """
    from lmeditor.editing import InlineEditor

    request = payload.model_copy(update={"doc_id": doc_id})
    _refuse_oversized_text(request.instruction, request.selected_text)
    section = await _load_section_draft(doc_id, request.section_id)

    editor = InlineEditor(
        index=_document_index(doc_id),
        chunks=_document_chunks(doc_id),
    )
    result = await editor.edit(request, section)

    if result.applied and request.persist and result.section is not None:
        session = await session_manager.get_session(doc_id)
        if session is not None:
            _persist_section(session, result.section)

    body = result.model_dump(mode="json")
    # The reason of a success is the one field of it that says why, and a
    # soft refusal may carry the machine's own detail in it: the reason is
    # therefore held to the same rule as the message of a failure. It is
    # taken out here, where the result becomes the body the client reads,
    # and nowhere else: the schema is a wire type shared with the tier, and
    # sanitising there would change what the tier sends.
    reason = body.get("reason")
    if isinstance(reason, str):
        body["reason"] = safe_detail_value(reason)
    return body


@router.put(
    "/documents/{doc_id}/sections/{section_id}",
    status_code=status.HTTP_200_OK,
    summary="Save the operator's own text for one section of a document",
)
async def save_document_section(
    doc_id: str,
    section_id: str,
    payload: SectionSaveRequest,
) -> dict[str, Any]:
    """Store the text the operator typed into one section of a document.

    A word processor that discards what the operator typed is not a word
    processor, so this is the server's "save". The operator's text replaces the
    section's content; the provenance the pipeline established for that
    section — which passage each citation came from — is kept, because the
    operator reworded the text and not the record of where it came from.

    The saved text is measured again before the response is sent, so the status
    and the scorecard describe the text as it now stands rather than the draft
    that preceded it. A measurement that cannot be made leaves the text stored
    and the section unmeasured: losing the operator's work because a verifier
    was unavailable would be the worse failure, and a verdict nobody measured
    would be the dishonest one.
    """
    from lmeditor.workflow.graph import build_document_scorecard

    session = await session_manager.get_session(doc_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )
    if not session.final_sections:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document '{doc_id}' has no manuscript yet, so there is nothing to save.",
        )

    draft = _session_section(session, section_id)
    merged = merge_operator_text(
        draft,
        content=payload.content,
        title=payload.title,
        order=payload.order,
    )
    measured, scorecard = await _measure_saved_section(
        doc_id,
        merged,
        threshold_f_beta=session.threshold_f_beta,
    )

    _persist_section(session, measured)
    session.scorecard = build_document_scorecard(
        [
            section
            for raw in session.final_sections
            if isinstance(raw, dict)
            for section in (_section_draft(raw),)
            if section is not None
        ]
    )

    result = SectionSaveResult(
        section_id=measured.section_id,
        title=measured.title,
        content=measured.content,
        status=measured.status,
        is_verified=measured.is_verified,
        scorecard=scorecard,
    )
    return result.model_dump(mode="json")


def _session_section(session: WorkflowSession, section_id: str) -> SectionDraft:
    """The named section of a session, or a 404 when the document has no such section."""
    for raw in session.final_sections:
        if not isinstance(raw, dict):
            continue
        draft = _section_draft(raw)
        if draft is not None and draft.section_id == section_id:
            return draft
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Section '{section_id}' not found in document '{session.doc_id}'.",
    )


async def _measure_saved_section(
    doc_id: str,
    section: SectionDraft,
    *,
    threshold_f_beta: float | None = None,
) -> tuple[SectionDraft, VerificationScorecard | None]:
    """Measure the grounding of one section against the document's sources.

    The measurement is best-effort by design. When the document's sources are
    no longer reachable the section is returned unmeasured rather than
    measured against nothing: "nothing supports this" and "there was nothing
    to check against" are different statements, and only the first of them is
    a verdict about the text.
    """
    from lmeditor.verification import CitationChecker
    from lmeditor.verification.citation_checker import DEFAULT_F_BETA_THRESHOLD
    from lmeditor.workflow.nodes import audit_sections

    chunks = {chunk.chunk_id: chunk for chunk in _document_chunks(doc_id)}
    if not chunks:
        logger.info(
            "Document '%s' has no reachable sources; storing the saved section unmeasured.",
            doc_id,
        )
        return section, None

    checker = CitationChecker(
        f_beta_threshold=(
            threshold_f_beta if threshold_f_beta is not None else DEFAULT_F_BETA_THRESHOLD
        )
    )
    try:
        outcomes = await audit_sections(checker, {section.section_id: section}, chunks)
    except Exception:
        # The text is already the operator's; a verifier that cannot run is
        # not a reason to throw it away.
        logger.exception(
            "Could not measure the saved section '%s'; storing it unmeasured.",
            section.section_id,
        )
        return section, None

    measured = outcomes.get(section.section_id)
    if measured is None:
        return section, None
    return measured[0], measured[1]


@router.post(
    "/documents/{doc_id}/factcheck",
    status_code=status.HTTP_200_OK,
    summary="Fact-check a passage against the document's sources, the web, or both",
)
async def fact_check_document(doc_id: str, payload: FactCheckRequest) -> dict[str, Any]:
    """Check the claims in a passage and report what supports or refutes them.

    A passage with no evidence is reported as unsupported, never as
    supported: the point of the check is to say honestly that nothing was
    found, not to produce a reassuring verdict.
    """
    from lmeditor.factcheck import FactChecker

    request = payload.model_copy(update={"doc_id": doc_id})
    _refuse_oversized_text(request.text)
    checker = FactChecker(
        index=_document_index(doc_id),
        chunks=_document_chunks(doc_id),
        web=_web_search_provider(),
    )
    report = await checker.check(request)
    return report.model_dump(mode="json")


@router.post(
    "/library/books",
    status_code=status.HTTP_201_CREATED,
    summary="Create a book in the library",
)
async def create_book(payload: CreateBookRequest) -> dict[str, Any]:
    """Create a book and store its sections for editing one at a time."""
    from lmeditor.library import import_sections

    store = book_store()
    book_id = payload.book_id or f"book_{uuid.uuid4().hex[:12]}"
    store.create(book_id, payload.title)

    if payload.sections:
        import_sections(
            store,
            book_id,
            payload.title,
            [(section.section_id, section.title, section.content) for section in payload.sections],
        )

    manifest = store.open(book_id)
    return manifest.model_dump(mode="json")


@router.post(
    "/library/books/import-docx",
    status_code=status.HTTP_201_CREATED,
    summary="Import a .docx file as a book, one section per heading",
)
async def import_book_docx(
    file: UploadFile = File(...),
    title: str = Form(default=""),
) -> dict[str, Any]:
    """Read a Word document and store it as a book of editable sections.

    Headings start a section, so the book's structure survives the import and
    each section can afterwards be edited, checked and rewritten on its own.
    """
    from lmeditor.library import import_word_document
    from lmeditor.word import WordReader

    data = await file.read()
    try:
        document = WordReader().read_bytes(data)
    except WordParseError as exc:
        # The reason may name the file the upload resolved to, which is the
        # server's own disk: the client is told the document could not be
        # read, and the reason goes to the log.
        logger.warning("The uploaded document could not be read: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The uploaded document could not be read: {safe_message(exc.reason)}",
        ) from exc

    store = book_store()
    book_id = f"book_{uuid.uuid4().hex[:12]}"
    book_title = title or getattr(document.properties, "title", "") or "Untitled"
    manifest = import_word_document(store, book_id, book_title, document)
    return manifest.model_dump(mode="json")


@router.put(
    "/library/books/{book_id}/sections/{section_id}",
    status_code=status.HTTP_200_OK,
    summary="Save the operator's own text for one section of a book",
)
async def save_book_section(
    book_id: str,
    section_id: str,
    payload: BookSectionSaveRequest,
) -> dict[str, Any]:
    """Store the text the operator typed into one section of a book.

    A book is edited one section at a time, and the operator's own hand has to
    be able to do that editing. The instruction-based edit asks the machine to
    rewrite; this stores what the operator wrote themselves. Without it, the
    only way to change a book is to ask for a rewrite and accept what comes
    back.

    The section's text changed, so its passages changed with it: the section is
    re-chunked, the manifest is told which passages belong to it now, and the
    book's index is dropped so the next retrieval is built from the text that
    is actually there. The passages built from the old text stay in the
    corpus file but are no longer referenced by any section, so they cannot be
    retrieved: an index that served a paragraph the editor deleted would be
    worse than no index at all.
    """
    from lmeditor.provenance.chunker import SourceChunker

    store = book_store()
    try:
        store.read_section(book_id, section_id)
        manifest = store.open(book_id)
    except BookNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except SectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc

    section = store.write_section(book_id, section_id, payload.content)

    chunker = SourceChunker(chunk_size=120, overlap_size=25)
    refreshed = chunker.chunk_text(
        text=payload.content,
        document_id=book_id,
        document_title=manifest.title,
    )
    store.put_chunks(book_id, refreshed)
    store.set_section_chunks(
        book_id, section_id, [chunk.chunk_id for chunk in refreshed]
    )
    invalidate_book_index(book_id)

    return BookSectionSaveResult(
        section_id=section.section_id,
        title=section.title,
        content=payload.content,
        word_count=section.word_count,
        char_count=section.char_count,
        chunk_count=len(refreshed),
    ).model_dump(mode="json")


@router.get("/library/books", summary="List the books in the library")
async def list_books() -> dict[str, Any]:
    """Every book the library holds, with its section index."""
    store = book_store()
    manifests = store.list_books()
    return {
        "books": [manifest.model_dump(mode="json") for manifest in manifests],
        "count": len(manifests),
    }


@router.get("/library/books/{book_id}", summary="Get the manifest of one book")
async def get_book(book_id: str) -> dict[str, Any]:
    """The book's manifest: metadata and section index, never the body text."""
    store = book_store()
    try:
        manifest = store.open(book_id)
    except BookNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    return manifest.model_dump(mode="json")


@router.get(
    "/library/books/{book_id}/sections/{section_id}",
    summary="Read one section of a book",
)
async def get_book_section(book_id: str, section_id: str) -> dict[str, Any]:
    """The text of one section.

    Only the requested section is read: the rest of the book stays on disk,
    which is what makes a book of any size navigable.
    """
    store = book_store()
    try:
        content = store.read_section(book_id, section_id)
        section = next(
            (item for item in store.open(book_id).sections if item.section_id == section_id),
            None,
        )
    except BookNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except SectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return {
        "book_id": book_id,
        "section_id": section_id,
        "title": section.title if section is not None else "",
        "order": section.order if section is not None else 0,
        "content": content,
    }


@router.get(
    "/library/books/{book_id}/working-set/{section_id}",
    summary="Build the bounded context for editing one section of a book",
)
async def get_book_working_set(
    book_id: str,
    section_id: str,
    neighbours: int = Query(default=1, ge=0, le=5),
    max_passages: int = Query(default=8, ge=0, le=50),
) -> dict[str, Any]:
    """The whole of the book the model sees while editing one section.

    The response reports ``context_chars`` so a client can show how small the
    working set is compared with the book: the target section, its immediate
    neighbours, the retrieved passages and the running brief, and nothing else.
    """
    from lmeditor.library import WorkingSetBuilder

    store = book_store()
    try:
        builder = WorkingSetBuilder(store, index=book_index(book_id, store))
        working_set = builder.build(
            book_id,
            section_id,
            neighbours=neighbours,
            max_passages=max_passages,
        )
    except BookNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except SectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return working_set.model_dump(mode="json")


@router.post(
    "/library/books/{book_id}/sections/{section_id}/edits",
    status_code=status.HTTP_200_OK,
    summary="Edit one section of a book in place",
)
async def edit_book_section(
    book_id: str,
    section_id: str,
    payload: BookSectionEditRequest,
) -> dict[str, Any]:
    """Rewrite one section of a book, leaving the rest of the book untouched.

    The revision is grounded in the book's own index. When it is accepted and
    persisted, the section is re-chunked and the book's index is rebuilt, so
    the next retrieval sees the text that is actually there.
    """
    from lmeditor.editing import InlineEditor
    from lmeditor.library import WorkingSetBuilder
    from lmeditor.provenance.chunker import SourceChunker

    store = book_store()
    request = payload.model_copy(update={"book_id": book_id, "section_id": section_id})
    _refuse_oversized_text(request.instruction, request.selected_text)

    try:
        content = store.read_section(book_id, section_id)
        manifest = store.open(book_id)
    except BookNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except SectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc

    section_meta = next(
        (item for item in manifest.sections if item.section_id == section_id),
        None,
    )
    draft = SectionDraft(
        section_id=section_id,
        # A section the manifest does not list still has to be editable, and a
        # heading is not optional: the section id stands in for it rather than
        # the request failing on a validation error the operator cannot see.
        title=section_meta.title if section_meta is not None else section_id,
        content=content,
        order=section_meta.order if section_meta is not None else 0,
    )

    index = book_index(book_id, store)
    builder = WorkingSetBuilder(store, index=index)
    working_set = builder.build(book_id, section_id, neighbours=1)

    editor = InlineEditor(
        index=index,
        # The passages the book's sections currently reference, never the raw
        # corpus: the corpus keeps the passages built from text an earlier
        # edit replaced, and grounding a rewrite in a paragraph the editor
        # deleted would be worse than grounding it in nothing.
        chunks=working_set.passages or book_passages(store, book_id),
    )
    # The editor works on a document's sections; a book's section is edited
    # through the same contract, with the book standing in as the document.
    edit_request = InlineEditRequest(
        doc_id=book_id,
        section_id=request.section_id,
        instruction=request.instruction,
        selected_text=request.selected_text,
        scope=request.scope,
        paragraph_index=request.paragraph_index,
        persist=request.persist,
    )
    result = await editor.edit(edit_request, draft)

    if result.applied and request.persist and result.section is not None:
        store.write_section(book_id, section_id, result.section.content)
        # The section's text changed, so its passages and the index over them
        # are stale: rebuild them rather than retrieving the old passages.
        chunker = SourceChunker(chunk_size=120, overlap_size=25)
        refreshed = chunker.chunk_text(
            text=result.section.content,
            document_id=book_id,
            document_title=manifest.title,
        )
        store.put_chunks(book_id, refreshed)
        # The manifest says which passages belong to which section, and the
        # retrieval index is built through it. Recording the section's new
        # passages is what keeps the passages built from the *old* text out of
        # the index: they are no longer true of the book.
        store.set_section_chunks(
            book_id, section_id, [chunk.chunk_id for chunk in refreshed]
        )
        invalidate_book_index(book_id)

    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# The vault
# ---------------------------------------------------------------------------
#
# A vault is a folder of markdown files on the operator's disk, and the
# server holds the one that is open: the tier keeps no state, so every call
# carries the root. The rules -- the path safety, the frontmatter, the link
# and tag grammar, the backlink index and the search order -- are the Kotlin
# tier's, and this half of the server only asks.
#
# A note id is a path, and a path in a URL is a path in a route, so a note
# id never goes in the route: it goes in the query or in the body.


class VaultOpenRequest(BaseModel):
    """Body of the request that chooses the vault folder."""

    root: str = Field(default="", description="Absolute path of the vault folder")


class VaultNoteWriteRequest(BaseModel):
    """Body of the request that saves the operator's text for one note."""

    note_id: str = Field(
        default="",
        description="The note's path inside the vault, without the .md",
    )
    content: str | None = Field(
        default=None,
        description="The note's text",
    )


class VaultNoteCreateRequest(BaseModel):
    """Body of the request that creates a note."""

    note_id: str = Field(
        default="",
        description="The note's path inside the vault, without the .md",
    )
    title: str = Field(default="", description="The note's title")
    content: str = Field(default="", description="The note's text")


class VaultNoteRenameRequest(BaseModel):
    """Body of the request that moves a note."""

    from_id: str = Field(
        default="",
        description="The note's current path inside the vault",
    )
    to_id: str = Field(
        default="",
        description="The note's new path inside the vault",
    )
    update_links: bool = Field(
        default=False,
        description="Rewrite the links that point at the note",
    )


def _vault_root() -> str:
    """The root of the open vault, or a 409 when no vault is open.

    Every vault route asks for it first: the client has to open a vault
    before it can ask anything of one, and a route that cannot be answered
    says so instead of answering with an empty result that looks like
    success.
    """
    try:
        return require_vault_root()
    except NoVaultOpenError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc
    except VaultError as exc:
        # The vault that was open is no longer usable -- it was closed, moved
        # or deleted. That is the caller's problem to fix, not a fault of
        # the server.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_message(exc.message),
        ) from exc


def _vault_refusal(exc: VaultError) -> HTTPException:
    """The HTTP answer a vault refusal gets.

    A note that is not there is a 404. A note id the tier refuses is the
    caller's mistake, so it is a 400. Either way the refusal keeps its
    meaning: the message says what was wrong, without describing the disk.
    """
    if isinstance(exc, VaultNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=safe_message(exc.message)
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=safe_message(exc.message)
    )


@router.post("/vault/open", summary="Choose the vault folder")
async def open_vault(payload: VaultOpenRequest) -> dict[str, Any]:
    """Open a vault folder and make it the active vault.

    A vault is a folder of markdown files on the operator's disk, so the
    root has to lead to one: a root that is not an absolute path, that is
    not there or that is not a directory is refused with a message that
    says which of those it was.
    """
    try:
        root = open_active_vault(payload.root)
    except VaultError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=safe_message(exc.message),
        ) from exc
    return {"root": root, "name": Path(root).name}


@router.get("/vault", summary="The active vault and how many notes it holds")
async def get_vault() -> dict[str, Any]:
    """The open vault: the folder it is, and how many notes it holds."""
    root = _vault_root()
    try:
        notes = vault.list_notes(root)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"root": root, "name": Path(root).name, "note_count": len(notes)}


@router.get("/vault/notes", summary="Every note in the vault")
async def list_vault_notes() -> dict[str, Any]:
    """Every note the vault holds, as a summary: enough to navigate by."""
    root = _vault_root()
    try:
        notes = vault.list_notes(root)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"notes": [note.model_dump(mode="json") for note in notes]}


@router.get("/vault/note", summary="One note, with its links and its backlinks")
async def get_vault_note(
    note_id: str = Query(default="", description="The note's path inside the vault"),
) -> dict[str, Any]:
    """One note, with the links it carries and the notes that link back to it."""
    root = _vault_root()
    try:
        note = vault.read_note(root, note_id)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"note": note.model_dump(mode="json")}


@router.put("/vault/note", summary="Save the operator's text for one note")
async def write_vault_note(payload: VaultNoteWriteRequest) -> dict[str, Any]:
    """Store the text the operator typed into one note.

    Writing is idempotent: the same text twice leaves the same bytes on
    disk, and the note's title, tags and links are recomputed from the
    text that is actually there, together with the backlinks of every note
    that points at it.
    """
    root = _vault_root()
    if payload.content is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The request carries no content to save.",
        )
    try:
        note = vault.write_note(root, payload.note_id, payload.content)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"note": note.model_dump(mode="json")}


@router.post("/vault/note", status_code=status.HTTP_201_CREATED, summary="Create a note")
async def create_vault_note(payload: VaultNoteCreateRequest) -> dict[str, Any]:
    """Create a note in the vault and return it as it was written."""
    root = _vault_root()
    try:
        note = vault.create_note(root, payload.note_id, payload.title, payload.content)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"note": note.model_dump(mode="json")}


@router.delete("/vault/note", summary="Delete a note")
async def delete_vault_note(
    note_id: str = Query(default="", description="The note's path inside the vault"),
) -> dict[str, Any]:
    """Delete a note from the vault."""
    root = _vault_root()
    try:
        deleted = vault.delete_note(root, note_id)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"deleted": deleted}


@router.post("/vault/note/rename", summary="Move a note")
async def rename_vault_note(payload: VaultNoteRenameRequest) -> dict[str, Any]:
    """Move a note to another path in the vault.

    When the caller asks for it, the ``[[Old]]`` links in the other notes
    are rewritten to ``[[New]]``, and the answer says how many of them
    were.
    """
    root = _vault_root()
    try:
        renamed = vault.rename_note(
            root,
            payload.from_id,
            payload.to_id,
            payload.update_links,
        )
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return renamed


@router.get("/vault/search", summary="Search the vault")
async def search_vault(
    q: str = Query(default="", description="The terms to search for"),
    limit: int = Query(default=50, ge=1, le=200, description="How many hits to return"),
) -> dict[str, Any]:
    """The vault's ranked search.

    A title hit outranks a tag hit, which outranks a body hit, and the
    order is the same every time.
    """
    root = _vault_root()
    try:
        results = vault.search(root, q, limit)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"results": [hit.model_dump(mode="json") for hit in results]}


@router.get("/vault/tags", summary="Every tag in the vault")
async def list_vault_tags() -> dict[str, Any]:
    """Every tag the vault holds, with the number of notes that carry it."""
    root = _vault_root()
    try:
        vault_tags = vault.tags(root)
    except VaultError as exc:
        raise _vault_refusal(exc) from exc
    return {"tags": [tag.model_dump(mode="json") for tag in vault_tags]}


# --- Notebooks -------------------------------------------------------------
#
# A notebook is a named collection of sources and notes on the operator's disk,
# and the Ask is the question it answers from its own sources. Every rule
# about a notebook is the Kotlin tier's; these routes are the door over it.


class NotebookOpenRequest(BaseModel):
    """The body of a request to choose the notebooks folder."""

    root: str = Field(
        default="",
        description="The absolute path of the folder of notebooks",
    )


class NotebookCreateRequest(BaseModel):
    """The body of a request to create a notebook."""

    title: str = Field(default="", description="The notebook's title")
    description: str = Field(default="", description="What the notebook is for")
    notebook_id: str | None = Field(
        default=None,
        description="The notebook's id; one is derived from the title when it is left out",
    )


class NotebookMetadataRequest(BaseModel):
    """The body of a request to rename a notebook."""

    title: str = Field(default="", description="The notebook's new title")
    description: str = Field(default="", description="What the notebook is for")


class NotebookSourceRequest(BaseModel):
    """The body of a request to hold a source in a notebook."""

    source_id: str = Field(default="", description="The id of the source to hold")


class NotebookNoteRequest(BaseModel):
    """The body of a request to refer to a note from a notebook."""

    note_id: str = Field(
        default="",
        description="The note's path inside the vault, with / separators",
    )


class NotebookAskRequest(BaseModel):
    """The body of a request to ask a notebook a question."""

    question: str = Field(default="", description="The question to ask")


def _notebooks_root() -> str:
    """The root of the notebooks folder the server holds.

    The folder always exists: it is the one the operator chose, or the
    default one. There is no answer of "there is no folder yet" to give,
    because there is no state in which the notebooks folder is missing.
    """
    try:
        return require_notebooks_root()
    except NotebookError as exc:
        # The folder the server holds is no longer usable. That is the
        # caller's problem to fix, not a fault of the server.
        raise _notebook_refusal(exc) from exc


def _notebook_refusal(exc: NotebookError) -> HTTPException:
    """The HTTP answer a notebook refusal gets.

    A notebook that is not there is a 404. A request the tier refused -- an
    unsafe id, a source the notebook does not hold -- is the caller's
    mistake, so it is a 400. Either way the refusal keeps its meaning: the
    message says what was wrong, without describing the disk.
    """
    if isinstance(exc, NotebookNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=safe_message(exc.message)
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=safe_message(exc.message)
    )


def _notebook_unavailable(exc: BridgeUnavailableError, operation: str) -> HTTPException:
    """The HTTP answer a failure of the seam gets.

    The tier could not be reached, or could not serve the method at all.
    That is not a bad request, and it is not an empty answer that looks like
    success: it is a 502.

    Why the tier is not there is the server's own business -- the reason
    names the JVM, the class it was looking for and the stderr it died with,
    and none of that may leave the process. The client is told that the tier
    is not available and which operation it lost; the reason goes to the log.
    """
    logger.error("The Kotlin tier is not available: %s", exc, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=tier_unavailable_detail(operation),
    )


@router.post("/notebooks/open", summary="Choose the notebooks folder")
async def open_notebooks(payload: NotebookOpenRequest) -> dict[str, Any]:
    """Open a notebooks folder and make it the one the server holds.

    A notebook is a folder on the operator's disk, so the root has to lead to
    one: a root that is not an absolute path, that is not there or that is
    not a directory is refused with a message that says which of those it
    was.

    The answer carries the folder, its name and how many notebooks it holds,
    so the client can read the state of the folder in one call.
    """
    try:
        root = open_notebooks_root(payload.root)
        entries = notebooks.list_notebooks(root)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "opening the notebooks folder") from exc
    return notebooks.NotebookLibrary(
        root=root,
        name=Path(root).name,
        notebook_count=len(entries),
        notebooks=entries,
    ).to_wire()


@router.get("/notebooks", summary="The notebooks folder and the notebooks in it")
async def list_notebooks() -> dict[str, Any]:
    """The notebooks folder the server holds, and the notebooks in it.

    The answer carries the root, the name of the folder, how many notebooks
    it holds and the notebooks themselves, so the client reads the state of
    the folder from one call.
    """
    root = _notebooks_root()
    try:
        entries = notebooks.list_notebooks(root)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "listing the notebooks") from exc
    return notebooks.NotebookLibrary(
        root=root,
        name=Path(root).name,
        notebook_count=len(entries),
        notebooks=entries,
    ).to_wire()


@router.post("/notebooks", summary="Create a notebook")
async def create_notebook(payload: NotebookCreateRequest) -> dict[str, Any]:
    """Create a notebook in the folder the server holds.

    Creating a notebook that is already there returns it unchanged, so the
    call is idempotent. A title with nothing in it is refused: a notebook
    the operator cannot name is a notebook they cannot find again.
    """
    root = _notebooks_root()
    try:
        notebook = notebooks.create_notebook(
            root,
            payload.title,
            payload.description,
            payload.notebook_id,
        )
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "creating the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.get("/notebooks/{notebook_id}", summary="One notebook")
async def read_notebook(notebook_id: str) -> dict[str, Any]:
    """One notebook, with the sources it holds and the notes it refers to."""
    root = _notebooks_root()
    try:
        notebook = notebooks.read_notebook(root, notebook_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "reading the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.put("/notebooks/{notebook_id}", summary="Rename a notebook")
async def rename_notebook(
    notebook_id: str,
    payload: NotebookMetadataRequest,
) -> dict[str, Any]:
    """Rename a notebook and say what it is for."""
    root = _notebooks_root()
    try:
        notebook = notebooks.set_notebook_metadata(
            root,
            notebook_id,
            payload.title,
            payload.description,
        )
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "renaming the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.delete("/notebooks/{notebook_id}", summary="Delete a notebook")
async def delete_notebook(notebook_id: str) -> dict[str, Any]:
    """Delete a notebook.

    The sources and the notes it pointed at are not deleted: a notebook only
    points at them, and they are the operator's.
    """
    root = _notebooks_root()
    try:
        deleted = notebooks.delete_notebook(root, notebook_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "deleting the notebook") from exc
    return {"deleted": deleted}


@router.post("/notebooks/{notebook_id}/sources", summary="Hold a source in a notebook")
async def add_notebook_source(
    notebook_id: str,
    payload: NotebookSourceRequest,
) -> dict[str, Any]:
    """Hold a source in a notebook.

    The source itself is not touched: it stays in the library, and the
    notebook only points at it.
    """
    root = _notebooks_root()
    try:
        notebook = notebooks.add_notebook_source(root, notebook_id, payload.source_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "adding a source to the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.delete("/notebooks/{notebook_id}/sources", summary="Stop holding a source")
async def remove_notebook_source(
    notebook_id: str,
    source_id: str = Query(default="", description="The id of the source to stop holding"),
) -> dict[str, Any]:
    """Stop holding a source. The source stays in the library.

    The source id travels in the query string, so the path carries nothing
    but the notebook it belongs to.
    """
    root = _notebooks_root()
    try:
        notebook = notebooks.remove_notebook_source(root, notebook_id, source_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "removing a source from the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.post("/notebooks/{notebook_id}/notes", summary="Refer to a note from a notebook")
async def add_notebook_note(
    notebook_id: str,
    payload: NotebookNoteRequest,
) -> dict[str, Any]:
    """Refer to a note from a notebook.

    A note id is a relative path with ``/`` separators, so it is a path and
    not a name. It travels in the body, never in the path: a percent-encoded
    ``/`` in a route does not survive the routing, and a note id is exactly
    the kind of id that carries one.
    """
    root = _notebooks_root()
    try:
        notebook = notebooks.add_notebook_note(root, notebook_id, payload.note_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "referring to a note from the notebook") from exc
    return {"notebook": notebook.to_wire()}


@router.delete("/notebooks/{notebook_id}/notes", summary="Stop referring to a note")
async def remove_notebook_note(
    notebook_id: str,
    note_id: str = Query(default="", description="The note's path inside the vault"),
) -> dict[str, Any]:
    """Stop referring to a note.

    The note id travels in the query string for the same reason it travels
    in the body on the way in: it is a path with ``/`` separators, and a
    path segment cannot hold one.
    """
    root = _notebooks_root()
    try:
        notebook = notebooks.remove_notebook_note(root, notebook_id, note_id)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "stopping the reference to a note") from exc
    return {"notebook": notebook.to_wire()}


@router.post("/notebooks/{notebook_id}/ask", summary="Ask a question of a notebook")
async def ask_notebook(notebook_id: str, payload: NotebookAskRequest) -> dict[str, Any]:
    """Ask a notebook a question, and answer it from the notebook's own sources.

    The answer leans on the passages the notebook retrieved from its own
    sources, and every citation quotes the passage it names. When the
    sources cannot answer the question, the answer says so and ``answered``
    is false -- never a made-up answer.
    """
    _refuse_oversized_text(payload.question)
    root = _notebooks_root()
    try:
        notebook = notebooks.read_notebook(root, notebook_id)
        answer = notebooks.ask_notebook(root, notebook, payload.question)
    except NotebookError as exc:
        raise _notebook_refusal(exc) from exc
    except BridgeUnavailableError as exc:
        raise _notebook_unavailable(exc, "asking the notebook") from exc
    return answer.to_wire()
