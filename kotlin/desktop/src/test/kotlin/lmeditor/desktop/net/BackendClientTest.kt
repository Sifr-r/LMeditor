package lmeditor.desktop.net

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.Authenticator
import java.net.CookieHandler
import java.net.ProxySelector
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpHeaders
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.Optional
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.Executor
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSession
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import lmeditor.core.ValidationError

/** HTTP regressions use an in-memory transport, without a backend or timing assumptions. */
class BackendClientTest {
    @Test
    fun `invalid backend addresses fail before connection`() {
        listOf("", "file:///tmp/a", "http://", "http://user:secret@example.test", "http://example.test?x=1", "http://example.test#fragment", "http://example.test:99999").forEach {
            assertFailsWith<ValidationError> { BackendClient(it) }
        }
        assertEquals("http://example.test/api", BackendClient(" http://example.test/api/ ").baseUrl)
    }

    @Test
    fun `connection failure is a contextual backend error`() = runBlocking {
        val http = StubHttpClient { CompletableFuture.failedFuture(IOException("offline")) }
        val failure = assertFailsWith<BackendError.Unreachable> { BackendClient("http://example.test", http).health() }
        assertEquals("http://example.test/api/v1/health", failure.url)
    }

    @Test
    fun `synchronous transport failure uses the same contextual error`() = runBlocking {
        val http = StubHttpClient { throw IOException("connection setup failed") }
        val failure = assertFailsWith<BackendError.Unreachable> { BackendClient("http://example.test", http).health() }
        assertEquals("http://example.test/api/v1/health", failure.url)
        assertEquals("connection setup failed", failure.cause?.message)
    }

    @Test
    fun `plain text refusal preserves HTTP status and detail`() = runBlocking {
        val http = StubHttpClient { CompletableFuture.completedFuture(StubResponse(it, 503, "maintenance".toByteArray())) }
        val failure = assertFailsWith<BackendError.Refused> { BackendClient("http://example.test", http).health() }
        assertEquals(503, failure.status)
        assertEquals("maintenance", failure.detail)
    }

    // The explicit `Unit` matters: `assertFailsWith` answers the exception it
    // caught, so a body ending in one would make the test method answer
    // `ValidationError` instead of `Unit` -- and JUnit 5 quietly skips a test
    // method that does not return `void`.
    @Test
    fun `document identifiers cannot alter request paths`() = runBlocking<Unit> {
        var requested: URI? = null
        val http = StubHttpClient {
            requested = it.uri()
            CompletableFuture.completedFuture(StubResponse(it, 404, "missing".toByteArray()))
        }
        assertFailsWith<BackendError.Refused> { BackendClient("http://example.test", http).document("a/b?c#d") }
        assertEquals("/api/v1/documents/a%2Fb%3Fc%23d", requested?.rawPath)
        assertEquals(null, requested?.rawQuery)
        assertFailsWith<ValidationError> { BackendClient("http://example.test", http).document("..") }
    }

    @Test
    fun `cancelling a request cancels its HTTP future`() = runBlocking {
        val pending = CompletableFuture<HttpResponse<*>>()
        val client = BackendClient("http://example.test", StubHttpClient { pending })
        val job = launch(start = CoroutineStart.UNDISPATCHED) { client.health() }
        job.cancelAndJoin()
        assertTrue(pending.isCancelled)
    }

    // --- The book library --------------------------------------------------

