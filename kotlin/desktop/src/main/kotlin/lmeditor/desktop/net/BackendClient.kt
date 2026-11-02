package lmeditor.desktop.net

import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.time.Duration
import lmeditor.core.BookManifest
import lmeditor.core.BookSectionSaveRequest
import lmeditor.core.BookSectionSaveResult
import lmeditor.core.Notebook
import lmeditor.core.NotebookAnswer
import lmeditor.core.NotebookLibrary
import lmeditor.core.OutlineNodeInput
import lmeditor.core.SectionDraft
import lmeditor.core.SourceChunk
import lmeditor.core.VaultNote
import lmeditor.core.VaultNoteSummary
import lmeditor.core.VaultRenameResult
import lmeditor.core.VaultSearchHit
import lmeditor.core.VaultSummary
import lmeditor.core.VaultTag
import lmeditor.core.VerificationScorecard
import lmeditor.core.ValidationError
import lmeditor.core.bookManifestFromWire
import lmeditor.core.bookSectionSaveResultFromWire
import lmeditor.core.json.Json
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonLong
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.core.notebookAnswerFromWire
import lmeditor.core.notebookFromWire
import lmeditor.core.notebookLibraryFromWire
import lmeditor.core.sectionDraftFromWire
import lmeditor.core.sourceChunkFromWire
import lmeditor.core.toWireMap
import lmeditor.core.vaultNoteFromWire
import lmeditor.core.vaultNoteSummaryFromWire
import lmeditor.core.vaultRenameResultFromWire
import lmeditor.core.vaultSearchHitFromWire
import lmeditor.core.vaultSummaryFromWire
import lmeditor.core.vaultTagFromWire
import lmeditor.core.verificationScorecardFromWire
import lmeditor.desktop.model.BookSectionText
import lmeditor.desktop.model.DocumentPayload
import lmeditor.desktop.model.GenerateAck
import lmeditor.desktop.model.HealthSummary
import lmeditor.desktop.model.IngestResult
import lmeditor.desktop.model.SourceSummary
import lmeditor.desktop.model.WorkflowStatus

/**
 * The client for the backend's HTTP surface.
 *
 * The transport is the JDK's own `java.net.http`, so the client adds no
 * dependency of its own and the wire it speaks is the wire the backend
 * publishes -- there is no second protocol hiding behind a library.
 *
 * Every response the backend sends is read with the tier's decoders wherever
 * the tier owns the model, so a payload the backend produced is read by the
 * same rules that wrote it.
 *
 * Every request carries the credential the API requires. The credential is
 * resolved when the client is built, so a token that cannot be read is a
 * failure the client names rather than a header it quietly leaves off.
 */
