package lmeditor.word

import lmeditor.core.WordRenderError
import java.io.ByteArrayInputStream
import java.util.zip.ZipInputStream
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The writer's contract: a package Word accepts, written deterministically.
 */
class WordWriterTest {

    private fun sampleDocument(): WordDocument = WordDocument(
        blocks = listOf(
            WordBlock(
                blockId = "b0001",
                kind = WordBlockKind.TITLE,
                text = "The Title",
                runs = listOf(WordRun(text = "The Title")),
            ),
            WordBlock(
                blockId = "b0002",
                kind = WordBlockKind.HEADING,
                text = "A Heading",
                level = 1,
                runs = listOf(WordRun(text = "A Heading")),
            ),
            WordBlock(
                blockId = "b0003",
                kind = WordBlockKind.PARAGRAPH,
                text = "A paragraph.",
                runs = listOf(WordRun(text = "A paragraph.")),
            ),
            WordBlock(
                blockId = "b0004",
                kind = WordBlockKind.LIST_ITEM,
                text = "First item",
                level = 0,
                listMarker = ListMarker.NUMBER,
                listGroup = "g1",
                runs = listOf(WordRun(text = "First item")),
            ),
            WordBlock(
                blockId = "b0005",
                kind = WordBlockKind.LIST_ITEM,
                text = "Second item",
                level = 0,
                listMarker = ListMarker.NUMBER,
                listGroup = "g1",
                runs = listOf(WordRun(text = "Second item")),
            ),
            WordBlock(
                blockId = "b0006",
                kind = WordBlockKind.TABLE,
                table = WordTable(
                    rows = listOf(
                        WordTableRow(
                            cells = listOf(
                                WordTableCell(text = "Evidence", isHeader = true),
                                WordTableCell(text = "Verdict", isHeader = true),
                            ),
                        ),
                        WordTableRow(
                            cells = listOf(
                                WordTableCell(text = "chunk-abc"),
                                WordTableCell(text = "entailed"),
                            ),
                        ),
                    ),
                    headerRows = 1,
                    columnWidths = listOf(4680, 4680),
                ),
            ),
            WordBlock(
                blockId = "b0007",
                kind = WordBlockKind.PARAGRAPH,
                text = "A cited claim.1",
                runs = listOf(
                    WordRun(text = "A cited claim."),
                    WordRun(text = "1", superscript = true, footnoteRef = "f1"),
                ),
            ),
        ),
        footnotes = listOf(
            WordFootnote(
                footnoteId = "f1",
                text = "the cited passage [chunk-abc]",
                runs = listOf(WordRun(text = "the cited passage [chunk-abc]")),
            ),
        ),
    )

