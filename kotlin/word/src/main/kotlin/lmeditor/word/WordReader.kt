package lmeditor.word

import lmeditor.core.DecompressionLimits
import lmeditor.core.WordParseError
import org.w3c.dom.Document
import org.w3c.dom.Element
import org.w3c.dom.Node
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import java.util.zip.ZipException
import java.util.zip.ZipInputStream
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Reads `.docx` packages into the shared document model.
 *
 * The reader is stateless between calls: every [read] resets the omission
 * register and re-reads the package from scratch, so the same bytes always yield
 * the same [WordDocument].
 *
 * ## What the reader recovers
 *
 * * **R1** paragraphs, headings, list items and tables, in document order.
 * * **R2** runs keep their formatting, and a run's text is the concatenation of
 *   its `w:t` children with control characters removed.
 * * **R3** a table is a grid: its rows, their cells, the cells' column spans and
 *   the vertical merges between them are recovered, and a cell that starts at a
 *   covered grid column belongs to the merged region even when it carries no
 *   `w:vMerge` of its own -- Word never displays its text. Header rows are
 *   counted in [WordTable.headerRows].
 * * **R4** a list item is recognised through its numbering: the numbering id
 *   becomes the continuity group and the level becomes the nesting depth.
 * * **R5** hyperlinks resolve their target through `word/_rels/document.xml.rels`;
 *   an absolute target is used verbatim, a relative one is resolved against the
 *   caller's `baseUri` -- or preserved verbatim and recorded in [lastOmissions]
 *   when there is no base -- and an internal anchor resolves to `#anchor`.
 * * **R6** footnotes are read from `word/footnotes.xml` and referenced from runs
 *   through `w:footnoteReference`.
 * * **R7** tracked revisions are represented explicitly rather than flattened:
 *   `w:ins`/`w:moveTo` content becomes an insertion and `w:del`/`w:moveFrom`
 *   content becomes a deletion, so deleted text is recoverable.
 * * **R8** content controls (`w:sdt`) are transparent: their content is walked.
 * * **R9** text boxes (`w:txbxContent`) become blocks of their own.
 *
 * ## What the reader never loses silently
 *
 * Content that has no representation in the model is not thrown away: the
 * package parts the reader cannot model -- a chart, an embedded object, a
 * glossary document -- are *carried through* with their bytes in
 * [WordDocument.parts], and the body reference that anchored them travels on the
 * run, so the writer can put both back into the package. What is still lost is
 * recorded in [lastOmissions] instead of disappearing without a trace: a part
 * that is missing from the package, a relationship that points at nothing, a
 * body reference that reaches outside the package.
 */
class WordReader {

    /**
     * The omissions of the most recent read, as human-readable descriptions.
     *
     * An empty list means the document was read without a single loss.
     */
    var lastOmissions: List<String> = emptyList()
        private set

    /**
     * The relationships the package root holds, from the most recent read.
     *
     * The package root reaches parts the document does not, and a part it
     * reaches carries that relationship on
     * [WordPart.packageRelationship]. A package-root relationship that reaches
     * no part of the package -- an external one, say -- has no place in the
     * document model, so it is kept here rather than being dropped.
     *
     * The order is by relationship id, so the same package always answers with
     * the same list.
     */
    val lastPackageRelationships: List<WordPartRelationship>
        get() = packageRelationships.values.sortedBy { it.relationshipId }

    private val omissionCounts = mutableMapOf<String, Int>()
    private val styles = mutableMapOf<String, StyleInfo>()
    private var numbering: NumberingInfo? = null
    private val relationships = mutableMapOf<String, Relationship>()
    private val footnoteRuns = mutableMapOf<String, List<WordRun>>()
    private val endnoteRuns = mutableMapOf<String, List<WordRun>>()
    private val commentRuns = mutableMapOf<String, List<WordRun>>()
    private val commentMeta = mutableMapOf<String, CommentMeta>()
    private val media = linkedMapOf<String, WordMedia>()
    private val parts = linkedMapOf<String, WordPart>()
    private val partRelationships = mutableMapOf<String, Pair<String, String>>()
    private val packageRelationships = mutableMapOf<String, WordPartRelationship>()
    private val contentTypes = mutableMapOf<String, String>()
    private val usedStyles = linkedSetOf<String>()
    private val openCommentRanges = mutableListOf<String>()
    private val sectionEnds = mutableListOf<Pair<Element?, Int>>()
    private var entries = linkedMapOf<String, ByteArray>()
    private var entryOrder: List<String> = emptyList()
    private var baseUri: String? = null
    private var origin: String = "document"

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    /**
     * Read a document from a path on disk.
     *
     * @param path the `.docx` file to read
     * @param baseUri the URI the document is served from. A hyperlink whose
     *   relationship target is relative is resolved against it; with no base the
     *   target is preserved verbatim and recorded in [lastOmissions].
     * @return the parsed document
     * @throws WordParseError if the package is missing, malformed or cannot be
     *   turned into the document model
     */
    fun read(path: Path, baseUri: String? = null): WordDocument {
        val origin = path.fileName?.toString() ?: "document"
        val data = try {
            Files.readAllBytes(path)
        } catch (exc: IOException) {
            throw WordParseError(origin, "the file could not be read: $exc")
        }
        return readBytes(data, baseUri, origin)
    }

    /**
     * Read a document from the raw bytes of a `.docx` package.
     *
     * @param data the bytes of a `.docx` package
     * @param baseUri the URI the document is served from
     * @param origin the name the document is known by in error messages
     * @return the parsed document
     * @throws WordParseError if the package is missing, malformed or cannot be
     *   turned into the document model
     */
    fun readBytes(data: ByteArray, baseUri: String? = null, origin: String = "document"): WordDocument {
        beginRead(baseUri)
        this.origin = origin
        return readPackage(data, origin)
    }

    // ------------------------------------------------------------------
    // Package level
    // ------------------------------------------------------------------

    private fun beginRead(baseUri: String?) {
        lastOmissions = emptyList()
        omissionCounts.clear()
        styles.clear()
        numbering = null
        relationships.clear()
        footnoteRuns.clear()
        endnoteRuns.clear()
        commentRuns.clear()
        commentMeta.clear()
        media.clear()
        parts.clear()
        partRelationships.clear()
        packageRelationships.clear()
        contentTypes.clear()
        usedStyles.clear()
        openCommentRanges.clear()
        sectionEnds.clear()
        entries = linkedMapOf()
        entryOrder = emptyList()
        this.baseUri = baseUri
    }

    /**
     * Parse an OPC package into a document.
     *
     * Every failure of the model boundary is translated into a [WordParseError]:
     * a malformed document must never reach the caller as a raw
     * [lmeditor.core.ValidationError].
     */
    private fun readPackage(data: ByteArray, origin: String): WordDocument {
        val reason = WordFormats.describeUnsupportedFormat(data)
        if (reason.startsWith("legacy .doc")) {
            throw WordParseError(origin, reason)
        }
        readEntries(data, origin)
        if (!entries.containsKey(DOCUMENT_PART)) {
            throw WordParseError(origin, "the package does not contain the required part word/document.xml")
        }
        val document = parsePart(DOCUMENT_PART, origin)
        val body = childElement(document, "body")
            ?: throw WordParseError(origin, "word/document.xml does not contain a w:body element")

        loadStyles(origin)
        loadNumbering(origin)
        loadRelationships(origin)
        loadPackageRelationships(origin)
        loadContentTypes(origin)
        loadFootnotes(origin)
        loadEndnotes(origin)
        loadComments(origin)

        val drafts = walkBlocks(body)
        carryParts()
        val sections = sectionEnds.map { (properties, endCount) ->
            sectionFrom(properties, sectionEndBlockId(endCount))
        } + bodySection(body, drafts)
        val headers = readHeadersAndFooters(sections, origin, "header")
        val footers = readHeadersAndFooters(sections, origin, "footer")
        val properties = readCoreProperties(origin)

        val blocks = buildBlocks(drafts)
        return WordDocument(
            blocks = blocks,
            properties = properties,
            footnotes = buildFootnotes(),
            endnotes = buildEndnotes(),
            comments = buildComments(blocks),
            headers = headers,
            footers = footers,
            sections = sections,
            styles = buildStyles(),
            media = media.values.toList(),
            parts = parts.values.sortedBy { it.partName },
        )
    }

    private fun sectionEndBlockId(endCount: Int): String? =
        if (endCount > 0) "b%04d".format(endCount) else null

    private fun bodySection(body: Element, drafts: List<Draft>): WordSection {
        val properties = childElement(body, "sectPr") ?: return WordSection()
        return sectionFrom(properties, if (drafts.isNotEmpty()) "b%04d".format(drafts.size) else null)
    }

    // ------------------------------------------------------------------
    // Package limits
    // ------------------------------------------------------------------

    private fun readEntries(data: ByteArray, origin: String) {
        val collected = linkedMapOf<String, ByteArray>()
        var total = 0L
        try {
            ZipInputStream(ByteArrayInputStream(data)).use { zip ->
                while (true) {
                    val entry: java.util.zip.ZipEntry = zip.nextEntry ?: break
                    if (entry.isDirectory) {
                        zip.closeEntry()
                        continue
                    }
                    if (collected.size >= MAX_PACKAGE_MEMBERS) {
                        throw WordParseError(
                            origin,
                            "the package holds more than $MAX_PACKAGE_MEMBERS members",
                        )
                    }
                    val buffer = ByteArrayOutputStream()
                    val chunk = ByteArray(READ_CHUNK_BYTES)
                    while (true) {
                        val count = zip.read(chunk)
                        if (count < 0) break
                        total += count
                        if (total > MAX_TOTAL_UNCOMPRESSED_BYTES) {
                            throw WordParseError(
                                origin,
                                "the package expands to more than $MAX_TOTAL_UNCOMPRESSED_BYTES bytes",
                            )
                        }
                        if (buffer.size().toLong() + count > DecompressionLimits.MAX_EXPANDED_BYTES) {
                            throw WordParseError(
                                origin,
                                "the package part ${entry.name} expands to more than " +
                                    "${DecompressionLimits.MAX_EXPANDED_BYTES} bytes",
                            )
                        }
                        buffer.write(chunk, 0, count)
                    }
                    collected[entry.name] = buffer.toByteArray()
                    val compressed = entry.compressedSize
                    if (compressed > 0) {
                        val ratio = buffer.size() / compressed
                        if (ratio > DecompressionLimits.MAX_COMPRESSION_RATIO) {
                            throw WordParseError(
                                origin,
                                "the package part ${entry.name} is compressed $ratio times, " +
                                    "more than the ${DecompressionLimits.MAX_COMPRESSION_RATIO} allowed",
                            )
                        }
                    }
                    zip.closeEntry()
                }
            }
        } catch (exc: WordParseError) {
            throw exc
        } catch (exc: ZipException) {
            throw WordParseError(origin, "the file is not a valid .docx package (bad zip container): $exc")
        } catch (exc: IOException) {
            throw WordParseError(origin, "the file could not be opened as a zip container: $exc")
        }
        entries = collected
        entryOrder = collected.keys.toList()
    }

    // ------------------------------------------------------------------
    // XML
    // ------------------------------------------------------------------

    private fun parsePart(name: String, origin: String): Element {
        val data = entries[name]
            ?: throw WordParseError(origin, "the package part $name is missing")
        return try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.isNamespaceAware = true
            factory.isXIncludeAware = false
            factory.isExpandEntityReferences = false
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
            setFeatureQuietly(factory, "http://apache.org/xml/features/disallow-doctype-decl", true)
            setFeatureQuietly(factory, "http://xml.org/sax/features/external-general-entities", false)
            setFeatureQuietly(factory, "http://xml.org/sax/features/external-parameter-entities", false)
            setAttributeQuietly(factory, XMLConstants.ACCESS_EXTERNAL_DTD, "")
            setAttributeQuietly(factory, XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
            val document: Document = factory.newDocumentBuilder().parse(ByteArrayInputStream(data))
            document.documentElement
                ?: throw WordParseError(origin, "the package part $name is empty")
        } catch (exc: WordParseError) {
            throw exc
        } catch (exc: Exception) {
            throw WordParseError(origin, "the package part $name could not be parsed: ${exc.message}")
        }
    }

