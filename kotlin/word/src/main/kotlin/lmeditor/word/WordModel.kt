package lmeditor.word

import lmeditor.core.ValidationError
import java.time.OffsetDateTime

/** The structural kinds a Word document is composed of. */
enum class WordBlockKind(val wire: String) {
    TITLE("title"),
    HEADING("heading"),
    PARAGRAPH("paragraph"),
    LIST_ITEM("list_item"),
    TABLE("table"),
    QUOTE("quote"),
    CODE("code"),
    PAGE_BREAK("page_break"),
    ;

    companion object {
        /** Return the kind [value] names, or throw a [ValidationError]. */
        fun fromWire(value: String): WordBlockKind =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown word block kind '$value'",
                    field = "kind",
                    value = value,
                )
    }
}

/** The marker style of a list item. */
enum class ListMarker(val wire: String) {
    BULLET("bullet"),
    NUMBER("number"),
    ;

    companion object {
        /** Return the marker [value] names, or throw a [ValidationError]. */
        fun fromWire(value: String): ListMarker =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown list marker '$value'",
                    field = "list_marker",
                    value = value,
                )
    }
}

/**
 * The kinds of tracked revision Word records.
 *
 * [INSERTION] and [DELETION] are the two the reader must never lose;
 * [MOVE_FROM]/[MOVE_TO] are the move pair, and [FORMAT_CHANGE] marks a run whose
 * *properties* changed rather than whose text did.
 */
enum class WordRevisionKind(val wire: String) {
    INSERTION("insertion"),
    DELETION("deletion"),
    MOVE_FROM("move_from"),
    MOVE_TO("move_to"),
    FORMAT_CHANGE("format_change"),
    ;

    companion object {
        /** Return the revision kind [value] names, or throw a [ValidationError]. */
        fun fromWire(value: String): WordRevisionKind =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown word revision kind '$value'",
                    field = "kind",
                    value = value,
                )
    }
}

/** Revision kinds whose text is *not* part of the document's final text. */
val DELETION_REVISION_KINDS: Set<WordRevisionKind> =
    setOf(WordRevisionKind.DELETION, WordRevisionKind.MOVE_FROM)

/**
 * One tracked revision, as Word records it.
 *
 * A revision is identified by [revisionId]: every run that belongs to the same
 * `w:ins`/`w:del` element carries the same id, which is how the writer can put
 * the runs back into a single revision element.
 */
data class WordRevision(
    val kind: WordRevisionKind,
    val author: String = "",
    val date: OffsetDateTime? = null,
    val revisionId: String = "",
)

/** A run of inline text carrying its own formatting. */
data class WordRun(
    val text: String = "",
    val bold: Boolean = false,
    val italic: Boolean = false,
    val underline: Boolean = false,
    val superscript: Boolean = false,
    val subscript: Boolean = false,
    val strike: Boolean = false,
    val font: String? = null,
    /** Font size in half-points, the unit Word uses. */
    val size: Int? = null,
    /** Font colour as a six-digit RRGGBB hex string. */
    val color: String? = null,
    /** Highlight colour, spelled the way Word spells it. */
    val highlight: String? = null,
    /** Absolute hyperlink target for the run. */
    val hyperlink: String? = null,
    /** Id of the [WordFootnote] this run references. */
    val footnoteRef: String? = null,
    /** Id of the [WordEndnote] this run references. */
    val endnoteRef: String? = null,
    /** Tracked revision this run belongs to, when it has one. */
    val revision: WordRevision? = null,
    /** Ids of the comments whose range covers this run. */
    val commentRefs: List<String> = emptyList(),
    /** Id of the [WordMedia] this run embeds. */
    val imageRef: String? = null,
    /** Raw OMML markup of an equation, preserved verbatim for re-emission. */
    val equationXml: String? = null,
    /** Package part a non-image body element anchors. */
    val partRef: String? = null,
    /**
     * Size the anchored body element is drawn at, in EMUs, as (width, height).
     * Word scales a chart or a picture by rewriting the extent of the drawing, so
     * without it the element comes back at a default size instead of the size it
     * was written at.
     */
    val anchorExtent: Pair<Int, Int>? = null,
    /**
     * Raw XML of the tracked formatting revision (`w:rPrChange`) that preceded
     * these properties, preserved verbatim for re-emission.
     */
    val formatChangeXml: String? = null,
) {
    init {
        if (size != null && size < 1) {
            throw ValidationError("size must be at least 1 half-point", field = "size", value = size)
        }
    }

    /**
     * Report whether the run's text was deleted by a tracked revision.
     *
     * A deleted run is part of the model -- its text is recoverable and it
     * survives a round trip -- but it is not part of the document's *final* text,
     * so it never reaches the plain-text projection.
     */
    val isDeleted: Boolean
        get() = revision != null && revision.kind in DELETION_REVISION_KINDS
}