    @Test
    fun `the library is listed with each book's section index`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(it, 200, """{"books":[$manifestJson],"count":1}""".toByteArray()),
            )
        }
        val books = BackendClient("http://example.test", http).listBooks()
        assertEquals(1, books.size)
        assertEquals("book_1", books[0].bookId)
        assertEquals("A Book", books[0].title)
        assertEquals(1, books[0].sectionCount)
        assertEquals(1, books[0].sections.size)
        assertEquals("sec_1", books[0].sections[0].sectionId)
        assertEquals("Chapter One", books[0].sections[0].title)
    }

    @Test
    fun `the manifest of one book is fetched by its id`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, manifestJson.toByteArray()))
        }
        val manifest = BackendClient("http://example.test", http).book("book_1")
        assertEquals("/api/v1/library/books/book_1", http.requestUri()?.rawPath)
        assertEquals("book_1", manifest.bookId)
        assertEquals("A Book", manifest.title)
    }

    @Test
    fun `a word document is imported as a book`() = runBlocking {
        var requested: HttpRequest? = null
        val http = StubHttpClient { request ->
            requested = request
            CompletableFuture.completedFuture(StubResponse(request, 201, manifestJson.toByteArray()))
        }
        val manifest = BackendClient("http://example.test", http)
            .importDocx("book.docx", "PK-package".toByteArray(), title = "A Book")
        assertEquals("/api/v1/library/books/import-docx", requested?.uri()?.rawPath)
        assertEquals("POST", requested?.method())
        val request = requested!!
        val contentType = request.headers().firstValue("Content-Type").orElse("")
        assertTrue(contentType.startsWith("multipart/form-data; boundary="), contentType)
        val body = String(request.bodyBytes(), StandardCharsets.UTF_8)
        assertTrue(body.contains("Content-Disposition: form-data; name=\"title\""), body)
        assertTrue(body.contains("Content-Disposition: form-data; name=\"file\"; filename=\"book.docx\""), body)
        assertTrue(
            body.contains(
                "Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            body,
        )
        assertTrue(body.contains("PK-package"), body)
        assertEquals("book_1", manifest.bookId)
    }

    @Test
    fun `the text of one section is read`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(
                    it,
                    200,
                    """{"book_id":"book_1","section_id":"sec_1","title":"Chapter One","order":2,"content":"The body."}"""
                        .toByteArray(),
                ),
            )
        }
        val section = BackendClient("http://example.test", http).bookSection("book_1", "sec_1")
        assertEquals("/api/v1/library/books/book_1/sections/sec_1", http.requestUri()?.rawPath)
        assertEquals("book_1", section.bookId)
        assertEquals("sec_1", section.sectionId)
        assertEquals("Chapter One", section.title)
        assertEquals(2, section.order)
        assertEquals("The body.", section.content)
    }

    @Test
    fun `the operator's text is saved for one section`() = runBlocking {
        var requested: HttpRequest? = null
        val http = StubHttpClient { request ->
            requested = request
            CompletableFuture.completedFuture(
                StubResponse(
                    request,
                    200,
                    """
                    {"section_id":"sec_1","title":"Chapter One","content":"The operator's words.",
                     "word_count":3,"char_count":20,"chunk_count":2}
                    """.trimIndent().toByteArray(),
                ),
            )
        }
        val result = BackendClient("http://example.test", http)
            .saveBookSection("book_1", "sec_1", "The operator's words.")
        assertEquals("PUT", requested?.method())
        assertEquals("/api/v1/library/books/book_1/sections/sec_1", requested?.uri()?.rawPath)
        assertEquals(
            """{"content":"The operator's words."}""",
            String(requested!!.bodyBytes(), StandardCharsets.UTF_8),
        )
        assertEquals("sec_1", result.sectionId)
        assertEquals("Chapter One", result.title)
        assertEquals("The operator's words.", result.content)
        assertEquals(3, result.wordCount)
        assertEquals(20, result.charCount)
        assertEquals(2, result.chunkCount)
    }

    @Test
    fun `the exported word document of a book is returned unchanged`() = runBlocking {
        val packageBytes = byteArrayOf(0x50, 0x4B, 0x03, 0x04, 0x00, 0xFF.toByte())
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, packageBytes))
        }
        val bytes = BackendClient("http://example.test", http).exportBookDocx("book_1")
        assertEquals("/api/v1/library/books/book_1/export.docx", http.requestUri()?.rawPath)
        assertContentEquals(packageBytes, bytes)
    }

    @Test
    fun `book and section identifiers cannot alter request paths`() = runBlocking<Unit> {
        val paths = mutableListOf<String?>()
        val http = StubHttpClient { request ->
            paths += request.uri().rawPath
            CompletableFuture.completedFuture(StubResponse(request, 404, "missing".toByteArray()))
        }
        val client = BackendClient("http://example.test", http)
        assertFailsWith<BackendError.Refused> { client.book("a/b?c#d") }
        assertFailsWith<BackendError.Refused> { client.bookSection("a/b?c#d", "e/f?g#h") }
        assertFailsWith<BackendError.Refused> { client.saveBookSection("a/b?c#d", "e/f?g#h", "text") }
        assertFailsWith<BackendError.Refused> { client.exportBookDocx("a/b?c#d") }
        assertEquals("/api/v1/library/books/a%2Fb%3Fc%23d", paths[0])
        assertEquals("/api/v1/library/books/a%2Fb%3Fc%23d/sections/e%2Ff%3Fg%23h", paths[1])
        assertEquals("/api/v1/library/books/a%2Fb%3Fc%23d/sections/e%2Ff%3Fg%23h", paths[2])
        assertEquals("/api/v1/library/books/a%2Fb%3Fc%23d/export.docx", paths[3])
        assertFailsWith<ValidationError> { client.book("..") }
    }

    /** A manifest as the server sends it, reused by the book tests. */
    private val manifestJson = """
        {"book_id":"book_1","title":"A Book","section_count":1,"chunk_count":2,
         "created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-02T00:00:00Z",
         "sections":[{"section_id":"sec_1","title":"Chapter One","order":1,
         "word_count":10,"char_count":60,"chunk_ids":["chunk_1"]}],
         "brief":"A brief."}
    """.trimIndent()

    // --- The vault ----------------------------------------------------------

    @Test
    fun `the vault answers are decoded by the tier's own decoders`() = runBlocking {
        val http = StubHttpClient { request ->
            CompletableFuture.completedFuture(
                StubResponse(request, 200, vaultAnswer(request.uri().rawPath, request.method()).toByteArray()),
            )
        }
        val client = BackendClient("http://example.test", http)

        val opened = client.openVault("C:/vault")
        assertEquals("C:/vault", opened.root)
        assertEquals("My Vault", opened.name)
        assertEquals(2, opened.noteCount)

        val summary = client.vault()
        assertEquals("C:/vault", summary.root)
        assertEquals("My Vault", summary.name)
        assertEquals(2, summary.noteCount)

        val notes = client.listVaultNotes()
        assertEquals(2, notes.size)
        assertEquals("folder/note", notes[0].noteId)
        assertEquals("The Note", notes[0].title)
        assertEquals("folder/note.md", notes[0].path)
        assertEquals(listOf("idea"), notes[0].tags)
        assertEquals(5, notes[0].wordCount)

        val note = client.vaultNote("folder/note")
        assertEquals("folder/note", note.noteId)
        assertEquals("The Note", note.title)
        assertEquals("# The Note\n\nthe body\n", note.content)
        assertEquals(listOf("idea"), note.tags)
        assertEquals(listOf("Other"), note.links)
        assertEquals(listOf("other"), note.backlinks)
        assertEquals(5, note.wordCount)

        val written = client.writeVaultNote("folder/note", "# The Note\n\nnew body\n")
        assertEquals("folder/note", written.noteId)

        val created = client.createVaultNote("folder/new", "New", "")
        assertEquals("folder/new", created.noteId)

        client.deleteVaultNote("folder/note")

        val renamed = client.renameVaultNote("folder/note", "other/note", updateLinks = true)
        assertEquals("folder/note", renamed.fromId)
        assertEquals("other/note", renamed.toId)
        assertEquals(2, renamed.updatedLinks)

        val hits = client.searchVault("idea", 50)
        assertEquals(1, hits.size)
        assertEquals("folder/note", hits[0].noteId)
        assertEquals(12, hits[0].score)
        assertEquals(listOf("idea"), hits[0].matchedTerms)

        val tags = client.vaultTags()
        assertEquals(1, tags.size)
        assertEquals("idea", tags[0].tag)
        assertEquals(1, tags[0].count)
    }

    @Test
    fun `a note id is sent encoded in the query string and never in the route`() = runBlocking<Unit> {
        val requests = mutableListOf<HttpRequest>()
        val http = StubHttpClient { request ->
            requests += request
            CompletableFuture.completedFuture(
                StubResponse(request, 200, vaultAnswer(request.uri().rawPath, request.method()).toByteArray()),
            )
        }
        val client = BackendClient("http://example.test", http)

        client.openVault("C:/vault")
        val open = requests[0]
        assertEquals("POST", open.method())
        assertEquals("/api/v1/vault/open", open.uri().rawPath)
        assertEquals("""{"root":"C:/vault"}""", String(open.bodyBytes(), StandardCharsets.UTF_8))

        client.vaultNote("folder/note")
        assertEquals("/api/v1/vault/note", requests[1].uri().rawPath)
        assertEquals("note_id=folder%2Fnote", requests[1].uri().rawQuery)

        client.writeVaultNote("folder/note", "body")
        assertEquals("PUT", requests[2].method())
        assertEquals("/api/v1/vault/note", requests[2].uri().rawPath)
        assertEquals(null, requests[2].uri().rawQuery)
        assertEquals(
            """{"note_id":"folder/note","content":"body"}""",
            String(requests[2].bodyBytes(), StandardCharsets.UTF_8),
        )

        client.createVaultNote("folder/new", "New", "")
        assertEquals("POST", requests[3].method())
        assertEquals("/api/v1/vault/note", requests[3].uri().rawPath)
        assertEquals(
            """{"note_id":"folder/new","title":"New","content":""}""",
            String(requests[3].bodyBytes(), StandardCharsets.UTF_8),
        )

        client.deleteVaultNote("folder/note")
        assertEquals("DELETE", requests[4].method())
        assertEquals("/api/v1/vault/note", requests[4].uri().rawPath)
        assertEquals("note_id=folder%2Fnote", requests[4].uri().rawQuery)

        client.renameVaultNote("folder/note", "other/note", updateLinks = true)
        assertEquals("POST", requests[5].method())
        assertEquals("/api/v1/vault/note/rename", requests[5].uri().rawPath)
        assertEquals(
            """{"from_id":"folder/note","to_id":"other/note","update_links":true}""",
            String(requests[5].bodyBytes(), StandardCharsets.UTF_8),
        )

        client.searchVault("idea", 50)
        assertEquals("/api/v1/vault/search", requests[6].uri().rawPath)
        assertEquals("q=idea&limit=50", requests[6].uri().rawQuery)

        client.vaultTags()
        assertEquals("/api/v1/vault/tags", requests[7].uri().rawPath)
    }

    @Test
    fun `a refused vault request is a refusal and not an empty vault`() = runBlocking<Unit> {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(it, 409, """{"detail":"no vault is open"}""".toByteArray()),
            )
        }
        val client = BackendClient("http://example.test", http)

        val notes = assertFailsWith<BackendError.Refused> { client.listVaultNotes() }
        assertEquals(409, notes.status)
        assertEquals("no vault is open", notes.detail)
        assertTrue(notes.message.orEmpty().contains("no vault is open"))

        val summary = assertFailsWith<BackendError.Refused> { client.vault() }
        assertEquals(409, summary.status)
        assertEquals("no vault is open", summary.detail)

        val tags = assertFailsWith<BackendError.Refused> { client.vaultTags() }
        assertEquals(409, tags.status)

        val note = assertFailsWith<BackendError.Refused> { client.vaultNote("folder/note") }
        assertEquals(409, note.status)
    }

    @Test
    fun `a note answer is read whether or not the server wraps it`() = runBlocking {
        val bare = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, vaultNoteJson.toByteArray()))
        }
        val bareNote = BackendClient("http://example.test", bare).vaultNote("folder/note")
        assertEquals("folder/note", bareNote.noteId)
        assertEquals("The Note", bareNote.title)

        val wrapped = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(it, 200, """{"note":$vaultNoteJson}""".toByteArray()),
            )
        }
        val wrappedNote = BackendClient("http://example.test", wrapped).vaultNote("folder/note")
        assertEquals("folder/note", wrappedNote.noteId)
        assertEquals("The Note", wrappedNote.title)
    }

    // --- The notebooks ------------------------------------------------------

    @Test
    fun `the notebooks are listed with the folder they live in`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(it, 200, notebookLibraryJson.toByteArray()),
            )
        }
        val library = BackendClient("http://example.test", http).notebooks()
        assertEquals("/api/v1/notebooks", http.requestUri()?.rawPath)
        assertEquals("GET", http.lastRequest?.method())
        assertEquals("C:/notebooks", library.root)
        assertEquals("notebooks", library.name)
        assertEquals(1, library.notebookCount)
        assertEquals(1, library.notebooks.size)
        assertEquals("nb_1", library.notebooks[0].notebookId)
        assertEquals("A notebook", library.notebooks[0].title)
        assertEquals(2, library.notebooks[0].sourceCount)
        assertEquals(1, library.notebooks[0].noteCount)
    }

    @Test
    fun `every notebook route is called the way the contract says`() = runBlocking<Unit> {
        val requests = mutableListOf<HttpRequest>()
        val http = StubHttpClient { request ->
            requests += request
            CompletableFuture.completedFuture(
                StubResponse(request, 200, notebookAnswer(request.uri().rawPath, request.method()).toByteArray()),
            )
        }
        val client = BackendClient("http://example.test", http)

        client.openNotebooks("C:/notebooks")
        assertEquals("POST", requests[0].method())
        assertEquals("/api/v1/notebooks/open", requests[0].uri().rawPath)
        assertEquals("""{"root":"C:/notebooks"}""", String(requests[0].bodyBytes(), StandardCharsets.UTF_8))

        client.notebook("nb_1")
        assertEquals("GET", requests[1].method())
        assertEquals("/api/v1/notebooks/nb_1", requests[1].uri().rawPath)

        client.createNotebook("A notebook", "What it is for.")
        assertEquals("POST", requests[2].method())
        assertEquals("/api/v1/notebooks", requests[2].uri().rawPath)
        assertEquals(
            """{"title":"A notebook","description":"What it is for."}""",
            String(requests[2].bodyBytes(), StandardCharsets.UTF_8),
        )

        client.updateNotebook("nb_1", "A notebook", "What it is for.")
        assertEquals("PUT", requests[3].method())
        assertEquals("/api/v1/notebooks/nb_1", requests[3].uri().rawPath)
        assertEquals(
            """{"title":"A notebook","description":"What it is for."}""",
            String(requests[3].bodyBytes(), StandardCharsets.UTF_8),
        )

        client.deleteNotebook("nb_1")
        assertEquals("DELETE", requests[4].method())
        assertEquals("/api/v1/notebooks/nb_1", requests[4].uri().rawPath)

        client.addNotebookSource("nb_1", "src_1")
        assertEquals("POST", requests[5].method())
        assertEquals("/api/v1/notebooks/nb_1/sources", requests[5].uri().rawPath)
        assertEquals("""{"source_id":"src_1"}""", String(requests[5].bodyBytes(), StandardCharsets.UTF_8))

        client.removeNotebookSource("nb_1", "src_1")
        assertEquals("DELETE", requests[6].method())
        assertEquals("/api/v1/notebooks/nb_1/sources", requests[6].uri().rawPath)
        assertEquals("source_id=src_1", requests[6].uri().rawQuery)

        client.addNotebookNote("nb_1", "folder/note")
        assertEquals("POST", requests[7].method())
        assertEquals("/api/v1/notebooks/nb_1/notes", requests[7].uri().rawPath)
        assertEquals("""{"note_id":"folder/note"}""", String(requests[7].bodyBytes(), StandardCharsets.UTF_8))

        client.removeNotebookNote("nb_1", "folder/note")
        assertEquals("DELETE", requests[8].method())
        assertEquals("/api/v1/notebooks/nb_1/notes", requests[8].uri().rawPath)
        assertEquals("note_id=folder%2Fnote", requests[8].uri().rawQuery)

        client.askNotebook("nb_1", "What does it say?")
        assertEquals("POST", requests[9].method())
        assertEquals("/api/v1/notebooks/nb_1/ask", requests[9].uri().rawPath)
        assertEquals(
            """{"question":"What does it say?"}""",
            String(requests[9].bodyBytes(), StandardCharsets.UTF_8),
        )
    }

    @Test
    fun `a note id travels in the query and never in the route`() = runBlocking<Unit> {
        val requests = mutableListOf<HttpRequest>()
        val http = StubHttpClient { request ->
            requests += request
            CompletableFuture.completedFuture(
                StubResponse(request, 200, notebookEnvelopeJson.toByteArray()),
            )
        }
        val client = BackendClient("http://example.test", http)

        client.addNotebookNote("nb_1", "folder/note")
        assertEquals("/api/v1/notebooks/nb_1/notes", requests[0].uri().rawPath)
        assertEquals(null, requests[0].uri().rawQuery)
        assertEquals("""{"note_id":"folder/note"}""", String(requests[0].bodyBytes(), StandardCharsets.UTF_8))

        client.removeNotebookNote("nb_1", "folder/note")
        assertEquals("/api/v1/notebooks/nb_1/notes", requests[1].uri().rawPath)
        assertEquals("note_id=folder%2Fnote", requests[1].uri().rawQuery)
        assertTrue(!requests[1].uri().rawPath.contains("%2F"), requests[1].uri().rawPath)
    }

    @Test
    fun `notebook identifiers cannot alter request paths`() = runBlocking<Unit> {
        var requested: URI? = null
        val http = StubHttpClient {
            requested = it.uri()
            CompletableFuture.completedFuture(StubResponse(it, 404, "missing".toByteArray()))
        }
        assertFailsWith<BackendError.Refused> { BackendClient("http://example.test", http).notebook("a/b?c#d") }
        assertEquals("/api/v1/notebooks/a%2Fb%3Fc%23d", requested?.rawPath)
        assertEquals(null, requested?.rawQuery)
    }

    @Test
    fun `a malformed notebook is rejected rather than read as an empty notebook`() = runBlocking<Unit> {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, """{"notebook":{}}""".toByteArray()))
        }
        val failure = assertFailsWith<BackendError.Malformed> {
            BackendClient("http://example.test", http).notebook("nb_1")
        }
        assertTrue(failure.message.orEmpty().contains("notebook"), failure.message.orEmpty())
    }

    @Test
    fun `a malformed library is rejected rather than read as an empty folder`() = runBlocking<Unit> {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(
                    it,
                    200,
                    """{"root":"C:/notebooks","name":"notebooks","notebook_count":1,
                        "notebooks":[{"notebook_id":""}]}""".toByteArray(),
                ),
            )
        }
        assertFailsWith<BackendError.Malformed> { BackendClient("http://example.test", http).notebooks() }
    }

    @Test
    fun `a refusal is an answer and not a failure`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(
                StubResponse(
                    it,
                    200,
                    """{"answer":"The sources do not answer this question.",
                        "answered":false,"citations":[]}""".toByteArray(),
                ),
            )
        }
        val answer = BackendClient("http://example.test", http).askNotebook("nb_1", "What does it say?")
        assertEquals(false, answer.answered)
        assertEquals("The sources do not answer this question.", answer.answer)
        assertTrue(answer.citations.isEmpty())
    }

    @Test
    fun `an answer carries the passages it leaned on`() = runBlocking {
        val http = StubHttpClient {
            CompletableFuture.completedFuture(StubResponse(it, 200, notebookAnswerJson.toByteArray()))
        }
        val answer = BackendClient("http://example.test", http).askNotebook("nb_1", "What does it say?")
        assertEquals(true, answer.answered)
        assertEquals(1, answer.citations.size)
        assertEquals("chunk_1", answer.citations[0].chunkId)
        assertEquals("src_1", answer.citations[0].sourceId)
        assertEquals("A study", answer.citations[0].title)
        assertEquals("The effect was measured.", answer.citations[0].quote)
    }

    /** The answer the notebooks' routes give, chosen by the route and the method. */
    private fun notebookAnswer(path: String, method: String): String = when {
        path == "/api/v1/notebooks/open" -> notebookLibraryJson
        path == "/api/v1/notebooks" && method == "GET" -> notebookLibraryJson
        path == "/api/v1/notebooks" -> notebookEnvelopeJson
        path.endsWith("/ask") -> notebookAnswerJson
        path.endsWith("/sources") -> notebookEnvelopeJson
        path.endsWith("/notes") -> notebookEnvelopeJson
        method == "DELETE" -> """{"deleted":true}"""
        else -> notebookEnvelopeJson
    }

    /** The notebooks folder, as the server sends it. */
    private val notebookLibraryJson = """
        {"root":"C:/notebooks","name":"notebooks","notebook_count":1,
        "notebooks":[{"notebook_id":"nb_1","title":"A notebook","description":"What it is for.",
        "source_count":2,"note_count":1,"modified_at":"2026-01-02T00:00:00Z"}]}
    """.trimIndent()

    /** One notebook, wrapped as the server sends it. */
    private val notebookEnvelopeJson = """
        {"notebook":{"notebook_id":"nb_1","title":"A notebook","description":"What it is for.",
        "source_ids":["src_1","src_2"],"note_ids":["folder/note"],
        "created_at":"2026-01-01T00:00:00Z","modified_at":"2026-01-02T00:00:00Z"}}
    """.trimIndent()

    /** The answer to one question about one notebook. */
    private val notebookAnswerJson = """
        {"answer":"The study measured the effect.","answered":true,
        "citations":[{"chunk_id":"chunk_1","source_id":"src_1","title":"A study",
        "quote":"The effect was measured."}]}
    """.trimIndent()

    /** The answer the vault's routes give, chosen by the route and the method. */
    private fun vaultAnswer(path: String, method: String): String = when {
        path == "/api/v1/vault/open" -> vaultSummaryJson
        path == "/api/v1/vault" -> vaultSummaryJson
        path == "/api/v1/vault/notes" -> vaultNotesJson
        path == "/api/v1/vault/note" && method == "DELETE" -> """{"deleted":true}"""
        path == "/api/v1/vault/note" && method == "POST" -> vaultNewNoteJson
        path == "/api/v1/vault/note" -> vaultNoteJson
        path == "/api/v1/vault/note/rename" ->
            """{"from_id":"folder/note","to_id":"other/note","updated_links":2}"""
        path == "/api/v1/vault/search" -> vaultResultsJson
        path == "/api/v1/vault/tags" -> vaultTagsJson
        else -> """{}"""
    }

    /** The vault's summary, as the server sends it. */
    private val vaultSummaryJson =
        """{"root":"C:/vault","name":"My Vault","note_count":2}"""

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

    /** The hits of the vault's search. */
    private val vaultResultsJson = """
        {"results":[{"note_id":"folder/note","title":"The Note","path":"folder/note.md",
        "score":12,"matched_terms":["idea"]}]}
    """.trimIndent()

    /** The note the server created. */
    private val vaultNewNoteJson = """
        {"note_id":"folder/new","title":"New","path":"folder/new.md","content":"# New\n",
        "tags":[],"links":[],"backlinks":[],"created_at":"2026-01-01T00:00:00Z",
        "modified_at":"2026-01-02T00:00:00Z","word_count":2}
    """.trimIndent()

    /** Every tag of the vault. */
    private val vaultTagsJson = """{"tags":[{"tag":"idea","count":1}]}"""
}

