package lmeditor.word

/**
 * The vertical-merge state of a table cell.
 */
internal enum class CellVerticalMerge {
    /** The cell is not part of a vertical merge. */
    NONE,

    /** The cell starts a vertical merge. */
    RESTART,

    /** The cell continues a vertical merge that started above it. */
    CONTINUE,
}

/**
 * One table cell, positioned on the table's grid.
 *
 * A cell occupies [colSpan] grid columns starting at [column]. A placement
 * whose [cell] is `null` is a continuation the writer synthesised for a merge
 * the model named only by its origin.
 */
internal data class CellPlacement(
    /** The cell the model supplied, or `null` for a synthesised continuation. */
    val cell: WordTableCell?,
    /** The number of grid columns the cell occupies. */
    val colSpan: Int,
    /** The vertical-merge state of the cell. */
    val verticalMerge: CellVerticalMerge,
    /** The grid column the cell starts on. */
    val column: Int,
)

/**
 * A vertical merge started by a cell, tracked over the rows it covers.
 */
private class MergeOrigin(
    /** The grid column the merge starts on. */
    val column: Int,
    /** The number of grid columns the merge covers. */
    val colSpan: Int,
    /** The first row the merge does not reach. */
    val lastRow: Int,
)

/**
 * Lay every row of [table] out on the grid, in column order.
 *
 * A table's grid is made of *grid columns*, not of cells: a cell occupies
 * `colSpan` columns starting at the column it is placed on, and the grid is
 * exactly as wide as the widest row needs it to be.
 *
 * A vertical merge is described twice in the model and the two mechanisms
 * cooperate here:
 *
 * * the cell that *starts* a merge carries `rowSpan > 1` and keeps its text; it
 *   marks its columns as covered in the rows below;
 * * the cells below it are flagged `isMergeContinuation` and carry no text of
 *   their own.
 *
 * A continuation cell is placed on the column its merge started in -- that is
 * the column the merge continues in -- and the merged region covers the
 * columns its origin spans, less the columns the continuation cells that
 * follow it inside the region cover, so the cells that follow it start after
 * the merged region. When the model supplies the continuation cells, they are
 * consumed at the covered grid position, so the two mechanisms never double
 * up. When it does not -- a hand-authored merge that only names the origin --
 * the writer synthesises the continuation cells, so the merge is rendered as a
 * merge instead of being flattened away.
 *
 * The layout is the reader's layout, read backwards. The reader reads a cell
 * that lands inside a merged region back as a continuation of that region, so
 * the model holds the cells of a ragged region as continuations: the region
 * the reader gave the model was that much narrower than the origin it
 * continues. The writer lays the region out that much narrower, and writes
 * those cells as ordinary cells inside it, so the row tiles the grid (W12)
 * and reading it back gives the model the writer was handed. A cell the model
 * does not mark as a continuation is never written inside a region: the
 * reader would swallow its text.
 */
internal fun layoutTable(table: WordTable): List<List<CellPlacement>> {
    val origins = mutableListOf<MergeOrigin>()
    val layout = mutableListOf<List<CellPlacement>>()
    for (rowIndex in table.rows.indices) {
        val (placements, newOrigins) = placeRow(table.rows[rowIndex], origins, rowIndex)
        layout.add(placements)
        origins.addAll(newOrigins)
    }
    return layout
}

/**
 * The width the continuation cells that follow a merged region cover inside it.
 *
 * The reader reads a cell that lands inside a merged region back as a
 * continuation of that region, so the model holds those cells as
 * continuations: the region the reader gave the model was that much narrower
 * than the origin it continues. The writer lays the region out that much
 * narrower, so the cells that follow tile the rest of the region and the row
 * keeps the width the grid has.
 *
 * Only the continuations that follow the region *immediately* are counted: a
 * cell that is not a continuation ends the region, because the writer places
 * such a cell outside the region, where the reader keeps its text.
 */
private fun widthInside(cells: List<WordTableCell>, from: Int): Int {
    var width = 0
    for (index in from until cells.size) {
        val cell = cells[index]
        if (!cell.isMergeContinuation) break
        width += cell.colSpan
    }
    return width
}

/**
 * Place the cells of one row on the grid.
 *
 * Returns the row's placements in column order together with the merges the
 * row starts.
 */