    private fun setFeatureQuietly(factory: DocumentBuilderFactory, name: String, value: Boolean) {
        try {
            factory.setFeature(name, value)
        } catch (_: Exception) {
            // A parser that does not know the feature cannot be attacked
            // through it either.
        }
    }

    private fun setAttributeQuietly(factory: DocumentBuilderFactory, name: String, value: String) {
        try {
            factory.setAttribute(name, value)
        } catch (_: Exception) {
            // Ditto.
        }
    }

    // ------------------------------------------------------------------
    // Styles and numbering
    // ------------------------------------------------------------------

    private fun loadStyles(origin: String) {
        if (!entries.containsKey(STYLES_PART)) return
        val root = parsePart(STYLES_PART, origin)
        for (style in childElements(root, "style")) {
            val styleId = attr(style, NS_W, "styleId")
            if (styleId.isEmpty()) continue
            val properties = childElement(style, "pPr")
            var numId: String? = null
            var numLevel = 0
            if (properties != null) {
                val numberingRef = childElement(properties, "numPr")
                if (numberingRef != null) {
                    val numRef = childElement(numberingRef, "numId")
                    if (numRef != null) numId = attr(numRef, NS_W, "val")
                    val levelRef = childElement(numberingRef, "ilvl")
                    if (levelRef != null) numLevel = safeInt(attr(levelRef, NS_W, "val"), 0)
                }
            }
            styles[styleId] = StyleInfo(
                styleId = styleId,
                name = valAttribute(childElement(style, "name")),
                basedOn = valAttribute(childElement(style, "basedOn")),
                numId = numId,
                numLevel = numLevel,
                runProps = readRunProperties(childElement(style, "rPr")),
            )
        }
    }

    private fun readRunProperties(properties: Element?): RunProps {
        if (properties == null) return RunProps()
        val vertical = childElement(properties, "vertAlign")
        val verticalValue = if (vertical == null) "" else attr(vertical, NS_W, "val").trim().lowercase()
        val fonts = childElement(properties, "rFonts")
        val font = fonts?.let {
            attr(it, NS_W, "ascii").trim().ifEmpty {
                attr(it, NS_W, "hAnsi").trim().ifEmpty {
                    attr(it, NS_W, "cs").trim().ifEmpty { null }
                }
            }
        }
        val sizeElement = childElement(properties, "sz")
        val size = sizeElement?.let {
            val raw = attr(it, NS_W, "val").trim()
            if (raw.isNotEmpty() && raw.all { ch -> ch.isDigit() }) raw.toInt().takeIf { value -> value > 0 } else null
        }
        val colorElement = childElement(properties, "color")
        val color = colorElement?.let {
            val raw = attr(it, NS_W, "val").trim().removePrefix("#")
            if (raw.isNotEmpty() && !raw.equals("auto", ignoreCase = true)) raw.uppercase() else null
        }
        val highlightElement = childElement(properties, "highlight")
        val highlight = highlightElement?.let {
            val raw = attr(it, NS_W, "val").trim()
            if (raw.isNotEmpty() && !raw.equals("none", ignoreCase = true)) raw else null
        }
        return RunProps(
            bold = onOff(childElement(properties, "b")),
            italic = onOff(childElement(properties, "i")),
            underline = underline(childElement(properties, "u")),
            superscript = verticalValue == "superscript",
            subscript = verticalValue == "subscript",
            strike = onOff(childElement(properties, "strike")),
            font = font,
            size = size,
            color = color,
            highlight = highlight,
        )
    }

    private fun loadNumbering(origin: String) {
        if (!entries.containsKey(NUMBERING_PART)) return
        val root = parsePart(NUMBERING_PART, origin)

        val abstractLevels = mutableMapOf<String, MutableMap<Int, String>>()
        val abstractLevelTexts = mutableMapOf<String, MutableMap<Int, String>>()
        for (abstract in childElements(root, "abstractNum")) {
            val abstractId = attr(abstract, NS_W, "abstractNumId")
            val levels = mutableMapOf<Int, String>()
            val levelTexts = mutableMapOf<Int, String>()
            for (level in childElements(abstract, "lvl")) {
                val index = safeInt(attr(level, NS_W, "ilvl"), 0)
                levels[index] = childElement(level, "numFmt")?.let { attr(it, NS_W, "val") } ?: ""
                levelTexts[index] = childElement(level, "lvlText")?.let { attr(it, NS_W, "val") } ?: ""
            }
            abstractLevels[abstractId] = levels
            abstractLevelTexts[abstractId] = levelTexts
        }

        val numToAbstract = mutableMapOf<String, String>()
        val overrides = mutableMapOf<String, MutableMap<Int, String>>()
        val overrideLevelTexts = mutableMapOf<String, MutableMap<Int, String>>()
        for (number in childElements(root, "num")) {
            val numId = attr(number, NS_W, "numId")
            numToAbstract[numId] = childElement(number, "abstractNumId")?.let { attr(it, NS_W, "val") } ?: ""
            val overrideLevels = mutableMapOf<Int, String>()
            val overrideTexts = mutableMapOf<Int, String>()
            for (override in childElements(number, "lvlOverride")) {
                val index = safeInt(attr(override, NS_W, "ilvl"), 0)
                val level = childElement(override, "lvl") ?: continue
                val formatElement = childElement(level, "numFmt") ?: continue
                overrideLevels[index] = attr(formatElement, NS_W, "val")
                overrideTexts[index] = childElement(level, "lvlText")?.let { attr(it, NS_W, "val") } ?: ""
            }
            overrides[numId] = overrideLevels
            overrideLevelTexts[numId] = overrideTexts
        }

        numbering = NumberingInfo(
            numToAbstract = numToAbstract,
            abstractLevels = abstractLevels,
            abstractLevelTexts = abstractLevelTexts,
            overrides = overrides,
            overrideLevelTexts = overrideLevelTexts,
        )
    }

    private fun numberFormat(numId: String, level: Int): String? {
        val info = numbering ?: return null
        val override = info.overrides[numId]?.get(level)
        if (!override.isNullOrEmpty()) return override
        val abstractId = info.numToAbstract[numId] ?: return null
        return info.abstractLevels[abstractId]?.get(level)
    }

    private fun numberLevelText(numId: String, level: Int): String? {
        val info = numbering ?: return null
        val override = info.overrideLevelTexts[numId]?.get(level)
        if (!override.isNullOrEmpty()) return override
        val abstractId = info.numToAbstract[numId] ?: return null
        return info.abstractLevelTexts[abstractId]?.get(level)
    }

    // ------------------------------------------------------------------
    // Relationships
    // ------------------------------------------------------------------

    private fun loadRelationships(origin: String) {
        if (!entries.containsKey(DOCUMENT_RELS_PART)) return
        val root = parsePart(DOCUMENT_RELS_PART, origin)
        for (element in elementChildren(root)) {
            if (localName(element) != "Relationship") continue
            val relId = plainAttribute(element, "Id")
            if (relId.isEmpty()) continue
            val target = plainAttribute(element, "Target")
            val relType = plainAttribute(element, "Type")
            val isExternal = plainAttribute(element, "TargetMode").trim().lowercase() == "external"
            relationships[relId] = Relationship(relId, target, relType, isExternal)
            if (isExternal) {
                // An external target names a file outside the package: there
                // are no bytes to carry through.
                continue
            }
            val partName = packagePartName(target)
            if (partName.isNotEmpty() && !partRelationships.containsKey(partName)) {
                // The first relationship that reaches a part is the one the
                // part is written back under; a part several relationships
                // point at keeps the first of them.
                partRelationships[partName] = relId to relType
            }
        }
    }

    private fun loadPackageRelationships(origin: String) {
        if (!entries.containsKey(PACKAGE_RELS_PART)) return
        val root = try {
            parsePart(PACKAGE_RELS_PART, origin)
        } catch (exc: WordParseError) {
            omit("package relationship part (_rels/.rels) could not be parsed")
            return
        }
        val collected = mutableListOf<WordPartRelationship>()
        for (element in elementChildren(root)) {
            if (localName(element) != "Relationship") continue
            val relId = plainAttribute(element, "Id")
            val relType = plainAttribute(element, "Type")
            val target = plainAttribute(element, "Target")
            if (relId.isEmpty()) {
                omit("package relationship part (_rels/.rels) holds a relationship without an id")
                continue
            }
            if (relType.isEmpty()) {
                omit("package relationship part (_rels/.rels) holds relationship $relId without a type")
                continue
            }
            if (target.isEmpty()) {
                omit("package relationship part (_rels/.rels) holds relationship $relId without a target")
                continue
            }
            val isExternal = plainAttribute(element, "TargetMode").trim().lowercase() == "external"
            collected.add(
                WordPartRelationship(
                    relationshipId = relId,
                    relationshipType = relType,
                    target = if (isExternal) target else resolveAgainst("", target),
                    isExternal = isExternal,
                ),
            )
        }
        collected.sortBy { it.relationshipId }
        for (relationship in collected) {
            packageRelationships[relationship.relationshipId] = relationship
        }
    }

    private fun packageRelationshipFor(partName: String): WordPartRelationship? {
        for (relationship in packageRelationships.values) {
            if (!relationship.isExternal && relationship.target == partName) return relationship
        }
        return null
    }

    // ------------------------------------------------------------------
    // Content types
    // ------------------------------------------------------------------

    private fun loadContentTypes(origin: String) {
        if (!entries.containsKey(CONTENT_TYPES_PART)) return
        val root = parsePart(CONTENT_TYPES_PART, origin)
        for (element in elementChildren(root)) {
            when (localName(element)) {
                "Default" -> {
                    val extension = plainAttribute(element, "Extension").trim().lowercase()
                    val contentType = plainAttribute(element, "ContentType").trim()
                    if (extension.isNotEmpty() && contentType.isNotEmpty()) {
                        contentTypes.putIfAbsent(extension, contentType)
                    }
                }

                "Override" -> {
                    val partName = plainAttribute(element, "PartName").trim()
                    val contentType = plainAttribute(element, "ContentType").trim()
                    if (partName.isNotEmpty() && contentType.isNotEmpty()) {
                        contentTypes.putIfAbsent(partName, contentType)
                    }
                }
            }
        }
    }

    private fun contentTypeFor(partName: String): String {
        contentTypes["/$partName"]?.let { return it }
        val extension = partName.substringAfterLast('.', "").lowercase()
        contentTypes[extension]?.let { return it }
        return DEFAULT_CONTENT_TYPES[extension] ?: "application/octet-stream"
    }

    // ------------------------------------------------------------------
    // Notes and comments
    // ------------------------------------------------------------------

    private fun loadFootnotes(origin: String) {
        if (!entries.containsKey(FOOTNOTES_PART)) return
        val root = parsePart(FOOTNOTES_PART, origin)
        for (footnote in childElements(root, "footnote")) {
            val footnoteId = attr(footnote, NS_W, "id")
            if (footnoteId.isEmpty()) continue
            val footnoteType = attr(footnote, NS_W, "type").trim().lowercase()
            if (footnoteType in SEPARATOR_TYPES) continue
            if (footnoteId.removePrefix("-").all { it.isDigit() } && footnoteId.removePrefix("-").isNotEmpty()) {
                // Word reserves id -1 and id 0 for the separator footnotes.
                if (footnoteId.toInt() <= 0) continue
            }
            footnoteRuns[footnoteId] = noteRuns(footnote)
        }
    }

