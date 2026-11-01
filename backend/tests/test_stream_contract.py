"""Contract tests for the server transport layer (AUDIT findings 2, 5 and 7).

Everything here is offline: the graph runner is stubbed at the two seams
``server.routes`` exposes (``_invoke_workflow`` and ``_resume_suspended_graph``),
so no model, no LLM and no network call is made. Filesystem artifacts live in
``tmp_path`` and uploads are redirected through ``LMEDITOR_UPLOAD_DIR``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from lmeditor.core.exceptions import InvalidStateTransitionError
from lmeditor.core.schemas import Claim, SectionDraft, SectionStatus, SourceChunk, WorkflowEvent
from lmeditor.core.types import EntailmentStatus, EventType, WorkflowStep
from lmeditor.server import routes
from lmeditor.server.app import create_app
from lmeditor.server.routes import GenerateDocumentRequest
from lmeditor.server.session import STATUS_FAILED, WorkflowSession, session_manager
from lmeditor.server.sources import source_library
from lmeditor.workflow.graph import (
    registered_workflow_threads,
    release_workflow,
    resume_workflow,
)

#: Content that must never be echoed back by a refused local-file ingestion.
SECRET = "INTERNAL-README-SECRET-9f2c1d"


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


#: Upper bound on a full SSE read. The stream endpoint is a generator that only
#: ends when a session delivers its terminal sentinel, so a regression in
#: terminal delivery would otherwise hang the whole suite instead of failing it.
STREAM_READ_TIMEOUT: float = 10.0


def _chunk() -> SourceChunk:
    text = "Grounded writing requires verifiable citations."
    return SourceChunk.create(
        document_id="doc_contract",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _request(doc_id: str) -> GenerateDocumentRequest:
    return GenerateDocumentRequest(
        topic="Grounded Expository Writing",
        source_chunks=[_chunk()],
        doc_id=doc_id,
    )


# ---------------------------------------------------------------------------
# Finding 2: filesystem origins are not a public ingestion path
# ---------------------------------------------------------------------------


def test_ingest_rejects_filesystem_origin_without_upload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`kind=text` plus a server-side path must not read and return the file."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    target = tmp_path / "private_notes.txt"
    target.write_text(f"{SECRET}\ninternal only\n", encoding="utf-8")

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/ingest",
                data={
                    "source_id": "src_leak",
                    "title": "private notes",
                    "kind": "text",
                    "origin": str(target),
                },
            )

    response = asyncio.run(run())

    assert response.status_code == 400, response.text
    assert SECRET not in response.text
    assert "chunks" not in response.json()


def test_ingest_rejects_project_readme_path(tmp_path: Path, monkeypatch: Any) -> None:
    """The exact AUDIT repro: the project's own backend README stays unreadable."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    readme = Path(__file__).resolve().parents[1] / "README.md"
    if not readme.exists():
        return

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/ingest",
                data={
                    "source_id": "src_readme",
                    "title": "readme",
                    "kind": "text",
                    "origin": str(readme),
                },
            )

    response = asyncio.run(run())

    assert 400 <= response.status_code < 500, response.text
    assert "chunks" not in response.json()


def test_ingest_rejects_invalid_source_id(tmp_path: Path, monkeypatch: Any) -> None:
    """A traversal `source_id` is refused before anything touches the disk."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/ingest",
                files={"file": ("note.txt", b"payload", "text/plain")},
                data={"source_id": "../../escape", "title": "note.txt"},
            )

    response = asyncio.run(run())

    assert response.status_code == 400, response.text
    assert not (tmp_path / "escape").exists()


def test_ingest_accepts_real_upload(tmp_path: Path, monkeypatch: Any) -> None:
    """A real upload still succeeds and is stored inside the upload root."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(upload_dir))

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/ingest",
                files={"file": ("note.txt", b"hello from the API\n", "text/plain")},
                data={"source_id": "src_contract", "title": "note.txt"},
            )

    try:
        response = asyncio.run(run())

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["source"]["id"] == "src_contract"
        assert body["source"]["kind"] == "text"
        assert len(body["chunks"]) >= 1

        # The upload is written to disk so it can be parsed, and removed again once
        # the source has been ingested. Leaving it behind would grow the upload
        # directory by one copy per upload, forever.
        stored = upload_dir / "src_contract" / "note.txt"
        assert not stored.exists()
    finally:
        # The ingest registered the source in the library, and the tier keeps one
        # registry for the whole process: this test leaves it as it found it,
        # whether the assertions passed or not.
        source_library.clear()


