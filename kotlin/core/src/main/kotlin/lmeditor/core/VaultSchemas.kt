package lmeditor.core

/**
 * The schemas of a vault: the notes of a folder of markdown files, and what
 * the client needs to navigate them.
 *
 * A vault is not a private format. A note is a markdown file on the
 * operator's disk, a wiki-link is a wiki-link, and a tag is a tag -- so the
 * same folder opens in Obsidian, in an editor, and in a grep. These schemas
 * describe that folder; they do not replace it.
 *
 * The models live here, in the core tier, because both ends of the contract
 * need them: the tier writes them and the client reads them. A shape that was
 * written twice would drift, and a shape that drifted would be a lie the
 * client renders.
 */

/**
 * One note of a vault: its text, and everything that can be read off it.
 *
 * A note's title is its first `# heading`, or its file name when it has none.
 * Its tags are the `#tag` tokens of its body and the `tags:` list of its
 * frontmatter. Its links are the wiki-links it holds; its backlinks are the
 * notes that hold a link to it.
 */
@kotlin.ExposedCopyVisibility
data class VaultNote private constructor(
    /** The note's path inside the vault, without the `.md`, with `/` separators. */
    val noteId: String,

    /** The note's title. */
    val title: String = "",

    /** The note's path inside the vault, with the `.md`. */
    val path: String = "",

    /** The note's text, exactly as the file holds it. */
    val content: String = "",

    /** The tags the note carries, lower-cased and sorted. */
    val tags: List<String> = emptyList(),

    /** The wiki-links the note holds, as they are written. */
    val links: List<String> = emptyList(),

    /** The notes that hold a link to this one. */
    val backlinks: List<String> = emptyList(),

    /** When the file was created, in ISO-8601. */
    val createdAt: String = "",

    /** When the file was last changed, in ISO-8601. */
    val modifiedAt: String = "",

    /** The words of the note's body. */
    val wordCount: Int = 0,
) {
    init {
        if (noteId.isEmpty()) {
            throw ValidationError("note_id must not be empty", field = "note_id")
        }
        if (wordCount < 0) {
            throw ValidationError("word_count must not be negative", field = "word_count")
        }
    }

    companion object {
        /**
         * Construct a [VaultNote], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace before
         * the constraints are checked, so a field that is nothing but whitespace
         * is empty.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            noteId: String,
            title: String = "",
            path: String = "",
            content: String = "",
            tags: List<String> = emptyList(),
            links: List<String> = emptyList(),
            backlinks: List<String> = emptyList(),
            createdAt: String = "",
            modifiedAt: String = "",
            wordCount: Int = 0,
        ): VaultNote = VaultNote(
            noteId = noteId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            path = path.stripModelWhitespace(),
            content = content,
            tags = tags.map { it.stripModelWhitespace() },
            links = links.map { it.stripModelWhitespace() },
            backlinks = backlinks.map { it.stripModelWhitespace() },
            createdAt = createdAt.stripModelWhitespace(),
            modifiedAt = modifiedAt.stripModelWhitespace(),
            wordCount = wordCount,
        )
    }
}

/**
 * What the client needs to list a note without reading it.
 *
 * The summary is the note's index entry: the same thing a book of a library
 * holds for its sections. It carries no text, so a vault of any size can be
 * listed without reading a byte of it.
 */
@kotlin.ExposedCopyVisibility
data class VaultNoteSummary private constructor(
    /** The note's path inside the vault, without the `.md`. */
    val noteId: String,

    /** The note's title. */
    val title: String = "",

    /** The note's path inside the vault, with the `.md`. */
    val path: String = "",

    /** The tags the note carries. */
    val tags: List<String> = emptyList(),

    /** The words of the note's body. */
    val wordCount: Int = 0,

    /** When the file was last changed, in ISO-8601. */
    val modifiedAt: String = "",
) {
    init {
        if (noteId.isEmpty()) {
            throw ValidationError("note_id must not be empty", field = "note_id")
        }
        if (wordCount < 0) {
            throw ValidationError("word_count must not be negative", field = "word_count")
        }
    }

    companion object {
        /** Construct a [VaultNoteSummary], the way Pydantic constructs the Python model. */
        operator fun invoke(
            noteId: String,
            title: String = "",
            path: String = "",
            tags: List<String> = emptyList(),
            wordCount: Int = 0,
            modifiedAt: String = "",
        ): VaultNoteSummary = VaultNoteSummary(
            noteId = noteId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            path = path.stripModelWhitespace(),
            tags = tags.map { it.stripModelWhitespace() },
            wordCount = wordCount,
            modifiedAt = modifiedAt.stripModelWhitespace(),
        )
    }
}

/**
 * One hit of a search.
 *
 * The score is the number the rule gave the note; the terms are the ones the
 * note matched on. Both are reported, so the operator can see *why* a note
 * came back and not only that it did.
 */
@kotlin.ExposedCopyVisibility
data class VaultSearchHit private constructor(
    /** The note's path inside the vault, without the `.md`. */
    val noteId: String,

    /** The note's title. */
    val title: String = "",

    /** The note's path inside the vault, with the `.md`. */
    val path: String = "",

    /** The score the rule gave the note. */
    val score: Int = 0,

    /** The terms the note matched on. */
    val matchedTerms: List<String> = emptyList(),
) {
    init {
        if (noteId.isEmpty()) {
            throw ValidationError("note_id must not be empty", field = "note_id")
        }
        if (score < 0) {
            throw ValidationError("score must not be negative", field = "score")
        }
    }

    companion object {
        /** Construct a [VaultSearchHit], the way Pydantic constructs the Python model. */
        operator fun invoke(
            noteId: String,
            title: String = "",
            path: String = "",
            score: Int = 0,
            matchedTerms: List<String> = emptyList(),
        ): VaultSearchHit = VaultSearchHit(
            noteId = noteId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            path = path.stripModelWhitespace(),
            score = score,
            matchedTerms = matchedTerms.map { it.stripModelWhitespace() },
        )
    }
}

/**
 * A tag of a vault, and how many notes carry it.
 */
@kotlin.ExposedCopyVisibility
data class VaultTag private constructor(
    /** The tag, lower-cased. */
    val tag: String,

    /** The notes that carry it. */
    val count: Int = 0,
) {
    init {
        if (tag.isEmpty()) {
            throw ValidationError("tag must not be empty", field = "tag")
        }
        if (count < 0) {
            throw ValidationError("count must not be negative", field = "count")
        }
    }

    companion object {
        /** Construct a [VaultTag], the way Pydantic constructs the Python model. */
        operator fun invoke(tag: String, count: Int = 0): VaultTag = VaultTag(
            tag = tag.stripModelWhitespace(),
            count = count,
        )
    }
}

/**
 * The vault the server has open.
 *
 * The root is the folder itself — the operator's own Obsidian vault, on their
 * own disk — and the name is that folder's name. The note count is how many
 * notes it holds, so the window can say what it opened without reading a byte
 * of it.
 */
@kotlin.ExposedCopyVisibility
data class VaultSummary private constructor(
    /** The absolute path of the folder the vault is. */
    val root: String,

    /** The folder's own name. */
    val name: String = "",

    /** The notes the folder holds. */
    val noteCount: Int = 0,
) {
    init {
        if (root.isEmpty()) {
            throw ValidationError("root must not be empty", field = "root")
        }
        if (noteCount < 0) {
            throw ValidationError("note_count must not be negative", field = "note_count")
        }
    }

    companion object {
        /** Construct a [VaultSummary], the way Pydantic constructs the Python model. */
        operator fun invoke(
            root: String,
            name: String = "",
            noteCount: Int = 0,
        ): VaultSummary = VaultSummary(
            root = root.stripModelWhitespace(),
            name = name.stripModelWhitespace(),
            noteCount = noteCount,
        )
    }
}

/**
 * What a rename of a note did.
 *
 * The links it rewrote are counted, because the operator is told how much of
 * their vault moved with the note.
 */
@kotlin.ExposedCopyVisibility
data class VaultRenameResult private constructor(
    /** The note's old id. */
    val fromId: String,

    /** The note's new id. */
    val toId: String,

    /** The links in other notes that were rewritten to point at the new id. */
    val updatedLinks: Int = 0,
) {
    init {
        if (fromId.isEmpty()) {
            throw ValidationError("from_id must not be empty", field = "from_id")
        }
        if (toId.isEmpty()) {
            throw ValidationError("to_id must not be empty", field = "to_id")
        }
        if (updatedLinks < 0) {
            throw ValidationError("updated_links must not be negative", field = "updated_links")
        }
    }

    companion object {
        /** Construct a [VaultRenameResult], the way Pydantic constructs the Python model. */
        operator fun invoke(
            fromId: String,
            toId: String,
            updatedLinks: Int = 0,
        ): VaultRenameResult = VaultRenameResult(
            fromId = fromId.stripModelWhitespace(),
            toId = toId.stripModelWhitespace(),
            updatedLinks = updatedLinks,
        )
    }
}

/** The wire form of a [VaultRenameResult]. */
fun VaultRenameResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "from_id" to fromId,
    "to_id" to toId,
    "updated_links" to updatedLinks,
)

/** The wire form of a [VaultSummary]. */
fun VaultSummary.toWireMap(): Map<String, Any?> = linkedMapOf(
    "root" to root,
    "name" to name,
    "note_count" to noteCount,
)

/** The wire form of a [VaultNote]. */
fun VaultNote.toWireMap(): Map<String, Any?> = linkedMapOf(
    "note_id" to noteId,
    "title" to title,
    "path" to path,
    "content" to content,
    "tags" to tags,
    "links" to links,
    "backlinks" to backlinks,
    "created_at" to createdAt,
    "modified_at" to modifiedAt,
    "word_count" to wordCount,
)

/** The wire form of a [VaultNoteSummary]. */
fun VaultNoteSummary.toWireMap(): Map<String, Any?> = linkedMapOf(
    "note_id" to noteId,
    "title" to title,
    "path" to path,
    "tags" to tags,
    "word_count" to wordCount,
    "modified_at" to modifiedAt,
)

/** The wire form of a [VaultSearchHit]. */
fun VaultSearchHit.toWireMap(): Map<String, Any?> = linkedMapOf(
    "note_id" to noteId,
    "title" to title,
    "path" to path,
    "score" to score,
    "matched_terms" to matchedTerms,
)

/** The wire form of a [VaultTag]. */
fun VaultTag.toWireMap(): Map<String, Any?> = linkedMapOf(
    "tag" to tag,
    "count" to count,
)
