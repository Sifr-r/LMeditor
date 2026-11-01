package lmeditor.word

import lmeditor.core.BibliographicRecord
import lmeditor.core.CitationLineage
import lmeditor.core.DocumentDraft
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The manuscript bridge: inline citation markers become superscript markers
 * with the cited passage attached as a footnote.
 */
class WordManuscriptTest {

    private fun section(
        id: String,
        title: String,
        content: String,
        order: Int = 0,
        citations: List<CitationLineage> = emptyList(),
        status: SectionStatus = SectionStatus.DRAFTING,
        isVerified: Boolean = false,
    ): SectionDraft = SectionDraft(
        sectionId = id,
        title = title,
        content = content,
        order = order,
        citations = citations,
        status = status,
        isVerified = isVerified,
    )

    private fun draft(vararg sections: SectionDraft): DocumentDraft = DocumentDraft(
        documentId = "doc-1",
        title = "The Manuscript",
        sections = sections.toList(),
    )

    private fun lineage(chunkId: String, quote: String? = null): CitationLineage = CitationLineage(
        lineageId = "lin-$chunkId",
        claimId = "claim-$chunkId",
        chunkId = chunkId,
        spanStart = 0,
        spanEnd = 10,
        exactQuote = quote,
    )

    // ------------------------------------------------------------------
    // Structure
    // ------------------------------------------------------------------

    @Test
    fun `the title becomes a title block`() {
        val document = WordManuscript.buildManuscriptDocument(draft())
        assertEquals(WordBlockKind.TITLE, document.blocks.first().kind)
        assertEquals("The Manuscript", document.blocks.first().text)
    }

