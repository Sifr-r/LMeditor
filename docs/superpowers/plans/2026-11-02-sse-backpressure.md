# SSE Backpressure, Frame Identity and Resume — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unbounded per-subscriber SSE queue with a cursor into the session's existing history, so a subscriber that stops reading accumulates nothing and is disconnected with a recoverable, named overflow.

**Architecture:** The session's bounded history becomes the only copy of the frames. A subscriber holds a position (the id of the last frame it read) and a wake-up event — no frames. The publisher assigns each event an id, appends it to the history, and wakes every subscriber; a subscriber reads the frame after its position straight out of the history. When the frames a subscriber has not read have left the history, the session raises `SubscriberOverrun`, the route ends that stream with an `overflow` control frame, and the client reconnects with `Last-Event-ID`. A resume that the history can no longer serve completely is answered with the retained frames preceded by a `gap` frame naming the hole.

**Tech Stack:** Python 3.12+ / FastAPI / Starlette (`asyncio`), strict `mypy --strict`; Kotlin 25 / Compose desktop (`kotlinx.coroutines`), JDK only — no new dependency.

**The contract:** `docs/sse-backpressure-blueprint.md`. Where this plan and the blueprint differ, the blueprint is authoritative — stop and say so.

## Global Constraints

- **This plan is implemented on top of the credential work** (PR #4, branch `feat/api-credential`). The stream route carries the bearer token that slice added, and the test helpers in `test_sse_fanout.py` and `test_stream_contract.py` are the migrated ones (`_client(app)` returns `async_client(app)` from `tests.http_support`). Merge PR #4 first, and branch from it.
- The bearer token from `docs/api-authentication-blueprint.md` is already required on every route under `/api/`. The stream is such a route, so it carries the credential; nothing in this plan changes that.
- `mypy --strict` is clean over `backend/src` and `backend/scripts/verify_release.py` today. It must stay clean.
- The backend suite is at 1,740 passing. The Kotlin tree is at 1,075 tests. No test may be weakened, skipped or deleted; the two suites must only grow.
- The release gate is `cd backend; .venv\Scripts\python.exe scripts/verify_release.py --portable --timeout 2400`. It must pass.
- House style: a comment explains **why**, never restates code; a docstring explains the rule. An error message never discloses machine detail (paths, JVM classes) to a client.
- The shell is Windows PowerShell 5.1: single quotes for literals, `$env:VAR` for environment variables, no `&&`, no bash heredocs. `git` and `gradle` write progress to stderr, so a non-zero exit code does not by itself mean failure — read the output text.
- `pytest --timeout` is not installed. Do not pass `--timeout`.
- A regression test that pins a fix must be verified to **fail without the fix** before it is committed.

---

### Task 1: The cursor

**Files:**
- Modify: `backend/src/lmeditor/server/session.py`
- Test: `backend/tests/test_sse_fanout.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `EventSubscription(subscription_id: int, position: int)` — a cursor, not a queue. Attributes: `subscription_id: int`, `position: int`.
  - `EventSubscription.wake() -> None`
  - `EventSubscription.wait() -> None` (async)
  - `WorkflowSession.subscribe() -> EventSubscription` (async) — unchanged signature; starts at the oldest retained frame.
  - `WorkflowSession.next_frame(subscription: EventSubscription) -> tuple[int, WorkflowEvent] | None` (async) — the frame after the position, or `None` at the end of the stream.
  - `WorkflowSession._oldest_retained_id() -> int`
  - `WorkflowSession._latest_event_id -> int` (attribute)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_sse_fanout.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_sse_fanout.py::test_a_subscriber_holds_no_frames_of_its_own -q -p no:cacheprovider`
Expected: FAIL — `EventSubscription` has no `position`, and the assertion about `_frames` finds the attribute.

- [ ] **Step 3: Replace the queue with a cursor**

In `backend/src/lmeditor/server/session.py`, replace the `EventSubscription` class with:

```python
class EventSubscription:
    """One SSE subscriber's place in a session's event stream.

    A subscriber holds no frames. It holds the id of the last frame it has
    read, and the session's history is the buffer it reads from: the frames
    it has not read are found there rather than copied here. A subscriber
    that stops reading therefore accumulates nothing -- it simply stops
    advancing -- and the session notices when the frames it has not read
    have left the history.

    Each subscription has its own wake-up, so that publishing wakes every
    subscriber and no subscriber's wake-up can be consumed by another.
    """

    def __init__(self, subscription_id: int, position: int) -> None:
        self.subscription_id = subscription_id
        #: The id of the last frame this subscriber has read. The frames it
        #: has not read are those after it.
        self.position = position
        self._wakeup = asyncio.Event()

    def wake(self) -> None:
        """Tell the subscriber that something has been published."""
        self._wakeup.set()

    async def wait(self) -> None:
        """Wait until the subscriber is woken, then clear the wake-up.

        Clearing after the wait rather than before it is what keeps a
        publish that lands between the subscriber's check and its wait
        from being lost: the event stays set, so the wait returns at once.
        """
        await self._wakeup.wait()
        self._wakeup.clear()
```

- [ ] **Step 4: Give the session's history frame ids**

In the same file, change `WorkflowSession.__init__`:

```python
        # Fan-out state (AUDIT finding 13). The previous design published into a
        # single ``asyncio.Queue`` that every subscriber consumed from, so
        # concurrent streams divided the frame sequence between them — including
        # the one terminal sentinel — and a stream opened after that sentinel
        # was consumed blocked forever on an empty queue.
        self._subscribers: dict[int, EventSubscription] = {}
        self._next_subscription_id: int = 0
        #: The frames the session remembers, with the id each was published
        #: under. This history is the only copy of a frame: a subscriber
        #: reads from it by position rather than holding a copy of the
        #: frames it has not read yet.
        self._history: deque[tuple[int, WorkflowEvent]] = deque(maxlen=STREAM_REPLAY_BUFFER)
        #: The id the next published frame will carry. Ids start at 1 so
        #: that a subscriber that has read nothing is at position 0 and the
        #: first frame it is owed is frame 1.
        self._next_event_id: int = 1
        self._terminal_event: WorkflowEvent | None = None
```

Add these methods to `WorkflowSession`:

```python
    @property
    def _latest_event_id(self) -> int:
        """The id of the most recently published frame."""
        return self._next_event_id - 1

    def _oldest_retained_id(self) -> int:
        """The id of the oldest frame the session still remembers.

        A session that has published nothing yet has nothing to remember,
        and its oldest frame is the one after the last it published.
        """
        if self._history:
            return self._history[0][0]
        return self._latest_event_id + 1

    def _frame_after(self, position: int) -> tuple[int, WorkflowEvent] | None:
        """The frame after the given id, if the history still holds it.

        The history is ordered by id and the ids are contiguous, so the
        frame is found by index rather than by a search.
        """
        if not self._history:
            return None
        oldest = self._history[0][0]
        target = position + 1
        if target < oldest:
            return None
        index = target - oldest
        if index >= len(self._history):
            return None
        return self._history[index]
```

- [ ] **Step 5: Replace `subscribe`, `_replay_frames` and the fan-out**

Replace `WorkflowSession.subscribe`:

```python
    async def subscribe(self) -> EventSubscription:
        """Register a subscriber at a position in the session's history.

        A subscriber starts at the oldest frame the session still holds, so
        it is given the recent history and the live frames that follow.

        A subscription on an already-closed session comes back **already
        terminated**: the history ends with the terminal frame and there is
        nothing after it, so the caller is closed deterministically instead
        of waiting for a frame nobody will publish. Such a subscription is
        not registered for fan-out.
        """
        self._next_subscription_id += 1
        subscription = EventSubscription(
            subscription_id=self._next_subscription_id,
            position=self._oldest_retained_id() - 1,
        )
        if not self._terminated:
            self._subscribers[subscription.subscription_id] = subscription
        return subscription
```

Delete `_replay_frames` entirely — the position replaces it.

Replace the fan-out at the end of `publish_event`:

```python
        self._history.append((self._next_event_id, event))
        self._next_event_id += 1
        for subscription in tuple(self._subscribers.values()):
            subscription.wake()
```

Replace `_close_subscribers`:

```python
    async def _close_subscribers(self) -> None:
        """Wake every live subscriber so each one observes the close.

        Every stream must observe the close. The single shared sentinel of the
        previous design could be consumed by one subscriber, leaving the others
        blocked on an empty queue for good.
        """
        for subscription in tuple(self._subscribers.values()):
            subscription.wake()
```

- [ ] **Step 6: Add `next_frame`**

Add to `WorkflowSession`:

```python
    async def next_frame(
        self, subscription: EventSubscription
    ) -> tuple[int, WorkflowEvent] | None:
        """The frame after the subscriber's position, or ``None`` at the end.

        ``None`` means the stream is finished: the session published its
        terminal frame and the subscriber has read it.

        The subscriber's position advances only when a frame is handed to
        it, which is what makes a subscriber that stops reading visible: it
        stops advancing, and the frames it has not read leave the history
        around it.
        """
        while True:
            frame = self._frame_after(subscription.position)
            if frame is not None:
                subscription.position = frame[0]
                return frame
            # The frame the subscriber is owed is not in the history. Either
            # the session has not published it yet, or the history has
            # rotated past it and the subscriber has missed frames.
            if self._oldest_retained_id() > subscription.position + 1:
                raise SubscriberOverrun(
                    subscription.subscription_id,
                    subscription.position,
                    self._oldest_retained_id(),
                )
            if self._terminated:
                return None
            await subscription.wait()
```

- [ ] **Step 7: Add the `SubscriberOverrun` exception**

Add above `EventSubscription` in `backend/src/lmeditor/server/session.py`:

```python
class SubscriberOverrun(RuntimeError):
    """A subscriber fell behind the session's history.

    The frames it has not read have left the history, so they cannot be
    given to it. The subscriber is disconnected and told to reconnect, and
    the reconnect is served what the session still remembers and told about
    the rest.
    """

    def __init__(self, subscription_id: int, position: int, oldest_retained: int) -> None:
        self.subscription_id = subscription_id
        self.position = position
        self.oldest_retained = oldest_retained
        self.frames_missed = oldest_retained - position - 1
        super().__init__(
            f"subscriber {subscription_id} fell behind at frame {position}; "
            f"the session still remembers from frame {oldest_retained}"
        )
```

- [ ] **Step 8: Fix the route's generator to read through the cursor**

In `backend/src/lmeditor/server/routes.py`, replace the `event_generator` body:

```python
    async def event_generator() -> AsyncGenerator[str, None]:
        subscription = await session.subscribe()
        try:
            while True:
                frame = await session.next_frame(subscription)
                if frame is None:
                    # The session is finished and this subscriber has read
                    # the terminal frame.
                    break
                event_id, event = frame
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"event: {event.event_type.value}\ndata: {payload}\n\n"
        finally:
            # A dropped client must not keep a subscription alive or keep a
            # position that nothing will ever read.
            await session.unsubscribe(subscription)
```

- [ ] **Step 9: Run the stream tests**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_sse_fanout.py tests/test_stream_contract.py -q -p no:cacheprovider`
Expected: PASS. If `test_a_dropped_stream_unsubscribes_and_stops_accumulating` fails because it asserts on the old frame text, the frame text has not changed in this task — read the failure and fix the *implementation*, not the assertion.

- [ ] **Step 10: Run the whole backend suite and the type checker**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`
Expected: PASS (1,740 plus the new test).

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m mypy --strict src scripts/verify_release.py`
Expected: `Success: no issues found in 135 source files`.

- [ ] **Step 11: Commit**

```bash
git add backend/src/lmeditor/server/session.py backend/src/lmeditor/server/routes.py backend/tests/test_sse_fanout.py
git commit -m "feat(stream): a subscriber is a cursor, not a queue

The subscriber's queue was the unbounded memory the release finding named:
a client that stops reading but keeps its connection open parks the
generator, the finally that unsubscribes it never runs, and every frame
published after that accumulates in that one queue.

The queue is removed rather than capped. The session already keeps the
frames it published, and that history can be the buffer: a subscriber
holds the id of the last frame it read, and reads the frame after it
straight out of the history. So the unbounded memory is not bounded -- it
is gone. A hundred stalled subscribers cost what one stalled subscriber
costs, and a subscriber that stops reading stops advancing instead of
accumulating.

Frames carry an id from the session's counter, so a subscriber's position
is a frame id and a reconnect can name it."
```

---

### Task 2: The overflow

**Files:**
- Modify: `backend/src/lmeditor/server/session.py`
- Modify: `backend/src/lmeditor/server/routes.py`
- Test: `backend/tests/test_sse_fanout.py`

**Interfaces:**
- Consumes: `EventSubscription`, `WorkflowSession.next_frame`, `SubscriberOverrun` from Task 1.
- Produces:
  - `WorkflowSession.overflowed_subscribers: int`
  - `WorkflowSession.frames_withheld: int`
  - The route yields `event: overflow` before closing the stream.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_sse_fanout.py`:

```python
def test_a_subscriber_that_fell_behind_is_told_and_disconnected() -> None:
    """A subscriber that stops reading is disconnected, and told why.

    The frames it has not read have left the session's history, so it can
    never be given them. It is ended with an overflow frame saying so, and
    the other subscribers are untouched.
    """
    doc_id = "doc_fanout_overrun"

    async def run() -> tuple[list[str], WorkflowSession]:
        session = await session_manager.create_session(doc_id=doc_id, topic="fanout")
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

        rest = []
        while True:
            try:
                rest.append(
                    await asyncio.wait_for(body.__anext__(), timeout=WAIT_TIMEOUT)
                )
            except StopAsyncIteration:
                break
        await body.aclose()
        return [first, *rest], session

    frames, session = asyncio.run(run())

    assert any("event: overflow" in frame for frame in frames)
    assert session.overflowed_subscribers == 1
```

`test_sse_fanout.py` already imports `STREAM_REPLAY_BUFFER` from `lmeditor.server.session`, and `_event(doc_id, event_type, step, **data)` is its event helper. `EventType.NODE_COMPLETE` and `WorkflowStep.DRAFT_SECTIONS` are the real member names in `lmeditor/core/types.py`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_sse_fanout.py::test_a_subscriber_that_fell_behind_is_told_and_disconnected -q -p no:cacheprovider`
Expected: FAIL — `session.overflowed_subscribers` does not exist, and no overflow frame is emitted.

- [ ] **Step 3: Count the overrun in the session**

In `backend/src/lmeditor/server/session.py`, add to `WorkflowSession.__init__`, beside `post_terminal_events_dropped`:

```python
        #: Subscribers disconnected for falling behind. Counted, because a
        #: subscriber that vanished for being slow is exactly what an
        #: operator needs to see.
        self.overflowed_subscribers: int = 0
        #: Frames a subscriber was never given because it fell behind. The
        #: frames were published to the session's history; they were
        #: withheld from that one subscriber.
        self.frames_withheld: int = 0
```

And in `next_frame`, where the overrun is detected:

```python
            if self._oldest_retained_id() > subscription.position + 1:
                overrun = SubscriberOverrun(
                    subscription.subscription_id,
                    subscription.position,
                    self._oldest_retained_id(),
                )
                self.overflowed_subscribers += 1
                self.frames_withheld += overrun.frames_missed
                raise overrun
```

- [ ] **Step 4: Emit the overflow frame from the route**

In `backend/src/lmeditor/server/routes.py`, import `SubscriberOverrun` from
`lmeditor.server.session` and replace the generator's loop:

```python
    async def event_generator() -> AsyncGenerator[str, None]:
        subscription = await session.subscribe()
        try:
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
                yield f"event: {event.event_type.value}\ndata: {payload}\n\n"
        finally:
            # A dropped client must not keep a subscription alive or keep a
            # position that nothing will ever read.
            await session.unsubscribe(subscription)
```

- [ ] **Step 5: Run the test and the stream tests**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_sse_fanout.py tests/test_stream_contract.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/lmeditor/server/session.py backend/src/lmeditor/server/routes.py backend/tests/test_sse_fanout.py
git commit -m "feat(stream): a subscriber that falls behind is told and disconnected

A subscriber whose position has fallen behind the session's history can
never be given the frames it has not read, because they have left it.
The stream is ended with an overflow frame naming the reason, so the
client knows the run is still going and this subscriber fell behind, and
the subscription is released rather than left behind.

The overrun is counted rather than quiet: a subscriber that vanished for
being slow is exactly what an operator needs to see."
```

---

### Task 3: Frame identity and the resume

**Files:**
- Modify: `backend/src/lmeditor/server/routes.py`
- Modify: `backend/src/lmeditor/server/session.py`
- Test: `backend/tests/test_stream_contract.py`

**Interfaces:**
- Consumes: `WorkflowSession.subscribe`, `WorkflowSession.next_frame`, `EventSubscription.position` from Task 1; the overflow frame from Task 2.
- Produces:
  - Every sequence frame carries `id: <n>`.
  - `WorkflowSession.subscribe(last_event_id: int | None = None) -> EventSubscription` — starts after the named frame when one is given.
  - `WorkflowSession.gap_before(last_event_id: int) -> dict[str, int] | None`
  - The route reads `Last-Event-ID` and emits `event: gap` before the retained frames when the history cannot reach the named frame.
  - `WorkflowSession.gaps_served: int`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_stream_contract.py`:

```python
def test_a_reconnect_with_the_last_event_id_is_served_exactly_the_frames_after_it() -> None:
    """A reconnecting client is given the frames it missed, and no others.

    The client names the last frame it received; the server serves the
    frames after it. The frames are numbered, so the client can see that
    the sequence is continuous.
    """
    doc_id = "doc_resume_exact"

    async def run() -> tuple[httpx.Response, httpx.Response]:
        session = await session_manager.create_session(doc_id=doc_id, topic="resume")
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.NODE_START,
                step=WorkflowStep.INGEST,
                doc_id=doc_id,
                data={"step": "ingest", "topic": "resume"},
            )
        )
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.NODE_COMPLETE,
                step=WorkflowStep.DRAFT_SECTIONS,
                doc_id=doc_id,
                data={"step": "draft_sections", "topic": "resume"},
            )
        )
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])

        app = create_app()
        async with _client(app) as client:
            first = await asyncio.wait_for(
                client.get(f"/api/v1/stream/{doc_id}"), timeout=WAIT_TIMEOUT
            )
            resumed = await asyncio.wait_for(
                client.get(
                    f"/api/v1/stream/{doc_id}",
                    headers={"Last-Event-ID": "1"},
                ),
                timeout=WAIT_TIMEOUT,
            )
        return first, resumed

    first, resumed = asyncio.run(run())

    assert first.status_code == resumed.status_code == 200
    resumed_ids = [
        line[len("id: ") :]
        for line in resumed.text.splitlines()
        if line.startswith("id: ")
    ]
    assert resumed_ids == ["2", "3"]
