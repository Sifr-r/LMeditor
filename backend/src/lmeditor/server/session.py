"""Asynchronous session store and event broker for LMeditor streaming workflows."""

import asyncio
from typing import Any
from lmeditor.core.schemas import HITLReviewPayload, VerificationScorecard, WorkflowEvent
from lmeditor.core.types import EventType, WorkflowStep


class WorkflowSession:
    """Encapsulates active document generation state, event queue, and HITL signaling."""

    def __init__(self, doc_id: str, topic: str) -> None:
        self.doc_id: str = doc_id
        self.topic: str = topic
        self.current_step: WorkflowStep = WorkflowStep.INGEST
        self.scorecard: VerificationScorecard | None = None
        self.hitl_pending: bool = False
        self.hitl_decision: HITLReviewPayload | None = None
        self.hitl_event: asyncio.Event = asyncio.Event()
        self.event_queue: asyncio.Queue[WorkflowEvent | None] = asyncio.Queue()
        self.final_sections: list[dict[str, Any]] = []
        self.is_completed: bool = False
        self.error: str | None = None

    async def publish_event(self, event: WorkflowEvent) -> None:
        """Publish an event to the SSE stream queue."""
        self.current_step = event.step
        await self.event_queue.put(event)

    async def complete(self, sections: list[dict[str, Any]] | None = None) -> None:
        """Mark workflow session as completed and terminate event queue."""
        self.is_completed = True
        if sections is not None:
            self.final_sections = sections
        await self.publish_event(
            WorkflowEvent(
                event_type=EventType.WORKFLOW_COMPLETE,
                step=WorkflowStep.COMPLETE,
                doc_id=self.doc_id,
                data={"sections": self.final_sections, "scorecard": self.scorecard.model_dump() if self.scorecard else None},
            )
        )
        await self.event_queue.put(None)  # Sentinel to close SSE stream

    async def fail(self, error_message: str) -> None:
        """Record workflow failure and publish error event."""
        self.error = error_message
        self.is_completed = True
        await self.publish_event(
            WorkflowEvent(
                event_type=EventType.WORKFLOW_ERROR,
                step=self.current_step,
                doc_id=self.doc_id,
                data={"error": error_message},
            )
        )
        await self.event_queue.put(None)


class SessionManager:
    """Thread-safe registry of active manuscript generation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, WorkflowSession] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def create_session(self, doc_id: str, topic: str) -> WorkflowSession:
        """Create and register a new workflow session."""
        async with self._lock:
            session = WorkflowSession(doc_id=doc_id, topic=topic)
            self._sessions[doc_id] = session
            return session

    async def get_session(self, doc_id: str) -> WorkflowSession | None:
        """Retrieve an existing session by doc_id."""
        async with self._lock:
            return self._sessions.get(doc_id)

    async def remove_session(self, doc_id: str) -> None:
        """Clean up and remove a completed session."""
        async with self._lock:
            self._sessions.pop(doc_id, None)


# Global singleton session manager
session_manager = SessionManager()
