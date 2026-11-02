package lmeditor.word

import lmeditor.core.WordRenderError
import java.io.ByteArrayOutputStream
import java.io.StringReader
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import javax.xml.parsers.DocumentBuilderFactory
import org.xml.sax.InputSource

/**
 * Writes a [WordDocument] back out as a `.docx` package.
 *
 * The writer builds the OOXML package itself: the JDK's XML and ZIP support is
 * the only machinery it uses, and the package carries no third-party dependency.
 *
 * ## What the writer guarantees
 *
 * * **W1** headings use real Word styles (`Heading1` .. `Heading9`), a title
 *   uses `Title`, a quote uses `Quote`, and a list item uses `List Paragraph`
 *   together with a numbering reference -- so Word renders the structure rather
 *   than a paragraph that merely looks like one.
 * * **W2** a numbered list restarts at 1 in every continuity group: one
 *   `w:num` is written per group, each with an explicit start override at every
 *   nesting depth.
 * * **W3** a table keeps its grid, its column spans, its vertical merges and
 *   its repeating header rows.
 * * **W4** footnotes, endnotes and comments are written as real parts and
 *   referenced from the body.
 * * **W5** a hyperlink is written as an *external* relationship; a part that
 *   travelled with the document is written back under the same name and reached
 *   through the same relationship id.
 * * **W6** the output is byte-deterministic by default: the same document
 *   always produces the same bytes, down to the zip member timestamps.
 */