```

`test_stream_contract.py` has no `_event` helper — it builds `WorkflowEvent`
inline, and that is what this test does. Its `_client(app)` already
presents the credential, because it comes from `tests.http_support`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_stream_contract.py::test_a_reconnect_with_the_last_event_id_is_served_exactly_the_frames_after_it -q -p no:cacheprovider`
Expected: FAIL — the frames carry no `id:`, and `Last-Event-ID` is ignored.

- [ ] **Step 3: Put the id on every sequence frame**

In `backend/src/lmeditor/server/routes.py`, change the frame it yields:

```python
                event_id, event = frame
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"id: {event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"
```

The control frames carry **no** id: a client's last-event-id buffer advances only when a frame with an `id:` is dispatched, and the overflow frame must leave the client's position where it can resume from correctly.

- [ ] **Step 4: Let a subscriber start after a named frame**

In `backend/src/lmeditor/server/session.py`, change `subscribe`:

```python
    async def subscribe(self, last_event_id: int | None = None) -> EventSubscription:
        """Register a subscriber at a position in the session's history.

        A subscriber that names the frame it last received starts after
        it, so it is given exactly the frames it missed. A subscriber that
        names none starts at the oldest frame the session still holds, so
        it is given the recent history and the live frames that follow.

        A subscription on an already-closed session comes back **already
        terminated**: the history ends with the terminal frame and there is
        nothing after it, so the caller is closed deterministically instead
        of waiting for a frame nobody will publish. Such a subscription is
        not registered for fan-out.
        """
        self._next_subscription_id += 1
        if last_event_id is None:
            position = self._oldest_retained_id() - 1
        else:
            position = last_event_id
        subscription = EventSubscription(
            subscription_id=self._next_subscription_id,
            position=position,
        )
        if not self._terminated:
            self._subscribers[subscription.subscription_id] = subscription
        return subscription
```

