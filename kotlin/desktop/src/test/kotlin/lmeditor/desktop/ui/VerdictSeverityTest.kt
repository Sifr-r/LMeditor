package lmeditor.desktop.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue
import lmeditor.core.Claim
import lmeditor.core.EntailmentStatus
import lmeditor.desktop.model.StudioState

/**
 * The verdict a passage is shown with.
 *
 * The order the enum is declared in is the wire contract and is not a
 * severity ranking: reading it as one made a passage that is also contradicted
 * render as entailed.
 */
class VerdictSeverityTest {

    @Test
    fun `the worst verdict wins whatever order the verdicts arrive in`() {
        assertEquals(
            EntailmentStatus.CONTRADICTED,
            worstVerdict(listOf(EntailmentStatus.ENTAILED, EntailmentStatus.CONTRADICTED)),
        )
        assertEquals(
            EntailmentStatus.CONTRADICTED,
            worstVerdict(listOf(EntailmentStatus.CONTRADICTED, EntailmentStatus.ENTAILED)),
        )
        assertEquals(
            EntailmentStatus.UNGROUNDED,
            worstVerdict(listOf(EntailmentStatus.ENTAILED, EntailmentStatus.UNGROUNDED)),
        )
        assertEquals(
            EntailmentStatus.NEUTRAL,
            worstVerdict(listOf(EntailmentStatus.ENTAILED, EntailmentStatus.NEUTRAL)),
        )
        assertNull(worstVerdict(emptyList()))
    }

    @Test
    fun `the severity order is not the order the enum is declared in`() {
        // The declaration order is ENTAILED, CONTRADICTED, NEUTRAL, UNGROUNDED.
        assertEquals(0, severityOf(EntailmentStatus.ENTAILED))
        assertTrue(severityOf(EntailmentStatus.NEUTRAL) > severityOf(EntailmentStatus.ENTAILED))
        assertTrue(severityOf(EntailmentStatus.UNGROUNDED) > severityOf(EntailmentStatus.NEUTRAL))
        assertTrue(severityOf(EntailmentStatus.CONTRADICTED) > severityOf(EntailmentStatus.UNGROUNDED))
    }

    @Test
    fun `a passage that is also contradicted is shown as contradicted`() {
        val state = StudioState(
            claims = listOf(
                Claim(
                    claimId = "claim_1",
                    sectionId = "sec_1",
                    text = "A claim the evidence supports.",
                    citedChunkIds = listOf("chunk_1"),
                    entailmentStatus = EntailmentStatus.ENTAILED,
                ),
                Claim(
                    claimId = "claim_2",
                    sectionId = "sec_1",
                    text = "A claim the evidence refutes.",
                    citedChunkIds = listOf("chunk_1"),
                    entailmentStatus = EntailmentStatus.CONTRADICTED,
                ),
            ),
        )
        assertEquals(EntailmentStatus.CONTRADICTED, chunkVerdicts(state)["chunk_1"])
    }

    @Test
    fun `a passage cited by one claim keeps that claim's verdict`() {
        val state = StudioState(
            claims = listOf(
                Claim(
                    claimId = "claim_1",
                    sectionId = "sec_1",
                    text = "A claim the evidence supports.",
                    citedChunkIds = listOf("chunk_1"),
                    entailmentStatus = EntailmentStatus.ENTAILED,
                ),
            ),
        )
        assertEquals(EntailmentStatus.ENTAILED, chunkVerdicts(state)["chunk_1"])
    }

    @Test
    fun `every verdict carries a mark of its own, so colour is never the only cue`() {
        val marks = EntailmentStatus.entries.map { verdictMark(it) } + verdictMark(null)
        assertEquals(marks.size, marks.distinct().size)
    }
}
