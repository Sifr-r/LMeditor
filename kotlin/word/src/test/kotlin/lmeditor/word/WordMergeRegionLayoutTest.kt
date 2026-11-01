package lmeditor.word

import java.io.ByteArrayInputStream
import java.util.zip.ZipInputStream
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The writer's layout of a **ragged** merged region.
 *
 * A merged region is as wide as its origin spans, but only as wide as the row
 * has room for: the reader clamps the continuation it hands the model to the
 * room the row had. A row that therefore holds a continuation which continues
 * nothing -- a cell the merged region covers -- is *ragged*: the cells of the
 * row do not tile the grid the way the origin does.
 *
 * The writer has to lay such a row out the way the reader reads it back:
 *
 * * the region covers the columns the model's continuation covers, so the row
 *   fills the grid (W12) instead of growing past it;
 * * a cell that lands inside the region is written as an ordinary cell, because
 *   the reader reads a cell inside a merged region back as a continuation of
 *   it -- so the region and the cells around it keep the shape the model has;
 * * the round trip therefore changes nothing: the same grid, the same spans,
 *   the same text.
 */
class WordMergeRegionLayoutTest {

    /**
     * A table whose grid is three columns wide, whose origin spans two of them,
     * and whose second row holds a cell the merged region covers.
     *
     * The row below the origin continues the merge and holds two more cells:
     * the first of them lands inside the merged region -- the origin spans two
     * columns and the row's own cells already fill the third -- so Word never
     * displays its text.
     */
    private fun raggedPackage(): ByteArray = WordFixtures.buildPackage(
        """
<w:tbl>
<w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr>
<w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>RAGGED-ORIGIN</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Third column</w:t></w:r></w:p></w:tc>
</w:tr>
<w:tr>
<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
<w:tc><w:p><w:r><w:t>Covered by the merge</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>Third column</w:t></w:r></w:p></w:tc>
</w:tr>
</w:tbl>
""",
    )

    @Test
    fun `every row of a ragged merged table fills the grid`() {
        // W12: a row that covers fewer columns than the grid has is a
        // malformed table -- Word re-lays it out, and the document the reader
        // opens is no longer the document the model describes.
        val document = WordReader().readBytes(raggedPackage())
        val table = document.blocks.first { it.table != null }.table!!
        val grid = gridColumns(tableXml(WordWriter().write(document)))

        assertEquals(3, grid, "the table keeps the grid it declares")
        assertEquals(
            listOf(3, 3),
            rowWidths(tableXml(WordWriter().write(document))),
            "every row fills the grid",
        )
        assertEquals(
            grid,
            table.columnWidths.size,
            "the grid the writer emits is the grid the model declares",
        )
    }

    @Test
    fun `a continuation that continues nothing does not widen the row`() {
        // The cell the merged region covers belongs to the region: it is
        // written as an ordinary cell inside it, so the row keeps the width
        // the grid has instead of growing past it.
        val document = WordReader().readBytes(raggedPackage())
        val body = tableXml(WordWriter().write(document))
        val secondRow = Regex("<w:tr>(.*?)</w:tr>", RegexOption.DOT_MATCHES_ALL)
            .findAll(body)
            .toList()[1]
            .groupValues[1]

        assertEquals(3, rowWidths(body)[1], "the row covers the three columns of the grid")
        assertTrue(
            !secondRow.contains("<w:gridSpan w:val=\"2\"/>"),
            "the region is as wide as the model says, not as wide as the origin: $secondRow",
        )
    }

    @Test
    fun `a ragged merged region survives the round trip`() {
        val document = WordReader().readBytes(raggedPackage())
        val table = document.blocks.first { it.table != null }.table!!

        val again = WordReader().readBytes(WordWriter().write(document))
            .blocks.first { it.table != null }.table!!

        assertEquals(shape(table), shape(again), "the round trip changes nothing")
        assertEquals(
            table.columnWidths,
            again.columnWidths,
            "the grid the round trip leaves behind is the grid it was given",
        )
    }

    @Test
    fun `the wide merge fixture's ragged table survives the round trip`() {
        // The second table of the wide-merge fixture is the ragged variant: the
        // origin spans the whole grid and the row below holds a cell the merge
        // covers, so the row is ragged in the same way.
        val document = WordReader().readBytes(WordFixtures.buildWideMergePackage())
        val ragged = document.blocks.filter { it.table != null }.map { it.table!! }[1]
        // The package holds two tables: the canonical merged block first, the
        // ragged variant second. The measurements below are about the ragged
        // one, so they are taken over the second table.
        val body = tableXml(WordWriter().write(document), 1)

        assertEquals(2, gridColumns(body), "the ragged table keeps its two columns")
        assertEquals(listOf(2, 2), rowWidths(body), "every row of the ragged table fills the grid")

        val again = WordReader().readBytes(WordWriter().write(document))
            .blocks.filter { it.table != null }.map { it.table!! }[1]
        assertEquals(shape(ragged), shape(again), "the round trip changes nothing")
    }