- [ ] **Step 5: Name the gap**

Add to `WorkflowSession` in `backend/src/lmeditor/server/session.py`:

```python
    def gap_before(self, last_event_id: int) -> dict[str, int] | None:
        """What a resume could not be given, if anything.

        The session remembers the last ``STREAM_REPLAY_BUFFER`` frames. A
        client that was away longer than that is owed frames the session
        no longer has, and the hole is named rather than papered over.
        """
        oldest = self._oldest_retained_id()
        if last_event_id + 1 >= oldest:
            return None
        self.gaps_served += 1
        return {"requested_after": last_event_id, "oldest_retained": oldest}
```

And add `self.gaps_served: int = 0` to `__init__`, beside `overflowed_subscribers`.

- [ ] **Step 6: Read `Last-Event-ID` in the route**

In `backend/src/lmeditor/server/routes.py`, add to the imports from `fastapi`: `Header`. Change the route's signature and body:

```python
@router.get(
    "/stream/{doc_id}",
    response_class=StreamingResponse,
    summary="Stream real-time workflow events via SSE",
)
async def stream_workflow_events(
    doc_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
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
                    payload = json.dumps(gap)
                    yield f"event: gap\ndata: {payload}\n\n"
            while True:
                try:
                    frame = await session.next_frame(subscription)
                except SubscriberOverrun as overrun:
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
                    break
                event_id, event = frame
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"id: {event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"
        finally:
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
```

