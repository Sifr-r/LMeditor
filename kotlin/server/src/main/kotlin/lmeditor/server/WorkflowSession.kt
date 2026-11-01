package lmeditor.server

import lmeditor.core.ActiveSessionExistsError
import lmeditor.core.EventType
import lmeditor.core.HITLDecisionMissingError
import lmeditor.core.HITLReviewNotPendingError
import lmeditor.core.HITLReviewPayload
import lmeditor.core.SectionStatus
import lmeditor.core.WorkflowEvent
import lmeditor.core.WorkflowStep
import java.util.ArrayDeque
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** The session is running. */
const val STATUS_RUNNING: String = "running"

/** The session is waiting for a human decision. */
const val STATUS_AWAITING_HUMAN_REVIEW: String = "awaiting_human_review"

/** The session finished successfully. */
const val STATUS_COMPLETED: String = "completed"

/** The session failed. */
const val STATUS_FAILED: String = "failed"

/** The session was rejected by the reviewer. */
const val STATUS_REJECTED: String = "rejected"

/**
 * How many events are retained per session.
 *
 * A late or reconnecting subscriber receives recent history instead of an
 * empty queue. Bounded, because the buffer lives for the whole session; an
 * unbounded log would grow with the run.
 */
const val STREAM_REPLAY_BUFFER: Int = 512

/**
 * One frame on a subscriber's stream.
 *
 * The terminal sentinel is a value of its own rather than a null: a
 * `LinkedBlockingQueue` refuses null elements, and a frame that is a frame is
 * easier to reason about than one that is an absence.
 */
sealed interface StreamFrame {

    /** A published event. */
    data class Published(val event: WorkflowEvent) : StreamFrame

    /** The sentinel that closes the stream: no more frames will arrive. */
    data object Terminal : StreamFrame
}

/**
 * One subscriber's private view of a session's event stream.
 *
 * Each subscription owns its buffer, so N concurrent streams on one document
 * each observe the *complete* frame sequence instead of dividing it.
 *
 * The buffer is unbounded on purpose: a bounded one would either block the
 * publishing worker (stalling every other stream) or silently drop frames a
 * slow client never received. A client that stops reading is released by
 * [WorkflowSession.unsubscribe], after which nothing is queued for it.
 */
class EventSubscription(
    /** This subscription's identifier. */
    val subscriptionId: Int,
    frames: List<StreamFrame>,
) {
    private val queue = LinkedBlockingQueue<StreamFrame>()

    init {
        for (frame in frames) queue.put(frame)
    }

    /** Buffer one frame for this subscriber; never blocks, never throws. */
    fun deliver(frame: StreamFrame) {
        queue.put(frame)
    }

    /** Await the next frame. */
    fun get(): StreamFrame = queue.take()

    /** Pop every frame buffered so far, stopping at the terminal sentinel. */
    fun drain(): List<WorkflowEvent> {
        val drained = mutableListOf<WorkflowEvent>()
        while (true) {
            when (val frame = queue.poll() ?: return drained) {
                is StreamFrame.Terminal -> return drained
                is StreamFrame.Published -> drained.add(frame.event)
            }
        }
    }
}

/**
 * Active document generation state, event fan-out, and human-in-the-loop
 * signalling.
 *
 * The human-review pair is deliberately *review-scoped*: [openHitlReview]
 * installs a fresh latch and clears any previous decision, so a decision
 * recorded for an earlier review can never resume a later one, and
 * [recordHitlDecision] refuses a second decision for a review that has
 * already been answered.
 */
