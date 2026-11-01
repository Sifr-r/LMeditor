package lmeditor.text

import lmeditor.core.ChunkNotFoundError
import lmeditor.core.CitationHallucinationError
import lmeditor.core.Citations
import lmeditor.core.CitationLineage
import lmeditor.core.HybridIndexer
import lmeditor.core.SpanAlignmentError
import java.time.Instant

/**
 * Engine for attributing draft text spans to ground truth source chunks.
 */
class CitationLineageEngine {

    companion object {
        /**
         * The marker grammar is owned by `lmeditor.core.Citations`; this alias
         * keeps the historical class attribute without a second, divergent
         * regex.
         *
         * The canonical grammar accepts a marker group (`[#a, #b]`) and a bare
         * `[id]`, and its first group is the whole bracketed body rather than a
         * single chunk id: use [TextOffsets.citationSpans] to read the chunk
         * ids out of a draft.
         */
        val CITATION_MARKER_PATTERN: Regex = Citations.CITATION_PATTERN

        /** Collapse whitespace and lowercase text for robust fuzzy comparison. */
        private fun normalizeText(text: String): String =
            text.lowercase().split(WHITESPACE_RUN).filter { it.isNotEmpty() }.joinToString(" ")

        /** The distinct words of [text], lower-cased. */
        private fun wordSet(text: String): Set<String> =
            WORD_PATTERN.findAll(text.lowercase()).map { it.value }.toSet()
    }

    /**
     * Attribute a span in draft text to a stored passage.
     *
     * The store is the corpus the engine reads: the id a passage is known by
     * and the text it carries. The engine scores the passage's *text*, and
     * nothing else, so the store carries the text and not the whole chunk --
     * the corpus the seam hands over is a list of passages, not a live index.
     *
     * Validations:
     * - Boundary check: `0 <= spanStart < spanEnd <= draftText.length`.
     * - Passage existence: [citedChunkId] must exist in [chunkStore] or [indexer].
     * - Exact quote validation: if provided, the quote must be grounded in the
     *   passage text.
     *
     * @param createdAt the UTC timestamp recorded on the lineage record; it
     *   defaults to now and can be injected for a reproducible run.
     */
    fun attributeSpan(
        draftText: String,
        spanStart: Int,
        spanEnd: Int,
        citedChunkId: String,
        claimId: String,
        chunkStore: Map<String, String>? = null,
        indexer: HybridIndexer? = null,
        exactQuote: String? = null,
        createdAt: String = Instant.now().toString(),
    ): CitationLineage {
        val docLength = TextOffsets.length(draftText)

        // 1. Boundary validation.
        if (spanStart < 0 || spanEnd > docLength || spanEnd <= spanStart) {
            throw SpanAlignmentError(
                spanStart = spanStart,
                spanEnd = spanEnd,
                docLength = docLength,
            )
        }

        // 2. Passage existence validation.
        val passageText: String = (when {
            chunkStore != null && chunkStore.containsKey(citedChunkId) -> chunkStore[citedChunkId]
            indexer != null -> indexer.getChunk(citedChunkId)?.text
            else -> null
        }) ?: throw ChunkNotFoundError(citedChunkId)

        // 3. Exact quote validation and confidence scoring.
        val confidenceScore = scoreAlignment(
            draftText = draftText,
            spanStart = spanStart,
            spanEnd = spanEnd,
            passageText = passageText,
            citedChunkId = citedChunkId,
            exactQuote = exactQuote,
        )

        return CitationLineage(
            // The Python tier mints a random id here. This tier mints a
            // deterministic one, so a lineage record can be reproduced and
            // compared across the seam.
            lineageId = "lin_${spanStart}_${citedChunkId.take(8)}",
            claimId = claimId,
            chunkId = citedChunkId,
            spanStart = spanStart,
            spanEnd = spanEnd,
            exactQuote = exactQuote,
            confidenceScore = confidenceScore,
            createdAt = createdAt,
        )
    }

