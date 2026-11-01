package lmeditor.server

import lmeditor.core.ActiveSessionExistsError
import lmeditor.core.EventType
import lmeditor.core.HITLDecisionMissingError
import lmeditor.core.HITLReviewNotPendingError
import lmeditor.core.HITLReviewPayload
import lmeditor.core.SourceChunk
import lmeditor.core.SourceKind
import lmeditor.core.WorkflowEvent
import lmeditor.core.WorkflowStep
import java.time.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The session broker's rules.
 *
 * These are the rules that decide whether a client sees a finished document
 * or a stream that never closes, so they are pinned here rather than left to
 * the reader of the code.
 */
class WorkflowSessionTest {

    private fun event(
        type: EventType,
        step: WorkflowStep = WorkflowStep.DRAFT_SECTIONS,
        docId: String = "doc-1",
    ) = WorkflowEvent(eventType = type, step = step, docId = docId, data = emptyMap())

    // --- fan-out -----------------------------------------------------------

    @Test
    fun `two subscribers each see the whole frame sequence`() {
        val session = WorkflowSession("doc-1", "topic")
        val first = session.subscribe()
        val second = session.subscribe()

        session.publishEvent(event(EventType.TOKEN_DELTA))

        assertEquals(1, first.drain().size)
        assertEquals(1, second.drain().size)
    }

    @Test
    fun `a frame published before a subscription is replayed to it`() {
        val session = WorkflowSession("doc-1", "topic")
        session.publishEvent(event(EventType.NODE_START))

        val subscription = session.subscribe()
        val replayed = subscription.drain()

        assertEquals(1, replayed.size)
        assertEquals(EventType.NODE_START, replayed.first().eventType)
    }

    @Test
    fun `an unsubscribed subscriber stops receiving frames`() {
        val session = WorkflowSession("doc-1", "topic")
        val subscription = session.subscribe()
        session.unsubscribe(subscription)

        session.publishEvent(event(EventType.TOKEN_DELTA))

        assertTrue(subscription.drain().isEmpty())
        assertEquals(0, session.subscriberCount)
    }

    @Test
    fun `unsubscribing twice is a no-op`() {
        val session = WorkflowSession("doc-1", "topic")
        val subscription = session.subscribe()
        session.unsubscribe(subscription)
        session.unsubscribe(subscription)
        assertEquals(0, session.subscriberCount)
    }

    @Test
    fun `a frame after the terminal frame is counted and not delivered`() {
        val session = WorkflowSession("doc-1", "topic")
        val subscription = session.subscribe()
        session.complete()

        session.publishEvent(event(EventType.TOKEN_DELTA))

        assertEquals(1, session.postTerminalEventsDropped)
        assertTrue(subscription.drain().none { it.eventType == EventType.TOKEN_DELTA })
    }

    @Test
    fun `the replay buffer is bounded`() {
        val session = WorkflowSession("doc-1", "topic")
        repeat(STREAM_REPLAY_BUFFER + 50) { index ->
            session.publishEvent(event(EventType.TOKEN_DELTA, WorkflowStep.values()[index % 10]))
        }
        val replayed = session.subscribe().drain()
        assertEquals(STREAM_REPLAY_BUFFER, replayed.size)
    }

    // --- terminal outcomes -------------------------------------------------

    @Test
    fun `completing publishes one terminal frame and closes every subscriber`() {
        val session = WorkflowSession("doc-1", "topic")
        val first = session.subscribe()
        val second = session.subscribe()

        session.complete(listOf(mapOf("section_id" to "s1")))

        assertTrue(session.isCompleted)
        assertEquals(STATUS_COMPLETED, session.status)
        assertTrue(session.isClosed)
        assertTrue(session.terminalEventPublished)

        val frames = first.drain()
        assertEquals(EventType.WORKFLOW_COMPLETE, frames.last().eventType)
        @Suppress("UNCHECKED_CAST")
        val sections = frames.last().data["sections"] as List<Map<String, Any?>>
        assertEquals("s1", sections.first()["section_id"])
        // Both subscribers observed the terminal frame; neither was left
        // waiting on a queue nobody would write to again.
        assertEquals(EventType.WORKFLOW_COMPLETE, second.drain().last().eventType)
    }

    @Test
    fun `completing twice publishes only one terminal frame`() {
        val session = WorkflowSession("doc-1", "topic")
        session.complete()
        session.complete()
        assertTrue(session.terminalEventPublished)
    }

    @Test
    fun `a failure is terminal but is never a completion`() {
        val session = WorkflowSession("doc-1", "topic")
        session.fail("the model refused")

        assertFalse(session.isCompleted)
        assertEquals(STATUS_FAILED, session.status)
        assertEquals("the model refused", session.error)
        assertTrue(session.isClosed)
    }

    @Test
    fun `a rejection is terminal but is never a completion`() {
        val session = WorkflowSession("doc-1", "topic")
        session.terminateRejected("the argument is wrong")

        assertFalse(session.isCompleted)
        assertEquals(STATUS_REJECTED, session.status)
        assertEquals("the argument is wrong", session.rejectionReason)
    }