/** A comment anchored on a range of the document. */
data class WordComment(
    /** Identifier of the comment. */
    val commentId: String,
    val author: String = "",
    val initials: String = "",
    val date: OffsetDateTime? = null,
    /** Normalised text of the comment. */
    val text: String = "",
    /** Inline runs, in order. */
    val runs: List<WordRun> = emptyList(),
    /** Id of the block the comment is anchored on, when it is anchored on one. */
    val anchorBlockId: String? = null,
    /** Text of the range the comment covers. */
    val anchorText: String = "",
) {
    init {
        if (commentId.isEmpty()) {
            throw ValidationError("comment_id must not be empty", field = "comment_id")
        }
    }
}

/** A footnote, referenced from the body by [footnoteId]. */
data class WordFootnote(
    /** Identifier referenced by runs. */
    val footnoteId: String,
    /** Normalised text of the footnote. */
    val text: String = "",
    /** Inline runs, in order. */
    val runs: List<WordRun> = emptyList(),
) {
    init {
        if (footnoteId.isEmpty()) {
            throw ValidationError("footnote_id must not be empty", field = "footnote_id")
        }
    }
}

/** An endnote, referenced from the body by [endnoteId]. */
data class WordEndnote(
    /** Identifier referenced by runs. */
    val endnoteId: String,
    /** Normalised text of the endnote. */
    val text: String = "",
    /** Inline runs, in order. */
    val runs: List<WordRun> = emptyList(),
) {
    init {
        if (endnoteId.isEmpty()) {
            throw ValidationError("endnote_id must not be empty", field = "endnote_id")
        }
    }
}

/**
 * One header or footer reference of a section.
 *
 * The reference *type* is what makes a header findable: a document whose
 * `header1.xml` holds the even-page header must not have its headers matched by
 * part order, because that swaps them.
 */
data class WordHeaderFooterRef(
    /** Reference type: `default`, `even` or `first`. */
    val refType: String = "default",
    /** Package part the reference points at. */
    val partName: String = "",
    /** Text of the header or footer. */
    val text: String = "",
)

/**
 * The properties of one section of the document.
 *
 * The values are the ones Word itself uses: page dimensions and margins are twips
 * (1/1440 inch), and the header/footer references are carried by type so that they
 * survive a round trip.
 */
data class WordSection(
    /** Page width in twips. */
    val pageWidth: Int = 12240,
    /** Page height in twips. */
    val pageHeight: Int = 15840,
    val orientation: String = "portrait",
    /** Top margin in twips. */
    val marginTop: Int = 1440,
    /** Right margin in twips. */
    val marginRight: Int = 1440,
    /** Bottom margin in twips. */
    val marginBottom: Int = 1440,
    /** Left margin in twips. */
    val marginLeft: Int = 1440,
    val headerRefs: List<WordHeaderFooterRef> = emptyList(),
    val footerRefs: List<WordHeaderFooterRef> = emptyList(),
    /** Id of the block the section ends at, when it ends at one. */
    val endBlockId: String? = null,
) {
    init {
        if (pageWidth < 1) {
            throw ValidationError("page_width must be at least 1 twip", field = "page_width", value = pageWidth)
        }
        if (pageHeight < 1) {
            throw ValidationError("page_height must be at least 1 twip", field = "page_height", value = pageHeight)
        }
        if (marginTop < 0 || marginRight < 0 || marginBottom < 0 || marginLeft < 0) {
            throw ValidationError("section margins must not be negative", field = "margins")
        }
    }
}