- [ ] **Step 7: Write the gap test**

Add to `backend/tests/test_stream_contract.py`:

```python
def test_a_reconnect_the_session_can_no_longer_serve_is_told_the_gap() -> None:
    """A resume the history cannot serve completely says so.

    The client asked for the frames after a frame the session no longer
    remembers. It is given what the session still has, and told exactly
    which frames it will not be given.
    """
    doc_id = "doc_resume_gap"

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="resume")
        await session.publish_event(
            WorkflowEvent(
                event_type=EventType.NODE_START,
                step=WorkflowStep.INGEST,
                doc_id=doc_id,
                data={"step": "ingest", "topic": "resume"},
            )
        )
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])

        app = create_app()
        async with _client(app) as client:
            return await asyncio.wait_for(
                client.get(
                    f"/api/v1/stream/{doc_id}",
                    headers={"Last-Event-ID": "999"},
                ),
                timeout=WAIT_TIMEOUT,
            )

    response = asyncio.run(run())

    assert response.status_code == 200
    assert "event: gap" in response.text
    gap = json.loads(
        [line for line in response.text.splitlines() if line.startswith("data: ")][0][len("data: ") :]
    )
    assert gap["requested_after"] == 999
```

- [ ] **Step 8: Write the bad-header test**

Add to the same file:

