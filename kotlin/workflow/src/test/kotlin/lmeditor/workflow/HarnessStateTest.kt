package lmeditor.workflow

import lmeditor.core.Source
import lmeditor.core.SourceKind
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The workflow state and its pure reducers.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_workflow.py` and
 * `backend/tests/test_workflow_options.py`.
 */
class HarnessStateTest {

    @Test
    fun `the initial state carries the seeded fields`() {
        val state = createInitialHarnessState(
            docId = "doc_100",
            topic = "Neural Architecture Search",
            rawTexts = listOf("Passage text for document 100."),
        )

        assertEquals("doc_100", state.docId)
        assertEquals("Neural Architecture Search", state.topic)
        assertEquals("initialized", state.status)
        assertEquals(1, state.rawTexts.size)
        assertTrue(state.sectionDrafts.isEmpty())
        assertFalse(state.requiresHitl)
        assertTrue(state.guidedResearch)
    }

    @Test
    fun `merge dicts merges without side effects`() {
        val first = mapOf("sec_1" to 1, "sec_2" to 2)
        val second = mapOf("sec_2" to 3, "sec_3" to 4)

        val merged = mergeDicts(first, second)

        assertEquals(mapOf("sec_1" to 1, "sec_2" to 3, "sec_3" to 4), merged)
        assertEquals(mapOf("sec_1" to 1, "sec_2" to 2), first)
    }

    @Test
    fun `append lists concatenates without side effects`() {
        val first = listOf("err_1")
        val second = listOf("err_2", "err_3")

        val appended = appendLists(first, second)

        assertEquals(listOf("err_1", "err_2", "err_3"), appended)
        assertEquals(listOf("err_1"), first)
    }

    @Test
    fun `the initial state carries an empty source list`() {
        val state = createInitialHarnessState(docId = "d1", topic = "t", rawTexts = emptyList())

        assertEquals(emptyList(), state.sources)
    }

    @Test
    fun `the initial state accepts and round-trips supplied sources`() {
        val source = Source(
            id = "src_seed",
            kind = SourceKind.TEXT,
            origin = "/tmp/x.txt",
            title = "x.txt",
        )

        val state = createInitialHarnessState(
            docId = "d2",
            topic = "t",
            rawTexts = emptyList(),
            sources = listOf(source),
        )

        assertEquals(1, state.sources.size)
        assertEquals("src_seed", state.sources[0].id)
    }

    @Test
    fun `the initial state seeds both option keys`() {
        val initial = createInitialHarnessState(docId = "d", topic = "t")

        assertEquals(3, initial.maxRetries)
        assertEquals(0.85, initial.thresholdFBeta)
    }

    @Test
    fun `the initial state stores explicit option values verbatim`() {
        val initial = createInitialHarnessState(
            docId = "d",
            topic = "t",
            maxRetries = 7,
            thresholdFBeta = 0.5,
        )

        assertEquals(7, initial.maxRetries)
        assertEquals(0.5, initial.thresholdFBeta)
    }

    @Test
    fun `a state that predates the option keys carries neither`() {
        val legacy = HarnessState(docId = "d", topic = "t")

        assertNull(legacy.maxRetries)
        assertNull(legacy.thresholdFBeta)
    }

    @Test
    fun `applying the same transition twice leaves the same state`() {
        val state = createInitialHarnessState(docId = "d", topic = "t")

        val once = state.copy(status = "sources_indexed")
        val twice = once.copy(status = "sources_indexed")

        assertEquals(once, twice)
    }
}
