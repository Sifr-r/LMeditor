package lmeditor.word

import lmeditor.core.ValidationError
import java.time.DateTimeException
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.Base64

/**
 * The wire form of the Word document model.
 *
 * `kotlin/word/WIRE.md` is the contract. The Kotlin model and the Python model
 * declare the same fields with the same rules; these converters are the third
 * thing the two tiers have to agree on -- how one of those fields is spelled
 * when it crosses the NDJSON seam.
 *
 * The rules the converters follow, all of them from the contract:
 *
 * - A key is `snake_case` and is named after the Python Pydantic field.
 * - An enum travels as its `wire` string, never as its ordinal.
 * - `bytes` travel as a padded base64 string.
 * - A timestamp travels as an ISO-8601 string.
 * - A field the model declares optional travels as `null` when it has no value,
 *   and a decoder reads an absent key exactly like an explicit `null`.
 * - A decoder ignores the keys it does not know, so the wire can grow without
 *   breaking an old peer.
 * - A decoder never guesses: a required key that is not there is an error, not
 *   a default. A default would let a typo become a silent difference between
 *   the two tiers, which is exactly the drift this file exists to prevent.
 *
 * The converters are public because the seam in the `:bridge` module uses them,
 * and `internal` would stop at the module boundary.
 */

/** The wire form of a [WordDocument]. */
fun wordDocumentToWire(doc: WordDocument): Map<String, Any?> = linkedMapOf(
    "blocks" to doc.blocks.map { wordBlockToWire(it) },
    "properties" to wordPropertiesToWire(doc.properties),
    "footnotes" to doc.footnotes.map { wordFootnoteToWire(it) },
    "endnotes" to doc.endnotes.map { wordEndnoteToWire(it) },
    "comments" to doc.comments.map { wordCommentToWire(it) },
    "headers" to doc.headers,
    "footers" to doc.footers,
    "sections" to doc.sections.map { wordSectionToWire(it) },
    "styles" to doc.styles.map { wordStyleToWire(it) },
    "media" to doc.media.map { wordMediaToWire(it) },
    "parts" to doc.parts.map { wordPartToWire(it) },
)

/** The wire form of a [WordBlock]. */
fun wordBlockToWire(block: WordBlock): Map<String, Any?> = linkedMapOf(
    "block_id" to block.blockId,
    "kind" to block.kind.wire,
    "text" to block.text,
    "level" to block.level,
    "list_marker" to block.listMarker?.wire,
    "list_group" to block.listGroup,
    "number_format" to block.numberFormat,
    "number_text" to block.numberText,
    "style" to block.style,
    "runs" to block.runs.map { wordRunToWire(it) },
    "table" to block.table?.let { wordTableToWire(it) },
)

/** The wire form of a [WordRun]. */
fun wordRunToWire(run: WordRun): Map<String, Any?> = linkedMapOf(
    "text" to run.text,
    "bold" to run.bold,
    "italic" to run.italic,
    "underline" to run.underline,
    "superscript" to run.superscript,
    "subscript" to run.subscript,
    "strike" to run.strike,
    "font" to run.font,
    "size" to run.size,
    "color" to run.color,
    "highlight" to run.highlight,
    "hyperlink" to run.hyperlink,
    "footnote_ref" to run.footnoteRef,
    "endnote_ref" to run.endnoteRef,
    "revision" to run.revision?.let { wordRevisionToWire(it) },
    "comment_refs" to run.commentRefs,
    "image_ref" to run.imageRef,
    "equation_xml" to run.equationXml,
    "part_ref" to run.partRef,
    "anchor_extent" to run.anchorExtent?.let { listOf(it.first, it.second) },
    "format_change_xml" to run.formatChangeXml,
)

/** The wire form of a [WordRevision]. */
fun wordRevisionToWire(revision: WordRevision): Map<String, Any?> = linkedMapOf(
    "kind" to revision.kind.wire,
    "author" to revision.author,
    "date" to revision.date?.toString(),
    "revision_id" to revision.revisionId,
)

/** The wire form of a [WordTable]. */
fun wordTableToWire(table: WordTable): Map<String, Any?> = linkedMapOf(
    "rows" to table.rows.map { wordTableRowToWire(it) },
    "header_rows" to table.headerRows,
    "column_widths" to table.columnWidths,
)

