package lmeditor.desktop.model

import java.io.File
import java.io.IOException
import java.net.http.HttpClient
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import lmeditor.core.BookManifest
import lmeditor.core.LMeditorError
import lmeditor.core.Notebook
import lmeditor.core.NotebookSummary
import lmeditor.core.OutlineNodeInput
import lmeditor.desktop.net.ApiCredential
import lmeditor.desktop.net.BackendClient
import lmeditor.desktop.net.BackendError
import lmeditor.desktop.net.SseClient

/**
 * How many hits the vault's search asks for.
 *
 * It is the server's own default, so the workspace asks for the same number
 * the backend would have answered with anyway.
 */
private const val VAULT_SEARCH_LIMIT: Int = 50

/**
 * Whether the status the window shows means a run is still in flight.
 *
 * The window asks this before it offers to start another run, and the strip
 * says "running" for as long as the answer is yes.
 */
internal fun isRunInFlight(status: String): Boolean =
    status == "starting" || status == "running" || status == "awaiting_human_review"

/**
 * The actions the studio window can take, and the state they leave behind.
 *
 * The controller owns the whole conversation with the backend: it starts runs,
 * follows the event stream, answers the review gate, and fetches the finished
 * document. The window itself holds no logic of its own -- it renders the
 * state and calls these methods -- so what the operator sees and what the
 * server is told cannot drift apart.
 */