```python
def test_a_last_event_id_that_is_not_a_frame_id_is_refused() -> None:
    """A header that is not a frame id is a client error.

    Ignoring it would serve the wrong frames, and the client would
    believe it had resumed when it had not.
    """
    doc_id = "doc_resume_bad_header"

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="resume")
        await session.complete(sections=[{"section_id": "sec_1", "title": "Intro"}])
        app = create_app()
        async with _client(app) as client:
            return await asyncio.wait_for(
                client.get(
                    f"/api/v1/stream/{doc_id}",
                    headers={"Last-Event-ID": "not-a-number"},
                ),
                timeout=WAIT_TIMEOUT,
            )

    response = asyncio.run(run())

    assert response.status_code == 400
```

- [ ] **Step 9: Run the tests**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests/test_stream_contract.py tests/test_sse_fanout.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 10: Run the whole suite and the type checker**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`
Expected: PASS.

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe -m mypy --strict src scripts/verify_release.py`
Expected: `Success: no issues found in 135 source files`.

- [ ] **Step 11: Commit**

```bash
git add backend/src/lmeditor/server/session.py backend/src/lmeditor/server/routes.py backend/tests/test_stream_contract.py
git commit -m "feat(stream): frames carry an id, and a client can resume from it

Every sequence frame now carries the id the session published it under,
and a client that reconnects names the last id it received in
Last-Event-ID. It is given exactly the frames after it -- no duplicates
and no gap -- which is what makes the overflow of the previous commit
recoverable rather than a loss.

A client that was away longer than the session remembers is told what it
missed: the stream continues with the frames the session still holds,
preceded by a gap frame naming the hole. A Last-Event-ID that is not a
frame id is refused with 400, because serving the wrong frames to a
client that believes it has resumed is worse than refusing it."
```