    private fun loadEndnotes(origin: String) {
        if (!entries.containsKey(ENDNOTES_PART)) return
        val root = parsePart(ENDNOTES_PART, origin)
        for (endnote in childElements(root, "endnote")) {
            val endnoteId = attr(endnote, NS_W, "id")
            if (endnoteId.isEmpty()) continue
            val endnoteType = attr(endnote, NS_W, "type").trim().lowercase()
            if (endnoteType in SEPARATOR_TYPES) continue
            if (endnoteId.removePrefix("-").all { it.isDigit() } && endnoteId.removePrefix("-").isNotEmpty()) {
                if (endnoteId.toInt() <= 0) continue
            }
            endnoteRuns[endnoteId] = noteRuns(endnote)
        }
    }

    private fun loadComments(origin: String) {
        if (!entries.containsKey(COMMENTS_PART)) return
        val root = parsePart(COMMENTS_PART, origin)
        for (comment in childElements(root, "comment")) {
            val commentId = attr(comment, NS_W, "id")
            if (commentId.isEmpty()) continue
            commentRuns[commentId] = noteRuns(comment)
            commentMeta[commentId] = CommentMeta(
                author = attr(comment, NS_W, "author"),
                initials = attr(comment, NS_W, "initials"),
                date = readTimestampValue(attr(comment, NS_W, "date").trim(), "comment"),
            )
        }
    }

    private fun noteRuns(container: Element): List<WordRun> {
        val collected = mutableListOf<WordRun>()
        for (paragraph in childElements(container, "p")) {
            for (segment in walkInline(paragraph, null)) {
                if (segment.isBreak || segment.blocks.isNotEmpty()) continue
                collected.addAll(segment.runs)
            }
        }
        return collected
    }

    // ------------------------------------------------------------------
    // Sections, headers and footers
    // ------------------------------------------------------------------

    private fun paragraphSection(paragraph: Element): Element? {
        val properties = childElement(paragraph, "pPr") ?: return null
        return childElement(properties, "sectPr")
    }

    private fun sectionFrom(properties: Element?, endBlockId: String?): WordSection {
        if (properties == null) return WordSection()
        val pageSize = childElement(properties, "pgSz")
        var pageWidth = 12240
        var pageHeight = 15840
        var orientation = "portrait"
        if (pageSize != null) {
            pageWidth = safeInt(attr(pageSize, NS_W, "w"), pageWidth)
            pageHeight = safeInt(attr(pageSize, NS_W, "h"), pageHeight)
            val rawOrientation = attr(pageSize, NS_W, "orient").trim().lowercase()
            orientation = if (rawOrientation.isNotEmpty()) rawOrientation else if (pageWidth > pageHeight) "landscape" else "portrait"
        }
        val margins = childElement(properties, "pgMar")
        var marginTop = 1440
        var marginRight = 1440
        var marginBottom = 1440
        var marginLeft = 1440
        if (margins != null) {
            marginTop = safeInt(attr(margins, NS_W, "top"), marginTop)
            marginRight = safeInt(attr(margins, NS_W, "right"), marginRight)
            marginBottom = safeInt(attr(margins, NS_W, "bottom"), marginBottom)
            marginLeft = safeInt(attr(margins, NS_W, "left"), marginLeft)
        }
        val headerRefs = childElements(properties, "headerReference")
            .map { headerFooterRef(it, "header") }
        val footerRefs = childElements(properties, "footerReference")
            .map { headerFooterRef(it, "footer") }
        return WordSection(
            pageWidth = maxOf(1, pageWidth),
            pageHeight = maxOf(1, pageHeight),
            orientation = orientation,
            marginTop = maxOf(0, marginTop),
            marginRight = maxOf(0, marginRight),
            marginBottom = maxOf(0, marginBottom),
            marginLeft = maxOf(0, marginLeft),
            headerRefs = headerRefs,
            footerRefs = footerRefs,
            endBlockId = endBlockId,
        )
    }

    private fun headerFooterRef(reference: Element, kind: String): WordHeaderFooterRef {
        val refType = attr(reference, NS_W, "type").trim().lowercase().ifEmpty { "default" }
        val relId = attr(reference, NS_R, "id")
        var partName = ""
        if (relId.isNotEmpty()) {
            val relationship = relationships[relId]
            if (relationship == null) {
                omit("$kind relationship $relId is missing")
            } else {
                partName = partNameFor(relationship.target)
            }
        }
        val text = if (partName.isNotEmpty()) headerFooterText(partName) else ""
        return WordHeaderFooterRef(refType = refType, partName = partName, text = text)
    }

    private fun partNameFor(target: String): String {
        if (target.isEmpty()) return ""
        if (target.startsWith("word/")) return target
        if (target.startsWith("/")) return target.trimStart('/')
        return "word/$target"
    }

    /**
     * Read the header or footer texts of the document.
     *
     * Headers and footers are matched through the section references that
     * name them -- `default`, `even` and `first` -- because the part name
     * says nothing about the role a header plays. A document that carries no
     * reference at all falls back to part-name order, which is all the
     * information such a document offers.
     */
    private fun readHeadersAndFooters(
        sections: List<WordSection>,
        origin: String,
        kind: String,
    ): List<String> {
        val hasReferences = sections.any {
            it.headerRefs.isNotEmpty() || it.footerRefs.isNotEmpty()
        }
        if (hasReferences) return orderedByRefType(sections, kind)
        val prefix = "word/$kind"
        val names = entryOrder.filter { name ->
            name.startsWith(prefix) && name.endsWith(".xml") && !name.removePrefix(prefix).contains('/')
        }
        return names.sortedWith(naturalOrder).map { name ->
            val root = parsePart(name, origin)
            partText(root)
        }
    }

    /**
     * Return the header or footer texts in reference-type order.
     *
     * The order is the one Word itself uses -- the default page, the first
     * page, then the even pages -- so the list means the same thing for every
     * document, whatever order the references appear in. Any reference of a
     * kind Word has no role for follows in its own order.
     */
    private fun orderedByRefType(sections: List<WordSection>, kind: String): List<String> {
        val order = listOf("default", "first", "even")
        val texts = mutableListOf<String>()
        for (refType in order) {
            for (section in sections) {
                for (ref in refsOf(section, kind)) {
                    if (ref.refType == refType && ref.text.isNotEmpty() && ref.text !in texts) {
                        texts.add(ref.text)
                    }
                }
            }
        }
        for (section in sections) {
            for (ref in refsOf(section, kind)) {
                if (ref.text.isNotEmpty() && ref.text !in texts) texts.add(ref.text)
            }
        }
        return texts
    }

    private fun refsOf(section: WordSection, kind: String): List<WordHeaderFooterRef> =
        if (kind == "header") section.headerRefs else section.footerRefs

    private fun partText(root: Element): String {
        val collected = mutableListOf<String>()
        for (draft in walkBlocks(root)) {
            val text = if (draft.kind == WordBlockKind.TABLE && draft.table != null) {
                draft.table.rows.joinToString("\n") { row ->
                    row.cells.joinToString(" | ") { it.text }
                }
            } else {
                draft.text
            }
            if (text.isNotBlank()) collected.add(text)
        }
        return collected.joinToString(" ")
    }

