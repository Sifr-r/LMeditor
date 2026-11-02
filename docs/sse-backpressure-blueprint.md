# The SSE stream's backpressure, identity and resume — a design

**Status:** agreed, 2026-11-02. The contract, written down before it is
implemented, the way `docs/api-authentication-blueprint.md` was.

**The finding it closes.** `ARCHITECTURE.md` §29.6 lists: *the live SSE
subscriber queue is unbounded; a slow reader can grow it without limit, and
there is no defined overflow behaviour.* §30 lists the same finding with
the rest: *bound live SSE subscriber memory and specify an overflow/disconnect
policy. Add frame identity, reconnect/replay proof and cancellation before
claiming streaming resilience.*

---

## 1. What is wrong today

`EventSubscription` owns one `asyncio.Queue` per subscriber. It is
unbounded, and the docstring says so on purpose:

> The buffer is unbounded on purpose: a bounded one would either block the
> publishing worker (stalling every other stream) or silently drop frames a
> slow client never received. A client that stops reading is released by
> `WorkflowSession.unsubscribe`, after which nothing is queued for it.

The last sentence is the claim that does not hold. `unsubscribe` is called
from the `finally` of the route's `event_generator`, and that `finally` runs
when the generator is closed — which happens when the **client** goes away.
A client that stops reading but keeps its connection open parks the
generator on `await subscription.get()`, and the `finally` never runs.
Every frame published after that point is appended to that one queue, for
as long as the run lasts, and nothing ever takes it out.

The bound was omitted because the two obvious ways to add one are both
wrong: a bounded queue that **blocks** the publisher stalls every other
stream on that session, and a bounded queue that **drops** loses frames
without telling anyone. Both objections are correct.

The reason both objections have force is that the design assumes a
subscriber needs its own copy of the frames. It does not.

## 2. The decisions

Three decisions were taken, and the rest of this document follows from
them.

1. **A subscriber that falls behind is disconnected, and the disconnect is
   recoverable.** The client is told why, and it reconnects with the id of
   the last frame it received.
2. **A subscriber's memory is bounded, and the bound is small.** The
   subscriber holds a position and a wake-up, not a queue of frames.
3. **A resume the server cannot serve completely says so.** The stream
   continues with the frames the session still holds, preceded by a frame
   naming exactly what was missed.

## 3. The mechanism: a subscriber is a cursor, not a queue

The session already keeps the frames it published, in a bounded history.
That history is the buffer. A subscriber does not need its own copy of the
frames it has not read yet — it needs to say **where it got to**.

So a subscriber becomes a position in the session's history, and the
frames it has not read are found by looking them up in the history the
session already holds:

```
                    the session's history (bounded, shared)
   ┌──────────────────────────────────────────────────────────────┐
   │ frame 91 │ frame 92 │ frame 93 │ ... │ frame 604 │ frame 605 │
   └──────────────────────────────────────────────────────────────┘
        ▲                                            ▲
        │                                            │
   the subscriber's                        the frames the session
   position: 90                            still remembers
   (it has read up to 90)                  (the newest ones)
```

**What a subscriber holds** is its id, the id of the last frame it has
read, and an `asyncio.Event` to wake it when something is published. That
is a fixed number of bytes, whatever the run does and however many frames
pass by. A hundred stalled subscribers cost what one stalled subscriber
costs: nothing.

**What the session holds** is its history, which is bounded today at
`STREAM_REPLAY_BUFFER` = 512 frames and stays bounded at 512.

So the unbounded memory is not capped — it is **removed**. There is no
per-subscriber queue of frames to grow.

**Reading.** The generator asks the session for the frame after the
subscriber's position. If the session has published one, it is returned and
the position advances. If not, the subscriber waits on its wake-up event,
and publishing sets that event for every subscriber.

**The publisher never blocks**, and no frame is ever copied for a
subscriber that has not asked for it.

## 4. Frame identity

The SSE specification already defines what is needed: a frame may carry an
`id:`, and a client reconnecting sends the last id it saw in a
`Last-Event-ID` request header. Nothing is invented here.

Every **sequence frame** — a frame that carries a published event — carries
an `id:`:

```
id: 7
event: section_drafted
data: {"event_type": "section_drafted", "step": "draft", "doc_id": "d1", "data": {...}}
```

The id is a per-session sequence number, starting at 1 and increasing by one
per published event. It is assigned by the session at publish time and
carried **alongside** the event, not inside it: `WorkflowEvent` is a domain
model, frozen and `extra="forbid"`, and a stream's frame numbering is a
property of the stream rather than of the thing it reports. The session's
history therefore holds `(event_id, WorkflowEvent)` pairs.

The two **control frames** — `overflow` and `gap` — carry **no** `id:`. A
client's last-event-id buffer advances only when a frame with an `id:` is
dispatched, so a control frame leaves the client's position where it was.
That is what is wanted: the client resumes from the last *sequence* frame
it actually received, which is the position it can resume from correctly.

## 5. The overflow

A subscriber falls behind when the frames it has not read have rotated out
of the session's history. The session notices this when it looks for the
frame after the subscriber's position and finds that the history no longer
reaches back that far.

At that moment the subscription is marked overflowed, the session counts
it, and the route's generator ends that stream with a control frame:

```
event: overflow
data: {"reason": "the subscriber fell behind", "behind": 640, "oldest_retained": 604}
```

Ending the response closes the connection, which closes the generator,
which runs the `finally` that unsubscribes — so the subscription is
released, and with it the only thing it held.

**The other subscribers are not affected.** The publisher never blocks and
never drops for them; only the subscription that fell behind is marked.

**The client reconnects.** It sends the `Last-Event-ID` of the last sequence
frame it received, and the server serves the frames after it from the
history. The overflow frame carried no `id:`, so the client's position is
exactly the last frame it actually received.

**What the reconnect gets.** The frames the session still holds after the
client's position, and — if the client was away long enough for the frames
between its position and the session's memory to have rotated out — a
`gap` frame naming exactly those frames:

```
event: gap
data: {"requested_after": 118, "oldest_retained": 640}
```

The client is told exactly what it missed rather than being left to assume
it has everything. It can re-fetch the authoritative state — the API already
serves the manuscript and its scorecard at `GET /api/v1/documents/{doc_id}`
— and carry on.

**The gap is not a separate case.** It is the same rule as the overflow's,
seen from the other side: the server serves what it remembers and names
what it does not. A subscriber that fell behind by a few frames reconnects
and is served completely, because the session still remembers them; one
that fell behind by more than the session's memory is told the difference.

## 6. The seed, and the late subscriber

A client that opens a stream with no `Last-Event-ID` wants the session's
recent history. It is given it from the oldest frame the session still
holds — which, under the cursor design, is simply where its position
starts. There is no seed to copy into a buffer and no truncation to
reason about, because there is no buffer.

A client that opens a stream with a `Last-Event-ID` the session still
holds is given the frames after that id. One that opens a stream with an id
the session has rotated past is given the frames the session still holds,
preceded by the `gap` frame.

A `Last-Event-ID` that is not an integer is a client error and is refused
with **400**. Ignoring it would serve the wrong frames, and the client
would believe it had resumed when it had not.

## 7. What is counted

The session gains three counters, beside the `post_terminal_events_dropped`
that already exists:

| Counter | What it counts |
|---|---|
| `overflowed_subscribers` | Subscribers disconnected for falling behind |
| `frames_withheld` | Frames a subscriber was never given because it fell behind |
| `gaps_served` | Resumes that could not be served completely |

The house rule is that a skip is not a pass and a dropped frame is not a
quiet frame. A subscriber that vanished because it was slow is exactly the
thing an operator needs to see, and a frame that was withheld from one
subscriber was still published to the session's history — counted, not
discarded.

## 8. Cancellation

Three things end a stream, and all three release the subscription:

- **The client closes the connection.** The generator's `finally` calls
  `unsubscribe`, the subscription leaves the session's subscriber map and
  its wake-up event is dropped. This is the existing behaviour.
- **The run reaches its terminal frame.** The session's terminated state
  ends every live subscriber's stream. This is the existing behaviour.