/** A style definition carried through from the source document. */
data class WordStyle(
    /** Style id, as Word spells it. */
    val styleId: String,
    /** Style name. */
    val name: String = "",
    /** Id of the style this one is based on. */
    val basedOn: String = "",
    /** Kind of style. */
    val styleType: String = "paragraph",
    /** Numbering id the style carries. */
    val numId: String? = null,
    /** Numbering level the style carries. */
    val numLevel: Int = 0,
) {
    init {
        if (styleId.isEmpty()) {
            throw ValidationError("style_id must not be empty", field = "style_id")
        }
        if (numLevel < 0) {
            throw ValidationError("num_level must not be negative", field = "num_level", value = numLevel)
        }
    }
}

/** An embedded media part, referenced from the body by [mediaId]. */
data class WordMedia(
    /** Identifier referenced by runs. */
    val mediaId: String,
    /** Package part name of the media. */
    val partName: String,
    /** Content type of the media part. */
    val contentType: String = "",
    /** Raw bytes of the media part. */
    val data: ByteArray = ByteArray(0),
    /** Relationship the part was reached by. */
    val relationshipId: String = "",
) {
    init {
        if (mediaId.isEmpty()) {
            throw ValidationError("media_id must not be empty", field = "media_id")
        }
        if (partName.isEmpty()) {
            throw ValidationError("part_name must not be empty", field = "part_name")
        }
    }

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is WordMedia) return false
        return mediaId == other.mediaId &&
            partName == other.partName &&
            contentType == other.contentType &&
            data.contentEquals(other.data) &&
            relationshipId == other.relationshipId
    }

    override fun hashCode(): Int {
        var result = mediaId.hashCode()
        result = 31 * result + partName.hashCode()
        result = 31 * result + contentType.hashCode()
        result = 31 * result + data.contentHashCode()
        result = 31 * result + relationshipId.hashCode()
        return result
    }
}

/**
 * One relationship of a package part.
 *
 * A relationship is a *pointer*: it names a part of the package or a target
 * outside it. The pointer is what makes a part travel, so it is carried with the
 * part rather than rebuilt at write time.
 */
data class WordPartRelationship(
    /** Id the relationship is known by inside the owning part. */
    val relationshipId: String,
    /** Type of the relationship. */
    val relationshipType: String,
    /** Package part name of the target, or its URL when it is external. */
    val target: String,
    /** Target lies outside the package. */
    val isExternal: Boolean = false,
) {
    init {
        if (relationshipId.isEmpty()) {
            throw ValidationError("relationship_id must not be empty", field = "relationship_id")
        }
        if (relationshipType.isEmpty()) {
            throw ValidationError("relationship_type must not be empty", field = "relationship_type")
        }
        if (target.isEmpty()) {
            throw ValidationError("target must not be empty", field = "target")
        }
    }
}

/**
 * A package part the reader carried through without modelling its content.
 *
 * The bytes are kept so the part is not lost by being read: the writer can put it
 * back into the package under the same name. A chart, an embedded object, a
 * glossary document and every other part the model does not represent travel this
 * way, so reading a document never costs it a part.
 *
 * [relationshipId] and [relationshipType] say how the part was reached from the
 * body: the writer re-creates the relationship under the same id and points the
 * body reference that anchored the part at it again. A part the document did *not*
 * reach has neither: it is reached from the relationship of the part that owns it,
 * which is what [relationships] records, or from the package root, which is what
 * [packageRelationship] records.
 */