def test_url_endpoint_rejects_non_url_kind(tmp_path: Path, monkeypatch: Any) -> None:
    """`POST /sources/url` is for remote sources only."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    target = tmp_path / "local.txt"
    target.write_text(SECRET, encoding="utf-8")

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/url",
                json={
                    "source_id": "src_url_kind",
                    "kind": "text",
                    "origin": str(target),
                    "title": "local file",
                },
            )

    response = asyncio.run(run())

    assert response.status_code == 400, response.text
    assert SECRET not in response.text


def test_url_endpoint_rejects_non_http_scheme(tmp_path: Path, monkeypatch: Any) -> None:
    """Only absolute http(s) origins are accepted."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))

    async def run() -> httpx.Response:
        app = create_app()
        async with _client(app) as client:
            return await client.post(
                "/api/v1/sources/url",
                json={
                    "source_id": "src_url_scheme",
                    "kind": "url",
                    "origin": f"file:///{(tmp_path / 'etc.txt').as_posix()}",
                    "title": "file scheme",
                },
            )

    response = asyncio.run(run())

    assert response.status_code == 400, response.text
    assert "chunks" not in response.json()


def test_url_endpoint_rejects_internal_destinations(tmp_path: Path, monkeypatch: Any) -> None:
    """Loopback and private-range destinations are refused before the fetch."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    origins = [
        "http://127.0.0.1:8080/internal",
        "http://localhost:8080/internal",
        "http://10.0.0.5/internal",
        "http://169.254.169.254/latest/meta-data/",
    ]

    async def run() -> list[httpx.Response]:
        app = create_app()
        responses: list[httpx.Response] = []
        async with _client(app) as client:
            for index, origin in enumerate(origins):
                responses.append(
                    await client.post(
                        "/api/v1/sources/url",
                        json={
                            "source_id": f"src_internal_{index}",
                            "kind": "url",
                            "origin": origin,
                            "title": "internal",
                        },
                    )
                )
        return responses

    for origin, response in zip(origins, asyncio.run(run()), strict=True):
        assert response.status_code == 400, f"{origin} -> {response.status_code}"
        assert "chunks" not in response.json()


# ---------------------------------------------------------------------------
# Finding 5: SSE envelope and terminal event names
# ---------------------------------------------------------------------------


def test_stream_frames_carry_canonical_envelope() -> None:
    """`event:` is the event type; `data:` is the {event_type,step,doc_id,data} envelope."""
    doc_id = "doc_stream_contract"

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="streaming")
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.NODE_START,
                step=WorkflowStep.INGEST,
                doc_id=doc_id,
                data={"step": "ingest", "topic": "streaming"},
            )
        )
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.HITL_REQUEST,
                step=WorkflowStep.HITL_GATE,
                doc_id=doc_id,
                data={"step": "hitl_gate", "reason": "review", "requires_hitl": True},
            )
        )
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])

        app = create_app()
        async with _client(app) as client:
            return await asyncio.wait_for(
                client.get(f"/api/v1/stream/{doc_id}"), timeout=STREAM_READ_TIMEOUT
            )

    try:
        response = asyncio.run(run())

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

        frames = [frame for frame in response.text.split("\n\n") if frame.strip()]
        assert len(frames) == 3

        names: list[str] = []
        for frame in frames:
            lines = frame.split("\n")
            assert lines[0].startswith("event: ")
            assert lines[1].startswith("data: ")
            name = lines[0][len("event: ") :]
            payload = json.loads(lines[1][len("data: ") :])
            names.append(name)
            assert payload["event_type"] == name
            assert set(payload) >= {"event_type", "step", "doc_id", "data"}
            assert payload["doc_id"] == doc_id
            assert isinstance(payload["data"], dict)

        # Terminal frames are exactly workflow_complete / workflow_error.
        assert names == ["node_start", "hitl_request", "workflow_complete"]
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_stream_rejection_terminates_with_workflow_error() -> None:
    """A rejected manuscript closes the stream without a success frame."""
    doc_id = "doc_stream_rejected"

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="streaming")
        session.open_hitl_review("review required")
        await session.terminate_rejected("the argument is unsupported")

        app = create_app()
        async with _client(app) as client:
            return await asyncio.wait_for(
                client.get(f"/api/v1/stream/{doc_id}"), timeout=STREAM_READ_TIMEOUT
            )

    try:
        response = asyncio.run(run())

        assert response.status_code == 200
        assert "event: workflow_complete" not in response.text
        assert "event: workflow_error" in response.text
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


# ---------------------------------------------------------------------------
# Finding 7: the worker honours the HITL lifecycle
# ---------------------------------------------------------------------------


class _NeverPassingScorecard:
    """Stand-in metrics for a section that never verifies."""

    precision = 0.0
    recall = 0.0
    f_beta = 0.0
    total_claims = 1
    supported_claims = 0
    ungrounded_claims = 1
    contradicted_claims = 0
    neutral_claims = 0
    details: list[Any] = []


class NeverPassingCitationChecker:
    """Offline stand-in that never verifies a section, so the gate is reached."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: dict[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, _NeverPassingScorecard]:
        claims = [
            Claim(
                claim_id=f"claim_{section.section_id}_0",
                section_id=section.section_id,
                text="A claim the offline stand-in refuses to ground.",
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
            _NeverPassingScorecard(),
        )


def _force_the_gate(monkeypatch: Any) -> None:
    """Make every section fail verification so the run suspends for review."""
    monkeypatch.setattr(
        "lmeditor.workflow.nodes.CitationChecker", NeverPassingCitationChecker, raising=True
    )


async def _wait_until_pending(session: WorkflowSession) -> None:
    for _ in range(1000):
        if session.hitl_pending:
            return
        await asyncio.sleep(0.002)
    raise AssertionError("worker never opened a pending human review")


def _types(events: list[WorkflowEvent]) -> list[EventType]:
    return [event.event_type for event in events]


def _steps(events: list[WorkflowEvent], event_type: EventType) -> list[WorkflowStep]:
    return [event.step for event in events if event.event_type is event_type]


def test_approval_resumes_the_real_graph_and_completes(monkeypatch: Any) -> None:
    """Approve -> the real ``resume_workflow`` re-enters the graph and completes.

    The evidence that the run genuinely resumed rather than restarting is that
    the post-decision frames contain the post-gate ``macro_polish`` node and the
    gate's own completion, and none of the pre-gate nodes (ingest, discover,
    outline, draft, verify), which a fresh run would replay.
    """
    _force_the_gate(monkeypatch)
    doc_id = "doc_hitl_approve_real"

    async def run() -> tuple[httpx.Response, WorkflowSession, list[WorkflowEvent], list[WorkflowEvent]]:
        session = await session_manager.create_session(doc_id=doc_id, topic="hitl")
        # Subscribed before the run starts, so this stream sees every live
        # frame exactly once — the per-subscriber replacement for the old
        # shared queue this test used to drain.
        stream = await session.subscribe()
        task = asyncio.create_task(routes._run_workflow_worker(session, _request(doc_id)))
        await _wait_until_pending(session)

        # Suspended: a review is open, nothing terminal was published, and the
        # graph is retained so the decision has something to resume.
        assert session.hitl_pending is True
        assert session.is_completed is False
        assert session.status == "awaiting_human_review"
        assert doc_id in registered_workflow_threads()
        before = stream.drain()
        assert _types(before)[-1] is EventType.HITL_REQUEST
        assert before[-1].data["reason"]
        assert before[-1].data["requires_hitl"] is True
        assert EventType.WORKFLOW_COMPLETE not in _types(before)
        assert EventType.WORKFLOW_ERROR not in _types(before)
        assert WorkflowStep.MACRO_POLISH not in _steps(before, EventType.NODE_COMPLETE)

        app = create_app()
        async with _client(app) as client:
            approval = await client.post(
                f"/api/v1/workflow/{doc_id}/approve",
                json={"doc_id": doc_id, "approved": True, "feedback": "looks good"},
            )
        await task
        return approval, session, before, stream.drain()

    try:
        approval, session, before, after = asyncio.run(run())

        assert approval.status_code == 200, approval.text
        assert session.hitl_pending is False
        assert session.is_completed is True
        assert session.status == "completed"
        assert session.error is None
        assert session.rejection_reason is None
        assert session.final_sections, "an approved run must publish its sections"

        # The graph released the run once it reached its terminal state.
        assert doc_id not in registered_workflow_threads()

        # Resumed past the gate, and did not replay the pre-gate nodes.
        assert WorkflowStep.MACRO_POLISH in _steps(after, EventType.NODE_START)
        assert WorkflowStep.MACRO_POLISH in _steps(after, EventType.NODE_COMPLETE)
        assert WorkflowStep.HITL_GATE in _steps(after, EventType.NODE_COMPLETE)
        for replayed in (
            WorkflowStep.INGEST,
            WorkflowStep.DISCOVER,
            WorkflowStep.OUTLINE,
            WorkflowStep.DRAFT_SECTIONS,
        ):
            assert replayed not in _steps(after, EventType.NODE_COMPLETE), (
                f"{replayed.value} re-ran: the graph restarted instead of resuming"
            )

        # Exactly one terminal frame, even though the graph also emits one.
        assert _types(after).count(EventType.WORKFLOW_COMPLETE) == 1
        assert _types(after)[-1] is EventType.WORKFLOW_COMPLETE
        assert EventType.WORKFLOW_ERROR not in _types(after)
        assert after[-1].data["sections"]
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_rejection_terminates_the_real_graph_without_completion(monkeypatch: Any) -> None:
    """Reject -> the graph ends at the gate and the session never completes."""
    _force_the_gate(monkeypatch)
    doc_id = "doc_hitl_reject_real"

    async def run() -> tuple[httpx.Response, WorkflowSession, list[WorkflowEvent], httpx.Response]:
        session = await session_manager.create_session(doc_id=doc_id, topic="hitl")
        stream = await session.subscribe()
        task = asyncio.create_task(routes._run_workflow_worker(session, _request(doc_id)))
        await _wait_until_pending(session)
        stream.drain()

        app = create_app()
        async with _client(app) as client:
            decision = await client.post(
                f"/api/v1/workflow/{doc_id}/approve",
                json={"doc_id": doc_id, "approved": False, "feedback": "unsupported claim"},
            )
        assert decision.status_code == 200, decision.text
        await task

        # The status endpoint agrees: a rejection is not a completion.
        async with _client(app) as client:
            status_response = await client.get(f"/api/v1/workflow/{doc_id}/status")
        return decision, session, stream.drain(), status_response

    try:
        _, session, after, status_response = asyncio.run(run())

        assert session.hitl_pending is False
        assert session.is_completed is False
        assert session.status == "rejected"
        assert session.rejection_reason == "unsupported claim"
        assert session.error is None
        assert session.final_sections == []

        # The gate ran but the post-gate node did not: a rejection is not a success.
        assert WorkflowStep.HITL_GATE in _steps(after, EventType.NODE_COMPLETE)
        assert WorkflowStep.MACRO_POLISH not in _steps(after, EventType.NODE_START)
        assert EventType.WORKFLOW_COMPLETE not in _types(after)
        assert _types(after)[-1] is EventType.WORKFLOW_ERROR
        assert after[-1].data["error"]
        assert after[-1].data["rejected"] is True

        # A rejected document is not resumable any more.
        assert doc_id not in registered_workflow_threads()
        with pytest.raises(InvalidStateTransitionError):
            asyncio.run(resume_workflow(doc_id, {"approved": False}, session=None))

        assert status_response.status_code == 200, status_response.text
        body = status_response.json()
        assert body["is_completed"] is False
        assert body["status"] == "rejected"
        assert body["rejection_reason"] == "unsupported claim"
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_workflow_failure_is_terminal_but_not_a_completion(monkeypatch: Any) -> None:
    """A failed run reports `failed`, never `is_completed: true`."""
    doc_id = "doc_failed_worker"

    async def exploding_invoke(
        session: WorkflowSession, request: GenerateDocumentRequest
    ) -> dict[str, Any]:
        raise RuntimeError("drafting backend unavailable")

    monkeypatch.setattr(routes, "_invoke_workflow", exploding_invoke)

    async def run() -> tuple[WorkflowSession, list[WorkflowEvent]]:
        session = await session_manager.create_session(doc_id=doc_id, topic="failure")
        stream = await session.subscribe()
        await routes._run_workflow_worker(session, _request(doc_id))
        return session, stream.drain()

    try:
        session, events = asyncio.run(run())

        assert session.status == STATUS_FAILED
        assert session.is_completed is False
        assert session.error == "drafting backend unavailable"
        assert session.hitl_pending is False
        # A failed run leaves nothing resumable behind.
        assert doc_id not in registered_workflow_threads()

        assert _types(events).count(EventType.WORKFLOW_ERROR) == 1
        assert EventType.WORKFLOW_COMPLETE not in _types(events)
        assert events[-1].event_type is EventType.WORKFLOW_ERROR
        assert events[-1].data["error"] == "drafting backend unavailable"
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_status_endpoint_never_reports_a_failure_as_completed() -> None:
    """`GET /workflow/{id}/status` must not answer `is_completed: true` for a failure."""
    doc_id = "doc_failed_status"

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="failure")
        await session.fail("verification backend timed out")

        app = create_app()
        async with _client(app) as client:
            return await client.get(f"/api/v1/workflow/{doc_id}/status")

    try:
        response = asyncio.run(run())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_completed"] is False
        assert body["status"] == "failed"
        assert body["error"] == "verification backend timed out"
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_approve_for_an_unresumable_run_is_a_client_error(monkeypatch: Any) -> None:
    """A pending review with no retained run is a 409, not a 500 or a no-op."""
    _force_the_gate(monkeypatch)
    doc_id = "doc_hitl_no_run"

    async def run() -> tuple[httpx.Response, WorkflowSession]:
        session = await session_manager.create_session(doc_id=doc_id, topic="hitl")
        session.open_hitl_review("review required")
        # Stands in for a run that already reached a terminal state.
        assert doc_id not in registered_workflow_threads()

        app = create_app()
        async with _client(app) as client:
            response = await client.post(
                f"/api/v1/workflow/{doc_id}/approve",
                json={"doc_id": doc_id, "approved": True},
            )
        return response, session

    try:
        response, session = asyncio.run(run())

        assert response.status_code == 409, response.text
        # The refused decision must not be left behind on the session.
        assert session.hitl_decision is None
        assert session.hitl_pending is True
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_remove_session_releases_a_suspended_run(monkeypatch: Any) -> None:
    """An abandoned document must not pin its checkpointer forever."""
    _force_the_gate(monkeypatch)
    doc_id = "doc_hitl_abandoned"

    async def run() -> None:
        session = await session_manager.create_session(doc_id=doc_id, topic="hitl")
        task = asyncio.create_task(routes._run_workflow_worker(session, _request(doc_id)))
        await _wait_until_pending(session)
        assert doc_id in registered_workflow_threads()

        await session_manager.remove_session(doc_id)

        assert await session_manager.get_session(doc_id) is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(run())

        assert doc_id not in registered_workflow_threads()
        assert release_workflow(doc_id) is False  # already released by cleanup
        with pytest.raises(InvalidStateTransitionError):
            asyncio.run(resume_workflow(doc_id, {"approved": True}, session=None))
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again even if the body failed before its own removal.
        asyncio.run(session_manager.remove_session(doc_id))


