package lmeditor.desktop.render

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The rules the renderer follows.
 *
 * The citation rules are the shared ones: the markers the renderer draws are
 * the markers the drafter wrote and the verifier reads, and a test that
 * pins them here pins them for the whole system.
 */
class ManuscriptBlocksTest {

    @Test
    fun `a paragraph is one block`() {
        val blocks = ManuscriptBlocks.parse("The first line.\nThe second line.")
        assertEquals(1, blocks.size)
        assertEquals(BlockKind.PARAGRAPH, blocks[0].kind)
        assertEquals("The first line. The second line.", blocks[0].plainText())
    }

    @Test
    fun `a blank line separates two paragraphs`() {
        val blocks = ManuscriptBlocks.parse("First.\n\nSecond.")
        assertEquals(2, blocks.size)
        assertEquals("First.", blocks[0].plainText())
        assertEquals("Second.", blocks[1].plainText())
    }

    @Test
    fun `a heading keeps its level`() {
        val blocks = ManuscriptBlocks.parse("### A subheading")
        assertEquals(1, blocks.size)
        assertEquals(BlockKind.HEADING, blocks[0].kind)
        assertEquals(3, blocks[0].level)
        assertEquals("A subheading", blocks[0].plainText())
    }

    @Test
    fun `a list item and a quote are their own kinds`() {
        val blocks = ManuscriptBlocks.parse("- a point\n> a quotation")
        assertEquals(2, blocks.size)
        assertEquals(BlockKind.LIST_ITEM, blocks[0].kind)
        assertEquals("a point", blocks[0].plainText())
        assertEquals(BlockKind.QUOTE, blocks[1].kind)
        assertEquals("a quotation", blocks[1].plainText())
    }

    @Test
    fun `a fenced block is code and keeps its lines`() {
        val blocks = ManuscriptBlocks.parse("```\nline one\nline two\n```")
        assertEquals(1, blocks.size)
        assertEquals(BlockKind.CODE, blocks[0].kind)
        assertEquals("line one\nline two", blocks[0].plainText())
    }

    @Test
    fun `a citation marker becomes a citation span`() {
        val blocks = ManuscriptBlocks.parse("Rates fell [#chunk_1] sharply.")
        val citations = blocks[0].spans.filterIsInstance<InlineSpan.Citation>()
        assertEquals(1, citations.size)
        assertEquals(listOf("chunk_1"), citations[0].chunkIds)
        assertEquals("[#chunk_1]", citations[0].text)
    }

    @Test
    fun `a marker naming two passages carries both`() {
        val blocks = ManuscriptBlocks.parse("Both [#a, #b] agree.")
        val citations = blocks[0].spans.filterIsInstance<InlineSpan.Citation>()
        assertEquals(1, citations.size)
        assertEquals(listOf("a", "b"), citations[0].chunkIds)
    }

    @Test
    fun `the marker prefix is stripped from the chunk id`() {
        val blocks = ManuscriptBlocks.parse("See [cite: chunk_2] and [chunk_3].")
        val citations = blocks[0].spans.filterIsInstance<InlineSpan.Citation>()
        assertEquals(listOf("chunk_2"), citations[0].chunkIds)
        assertEquals(listOf("chunk_3"), citations[1].chunkIds)
    }

    @Test
    fun `a markdown link is not a citation`() {
        val blocks = ManuscriptBlocks.parse("A [link](https://example.test) is not a citation.")
        assertEquals(0, blocks[0].spans.filterIsInstance<InlineSpan.Citation>().size)
    }

    @Test
    fun `the text around a marker is preserved`() {
        val blocks = ManuscriptBlocks.parse("Rates fell [#chunk_1] sharply.")
        assertEquals("Rates fell [#chunk_1] sharply.", blocks[0].plainText())
    }

    @Test
    fun `bold, italic and code are their own styles`() {
        val blocks = ManuscriptBlocks.parse("A **bold** and an *italic* and `code`.")
        val styles = blocks[0].spans.filterIsInstance<InlineSpan.Text>().map { it.style }
        assertTrue(styles.contains(Emphasis.BOLD))
        assertTrue(styles.contains(Emphasis.ITALIC))
        assertTrue(styles.contains(Emphasis.CODE))
    }

    @Test
    fun `an empty text has no blocks`() {
        assertEquals(0, ManuscriptBlocks.parse("").size)
    }
}

/** The text of a block, with the markers as they were written. */
private fun ManuscriptBlock.plainText(): String =
    spans.joinToString("") { span ->
        when (span) {
            is InlineSpan.Text -> span.text
            is InlineSpan.Citation -> span.text
        }
    }