class WordWriter(
    /**
     * Whether the output is byte-deterministic.
     *
     * The default is `true`: the writer fixes the zip member timestamps and the
     * member order, so two renders of the same document are byte-identical.
     */
    private val deterministic: Boolean = true,
) {

    /**
     * The omissions of the most recent write, as human-readable descriptions.
     *
     * An empty list means the document was written without a single loss.
     */
    var lastOmissions: List<String> = emptyList()
        private set

    private val omissionCounts = mutableMapOf<String, Int>()
    private lateinit var mediaById: Map<String, WordMedia>
    private lateinit var partsByName: Map<String, WordPart>
    private lateinit var documentStyles: List<WordStyle>
    private lateinit var rels: MutableList<RelationshipSpec>
    private lateinit var packageRels: MutableList<RelationshipSpec>
    private lateinit var contentTypeOverrides: MutableMap<String, String>
    private lateinit var contentTypeDefaults: MutableMap<String, String>
    private lateinit var usedRelIds: MutableSet<String>
    private lateinit var headerReferences: MutableList<Pair<String, String>>
    private lateinit var footerReferences: MutableList<Pair<String, String>>
    private var relCounter = 0
    private var revisionCounter = 0

    /**
     * Render [document] as the bytes of a `.docx` package.
     *
     * @param document the document to render
     * @return the bytes of the package
     * @throws WordRenderError if the document cannot be rendered
     */
    fun write(document: WordDocument): ByteArray {
        omissionCounts.clear()
        lastOmissions = emptyList()
        mediaById = document.media.associateBy { it.mediaId }
        partsByName = document.parts.associateBy { it.partName }
        documentStyles = document.styles
        rels = mutableListOf()
        packageRels = mutableListOf(
            RelationshipSpec("rId1", REL_TYPE_OFFICE_DOCUMENT, "word/document.xml", false),
            RelationshipSpec("rId2", REL_TYPE_CORE_PROPERTIES, "docProps/core.xml", false),
        )
        usedRelIds = mutableSetOf()
        headerReferences = mutableListOf()
        footerReferences = mutableListOf()
        contentTypeOverrides = linkedMapOf()
        contentTypeDefaults = linkedMapOf(
            "rels" to "application/vnd.openxmlformats-package.relationships+xml",
            "xml" to "application/xml",
        )
        relCounter = 0
        revisionCounter = 0

        val state = RenderState(
            numbering = NumberingPlan(document),
            footnoteIds = mapFootnoteIds(document),
            endnoteIds = mapEndnoteIds(document),
            commentIds = mapCommentIds(document),
        )

        // The relationships are created before the body is rendered: a part
        // keeps the id of the relationship it was reached by, and the body
        // needs those ids to point at the parts it anchors. The carried parts
        // are related first, so that a part the model wants under a given id
        // is never pushed aside by one the builder hands a fresh id to.
        val headerPlan = headerPlan(document)
        val footerPlan = footerPlan(document)
        val carriedParts = renderCarriedParts(
            document,
            state,
            builderPartNames(document, headerPlan, footerPlan),
        )
        val mediaParts = renderMedia(document, state)
        val headerFooterParts = renderHeadersAndFooters(headerPlan, footerPlan)

        val body = StringBuilder()
        var previousWasTable = false
        for (block in document.blocks) {
            if (block.kind == WordBlockKind.TABLE && previousWasTable) {
                // OOXML has no table boundary: Word reads two adjacent
                // `w:tbl` elements as ONE table, which silently destroys the
                // second table's grid (and any merge in it). An empty paragraph
                // between them is what Word itself writes when it splits a
                // table, and it is the only separator Word honours.
                body.append("<w:p/>")
            }
            body.append(renderBlock(block, state))
            previousWasTable = block.kind == WordBlockKind.TABLE
        }
        body.append(closeOpenCommentRanges(state))
        body.append(renderSections(document))

        val documentXml = xmlDeclaration() +
            "<w:document $nsW $nsR $nsMc $nsWp $nsA $nsPic $nsM $nsV>" +
            "<w:body>$body</w:body>" +
            "</w:document>"

        val parts = mutableListOf<PackageEntry>()
        // The note parts are rendered first so that their content types are
        // registered before the content-type part is rendered: a part that is
        // in the package must also be declared in `[Content_Types].xml`.
        val footnotesXml = if (document.footnotes.isNotEmpty()) {
            contentTypeOverrides["/word/footnotes.xml"] = CONTENT_TYPE_FOOTNOTES
            renderNotes("footnotes", document.footnotes.map { Note(it.footnoteId, it.text, it.runs) }, state)
        } else {
            null
        }
        val endnotesXml = if (document.endnotes.isNotEmpty()) {
            contentTypeOverrides["/word/endnotes.xml"] = CONTENT_TYPE_ENDNOTES
            renderNotes("endnotes", document.endnotes.map { Note(it.endnoteId, it.text, it.runs) }, state)
        } else {
            null
        }
        val commentsXml = if (document.comments.isNotEmpty()) {
            contentTypeOverrides["/word/comments.xml"] = CONTENT_TYPE_COMMENTS
            renderComments(document, state)
        } else {
            null
        }

        parts.add(PackageEntry("[Content_Types].xml", renderContentTypes().toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("_rels/.rels", renderPackageRelationships().toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("docProps/core.xml", renderCoreProperties(document).toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("customXml/item1.xml", CUSTOM_XML_ITEM.toByteArray(Charsets.UTF_8)))
        parts.add(
            PackageEntry(
                "customXml/itemProps1.xml",
                CUSTOM_XML_ITEM_PROPERTIES.toByteArray(Charsets.UTF_8),
            ),
        )
        parts.add(
            PackageEntry(
                "customXml/_rels/item1.xml.rels",
                CUSTOM_XML_ITEM_RELATIONSHIPS.toByteArray(Charsets.UTF_8),
            ),
        )
        parts.add(PackageEntry("word/document.xml", documentXml.toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("word/styles.xml", renderStyles(document).toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("word/numbering.xml", state.numbering.render().toByteArray(Charsets.UTF_8)))
        parts.add(PackageEntry("word/settings.xml", renderSettings().toByteArray(Charsets.UTF_8)))

        footnotesXml?.let { parts.add(PackageEntry("word/footnotes.xml", it.toByteArray(Charsets.UTF_8))) }
        endnotesXml?.let { parts.add(PackageEntry("word/endnotes.xml", it.toByteArray(Charsets.UTF_8))) }
        commentsXml?.let { parts.add(PackageEntry("word/comments.xml", it.toByteArray(Charsets.UTF_8))) }

        parts.addAll(headerFooterParts)
        parts.addAll(mediaParts)
        parts.addAll(carriedParts)

        parts.add(PackageEntry("word/_rels/document.xml.rels", renderRelationships().toByteArray(Charsets.UTF_8)))

        return buildZip(parts)
    }

    // ------------------------------------------------------------------
    // Blocks
    // ------------------------------------------------------------------

    private fun renderBlock(block: WordBlock, state: RenderState): String {
        when (block.kind) {
            WordBlockKind.TABLE -> {
                val table = block.table
                    ?: throw WordRenderError("table block ${block.blockId} carries no table payload")
                return renderTable(table, state)
            }

            WordBlockKind.PAGE_BREAK -> return "<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>"
            else -> Unit
        }

        // A body-level reference -- an alternative-format chunk, a
        // subdocument -- stands beside the paragraphs rather than inside a
        // run: Word expects it in the body of the document.
        val anchor = blockAnchor(block)
        if (anchor != null) {
            return renderBlockAnchor(anchor, state)
        }

        val properties = StringBuilder()
        val styleId = styleForBlock(block)
        if (styleId != null) properties.append("<w:pStyle w:val=\"$styleId\"/>")
        if (block.kind == WordBlockKind.LIST_ITEM) {
            val numId = state.numbering.numIdFor(block)
            val level = block.level.coerceIn(0, LIST_LEVEL_COUNT - 1)
            properties.append(
                "<w:numPr><w:ilvl w:val=\"$level\"/><w:numId w:val=\"$numId\"/></w:numPr>",
            )
            // Direct indentation wins over the paragraph style, so the nesting
            // depth stays visible whatever style the list paragraph carries.
            // The values are twips, which is what `w:ind` expects.
            properties.append(
                "<w:ind w:left=\"${LIST_INDENT_STEP * (level + 1)}\" w:hanging=\"$LIST_HANGING\"/>",
            )
        }
        val pPr = if (properties.isEmpty()) "" else "<w:pPr>$properties</w:pPr>"
        return "<w:p>$pPr${renderBlockRuns(block, state)}</w:p>"
    }

    private fun renderBlockRuns(block: WordBlock, state: RenderState): String {
        if (block.runs.isEmpty()) {
            // A block without runs still carries text; emit it as one run.
            return renderRun(WordRun(text = block.text), state)
        }
        val builder = StringBuilder()
        var index = 0
        val runs = block.runs
        while (index < runs.size) {
            val run = runs[index]
            val revision = run.revision
            if (revision == null || revision.kind == WordRevisionKind.FORMAT_CHANGE) {
                // A formatting revision is a revision of the run's *properties*:
                // the run itself is not inserted or deleted, so it is written as
                // an ordinary run that carries the revision in its run properties.
                openCommentRanges(run, state, builder)
                builder.append(renderRun(run, state))
                index++
                continue
            }
            val group = mutableListOf(run)
            index++
            while (index < runs.size && sameRevision(runs[index].revision, revision)) {
                group.add(runs[index])
                index++
            }
            builder.append(renderRevisionGroup(revision, group, state))
        }
        return builder.toString()
    }

    /**
     * The part a block anchors in the body, beside its paragraphs.
     *
     * An alternative-format chunk and a subdocument are body-level references:
     * Word expects them in the body of the document, not inside a run. A block
     * that holds exactly one run, that run points at a part of that kind and
     * carries no text of its own, is such a reference.
     */
    private fun blockAnchor(block: WordBlock): WordPart? {
        val runs = block.runs
        if (runs.size != 1) return null
        val run = runs[0]
        if (run.partRef == null || run.text.isNotEmpty()) return null
        val part = partsByName[run.partRef] ?: return null
        val kind = PART_ANCHOR_KINDS[relationshipKind(part.relationshipType)] ?: return null
        if (kind !in BLOCK_LEVEL_ANCHORS) return null
        return part
    }

    /**
     * Write a body-level reference into the document body.
     */
    private fun renderBlockAnchor(part: WordPart, state: RenderState): String {
        val relId = state.partRels[part.partName]
        if (relId == null) {
            omit("package part (${part.partName}) was not written into the package")
            return ""
        }
        val kind = PART_ANCHOR_KINDS[relationshipKind(part.relationshipType)]
        return when (kind) {
            "altChunk" -> "<w:altChunk r:id=\"$relId\"/>"
            "subDoc" -> "<w:subDoc r:id=\"$relId\"/>"
            else -> {
                omit("package part (${part.partName}) is not a body-level reference")
                ""
            }
        }
    }

    /**
     * Open the comment ranges a run belongs to.
     *
     * A range is opened by the run that carries the comment and closed at the
     * end of the body, so that the comment spans the text it is attached to.
     * A comment the document does not hold is a loss, and the register names
     * it: the range is simply not opened.
     */
    private fun openCommentRanges(run: WordRun, state: RenderState, builder: StringBuilder) {
        for (commentId in run.commentRefs) {
            if (commentId in state.openComments) continue
            val numericId = state.commentIds[commentId]
            if (numericId == null) {
                omit("comment range references unknown comment '$commentId'")
                continue
            }
            builder.append("<w:commentRangeStart w:id=\"$numericId\"/>")
            state.openComments[commentId] = numericId
        }
    }

    /**
     * Close the comment ranges that are still open.
     *
     * The closing paragraph carries the end of every range and the reference
     * that tells Word which comment the range belongs to.
     */
    private fun closeOpenCommentRanges(state: RenderState): String {
        if (state.openComments.isEmpty()) return ""
        val builder = StringBuilder("<w:p>")
        for (numericId in state.openComments.values) {
            builder.append("<w:commentRangeEnd w:id=\"$numericId\"/>")
            builder.append("<w:r><w:commentReference w:id=\"$numericId\"/></w:r>")
        }
        builder.append("</w:p>")
        state.openComments.clear()
        return builder.toString()
    }

    private fun renderRevisionGroup(revision: WordRevision, group: List<WordRun>, state: RenderState): String {
        val tag = when (revision.kind) {
            WordRevisionKind.INSERTION -> "w:ins"
            WordRevisionKind.DELETION -> "w:del"
            WordRevisionKind.MOVE_FROM -> "w:moveFrom"
            WordRevisionKind.MOVE_TO -> "w:moveTo"
            WordRevisionKind.FORMAT_CHANGE -> "w:ins"
        }
        val attributes = revisionAttributes(revision)
        val inner = group.joinToString("") { renderRun(it, state, insideDeletion = revision.kind == WordRevisionKind.DELETION) }
        return "<$tag$attributes>$inner</$tag>"
    }

    private fun revisionAttributes(revision: WordRevision): String {
        val builder = StringBuilder()
        builder.append(" w:id=\"${revisionCounter++}\"")
        builder.append(" w:author=\"${escapeXml(revision.author)}\"")
        revision.date?.let { builder.append(" w:date=\"${it.toIsoString()}\"") }
        return builder.toString()
    }

    private fun sameRevision(left: WordRevision?, right: WordRevision?): Boolean {
        if (left == null || right == null) return false
        if (left.kind != right.kind) return false
        if (left.revisionId.isNotEmpty() && right.revisionId.isNotEmpty()) {
            return left.revisionId == right.revisionId
        }
        return left.author == right.author && left.date == right.date
    }

    // ------------------------------------------------------------------
    // Runs
    // ------------------------------------------------------------------

    private fun renderRun(run: WordRun, state: RenderState, insideDeletion: Boolean = false): String {
        // An equation is a paragraph-level element, not a run, so it takes the
        // place of the run rather than sitting inside it.
        if (run.equationXml != null) {
            val markup = equationMarkup(run.equationXml)
            if (markup != null) return markup
        }

        val properties = runPropertiesXml(run)
        val text = WordText.stripControlCharacters(run.text)
        val textTag = if (insideDeletion) "w:delText" else "w:t"
        val textXml = textElement(text, textTag)

        // A run carries its text *and* the references it anchors. The
        // manuscript bridge writes a superscript marker whose text is the
        // citation number and whose footnote reference points at the cited
        // passage, so both have to reach the package.
        val references = StringBuilder()
        if (run.footnoteRef != null) {
            val numericId = state.footnoteIds[run.footnoteRef]
                ?: throw WordRenderError("run references unknown footnote '${run.footnoteRef}'")
            references.append("<w:footnoteReference w:id=\"$numericId\"/>")
        }
        if (run.endnoteRef != null) {
            val numericId = state.endnoteIds[run.endnoteRef]
                ?: throw WordRenderError("run references unknown endnote '${run.endnoteRef}'")
            references.append("<w:endnoteReference w:id=\"$numericId\"/>")
        }

        val anchor = StringBuilder()
        if (run.imageRef != null) {
            val media = mediaById[run.imageRef]
            val relId = media?.let { state.mediaRels[it.partName] }
            if (media == null || relId == null) {
                // An image the document does not carry is a loss, and the
                // register names it: the run is written without it rather
                // than the whole document being lost with it.
                omit("image (${run.imageRef}) is not embedded in the output package")
            } else {
                anchor.append(imageXml(relId, media, run.anchorExtent))
            }
        } else if (run.partRef != null) {
            val part = partsByName[run.partRef]
            if (part == null) {
                omit("package part (${run.partRef}) is not carried in the output package")
            } else {
                val relId = state.partRels[part.partName]
                if (relId == null) {
                    omit("package part (${part.partName}) was not written into the package")
                } else {
                    anchor.append(partAnchorXml(part, relId, state, run.anchorExtent))
                }
            }
        }

        if (run.hyperlink != null) {
            val url = run.hyperlink.trim()
            if (url.isEmpty()) {
                throw WordRenderError("a hyperlink run requires a non-empty target URL")
            }
            val relId = relate(url, REL_TYPE_HYPERLINK, external = true)
            return "<w:hyperlink r:id=\"$relId\"><w:r>$properties$textXml$anchor</w:r></w:hyperlink>"
        }
        return "<w:r>$properties$textXml$references$anchor</w:r>"
    }

    /**
     * The equation markup of a run, or `null` when it cannot be parsed.
     *
     * Markup the writer cannot parse costs the equation, not the document:
     * the register names it and the run is written as the text it carries.
     */
    private fun equationMarkup(markup: String): String? {
        val bound = bindMathNamespace(markup)
        val failure = xmlParseFailure(bound)
        if (failure != null) {
            omit("equation markup could not be parsed: $failure")
            return null
        }
        return bound
    }

    /**
     * Bind the math namespace into markup that does not declare it.
     *
     * The markup the model carries is a fragment of the document part, and the
     * prefix it uses for the math namespace is the one the root of the
     * document declares. A fragment that carries the namespace itself is left
     * alone.
     */
    private fun bindMathNamespace(markup: String): String {
        if (markup.contains("xmlns:m=")) return markup
        val end = markup.indexOf('>')
        if (end < 0) return markup
        return markup.substring(0, end) +
            " xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\"" +
            markup.substring(end)
    }

    /**
     * The reason a piece of XML cannot be parsed, or `null` when it can.
     *
     * The parser is told not to resolve anything outside the markup: the
     * markup comes from a document, and a document may say anything.
     */
    private fun xmlParseFailure(markup: String): String? {
        return try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.isNamespaceAware = true
            factory.isXIncludeAware = false
            factory.isExpandEntityReferences = false
            setFeatureQuietly(factory, "http://apache.org/xml/features/disallow-doctype-decl", true)
            setFeatureQuietly(factory, "http://xml.org/sax/features/external-general-entities", false)
            setFeatureQuietly(factory, "http://xml.org/sax/features/external-parameter-entities", false)
            setFeatureQuietly(factory, "http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
            factory.newDocumentBuilder().parse(InputSource(StringReader(markup)))
            null
        } catch (exc: Exception) {
            exc.message ?: exc.toString()
        }
    }

    private fun setFeatureQuietly(
        factory: DocumentBuilderFactory,
        feature: String,
        value: Boolean,
    ) {
        try {
            factory.setFeature(feature, value)
        } catch (_: Exception) {
            // A parser that does not know the feature cannot be tricked by it
            // either: the DOCTYPE is refused by the features that are known.
        }
    }

    private fun textElement(text: String, tag: String = "w:t"): String {
        if (text.isEmpty()) return ""
        val pieces = text.split("\n")
        val builder = StringBuilder()
        for (piece in pieces) {
            if (builder.isNotEmpty()) builder.append("<w:br/>")
            // The space is only marked as preserved when there is space to
            // preserve: a run whose text has none reads the same either way,
            // and the marking is what tells Word not to trim.
            val preserve = piece.trim().length != piece.length
            builder.append(
                "<$tag" + (if (preserve) " xml:space=\"preserve\"" else "") + ">" +
                    escapeXml(piece) + "</$tag>",
            )
        }
        return builder.toString()
    }

    /**
     * Render the run properties of a run as XML.
     *
     * The elements are emitted in the order the schema requires, so that Word
     * accepts the result without a repair prompt. A tracked formatting revision
     * is written last, which is where the schema expects it.
     */
    private fun runPropertiesXml(run: WordRun): String {
        val elements = mutableListOf<String>()
        run.font?.let { elements.add("<w:rFonts w:ascii=\"${escapeXml(it)}\" w:hAnsi=\"${escapeXml(it)}\"/>") }
        if (run.bold) elements.add("<w:b/>")
        if (run.italic) elements.add("<w:i/>")
        if (run.strike) elements.add("<w:strike/>")
        run.color?.let { elements.add("<w:color w:val=\"${escapeXml(it)}\"/>") }
        run.size?.let { elements.add("<w:sz w:val=\"$it\"/>") }
        run.highlight?.let { elements.add("<w:highlight w:val=\"${escapeXml(it)}\"/>") }
        if (run.underline) elements.add("<w:u w:val=\"single\"/>")
        if (run.superscript) elements.add("<w:vertAlign w:val=\"superscript\"/>")
        else if (run.subscript) elements.add("<w:vertAlign w:val=\"subscript\"/>")
        run.formatChangeXml?.let { elements.add(it) }
        if (elements.isEmpty()) return ""
        return "<w:rPr>${elements.joinToString("")}</w:rPr>"
    }

    private fun defaultExtent(media: WordMedia?): Pair<Int, Int>? {
        if (media == null) return null
        val size = imagePixelSize(media.data) ?: return null
        val width = (size.first * 9525).coerceAtLeast(1)
        val height = (size.second * 9525).coerceAtLeast(1)
        return width to height
    }

    private fun imageXml(relId: String, media: WordMedia, extent: Pair<Int, Int>?): String {
        val (cx, cy) = extent ?: defaultExtent(media) ?: (1905000 to 1905000)
        val name = media.partName.substringAfterLast('/')
        return "<w:drawing>" +
            "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">" +
            "<wp:extent cx=\"$cx\" cy=\"$cy\"/>" +
            "<wp:docPr id=\"1\" name=\"${escapeXml(name)}\"/>" +
            "<a:graphic>" +
            "<a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">" +
            "<pic:pic>" +
            "<pic:nvPicPr><pic:cNvPr id=\"1\" name=\"${escapeXml(name)}\"/><pic:cNvPicPr/></pic:nvPicPr>" +
            "<pic:blipFill><a:blip r:embed=\"$relId\"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>" +
            "<pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"$cx\" cy=\"$cy\"/></a:xfrm>" +
            "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr>" +
            "</pic:pic>" +
            "</a:graphicData></a:graphic></wp:inline></w:drawing>"
    }

    /**
     * The body reference of a part the run anchors.
     *
     * How a part is anchored is decided by the *kind* of the relationship
     * that reaches it: an embedded object is a `w:object`, a chart and a
     * diagram are DrawingML graphics, an alternative-format chunk and a
     * subdocument are body-level references. A part the body points at that
     * has no such role is named rather than written: a body reference nobody
     * can resolve is what makes Word report the package as corrupt.
     */
    private fun partAnchorXml(
        part: WordPart,
        relId: String,
        state: RenderState,
        extent: Pair<Int, Int>?,
    ): String {
        val kind = PART_ANCHOR_KINDS[relationshipKind(part.relationshipType)]
        if (kind == null) {
            omit("package part (${part.partName}) is not anchored by a body reference")
            return ""
        }
        return when (kind) {
            "object" -> objectXml(relId, extent)
            "chart" -> chartXml(relId, extent)
            "diagram" -> diagramXml(state, extent)
            "altChunk" -> "<w:altChunk r:id=\"$relId\"/>"
            else -> "<w:subDoc r:id=\"$relId\"/>"
        }
    }

    /**
     * The body reference of an embedded object.
     *
     * The shape of the object is sized in points, so the recorded extent is
     * converted from EMUs into them before it is written into the shape.
     */
    private fun objectXml(relId: String, extent: Pair<Int, Int>?): String {
        val (cx, cy) = drawingExtent(extent, DEFAULT_OBJECT_EXTENT)
        val width = points(cx)
        val height = points(cy)
        // The `o` prefix is declared here rather than on the root element,
        // because the object is the only thing in the document that uses it.
        // An undeclared prefix is not a rendering difference: it makes the
        // whole part unparseable, and a package Word cannot open is not a
        // package.
        return "<w:object $nsO>" +
            "<v:shape id=\"_x0000_i1025\" type=\"#_x0000_t75\"" +
            " style=\"width:${width}pt;height:${height}pt\" o:ole=\"\"/>" +
            "<o:OLEObject Type=\"Embed\" ProgID=\"Package\" ShapeID=\"_x0000_i1025\"" +
            " DrawAspect=\"Content\" ObjectID=\"_1\" r:id=\"$relId\"/>" +
            "</w:object>"
    }

    /**
     * The body reference of a chart.
     */
    private fun chartXml(relId: String, extent: Pair<Int, Int>?): String {
        val (cx, cy) = drawingExtent(extent, DEFAULT_DRAWING_EXTENT)
        return "<w:drawing $nsC>" +
            "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">" +
            "<wp:extent cx=\"$cx\" cy=\"$cy\"/>" +
            "<wp:effectExtent l=\"0\" t=\"0\" r=\"0\" b=\"0\"/>" +
            "<wp:docPr id=\"1\" name=\"Chart 1\"/>" +
            "<wp:cNvGraphicFramePr/>" +
            "<a:graphic>" +
            "<a:graphicData uri=\"$nsChartUri\">" +
            "<c:chart r:id=\"$relId\"/>" +
            "</a:graphicData>" +
            "</a:graphic>" +
            "</wp:inline>" +
            "</w:drawing>"
    }

    /**
     * The body reference of a diagram.
     *
     * A diagram is drawn from four parts: its data, its layout, its quick
     * style and its colours. The relationship of each is named by the
     * attribute it is reached through, and a part that is not in the package
     * is left out rather than pointed at.
     */
    private fun diagramXml(state: RenderState, extent: Pair<Int, Int>?): String {
        val (cx, cy) = drawingExtent(extent, DEFAULT_DRAWING_EXTENT)
        val attributes = StringBuilder()
        for (attribute in DIAGRAM_RELATIONSHIPS.keys.sorted()) {
            val kind = DIAGRAM_RELATIONSHIPS.getValue(attribute)
            val relId = state.partRelsByKind[kind] ?: continue
            attributes.append(" $attribute=\"$relId\"")
        }
        return "<w:drawing $nsDgm>" +
            "<wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">" +
            "<wp:extent cx=\"$cx\" cy=\"$cy\"/>" +
            "<wp:effectExtent l=\"0\" t=\"0\" r=\"0\" b=\"0\"/>" +
            "<wp:docPr id=\"1\" name=\"Diagram 1\"/>" +
            "<wp:cNvGraphicFramePr/>" +
            "<a:graphic>" +
            "<a:graphicData uri=\"$nsDiagramUri\">" +
            "<dgm:relIds$attributes/>" +
            "</a:graphicData>" +
            "</a:graphic>" +
            "</wp:inline>" +
            "</w:drawing>"
    }

    /**
     * The extent an anchored element is drawn at, in EMUs.
     *
     * The recorded extent wins over the default: Word scales a chart by
     * rewriting the extent of the drawing that holds it, so an element that
     * comes back at a default size is an element that came back the wrong
     * size.
     */
    private fun drawingExtent(extent: Pair<Int, Int>?, default: Pair<Int, Int>): Pair<Int, Int> {
        if (extent == null) return default
        return maxOf(1, extent.first) to maxOf(1, extent.second)
    }

    /**
     * The whole number of points an extent is measured in.
     *
     * A shape is sized in points, and Word writes a size as a whole number of
     * them: `100pt`, never `100.0pt`.
     */
    private fun points(emu: Int): Int = maxOf(1, Math.round(emu / EMU_PER_POINT.toDouble()).toInt())

    // ------------------------------------------------------------------
    // Tables
    // ------------------------------------------------------------------

    private fun renderTable(table: WordTable, state: RenderState): String {
        // The grid is laid out first: the cells of a row are placed on the
        // grid columns they occupy, and a merge the model names only by its
        // origin gets the continuation cells it needs.
        val layout = layoutTable(table)
        val gridWidth = layout.maxOfOrNull { row ->
            row.maxOfOrNull { placement -> placement.column + placement.colSpan } ?: 0
        } ?: 0
        val columnWidths = columnWidths(table, gridWidth)

        val rows = StringBuilder()
        for (rowIndex in table.rows.indices) {
            val row = table.rows[rowIndex]
            val isHeader = rowIndex < table.headerRows || row.cells.any { it.isHeader }
            rows.append(renderTableRow(layout[rowIndex], columnWidths, state, isHeader))
        }
        val grid = columnWidths.joinToString("") { "<w:gridCol w:w=\"$it\"/>" }
        // The table does not declare `xmlns:w` for itself: the root of the
        // document part already declares it, and a second declaration of the
        // same prefix is noise. The reference writer builds this element with
        // lxml, which drops the redundant declaration when the element is
        // moved into the body, so the package it produces carries a bare
        // `<w:tbl>`. Writing the declaration here would make the two writers
        // differ in the bytes of the document part for no gain.
        return "<w:tbl>" +
            "<w:tblPr>" +
            "<w:tblW w:w=\"0\" w:type=\"auto\"/>" +
            "<w:tblBorders>" +
            "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "<w:left w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "<w:right w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "<w:insideV w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"auto\"/>" +
            "</w:tblBorders>" +
            "<w:tblLayout w:type=\"fixed\"/>" +
            "<w:tblLook w:val=\"04A0\" w:firstRow=\"1\" w:lastRow=\"0\"" +
            " w:firstColumn=\"1\" w:lastColumn=\"0\" w:noHBand=\"0\" w:noVBand=\"1\"/>" +
            "</w:tblPr>" +
            "<w:tblGrid>$grid</w:tblGrid>" +
            rows +
            "</w:tbl>"
    }

    /**
     * Return the width of every grid column of a table, in twips.
     *
     * The widths the document declares are the source document's own; when the
     * declaration does not match the grid the table falls back to an even
     * split and says so, because a grid that does not add up is a table Word
     * will re-lay out on its own.
     */
    private fun columnWidths(table: WordTable, gridWidth: Int): List<Int> {
        if (gridWidth == 0) return emptyList()
        val declared = table.columnWidths
        if (declared.size == gridWidth) return declared
        if (declared.isNotEmpty()) {
            omit("table declares ${declared.size} column widths for a grid of $gridWidth columns")
        }
        val width = maxOf(1, TABLE_TOTAL_WIDTH / gridWidth)
        return List(gridWidth) { width }
    }

    private fun renderTableRow(
        placements: List<CellPlacement>,
        columnWidths: List<Int>,
        state: RenderState,
        isHeader: Boolean,
    ): String {
        val properties = if (isHeader) "<w:trPr><w:tblHeader/></w:trPr>" else ""
        val cells = StringBuilder()
        for (placement in placements) {
            cells.append(renderTableCell(placement, columnWidths, state))
        }
        return "<w:tr>$properties$cells</w:tr>"
    }

    private fun renderTableCell(
        placement: CellPlacement,
        columnWidths: List<Int>,
        state: RenderState,
    ): String {
        val cell = placement.cell
        val colSpan = placement.colSpan
        val properties = mutableListOf<String>()
        val width = cell?.width ?: columnWidths
            .subList(
                placement.column.coerceAtMost(columnWidths.size),
                (placement.column + colSpan).coerceAtMost(columnWidths.size),
            )
            .sum()
        properties.add("<w:tcW w:w=\"${maxOf(1, width)}\" w:type=\"dxa\"/>")
        if (colSpan > 1) properties.add("<w:gridSpan w:val=\"$colSpan\"/>")
        when (placement.verticalMerge) {
            CellVerticalMerge.RESTART -> properties.add("<w:vMerge w:val=\"restart\"/>")
            CellVerticalMerge.CONTINUE -> properties.add("<w:vMerge/>")
            CellVerticalMerge.NONE -> Unit
        }

        val content = StringBuilder()
        if (cell == null) {
            // A continuation the writer synthesised for a merge the model
            // named only by its origin carries no content of its own.
            content.append("<w:p/>")
        } else if (cell.runs.isNotEmpty()) {
            content.append("<w:p>")
            cell.runs.forEach { content.append(renderRun(it, state)) }
            content.append("</w:p>")
        } else if (cell.text.isNotEmpty()) {
            content.append("<w:p><w:r>${textElement(WordText.stripControlCharacters(cell.text))}</w:r></w:p>")
        }
        if (cell != null) {
            for (nested in cell.tables) {
                content.append(renderTable(nested, state))
            }
        }
        if (content.isEmpty()) {
            content.append("<w:p/>")
        } else if (content.toString().endsWith("</w:tbl>")) {
            // Word requires a cell to end with a paragraph, and a nested table
            // is not one.
            content.append("<w:p/>")
        }

        // The cell does not declare `xmlns:w` or `xmlns:r` for itself: the root
        // of the document part already declares both, and a second declaration
        // of the same prefix is noise. See `renderTable` for the reasoning.
        return "<w:tc><w:tcPr>${properties.joinToString("")}</w:tcPr>$content</w:tc>"
    }

    // ------------------------------------------------------------------
    // Styles and numbering
    // ------------------------------------------------------------------

    private fun styleForBlock(block: WordBlock): String? {
        block.style?.let { name ->
            documentStyles.firstOrNull {
                it.name.trim().lowercase() == name.trim().lowercase() ||
                    it.styleId.trim().lowercase() == name.trim().lowercase()
            }?.let { return it.styleId }
            omit("style ($name) is not defined in the source document")
        }
        return when (block.kind) {
            WordBlockKind.TITLE -> "Title"
            WordBlockKind.HEADING -> "Heading${block.level.coerceIn(1, 9)}"
            WordBlockKind.QUOTE -> "Quote"
            WordBlockKind.CODE -> "NoSpacing"
            WordBlockKind.LIST_ITEM -> "ListParagraph"
            else -> null
        }
    }

    private fun renderStyles(document: WordDocument): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<w:styles $nsW>")
        builder.append(
            "<w:docDefaults><w:rPrDefault><w:rPr>" +
                "<w:rFonts w:ascii=\"Calibri\" w:hAnsi=\"Calibri\" w:cs=\"Calibri\"/>" +
                "<w:sz w:val=\"22\"/><w:szCs w:val=\"22\"/>" +
                "</w:rPr></w:rPrDefault>" +
                "<w:pPrDefault><w:pPr><w:spacing w:after=\"160\" w:line=\"259\" w:lineRule=\"auto\"/></w:pPr></w:pPrDefault>" +
                "</w:docDefaults>",
        )
        // The default style is the root of the style hierarchy: a `w:basedOn`
        // that named the style itself would make the hierarchy invalid
        // (ECMA-376 17.7.4.3), so it carries none.
        builder.append(styleDefinition("Normal", "Normal", null, null, isDefault = true))
        builder.append(styleDefinition("Title", "Title", "Normal", null, rPr = "<w:sz w:val=\"56\"/><w:b/>"))
        for (level in 1..9) {
            builder.append(
                styleDefinition(
                    "Heading$level",
                    "heading $level",
                    "Normal",
                    null,
                    pPr = "<w:keepNext/><w:keepLines/><w:spacing w:before=\"240\" w:after=\"120\"/>" +
                        "<w:outlineLvl w:val=\"${level - 1}\"/>",
                ),
            )
        }
        builder.append(styleDefinition("Quote", "Quote", "Normal", null, rPr = "<w:i/>"))
        builder.append(styleDefinition("NoSpacing", "No Spacing", "Normal", null))
        builder.append(styleDefinition("ListParagraph", "List Paragraph", "Normal", null))
        builder.append(styleDefinition("FootnoteReference", "footnote reference", null, null, type = "character"))
        builder.append(styleDefinition("FootnoteText", "footnote text", "Normal", null))
        builder.append(styleDefinition("EndnoteReference", "endnote reference", null, null, type = "character"))
        builder.append(styleDefinition("EndnoteText", "endnote text", "Normal", null))
        builder.append(styleDefinition("CommentReference", "annotation reference", null, null, type = "character"))
        builder.append(styleDefinition("CommentText", "annotation text", "Normal", null))
        for (style in document.styles) {
            if (BUILTIN_STYLE_IDS.contains(style.styleId)) continue
            builder.append(styleDefinition(style.styleId, style.name.ifEmpty { style.styleId }, style.basedOn, style.numId))
        }
        builder.append("</w:styles>")
        return builder.toString()
    }

    private fun styleDefinition(
        id: String,
        name: String,
        basedOn: String?,
        numId: String?,
        type: String = "paragraph",
        isDefault: Boolean = false,
        pPr: String = "",
        rPr: String = "",
    ): String {
        val builder = StringBuilder()
        builder.append("<w:style w:type=\"$type\"")
        if (isDefault) builder.append(" w:default=\"1\"")
        builder.append(" w:styleId=\"${escapeXml(id)}\">")
        builder.append("<w:name w:val=\"${escapeXml(name)}\"/>")
        if (basedOn == id) {
            omit("style ($id) inherits from itself")
        } else if (!basedOn.isNullOrEmpty()) {
            builder.append("<w:basedOn w:val=\"${escapeXml(basedOn)}\"/>")
        }
        val paragraphProperties = buildString {
            if (pPr.isNotEmpty()) append(pPr)
            if (!numId.isNullOrEmpty()) append("<w:numPr><w:numId w:val=\"${escapeXml(numId)}\"/></w:numPr>")
        }
        if (paragraphProperties.isNotEmpty()) builder.append("<w:pPr>$paragraphProperties</w:pPr>")
        if (rPr.isNotEmpty()) builder.append("<w:rPr>$rPr</w:rPr>")
        builder.append("</w:style>")
        return builder.toString()
    }

    // ------------------------------------------------------------------
    // Sections, headers and footers
    // ------------------------------------------------------------------

    private fun renderSections(document: WordDocument): String {
        val sections = document.sections
        if (sections.isEmpty()) return "<w:sectPr/>"
        // Only the first section's page setup is written: the package holds one
        // section, and a section the writer cannot write is a loss the
        // register names rather than a change nobody asked for.
        val section = sections.first()
        if (sections.size > 1) {
            omit(
                "only the first section's page setup was written " +
                    "(${sections.size} sections in the document)",
            )
        }
        val orientation = if (section.orientation == "landscape") " w:orient=\"landscape\"" else ""
        // The header and footer references come first: the schema puts them
        // before the page setup, and they are what gives a header its role.
        val references = StringBuilder()
        for ((refType, relId) in headerReferences) {
            references.append("<w:headerReference w:type=\"$refType\" r:id=\"$relId\"/>")
        }
        for ((refType, relId) in footerReferences) {
            references.append("<w:footerReference w:type=\"$refType\" r:id=\"$relId\"/>")
        }
        // A first-page header is only the first-page header when the section
        // says it has one; an even-page header is only the even-page header
        // when the document says its pages differ.
        val titlePage = if (
            headerReferences.any { it.first == HEADER_TYPE_FIRST } ||
            footerReferences.any { it.first == HEADER_TYPE_FIRST }
        ) {
            "<w:titlePg/>"
        } else {
            ""
        }
        return "<w:sectPr>" +
            references +
            "<w:pgSz w:w=\"${section.pageWidth}\" w:h=\"${section.pageHeight}\"$orientation/>" +
            "<w:pgMar w:top=\"${section.marginTop}\" w:right=\"${section.marginRight}\"" +
            " w:bottom=\"${section.marginBottom}\" w:left=\"${section.marginLeft}\"" +
            " w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/>" +
            titlePage +
            "</w:sectPr>"
    }

    /**
     * The header texts of the document, keyed by reference type.
     *
     * A document that names its headers through its sections is matched by
     * *reference type* -- `default`, `first` or `even` -- and not by the order
     * the parts happen to have: a document whose `header1.xml` holds the
     * even-page header must not come back with its headers swapped. A
     * document that names none is matched by the order of its parts, which is
     * what the model carries.
     */
    private fun headersByType(document: WordDocument): Map<String, String> {
        val refs = document.sections.firstOrNull()?.headerRefs.orEmpty()
        if (refs.isNotEmpty()) {
            return refs.filter { it.text.isNotEmpty() }.associate { it.refType to it.text }
        }
        val positional = listOf(HEADER_TYPE_DEFAULT, HEADER_TYPE_FIRST, HEADER_TYPE_EVEN)
        return document.headers.mapIndexedNotNull { index, text ->
            if (index < positional.size) positional[index] to text else null
        }.toMap()
    }

    /**
     * The footer texts of the document, keyed by reference type.
     */
    private fun footersByType(document: WordDocument): Map<String, String> {
        val refs = document.sections.firstOrNull()?.footerRefs.orEmpty()
        if (refs.isNotEmpty()) {
            return refs.filter { it.text.isNotEmpty() }.associate { it.refType to it.text }
        }
        val positional = listOf(HEADER_TYPE_DEFAULT, HEADER_TYPE_FIRST, HEADER_TYPE_EVEN)
        return document.footers.mapIndexedNotNull { index, text ->
            if (index < positional.size) positional[index] to text else null
        }.toMap()
    }

    /**
     * The header and footer parts the package is going to hold.
     *
     * A reference type Word has no role for is not written: the role is what
     * makes a header a header, and a header with no role is a part nobody
     * reaches. The register names the type that was lost.
     */
    private fun headerPlan(document: WordDocument): List<HeaderFooterPlan> {
        val plan = mutableListOf<HeaderFooterPlan>()
        var index = 1
        for ((refType, text) in headersByType(document)) {
            if (refType !in SUPPORTED_REFERENCE_TYPES) {
                omit("header reference type '$refType' is not supported")
                continue
            }
            plan.add(HeaderFooterPlan(refType, "word/header$index.xml", text))
            index++
        }
        return plan
    }

    /**
     * The footer parts the package is going to hold.
     */
    private fun footerPlan(document: WordDocument): List<HeaderFooterPlan> {
        val plan = mutableListOf<HeaderFooterPlan>()
        var index = 1
        for ((refType, text) in footersByType(document)) {
            if (refType !in SUPPORTED_REFERENCE_TYPES) {
                omit("footer reference type '$refType' is not supported")
                continue
            }
            plan.add(HeaderFooterPlan(refType, "word/footer$index.xml", text))
            index++
        }
        return plan
    }

    private fun renderHeadersAndFooters(
        headers: List<HeaderFooterPlan>,
        footers: List<HeaderFooterPlan>,
    ): List<PackageEntry> {
        val entries = mutableListOf<PackageEntry>()
        if (headers.isEmpty() && footers.isEmpty()) return entries
        for (plan in headers) {
            entries.add(
                PackageEntry(
                    plan.partName,
                    renderHeaderFooter("hdr", plan.text).toByteArray(Charsets.UTF_8),
                ),
            )
            contentTypeOverrides["/${plan.partName}"] = CONTENT_TYPE_HEADER
            val relId = relate(
                plan.partName,
                REL_TYPE_HEADER,
                external = false,
                targetOverride = plan.partName.removePrefix("word/"),
            )
            headerReferences.add(plan.refType to relId)
        }
        for (plan in footers) {
            entries.add(
                PackageEntry(
                    plan.partName,
                    renderHeaderFooter("ftr", plan.text).toByteArray(Charsets.UTF_8),
                ),
            )
            contentTypeOverrides["/${plan.partName}"] = CONTENT_TYPE_FOOTER
            val relId = relate(
                plan.partName,
                REL_TYPE_FOOTER,
                external = false,
                targetOverride = plan.partName.removePrefix("word/"),
            )
            footerReferences.add(plan.refType to relId)
        }
        return entries
    }

    private fun renderHeaderFooter(tag: String, text: String): String =
        xmlDeclaration() +
            "<w:$tag $nsW $nsR>" +
            "<w:p><w:r><w:t xml:space=\"preserve\">${escapeXml(text)}</w:t></w:r></w:p>" +
            "</w:$tag>"

    // ------------------------------------------------------------------
    // Notes and comments
    // ------------------------------------------------------------------

    private fun renderNotes(tag: String, notes: List<Note>, state: RenderState): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<w:$tag $nsW $nsR>")
        builder.append(
            "<w:${tag.removeSuffix("s")} w:type=\"separator\" w:id=\"0\"><w:p><w:r><w:separator/></w:r></w:p></w:${tag.removeSuffix("s")}>",
        )
        builder.append(
            "<w:${tag.removeSuffix("s")} w:type=\"continuationSeparator\" w:id=\"1\"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:${tag.removeSuffix("s")}>",
        )
        for (note in notes) {
            val numericId = if (tag == "footnotes") state.footnoteIds[note.id] else state.endnoteIds[note.id]
            builder.append("<w:${tag.removeSuffix("s")} w:id=\"$numericId\">")
            builder.append(
                "<w:p><w:pPr><w:pStyle w:val=\"${if (tag == "footnotes") "FootnoteText" else "EndnoteText"}\"/></w:pPr>" +
                    "<w:r><w:rPr><w:rStyle w:val=\"${if (tag == "footnotes") "FootnoteReference" else "EndnoteReference"}\"/></w:rPr>" +
                    "<w:${tag.removeSuffix("s")}Ref/></w:r>",
            )
            val runs = note.runs.ifEmpty { listOf(WordRun(text = note.text)) }
            for (run in runs) {
                builder.append(renderRun(run, state))
            }
            builder.append("</w:p></w:${tag.removeSuffix("s")}>")
        }
        builder.append("</w:$tag>")
        return builder.toString()
    }

    private fun renderComments(document: WordDocument, state: RenderState): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<w:comments $nsW $nsR>")
        for (comment in document.comments) {
            val numericId = state.commentIds[comment.commentId] ?: continue
            builder.append("<w:comment w:id=\"$numericId\" w:author=\"${escapeXml(comment.author)}\"")
            comment.initials.takeIf { it.isNotEmpty() }?.let { builder.append(" w:initials=\"${escapeXml(it)}\"") }
            comment.date?.let { builder.append(" w:date=\"${it.toIsoString()}\"") }
            builder.append(">")
            builder.append("<w:p><w:pPr><w:pStyle w:val=\"CommentText\"/></w:pPr>")
            val runs = comment.runs.ifEmpty { listOf(WordRun(text = comment.text)) }
            for (run in runs) {
                builder.append(renderRun(run, state))
            }
            builder.append("</w:p></w:comment>")
        }
        builder.append("</w:comments>")
        return builder.toString()
    }

    // ------------------------------------------------------------------
    // Media and carried parts
    // ------------------------------------------------------------------

    private fun renderMedia(document: WordDocument, state: RenderState): List<PackageEntry> {
        val entries = mutableListOf<PackageEntry>()
        for (media in document.media) {
            entries.add(PackageEntry(media.partName, media.data))
            val extension = media.partName.substringAfterLast('.', "").lowercase()
            if (extension.isNotEmpty()) {
                contentTypeDefaults.putIfAbsent(extension, media.contentType.ifEmpty { "application/octet-stream" })
            }
            contentTypeOverrides["/${media.partName}"] = media.contentType.ifEmpty { "application/octet-stream" }
            val relId = relate(
                media.partName,
                REL_TYPE_IMAGE,
                external = false,
                targetOverride = media.partName.removePrefix("word/"),
            )
            state.mediaRels[media.partName] = relId
        }
        return entries
    }

    /**
     * The carried parts of a document, and the relationship parts beside them.
     *
     * A part the reader carried through is written back under its own name,
     * with its bytes and its content type, so that Word accepts the package
     * instead of reporting it as corrupt.
     *
     * Three things travel with a part, and each of them is a place this
     * writer used to lose it:
     *
     * 1. the id the document reached it by -- a body reference points at that
     *    id, so a fresh id would leave the reference dangling;
     * 2. the relationships the part holds to other parts -- a chart's colour
     *    style and the workbook behind it are reached from the chart's *own*
     *    relationship part, not from `document.xml`;
     * 3. the package-root relationship of a part the document did not reach --
     *    the custom document properties, for one. Inventing a document
     *    relationship for it is what makes Word report the package as corrupt.
     */
    /**
     * The names the package builder's own package already holds.
     *
     * A part the document carries under one of these names is not written
     * again: two members of the same name make Word report the package as
     * corrupt. The builder holds a custom XML item of its own, a document may
     * carry a part of the same name for, and the package ends up with the
     * part exactly once.
     */
    private fun builderPartNames(
        document: WordDocument,
        headers: List<HeaderFooterPlan>,
        footers: List<HeaderFooterPlan>,
    ): MutableSet<String> {
        val names = mutableSetOf(
            "/[Content_Types].xml",
            "/_rels/.rels",
            "/docProps/core.xml",
            "/customXml/item1.xml",
            "/customXml/itemProps1.xml",
            "/word/document.xml",
            "/word/styles.xml",
            "/word/numbering.xml",
            "/word/settings.xml",
            "/word/_rels/document.xml.rels",
        )
        if (document.footnotes.isNotEmpty()) names.add("/word/footnotes.xml")
        if (document.endnotes.isNotEmpty()) names.add("/word/endnotes.xml")
        if (document.comments.isNotEmpty()) names.add("/word/comments.xml")
        for (plan in headers) names.add("/${plan.partName}")
        for (plan in footers) names.add("/${plan.partName}")
        for (media in document.media) names.add("/${media.partName}")
        return names
    }

    private fun renderCarriedParts(
        document: WordDocument,
        state: RenderState,
        taken: MutableSet<String>,
    ): List<PackageEntry> {
        val candidates = mutableListOf<WordPart>()
        val documentReached = mutableSetOf<String>()
        val packageReached = mutableSetOf<String>()

        for (part in document.parts) {
            val name = part.partName.trim()
            if (!isWritablePartName(name)) {
                omit("package part (${part.partName}) has a name Word would refuse")
                continue
            }
            val key = normalisePartName(name)
            if (isRelationshipPartName(key)) {
                omit("package part (${part.partName}) is a relationship part and is not content")
                continue
            }
            if ("/$key" in taken) {
                omit("package part (${part.partName}) is already in the output package")
                continue
            }
            taken.add("/$key")
            candidates.add(part)

            if (documentReaches(part)) {
                val relId = relateWithId(
                    desiredId = part.relationshipId,
                    type = part.relationshipType,
                    target = relativeToDirectory(part.partName, DOCUMENT_DIRECTORY),
                )
                state.partRels[part.partName] = relId
                documentReached.add(key)
                val kind = relationshipKind(part.relationshipType)
                if (kind.isNotEmpty()) state.partRelsByKind[kind] = relId
            } else {
                relateFromPackageRoot(part, packageReached, key)
            }
        }

        // The relationships a carried part holds to other parts are put back
        // once every part exists, so that a part the document did not reach
        // is reached from the part that owns it.
        val known = taken.map { normalisePartName(it) }.toSet()
        val partEdges = mutableMapOf<String, MutableList<RelationshipSpec>>()
        for (part in candidates) {
            val key = normalisePartName(part.partName)
            val edges = partEdges.getOrPut(key) { mutableListOf() }
            val usedIds = mutableSetOf<String>()
            for (relationship in part.relationships) {
                val edge = addPartRelationship(part, relationship, known, usedIds) ?: continue
                edges.add(edge)
            }
        }

        val reachable = reachableParts(candidates, documentReached, packageReached, partEdges)
        val entries = mutableListOf<PackageEntry>()
        for (part in candidates) {
            val key = normalisePartName(part.partName)
            if (key !in reachable) {
                omit(
                    "package part ($key) is not reached by any relationship and " +
                        "cannot travel in the package",
                )
                continue
            }
            entries.add(PackageEntry(part.partName, part.data))
            if (part.contentType.isNotEmpty()) {
                contentTypeOverrides["/${part.partName}"] = part.contentType
            }
            val edges = partEdges[key].orEmpty()
            if (edges.isEmpty()) continue
            entries.add(
                PackageEntry(
                    relsPartName(part.partName),
                    renderPartRelationships(part.partName, edges).toByteArray(Charsets.UTF_8),
                ),
            )
        }
        return entries
    }

    /**
     * Add one relationship of a carried part to the part that owns it.
     *
     * The id and the type are the ones the relationship was read with: they
     * are what the owner's own markup points at. A relationship that points at
     * a part the package does not hold is a loss, and the register names it:
     * the relationship is not written, because a relationship to nothing is
     * what makes Word report the package as corrupt.
     */
    private fun addPartRelationship(
        owner: WordPart,
        relationship: WordPartRelationship,
        known: Set<String>,
        usedIds: MutableSet<String>,
    ): RelationshipSpec? {
        val relId = relationship.relationshipId.trim()
        if (relId in usedIds) {
            omit(
                "relationship ($relId) of package part (${owner.partName}) is " +
                    "already used by another relationship",
            )
            return null
        }
        if (relationship.isExternal) {
            // An external target is a URL rather than a part of the package:
            // the relationship travels, but it reaches nothing.
            usedIds.add(relId)
            return RelationshipSpec(
                id = relId,
                type = relationship.relationshipType,
                target = relationship.target,
                external = true,
            )
        }
        val targetName = normalisePartName(relationship.target)
        if (targetName !in known) {
            omit(
                "relationship ($relId) of package part (${owner.partName}) points " +
                    "at a part that is not in the package (${relationship.target})",
            )
            return null
        }
        usedIds.add(relId)
        return RelationshipSpec(
            id = relId,
            type = relationship.relationshipType,
            target = relationship.target,
            external = false,
        )
    }

    /**
     * Report whether the document reached a part.
     *
     * A part the document reached is named by a relationship of the document
     * part: it carries the id of that relationship and the type of it. A part
     * the document did not reach carries neither.
     */
    private fun documentReaches(part: WordPart): Boolean =
        part.relationshipId.isNotBlank() && part.relationshipType.isNotBlank()

    /**
     * Relate a part the document did not reach from the package root.
     *
     * A part the document does not name is reached from the root of the
     * package -- the custom document properties, for one. Relating it from
     * the document instead is what makes Word report the package as corrupt.
     *
     * The relationship keeps the id the reader saw it with: a body reference
     * or another part may point at that id. When the root's own relationship
     * already holds it, that one is moved aside to a fresh id rather than
     * lost, so the package still reaches the document it held before.
     *
     * An external target is a URL rather than a part of the package, so it is
     * added as an external relationship and reaches nothing: the bytes the
     * model carried for it have no place in the package, and the part is
     * named as lost rather than dropped in silence.
     */
    private fun relateFromPackageRoot(
        part: WordPart,
        packageReached: MutableSet<String>,
        key: String,
    ) {
        val relationship = part.packageRelationship ?: return
        val relId = relationship.relationshipId.trim().ifEmpty { nextPackageRelId() }
        displacePackageRelationship(relId)
        packageRels.add(
            RelationshipSpec(
                id = relId,
                type = relationship.relationshipType,
                target = relationship.target,
                external = relationship.isExternal,
            ),
        )
        if (relationship.isExternal) return
        packageReached.add(key)
    }

    /**
     * Move a relationship of the package root aside to a fresh id.
     *
     * The id a part was reached by is the id the package expects to keep, and
     * the relationship that held it is one of the builder's own: nothing in
     * the document refers to it by id.
     */
    private fun displacePackageRelationship(relId: String) {
        val index = packageRels.indexOfFirst { it.id == relId }
        if (index < 0) return
        val fresh = nextPackageRelId(excluding = relId)
        packageRels[index] = packageRels[index].copy(id = fresh)
    }

    /**
     * The next free relationship id of the package root.
     *
     * The root's relationship list is its own: the ids of the document's
     * relationships are a different space and never collide with these.
     */
    private fun nextPackageRelId(excluding: String? = null): String {
        var counter = 1
        while (true) {
            val id = "rId$counter"
            if (id != excluding && packageRels.none { it.id == id }) return id
            counter++
        }
    }

    /**
     * The parts the package's relationship graph reaches.
     *
     * The graph has two roots: the document, which reaches the parts the body
     * draws, and the package root, which reaches the parts that are not part
     * of the document at all. A part is reachable when one of those roots
     * reaches it, or when a part that is reachable reaches it.
     */
    private fun reachableParts(
        candidates: List<WordPart>,
        documentReached: Set<String>,
        packageReached: Set<String>,
        partEdges: Map<String, List<RelationshipSpec>>,
    ): Set<String> {
        val byName = candidates.associateBy { normalisePartName(it.partName) }
        val reachable = mutableSetOf<String>()
        val pending = ArrayDeque(documentReached + packageReached)
        while (pending.isNotEmpty()) {
            val name = pending.removeFirst()
            if (!reachable.add(name)) continue
            for (edge in partEdges[name].orEmpty()) {
                if (edge.external) continue
                val target = normalisePartName(edge.target)
                if (byName.containsKey(target)) pending.addLast(target)
            }
        }
        return reachable
    }

    /**
     * The relationship part that belongs to a part.
     *
     * A part's own relationships live beside it, in `<dir>/_rels/<name>.rels`,
     * and their targets are written relative to the part's own directory --
     * which is what makes a chart find its colour style.
     */
    private fun renderPartRelationships(partName: String, edges: List<RelationshipSpec>): String {
        val directory = directoryOf(partName)
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<Relationships xmlns=\"$nsPackageRelationships\">")
        for (edge in edges) {
            val target =
                if (edge.external) edge.target
                else relativeToDirectory(normalisePartName(edge.target), directory)
            builder.append(
                "<Relationship Id=\"${escapeXml(edge.id)}\"" +
                    " Type=\"${escapeXml(edge.type)}\"" +
                    " Target=\"${escapeXml(target)}\"" +
                    (if (edge.external) " TargetMode=\"External\"" else "") +
                    "/>",
            )
        }
        builder.append("</Relationships>")
        return builder.toString()
    }

    /**
     * The name of the relationship part that belongs to a part.
     */
    private fun relsPartName(partName: String): String {
        val directory = directoryOf(partName)
        val name = partName.substringAfterLast('/')
        return if (directory.isEmpty()) "_rels/$name.rels" else "$directory/_rels/$name.rels"
    }

    /**
     * The directory a part lives in.
     */
    private fun directoryOf(partName: String): String =
        partName.substringBeforeLast('/', missingDelimiterValue = "")

    /**
     * A path as the package spells it: no leading slash, no `.` segments.
     */
    private fun normalisePartName(path: String): String =
        path.trimStart('/').split('/').filter { it.isNotEmpty() && it != "." }.joinToString("/")

    /**
     * A path as it is seen from a directory.
     *
     * The target of a relationship is written relative to the directory of the
     * part that holds the relationship: a part under `word/` is named
     * `embeddings/image1.png`, a part outside it is named
     * `../customXml/item1.xml`.
     */
    private fun relativeToDirectory(path: String, directory: String): String {
        val target = normalisePartName(path).split('/').filter { it.isNotEmpty() }
        val base = directory.split('/').filter { it.isNotEmpty() }
        var common = 0
        while (common < target.size && common < base.size && target[common] == base[common]) {
            common++
        }
        val up = List(base.size - common) { ".." }
        val down = target.drop(common)
        val parts = up + down
        return if (parts.isEmpty()) "." else parts.joinToString("/")
    }

    // ------------------------------------------------------------------
    // Package plumbing
    // ------------------------------------------------------------------

    private fun relate(
        targetName: String,
        type: String,
        external: Boolean,
        targetOverride: String? = null,
    ): String {
        val id = nextRelId()
        rels.add(
            RelationshipSpec(
                id = id,
                type = type,
                target = targetOverride ?: targetName,
                external = external,
            ),
        )
        return id
    }

    /**
     * Relate a part under the id the model says it was reached by.
     *
     * A body reference points at that id, so a fresh one would leave the
     * reference dangling. When the id is already held by one of the package
     * builder's own relationships, that one is moved aside to a fresh id:
     * nothing in the document refers to it by id, and the id it held is one
     * the model expects to keep.
     */
    private fun relateWithId(desiredId: String, type: String, target: String): String {
        val wanted = desiredId.trim()
        if (wanted.isEmpty()) return relate(target, type, external = false)
        val existing = rels.indexOfFirst { it.id == wanted }
        if (existing >= 0) {
            val displaced = rels.removeAt(existing)
            rels.add(displaced.copy(id = nextRelId()))
        }
        usedRelIds.add(wanted)
        rels.add(
            RelationshipSpec(
                id = wanted,
                type = type,
                target = target,
                external = false,
            ),
        )
        return wanted
    }

    /**
     * The next free relationship id.
     *
     * An id the model wants to keep is never handed out again, so a fresh id
     * cannot collide with one the document already names.
     */
    private fun nextRelId(): String {
        while (true) {
            val id = "rId${++relCounter}"
            if (usedRelIds.add(id)) return id
        }
    }

    private fun renderRelationships(): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<Relationships xmlns=\"$nsPackageRelationships\">")
        builder.append(
            "<Relationship Id=\"rIdStyles\" Type=\"$REL_TYPE_STYLES\" Target=\"styles.xml\"/>",
        )
        builder.append(
            "<Relationship Id=\"rIdNumbering\" Type=\"$REL_TYPE_NUMBERING\" Target=\"numbering.xml\"/>",
        )
        builder.append(
            "<Relationship Id=\"rIdSettings\" Type=\"$REL_TYPE_SETTINGS\" Target=\"settings.xml\"/>",
        )
        // The custom XML item the package holds is reached from the document,
        // the way the package builder's own template reaches it.
        builder.append(
            "<Relationship Id=\"rIdCustomXml\" Type=\"$REL_TYPE_CUSTOM_XML\"" +
                " Target=\"../customXml/item1.xml\"/>",
        )
        if (contentTypeOverrides.containsKey("/word/footnotes.xml")) {
            builder.append(
                "<Relationship Id=\"rIdFootnotes\" Type=\"$REL_TYPE_FOOTNOTES\" Target=\"footnotes.xml\"/>",
            )
        }
        if (contentTypeOverrides.containsKey("/word/endnotes.xml")) {
            builder.append(
                "<Relationship Id=\"rIdEndnotes\" Type=\"$REL_TYPE_ENDNOTES\" Target=\"endnotes.xml\"/>",
            )
        }
        if (contentTypeOverrides.containsKey("/word/comments.xml")) {
            builder.append(
                "<Relationship Id=\"rIdComments\" Type=\"$REL_TYPE_COMMENTS\" Target=\"comments.xml\"/>",
            )
        }
        for (relationship in rels) {
            builder.append(
                "<Relationship Id=\"${escapeXml(relationship.id)}\"" +
                    " Type=\"${escapeXml(relationship.type)}\"" +
                    " Target=\"${escapeXml(relationship.target)}\"" +
                    (if (relationship.external) " TargetMode=\"External\"" else "") +
                    "/>",
            )
        }
        builder.append("</Relationships>")
        return builder.toString()
    }

    private fun renderPackageRelationships(): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<Relationships xmlns=\"$nsPackageRelationships\">")
        // The root holds the package's own relationships -- the document and
        // the core properties -- and the parts the document did not reach.
        for (relationship in packageRels) {
            builder.append(
                "<Relationship Id=\"${escapeXml(relationship.id)}\"" +
                    " Type=\"${escapeXml(relationship.type)}\"" +
                    " Target=\"${escapeXml(relationship.target)}\"" +
                    (if (relationship.external) " TargetMode=\"External\"" else "") +
                    "/>",
            )
        }
        builder.append("</Relationships>")
        return builder.toString()
    }

    private fun renderContentTypes(): String {
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<Types xmlns=\"$nsContentTypes\">")
        for ((extension, contentType) in contentTypeDefaults) {
            builder.append("<Default Extension=\"$extension\" ContentType=\"$contentType\"/>")
        }
        builder.append(
            "<Override PartName=\"/word/document.xml\" ContentType=\"$CONTENT_TYPE_DOCUMENT\"/>",
        )
        builder.append(
            "<Override PartName=\"/word/styles.xml\" ContentType=\"$CONTENT_TYPE_STYLES\"/>",
        )
        builder.append(
            "<Override PartName=\"/word/numbering.xml\" ContentType=\"$CONTENT_TYPE_NUMBERING\"/>",
        )
        builder.append(
            "<Override PartName=\"/word/settings.xml\" ContentType=\"$CONTENT_TYPE_SETTINGS\"/>",
        )
        builder.append(
            "<Override PartName=\"/docProps/core.xml\" ContentType=\"$CONTENT_TYPE_CORE\"/>",
        )
        builder.append(
            "<Override PartName=\"/customXml/itemProps1.xml\"" +
                " ContentType=\"$CONTENT_TYPE_CUSTOM_XML_PROPERTIES\"/>",
        )
        if (contentTypeOverrides.containsKey("/word/footnotes.xml")) {
            builder.append(
                "<Override PartName=\"/word/footnotes.xml\" ContentType=\"$CONTENT_TYPE_FOOTNOTES\"/>",
            )
        }
        if (contentTypeOverrides.containsKey("/word/endnotes.xml")) {
            builder.append(
                "<Override PartName=\"/word/endnotes.xml\" ContentType=\"$CONTENT_TYPE_ENDNOTES\"/>",
            )
        }
        if (contentTypeOverrides.containsKey("/word/comments.xml")) {
            builder.append(
                "<Override PartName=\"/word/comments.xml\" ContentType=\"$CONTENT_TYPE_COMMENTS\"/>",
            )
        }
        for ((partName, contentType) in contentTypeOverrides) {
            if (partName == "/word/footnotes.xml" || partName == "/word/endnotes.xml" ||
                partName == "/word/comments.xml"
            ) {
                continue
            }
            builder.append("<Override PartName=\"$partName\" ContentType=\"$contentType\"/>")
        }
        builder.append("</Types>")
        return builder.toString()
    }

    private fun renderSettings(): String {
        // A document whose pages differ needs to say so: without the setting,
        // the even-page header is a part the package holds but never shows.
        val evenAndOdd = headerReferences.any { it.first == HEADER_TYPE_EVEN } ||
            footerReferences.any { it.first == HEADER_TYPE_EVEN }
        return xmlDeclaration() +
            "<w:settings $nsW><w:zoom w:percent=\"100\"/>" +
            (if (evenAndOdd) "<w:evenAndOddHeaders/>" else "") +
            "</w:settings>"
    }

    private fun renderCoreProperties(document: WordDocument): String {
        val properties = document.properties
        val builder = StringBuilder()
        builder.append(xmlDeclaration())
        builder.append("<cp:coreProperties xmlns:cp=\"$nsCoreProperties\" xmlns:dc=\"$nsDc\"")
        builder.append(" xmlns:dcterms=\"$nsDcterms\" xmlns:xsi=\"$nsXsi\">")
        builder.append("<dc:title>${escapeXml(properties.title)}</dc:title>")
        builder.append("<dc:creator>${escapeXml(properties.author)}</dc:creator>")
        builder.append("<dc:subject>${escapeXml(properties.subject)}</dc:subject>")
        builder.append("<cp:keywords>${escapeXml(properties.keywords)}</cp:keywords>")
        builder.append("<dc:description>${escapeXml(properties.comments)}</dc:description>")
        properties.created?.let {
            builder.append("<dcterms:created xsi:type=\"dcterms:W3CDTF\">${it.toUtcIsoString()}</dcterms:created>")
        }
        properties.modified?.let {
            builder.append("<dcterms:modified xsi:type=\"dcterms:W3CDTF\">${it.toUtcIsoString()}</dcterms:modified>")
        }
        builder.append("<cp:revision>${properties.revision}</cp:revision>")
        builder.append("</cp:coreProperties>")
        return builder.toString()
    }

    // ------------------------------------------------------------------
    // Zip
    // ------------------------------------------------------------------

    private fun buildZip(entries: List<PackageEntry>): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for (entry in entries) {
                val zipEntry = ZipEntry(entry.name)
                if (deterministic) {
                    zipEntry.time = FIXED_ZIP_TIME
                }
                zipEntry.method = ZipEntry.DEFLATED
                zipEntry.size = entry.data.size.toLong()
                val crc = CRC32()
                crc.update(entry.data)
                zipEntry.crc = crc.value
                zip.putNextEntry(zipEntry)
                zip.write(entry.data)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }

    // ------------------------------------------------------------------
    // Id mapping
    // ------------------------------------------------------------------

    private fun mapFootnoteIds(document: WordDocument): Map<String, Int> =
        document.footnotes.mapIndexed { index, footnote -> footnote.footnoteId to (FIRST_NOTE_ID + index) }.toMap()

    private fun mapEndnoteIds(document: WordDocument): Map<String, Int> =
        document.endnotes.mapIndexed { index, endnote -> endnote.endnoteId to (FIRST_NOTE_ID + index) }.toMap()

    private fun mapCommentIds(document: WordDocument): Map<String, Int> =
        document.comments.mapIndexed { index, comment -> comment.commentId to index }.toMap()

    private fun omit(description: String) {
        omissionCounts[description] = (omissionCounts[description] ?: 0) + 1
        lastOmissions = omissionCounts.entries
            .sortedBy { it.key }
            .map { (text, count) -> if (count == 1) text else "$text x$count" }
    }

    // ------------------------------------------------------------------
    // Internal types
    // ------------------------------------------------------------------

    private data class RelationshipSpec(
        val id: String,
        val type: String,
        val target: String,
        val external: Boolean,
    )

    private data class PackageEntry(val name: String, val data: ByteArray)

    /**
     * One header or footer the package is going to hold.
     *
     * The [refType] is the *role* the part plays -- `default`, `first` or
     * `even` -- and it is the role that travels into the section properties,
     * so that a header comes back as the header it was.
     */
    private data class HeaderFooterPlan(
        val refType: String,
        val partName: String,
        val text: String,
    )

    private data class Note(val id: String, val text: String, val runs: List<WordRun>)

    private class RenderState(
        val numbering: NumberingPlan,
        val footnoteIds: Map<String, Int>,
        val endnoteIds: Map<String, Int>,
        val commentIds: Map<String, Int>,
        val mediaRels: MutableMap<String, String> = mutableMapOf(),
        val partRels: MutableMap<String, String> = mutableMapOf(),
        /**
         * The relationship id of the part of each *kind* the body anchors.
         *
         * A diagram is drawn from four parts -- its data, its layout, its quick
         * style and its colours -- and the drawing names each of them by the
         * kind of the part, not by its name.
         */
        val partRelsByKind: MutableMap<String, String> = mutableMapOf(),
        /**
         * The comment ranges that are open, in the order they were opened.
         *
         * A range is opened by the run that carries the comment and closed at
         * the end of the body, so that the comment spans the text it is
         * attached to.
         */
        val openComments: MutableMap<String, Int> = LinkedHashMap(),
    )

    /**
     * Owns the `numbering.xml` a document needs.
     *
     * One `w:abstractNum` is created per *level signature* -- the per-level
     * `w:numFmt` and `w:lvlText` the source document used -- and one `w:num` is
     * created per continuity group, so that a new group restarts its numbering
     * at 1 while items of the same group continue a single sequence.
     *
     * The format and the level text are the source document's, not a guess: a
     * roman or alpha list keeps looking the way it did.
     */
    private class NumberingPlan(document: WordDocument) {

        private val abstractIds = mutableMapOf<List<Triple<Int, String, String>>, Int>()
        private val numIds = mutableMapOf<String, Int>()
        private val specs = mutableMapOf<String, MutableMap<Int, Pair<String, String>>>()
        private val abstractXml = mutableListOf<String>()
        private val numXml = mutableListOf<String>()
        private var nextAbstractId = 0
        private var nextNumId = 1

        init {
            prepare(document)
        }

        private fun prepare(document: WordDocument) {
            val groups = linkedMapOf<String, MutableList<WordBlock>>()
            for (block in document.blocks) {
                if (block.kind != WordBlockKind.LIST_ITEM || block.listMarker == null) continue
                groups.getOrPut(ownerKey(block)) { mutableListOf() }.add(block)
            }
            for ((owner, blocks) in groups) {
                val spec = mutableMapOf<Int, Pair<String, String>>()
                for (block in blocks) {
                    val level = block.level.coerceIn(0, LIST_LEVEL_COUNT - 1)
                    spec[level] = levelSpec(block, level)
                }
                specs[owner] = spec
            }
        }

        /**
         * Return the key that decides which `w:num` a block belongs to.
         *
         * A numbered list is identified by its continuity group: two lists of
         * the same group continue one sequence. A bullet list carries no
         * sequence, so bullets share one instance as long as they agree on the
         * way their marker is rendered.
         */
        private fun ownerKey(block: WordBlock): String {
            val marker = block.listMarker
            if (marker == ListMarker.NUMBER) return "number|${block.listGroup ?: ""}"
            return "bullet|${levelSignature(block)}"
        }

        private fun levelSignature(block: WordBlock): String {
            val level = block.level.coerceIn(0, LIST_LEVEL_COUNT - 1)
            val (format, text) = levelSpec(block, level)
            return "$format|$text"
        }

        private fun levelSpec(block: WordBlock, level: Int): Pair<String, String> {
            val marker = block.listMarker
            val defaultFormat = if (marker == ListMarker.BULLET) "bullet" else "decimal"
            val format = block.numberFormat?.trim().orEmpty().ifEmpty { defaultFormat }
            var text = block.numberText?.trim().orEmpty()
            if (text.isEmpty()) {
                text = if (format == "bullet") {
                    BULLET_TEXT
                } else {
                    (0..level).joinToString("") { "%${it + 1}." }
                }
            }
            return format to text
        }

        fun numIdFor(block: WordBlock): Int {
            val marker = block.listMarker
                ?: throw WordRenderError("list item ${block.blockId} carries no list marker")
            val owner = ownerKey(block)
            numIds[owner]?.let { return it }
            val spec = specs[owner] ?: emptyMap()
            val abstractId = abstractIdFor(spec)
            val numId = nextNumId++
            // An explicit start override makes every continuity group begin at
            // 1, at every nesting depth.
            val overrides = (0 until LIST_LEVEL_COUNT).joinToString("") { level ->
                "<w:lvlOverride w:ilvl=\"$level\"><w:startOverride w:val=\"1\"/></w:lvlOverride>"
            }
            numXml.add("<w:num w:numId=\"$numId\"><w:abstractNumId w:val=\"$abstractId\"/>$overrides</w:num>")
            numIds[owner] = numId
            return numId
        }

        private fun abstractIdFor(spec: Map<Int, Pair<String, String>>): Int {
            val signature = spec.entries
                .map { (level, value) -> Triple(level, value.first, value.second) }
                .sortedWith(compareBy({ it.first }, { it.second }, { it.third }))
            abstractIds[signature]?.let { return it }
            val abstractId = nextAbstractId++
            abstractXml.add(buildAbstractNum(abstractId, spec))
            abstractIds[signature] = abstractId
            return abstractId
        }

        private fun buildAbstractNum(abstractId: Int, spec: Map<Int, Pair<String, String>>): String {
            val default = if (spec.isEmpty()) {
                "decimal" to "%1."
            } else {
                spec[spec.keys.min()] ?: ("decimal" to "%1.")
            }
            val levels = (0 until LIST_LEVEL_COUNT).joinToString("") { level ->
                val (format, text) = spec[level] ?: derivedLevel(default, level)
                val left = LIST_INDENT_STEP * (level + 1)
                "<w:lvl w:ilvl=\"$level\">" +
                    "<w:start w:val=\"1\"/>" +
                    "<w:numFmt w:val=\"${escapeXml(format)}\"/>" +
                    "<w:lvlText w:val=\"${escapeXml(text)}\"/>" +
                    "<w:lvlJc w:val=\"left\"/>" +
                    "<w:pPr><w:ind w:left=\"$left\" w:hanging=\"$LIST_HANGING\"/></w:pPr>" +
                    "</w:lvl>"
            }
            val nsid = "%08X".format(0x5A000000L + abstractId)
            val template = "%08X".format(0x5B000000L + abstractId)
            return "<w:abstractNum $nsW w:abstractNumId=\"$abstractId\">" +
                "<w:nsid w:val=\"$nsid\"/>" +
                "<w:multiLevelType w:val=\"multilevel\"/>" +
                "<w:tmpl w:val=\"$template\"/>" +
                levels +
                "</w:abstractNum>"
        }

        private fun derivedLevel(default: Pair<String, String>, level: Int): Pair<String, String> {
            val (format, text) = default
            if (format == "bullet") return format to text
            if (text.contains('%')) return format to text
            return format to "%${level + 1}."
        }

        fun render(): String =
            xmlDeclaration() +
                "<w:numbering $nsW>" +
                abstractXml.joinToString("") +
                numXml.joinToString("") +
                "</w:numbering>"
    }

    companion object {
        /** Number of list levels (0-8) defined for every abstract numbering definition. */
        const val LIST_LEVEL_COUNT: Int = 9

        /** Indentation step, in twips, applied per list nesting level. */
        const val LIST_INDENT_STEP: Int = 720

        /** Hanging indent, in twips, applied to list paragraphs. */
        const val LIST_HANGING: Int = 360

        /** Total width of a table in twips (US Letter minus one-inch margins). */
        const val TABLE_TOTAL_WIDTH: Int = 9360

        /**
         * Lowest `w:id` that a model footnote may be mapped onto. Ids 0 and 1 are
         * reserved for the separator and continuation-separator footnotes that
         * Word itself writes.
         */
        const val FIRST_NOTE_ID: Int = 2

        private const val BULLET_TEXT = "•"
        /**
         * The epoch every member of the package carries.
         *
         * The zip format's own epoch is the earliest a DOS timestamp can
         * name, so the output is the same bytes on every machine and in
         * every year.
         */
        private const val FIXED_ZIP_TIME = 315_532_800_000L // 1980-01-01T00:00:00Z

        private val BUILTIN_STYLE_IDS = setOf(
            "Normal",
            "Title",
            "Heading1",
            "Heading2",
            "Heading3",
            "Heading4",
            "Heading5",
            "Heading6",
            "Heading7",
            "Heading8",
            "Heading9",
            "Quote",
            "NoSpacing",
            "ListParagraph",
            "FootnoteReference",
            "FootnoteText",
            "EndnoteReference",
            "EndnoteText",
            "CommentReference",
            "CommentText",
        )

        private const val nsW = "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\""
        private const val nsR = "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\""
        private const val nsMc = "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\""
        private const val nsWp = "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\""
        private const val nsA = "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\""
        private const val nsPic = "xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\""
        private const val nsM = "xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\""
        private const val nsV = "xmlns:v=\"urn:schemas-microsoft-com:vml\""
        private const val nsO = "xmlns:o=\"urn:schemas-microsoft-com:office:office\""

        /**
         * The directory the document part lives in.
         *
         * The targets of the document's relationships are written relative to
         * it: a part under `word/` is named `embeddings/image1.png`, a part
         * outside it is named `../customXml/item1.xml`.
         */
        private const val DOCUMENT_DIRECTORY = "word"
        private const val nsPackageRelationships =
            "http://schemas.openxmlformats.org/package/2006/relationships"
        private const val nsContentTypes =
            "http://schemas.openxmlformats.org/package/2006/content-types"
        private const val nsCoreProperties =
            "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
        private const val nsDc = "http://purl.org/dc/elements/1.1/"
        private const val nsDcterms = "http://purl.org/dc/terms/"
        private const val nsXsi = "http://www.w3.org/2001/XMLSchema-instance"

        private const val REL_TYPE_OFFICE_DOCUMENT =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        private const val REL_TYPE_CORE_PROPERTIES =
            "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
        private const val REL_TYPE_STYLES =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
        private const val REL_TYPE_NUMBERING =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
        private const val REL_TYPE_SETTINGS =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
        private const val REL_TYPE_FOOTNOTES =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
        private const val REL_TYPE_ENDNOTES =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes"
        private const val REL_TYPE_COMMENTS =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
        private const val REL_TYPE_HYPERLINK =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
        private const val REL_TYPE_IMAGE =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        private const val REL_TYPE_HEADER =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
        private const val REL_TYPE_FOOTER =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
        private const val REL_TYPE_CUSTOM_XML =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
        private const val REL_TYPE_CUSTOM_XML_PROPS =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXmlProps"

        private const val CONTENT_TYPE_DOCUMENT =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        private const val CONTENT_TYPE_STYLES =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"
        private const val CONTENT_TYPE_NUMBERING =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"
        private const val CONTENT_TYPE_SETTINGS =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
        private const val CONTENT_TYPE_FOOTNOTES =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
        private const val CONTENT_TYPE_ENDNOTES =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"
        private const val CONTENT_TYPE_COMMENTS =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
        private const val CONTENT_TYPE_HEADER =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
        private const val CONTENT_TYPE_FOOTER =
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
        private const val CONTENT_TYPE_CORE =
            "application/vnd.openxmlformats-package.core-properties+xml"
        private const val CONTENT_TYPE_CUSTOM_XML_PROPERTIES =
            "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"

        /** The reference type of the header every page carries. */
        private const val HEADER_TYPE_DEFAULT = "default"

        /** The reference type of the header the first page carries. */
        private const val HEADER_TYPE_FIRST = "first"

        /** The reference type of the header the even pages carry. */
        private const val HEADER_TYPE_EVEN = "even"

        /** The reference types Word has a role for. */
        private val SUPPORTED_REFERENCE_TYPES =
            setOf(HEADER_TYPE_DEFAULT, HEADER_TYPE_FIRST, HEADER_TYPE_EVEN)

        /**
         * How a part is anchored in the body, keyed by the kind of the
         * relationship that reaches it.
         *
         * An embedded object is anchored by `w:object`, a chart by a DrawingML
         * graphic, a diagram by a DrawingML graphic of its own, an
         * alternative-format chunk by `w:altChunk`. A part the model carries
         * but that the body never points at -- a glossary document, say -- is
         * not in this table, and no body reference is written for it.
         */
        private val PART_ANCHOR_KINDS: Map<String, String> = mapOf(
            "oleObject" to "object",
            "package" to "object",
            "chart" to "chart",
            "diagramData" to "diagram",
            "aFChunk" to "altChunk",
            "subDocument" to "subDoc",
        )

        /**
         * The body references that stand beside the paragraphs rather than
         * inside a run: Word expects them in the body of the document, not in
         * a run.
         */
        private val BLOCK_LEVEL_ANCHORS = setOf("altChunk", "subDoc")

        /**
         * The relationship types of the parts a diagram is drawn from, keyed by
         * the attribute the diagram names them with.
         */
        private val DIAGRAM_RELATIONSHIPS: Map<String, String> = mapOf(
            "dm" to "diagramData",
            "lo" to "diagramLayout",
            "qs" to "diagramQuickStyle",
            "cs" to "diagramColors",
        )

        /** The extent an embedded object is drawn at when none was recorded. */
        private val DEFAULT_OBJECT_EXTENT = 1270000 to 635000

        /** The extent a drawing is drawn at when none was recorded. */
        private val DEFAULT_DRAWING_EXTENT = 1905000 to 1905000

        /** The number of EMUs in one point. */
        private const val EMU_PER_POINT = 12700

        private const val nsC = "xmlns:c=\"http://schemas.openxmlformats.org/drawingml/2006/chart\""
        private const val nsDgm =
            "xmlns:dgm=\"http://schemas.openxmlformats.org/drawingml/2006/diagram\""
        private const val nsChartUri = "http://schemas.openxmlformats.org/drawingml/2006/chart"
        private const val nsDiagramUri = "http://schemas.openxmlformats.org/drawingml/2006/diagram"

        /**
         * The custom XML item every package carries.
         *
         * The package builder holds a part of its own, and a document may
         * carry a part of the same name for: the part ends up in the package
         * exactly once, which is what Word insists on. Two members of the same
         * name make Word report the package as corrupt.
         *
         * The bytes are the ones the reference's package builder holds, so
         * that the two writers put the same part into the package.
         */
        private val CUSTOM_XML_ITEM =
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n" +
                "<b:Sources xmlns:b=\"http://schemas.openxmlformats.org/officeDocument/2006/bibliography\"" +
                " xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/bibliography\"" +
                " SelectedStyle=\"/APA.XSL\" StyleName=\"APA\"/>\n"

        /** The properties of the custom XML item. */
        private val CUSTOM_XML_ITEM_PROPERTIES =
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n" +
                "<ds:datastoreItem xmlns:ds=\"http://schemas.openxmlformats.org/officeDocument/2006/customXml\"" +
                " ds:itemID=\"{EF278816-EC6F-A645-907D-7F25AECB1D4A}\">\n" +
                "  <ds:schemaRefs>\n" +
                "    <ds:schemaRef ds:uri=\"http://schemas.openxmlformats.org/officeDocument/2006/bibliography\"/>\n" +
                "  </ds:schemaRefs>\n" +
                "</ds:datastoreItem>\n"

        /** The relationship list of the custom XML item. */
        private val CUSTOM_XML_ITEM_RELATIONSHIPS =
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n" +
                "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">\n" +
                "  <Relationship Id=\"rId1\" Type=\"$REL_TYPE_CUSTOM_XML_PROPS\"" +
                " Target=\"itemProps1.xml\"/>\n" +
                "</Relationships>"
    }
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

