package lmeditor.library

import lmeditor.core.SourceChunk
import lmeditor.core.WorkingSetRole
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The bounded working set, ported from `backend/tests/test_library.py`.
 *
 * The bound is the point of this module: a book of any size is edited one
 * section at a time, and the tests below measure that -- not the size of the
 * result, but the number of bodies that were read to build it.
 */
class WorkingSetTest {

    /** The working set holds the target section and its immediate neighbours. */
    @Test
    fun theWorkingSetHoldsTheTargetAndItsNeighbours() {
        val store = storeWithSections(5)
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0002")
        assertEquals("book_01", workingSet.bookId)
        assertEquals("section_0002", workingSet.targetSectionId)
        assertEquals(
            listOf("section_0001", "section_0002", "section_0003"),
            workingSet.sections.map { it.sectionId },
        )
        assertEquals(WorkingSetRole.PREVIOUS, workingSet.sections[0].role)
        assertEquals(WorkingSetRole.TARGET, workingSet.sections[1].role)
        assertEquals(WorkingSetRole.NEXT, workingSet.sections[2].role)
    }

    /** The neighbours on either side can be widened. */
    @Test
    fun theNeighboursCanBeWidened() {
        val store = storeWithSections(7)
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0003", neighbours = 2)
        assertEquals(
            listOf(
                "section_0001",
                "section_0002",
                "section_0003",
                "section_0004",
                "section_0005",
            ),
            workingSet.sections.map { it.sectionId },
        )
    }

    /** A target at the edge of the book has fewer neighbours, not an error. */
    @Test
    fun aTargetAtTheEdgeHasFewerNeighbours() {
        val store = storeWithSections(3)
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0000")
        assertEquals(
            listOf("section_0000", "section_0001"),
            workingSet.sections.map { it.sectionId },
        )
    }

    /**
     * Only the sections in the working set are read.
     *
     * This is the bound that matters: the size of the working set does not
     * grow with the size of the book.
     */
    @Test
    fun onlyTheSectionsInTheWorkingSetAreRead() {
        val store = storeWithSections(50)
        val before = store.readCount
        WorkingSetBuilder(store).build("book_01", "section_0025")
        assertEquals(3, store.readCount - before)
    }

    /** The number of bodies read is `2 * neighbours + 1`, whatever the book holds. */
    @Test
    fun theNumberOfBodiesReadIsBounded() {
        for (size in listOf(20, 200)) {
            val store = storeWithSections(size)
            val before = store.readCount
            WorkingSetBuilder(store).build("book_01", "section_0010", neighbours = 2)
            assertEquals(5, store.readCount - before, "a book of $size sections must cost the same")
        }
    }

    /** The passages are the ones the index returns, and there are no more than asked for. */
    @Test
    fun thePassagesComeFromTheIndex() {
        val store = storeWithSections(3)
        val index = PassageIndex { query, topK ->
            (1..topK).map { index -> passage("passage_$index") }
        }
        val workingSet = WorkingSetBuilder(store, index).build(
            bookId = "book_01",
            sectionId = "section_0001",
            maxPassages = 3,
        )
        assertEquals(3, workingSet.passages.size)
        assertEquals(hexId("passage_1"), workingSet.passages[0].chunkId)
    }

    /** Without an index the working set carries no passages. */
    @Test
    fun withoutAnIndexThereAreNoPassages() {
        val store = storeWithSections(3)
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0001")
        assertTrue(workingSet.passages.isEmpty())
    }

    /** The brief of the book is part of the working set, bounded. */
    @Test
    fun theBriefIsPartOfTheWorkingSet() {
        val store = storeWithSections(3)
        store.updateBrief("book_01", "b".repeat(100))
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0001", maxBriefChars = 20)
        assertEquals(20, workingSet.brief.length)
    }

    /** A section body is truncated to the bound that applies to it. */
    @Test
    fun aSectionBodyIsTruncated() {
        val store = storeWithSections(3)
        store.writeSection("book_01", "section_0001", "x".repeat(500))
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0001", maxSectionChars = 30)
        assertEquals(30, workingSet.sections[1].content.length)
    }

    /** The size of the working set is reported, so a caller can measure it. */
    @Test
    fun theSizeOfTheWorkingSetIsReported() {
        val store = storeWithSections(3)
        store.updateBrief("book_01", "a brief")
        val workingSet = WorkingSetBuilder(store).build("book_01", "section_0001")
        val expected = workingSet.sections.sumOf { it.content.length } +
            workingSet.passages.sumOf { it.text.length } +
            workingSet.brief.length
        assertEquals(expected, workingSet.contextChars)
    }

    /** A negative bound is refused. */
    @Test
    fun aNegativeBoundIsRefused() {
        val store = storeWithSections(3)
        assertFailsWith<IllegalArgumentException> {
            WorkingSetBuilder(store).build("book_01", "section_0001", neighbours = -1)
        }
        assertFailsWith<IllegalArgumentException> {
            WorkingSetBuilder(store).build("book_01", "section_0001", maxPassages = -1)
        }
    }
}

/** Return a store holding a book of [size] sections. */
private fun storeWithSections(size: Int): BookStore {
    val store = newStore()
    store.create("book_01", "A book")
    for (index in 0 until size) {
        store.addSection(
            "book_01",
            "section_${"%04d".format(index)}",
            "The body of section $index.",
            title = "Section $index",
        )
    }
    store.updateBrief("book_01", "A brief.")
    return store
}

/** Return a chunk with the given id. */
private fun passage(id: String): SourceChunk = SourceChunk(
    chunkId = hexId(id),
    documentId = "doc_01",
    text = "A passage.",
    charStart = 0,
    charEnd = 10,
    byteStart = 0,
    byteEnd = 10,
    tokenCount = 2,
    startChar = 0,
    endChar = 10,
)