data class WordPart(
    /** Package part name, as the package names it. */
    val partName: String,
    /** Content type of the part. */
    val contentType: String = "",
    /** Raw bytes of the part. */
    val data: ByteArray = ByteArray(0),
    /** Relationship the part was reached by. */
    val relationshipId: String = "",
    /** Type of the relationship the part was reached by. */
    val relationshipType: String = "",
    /** Relationships the part holds to other parts. */
    val relationships: List<WordPartRelationship> = emptyList(),
    /** Relationship the package root holds to this part. */
    val packageRelationship: WordPartRelationship? = null,
) {
    init {
        if (partName.isEmpty()) {
            throw ValidationError("part_name must not be empty", field = "part_name")
        }
    }

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is WordPart) return false
        return partName == other.partName &&
            contentType == other.contentType &&
            data.contentEquals(other.data) &&
            relationshipId == other.relationshipId &&
            relationshipType == other.relationshipType &&
            relationships == other.relationships &&
            packageRelationship == other.packageRelationship
    }

    override fun hashCode(): Int {
        var result = partName.hashCode()
        result = 31 * result + contentType.hashCode()
        result = 31 * result + data.contentHashCode()
        result = 31 * result + relationshipId.hashCode()
        result = 31 * result + relationshipType.hashCode()
        result = 31 * result + relationships.hashCode()
        result = 31 * result + (packageRelationship?.hashCode() ?: 0)
        return result
    }
}

/**
 * One cell of a Word table.
 *
 * A cell carries its own content: the runs of its paragraphs, and the tables
 * nested inside it. The flat [text] is the fallback for a cell with no runs.
 */
data class WordTableCell(
    /** Normalised text of the cell. */
    val text: String = "",
    /** Number of rows the cell spans. */
    val rowSpan: Int = 1,
    /** Number of grid columns the cell spans. */
    val colSpan: Int = 1,
    /** Cell belongs to a header row. */
    val isHeader: Boolean = false,
    /** Cell continues a vertical merge started above it. */
    val isMergeContinuation: Boolean = false,
    /** Width of the cell in twips, when the document declares one. */
    val width: Int? = null,
    /** Inline runs of the cell. */
    val runs: List<WordRun> = emptyList(),
    /** Tables nested inside the cell. */
    val tables: List<WordTable> = emptyList(),
) {
    init {
        if (rowSpan < 1) {
            throw ValidationError("row_span must be at least 1", field = "row_span", value = rowSpan)
        }
        if (colSpan < 1) {
            throw ValidationError("col_span must be at least 1", field = "col_span", value = colSpan)
        }
        if (width != null && width < 1) {
            throw ValidationError("width must be at least 1 twip", field = "width", value = width)
        }
        // A continuation cell cannot span rows and carries no text.
        //
        // The text rule is not cosmetic: a merged cell's content lives in the cell
        // that *starts* the merge, and Word ignores whatever is stored in a
        // continuation cell. Accepting text here would let the model carry content
        // that no renderer can display -- silently lost content, which is the
        // defect this domain exists to eliminate.
        if (isMergeContinuation) {
            if (rowSpan != 1) {
                throw ValidationError(
                    "a merge continuation cell spans a single row, got row_span=$rowSpan",
                    field = "row_span",
                    value = rowSpan,
                )
            }
            if (text.isNotEmpty()) {
                throw ValidationError(
                    "a merge continuation cell carries no text of its own; the text belongs to the cell that starts the merge",
                    field = "text",
                    value = text,
                )
            }
        }
    }
}

/** One row of a Word table. */
data class WordTableRow(
    /** Cells left to right. */
    val cells: List<WordTableCell> = emptyList(),
)

/** A Word table: a grid of rows, each row a list of cells. */
data class WordTable(
    /** Rows top to bottom. */
    val rows: List<WordTableRow> = emptyList(),
    /** Number of leading rows that repeat as a header row on every page. */
    val headerRows: Int = 0,
    /** Width of every grid column, in twips. */
    val columnWidths: List<Int> = emptyList(),
) {
    init {
        if (headerRows < 0) {
            throw ValidationError("header_rows must not be negative", field = "header_rows", value = headerRows)
        }
    }
}