    private fun headerFooterText(partName: String): String {
        val data = entries[partName] ?: return ""
        return try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.isNamespaceAware = true
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
            setFeatureQuietly(factory, "http://apache.org/xml/features/disallow-doctype-decl", true)
            val document = factory.newDocumentBuilder().parse(ByteArrayInputStream(data))
            document.documentElement?.let { partText(it) } ?: ""
        } catch (_: Exception) {
            // A header that cannot be parsed is a loss, and a loss that is not
            // reported is the defect this register exists to prevent.
            omit("header or footer part ($partName) could not be parsed")
            ""
        }
    }

    // ------------------------------------------------------------------
    // Core properties
    // ------------------------------------------------------------------

    private fun readCoreProperties(origin: String): WordProperties {
        if (!entries.containsKey(CORE_PROPERTIES_PART)) return WordProperties()
        val root = parsePart(CORE_PROPERTIES_PART, origin)

        var revision = 1
        val revisionElement = childElementByNamespace(root, NS_CP, "revision")
        if (revisionElement != null) {
            val raw = elementText(revisionElement)
            if (raw.isNotEmpty()) {
                revision = raw.toIntOrNull()?.let { maxOf(1, it) } ?: run {
                    omit("core property cp:revision is not an integer")
                    1
                }
            }
        }

        return WordProperties(
            title = elementText(childElementByNamespace(root, NS_DC, "title")),
            author = elementText(childElementByNamespace(root, NS_DC, "creator")),
            subject = elementText(childElementByNamespace(root, NS_DC, "subject")),
            keywords = elementText(childElementByNamespace(root, NS_CP, "keywords")),
            comments = elementText(childElementByNamespace(root, NS_DC, "description")),
            created = readTimestamp(childElementByNamespace(root, NS_DCTERMS, "created"), "created"),
            modified = readTimestamp(childElementByNamespace(root, NS_DCTERMS, "modified"), "modified"),
            revision = revision,
        )
    }

    private fun readTimestamp(element: Element?, label: String): OffsetDateTime? =
        readTimestampValue(if (element == null) "" else elementText(element), label)

    private fun readTimestampValue(raw: String, label: String): OffsetDateTime? {
        if (raw.isEmpty()) return null
        val parsed = try {
            OffsetDateTime.parse(raw)
        } catch (_: DateTimeParseException) {
            try {
                java.time.LocalDateTime.parse(raw).atOffset(java.time.ZoneOffset.UTC)
            } catch (_: DateTimeParseException) {
                null
            }
        } ?: run {
            omit("core property dcterms:$label is not a valid timestamp")
            return null
        }
        return parsed.withOffsetSameInstant(java.time.ZoneOffset.UTC)
    }

    // ------------------------------------------------------------------
    // Carried parts
    // ------------------------------------------------------------------

    private fun carryParts() {
        for (name in entryOrder.sorted()) {
            if (name in IGNORED_PARTS) continue
            if (name.startsWith("word/theme/")) continue
            if (isRelationshipPart(name)) continue
            if (name.startsWith("word/header") || name.startsWith("word/footer")) continue
            if (name.startsWith("word/media/")) {
                if (name in media) {
                    // The media travelled into the model and can be written
                    // back out; nothing was lost.
                    continue
                }
                omit("image media part ($name)")
                continue
            }
            carryPart(name)
        }
    }

    private fun carryPart(name: String) {
        if (parts.containsKey(name)) return
        val data = entries[name] ?: return
        val (relationshipId, relationshipType) = partRelationships[name] ?: ("" to "")
        parts[name] = WordPart(
            partName = name,
            contentType = contentTypeFor(name),
            data = data,
            relationshipId = relationshipId,
            relationshipType = relationshipType,
            relationships = partOwnRelationships(name),
            packageRelationship = packageRelationshipFor(name),
        )
    }

    private fun partOwnRelationships(partName: String): List<WordPartRelationship> {
        val relsName = relationshipPartName(partName)
        val data = entries[relsName] ?: return emptyList()
        val root = try {
            parsePart(relsName, origin)
        } catch (_: WordParseError) {
            omit("relationship part ($relsName) could not be parsed")
            return emptyList()
        }
        val collected = mutableListOf<WordPartRelationship>()
        for (element in elementChildren(root)) {
            if (localName(element) != "Relationship") continue
            val relId = plainAttribute(element, "Id")
            val relType = plainAttribute(element, "Type")
            val target = plainAttribute(element, "Target")
            if (relId.isEmpty()) {
                omit("relationship part ($relsName) holds a relationship without an id")
                continue
            }
            if (relType.isEmpty()) {
                omit("relationship part ($relsName) holds relationship $relId without a type")
                continue
            }
            if (target.isEmpty()) {
                omit("relationship part ($relsName) holds relationship $relId without a target")
                continue
            }
            val isExternal = plainAttribute(element, "TargetMode").trim().lowercase() == "external"
            collected.add(
                WordPartRelationship(
                    relationshipId = relId,
                    relationshipType = relType,
                    target = if (isExternal) target else resolveAgainst(partName.substringBeforeLast('/', ""), target),
                    isExternal = isExternal,
                ),
            )
        }
        collected.sortBy { it.relationshipId }
        return collected
    }

    private fun partReference(relId: String, description: String): String? {
        val relationship = relationships[relId]
        if (relationship == null) {
            omit("$description relationship $relId is missing")
            return null
        }
        if (relationship.isExternal) {
            omit("$description points at an external target (${relationship.target})")
            return null
        }
        val partName = packagePartName(relationship.target)
        if (partName.isEmpty()) {
            omit("$description relationship $relId has no target")
            return null
        }
        if (!entries.containsKey(partName)) {
            omit("$description points at a missing package part ($partName)")
            return null
        }
        carryPart(partName)
        return partName
    }

    private fun loadMedia(partName: String, relId: String): WordMedia? {
        media[partName]?.let { return it }
        val data = entries[partName] ?: return null
        val loaded = WordMedia(
            mediaId = partName,
            partName = partName,
            contentType = contentTypeFor(partName),
            data = data,
            relationshipId = relId,
        )
        media[partName] = loaded
        return loaded
    }

    // ------------------------------------------------------------------
    // Block level
    // ------------------------------------------------------------------

    private fun walkBlocks(container: Element, revision: WordRevision? = null): List<Draft> {
        val drafts = mutableListOf<Draft>()
        for (child in elementChildren(container)) {
            val tag = localName(child)
            if (tag.isEmpty()) continue
            when {
                tag == "commentRangeStart" -> openCommentRange(attr(child, NS_W, "id"))
                tag == "commentRangeEnd" -> closeCommentRange(attr(child, NS_W, "id"))
                tag in BLOCK_SKIP -> Unit
                tag == "p" -> {
                    drafts.addAll(paragraphDrafts(child, revision))
                    val section = paragraphSection(child)
                    if (section != null) {
                        sectionEnds.add(section to drafts.size)
                    }
                }

                tag == "tbl" -> drafts.add(tableDraft(child))
                tag == "sdt" -> {
                    val content = childElement(child, "sdtContent")
                    if (content == null) {
                        omit("content control without content (w:sdt)")
                    } else {
                        drafts.addAll(walkBlocks(content, revision))
                    }
                }

                tag == "ins" || tag == "moveTo" -> drafts.addAll(walkBlocks(child, revision))
                tag == "del" || tag == "moveFrom" -> {
                    // A deleted paragraph is still a paragraph: its text is kept as
                    // a deletion so that the revision survives the round trip.
                    drafts.addAll(walkBlocks(child, revision ?: revisionFor(child, REVISION_KINDS.getValue(tag))))
                }

                tag == "customXml" -> drafts.addAll(walkBlocks(child, revision))
                tag == "altChunk" || tag == "subDoc" -> {
                    anchorDraft(child, tag)?.let { drafts.add(it) }
                }

                else -> omit("unsupported body element (w:$tag)")
            }
        }
        return drafts
    }

    private fun openCommentRange(commentId: String) {
        if (commentId.isNotEmpty() && !openCommentRanges.contains(commentId)) {
            openCommentRanges.add(commentId)
        }
    }

    private fun closeCommentRange(commentId: String) {
        openCommentRanges.remove(commentId)
    }

    private fun paragraphDrafts(paragraph: Element, revision: WordRevision?): List<Draft> {
        var styleId = ""
        var numId: String? = null
        var numLevel = 0
        var hasNumPr = false

        val properties = childElement(paragraph, "pPr")
        if (properties != null) {
            val styleElement = childElement(properties, "pStyle")
            if (styleElement != null) styleId = attr(styleElement, NS_W, "val")
            val numberingRef = childElement(properties, "numPr")
            if (numberingRef != null) {
                hasNumPr = true
                val numRef = childElement(numberingRef, "numId")
                if (numRef != null) numId = attr(numRef, NS_W, "val")
                val levelRef = childElement(numberingRef, "ilvl")
                if (levelRef != null) numLevel = safeInt(attr(levelRef, NS_W, "val"), 0)
            }
        }

        if (!hasNumPr) {
            val (styleNumId, styleNumLevel) = styleNumbering(styleId)
            numId = styleNumId
            numLevel = styleNumLevel
        }

        if (styleId.isNotEmpty()) usedStyles.add(styleId)
        val styleName = effectiveStyleName(styleId)
        val marker = resolveMarker(numId, numLevel, styleName)

        val kind: WordBlockKind
        val level: Int
        val listMarker: ListMarker?
        val listGroup: String?
        var numberFormat: String? = null
        var numberText: String? = null
        if (marker != null && numId != null) {
            kind = WordBlockKind.LIST_ITEM
            level = maxOf(0, minOf(8, numLevel))
            listMarker = marker
            listGroup = numId
            numberFormat = numberFormat(numId, numLevel)
            numberText = numberLevelText(numId, numLevel)
        } else {
            val classified = styleKind(styleId)
            kind = classified.first
            level = classified.second
            listMarker = null
            listGroup = null
        }

        val drafts = mutableListOf<Draft>()
        var pending = mutableListOf<WordRun>()

        fun flush() {
            if (pending.isNotEmpty()) {
                val draft = paragraphDraft(
                    kind = kind,
                    level = level,
                    listMarker = listMarker,
                    listGroup = listGroup,
                    numberFormat = numberFormat,
                    numberText = numberText,
                    style = styleName.ifEmpty { null },
                    runs = pending.toList(),
                )
                if (draft != null) drafts.add(draft)
                pending = mutableListOf()
            }
        }

        for (segment in walkInline(paragraph, null, revision)) {
            when {
                segment.isBreak -> {
                    flush()
                    drafts.add(Draft(kind = WordBlockKind.PAGE_BREAK))
                }

                segment.blocks.isNotEmpty() -> {
                    flush()
                    drafts.addAll(segment.blocks)
                }

                else -> pending.addAll(segment.runs)
            }
        }
        flush()
        return drafts
    }

    private fun paragraphDraft(
        kind: WordBlockKind,
        level: Int,
        listMarker: ListMarker?,
        listGroup: String?,
        numberFormat: String?,
        numberText: String?,
        style: String?,
        runs: List<WordRun>,
    ): Draft? {
        val text = WordText.normalizeWhitespace(runs.filterNot { it.isDeleted }.joinToString("") { it.text })
        if (runs.isEmpty()) return null
        return Draft(
            kind = kind,
            runs = runs,
            text = text,
            level = level,
            listMarker = listMarker,
            listGroup = listGroup,
            numberFormat = numberFormat,
            numberText = numberText,
            style = style,
        )
    }

    private fun styleKind(styleId: String): Pair<WordBlockKind, Int> {
        val seen = mutableSetOf<String>()
        var current = styleId
        while (current.isNotEmpty() && seen.add(current)) {
            val info = lookupStyle(current) ?: break
            val classified = classifyStyle(info.name, info.styleId)
            if (classified != null) return classified
            current = info.basedOn
        }
        return WordBlockKind.PARAGRAPH to 0
    }

    private fun effectiveStyleName(styleId: String): String {
        val seen = mutableSetOf<String>()
        var current = styleId
        while (current.isNotEmpty() && seen.add(current)) {
            val info = lookupStyle(current) ?: return ""
            if (info.name.isNotEmpty()) return info.name
            current = info.basedOn
        }
        return ""
    }

    private fun lookupStyle(styleId: String): StyleInfo? {
        styles[styleId]?.let { return it }
        val lowered = styleId.trim().lowercase()
        if (lowered.isEmpty()) return null
        for (candidate in styles.values) {
            if (candidate.name.trim().lowercase() == lowered) return candidate
        }
        return null
    }

    private fun styleNumbering(styleId: String): Pair<String?, Int> {
        val seen = mutableSetOf<String>()
        var current = styleId
        while (current.isNotEmpty() && seen.add(current)) {
            val info = lookupStyle(current) ?: break
            if (!info.numId.isNullOrEmpty()) return info.numId to info.numLevel
            current = info.basedOn
        }
        return null to 0
    }

    private fun resolveMarker(numId: String?, level: Int, styleName: String): ListMarker? {
        if (numId.isNullOrEmpty() || numId == "0") return null
        val format = numberFormat(numId, level)
        if (format == null) {
            if (styleName.trim().lowercase().startsWith("list bullet")) return ListMarker.BULLET
            return ListMarker.NUMBER
        }
        if (format.trim().lowercase() == "bullet") return ListMarker.BULLET
        return ListMarker.NUMBER
    }

    // ------------------------------------------------------------------
    // Inline content
    // ------------------------------------------------------------------

    private fun walkInline(container: Element, hyperlink: String?, revision: WordRevision? = null): List<Segment> {
        val segments = mutableListOf<Segment>()
        for (child in elementChildren(container)) {
            val tag = localName(child)
            if (tag.isEmpty()) continue
            when {
                tag == "commentRangeStart" -> openCommentRange(attr(child, NS_W, "id"))
                tag == "commentRangeEnd" -> closeCommentRange(attr(child, NS_W, "id"))
                tag in REVISION_KINDS -> {
                    segments.addAll(
                        walkInline(child, hyperlink, revisionFor(child, REVISION_KINDS.getValue(tag))),
                    )
                }

                tag in INLINE_SKIP -> Unit
                tag == "r" -> segments.addAll(runSegments(child, hyperlink, revision))
                tag == "hyperlink" -> {
                    segments.addAll(walkInline(child, hyperlinkTarget(child), revision))
                }

                tag == "sdt" -> {
                    val content = childElement(child, "sdtContent")
                    if (content == null) {
                        omit("content control without content (w:sdt)")
                    } else {
                        segments.addAll(walkInline(content, hyperlink, revision))
                    }
                }

                tag == "txbxContent" -> segments.add(Segment(blocks = walkBlocks(child)))
                tag == "drawing" || tag == "pict" -> {
                    val (_, partRef) = drawingReference(child)
                    val blocks = drawingBlocks(child)
                    if (blocks.isNotEmpty()) segments.add(Segment(blocks = blocks))
                    if (partRef != null) {
                        segments.add(
                            Segment(
                                runs = listOf(
                                    WordRun(text = "", partRef = partRef, anchorExtent = anchorExtent(child)),
                                ),
                            ),
                        )
                    }
                }

                tag == "object" -> {
                    val partRef = objectPartRef(child)
                    if (partRef != null) {
                        segments.add(
                            Segment(
                                runs = listOf(
                                    WordRun(text = "", partRef = partRef, anchorExtent = anchorExtent(child)),
                                ),
                            ),
                        )
                    }
                }

                tag == "subDoc" || tag == "altChunk" -> {
                    anchorSegment(child, tag)?.let { segments.add(it) }
                }

                tag == "oMath" || tag == "oMathPara" -> {
                    equationSegment(child)?.let { segments.add(it) }
                }

                else -> {
                    // Any other inline container (`w:smartTag`, `w:ruby`, ...)
                    // is transparent: its children are ordinary inline content.
                    segments.addAll(walkInline(child, hyperlink, revision))
                }
            }
        }
        return segments
    }

    private fun equationSegment(element: Element): Segment? {
        val markup = serialize(element)
        if (markup == null || markup.isEmpty()) {
            omit("equation (m:oMath) carries no markup")
            return null
        }
        return Segment(runs = listOf(WordRun(text = equationText(element), equationXml = markup)))
    }

    private fun equationText(element: Element): String {
        val parts = mutableListOf<String>()
        val nodes = element.getElementsByTagNameNS(NS_M, "t")
        for (index in 0 until nodes.length) {
            parts.add(WordText.stripControlCharacters(elementText(nodes.item(index))))
        }
        return parts.joinToString("")
    }

    private fun runSegments(run: Element, hyperlink: String?, revision: WordRevision?): List<Segment> {
        val properties = childElement(run, "rPr")
        val formatting = readRunProperties(properties)
        val formatChangeXml = formatChangeXml(properties)
        val effectiveRevision = if (formatChangeXml != null && revision == null) {
            formatChangeRevision(properties)
        } else {
            revision
        }

        val segments = mutableListOf<Segment>()
        var parts = mutableListOf<String>()
        var footnoteRef: String? = null
        var endnoteRef: String? = null
        var imageRef: String? = null
        var partRef: String? = null
        var extent: Pair<Int, Int>? = null
        val commentIds = openCommentRanges.toList()

        fun flush() {
            if (parts.isNotEmpty() || footnoteRef != null || endnoteRef != null ||
                imageRef != null || partRef != null
            ) {
                segments.add(
                    Segment(
                        runs = listOf(
                            WordRun(
                                text = parts.joinToString(""),
                                bold = formatting.bold,
                                italic = formatting.italic,
                                underline = formatting.underline,
                                superscript = formatting.superscript,
                                subscript = formatting.subscript,
                                strike = formatting.strike,
                                font = formatting.font,
                                size = formatting.size,
                                color = formatting.color,
                                highlight = formatting.highlight,
                                hyperlink = hyperlink,
                                footnoteRef = footnoteRef,
                                endnoteRef = endnoteRef,
                                imageRef = imageRef,
                                commentRefs = commentIds,
                                revision = effectiveRevision,
                                partRef = partRef,
                                anchorExtent = extent,
                                formatChangeXml = formatChangeXml,
                            ),
                        ),
                    ),
                )
            }
            parts = mutableListOf()
            footnoteRef = null
            endnoteRef = null
            imageRef = null
            partRef = null
            extent = null
        }

        for (child in elementChildren(run)) {
            val tag = localName(child)
            if (tag.isEmpty() || tag == "rPr") continue
            when (tag) {
                "t" -> parts.add(WordText.stripControlCharacters(directText(child)))
                "delText" -> {
                    // The text of a deleted run lives in `w:delText`. It is kept
                    // -- the revision is part of the document -- but it is not part
                    // of the document's final text.
                    parts.add(WordText.stripControlCharacters(directText(child)))
                }

                "tab" -> parts.add(" ")
                "br" -> {
                    if (attr(child, NS_W, "type").trim().lowercase() == "page") {
                        flush()
                        segments.add(Segment(isBreak = true))
                    } else {
                        parts.add(" ")
                    }
                }

                "cr" -> parts.add(" ")
                "noBreakHyphen" -> parts.add("-")
                "softHyphen" -> parts.add(" ")
                "ptab" -> parts.add(" ")
                "footnoteReference" -> {
                    val reference = attr(child, NS_W, "id")
                    if (reference.isEmpty()) continue
                    if (footnoteRuns.containsKey(reference)) {
                        footnoteRef = reference
                    } else {
                        omit("footnote reference to unknown footnote id $reference")
                    }
                }

                "endnoteReference" -> {
                    val reference = attr(child, NS_W, "id")
                    if (reference.isEmpty()) continue
                    if (endnoteRuns.containsKey(reference)) {
                        endnoteRef = reference
                    } else {
                        omit("endnote reference to unknown endnote id $reference")
                    }
                }

                "oMath", "oMathPara" -> {
                    // Word keeps an equation beside the runs of a paragraph, but a
                    // producer may nest one inside a run. It must not be lost
                    // there either, so it becomes a segment of its own.
                    flush()
                    equationSegment(child)?.let { segments.add(it) }
                }

                "drawing", "pict" -> {
                    val (image, part) = drawingReference(child)
                    imageRef = image
                    partRef = part
                    extent = anchorExtent(child)
                    val blocks = drawingBlocks(child)
                    if (blocks.isNotEmpty()) {
                        flush()
                        segments.add(Segment(blocks = blocks))
                    }
                }

                "object" -> {
                    partRef = objectPartRef(child)
                    extent = anchorExtent(child)
                }

                "commentReference" -> Unit
                "sym" -> appendSymbol(child, parts)
                "instrText", "delInstrText", "fldChar", "lastRenderedPageBreak" -> Unit
            }
        }
        flush()
        return segments
    }

    private fun appendSymbol(element: Element, parts: MutableList<String>) {
        val font = attr(element, NS_W, "font").trim()
        val rawCode = attr(element, NS_W, "char").trim()
        val code = symbolCode(rawCode)
        val table = SYMBOL_CHARS[font.lowercase()]
        val character = if (table != null && code != null) table[code] else null
        if (character == null) {
            omit(
                "symbol (w:sym) ${font.ifEmpty { "unknown font" }} " +
                    "${rawCode.ifEmpty { "no code" }} has no known character",
            )
            return
        }
        parts.add(character)
    }

    private fun hyperlinkTarget(hyperlink: Element): String? {
        val relId = attr(hyperlink, NS_R, "id")
        if (relId.isNotEmpty()) {
            val relationship = relationships[relId]
            if (relationship == null) {
                omit("hyperlink relationship $relId is missing")
                return null
            }
            return resolveTarget(relationship.target).ifEmpty { null }
        }
        val anchor = attr(hyperlink, NS_W, "anchor")
        if (anchor.isNotEmpty()) return "#$anchor"
        return null
    }

    private fun resolveTarget(target: String): String {
        if (target.isEmpty()) return ""
        if (hasUriScheme(target)) return target
        val base = baseUri
        if (base.isNullOrEmpty()) {
            omit("relative hyperlink target ($target)")
            return target
        }
        return urlJoin(base, target)
    }

    // ------------------------------------------------------------------
    // Drawings
    // ------------------------------------------------------------------

    private fun drawingReference(node: Element): Pair<String?, String?> {
        val relId = graphicDataRelationship(node)
        if (relId.isNotEmpty()) {
            return null to partReference(relId, "drawing")
        }
        return imageReference(node) to null
    }

    private fun anchorExtent(node: Element): Pair<Int, Int>? {
        for (element in descendants(node)) {
            if (localName(element) != "inline" && localName(element) != "anchor") continue
            if (element.namespaceURI != NS_WP) continue
            val extent = childElementByNamespace(element, NS_WP, "extent") ?: continue
            val size = emuSize(plainAttribute(extent, "cx"), plainAttribute(extent, "cy"))
            if (size != null) return size
        }
        for (element in descendants(node)) {
            if (localName(element) != "shape") continue
            if (element.namespaceURI != NS_V) continue
            val size = vmlSize(plainAttribute(element, "style"))
            if (size != null) return size
        }
        return null
    }

    private fun graphicDataRelationship(node: Element): String {
        var found = ""
        var bestRank = -1
        for (element in descendants(node)) {
            if (localName(element) != "graphicData") continue
            val uri = plainAttribute(element, "uri")
            if (uri.endsWith("/picture")) continue
            val relId = graphicRelationship(element)
            if (relId.isEmpty()) continue
            val rank = alternateBranchRank(element)
            if (rank > bestRank) {
                bestRank = rank
                found = relId
            }
        }
        return found
    }

    private fun graphicRelationship(element: Element): String {
        for (attribute in GRAPHIC_RELATIONSHIP_ATTRIBUTES) {
            val value = attr(element, NS_R, attribute)
            if (value.isNotEmpty()) return value
        }
        for (child in descendants(element)) {
            for (attribute in GRAPHIC_RELATIONSHIP_ATTRIBUTES) {
                val value = attr(child, NS_R, attribute)
                if (value.isNotEmpty()) return value
            }
        }
        return ""
    }

    private fun imageRelationship(node: Element): String {
        for (element in descendants(node)) {
            val tag = localName(element)
            if (tag != "blip" && tag != "imagedata") continue
            val relId = attr(element, NS_R, "embed").ifEmpty { attr(element, NS_R, "id") }
            if (relId.isNotEmpty()) return relId
        }
        return ""
    }

    private fun imageReference(node: Element): String? {
        val relId = imageRelationship(node)
        if (relId.isEmpty()) {
            reportImage("unknown", "unknown")
            return null
        }
        val relationship = relationships[relId]
        if (relationship == null) {
            reportImage(relId, "unknown")
            return null
        }
        val partName = partNameFor(relationship.target)
        val loaded = loadMedia(partName, relId)
        if (loaded == null) {
            reportImage(relId, partName)
            return null
        }
        return loaded.mediaId
    }

    private fun reportImage(relId: String, partName: String) {
        omit("image (rel $relId, part $partName)")
    }

    private fun objectPartRef(element: Element): String? {
        val relId = objectRelationship(element)
        if (relId.isEmpty()) {
            omit("embedded object (w:object) carries no relationship")
            return null
        }
        return partReference(relId, "embedded object (w:object)")
    }

    private fun objectRelationship(node: Element): String {
        var fallback = ""
        for (element in descendants(node)) {
            val relId = attr(element, NS_R, "id")
                .ifEmpty { attr(element, NS_R, "embed") }
                .ifEmpty { attr(element, NS_R, "link") }
            if (relId.isEmpty()) continue
            if (localName(element) in OLE_RELATIONSHIP_ELEMENTS) return relId
            if (fallback.isEmpty()) fallback = relId
        }
        return fallback
    }

    private fun anchorSegment(element: Element, tag: String): Segment? {
        val partRef = anchorPartRef(element, tag) ?: return null
        return Segment(runs = listOf(WordRun(text = "", partRef = partRef)))
    }

    private fun anchorDraft(element: Element, tag: String): Draft? {
        val partRef = anchorPartRef(element, tag) ?: return null
        return Draft(kind = WordBlockKind.PARAGRAPH, runs = listOf(WordRun(text = "", partRef = partRef)), text = "")
    }

    private fun anchorPartRef(element: Element, tag: String): String? {
        val relId = attr(element, NS_R, "id")
        val description = "$tag reference (w:$tag)"
        if (relId.isEmpty()) {
            omit("$description carries no relationship")
            return null
        }
        return partReference(relId, description)
    }

    private fun drawingBlocks(node: Element): List<Draft> {
        val drafts = mutableListOf<Draft>()
        for (textBox in selectTextBoxes(node)) {
            drafts.addAll(walkBlocks(textBox))
        }
        return drafts
    }

    private fun selectTextBoxes(node: Element): List<Element> {
        val candidates = descendants(node).filter { localName(it) == "txbxContent" }
        if (candidates.isEmpty()) return emptyList()
        val groups = linkedMapOf<String, MutableList<Element>>()
        val ranks = mutableMapOf<String, Int>()
        for (element in candidates) {
            val ancestor = alternateContentAncestor(element)
            val key = System.identityHashCode(ancestor).toString()
            val rank = alternateBranchRank(element)
            val existing = groups[key]
            if (existing == null || rank > ranks.getOrDefault(key, -1)) {
                groups[key] = mutableListOf(element)
                ranks[key] = rank
            } else {
                existing.add(element)
            }
        }
        return groups.values.flatten()
    }

    private fun alternateContentAncestor(element: Element): Element? {
        var child: Node = element
        var parent = element.parentNode
        while (parent is Element) {
            if (localName(parent) == "AlternateContent") return parent
            child = parent
            parent = parent.parentNode
        }
        return null
    }

    private fun alternateBranchRank(element: Element): Int {
        val parent = alternateContentAncestor(element) ?: return 0
        var child: Node = element
        for (branch in elementChildren(parent)) {
            val name = localName(branch)
            if ((name == "Choice" || name == "Fallback") && branch === child) {
                return if (name == "Choice") 2 else 1
            }
        }
        return 0
    }

    // ------------------------------------------------------------------
    // Tables
    // ------------------------------------------------------------------

    private fun tableDraft(table: Element): Draft {
        val rowElements = liveRows(table)
        val resolved = resolveTableRows(rowElements, tableGridColumns(table))

        val rows = mutableListOf<WordTableRow>()
        var headerRows = 0
        for (rowIndex in rowElements.indices) {
            val rowElement = rowElements[rowIndex]
            val isHeader = rowIsHeader(rowElement)
            if (isHeader) headerRows++
            val cells = resolved[rowIndex].layout.mapIndexed { cellIndex, layout ->
                tableCell(layout, isHeader, resolved[rowIndex].states[cellIndex])
            }
            rows.add(WordTableRow(cells = cells))
        }
        return Draft(
            kind = WordBlockKind.TABLE,
            table = WordTable(
                rows = rows,
                headerRows = headerRows,
                columnWidths = tableColumnWidths(table),
            ),
        )
    }

    private fun tableColumnWidths(table: Element): List<Int> {
        val grid = childElement(table, "tblGrid") ?: return emptyList()
        val widths = mutableListOf<Int>()
        for (column in childElements(grid, "gridCol")) {
            val width = safeInt(attr(column, NS_W, "w"), 0)
            if (width > 0) widths.add(width)
        }
        return widths
    }

    private fun liveRows(table: Element): List<Element> =
        tableRows(table).filterNot { rowIsDeleted(it) }

    private fun tableRows(table: Element): List<Element> {
        val rows = mutableListOf<Element>()
        fun scan(node: Element) {
            for (child in elementChildren(node)) {
                when (localName(child)) {
                    "tr" -> rows.add(child)
                    "tbl" -> Unit
                    "sdt" -> childElement(child, "sdtContent")?.let { scan(it) }
                    "ins", "moveTo", "customXml" -> scan(child)
                }
            }
        }
        scan(table)
        return rows
    }

    private fun rowCells(row: Element): List<Element> {
        val cells = mutableListOf<Element>()
        fun scan(node: Element) {
            for (child in elementChildren(node)) {
                when (localName(child)) {
                    "tc" -> cells.add(child)
                    "sdt" -> childElement(child, "sdtContent")?.let { scan(it) }
                    "ins", "moveTo", "customXml" -> scan(child)
                }
            }
        }
        scan(row)
        return cells
    }

    private fun rowIsHeader(row: Element): Boolean {
        val properties = childElement(row, "trPr") ?: return false
        return childElement(properties, "tblHeader") != null
    }

    private fun rowIsDeleted(row: Element): Boolean {
        val properties = childElement(row, "trPr") ?: return false
        return childElement(properties, "del") != null
    }

    /** The number of grid columns the table declares. */
    private fun tableGridColumns(table: Element): Int {
        val grid = childElement(table, "tblGrid") ?: return 0
        return childElements(grid, "gridCol").size
    }

    /**
     * Lay the rows of a table out on the table's grid and resolve the vertical
     * merges they carry.
     *
     * A table's grid is as wide as its widest row: the cells of a row tile it
     * from left to right, and a row that falls short of it is a malformed table
     * (W4, W12).
     *
     * A vertical merge occupies *every* grid column its origin spans, and the
     * cell that continues the merge is that region: it covers the same
     * columns, so the cells that follow it start after the merged region and
     * keep their own text. A continuation takes only the columns the row has
     * room for -- a row whose other cells already fill the grid leaves the
     * continuation the width it declares -- and a cell that then lands inside
     * the region belongs to the merge and carries no text of its own.
     */
    private fun resolveTableRows(rows: List<Element>, declaredColumns: Int): List<ResolvedRow> {
        val natural = rows.map { row -> rowCells(row).sumOf { cellGridSpan(it) } }
        val gridWidth = maxOf(declaredColumns, natural.maxOrNull() ?: 0)
        val resolved = mutableListOf<ResolvedRow>()
        val openMerges = mutableListOf<OpenMerge>()
        val merges = mutableListOf<OpenMerge>()
        for (rowIndex in rows.indices) {
            val cells = rowCells(rows[rowIndex])
            val fixed = cells.filter { cellMergeState(it) != MERGE_CONTINUE }.sumOf { cellGridSpan(it) }
            var room = gridWidth - fixed
            val layout = mutableListOf<CellLayout>()
            val states = mutableMapOf<Int, MergeState>()
            val continued = mutableSetOf<OpenMerge>()
            var cursor = 0
            for ((cellIndex, cell) in cells.withIndex()) {
                val own = cellGridSpan(cell)
                val mergeState = cellMergeState(cell)
                val merge = if (mergeState == MERGE_CONTINUE) {
                    openMerges.firstOrNull { it.covers(cursor) && it !in continued }
                } else {
                    null
                }
                if (merge != null) {
                    // The cell continues the merge: it *is* the merged region in
                    // this row, so it covers every column the origin spans.
                    val span = maxOf(own, minOf(merge.columnSpan, room))
                    room -= span
                    continued.add(merge)
                    merge.rowSpan++
                    states[cellIndex] = MergeState(isContinuation = true)
                    layout.add(CellLayout(cell, merge.startColumn, span))
                    cursor = merge.startColumn + span
                    continue
                }
                val column = cursor
                layout.add(CellLayout(cell, column, own))
                cursor += own
                if (mergeState == MERGE_RESTART) {
                    // A new merge replaces every merge it overlaps.
                    openMerges.removeAll { it.overlaps(column, own) }
                    val origin = OpenMerge(
                        originKey = rowIndex to cellIndex,
                        originRow = rowIndex,
                        startColumn = column,
                        columnSpan = own,
                    )
                    merges.add(origin)
                    openMerges.add(origin)
                    states[cellIndex] = MergeState(rowSpan = 1)
                } else if (mergeState == MERGE_CONTINUE) {
                    // A continuation without an origin above it belongs to no
                    // merge, so it stays an ordinary cell.
                    room -= own
                }
            }
            // A cell that starts at a grid column the merge already covers
            // belongs to the merged region even when it carries no
            // `w:vMerge` of its own: the merge covers that grid position,
            // so Word never displays the cell's text.
            for (cellIndex in layout.indices) {
                if (states.containsKey(cellIndex)) continue
                val covering = openMerges.firstOrNull { it.covers(layout[cellIndex].gridColumn) }
                if (covering != null && covering in continued) {
                    states[cellIndex] = MergeState(isContinuation = true)
                }
            }
            // A merge that no cell continues in this row is closed.
            openMerges.removeAll { it.originRow != rowIndex && it !in continued }
            resolved.add(ResolvedRow(layout, states))
        }
        for (merge in merges) {
            val origin = resolved[merge.originKey.first]
            origin.states[merge.originKey.second] = MergeState(rowSpan = merge.rowSpan)
        }
        return resolved
    }

    private fun cellGridSpan(cell: Element): Int {
        val properties = childElement(cell, "tcPr") ?: return 1
        val gridSpan = childElement(properties, "gridSpan") ?: return 1
        return maxOf(1, safeInt(attr(gridSpan, NS_W, "val"), 1))
    }

    private fun cellMergeState(cell: Element): String {
        val properties = childElement(cell, "tcPr") ?: return ""
        val merge = childElement(properties, "vMerge") ?: return ""
        val value = attr(merge, NS_W, "val").trim().lowercase()
        if (value == MERGE_RESTART) return MERGE_RESTART
        return MERGE_CONTINUE
    }

    /**
     * Build one table cell.
     *
     * A vertical merge is represented the way Word represents it: the cell that
     * starts the merge keeps its text and reports the number of rows the merge
     * occupies, and the cells below it are continuation cells that carry no text
     * of their own, because Word ignores the content stored in a continuation
     * cell.
     */
    private fun tableCell(layout: CellLayout, isHeader: Boolean, mergeState: MergeState?): WordTableCell {
        val state = mergeState ?: MergeState()
        if (state.isContinuation) {
            return WordTableCell(
                text = "",
                rowSpan = 1,
                colSpan = layout.colSpan,
                isHeader = isHeader,
                isMergeContinuation = true,
            )
        }
        return WordTableCell(
            text = cellText(layout.element),
            rowSpan = state.rowSpan,
            colSpan = layout.colSpan,
            isHeader = isHeader,
            isMergeContinuation = false,
            width = cellWidth(layout.element),
            runs = cellRuns(layout.element),
            tables = nestedTables(layout.element),
        )
    }

    private fun cellWidth(cell: Element): Int? {
        val properties = childElement(cell, "tcPr") ?: return null
        val width = childElement(properties, "tcW") ?: return null
        val raw = attr(width, NS_W, "w").trim()
        if (raw.isEmpty() || !raw.all { it.isDigit() }) return null
        return raw.toInt().takeIf { it > 0 }
    }

    private fun cellRuns(cell: Element): List<WordRun> {
        val collected = mutableListOf<WordRun>()
        for (child in elementChildren(cell)) {
            when (localName(child)) {
                "p" -> {
                    for (segment in walkInline(child, null)) {
                        if (segment.isBreak || segment.blocks.isNotEmpty()) continue
                        collected.addAll(segment.runs)
                    }
                }

                "sdt" -> {
                    val content = childElement(child, "sdtContent")
                    if (content == null) {
                        omit("content control without content (w:sdt)")
                        continue
                    }
                    collected.addAll(cellRuns(content))
                }
            }
        }
        return collected
    }

    private fun nestedTables(cell: Element): List<WordTable> {
        val tables = mutableListOf<WordTable>()
        for (child in elementChildren(cell)) {
            when (localName(child)) {
                "tbl" -> {
                    val draft = tableDraft(child)
                    draft.table?.let { tables.add(it) }
                }

                "sdt" -> {
                    val content = childElement(child, "sdtContent")
                    if (content == null) {
                        omit("content control without content (w:sdt)")
                        continue
                    }
                    tables.addAll(nestedTables(content))
                }
            }
        }
        return tables
    }

    private fun cellText(cell: Element): String =
        WordText.normalizeWhitespace(cellParts(cell).filter { it.isNotEmpty() }.joinToString(" "))

    private fun cellParts(cell: Element): List<String> {
        val parts = mutableListOf<String>()
        for (child in elementChildren(cell)) {
            when (localName(child)) {
                "p" -> {
                    val text = paragraphText(child)
                    if (text.isNotEmpty()) parts.add(text)
                }

                "tbl" -> {
                    val text = nestedTableText(child)
                    if (text.isNotEmpty()) parts.add(text)
                }

                "sdt" -> {
                    val content = childElement(child, "sdtContent")
                    if (content == null) {
                        omit("content control without content (w:sdt)")
                        continue
                    }
                    parts.addAll(cellParts(content))
                }

                "ins", "moveTo", "customXml" -> parts.addAll(cellParts(child))
                "del", "moveFrom" -> Unit
            }
        }
        return parts
    }

    private fun nestedTableText(table: Element): String {
        val draft = tableDraft(table)
        val payload = draft.table ?: return ""
        val rows = payload.rows.map { row -> row.cells.joinToString(" | ") { it.text } }
        return WordText.normalizeWhitespace(rows.joinToString(" "))
    }

    private fun paragraphText(paragraph: Element): String {
        val parts = mutableListOf<String>()
        for (segment in walkInline(paragraph, null)) {
            if (segment.isBreak) continue
            if (segment.blocks.isNotEmpty()) {
                parts.addAll(segment.blocks.map { it.text })
            } else {
                parts.add(segment.runs.joinToString("") { it.text })
            }
        }
        return WordText.normalizeWhitespace(parts.joinToString(" "))
    }

    // ------------------------------------------------------------------
    // Revisions
    // ------------------------------------------------------------------

    private fun revisionFor(element: Element, kind: WordRevisionKind): WordRevision = WordRevision(
        kind = kind,
        author = attr(element, NS_W, "author"),
        date = readTimestampValue(attr(element, NS_W, "date").trim(), "revision"),
        revisionId = attr(element, NS_W, "id"),
    )

    private fun formatChangeRevision(properties: Element?): WordRevision? {
        if (properties == null) return null
        val change = childElement(properties, "rPrChange") ?: return null
        return WordRevision(
            kind = WordRevisionKind.FORMAT_CHANGE,
            author = attr(change, NS_W, "author"),
            date = readTimestampValue(attr(change, NS_W, "date").trim(), "revision"),
            revisionId = attr(change, NS_W, "id"),
        )
    }

    private fun formatChangeXml(properties: Element?): String? {
        if (properties == null) return null
        val change = childElement(properties, "rPrChange") ?: return null
        val markup = serialize(change)
        if (markup == null) {
            omit("tracked formatting revision (w:rPrChange) could not be serialised")
            return null
        }
        return markup.ifEmpty { null }
    }

    // ------------------------------------------------------------------
    // Assembly
    // ------------------------------------------------------------------

    private fun buildBlocks(drafts: List<Draft>): List<WordBlock> =
        drafts.mapIndexed { index, draft ->
            WordBlock(
                blockId = "b%04d".format(index + 1),
                kind = draft.kind,
                text = draft.text,
                level = draft.level,
                listMarker = draft.listMarker,
                listGroup = draft.listGroup,
                numberFormat = draft.numberFormat,
                numberText = draft.numberText,
                style = draft.style,
                runs = draft.runs,
                table = draft.table,
            )
        }

    private fun buildFootnotes(): List<WordFootnote> =
        footnoteRuns.keys.sortedWith(numericOrder).map { footnoteId ->
            val runs = footnoteRuns.getValue(footnoteId)
            WordFootnote(
                footnoteId = footnoteId,
                text = WordText.normalizeWhitespace(runs.filterNot { it.isDeleted }.joinToString("") { it.text }),
                runs = runs,
            )
        }

    private fun buildEndnotes(): List<WordEndnote> =
        endnoteRuns.keys.sortedWith(numericOrder).map { endnoteId ->
            val runs = endnoteRuns.getValue(endnoteId)
            WordEndnote(
                endnoteId = endnoteId,
                text = WordText.normalizeWhitespace(runs.filterNot { it.isDeleted }.joinToString("") { it.text }),
                runs = runs,
            )
        }

    private fun buildComments(blocks: List<WordBlock>): List<WordComment> {
        val anchors = mutableMapOf<String, Pair<String?, MutableList<String>>>()
        for (block in blocks) {
            for (run in block.runs) {
                for (commentId in run.commentRefs) {
                    val existing = anchors.getOrPut(commentId) { null to mutableListOf() }
                    val blockId = existing.first ?: block.blockId
                    existing.second.add(run.text)
                    anchors[commentId] = blockId to existing.second
                }
            }
        }
        return commentRuns.keys.sortedWith(numericOrder).map { commentId ->
            val runs = commentRuns.getValue(commentId)
            val meta = commentMeta[commentId] ?: CommentMeta("", "", null)
            val anchor = anchors[commentId]
            WordComment(
                commentId = commentId,
                author = meta.author,
                initials = meta.initials,
                date = meta.date,
                text = WordText.normalizeWhitespace(runs.filterNot { it.isDeleted }.joinToString("") { it.text }),
                runs = runs,
                anchorBlockId = anchor?.first,
                anchorText = WordText.normalizeWhitespace(anchor?.second?.joinToString("") ?: ""),
            )
        }
    }

    /**
     * Turn the styles the document uses into model styles.
     *
     * Only the styles a paragraph actually uses are carried, together with the
     * chain they are based on: the writer has to be able to rebuild the chain,
     * and an unused style is not worth carrying.
     */
    private fun buildStyles(): List<WordStyle> {
        val wanted = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        for (styleId in usedStyles.sorted()) {
            var current = styleId
            while (current.isNotEmpty() && seen.add(current)) {
                wanted.add(current)
                val info = lookupStyle(current) ?: break
                current = info.basedOn
            }
        }
        return wanted.mapNotNull { styleId ->
            val info = lookupStyle(styleId) ?: return@mapNotNull null
            WordStyle(
                styleId = info.styleId,
                name = info.name,
                basedOn = info.basedOn,
                styleType = "paragraph",
                numId = info.numId,
                numLevel = info.numLevel,
            )
        }
    }

    // ------------------------------------------------------------------
    // Omission register
    // ------------------------------------------------------------------

    private fun omit(description: String) {
        omissionCounts[description] = (omissionCounts[description] ?: 0) + 1
        lastOmissions = omissionCounts.entries
            .sortedBy { it.key }
            .map { (description, count) -> if (count == 1) description else "$description x$count" }
    }

    // ------------------------------------------------------------------
    // Internal types
    // ------------------------------------------------------------------

    private data class StyleInfo(
        val styleId: String,
        val name: String,
        val basedOn: String,
        val numId: String?,
        val numLevel: Int,
        val runProps: RunProps,
    )

    private data class RunProps(
        val bold: Boolean = false,
        val italic: Boolean = false,
        val underline: Boolean = false,
        val superscript: Boolean = false,
        val subscript: Boolean = false,
        val strike: Boolean = false,
        val font: String? = null,
        val size: Int? = null,
        val color: String? = null,
        val highlight: String? = null,
    )

    private data class NumberingInfo(
        val numToAbstract: Map<String, String>,
        val abstractLevels: Map<String, Map<Int, String>>,
        val abstractLevelTexts: Map<String, Map<Int, String>>,
        val overrides: Map<String, Map<Int, String>>,
        val overrideLevelTexts: Map<String, Map<Int, String>>,
    )

    private data class Relationship(
        val relId: String,
        val target: String,
        val relType: String,
        val isExternal: Boolean,
    )

    private data class CommentMeta(
        val author: String,
        val initials: String,
        val date: java.time.OffsetDateTime?,
    )

    private data class Draft(
        val kind: WordBlockKind,
        val runs: List<WordRun> = emptyList(),
        val text: String = "",
        val level: Int = 0,
        val listMarker: ListMarker? = null,
        val listGroup: String? = null,
        val numberFormat: String? = null,
        val numberText: String? = null,
        val style: String? = null,
        val table: WordTable? = null,
    )

    private data class Segment(
        val runs: List<WordRun> = emptyList(),
        val isBreak: Boolean = false,
        val blocks: List<Draft> = emptyList(),
    )

    private data class CellLayout(
        val element: Element,
        val gridColumn: Int,
        val colSpan: Int,
    )

    /** One row of a table: where its cells sit on the grid, and their merge states. */
    private class ResolvedRow(
        val layout: List<CellLayout>,
        val states: MutableMap<Int, MergeState>,
    )

    private data class MergeState(
        val rowSpan: Int = 1,
        val isContinuation: Boolean = false,
    )

    private class OpenMerge(
        val originKey: Pair<Int, Int>,
        val originRow: Int,
        val startColumn: Int,
        val columnSpan: Int,
    ) {
        var rowSpan: Int = 1

        fun covers(column: Int): Boolean = column in startColumn until (startColumn + columnSpan)

        fun overlaps(column: Int, span: Int): Boolean =
            column < startColumn + columnSpan && startColumn < column + span
    }

    companion object {
        /** The namespace of the WordprocessingML elements. */
        const val NS_W: String = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        /** The namespace of the office document relationships. */
        const val NS_R: String = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

        /** The namespace of the package relationships. */
        const val NS_PR: String = "http://schemas.openxmlformats.org/package/2006/relationships"

        /** The namespace of the core document properties. */
        const val NS_CP: String = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

        /** The namespace of the Dublin Core properties. */
        const val NS_DC: String = "http://purl.org/dc/elements/1.1/"

        /** The namespace of the Dublin Core terms. */
        const val NS_DCTERMS: String = "http://purl.org/dc/terms/"

        /** The namespace of the office math markup. */
        const val NS_M: String = "http://schemas.openxmlformats.org/officeDocument/2006/math"

        /** The namespace of the anchors DrawingML wraps a drawing in. */
        const val NS_WP: String = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

        /** The namespace of the shapes VML draws a legacy graphic with. */
        const val NS_V: String = "urn:schemas-microsoft-com:vml"

        private const val DOCUMENT_PART = "word/document.xml"
        private const val STYLES_PART = "word/styles.xml"
        private const val NUMBERING_PART = "word/numbering.xml"
        private const val DOCUMENT_RELS_PART = "word/_rels/document.xml.rels"
        private const val PACKAGE_RELS_PART = "_rels/.rels"
        private const val CONTENT_TYPES_PART = "[Content_Types].xml"
        private const val FOOTNOTES_PART = "word/footnotes.xml"
        private const val ENDNOTES_PART = "word/endnotes.xml"
        private const val COMMENTS_PART = "word/comments.xml"
        private const val CORE_PROPERTIES_PART = "docProps/core.xml"

        /** Guard rails against a decompression bomb disguised as a `.docx` package. */
        const val MAX_PACKAGE_MEMBERS: Int = 2048
        const val MAX_TOTAL_UNCOMPRESSED_BYTES: Long = 256L * 1024 * 1024

        // The size and the ratio a single package member may have are the
        // shared bounds in `lmeditor.core.DecompressionLimits`: a package part
        // and a PDF content stream are the same threat, so they are refused by
        // the same numbers.

        private const val READ_CHUNK_BYTES = 64 * 1024

        private val DEFAULT_CONTENT_TYPES = mapOf(
            "png" to "image/png",
            "jpeg" to "image/jpeg",
            "jpg" to "image/jpeg",
            "gif" to "image/gif",
            "bmp" to "image/bmp",
            "tif" to "image/tiff",
            "tiff" to "image/tiff",
            "emf" to "image/x-emf",
            "wmf" to "image/x-wmf",
            "svg" to "image/svg+xml",
        )

        /** Package parts that carry no content the model cannot represent. */
        val IGNORED_PARTS: Set<String> = setOf(
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/styles.xml",
            "word/numbering.xml",
            "word/settings.xml",
            "word/fontTable.xml",
            "word/webSettings.xml",
            "word/footnotes.xml",
            "word/endnotes.xml",
            "word/comments.xml",
            "docProps/core.xml",
            "docProps/app.xml",
        )

        /** Inline elements that carry no readable content. */
        val INLINE_SKIP: Set<String> = setOf(
            "pPr",
            "rPr",
            "fldSimple",
            "bookmarkStart",
            "bookmarkEnd",
            "proofErr",
            "permStart",
            "permEnd",
            "lastRenderedPageBreak",
            "tbl",
            "instrText",
            "delInstrText",
            "fldChar",
            "separator",
            "continuationSeparator",
            "endnoteRef",
            "sectPr",
        )

        /** Block-level elements that carry no readable content. */
        val BLOCK_SKIP: Set<String> = setOf(
            "bookmarkStart",
            "bookmarkEnd",
            "sectPr",
            "proofErr",
            "permStart",
            "permEnd",
            "pPr",
        )

        /** The tracked-revision elements and the revision kind each one records. */
        val REVISION_KINDS: Map<String, WordRevisionKind> = mapOf(
            "ins" to WordRevisionKind.INSERTION,
            "del" to WordRevisionKind.DELETION,
            "moveFrom" to WordRevisionKind.MOVE_FROM,
            "moveTo" to WordRevisionKind.MOVE_TO,
        )

        private const val MERGE_RESTART = "restart"
        private const val MERGE_CONTINUE = "continue"

        private val SEPARATOR_TYPES = setOf("separator", "continuationseparator", "continuationnotice")

        private val GRAPHIC_RELATIONSHIP_ATTRIBUTES = listOf("id", "embed", "link")

        private val OLE_RELATIONSHIP_ELEMENTS = setOf("oleObject", "package", "control", "objectEmbed", "objectLink")
    }
}

