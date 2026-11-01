// The schema models strip whitespace when they are constructed and not when
// they are copied, the way Pydantic's constructor and `model_copy()` do. The
// generated `copy()` of a model with a private constructor is flagged by the
// compiler; the pattern is deliberate, and it is documented in
// `lmeditor.core.Schemas`.
@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.text

import lmeditor.core.CitationHallucinationError
import lmeditor.core.Claim
import lmeditor.core.EntailmentStatus
import lmeditor.core.NliEvaluator
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import lmeditor.core.SourceChunk
import lmeditor.core.VerificationScorecard
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Single source of truth for the groundedness threshold that separates a
 * VERIFIED section from a NEEDS_REVISION one.
 *
 * The workflow gate and the checker read the same constant, so the graph and
 * the checker can never disagree.
 */
const val DEFAULT_F_BETA_THRESHOLD: Double = 0.85

/**
 * Hard ceiling on the number of NLI model calls that may be in flight at once
 * across the whole process.
 *
 * The Python tier admits every `evaluate` through one semaphore, so a
 * 200-claim document with 50 sections costs 8 concurrent inferences rather
 * than an unbounded fan-out. The Kotlin seam is synchronous -- one
 * `NliEvaluator` call at a time -- so there is no in-flight fan-out to cap
 * here; the constant is kept so that both tiers state the same limit.
 */
const val MAX_CONCURRENT_NLI_EVALUATIONS: Int = 8

/**
 * How much of a claim a sentence must cover before that sentence is used as
 * the premise.
 *
 * Below this the claim is not about that sentence, and the whole chunk stays
 * the premise.
 */
private const val PREMISE_FOCUS_THRESHOLD: Double = 0.5

/**
 * An NLI evaluator that names itself.
 *
 * The scorecard records which evaluator produced its verdicts, so a scorecard
 * produced by the degraded heuristic is visibly different from one produced
 * by the cross-encoder.
 */
interface NamedNliEvaluator : NliEvaluator {
    /** The identifier of the evaluator, as it appears on the scorecard. */
    val evaluatorName: String
}

/**
 * Calculate citation precision (P_cite).
 *
 * `P_cite = (supported cited claims) / (total cited claims)`.
 *
 * Edge case: when there are no cited claims the precision is 1.0 if there are
 * no claims at all, and 0.0 otherwise.
 */
fun computeCitationPrecision(
    supportedCitedClaims: Int,
    totalCitedClaims: Int,
    totalClaims: Int,
): Double {
    if (totalCitedClaims == 0) return if (totalClaims == 0) 1.0 else 0.0
    return roundTo(supportedCitedClaims.toDouble() / totalCitedClaims, 4)
}

/**
 * Calculate citation recall (R_cite).
 *
 * `R_cite = (supported claims) / (total claims)`.
 *
 * Edge case: when there are no claims the recall is 1.0.
 */
fun computeCitationRecall(supportedClaims: Int, totalClaims: Int): Double {
    if (totalClaims == 0) return 1.0
    return roundTo(supportedClaims.toDouble() / totalClaims, 4)
}

/**
 * Calculate groundedness (F_beta), the harmonic mean weighting precision
 * against recall.
 *
 * `F_beta = (1 + beta^2) * (P * R) / (beta^2 * P + R)`.
 * For beta = 0.5 that is `1.25 * (P * R) / (0.25 * P + R)`.
 *
 * Edge case: a precision or a recall of zero yields 0.0.
 */
fun computeGroundednessFBeta(
    precision: Double,
    recall: Double,
    beta: Double = 0.5,
): Double {
    if (precision <= 0.0 || recall <= 0.0) return 0.0

    val betaSquared = beta * beta
    val denominator = (betaSquared * precision) + recall
    if (denominator == 0.0) return 0.0

    val fBeta = (1.0 + betaSquared) * (precision * recall) / denominator
    return roundTo(fBeta, 4)
}

/**
 * The weight the groundedness score gives to recall, when nobody says
 * otherwise.
 *
 * Precision and recall are weighed equally by default, so a document that
 * cites well but grounds little scores no better than one that grounds well
 * but cites little.
 */
const val DEFAULT_BETA: Double = 0.5

/**
 * Auditor for citation precision, recall and groundedness.
 */
