package lmeditor.core

/**
 * The schemas of a notebook: a named collection of sources and notes, and the
 * answer a question about it got.
 *
 * A notebook is the unit Open Notebook is built around: the material the
 * operator gathered (the **sources**), the notes they wrote beside it (the
 * **notes**), and the questions they asked of that material (the **ask**). The
 * models live here, in the core tier, because both ends of the contract need
 * them: the tier writes the notebooks and the client reads them, and the
 * backend writes the answers and the client reads those. A shape written
 * twice would drift, and a shape that drifted would be a lie the client
 * renders.
 */

/**
 * One notebook: the sources it holds, the notes it refers to, and when it was
 * last changed.
 *
 * A notebook *points at* its sources and notes; it does not own them. Removing
 * a source from a notebook leaves the source where it is, because the source
 * is the operator's and the notebook only borrows it.
 */
@kotlin.ExposedCopyVisibility
data class Notebook private constructor(
    /** The notebook's id: a safe path segment, and its directory name. */
    val notebookId: String,

    /** The notebook's title. */
    val title: String = "",

    /** What the notebook is for. */
    val description: String = "",

    /** The ids of the sources the notebook holds. */
    val sourceIds: List<String> = emptyList(),

    /** The ids of the notes the notebook refers to. */
    val noteIds: List<String> = emptyList(),

    /** When the notebook was created, in ISO-8601. */
    val createdAt: String = "",

    /** When the notebook was last changed, in ISO-8601. */
    val modifiedAt: String = "",
) {
    init {
        if (notebookId.isEmpty()) {
            throw ValidationError("notebook_id must not be empty", field = "notebook_id")
        }
    }

    companion object {
        /**
         * Construct a [Notebook], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace before
         * the constraints are checked, so a field that is nothing but whitespace
         * is empty.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            notebookId: String,
            title: String = "",
            description: String = "",
            sourceIds: List<String> = emptyList(),
            noteIds: List<String> = emptyList(),
            createdAt: String = "",
            modifiedAt: String = "",
        ): Notebook = Notebook(
            notebookId = notebookId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            sourceIds = sourceIds.map { it.stripModelWhitespace() },
            noteIds = noteIds.map { it.stripModelWhitespace() },
            createdAt = createdAt.stripModelWhitespace(),
            modifiedAt = modifiedAt.stripModelWhitespace(),
        )
    }
}

/**
 * What the client needs to list a notebook without reading it whole.
 *
 * The summary is the notebook's index entry: its title, what it is for, and how
 * much material it holds. The sources and the notes themselves are not in it,
 * so a list of notebooks never costs a read of a single source.
 */
@kotlin.ExposedCopyVisibility
data class NotebookSummary private constructor(
    /** The notebook's id. */
    val notebookId: String,

    /** The notebook's title. */
    val title: String = "",

    /** What the notebook is for. */
    val description: String = "",

    /** How many sources the notebook holds. */
    val sourceCount: Int = 0,

    /** How many notes the notebook refers to. */
    val noteCount: Int = 0,

    /** When the notebook was last changed, in ISO-8601. */
    val modifiedAt: String = "",
) {
    init {
        if (notebookId.isEmpty()) {
            throw ValidationError("notebook_id must not be empty", field = "notebook_id")
        }
        if (sourceCount < 0) {
            throw ValidationError("source_count must not be negative", field = "source_count")
        }
        if (noteCount < 0) {
            throw ValidationError("note_count must not be negative", field = "note_count")
        }
    }

    companion object {
        /** Construct a [NotebookSummary], the way Pydantic constructs the Python model. */
        operator fun invoke(
            notebookId: String,
            title: String = "",
            description: String = "",
            sourceCount: Int = 0,
            noteCount: Int = 0,
            modifiedAt: String = "",
        ): NotebookSummary = NotebookSummary(
            notebookId = notebookId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            sourceCount = sourceCount,
            noteCount = noteCount,
            modifiedAt = modifiedAt.stripModelWhitespace(),
        )
    }
}

/**
 * One passage an answer leaned on.
 *
 * A citation is a claim about what the machine did, so it names the passage it
 * came from, the source it belongs to, the source's title, and the passage's
 * own words. The quote is the passage's text verbatim, so the operator can
 * read the evidence rather than trust it.
 */
@kotlin.ExposedCopyVisibility
data class NotebookCitation private constructor(
    /** The id of the passage the answer leaned on. */
    val chunkId: String,

    /** The id of the source the passage belongs to. */
    val sourceId: String,

    /** The source's title. */
    val title: String = "",

    /** The passage's own words. */
    val quote: String = "",
) {
    init {
        if (chunkId.isEmpty()) {
            throw ValidationError("chunk_id must not be empty", field = "chunk_id")
        }
        if (sourceId.isEmpty()) {
            throw ValidationError("source_id must not be empty", field = "source_id")
        }
    }

    companion object {
        /** Construct a [NotebookCitation], the way Pydantic constructs the Python model. */
        operator fun invoke(
            chunkId: String,
            sourceId: String,
            title: String = "",
            quote: String = "",
        ): NotebookCitation = NotebookCitation(
            chunkId = chunkId.stripModelWhitespace(),
            sourceId = sourceId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            quote = quote,
        )
    }
}

/**
 * The answer to one question about one notebook.
 *
 * The answer is `answered` only when it leans on at least one passage of the
 * notebook's own sources. An answer that says the sources do not answer the
 * question is a successful ask with `answered` false; an answer that invents
 * one is not an answer at all.
 */
@kotlin.ExposedCopyVisibility
data class NotebookAnswer private constructor(
    /** The answer's text, carrying the inline citation tags it used. */
    val answer: String = "",

    /** Whether the answer leaned on the notebook's own sources. */
    val answered: Boolean = false,

    /** The passages the answer leaned on. */
    val citations: List<NotebookCitation> = emptyList(),
) {
    companion object {
        /** Construct a [NotebookAnswer], the way Pydantic constructs the Python model. */
        operator fun invoke(
            answer: String = "",
            answered: Boolean = false,
            citations: List<NotebookCitation> = emptyList(),
        ): NotebookAnswer = NotebookAnswer(
            answer = answer,
            answered = answered,
            citations = citations,
        )
    }
}

/**
 * The notebooks folder the server holds, and the notebooks in it.
 *
 * The folder is the operator's own directory of notebooks. The client shows
 * where it is, so the operator always knows which folder their work is in.
 */
@kotlin.ExposedCopyVisibility
data class NotebookLibrary private constructor(
    /** The absolute path of the folder the notebooks are kept in. */
    val root: String = "",

    /** The folder's own name. */
    val name: String = "",

    /** How many notebooks the folder holds. */
    val notebookCount: Int = 0,

    /** The notebooks themselves, as summaries. */
    val notebooks: List<NotebookSummary> = emptyList(),
) {
    init {
        if (notebookCount < 0) {
            throw ValidationError("notebook_count must not be negative", field = "notebook_count")
        }
    }

    companion object {
        /** Construct a [NotebookLibrary], the way Pydantic constructs the Python model. */
        operator fun invoke(
            root: String = "",
            name: String = "",
            notebookCount: Int = 0,
            notebooks: List<NotebookSummary> = emptyList(),
        ): NotebookLibrary = NotebookLibrary(
            root = root.stripModelWhitespace(),
            name = name.stripModelWhitespace(),
            notebookCount = notebookCount,
            notebooks = notebooks,
        )
    }
}

/** The wire form of a [NotebookLibrary]. */
fun NotebookLibrary.toWireMap(): Map<String, Any?> = linkedMapOf(
    "root" to root,
    "name" to name,
    "notebook_count" to notebookCount,
    "notebooks" to notebooks.map { it.toWireMap() },
)

/** The wire form of a [Notebook]. */
fun Notebook.toWireMap(): Map<String, Any?> = linkedMapOf(
    "notebook_id" to notebookId,
    "title" to title,
    "description" to description,
    "source_ids" to sourceIds,
    "note_ids" to noteIds,
    "created_at" to createdAt,
    "modified_at" to modifiedAt,
)

/** The wire form of a [NotebookSummary]. */
fun NotebookSummary.toWireMap(): Map<String, Any?> = linkedMapOf(
    "notebook_id" to notebookId,
    "title" to title,
    "description" to description,
    "source_count" to sourceCount,
    "note_count" to noteCount,
    "modified_at" to modifiedAt,
)

/** The wire form of a [NotebookCitation]. */
fun NotebookCitation.toWireMap(): Map<String, Any?> = linkedMapOf(
    "chunk_id" to chunkId,
    "source_id" to sourceId,
    "title" to title,
    "quote" to quote,
)

/** The wire form of a [NotebookAnswer]. */
fun NotebookAnswer.toWireMap(): Map<String, Any?> = linkedMapOf(
    "answer" to answer,
    "answered" to answered,
    "citations" to citations.map { it.toWireMap() },
)
