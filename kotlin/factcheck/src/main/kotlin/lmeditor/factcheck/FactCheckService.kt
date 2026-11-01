package lmeditor.factcheck

import lmeditor.core.Claim
import lmeditor.core.ClaimVerdict
import lmeditor.core.CodePoints
import lmeditor.core.EntailmentStatus
import lmeditor.core.Evidence
import lmeditor.core.EvidenceSource
import lmeditor.core.FactCheckError
import lmeditor.core.FactCheckReport
import lmeditor.core.FactCheckRequest
import lmeditor.core.FactCheckVerdict
import lmeditor.core.NliEvaluator
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SourceChunk
import lmeditor.core.WebEvidenceError
import lmeditor.core.VerificationScorecard
import lmeditor.text.CitationChecker
import lmeditor.text.NamedNliEvaluator
import lmeditor.text.computeCitationPrecision
import lmeditor.text.computeCitationRecall
import lmeditor.text.computeGroundednessFBeta
import lmeditor.text.extractClaims
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Smallest number of evidence items a check may retrieve per claim.
 */
const val MIN_EVIDENCE_LIMIT: Int = 1

/**
 * Largest number of evidence items a check may retrieve per claim.
 */
const val MAX_EVIDENCE_LIMIT: Int = 25

/**
 * The default `FactCheckRequest.maxEvidence` carries.
 *
 * A request that leaves the field at this value is taken to have no
 * preference of its own, so the checker's own default applies instead.
 */
const val REQUEST_EVIDENCE_DEFAULT: Int = 5

/**
 * A web snippet shorter than this carries too little text to judge, so the
 * page itself is read instead.
 */
const val MIN_WEB_SNIPPET_CHARS: Int = 120

/**
 * Longest page text kept as the premise for a thin web snippet.
 */
const val MAX_PAGE_TEXT_CHARS: Int = 4_000

/**
 * Function words excluded from the relevance measure.
 *
 * A claim and a passage that share only "the" share no evidence.
 */
private val STOPWORDS: Set<String> = setOf(
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "in", "is", "it", "its", "of",
    "on", "or", "she", "that", "the", "their", "them", "there", "these",
    "they", "this", "to", "was", "were", "which", "who", "will", "with",
)

/** Word tokenizer used by the relevance measure. */
private val TOKEN: Regex = Regex("[a-z0-9]+")

/**
 * How the verdicts are ordered when two of them score exactly the same.
 *
 * A contradiction is the more serious reading of an ambiguous signal, so it
 * wins the tie: reporting a passage as supported when the evidence could
 * just as well refute it is the worse mistake.
 */
private val TIE_BREAK_PRIORITY: Map<EntailmentStatus, Int> = mapOf(
    EntailmentStatus.CONTRADICTED to 3,
    EntailmentStatus.ENTAILED to 2,
    EntailmentStatus.NEUTRAL to 1,
    EntailmentStatus.UNGROUNDED to 0,
)

/**
 * Fact-checks a passage against a document's sources, the web, or both.
 *
 * The checker owns four collaborators, each of which may be replaced:
 *
 * * [index] -- the retrieval view of the document's corpus. When it is
 *   absent, the [chunks] passed to the constructor are ranked directly.
 * * [web] -- the search provider. When it is absent, a check is corpus-only
 *   and never touches the network.
 * * [pageReader] -- the reader for a page whose snippet is too thin to
 *   judge. When it is absent, a thin snippet is judged as it stands.
 * * [evaluator] -- the NLI evaluator that judges a claim against its
 *   evidence, and [checker] -- the citation checker that owns the
 *   verification metrics. The two are linked: the checker is built from the
 *   evaluator unless one is supplied.
 *
 * The rules the implementation holds itself to are the ones that make the
 * answer worth anything:
 *
 * * a claim with no evidence is `UNGROUNDED`, never supported;
 * * a passage with no checkable claim is `INCONCLUSIVE`, never supported;
 * * the web is consulted only when the request asks for it and a provider is
 *   configured, and a web failure never voids a check the corpus can answer;
 * * nothing is ever reported as supported without an evidence item behind it,
 *   and no evidence item is ever invented -- every one carries the address or
 *   the chunk it came from.
 *
 * @param evaluator the NLI evaluator that judges claims against evidence.
 * @param index the retrieval view of the corpus, when one exists.
 * @param chunks the corpus itself, used when there is no index.
 * @param web the web-search provider, when the web may be consulted.
 * @param pageReader the reader for a page whose snippet is too thin.
 * @param checker the citation checker that owns the metrics.
 * @param maxEvidence how many evidence items to retrieve per claim when the
 *   request does not say.
 * @throws FactCheckError [maxEvidence] is outside the accepted range.
 */
