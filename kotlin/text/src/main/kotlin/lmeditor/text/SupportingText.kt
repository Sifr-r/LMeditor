package lmeditor.text

import lmeditor.core.CodePoints

/**
 * The text a claim and a passage share, and the share of the claim it covers.
 *
 * @property quote the verbatim text of the passage the claim shares with it,
 *   or `null` when the two share nothing worth quoting.
 * @property coverage the share of the claim's content words the quote
 *   accounts for.
 */
data class SupportingTextMatch(val quote: String?, val coverage: Double)

/**
 * The supporting-text rule: what a claim and a passage say in common.
 *
 * Port of `backend/src/lmeditor/provenance/service.py`'s `_supporting_text`.
 *
 * The quote is a verbatim substring of the passage: the longest run of
 * consecutive words the two have in common. A run of function words is not
 * evidence and is rejected, so the lineage engine never certifies a quotation
 * that says nothing.
 *
 * The coverage is the share of the claim's content words the run accounts
 * for: `1.0` when the whole claim is in the passage, less when only part of
 * it is.
 */
object SupportingText {

    /**
     * Function words.
     *
     * A claim and a passage that share only "the" share no evidence, and a
     * quote made of such words would earn the confidence of a verbatim
     * quotation.
     */
    val STOPWORDS: Set<String> = setOf(
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
        "had", "has", "have", "he", "her", "his", "in", "is", "it", "its", "of",
        "on", "or", "she", "that", "the", "their", "them", "there", "these",
        "they", "this", "to", "was", "were", "which", "who", "will", "with",
    )

    /**
     * The text [claimText] and [chunkText] share, and the claim's coverage.
     *
     * The longest run of consecutive equal words wins; when two runs are
     * equally long, the earlier one does, because the search keeps the first
     * match it finds and only replaces it with a strictly longer one.
     */
    fun supportingText(claimText: String, chunkText: String): SupportingTextMatch {
        val claimSpans = wordSpans(claimText)
        val chunkSpans = wordSpans(chunkText)
        if (claimSpans.isEmpty() || chunkSpans.isEmpty()) {
            return SupportingTextMatch(null, 0.0)
        }

        val claimWords = claimSpans.map { span -> claimText.substring(span.start, span.end).lowercase() }
        val chunkWords = chunkSpans.map { span -> chunkText.substring(span.start, span.end).lowercase() }

        var bestLength = 0
        var bestClaimStart = 0
        var bestChunkStart = 0
        for (claimIndex in claimWords.indices) {
            for (chunkIndex in chunkWords.indices) {
                if (claimWords[claimIndex] != chunkWords[chunkIndex]) continue
                var length = 0
                while (
                    claimIndex + length < claimWords.size &&
                    chunkIndex + length < chunkWords.size &&
                    claimWords[claimIndex + length] == chunkWords[chunkIndex + length]
                ) {
                    length++
                }
                if (length > bestLength) {
                    bestLength = length
                    bestClaimStart = claimIndex
                    bestChunkStart = chunkIndex
                }
            }
        }

        if (bestLength == 0) {
            return SupportingTextMatch(null, 0.0)
        }

        val matchedWords = claimWords.subList(bestClaimStart, bestClaimStart + bestLength)
        if (bestLength < 2 || matchedWords.none { word -> word !in STOPWORDS }) {
            // A single word, or a run of function words, is not a quotation.
            return SupportingTextMatch(null, 0.0)
        }

        val contentWords = claimWords.filter { word -> word !in STOPWORDS }
        val matchedContent = matchedWords.filter { word -> word !in STOPWORDS }
        val coverage = if (contentWords.isEmpty()) {
            1.0
        } else {
            minOf(1.0, matchedContent.size.toDouble() / contentWords.size.toDouble())
        }

        val first = chunkSpans[bestChunkStart]
        val last = chunkSpans[bestChunkStart + bestLength - 1]
        return SupportingTextMatch(chunkText.substring(first.start, last.end), coverage)
    }

    /**
     * The maximal runs of non-whitespace in [text], as offsets into it.
     *
     * This is Python's `re.finditer(r"\S+", text)`, and Python's `\S` is the
     * complement of Python's `\s` -- which is not Java's whitespace. The
     * non-breaking spaces are whitespace in Python, so they break a word
     * here too.
     */
    private fun wordSpans(text: String): List<Span> {
        val spans = mutableListOf<Span>()
        var index = 0
        var start = -1
        while (index < text.length) {
            val codePoint = text.codePointAt(index)
            if (!CodePoints.isPythonWhitespace(codePoint)) {
                if (start < 0) start = index
            } else if (start >= 0) {
                spans.add(Span(start, index))
                start = -1
            }
            index += Character.charCount(codePoint)
        }
        if (start >= 0) {
            spans.add(Span(start, text.length))
        }
        return spans
    }

    /** A run of text, as offsets into the text it came from. */
    private data class Span(val start: Int, val end: Int)
}