class BackendClient(
    baseUrl: String,
    private val http: HttpClient = defaultHttpClient(),
    private val credential: ApiCredential = ApiCredential.resolve(),
) {

    /** The backend's base URL, without a trailing slash. */
    val baseUrl: String = backendUrl(baseUrl)

    /** Ask the backend whether it is up, and whether it can write. */
    suspend fun health(): HealthSummary {
        val payload = getJson("/api/v1/health")
        val lm = payload.jsonObject("lm")
        return HealthSummary(
            status = payload.jsonString("status"),
            version = payload.jsonString("version"),
            lmConfigured = lm.jsonBool("configured"),
            lmStub = lm.jsonBool("is_stub"),
        )
    }

    /** List the sources the server holds. */
    suspend fun listSources(): List<SourceSummary> {
        val payload = getJson("/api/v1/sources")
        return payload.jsonObjectList("sources").map { source ->
            SourceSummary(
                sourceId = source.jsonString("source_id"),
                title = source.jsonString("title"),
                kind = source.jsonString("kind"),
                origin = source.jsonString("origin"),
                chunkCount = source.jsonInt("chunk_count"),
                byteSize = source.jsonLong("byte_size"),
                addedAt = source.jsonString("added_at"),
                omissions = source.jsonStringList("omissions"),
            )
        }
    }

    /**
     * Ingest a file.
     *
     * The file is uploaded, not named: the server refuses a filesystem origin
     * it did not write itself, because a client-supplied path would be an
     * arbitrary read of the server's disk.
     */
    suspend fun ingestFile(
        sourceId: String,
        title: String,
        fileName: String,
        content: ByteArray,
    ): IngestResult {
        val body = MultipartBody.build(
            fields = mapOf("source_id" to sourceId, "title" to title),
            fileName = fileName,
            fileContentType = "application/octet-stream",
            fileContent = content,
        )
        val payload = sendJson(
            path = "/api/v1/sources/ingest",
            method = "POST",
            body = body,
            contentType = "multipart/form-data; boundary=${MultipartBody.BOUNDARY}",
        )
        return payload.toIngestResult()
    }

    /** Ingest a source the server fetches from a URL. */
    suspend fun ingestUrl(sourceId: String, title: String, url: String): IngestResult {
        val body = Json.write(
            jsonObject {
                put("source_id", sourceId)
                put("kind", "url")
                put("origin", url)
                put("title", title)
            },
        )
        return postJson("/api/v1/sources/url", body).toIngestResult()
    }

    /**
     * Start a run.
     *
     * The outline is the operator's steer: the sections the draft is written
     * to. It is optional, and an empty steer leaves the run to discover its
     * own outline.
     */
    suspend fun generate(
        topic: String,
        sourceIds: List<String>,
        outline: List<OutlineNodeInput>,
    ): GenerateAck {
        val body = Json.write(
            jsonObject {
                put("topic", topic)
                put("source_ids", sourceIds)
                if (outline.isNotEmpty()) {
                    put("outline", outline.map { node -> node.toWireMap() })
                }
            },
        )
        val payload = postJson("/api/v1/documents/generate", body)
        return GenerateAck(
            docId = payload.jsonString("doc_id"),
            status = payload.jsonString("status"),
            streamUrl = payload.jsonString("stream_url"),
        )
    }

    /** Fetch the authoritative manuscript for a document. */
    suspend fun document(docId: String): DocumentPayload {
        val payload = getJson("/api/v1/documents/${pathSegment(docId)}")
        return DocumentPayload(
            docId = payload.jsonString("doc_id"),
            topic = payload.jsonString("topic"),
            status = payload.jsonString("status"),
            isCompleted = payload.jsonBool("is_completed"),
            hitlPending = payload.jsonBool("hitl_pending"),
            error = payload["error"]?.toString(),
            sections = payload.jsonObjectList("sections").map { sectionDraftFromWire(it) },
            scorecard = payload.scorecardOrNull(),
        )
    }

    /** Fetch the state of a run. */
    suspend fun status(docId: String): WorkflowStatus {
        val payload = getJson("/api/v1/workflow/${pathSegment(docId)}/status")
        return WorkflowStatus(
            docId = payload.jsonString("doc_id"),
            topic = payload.jsonString("topic"),
            status = payload.jsonString("status"),
            currentStep = payload.jsonString("current_step"),
            hitlPending = payload.jsonBool("hitl_pending"),
            isCompleted = payload.jsonBool("is_completed"),
            error = payload["error"]?.toString(),
            rejectionReason = payload["rejection_reason"]?.toString(),
        )
    }

    /** Answer the review a run is waiting for. */
    suspend fun approve(docId: String, approved: Boolean, feedback: String?) {
        val body = Json.write(
            jsonObject {
                put("approved", approved)
                if (feedback != null) put("feedback", feedback)
            },
        )
        postJson("/api/v1/workflow/${pathSegment(docId)}/approve", body)
    }

    /** Fetch the exported Word document. */
    suspend fun exportDocx(docId: String): ByteArray {
        val request = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/api/v1/documents/${pathSegment(docId)}/export.docx"))
            .header("Authorization", credential.authorization())
            .GET()
            .build()
        val response = http.sendCancellable(request, HttpResponse.BodyHandlers.ofByteArray())
        if (response.statusCode() != 200) {
            throw BackendError.Refused(response.statusCode(), response.readDetail())
        }
        return response.body()
    }

    /** List every book the library holds, each with its section index. */
    suspend fun listBooks(): List<BookManifest> {
        val payload = getJson("/api/v1/library/books")
        return payload.jsonObjectList("books").map { bookManifestFromWire(it) }
    }

    /**
     * Import a Word document as a book of editable sections.
     *
     * The file is uploaded rather than named: the server reads the bytes it
     * was given, so a client-supplied path is never an arbitrary read of the
     * server's disk. Headings start a section, so the book's structure
     * survives the import and each section can be edited on its own.
     */
    suspend fun importDocx(
        fileName: String,
        content: ByteArray,
        title: String = "",
    ): BookManifest {
        val body = MultipartBody.build(
            fields = mapOf("title" to title),
            fileName = fileName,
            fileContentType = DOCX_CONTENT_TYPE,
            fileContent = content,
        )
        val payload = sendJson(
            path = "/api/v1/library/books/import-docx",
            method = "POST",
            body = body,
            contentType = "multipart/form-data; boundary=${MultipartBody.BOUNDARY}",
        )
        return bookManifestFromWire(payload)
    }

    /** Fetch the manifest of one book: its metadata and its section index. */
    suspend fun book(bookId: String): BookManifest {
        val payload = getJson("/api/v1/library/books/${pathSegment(bookId)}")
        return bookManifestFromWire(payload)
    }

    /**
     * Fetch the body text of one section of a book.
     *
     * Only the requested section is read; the rest of the book stays on the
     * server, which is what keeps a book of any size navigable.
     */
    suspend fun bookSection(bookId: String, sectionId: String): BookSectionText {
        val payload = getJson(
            "/api/v1/library/books/${pathSegment(bookId)}/sections/${pathSegment(sectionId)}",
        )
        return BookSectionText(
            bookId = payload.jsonString("book_id"),
            sectionId = payload.jsonString("section_id"),
            title = payload.jsonString("title"),
            order = payload.jsonInt("order"),
            content = payload.jsonString("content"),
        )
    }

    /**
     * Save the operator's own text for one section of a book.
     *
     * The text is the operator's, so the server stores what was typed and
     * answers with what it stored: the counts and the new passage total.
     */
    suspend fun saveBookSection(
        bookId: String,
        sectionId: String,
        content: String,
    ): BookSectionSaveResult {
        val body = Json.write(BookSectionSaveRequest(content = content).toWireMap())
        val payload = putJson(
            "/api/v1/library/books/${pathSegment(bookId)}/sections/${pathSegment(sectionId)}",
            body,
        )
        return bookSectionSaveResultFromWire(payload)
    }

    /** Fetch the exported Word document of a book. */
    suspend fun exportBookDocx(bookId: String): ByteArray {
        val request = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/api/v1/library/books/${pathSegment(bookId)}/export.docx"))
            .header("Authorization", credential.authorization())
            .GET()
            .build()
        val response = http.sendCancellable(request, HttpResponse.BodyHandlers.ofByteArray())
        if (response.statusCode() != 200) {
            throw BackendError.Refused(response.statusCode(), response.readDetail())
        }
        return response.body()
    }

    // --- The vault ----------------------------------------------------------

    /**
     * Open a folder as the vault and make it the active one.
     *
     * The root is the operator's own folder, named by its absolute path: a
     * vault is a folder of markdown files, so the client says which folder
     * and the server works inside it. The folder stays a folder -- it opens
     * in Obsidian, in an editor, and in a grep.
     */
    suspend fun openVault(root: String): VaultSummary {
        val body = Json.write(
            jsonObject {
                put("root", root)
            },
        )
        val payload = postJson("/api/v1/vault/open", body)
        return decode("vault summary") { vaultSummaryFromWire(payload) }
    }

    /**
     * The vault the server has open: the folder it is and how many notes it
     * holds.
     *
     * The server answers 409 when no vault is open. That is a refusal, and
     * it is raised as one: an empty vault and a refused question are not the
     * same thing, and the operator has to be able to tell them apart.
     */
    suspend fun vault(): VaultSummary {
        val payload = getJson("/api/v1/vault")
        return decode("vault summary") { vaultSummaryFromWire(payload) }
    }

    /** Every note of the open vault, as a summary: enough to navigate by. */
    suspend fun listVaultNotes(): List<VaultNoteSummary> {
        val payload = getJson("/api/v1/vault/notes")
        return decode("vault note list") {
            payload.jsonObjectList("notes").map { note -> vaultNoteSummaryFromWire(note) }
        }
    }

    /**
     * One note of the open vault, with the links it holds and the backlinks
     * that point at it.
     *
     * The note id is a path, so it holds `/`: it is sent as one encoded
     * query value and never pasted into the route, where it would become
     * two path segments.
     */
    suspend fun vaultNote(noteId: String): VaultNote {
        val payload = getJson("/api/v1/vault/note?note_id=${queryValue(noteId)}")
        return decode("vault note") { vaultNoteFromWire(payload.noteBody()) }
    }

    /**
     * Store the operator's own text for one note.
     *
     * The text is the operator's, so the server stores what was typed and
     * answers with what it stored.
     */
    suspend fun writeVaultNote(noteId: String, content: String): VaultNote {
        val body = Json.write(
            jsonObject {
                put("note_id", noteId)
                put("content", content)
            },
        )
        val payload = putJson("/api/v1/vault/note", body)
        return decode("vault note") { vaultNoteFromWire(payload.noteBody()) }
    }

    /** Create a note in the open vault and answer with it, as it was written. */
    suspend fun createVaultNote(noteId: String, title: String, content: String): VaultNote {
        val body = Json.write(
            jsonObject {
                put("note_id", noteId)
                put("title", title)
                put("content", content)
            },
        )
        val payload = postJson("/api/v1/vault/note", body)
        return decode("vault note") { vaultNoteFromWire(payload.noteBody()) }
    }

    /**
     * Delete a note from the open vault.
     *
     * The server answers `{"deleted": true}`. An answer that says the note
     * was not deleted is a failure, not a quiet success, and it is raised.
     */
    suspend fun deleteVaultNote(noteId: String) {
        val payload = sendJson(
            path = "/api/v1/vault/note?note_id=${queryValue(noteId)}",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
        )
        if (payload.containsKey("deleted") && !payload.jsonBool("deleted")) {
            throw BackendError.Malformed(
                "answer to the delete",
                IllegalStateException("the server answered that the note was not deleted"),
            )
        }
    }

    /**
     * Move a note to another path in the open vault.
     *
     * When the caller asks for it, the `[[Old]]` links in the other notes
     * are rewritten to `[[New]]`, and the answer says how many of them
     * were.
     */
    suspend fun renameVaultNote(
        fromId: String,
        toId: String,
        updateLinks: Boolean,
    ): VaultRenameResult {
        val body = Json.write(
            jsonObject {
                put("from_id", fromId)
                put("to_id", toId)
                put("update_links", updateLinks)
            },
        )
        val payload = postJson("/api/v1/vault/note/rename", body)
        return decode("rename answer") { vaultRenameResultFromWire(payload) }
    }

    /** The vault's ranked search: a title hit outranks a body hit. */
    suspend fun searchVault(query: String, limit: Int): List<VaultSearchHit> {
        val payload = getJson(
            "/api/v1/vault/search?q=${queryValue(query)}&limit=$limit",
        )
        return decode("search hits") {
            payload.jsonObjectList("results").map { hit -> vaultSearchHitFromWire(hit) }
        }
    }

    /** Every tag of the open vault, with the number of notes that carry it. */
    suspend fun vaultTags(): List<VaultTag> {
        val payload = getJson("/api/v1/vault/tags")
        return decode("vault tags") {
            payload.jsonObjectList("tags").map { tag -> vaultTagFromWire(tag) }
        }
    }

    // --- The notebooks ------------------------------------------------------

    /**
     * The notebooks the server holds, and the folder they live in.
     *
     * The library is the answer in one piece: where the notebooks are kept
     * and what is in there, so the client never shows a list beside a folder
     * that does not hold it.
     */
    suspend fun notebooks(): NotebookLibrary {
        val payload = getJson("/api/v1/notebooks")
        return decode("notebook library") { notebookLibraryFromWire(payload) }
    }

    /**
     * Point the server at a folder of notebooks and make it the active one.
     *
     * The root is the operator's own folder, named by its absolute path: a
     * notebook is a folder of directories the operator keeps, so the client
     * says which folder and the server works inside it.
     */
    suspend fun openNotebooks(root: String): NotebookLibrary {
        val body = Json.write(
            jsonObject {
                put("root", root)
            },
        )
        val payload = postJson("/api/v1/notebooks/open", body)
        return decode("notebook library") { notebookLibraryFromWire(payload) }
    }

    /**
     * One notebook, whole: its title, the sources it holds and the notes it
     * refers to.
     */
    suspend fun notebook(notebookId: String): Notebook {
        val payload = getJson("/api/v1/notebooks/${pathSegment(notebookId)}")
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Create a notebook with a title and a description.
     *
     * The id is the server's to derive from the title, so the same title
     * always yields the same notebook.
     */
    suspend fun createNotebook(title: String, description: String): Notebook {
        val body = Json.write(
            jsonObject {
                put("title", title)
                put("description", description)
            },
        )
        val payload = postJson("/api/v1/notebooks", body)
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Write a notebook's title and description back.
     *
     * The text is the operator's, so the server stores what was typed and
     * answers with what it stored.
     */
    suspend fun updateNotebook(
        notebookId: String,
        title: String,
        description: String,
    ): Notebook {
        val body = Json.write(
            jsonObject {
                put("title", title)
                put("description", description)
            },
        )
        val payload = putJson("/api/v1/notebooks/${pathSegment(notebookId)}", body)
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Delete a notebook.
     *
     * The server answers `{"deleted": true}`. An answer that says the
     * notebook was not deleted is a failure, not a quiet success, and it is
     * raised.
     */
    suspend fun deleteNotebook(notebookId: String) {
        val payload = sendJson(
            path = "/api/v1/notebooks/${pathSegment(notebookId)}",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
        )
        if (payload.containsKey("deleted") && !payload.jsonBool("deleted")) {
            throw BackendError.Malformed(
                "answer to the delete",
                IllegalStateException("the server answered that the notebook was not deleted"),
            )
        }
    }

    /**
     * Add a source to a notebook.
     *
     * The source is the operator's; the notebook only points at it. Adding a
     * source the notebook already holds changes nothing.
     */
    suspend fun addNotebookSource(notebookId: String, sourceId: String): Notebook {
        val body = Json.write(
            jsonObject {
                put("source_id", sourceId)
            },
        )
        val payload = postJson("/api/v1/notebooks/${pathSegment(notebookId)}/sources", body)
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Remove a source from a notebook.
     *
     * The source stays in the library: removing it from the notebook does not
     * take it away from the operator.
     */
    suspend fun removeNotebookSource(notebookId: String, sourceId: String): Notebook {
        val payload = sendJson(
            path = "/api/v1/notebooks/${pathSegment(notebookId)}/sources" +
                "?source_id=${queryValue(sourceId)}",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
        )
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Add a note to a notebook.
     *
     * The note id is a relative path, so it holds `/`: it travels in the body
     * and never in the route, where it would become two path segments.
     */
    suspend fun addNotebookNote(notebookId: String, noteId: String): Notebook {
        val body = Json.write(
            jsonObject {
                put("note_id", noteId)
            },
        )
        val payload = postJson("/api/v1/notebooks/${pathSegment(notebookId)}/notes", body)
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Remove a note from a notebook.
     *
     * The note id is a relative path, so it travels in the query string: a
     * `%2F` in a route breaks FastAPI's routing.
     */
    suspend fun removeNotebookNote(notebookId: String, noteId: String): Notebook {
        val payload = sendJson(
            path = "/api/v1/notebooks/${pathSegment(notebookId)}/notes" +
                "?note_id=${queryValue(noteId)}",
            method = "DELETE",
            body = ByteArray(0),
            contentType = null,
        )
        return decode("notebook") { notebookFromWire(payload.notebookBody()) }
    }

    /**
     * Ask the notebook a question of its own sources.
     *
     * The answer is grounded: it leans on passages of the notebook's sources
     * and cites them. An answer that says the sources do not answer the
     * question is a successful ask, and it is reported as it was given.
     */
    suspend fun askNotebook(notebookId: String, question: String): NotebookAnswer {
        val body = Json.write(
            jsonObject {
                put("question", question)
            },
        )
        val payload = postJson("/api/v1/notebooks/${pathSegment(notebookId)}/ask", body)
        return decode("notebook answer") {
            if (!payload.containsKey("answer")) {
                throw ValidationError("the answer must carry its own text", field = "answer")
            }
            notebookAnswerFromWire(payload.answerBody())
        }
    }

    // --- The transport ------------------------------------------------------

    private suspend fun getJson(path: String): Map<String, Any?> =
        sendJson(path, "GET", ByteArray(0), null)

    private suspend fun postJson(path: String, body: String): Map<String, Any?> =
        sendJson(path, "POST", body.toByteArray(StandardCharsets.UTF_8), "application/json")

    private suspend fun putJson(path: String, body: String): Map<String, Any?> =
        sendJson(path, "PUT", body.toByteArray(StandardCharsets.UTF_8), "application/json")

    private suspend fun sendJson(
        path: String,
        method: String,
        body: ByteArray,
        contentType: String?,
    ): Map<String, Any?> {
        val builder = HttpRequest.newBuilder().uri(URI.create("$baseUrl$path"))
        builder.header("Authorization", credential.authorization())
        if (contentType != null) {
            builder.header("Content-Type", contentType)
        }
        builder.header("Accept", "application/json")
        val publisher = if (body.isEmpty()) {
            HttpRequest.BodyPublishers.noBody()
        } else {
            HttpRequest.BodyPublishers.ofByteArray(body)
        }
        builder.method(method, publisher)

        val response = http.sendCancellable(builder.build(), HttpResponse.BodyHandlers.ofByteArray())
        if (response.statusCode() !in 200..299) {
            throw BackendError.Refused(response.statusCode(), response.readDetail())
        }
        val text = String(response.body(), StandardCharsets.UTF_8)
        if (text.isBlank()) {
            return emptyMap()
        }
        return try {
            Json.parseObject(text)
        } catch (cause: ValidationError) {
            throw BackendError.Malformed("response", cause)
        }
    }

    /** Read the `detail` of a refusal, in whichever shape the server used. */
    private fun HttpResponse<ByteArray>.readDetail(): String {
        val text = String(body(), StandardCharsets.UTF_8)
        if (text.isBlank()) return "no detail was given"
        return try {
            val detail = Json.parseObject(text)["detail"]
            when (detail) {
                null -> text
                is String -> detail
                else -> Json.write(detail)
            }
        } catch (cause: ValidationError) {
            text
        }
    }

    // --- The payloads ------------------------------------------------------

    private fun Map<String, Any?>.toIngestResult(): IngestResult {
        val source = jsonObject("source")
        return IngestResult(
            sourceId = source.jsonString("id", jsonString("source_id")),
            title = source.jsonString("title"),
            chunks = jsonObjectList("chunks").map { chunk -> sourceChunkFromWire(chunk) },
            omissions = jsonStringList("omissions"),
        )
    }

    private fun Map<String, Any?>.scorecardOrNull(): VerificationScorecard? {
        val card = this["scorecard"]
        if (card !is Map<*, *>) return null
        @Suppress("UNCHECKED_CAST")
        return verificationScorecardFromWire(card as Map<String, Any?>)
    }

    /**
     * Read an answer with the tier's own decoder.
     *
     * A payload the tier cannot read is a malformed answer, not a silent
     * empty result: the operator is told, rather than shown a vault that
     * looks empty.
     */
    private inline fun <T> decode(what: String, block: () -> T): T = try {
        block()
    } catch (cause: ValidationError) {
        throw BackendError.Malformed(what, cause)
    }

    /**
     * Encode a value for a query string.
     *
     * The value is encoded on its own, never the whole URL: a note id is a
     * path and holds `/`, and a path pasted into a route becomes two path
     * segments. A space is encoded as `%20` rather than `+`, so the value
     * reads the same however the server decodes it.
     */
    private fun queryValue(value: String): String =
        URLEncoder.encode(value, Charsets.UTF_8).replace("+", "%20")

    /**
     * The note inside an answer.
     *
     * The contract fixes the note itself as the answer; the server may wrap
     * it in a `note` envelope. Both shapes are read here, so the client
     * speaks the contract and still reads the server that answers it.
     */
    private fun Map<String, Any?>.noteBody(): Map<String, Any?> {
        val envelope = this["note"]
        @Suppress("UNCHECKED_CAST")
        return if (envelope is Map<*, *>) envelope as Map<String, Any?> else this
    }

    /**
     * The notebook inside an answer.
     *
     * The contract wraps the notebook in a `notebook` envelope. Both shapes
     * are read here, as the note's are, so the client speaks the contract and
     * still reads a server that answers it plainly.
     */
    private fun Map<String, Any?>.notebookBody(): Map<String, Any?> {
        val envelope = this["notebook"]
        @Suppress("UNCHECKED_CAST")
        return if (envelope is Map<*, *>) envelope as Map<String, Any?> else this
    }

    /**
     * The answer inside an answer.
     *
     * The contract fixes the answer itself as the payload; the server may
     * wrap it in an `answer` envelope. The answer's own text is a string, so
     * a wrapped answer is the only case in which the `answer` key holds an
     * object.
     */
    private fun Map<String, Any?>.answerBody(): Map<String, Any?> {
        val envelope = this["answer"]
        @Suppress("UNCHECKED_CAST")
        return if (envelope is Map<*, *>) envelope as Map<String, Any?> else this
    }

    companion object {
        /** The content type of a Word document package. */
        private const val DOCX_CONTENT_TYPE: String =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        /** The HTTP client every call shares. */
        fun defaultHttpClient(): HttpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .followRedirects(HttpClient.Redirect.NORMAL)
            .build()
    }
}