def test_late_approval_for_resolved_review_is_a_client_error(monkeypatch: Any) -> None:
    """A second decision for an answered review is a 409, not a silent no-op."""
    _force_the_gate(monkeypatch)
    doc_id = "doc_hitl_late"

    async def run() -> list[httpx.Response]:
        session = await session_manager.create_session(doc_id=doc_id, topic="hitl")
        stream = await session.subscribe()
        task = asyncio.create_task(routes._run_workflow_worker(session, _request(doc_id)))
        await _wait_until_pending(session)
        stream.drain()

        app = create_app()
        async with _client(app) as client:
            first = await client.post(
                f"/api/v1/workflow/{doc_id}/approve",
                json={"doc_id": doc_id, "approved": True},
            )
            second = await client.post(
                f"/api/v1/workflow/{doc_id}/approve",
                json={"doc_id": doc_id, "approved": True},
            )
        await task
        return [first, second]

    try:
        first, second = asyncio.run(run())

        assert first.status_code == 200
        assert second.status_code == 409, second.text
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_stale_decision_cannot_resume_a_new_review() -> None:
    """A verdict recorded for an earlier review does not release the next one."""
    from lmeditor.core.schemas import HITLReviewPayload

    session = WorkflowSession(doc_id="doc_stale", topic="hitl")
    first = session.open_hitl_review("review one")
    session.record_hitl_decision(HITLReviewPayload(doc_id=session.doc_id, approved=True))

    second = session.open_hitl_review("review two")

    assert first != second
    assert session.hitl_pending is True
    assert session.hitl_decision is None
    assert session.hitl_event.is_set() is False

    async def still_waiting() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(session.await_hitl_decision(), timeout=0.05)

    asyncio.run(still_waiting())