/**
 * One block of the document: a paragraph, a heading, a list item, a table.
 *
 * A block carries both its normalised plain [text] and the [runs] it was built
 * from. The two must agree, and the model refuses a block whose text is not the
 * normalised concatenation of its live runs -- content that no renderer can
 * display must never reach the model.
 */
data class WordBlock(
    /** Stable identifier within the document. */
    val blockId: String,
    /** Structural kind of the block. */
    val kind: WordBlockKind,
    /** Normalised plain text of the block. */
    val text: String = "",
    /** Nesting depth: the heading level, or the list level. */
    val level: Int = 0,
    /** Marker style of a list item. */
    val listMarker: ListMarker? = null,
    /** Continuity group a numbered list item belongs to. */
    val listGroup: String? = null,
    /** Numbering format the source document used, when it used one. */
    val numberFormat: String? = null,
    /** Numbering level text the source document used, when it used one. */
    val numberText: String? = null,
    /** Source style name, when known. */
    val style: String? = null,
    /** Inline runs, in order. */
    val runs: List<WordRun> = emptyList(),
    /** Table payload for TABLE blocks. */
    val table: WordTable? = null,
) {
    init {
        if (blockId.isEmpty()) {
            throw ValidationError("block_id must not be empty", field = "block_id")
        }
        if (level < 0) {
            throw ValidationError("level must not be negative", field = "level", value = level)
        }
        when (kind) {
            WordBlockKind.TABLE -> {
                if (table == null) {
                    throw ValidationError("a TABLE block requires a table payload", field = "table")
                }
                if (text.isNotEmpty() || runs.isNotEmpty()) {
                    throw ValidationError("a TABLE block carries no inline text or runs", field = "runs")
                }
            }

            else -> {
                if (table != null) {
                    throw ValidationError(
                        "a ${kind.wire} block must not carry a table payload",
                        field = "table",
                    )
                }
            }
        }
        when (kind) {
            WordBlockKind.LIST_ITEM -> {
                if (listMarker == null) {
                    throw ValidationError("a LIST_ITEM block requires a list marker", field = "list_marker")
                }
                if (level > 8) {
                    throw ValidationError(
                        "a LIST_ITEM block cannot nest deeper than 8, got $level",
                        field = "level",
                        value = level,
                    )
                }
            }

            else -> {
                if (listMarker != null) {
                    throw ValidationError(
                        "a ${kind.wire} block must not carry a list marker",
                        field = "list_marker",
                    )
                }
                if (kind != WordBlockKind.PAGE_BREAK && kind != WordBlockKind.HEADING && level != 0) {
                    throw ValidationError(
                        "a ${kind.wire} block must have level 0, got $level",
                        field = "level",
                        value = level,
                    )
                }
            }
        }
        if (kind == WordBlockKind.HEADING && level !in 1..9) {
            throw ValidationError(
                "a HEADING block requires a level between 1 and 9, got $level",
                field = "level",
                value = level,
            )
        }
        if (kind == WordBlockKind.PAGE_BREAK && text.isNotEmpty()) {
            throw ValidationError("a PAGE_BREAK block carries no text", field = "text")
        }
        if (runs.isNotEmpty()) {
            val expected = WordText.normalizeWhitespace(
                runs.filterNot { it.isDeleted }.joinToString("") { it.text },
            )
            if (text != expected) {
                throw ValidationError(
                    "block text must equal the normalised concatenation of its live runs " +
                        "('$text' != '$expected')",
                    field = "text",
                    value = text,
                )
            }
        }
    }

    /**
     * Render the block as plain text for ingestion and round-trip comparison.
     *
     * Implements the contract's T5-T9: empty blocks render as nothing, tables
     * render exactly one pipe-separated line per row -- blank rows included --
     * and list markers are omitted because they are structure rather than content.
     */
    fun renderText(): String {
        if (kind == WordBlockKind.TABLE) {
            val payload = table ?: return ""
            return payload.rows.joinToString("\n") { row ->
                row.cells.joinToString(" | ") { it.text }
            }
        }
        if (kind == WordBlockKind.PAGE_BREAK) return ""
        return text
    }
}