    /**
     * Extract inline citation tags from draft text and construct
     * [CitationLineage] records.
     *
     * The markers are found with the canonical grammar from
     * `lmeditor.core.Citations` -- the same grammar the drafting side writes,
     * the verifier reads and the manuscript bridge consumes -- so the lineage
     * records and the footnotes of an exported document can never disagree
     * about which chunks a section cites.
     *
     * A marker group such as `[#chunk_a, #chunk_b]` yields one record per
     * cited chunk, all of them pointing at the same marker.
     */
    fun extractCitationsFromDraft(
        draftText: String,
        chunkStore: Map<String, String>,
        createdAt: String = Instant.now().toString(),
    ): List<CitationLineage> {
        val citations = mutableListOf<CitationLineage>()

        for (span in TextOffsets.citationSpans(draftText)) {
            // The stored ids are the chunker's lower-case hex digests, so a
            // marker that spells the id in upper case must still resolve.
            // Look the id up as written first, then fall back to the
            // lower-cased form.
            val resolved = when {
                chunkStore.containsKey(span.chunkId) -> span.chunkId
                chunkStore.containsKey(span.chunkId.lowercase()) -> span.chunkId.lowercase()
                else -> null
            } ?: continue

            citations.add(
                CitationLineage(
                    lineageId = "lin_${span.start}_${resolved.take(8)}",
                    claimId = "claim_${resolved.take(8)}_${span.start}",
                    chunkId = resolved,
                    spanStart = span.start,
                    spanEnd = span.end,
                    exactQuote = null,
                    confidenceScore = 1.0,
                    createdAt = createdAt,
                ),
            )
        }

        return citations
    }

    /** Score how well the draft span, or the quoted passage, matches the passage. */
    private fun scoreAlignment(
        draftText: String,
        spanStart: Int,
        spanEnd: Int,
        passageText: String,
        citedChunkId: String,
        exactQuote: String?,
    ): Double {
        val normalizedChunkText = normalizeText(passageText)

        if (exactQuote != null) {
            if (exactQuote.isBlank()) {
                throw CitationHallucinationError(
                    rawChunkId = citedChunkId,
                    quote = exactQuote,
                    reason = "Exact quote cannot be empty",
                )
            }

            // Verbatim presence.
            if (passageText.contains(exactQuote) || passageText.lowercase().contains(exactQuote.lowercase())) {
                return 1.0
            }

            // Normalized presence.
            val normalizedQuote = normalizeText(exactQuote)
            if (normalizedChunkText.contains(normalizedQuote)) return 0.95

            throw CitationHallucinationError(
                rawChunkId = citedChunkId,
                quote = exactQuote,
                reason = "Quoted text does not appear in source chunk",
            )
        }

        // Evaluate span text alignment if no explicit quote is given.
        val spanText = TextOffsets.slice(draftText, spanStart, spanEnd)
        if (passageText.contains(spanText)) return 1.0
        if (normalizedChunkText.contains(normalizeText(spanText))) return 0.95

        // Token overlap heuristic between draft span and passage.
        //
        // The score is the *measured* overlap, not a floor above it. The client
        // renders this number as the passage's "Fidelity" to the claim, so a
        // fixed 0.5 for a passage that shares nothing with the claim would
        // tell the user that a passage which supports nothing supports the
        // claim halfway. The contract in `ProvenanceIndex.lineage_for_claim`
        // promises the ratio, so the ratio is what is reported.
        val spanTokens = wordSet(spanText)
        val chunkTokens = wordSet(passageText)
        if (spanTokens.isEmpty() || chunkTokens.isEmpty()) return 0.0

        val overlap = spanTokens.intersect(chunkTokens).size.toDouble() / spanTokens.size
        return minOf(1.0, roundTo(overlap, 3))
    }
}

/** A run of whitespace. */
private val WHITESPACE_RUN: Regex = Regex("(?U)\\s+")

/**
 * A word, as the Python tier's `\w` finds it.
 *
 * Python's `\w` for a `str` pattern is "alphanumeric or underscore", where
 * alphanumeric means `str.isalnum()`: the letters, the decimal digits and the
 * other numeric characters (`No` and `Nl`, such as `½` and `Ⅷ`). Java's
 * `(?U)\w` is a different set: it keeps combining marks and drops `No` and
 * `Nl`. The two tokenise the same text differently, and the lineage
 * confidence is computed from the tokens, so the class is written out rather
 * than borrowed.
 */
private val WORD_PATTERN: Regex = Regex("[\\p{L}\\p{N}_]+")