// ---------------------------------------------------------------------------
// Private helpers
//
// These are the XML, path and arithmetic helpers the reader needs. They live
// beside the reader because they are part of the same cohesive group and are
// not part of the module's public surface.
// ---------------------------------------------------------------------------

private val SYMBOL_CHARS: Map<String, Map<String, String>> = WordSymbols.CHARS

private fun localName(node: Node): String {
    val local = node.localName ?: node.nodeName ?: ""
    return if (local.contains(':')) local.substringAfterLast(':') else local
}

private fun elementChildren(node: Node): List<Element> {
    val children = node.childNodes
    val collected = ArrayList<Element>()
    for (index in 0 until children.length) {
        val child = children.item(index)
        if (child is Element) collected.add(child)
    }
    return collected
}

private fun descendants(node: Node): List<Element> {
    val collected = ArrayList<Element>()
    val holder = node as? Element
    val nodes = holder?.getElementsByTagName("*") ?: (node as? org.w3c.dom.Document)?.getElementsByTagName("*")
    if (nodes == null) return collected
    for (index in 0 until nodes.length) {
        val child = nodes.item(index)
        if (child is Element) collected.add(child)
    }
    return collected
}

private fun childElement(node: Node, name: String): Element? =
    elementChildren(node).firstOrNull { it.localName == name && it.namespaceURI == WordReader.NS_W }