private fun xmlDeclaration(): String = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"

private fun escapeXml(text: String): String {
    val builder = StringBuilder(text.length)
    for (ch in text) {
        when (ch) {
            '&' -> builder.append("&amp;")
            '<' -> builder.append("&lt;")
            '>' -> builder.append("&gt;")
            '"' -> builder.append("&quot;")
            '\'' -> builder.append("&apos;")
            else -> builder.append(ch)
        }
    }
    return builder.toString()
}

private fun OffsetDateTime.toIsoString(): String =
    DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(this)

private fun OffsetDateTime.toUtcIsoString(): String =
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").format(withOffsetSameInstant(ZoneOffset.UTC))

/**
 * Report whether a package part name may be written back into the package.
 *
 * A package part name is a relative path: no leading slash, no empty segment,
 * and no `..` that would climb out of the package. A name that breaks those
 * rules is refused rather than written: Word would report the package as
 * corrupt.
 */
private fun isWritablePartName(name: String): Boolean {
    if (name.isEmpty() || name.startsWith("/")) return false
    if (name.contains('\\')) return false
    if (name.any { it == '\r' || it == '\n' || it == '\t' }) return false
    return name.split('/').all { segment -> segment.isNotEmpty() && segment != "." && segment != ".." }
}

/**
 * Report whether a part name is the relationship part of a sibling part.
 *
 * A `.rels` file is the relationship list of the part beside it, not content:
 * handing one to Word as content is what makes Word report the package as
 * corrupt.
 */