/** Document-level metadata, mapped from and to `docProps/core.xml`. */
data class WordProperties(
    /** Document title. */
    val title: String = "",
    /** Document author. */
    val author: String = "",
    /** Document subject. */
    val subject: String = "",
    /** Document keywords. */
    val keywords: String = "",
    /** Document comments. */
    val comments: String = "",
    /** Creation timestamp. */
    val created: OffsetDateTime? = null,
    /** Last modification timestamp. */
    val modified: OffsetDateTime? = null,
    /** Revision number. */
    val revision: Int = 1,
) {
    init {
        if (revision < 1) {
            throw ValidationError("revision must be at least 1", field = "revision", value = revision)
        }
    }
}

/**
 * A whole Word document, as the reader recovers it and the writer renders it.
 *
 * The model is deliberately *flat* for the body: every block -- a heading, a
 * paragraph, a list item, a table -- sits in one ordered list, so that the
 * document's reading order is the model's order and a round trip cannot reorder
 * it.
 */
data class WordDocument(
    /** Blocks in document order. */
    val blocks: List<WordBlock> = emptyList(),
    /** Core properties. */
    val properties: WordProperties = WordProperties(),
    /** Footnotes by id. */
    val footnotes: List<WordFootnote> = emptyList(),
    /** Endnotes by id. */
    val endnotes: List<WordEndnote> = emptyList(),
    /** Comments by id. */
    val comments: List<WordComment> = emptyList(),
    /** Header texts, in part order. */
    val headers: List<String> = emptyList(),
    /** Footer texts, in part order. */
    val footers: List<String> = emptyList(),
    /** Sections, in document order. */
    val sections: List<WordSection> = emptyList(),
    /** Style definitions. */
    val styles: List<WordStyle> = emptyList(),
    /** Embedded media parts. */
    val media: List<WordMedia> = emptyList(),
    /** Package parts carried through without modelling their content. */
    val parts: List<WordPart> = emptyList(),
) {
    init {
        val seen = mutableSetOf<String>()
        for (block in blocks) {
            if (!seen.add(block.blockId)) {
                throw ValidationError("duplicate block_id '${block.blockId}'", field = "block_id", value = block.blockId)
            }
        }
        val knownFootnotes = footnotes.map { it.footnoteId }.toSet()
        val knownEndnotes = endnotes.map { it.endnoteId }.toSet()
        val knownComments = comments.map { it.commentId }.toSet()
        for (block in blocks) {
            for (run in block.runs) {
                val footnoteRef = run.footnoteRef
                if (footnoteRef != null && footnoteRef !in knownFootnotes) {
                    throw ValidationError(
                        "run references unknown footnote '$footnoteRef'",
                        field = "footnote_ref",
                        value = footnoteRef,
                    )
                }
                val endnoteRef = run.endnoteRef
                if (endnoteRef != null && endnoteRef !in knownEndnotes) {
                    throw ValidationError(
                        "run references unknown endnote '$endnoteRef'",
                        field = "endnote_ref",
                        value = endnoteRef,
                    )
                }
                for (commentId in run.commentRefs) {
                    if (commentId !in knownComments) {
                        throw ValidationError(
                            "run references unknown comment '$commentId'",
                            field = "comment_refs",
                            value = commentId,
                        )
                    }
                }
            }
        }
    }

    /**
     * Render the whole document as plain text.
     *
     * Implements the contract's T6: surviving blocks are joined with a single
     * blank line. The result is deterministic, which is what makes the round-trip
     * property testable.
     */
    fun plainText(): String =
        blocks.map { it.renderText() }.filter { it.isNotBlank() }.joinToString("\n\n")
}