private fun childElements(node: Node, name: String): List<Element> =
    elementChildren(node).filter { it.localName == name && it.namespaceURI == WordReader.NS_W }

private fun childElementByNamespace(node: Node, namespace: String, name: String): Element? =
    elementChildren(node).firstOrNull { it.localName == name && it.namespaceURI == namespace }

private fun attr(node: Node, namespace: String, name: String): String {
    val attributes = node.attributes ?: return ""
    attributes.getNamedItemNS(namespace, name)?.let { return it.nodeValue ?: "" }
    val prefixed = attributes.getNamedItem("w:$name")
    if (prefixed != null) return prefixed.nodeValue ?: ""
    return ""
}

private fun plainAttribute(node: Node, name: String): String {
    val attributes = node.attributes ?: return ""
    attributes.getNamedItem(name)?.let { return it.nodeValue ?: "" }
    return ""
}

private fun elementText(node: Node?): String {
    if (node == null) return ""
    return node.textContent?.trim() ?: ""
}

private fun directText(node: Node): String {
    val children = node.childNodes
    val builder = StringBuilder()
    for (index in 0 until children.length) {
        val child = children.item(index)
        if (child.nodeType == Node.TEXT_NODE || child.nodeType == Node.CDATA_SECTION_NODE) {
            builder.append(child.nodeValue ?: "")
        }
    }
    return builder.toString()
}

