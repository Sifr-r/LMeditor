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

/** SSE framing and resource ownership, exercised entirely in memory. */
class SseClientTest {
    @Test
    fun `multiple data lines form one JSON event and comments are ignored`() = runBlocking {
        val input = ByteArrayInputStream((": heartbeat\n" +
            "data: {\"event_type\":\"node_start\",\n" +
            "data: \"step\":\"ingest\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n").toByteArray())
        val frames = client(input).events("doc_1").toList()
        assertEquals(1, frames.size)
        assertEquals("doc_1", frames.single().docId)
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
