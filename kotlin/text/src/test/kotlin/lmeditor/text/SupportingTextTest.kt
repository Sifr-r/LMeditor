package lmeditor.text

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * The supporting-text rule, ported from
 * `backend/src/lmeditor/provenance/service.py`.
 */
class SupportingTextTest {

    @Test
    fun theLongestSharedRunIsQuoted() {
        val result = SupportingText.supportingText(
            claimText = "the model is grounded",
            chunkText = "the model is grounded in evidence",
        )

        assertEquals("the model is grounded", result.quote)
        assertEquals(1.0, result.coverage)
    }

    @Test
    fun theQuoteIsAVerbatimSubstringOfThePassage() {
        // The quote is the passage's own text, not the claim's: it is what
        // the source says, and it has to be findable in the source.
        val result = SupportingText.supportingText(
            claimText = "Retrieval augmented generation grounds a language model in sources.",
            chunkText = "The retrieval pipeline grounds the model in its sources.",
        )

        assertEquals("model in", result.quote)
        assertEquals(0.14285714285714285, result.coverage)
    }

    @Test
    fun aSingleSharedWordIsNotAQuotation() {
        val result = SupportingText.supportingText(
            claimText = "single",
            chunkText = "single word here",
        )

        assertNull(result.quote)
        assertEquals(0.0, result.coverage)
    }

    @Test
    fun aRunOfFunctionWordsIsNotAQuotation() {
        // Three shared function words are three words, but they say nothing:
        // a quote made of them would earn the confidence of a real quotation.
        val result = SupportingText.supportingText(
            claimText = "it is the",
            chunkText = "it is the one that matters",
        )

        assertNull(result.quote)
        assertEquals(0.0, result.coverage)
    }

    @Test
    fun theCoverageIsTheShareOfTheClaimTheQuoteAccountsFor() {
        // The claim has five content words; the run covers two of them.
        val result = SupportingText.supportingText(
            claimText = "the quick brown fox and the lazy dog",
            chunkText = "the lazy dog sleeps",
        )

        assertEquals("the lazy dog", result.quote)
        assertEquals(0.4, result.coverage)
    }

    @Test
    fun theFirstOfTwoEquallyLongRunsWins() {
        val result = SupportingText.supportingText(
            claimText = "alpha beta gamma",
            chunkText = "alpha beta omega",
        )

        assertEquals("alpha beta", result.quote)
    }

    @Test
    fun passagesThatShareNothingAreNotQuoted() {
        val result = SupportingText.supportingText(
            claimText = "a claim about nothing",
            chunkText = "a passage about something else",
        )

        assertNull(result.quote)
        assertEquals(0.0, result.coverage)
    }

    @Test
    fun anEmptyTextHasNoSharedRun() {
        assertNull(SupportingText.supportingText("", "some text").quote)
        assertNull(SupportingText.supportingText("some text", "").quote)
        assertEquals(0.0, SupportingText.supportingText("", "some text").coverage)
    }

    @Test
    fun theQuoteKeepsTheAccentsAndTheDigitsOfThePassage() {
        val result = SupportingText.supportingText(
            claimText = "Ünicödé wörds 123",
            chunkText = "Ünicödé wörds 123 here",
        )

        assertEquals("Ünicödé wörds 123", result.quote)
        assertEquals(1.0, result.coverage)
    }

    @Test
    fun theNonBreakingSpaceIsWhitespace() {
        // Python's `\S` is the complement of Python's `\s`, and Python's
        // `\s` includes the non-breaking space. Java's whitespace does not,
        // so the tier spells Python's set out. The quote is still the
        // passage's own text, non-breaking space and all.
        val result = SupportingText.supportingText(
            claimText = "alpha\u00A0beta",
            chunkText = "alpha\u00A0beta gamma",
        )

        assertEquals("alpha\u00A0beta", result.quote)
        assertEquals(1.0, result.coverage)
    }
}
