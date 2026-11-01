package lmeditor.text

import lmeditor.core.CitationHallucinationError
import lmeditor.core.EntailmentStatus
import lmeditor.core.NliEvaluator
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import lmeditor.core.SourceChunk
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The citation audit rules, ported from `backend/tests/test_verification.py`.
 *
 * The entailment model itself stays in the Python tier; these tests pin the
 * arithmetic and the routing around it, which is what this module owns.
 */
class CitationCheckerTest {

    @Test
    fun theCitationPrecisionIsTheShareOfCitedClaimsThatAreSupported() {
        // 8 supported out of 10 cited.
        assertEquals(0.8, computeCitationPrecision(8, 10, 10))
        // All cited claims supported.
        assertEquals(1.0, computeCitationPrecision(5, 5, 5))
        // None supported.
        assertEquals(0.0, computeCitationPrecision(0, 5, 5))
    }

    @Test
    fun theCitationPrecisionHandlesTheCaseOfNoCitedClaims() {
        // Vacuously true: no claims exist in the manuscript.
        assertEquals(1.0, computeCitationPrecision(0, 0, 0))
        // Ungrounded failure: claims exist in the draft but none were cited.
        assertEquals(0.0, computeCitationPrecision(0, 0, 5))
    }

    @Test
    fun theCitationRecallIsTheShareOfClaimsThatAreSupported() {
        // Standard: 7 supported out of 10 claims.
        assertEquals(0.7, computeCitationRecall(7, 10))
        // Edge: 0 claims in the document -> the recall is vacuously 1.0.
        assertEquals(1.0, computeCitationRecall(0, 0))
        // 0 supported out of 8 claims.
        assertEquals(0.0, computeCitationRecall(0, 8))
    }

    @Test
    fun theGroundednessScoreHasTheExpectedBoundaryValues() {
        // Perfect alignment.
        assertEquals(1.0, computeGroundednessFBeta(precision = 1.0, recall = 1.0, beta = 0.5))
        // Zero precision or recall.
        assertEquals(0.0, computeGroundednessFBeta(precision = 0.0, recall = 1.0, beta = 0.5))
        assertEquals(0.0, computeGroundednessFBeta(precision = 1.0, recall = 0.0, beta = 0.5))
        assertEquals(0.0, computeGroundednessFBeta(precision = 0.0, recall = 0.0, beta = 0.5))
    }

    @Test
    fun theGroundednessScoreWeightsPrecisionHigherThanRecall() {
        // Case A: high precision (0.8), low recall (0.4).
        val caseA = computeGroundednessFBeta(precision = 0.8, recall = 0.4, beta = 0.5)
        // Case B: low precision (0.4), high recall (0.8).
        val caseB = computeGroundednessFBeta(precision = 0.4, recall = 0.8, beta = 0.5)

        // With beta = 0.5, case A must score higher than case B.
        assertTrue(caseA > caseB)
        // Pinned against the Python tier.
        assertEquals(0.6667, caseA)
        assertEquals(0.4444, caseB)
    }

    @Test
    fun aFullyGroundedSectionIsVerified() {
        val textPassage = "Stanford STORM explores diverse perspectives before outline generation."
        val chunk = SourceChunk.create(
            documentId = "doc_1",
            text = textPassage,
            charStart = 0,
            charEnd = textPassage.length,
            byteStart = 0,
            byteEnd = textPassage.toByteArray(Charsets.UTF_8).size,
        )
        val chunksMap = mapOf(chunk.chunkId to chunk)

        val section = SectionDraft(
            sectionId = "sec_storm",
            title = "Perspective Discovery",
            content = "$textPassage [${chunk.chunkId}].",
        )

        val checker = CitationChecker(evaluator = StubEvaluator(), beta = 0.5)
        val (auditedSection, scorecard) = checker.auditSection(section, chunksMap)

        assertEquals(1.0, scorecard.precision)
        assertEquals(1.0, scorecard.recall)
        assertEquals(1.0, scorecard.fBeta)
        assertEquals(1, scorecard.supportedClaims)
        assertEquals(0, scorecard.ungroundedClaims)
        assertEquals(0, scorecard.contradictedClaims)
        assertTrue(auditedSection.isVerified)
        assertEquals(SectionStatus.VERIFIED, auditedSection.status)
    }

    @Test
    fun aSectionThatCitesAMissingChunkIsRejected() {
        val section = SectionDraft(
            sectionId = "sec_err",
            title = "Error Section",
            content = "This claim references a phantom chunk [chunk_nonexistent_999].",
        )
        val checker = CitationChecker(evaluator = StubEvaluator())

        val failure = assertFailsWith<CitationHallucinationError> {
            checker.auditSection(section, chunksById = emptyMap(), strictHallucinationCheck = true)
        }
        assertTrue(failure.message.contains("chunk_nonexistent_999"))
    }

    @Test
    fun aClaimWithoutACitationIsUngrounded() {
        val section = SectionDraft(
            sectionId = "sec_uncited",
            title = "Uncited",
            content = "This claim cites nothing at all.",
        )
        val checker = CitationChecker(evaluator = StubEvaluator())

        val (auditedSection, scorecard) = checker.auditSection(section, emptyMap())

        assertEquals(1, scorecard.totalClaims)
        assertEquals(1, scorecard.ungroundedClaims)
        assertEquals(0.0, scorecard.precision)
        assertEquals(0.0, scorecard.recall)
        assertEquals(0.0, scorecard.fBeta)
        assertTrue(!auditedSection.isVerified)
        assertEquals(SectionStatus.NEEDS_REVISION, auditedSection.status)
    }

    @Test
    fun anUnwrittenSectionIsNeverVerified() {
        // A section whose drafting failed is empty precisely because there is
        // nothing there; it must not be reported as a finished, grounded one.
        val section = SectionDraft(
            sectionId = "sec_empty",
            title = "Never drafted",
            content = "   ",
        )
        val checker = CitationChecker(evaluator = StubEvaluator())

        val (auditedSection, scorecard) = checker.auditSection(section, emptyMap())

        assertEquals(0.0, scorecard.fBeta)
        assertEquals(1, scorecard.totalClaims)
        assertEquals(1, scorecard.ungroundedClaims)
        assertTrue(!auditedSection.isVerified)
        assertEquals(SectionStatus.NEEDS_REVISION, auditedSection.status)
        assertTrue(auditedSection.critique != null)
    }

    /**
     * A deterministic stand-in for the entailment model, which stays in the
     * Python tier: every premise entails its claim.
     */
    private class StubEvaluator : NliEvaluator {
        override fun evaluate(premise: String, hypothesis: String): Pair<EntailmentStatus, Double> =
            EntailmentStatus.ENTAILED to 0.95
    }
}