class FactCheckService(
    private val evaluator: NliEvaluator,
    private val index: ProvenanceIndexer? = null,
    private val chunks: List<SourceChunk> = emptyList(),
    private val web: WebSearchProvider? = null,
    private val pageReader: WebPageReader? = null,
    checker: CitationChecker? = null,
    private val maxEvidence: Int = 5,
) {

    /** The citation checker that owns the verification metrics. */
    private val checker: CitationChecker = checker ?: CitationChecker(evaluator = evaluator)

    init {
        validateMaxEvidence(maxEvidence)
    }

    /**
     * Fact-check the passage carried by [request].
     *
     * The passage is split into atomic claims, each claim is given the
     * evidence that speaks to it, and each claim is judged against that
     * evidence. The report states, per claim and in aggregate, what the
     * evidence actually supports.
     *
     * @param request the passage to check and the sources to check it against.
     * @return the report for the passage. A passage with no checkable claim
     *   yields an `INCONCLUSIVE` report with no claims; a passage with no
     *   evidence at all yields an `UNSUPPORTED` report whose claims are
     *   `UNGROUNDED`.
     * @throws FactCheckError the request is not a usable fact-check request.
     * @throws WebEvidenceError the web failed for every claim *and* the
     *   corpus could not answer the check either.
     */
    fun check(request: FactCheckRequest): FactCheckReport {
        validateRequest(request)

        val claims = extractClaims(request.text, request.sectionId)
        if (claims.isEmpty()) {
            return FactCheckReport(
                docId = request.docId,
                text = request.text,
                verdict = FactCheckVerdict.INCONCLUSIVE,
                claims = emptyList(),
                scorecard = null,
                webSearched = false,
                summary = (
                    "The passage contains no checkable claim, so there was " +
                        "nothing to verify. It was not assessed for support."
                    ),
            )
        }

        val limit = evidenceLimit(request)
        val verdicts = mutableListOf<ClaimVerdict>()
        val notes = mutableListOf<String>()
        var webConsulted = false
        var webFailures = 0
        var evidenceFound = 0

        for (claim in claims) {
            val corpusEvidence = buildCorpusEvidence(claim.text, limit)
            var webItems = emptyList<Evidence>()
            val claimNotes = mutableListOf<String>()

            if (request.includeWeb && web != null) {
                webConsulted = true
                val (hits, failed) = searchWeb(claim.text, limit, claimNotes)
                if (failed) {
                    webFailures++
                }
                webItems = buildWebEvidence(claim.text, hits, claimNotes)
            }

            val evidence = corpusEvidence + webItems
            evidenceFound += evidence.size

            val (status, score, judged) = judge(claim.text, evidence, claimNotes)
            var reasoning = judged
            if (claimNotes.isNotEmpty()) {
                reasoning = "$judged ${claimNotes.joinToString(" ")}".trim()
                notes.addAll(claimNotes)
            }

            verdicts.add(
                ClaimVerdict(
                    claimId = claim.claimId,
                    claimText = claim.text,
                    status = status,
                    score = score,
                    evidence = evidence,
                    reasoning = reasoning,
                ),
            )
        }

        if (
            request.includeWeb &&
            web != null &&
            webFailures == claims.size &&
            evidenceFound == 0
        ) {
            // Every claim failed on the web and the corpus had nothing to say
            // either: there is no evidence behind this report at all, and
            // saying so quietly would look like a verdict on the passage.
            throw WebEvidenceError(
                reason = "the web provider failed for every claim and the corpus held no evidence",
                query = request.text,
            )
        }

        val evaluatorName = (evaluator as? NamedNliEvaluator)?.evaluatorName ?: ""
        return FactCheckReportBuilder.build(
            docId = request.docId,
            text = request.text,
            claims = claims,
            verdicts = verdicts,
            evidenceFound = evidenceFound,
            webConsulted = webConsulted,
            notes = notes,
            beta = checker.beta,
            evaluatorName = evaluatorName,
        )
    }

    // ------------------------------------------------------------------
    // Validation
    // ------------------------------------------------------------------

    /**
     * Refuse a request that cannot be checked.
     *
     * @throws FactCheckError the request is malformed.
     */
    private fun validateRequest(request: FactCheckRequest) {
        if (request.docId.isBlank()) {
            throw FactCheckError(
                "a fact-check request must name the document whose sources are checked"
            )
        }
        if (request.text.isBlank()) {
            throw FactCheckError("a fact-check request must carry the passage to check")
        }
        validateMaxEvidence(request.maxEvidence)
    }

    /**
     * Return how many evidence items to retrieve for each claim.
     *
     * The request is authoritative when it expresses a preference of its
     * own. A request that leaves `maxEvidence` at the schema default has
     * expressed none, so the checker's own default applies.
     */
    private fun evidenceLimit(request: FactCheckRequest): Int =
        evidenceLimit(maxEvidence, request.maxEvidence)

    // ------------------------------------------------------------------
    // Evidence
    // ------------------------------------------------------------------

    /** Return the corpus passages that best answer [query]. */
    private fun retrieve(query: String, limit: Int): List<SourceChunk> {
        val retrieval = index
        if (retrieval != null) {
            return retrieval.query(query, limit)
        }

        val scored = chunks.map { relevance(query, it.text) to it }
        val relevant = scored
            .filter { it.first > 0.0 }
            .sortedWith(
                compareByDescending<Pair<Double, SourceChunk>> { it.first }.thenBy { it.second.chunkId },
            )
        return relevant.take(limit).map { it.second }
    }

    /** Return the corpus passages that speak to [claimText]. */
    private fun buildCorpusEvidence(claimText: String, limit: Int): List<Evidence> {
        val evidence = mutableListOf<Evidence>()
        for (chunk in retrieve(claimText, limit)) {
            val relevance = relevance(claimText, chunk.text)
            if (relevance <= 0.0) {
                // A passage that shares nothing with the claim is not
                // evidence for it; keeping it would turn silence into a
                // finding.
                continue
            }
            evidence.add(
                Evidence(
                    source = EvidenceSource.CORPUS,
                    chunkId = chunk.chunkId,
                    title = chunk.documentTitle,
                    excerpt = chunk.text,
                    score = relevance,
                ),
            )
        }
        return evidence
    }

    /**
     * Return the web hits for [claimText] and whether the search failed.
     *
     * A provider that fails is contained here: the failure is recorded as a
     * note on the report and never raised into the check.
     */
    private fun searchWeb(
        claimText: String,
        limit: Int,
        notes: MutableList<String>,
    ): Pair<List<WebResult>, Boolean> {
        val provider = web ?: return emptyList<WebResult>() to false
        return try {
            provider.search(claimText, limit) to false
        } catch (e: WebEvidenceError) {
            notes.add("Web evidence unavailable for '$claimText': ${e.reason}")
            emptyList<WebResult>() to true
        } catch (e: RuntimeException) {
            // The provider is an injected dependency and may fail in any
            // way. The failure is not swallowed: it becomes a note on the
            // report, so the caller can see that the web was consulted and
            // did not answer.
            notes.add("Web evidence unavailable for '$claimText': $e")
            emptyList<WebResult>() to true
        }
    }

    /**
     * Return the web hits that speak to [claimText].
     *
     * A hit whose snippet is too thin to judge is read in full: the page is
     * fetched through the vetted path and its text becomes the premise. A
     * page that cannot be read is recorded as a note and the hit is kept as
     * it was, because a thin snippet is still better than nothing.
     */
    private fun buildWebEvidence(
        claimText: String,
        hits: List<WebResult>,
        notes: MutableList<String>,
    ): List<Evidence> {
        val evidence = mutableListOf<Evidence>()
        for (hit in hits) {
            if (hit.url.isEmpty()) {
                continue
            }
            var text = hit.snippet.trim()
            if (CodePoints.length(text) < MIN_WEB_SNIPPET_CHARS) {
                val reader = pageReader
                if (reader != null) {
                    try {
                        val pageText = reader.readPageText(hit.url)
                        if (pageText.isNotEmpty()) {
                            text = pageText
                        }
                    } catch (e: WebEvidenceError) {
                        notes.add("Could not read ${hit.url}: ${e.reason}")
                    } catch (e: RuntimeException) {
                        notes.add("Could not read ${hit.url}: $e")
                    }
                }
            }
            if (text.isEmpty()) {
                continue
            }
            val relevance = relevance(claimText, text)
            if (relevance <= 0.0) {
                continue
            }
            evidence.add(
                Evidence(
                    source = EvidenceSource.WEB,
                    url = hit.url,
                    title = hit.title,
                    excerpt = CodePoints.slice(text, 0, MAX_PAGE_TEXT_CHARS),
                    score = relevance,
                ),
            )
        }
        return evidence
    }

    // ------------------------------------------------------------------
    // Judging
    // ------------------------------------------------------------------

    /**
     * Judge [claimText] against [evidence].
     *
     * The premise is the evidence's excerpt and the hypothesis is the
     * claim. The claim's verdict is the judgement of its strongest
     * evidence: the highest score wins, and an exact tie is broken in
     * favour of the more serious reading.
     */
    private fun judge(
        claimText: String,
        evidence: List<Evidence>,
        notes: MutableList<String>,
    ): Triple<EntailmentStatus, Double, String> {
        if (evidence.isEmpty()) {
            return Triple(
                EntailmentStatus.UNGROUNDED,
                0.0,
                "No evidence was found for this claim, so it was not verified.",
            )
        }

        var bestStatus = EntailmentStatus.UNGROUNDED
        var bestScore = 0.0
        var bestExcerpt = ""
        var failures = 0

        for (item in evidence) {
            val verdict = try {
                evaluator.evaluate(item.excerpt, claimText)
            } catch (e: RuntimeException) {
                // One bad verdict must not void the rest. The failure is not
                // swallowed: it is recorded as a note on the report.
                failures++
                notes.add("Could not judge a claim against an evidence item: $e")
                continue
            }
            val status = verdict.first
            val score = verdict.second
            if (isStronger(status, score, bestStatus, bestScore)) {
                bestStatus = status
                bestScore = score
                bestExcerpt = item.excerpt
            }
        }

        if (failures == evidence.size) {
            return Triple(
                EntailmentStatus.UNGROUNDED,
                0.0,
                "Evidence was found for this claim, but none of it could be judged.",
            )
        }

        var reasoning = "Judged against ${evidence.size} evidence item(s): " +
            "${bestStatus.wire} at ${formatFixed(bestScore)}."
        if (bestExcerpt.isNotEmpty()) {
            reasoning = "$reasoning Strongest evidence: ${CodePoints.slice(bestExcerpt, 0, 240)}"
        }
        return Triple(bestStatus, bestScore, reasoning)
    }

    // ------------------------------------------------------------------
    // Aggregation and reporting
    // ------------------------------------------------------------------

    /** Return the verdict that covers every claim in the passage. */
    private fun aggregate(verdicts: List<ClaimVerdict>): FactCheckVerdict =
        FactCheckReportBuilder.aggregate(verdicts)

    /**
     * Return the verification scorecard for the passage.
     *
     * The counters are the ones the passage's own verdicts imply, so the
     * scorecard and the per-claim verdicts can never disagree.
     */
    private fun scorecard(
        claims: List<Claim>,
        verdicts: List<ClaimVerdict>,
    ): VerificationScorecard {
        val evaluatorName = (evaluator as? NamedNliEvaluator)?.evaluatorName ?: ""
        return FactCheckReportBuilder.scorecard(claims, verdicts, checker.beta, evaluatorName)
    }

    /** Return the human-readable synthesis of the findings. */
    private fun summary(
        verdicts: List<ClaimVerdict>,
        verdict: FactCheckVerdict,
        evidenceFound: Int,
        webConsulted: Boolean,
        notes: List<String>,
    ): String =
        FactCheckReportBuilder.summary(verdicts, verdict, evidenceFound, webConsulted, notes)
}

