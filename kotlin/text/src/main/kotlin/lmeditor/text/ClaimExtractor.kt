package lmeditor.text

import lmeditor.core.Citations
import lmeditor.core.Claim
import lmeditor.core.EntailmentStatus
import lmeditor.core.InlineCitation

/**
 * Sentence segmentation and atomic proposition extraction engine.
 */
class ClaimExtractor {

    /**
     * Split [text] into sentences while respecting markdown, abbreviations
     * and citations.
     *
     * @return the non-empty sentence strings, in order of appearance.
     */
    fun segmentSentences(text: String): List<String> {
        val cleaned = text.trim()
        if (cleaned.isEmpty()) return emptyList()

        // Split the text by lines first to cleanly isolate headers, list
        // items and blockquotes.
        val processedBlocks = mutableListOf<String>()
        val currentBlock = mutableListOf<String>()

        for (rawLine in splitLines(cleaned)) {
            val line = rawLine.trim()
            if (line.isEmpty()) {
                if (currentBlock.isNotEmpty()) {
                    processedBlocks.add(currentBlock.joinToString(" "))
                    currentBlock.clear()
                }
                continue
            }

            // A markdown heading or bullet point forms its own block.
            if (isBlockStart(line)) {
                if (currentBlock.isNotEmpty()) {
                    processedBlocks.add(currentBlock.joinToString(" "))
                    currentBlock.clear()
                }
                // Strip the markdown marker for cleaner sentence processing.
                val cleanMarkerLine = MARKER_PREFIX_PATTERN.replace(line, "").trim()
                if (cleanMarkerLine.isNotEmpty()) processedBlocks.add(cleanMarkerLine)
            } else {
                currentBlock.add(line)
            }
        }

        if (currentBlock.isNotEmpty()) processedBlocks.add(currentBlock.joinToString(" "))

        val sentences = mutableListOf<String>()
        for (block in processedBlocks) sentences.addAll(splitIntoSentences(block))

        return sentences.filter { it.isNotBlank() }
    }

    /**
     * Extract every inline citation (`[#chunk_id]`, `[chunk_id]`,
     * `[cite: chunk_id]`) with its exact span offsets.
     *
     * The marker grammar is owned by `lmeditor.core.Citations` so that the
     * drafter and the verifier can never disagree on what a citation is.
     *
     * @return the inline citations, in order of appearance.
     */
    fun extractCitations(text: String): List<InlineCitation> =
        TextOffsets.citationSpans(text).mapIndexed { index, span ->
            InlineCitation(
                citationId = "cit_%04d".format(index + 1),
                chunkId = span.chunkId,
                spanStart = span.start,
                spanEnd = span.end,
            )
        }

    /**
     * Deconstruct manuscript text into atomic, verifiable propositional claims.
     *
     * @param sectionId the identifier of the parent section.
     */
    fun extractClaims(text: String, sectionId: String = ""): List<Claim> {
        if (text.isBlank()) return emptyList()

        val sentences = segmentSentences(text)
        val claims = mutableListOf<Claim>()
        val secId = sectionId.ifEmpty { "sec_default" }
        // Search cursor: the offset in `text` from which the next sentence is
        // looked up. Advancing it across the document is what makes a repeated
        // sentence map to its own occurrence instead of always to the first
        // one.
        var cursor = 0

        for ((sentIdx, sentence) in sentences.withIndex()) {
            // 1. Extract the cited chunks referenced in this sentence.
            val citedChunkIds = extractCitations(sentence).map { it.chunkId }.distinct()

            // 2. Strip the citation markers from the sentence to get clean text.
            var cleanSentence = Citations.stripCitations(sentence)
            cleanSentence = WHITESPACE_RUN.replace(cleanSentence, " ").trim()
            cleanSentence = cleanSentence.trimEnd('.')

            if (cleanSentence.isEmpty()) continue

            // 3. Deconstruct compound clauses into atomic propositions.
            val propositions = deconstructPropositions(cleanSentence)

            for ((propIdx, proposition) in propositions.withIndex()) {
                val propText = proposition.trim()
                if (propText.isEmpty()) continue

                val claimId = "claim_${secId}_${sentIdx.pad(3)}_${propIdx.pad(2)}"

                // Calculate the relative character spans, if the sentence was
                // found in the original text. The lookup resumes where the
                // previous sentence ended, so a sentence that occurs more than
                // once is attributed to the occurrence that follows its
                // predecessor.
                var charStart = TextOffsets.indexOf(text, sentence, cursor)
                if (charStart < 0) {
                    // The sentence was normalised during segmentation (joined
                    // lines, collapsed whitespace); fall back to a global
                    // lookup.
                    charStart = TextOffsets.indexOf(text, sentence, 0)
                }
                val charEnd: Int
                if (charStart < 0) {
                    charStart = 0
                    charEnd = 0
                } else {
                    charEnd = charStart + TextOffsets.length(sentence)
                    cursor = charEnd
                }

                claims.add(
                    Claim(
                        claimId = claimId,
                        sectionId = secId,
                        sentenceIdx = sentIdx,
                        text = propText,
                        charStart = charStart,
                        charEnd = charEnd,
                        citedChunkIds = citedChunkIds,
                        entailmentStatus = EntailmentStatus.UNGROUNDED,
                        entailmentScore = 0.0,
                    ),
                )
            }
        }

        return claims
    }

