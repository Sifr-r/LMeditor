"""Regression tests for the transport layer: SSE fan-out and event-loop blocking.

AUDIT finding 13 was that :class:`~lmeditor.server.session.WorkflowSession`
published into a *single* ``asyncio.Queue`` that every stream consumed from, so
concurrent subscribers divided the frame sequence between them — including the
one terminal ``None`` sentinel — and a stream opened after that sentinel was
consumed blocked forever on an empty queue. Finding 15 was that the ingestion
endpoints are ``async def`` but ran synchronous disk, parser and network work
directly on the event-loop thread, stalling status, SSE, health and HITL
approval for every other client.

Everything here is offline. No stream is proven over live HTTP: httpx's
``ASGITransport`` buffers a response body, which cannot represent two
concurrently open SSE responses, and a real network fetch is out of scope. The
delivery defect lives in the session's fan-out, so that is where the fan-out is
proven, and the routes are proven through the functions FastAPI calls. Every
wait that could hang on a stream that never ends is bounded, so a regression
fails a test instead of stalling the suite.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
from pathlib import Path
from typing import Any, AsyncGenerator, cast

import httpx
import pytest
from fastapi import UploadFile

from lmeditor.core.schemas import SourceChunk, WorkflowEvent
from lmeditor.core.types import EventType, WorkflowStep
from lmeditor.server import routes, routes_helpers
from lmeditor.server.app import create_app
from lmeditor.server.routes import SourceIngestResponse
from lmeditor.server.session import (
    STATUS_FAILED,
    STATUS_REJECTED,
    STREAM_REPLAY_BUFFER,
    ActiveSessionExistsError,
    EventSubscription,
    WorkflowSession,
    session_manager,
)
from tests.http_support import async_client

#: Upper bound for every wait that a stream that never ends could turn into
#: a hang.
WAIT_TIMEOUT: float = 5.0

#: Bound on a deliberately blocking seam, so a test that *should* have stayed
#: responsive still terminates when it does not.
BLOCK_TIMEOUT: float = 2.0


def _client(app: Any) -> httpx.AsyncClient:
    return async_client(app)


def _chunk() -> SourceChunk:
    text = "Grounded writing requires verifiable citations."
    return SourceChunk.create(
        document_id="doc_fanout",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _event(doc_id: str, event_type: EventType, step: WorkflowStep, **data: Any) -> WorkflowEvent:
    return WorkflowEvent(event_type=event_type, step=step, doc_id=doc_id, data=data)


def _types(frames: list[WorkflowEvent]) -> list[EventType]:
    return [frame.event_type for frame in frames]


async def _collect(
    session: WorkflowSession, subscription: EventSubscription
) -> list[WorkflowEvent]:
    """Read frames until the stream ends. Bound by the caller."""
    frames: list[WorkflowEvent] = []
    while True:
        frame = await session.next_frame(subscription)
        if frame is None:
            return frames
        frames.append(frame[1])


async def _collect_bounded(
    session: WorkflowSession, subscription: EventSubscription
) -> list[WorkflowEvent]:
    """``_collect`` under a timeout: a stream that never ends fails the test.

    Under the old shared-queue design only one of several subscribers ever
    received the sentinel, so this is where such a regression surfaces as a
    ``TimeoutError`` instead of a hung suite.
    """
    return await asyncio.wait_for(
        _collect(session, subscription), timeout=WAIT_TIMEOUT
    )


# ---------------------------------------------------------------------------
# Finding 13: every subscriber gets the whole sequence
# ---------------------------------------------------------------------------


def test_two_concurrent_subscribers_each_receive_the_complete_sequence() -> None:
    """The finding-13 regression: N streams must not divide the frame stream.

    Each subscriber is asserted to hold the *complete* list — not that the
    union of both is complete, which the old design also satisfied. With one
    shared queue the first ``get()`` took a frame and the second took the
    next, so both lists came out short and only one of them saw the sentinel.
    """
    doc_id = "doc_fanout_two_subscribers"
    expected = [
        EventType.NODE_START,
        EventType.NODE_COMPLETE,
        EventType.HITL_REQUEST,
        EventType.WORKFLOW_COMPLETE,
    ]

    async def run() -> tuple[list[WorkflowEvent], list[WorkflowEvent]]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        first = await session.subscribe()
        second = await session.subscribe()
        assert session.subscriber_count == 2

        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        await session.publish_event(_event(doc_id, EventType.NODE_COMPLETE, WorkflowStep.INGEST))
        await session.publish_event(
            _event(doc_id, EventType.HITL_REQUEST, WorkflowStep.HITL_GATE, requires_hitl=True)
        )
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])

        return await _collect_bounded(session, first), await _collect_bounded(session, second)

    first_frames, second_frames = asyncio.run(run())

    assert _types(first_frames) == expected
    assert _types(second_frames) == expected
    for frames in (first_frames, second_frames):
        assert _types(frames).count(EventType.WORKFLOW_COMPLETE) == 1
        assert _types(frames)[-1] is EventType.WORKFLOW_COMPLETE
    assert first_frames[0].doc_id == second_frames[-1].doc_id == doc_id


def test_a_subscriber_that_joins_a_closed_session_is_terminated_at_once() -> None:
    """A stream opened after completion must not wait for a frame nobody publishes.

    Bounded by :data:`WAIT_TIMEOUT`: the old design consumed the single
    sentinel for the first stream, so this subscribe blocked on an empty queue
    and the test would time out rather than hang.
    """
    doc_id = "doc_fanout_late_subscriber"

    async def run() -> list[WorkflowEvent]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])
        assert session.is_closed is True

        late = await session.subscribe()
        # Nothing more can ever be published, so it is not registered for
        # fan-out; it is closed by the replay buffer instead.
        assert session.subscriber_count == 0
        return await _collect_bounded(session, late)

    frames = asyncio.run(run())

    assert _types(frames) == [EventType.NODE_START, EventType.WORKFLOW_COMPLETE]
    assert _types(frames).count(EventType.WORKFLOW_COMPLETE) == 1


def test_a_mid_run_subscriber_receives_replayed_history_then_live_frames() -> None:
    """Bounded replay: a late subscriber sees recent history, not an empty stream.

    History is delivered exactly once — as replay — and live frames continue on
    the same subscription without repeating or reordering the replayed ones.
    """
    doc_id = "doc_fanout_replay"

    async def run() -> list[WorkflowEvent]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        await session.publish_event(_event(doc_id, EventType.NODE_COMPLETE, WorkflowStep.INGEST))

        late = await session.subscribe()
        replayed = session.drain(late)
        assert _types(replayed) == [EventType.NODE_START, EventType.NODE_COMPLETE]

        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.DRAFT_SECTIONS))
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])
        return replayed + await _collect_bounded(session, late)

    frames = asyncio.run(run())

    assert _types(frames) == [
        EventType.NODE_START,
        EventType.NODE_COMPLETE,
        EventType.NODE_START,
        EventType.WORKFLOW_COMPLETE,
    ]
    assert [frame.step for frame in frames] == [
        WorkflowStep.INGEST,
        WorkflowStep.INGEST,
        WorkflowStep.DRAFT_SECTIONS,
        WorkflowStep.COMPLETE,
    ]


def test_the_replay_buffer_is_bounded_to_the_documented_size() -> None:
    """The buffer is a bound, not an unbounded log: it keeps the newest frames."""
    doc_id = "doc_fanout_buffer_bound"
    overflow = 7

    async def run() -> list[WorkflowEvent]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        for index in range(STREAM_REPLAY_BUFFER + overflow):
            await session.publish_event(
                _event(doc_id, EventType.TOKEN_DELTA, WorkflowStep.DRAFT_SECTIONS, index=index)
            )
        assert STREAM_REPLAY_BUFFER == 512

        late = await session.subscribe()
        return session.drain(late)

    frames = asyncio.run(run())

    assert len(frames) == STREAM_REPLAY_BUFFER
    # The oldest frames were dropped, so the retained window is the newest one.
    assert frames[0].data["index"] == overflow
    assert frames[-1].data["index"] == STREAM_REPLAY_BUFFER + overflow - 1


def test_frames_published_after_the_terminal_frame_are_counted_not_dropped_silently() -> None:
    """A late frame cannot corrupt replay order, and is never a silent no-op."""
    doc_id = "doc_fanout_post_terminal"

    async def run() -> WorkflowSession:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.fail("drafting backend unavailable")
        assert session.post_terminal_events_dropped == 0

        await session.publish_event(_event(doc_id, EventType.NODE_COMPLETE, WorkflowStep.INGEST))
        return session

    session = asyncio.run(run())

    assert session.post_terminal_events_dropped == 1
    late = WorkflowSession(doc_id=doc_id, topic="fanout")
    assert late.post_terminal_events_dropped == 0


# ---------------------------------------------------------------------------
# Finding 13: session lifecycle
# ---------------------------------------------------------------------------


def test_a_duplicate_live_doc_id_is_rejected_and_a_terminal_one_is_replaced() -> None:
    """The original worker must not be orphaned by a second session.

    ``create_session`` used to overwrite a live session for the same
    ``doc_id`` while ``asyncio.create_task(_run_workflow_worker(...))`` kept
    running against the now-unreachable object.
    """
    doc_id = "doc_fanout_duplicate"

    async def run() -> tuple[WorkflowSession, WorkflowSession]:
        original = await session_manager.create_session(doc_id=doc_id, topic="fanout")

        with pytest.raises(ActiveSessionExistsError) as excinfo:
            await session_manager.create_session(doc_id=doc_id, topic="another run")
        assert excinfo.value.doc_id == doc_id
        assert excinfo.value.status == "running"

        # The refusal left the live session and its worker untouched.
        assert await session_manager.get_session(doc_id) is original
        assert original.status == "running"

        await original.complete()
        replacement = await session_manager.create_session(doc_id=doc_id, topic="fanout again")
        return original, replacement

    original, replacement = asyncio.run(run())

    assert replacement is not original
    assert replacement.status == "running"
    assert replacement.is_completed is False


def test_duplicate_active_doc_id_is_a_409(monkeypatch: Any) -> None:
    """The route maps the refusal to ``409 Conflict``, not a 202 and not a 500."""

    async def idle_worker(session: WorkflowSession, request: Any) -> None:
        """Stand-in worker that never terminates the session, keeping it live."""

    monkeypatch.setattr(routes, "_run_workflow_worker", idle_worker)
    doc_id = "doc_fanout_duplicate_route"
    body = {
        "topic": "fan-out",
        "source_chunks": [_chunk().model_dump(mode="json")],
        "doc_id": doc_id,
    }

    async def run() -> tuple[httpx.Response, httpx.Response, WorkflowSession | None]:
        app = create_app()
        async with _client(app) as client:
            first = await client.post("/api/v1/documents/generate", json=body)
            second = await client.post("/api/v1/documents/generate", json=body)
        # Still the first session: the second request replaced nothing.
        return first, second, await session_manager.get_session(doc_id)

    first, second, session = asyncio.run(run())

    assert first.status_code == 202, first.text
    assert first.json()["doc_id"] == doc_id
    assert second.status_code == 409, second.text
    assert doc_id in second.text
    assert session is not None
    assert session.status == "running"


def test_failure_and_rejection_deliver_one_terminal_frame_and_no_completion() -> None:
    """Error and rejection paths stay terminal without claiming a completion.

    Both subscribers of both sessions must see exactly one terminal frame, and
    ``is_completed`` must stay ``False`` for a failure and for a rejection.
    """
    failure_id = "doc_fanout_failure"
    rejection_id = "doc_fanout_rejection"

    async def run() -> tuple[WorkflowSession, WorkflowSession]:
        failed = await session_manager.create_session(doc_id=failure_id, topic="fanout")
        failed_subscribers = [await failed.subscribe(), await failed.subscribe()]
        await failed.fail("verification backend timed out")

        rejected = await session_manager.create_session(doc_id=rejection_id, topic="fanout")
        rejected.open_hitl_review("review required")
        rejected_subscribers = [await rejected.subscribe(), await rejected.subscribe()]
        await rejected.terminate_rejected("the argument is unsupported")

        for session, subscription in (
            (failed, failed_subscribers[0]),
            (failed, failed_subscribers[1]),
            (rejected, rejected_subscribers[0]),
            (rejected, rejected_subscribers[1]),
        ):
            assert _types(await _collect_bounded(session, subscription)) == [EventType.WORKFLOW_ERROR]
        return failed, rejected

    failed, rejected = asyncio.run(run())

    assert failed.status == STATUS_FAILED
    assert failed.is_completed is False
    assert failed.error == "verification backend timed out"
    assert rejected.status == STATUS_REJECTED
    assert rejected.is_completed is False
    assert rejected.rejection_reason == "the argument is unsupported"


def test_a_terminal_transition_is_idempotent_for_every_subscriber() -> None:
    """A second terminal transition adds no frame and no second close."""
    doc_id = "doc_fanout_idempotent"

    async def run() -> list[WorkflowEvent]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        subscriber = await session.subscribe()
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])
        await session.complete(sections=[{"section_id": "sec_2", "title": "Other"}])
        await session.fail("must not overwrite a completed run")
        return await _collect_bounded(session, subscriber)

    frames = asyncio.run(run())

    assert _types(frames) == [EventType.WORKFLOW_COMPLETE]
    assert frames[0].data["sections"] == [{"section_id": "sec_1", "title": "Intro"}]


# ---------------------------------------------------------------------------
# Finding 13: a subscriber is a position, not a queue
# ---------------------------------------------------------------------------


def test_a_subscriber_holds_no_frames_of_its_own() -> None:
    """A subscriber's memory is a position, not a copy of the frames.

    The session's history is the only copy. A subscriber that has not read
    anything holds nothing, so a hundred stalled subscribers cost what one
    stalled subscriber costs.
    """
    doc_id = "doc_fanout_cursor"

    async def run() -> WorkflowSession:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        subscription = await session.subscribe()
        assert not hasattr(subscription, "_frames")
        assert subscription.position == 0
        return session

    session = asyncio.run(run())

    assert session.subscriber_count == 1


def test_a_subscriber_that_fell_behind_is_told_and_disconnected() -> None:
    """A subscriber that stops reading is disconnected, and told why.

    The frames it has not read have left the session's history, so it can
    never be given them. It is ended with an overflow frame saying so, the
    subscriber that kept reading is given every frame, and the overflowed
    subscription is released.
    """
    doc_id = "doc_fanout_overrun"

    async def run() -> tuple[list[str], list[int], int, WorkflowSession]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")

        # A second subscriber keeps reading every frame the session publishes.
        keeper = await session.subscribe()
        received: list[int] = []

        async def keep_up() -> None:
            while True:
                frame = await session.next_frame(keeper)
                if frame is None:
                    return
                received.append(frame[0])

        reading = asyncio.create_task(keep_up())
        before_attach = session.subscriber_count

        response = await routes.stream_workflow_events(doc_id)
        body = cast(AsyncGenerator[str, None], response.body_iterator)

        # The subscriber reads one frame and then stops reading entirely.
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        first = await asyncio.wait_for(body.__anext__(), timeout=WAIT_TIMEOUT)

        # More frames than the history holds, so the one the subscriber did
        # not read has left it.
        for _ in range(STREAM_REPLAY_BUFFER + 2):
            await session.publish_event(
                _event(doc_id, EventType.NODE_COMPLETE, WorkflowStep.DRAFT_SECTIONS)
            )
            # The subscriber that keeps up is given the chance to read each
            # frame, so that it stays level with the publisher.
            await asyncio.sleep(0)

        rest = []
        while True:
            try:
                rest.append(
                    await asyncio.wait_for(body.__anext__(), timeout=WAIT_TIMEOUT)
                )
            except StopAsyncIteration:
                break
        await body.aclose()

        # The session ends, so the subscriber that kept up finishes with it.
        await session.complete()
        await asyncio.wait_for(reading, timeout=WAIT_TIMEOUT)
        return [first, *rest], received, before_attach, session

    frames, received, before_attach, session = asyncio.run(run())

    assert any("event: overflow" in frame for frame in frames)
    assert session.overflowed_subscribers == 1
    # The overflowed subscription is released: the subscriber count is back
    # to what it was before that stream attached.
    assert session.subscriber_count == before_attach
    # The publisher neither blocked nor dropped anything for the subscriber
    # that kept reading: it was given every frame, in order -- the first
    # frame, the STREAM_REPLAY_BUFFER + 2 that pushed the stalled subscriber
    # out of the history, and the terminal frame.
    assert received == list(range(1, STREAM_REPLAY_BUFFER + 5))


def test_a_dropped_stream_unsubscribes_and_stops_accumulating() -> None:
    """Cleanup on disconnect, not only on stream completion.

    httpx's ASGI transport buffers a whole response, so it cannot represent a
    half-read SSE stream. The generator is therefore driven directly: one frame
    is read, then the client disconnects and the generator is closed.
    """
    doc_id = "doc_fanout_disconnect"

    async def run() -> tuple[str, WorkflowSession]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))

        response = await routes.stream_workflow_events(doc_id)
        body = cast(AsyncGenerator[str, None], response.body_iterator)
        first_frame = await asyncio.wait_for(body.__anext__(), timeout=WAIT_TIMEOUT)
        assert first_frame.startswith("event: node_start")
        assert session.subscriber_count == 1

        # The client goes away mid-stream.
        await body.aclose()
        assert session.subscriber_count == 0

        # The subscription is gone, so the frames published after the disconnect
        # are not held for that client, and a stream that attaches now is
        # closed by the session's terminal frame.
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])
        return first_frame, session

    first_frame, session = asyncio.run(run())

    assert first_frame.startswith("event: node_start")
    assert session.subscriber_count == 0
    assert session.is_completed is True


def test_a_reconnecting_stream_replays_the_session_history_over_http() -> None:
    """A stream opened after completion is closed by the session, not by luck.

    The whole scenario is bounded: an HTTP stream that never ends leaves the
    request suspended forever rather than failing, because the loop stays
    responsive while the generator waits on the subscription.
    """
    doc_id = "doc_fanout_reconnect"

    async def run() -> tuple[httpx.Response, httpx.Response]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
        await session.publish_event(_event(doc_id, EventType.NODE_START, WorkflowStep.INGEST))
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])

        app = create_app()
        async with _client(app) as client:
            first = await asyncio.wait_for(
                client.get(f"/api/v1/stream/{doc_id}"), timeout=WAIT_TIMEOUT
            )
            second = await asyncio.wait_for(
                client.get(f"/api/v1/stream/{doc_id}"), timeout=WAIT_TIMEOUT
            )
        return first, second

    first, second = asyncio.run(run())

    assert first.status_code == second.status_code == 200
    for response in (first, second):
        names = [
            frame.split("\n")[0][len("event: ") :]
            for frame in response.text.split("\n\n")
            if frame.strip()
        ]
        assert names == ["node_start", "workflow_complete"]
        assert json.loads(response.text.split("\n")[1][len("data: ") :])["doc_id"] == doc_id


# ---------------------------------------------------------------------------
# Finding 15: ingestion must not block the event loop
# ---------------------------------------------------------------------------


def _drive(scenario: Any) -> bool:
    """Run ``scenario()`` on a fresh event loop in a worker thread.

    The main thread joins with a wall-clock bound instead of an
    ``asyncio.wait_for``: a *blocked* loop cannot run its own timers, so an
    in-loop timeout would never fire and the suite would hang. The blocking
    seams in the scenarios below are separately time-bounded, so a regression
    fails the assertions instead of stalling. Returns whether the scenario
    finished at all.
    """
    finished = threading.Event()

    def runner() -> None:
        try:
            asyncio.run(scenario())
        finally:
            finished.set()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout=30.0)
    return finished.is_set()


def test_a_slow_sync_ingest_leaves_the_event_loop_responsive(monkeypatch: Any) -> None:
    """A blocking ingest must not stall unrelated work on the same loop.

    Asserting a 202 response would prove nothing here: the old code returned
    202 *after* stalling the loop for the whole parse. The proof is that a
    separate coroutine on the same loop runs to completion while the ingest is
    still in flight, and that the ingest body ran off the loop thread.
    """
    started = threading.Event()
    release = threading.Event()
    threads: list[str] = []

    def blocking_ingest(**kwargs: Any) -> SourceIngestResponse:
        """Stands in for the synchronous ingestion pipeline."""
        threads.append(threading.current_thread().name)
        started.set()
        release.wait(timeout=BLOCK_TIMEOUT)
        return SourceIngestResponse(source={"id": kwargs.get("source_id", "")}, chunks=[])

    monkeypatch.setattr(routes, "_ingest_source", blocking_ingest)
    observations: dict[str, Any] = {}

    async def scenario() -> None:
        loop_thread = threading.current_thread().name
        ingest = asyncio.create_task(
            routes.ingest_url_source(
                routes.SourceIngestRequest(
                    source_id="src_fanout_slow",
                    kind="url",
                    origin="https://example.com/paper.pdf",
                    title="slow source",
                )
            )
        )
        while not started.is_set():
            await asyncio.sleep(0.005)

        # The ingest is in flight and still blocked. The loop must keep serving.
        async def probe() -> str:
            await asyncio.sleep(0)
            return threading.current_thread().name

        observations["probe_thread"] = await asyncio.wait_for(probe(), timeout=WAIT_TIMEOUT)
        observations["still_blocked"] = not release.is_set()
        observations["loop_thread"] = loop_thread

        release.set()
        response = await asyncio.wait_for(ingest, timeout=WAIT_TIMEOUT)
        observations["chunks"] = response.chunks
        observations["subscriber"] = await asyncio.wait_for(probe(), timeout=WAIT_TIMEOUT)

    assert _drive(scenario), "the scenario never finished: the loop was blocked"

    # The ingest body did not run on the loop thread.
    assert observations["loop_thread"] != threads[0]
    # The loop ran other work while the ingest was still blocked, on itself.
    assert observations["probe_thread"] == observations["loop_thread"]
    assert observations["still_blocked"] is True
    assert observations["subscriber"] == observations["loop_thread"]


def test_persisting_an_upload_writes_off_the_event_loop(tmp_path: Path, monkeypatch: Any) -> None:
    """``persist_source_to_disk`` must offload its ``mkdir`` and ``write_bytes``.

    The real write is a few microseconds, so the seam is replaced by a blocking
    one: what is proven is *where* the write body runs, which is the whole
    point of the fix.
    """
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    started = threading.Event()
    release = threading.Event()
    threads: list[str] = []
    # Captured before patching: the replacement delegates to the real write, so
    # the test proves where the work runs and not that a stub was called.
    real_write = routes_helpers._store_save

    def blocking_write(source_id: str, filename: str, content: bytes) -> Path:
        threads.append(threading.current_thread().name)
        started.set()
        release.wait(timeout=BLOCK_TIMEOUT)
        return real_write(source_id, filename, content)

    monkeypatch.setattr(routes_helpers, "_store_save", blocking_write)
    observations: dict[str, Any] = {}

    async def scenario() -> None:
        loop_thread = threading.current_thread().name
        upload = UploadFile(file=io.BytesIO(b"payload\n"), filename="note.txt")
        persist = asyncio.create_task(
            routes_helpers.persist_source_to_disk(upload, source_id="src_fanout_write")
        )
        while not started.is_set():
            await asyncio.sleep(0.005)

        async def probe() -> str:
            await asyncio.sleep(0)
            return threading.current_thread().name

        observations["probe_thread"] = await asyncio.wait_for(probe(), timeout=WAIT_TIMEOUT)
        observations["still_blocked"] = not release.is_set()
        observations["loop_thread"] = loop_thread

        release.set()
        saved = await asyncio.wait_for(persist, timeout=WAIT_TIMEOUT)
        observations["path"] = saved.path

    assert _drive(scenario), "the scenario never finished: the loop was blocked"

    assert observations["loop_thread"] != threads[0]
    assert observations["probe_thread"] == observations["loop_thread"]
    assert observations["still_blocked"] is True
    # The offloaded write did the real work.
    assert Path(observations["path"]).read_text(encoding="utf-8") == "payload\n"


def test_a_slow_upload_ingest_leaves_the_event_loop_responsive(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The multipart endpoint is offloaded too, not only the URL one."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path / "uploads"))
    started = threading.Event()
    release = threading.Event()
    threads: list[str] = []

    def blocking_ingest(**kwargs: Any) -> SourceIngestResponse:
        threads.append(threading.current_thread().name)
        started.set()
        release.wait(timeout=BLOCK_TIMEOUT)
        return SourceIngestResponse(source={"id": kwargs.get("source_id", "")}, chunks=[])

    monkeypatch.setattr(routes, "_ingest_source", blocking_ingest)
    observations: dict[str, Any] = {}

    async def scenario() -> None:
        loop_thread = threading.current_thread().name
        upload = UploadFile(file=io.BytesIO(b"payload\n"), filename="note.txt")
        ingest = asyncio.create_task(
            routes.ingest_source(
                source_id="src_fanout_upload",
                title="note.txt",
                file=upload,
            )
        )
        while not started.is_set():
            await asyncio.sleep(0.005)

        async def probe() -> str:
            await asyncio.sleep(0)
            return threading.current_thread().name

        observations["probe_thread"] = await asyncio.wait_for(probe(), timeout=WAIT_TIMEOUT)
        observations["still_blocked"] = not release.is_set()
        observations["loop_thread"] = loop_thread
        # The upload was persisted before the offloaded ingest ran, so the
        # file is on disk while the ingest is still blocked.
        observations["persisted_while_blocked"] = (
            tmp_path / "uploads" / "src_fanout_upload" / "note.txt"
        ).is_file()

        release.set()
        response = await asyncio.wait_for(ingest, timeout=WAIT_TIMEOUT)
        observations["chunks"] = response.chunks

    assert _drive(scenario), "the scenario never finished: the loop was blocked"

    assert observations["loop_thread"] != threads[0]
    assert observations["probe_thread"] == observations["loop_thread"]
    assert observations["still_blocked"] is True
    # The upload was persisted before the offloaded ingest ran.
    assert observations["persisted_while_blocked"] is True
    # Once the source has been ingested the artifact is removed again, so the
    # upload directory does not grow by one copy per upload.
    stored = tmp_path / "uploads" / "src_fanout_upload" / "note.txt"
    assert not stored.exists()
