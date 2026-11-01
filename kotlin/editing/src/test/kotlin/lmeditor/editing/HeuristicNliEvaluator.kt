package lmeditor.editing

import lmeditor.core.EntailmentStatus
import lmeditor.core.NLIModelError
import lmeditor.core.NliEvaluator
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * The deterministic lexical evaluator, ported from the Python tier's
 * `HeuristicNLIEvaluator`.
 *
 * The model-backed evaluator stays in the Python tier; this is the fallback
 * the Python tests judge with, and it is deterministic, so the Kotlin tests
 * reach the same verdicts the Python tests do.
 *
 * It answers three questions before it answers the entailment question:
 *
 * 1. **Is the polarity reversed?** A premise that says a quantity *fell* and
 *    a hypothesis that says it *rose* assert opposite worlds.
 * 2. **Is the negation reversed?** "demonstrated" and "did not demonstrate"
 *    are opposites.
 * 3. **Does the hypothesis assert a quantity the premise never mentions?**
 *    "30 percent" and "60 percent" are different claims, and the premise
 *    does not support the second one.
 *
 * Any of the three is a contradiction, never an entailment.
 */
class HeuristicNliEvaluator(
    private val entailmentThreshold: Double = 0.65,
) : NliEvaluator {

    override fun evaluate(premise: String, hypothesis: String): Pair<EntailmentStatus, Double> {
        val pClean = premise.trim().lowercase()
        val hClean = hypothesis.trim().lowercase()

        if (hClean.isEmpty()) {
            throw NLIModelError("Hypothesis cannot be empty.")
        }
        if (pClean.isEmpty()) {
            return EntailmentStatus.UNGROUNDED to 0.0
        }

        val pTokens = tokens(pClean).toSet()
        val hTokens = tokens(hClean)
        if (hTokens.isEmpty()) {
            return EntailmentStatus.NEUTRAL to 0.0
        }

        var contentH = hTokens.filter { !STOPWORDS.contains(it) && !NEGATION_WORDS.contains(it) }
        if (contentH.isEmpty()) {
            contentH = hTokens
        }

        val matched = contentH.count { pTokens.contains(it) }
        val overlapRatio = matched.toDouble() / contentH.size

        val negationClash = hasNegation(pClean) != hasNegation(hClean)
        val premiseNumbers = numbers(pClean)
        val numericMismatch = numbers(hClean).any { !premiseNumbers.contains(it) }
        val pPolarities = directionPolarities(pClean)
        val hPolarities = directionPolarities(hClean)
        val directionFlip = pPolarities.isNotEmpty() &&
            hPolarities.isNotEmpty() &&
            pPolarities.intersect(hPolarities).isEmpty()

        // 1. Reversed negation: same content, opposite assertion.
        if (negationClash && overlapRatio >= 0.5) {
            return EntailmentStatus.CONTRADICTED to round(maxOf(0.1, 1.0 - overlapRatio))
        }

        // 2. A quantity the premise does not contain.
        if (numericMismatch) {
            if (overlapRatio >= TOPICAL_OVERLAP) {
                return EntailmentStatus.CONTRADICTED to round(maxOf(0.1, 1.0 - overlapRatio))
            }
            return EntailmentStatus.NEUTRAL to round(overlapRatio)
        }

        // 3. A reversed direction ("fell" vs "rose").
        if (directionFlip) {
            if (overlapRatio >= TOPICAL_OVERLAP) {
                return EntailmentStatus.CONTRADICTED to round(maxOf(0.1, 1.0 - overlapRatio))
            }
            return EntailmentStatus.NEUTRAL to round(overlapRatio)
        }

        // 4. The hypothesis quotes the premise verbatim.
        if (pClean.contains(hClean)) {
            return EntailmentStatus.ENTAILED to 1.0
        }

        // 5. Paraphrase: most of the content words are present.
        if (overlapRatio >= entailmentThreshold) {
            return EntailmentStatus.ENTAILED to round(minOf(1.0, overlapRatio))
        }

        // 6. Related but not entailed, or unrelated.
        return EntailmentStatus.NEUTRAL to round(overlapRatio)
    }
}

/** Overlap ratio at or above which two texts are about the same subject. */
private const val TOPICAL_OVERLAP: Double = 0.35

/** Function words excluded from the content-token sets. */
private val STOPWORDS: Set<String> = setOf(
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "that", "this", "these", "those",
    "it", "its", "and", "or", "as", "according",
)

/** Negation markers, matched as whole tokens. */
private val NEGATION_WORDS: Set<String> = setOf(
    "not", "never", "no", "none", "neither", "nor", "cannot", "hardly", "scarcely", "without",
)

/** Negation markers, matched as phrases. */
private val NEGATION_PHRASES: List<Regex> = listOf(
    Regex("\\bdid\\s+not\\b"),
    Regex("\\bdoes\\s+not\\b"),
    Regex("\\bdo\\s+not\\b"),
    Regex("\\bwas\\s+not\\b"),
    Regex("\\bwere\\s+not\\b"),
    Regex("\\bis\\s+not\\b"),
    Regex("\\bare\\s+not\\b"),
    Regex("\\bhas\\s+not\\b"),
    Regex("\\bhave\\s+not\\b"),
    Regex("\\bhad\\s+not\\b"),
    Regex("\\bwill\\s+not\\b"),
    Regex("\\bwould\\s+not\\b"),
    Regex("\\bshould\\s+not\\b"),
    Regex("\\bcan\\s+not\\b"),
    Regex("\\bcould\\s+not\\b"),
    Regex("\\bfail(?:s|ed)?\\s+to\\b"),
)