---

### Task 4: The desktop client

**Files:**
- Modify: `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/SseClient.kt`
- Test: `kotlin/desktop/src/test/kotlin/lmeditor/desktop/net/SseClientTest.kt`

**Interfaces:**
- Consumes: the `id:` on sequence frames, the `overflow` and `gap` control frames, and `Last-Event-ID` from Task 3.
- Produces: `SseClient.events(docId: String): Flow<WorkflowEvent>` — unchanged signature; reads the `event:` field, tracks the last id, and reconnects once on an overflow.

- [ ] **Step 1: Write the failing test**

Add to `kotlin/desktop/src/test/kotlin/lmeditor/desktop/net/SseClientTest.kt`:

```kotlin
    @Test
    fun `an overflow frame ends the stream and the client reconnects with its last id`() = runBlocking {
        val first = ByteArrayInputStream(
            ("id: 1\n" +
                "event: node_start\n" +
                "data: {\"event_type\":\"node_start\",\"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n" +
                "event: overflow\n" +
                "data: {\"reason\":\"the subscriber fell behind\",\"behind\":3,\"oldest_retained\":2}\n\n")
                .toByteArray()
        )
        val second = ByteArrayInputStream(
            ("id: 2\n" +
                "event: workflow_complete\n" +
                "data: {\"event_type\":\"workflow_complete\",\"step\":\"complete\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n")
                .toByteArray()
        )
        val responses = ArrayDeque(listOf(first, second))
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, responses.removeFirst()))
        }

        val events = SseClient(http, "http://example.test").events("doc_1").toList()

        assertEquals(listOf("node_start", "workflow_complete"), events.map { it.eventType.value })
        assertEquals("1", http.lastRequest?.headers()?.firstValue("Last-Event-ID")?.orElse(null))
    }
```

`StubHttpClient` and `StubResponse` live in `BackendClientTest.kt` and are
`internal` to the test source set, so this file already sees them. The
harness records the last request, which is how the test asserts that the
reconnect carried the last id.

