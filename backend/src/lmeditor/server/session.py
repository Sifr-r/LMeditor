"""Asynchronous session store and event broker for LMeditor streaming workflows."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from lmeditor.core.schemas import HITLReviewPayload, VerificationScorecard, WorkflowEvent
from lmeditor.core.types import EventType, WorkflowStep
from lmeditor.verification.citation_checker import DEFAULT_F_BETA_THRESHOLD

#: Session lifecycle states. ``is_completed`` is True only for
#: :data:`STATUS_COMPLETED`; a rejection and a failure are terminal without ever
#: being reported as a successful completion.
STATUS_RUNNING = "running"
STATUS_AWAITING_HUMAN_REVIEW = "awaiting_human_review"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

#: Number of events retained per session so a late or reconnecting subscriber
#: receives recent history instead of an empty stream. Bounded because the
#: buffer lives for the whole session; an unbounded log would grow with the
#: run. It is also the bound on how far a subscriber may fall behind before
#: it is disconnected.
STREAM_REPLAY_BUFFER: int = 512


class HITLReviewNotPendingError(RuntimeError):
    """Raised when a decision arrives for a review that is not awaiting one."""


class HITLDecisionMissingError(RuntimeError):
    """Raised when a review is released without a recorded decision payload."""


class ActiveSessionExistsError(RuntimeError):
    """Raised when a live (non-terminal) session already owns a ``doc_id``.

    Creating a second session for the same document would orphan the first
    one's background worker, which keeps running and publishing into an object
    nothing can reach any more (AUDIT finding 13). Replacing a session that
    already reached a terminal outcome stays allowed.
    """

    def __init__(self, doc_id: str, status: str) -> None:
        self.doc_id = doc_id
        self.status = status
        super().__init__(
            f"document {doc_id!r} already has a live workflow session (status={status!r})"
        )


def scorecard_payload(
    scorecard: dict[str, Any] | VerificationScorecard | None,
) -> dict[str, Any]:
    """Render a scorecard for the wire.

    Accepts either the aggregated payload produced by the workflow graph or
    the domain model, so a caller never has to know which one it holds.
    """
    if scorecard is None:
        return {}
    if isinstance(scorecard, dict):
        return dict(scorecard)
    return scorecard.model_dump(mode="json")


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


class WorkflowSession:
    """Encapsulates active document generation state, event fan-out, and HITL signaling.

    The HITL pair is deliberately *review-scoped*: :meth:`open_hitl_review`
    installs a fresh :class:`asyncio.Event` and clears any previous decision, so
    a decision recorded for an earlier review can never resume a later one, and
    :meth:`record_hitl_decision` refuses a second decision for a review that has
    already been answered.
    """

    def __init__(self, doc_id: str, topic: str) -> None:
        self.doc_id: str = doc_id
        self.topic: str = topic
        #: The verification threshold this document is written to. The save
        #: route measures an operator's edited text against it, so the bar the
        #: document was written to is the bar the edit is judged by.
        self.threshold_f_beta: float = DEFAULT_F_BETA_THRESHOLD
        self.current_step: WorkflowStep = WorkflowStep.INGEST
        self.status: str = STATUS_RUNNING
        #: The document-level verification scorecard. Either the domain model or
        #: the aggregated payload produced by the workflow graph; both are
        #: serialised for the client.
        self.scorecard: dict[str, Any] | VerificationScorecard | None = None
        self.hitl_pending: bool = False
        self.hitl_decision: HITLReviewPayload | None = None
        self.hitl_reason: str | None = None
        self.hitl_event: asyncio.Event = asyncio.Event()
        self.hitl_review_seq: int = 0
        self.hitl_requests_published: int = 0
        self.terminal_event_published: bool = False
        self.final_sections: list[dict[str, Any]] = []
        self.is_completed: bool = False
        self.error: str | None = None
        self.rejection_reason: str | None = None
        self._terminated: bool = False
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
        #: Frames refused because they followed the terminal frame. Counted
        #: rather than dropped silently.
        self.post_terminal_events_dropped: int = 0
        #: Subscribers disconnected for falling behind. Counted, because a
        #: subscriber that vanished for being slow is exactly what an
        #: operator needs to see.
        self.overflowed_subscribers: int = 0
        #: Frames a subscriber was never given because it fell behind. The
        #: frames were published to the session's history; they were
        #: withheld from that one subscriber.
        self.frames_withheld: int = 0

    # ------------------------------------------------------------------
    # Subscriber fan-out
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        """True once a terminal outcome was recorded for this session.

        The rule is the session's own terminal flag: the three terminal
        transitions set it, and no suspension point separates setting it
        from publishing the terminal frame, so a session that is closed has
        already published its terminal frame and none will follow it.
        """
        return self._terminated

    @property
    def subscriber_count(self) -> int:
        """Number of subscriptions currently registered for fan-out.

        A subscription leaves this count when its stream's ``finally``
        unsubscribes it, which is after the close rather than at it: the
        close only wakes the subscribers, and each one releases itself.
        """
        return len(self._subscribers)

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

    async def unsubscribe(self, subscription: EventSubscription) -> None:
        """Detach a subscriber so a dropped client stops accumulating frames.

        Idempotent: unsubscribing an unknown or already-detached subscription
        is a no-op, so cleanup can run from a ``finally`` without bookkeeping.
        """
        self._subscribers.pop(subscription.subscription_id, None)

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

    def _raise_if_behind(self, subscription: EventSubscription) -> None:
        """Raise and count if the subscriber has fallen behind the history.

        The rule is the same wherever it is noticed: a subscriber that has
        fallen behind has been denied the frames it has not read, because
        they have left the history and cannot be given to it. The denial is
        counted wherever it is noticed, so that the two reads cannot
        disagree about whether a subscriber was owed frames it did not get.
        """
        oldest = self._oldest_retained_id()
        if oldest > subscription.position + 1:
            overrun = SubscriberOverrun(
                subscription.subscription_id, subscription.position, oldest
            )
            self.overflowed_subscribers += 1
            self.frames_withheld += overrun.frames_missed
            raise overrun

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

        The subscription must be one of this session's own: a subscription
        from another session would be read against the wrong history, and
        its position silently corrupted.
        """
        while True:
            frame = self._frame_after(subscription.position)
            if frame is not None:
                subscription.position = frame[0]
                return frame
            # The frame the subscriber is owed is not in the history. Either
            # the session has not published it yet, or the history has
            # rotated past it and the subscriber has missed frames.
            self._raise_if_behind(subscription)
            if self._terminated:
                return None
            await subscription.wait()

    def drain(self, subscription: EventSubscription) -> list[WorkflowEvent]:
        """The frames published since the subscriber last read, without waiting.

        The frames are the ones the session's history still holds, so a
        subscriber that has fallen behind the history is told so rather
        than handed a shortened sequence.

        The subscription must be one of this session's own: a subscription
        from another session would be read against the wrong history, and
        its position silently corrupted.
        """
        self._raise_if_behind(subscription)
        drained: list[WorkflowEvent] = []
        while True:
            frame = self._frame_after(subscription.position)
            if frame is None:
                return drained
            subscription.position = frame[0]
            drained.append(frame[1])

    async def _close_subscribers(self) -> None:
        """Wake every live subscriber so each one observes the close.

        Every stream must observe the close. The single shared sentinel of the
        previous design could be consumed by one subscriber, leaving the others
        blocked on an empty queue for good.

        The map is not cleared here. A subscription is released by its own
        stream, which unsubscribes in its ``finally``; clearing it here
        would only hide a stream that has not finished yet. A client that
        stops reading leaves one registration behind, and nothing else:
        the frames it has not read live in the session's history, not in it.
        """
        for subscription in tuple(self._subscribers.values()):
            subscription.wake()

    async def publish_event(self, event: WorkflowEvent) -> None:
        """Record an event in the session's history and wake its subscribers.

        The event is appended once, to the one bounded history, and every
        subscriber is woken to read it there: no frame is copied for a
        subscriber that has not asked for it.

        All bookkeeping is synchronous and no ``await`` runs between the checks
        and the mutations, and the only owner of this state is the single event
        loop the session runs on, so no lock is needed. Subscribers are visited
        over a snapshot so a subscriber attaching or detaching concurrently
        cannot mutate the mapping mid-fan-out.
        """
        if self._terminal_event is not None:
            # A frame after the terminal frame would corrupt the replay order a
            # late subscriber receives. Counted, never silently discarded.
            self.post_terminal_events_dropped += 1
            return

        self.current_step = event.step
        if event.event_type is EventType.HITL_REQUEST:
            self.hitl_requests_published += 1
        elif event.event_type in (EventType.WORKFLOW_COMPLETE, EventType.WORKFLOW_ERROR):
            self.terminal_event_published = True
            self._terminal_event = event

        self._history.append((self._next_event_id, event))
        self._next_event_id += 1
        for subscription in tuple(self._subscribers.values()):
            subscription.wake()

    async def _publish_terminal(self, event: WorkflowEvent) -> None:
        """Publish a terminal frame unless one was already published.

        ``workflow.graph.resume_workflow`` emits its own ``workflow_complete``
        when an approved run reaches the end, so the session must not add a
        second terminal frame for the same run.
        """
        if self.terminal_event_published:
            return
        await self.publish_event(event)

    # ------------------------------------------------------------------
    # Human-in-the-loop lifecycle
    # ------------------------------------------------------------------

    def open_hitl_review(self, reason: str) -> int:
        """Open a review and return its sequence number.

        Discards any decision left over from a previous review, which is what
        keeps a stale ``approved`` verdict from resuming a new gate.
        """
        self.status = STATUS_AWAITING_HUMAN_REVIEW
        self.hitl_pending = True
        self.hitl_reason = reason
        self.hitl_decision = None
        self.hitl_event = asyncio.Event()
        self.hitl_review_seq += 1
        return self.hitl_review_seq

    def record_hitl_decision(self, payload: HITLReviewPayload) -> int:
        """Record the operator decision for the currently pending review.

        Raises :class:`HITLReviewNotPendingError` when no review is open, so a
        second or late decision is a client error rather than a silent no-op.
        """
        if not self.hitl_pending:
            raise HITLReviewNotPendingError(
                f"document {self.doc_id!r} has no pending human review; "
                "the decision cannot be recorded"
            )
        self.hitl_decision = payload
        self.hitl_pending = False
        self.status = STATUS_RUNNING
        self.hitl_event.set()
        return self.hitl_review_seq

    async def await_hitl_decision(self) -> HITLReviewPayload:
        """Wait for exactly one recorded decision and return it."""
        await self.hitl_event.wait()
        decision = self.hitl_decision
        if decision is None:
            raise HITLDecisionMissingError(
                f"human review for document {self.doc_id!r} was released without a decision"
            )
        self.hitl_pending = False
        return decision

    # ------------------------------------------------------------------
    # Terminal outcomes
    # ------------------------------------------------------------------

    async def complete(self, sections: list[dict[str, Any]] | None = None) -> None:
        """Mark workflow session as completed and terminate every subscriber.

        Only ever called once the graph has actually reached its completed
        state; the ``_terminated`` guard keeps the transition idempotent so a
        late caller cannot emit a second ``workflow_complete`` frame.
        """
        if self._terminated:
            return
        self._terminated = True
        self.is_completed = True
        self.status = STATUS_COMPLETED
        self.hitl_pending = False
        if sections is not None:
            self.final_sections = sections
        card = scorecard_payload(self.scorecard)
        await self._publish_terminal(
            WorkflowEvent(
                event_type=EventType.WORKFLOW_COMPLETE,
                step=WorkflowStep.COMPLETE,
                doc_id=self.doc_id,
                data={
                    "sections": self.final_sections,
                    "scorecard": card,
                    # Whether every section of the manuscript passed
                    # verification. The client must be able to tell a fully
                    # grounded document from one the reviewer released with
                    # unverified sections still in it.
                    "verified": bool(card.get("all_passed")) if card else False,
                    "unverified_section_ids": list(card.get("unverified_section_ids", []))
                    if card
                    else [],
                },
            )
        )
        await self._close_subscribers()  # Sentinel closes every live SSE stream

    async def fail(self, error_message: str) -> None:
        """Record workflow failure and publish error event.

        Terminal but never a completion: ``is_completed`` stays False so a failed
        run is not reported as a successful one, matching the invariant on
        :data:`STATUS_FAILED` and the behaviour of :meth:`terminate_rejected`.
        """
        if self._terminated:
            return
        self._terminated = True
        self.error = error_message
        self.is_completed = False
        self.status = STATUS_FAILED
        self.hitl_pending = False
        await self._publish_terminal(
            WorkflowEvent(
                event_type=EventType.WORKFLOW_ERROR,
                step=self.current_step,
                doc_id=self.doc_id,
                data={"error": error_message},
            )
        )
        await self._close_subscribers()

    async def terminate_rejected(self, reason: str) -> None:
        """Terminate the session because the operator rejected the manuscript.

        A rejection is terminal but is *not* a completion: ``is_completed``
        stays False and no ``sections`` payload is published. The stream is
        closed with ``workflow_error`` because the client contract defines
        exactly two terminal event names.
        """
        if self._terminated:
            return
        self._terminated = True
        self.status = STATUS_REJECTED
        self.rejection_reason = reason
        self.is_completed = False
        self.hitl_pending = False
        await self._publish_terminal(
            WorkflowEvent(
                event_type=EventType.WORKFLOW_ERROR,
                step=WorkflowStep.HITL_GATE,
                doc_id=self.doc_id,
                data={
                    "error": f"manuscript rejected by reviewer: {reason}",
                    "rejected": True,
                    "reason": reason,
                },
            )
        )
        await self._close_subscribers()