    @Test
    fun `a subscriber on a closed session comes back already terminated`() {
        val session = WorkflowSession("doc-1", "topic")
        session.complete()

        val subscription = session.subscribe()
        val frames = subscription.drain()

        assertEquals(EventType.WORKFLOW_COMPLETE, frames.last().eventType)
        assertEquals(0, session.subscriberCount)
    }

    // --- the human review --------------------------------------------------

    @Test
    fun `a decision recorded before the wait is still delivered`() {
        val session = WorkflowSession("doc-1", "topic")
        session.openHitlReview("needs review")
        session.recordHitlDecision(HITLReviewPayload(approved = true))

        val decision = session.awaitHitlDecision()
        assertTrue(decision.approved)
    }

    @Test
    fun `a decision for a review that is not pending is refused`() {
        val session = WorkflowSession("doc-1", "topic")
        assertFailsWith<HITLReviewNotPendingError> {
            session.recordHitlDecision(HITLReviewPayload(approved = true))
        }
    }

    @Test
    fun `opening a new review discards the previous decision`() {
        val session = WorkflowSession("doc-1", "topic")
        session.openHitlReview("first")
        session.recordHitlDecision(HITLReviewPayload(approved = false))
        session.openHitlReview("second")

        assertNull(session.hitlDecision)
        assertTrue(session.hitlPending)
        assertEquals(2, session.hitlReviewSeq)
    }

    @Test
    fun `a second decision for the same review is refused`() {
        val session = WorkflowSession("doc-1", "topic")
        session.openHitlReview("review")
        session.recordHitlDecision(HITLReviewPayload(approved = true))

        assertFailsWith<HITLReviewNotPendingError> {
            session.recordHitlDecision(HITLReviewPayload(approved = false))
        }
    }

    // --- the registry ------------------------------------------------------

    @Test
    fun `a live session refuses to be replaced`() {
        val manager = SessionManager()
        manager.createSession("doc-1", "topic")

        assertFailsWith<ActiveSessionExistsError> { manager.createSession("doc-1", "topic") }
    }

    @Test
    fun `a finished session may be replaced`() {
        val manager = SessionManager()
        val first = manager.createSession("doc-1", "topic")
        first.complete()

        val second = manager.createSession("doc-1", "topic")
        assertNotNull(second)
    }
}

/** The source library's rules. */
class SourceLibraryTest {

    private fun chunk(id: String): SourceChunk = SourceChunk.create(
        documentId = "doc",
        text = "passage $id",
        charStart = 0,
        charEnd = 8,
        byteStart = 0,
        byteEnd = 8,
    )

    private fun stored(
        id: String,
        addedAt: Instant,
        chunks: List<SourceChunk> = listOf(chunk(id)),
    ) = StoredSource(
        sourceId = id,
        title = "Source $id",
        kind = SourceKind.TEXT,
        origin = "/tmp/$id.txt",
        chunks = chunks,
        addedAt = addedAt,
    )

    @Test
    fun `a stored source is found by its id`() {
        val library = SourceLibrary()
        library.add(stored("a", Instant.EPOCH))
        assertEquals("Source a", library.get("a")?.title)
    }

    @Test
    fun `an unknown id is simply absent`() {
        val library = SourceLibrary()
        assertNull(library.get("missing"))
    }

    @Test
    fun `the chunks of the named sources come back in the order asked for`() {
        val library = SourceLibrary()
        library.add(stored("a", Instant.EPOCH, listOf(chunk("a1"), chunk("a2"))))
        library.add(stored("b", Instant.EPOCH.plusSeconds(1), listOf(chunk("b1"))))

        val chunks = library.chunksFor(listOf("b", "a"))
        assertEquals(listOf("passage b1", "passage a1", "passage a2"), chunks.map { it.text })
    }

    @Test
    fun `an unknown source id is skipped rather than fatal`() {
        val library = SourceLibrary()
        library.add(stored("a", Instant.EPOCH))
        assertEquals(1, library.chunksFor(listOf("missing", "a")).size)
    }

    @Test
    fun `the library evicts the oldest entry when it is full`() {
        val library = SourceLibrary(maxSources = 2)
        library.add(stored("oldest", Instant.EPOCH))
        library.add(stored("middle", Instant.EPOCH.plusSeconds(1)))
        library.add(stored("newest", Instant.EPOCH.plusSeconds(2)))

        assertEquals(2, library.size)
        assertNull(library.get("oldest"))
        assertNotNull(library.get("newest"))
    }

    @Test
    fun `the sources are listed oldest first`() {
        val library = SourceLibrary()
        library.add(stored("second", Instant.EPOCH.plusSeconds(1)))
        library.add(stored("first", Instant.EPOCH))

        assertEquals(listOf("first", "second"), library.list().map { it.sourceId })
    }

    @Test
    fun `a source can be removed`() {
        val library = SourceLibrary()
        library.add(stored("a", Instant.EPOCH))
        assertTrue(library.remove("a"))
        assertFalse(library.remove("a"))
    }
}
