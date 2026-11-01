package lmeditor.library

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The bounded brief, ported from `backend/tests/test_library.py`.
 */
class BookBriefTest {

    /**
     * The brief names the book and every one of its sections.
     */
    @Test
    fun theBriefNamesTheBookAndItsSections() {
        val store = newStore()
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body.", title = "Intro")
        store.addSection("book_01", "section_0001", "The body.", title = "Middle")

        val brief = buildBrief(store, "book_01")
        assertTrue(brief.contains("Book: A book"))
        assertTrue(brief.contains("Sections: 2"))
        assertTrue(brief.contains("1. Intro"))
        assertTrue(brief.contains("2. Middle"))
    }

    /**
     * The brief is bounded, and the oldest entries are the ones that are
     * dropped.
     */
    @Test
    fun theBriefIsBoundedAndDropsTheOldestEntries() {
        val store = newStore()
        store.create("book_01", "A book")
        for (index in 0 until 20) {
            store.addSection(
                "book_01",
                "section_${"%04d".format(index)}",
                "The body.",
                title = "Section ${"%04d".format(index)}",
            )
        }
        val brief = buildBrief(store, "book_01", maxChars = 200)
        assertTrue(brief.length <= 200, "the brief must fit its budget, was ${brief.length}")
        assertTrue(brief.contains("Section 0019"), "the most recent section must survive")
        assertTrue(!brief.contains("Section 0000"), "the oldest section must be dropped")
    }

    /**
     * The brief reads no section body: it is derived from the index, so its
     * cost does not grow with the length of the book.
     */
    @Test
    fun theBriefReadsNoSectionBody() {
        val store = newStore()
        store.create("book_01", "A book")
        store.addSection("book_01", "section_0000", "The body.", title = "Intro")
        store.addSection("book_01", "section_0001", "The body.", title = "Middle")
        val before = store.readCount
        buildBrief(store, "book_01")
        assertEquals(before, store.readCount)
    }

    /**
     * Merging an addition keeps the brief bounded, and trims it from the
     * front so the most recent information survives.
     */
    @Test
    fun mergingTrimsTheBriefFromTheFront() {
        val merged = mergeBrief("old line one\nold line two", "new line", maxChars = 20)
        assertTrue(merged.length <= 20)
        assertTrue(merged.contains("new line"))
        assertTrue(!merged.contains("old line one"))
    }

    /** An empty addition leaves the brief as it stands. */
    @Test
    fun anEmptyAdditionLeavesTheBriefAlone() {
        assertEquals("the brief", mergeBrief("the brief", "", maxChars = 100))
        assertEquals("the brief", mergeBrief("the brief", "   ", maxChars = 100))
    }

    /** A budget of zero yields an empty brief. */
    @Test
    fun aBudgetOfZeroYieldsAnEmptyBrief() {
        assertEquals("", mergeBrief("the brief", "more", maxChars = 0))
        val store = newStore()
        store.create("book_01", "A book")
        assertEquals("", buildBrief(store, "book_01", maxChars = 0))
    }

    /**
     * A single line that is longer than the budget on its own is cut from the
     * front, so the brief never exceeds its budget.
     */
    @Test
    fun aLongSingleLineIsCutFromTheFront() {
        val merged = mergeBrief("x".repeat(100), "y".repeat(100), maxChars = 10)
        assertTrue(merged.length <= 10)
    }
}