    @Test
    fun `a region that is followed by an ordinary cell keeps the width of its origin`() {
        // A hand-authored model may name a continuation that is narrower than
        // the origin it continues. The region keeps the width of its origin
        // then, so the ordinary cell that follows lands outside the region and
        // keeps its text -- the reader swallows a cell that lands inside a
        // merged region.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(
                        rows = listOf(
                            WordTableRow(
                                cells = listOf(
                                    WordTableCell(text = "MERGED-ORIGIN", rowSpan = 2, colSpan = 2),
                                ),
                            ),
                            WordTableRow(
                                cells = listOf(
                                    WordTableCell(colSpan = 1, isMergeContinuation = true),
                                    WordTableCell(text = "lower right", colSpan = 1),
                                ),
                            ),
                        ),
                        columnWidths = listOf(3000, 3000),
                    ),
                ),
            ),
        )

        val body = tableXml(WordWriter().write(document))
        val secondRow = Regex("<w:tr>(.*?)</w:tr>", RegexOption.DOT_MATCHES_ALL)
            .findAll(body)
            .toList()[1]
            .groupValues[1]

        assertTrue(
            secondRow.contains("<w:gridSpan w:val=\"2\"/>"),
            "the region is as wide as its origin: $secondRow",
        )

        val again = WordReader().readBytes(WordWriter().write(document))
            .blocks.first { it.table != null }.table!!
        assertEquals(
            listOf(listOf("MERGED-ORIGIN"), listOf("", "lower right")),
            again.rows.map { row -> row.cells.map { it.text } },
            "the cell that follows the region keeps its text",
        )
    }

    @Test
    fun `a region after an ordinary cell keeps the cell it covers an ordinary cell`() {
        // The shape the backend's parity harness holds the tier to: the origin
        // of the region is the second cell of the first row, so the region
        // follows an ordinary cell and covers the second and third columns of
        // the grid.  The row below continues the region over the one column it
        // has room for and carries a cell the region covers, so the row is
        // ragged in the same way.
        //
        // The cell that lands inside the region is written as an ordinary
        // cell.  Written as a continuation, the region would claim the column
        // the cell sits on as well, and the reader would read the row back a
        // column wider than the grid: the row would no longer tile it.
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b0001",
                    kind = WordBlockKind.TABLE,
                    table = WordTable(
                        rows = listOf(
                            WordTableRow(
                                cells = listOf(
                                    WordTableCell(text = "ORDINARY", colSpan = 1),
                                    WordTableCell(text = "ORIGIN", colSpan = 2, rowSpan = 2),
                                ),
                            ),
                            WordTableRow(
                                cells = listOf(
                                    WordTableCell(text = "BELOW", colSpan = 1),
                                    WordTableCell(colSpan = 1, isMergeContinuation = true),
                                    WordTableCell(colSpan = 1, isMergeContinuation = true),
                                ),
                            ),
                        ),
                        columnWidths = listOf(3000, 3000, 3000),
                    ),
                ),
            ),
        )
        val table = document.blocks.first { it.table != null }.table!!

        val body = tableXml(WordWriter().write(document))
        val secondRow = Regex("<w:tr>(.*?)</w:tr>", RegexOption.DOT_MATCHES_ALL)
            .findAll(body)
            .toList()[1]
            .groupValues[1]

        assertEquals(3, gridColumns(body), "the table keeps the three columns it declares")
        assertEquals(listOf(3, 3), rowWidths(body), "every row fills the grid")
        assertEquals(
            1,
            Regex("<w:vMerge/>").findAll(secondRow).count(),
            "only the region continues the merge: $secondRow",
        )
        assertTrue(
            !secondRow.substringAfterLast("<w:tc>").contains("<w:vMerge"),
            "the cell inside the region is written as an ordinary cell: $secondRow",
        )

        val again = WordReader().readBytes(WordWriter().write(document))
            .blocks.first { it.table != null }.table!!
        assertEquals(shape(table), shape(again), "the round trip changes nothing")
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /** The shape of a table: the spans, the merge flags and the text of its cells. */
    private fun shape(table: WordTable): List<List<String>> =
        table.rows.map { row ->
            row.cells.map { cell ->
                "${cell.colSpan}/${cell.rowSpan}/${cell.isMergeContinuation}/${cell.text}"
            }
        }

    /** The `w:tbl` element at [index] of a written package, as text. */
    private fun tableXml(bytes: ByteArray, index: Int = 0): String =
        Regex("<w:tbl>.*?</w:tbl>", RegexOption.DOT_MATCHES_ALL)
            .findAll(partsOf(bytes).getValue("word/document.xml"))
            .toList()[index]
            .value

    /** The number of grid columns the written table declares. */
    private fun gridColumns(table: String): Int = Regex("<w:gridCol").findAll(table).count()

    /** The number of grid columns every written row covers. */
    private fun rowWidths(table: String): List<Int> =
        Regex("<w:tr>(.*?)</w:tr>", RegexOption.DOT_MATCHES_ALL)
            .findAll(table)
            .map { row ->
                Regex("<w:tc>.*?</w:tc>", RegexOption.DOT_MATCHES_ALL)
                    .findAll(row.groupValues[1])
                    .sumOf { cell ->
                        Regex("<w:gridSpan w:val=\"(\\d+)\"/>")
                            .find(cell.value)
                            ?.groupValues
                            ?.get(1)
                            ?.toInt()
                            ?: 1
                    }
            }
            .toList()

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
}