private fun isRelationshipPartName(name: String): Boolean {
    val segments = name.split('/')
    return segments.size >= 2 && segments[segments.size - 2] == "_rels"
}

/**
 * Return the kind of relationship a relationship type names.
 *
 * The kind is the last segment of the type: the namespace every relationship
 * shares says nothing about the part, the last segment says what the part is.
 */
private fun relationshipKind(relType: String): String =
    relType.trim().substringAfterLast('/').trim()

/**
 * Return the pixel size of an image, or `null` when the format is not one the
 * JDK-free writer can measure.
 *
 * Only PNG and GIF carry their size in a header the writer can read without a
 * third-party decoder; for every other format the writer falls back to a
 * default extent rather than guessing.
 */
private fun imagePixelSize(data: ByteArray): Pair<Int, Int>? {
    if (data.size >= 24 && data[0] == 0x89.toByte() && data[1] == 'P'.code.toByte()) {
        val width = ((data[16].toInt() and 0xFF) shl 24) or
            ((data[17].toInt() and 0xFF) shl 16) or
            ((data[18].toInt() and 0xFF) shl 8) or
            (data[19].toInt() and 0xFF)
        val height = ((data[20].toInt() and 0xFF) shl 24) or
            ((data[21].toInt() and 0xFF) shl 16) or
            ((data[22].toInt() and 0xFF) shl 8) or
            (data[23].toInt() and 0xFF)
        return width to height
    }
    if (data.size >= 10 && data[0] == 'G'.code.toByte() && data[1] == 'I'.code.toByte()) {
        val width = (data[6].toInt() and 0xFF) or ((data[7].toInt() and 0xFF) shl 8)
        val height = (data[8].toInt() and 0xFF) or ((data[9].toInt() and 0xFF) shl 8)
        return width to height
    }
    return null
}
