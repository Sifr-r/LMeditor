package lmeditor.word

import lmeditor.core.ValidationError
import java.time.OffsetDateTime
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull

/**
 * The wire form of the Word model.
 *
 * These tests pin the facts the Kotlin tier and the Python tier have to agree
 * on: the shape a document takes on the wire, the rules a decoder follows, and
 * the tolerance the contract demands of it.
 */
class WordWireTest {

    // ------------------------------------------------------------------
    // The round trip
    // ------------------------------------------------------------------

    @Test
    fun `every field of the model survives the wire`() {
        val document = fullDocument()
        val decoded = wordDocumentFromWire(wordDocumentToWire(document))
        assertEquals(document, decoded)
    }

    @Test
    fun `every field of the model survives the wire as a JSON document`() {
        // The same round trip, but through the JSON codec the seam uses, so a
        // value the codec cannot carry shows up as a failure here.
        val document = fullDocument()
        val text = lmeditor.core.json.Json.write(wordDocumentToWire(document))
        val decoded = wordDocumentFromWire(lmeditor.core.json.Json.parseObject(text))
        assertEquals(document, decoded)
    }

    // ------------------------------------------------------------------
    // The documented shape
    // ------------------------------------------------------------------

    @Test
    fun `a minimal document has the documented shape`() {
        val wire = wordDocumentToWire(minimalDocument())
        assertEquals(minimalDocumentWire(minimalBlockWire(minimalRunWire())), wire)
    }

    @Test
    @Suppress("UNCHECKED_CAST")
    fun `a minimal document carries every key, in the documented order`() {
        val wire = wordDocumentToWire(minimalDocument())
        assertEquals(
            listOf(
                "blocks",
                "properties",
                "footnotes",
                "endnotes",
                "comments",
                "headers",
                "footers",
                "sections",
                "styles",
                "media",
                "parts",
            ),
            wire.keys.toList(),
        )
        val block = (wire["blocks"] as List<Map<String, Any?>>).first()
        assertEquals(
            listOf(
                "block_id",
                "kind",
                "text",
                "level",
                "list_marker",
                "list_group",
                "number_format",
                "number_text",
                "style",
                "runs",
                "table",
            ),
            block.keys.toList(),
        )
        val run = (block["runs"] as List<Map<String, Any?>>).first()
        assertEquals(
            listOf(
                "text",
                "bold",
                "italic",
                "underline",
                "superscript",
                "subscript",
                "strike",
                "font",
                "size",
                "color",
                "highlight",
                "hyperlink",
                "footnote_ref",
                "endnote_ref",
                "revision",
                "comment_refs",
                "image_ref",
                "equation_xml",
                "part_ref",
                "anchor_extent",
                "format_change_xml",
            ),
            run.keys.toList(),
        )
    }

    // ------------------------------------------------------------------
    // The rules a decoder follows
    // ------------------------------------------------------------------