/** The wire form of a [WordTableRow]. */
fun wordTableRowToWire(row: WordTableRow): Map<String, Any?> = linkedMapOf(
    "cells" to row.cells.map { wordTableCellToWire(it) },
)

/** The wire form of a [WordTableCell]. */
fun wordTableCellToWire(cell: WordTableCell): Map<String, Any?> = linkedMapOf(
    "text" to cell.text,
    "row_span" to cell.rowSpan,
    "col_span" to cell.colSpan,
    "is_header" to cell.isHeader,
    "is_merge_continuation" to cell.isMergeContinuation,
    "width" to cell.width,
    "runs" to cell.runs.map { wordRunToWire(it) },
    "tables" to cell.tables.map { wordTableToWire(it) },
)

/** The wire form of a [WordFootnote]. */
fun wordFootnoteToWire(footnote: WordFootnote): Map<String, Any?> = linkedMapOf(
    "footnote_id" to footnote.footnoteId,
    "text" to footnote.text,
    "runs" to footnote.runs.map { wordRunToWire(it) },
)

/** The wire form of a [WordEndnote]. */
fun wordEndnoteToWire(endnote: WordEndnote): Map<String, Any?> = linkedMapOf(
    "endnote_id" to endnote.endnoteId,
    "text" to endnote.text,
    "runs" to endnote.runs.map { wordRunToWire(it) },
)

/** The wire form of a [WordComment]. */
fun wordCommentToWire(comment: WordComment): Map<String, Any?> = linkedMapOf(
    "comment_id" to comment.commentId,
    "author" to comment.author,
    "initials" to comment.initials,
    "date" to comment.date?.toString(),
    "text" to comment.text,
    "runs" to comment.runs.map { wordRunToWire(it) },
    "anchor_block_id" to comment.anchorBlockId,
    "anchor_text" to comment.anchorText,
)

/** The wire form of a [WordHeaderFooterRef]. */
fun wordHeaderFooterRefToWire(ref: WordHeaderFooterRef): Map<String, Any?> = linkedMapOf(
    "ref_type" to ref.refType,
    "part_name" to ref.partName,
    "text" to ref.text,
)

/** The wire form of a [WordSection]. */
fun wordSectionToWire(section: WordSection): Map<String, Any?> = linkedMapOf(
    "page_width" to section.pageWidth,
    "page_height" to section.pageHeight,
    "orientation" to section.orientation,
    "margin_top" to section.marginTop,
    "margin_right" to section.marginRight,
    "margin_bottom" to section.marginBottom,
    "margin_left" to section.marginLeft,
    "header_refs" to section.headerRefs.map { wordHeaderFooterRefToWire(it) },
    "footer_refs" to section.footerRefs.map { wordHeaderFooterRefToWire(it) },
    "end_block_id" to section.endBlockId,
)

/** The wire form of a [WordStyle]. */
fun wordStyleToWire(style: WordStyle): Map<String, Any?> = linkedMapOf(
    "style_id" to style.styleId,
    "name" to style.name,
    "based_on" to style.basedOn,
    "style_type" to style.styleType,
    "num_id" to style.numId,
    "num_level" to style.numLevel,
)

/** The wire form of a [WordMedia]. */
fun wordMediaToWire(media: WordMedia): Map<String, Any?> = linkedMapOf(
    "media_id" to media.mediaId,
    "part_name" to media.partName,
    "content_type" to media.contentType,
    "data" to Base64.getEncoder().encodeToString(media.data),
    "relationship_id" to media.relationshipId,
)

/** The wire form of a [WordPart]. */
fun wordPartToWire(part: WordPart): Map<String, Any?> = linkedMapOf(
    "part_name" to part.partName,
    "content_type" to part.contentType,
    "data" to Base64.getEncoder().encodeToString(part.data),
    "relationship_id" to part.relationshipId,
    "relationship_type" to part.relationshipType,
    "relationships" to part.relationships.map { wordPartRelationshipToWire(it) },
    "package_relationship" to part.packageRelationship?.let { wordPartRelationshipToWire(it) },
)

/** The wire form of a [WordPartRelationship]. */
fun wordPartRelationshipToWire(relationship: WordPartRelationship): Map<String, Any?> = linkedMapOf(
    "relationship_id" to relationship.relationshipId,
    "relationship_type" to relationship.relationshipType,
    "target" to relationship.target,
    "is_external" to relationship.isExternal,
)