/** Minimal controllable JDK client for transport failure and resource ownership tests. */
internal class StubHttpClient(
    private val respond: (HttpRequest) -> CompletableFuture<out HttpResponse<*>>,
) : HttpClient() {
    /** The last request the client was asked to send, so a test can assert on it. */
    var lastRequest: HttpRequest? = null
        private set

    override fun cookieHandler(): Optional<CookieHandler> = Optional.empty()
    override fun connectTimeout(): Optional<Duration> = Optional.empty()
    override fun followRedirects(): Redirect = Redirect.NEVER
    override fun proxy(): Optional<ProxySelector> = Optional.empty()
    override fun sslContext(): SSLContext = SSLContext.getDefault()
    override fun sslParameters(): SSLParameters = SSLParameters()
    override fun authenticator(): Optional<Authenticator> = Optional.empty()
    override fun version(): Version = Version.HTTP_1_1
    override fun executor(): Optional<Executor> = Optional.empty()
    override fun <T> send(request: HttpRequest, handler: HttpResponse.BodyHandler<T>): HttpResponse<T> =
        throw AssertionError("The transport must use cancellable asynchronous HTTP.")
    @Suppress("UNCHECKED_CAST")
    override fun <T> sendAsync(request: HttpRequest, handler: HttpResponse.BodyHandler<T>): CompletableFuture<HttpResponse<T>> {
        lastRequest = request
        return respond(request) as CompletableFuture<HttpResponse<T>>
    }
    override fun <T> sendAsync(request: HttpRequest, handler: HttpResponse.BodyHandler<T>, pushPromiseHandler: HttpResponse.PushPromiseHandler<T>): CompletableFuture<HttpResponse<T>> =
        sendAsync(request, handler)
}

