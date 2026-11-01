package lmeditor.text

import lmeditor.core.CitationLineage

/**
 * The claim-lineage rule: which passage supports which claim.
 *
 * Port of `backend/src/lmeditor/provenance/service.py`'s
 * `ProvenanceIndex.lineage_for_claim`. The Python module this replaces is a
 * *door*: it keeps the API its callers have and answers with the answers
 * computed here.
 *
 * The rule is a composition, and the composition is the rule:
 *
 * 1. **Rank.** The candidates are the passages that best answer the claim's
 *    own text, by the hybrid index's fusion of BM25 and dense retrieval.
 * 2. **Find the support.** Each candidate is asked what it and the claim say
 *    in common, and how much of the claim that shared text accounts for.
 * 3. **Attribute the span.** The claim's span in the draft is attributed to
 *    the candidate, with the shared text as the exact quote.
 * 4. **Scale the confidence.** The engine scored the *quote*: it is verbatim
 *    in the passage. The claim is only as well supported as the share of it
 *    the quote covers, so the score is scaled by that share.
 *
 * The steps were four round trips across the seam when the composition lived
 * in Python; here they are one call, and the rule that decides which passage
 * supports which claim is written down in one place.
 *
 * **The offsets are code points.** Python's `len()` on a `str` counts code
 * points and Kotlin's `String.length` counts UTF-16 units, so every offset
 * here is measured with [TextOffsets], the code-point aware offsets this
 * module uses everywhere else. A draft that holds an emoji before the span
 * would otherwise be sliced in the wrong place.
 *
 * **The rounding is Python's.** The confidence is scaled by a coverage and
 * rounded to three decimals, and Python's `round` rounds half to even on the
 * exact binary value -- which is what [roundTo] does. `kotlin.math.round`
 * rounds half up, and would disagree on the boundary.
 */
object ClaimLineage {

    /** The default number of candidate passages a claim is attributed to. */
    const val DEFAULT_TOP_K: Int = 5

    /**
     * Attribute a claim to the passages that support it.
     *
     * @param chunks the corpus the candidates are drawn from.
     * @param draftText the text the claim was written in; the returned records
     *   point into it. When it is not given, the claim text itself is used and
     *   the span covers the whole claim.
     * @param spanStart the code-point offset the claim starts at.
     * @param spanEnd the code-point offset the claim ends at; when it is not
     *   given, the span covers as much of the claim as the text holds.
     * @param denseVectors the dense vectors of the corpus, already normalised
     *   by the sender, or `null` when the tier is to compute its own
     *   term-frequency hash vectors.
     * @param queryVector the dense vector of the claim, already normalised by
     *   the sender, or `null` when the tier is to compute its own.
     */
    fun attributeClaim(
        claimId: String,
        claimText: String,
        chunks: List<RetrievalChunk>,
        draftText: String? = null,
        spanStart: Int = 0,
        spanEnd: Int? = null,
        topK: Int = DEFAULT_TOP_K,
        rrfK: Int = Indexer.DEFAULT_RRF_K,
        alpha: Double = Indexer.DEFAULT_ALPHA,
        k1: Double = Indexer.DEFAULT_K1,
        b: Double = Indexer.DEFAULT_B,
        embeddingDim: Int = Indexer.DEFAULT_EMBEDDING_DIM,
        denseVectors: Map<String, List<Double>>? = null,
        queryVector: List<Double>? = null,
    ): List<CitationLineage> {
        // 1. Rank the corpus for the claim's own text.
        val candidates = Indexer.rank(
            chunks = chunks,
            query = claimText,
            mode = "hybrid",
            topK = topK,
            rrfK = rrfK,
            alpha = alpha,
            k1 = k1,
            b = b,
            embeddingDim = embeddingDim,
            denseVectors = denseVectors,
            queryVector = queryVector,
        )
        if (candidates.isEmpty()) return emptyList()

        // 2. The span the claim occupies in the draft.
        val text = draftText ?: claimText
        val span = claimSpan(text, claimText, spanStart, spanEnd)

        // 3. The corpus, as the lineage engine's store: the id a passage is
        //    known by and the text it carries.
        val store = chunks.associate { chunk -> chunk.chunkId to chunk.text }
        val passages = chunks.associateBy { chunk -> chunk.chunkId }
        val engine = CitationLineageEngine()

        val lineages = mutableListOf<CitationLineage>()
        for (candidate in candidates) {
            val passage = passages[candidate.chunkId] ?: continue

            // 4. What the claim and the passage say in common.
            val supporting = SupportingText.supportingText(claimText, passage.text)

            // 5. The span, attributed to the passage.
            val record = engine.attributeSpan(
                draftText = text,
                spanStart = span.first,
                spanEnd = span.second,
                citedChunkId = candidate.chunkId,
                claimId = claimId,
                chunkStore = store,
                exactQuote = supporting.quote,
            )

            // 6. The confidence, scaled by the share of the claim the quote
            //    covers.
            lineages.add(scaledByCoverage(record, supporting))
        }
        return lineages
    }

    /**
     * The span of the draft the claim occupies, in code points.
     *
     * The span ends where the claim ends, or where the text ends, whichever
     * comes first; and a span that would be empty is pushed forward to cover
     * as much of the claim as the text holds.
     */
    private fun claimSpan(
        text: String,
        claimText: String,
        spanStart: Int,
        spanEnd: Int?,
    ): Pair<Int, Int> {
        val textLength = TextOffsets.length(text)
        val claimLength = TextOffsets.length(claimText)
        val start = spanStart
        var end = spanEnd ?: (start + claimLength)
        if (end > textLength) end = textLength
        if (end <= start) end = minOf(textLength, start + claimLength)
        return start to end
    }

    /**
     * The record's confidence, scaled by the share of the claim the quote
     * covers.
     *
     * A quote that covers the whole claim leaves the confidence alone; a
     * quote that covers part of it scales the confidence down, because the
     * claim is only as well supported as the part of it that was found. A
     * claim with no quote at all keeps the engine's own score.
     */
    private fun scaledByCoverage(
        record: CitationLineage,
        supporting: SupportingTextMatch,
    ): CitationLineage {
        val quote = supporting.quote ?: return record
        if (supporting.coverage < 1.0) {
            return CitationLineage(
                lineageId = record.lineageId,
                claimId = record.claimId,
                chunkId = record.chunkId,
                spanStart = record.spanStart,
                spanEnd = record.spanEnd,
                exactQuote = record.exactQuote,
                confidenceScore = roundTo(record.confidenceScore * supporting.coverage, 3),
                createdAt = record.createdAt,
            )
        }
        return record
    }
}