class StudioController(
    private val scope: CoroutineScope,
    private val http: HttpClient = BackendClient.defaultHttpClient(),
) {

    private val _state = MutableStateFlow(StudioState())
    private val connectionLock = Any()

    /**
     * The credential the API requires.
     *
     * It is resolved once, when the window is built, and the same one
     * travels on every request: the REST client and the stream are two
     * halves of one conversation with the server, and a client that carried
     * two different tokens would be refused by whichever one was wrong.
     *
     * Reading it once is enough. A token that changes on disk changes on the
     * server's next start too, and the client is restarted with it.
     */
    private val credential: ApiCredential = ApiCredential.resolve()

    private var connection = Connection(
        BackendClient(StudioState.DEFAULT_BACKEND_URL, http, credential),
    )
    private var streamJob: Job? = null

    private inner class Connection(val backend: BackendClient) {
        val events = SseClient(http, backend.baseUrl, credential)
        val job = Job(scope.coroutineContext[Job])
        var activeTasks = 0

        /**
         * Whether a run is in flight right now.
         *
         * The window keeps this, and not the status string, because a second
         * run has to be refused while the first one is still streaming: the
         * first would be aborted in the middle, and an operator told nothing
         * would be left with an empty manuscript and no reason for it.
         */
        var runInFlight = false

        /** Which run owns the flag: a newer run supersedes an older one. */
        var runToken = 0

        fun updateState(transform: (StudioState) -> StudioState) {
            synchronized(connectionLock) {
                if (connection === this) _state.update(transform)
            }
        }

        /**
         * Make a section the server read the open one.
         *
         * The server's answer is the truth: the buffer, the saved text and
         * the heading all come from it, and the section starts clean.
         */
        fun openSectionText(text: BookSectionText) {
            updateState { state ->
                state.copy(
                    documents = state.documents.copy(
                        openSectionId = text.sectionId,
                        sectionTitle = text.title,
                        sectionDraft = text.content,
                        savedContent = text.content,
                        sectionDirty = false,
                        saving = false,
                    ),
                )
            }
        }

        /**
         * Save one section's text, and record what the server stored.
         *
         * A failed save keeps the draft and leaves the section dirty: the
         * operator's text is never thrown away by a save that did not happen.
         * The answer says whether the caller may carry on.
         */
        suspend fun saveSectionText(bookId: String, sectionId: String, content: String): Boolean {
            updateState { it.copy(documents = it.documents.copy(saving = true)) }
            try {
                val result = backend.saveBookSection(bookId, sectionId, content)
                updateState { state ->
                    val documents = state.documents
                    if (documents.openBook?.bookId != bookId ||
                        documents.openSectionId != sectionId
                    ) {
                        return@updateState state.copy(documents = documents.copy(saving = false))
                    }
                    state.copy(
                        documents = documents.copy(
                            sectionDraft = result.content,
                            savedContent = result.content,
                            sectionDirty = false,
                            saving = false,
                        ),
                    )
                }
                return true
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                updateState {
                    it.copy(documents = it.documents.copy(saving = false), error = cause.message)
                }
                return false
            } catch (cause: LMeditorError) {
                updateState {
                    it.copy(documents = it.documents.copy(saving = false), error = cause.message)
                }
                return false
            }
        }

        /**
         * Save the open section when it holds something the server has not
         * been told.
         *
         * The answer says whether the caller may switch away from the
         * section. A failed save refuses the switch: a word processor that
         * drops an unsaved paragraph to show another one is worse than one
         * that refuses to move.
         */
        suspend fun saveDirtySection(): Boolean {
            val documents = _state.value.documents
            val bookId = documents.openBook?.bookId ?: return true
            val sectionId = documents.openSectionId ?: return true
            if (!documents.sectionDirty) return true
            return saveSectionText(bookId, sectionId, documents.sectionDraft)
        }

        /** Open the book's first section, if the book has one at all. */
        suspend fun openFirstSection(manifest: BookManifest) {
            val first = manifest.sections.firstOrNull() ?: return
            val text = backend.bookSection(manifest.bookId, first.sectionId)
            openSectionText(text)
        }

        /**
         * Fetch the book's manifest again, so its counts are current.
         *
         * The section's text changed, so the words and passages it holds
         * changed with it. A manifest that could not be fetched is reported,
         * but the save it follows has already happened and is not undone.
         */
        suspend fun refreshOpenBook(bookId: String) {
            val manifest = try {
                backend.book(bookId)
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                updateState {
                    it.copy(error = "The section was saved, but the book could not be read again: ${cause.message}")
                }
                return
            } catch (cause: LMeditorError) {
                updateState {
                    it.copy(error = "The section was saved, but the book could not be read again: ${cause.message}")
                }
                return
            }
            updateState { state ->
                val documents = state.documents
                if (documents.openBook?.bookId != bookId) return@updateState state
                state.copy(
                    documents = documents.copy(
                        openBook = manifest,
                        books = documents.books.map { if (it.bookId == bookId) manifest else it },
                    ),
                )
            }
        }

        /**
         * Save one note's text, and record what the server stored.
         *
         * A failed save keeps the draft and leaves the note dirty: the
         * operator's text is never thrown away by a save that did not happen.
         * The answer says whether the caller may carry on.
         */
        suspend fun saveVaultNoteText(noteId: String, content: String): Boolean {
            updateState { it.copy(vault = it.vault.copy(saving = true)) }
            try {
                val note = backend.writeVaultNote(noteId, content)
                updateState { state ->
                    val vault = state.vault
                    if (vault.openNoteId != noteId) {
                        return@updateState state.copy(vault = vault.copy(saving = false))
                    }
                    state.copy(
                        vault = vault.copy(
                            note = note,
                            draft = note.content,
                            savedContent = note.content,
                            dirty = false,
                            saving = false,
                        ),
                    )
                }
                return true
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                updateState {
                    it.copy(vault = it.vault.copy(saving = false), error = cause.message)
                }
                return false
            } catch (cause: LMeditorError) {
                updateState {
                    it.copy(vault = it.vault.copy(saving = false), error = cause.message)
                }
                return false
            }
        }

        /**
         * Save the open note when it holds something the server has not been
         * told.
         *
         * The answer says whether the caller may switch away from the note.
         * A failed save refuses the switch: a vault that drops an unsaved
         * paragraph to show another note is worse than one that refuses to
         * move.
         */
        suspend fun saveDirtyVaultNote(): Boolean {
            val vault = _state.value.vault
            val noteId = vault.openNoteId ?: return true
            if (!vault.dirty) return true
            return saveVaultNoteText(noteId, vault.draft)
        }

        /**
         * Read everything the workspace shows about the open vault.
         *
         * The three answers belong to one moment: the summary says which
         * folder is open, the notes are what it holds and the tags are what
         * they carry. They are read together and written together, so the
         * window never shows the notes of one vault beside the tags of
         * another.
         */
        suspend fun refreshVaultContents() {
            val summary = backend.vault()
            val notes = backend.listVaultNotes()
            val tags = backend.vaultTags()
            updateState { state ->
                state.copy(
                    vault = state.vault.copy(
                        summary = summary,
                        notes = notes,
                        tags = tags,
                    ),
                )
            }
        }

        /**
         * Read the notebooks again: the folder they live in and the notebooks
         * in it.
         *
         * The library is one answer, so it is written in one piece: the
         * window never shows a list of notebooks beside a folder that does
         * not hold them. The open notebook is kept only when the library
         * still holds it -- a notebook that is no longer there must not stay
         * on screen as though it were.
         */
        suspend fun refreshNotebookLibrary() {
            val library = backend.notebooks()
            updateState { state ->
                val notebooks = state.notebooks
                val open = notebooks.openNotebook
                val kept = open?.takeIf { notebook ->
                    library.notebooks.any { it.notebookId == notebook.notebookId }
                }
                state.copy(
                    notebooks = notebooks.copy(
                        library = library,
                        notebooks = library.notebooks,
                        openNotebook = kept,
                        titleDraft = if (kept != null) notebooks.titleDraft else "",
                        descriptionDraft = if (kept != null) notebooks.descriptionDraft else "",
                        answer = if (kept != null) notebooks.answer else null,
                    ),
                )
            }
        }

        /**
         * Apply a notebook the server returned.
         *
         * The server's answer is the truth: the open notebook becomes the
         * server's own copy, and the summary in the list is rebuilt from it,
         * so the counts the list shows are the counts the notebook really
         * holds.
         */
        fun applyNotebook(notebook: Notebook) {
            updateState { state ->
                val notebooks = state.notebooks
                val summary = NotebookSummary(
                    notebookId = notebook.notebookId,
                    title = notebook.title,
                    description = notebook.description,
                    sourceCount = notebook.sourceIds.size,
                    noteCount = notebook.noteIds.size,
                    modifiedAt = notebook.modifiedAt,
                )
                state.copy(
                    notebooks = notebooks.copy(
                        notebooks = notebooks.notebooks.map {
                            if (it.notebookId == notebook.notebookId) summary else it
                        },
                        openNotebook = notebook,
                        titleDraft = notebook.title,
                        descriptionDraft = notebook.description,
                    ),
                )
            }
        }

        /**
         * Record a failure of the notebook workspace, in plain words.
         *
         * The failure is the workspace's own, so it is shown beside the
         * notebooks rather than as a fault of the whole window.
         */
        fun notebookFailure(message: String) {
            updateState { it.copy(notebooks = it.notebooks.copy(error = message)) }
        }
    }

    /** The state the window renders. */
    val state: StateFlow<StudioState> = _state.asStateFlow()

    // --- The connection -----------------------------------------------------

    /** Point the window at a different backend. */
    fun setBackendUrl(url: String) {
        val next = try {
            BackendClient(url, http, credential)
        } catch (cause: LMeditorError) {
            _state.update { it.copy(error = cause.message) }
            return
        }
        synchronized(connectionLock) {
            if (connection.backend.baseUrl == next.baseUrl) return
            connection.job.cancel()
            stopStream()
            connection = Connection(next)
            _state.update {
                StudioState(
                    backendUrl = next.baseUrl,
                    topic = it.topic,
                    operatorOutline = it.operatorOutline,
                )
            }
        }
    }

    /** Ask the backend whether it is up. */
    fun checkHealth() = launchTask {
        val health = backend.health()
        updateState { it.copy(health = health) }
    }

    /** List the sources the server holds. */
    fun refreshSources() = launchTask {
        val sources = backend.listSources()
        updateState { it.copy(sources = sources) }
    }

    /** Choose the source the next run is written from. */
    fun selectSource(sourceId: String?) {
        _state.update { it.copy(selectedSourceId = sourceId) }
    }

    // --- The material -------------------------------------------------------

    /** Ingest a file the operator picked. */
    fun ingestFile(sourceId: String, title: String, fileName: String, content: ByteArray) =
        launchTask {
            val result = backend.ingestFile(sourceId, title, fileName, content)
            val sources = backend.listSources()
            updateState {
                it.copy(
                    chunks = result.chunks,
                    sources = sources,
                    selectedSourceId = result.sourceId,
                    notice = "Ingested ${result.chunks.size} chunk(s) from $fileName.",
                )
            }
        }

    /** Ingest a source the server fetches from a URL. */
    fun ingestUrl(sourceId: String, title: String, url: String) = launchTask {
        val result = backend.ingestUrl(sourceId, title, url)
        val sources = backend.listSources()
        updateState {
            it.copy(
                chunks = result.chunks,
                sources = sources,
                selectedSourceId = result.sourceId,
                notice = "Ingested ${result.chunks.size} chunk(s) from $url.",
            )
        }
    }

    // --- The run ------------------------------------------------------------

    /** Set what the next document is about. */
    fun setTopic(topic: String) {
        _state.update { it.copy(topic = topic) }
    }

    /**
     * Set the operator's own outline: the sections the next draft is written
     * to.
     *
     * The outline is replaced wholesale rather than edited in place, so the
     * operator's steer is always the whole steer. A steer that half-applied
     * would be a steer the operator did not give.
     */
    fun setOperatorOutline(outline: List<OutlineNodeInput>) {
        _state.update { it.copy(operatorOutline = outline) }
    }

    /**
     * Start a run and follow it.
     *
     * A second run is refused while one is in flight, and the refusal is
     * shown: the first run would otherwise be aborted in the middle and the
     * window would look idle while it was still working.
     */
    fun startRun() {
        val owner = synchronized(connectionLock) { connection }
        if (owner.runInFlight) {
            owner.updateState {
                it.copy(error = "A run is already in flight. Stop it before starting another.")
            }
            return
        }
        owner.runInFlight = true
        val token = owner.runToken + 1
        owner.runToken = token
        launchTask {
            val current = _state.value
            val topic = current.topic.trim()
            if (topic.isEmpty()) {
                runInFlight = false
                updateState { it.copy(error = "A document needs a topic before it can be written.") }
                return@launchTask
            }
            // The run has begun: the strip says so before the server answers,
            // so the window never shows itself as idle while it is working.
            updateState { it.copy(status = "starting", error = null) }
            val sourceIds = current.selectedSourceId?.let { listOf(it) } ?: emptyList()
            val ack = try {
                backend.generate(topic, sourceIds, current.operatorOutline)
            } catch (cause: CancellationException) {
                runInFlight = false
                updateState { it.copy(status = "failed") }
                throw cause
            } catch (cause: BackendError) {
                runInFlight = false
                updateState { it.copy(status = "failed") }
                throw cause
            } catch (cause: LMeditorError) {
                runInFlight = false
                updateState { it.copy(status = "failed") }
                throw cause
            }
            updateState {
                it.copy(
                    docId = ack.docId,
                    status = "running",
                    currentStep = "",
                    topic = topic,
                    perspectives = emptyList(),
                    researchNotes = emptyList(),
                    referencedChunkIds = emptyList(),
                    outline = emptyList(),
                    liveText = emptyMap(),
                    sections = emptyList(),
                    claims = emptyList(),
                    lineages = emptyList(),
                    scorecard = null,
                    pendingReview = null,
                    metrics = null,
                    selectedClaimId = null,
                    selectedChunkId = null,
                    notice = "Run started.",
                )
            }
            followStream(this, ack.docId, token)
        }
    }

    /**
     * Stop following the run that is in flight.
     *
     * The run is stopped deliberately, and the window says so: a stream that
     * ends without a word is indistinguishable from a run that finished.
     */
    fun stopRun() {
        val owner = synchronized(connectionLock) { connection }
        stopStream()
        owner.runToken++
        owner.runInFlight = false
        owner.updateState {
            it.copy(
                status = "stopped",
                notice = "Stopped following this run. The server may still be working on it.",
            )
        }
    }

    /**
     * Whether a run is in flight right now.
     *
     * The window asks this before it offers to start another one.
     */
    fun runInFlight(): Boolean = synchronized(connectionLock) { connection.runInFlight }

    /** Say something in the strip, without failing anything. */
    fun notice(message: String) {
        _state.update { it.copy(notice = message) }
    }

    /** Fetch the finished document, and the scorecard measured on it. */
    fun refreshDocument() = launchTask {
        val docId = _state.value.docId ?: return@launchTask
        val document = backend.document(docId)
        updateState {
            if (it.docId != docId) return@updateState it
            it.copy(
                status = document.status,
                sections = document.sections,
                scorecard = document.scorecard ?: it.scorecard,
                error = document.error,
                pendingReview = if (document.isCompleted || document.status == "failed") null else it.pendingReview,
            )
        }
    }

    /** Answer the review a run is waiting for. */
    fun answerReview(approved: Boolean, feedback: String) = launchTask {
        val docId = _state.value.docId ?: return@launchTask
        backend.approve(docId, approved, feedback.ifBlank { null })
        updateState {
            if (it.docId != docId) return@updateState it
            it.copy(
                pendingReview = null,
                notice = if (approved) "The run was approved." else "The run was rejected.",
            )
        }
    }

    /** Fetch the exported Word document and write it where the operator asked. */
    fun exportDocxTo(target: File) = launchTask {
        val docId = _state.value.docId ?: return@launchTask
        val bytes = backend.exportDocx(docId)
        withContext(Dispatchers.IO) {
            target.parentFile?.let { Files.createDirectories(it.toPath()) }
            Files.write(target.toPath(), bytes)
        }
        updateState { it.copy(notice = "Exported ${bytes.size} bytes to ${target.absolutePath}.") }
    }

    // --- The documents ------------------------------------------------------

    /**
     * List every book the server's library holds.
     *
     * A failure leaves the list as it was: an empty list would look like a
     * library with nothing in it, which is not what a refused request means.
     */
    fun refreshBooks() = launchTask {
        val books = backend.listBooks()
        updateState { it.copy(documents = it.documents.copy(books = books)) }
    }

    /**
     * Import a Word document the operator picked, and open it.
     *
     * The file is read here and its bytes are uploaded: the server reads what
     * it was given rather than a path on this machine. The imported book
     * becomes the open book and its first section is opened, so the operator
     * sees the document's text at once instead of an empty editor.
     */
    fun openDocx(file: File, title: String = "") = launchTask {
        if (!saveDirtySection()) return@launchTask
        val content = try {
            withContext(Dispatchers.IO) { file.readBytes() }
        } catch (cause: IOException) {
            updateState {
                it.copy(
                    error = "Could not read ${file.absolutePath}: " +
                        (cause.message ?: "the file could not be read"),
                )
            }
            return@launchTask
        }
        val manifest = backend.importDocx(file.name, content, title)
        val books = backend.listBooks()
        updateState {
            it.copy(
                documents = it.documents.copy(
                    books = books,
                    openBook = manifest,
                    openSectionId = null,
                    sectionTitle = "",
                    sectionDraft = "",
                    savedContent = "",
                    sectionDirty = false,
                    saving = false,
                ),
            )
        }
        openFirstSection(manifest)
    }

    /**
     * Open one of the server's books, and its first section.
     */
    fun openBook(bookId: String) = launchTask {
        if (!saveDirtySection()) return@launchTask
        val manifest = backend.book(bookId)
        updateState {
            it.copy(
                documents = it.documents.copy(
                    openBook = manifest,
                    openSectionId = null,
                    sectionTitle = "",
                    sectionDraft = "",
                    savedContent = "",
                    sectionDirty = false,
                    saving = false,
                ),
            )
        }
        openFirstSection(manifest)
    }

    /**
     * Open one section of the open book.
     *
     * The text comes from the server, so the buffer starts as the server's
     * own copy and the section starts clean. A refused request leaves the
     * open section exactly as it was.
     */
    fun openSection(sectionId: String) = launchTask {
        val bookId = _state.value.documents.openBook?.bookId
        if (bookId == null) {
            updateState { it.copy(error = "No book is open, so there is no section to open.") }
            return@launchTask
        }
        if (!saveDirtySection()) return@launchTask
        val text = backend.bookSection(bookId, sectionId)
        openSectionText(text)
    }

    /**
     * Take the operator's text for the open section.
     *
     * This is a local edit: nothing is told to the server until the section
     * is saved. The dirty mark is the difference between the buffer and what
     * the server holds, so an edit that is typed and then undone is not
     * dirty.
     */
    fun setSectionDraft(text: String) {
        _state.update {
            val documents = it.documents
            if (documents.openSectionId == null) return@update it
            it.copy(
                documents = documents.copy(
                    sectionDraft = text,
                    sectionDirty = text != documents.savedContent,
                ),
            )
        }
    }

    /**
     * Save the open section's text to the server.
     */
    fun saveSection() = launchTask {
        val documents = _state.value.documents
        val bookId = documents.openBook?.bookId
        val sectionId = documents.openSectionId
        if (bookId == null || sectionId == null) {
            updateState { it.copy(error = "No section is open, so there is nothing to save.") }
            return@launchTask
        }
        if (!saveSectionText(bookId, sectionId, documents.sectionDraft)) return@launchTask
        refreshOpenBook(bookId)
    }

    /**
     * Fetch the open book's Word document and write it where the operator
     * asked.
     */
    fun exportBookDocxTo(target: File) = launchTask {
        val bookId = _state.value.documents.openBook?.bookId
        if (bookId == null) {
            updateState { it.copy(error = "No book is open, so there is nothing to export.") }
            return@launchTask
        }
        val bytes = backend.exportBookDocx(bookId)
        withContext(Dispatchers.IO) {
            target.parentFile?.let { Files.createDirectories(it.toPath()) }
            writeWholeFile(target, bytes)
        }
        updateState { it.copy(notice = "Exported ${bytes.size} bytes to ${target.absolutePath}.") }
    }

    /**
     * Close the open book and its section.
     *
     * The library stays: the books are the server's, and the operator comes
     * back to them. Text the operator had not saved is saved first, exactly as
     * it is when another section or another book is opened: closing the book is
     * a move away from the text just as much as opening another one is, and a
     * word processor that drops an unsaved paragraph on the way out is worse
     * than one that keeps it.
     */
    fun closeBook() = launchTask {
        if (!saveDirtySection()) return@launchTask
        _state.update {
            val documents = it.documents
            it.copy(
                documents = documents.copy(
                    openBook = null,
                    openSectionId = null,
                    sectionTitle = "",
                    sectionDraft = "",
                    savedContent = "",
                    sectionDirty = false,
                    saving = false,
                ),
                notice = "The book was closed.",
            )
        }
    }

    /**
     * Write the bytes to the path, whole or not at all.
     *
     * The bytes go to a temporary file beside the target and are moved into
     * place only once the whole write has succeeded, so a failed export never
     * leaves a truncated Word package where the operator expects a document.
     */
    private fun writeWholeFile(target: File, bytes: ByteArray) {
        val directory = target.absoluteFile.parentFile
            ?: throw IOException("The path ${target.absolutePath} has no directory to write into.")
        val temporary = Files.createTempFile(directory.toPath(), "lmeditor-", ".part")
        try {
            Files.write(temporary, bytes)
            Files.move(temporary, target.toPath(), StandardCopyOption.REPLACE_EXISTING)
        } catch (cause: IOException) {
            try {
                Files.deleteIfExists(temporary)
            } catch (cleanup: IOException) {
                cause.addSuppressed(cleanup)
            }
            throw cause
        }
    }

    // --- The vault ----------------------------------------------------------

    /**
     * Open the folder the operator chose as the vault.
     *
     * The folder is the operator's own: a vault is a folder of markdown
     * files, not a private format, so the client names the folder and the
     * server works inside it. Everything the window shows afterwards is read
     * again from the server, so nothing on screen is left over from the
     * vault that was open before.
     */
    fun openVault(folder: File) = launchTask {
        backend.openVault(folder.absolutePath)
        refreshVaultContents()
    }

    /**
     * Read the open vault again: its summary, its notes and its tags.
     *
     * A failure leaves the workspace as it was: an empty list would look
     * like a vault with nothing in it, which is not what a refused request
     * means.
     */
    fun refreshVault() = launchTask {
        refreshVaultContents()
    }

    /**
     * Open one note of the vault.
     *
     * The text comes from the server, so the draft starts as the server's
     * own copy and the note starts clean. A note that holds unsaved changes
     * is saved first: opening another note is a move away from the
     * operator's text, and a failed save refuses the move.
     */
    fun openVaultNote(noteId: String) = launchTask {
        if (noteId.isBlank()) {
            updateState { it.copy(error = "No note was named, so there is nothing to open.") }
            return@launchTask
        }
        if (!saveDirtyVaultNote()) return@launchTask
        val note = backend.vaultNote(noteId)
        updateState { state ->
            state.copy(
                vault = state.vault.copy(
                    openNoteId = note.noteId,
                    note = note,
                    draft = note.content,
                    savedContent = note.content,
                    dirty = false,
                    saving = false,
                ),
            )
        }
    }

    /**
     * Take the operator's text for the open note.
     *
     * This is a local edit: nothing is told to the server until the note is
     * saved. The dirty mark is the difference between the draft and what
     * the vault holds, so an edit that is typed and then undone is not
     * dirty.
     */
    fun setVaultDraft(text: String) {
        _state.update {
            val vault = it.vault
            if (vault.openNoteId == null) return@update it
            it.copy(
                vault = vault.copy(
                    draft = text,
                    dirty = text != vault.savedContent,
                ),
            )
        }
    }

    /**
     * Save the open note's text back to the file it came from.
     *
     * The vault is a folder of markdown files, so this writes that file and
     * nothing else: the operator's Obsidian opens the same folder and sees
     * the same note.
     */
    fun saveVaultNote() = launchTask {
        val vault = _state.value.vault
        val noteId = vault.openNoteId
        if (noteId == null) {
            updateState { it.copy(error = "No note is open, so there is nothing to save.") }
            return@launchTask
        }
        saveVaultNoteText(noteId, vault.draft)
    }

    /**
     * Create a note in the open vault.
     *
     * The note is created with its title and no body: the server writes the
     * title as the note's first heading, which is how Obsidian names a
     * note. The list is read again afterwards, so the new note is in it,
     * and the new note is opened.
     */
    fun createVaultNote(noteId: String, title: String) = launchTask {
        if (noteId.isBlank()) {
            updateState { it.copy(error = "A note needs an id before it can be created.") }
            return@launchTask
        }
        val note = backend.createVaultNote(noteId, title, "")
        val notes = backend.listVaultNotes()
        updateState { state ->
            state.copy(
                vault = state.vault.copy(
                    notes = notes,
                    openNoteId = note.noteId,
                    note = note,
                    draft = note.content,
                    savedContent = note.content,
                    dirty = false,
                    saving = false,
                ),
                notice = "Created ${note.path.ifBlank { note.noteId }}.",
            )
        }
    }

    /**
     * Delete a note from the open vault.
     *
     * The file is removed and the list is read again, so what is left on
     * screen is what is left on disk. A note that was open is closed: it
     * does not exist any more.
     */
    fun deleteVaultNote(noteId: String) = launchTask {
        if (noteId.isBlank()) {
            updateState { it.copy(error = "No note was named, so there is nothing to delete.") }
            return@launchTask
        }
        backend.deleteVaultNote(noteId)
        val notes = backend.listVaultNotes()
        updateState { state ->
            val vault = state.vault
            val wasOpen = vault.openNoteId == noteId
            state.copy(
                vault = vault.copy(
                    notes = notes,
                    openNoteId = if (wasOpen) null else vault.openNoteId,
                    note = if (wasOpen) null else vault.note,
                    draft = if (wasOpen) "" else vault.draft,
                    savedContent = if (wasOpen) "" else vault.savedContent,
                    dirty = if (wasOpen) false else vault.dirty,
                ),
                notice = "Deleted $noteId.",
            )
        }
    }

    /**
     * Move a note to another path in the open vault.
     *
     * The links that point at the note are rewritten to the new name, so
     * the graph stays whole; the answer says how many of them moved. The
     * note that is moved is saved first when it holds unsaved changes: a
     * move is not a reason to lose the operator's text.
     */
    fun renameVaultNote(fromId: String, toId: String) = launchTask {
        if (fromId.isBlank() || toId.isBlank()) {
            updateState {
                it.copy(error = "A rename needs both the note's old id and its new one.")
            }
            return@launchTask
        }
        val wasOpen = _state.value.vault.openNoteId == fromId
        if (wasOpen && !saveDirtyVaultNote()) return@launchTask
        val result = backend.renameVaultNote(fromId, toId, updateLinks = true)
        val notes = backend.listVaultNotes()
        val moved = if (wasOpen) backend.vaultNote(result.toId) else null
        updateState { state ->
            val vault = state.vault
            state.copy(
                vault = vault.copy(
                    notes = notes,
                    openNoteId = if (wasOpen) result.toId else vault.openNoteId,
                    note = moved ?: vault.note,
                    draft = moved?.content ?: vault.draft,
                    savedContent = moved?.content ?: vault.savedContent,
                    dirty = if (moved != null) false else vault.dirty,
                ),
                notice = "Renamed $fromId to ${result.toId} " +
                    "(${result.updatedLinks} link(s) updated).",
            )
        }
    }

    /**
     * Search the vault.
     *
     * The ranking is the tier's own: a title hit outranks a tag hit, which
     * outranks a body hit, and the order is the same every time.
     */
    fun searchVault(query: String) = launchTask {
        val trimmed = query.trim()
        if (trimmed.isEmpty()) {
            updateState {
                it.copy(vault = it.vault.copy(searchQuery = "", results = emptyList()))
            }
            return@launchTask
        }
        val results = backend.searchVault(trimmed, VAULT_SEARCH_LIMIT)
        updateState {
            it.copy(vault = it.vault.copy(searchQuery = trimmed, results = results))
        }
    }

    /**
     * Put the search results away, so the whole vault is listed again.
     */
    fun clearVaultSearch() {
        _state.update {
            it.copy(vault = it.vault.copy(searchQuery = "", results = emptyList()))
        }
    }

    // --- The notebooks ------------------------------------------------------

    /**
     * Read the notebooks the server holds, and the folder they live in.
     *
     * A failure leaves the workspace as it was: an empty list would look like
     * a folder with nothing in it, which is not what a refused request means.
     */
    fun refreshNotebooks() = launchNotebookTask {
        refreshNotebookLibrary()
    }

    /**
     * Open the folder the operator chose as the notebooks folder.
     *
     * The folder is the operator's own: a notebook is a folder of directories
     * the operator keeps, not a private format, so the client names the
     * folder and the server works inside it. Everything the window shows
     * afterwards is read again from the server, so nothing on screen is left
     * over from the folder that was open before.
     */
    fun openNotebooks(folder: File) = launchNotebookTask {
        backend.openNotebooks(folder.absolutePath)
        refreshNotebookLibrary()
    }

    /**
     * Open one notebook of the library.
     *
     * The notebook comes from the server, so the drafts start as the server's
     * own copy. The last answer is put away: it was the answer to a question
     * about another notebook, and showing it here would be a lie about what
     * was asked.
     */
    fun selectNotebook(notebookId: String) = launchNotebookTask {
        if (notebookId.isBlank()) {
            notebookFailure("No notebook was named, so there is nothing to open.")
            return@launchNotebookTask
        }
        val notebook = backend.notebook(notebookId)
        updateState { state ->
            state.copy(
                notebooks = state.notebooks.copy(
                    openNotebook = notebook,
                    titleDraft = notebook.title,
                    descriptionDraft = notebook.description,
                    answer = null,
                ),
            )
        }
    }

    /**
     * Create a notebook with a title and a description.
     *
     * The library is read again afterwards, so the new notebook is in the
     * list, and the new notebook is opened: it is what the operator was
     * making.
     */
    fun createNotebook(title: String, description: String) = launchNotebookTask {
        val cleanTitle = title.trim()
        if (cleanTitle.isEmpty()) {
            notebookFailure("A notebook needs a title before it can be created.")
            return@launchNotebookTask
        }
        val notebook = backend.createNotebook(cleanTitle, description.trim())
        refreshNotebookLibrary()
        updateState { state ->
            state.copy(
                notebooks = state.notebooks.copy(
                    openNotebook = notebook,
                    titleDraft = notebook.title,
                    descriptionDraft = notebook.description,
                    answer = null,
                ),
                notice = "Created ${notebook.title.ifBlank { notebook.notebookId }}.",
            )
        }
    }

    /**
     * Take the operator's title for the open notebook.
     *
     * This is a local edit: nothing is told to the server until the notebook
     * is saved. The dirty mark is the difference between the draft and what
     * the server holds.
     */
    fun setNotebookTitle(text: String) {
        _state.update {
            it.copy(notebooks = it.notebooks.copy(titleDraft = text))
        }
    }

    /**
     * Take the operator's description for the open notebook.
     */
    fun setNotebookDescription(text: String) {
        _state.update {
            it.copy(notebooks = it.notebooks.copy(descriptionDraft = text))
        }
    }

    /**
     * Dismiss the notebook workspace's own failure.
     *
     * The workspace keeps its failures beside the notebooks, so they are
     * dismissed on their own account and not only with the window's.
     */
    fun clearNotebookError() {
        _state.update {
            it.copy(notebooks = it.notebooks.copy(error = null))
        }
    }

    /**
     * Write the open notebook's title and description back.
     *
     * The text is the operator's, so the server stores what was typed and
     * answers with what it stored. A failed save keeps the drafts and leaves
     * the notebook dirty: the operator's words are never thrown away by a
     * save that did not happen.
     */
    fun saveNotebook() = launchNotebookTask {
        val notebooks = _state.value.notebooks
        val notebook = notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to save.")
            return@launchNotebookTask
        }
        val title = notebooks.titleDraft.trim()
        if (title.isEmpty()) {
            notebookFailure("A notebook needs a title.")
            return@launchNotebookTask
        }
        val saved = backend.updateNotebook(
            notebook.notebookId,
            title,
            notebooks.descriptionDraft.trim(),
        )
        applyNotebook(saved)
        updateState { it.copy(notice = "Saved ${saved.title.ifBlank { saved.notebookId }}.") }
    }

    /**
     * Delete the open notebook.
     *
     * The library is read again afterwards, so what is left on screen is what
     * is left in the folder. The notebook that was open is closed: it does
     * not exist any more.
     */
    fun deleteNotebook() = launchNotebookTask {
        val notebook = _state.value.notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to delete.")
            return@launchNotebookTask
        }
        backend.deleteNotebook(notebook.notebookId)
        refreshNotebookLibrary()
        updateState { state ->
            state.copy(
                notebooks = state.notebooks.copy(
                    openNotebook = null,
                    titleDraft = "",
                    descriptionDraft = "",
                    answer = null,
                ),
                notice = "Deleted ${notebook.title.ifBlank { notebook.notebookId }}.",
            )
        }
    }

    /**
     * Add a source to the open notebook.
     *
     * The source stays in the library: the notebook only points at it, so the
     * operator's material is not taken away by being filed.
     */
    fun addNotebookSource(sourceId: String) = launchNotebookTask {
        val notebook = _state.value.notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to add to.")
            return@launchNotebookTask
        }
        if (sourceId.isBlank()) {
            notebookFailure("No source was named, so there is nothing to add.")
            return@launchNotebookTask
        }
        applyNotebook(backend.addNotebookSource(notebook.notebookId, sourceId))
    }

    /**
     * Remove a source from the open notebook.
     *
     * The source itself is left alone: removing it from the notebook does not
     * take it away from the operator.
     */
    fun removeNotebookSource(sourceId: String) = launchNotebookTask {
        val notebook = _state.value.notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to remove from.")
            return@launchNotebookTask
        }
        if (sourceId.isBlank()) {
            notebookFailure("No source was named, so there is nothing to remove.")
            return@launchNotebookTask
        }
        applyNotebook(backend.removeNotebookSource(notebook.notebookId, sourceId))
    }

    /**
     * Add a note to the open notebook.
     *
     * The note id is a path inside the vault, so it holds `/`: it is sent as
     * one value and never pasted into a route, where it would become two
     * path segments.
     */
    fun addNotebookNote(noteId: String) = launchNotebookTask {
        val notebook = _state.value.notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to add to.")
            return@launchNotebookTask
        }
        if (noteId.isBlank()) {
            notebookFailure("A note needs an id before it can be added.")
            return@launchNotebookTask
        }
        applyNotebook(backend.addNotebookNote(notebook.notebookId, noteId))
    }

    /**
     * Remove a note from the open notebook.
     *
     * The note itself is left alone: removing it from the notebook does not
     * take it away from the operator.
     */
    fun removeNotebookNote(noteId: String) = launchNotebookTask {
        val notebook = _state.value.notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to remove from.")
            return@launchNotebookTask
        }
        if (noteId.isBlank()) {
            notebookFailure("No note was named, so there is nothing to remove.")
            return@launchNotebookTask
        }
        applyNotebook(backend.removeNotebookNote(notebook.notebookId, noteId))
    }

    /**
     * Take the operator's question.
     *
     * This is a local edit: the question is told to the server only when the
     * operator asks.
     */
    fun setNotebookQuestion(text: String) {
        _state.update {
            it.copy(notebooks = it.notebooks.copy(question = text))
        }
    }

    /**
     * Ask the open notebook a question of its own sources.
     *
     * The answer is the server's, and it is shown as it was given: an answer
     * that says the sources do not answer the question is a refusal, and it
     * is recorded as the last answer rather than as a failure. A refusal is
     * a true answer to the question the operator asked.
     */
    fun askNotebook() = launchNotebookTask {
        val notebooks = _state.value.notebooks
        val notebook = notebooks.openNotebook
        if (notebook == null) {
            notebookFailure("No notebook is open, so there is nothing to ask.")
            return@launchNotebookTask
        }
        val question = notebooks.question.trim()
        if (question.isEmpty()) {
            notebookFailure("A question is needed before the notebook can answer.")
            return@launchNotebookTask
        }
        val answer = backend.askNotebook(notebook.notebookId, question)
        updateState { it.copy(notebooks = it.notebooks.copy(answer = answer)) }
    }

    // --- The inspection -----------------------------------------------------

    /** Choose the claim the operator is inspecting. */
    fun selectClaim(claimId: String?) {
        _state.update { it.copy(selectedClaimId = claimId) }
    }

    /** Choose the passage the operator is inspecting. */
    fun selectChunk(chunkId: String?) {
        _state.update { it.copy(selectedChunkId = chunkId) }
    }

    /** Dismiss the last failure. */
    fun clearError() {
        _state.update { it.copy(error = null) }
    }

    // --- The stream ---------------------------------------------------------

    /**
     * Follow the event stream of one run.
     *
     * The stream is work in its own right: it is counted as one, so the
     * window stays busy and the button that starts a run stays out of reach
     * until the run ends rather than until the request that began it returns.
     */
    private fun followStream(owner: Connection, docId: String, token: Int) {
        synchronized(connectionLock) {
            if (connection !== owner) return
            streamJob?.cancel()
            owner.activeTasks++
            streamJob = scope.launch(owner.job) {
                try {
                    owner.events.events(docId).collect { event ->
                        owner.updateState { RunReducer.reduce(it, event) }
                    }
                    owner.updateState {
                        if (it.docId == docId && it.status !in setOf("completed", "failed")) {
                            it.copy(error = "The event stream ended before the run completed.")
                        } else it
                    }
                } catch (cause: CancellationException) {
                    throw cause
                } catch (cause: BackendError) {
                    owner.updateState { if (it.docId == docId) it.copy(error = cause.message) else it }
                } catch (cause: LMeditorError) {
                    owner.updateState { if (it.docId == docId) it.copy(error = cause.message) else it }
                } finally {
                    synchronized(connectionLock) {
                        if (owner.runToken == token) owner.runInFlight = false
                        owner.activeTasks--
                        owner.updateState { it.copy(busy = owner.activeTasks > 0) }
                    }
                }
            }
        }
    }

    /** Stop following the stream. */
    private fun stopStream() {
        streamJob?.cancel()
        streamJob = null
    }

    // --- The plumbing -------------------------------------------------------

    /**
     * Run one request, and record what it did.
     *
     * A failure is not swallowed: it is recorded in the state, where the
     * operator can see it, and it is not turned into an empty result that
     * would look like success.
     */
    private fun launchTask(block: suspend Connection.() -> Unit) {
        val owner = synchronized(connectionLock) { connection }
        scope.launch(owner.job) {
            synchronized(connectionLock) {
                owner.activeTasks++
                owner.updateState { it.copy(busy = true, error = null) }
            }
            try {
                owner.block()
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                owner.updateState { it.copy(error = cause.message) }
            } catch (cause: LMeditorError) {
                owner.updateState { it.copy(error = cause.message) }
            } catch (cause: IOException) {
                owner.updateState { it.copy(error = "Could not write the exported document: ${cause.message}") }
            } finally {
                synchronized(connectionLock) {
                    owner.activeTasks--
                    owner.updateState { it.copy(busy = owner.activeTasks > 0) }
                }
            }
        }
    }

    /**
     * Run one notebook request, and record what it did in the notebook
     * workspace.
     *
     * A failure is not swallowed: it is recorded in the workspace's own
     * error, where the operator sees it beside the notebooks, and it is
     * raised to the launcher as well, so the window's status strip says what
     * went wrong. The workspace's busy mark is cleared either way, so a
     * failed request cannot leave the notebooks looking as though they were
     * still being read.
     */
    private fun launchNotebookTask(block: suspend Connection.() -> Unit) {
        launchTask {
            updateState { it.copy(notebooks = it.notebooks.copy(busy = true, error = null)) }
            try {
                block()
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                updateState { it.copy(notebooks = it.notebooks.copy(error = cause.message)) }
                throw cause
            } catch (cause: LMeditorError) {
                updateState { it.copy(notebooks = it.notebooks.copy(error = cause.message)) }
                throw cause
            } finally {
                updateState { it.copy(notebooks = it.notebooks.copy(busy = false)) }
            }
        }
    }
}