- **The subscriber falls behind.** The overflow frame ends the stream, the
  connection closes, and the `finally` releases the subscription. This is
  new, and it is the case that used to leak.

The server side of a run being cancelled — terminating the worker's process
tree — is unchanged and is not part of this work.

Under this design the leak is not merely closed but **structurally
impossible**: a subscriber holds no frames, so there is nothing for it to
accumulate, and a subscriber that stops reading is noticed and released
rather than being discovered later.

## 9. The desktop client

The client has to learn three things.

- **It has to read the `event:` field.** Today it reads only `data:` lines
  and decodes every one as a `WorkflowEvent`. The two control frames are
  not events, so decoding them as such would be a `BackendError.Malformed`
  — a genuine failure for a frame that is perfectly well formed.
- **It has to remember the last id it saw**, and send it as `Last-Event-ID`
  when it opens a stream.
- **It has to reconnect once when the stream ends with an overflow.** The
  overflow frame says the run is still going and this subscriber fell
  behind, so the client reopens the stream with its last id and carries on.

**What the client does not do.** It does not reconnect in a loop. A retry
loop needs a backoff, a budget and some operator-visible state about how
many attempts have been made, and that is a different design. One reconnect
on an overflow is what this slice covers; a gap, by contrast, is not
something to retry blindly — the client is told what it missed and
re-fetches the document's state instead.

## 10. What is tested

The behaviour is proved by tests that name it, and every test that pins a
fix is verified to fail without it.

**The bound.**
- A subscriber holds no frames: the session's history is the only copy, and
  it stays within its bound however long a run goes on.
- A subscriber that is not being read is noticed: it is marked overflowed
  once the frames it has not read have left the session's memory.
- The publisher keeps going for the other subscribers without blocking and
  without dropping anything for them.

**The overflow.**
- The generator ends the overflowed stream with the `overflow` control
  frame, and the connection closes.
- The overflowed subscription is released — the session's subscriber count
  returns to what it was.

**The identity.**
- Every sequence frame the server emits carries an `id:`, and the ids are
  strictly increasing with no repeats.
- The control frames carry no `id:`.

**The resume.**
- A client reconnecting with `Last-Event-ID` is served exactly the frames
  after that id — no duplicates, no gaps.
- A subscriber that falls behind and then resumes is served every frame
  the session still remembers.
- A client reconnecting with an id the session has rotated past is served
  the retained frames preceded by a `gap` frame naming the hole.
- A `Last-Event-ID` that is not an integer is refused with 400.
- A client that opens a stream with no `Last-Event-ID` is served the
  session's retained history, from its oldest retained frame.

**The cancellation.**
- Closing a stream releases its subscription.
- A terminal frame closes every live subscriber's stream.

**The client.**
- The client reads the `event:` field and recognises the two control
  frames rather than failing on them.
- The client sends its last id when it reconnects, and reconnects once on
  an overflow.

## 11. What this does not do

- It does not make the stream lossless for a client that was away longer
  than the session's memory. That client gets a gap, is told, and re-fetches
  the authoritative state.
- It does not change the history's size, or what the history is for a late
  subscriber. It changes who reads it.
- It does not change the terminal frames, the fan-out semantics, or the
  fact that concurrent subscribers each receive the whole sequence.
- It does not establish production readiness. The installed-wheel
  deployment, the accessibility evidence and the remaining §29.6 items are
  untouched by it.

## 12. The files

| File | What changes |
|---|---|
| `backend/src/lmeditor/server/session.py` | The cursor, the frame ids, the overrun, the resume, the counters |
| `backend/src/lmeditor/server/routes.py` | The `id:` on each frame, the two control frames, the `Last-Event-ID` header |
| `backend/tests/test_stream_contract.py` | The proofs in §10 |
| `backend/tests/test_sse_fanout.py` | The overrun's effect on the other subscribers |
| `kotlin/desktop/.../net/SseClient.kt` | The `event:` field, the last id, the reconnect |
| `kotlin/desktop/.../net/SseClientTest.kt` | The client-side proofs |
| `ARCHITECTURE.md` | The ledger, and §29.6's list |
| `docs/production-readiness.md` | The closed item and what remains |
