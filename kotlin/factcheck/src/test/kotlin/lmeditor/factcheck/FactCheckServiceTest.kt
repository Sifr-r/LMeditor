package lmeditor.factcheck

import lmeditor.core.CodePoints
import lmeditor.core.EntailmentStatus
import lmeditor.core.EvidenceSource
import lmeditor.core.FactCheckError
import lmeditor.core.FactCheckRequest
import lmeditor.core.FactCheckVerdict
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError
import lmeditor.core.WebEvidenceError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Fact-checking a passage against the corpus, the web, or both.
 *
 * The point of this domain is that a passage is never reported as supported
 * without evidence behind it, and that no source is ever invented. These
 * tests pin both halves of that promise: the verdicts a corpus-backed
 * passage gets, and the way a web failure is contained rather than allowed
 * to void a check the corpus can answer.
 *
 * Ported from `backend/tests/test_factcheck.py`. No test in this file
 * touches the network: the web provider is a recorded fake.
 */
class FactCheckServiceTest {

    // ------------------------------------------------------------------
    // 1. A passage the corpus states
    // ------------------------------------------------------------------

    @Test
    fun aSupportedPassageIsSupportedAndNeverConsultsTheWeb() {
        val web = RecordingWebProvider()
        val checker = checker(web = web)

        val report = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))

        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
        assertFalse(report.webSearched)
        assertTrue(web.queries.isEmpty())
        assertEquals(1, report.claims.size)
        assertEquals(EntailmentStatus.ENTAILED, report.claims[0].status)
        assertTrue(report.claims[0].evidence.isNotEmpty())
        assertEquals(EvidenceSource.CORPUS, report.claims[0].evidence[0].source)
        assertTrue(report.claims[0].evidence[0].chunkId.isNotEmpty())
        val scorecard = assertNotNull(report.scorecard)
        assertEquals(1, scorecard.supportedClaims)
    }

    // ------------------------------------------------------------------
    // 2. A passage the corpus does not state
    // ------------------------------------------------------------------

    @Test
    fun aPassageWithoutSupportingEvidenceIsUnsupportedAndUngrounded() {
        val checker = checker()

        val report = checker.check(FactCheckRequest(docId = "doc", text = PHOTOSYNTHESIS_SENTENCE))

        assertEquals(FactCheckVerdict.UNSUPPORTED, report.verdict)
        assertTrue(report.verdict != FactCheckVerdict.SUPPORTED)
        assertTrue(report.claims.isNotEmpty())
        for (claim in report.claims) {
            assertEquals(EntailmentStatus.UNGROUNDED, claim.status)
            assertEquals(0.0, claim.score)
            assertTrue(claim.evidence.isEmpty())
        }
        assertTrue(report.summary.contains("No evidence was found"))
    }

    // ------------------------------------------------------------------
    // 3. A passage with nothing checkable in it
    // ------------------------------------------------------------------

    @Test
    fun aPassageWithoutCheckableClaimsIsInconclusive() {
        val checker = checker()

        for (text in listOf("...", "[1]")) {
            val report = checker.check(FactCheckRequest(docId = "doc", text = text))

            assertEquals(FactCheckVerdict.INCONCLUSIVE, report.verdict)
            assertTrue(report.verdict != FactCheckVerdict.SUPPORTED)
            assertTrue(report.claims.isEmpty())
        }
    }

    // ------------------------------------------------------------------
    // 4. A passage that is only partly stated by the corpus
    // ------------------------------------------------------------------

    @Test
    fun aMixedPassageIsPartiallySupported() {
        val checker = checker()

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = "$REEF_SENTENCE $PHOTOSYNTHESIS_SENTENCE"),
        )

        assertEquals(FactCheckVerdict.PARTIALLY_SUPPORTED, report.verdict)
        val statuses = report.claims.map { it.status }.toSet()
        assertTrue(statuses.contains(EntailmentStatus.ENTAILED))
        assertTrue(statuses.contains(EntailmentStatus.UNGROUNDED))
    }

    // ------------------------------------------------------------------
    // 5. A passage the corpus refutes
    // ------------------------------------------------------------------

    @Test
    fun aContradictedClaimYieldsAContradictedVerdict() {
        val checker = checker()

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = "Company revenue fell by 30 percent in 2024."),
        )

        assertEquals(FactCheckVerdict.CONTRADICTED, report.verdict)
        assertEquals(EntailmentStatus.CONTRADICTED, report.claims[0].status)
        assertTrue(report.claims[0].evidence.isNotEmpty())
        val scorecard = assertNotNull(report.scorecard)
        assertEquals(1, scorecard.contradictedClaims)
    }

    @Test
    fun aRefutedClaimIsNotDilutedIntoPartlySupported() {
        // A contradiction outranks a support: the passage is reported as
        // refuted rather than as half-supported.
        val checker = checker()

        val report = checker.check(
            FactCheckRequest(
                docId = "doc",
                text = "$REEF_SENTENCE Company revenue fell by 30 percent in 2024.",
            ),
        )

        assertEquals(FactCheckVerdict.CONTRADICTED, report.verdict)
    }

    // ------------------------------------------------------------------
    // 6. Web evidence
    // ------------------------------------------------------------------

    @Test
    fun webEvidenceIsRecordedWhenTheWebIsConsulted() {
        val web = RecordingWebProvider(
            results = listOf(
                WebResult(
                    url = "https://reefs.example.org/census",
                    title = "Global reef census",
                    snippet = WEB_SNIPPET,
                ),
            ),
        )
        val checker = checker(chunks = emptyList(), web = web)

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = REEF_SENTENCE, includeWeb = true),
        )

        assertTrue(report.webSearched)
        assertEquals(listOf(REEF_SENTENCE.trimEnd('.')), web.queries)
        val sources = report.claims.flatMap { claim -> claim.evidence }.map { it.source }.toSet()
        assertTrue(sources.contains(EvidenceSource.WEB))
        val webEvidence = report.claims
            .flatMap { claim -> claim.evidence }
            .filter { it.source == EvidenceSource.WEB }
        assertEquals("https://reefs.example.org/census", webEvidence[0].url)
        assertEquals("Global reef census", webEvidence[0].title)
    }

    // ------------------------------------------------------------------
    // 7. A web failure is contained
    // ------------------------------------------------------------------

    @Test
    fun aWebFailureDoesNotVoidACorpusBackedCheck() {
        val web = RecordingWebProvider(
            error = WebEvidenceError(
                reason = "the search endpoint refused the request",
                query = REEF_SENTENCE,
            ),
        )
        val checker = checker(web = web)

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = REEF_SENTENCE, includeWeb = true),
        )

        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
        assertEquals(EntailmentStatus.ENTAILED, report.claims[0].status)
        // The failure is reported rather than swallowed.
        assertTrue(report.summary.contains("the search endpoint refused the request"))
        assertTrue(report.claims[0].reasoning.contains("the search endpoint refused the request"))
    }

    @Test
    fun aWebFailureWithNoEvidenceAtAllIsRaised() {
        val web = RecordingWebProvider(
            error = WebEvidenceError(reason = "the search endpoint refused the request"),
        )
        val checker = checker(web = web)

        assertFailsWith<WebEvidenceError> {
            checker.check(
                FactCheckRequest(
                    docId = "doc",
                    text = PHOTOSYNTHESIS_SENTENCE,
                    includeWeb = true,
                ),
            )
        }
    }

    @Test
    fun aWebFailureIsContainedEvenWhenTheProviderThrowsSomethingElse() {
        // The provider is an injected dependency and may fail in any way. The
        // failure becomes a note on the report; it never voids the check.
        val web = RecordingWebProvider(error = IllegalStateException("the network is down"))
        val checker = checker(web = web)

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = REEF_SENTENCE, includeWeb = true),
        )

        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
        assertTrue(report.summary.contains("the network is down"))
    }

    // ------------------------------------------------------------------
    // 8. The null provider
    // ------------------------------------------------------------------

    @Test
    fun theNullWebProviderReturnsNothingAndACorpusCheckNeverCallsTheWeb() {
        assertTrue(NullWebSearchProvider().search("coral reefs").isEmpty())

        val web = RecordingWebProvider()
        val checker = checker(web = web)

        val report = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))

        assertTrue(web.queries.isEmpty())
        assertFalse(report.webSearched)
        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
    }

    // ------------------------------------------------------------------
    // 9. The retrieval path and the boundary
    // ------------------------------------------------------------------

    @Test
    fun theCorpusIndexIsUsedForRetrieval() {
        val index = TestProvenanceIndex(corpus())
        val checker = FactCheckService(
            evaluator = HeuristicNliEvaluator(),
            index = index,
        )

        val report = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))

        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
        assertEquals(EvidenceSource.CORPUS, report.claims[0].evidence[0].source)
        assertTrue(report.claims[0].evidence[0].chunkId.isNotEmpty())
    }

    @Test
    fun theCheckerKeepsItsOwnEvidenceLimit() {
        val web = RecordingWebProvider(
            results = List(5) { index ->
                WebResult(url = "https://example.org/$index", snippet = WEB_SNIPPET)
            },
        )
        val checker = checker(chunks = emptyList(), web = web, maxEvidence = 2)

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = REEF_SENTENCE, includeWeb = true),
        )

        assertEquals(2, report.claims[0].evidence.size)
    }

    @Test
    fun aRequestWithoutADocumentIsRefused() {
        val checker = checker()

        assertFailsWith<FactCheckError> {
            checker.check(FactCheckRequest(docId = "", text = REEF_SENTENCE))
        }
    }

    @Test
    fun anOutOfRangeEvidenceLimitIsRefusedWhenTheCheckerIsBuilt() {
        assertFailsWith<FactCheckError> {
            FactCheckService(evaluator = HeuristicNliEvaluator(), maxEvidence = 0)
        }
        assertFailsWith<FactCheckError> {
            FactCheckService(evaluator = HeuristicNliEvaluator(), maxEvidence = 99)
        }
    }

    @Test
    fun anOutOfRangeEvidenceLimitIsRefusedAtTheSchemaBoundary() {
        // The Python tier reaches the service's own check only by bypassing
        // the schema's validation. The Kotlin schema refuses the value
        // first, so the boundary is the schema.
        assertFailsWith<ValidationError> {
            FactCheckRequest(docId = "doc", text = REEF_SENTENCE, maxEvidence = 0)
        }
    }

    @Test
    fun theCheckIsAvailableFromAPlainScript() {
        val checker = checker()

        val report = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))

        assertEquals(FactCheckVerdict.SUPPORTED, report.verdict)
        assertEquals("doc", report.docId)
        assertEquals(REEF_SENTENCE, report.text)
    }

    // ------------------------------------------------------------------
    // 10. The report is self-consistent
    // ------------------------------------------------------------------

    @Test
    fun theScorecardCountersMatchThePerClaimVerdicts() {
        val checker = checker()

        val report = checker.check(
            FactCheckRequest(docId = "doc", text = "$REEF_SENTENCE $PHOTOSYNTHESIS_SENTENCE"),
        )

        val scorecard = assertNotNull(report.scorecard)
        assertEquals(report.claims.size, scorecard.totalClaims)
        assertEquals(
            report.claims.count { it.status == EntailmentStatus.ENTAILED },
            scorecard.supportedClaims,
        )
        assertEquals(
            report.claims.count { it.status == EntailmentStatus.UNGROUNDED },
            scorecard.ungroundedClaims,
        )
        assertEquals(
            report.claims.count { it.status == EntailmentStatus.CONTRADICTED },
            scorecard.contradictedClaims,
        )
    }

    @Test
    fun aVerdictWithNoEvidenceIsNoVerdict() {
        // The summary says plainly that nothing was verified.
        val checker = checker()

        val report = checker.check(FactCheckRequest(docId = "doc", text = PHOTOSYNTHESIS_SENTENCE))

        assertEquals(FactCheckVerdict.UNSUPPORTED, report.verdict)
        assertTrue(report.summary.contains("No evidence was found for this passage"))
        assertTrue(report.claims.all { it.evidence.isEmpty() })
    }

    @Test
    fun checkingTheSamePassageTwiceGivesTheSameReport() {
        val checker = checker()

        val first = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))
        val second = checker.check(FactCheckRequest(docId = "doc", text = REEF_SENTENCE))

        assertEquals(first, second)
    }
}