private fun placeRow(
    row: WordTableRow,
    origins: List<MergeOrigin>,
    rowIndex: Int,
): Pair<List<CellPlacement>, List<MergeOrigin>> {
    val openMerges = origins.filter { rowIndex < it.lastRow }.sortedBy { it.column }

    val taken = mutableSetOf<Int>()
    val matched = mutableListOf<Pair<Int, Int>>()
    val loose = mutableListOf<WordTableCell>()
    val covered = mutableListOf<Pair<WordTableCell, Int>>()
    val used = mutableSetOf<Int>()
    var region: MergeOrigin? = null
    var column = 0
    for ((cellIndex, cell) in row.cells.withIndex()) {
        if (cell.isMergeContinuation) {
            val index = matchMerge(openMerges, used, column)
            if (index == null) {
                val open = region
                if (open != null && column < open.column + open.colSpan) {
                    // A continuation cell that continues nothing but lands
                    // inside the merged region the row has already continued
                    // belongs to that region: the reader reads a cell inside a
                    // merged region back as a continuation of it, so it is
                    // written as an ordinary cell -- inside the region, so the
                    // row keeps the width the grid has.
                    covered.add(cell to column)
                    taken.addAll(column until column + cell.colSpan)
                    column += cell.colSpan
                    continue
                }
                // A continuation cell without a merge above it continues
                // nothing: it is placed like any other cell and is still
                // rendered as a continuing merge rather than dropped.
                loose.add(cell)
                column += cell.colSpan
            } else {
                used.add(index)
                val merge = openMerges[index]
                // The merged region covers the columns its origin spans, less
                // the columns the continuation cells that follow it inside the
                // region cover: the reader read those cells back as
                // continuations of the region, so the region it gave the model
                // was that much narrower than the origin, and the cells that
                // follow tile the rest of it. A row that follows a region with
                // an ordinary cell keeps the region as wide as its origin, so
                // that the ordinary cell lands outside it and keeps its text.
                val width = maxOf(1, merge.colSpan - widthInside(row.cells, cellIndex + 1))
                matched.add(merge.column to width)
                taken.addAll(merge.column until merge.column + width)
                region = merge
                column = maxOf(column, merge.column + width)
            }
        } else {
            loose.add(cell)
            column += cell.colSpan
        }
    }

    // A merge the row does not continue receives a synthesised continuation,
    // so that a model naming only the origin still renders as a merge.
    for (index in openMerges.indices) {
        if (index in used) continue
        val merge = openMerges[index]
        matched.add(merge.column to merge.colSpan)
        taken.addAll(merge.column until merge.column + merge.colSpan)
    }

    val placements = mutableListOf<CellPlacement>()
    val newOrigins = mutableListOf<MergeOrigin>()
    var cursor = 0
    for (cell in loose) {
        val position = nextFree(taken, cursor)
        taken.addAll(position until position + cell.colSpan)
        cursor = position + cell.colSpan
        if (cell.isMergeContinuation) {
            placements.add(
                CellPlacement(cell, cell.colSpan, CellVerticalMerge.CONTINUE, position),
            )
            continue
        }
        val startsMerge = cell.rowSpan > 1
        placements.add(
            CellPlacement(
                cell,
                cell.colSpan,
                if (startsMerge) CellVerticalMerge.RESTART else CellVerticalMerge.NONE,
                position,
            ),
        )
        if (startsMerge) {
            newOrigins.add(MergeOrigin(position, cell.colSpan, rowIndex + cell.rowSpan))
        }
    }
    for ((position, colSpan) in matched) {
        placements.add(CellPlacement(null, colSpan, CellVerticalMerge.CONTINUE, position))
    }
    for ((cell, position) in covered) {
        // The cell sits inside the merged region it belongs to, so it is
        // written as an ordinary cell: the reader reads a cell inside a merged
        // region back as a continuation of that region.
        placements.add(
            CellPlacement(cell, cell.colSpan, CellVerticalMerge.NONE, position),
        )
    }
    placements.sortBy { it.column }
    return placements to newOrigins
}

/**
 * Return the index of the merge a continuation cell continues.
 *
 * The merge whose region contains `column` wins. A merge that starts at or
 * after the column comes next, and the leftmost merge still free is the last
 * resort -- the merge survives even when the model puts the continuation cell
 * somewhere else in its row. `null` means that the cell continues no merge at
 * all.
 */
private fun matchMerge(openMerges: List<MergeOrigin>, used: Set<Int>, column: Int): Int? {
    for (index in openMerges.indices) {
        if (index in used) continue
        val merge = openMerges[index]
        if (merge.column <= column && column < merge.column + merge.colSpan) return index
    }
    for (index in openMerges.indices) {
        if (index in used) continue
        if (openMerges[index].column >= column) return index
    }
    for (index in openMerges.indices) {
        if (index !in used) return index
    }
    return null
}

/**
 * Return the first column at or after `cursor` that is still free.
 */
private fun nextFree(taken: Set<Int>, cursor: Int): Int {
    var column = cursor
    while (column in taken) column++
    return column
}