    @Test
    fun `an absent key decodes to null`() {
        val run = minimalRunWire().apply { remove("font") }
        val decoded = wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(run)))
        assertNull(decoded.blocks.first().runs.first().font)
    }

    @Test
    fun `an absent key and an explicit null decode alike`() {
        val absent = minimalRunWire().apply { remove("highlight") }
        val explicit = minimalRunWire().apply { put("highlight", null) }
        val fromAbsent = wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(absent)))
        val fromNull = wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(explicit)))
        assertEquals(fromAbsent, fromNull)
        assertNull(fromAbsent.blocks.first().runs.first().highlight)
    }

    @Test
    fun `an unknown key is ignored`() {
        val run = minimalRunWire().apply { put("a_key_from_the_future", "anything") }
        val decoded = wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(run)))
        assertEquals(minimalDocument(), decoded)
    }

    @Test
    fun `a missing required key is an error`() {
        val run = minimalRunWire().apply { remove("text") }
        val failure = assertFailsWith<ValidationError> {
            wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(run)))
        }
        assertEquals("text", failure.field)
    }

    @Test
    fun `a missing required document key is an error`() {
        val wire = minimalDocumentWire(minimalBlockWire(minimalRunWire())).apply {
            remove("blocks")
        }
        val failure = assertFailsWith<ValidationError> { wordDocumentFromWire(wire) }
        assertEquals("blocks", failure.field)
    }

    @Test
    fun `a value of the wrong type is an error`() {
        val run = minimalRunWire().apply { put("bold", "yes") }
        assertFailsWith<ValidationError> {
            wordDocumentFromWire(minimalDocumentWire(minimalBlockWire(run)))
        }
    }

    @Test
    fun `a model invariant is enforced when the document is decoded`() {
        // A TABLE block without a table payload is refused by the model, and
        // the model is where the invariants live.
        val block = minimalBlockWire(minimalRunWire()).apply {
            put("kind", "table")
            put("table", null)
        }
        assertFailsWith<ValidationError> {
            wordDocumentFromWire(minimalDocumentWire(block))
        }
    }

    // ------------------------------------------------------------------
    // The shared facts
    // ------------------------------------------------------------------

    @Test
    fun `the enum wire strings are the documented ones`() {
        assertEquals(
            listOf("title", "heading", "paragraph", "list_item", "table", "quote", "code", "page_break"),
            WordBlockKind.entries.map { it.wire },
        )
        assertEquals(listOf("bullet", "number"), ListMarker.entries.map { it.wire })
        assertEquals(
            listOf("insertion", "deletion", "move_from", "move_to", "format_change"),
            WordRevisionKind.entries.map { it.wire },
        )
    }

    @Test
    fun `a naive timestamp is read as UTC and an offset one keeps its offset`() {
        val naive = wordPropertiesFromWire(
            minimalPropertiesWire().apply { put("created", "2024-01-01T00:00:00") },
        )
        assertEquals(OffsetDateTime.parse("2024-01-01T00:00:00Z"), naive.created)

        val aware = wordPropertiesFromWire(
            minimalPropertiesWire().apply { put("created", "2024-01-01T00:00:00+02:00") },
        )
        assertEquals(OffsetDateTime.parse("2024-01-01T00:00:00+02:00"), aware.created)
    }

    @Test
    fun `an unparseable timestamp is an error`() {
        assertFailsWith<ValidationError> {
            wordPropertiesFromWire(minimalPropertiesWire().apply { put("created", "yesterday") })
        }
    }

    @Test
    fun `binary payloads travel as padded base64`() {
        val media = WordMedia(
            mediaId = "m-1",
            partName = "word/media/image1.png",
            contentType = "image/png",
            data = byteArrayOf(0, 1, 2, -1, -2, 127),
            relationshipId = "rId5",
        )
        val wire = wordMediaToWire(media)
        assertEquals("AAEC//5/", wire["data"])
        assertEquals(media, wordMediaFromWire(wire))
    }

    @Test
    fun `binary payloads that are not base64 are an error`() {
        val wire = LinkedHashMap(
            wordMediaToWire(
                WordMedia(
                    mediaId = "m-1",
                    partName = "word/media/image1.png",
                    contentType = "image/png",
                    data = byteArrayOf(1),
                    relationshipId = "rId5",
                ),
            ),
        ).apply { put("data", "not base64!") }
        assertFailsWith<ValidationError> { wordMediaFromWire(wire) }
    }

    @Test
    fun `an anchor extent travels as two integers`() {
        val wire = wordRunToWire(WordRun(text = "x", anchorExtent = 914400 to 457200))
        assertEquals(listOf(914400, 457200), wire["anchor_extent"])
        assertEquals(914400 to 457200, wordRunFromWire(wire).anchorExtent)
    }

    @Test
    fun `an anchor extent that is not a pair is an error`() {
        val wire = LinkedHashMap(wordRunToWire(WordRun(text = "x", anchorExtent = 1 to 2))).apply {
            put("anchor_extent", listOf(1, 2, 3))
        }
        assertFailsWith<ValidationError> { wordRunFromWire(wire) }
    }

    // ------------------------------------------------------------------
    // A real package
    // ------------------------------------------------------------------

    @Test
    fun `a document read from a package survives the wire`() {
        // The fixture is a Word-365-shaped package: tracked revisions, a table,
        // a footnote, a header, a footer, a hyperlink. A wire form that cannot
        // carry it is not a wire form.
        val reader = WordReader()
        val document = reader.readBytes(WordFixtures.buildWord365Fixture(), null, "fixture.docx")
        val decoded = wordDocumentFromWire(wordDocumentToWire(document))
        assertEquals(document, decoded)
    }

    @Test
    fun `the package relationships of the last read travel the wire`() {
        val reader = WordReader()
        reader.readBytes(WordFixtures.buildWord365Fixture(), null, "fixture.docx")
        val relationships = reader.lastPackageRelationships
        assertEquals(listOf("rId1", "rId2"), relationships.map { it.relationshipId })
        assertEquals(
            listOf("word/document.xml", "docProps/core.xml"),
            relationships.map { it.target },
        )
        val wire = relationships.map { wordPartRelationshipToWire(it) }
        assertEquals(relationships, wire.map { wordPartRelationshipFromWire(it) })
    }

    // ------------------------------------------------------------------
    // The fixtures
    // ------------------------------------------------------------------

    private fun minimalDocument(): WordDocument = WordDocument(
        blocks = listOf(
            WordBlock(
                blockId = "b1",
                kind = WordBlockKind.PARAGRAPH,
                text = "Hello",
                runs = listOf(WordRun(text = "Hello")),
            ),
        ),
    )

    private fun minimalRunWire(): LinkedHashMap<String, Any?> = linkedMapOf(
        "text" to "Hello",
        "bold" to false,
        "italic" to false,
        "underline" to false,
        "superscript" to false,
        "subscript" to false,
        "strike" to false,
        "font" to null,
        "size" to null,
        "color" to null,
        "highlight" to null,
        "hyperlink" to null,
        "footnote_ref" to null,
        "endnote_ref" to null,
        "revision" to null,
        "comment_refs" to emptyList<String>(),
        "image_ref" to null,
        "equation_xml" to null,
        "part_ref" to null,
        "anchor_extent" to null,
        "format_change_xml" to null,
    )

    private fun minimalBlockWire(run: Map<String, Any?>): LinkedHashMap<String, Any?> = linkedMapOf(
        "block_id" to "b1",
        "kind" to "paragraph",
        "text" to "Hello",
        "level" to 0,
        "list_marker" to null,
        "list_group" to null,
        "number_format" to null,
        "number_text" to null,
        "style" to null,
        "runs" to listOf(run),
        "table" to null,
    )

    private fun minimalPropertiesWire(): LinkedHashMap<String, Any?> = linkedMapOf(
        "title" to "",
        "author" to "",
        "subject" to "",
        "keywords" to "",
        "comments" to "",
        "created" to null,
        "modified" to null,
        "revision" to 1,
    )

    private fun minimalDocumentWire(block: Map<String, Any?>): LinkedHashMap<String, Any?> = linkedMapOf(
        "blocks" to listOf(block),
        "properties" to minimalPropertiesWire(),
        "footnotes" to emptyList<Any?>(),
        "endnotes" to emptyList<Any?>(),
        "comments" to emptyList<Any?>(),
        "headers" to emptyList<String>(),
        "footers" to emptyList<String>(),
        "sections" to emptyList<Any?>(),
        "styles" to emptyList<Any?>(),
        "media" to emptyList<Any?>(),
        "parts" to emptyList<Any?>(),
    )

    /**
     * A document that exercises every field of every type the model declares.
     *
     * The document is built so that it satisfies the model's own invariants:
     * the text of a block is the normalised concatenation of its live runs, a
     * merged cell is not repeated in the grid, and every reference a run
     * carries resolves.
     */
    private fun fullDocument(): WordDocument {
        val inserted = WordRevision(
            kind = WordRevisionKind.INSERTION,
            author = "Ada Lovelace",
            date = OffsetDateTime.parse("2024-01-01T00:00:00Z"),
            revisionId = "rev-1",
        )
        val deleted = WordRevision(
            kind = WordRevisionKind.DELETION,
            author = "Grace Hopper",
            date = null,
            revisionId = "rev-2",
        )

        val footnote = WordFootnote(
            footnoteId = "fn-1",
            text = "A footnote",
            runs = listOf(WordRun(text = "A footnote")),
        )
        val endnote = WordEndnote(
            endnoteId = "en-1",
            text = "An endnote",
            runs = listOf(WordRun(text = "An endnote")),
        )
        val comment = WordComment(
            commentId = "c-1",
            author = "Ada Lovelace",
            initials = "AL",
            date = OffsetDateTime.parse("2024-02-03T04:05:06Z"),
            text = "A comment",
            runs = listOf(WordRun(text = "A comment")),
            anchorBlockId = "b-para",
            anchorText = "Annotated passage",
        )

        val media = WordMedia(
            mediaId = "m-1",
            partName = "word/media/image1.png",
            contentType = "image/png",
            data = byteArrayOf(0, 1, 2, -1, -2, 127),
            relationshipId = "rId5",
        )
        val part = WordPart(
            partName = "word/charts/chart1.xml",
            contentType = "application/xml",
            data = "<c:chart/>".toByteArray(Charsets.UTF_8),
            relationshipId = "rId7",
            relationshipType = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart",
            relationships = listOf(
                WordPartRelationship(
                    relationshipId = "rId1",
                    relationshipType = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package",
                    target = "../embeddings/Microsoft_Excel_Sheet1.xlsx",
                ),
                WordPartRelationship(
                    relationshipId = "rId2",
                    relationshipType = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                    target = "https://example.org/image.png",
                    isExternal = true,
                ),
            ),
            packageRelationship = WordPartRelationship(
                relationshipId = "rId9",
                relationshipType = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml",
                target = "customXml/item1.xml",
            ),
        )

        val section = WordSection(
            pageWidth = 11906,
            pageHeight = 16838,
            orientation = "landscape",
            marginTop = 1000,
            marginRight = 1001,
            marginBottom = 1002,
            marginLeft = 1003,
            headerRefs = listOf(
                WordHeaderFooterRef(
                    refType = "default",
                    partName = "word/header1.xml",
                    text = "The header",
                ),
            ),
            footerRefs = listOf(
                WordHeaderFooterRef(
                    refType = "even",
                    partName = "word/footer1.xml",
                    text = "The footer",
                ),
            ),
            endBlockId = "b-table",
        )

        val styles = listOf(
            WordStyle(
                styleId = "Normal",
                name = "Normal",
                basedOn = "",
                styleType = "paragraph",
                numId = null,
                numLevel = 0,
            ),
            WordStyle(
                styleId = "Heading2",
                name = "heading 2",
                basedOn = "Normal",
                styleType = "paragraph",
                numId = "1",
                numLevel = 1,
            ),
        )

        val properties = WordProperties(
            title = "The Title",
            author = "Ada Lovelace",
            subject = "The subject",
            keywords = "alpha, beta",
            comments = "The comments",
            created = OffsetDateTime.parse("2024-01-01T00:00:00Z"),
            modified = OffsetDateTime.parse("2024-03-04T05:06:07+02:00"),
            revision = 7,
        )

        return WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b-title",
                    kind = WordBlockKind.TITLE,
                    text = "The Title",
                    runs = listOf(
                        WordRun(text = "The ", bold = true),
                        WordRun(text = "Title", italic = true),
                    ),
                ),
                WordBlock(
                    blockId = "b-heading",
                    kind = WordBlockKind.HEADING,
                    text = "Section One",
                    level = 2,
                    style = "Heading2",
                    runs = listOf(WordRun(text = "Section One")),
                ),
                WordBlock(
                    blockId = "b-list",
                    kind = WordBlockKind.LIST_ITEM,
                    text = "First item",
                    level = 1,
                    listMarker = ListMarker.BULLET,
                    listGroup = "group-1",
                    numberFormat = "bullet",
                    numberText = "•",
                    style = "ListParagraph",
                    runs = listOf(WordRun(text = "First item")),
                ),
                WordBlock(
                    blockId = "b-numbered",
                    kind = WordBlockKind.LIST_ITEM,
                    text = "Second item",
                    level = 0,
                    listMarker = ListMarker.NUMBER,
                    runs = listOf(WordRun(text = "Second item")),
                ),
                WordBlock(
                    blockId = "b-table",
                    kind = WordBlockKind.TABLE,
                    table = fullTable(),
                ),
                WordBlock(
                    blockId = "b-quote",
                    kind = WordBlockKind.QUOTE,
                    text = "Quoted passage",
                    runs = listOf(WordRun(text = "Quoted passage")),
                ),
                WordBlock(
                    blockId = "b-code",
                    kind = WordBlockKind.CODE,
                    text = "print(1)",
                    runs = listOf(WordRun(text = "print(1)", font = "Consolas", size = 20)),
                ),
                WordBlock(
                    blockId = "b-break",
                    kind = WordBlockKind.PAGE_BREAK,
                ),
                WordBlock(
                    blockId = "b-para",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Annotated passage",
                    runs = listOf(
                        WordRun(
                            text = "Annotated passage",
                            underline = true,
                            superscript = true,
                            font = "Georgia",
                            size = 24,
                            color = "FF0000",
                            highlight = "yellow",
                            hyperlink = "https://example.org/source",
                            footnoteRef = "fn-1",
                            endnoteRef = "en-1",
                            revision = inserted,
                            commentRefs = listOf("c-1"),
                        ),
                    ),
                ),
                WordBlock(
                    blockId = "b-image",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Picture",
                    runs = listOf(
                        WordRun(
                            text = "Picture",
                            imageRef = "m-1",
                            anchorExtent = 914400 to 457200,
                        ),
                    ),
                ),
                WordBlock(
                    blockId = "b-part",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Chart",
                    runs = listOf(
                        WordRun(
                            text = "Chart",
                            partRef = "word/charts/chart1.xml",
                            anchorExtent = 100 to 200,
                        ),
                    ),
                ),
                WordBlock(
                    blockId = "b-equation",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Equation",
                    runs = listOf(
                        WordRun(
                            text = "Equation",
                            subscript = true,
                            strike = true,
                            equationXml = "<m:oMath/>",
                            formatChangeXml = "<w:rPrChange/>",
                        ),
                    ),
                ),
                WordBlock(
                    blockId = "b-deleted",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Live",
                    runs = listOf(
                        WordRun(text = "Live"),
                        WordRun(text = "Gone", revision = deleted),
                    ),
                ),
            ),
            properties = properties,
            footnotes = listOf(footnote),
            endnotes = listOf(endnote),
            comments = listOf(comment),
            headers = listOf("The header"),
            footers = listOf("The footer"),
            sections = listOf(section),
            styles = styles,
            media = listOf(media),
            parts = listOf(part),
        )
    }

    /** A table with a header row, a merged cell and a table nested in a cell. */
    private fun fullTable(): WordTable = WordTable(
        headerRows = 1,
        columnWidths = listOf(1000, 2000),
        rows = listOf(
            WordTableRow(
                cells = listOf(
                    WordTableCell(
                        text = "Head",
                        isHeader = true,
                        width = 1000,
                        runs = listOf(WordRun(text = "Head", bold = true)),
                    ),
                    WordTableCell(
                        text = "Head",
                        isHeader = true,
                        width = 2000,
                        runs = listOf(WordRun(text = "Head", bold = true)),
                    ),
                ),
            ),
            WordTableRow(
                cells = listOf(
                    WordTableCell(
                        text = "Merged",
                        rowSpan = 2,
                        width = 1000,
                        runs = listOf(WordRun(text = "Merged")),
                    ),
                    WordTableCell(
                        text = "Value",
                        width = 2000,
                        runs = listOf(WordRun(text = "Value")),
                        tables = listOf(
                            WordTable(
                                rows = listOf(
                                    WordTableRow(
                                        cells = listOf(
                                            WordTableCell(
                                                text = "Nested",
                                                runs = listOf(WordRun(text = "Nested")),
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            WordTableRow(
                cells = listOf(
                    WordTableCell(isMergeContinuation = true),
                    WordTableCell(
                        text = "Second",
                        width = 2000,
                        runs = listOf(WordRun(text = "Second")),
                    ),
                ),
            ),
        ),
    )
}