/** The wire form of a [WordProperties]. */
fun wordPropertiesToWire(properties: WordProperties): Map<String, Any?> = linkedMapOf(
    "title" to properties.title,
    "author" to properties.author,
    "subject" to properties.subject,
    "keywords" to properties.keywords,
    "comments" to properties.comments,
    "created" to properties.created?.toString(),
    "modified" to properties.modified?.toString(),
    "revision" to properties.revision,
)

// ---------------------------------------------------------------------------
// The reading half
// ---------------------------------------------------------------------------

/**
 * Read a [WordDocument] from the wire.
 *
 * @param map the wire form of the document
 * @return the document
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordDocumentFromWire(map: Map<String, Any?>): WordDocument = WordDocument(
    blocks = map.requiredObjectList("blocks").map { wordBlockFromWire(it) },
    properties = wordPropertiesFromWire(map.requiredObject("properties")),
    footnotes = map.requiredObjectList("footnotes").map { wordFootnoteFromWire(it) },
    endnotes = map.requiredObjectList("endnotes").map { wordEndnoteFromWire(it) },
    comments = map.requiredObjectList("comments").map { wordCommentFromWire(it) },
    headers = map.requiredStringList("headers"),
    footers = map.requiredStringList("footers"),
    sections = map.requiredObjectList("sections").map { wordSectionFromWire(it) },
    styles = map.requiredObjectList("styles").map { wordStyleFromWire(it) },
    media = map.requiredObjectList("media").map { wordMediaFromWire(it) },
    parts = map.requiredObjectList("parts").map { wordPartFromWire(it) },
)

/**
 * Read a [WordBlock] from the wire.
 *
 * @param map the wire form of the block
 * @return the block
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordBlockFromWire(map: Map<String, Any?>): WordBlock = WordBlock(
    blockId = map.requiredString("block_id"),
    kind = WordBlockKind.fromWire(map.requiredString("kind")),
    text = map.requiredString("text"),
    level = map.requiredInt("level"),
    listMarker = map.optionalString("list_marker")?.let { ListMarker.fromWire(it) },
    listGroup = map.optionalString("list_group"),
    numberFormat = map.optionalString("number_format"),
    numberText = map.optionalString("number_text"),
    style = map.optionalString("style"),
    runs = map.requiredObjectList("runs").map { wordRunFromWire(it) },
    table = map.optionalObject("table")?.let { wordTableFromWire(it) },
)

/**
 * Read a [WordRun] from the wire.
 *
 * @param map the wire form of the run
 * @return the run
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordRunFromWire(map: Map<String, Any?>): WordRun = WordRun(
    text = map.requiredString("text"),
    bold = map.requiredBoolean("bold"),
    italic = map.requiredBoolean("italic"),
    underline = map.requiredBoolean("underline"),
    superscript = map.requiredBoolean("superscript"),
    subscript = map.requiredBoolean("subscript"),
    strike = map.requiredBoolean("strike"),
    font = map.optionalString("font"),
    size = map.optionalInt("size"),
    color = map.optionalString("color"),
    highlight = map.optionalString("highlight"),
    hyperlink = map.optionalString("hyperlink"),
    footnoteRef = map.optionalString("footnote_ref"),
    endnoteRef = map.optionalString("endnote_ref"),
    revision = map.optionalObject("revision")?.let { wordRevisionFromWire(it) },
    commentRefs = map.requiredStringList("comment_refs"),
    imageRef = map.optionalString("image_ref"),
    equationXml = map.optionalString("equation_xml"),
    partRef = map.optionalString("part_ref"),
    anchorExtent = map.optionalList("anchor_extent")?.let { anchorExtentFromWire(it) },
    formatChangeXml = map.optionalString("format_change_xml"),
)

/**
 * Read a [WordRevision] from the wire.
 *
 * @param map the wire form of the revision
 * @return the revision
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordRevisionFromWire(map: Map<String, Any?>): WordRevision = WordRevision(
    kind = WordRevisionKind.fromWire(map.requiredString("kind")),
    author = map.requiredString("author"),
    date = map.optionalString("date")?.let { offsetDateTimeFromWire(it, "date") },
    revisionId = map.requiredString("revision_id"),
)

/**
 * Read a [WordTable] from the wire.
 *
 * @param map the wire form of the table
 * @return the table
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordTableFromWire(map: Map<String, Any?>): WordTable = WordTable(
    rows = map.requiredObjectList("rows").map { wordTableRowFromWire(it) },
    headerRows = map.requiredInt("header_rows"),
    columnWidths = map.requiredIntList("column_widths"),
)

/**
 * Read a [WordTableRow] from the wire.
 *
 * @param map the wire form of the row
 * @return the row
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordTableRowFromWire(map: Map<String, Any?>): WordTableRow = WordTableRow(
    cells = map.requiredObjectList("cells").map { wordTableCellFromWire(it) },
)

/**
 * Read a [WordTableCell] from the wire.
 *
 * @param map the wire form of the cell
 * @return the cell
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordTableCellFromWire(map: Map<String, Any?>): WordTableCell = WordTableCell(
    text = map.requiredString("text"),
    rowSpan = map.requiredInt("row_span"),
    colSpan = map.requiredInt("col_span"),
    isHeader = map.requiredBoolean("is_header"),
    isMergeContinuation = map.requiredBoolean("is_merge_continuation"),
    width = map.optionalInt("width"),
    runs = map.requiredObjectList("runs").map { wordRunFromWire(it) },
    tables = map.requiredObjectList("tables").map { wordTableFromWire(it) },
)

/**
 * Read a [WordFootnote] from the wire.
 *
 * @param map the wire form of the footnote
 * @return the footnote
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordFootnoteFromWire(map: Map<String, Any?>): WordFootnote = WordFootnote(
    footnoteId = map.requiredString("footnote_id"),
    text = map.requiredString("text"),
    runs = map.requiredObjectList("runs").map { wordRunFromWire(it) },
)

/**
 * Read a [WordEndnote] from the wire.
 *
 * @param map the wire form of the endnote
 * @return the endnote
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordEndnoteFromWire(map: Map<String, Any?>): WordEndnote = WordEndnote(
    endnoteId = map.requiredString("endnote_id"),
    text = map.requiredString("text"),
    runs = map.requiredObjectList("runs").map { wordRunFromWire(it) },
)

/**
 * Read a [WordComment] from the wire.
 *
 * @param map the wire form of the comment
 * @return the comment
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordCommentFromWire(map: Map<String, Any?>): WordComment = WordComment(
    commentId = map.requiredString("comment_id"),
    author = map.requiredString("author"),
    initials = map.requiredString("initials"),
    date = map.optionalString("date")?.let { offsetDateTimeFromWire(it, "date") },
    text = map.requiredString("text"),
    runs = map.requiredObjectList("runs").map { wordRunFromWire(it) },
    anchorBlockId = map.optionalString("anchor_block_id"),
    anchorText = map.requiredString("anchor_text"),
)

/**
 * Read a [WordHeaderFooterRef] from the wire.
 *
 * @param map the wire form of the reference
 * @return the reference
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordHeaderFooterRefFromWire(map: Map<String, Any?>): WordHeaderFooterRef = WordHeaderFooterRef(
    refType = map.requiredString("ref_type"),
    partName = map.requiredString("part_name"),
    text = map.requiredString("text"),
)

/**
 * Read a [WordSection] from the wire.
 *
 * @param map the wire form of the section
 * @return the section
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordSectionFromWire(map: Map<String, Any?>): WordSection = WordSection(
    pageWidth = map.requiredInt("page_width"),
    pageHeight = map.requiredInt("page_height"),
    orientation = map.requiredString("orientation"),
    marginTop = map.requiredInt("margin_top"),
    marginRight = map.requiredInt("margin_right"),
    marginBottom = map.requiredInt("margin_bottom"),
    marginLeft = map.requiredInt("margin_left"),
    headerRefs = map.requiredObjectList("header_refs").map { wordHeaderFooterRefFromWire(it) },
    footerRefs = map.requiredObjectList("footer_refs").map { wordHeaderFooterRefFromWire(it) },
    endBlockId = map.optionalString("end_block_id"),
)

/**
 * Read a [WordStyle] from the wire.
 *
 * @param map the wire form of the style
 * @return the style
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordStyleFromWire(map: Map<String, Any?>): WordStyle = WordStyle(
    styleId = map.requiredString("style_id"),
    name = map.requiredString("name"),
    basedOn = map.requiredString("based_on"),
    styleType = map.requiredString("style_type"),
    numId = map.optionalString("num_id"),
    numLevel = map.requiredInt("num_level"),
)

/**
 * Read a [WordMedia] from the wire.
 *
 * @param map the wire form of the media
 * @return the media
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordMediaFromWire(map: Map<String, Any?>): WordMedia = WordMedia(
    mediaId = map.requiredString("media_id"),
    partName = map.requiredString("part_name"),
    contentType = map.requiredString("content_type"),
    data = bytesFromWire(map.requiredString("data"), "data"),
    relationshipId = map.requiredString("relationship_id"),
)

/**
 * Read a [WordPart] from the wire.
 *
 * @param map the wire form of the part
 * @return the part
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordPartFromWire(map: Map<String, Any?>): WordPart = WordPart(
    partName = map.requiredString("part_name"),
    contentType = map.requiredString("content_type"),
    data = bytesFromWire(map.requiredString("data"), "data"),
    relationshipId = map.requiredString("relationship_id"),
    relationshipType = map.requiredString("relationship_type"),
    relationships = map.requiredObjectList("relationships").map { wordPartRelationshipFromWire(it) },
    packageRelationship = map.optionalObject("package_relationship")
        ?.let { wordPartRelationshipFromWire(it) },
)

/**
 * Read a [WordPartRelationship] from the wire.
 *
 * @param map the wire form of the relationship
 * @return the relationship
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordPartRelationshipFromWire(map: Map<String, Any?>): WordPartRelationship = WordPartRelationship(
    relationshipId = map.requiredString("relationship_id"),
    relationshipType = map.requiredString("relationship_type"),
    target = map.requiredString("target"),
    isExternal = map.requiredBoolean("is_external"),
)

/**
 * Read a [WordProperties] from the wire.
 *
 * @param map the wire form of the properties
 * @return the properties
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun wordPropertiesFromWire(map: Map<String, Any?>): WordProperties = WordProperties(
    title = map.requiredString("title"),
    author = map.requiredString("author"),
    subject = map.requiredString("subject"),
    keywords = map.requiredString("keywords"),
    comments = map.requiredString("comments"),
    created = map.optionalString("created")?.let { offsetDateTimeFromWire(it, "created") },
    modified = map.optionalString("modified")?.let { offsetDateTimeFromWire(it, "modified") },
    revision = map.requiredInt("revision"),
)

// ---------------------------------------------------------------------------
// The strict readers
//
// The generic accessors in `lmeditor.core.json` default: they answer a
// question the payload did not ask. The contract says the opposite for a
// required key -- a decoder must raise rather than invent a value -- so the
// word wire has readers of its own. The rule they share is that a value of the
// wrong JSON type is a malformed payload, not something to coerce.
// ---------------------------------------------------------------------------

/**
 * The value of a key the payload must carry.
 *
 * An absent key and an explicit `null` are the same thing here: neither is a
 * value the model can hold, so neither may be turned into one.
 */
