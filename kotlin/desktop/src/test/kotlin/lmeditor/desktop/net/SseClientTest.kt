package lmeditor.desktop.net

import java.io.ByteArrayInputStream
import java.io.InputStream
import java.util.concurrent.CompletableFuture
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import lmeditor.core.WorkflowEvent

/** SSE framing and resource ownership, exercised entirely in memory. */
class SseClientTest {
    @Test
    fun `multiple data lines form one JSON event and comments are ignored`() = runBlocking {
        val input = ByteArrayInputStream((": heartbeat\n" +
            "data: {\"event_type\":\"node_start\",\n" +
            "data: \"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n").toByteArray())
        val messages = client(input).events("doc_1").toList()
        assertEquals(1, messages.size)
        assertEquals("doc_1", messages.events().single().docId)
    }

    @Test
    fun `refused streams close their response body`() = runBlocking {
        val input = TrackedInput()
        val failure = assertFailsWith<BackendError.Refused> { client(input, 403).events("doc_1").toList() }
        assertEquals(403, failure.status)
        assertTrue(input.closed)
    }

    @Test
    fun `malformed frames report errors and release their body`() = runBlocking {
        var closed = false
        val input = object : ByteArrayInputStream("data: not json\n\n".toByteArray()) {
            override fun close() { closed = true; super.close() }
        }
        assertFailsWith<BackendError.Malformed> { client(input).events("doc_1").toList() }
        assertTrue(closed)
    }

    @Test
    fun `cancelling an idle stream closes and unblocks its reader`() = runBlocking {
        val input = TrackedInput()
        val job = launch { client(input).events("doc_1").collect() }
        withTimeout(5_000) { input.readStarted.await() }
        withTimeout(5_000) { job.cancelAndJoin() }
        assertTrue(input.closed)
    }

    @Test
    fun `the stream carries the credential the API requires`() = runBlocking<Unit> {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, ByteArrayInputStream(ByteArray(0))))
        }
        SseClient(http, "http://example.test", ApiCredential.Read("test-token")).events("doc_1").toList()
        assertEquals(
            "Bearer test-token",
            http.lastRequest?.headers()?.firstValue("Authorization")?.orElse(""),
        )
    }

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
        var requests = 0
        val http = StubHttpClient {
            requests += 1
            CompletableFuture.completedFuture(StubResponse(it, 200, responses.removeFirst()))
        }

        val messages = SseClient(http, "http://example.test").events("doc_1").toList()

        assertEquals(listOf("node_start", "workflow_complete"), messages.events().map { it.eventType.wire })
        assertEquals(2, requests)
        assertEquals("1", http.lastRequest?.headers()?.firstValue("Last-Event-ID")?.orElse(null))
        // The caller is told what the server withheld: the control frame is
        // read for what it says, not thrown away.
        val overflow = messages.filterIsInstance<SseMessage.Overflow>().single()
        assertEquals(3, overflow.behind)
        assertEquals(2, overflow.oldestRetained)
    }

    @Test
    fun `a second overflow does not reopen the stream`() = runBlocking {
        val overflowing = (
            "id: 1\n" +
                "event: node_start\n" +
                "data: {\"event_type\":\"node_start\",\"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n" +
                "event: overflow\n" +
                "data: {\"reason\":\"the subscriber fell behind\",\"behind\":3,\"oldest_retained\":2}\n\n"
            ).toByteArray()
        // Two overflowing streams, and then an empty one: a client that
        // reconnects without a bound would open a third request and finish
        // on the empty one, so the request count is what tells the two
        // behaviours apart.
        val responses = ArrayDeque(
            listOf(ByteArrayInputStream(overflowing), ByteArrayInputStream(overflowing))
        )
        var requests = 0
        val http = StubHttpClient {
            requests += 1
            val next = responses.removeFirstOrNull()
            CompletableFuture.completedFuture(
                StubResponse(it, 200, next ?: ByteArrayInputStream(ByteArray(0)))
            )
        }

        val messages = SseClient(http, "http://example.test").events("doc_1").toList()

        assertEquals(listOf("node_start", "node_start"), messages.events().map { it.eventType.wire })
        assertEquals(2, requests)
        // Both overflows reached the caller: neither was swallowed.
        assertEquals(2, messages.filterIsInstance<SseMessage.Overflow>().size)
    }

    @Test
    fun `a comment block with no data is read past`() = runBlocking {
        val input = ByteArrayInputStream(
            (": heartbeat\n" +
                "\n" +
                "id: 7\n" +
                "event: node_start\n" +
                "data: {\"event_type\":\"node_start\",\"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n")
                .toByteArray()
        )
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, input))
        }

        val messages = SseClient(http, "http://example.test").events("doc_1").toList()

        assertEquals(listOf("node_start"), messages.events().map { it.eventType.wire })
        // The comment block is not a message of its own.
        assertEquals(1, messages.size)
    }

    @Test
    fun `a gap frame names what was missed and the stream carries on`() = runBlocking {
        val input = ByteArrayInputStream(
            ("event: gap\n" +
                "data: {\"requested_after\":1,\"oldest_retained\":3}\n\n" +
                "id: 5\n" +
                "event: node_start\n" +
                "data: {\"event_type\":\"node_start\",\"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n")
                .toByteArray()
        )
        var requests = 0
        val http = StubHttpClient {
            requests += 1
            CompletableFuture.completedFuture(StubResponse(it, 200, input))
        }

        val messages = SseClient(http, "http://example.test").events("doc_1").toList()

        assertEquals(listOf("node_start"), messages.events().map { it.eventType.wire })
        assertEquals(1, requests)
        assertEquals(null, http.lastRequest?.headers()?.firstValue("Last-Event-ID")?.orElse(null))
        // The caller is told which frames are gone: the control frame is read
        // for what it says, not thrown away.
        val gap = messages.filterIsInstance<SseMessage.Gap>().single()
        assertEquals(1, gap.requestedAfter)
        assertEquals(3, gap.oldestRetained)
    }

    @Test
    fun `a control frame the client cannot read is reported`() = runBlocking {
        val notAnObject = "event: gap\ndata: [1, 2]\n\n"
        val notAnInteger = "event: overflow\ndata: {\"behind\":\"three\",\"oldest_retained\":2}\n\n"
        val missingField = "event: gap\ndata: {\"requested_after\":1}\n\n"
        for (payload in listOf(notAnObject, notAnInteger, missingField)) {
            val input = ByteArrayInputStream(payload.toByteArray())
            assertFailsWith<BackendError.Malformed> { client(input).events("doc_1").toList() }
        }
    }

    /** The workflow events among the messages the stream carried. */
    private fun List<SseMessage>.events(): List<WorkflowEvent> =
        filterIsInstance<SseMessage.Event>().map { it.event }

    private fun client(input: InputStream, status: Int = 200): SseClient = SseClient(
        StubHttpClient { CompletableFuture.completedFuture(StubResponse(it, status, input)) },
        "http://example.test",
    )

    private class TrackedInput : InputStream() {
        val readStarted = CompletableDeferred<Unit>()
        private val monitor = Object()
        @Volatile var closed = false
            private set
        override fun read(): Int = synchronized(monitor) {
            readStarted.complete(Unit)
            while (!closed) monitor.wait()
            -1
        }
        override fun close() = synchronized(monitor) {
            closed = true
            monitor.notifyAll()
        }
    }
}