/** The URI the stub was last asked to request, so a test can assert on the path. */
internal fun StubHttpClient.requestUri(): URI? = lastRequest?.uri()

/** The bytes a request would send, so a body can be asserted on. */
internal fun HttpRequest.bodyBytes(): ByteArray {
    val publisher = bodyPublisher().orElse(null) ?: return ByteArray(0)
    val collected = ByteArrayOutputStream()
    val subscriber = object : java.util.concurrent.Flow.Subscriber<ByteBuffer> {
        override fun onSubscribe(subscription: java.util.concurrent.Flow.Subscription) {
            subscription.request(Long.MAX_VALUE)
        }
        override fun onNext(item: ByteBuffer) {
            val bytes = ByteArray(item.remaining())
            item.get(bytes)
            collected.write(bytes)
        }
        override fun onError(throwable: Throwable) {
            throw throwable
        }
        override fun onComplete() = Unit
        fun get(): CompletionStage<ByteBuffer> = CompletableFuture.completedFuture(null)
    }
    publisher.subscribe(subscriber)
    return collected.toByteArray()
}

/** Immutable HTTP response with a caller-owned body. */
internal class StubResponse<T>(private val request: HttpRequest, private val status: Int, private val body: T) : HttpResponse<T> {
    override fun statusCode(): Int = status
    override fun request(): HttpRequest = request
    override fun previousResponse(): Optional<HttpResponse<T>> = Optional.empty()
    override fun headers(): HttpHeaders = HttpHeaders.of(emptyMap()) { _, _ -> true }
    override fun body(): T = body
    override fun sslSession(): Optional<SSLSession> = Optional.empty()
    override fun uri(): URI = request.uri()
    override fun version(): HttpClient.Version = HttpClient.Version.HTTP_1_1
}