/** The passage the corpus states. */
private const val REEF_SENTENCE: String =
    "Coral reefs shelter a quarter of all marine species and their larvae."

/** The passage the corpus does not state. */
private const val PHOTOSYNTHESIS_SENTENCE: String =
    "Photosynthesis converts light energy into chemical energy stored in glucose."

/** The passage the corpus states in the opposite direction. */
private const val REVENUE_SENTENCE: String = "Company revenue rose by 30 percent in 2024."

/** A snippet long enough that the page is never fetched for it. */
private const val WEB_SNIPPET: String =
    "Coral reefs shelter a quarter of all marine species and their larvae, " +
        "according to the global reef census published in 2024, which surveyed " +
        "more than four hundred reef sites across the tropics."

/** A chunk holding [text]. */
private fun chunk(text: String, documentId: String = "doc"): SourceChunk =
    SourceChunk.create(
        documentId = documentId,
        text = text,
        charStart = 0,
        charEnd = CodePoints.length(text),
        byteStart = 0,
        byteEnd = CodePoints.utf8Size(text),
    )

/** The corpus the checks are run against. */
private fun corpus(): List<SourceChunk> = listOf(
    chunk(REEF_SENTENCE),
    chunk("Mangrove forests slow storm surge and trap sediment along tropical coasts."),
    chunk("Medieval abbeys kept meticulous accounts of grain harvests and rents."),
    chunk(REVENUE_SENTENCE),
)