    /** Heuristic sentence tokenizer handling periods, abbreviations and floats. */
    private fun splitIntoSentences(text: String): List<String> {
        val norm = WHITESPACE_RUN.replace(text, " ").trim()
        if (norm.isEmpty()) return emptyList()

        // A look-ahead split: after a terminator, before whitespace that is
        // followed by a capital letter, a quote or an opening bracket.
        val potentialSplits = SENTENCE_SPLIT_PATTERN.split(norm)
        val result = mutableListOf<String>()
        var carry = ""

        for (piece in potentialSplits) {
            val candidate = if (carry.isNotEmpty()) "$carry $piece".trim() else piece
            val tokens = candidate.split(WHITESPACE_RUN).filter { it.isNotEmpty() }
            val lastWord = tokens.lastOrNull() ?: ""
            // Check whether the last word is an abbreviation.
            val cleanWord = lastWord.trimEnd('.', '!', '?', ',', ';', ':', '"', '\'').lowercase()
            if (cleanWord in ABBREVIATIONS) {
                carry = candidate
                continue
            }

            result.add(candidate)
            carry = ""
        }

        if (carry.isNotEmpty()) result.add(carry)

        return reattachTrailingMarkers(result)
    }

    /**
     * Move a citation marker back onto the sentence it follows.
     *
     * A citation marks the claim it follows: "rates fell by 30 percent
     * [#chunk].". The sentence splitter, however, breaks after the full stop
     * and before the marker, because the marker starts with `[`. The marker
     * therefore landed at the *start* of the next sentence, and the sentence it
     * actually supports was left with no citation at all -- so a perfectly
     * grounded claim was recorded as having no premise.
     *
     * Moving a leading marker back to the end of the preceding sentence
     * restores the attribution. A marker that already has a sentence of its
     * own to sit in is left alone.
     */
    private fun reattachTrailingMarkers(sentences: List<String>): List<String> {
        val result = mutableListOf<String>()
        for (sentence in sentences) {
            var current = sentence
            val leading = LEADING_MARKER_PATTERN.find(current)
            if (leading != null && result.isNotEmpty()) {
                val marker = leading.groupValues[1]
                val previous = result[result.size - 1]
                if (!Citations.CITATION_PATTERN.containsMatchIn(previous)) {
                    result[result.size - 1] = "${previous.trimEnd()} $marker"
                    current = current.substring(leading.range.last + 1)
                }
            }
            result.add(current)
        }
        return result.filter { it.isNotBlank() }
    }