private fun safeInt(value: String?, default: Int): Int {
    if (value == null) return default
    return value.trim().toIntOrNull() ?: default
}

private fun onOff(element: Element?): Boolean {
    if (element == null) return false
    val attributes = element.attributes ?: return true
    val value = attributes.getNamedItemNS(WordReader.NS_W, "val")
        ?: attributes.getNamedItem("w:val")
    if (value == null) return true
    val text = (value.nodeValue ?: "").trim().lowercase()
    return text == "1" || text == "true" || text == "on"
}

private fun underline(element: Element?): Boolean {
    if (element == null) return false
    val attributes = element.attributes ?: return true
    val value = attributes.getNamedItemNS(WordReader.NS_W, "val")
        ?: attributes.getNamedItem("w:val")
    if (value == null) return true
    val text = (value.nodeValue ?: "").trim().lowercase()
    return text != "none" && text != "0" && text != "false"
}

private fun valAttribute(element: Element?): String {
    if (element == null) return ""
    return attr(element, WordReader.NS_W, "val").trim()
}

private fun serialize(element: Element): String? = try {
    val transformer = javax.xml.transform.TransformerFactory.newInstance().newTransformer()
    transformer.setOutputProperty(javax.xml.transform.OutputKeys.OMIT_XML_DECLARATION, "yes")
    transformer.setOutputProperty(javax.xml.transform.OutputKeys.INDENT, "no")
    val writer = java.io.StringWriter()
    transformer.transform(javax.xml.transform.dom.DOMSource(element), javax.xml.transform.stream.StreamResult(writer))
    writer.toString()
} catch (_: Exception) {
    null
}