class CitationChecker(
    /** The evaluator that judges a premise against a claim. */
    private val evaluator: NliEvaluator,
    /** The weight of precision against recall in the groundedness score. */
    val beta: Double = DEFAULT_BETA,
    /** The groundedness score a section must reach to count as verified. */
    val fBetaThreshold: Double = DEFAULT_F_BETA_THRESHOLD,
) {

    /**
     * Verify each claim against its cited source chunks and compute the
     * verification metrics.
     *
     * The hallucination check runs as a strictly sequential pre-pass, so the
     * *first* offending claim in claim order raises
     * [CitationHallucinationError]. Doing it up front also avoids spending
     * model calls on a section that is going to be rejected anyway.
     *
     * @return the evaluated claims and the scorecard they produced.
     */
    fun auditClaims(
        claims: List<Claim>,
        chunksById: Map<String, SourceChunk>,
        strictHallucinationCheck: Boolean = true,
        sectionId: String = "",
    ): Pair<List<Claim>, VerificationScorecard> {
        val premises = resolvePremises(
            claims = claims,
            chunksById = chunksById,
            strictHallucinationCheck = strictHallucinationCheck,
            sectionId = sectionId,
        )
        val citedFlags = premises.first
        val premiseTexts = premises.second

        val evaluatedClaims = mutableListOf<Claim>()
        var supportedCited = 0
        var totalCited = 0
        var supportedTotal = 0
        var ungroundedTotal = 0
        var contradictedTotal = 0
        var neutralTotal = 0

        for ((idx, claim) in claims.withIndex()) {
            if (citedFlags[idx]) {
                totalCited++
                val premiseText = premiseTexts[idx]
                val (status, score) = evaluator.evaluate(premiseText, claim.text)

                when (status) {
                    EntailmentStatus.ENTAILED -> {
                        supportedCited++
                        supportedTotal++
                    }
                    EntailmentStatus.CONTRADICTED -> contradictedTotal++
                    EntailmentStatus.NEUTRAL -> neutralTotal++
                    else -> ungroundedTotal++
                }

                evaluatedClaims.add(
                    claim.copy(
                        entailmentStatus = status,
                        entailmentScore = score,
                        premiseText = premiseText,
                        reasoning = "NLI evaluation returned ${status.wire} with confidence ${formatFixed(score, 4)}",
                    ),
                )
            } else {
                // No citations attached.
                ungroundedTotal++
                evaluatedClaims.add(
                    claim.copy(
                        citedChunkIds = emptyList(),
                        entailmentStatus = EntailmentStatus.UNGROUNDED,
                        entailmentScore = 0.0,
                        premiseText = null,
                        reasoning = "Claim has no inline citations.",
                    ),
                )
            }
        }

        val totalClaims = claims.size
        val precision = computeCitationPrecision(supportedCited, totalCited, totalClaims)
        val recall = computeCitationRecall(supportedTotal, totalClaims)
        val fBeta = computeGroundednessFBeta(precision, recall, beta)

        val scorecard = VerificationScorecard(
            precision = precision,
            recall = recall,
            fBeta = fBeta,
            beta = beta,
            totalClaims = totalClaims,
            supportedClaims = supportedTotal,
            ungroundedClaims = ungroundedTotal,
            contradictedClaims = contradictedTotal,
            neutralClaims = neutralTotal,
            evaluator = evaluatorName(),
            details = evaluatedClaims,
        )

        return evaluatedClaims to scorecard
    }

    /**
     * Audit all claims in a manuscript section and update the section status.
     *
     * @return the updated section and the scorecard it was measured with.
     */
    fun auditSection(
        section: SectionDraft,
        chunksById: Map<String, SourceChunk>,
        strictHallucinationCheck: Boolean = true,
    ): Pair<SectionDraft, VerificationScorecard> {
        val claims = if (section.claims.isNotEmpty()) {
            section.claims
        } else {
            extractClaims(section.content, section.sectionId)
        }

        val (evaluatedClaims, measured) = auditClaims(
            claims = claims,
            chunksById = chunksById,
            strictHallucinationCheck = strictHallucinationCheck,
            sectionId = section.sectionId,
        )

        // A section that was never written cannot be verified.
        //
        // The metrics are defined to be perfect when there is nothing to
        // check: zero claims gives precision 1.0, recall 1.0 and F_beta 1.0.
        // That is the right convention for a section that *was* written but
        // contains no checkable proposition -- but it is wrong for a section
        // whose drafting failed, because the section is empty precisely
        // because there is nothing there.
        val hasContent = section.content.isNotBlank()
        val scorecard = if (hasContent) {
            measured
        } else {
            measured.copy(
                precision = 0.0,
                recall = 0.0,
                fBeta = 0.0,
                // One unsupported unit, so the counts still add up.
                totalClaims = maxOf(measured.totalClaims, 1),
                ungroundedClaims = maxOf(measured.ungroundedClaims, 1),
            )
        }

        // Determine the section status. All four conditions must hold:
        // NEUTRAL claims are unverified assertions and must not be waved
        // through by a high F_beta, because F_beta is computed from
        // precision/recall and does not count neutral claims as a failure.
        val isVerified = hasContent &&
            scorecard.fBeta >= fBetaThreshold &&
            scorecard.contradictedClaims == 0 &&
            scorecard.ungroundedClaims == 0 &&
            scorecard.neutralClaims == 0
        val status = if (isVerified) SectionStatus.VERIFIED else SectionStatus.NEEDS_REVISION

        val critique = if (isVerified) {
            null
        } else if (!hasContent) {
            "This section has no content, so there is nothing to verify. " +
                "It was most likely never drafted; the drafting failure is " +
                "recorded in the run's errors."
        } else {
            "Section verification failed: F_beta=${formatFixed(scorecard.fBeta, 3)} " +
                "(< ${formatFixed(fBetaThreshold, 2)} required), " +
                "Ungrounded claims: ${scorecard.ungroundedClaims}, " +
                "Contradictions: ${scorecard.contradictedClaims}, " +
                "Neutral claims: ${scorecard.neutralClaims}."
        }

        val updatedSection = SectionDraft(
            sectionId = section.sectionId,
            title = section.title,
            content = section.content,
            order = section.order,
            citations = section.citations,
            claims = evaluatedClaims,
            isVerified = isVerified,
            status = status,
            revisionCount = section.revisionCount,
            critique = critique,
        )

        return updatedSection to scorecard
    }

    /**
     * Validate the chunk references and assemble one premise string per claim.
     *
     * This rule is public because it is a rule of its own: the entailment
     * model lives in the Python tier, and the model needs the premise before
     * it can judge the claim. The Python tier asks for the premises here,
     * runs the model, and hands the scored claims back to
     * [auditClaims], so the rule exists in exactly one place.
     *
     * @return whether each claim is cited, and its premise.
     */
    fun resolvePremises(
        claims: List<Claim>,
        chunksById: Map<String, SourceChunk>,
        strictHallucinationCheck: Boolean,
        sectionId: String,
    ): Pair<List<Boolean>, List<String>> {
        val citedFlags = mutableListOf<Boolean>()
        val premiseTexts = mutableListOf<String>()

        for (claim in claims) {
            if (claim.citedChunkIds.isEmpty()) {
                citedFlags.add(false)
                premiseTexts.add("")
                continue
            }

            for (chunkId in claim.citedChunkIds) {
                if (!chunksById.containsKey(chunkId) && strictHallucinationCheck) {
                    throw CitationHallucinationError(
                        rawChunkId = chunkId,
                        sectionId = sectionId.ifEmpty { claim.sectionId },
                        quote = claim.text,
                        reason = "Cited chunk '$chunkId' is missing from source index.",
                    )
                }
            }

            citedFlags.add(true)
            val premises = claim.citedChunkIds
                .filter { chunksById.containsKey(it) }
                .map { focusPremise(chunksById.getValue(it).text, claim.text) }
            premiseTexts.add(premises.filter { it.isNotEmpty() }.joinToString(" "))
        }

        return citedFlags to premiseTexts
    }

    /**
     * Narrow a premise to the part of it that speaks to the claim.
     *
     * An entailment model is trained on pairs of *statements*. A chunk that
     * holds three sentences, judged against a claim that restates one of
     * them, is a different task from the one the model was trained for -- and
     * it answers it badly: a verbatim quote of one sentence came back
     * `NEUTRAL` at 0.998 because the premise said much more than the claim
     * needed.
     *
     * So the premise is narrowed to the sentence of the chunk that the claim
     * actually speaks to. A claim that appears verbatim in the chunk gets
     * that sentence; a paraphrase gets the sentence it overlaps most; a claim
     * that speaks to nothing in the chunk keeps the whole chunk as its
     * premise, so an unrelated claim is still judged on the full text.
     */
    private fun focusPremise(premise: String, claimText: String): String {
        if (premise.isBlank() || claimText.isBlank()) return premise

        val sentences = SENTENCE_BOUNDARY.split(premise)
            .map { it.trim() }
            .filter { it.isNotEmpty() }
        if (sentences.size <= 1) return premise

        val claimWords = longWords(claimText)
        if (claimWords.isEmpty()) return premise

        var bestSentence = ""
        var bestScore = 0.0
        for (sentence in sentences) {
            val sentenceWords = longWords(sentence)
            if (sentenceWords.isEmpty()) continue
            val score = claimWords.intersect(sentenceWords).size.toDouble() / claimWords.size
            if (score > bestScore) {
                bestScore = score
                bestSentence = sentence
            }
        }

        if (bestSentence.isNotEmpty() && bestScore >= PREMISE_FOCUS_THRESHOLD) {
            return bestSentence
        }
        return premise
    }

    /** The identifier of the evaluator that produced the verdicts. */
    private fun evaluatorName(): String =
        (evaluator as? NamedNliEvaluator)?.evaluatorName ?: ""
}

/** The words of [text] that are longer than three characters. */
private fun longWords(text: String): Set<String> =
    WORD_PATTERN.findAll(text.lowercase())
        .map { it.value }
        .filter { it.length > 3 }
        .toSet()

/** Sentence boundary used when a premise is narrowed to the part that speaks to a claim. */
private val SENTENCE_BOUNDARY: Regex = Regex("(?U)(?<=[.!?])\\s+")

/**
 * Word pattern used when measuring how much of a claim a sentence covers.
 *
 * The class is written out rather than borrowed from `(?U)\w`, because Java's
 * Unicode word class and Python's `\w` are not the same set: Python keeps the
 * other numeric characters (`½`, `Ⅷ`) and drops combining marks, and Java does
 * the opposite. The same set as `lmeditor.text.Lineage` uses.
 */
private val WORD_PATTERN: Regex = Regex("[\\p{L}\\p{N}_]+")

/** Format [value] with [digits] decimals, the way Python's `:.4f` does. */
private fun formatFixed(value: Double, digits: Int): String =
    if (!value.isFinite()) {
        value.toString()
    } else {
        BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).toPlainString()
    }