**The behaviour asserted is what matters:** the client reads the `event:`
field, recognises the overflow frame rather than failing on it, reconnects,
and the reconnect carries `Last-Event-ID` with the last id it saw.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd D:\LMeditor\kotlin; .\gradlew.bat :desktop:test --tests "*SseClientTest*" --console=plain`
Expected: FAIL — the client does not read the `event:` field, does not send `Last-Event-ID`, and does not reconnect.

- [ ] **Step 3: Parse the frame's event name and id**

In `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/SseClient.kt`, add a private frame type and a parser:

```kotlin
/**
 * One frame of the stream, as the server wrote it.
 *
 * The server writes three kinds of frame: a sequence frame, which carries an
 * `id:` and a `data:` envelope of a workflow event; and two control frames,
 * `overflow` and `gap`, which carry neither an id nor an event envelope.
 * They are told apart by the `event:` name, which is why it is read.
 */
private data class SseFrame(val event: String?, val id: String?, val data: String)
```

and a method:

```kotlin
    /** Read one frame's lines into a frame, or null at the end of the stream. */
    private fun readFrame(reader: BufferedReader): SseFrame? {
        var event: String? = null
        var id: String? = null
        val data = mutableListOf<String>()
        while (true) {
            val line = reader.readLine() ?: return null
            when {
                line.startsWith("event:") -> event = line.substring(6).trim()
                line.startsWith("id:") -> id = line.substring(3).trim()
                line.startsWith("data:") -> data.add(line.substring(5).removePrefix(" "))
                line.isEmpty() -> return SseFrame(event, id, data.joinToString("\n"))
            }
        }
    }
```

A line that is none of those is a comment, and the SSE specification says a
comment is to be ignored. That is the behaviour the existing
`multiple data lines form one JSON event and comments are ignored` test
pins, and this reader keeps it.

- [ ] **Step 4: Read the control frames, track the last id, and reconnect**

Replace `SseClient.events` with:

```kotlin
    /**
     * The frames of one run, in the order the server published them.
     *
     * The flow throws [BackendError] when the stream cannot be read at all.
     * A frame the client cannot decode is a genuine disagreement between the
     * two halves of the system, and it is reported rather than skipped: a
     * silently dropped frame is a run that looks complete and is not.
     *
     * The server disconnects a subscriber that has fallen behind, and says so
     * in an `overflow` frame before it closes. That is not a failure: the run
     * is still going, so the client reopens the stream with the id of the last
     * frame it received and carries on. It does that once -- a retry loop needs
     * a backoff and a budget, which is a different decision.
     */
    fun events(docId: String): Flow<WorkflowEvent> = channelFlow {
        var lastEventId: String? = null
        while (true) {
            val request = buildRequest(docId, lastEventId)
            val response = http.sendCancellable(request, HttpResponse.BodyHandlers.ofInputStream())
            val body = response.body()
            var overflowed = false
            try {
                body.use {
                    if (response.statusCode() != 200) {
                        throw BackendError.Refused(response.statusCode(), "the event stream was refused")
                    }
                    val reader = BufferedReader(InputStreamReader(it, StandardCharsets.UTF_8))
                    while (true) {
                        val frame = readFrame(reader) ?: break
                        when (frame.event) {
                            "overflow" -> {
                                // The run is still going and this subscriber fell
                                // behind. The stream is reopened with the last
                                // frame received, so the frames it missed are
                                // replayed rather than lost.
                                overflowed = true
                                break
                            }
                            "gap" -> {
                                // The server could not give this client the frames it
                                // asked for, and named the hole. The authoritative
                                // state is fetched separately; the stream continues.
                            }
                            else -> {
                                frame.id?.let { lastEventId = it }
                                send(decode(frame.data))
                            }
                        }
                    }
                }
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: IOException) {
                throw BackendError.Unreachable(request.uri().toString(), cause)
            }
            if (!overflowed) {
                break
            }
        }
    }
```

The loop lives in the `channelFlow`'s producer scope, so the producer is
cancelled with the flow and `awaitClose` is not needed: the producer owns
the response body, and `body.use` closes it on every path out of the read
loop.

Add the request builder next to it:

```kotlin
    /** The request for one stream, resuming after the last frame received. */
    private fun buildRequest(docId: String, lastEventId: String?): HttpRequest {
        val builder = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/api/v1/stream/${pathSegment(docId)}"))
            .header("Authorization", credential.authorization())
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
        if (lastEventId != null) {
            builder.header("Last-Event-ID", lastEventId)
        }
        return builder.GET().build()
    }
