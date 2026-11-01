package lmeditor.factcheck

import lmeditor.core.Claim
import lmeditor.core.ClaimVerdict
import lmeditor.core.EntailmentStatus
import lmeditor.core.EvidenceSource
import lmeditor.core.FactCheckReport
import lmeditor.core.FactCheckVerdict
import lmeditor.core.VerificationScorecard
import lmeditor.text.computeCitationPrecision
import lmeditor.text.computeCitationRecall
import lmeditor.text.computeGroundednessFBeta

/**
 * Turns the verdicts on a passage's claims into the report for that passage.
 *
 * The retrieval and the judgement are the model's business and stay in the
 * Python tier; what the report *says* is a rule, and the rule is here. That
 * split is why the aggregation lives in its own object rather than inside
 * [FactCheckService]: the seam needs the rule without the service, because
 * the service is the part that needs a model.
 *
 * The counters in the scorecard are the ones the passage's own verdicts
 * imply, so the scorecard and the per-claim verdicts can never disagree.
 */
object FactCheckReportBuilder {

    /**
     * Build the report for a passage from the verdicts on its claims.
     *
     * @param docId the document the passage belongs to.
     * @param text the passage that was checked.
     * @param claims the claims the passage was split into.
     * @param verdicts the verdict on each claim.
     * @param evidenceFound how many evidence items were considered.
     * @param webConsulted whether the open web was consulted.
     * @param notes the caveats worth telling the reader about.
     * @param beta the weight the groundedness score gives to recall.
     * @param evaluatorName the name of the model that judged the claims.
     * @return the report for the passage.
     */
    fun build(
        docId: String,
        text: String,
        claims: List<Claim>,
        verdicts: List<ClaimVerdict>,
        evidenceFound: Int,
        webConsulted: Boolean,
        notes: List<String>,
        beta: Double,
        evaluatorName: String,
    ): FactCheckReport {
        val verdict = aggregate(verdicts)
        return FactCheckReport(
            docId = docId,
            text = text,
            verdict = verdict,
            claims = verdicts,
            scorecard = scorecard(claims, verdicts, beta, evaluatorName),
            webSearched = webConsulted,
            summary = summary(verdicts, verdict, evidenceFound, webConsulted, notes),
        )
    }

    /**
     * Return the verdict that covers every claim in the passage.
     */
    fun aggregate(verdicts: List<ClaimVerdict>): FactCheckVerdict {
        if (verdicts.isEmpty()) {
            return FactCheckVerdict.INCONCLUSIVE
        }
        if (verdicts.any { it.status == EntailmentStatus.CONTRADICTED }) {
            // A refuted claim is the finding that matters most, and it is
            // reported as such rather than diluted into "partly supported".
            return FactCheckVerdict.CONTRADICTED
        }
        val supported = verdicts.count { it.status == EntailmentStatus.ENTAILED }
        if (supported == verdicts.size) {
            return FactCheckVerdict.SUPPORTED
        }
        if (supported > 0) {
            return FactCheckVerdict.PARTIALLY_SUPPORTED
        }
        return FactCheckVerdict.UNSUPPORTED
    }

    /**
     * Return the verification scorecard for the passage.
     */
    fun scorecard(
        claims: List<Claim>,
        verdicts: List<ClaimVerdict>,
        beta: Double,
        evaluatorName: String,
    ): VerificationScorecard {
        val total = verdicts.size
        val supported = verdicts.count { it.status == EntailmentStatus.ENTAILED }
        val contradicted = verdicts.count { it.status == EntailmentStatus.CONTRADICTED }
        val ungrounded = verdicts.count { it.status == EntailmentStatus.UNGROUNDED }
        val neutral = verdicts.count { it.status == EntailmentStatus.NEUTRAL }

        val cited = verdicts.count { verdict ->
            verdict.evidence.any { it.source == EvidenceSource.CORPUS }
        }
        val supportedCited = verdicts.count { verdict ->
            verdict.status == EntailmentStatus.ENTAILED &&
                verdict.evidence.any { it.source == EvidenceSource.CORPUS }
        }

        val precision = computeCitationPrecision(supportedCited, cited, total)
        val recall = computeCitationRecall(supported, total)
        val fBeta = computeGroundednessFBeta(precision, recall, beta)

        val details = claims.zip(verdicts).map { (claim, verdict) ->
            Claim(
                claimId = claim.claimId,
                sectionId = claim.sectionId,
                sentenceIdx = claim.sentenceIdx,
                text = claim.text,
                charStart = claim.charStart,
                charEnd = claim.charEnd,
                citedChunkIds = verdict.evidence
                    .filter { it.source == EvidenceSource.CORPUS && it.chunkId.isNotEmpty() }
                    .map { it.chunkId },
                entailmentStatus = verdict.status,
                entailmentScore = verdict.score,
                premiseText = verdict.evidence.firstOrNull()?.excerpt,
                reasoning = verdict.reasoning,
            )
        }

        return VerificationScorecard(
            precision = precision,
            recall = recall,
            fBeta = fBeta,
            beta = beta,
            totalClaims = total,
            supportedClaims = supported,
            ungroundedClaims = ungrounded,
            contradictedClaims = contradicted,
            neutralClaims = neutral,
            details = details,
            evaluator = evaluatorName,
        )
    }

    /**
     * Return the human-readable synthesis of the findings.
     */
    fun summary(
        verdicts: List<ClaimVerdict>,
        verdict: FactCheckVerdict,
        evidenceFound: Int,
        webConsulted: Boolean,
        notes: List<String>,
    ): String {
        val supported = verdicts.count { it.status == EntailmentStatus.ENTAILED }
        val contradicted = verdicts.count { it.status == EntailmentStatus.CONTRADICTED }
        val ungrounded = verdicts.count { it.status == EntailmentStatus.UNGROUNDED }
        val neutral = verdicts.count { it.status == EntailmentStatus.NEUTRAL }

        val sentences = mutableListOf<String>()
        sentences.add("Verdict: ${verdict.wire}.")
        sentences.add(
            "$supported of ${verdicts.size} claim(s) supported by evidence, " +
                "$contradicted contradicted, $ungrounded ungrounded.",
        )
        if (neutral > 0) {
            sentences.add("$neutral claim(s) neither supported nor refuted.")
        }
        if (evidenceFound == 0) {
            sentences.add(
                "No evidence was found for this passage, so nothing in it has " +
                    "been verified.",
            )
        } else {
            sentences.add("$evidenceFound evidence item(s) were considered.")
        }
        if (webConsulted) {
            sentences.add("The open web was consulted for this check.")
        }
        sentences.addAll(notes)
        return sentences.joinToString(" ")
    }
}