class WorkflowSession(
    /** The document this session is generating. */
    val docId: String,
    /** The topic the document is about. */
    val topic: String,
) {

    /**
     * The verification threshold this document is written to.
     *
     * The save route measures an operator's edited text against it, so the bar
     * the document was written to is the bar the edit is judged by.
     */
    var thresholdFBeta: Double = DEFAULT_F_BETA_THRESHOLD

    /** The step the run is currently at. */
    var currentStep: WorkflowStep = WorkflowStep.INGEST

    /** The session's lifecycle status. */
    var status: String = STATUS_RUNNING

    /**
     * The document-level verification scorecard, as the aggregated payload the
     * workflow graph produced.
     */
    var scorecard: Map<String, Any?>? = null

    /** Whether a human decision is being waited for. */
    var hitlPending: Boolean = false

    /** The operator's decision for the pending review. */
    var hitlDecision: HITLReviewPayload? = null

    /** Why the review was opened. */
    var hitlReason: String? = null

    /** How many reviews this session has opened. */
    var hitlReviewSeq: Int = 0
        private set

    /** How many review requests have been published. */
    var hitlRequestsPublished: Int = 0
        private set

    /** Whether a terminal frame has been published. */
    var terminalEventPublished: Boolean = false
        private set

    /** The sections the finished document contains. */
    var finalSections: List<Map<String, Any?>> = emptyList()

    /** Whether the run finished successfully. */
    var isCompleted: Boolean = false

    /** The failure message, when the run failed. */
    var error: String? = null

    /** Why the reviewer rejected the manuscript. */
    var rejectionReason: String? = null

    /**
     * Frames refused because they followed the terminal frame.
     *
     * Counted rather than dropped silently.
     */
    var postTerminalEventsDropped: Int = 0
        private set

    private val lock = ReentrantLock()

    private var hitlLatch = CountDownLatch(1)

    private val subscribers = ConcurrentHashMap<Int, EventSubscription>()

    private var nextSubscriptionId = 0

    private val history = ArrayDeque<WorkflowEvent>()

    private var terminalEvent: WorkflowEvent? = null

    private var terminated = false

    /**
     * True once a terminal outcome was recorded for this session.
     *
     * Equivalent to "the terminal frame and the close sentinel were
     * delivered": no suspension point separates those two steps.
     */
    val isClosed: Boolean
        get() = terminated

    /** The number of live subscriptions currently receiving fan-out. */
    val subscriberCount: Int
        get() = subscribers.size

    // --- subscriber fan-out ------------------------------------------------

    /**
     * Register a subscriber and seed it with the replay buffer.
     *
     * A subscription on an already-closed session comes back **already
     * terminated**: the buffer ends with the terminal frame and the `null`
     * sentinel, so the caller is closed deterministically instead of waiting
     * on a queue nobody will write to again.
     */
    fun subscribe(): EventSubscription = lock.withLock {
        nextSubscriptionId += 1
        val subscription = EventSubscription(nextSubscriptionId, replayFrames())
        if (!terminated) {
            subscribers[subscription.subscriptionId] = subscription
        }
        subscription
    }

    /**
     * Detach a subscriber so a dropped client stops accumulating frames.
     *
     * Idempotent: unsubscribing an unknown or already-detached subscription is
     * a no-op, so cleanup can run from a `finally` without bookkeeping.
     */
    fun unsubscribe(subscription: EventSubscription) {
        subscribers.remove(subscription.subscriptionId)
    }

    /**
     * Frames a new subscriber receives before any live frame.
     *
     * A closed session's history already ends with the terminal frame, so the
     * sentinel is appended exactly once; a session whose terminal frame is
     * missing from its history (a guard suppressed it) gets the frame first, so
     * a late subscriber still sees a terminal outcome.
     */
    private fun replayFrames(): List<StreamFrame> {
        val frames = mutableListOf<StreamFrame>()
        for (event in history) frames.add(StreamFrame.Published(event))
        if (!terminated) return frames
        val terminal = terminalEvent
        if (terminal != null && (history.isEmpty() || history.last() !== terminal)) {
            frames.add(StreamFrame.Published(terminal))
        }
        frames.add(StreamFrame.Terminal)
        return frames
    }

    /** Deliver the terminal sentinel to every live subscriber. */
    private fun closeSubscribers() {
        for (subscription in subscribers.values.toList()) {
            subscription.deliver(StreamFrame.Terminal)
        }
        subscribers.clear()
    }

    /**
     * Publish an event to every live subscriber and to the replay buffer.
     *
     * All bookkeeping is synchronous and no suspension point runs between the
     * checks and the mutations, so no lock is needed beyond the one that
     * guards the subscriber map. Subscribers are visited over a snapshot so a
     * subscriber attaching or detaching concurrently cannot mutate the map
     * mid-fan-out.
     */
    fun publishEvent(event: WorkflowEvent) {
        lock.withLock {
            if (terminalEvent != null) {
                // A frame after the terminal frame would corrupt the replay
                // order a late subscriber receives. Counted, never silently
                // discarded.
                postTerminalEventsDropped += 1
                return
            }

            currentStep = event.step
            when (event.eventType) {
                EventType.HITL_REQUEST -> hitlRequestsPublished += 1
                EventType.WORKFLOW_COMPLETE, EventType.WORKFLOW_ERROR -> {
                    terminalEventPublished = true
                    terminalEvent = event
                }
                else -> Unit
            }

            history.addLast(event)
            while (history.size > STREAM_REPLAY_BUFFER) history.removeFirst()
            for (subscription in subscribers.values.toList()) {
                subscription.deliver(StreamFrame.Published(event))
            }
        }
    }

    /**
     * Publish a terminal frame unless one was already published.
     *
     * The workflow graph emits its own `workflow_complete` when an approved
     * run reaches the end, so the session must not add a second terminal frame
     * for the same run.
     */
    private fun publishTerminal(event: WorkflowEvent) {
        if (terminalEventPublished) return
        publishEvent(event)
    }

    // --- human-in-the-loop lifecycle ---------------------------------------

    /**
     * Open a review and return its sequence number.
     *
     * Discards any decision left over from a previous review, which is what
     * keeps a stale `approved` verdict from resuming a new gate.
     */
    fun openHitlReview(reason: String): Int = lock.withLock {
        status = STATUS_AWAITING_HUMAN_REVIEW
        hitlPending = true
        hitlReason = reason
        hitlDecision = null
        hitlLatch = CountDownLatch(1)
        hitlReviewSeq += 1
        return hitlReviewSeq
    }

    /**
     * Record the operator decision for the currently pending review.
     *
     * @throws HITLReviewNotPendingError when no review is open, so a second or
     *   late decision is a client error rather than a silent no-op.
     */
    fun recordHitlDecision(payload: HITLReviewPayload): Int = lock.withLock {
        if (!hitlPending) {
            throw HITLReviewNotPendingError(
                "document '$docId' has no pending human review; the decision cannot be recorded",
                mapOf("doc_id" to docId),
            )
        }
        hitlDecision = payload
        hitlPending = false
        status = STATUS_RUNNING
        hitlLatch.countDown()
        return hitlReviewSeq
    }

    /** Wait for exactly one recorded decision and return it. */
    fun awaitHitlDecision(): HITLReviewPayload {
        hitlLatch.await()
        val decision = hitlDecision
            ?: throw HITLDecisionMissingError(
                "human review for document '$docId' was released without a decision",
                mapOf("doc_id" to docId),
            )
        hitlPending = false
        return decision
    }

    // --- terminal outcomes -------------------------------------------------

    /**
     * Mark the session completed and terminate every subscriber.
     *
     * Only ever called once the graph has actually reached its completed
     * state; the `terminated` guard keeps the transition idempotent so a late
     * caller cannot emit a second `workflow_complete` frame.
     */
    fun complete(sections: List<Map<String, Any?>>? = null) {
        lock.withLock {
            if (terminated) return
            terminated = true
            isCompleted = true
            status = STATUS_COMPLETED
            hitlPending = false
            if (sections != null) finalSections = sections
            val card = scorecard ?: emptyMap()
            publishTerminal(
                WorkflowEvent(
                    eventType = EventType.WORKFLOW_COMPLETE,
                    step = WorkflowStep.COMPLETE,
                    docId = docId,
                    data = linkedMapOf(
                        "sections" to finalSections,
                        "scorecard" to card,
                        // Whether every section of the manuscript passed
                        // verification. The client must be able to tell a
                        // fully grounded document from one the reviewer
                        // released with unverified sections still in it.
                        "verified" to (card["all_passed"] as? Boolean ?: false),
                        "unverified_section_ids" to
                            (card["unverified_section_ids"] as? List<*> ?: emptyList<Any?>()),
                    ),
                ),
            )
            closeSubscribers()
        }
    }

    /**
     * Record a workflow failure and publish the error event.
     *
     * Terminal but never a completion: [isCompleted] stays false so a failed
     * run is not reported as a successful one.
     */
    fun fail(errorMessage: String) {
        lock.withLock {
            if (terminated) return
            terminated = true
            error = errorMessage
            isCompleted = false
            status = STATUS_FAILED
            hitlPending = false
            publishTerminal(
                WorkflowEvent(
                    eventType = EventType.WORKFLOW_ERROR,
                    step = currentStep,
                    docId = docId,
                    data = linkedMapOf("error" to errorMessage),
                ),
            )
            closeSubscribers()
        }
    }

    /**
     * Terminate the session because the operator rejected the manuscript.
     *
     * A rejection is terminal but is *not* a completion: [isCompleted] stays
     * false and no `sections` payload is published. The stream is closed with
     * `workflow_error` because the client contract defines exactly two
     * terminal event names.
     */
    fun terminateRejected(reason: String) {
        lock.withLock {
            if (terminated) return
            terminated = true
            status = STATUS_REJECTED
            rejectionReason = reason
            isCompleted = false
            hitlPending = false
            publishTerminal(
                WorkflowEvent(
                    eventType = EventType.WORKFLOW_ERROR,
                    step = WorkflowStep.HITL_GATE,
                    docId = docId,
                    data = linkedMapOf(
                        "error" to "manuscript rejected by reviewer: $reason",
                        "rejected" to true,
                        "reason" to reason,
                    ),
                ),
            )
            closeSubscribers()
        }
    }
}