/**
 * Whether a verdict should replace the best one held so far.
 *
 * The higher score wins, because the score is the confidence the evaluator
 * had. An exact tie is broken in favour of the more serious reading.
 */
private fun isStronger(
    status: EntailmentStatus,
    score: Double,
    bestStatus: EntailmentStatus,
    bestScore: Double,
): Boolean {
    if (score > bestScore) {
        return true
    }
    if (score < bestScore) {
        return false
    }
    return (TIE_BREAK_PRIORITY[status] ?: 0) > (TIE_BREAK_PRIORITY[bestStatus] ?: 0)
}

/**
 * Return the words of [text] that carry content.
 */
fun contentTokens(text: String): Set<String> =
    TOKEN.findAll(text.lowercase())
        .map { it.value }
        .filter { !STOPWORDS.contains(it) }
        .toSet()

/**
 * Return the share of the claim's content words the evidence contains.
 *
 * The measure is the one that decides whether a passage is *evidence at
 * all*: a passage that shares nothing with the claim cannot support it,
 * cannot refute it, and is not evidence for anything. Reporting such a
 * claim as merely neutral would dress noise up as a finding, so the passage
 * is left out and the claim is reported as ungrounded.
 *
 * This is a rule, and it is public because the seam serves it: the Python
 * tier asks for the relevance rather than keeping a second copy of the rule.
 */