/** A checker that judges with the deterministic heuristic. */
private fun checker(
    chunks: List<SourceChunk> = corpus(),
    web: WebSearchProvider? = null,
    maxEvidence: Int = 5,
): FactCheckService = FactCheckService(
    evaluator = HeuristicNliEvaluator(),
    chunks = chunks,
    web = web,
    maxEvidence = maxEvidence,
)

/** A web provider that answers from a fixed list, or fails on demand. */
private class RecordingWebProvider(
    private val results: List<WebResult> = emptyList(),
    private val error: RuntimeException? = null,
) : WebSearchProvider {

    /** The queries the provider was asked. */
    val queries: MutableList<String> = mutableListOf()

    override fun search(query: String, limit: Int): List<WebResult> {
        queries.add(query)
        error?.let { throw it }
        return results.take(limit)
    }
}

/**
 * A retrieval index that ranks the chunks by the content words they share
 * with the query.
 */
private class TestProvenanceIndex(private val chunks: List<SourceChunk>) : ProvenanceIndexer {

    override fun query(query: String, topK: Int): List<SourceChunk> {
        val words = CONTENT_WORD.findAll(query.lowercase())
            .map { it.value }
            .filter { it.length > 3 }
            .toSet()
        return chunks
            .map { chunk ->
                val overlap = CONTENT_WORD.findAll(chunk.text.lowercase())
                    .map { it.value }
                    .filter { it.length > 3 }
                    .toSet()
                    .intersect(words)
                    .size
                overlap to chunk
            }
            .filter { it.first > 0 }
            .sortedWith(
                compareByDescending<Pair<Int, SourceChunk>> { it.first }.thenBy { it.second.chunkId },
            )
            .take(topK)
            .map { it.second }
    }

    override fun getChunk(chunkId: String): SourceChunk? = chunks.firstOrNull { it.chunkId == chunkId }

    override fun hasChunk(chunkId: String): Boolean = getChunk(chunkId) != null

    override fun getAllChunkIds(): List<String> = chunks.map { it.chunkId }

    private companion object {
        val CONTENT_WORD: Regex = Regex("[a-z0-9]+")
    }
}
