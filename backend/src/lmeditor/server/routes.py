"""Server-Sent Events (SSE) and REST gateway routes for LMeditor."""

import asyncio
import json
import uuid
from typing import Any, AsyncGenerator
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lmeditor.core.schemas import HITLReviewPayload, SourceChunk, WorkflowEvent
from lmeditor.core.types import EventType, WorkflowStep
from lmeditor.server.session import session_manager, WorkflowSession

router = APIRouter(prefix="/api/v1", tags=["Workflow & Streaming"])


class GenerateDocumentRequest(BaseModel):
    """Payload for triggering an end-to-end manuscript generation workflow."""

    topic: str = Field(..., min_length=1, description="Manuscript subject or thesis statement")
    source_chunks: list[SourceChunk] = Field(..., min_length=1, description="Candidate ground truth source chunks")
    doc_id: str | None = Field(default=None, description="Optional custom document identifier")
    max_retries: int = Field(default=3, ge=1, le=10, description="Maximum cyclic revision loops before HITL")
    threshold_f_beta: float = Field(default=0.85, ge=0.0, le=1.0, description="Minimum F_0.5 verification score required")


class GenerateDocumentResponse(BaseModel):
    """Initial response acknowledging workflow instantiation."""

    doc_id: str
    status: str
    stream_url: str


class WorkflowStatusResponse(BaseModel):
    """Snapshot status of an active or completed workflow."""

    doc_id: str
    topic: str
    current_step: WorkflowStep
    hitl_pending: bool
    is_completed: bool
    scorecard: dict[str, Any] | None = None
    error: str | None = None


async def _run_workflow_worker(session: WorkflowSession, request: GenerateDocumentRequest) -> None:
    """Background worker executing the LangGraph workflow and streaming events."""
    try:
        from lmeditor.workflow.graph import run_workflow

        # Publish initial ingestion event
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.NODE_START,
                step=WorkflowStep.INGEST,
                doc_id=session.doc_id,
                data={"topic": request.topic, "total_chunks": len(request.source_chunks)},
            )
        )

        final_state = await run_workflow(
            doc_id=session.doc_id,
            topic=request.topic,
            source_chunks=request.source_chunks,
            max_retries=request.max_retries,
            threshold_f_beta=request.threshold_f_beta,
            session=session,
        )

        sections_dump = [s.model_dump() for s in final_state.get("sections", [])]
        if final_state.get("scorecard"):
            session.scorecard = final_state["scorecard"]

        await session.complete(sections=sections_dump)

    except Exception as exc:
        await session.fail(str(exc))


@router.post(
    "/documents/generate",
    response_model=GenerateDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger new manuscript generation workflow",
)
async def generate_document(request: GenerateDocumentRequest) -> GenerateDocumentResponse:
    """Initialize a LangGraph manuscript generation workflow and return stream URL."""
    doc_id = request.doc_id or f"doc_{uuid.uuid4().hex[:12]}"
    session = await session_manager.create_session(doc_id=doc_id, topic=request.topic)

    # Launch execution worker asynchronously
    asyncio.create_task(_run_workflow_worker(session, request))

    return GenerateDocumentResponse(
        doc_id=doc_id,
        status="started",
        stream_url=f"/api/v1/stream/{doc_id}",
    )


@router.get(
    "/stream/{doc_id}",
    response_class=StreamingResponse,
    summary="Stream real-time workflow events via SSE",
)
async def stream_workflow_events(doc_id: str) -> StreamingResponse:
    """Connect to Server-Sent Events stream for a specific document workflow."""
    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        while True:
            event = await session.event_queue.get()
            if event is None:
                # Stream finished
                break
            payload = json.dumps(event.model_dump(mode="json"))
            yield f"event: {event.event_type.value}\ndata: {payload}\n\n"

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
    """Provide operator approval or critique feedback to resume gated workflow."""
    session = await session_manager.get_session(doc_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow session '{doc_id}' not found.",
        )

    session.hitl_decision = payload
    session.hitl_pending = False
    session.hitl_event.set()

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
        hitl_pending=session.hitl_pending,
        is_completed=session.is_completed,
        scorecard=session.scorecard.model_dump() if session.scorecard else None,
        error=session.error,
    )


@router.get("/health", tags=["Health"], summary="Service health check")
async def health_check() -> dict[str, str]:
    """Verify backend API readiness."""
    return {"status": "healthy", "service": "lmeditor-api", "version": "0.1.0"}