private fun Map<String, Any?>.requiredValue(key: String): Any? =
    this[key] ?: throw ValidationError(
        "the wire payload does not carry the required key '$key'",
        field = key,
    )

/** Read a required string. */
private fun Map<String, Any?>.requiredString(key: String): String =
    stringFromWire(requiredValue(key), key)

/** Read an optional string: an absent key and an explicit `null` both read as `null`. */
private fun Map<String, Any?>.optionalString(key: String): String? {
    val value = this[key] ?: return null
    return stringFromWire(value, key)
}

/** Read a required integer. */
private fun Map<String, Any?>.requiredInt(key: String): Int =
    intFromWire(requiredValue(key), key)

/** Read an optional integer: an absent key and an explicit `null` both read as `null`. */
private fun Map<String, Any?>.optionalInt(key: String): Int? {
    val value = this[key] ?: return null
    return intFromWire(value, key)
}

/** Read a required boolean. */
private fun Map<String, Any?>.requiredBoolean(key: String): Boolean {
    val value = requiredValue(key)
    return value as? Boolean
        ?: throw ValidationError(
            "the wire payload key '$key' must be a boolean",
            field = key,
            value = value,
        )
}

/** Read a required object. */
@Suppress("UNCHECKED_CAST")
private fun Map<String, Any?>.requiredObject(key: String): Map<String, Any?> {
    val value = requiredValue(key)
    return value as? Map<String, Any?>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an object",
            field = key,
            value = value,
        )
}