```

- [ ] **Step 5: Run the desktop tests**

Run: `cd D:\LMeditor\kotlin; .\gradlew.bat :desktop:test --console=plain`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 6: Run the whole Kotlin tree**

Run: `cd D:\LMeditor\kotlin; .\gradlew.bat cleanTest test --console=plain`
Expected: BUILD SUCCESSFUL, and the JUnit XML under `kotlin/desktop/build/test-results/test` shows the new test passing.

- [ ] **Step 7: Commit**

```bash
git add kotlin/desktop
git commit -m "feat(client): the desktop client resumes the stream it fell behind on

The server now numbers its frames and disconnects a subscriber that has
fallen behind, saying so in an overflow frame before it closes. The client
has to read the event field to tell that frame from a workflow event,
remember the last id it received, and reopen the stream with it.

It reconnects once. A retry loop needs a backoff and a budget, which is a
different decision; a gap, by contrast, is not something to retry blindly,
because the client is told what it missed and fetches the document's
state instead."
```

---

### Task 5: The ledger and the readiness list

**Files:**
- Modify: `ARCHITECTURE.md`
- Modify: `docs/production-readiness.md`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: the ledger entries naming the behaviour and the tests that prove it.

- [ ] **Step 1: Update the §29.6 finding list**

In `ARCHITECTURE.md`, find the §29.6 list and remove the line about the unbounded SSE subscriber queue, replacing it with a note that it is closed in the new section. Leave every other line of that list alone.

- [ ] **Step 2: Write the ledger section**

Append a new section to `ARCHITECTURE.md`, numbered after the highest section
that exists (read the file to find it — §32 is the current last), titled
something like "The SSE stream's backpressure, identity and resume — <date>".
It must record:

- The finding, and that the queue was removed rather than capped.
- The mechanism: a subscriber is a cursor into the session's history.
- The three decisions, and where each is proved.
- The three defects the work found along the way, if it found any.
- The verification numbers, taken from the actual runs — not from this plan.

- [ ] **Step 3: Update the readiness document**

In `docs/production-readiness.md`, remove the first item of "Remaining
release work, in order" and renumber what remains. Record what the slice
closed and what it did not.

- [ ] **Step 4: Run the release gate**

Run: `cd D:\LMeditor\backend; .venv\Scripts\python.exe scripts/verify_release.py --portable --timeout 2400`
Expected: `Portable checks passed; full release readiness remains unproven.`
Record the evidence directory it prints.

- [ ] **Step 5: Fill the numbers into the ledger section**

Replace every placeholder in the new section with the real numbers from the
gate run and from the suites.

- [ ] **Step 6: Commit**

```bash
git add ARCHITECTURE.md docs/production-readiness.md
git commit -m "docs: the SSE stream's backpressure, identity and resume

The ledger records what the slice closed and what it did not, and the
readiness list is renumbered after it. The verification numbers are the
ones the gate actually produced."
```

---

## Self-review

**Spec coverage.** Every section of `docs/sse-backpressure-blueprint.md` maps
to a task: §3 (frame identity) → Task 3; §4-§5 (the cursor, the overflow)
→ Tasks 1 and 2; §6 (the resume and the gap) → Task 3; §7 (the counters)
→ Task 2 and Task 3; §8 (cancellation) → Task 1, with the existing
`test_a_dropped_stream_unsubscribes_and_stops_accumulating` as its proof;
§9 (the desktop client) → Task 4; §10 (the tests) → spread across the
tasks; §12 (the files) → the tasks' file lists.

**Placeholders.** None remain. The names that were uncertain during the
draft were checked against the tree before the plan was committed:
`EventType.NODE_START`, `EventType.NODE_COMPLETE`, `EventType.WORKFLOW_COMPLETE`
and `WorkflowStep.INGEST`, `WorkflowStep.DRAFT_SECTIONS` are the real members
of `lmeditor/core/types.py`; `STREAM_REPLAY_BUFFER` is imported from
`lmeditor.server.session`; `test_sse_fanout.py` has the `_event(doc_id,
event_type, step, **data)` helper and `test_stream_contract.py` does not, so
the latter builds `WorkflowEvent` inline; and the Kotlin test uses the
`StubHttpClient`/`StubResponse` doubles that `BackendClientTest.kt` declares.

**Type consistency.** `EventSubscription.position`, `WorkflowSession.next_frame`,
`WorkflowSession.subscribe(last_event_id)`, `WorkflowSession.gap_before`,
`SubscriberOverrun.frames_missed` and `.oldest_retained` are named the same
way in the producing task and the consuming task. `subscribe()` keeps its
old signature as a defaulted parameter, so the existing call sites and the
direct calls in the tests keep working. `EventSubscription` loses its
`frames` parameter and gains `position`; the only construction site is
`subscribe()`, which Task 1 changes in the same step.
