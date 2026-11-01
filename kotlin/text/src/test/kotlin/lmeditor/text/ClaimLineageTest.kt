package lmeditor.text

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The claim-lineage rule, ported from
 * `backend/src/lmeditor/provenance/service.py`'s `lineage_for_claim`.
 */
class ClaimLineageTest {

    private val reef = RetrievalChunk(
        chunkId = "reef",
        text = "Coral reefs shelter a quarter of all marine species and their larvae.",
    )
    private val mangrove = RetrievalChunk(
        chunkId = "mangrove",
        text = "Mangrove forests slow storm surge along tropical coasts.",
    )

    @Test
    fun aClaimIsAttributedToThePassageThatCarriesItsWords() {
        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_001",
            claimText = "Coral reefs shelter a quarter of all marine species",
            chunks = listOf(reef, mangrove),
        )

        // Every candidate gets a record: the passage that supports the claim
        // and the passage that does not are both reported, and the confidence
        // is what tells them apart.
        assertEquals(2, lineages.size)

        val lineage = lineages.first { it.chunkId == "reef" }
        assertEquals("claim_001", lineage.claimId)
        assertEquals(
            "Coral reefs shelter a quarter of all marine species",
            lineage.exactQuote,
        )
        // The quote is verbatim in the passage and covers the whole claim.
        assertEquals(1.0, lineage.confidenceScore)

        val unsupported = lineages.first { it.chunkId == "mangrove" }
        assertNull(unsupported.exactQuote)
        assertEquals(0.0, unsupported.confidenceScore)
    }

    @Test
    fun theSpanIsMeasuredInCodePointsAndNotInUtf16Units() {
        // The draft holds an emoji before the claim, and the claim runs past
        // the end of the draft. Python's `len()` counts code points, so the
        // span ends where the draft ends: at code point 34. Kotlin's
        // `String.length` counts UTF-16 units, and the emoji is two of them,
        // so a tier that counted units would end the span at 35 -- one past
        // the end of the draft, and the lineage engine would refuse it.
        val draft = "🌊 Coral reefs shelter a quarter of"
        val claim = "Coral reefs shelter a quarter of all marine species"

        // The two counts are different, which is the whole point.
        assertEquals(34, draft.codePointCount(0, draft.length))
        assertEquals(35, draft.length)

        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_002",
            claimText = claim,
            chunks = listOf(reef),
            draftText = draft,
        )

        assertEquals(1, lineages.size)
        val lineage = lineages.first()
        assertEquals(0, lineage.spanStart)
        assertEquals(34, lineage.spanEnd)
    }

    @Test
    fun theSpanPointsAtTheClaimAfterAnAstralCharacter() {
        // The claim starts after the emoji, at code point 2. The offsets the
        // record carries are code points, so the span they name is the
        // claim's own text and nothing else.
        val draft = "🌊 Coral reefs shelter a quarter of all marine species"
        val claim = "Coral reefs shelter a quarter of all marine species"

        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_003",
            claimText = claim,
            chunks = listOf(reef),
            draftText = draft,
            spanStart = 2,
        )

        assertEquals(1, lineages.size)
        val lineage = lineages.first()
        assertEquals(2, lineage.spanStart)
        assertEquals(2 + claim.codePointCount(0, claim.length), lineage.spanEnd)
        assertEquals(claim, codePointSlice(draft, lineage.spanStart, lineage.spanEnd))
    }

    @Test
    fun theConfidenceIsScaledByTheShareOfTheClaimTheQuoteCovers() {
        // The passage carries every word of the claim but the last one, so the
        // quote covers seven of the claim's eight content words and the
        // confidence is scaled to that share.
        val claim = "Coral reefs shelter a quarter of all marine species and their larvae"
        val partial = RetrievalChunk(
            chunkId = "reef_head",
            text = "Coral reefs shelter a quarter of all marine species",
        )

        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_004",
            claimText = claim,
            chunks = listOf(partial),
        )

        assertEquals(1, lineages.size)
        val lineage = lineages.first()
        assertEquals(
            "Coral reefs shelter a quarter of all marine species",
            lineage.exactQuote,
        )
        assertEquals(0.875, lineage.confidenceScore)
    }

    @Test
    fun aClaimNoPassageSupportsIsReportedWithNoQuote() {
        // The claim shares one word with the reef passage -- "of" -- and a
        // single word is not a quotation, so neither passage is quoted. The
        // confidence is then the measured overlap: one word of the claim's
        // eight for the reef, none at all for the mangrove.
        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_005",
            claimText = "Volcanic ash fertilises the soil of Andean valleys",
            chunks = listOf(reef, mangrove),
        )

        assertEquals(2, lineages.size)
        for (lineage in lineages) {
            assertNull(lineage.exactQuote)
        }
        assertEquals(0.125, lineages.first { it.chunkId == "reef" }.confidenceScore)
        assertEquals(0.0, lineages.first { it.chunkId == "mangrove" }.confidenceScore)
    }

    @Test
    fun theRecordsComeBackInTheOrderTheCorpusWasRankedIn() {
        val claim = "Coral reefs shelter a quarter of all marine species"
        val corpus = listOf(reef, mangrove)

        val ranked = Indexer.rank(corpus, claim, mode = "hybrid", topK = 5)
        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_006",
            claimText = claim,
            chunks = corpus,
        )

        assertEquals(ranked.map { it.chunkId }, lineages.map { it.chunkId })
    }

    @Test
    fun anEmptyCorpusProducesNoLineage() {
        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_007",
            claimText = "Coral reefs shelter a quarter of all marine species",
            chunks = emptyList(),
        )

        assertTrue(lineages.isEmpty())
    }

    @Test
    fun thePassageThatSupportsTheClaimIsTheOneThatIsQuoted() {
        // Two passages, one of which carries the claim's words: the quote is
        // the passage's own text, so only the passage that supports the claim
        // is quoted.
        val lineages = ClaimLineage.attributeClaim(
            claimId = "claim_008",
            claimText = "Mangrove forests slow storm surge along tropical coasts",
            chunks = listOf(reef, mangrove),
        )

        assertEquals(2, lineages.size)
        val quoted = lineages.filter { it.exactQuote != null }
        assertEquals(1, quoted.size)
        assertEquals("mangrove", quoted.first().chunkId)
        assertNotNull(quoted.first().exactQuote)
    }

    /**
     * The text between two code-point offsets.
     *
     * Written out rather than borrowed from the tier's own offsets, so that
     * the assertion does not check the rule with the rule.
     */
    private fun codePointSlice(text: String, start: Int, end: Int): String {
        val codePoints = text.codePoints().toArray()
        val slice = StringBuilder()
        for (index in start until minOf(end, codePoints.size)) {
            slice.appendCodePoint(codePoints[index])
        }
        return slice.toString()
    }
}