/**
 * Resolve a relationship target to the package part it names.
 *
 * A target is written relative to the part that holds the relationship, so a
 * target that climbs out of `word/` names a part of the package root; the name
 * is normalised so that the same part is always spelled the same way.
 */
private fun packagePartName(target: String): String {
    val name = when {
        target.isEmpty() -> ""
        target.startsWith("word/") -> target
        target.startsWith("/") -> target.trimStart('/')
        else -> "word/$target"
    }
    if (name.isEmpty()) return ""
    return normalisePath(name)
}

private fun isRelationshipPart(name: String): Boolean {
    val directory = name.substringBeforeLast('/', "")
    if (directory.isEmpty()) return false
    return directory.substringAfterLast('/') == "_rels"
}

private fun relationshipPartName(partName: String): String {
    val directory = partName.substringBeforeLast('/', "")
    val base = partName.substringAfterLast('/')
    return if (directory.isEmpty()) "_rels/$base.rels" else "$directory/_rels/$base.rels"
}

private fun resolveAgainst(directory: String, target: String): String {
    if (target.startsWith("/")) return normalisePath(target.trimStart('/'))
    return normalisePath(if (directory.isEmpty()) target else "$directory/$target")
}

/**
 * Normalise a package path the way `posixpath.normpath` does.
 *
 * The path is always relative and always uses forward slashes, so a package
 * part is always spelled the same way whatever route the reader took to it.
 */
private fun normalisePath(path: String): String {
    val absolute = path.startsWith("/")
    val parts = path.split('/').toMutableList()
    val output = ArrayList<String>()
    for (part in parts) {
        when (part) {
            "", "." -> Unit
            ".." -> if (output.isNotEmpty() && output.last() != "..") {
                output.removeAt(output.size - 1)
            } else if (!absolute) {
                output.add("..")
            }

            else -> output.add(part)
        }
    }
    val joined = output.joinToString("/")
    return if (absolute) "/$joined" else if (joined.isEmpty()) "." else joined
}

private fun hasUriScheme(target: String): Boolean {
    val colon = target.indexOf(':')
    if (colon <= 0) return false
    val scheme = target.substring(0, colon)
    if (!scheme[0].isLetter()) return false
    return scheme.all { it.isLetterOrDigit() || it == '+' || it == '-' || it == '.' }
}

/**
 * Resolve a relative reference against a base URI, `urljoin` style.
 *
 * The JDK has no `urljoin`, and the behaviour that matters is narrow: an
 * absolute reference wins, a reference with a scheme wins, and a relative
 * reference replaces the last path segment of the base.
 */
private fun urlJoin(base: String, target: String): String {
    if (hasUriScheme(target)) return target
    if (base.isEmpty()) return target
    val baseScheme = base.substringBefore("://", "")
    if (baseScheme.isEmpty()) return target
    val authority = base.substringAfter("://", "").substringBefore('/')
    val basePath = base.substringAfter("://", "").substringAfter('/', "")
    val baseDirectory = basePath.substringBeforeLast('/', "")
    val resolved = when {
        target.startsWith("/") -> target
        else -> if (baseDirectory.isEmpty()) target else "$baseDirectory/$target"
    }
    val path = if (resolved.startsWith("/")) resolved else "/$resolved"
    return "$baseScheme://$authority$path"
}

private fun emuSize(width: String?, height: String?): Pair<Int, Int>? {
    if (width == null || height == null) return null
    val w = width.trim().toIntOrNull() ?: return null
    val h = height.trim().toIntOrNull() ?: return null
    return w to h
}

private val EMU_PER_UNIT: Map<String, Int> = mapOf(
    "pt" to 12700,
    "pc" to 12 * 12700,
    "in" to 72 * 12700,
    "cm" to 360000,
    "mm" to 36000,
)

private fun emuFromMeasure(measure: String?): Int? {
    if (measure.isNullOrEmpty()) return null
    for ((unit, factor) in EMU_PER_UNIT) {
        if (!measure.endsWith(unit)) continue
        val number = measure.dropLast(unit.length).trim().toDoubleOrNull() ?: return null
        return Math.round(number * factor).toInt()
    }
    return null
}

private fun vmlSize(style: String?): Pair<Int, Int>? {
    if (style.isNullOrEmpty()) return null
    val measures = mutableMapOf<String, String>()
    for (declaration in style.split(";")) {
        val separator = declaration.indexOf(':')
        if (separator < 0) continue
        val name = declaration.substring(0, separator).trim().lowercase()
        measures[name] = declaration.substring(separator + 1).trim().lowercase()
    }
    val width = emuFromMeasure(measures["width"]) ?: return null
    val height = emuFromMeasure(measures["height"]) ?: return null
    return width to height
}

/**
 * Return the font code a `w:sym` character code stands for.
 *
 * Word writes the code in the font's private-use range (`F0B7`), but producers
 * also write the bare code (`B7`). Both name the same character, so the
 * private-use offset is removed before the lookup. A code that is not a
 * hexadecimal byte has no font code at all.
 */
private fun symbolCode(raw: String): String? {
    var text = raw.trim().uppercase()
    if (text.startsWith("0X")) text = text.substring(2)
    if (text.isEmpty()) return null
    val value = text.toIntOrNull(16) ?: return null
    val reduced = if (value in 0xF000..0xF0FF) value and 0xFF else value
    if (reduced > 0xFF) return null
    return "%02X".format(reduced)
}

/**
 * Classify a style by name and id, or return `null` when unclassified.
 */
private fun classifyStyle(name: String, styleId: String): Pair<WordBlockKind, Int>? {
    val nameNorm = name.trim().lowercase()
    val idNorm = styleId.trim().lowercase()

    val headingName = Regex("^heading\\s*([1-9])$").find(nameNorm)
    if (headingName != null) {
        return WordBlockKind.HEADING to headingName.groupValues[1].toInt()
    }
    val headingId = Regex("^heading([1-9])$").find(idNorm)
    if (headingId != null) {
        return WordBlockKind.HEADING to headingId.groupValues[1].toInt()
    }
    if (nameNorm == "title" || idNorm == "title") return WordBlockKind.TITLE to 0
    if (nameNorm in setOf("quote", "intense quote") || idNorm in setOf("quote", "intensequote")) {
        return WordBlockKind.QUOTE to 0
    }
    if (nameNorm.contains("code") || nameNorm.contains("preformatted")) return WordBlockKind.CODE to 0
    if (idNorm.contains("code") || idNorm.contains("preformatted")) return WordBlockKind.CODE to 0
    return null
}

/** A natural sort key so `header2` sorts before `header10`. */
private val naturalOrder: Comparator<String> = Comparator { left, right ->
    val leftKey = naturalKey(left)
    val rightKey = naturalKey(right)
    val size = minOf(leftKey.size, rightKey.size)
    for (index in 0 until size) {
        val comparison = compareNaturalParts(leftKey[index], rightKey[index])
        if (comparison != 0) return@Comparator comparison
    }
    leftKey.size.compareTo(rightKey.size)
}

private fun compareNaturalParts(left: Triple<Int, Int, String>, right: Triple<Int, Int, String>): Int {
    if (left.first != right.first) return left.first.compareTo(right.first)
    if (left.second != right.second) return left.second.compareTo(right.second)
    return left.third.compareTo(right.third)
}

private fun naturalKey(value: String): List<Triple<Int, Int, String>> {
    val key = mutableListOf<Triple<Int, Int, String>>()
    for (part in Regex("(\\d+)").split(value)) {
        when {
            part.isNotEmpty() && part.all { it.isDigit() } -> key.add(Triple(0, part.toInt(), ""))
            part.isNotEmpty() -> key.add(Triple(1, 0, part))
        }
    }
    return key
}

/** Sort numeric ids numerically and non-numeric ids lexicographically. */
private val numericOrder: Comparator<String> = Comparator { left, right ->
    val leftNumber = left.toIntOrNull()
    val rightNumber = right.toIntOrNull()
    when {
        leftNumber != null && rightNumber != null -> leftNumber.compareTo(rightNumber)
        leftNumber != null -> -1
        rightNumber != null -> 1
        else -> left.compareTo(right)
    }
}
