package lmeditor.desktop.model

import java.io.ByteArrayInputStream
import java.io.File
import java.net.Authenticator
import java.net.CookieHandler
import java.net.ProxySelector
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpHeaders
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.time.Duration
import java.util.Optional
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executor
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSession
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import lmeditor.core.json.Json
import lmeditor.desktop.net.bodyBytes

/** Controller regressions use controlled HTTP completions, without sockets or sleeps. */
class StudioControllerTest {
    @Test
    fun `invalid backend input preserves the active connection and reports validation`() {
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, ControlledHttpClient())
            controller.setTopic("A topic")
            controller.setBackendUrl("file:///tmp/backend")
            assertEquals(StudioState.DEFAULT_BACKEND_URL, controller.state.value.backendUrl)
            assertEquals("A topic", controller.state.value.topic)
            assertNotNull(controller.state.value.error)
        } finally { scope.cancel() }
    }

    @Test
    fun `switching backend cancels old work and clears its document`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            val stream = http.next()
            controller.checkHealth()
            val oldHealth = http.next()
            controller.setBackendUrl("http://new-backend:8000")
            assertTrue(oldHealth.future.isCancelled)
            assertTrue(stream.future.isCancelled)
            val state = controller.state.value
            assertEquals("http://new-backend:8000", state.backendUrl)
            assertEquals(null, state.docId)
            assertEquals("", state.status)
            assertEquals(null, state.health)
            assertEquals(null, state.pendingReview)
            assertEquals(false, state.busy)
            assertEquals("A topic", state.topic)
            oldHealth.reply("""{"status":"old"}""")
            assertEquals(state, controller.state.value)
        } finally { scope.cancel() }
    }

    @Test
    fun `the rest client and the stream carry the same credential`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            val run = http.next()
            run.reply("""{"doc_id":"doc_1","status":"running"}""")
            val stream = http.next()
            controller.checkHealth()
            val health = http.next()
            health.reply("""{"status":"ok","version":"0.1.0","lm":{"configured":false,"is_stub":true}}""")
            val rest = run.request.headers().firstValue("Authorization").orElse("")
            val streamed = stream.request.headers().firstValue("Authorization").orElse("")
            assertTrue(rest.isNotEmpty())
            assertEquals(rest, streamed)
        } finally { scope.cancel() }
    }

    @Test
    fun `stream failure is visible without failing the controller scope`() = runBlocking {
        val http = ControlledHttpClient()
        val job = Job()
        val scope = CoroutineScope(job + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply("", status = 503, stream = true)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null } }
            assertTrue(failed.error.orEmpty().contains("503"))
            assertTrue(job.isActive)
        } finally { scope.cancel() }
    }

    @Test
    fun `replacing a run ignores the old stream completion`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            val oldStream = http.next()
            // The first run is stopped on purpose before the second is armed.
            controller.stopRun()
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_2","status":"running"}""")
            http.next()
            assertTrue(oldStream.future.isCancelled)
            oldStream.reply("", status = 503, stream = true)
            assertEquals("doc_2", controller.state.value.docId)
            assertEquals(null, controller.state.value.error)
        } finally { scope.cancel() }
    }

    @Test
    fun `the window says the run has begun before the server answers`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            // The request has not been answered yet: the strip must not read
            // as idle while the run is being armed.
            assertEquals("starting", controller.state.value.status)
            assertEquals(true, controller.state.value.busy)
        } finally { scope.cancel() }
    }

    @Test
    fun `the window stays busy while the run streams`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next()
            // The stream is open: the run is still going, so the window says so.
            assertEquals("running", controller.state.value.status)
            assertEquals(true, controller.state.value.busy)
            assertEquals(true, controller.runInFlight())
        } finally { scope.cancel() }
    }

    @Test
    fun `a second run is refused while a run is in flight, and the first is not aborted`() =
        runBlocking {
            val http = ControlledHttpClient()
            val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
            try {
                val controller = StudioController(scope, http)
                controller.setTopic("A topic")
                controller.startRun()
                http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
                val stream = http.next()
                controller.startRun()
                val refused = withTimeout(5_000) { controller.state.first { it.error != null } }
                assertTrue(refused.error.orEmpty().contains("already in flight"))
                // The run that was in flight is untouched: nothing was thrown
                // away behind the operator's back.
                assertEquals("doc_1", refused.docId)
                assertEquals(false, stream.future.isCancelled)
            } finally { scope.cancel() }
        }

    @Test
    fun `stopping a run says so and frees the window`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next()
            controller.stopRun()
            val stopped = withTimeout(5_000) {
                controller.state.first { it.status == "stopped" && !it.busy }
            }
            assertTrue(stopped.notice.orEmpty().contains("Stopped"))
            assertEquals(false, controller.runInFlight())
        } finally { scope.cancel() }
    }

    @Test
    fun `failed export file write is reported to the operator`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            // Keep the stream open while the independent export runs.
            http.next()
            // The project directory already exists and cannot be replaced with file bytes.
            controller.exportDocxTo(java.io.File("."))
            http.next().reply("docx bytes")
            // The run is still streaming, so the window is still busy: the
            // failure is reported on its own.
            val failed = withTimeout(5_000) { controller.state.first { it.error != null } }
            assertTrue(failed.error.orEmpty().contains("exported document"))
        } finally { scope.cancel() }
    }

    @Test
    fun `stream ending without a terminal frame reports an incomplete run`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply("", stream = true)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null } }
            assertTrue(failed.error.orEmpty().contains("before the run completed"))
        } finally { scope.cancel() }
    }

    @Test
    fun `a terminal stream may end successfully`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply("data: {\"event_type\":\"workflow_complete\",\"step\":\"complete\",\"doc_id\":\"doc_1\",\"data\":{}}\n\n", stream = true)
            val completed = withTimeout(5_000) { controller.state.first { it.status == "completed" } }
            assertEquals(null, completed.error)
        } finally { scope.cancel() }
    }

    @Test
    fun `the stream's control frames reach the operator as notices`() = runBlocking {
        // A gap that names frames that are gone says which ones.
        val gap = noticeFromStream(
            "event: gap\ndata: {\"requested_after\":1,\"oldest_retained\":3}\n\n",
            "missed",
        )
        assertTrue(gap.contains("missed frame 2"), gap)

        // A gap that names a frame the session has not published yet claims
        // no loss: nothing was missed there.
        val ahead = noticeFromStream(
            "event: gap\ndata: {\"requested_after\":999,\"oldest_retained\":1}\n\n",
            "has not published yet",
        )
        assertTrue(ahead.contains("No events were missed"), ahead)

        // An overflow says how many frames the server withheld.
        val overflow = noticeFromStream(
            "event: overflow\ndata: {\"reason\":\"the subscriber fell behind\"," +
                "\"behind\":3,\"oldest_retained\":2}\n\n",
            "fell behind",
        )
        assertTrue(overflow.contains("held back 3"), overflow)
        // It does not claim the stream was reopened: the client reopens it on
        // the first overflow only, and a second one ends the run instead.
        assertTrue(overflow.contains("ended that stream"), overflow)
        assertFalse(overflow.contains("reopened"), overflow)
    }

    @Test
    fun `permanent stream loss survives overflow errors and rejected starts until a new run begins`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply(
                "event: gap\ndata: {\"requested_after\":1,\"oldest_retained\":3}\n\n" +
                    "event: overflow\ndata: {\"behind\":3,\"oldest_retained\":2}\n\n",
                stream = true,
            )
            val resumed = http.next()
            val overflowed = withTimeout(5_000) {
                controller.state.first { it.notice?.contains("held back 3") == true }
            }
            val loss = assertNotNull(overflowed.streamLossNotice)
            assertTrue(loss.contains("missed frame 2"))
            assertTrue(overflowed.notice.orEmpty().contains("held back 3"))
            assertEquals(listOf(loss, overflowed.notice), overflowed.statusNotices)
            controller.startRun()
            assertTrue(controller.state.value.error.orEmpty().contains("already in flight"))
            assertEquals(loss, controller.state.value.streamLossNotice)

            // An ahead-of-session gap is transient and must not replace actual loss.
            resumed.reply(
                "event: gap\ndata: {\"requested_after\":999,\"oldest_retained\":1}\n\n" +
                    "data: {\"event_type\":\"workflow_error\",\"step\":\"complete\"," +
                    "\"doc_id\":\"doc_1\",\"data\":{\"error\":\"Generation failed\"}}\n\n",
                stream = true,
            )
            val failed = withTimeout(5_000) {
                controller.state.first { it.status == "failed" && !it.busy }
            }
            assertEquals("Generation failed", failed.error)
            assertEquals(loss, failed.streamLossNotice)
            assertTrue(failed.statusNotices.contains(loss))
            assertTrue(failed.notice.orEmpty().contains("No events were missed"))

            controller.notice("Copied to clipboard.")
            assertEquals(listOf(loss, "Copied to clipboard."), controller.state.value.statusNotices)
            controller.clearError()
            assertEquals(loss, controller.state.value.streamLossNotice)

            controller.setTopic("")
            controller.startRun()
            val refused = withTimeout(5_000) {
                controller.state.first { it.error?.contains("needs a topic") == true && !it.busy }
            }
            assertEquals(loss, refused.streamLossNotice)

            controller.setTopic("A new topic")
            controller.startRun()
            val refusedRequest = http.next()
            assertEquals("starting", controller.state.value.status)
            assertEquals(loss, controller.state.value.streamLossNotice)
            refusedRequest.reply("""{"detail":"Generation unavailable"}""", status = 503)
            val unavailable = withTimeout(5_000) {
                controller.state.first { it.status == "failed" && it.error != null && !it.busy }
            }
            assertEquals(loss, unavailable.streamLossNotice)

            controller.startRun()
            http.next().reply("""{"doc_id":"doc_2","status":"running"}""")
            http.next()
            assertEquals("doc_2", controller.state.value.docId)
            assertEquals(null, controller.state.value.streamLossNotice)
            assertEquals(listOf("Run started."), controller.state.value.statusNotices)
        } finally { scope.cancel() }
    }

    @Test
    fun `an ahead-of-session gap does not pin a permanent loss warning`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply(
                "event: gap\ndata: {\"requested_after\":2147483647,\"oldest_retained\":1}\n\n",
                stream = true,
            )
            val ended = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertEquals(null, ended.streamLossNotice)
            assertTrue(ended.notice.orEmpty().contains("No events were missed"))
            controller.notice("Saved.")
            assertEquals(listOf("Saved."), controller.state.value.statusNotices)
        } finally { scope.cancel() }
    }

    @Test
    fun `the first permanent gap remains visible after further gaps and malformed frames`() = runBlocking {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply(
                "event: gap\ndata: {\"requested_after\":1,\"oldest_retained\":3}\n\n" +
                    "event: gap\ndata: {\"requested_after\":3,\"oldest_retained\":6}\n\n" +
                    "data: not-json\n\n",
                stream = true,
            )
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertTrue(failed.streamLossNotice.orEmpty().contains("missed frame 2"))
            assertTrue(failed.notice.orEmpty().contains("missed frames 4 to 5"))
            assertEquals(listOf(failed.streamLossNotice, failed.notice), failed.statusNotices)
        } finally { scope.cancel() }
    }

    /**
     * The notice one stream's body leaves in the state.
     *
     * A fresh controller and a fresh client for every stream: the notice is
     * the last thing the window was told, so one run's notice would hide
     * another's.
     */
    private suspend fun noticeFromStream(body: String, expected: String): String {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.setTopic("A topic")
            controller.startRun()
            http.next().reply("""{"doc_id":"doc_1","status":"running"}""")
            http.next().reply(body, stream = true)
            val noticed = withTimeout(5_000) {
                controller.state.first { it.notice?.contains(expected) == true }
            }
            return noticed.notice.orEmpty()
        } finally {
            scope.cancel()
        }
    }

    @Test
    fun `importing a docx opens the book and its first section`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        val file = temporaryDocx()
        try {
            val controller = StudioController(scope, http)
            controller.openDocx(file)
            val import = http.next()
            assertEquals("POST", import.request.method())
            import.reply(manifestJson)
            http.next().reply("""{"books":[$manifestJson],"count":1}""")
            http.next().reply(sectionOneJson)
            val documents = withTimeout(5_000) {
                controller.state.first { it.documents.openSectionId == "sec_1" }
            }.documents
            assertEquals("book_1", documents.openBook?.bookId)
            assertEquals("First chapter", documents.sectionTitle)
            assertEquals("Chapter one text.", documents.sectionDraft)
            assertEquals("Chapter one text.", documents.savedContent)
            assertEquals(false, documents.sectionDirty)
            assertEquals(1, documents.books.size)
        } finally {
            scope.cancel()
            file.delete()
        }
    }

    @Test
    fun `a refused import leaves the workspace untouched and reports the failure`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        val file = temporaryDocx()
        try {
            val controller = StudioController(scope, http)
            controller.openDocx(file)
            http.next().reply("", status = 422)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertNotNull(failed.error)
            assertEquals(null, failed.documents.openBook)
            assertEquals(null, failed.documents.openSectionId)
            assertEquals("", failed.documents.sectionDraft)
            assertTrue(failed.documents.books.isEmpty())
        } finally {
            scope.cancel()
            file.delete()
        }
    }

    @Test
    fun `an unreadable file is reported in plain words`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        val missing = File(
            System.getProperty("java.io.tmpdir"),
            "lmeditor-missing-${System.nanoTime()}.docx",
        )
        try {
            val controller = StudioController(scope, http)
            controller.openDocx(missing)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertTrue(failed.error.orEmpty().contains("Could not read"))
            assertTrue(failed.error.orEmpty().contains("lmeditor-missing-"))
            assertEquals(null, failed.documents.openBook)
        } finally { scope.cancel() }
    }

    @Test
    fun `switching sections replaces the draft and clears the dirty mark`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            controller.openSection("sec_2")
            http.next().reply(saveResultJson)
            http.next().reply(sectionTwoJson)
            val documents = withTimeout(5_000) {
                controller.state.first { it.documents.openSectionId == "sec_2" }
            }.documents
            assertEquals("Second chapter", documents.sectionTitle)
            assertEquals("Chapter two text.", documents.sectionDraft)
            assertEquals("Chapter two text.", documents.savedContent)
            assertEquals(false, documents.sectionDirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `a draft marks the section dirty and undoing the edit clears it`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            val dirty = controller.state.value.documents
            assertEquals(true, dirty.sectionDirty)
            assertEquals(true, dirty.hasUnsavedChanges)
            controller.setSectionDraft("Chapter one text.")
            val clean = controller.state.value.documents
            assertEquals(false, clean.sectionDirty)
            assertEquals(false, clean.hasUnsavedChanges)
        } finally { scope.cancel() }
    }

    @Test
    fun `saving persists the draft and clears the dirty mark`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            controller.saveSection()
            val save = http.next()
            assertEquals("PUT", save.request.method())
            save.reply(saveResultJson)
            http.next().reply(manifestJson)
            val documents = withTimeout(5_000) {
                controller.state.first {
                    it.documents.savedContent == "Edited text." && !it.documents.sectionDirty
                }
            }.documents
            assertEquals("Edited text.", documents.sectionDraft)
            assertEquals("Edited text.", documents.savedContent)
            assertEquals(false, documents.sectionDirty)
            assertEquals(false, documents.saving)
        } finally { scope.cancel() }
    }

    @Test
    fun `a failed save keeps the draft and keeps the section dirty`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            controller.saveSection()
            http.next().reply("", status = 500)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertNotNull(failed.error)
            assertEquals("Edited text.", failed.documents.sectionDraft)
            assertEquals("Chapter one text.", failed.documents.savedContent)
            assertEquals(true, failed.documents.sectionDirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `switching away from a dirty section saves it first`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            controller.openSection("sec_2")
            val save = http.next()
            assertEquals("PUT", save.request.method())
            assertTrue(save.request.uri().path.endsWith("/sections/sec_1"))
            save.reply(saveResultJson)
            http.next().reply(sectionTwoJson)
            val documents = withTimeout(5_000) {
                controller.state.first { it.documents.openSectionId == "sec_2" }
            }.documents
            assertEquals("Chapter two text.", documents.sectionDraft)
            assertEquals(false, documents.sectionDirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `a failed save during a switch prevents the switch`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")
            controller.openSection("sec_2")
            http.next().reply("", status = 500)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertNotNull(failed.error)
            assertEquals("sec_1", failed.documents.openSectionId)
            assertEquals("Edited text.", failed.documents.sectionDraft)
            assertEquals(true, failed.documents.sectionDirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `exporting writes the bytes to the chosen path`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        val directory = Files.createTempDirectory("lmeditor-export")
        val target = File(directory.toFile(), "exported.docx")
        try {
            val controller = StudioController(scope, http)
            openFirstBook(controller, http)
            controller.exportBookDocxTo(target)
            http.next().reply("docx bytes")
            withTimeout(5_000) { controller.state.first { it.notice != null && !it.busy } }
            assertEquals("docx bytes", target.readText())
        } finally {
            scope.cancel()
            target.delete()
            directory.toFile().delete()
        }
    }

    @Test
    fun `closing the book clears the open book and section`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.refreshBooks()
            http.next().reply("""{"books":[$manifestJson],"count":1}""")
            withTimeout(5_000) { controller.state.first { it.documents.books.isNotEmpty() } }
            openFirstBook(controller, http)
            controller.closeBook()
            val documents = controller.state.value.documents
            assertEquals(null, documents.openBook)
            assertEquals(null, documents.openSectionId)
            assertEquals("", documents.sectionTitle)
            assertEquals("", documents.sectionDraft)
            assertEquals(1, documents.books.size)
        } finally { scope.cancel() }
    }

    @Test
    fun `closing the book saves the unsaved section first`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.refreshBooks()
            http.next().reply("""{"books":[$manifestJson],"count":1}""")
            withTimeout(5_000) { controller.state.first { it.documents.books.isNotEmpty() } }
            openFirstBook(controller, http)
            controller.setSectionDraft("Edited text.")

            controller.closeBook()
            http.next().reply(saveResultJson)
            withTimeout(5_000) { controller.state.first { it.documents.openBook == null } }

            val documents = controller.state.value.documents
            assertEquals(null, documents.openBook)
            assertEquals(null, documents.openSectionId)
            assertEquals(false, documents.sectionDirty)
            assertEquals(null, controller.state.value.error)
        } finally { scope.cancel() }
    }

    @Test
    fun `closing the book is refused when the unsaved section cannot be saved`() =
        runBlocking<Unit> {
            val http = ControlledHttpClient()
            val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
            try {
                val controller = StudioController(scope, http)
                controller.refreshBooks()
                http.next().reply("""{"books":[$manifestJson],"count":1}""")
                withTimeout(5_000) { controller.state.first { it.documents.books.isNotEmpty() } }
                openFirstBook(controller, http)
                controller.setSectionDraft("Edited text.")

                controller.closeBook()
                http.next().reply("nope", status = 503)
                withTimeout(5_000) { controller.state.first { it.error != null } }

                val documents = controller.state.value.documents
                assertEquals("book_1", documents.openBook?.bookId)
                assertEquals("Edited text.", documents.sectionDraft)
                assertEquals(true, documents.sectionDirty)
            } finally { scope.cancel() }
        }

    // --- The vault ----------------------------------------------------------

    @Test
    fun `opening a vault refreshes the notes and the tags`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        val folder = Files.createTempDirectory("lmeditor-vault").toFile()
        try {
            val controller = StudioController(scope, http)
            controller.openVault(folder)
            val open = http.next()
            assertEquals("POST", open.request.method())
            assertEquals("/api/v1/vault/open", open.request.uri().rawPath)
            val body = Json.parseObject(String(open.request.bodyBytes(), StandardCharsets.UTF_8))
            assertEquals(folder.absolutePath, body["root"])
            open.reply(vaultSummaryJson)
            http.next().reply(vaultSummaryJson)
            http.next().reply(vaultNotesJson)
            http.next().reply(vaultTagsJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.notes.isNotEmpty() }
            }.vault
            assertEquals("My Vault", vault.summary?.name)
            assertEquals(2, vault.notes.size)
            assertEquals("folder/note", vault.notes[0].noteId)
            assertEquals(1, vault.tags.size)
            assertEquals("idea", vault.tags[0].tag)
        } finally {
            scope.cancel()
            folder.delete()
        }
    }

    @Test
    fun `opening a note loads its text and clears the dirty mark`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.openVaultNote("folder/note")
            val read = http.next()
            assertEquals("/api/v1/vault/note", read.request.uri().rawPath)
            assertEquals("note_id=folder%2Fnote", read.request.uri().rawQuery)
            read.reply(vaultNoteJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.openNoteId == "folder/note" }
            }.vault
            assertEquals("# The Note\n\nthe body\n", vault.draft)
            assertEquals("# The Note\n\nthe body\n", vault.savedContent)
            assertEquals(false, vault.dirty)
            assertEquals(false, vault.hasUnsavedChanges)
            assertEquals(listOf("Other"), vault.note?.links)
            assertEquals(listOf("other"), vault.note?.backlinks)
        } finally { scope.cancel() }
    }

    @Test
    fun `a draft marks the note dirty and undoing the edit clears it`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstVaultNote(controller, http)
            controller.setVaultDraft("Edited text.")
            val dirty = controller.state.value.vault
            assertEquals(true, dirty.dirty)
            assertEquals(true, dirty.hasUnsavedChanges)
            controller.setVaultDraft("# The Note\n\nthe body\n")
            val clean = controller.state.value.vault
            assertEquals(false, clean.dirty)
            assertEquals(false, clean.hasUnsavedChanges)
        } finally { scope.cancel() }
    }

    @Test
    fun `saving a note persists the draft and clears the dirty mark`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstVaultNote(controller, http)
            controller.setVaultDraft("Edited text.")
            controller.saveVaultNote()
            val save = http.next()
            assertEquals("PUT", save.request.method())
            assertEquals("/api/v1/vault/note", save.request.uri().rawPath)
            val body = Json.parseObject(String(save.request.bodyBytes(), StandardCharsets.UTF_8))
            assertEquals("folder/note", body["note_id"])
            assertEquals("Edited text.", body["content"])
            save.reply(vaultSavedNoteJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.savedContent == "Edited text." && !it.vault.dirty }
            }.vault
            assertEquals("Edited text.", vault.draft)
            assertEquals("Edited text.", vault.savedContent)
            assertEquals(false, vault.dirty)
            assertEquals(false, vault.saving)
        } finally { scope.cancel() }
    }

    @Test
    fun `a failed save keeps the draft and keeps the note dirty`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstVaultNote(controller, http)
            controller.setVaultDraft("Edited text.")
            controller.saveVaultNote()
            http.next().reply("", status = 500)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertNotNull(failed.error)
            assertEquals("Edited text.", failed.vault.draft)
            assertEquals("# The Note\n\nthe body\n", failed.vault.savedContent)
            assertEquals(true, failed.vault.dirty)
            assertEquals(true, failed.vault.hasUnsavedChanges)
        } finally { scope.cancel() }
    }

    @Test
    fun `switching away from a dirty note saves it first`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstVaultNote(controller, http)
            controller.setVaultDraft("Edited text.")
            controller.openVaultNote("other")
            val save = http.next()
            assertEquals("PUT", save.request.method())
            val body = Json.parseObject(String(save.request.bodyBytes(), StandardCharsets.UTF_8))
            assertEquals("folder/note", body["note_id"])
            assertEquals("Edited text.", body["content"])
            save.reply(vaultSavedNoteJson)
            val read = http.next()
            assertEquals("note_id=other", read.request.uri().rawQuery)
            read.reply(vaultOtherNoteJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.openNoteId == "other" }
            }.vault
            assertEquals("Other", vault.note?.title)
            assertEquals(false, vault.dirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `a failed save during a note switch prevents the switch`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            openFirstVaultNote(controller, http)
            controller.setVaultDraft("Edited text.")
            controller.openVaultNote("other")
            http.next().reply("", status = 500)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertNotNull(failed.error)
            assertEquals("folder/note", failed.vault.openNoteId)
            assertEquals("Edited text.", failed.vault.draft)
            assertEquals(true, failed.vault.dirty)
        } finally { scope.cancel() }
    }

    @Test
    fun `searching puts the hits in the results and clearing puts them away`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.searchVault("idea")
            val search = http.next()
            assertEquals("/api/v1/vault/search", search.request.uri().rawPath)
            assertEquals("q=idea&limit=50", search.request.uri().rawQuery)
            search.reply(vaultResultsJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.results.isNotEmpty() }
            }.vault
            assertEquals("idea", vault.searchQuery)
            assertEquals(1, vault.results.size)
            assertEquals("folder/note", vault.results[0].noteId)
            assertEquals(12, vault.results[0].score)
            assertEquals(listOf("idea"), vault.results[0].matchedTerms)
            controller.clearVaultSearch()
            val cleared = controller.state.value.vault
            assertEquals("", cleared.searchQuery)
            assertTrue(cleared.results.isEmpty())
        } finally { scope.cancel() }
    }

    @Test
    fun `creating a note calls the server and refreshes the list`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.createVaultNote("folder/new", "New")
            val create = http.next()
            assertEquals("POST", create.request.method())
            assertEquals("/api/v1/vault/note", create.request.uri().rawPath)
            val body = Json.parseObject(String(create.request.bodyBytes(), StandardCharsets.UTF_8))
            assertEquals("folder/new", body["note_id"])
            assertEquals("New", body["title"])
            create.reply(vaultNewNoteJson)
            http.next().reply(vaultNotesJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.openNoteId == "folder/new" }
            }.vault
            assertEquals(2, vault.notes.size)
            assertEquals("# New\n", vault.draft)
        } finally { scope.cancel() }
    }

    @Test
    fun `renaming a note calls the server and refreshes the list`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.renameVaultNote("folder/note", "other/note")
            val rename = http.next()
            assertEquals("POST", rename.request.method())
            assertEquals("/api/v1/vault/note/rename", rename.request.uri().rawPath)
            val body = Json.parseObject(String(rename.request.bodyBytes(), StandardCharsets.UTF_8))
            assertEquals("folder/note", body["from_id"])
            assertEquals("other/note", body["to_id"])
            assertEquals(true, body["update_links"])
            rename.reply(vaultRenameJson)
            http.next().reply(vaultNotesJson)
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.notes.isNotEmpty() }
            }.vault
            assertEquals(2, vault.notes.size)
            assertNotNull(controller.state.value.notice)
        } finally { scope.cancel() }
    }

    @Test
    fun `deleting a note calls the server and refreshes the list`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.deleteVaultNote("folder/note")
            val delete = http.next()
            assertEquals("DELETE", delete.request.method())
            assertEquals("/api/v1/vault/note", delete.request.uri().rawPath)
            assertEquals("note_id=folder%2Fnote", delete.request.uri().rawQuery)
            delete.reply("""{"deleted":true}""")
            http.next().reply("""{"notes":[]}""")
            val vault = withTimeout(5_000) {
                controller.state.first { it.vault.notes.isEmpty() && it.notice != null }
            }.vault
            assertTrue(vault.notes.isEmpty())
            assertEquals(null, vault.openNoteId)
        } finally { scope.cancel() }
    }

    @Test
    fun `a refused vault request is reported and invents nothing`() = runBlocking<Unit> {
        val http = ControlledHttpClient()
        val scope = CoroutineScope(Job() + Dispatchers.Unconfined)
        try {
            val controller = StudioController(scope, http)
            controller.refreshVault()
            http.next().reply("""{"detail":"no vault is open"}""", status = 409)
            val failed = withTimeout(5_000) { controller.state.first { it.error != null && !it.busy } }
            assertTrue(failed.error.orEmpty().contains("no vault is open"))
            assertEquals(null, failed.vault.summary)
            assertTrue(failed.vault.notes.isEmpty())
            assertTrue(failed.vault.tags.isEmpty())
        } finally { scope.cancel() }
    }

    /** Open the first note of the vault, so the editor holds a note. */
    private suspend fun openFirstVaultNote(
        controller: StudioController,
        http: ControlledHttpClient,
    ) {
        controller.openVaultNote("folder/note")
        http.next().reply(vaultNoteJson)
        withTimeout(5_000) { controller.state.first { it.vault.openNoteId == "folder/note" } }
    }

    /** The vault the server holds. */
    private val vaultSummaryJson = """{"root":"C:/vault","name":"My Vault","note_count":2}"""

    /** Every note of the vault, as a summary. */
    private val vaultNotesJson = """
        {"notes":[
        {"note_id":"folder/note","title":"The Note","path":"folder/note.md","tags":["idea"],
        "word_count":5,"modified_at":"2026-01-02T00:00:00Z"},
        {"note_id":"other","title":"Other","path":"other.md","tags":[],"word_count":3,
        "modified_at":"2026-01-02T00:00:00Z"}]}
    """.trimIndent()

    /** One note of the vault, with its links and its backlinks. */
    private val vaultNoteJson = """
        {"note_id":"folder/note","title":"The Note","path":"folder/note.md",
        "content":"# The Note\n\nthe body\n","tags":["idea"],"links":["Other"],
        "backlinks":["other"],"created_at":"2026-01-01T00:00:00Z",
        "modified_at":"2026-01-02T00:00:00Z","word_count":5}
    """.trimIndent()

    /** What the server stored when the operator's text was saved. */
    private val vaultSavedNoteJson = """
        {"note_id":"folder/note","title":"The Note","path":"folder/note.md",
        "content":"Edited text.","tags":["idea"],"links":["Other"],"backlinks":["other"],
        "created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-02T00:00:00Z","word_count":2}
    """.trimIndent()

    /** Another note of the vault. */
    private val vaultOtherNoteJson = """
        {"note_id":"other","title":"Other","path":"other.md","content":"# Other\n",
        "tags":[],"links":[],"backlinks":["folder/note"],
        "created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-02T00:00:00Z","word_count":2}
    """.trimIndent()

    /** The note the server created. */
    private val vaultNewNoteJson = """
        {"note_id":"folder/new","title":"New","path":"folder/new.md","content":"# New\n",
        "tags":[],"links":[],"backlinks":[],"created_at":"2026-01-01T00:00:00Z",
        "modified_at":"2026-01-02T00:00:00Z","word_count":2}
    """.trimIndent()

    /** The hits of the vault's search. */
    private val vaultResultsJson = """
        {"results":[{"note_id":"folder/note","title":"The Note","path":"folder/note.md",
        "score":12,"matched_terms":["idea"]}]}
    """.trimIndent()

    /** Every tag of the vault. */
    private val vaultTagsJson = """{"tags":[{"tag":"idea","count":1}]}"""

    /** What a rename of a note did. */
    private val vaultRenameJson =
        """{"from_id":"folder/note","to_id":"other/note","updated_links":2}"""

    /** Open the book and its first section, so the editor holds a section. */
    private suspend fun openFirstBook(controller: StudioController, http: ControlledHttpClient) {
        controller.openBook("book_1")
        http.next().reply(manifestJson)
        http.next().reply(sectionOneJson)
        withTimeout(5_000) { controller.state.first { it.documents.openSectionId == "sec_1" } }
    }

    /** A file that holds bytes, standing in for a Word package. */
    private fun temporaryDocx(): File {
        val file = File.createTempFile("lmeditor-", ".docx")
        file.writeBytes("PK-docx-bytes".toByteArray())
        return file
    }

    /** The manifest the server answers with: a book with two sections. */
    private val manifestJson = """
        {"book_id":"book_1","title":"A book","section_count":2,"chunk_count":0,
        "created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-02T00:00:00Z",
        "sections":[
        {"section_id":"sec_1","title":"First chapter","order":0,"word_count":3,"char_count":17,"chunk_ids":[]},
        {"section_id":"sec_2","title":"Second chapter","order":1,"word_count":2,"char_count":12,"chunk_ids":[]}],
        "brief":""}
    """.trimIndent()

    /** The body text of the first section. */
    private val sectionOneJson = """
        {"book_id":"book_1","section_id":"sec_1","title":"First chapter","order":0,
        "content":"Chapter one text."}
    """.trimIndent()

    /** The body text of the second section. */
    private val sectionTwoJson = """
        {"book_id":"book_1","section_id":"sec_2","title":"Second chapter","order":1,
        "content":"Chapter two text."}
    """.trimIndent()

    /** What the server stored: the operator's own text, and its counts. */
    private val saveResultJson = """
        {"section_id":"sec_1","title":"First chapter","content":"Edited text.",
        "word_count":2,"char_count":12,"chunk_count":0}
    """.trimIndent()

    private class Pending(val request: HttpRequest, val future: CompletableFuture<HttpResponse<Any>>) {
        fun reply(text: String, status: Int = 200, stream: Boolean = false) {
            val bytes = text.toByteArray(Charsets.UTF_8)
            future.complete(object : HttpResponse<Any> {
                override fun body(): Any = if (stream) ByteArrayInputStream(bytes) else bytes
                override fun statusCode() = status
                override fun request() = request
                override fun previousResponse(): Optional<HttpResponse<Any>> = Optional.empty()
                override fun headers(): HttpHeaders = HttpHeaders.of(emptyMap()) { _, _ -> true }
                override fun sslSession(): Optional<SSLSession> = Optional.empty()
                override fun uri(): URI = request.uri()
                override fun version(): HttpClient.Version = HttpClient.Version.HTTP_1_1
            })
        }
    }

    private class ControlledHttpClient : HttpClient() {
        private val pending = Channel<Pending>(Channel.UNLIMITED)
        suspend fun next(): Pending = withTimeout(5_000) { pending.receive() }
        override fun <T> sendAsync(request: HttpRequest, handler: HttpResponse.BodyHandler<T>): CompletableFuture<HttpResponse<T>> {
            val future = CompletableFuture<HttpResponse<Any>>()
            check(pending.trySend(Pending(request, future)).isSuccess)
            @Suppress("UNCHECKED_CAST")
            return future as CompletableFuture<HttpResponse<T>>
        }
        override fun <T> sendAsync(request: HttpRequest, handler: HttpResponse.BodyHandler<T>, push: HttpResponse.PushPromiseHandler<T>): CompletableFuture<HttpResponse<T>> = sendAsync(request, handler)
        override fun <T> send(request: HttpRequest, handler: HttpResponse.BodyHandler<T>): HttpResponse<T> = throw AssertionError("Blocking HTTP is forbidden")
        override fun cookieHandler(): Optional<CookieHandler> = Optional.empty()
        override fun connectTimeout(): Optional<Duration> = Optional.empty()
        override fun followRedirects(): Redirect = Redirect.NEVER
        override fun proxy(): Optional<ProxySelector> = Optional.empty()
        override fun sslContext(): SSLContext = SSLContext.getDefault()
        override fun sslParameters(): SSLParameters = SSLParameters()
        override fun authenticator(): Optional<Authenticator> = Optional.empty()
        override fun version(): Version = Version.HTTP_1_1
        override fun executor(): Optional<Executor> = Optional.empty()
    }
}