/** The registry of active manuscript generation sessions. */
class SessionManager {

    private val sessions = ConcurrentHashMap<String, WorkflowSession>()
    private val lock = ReentrantLock()

    /**
     * Create and register a new workflow session.
     *
     * @throws ActiveSessionExistsError when a session for [docId] is still
     *   live: silently replacing it would orphan the running worker's session
     *   object while the worker kept publishing into it. A session that
     *   already reached a terminal outcome is replaced as before.
     */
    fun createSession(docId: String, topic: String): WorkflowSession = lock.withLock {
        val existing = sessions[docId]
        if (existing != null && !existing.isClosed) {
            throw ActiveSessionExistsError(docId, existing.status)
        }
        val session = WorkflowSession(docId, topic)
        sessions[docId] = session
        return session
    }

    /** Retrieve an existing session by document id. */
    fun getSession(docId: String): WorkflowSession? = sessions[docId]

    /** Clean up and remove a completed or abandoned session. */
    fun removeSession(docId: String) {
        sessions.remove(docId)
    }

    /** How many sessions the manager holds. */
    val size: Int
        get() = sessions.size
}

/**
 * The threshold a section must reach to count as verified.
 *
 * The same number the Python tier uses -- `citation_checker.DEFAULT_F_BETA_THRESHOLD`
 * -- so a document written by one tier is judged by the other the same way. A
 * section scoring between 0.5 and 0.85 is *not* verified; treating it as
 * verified would ship a document the Python tier would have bounced.
 */
const val DEFAULT_F_BETA_THRESHOLD: Double = 0.85

/** The section status a freshly saved, unmeasured section carries. */
val SAVE_SECTION_STATUS: SectionStatus = SectionStatus.PENDING