    @Test
    fun `every section becomes a heading followed by its blocks`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section("s1", "First", "Some prose."),
                section("s2", "Second", "More prose.", order = 1),
            ),
        )
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }
        assertEquals(2, headings.size)
        assertEquals("First", headings[0].text)
        assertEquals("Second", headings[1].text)
        assertTrue(headings.all { it.level == 1 })
    }

    @Test
    fun `sections are ordered by order and then by id`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section("s2", "Second", "b", order = 1),
                section("s1", "First", "a", order = 0),
                section("s0", "Zeroth", "c", order = 0),
            ),
        )
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }.map { it.text }
        assertEquals(listOf("Zeroth", "First", "Second"), headings)
    }

    @Test
    fun `verified only drops the sections that did not pass verification`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section("s1", "Verified", "a", status = SectionStatus.VERIFIED),
                section("s2", "Approved", "b", status = SectionStatus.APPROVED),
                section("s3", "Drafting", "c", status = SectionStatus.DRAFTING),
            ),
            verifiedOnly = true,
        )
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }.map { it.text }
        assertEquals(listOf("Verified", "Approved"), headings)
    }

    @Test
    fun `an empty section says so rather than vanishing`() {
        val document = WordManuscript.buildManuscriptDocument(draft(section("s1", "Empty", "")))
        assertTrue(document.plainText().contains("Section has no drafted content."))
    }

    // ------------------------------------------------------------------
    // The manuscript syntax
    // ------------------------------------------------------------------

    @Test
    fun `a heading inside a section keeps its level`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "## A sub heading")),
        )
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }
        assertEquals(2, headings.size)
        assertEquals(2, headings[1].level)
        assertEquals("A sub heading", headings[1].text)
    }

    @Test
    fun `a bullet list item becomes a list item`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "- A bullet")),
        )
        val item = document.blocks.first { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(ListMarker.BULLET, item.listMarker)
        assertEquals("A bullet", item.text)
    }

    @Test
    fun `an indented list item is nested`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "- A bullet\n  - A nested bullet")),
        )
        val items = document.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(2, items.size)
        assertEquals(0, items[0].level)
        assertEquals(1, items[1].level)
    }

    @Test
    fun `a numbered list that continues shares a group`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "1. One\n2. Two")),
        )
        val items = document.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(2, items.size)
        assertTrue(items.all { it.listMarker == ListMarker.NUMBER })
        assertEquals(items[0].listGroup, items[1].listGroup)
    }

    @Test
    fun `a numbered list that restarts gets a new group`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "1. One\n2. Two\n\n1. Again")),
        )
        val items = document.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(3, items.size)
        assertEquals(items[0].listGroup, items[1].listGroup)
        assertTrue(items[2].listGroup != items[0].listGroup)
    }

    @Test
    fun `a number written in a non-ASCII digit is still a numbered item`() {
        // Python's `\d` is a Unicode decimal digit, and the frozen witness
        // reads it that way.  Java's `\d` is `[0-9]` unless the
        // Unicode-character-class flag is set, so a list written in
        // Arabic-Indic digits would silently become prose.
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "\u0663. Three\n\u0664. Four")),
        )
        val items = document.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(2, items.size)
        assertTrue(items.all { it.listMarker == ListMarker.NUMBER })
        assertEquals("Three", items[0].text)
        assertEquals("Four", items[1].text)
    }

    @Test
    fun `an item whose text is only a unicode space is prose, not a list item`() {
        // Python's `\s` reaches the no-break space and the other Unicode
        // separators, so a marker followed by nothing but whitespace is not a
        // list item.  Java's ASCII `\s` would say otherwise.
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "lead\n- \u00a0\n- \u001c")),
        )
        val items = document.blocks.filter { it.kind == WordBlockKind.LIST_ITEM }
        assertEquals(0, items.size)
        assertTrue(document.blocks.any { it.kind == WordBlockKind.PARAGRAPH })
    }

    @Test
    fun `a block quote becomes a quote`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "> A quotation")),
        )
        val quote = document.blocks.first { it.kind == WordBlockKind.QUOTE }
        assertEquals("A quotation", quote.text)
    }

    @Test
    fun `a fenced code block keeps its content verbatim`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "```\nconst a = **not bold**;\n```")),
        )
        val code = document.blocks.first { it.kind == WordBlockKind.CODE }
        assertEquals("const a = **not bold**;", code.text)
    }

    @Test
    fun `a markdown table becomes a word table with a header row`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "| A | B |\n| --- | --- |\n| 1 | 2 |")),
        )
        val table = document.blocks.first { it.table != null }.table!!
        assertEquals(2, table.rows.size)
        assertEquals(1, table.headerRows)
        assertEquals(listOf("A", "B"), table.rows[0].cells.map { it.text })
        assertEquals(listOf("1", "2"), table.rows[1].cells.map { it.text })
    }

    @Test
    fun `a line that merely carries a pipe stays prose`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "Prose with a | pipe.")),
        )
        assertTrue(document.blocks.none { it.table != null })
        assertTrue(document.plainText().contains("Prose with a | pipe."))
    }

    @Test
    fun `inline emphasis becomes run formatting`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "Plain **bold** and *italic*.")),
        )
        val runs = document.blocks.first { it.kind == WordBlockKind.PARAGRAPH }.runs
        assertTrue(runs.any { it.bold && it.text == "bold" })
        assertTrue(runs.any { it.italic && it.text == "italic" })
    }

    @Test
    fun `an unpaired marker stays literal text`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "A **bold that never closes.")),
        )
        assertTrue(document.plainText().contains("**bold that never closes."))
    }

    // ------------------------------------------------------------------
    // Citations
    // ------------------------------------------------------------------

    @Test
    fun `a citation marker becomes a superscript number`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "A claim [#chunk-1] here.")),
        )
        val runs = document.blocks.first { it.kind == WordBlockKind.PARAGRAPH }.runs
        val marker = runs.first { it.superscript }
        assertEquals("1", marker.text)
    }

    @Test
    fun `the marker text itself never survives`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "A claim [#chunk-1] here.")),
        )
        assertTrue(!document.plainText().contains("[#chunk-1]"))
    }

    @Test
    fun `markers are numbered in first appearance order`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "One [#b] two [#a] three [#b].")),
        )
        val markers = document.blocks.flatMap { it.runs }.filter { it.superscript }
        assertEquals(listOf("1", "2", "1"), markers.map { it.text })
    }

    @Test
    fun `a cited passage becomes a footnote`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section(
                    "s1",
                    "Section",
                    "A claim [#chunk-1] here.",
                    citations = listOf(lineage("chunk-1", "The passage.")),
                ),
            ),
        )
        assertEquals(1, document.footnotes.size)
        assertEquals("The passage. [chunk-1]", document.footnotes[0].text)
        val marker = document.blocks.flatMap { it.runs }.first { it.superscript }
        assertEquals(document.footnotes[0].footnoteId, marker.footnoteRef)
    }

    @Test
    fun `a chunk without a passage gets no footnote`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section(
                    "s1",
                    "Section",
                    "A claim [#chunk-1] here.",
                    citations = listOf(lineage("chunk-1", null)),
                ),
            ),
        )
        assertTrue(document.footnotes.isEmpty())
        val marker = document.blocks.flatMap { it.runs }.first { it.superscript }
        assertEquals(null, marker.footnoteRef)
    }

    @Test
    fun `an unknown chunk id is still numbered`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "A claim [#unknown] here.")),
        )
        val marker = document.blocks.flatMap { it.runs }.first { it.superscript }
        assertEquals("1", marker.text)
        assertEquals(null, marker.footnoteRef)
        assertTrue(document.footnotes.isEmpty())
    }

    @Test
    fun `a marker that abuts a word is separated from it`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "word[#chunk-1]")),
        )
        val paragraph = document.blocks.first { it.kind == WordBlockKind.PARAGRAPH }
        assertEquals("word 1", paragraph.text)
    }

    @Test
    fun `two adjacent markers are kept apart`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "[#a][#b]")),
        )
        val markers = document.blocks.flatMap { it.runs }.filter { it.superscript }
        assertEquals(listOf("1", "2"), markers.map { it.text })
    }

    @Test
    fun `a marker inside a table cell is consumed by the same ledger`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(
                section(
                    "s1",
                    "Section",
                    "| A | B |\n| --- | --- |\n| one [#chunk-1] | two |",
                    citations = listOf(lineage("chunk-1", "The passage.")),
                ),
            ),
        )
        assertEquals(1, document.footnotes.size)
        val table = document.blocks.first { it.table != null }.table!!
        assertEquals("one 1", table.rows[1].cells[0].text)
    }

    // ------------------------------------------------------------------
    // The bibliography
    // ------------------------------------------------------------------

    @Test
    fun `a bibliography becomes a references heading and one paragraph per record`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "Prose.")),
            bibliography = listOf(
                BibliographicRecord(
                    title = "A Paper",
                    authors = listOf("An Author", "Another Author"),
                    year = 2024,
                    containerTitle = "A Journal",
                    volume = "3",
                    issue = "1",
                    pages = "10-20",
                    doi = "10.1000/xyz",
                ),
            ),
        )
        val headings = document.blocks.filter { it.kind == WordBlockKind.HEADING }
        assertEquals("References", headings.last().text)
        val text = document.plainText()
        assertTrue(text.contains("An Author, Another Author"))
        assertTrue(text.contains("A Paper"))
        assertTrue(text.contains("2024"))
    }

    @Test
    fun `a record without authors gets the anonymous placeholder`() {
        val document = WordManuscript.buildManuscriptDocument(
            draft(section("s1", "Section", "Prose.")),
            bibliography = listOf(BibliographicRecord(title = "An Anonymous Paper")),
        )
        assertTrue(document.plainText().contains("Anon. An Anonymous Paper"))
    }

    @Test
    fun `no bibliography means no references heading`() {
        val document = WordManuscript.buildManuscriptDocument(draft(section("s1", "Section", "Prose.")))
        assertTrue(document.blocks.none { it.kind == WordBlockKind.HEADING && it.text == "References" })
    }

    // ------------------------------------------------------------------
    // The bridge is pure
    // ------------------------------------------------------------------

    @Test
    fun `the inputs are never mutated`() {
        val sections = listOf(
            section("s2", "Second", "b", order = 1),
            section("s1", "First", "a", order = 0),
        )
        val document = DocumentDraft(documentId = "doc-1", title = "T", sections = sections)
        WordManuscript.buildManuscriptDocument(document)
        assertEquals(listOf("s2", "s1"), document.sections.map { it.sectionId })
    }
}