/** Read an optional object: an absent key and an explicit `null` both read as `null`. */
@Suppress("UNCHECKED_CAST")
private fun Map<String, Any?>.optionalObject(key: String): Map<String, Any?>? {
    val value = this[key] ?: return null
    return value as? Map<String, Any?>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an object or null",
            field = key,
            value = value,
        )
}

/** Read a required array. */
private fun Map<String, Any?>.requiredList(key: String): List<Any?> {
    val value = requiredValue(key)
    return value as? List<Any?>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an array",
            field = key,
            value = value,
        )
}

/** Read an optional array: an absent key and an explicit `null` both read as `null`. */
private fun Map<String, Any?>.optionalList(key: String): List<Any?>? {
    val value = this[key] ?: return null
    return value as? List<Any?>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an array or null",
            field = key,
            value = value,
        )
}

/** Read a required array of objects. */
@Suppress("UNCHECKED_CAST")
private fun Map<String, Any?>.requiredObjectList(key: String): List<Map<String, Any?>> =
    requiredList(key).map { entry ->
        entry as? Map<String, Any?>
            ?: throw ValidationError(
                "every entry of the wire payload key '$key' must be an object",
                field = key,
                value = entry,
            )
    }

/** Read a required array of strings. */
private fun Map<String, Any?>.requiredStringList(key: String): List<String> =
    requiredList(key).map { entry ->
        entry as? String
            ?: throw ValidationError(
                "every entry of the wire payload key '$key' must be a string",
                field = key,
                value = entry,
            )
    }