/** Direction markers: `1` means up, `-1` means down. */
private val DIRECTION_POLARITY: Map<String, Int> = mapOf(
    "rose" to 1, "roses" to 1, "risen" to 1, "rising" to 1, "rise" to 1,
    "increase" to 1, "increases" to 1, "increased" to 1, "increasing" to 1,
    "grew" to 1, "grows" to 1, "growing" to 1, "growth" to 1,
    "gain" to 1, "gains" to 1, "gained" to 1, "gaining" to 1,
    "up" to 1, "upward" to 1, "upwards" to 1,
    "higher" to 1, "more" to 1, "greater" to 1,
    "above" to 1, "exceed" to 1, "exceeds" to 1, "exceeded" to 1, "exceeding" to 1,
    "ascend" to 1, "ascended" to 1, "ascending" to 1, "ascent" to 1,
    "expand" to 1, "expanded" to 1, "expanding" to 1, "expansion" to 1,
    "climb" to 1, "climbed" to 1, "climbing" to 1,
    "improve" to 1, "improved" to 1, "improving" to 1, "improvement" to 1,
    "win" to 1, "wins" to 1, "won" to 1, "winning" to 1,
    "raise" to 1, "raised" to 1, "raising" to 1,
    "elevate" to 1, "elevated" to 1,
    "boost" to 1, "boosted" to 1,
    "surge" to 1, "surged" to 1, "surging" to 1,
    "fell" to -1, "fall" to -1, "falls" to -1, "fallen" to -1, "falling" to -1,
    "decline" to -1, "declines" to -1, "declined" to -1, "declining" to -1,
    "decrease" to -1, "decreases" to -1, "decreased" to -1, "decreasing" to -1,
    "shrank" to -1, "shrunk" to -1, "shrink" to -1, "shrinks" to -1, "shrinking" to -1,
    "loss" to -1, "losses" to -1, "lost" to -1, "lose" to -1, "loses" to -1, "losing" to -1,
    "down" to -1, "downward" to -1, "downwards" to -1,
    "lower" to -1, "less" to -1, "fewer" to -1,
    "below" to -1,
    "descend" to -1, "descended" to -1, "descending" to -1, "descent" to -1,
    "contract" to -1, "contracted" to -1, "contracting" to -1, "contraction" to -1,
    "drop" to -1, "drops" to -1, "dropped" to -1, "dropping" to -1,
    "reduce" to -1, "reduces" to -1, "reduced" to -1, "reducing" to -1,
    "reduction" to -1, "reductions" to -1,
    "worsen" to -1, "worsened" to -1, "worsening" to -1,
    "slump" to -1, "slumped" to -1, "slumping" to -1,
    "plunge" to -1, "plunged" to -1, "plunging" to -1,
    "sink" to -1, "sank" to -1, "sinking" to -1,
    "diminish" to -1, "diminished" to -1, "diminishing" to -1,
    "weaken" to -1, "weakened" to -1, "weakening" to -1,
)

/** Word tokenizer that keeps contractions intact. */
private val TOKEN: Regex = Regex("[a-z0-9]+(?:['\u2019][a-z]+)*")

/** Number tokenizer; the unit is captured so "30 percent" and "30%" agree. */
private val NUMBER: Regex =
    Regex("(?<![\\w.])(\\d+(?:[.,]\\d+)*)\\s*(%|percent|percentage|pct|per\\s?cent)?", RegexOption.IGNORE_CASE)

/** Lower-cased word tokens, contractions kept intact. */
private fun tokens(text: String): List<String> = TOKEN.findAll(text.lowercase()).map { it.value }.toList()

/** Whether the text carries an explicit negation. */
private fun hasNegation(text: String): Boolean {
    val lowered = text.lowercase()
    val wordTokens = tokens(lowered)
    if (wordTokens.any { it.endsWith("n't") }) {
        return true
    }
    if (wordTokens.any { NEGATION_WORDS.contains(it) }) {
        return true
    }
    return NEGATION_PHRASES.any { it.containsMatchIn(lowered) }
}

/** Every quantity asserted by the text, normalised to floats. */
private fun numbers(text: String): Set<Double> =
    NUMBER.findAll(text).mapNotNull { match ->
        val raw = match.groupValues[1].replace(",", "")
        raw.toDoubleOrNull()
    }.toSet()

/** The directional polarities the text asserts. */
private fun directionPolarities(text: String): Set<Int> =
    tokens(text).mapNotNull { DIRECTION_POLARITY[it] }.toSet()

/** Round [value] to four decimal places the way Python's `round` does. */
private fun round(value: Double): Double =
    BigDecimal(value).setScale(4, RoundingMode.HALF_EVEN).toDouble()