    /** Break down a compound sentence into distinct verifiable propositions. */
    private fun deconstructPropositions(sentence: String): List<String> {
        // A sentence containing semicolons is split on them.
        if (sentence.contains(';')) {
            return sentence.split(';').map { it.trim() }.filter { it.isNotEmpty() }
        }

        // Split on coordinating conjunctions with commas: ", and ", ", but ",
        // ", however ".
        val conjunctionSplit = CONJUNCTION_PATTERN.split(sentence)
        if (conjunctionSplit.size > 1) {
            val propositions = mutableListOf<String>()
            for (rawPart in conjunctionSplit) {
                val part = rawPart.trim()
                val words = part.split(WHITESPACE_RUN).filter { it.isNotEmpty() }
                if (words.size >= 3) {
                    // A part must have a minimal clause length.
                    propositions.add(part)
                } else if (propositions.isNotEmpty()) {
                    // Append a short fragment back to the previous clause.
                    propositions[propositions.size - 1] = "${propositions.last()}, $part"
                } else {
                    propositions.add(part)
                }
            }
            if (propositions.isNotEmpty()) return propositions
        }

        return listOf(sentence)
    }

    companion object {

        /**
         * Common abbreviations that must not trigger a sentence boundary.
         */
        val ABBREVIATIONS: Set<String> = setOf(
            "e.g", "i.e", "dr", "mr", "mrs", "ms", "prof", "vs", "al", "etc",
            "inc", "ltd", "u.s", "u.k", "fig", "tab", "eq", "no", "approx",
            "dept", "est", "vol",
        )

        /** Whether the line starts a markdown heading, bullet or ordered item. */
        private fun isBlockStart(line: String): Boolean =
            HEADING_PATTERN.containsMatchIn(line) ||
                BULLET_PATTERN.containsMatchIn(line) ||
                ORDERED_ITEM_PATTERN.containsMatchIn(line)
    }
}

/**
 * Compatibility alias: the extractor was renamed, and the old name still
 * resolves to it.
 */
typealias PropositionClaimExtractor = ClaimExtractor

/** The default extractor used by the module-level convenience functions. */
private val DEFAULT_EXTRACTOR: ClaimExtractor = ClaimExtractor()

/** Extract the claims of a section draft. */
fun extractClaims(text: String, sectionId: String = ""): List<Claim> =
    DEFAULT_EXTRACTOR.extractClaims(text, sectionId)

/** Extract the inline citations of a manuscript text. */
fun extractCitations(text: String): List<InlineCitation> =
    DEFAULT_EXTRACTOR.extractCitations(text)

/** Split a manuscript text into sentences. */
fun segmentSentences(text: String): List<String> =
    DEFAULT_EXTRACTOR.segmentSentences(text)

/**
 * Split [text] into lines, on the same boundaries Python's `str.splitlines`
 * uses.
 */
private fun splitLines(text: String): List<String> = LINE_BREAK_PATTERN.split(text)

/** The number, zero-padded to [width] digits. */
private fun Int.pad(width: Int): String = toString().padStart(width, '0')

/** A leading citation marker, and the whitespace that follows it. */
private val LEADING_MARKER_PATTERN: Regex = Regex("(?U)^\\s*(\\[[^\\]]*\\])\\s*")

/** A coordinating conjunction that joins two clauses with a comma. */
private val CONJUNCTION_PATTERN: Regex =
    Regex("(?iu),\\s+(?:and|but|while|whereas|however)\\s+")

/** A run of whitespace. */
private val WHITESPACE_RUN: Regex = Regex("(?U)\\s+")

/** A line break, on any of the boundaries Python's `str.splitlines` accepts. */
private val LINE_BREAK_PATTERN: Regex =
    Regex("\\r\\n|[\\n\\r\\u000B\\u000C\\u001C-\\u001E\\u0085\\u2028\\u2029]")

/** A markdown heading, a bullet point or an ordered list item. */
private val HEADING_PATTERN: Regex = Regex("(?U)^#{1,6}\\s+")
private val BULLET_PATTERN: Regex = Regex("(?U)^[-*+]\\s+")
private val ORDERED_ITEM_PATTERN: Regex = Regex("(?U)^\\d+\\.\\s+")

/** The markdown marker that opens a block, and the whitespace that follows it. */
private val MARKER_PREFIX_PATTERN: Regex = Regex("(?U)^(#{1,6}|[-*+]|\\d+\\.)\\s+")

/** A sentence boundary: a terminator, whitespace, and a capital after it. */
private val SENTENCE_SPLIT_PATTERN: Regex = Regex("(?U)(?<=[.!?])\\s+(?=[A-Z\"'\\[])")