/** Read a required array of integers. */
private fun Map<String, Any?>.requiredIntList(key: String): List<Int> =
    requiredList(key).map { entry -> intFromWire(entry, key) }

/** Read a string value, refusing to coerce a value of another type. */
private fun stringFromWire(value: Any?, key: String): String =
    value as? String
        ?: throw ValidationError(
            "the wire payload key '$key' must be a string",
            field = key,
            value = value,
        )

/** Read an integer value, refusing to coerce a value of another type. */
private fun intFromWire(value: Any?, key: String): Int {
    val number = value as? Number
        ?: throw ValidationError(
            "the wire payload key '$key' must be an integer",
            field = key,
            value = value,
        )
    val asLong = number.toLong()
    val whole = number.toDouble() == asLong.toDouble()
    if (!whole || asLong < Int.MIN_VALUE || asLong > Int.MAX_VALUE) {
        throw ValidationError(
            "the wire payload key '$key' must be an integer",
            field = key,
            value = value,
        )
    }
    return asLong.toInt()
}

/**
 * Read a timestamp.
 *
 * Both spellings of an ISO-8601 timestamp are accepted: the one with an offset
 * and the naive one. A naive timestamp is read as UTC, because a timestamp
 * without an offset has no other meaning.
 */
private fun offsetDateTimeFromWire(value: String, key: String): OffsetDateTime =
    try {
        OffsetDateTime.parse(value)
    } catch (_: DateTimeException) {
        try {
            LocalDateTime.parse(value).atOffset(ZoneOffset.UTC)
        } catch (_: DateTimeException) {
            throw ValidationError(
                "the wire payload key '$key' is not an ISO-8601 timestamp",
                field = key,
                value = value,
            )
        }
    }

/** Read a base64 payload. */
private fun bytesFromWire(value: String, key: String): ByteArray =
    try {
        Base64.getDecoder().decode(value)
    } catch (_: IllegalArgumentException) {
        throw ValidationError(
            "the wire payload key '$key' is not valid base64",
            field = key,
            value = value,
        )
    }

/** Read the `(width, height)` pair a run is drawn at. */
private fun anchorExtentFromWire(items: List<Any?>): Pair<Int, Int> {
    if (items.size != 2) {
        throw ValidationError(
            "the wire payload key 'anchor_extent' must hold exactly two integers, got ${items.size}",
            field = "anchor_extent",
        )
    }
    return intFromWire(items[0], "anchor_extent") to intFromWire(items[1], "anchor_extent")
}