class SessionManager:
    """Thread-safe registry of active manuscript generation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, WorkflowSession] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def create_session(self, doc_id: str, topic: str) -> WorkflowSession:
        """Create and register a new workflow session.

        Raises :class:`ActiveSessionExistsError` when a session for ``doc_id``
        is still live: silently replacing it would orphan the running worker's
        session object while the worker kept publishing into it. A session that
        already reached a terminal outcome is replaced as before.
        """
        async with self._lock:
            existing = self._sessions.get(doc_id)
            if existing is not None and not existing.is_closed:
                raise ActiveSessionExistsError(doc_id, existing.status)
            session = WorkflowSession(doc_id=doc_id, topic=topic)
            self._sessions[doc_id] = session
            return session

    async def get_session(self, doc_id: str) -> WorkflowSession | None:
        """Retrieve an existing session by doc_id."""
        async with self._lock:
            return self._sessions.get(doc_id)

    async def remove_session(self, doc_id: str) -> None:
        """Clean up and remove a completed or abandoned session.

        A document discarded while still suspended would otherwise pin its
        checkpoint in the workflow registry, so the retained run is released.
        The document's retrieval index goes with it: the index belongs to the
        document, not to the run, and a discarded document is the one moment
        when nothing can still need its sources.
        """
        from lmeditor.workflow.graph import release_workflow
        from lmeditor.workflow.nodes import release_provenance_index

        release_workflow(doc_id)
        release_provenance_index(doc_id)
        async with self._lock:
            self._sessions.pop(doc_id, None)


# Global singleton session manager
session_manager = SessionManager()

__all__ = [
    "STATUS_AWAITING_HUMAN_REVIEW",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "STATUS_RUNNING",
    "STREAM_REPLAY_BUFFER",
    "ActiveSessionExistsError",
    "EventSubscription",
    "HITLDecisionMissingError",
    "HITLReviewNotPendingError",
    "SessionManager",
    "SubscriberOverrun",
    "WorkflowSession",
    "session_manager",
]