fun relevance(claimText: String, evidenceText: String): Double {
    val claimTokens = contentTokens(claimText)
    if (claimTokens.isEmpty()) {
        return 0.0
    }
    val evidenceTokens = contentTokens(evidenceText)
    if (evidenceTokens.isEmpty()) {
        return 0.0
    }
    val shared = claimTokens.intersect(evidenceTokens)
    return roundTo(shared.size.toDouble() / claimTokens.size, 4)
}

/**
 * Return how many evidence items to retrieve for each claim.
 *
 * The request is authoritative when it expresses a preference of its own; a
 * request that leaves `requestedMaxEvidence` at the schema default has
 * expressed none, so the checker's own default applies.
 *
 * This is a rule, and it is public because the seam serves it: the Python
 * tier asks for the limit rather than keeping a second copy of the rule.
 */
fun evidenceLimit(maxEvidence: Int, requestedMaxEvidence: Int): Int =
    if (requestedMaxEvidence != REQUEST_EVIDENCE_DEFAULT) requestedMaxEvidence else maxEvidence

/**
 * Return [maxEvidence] when it is inside the accepted range.
 *
 * @throws FactCheckError the value is outside the accepted range.
 */
fun validateMaxEvidence(maxEvidence: Int): Int {
    if (maxEvidence < MIN_EVIDENCE_LIMIT || maxEvidence > MAX_EVIDENCE_LIMIT) {
        throw FactCheckError(
            "max_evidence must be between $MIN_EVIDENCE_LIMIT and " +
                "$MAX_EVIDENCE_LIMIT, got $maxEvidence"
        )
    }
    return maxEvidence
}

/** Round [value] to [digits] decimal places the way Python's `round` does. */
private fun roundTo(value: Double, digits: Int): Double =
    BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).toDouble()

/** Format [value] with [digits] decimals, the way Python's `:.4f` does. */
private fun formatFixed(value: Double, digits: Int = 4): String {
    if (!value.isFinite()) {
        return value.toString()
    }
    return BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).toPlainString()
}
