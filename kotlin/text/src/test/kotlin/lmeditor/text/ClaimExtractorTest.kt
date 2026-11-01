package lmeditor.text

import lmeditor.core.EntailmentStatus
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The claim extraction rules, ported from `backend/tests/test_verification.py`.
 */
class ClaimExtractorTest {

    @Test
    fun theSentenceBoundaryDetectorAvoidsAbbreviationsAndFloats() {
        val text = (
            "Dr. Smith visited the U.S. facility, e.g. the lab in Boston. " +
                "The estimated cost was \$3.14 million for Phase 1. " +
                "Another sentence follows."
            )
        val sentences = segmentSentences(text)

        assertEquals(3, sentences.size)
        assertTrue(sentences[0].contains("Dr. Smith visited the U.S. facility, e.g. the lab in Boston."))
        assertTrue(sentences[1].contains("\$3.14 million"))
        assertEquals("Another sentence follows.", sentences[2])
    }

    @Test
    fun theSentenceBoundariesMatchThePythonTier() {
        val text = (
            "Dr. Smith visited the U.S. facility, e.g. the lab in Boston. " +
                "The estimated cost was \$3.14 million for Phase 1. " +
                "Another sentence follows."
            )
        // Pinned against the Python tier.
        assertEquals(
            listOf(
                "Dr. Smith visited the U.S. facility, e.g. the lab in Boston.",
                "The estimated cost was \$3.14 million for Phase 1.",
                "Another sentence follows.",
            ),
            segmentSentences(text),
        )
    }

    @Test
    fun theSegmentationHandlesMarkdownStructures() {
        val text = (
            "# Introduction\n\n" +
                "This is paragraph one.\n\n" +
                "## Key Objectives\n" +
                "- First objective is verifiable citations.\n" +
                "- Second objective is deterministic state transitions.\n"
            )
        val sentences = segmentSentences(text)

        assertEquals(5, sentences.size)
        assertEquals("Introduction", sentences[0])
        assertEquals("This is paragraph one.", sentences[1])
        assertEquals("Key Objectives", sentences[2])
        assertTrue(sentences[3].contains("First objective"))
        assertTrue(sentences[4].contains("Second objective"))
    }

    @Test
    fun inlineCitationsAreExtractedInEverySyntax() {
        val text = (
            "First statement [chunk_01]. " +
                "Second statement with prefix [cite: chunk_02]. " +
                "Compound statement [chunk_03, chunk_04] and adjacent [chunk_05][chunk_06]. " +
                "Markdown link [Link text](https://example.com) should be ignored."
            )
        val citations = extractCitations(text)
        val chunkIds = citations.map { it.chunkId }

        assertTrue("chunk_01" in chunkIds)
        assertTrue("chunk_02" in chunkIds)
        assertTrue("chunk_03" in chunkIds)
        assertTrue("chunk_04" in chunkIds)
        assertTrue("chunk_05" in chunkIds)
        assertTrue("chunk_06" in chunkIds)
        assertTrue("Link text" !in chunkIds)
        assertEquals(6, citations.size)
    }

    @Test
    fun theInlineCitationSpansMatchThePythonTier() {
        val text = (
            "First statement [chunk_01]. " +
                "Second statement with prefix [cite: chunk_02]. " +
                "Compound statement [chunk_03, chunk_04] and adjacent [chunk_05][chunk_06]. " +
                "Markdown link [Link text](https://example.com) should be ignored."
            )
        val citations = extractCitations(text)

        // Pinned against the Python tier.
        assertEquals(
            listOf("cit_0001", "cit_0002", "cit_0003", "cit_0004", "cit_0005", "cit_0006"),
            citations.map { it.citationId },
        )
        assertEquals(
            listOf(
                "chunk_01" to (16 to 26),
                "chunk_02" to (57 to 73),
                "chunk_03" to (94 to 114),
                "chunk_04" to (94 to 114),
                "chunk_05" to (128 to 138),
                "chunk_06" to (138 to 148),
            ),
            citations.map { it.chunkId to (it.spanStart to it.spanEnd) },
        )
    }

    @Test
    fun aCompoundSentenceIsDeconstructedIntoAtomicClaims() {
        val text = (
            "LMeditor enforces chunk provenance [chunk_01], and all propositions " +
                "are verified against ground truth passages [chunk_02]."
            )
        val claims = extractClaims(text, sectionId = "sec_01")

        assertTrue(claims.size >= 2)
        for (claim in claims) {
            // The citation brackets are stripped from the proposition text.
            assertTrue("[" !in claim.text)
            assertTrue("]" !in claim.text)
            assertEquals("sec_01", claim.sectionId)
            assertEquals(EntailmentStatus.UNGROUNDED, claim.entailmentStatus)
        }
    }

    @Test
    fun theDeconstructedClaimsMatchThePythonTier() {
        val text = (
            "LMeditor enforces chunk provenance [chunk_01], and all propositions " +
                "are verified against ground truth passages [chunk_02]."
            )
        val claims = extractClaims(text, sectionId = "sec_01")

        // Pinned against the Python tier.
        assertEquals(
            listOf(
                "claim_sec_01_000_00" to "LMeditor enforces chunk provenance",
                "claim_sec_01_000_01" to "all propositions are verified against ground truth passages",
            ),
            claims.map { it.claimId to it.text },
        )
        assertEquals(listOf(0, 0), claims.map { it.charStart })
        assertEquals(listOf(122, 122), claims.map { it.charEnd })
        assertEquals(
            listOf(listOf("chunk_01", "chunk_02"), listOf("chunk_01", "chunk_02")),
            claims.map { it.citedChunkIds },
        )
    }

    @Test
    fun anEmptyTextYieldsNoClaims() {
        val extractor = ClaimExtractor()
        assertTrue(extractor.extractClaims("").isEmpty())
        assertTrue(extractor.extractClaims("   ").isEmpty())
    }

    @Test
    fun aRepeatedSentenceIsMappedToItsOwnOccurrence() {
        // The lookup used to be a global `find`, which always returned the
        // *first* occurrence, so every repetition of a sentence pointed at the
        // same span of the document.
        val sentence = "The experiment failed twice."
        val text = "$sentence $sentence The experiment finally succeeded."

        val claims = extractClaims(text, sectionId = "sec_repeat")
        assertEquals(3, claims.size)
        val (first, second, third) = claims

        // Each occurrence points at its own place in the document.
        assertEquals(0, first.charStart)
        assertEquals(sentence.length + 1, second.charStart)
        assertEquals(2 * (sentence.length + 1), third.charStart)
        assertTrue(first.charStart != second.charStart)

        // And the span really does contain the sentence it claims to come from.
        assertEquals(sentence, TextOffsets.slice(text, first.charStart, first.charEnd))
        assertEquals(sentence, TextOffsets.slice(text, second.charStart, second.charEnd))
        assertEquals(
            "The experiment finally succeeded.",
            TextOffsets.slice(text, third.charStart, third.charEnd),
        )
    }
}