    private fun partsOf(bytes: ByteArray): Map<String, String> {
        val parts = linkedMapOf<String, String>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                if (entry.isDirectory) {
                    zip.closeEntry()
                    continue
                }
                parts[entry.name] = zip.readBytes().toString(Charsets.UTF_8)
                zip.closeEntry()
            }
        }
        return parts
    }

    // ------------------------------------------------------------------
    // The package
    // ------------------------------------------------------------------

    @Test
    fun `output is a zip package`() {
        val bytes = WordWriter().write(sampleDocument())
        assertTrue(partsOf(bytes).containsKey("word/document.xml"))
    }

    @Test
    fun `output has the expected parts`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        for (name in listOf(
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/_rels/document.xml.rels",
            "word/styles.xml",
            "word/numbering.xml",
            "word/footnotes.xml",
            "docProps/core.xml",
        )) {
            assertTrue(parts.containsKey(name), "the package must carry $name")
        }
    }

    @Test
    fun `every emitted xml part is well formed`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        for ((name, payload) in parts) {
            if (!name.endsWith(".xml") && !name.endsWith(".rels")) continue
            val factory = javax.xml.parsers.DocumentBuilderFactory.newInstance()
            factory.isNamespaceAware = true
            factory.setFeature(javax.xml.XMLConstants.FEATURE_SECURE_PROCESSING, true)
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
            try {
                factory.newDocumentBuilder().parse(ByteArrayInputStream(payload.toByteArray(Charsets.UTF_8)))
            } catch (exc: Exception) {
                throw AssertionError("the part $name is not well formed: ${exc.message}", exc)
            }
        }
    }

    // ------------------------------------------------------------------
    // Styles and numbering
    // ------------------------------------------------------------------

    @Test
    fun `headings use the real heading styles`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.getValue("word/document.xml").contains("<w:pStyle w:val=\"Heading1\"/>"))
        assertTrue(parts.getValue("word/styles.xml").contains("w:styleId=\"Heading1\""))
    }

    @Test
    fun `heading levels reach the style definition`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val styles = parts.getValue("word/styles.xml")
        for (level in 1..9) {
            assertTrue(styles.contains("w:styleId=\"Heading$level\""), "Heading$level must exist")
        }
    }

    @Test
    fun `numbering part is created`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val numbering = parts.getValue("word/numbering.xml")
        assertTrue(numbering.contains("<w:abstractNum"))
        assertTrue(numbering.contains("<w:num w:numId="))
    }

    @Test
    fun `the default style does not inherit from itself`() {
        // The default style is the root of the style hierarchy: a `w:basedOn`
        // that named the style itself would make the hierarchy invalid
        // (ECMA-376 17.7.4.3), and Word rejects it.
        val styles = partsOf(WordWriter().write(sampleDocument())).getValue("word/styles.xml")
        val normal = styles.substringAfter("w:styleId=\"Normal\"").substringBefore("</w:style>")
        assertFalse(normal.contains("w:basedOn"), "the default style must not inherit: $normal")
    }

    @Test
    fun `a style that inherits from the default keeps its inheritance`() {
        val styles = partsOf(WordWriter().write(sampleDocument())).getValue("word/styles.xml")
        assertTrue(styles.contains("<w:basedOn w:val=\"Normal\"/>"))
    }

    @Test
    fun `a custom style cannot inherit from itself and the omitted edge is reported`() {
        val document = sampleDocument().copy(
            styles = listOf(WordStyle(styleId = "Custom", name = "Custom style", basedOn = "Custom")),
        )
        val writer = WordWriter()
        val bytes = writer.write(document)
        val styles = partsOf(bytes).getValue("word/styles.xml")
        val custom = styles.substringAfter("w:styleId=\"Custom\"").substringBefore("</w:style>")
        assertTrue(custom.contains("<w:name w:val=\"Custom style\"/>"), custom)
        assertFalse(custom.contains("w:basedOn"), custom)
        assertEquals(listOf("style (Custom) inherits from itself"), writer.lastOmissions)
        assertContentEquals(bytes, writer.write(document))
        assertEquals(listOf("style (Custom) inherits from itself"), writer.lastOmissions)
    }

    @Test
    fun `a custom style keeps its valid inheritance without an omission`() {
        val document = sampleDocument().copy(
            styles = listOf(
                WordStyle(styleId = "Base", basedOn = "Normal"),
                WordStyle(styleId = "Custom", basedOn = "Base"),
            ),
        )
        val writer = WordWriter()
        val styles = partsOf(writer.write(document)).getValue("word/styles.xml")
        val custom = styles.substringAfter("w:styleId=\"Custom\"").substringBefore("</w:style>")
        assertTrue(custom.contains("<w:basedOn w:val=\"Base\"/>"), custom)
        assertTrue(writer.lastOmissions.isEmpty(), writer.lastOmissions.toString())
    }

    @Test
    fun `list items reference a numbering id`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.getValue("word/document.xml").contains("<w:numPr>"))
    }

    @Test
    fun `a nested list item writes its level as ilvl`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.LIST_ITEM,
                    text = "Nested",
                    level = 1,
                    listMarker = ListMarker.BULLET,
                    runs = listOf(WordRun(text = "Nested")),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        assertTrue(parts.getValue("word/document.xml").contains("<w:ilvl w:val=\"1\"/>"))
    }

    @Test
    fun `a shared numbering group uses one numbering id`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val numbering = parts.getValue("word/numbering.xml")
        assertEquals(1, Regex("<w:num w:numId=").findAll(numbering).count())
    }

    @Test
    fun `a separate numbering group restarts the sequence`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.LIST_ITEM,
                    text = "One",
                    level = 0,
                    listMarker = ListMarker.NUMBER,
                    listGroup = "g1",
                    runs = listOf(WordRun(text = "One")),
                ),
                WordBlock(
                    blockId = "b0002",
                    kind = WordBlockKind.LIST_ITEM,
                    text = "Two",
                    level = 0,
                    listMarker = ListMarker.NUMBER,
                    listGroup = "g2",
                    runs = listOf(WordRun(text = "Two")),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val numbering = parts.getValue("word/numbering.xml")
        assertEquals(2, Regex("<w:num w:numId=").findAll(numbering).count())
        assertTrue(numbering.contains("<w:startOverride w:val=\"1\"/>"))
    }

    // ------------------------------------------------------------------
    // Tables
    // ------------------------------------------------------------------

    @Test
    fun `table is emitted with its rows and cells`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val document = parts.getValue("word/document.xml")
        assertTrue(document.contains("<w:tbl"))
        assertTrue(document.contains("Evidence"))
        assertTrue(document.contains("entailed"))
    }

    @Test
    fun `header row is flagged for repetition`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.getValue("word/document.xml").contains("<w:tblHeader/>"))
    }

    @Test
    fun `a continuation covers every column of the merge it continues`() {
        // The merged region is as wide as the origin that started it. A
        // continuation that covered less would leave a column of the region
        // uncovered, and the cell after it would be written inside the region.
        val document = WordReader().readBytes(WordFixtures.buildMergeRegionPackage())
        val body = partsOf(WordWriter().write(document)).getValue("word/document.xml")
        val secondRow = body.substringAfter("</w:tr>").substringBefore("</w:tr>")
        assertTrue(secondRow.contains("<w:gridSpan w:val=\"2\"/>"), "the region is two columns wide: $secondRow")
        assertTrue(secondRow.contains("<w:vMerge/>"), "the cell continues the merge: $secondRow")
    }

    @Test
    fun `the cell after a merged region is written after the region`() {
        val document = WordReader().readBytes(WordFixtures.buildMergeRegionPackage())
        val body = partsOf(WordWriter().write(document)).getValue("word/document.xml")
        val secondRow = body.substringAfter("</w:tr>").substringBefore("</w:tr>")
        val region = secondRow.indexOf("<w:vMerge/>")
        val payload = secondRow.indexOf("MERGE-REGION-PAYLOAD")
        assertTrue(payload > region, "the payload cell follows the merged region: $secondRow")
    }

    @Test
    fun `a table does not redeclare the namespace of the document part`() {
        // The root of the document part already declares `xmlns:w` and
        // `xmlns:r`. A table or a cell that declared them again would still be
        // the same XML, but the reference writer builds its tables with lxml,
        // which drops the redundant declaration when the element is moved into
        // the body. The two writers would then disagree on the bytes of the
        // document part for no gain.
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val document = parts.getValue("word/document.xml")
        assertTrue(document.contains("<w:tbl>"), "the table must not carry attributes")
        assertTrue(document.contains("<w:tc>"), "the cell must not carry attributes")
        assertFalse(document.contains("<w:tbl xmlns"))
        assertFalse(document.contains("<w:tc xmlns"))
    }

    // ------------------------------------------------------------------
    // Runs and links
    // ------------------------------------------------------------------

    @Test
    fun `run formatting reaches the xml`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Bold",
                    runs = listOf(WordRun(text = "Bold", bold = true)),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        assertTrue(parts.getValue("word/document.xml").contains("<w:b/>"))
    }

    @Test
    fun `hyperlink relationship is external`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "A link",
                    runs = listOf(WordRun(text = "A link", hyperlink = "https://example.org/source")),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val rels = parts.getValue("word/_rels/document.xml.rels")
        assertTrue(rels.contains("TargetMode=\"External\""))
        assertTrue(rels.contains("https://example.org/source"))
        assertTrue(rels.contains("/hyperlink"))
        assertTrue(parts.getValue("word/document.xml").contains("<w:hyperlink r:id="))
    }

    // ------------------------------------------------------------------
    // Footnotes
    // ------------------------------------------------------------------

    @Test
    fun `footnotes part is created`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.containsKey("word/footnotes.xml"))
    }

    @Test
    fun `footnote reference is emitted`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.getValue("word/document.xml").contains("<w:footnoteReference w:id="))
    }

    @Test
    fun `footnotes part is declared in the content types`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        assertTrue(parts.getValue("[Content_Types].xml").contains("/word/footnotes.xml"))
    }

    @Test
    fun `footnotes part is related from the document`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val rels = parts.getValue("word/_rels/document.xml.rels")
        assertTrue(rels.contains("/footnotes"))
        assertTrue(rels.contains("Target=\"footnotes.xml\""))
    }

    // ------------------------------------------------------------------
    // Core properties
    // ------------------------------------------------------------------

    @Test
    fun `core properties are written`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "Body",
                    runs = listOf(WordRun(text = "Body")),
                ),
            ),
            properties = WordProperties(title = "A Title", author = "An Author", revision = 4),
        )
        val parts = partsOf(WordWriter().write(document))
        val core = parts.getValue("docProps/core.xml")
        assertTrue(core.contains("<dc:title>A Title</dc:title>"))
        assertTrue(core.contains("<dc:creator>An Author</dc:creator>"))
        assertTrue(core.contains("<cp:revision>4</cp:revision>"))
    }

    @Test
    fun `a document without timestamps claims none`() {
        val parts = partsOf(WordWriter().write(sampleDocument()))
        val core = parts.getValue("docProps/core.xml")
        assertTrue(!core.contains("dcterms:created"))
        assertTrue(!core.contains("dcterms:modified"))
    }

    // ------------------------------------------------------------------
    // Determinism
    // ------------------------------------------------------------------

    @Test
    fun `writing twice produces identical bytes`() {
        val document = sampleDocument()
        assertContentEquals(WordWriter().write(document), WordWriter().write(document))
    }

    @Test
    fun `determinism holds across writer instances`() {
        val document = sampleDocument()
        assertContentEquals(WordWriter().write(document), WordWriter(deterministic = true).write(document))
    }

    @Test
    fun `deterministic output pins the epoch`() {
        val bytes = WordWriter().write(sampleDocument())
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                assertEquals(
                    315_532_800_000L,
                    entry.time,
                    "the member ${entry.name} must carry the fixed timestamp",
                )
                zip.closeEntry()
            }
        }
    }

    // ------------------------------------------------------------------
    // Table separation
    // ------------------------------------------------------------------

    @Test
    fun `two adjacent tables are separated by a paragraph`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(rows = listOf(WordTableRow(cells = listOf(WordTableCell(text = "one"))))),
                ),
                WordBlock(
                    blockId = "b0002",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(rows = listOf(WordTableRow(cells = listOf(WordTableCell(text = "two"))))),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        val firstTable = body.indexOf("<w:tbl")
        val firstEnd = body.indexOf("</w:tbl>", firstTable)
        val secondTable = body.indexOf("<w:tbl", firstEnd)
        val between = body.substring(firstEnd, secondTable)
        assertTrue(between.contains("<w:p/>"), "two adjacent tables need an empty paragraph between them")
    }

    @Test
    fun `a table followed by a paragraph is not given a second one`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(rows = listOf(WordTableRow(cells = listOf(WordTableCell(text = "one"))))),
                ),
                WordBlock(
                    blockId = "b0002",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "after",
                    runs = listOf(WordRun(text = "after")),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        val firstEnd = body.indexOf("</w:tbl>")
        val after = body.substring(firstEnd, body.indexOf("</w:body>"))
        assertEquals(1, Regex("<w:p[ />]").findAll(after).count())
    }

    // ------------------------------------------------------------------
    // Failures
    // ------------------------------------------------------------------

    @Test
    fun `a hyperlink run without a target is refused`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "A link",
                    runs = listOf(WordRun(text = "A link", hyperlink = "   ")),
                ),
            ),
        )
        assertFailsWith<WordRenderError> { WordWriter().write(document) }
    }

    @Test
    fun `a run that references an unknown footnote is refused by the model`() {
        assertFailsWith<lmeditor.core.ValidationError> {
            WordDocument(
                blocks = listOf(
                    WordBlock(
                        blockId = "b0001",
                        kind = WordBlockKind.PARAGRAPH,
                        text = "",
                        runs = listOf(WordRun(text = "", footnoteRef = "missing")),
                    ),
                ),
            )
        }
    }

    // ------------------------------------------------------------------
    // The round trip
    // ------------------------------------------------------------------

    @Test
    fun `a document written and read back keeps its text, headings, tables and footnotes`() {
        val original = sampleDocument()
        val bytes = WordWriter().write(original)
        val reread = WordReader().readBytes(bytes)

        assertEquals(original.plainText(), reread.plainText())

        val originalHeadings = original.blocks.filter { it.kind == WordBlockKind.HEADING }
            .map { it.level to it.text }
        val rereadHeadings = reread.blocks.filter { it.kind == WordBlockKind.HEADING }
            .map { it.level to it.text }
        assertEquals(originalHeadings, rereadHeadings)

        val originalTable = original.blocks.first { it.table != null }.table!!
        val rereadTable = reread.blocks.first { it.table != null }.table!!
        assertEquals(originalTable.rows.size, rereadTable.rows.size)
        assertEquals(
            originalTable.rows.map { row -> row.cells.map { it.text } },
            rereadTable.rows.map { row -> row.cells.map { it.text } },
        )
        assertEquals(originalTable.headerRows, rereadTable.headerRows)

        assertEquals(1, reread.footnotes.size)
        assertEquals("the cited passage [chunk-abc]", reread.footnotes[0].text)
    }

    @Test
    fun `a written list keeps its marker and its group`() {
        val bytes = WordWriter().write(sampleDocument())
        val reread = WordReader().readBytes(bytes)
        val items = reread.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(2, items.size)
        assertTrue(items.all { it.listMarker == ListMarker.NUMBER })
        assertEquals(items[0].listGroup, items[1].listGroup)
    }

    @Test
    fun `the cell after a merged region survives a read write read round trip`() {
        // The marker is the only text in the cell that follows the merged
        // region. If the reader or the writer lost the cell, the marker would
        // be gone for good.
        val first = WordReader().readBytes(WordFixtures.buildMergeRegionPackage())
        val second = WordReader().readBytes(WordWriter().write(first))
        val table = second.blocks.first { it.table != null }.table!!
        assertEquals("MERGE-REGION-PAYLOAD", table.rows[1].cells[1].text)
        assertEquals("MERGED-ORIGIN", table.rows[0].cells[0].text)
        assertEquals(2, table.rows[0].cells[0].rowSpan)
        assertTrue(table.rows[1].cells[0].isMergeContinuation)
    }

    // ------------------------------------------------------------------
    // The carried parts
    // ------------------------------------------------------------------

    @Test
    fun `a part the document reached keeps the id it was reached by`() {
        val document = documentWithPart(
            WordPart(
                partName = "customXml/item2.xml",
                contentType = "application/xml",
                data = "<root/>".toByteArray(),
                relationshipId = "rId7",
                relationshipType = REL_TYPE_CUSTOM_XML,
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val rels = parts.getValue("word/_rels/document.xml.rels")
        assertTrue(
            rels.contains("Id=\"rId7\""),
            "the part should keep the id the document reached it by: $rels",
        )
        assertTrue(
            rels.contains("Target=\"../customXml/item2.xml\""),
            "the target should be relative to the document's own directory: $rels",
        )
    }

    @Test
    fun `a part keeps the relationships it holds to other parts`() {
        val document = WordDocument(
            parts = listOf(
                WordPart(
                    partName = "word/charts/chart1.xml",
                    contentType = "application/xml",
                    data = "<chart/>".toByteArray(),
                    relationshipId = "rId4",
                    relationshipType = REL_TYPE_CHART,
                    relationships = listOf(
                        WordPartRelationship(
                            relationshipId = "rId1",
                            relationshipType = REL_TYPE_CHART_STYLE,
                            target = "word/charts/style1.xml",
                        ),
                    ),
                ),
                WordPart(
                    partName = "word/charts/style1.xml",
                    contentType = "application/xml",
                    data = "<style/>".toByteArray(),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val partRels = parts.getValue("word/charts/_rels/chart1.xml.rels")
        assertTrue(
            partRels.contains("Id=\"rId1\""),
            "the part's own relationship should be kept: $partRels",
        )
        assertTrue(
            partRels.contains("Target=\"style1.xml\""),
            "the target should be relative to the part's own directory: $partRels",
        )
    }

    @Test
    fun `a relationship that points at a part the package does not hold is named`() {
        val document = documentWithPart(
            WordPart(
                partName = "word/charts/chart1.xml",
                contentType = "application/xml",
                data = "<chart/>".toByteArray(),
                relationshipId = "rId4",
                relationshipType = REL_TYPE_CHART,
                relationships = listOf(
                    WordPartRelationship(
                        relationshipId = "rId1",
                        relationshipType = REL_TYPE_CHART_STYLE,
                        target = "word/charts/missing.xml",
                    ),
                ),
            ),
        )
        val writer = WordWriter()
        val parts = partsOf(writer.write(document))
        assertFalse(
            parts.containsKey("word/charts/_rels/chart1.xml.rels"),
            "a relationship to nothing must not be written: ${parts.keys}",
        )
        assertTrue(
            writer.lastOmissions.any { it.contains("word/charts/missing.xml") },
            "the loss must be named: ${writer.lastOmissions}",
        )
    }

    @Test
    fun `a part the package already holds is not written twice`() {
        val document = documentWithPart(
            WordPart(
                partName = "customXml/item1.xml",
                contentType = "application/xml",
                data = "<item/>".toByteArray(),
            ),
        )
        val writer = WordWriter()
        val parts = partsOf(writer.write(document))
        assertEquals(
            1,
            parts.keys.count { it == "customXml/item1.xml" },
            "the package must hold the name exactly once: ${parts.keys}",
        )
        assertTrue(
            writer.lastOmissions.any { it.contains("customXml/item1.xml") },
            "the part the package already holds must be named: ${writer.lastOmissions}",
        )
    }

    @Test
    fun `a part the document did not reach is reached from the package root`() {
        val document = documentWithPart(
            WordPart(
                partName = "docProps/thumbnail.jpeg",
                contentType = "image/jpeg",
                data = byteArrayOf(1, 2, 3),
                packageRelationship = WordPartRelationship(
                    relationshipId = "rId3",
                    relationshipType = REL_TYPE_THUMBNAIL,
                    target = "docProps/thumbnail.jpeg",
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val packageRels = parts.getValue("_rels/.rels")
        assertTrue(
            packageRels.contains("Target=\"docProps/thumbnail.jpeg\""),
            "the package root should reach the part: $packageRels",
        )
    }

    @Test
    fun `an embedded object writes a package the reader can parse`() {
        // The `o` prefix of the VML image data is only declared on the
        // object. An undeclared prefix is not a difference in rendering: it
        // makes the whole part unparseable.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "an object",
                    runs = listOf(WordRun(text = "an object", partRef = "word/embeddings/o.bin")),
                ),
            ),
            parts = listOf(
                WordPart(
                    partName = "word/embeddings/o.bin",
                    contentType = "application/octet-stream",
                    data = byteArrayOf(1, 2, 3),
                    relationshipId = "rId5",
                    relationshipType = REL_TYPE_PACKAGE,
                ),
            ),
        )
        val bytes = WordWriter().write(document)
        val parts = partsOf(bytes)
        val body = parts.getValue("word/document.xml")
        assertTrue(
            body.contains("xmlns:o="),
            "the object should declare the office namespace: $body",
        )
        // The reader parses the package, so a package it cannot parse is a
        // package nobody can open.
        val reread = WordReader().readBytes(bytes)
        assertEquals("an object", reread.plainText())
    }

    @Test
    fun `an external relationship is written as a closed element`() {
        // `+` binds tighter than `if` in Kotlin, so the `/>` of an external
        // relationship used to be swallowed by the `else` branch. The result
        // was a relationship part that no parser accepts.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "a link",
                    runs = listOf(
                        WordRun(text = "a link", hyperlink = "https://example.org/paper"),
                    ),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val rels = parts.getValue("word/_rels/document.xml.rels")
        assertTrue(
            rels.contains("TargetMode=\"External\"/>"),
            "an external relationship must be closed: $rels",
        )
    }

    // ------------------------------------------------------------------
    // The capabilities the reference writer defines
    // ------------------------------------------------------------------

    @Test
    fun `a header keeps the role its reference type gives it`() {
        // A: the role is the reference type, not the order of the parts.
        val document = WordReader().readBytes(WordFixtures.buildReferencedHeaderPackage())
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        assertTrue(
            body.contains("<w:headerReference w:type=\"default\""),
            "the default header must be referenced as the default: $body",
        )
        assertTrue(
            body.contains("<w:headerReference w:type=\"first\""),
            "the first-page header must be referenced as the first page: $body",
        )
        assertTrue(
            body.contains("<w:headerReference w:type=\"even\""),
            "the even-page header must be referenced as the even pages: $body",
        )
        assertTrue(
            body.contains("<w:titlePg/>"),
            "a first-page header needs a first page: $body",
        )
        assertTrue(
            parts.getValue("word/settings.xml").contains("<w:evenAndOddHeaders/>"),
            "an even-page header needs pages that differ: ${parts.getValue("word/settings.xml")}",
        )
        // And the roles survive the round trip.
        val again = WordReader().readBytes(WordWriter().write(document))
        assertEquals(
            listOf("Default page header", "First page header", "Even page header"),
            again.headers,
        )
        assertEquals(
            mapOf(
                "default" to "Default page header",
                "first" to "First page header",
                "even" to "Even page header",
            ),
            again.sections[0].headerRefs.associate { it.refType to it.text },
        )
    }

    @Test
    fun `a comment is anchored in the body`() {
        // B: a comment Word cannot see is a comment that is not there.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "a claim",
                    runs = listOf(WordRun(text = "a claim", commentRefs = listOf("c1"))),
                ),
            ),
            comments = listOf(
                WordComment(commentId = "c1", author = "An author", text = "a note"),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        assertTrue(
            body.contains("<w:commentRangeStart w:id=\"0\"/>"),
            "the range must open: $body",
        )
        assertTrue(
            body.contains("<w:commentRangeEnd w:id=\"0\"/>"),
            "the range must close: $body",
        )
        assertTrue(
            body.contains("<w:commentReference w:id=\"0\"/>"),
            "the comment must be referenced: $body",
        )
    }

    @Test
    fun `a run that points at a missing image is reported and the write carries on`() {
        // C: a loss the writer cannot carry is named, not fatal.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "",
                    runs = listOf(WordRun(text = "", imageRef = "word/media/missing.png")),
                ),
            ),
        )
        val writer = WordWriter()
        val parts = partsOf(writer.write(document))
        assertTrue(
            writer.lastOmissions.any { it.contains("image") },
            "the loss must be named: ${writer.lastOmissions}",
        )
        assertTrue(
            parts.containsKey("word/document.xml"),
            "the document must still be written: ${parts.keys}",
        )
    }

    @Test
    fun `a section beyond the first is reported`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "a document",
                    runs = listOf(WordRun(text = "a document")),
                ),
            ),
            sections = listOf(WordSection(), WordSection(pageWidth = 1000)),
        )
        val writer = WordWriter()
        writer.write(document)
        assertTrue(
            writer.lastOmissions.any { it.contains("section") },
            "the loss must be named: ${writer.lastOmissions}",
        )
    }

    @Test
    fun `the omission register belongs to one render and not to the writer`() {
        val writer = WordWriter()
        writer.write(
            WordDocument(
                blocks = listOf(
                    WordBlock(
                        blockId = "b0001",
                        kind = WordBlockKind.PARAGRAPH,
                        text = "",
                        runs = listOf(WordRun(text = "", imageRef = "word/media/missing.png")),
                    ),
                ),
            ),
        )
        assertTrue(writer.lastOmissions.isNotEmpty())
        writer.write(
            WordDocument(
                blocks = listOf(
                    WordBlock(
                        blockId = "b0001",
                        kind = WordBlockKind.PARAGRAPH,
                        text = "a document",
                        runs = listOf(WordRun(text = "a document")),
                    ),
                ),
            ),
        )
        assertTrue(writer.lastOmissions.isEmpty(), writer.lastOmissions.toString())
    }

    @Test
    fun `an equation that cannot be parsed falls back to the flattened text`() {
        // C: markup the writer cannot parse costs the equation, not the
        // document.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "E=mc2",
                    runs = listOf(
                        WordRun(
                            text = "E=mc2",
                            equationXml = "<m:oMath><m:r><m:t>E=mc2</m:t></m:r>",
                        ),
                    ),
                ),
            ),
        )
        val writer = WordWriter()
        val parts = partsOf(writer.write(document))
        assertTrue(
            writer.lastOmissions.any { it.contains("equation") },
            "the loss must be named: ${writer.lastOmissions}",
        )
        val body = parts.getValue("word/document.xml")
        assertTrue(body.contains("E=mc2"), "the text must survive: $body")
        assertFalse(body.contains("m:oMath"), "the equation must not be written: $body")
    }

    @Test
    fun `the package root keeps the id a part was reached by`() {
        // D: the id the reader saw the part with is the id the package uses.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "a document",
                    runs = listOf(WordRun(text = "a document")),
                ),
            ),
            parts = listOf(
                WordPart(
                    partName = "docProps/custom.xml",
                    contentType = "application/xml",
                    data = "<custom/>".toByteArray(),
                    packageRelationship = WordPartRelationship(
                        relationshipId = "rId1",
                        relationshipType = REL_TYPE_CUSTOM_PROPERTIES,
                        target = "docProps/custom.xml",
                    ),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val root = parts.getValue("_rels/.rels")
        assertTrue(
            root.contains("Id=\"rId1\"") && root.contains("docProps/custom.xml"),
            "the part must keep the id it was reached by: $root",
        )
        assertEquals(
            1,
            Regex("""Type="[^"]*/officeDocument"""").findAll(root).count(),
            "the package must still reach its document: $root",
        )
    }

    @Test
    fun `a chart is drawn at the size it was recorded at`() {
        // E: the recorded extent wins over the default.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "",
                    runs = listOf(
                        WordRun(
                            text = "",
                            partRef = "word/charts/chart1.xml",
                            anchorExtent = 5486400 to 3200400,
                        ),
                    ),
                ),
            ),
            parts = listOf(
                WordPart(
                    partName = "word/charts/chart1.xml",
                    contentType = "application/xml",
                    data = "<c:chartSpace/>".toByteArray(),
                    relationshipId = "rId4",
                    relationshipType = REL_TYPE_CHART,
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        assertTrue(body.contains("cx=\"5486400\""), "the width must survive: $body")
        assertTrue(body.contains("cy=\"3200400\""), "the height must survive: $body")
    }

    @Test
    fun `a merge the model names only by its origin gets its continuation`() {
        // F: a merge that is only named by its origin is still a merge.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(
                        rows = listOf(
                            WordTableRow(
                                cells = listOf(
                                    WordTableCell(text = "origin", rowSpan = 2),
                                    WordTableCell(text = "right"),
                                ),
                            ),
                            WordTableRow(cells = listOf(WordTableCell(text = "lower right"))),
                        ),
                    ),
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        assertTrue(
            body.contains("<w:vMerge w:val=\"restart\"/>"),
            "the merge must start: $body",
        )
        assertTrue(
            Regex("<w:vMerge\\s*/>").containsMatchIn(body),
            "the merge must continue: $body",
        )
    }

    @Test
    fun `an alternative format chunk is written as a body reference`() {
        // G: the chunk is a body reference, not a run.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "",
                    runs = listOf(WordRun(text = "", partRef = "word/afchunk1.html")),
                ),
            ),
            parts = listOf(
                WordPart(
                    partName = "word/afchunk1.html",
                    contentType = "text/html",
                    data = "<html/>".toByteArray(),
                    relationshipId = "rId24",
                    relationshipType = REL_TYPE_ALTERNATIVE_FORMAT_CHUNK,
                ),
            ),
        )
        val parts = partsOf(WordWriter().write(document))
        val body = parts.getValue("word/document.xml")
        assertTrue(
            body.contains("<w:altChunk r:id=\"rId24\"/>"),
            "the chunk must be written as a body reference: $body",
        )
    }

    private fun documentWithPart(part: WordPart): WordDocument = WordDocument(
        blocks = listOf(
            WordBlock(
                blockId = "b0001",
                kind = WordBlockKind.PARAGRAPH,
                text = "a part",
                runs = listOf(WordRun(text = "a part")),
            ),
        ),
        parts = listOf(part),
    )

    private companion object {
        const val REL_TYPE_CUSTOM_XML =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
        const val REL_TYPE_CHART =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
        const val REL_TYPE_CHART_STYLE =
            "http://schemas.microsoft.com/office/2011/relationships/chartStyle"
        const val REL_TYPE_THUMBNAIL =
            "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
        const val REL_TYPE_PACKAGE =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
        const val REL_TYPE_CUSTOM_PROPERTIES =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
        const val REL_TYPE_ALTERNATIVE_FORMAT_CHUNK =
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/aFChunk"
    }
}
