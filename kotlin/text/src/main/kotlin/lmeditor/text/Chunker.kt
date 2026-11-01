package lmeditor.text

import lmeditor.core.Chunker
import lmeditor.core.Hashing
import lmeditor.core.SourceChunk
import lmeditor.core.SpanAlignmentError
import lmeditor.core.ValidationError

/**
 * Characters per token used by [estimateTokenCount].
 *
 * Four is the commonly cited average for English prose.
 */
const val CHARS_PER_TOKEN: Int = 4

/**
 * The window a chunker uses when the caller names none: 500 characters.
 *
 * It is a named constant because it is a rule and not a setting of one
 * caller: the seam serves the chunker, and a chunker that was configured with
 * another window must answer with that window's chunks.
 */
const val DEFAULT_WINDOW_SIZE: Int = 500

/**
 * The overlap two consecutive chunks share when the caller names none:
 * 100 characters.
 */
const val DEFAULT_OVERLAP_SIZE: Int = 100

/**
 * Compute the deterministic SHA-256 chunk id, as a lowercase hex string.
 *
 * The payload is `document_id:char_start:char_end:text`, byte for byte the
 * payload the Python tier hashes, so a chunk id minted on one side of the
 * seam resolves on the other.
 */
fun computeChunkId(documentId: String, charStart: Int, charEnd: Int, text: String): String =
    Hashing.sha256Hex("$documentId:$charStart:$charEnd:$text")

/**
 * Compute the UTF-8 byte offsets of a character span in [text].
 *
 * The span is given in code points, as the Python tier counts them; the byte
 * offsets are the UTF-8 lengths of the two halves of the span. They are *not*
 * the character offsets: one character can be several bytes.
 */
fun getByteOffsets(text: String, charStart: Int, charEnd: Int): Pair<Int, Int> {
    val byteStart = TextOffsets.utf8Length(text, charStart)
    val byteEnd = byteStart + TextOffsets.utf8Size(TextOffsets.slice(text, charStart, charEnd))
    return byteStart to byteEnd
}

/**
 * Return an *estimate* of the number of tokens in [text].
 *
 * This is **not** a tokenizer count and must not be used as one: no tokenizer
 * is loaded, and the value is derived from the character length alone,
 * assuming the commonly cited average of roughly four characters per token in
 * English prose. It is accurate to within a factor of about two for prose and
 * useless for code or tables.
 *
 * It exists so a chunk can report a size without dragging a tokenizer into
 * the ingestion path, where the cost would be paid for every chunk of every
 * document. Treat the number as an order of magnitude.
 */
fun estimateTokenCount(text: String): Int {
    if (text.isEmpty()) return 0
    val characters = TextOffsets.length(text)
    val tokens = (characters + CHARS_PER_TOKEN - 1) / CHARS_PER_TOKEN
    return maxOf(1, tokens)
}

/**
 * Sliding-window text chunker with character offset and byte offset
 * preservation.
 *
 * The window cuts wherever the character count says so, which lands in the
 * middle of a sentence more often than not. Use [SentenceAwareChunker] when
 * the chunks are meant to be cited.
 */
class SlidingWindowChunker private constructor(
    /** The effective window size, in characters. */
    val windowSize: Int,
    /** The number of characters two consecutive chunks share. */
    val overlapSize: Int,
) : Chunker {

    constructor(
        windowSize: Int = 500,
        overlapSize: Int = 100,
        chunkSize: Int? = null,
    ) : this(
        windowSize = chunkSize ?: windowSize,
        overlapSize = overlapSize,
    )

    init {
        if (windowSize <= 0) {
            throw ValidationError(
                "window_size must be positive, got $windowSize",
                field = "window_size",
                value = windowSize,
            )
        }
        if (overlapSize < 0 || overlapSize >= windowSize) {
            throw ValidationError(
                "overlap_size must be non-negative and strictly less than window_size ($windowSize), got $overlapSize",
                field = "overlap_size",
                value = overlapSize,
            )
        }
    }

    /**
     * Convenience method accepting a document title, which is recorded in the
     * chunk metadata.
     */
    fun chunkText(
        text: String,
        documentId: String,
        documentTitle: String = "",
        metadata: Map<String, Any?> = emptyMap(),
    ): List<SourceChunk> {
        val meta = metadata.toMutableMap()
        if (documentTitle.isNotEmpty()) meta["document_title"] = documentTitle
        return chunk(text, documentId, meta)
    }

    /** Segment [text] into overlapping sliding-window chunks. */
    override fun chunk(
        text: String,
        documentId: String,
        metadata: Map<String, Any?>,
    ): List<SourceChunk> {
        requireChunkable(text, documentId)

        val step = windowSize - overlapSize
        val length = TextOffsets.length(text)
        val chunks = mutableListOf<SourceChunk>()
        var start = 0

        while (start < length) {
            val end = minOf(start + windowSize, length)
            val chunk = buildChunk(text, documentId, start, end, metadata)
            verifyBoundaryIntegrity(text, chunk)
            chunks.add(chunk)

            if (end >= length) break
            start += step
        }

        return chunks
    }

    /** Segment [text] into overlapping sliding-window chunks. */
    fun chunk(text: String, documentId: String): List<SourceChunk> =
        chunk(text, documentId, emptyMap())
}

/**
 * Semantic chunker respecting natural paragraph and sentence boundaries.
 */
class SemanticChunker(
    /** The largest number of characters one chunk may hold. */
    val maxChunkSize: Int = 1000,
    /** The smallest number of characters a trailing chunk may hold. */
    val minChunkSize: Int = 50,
) : Chunker {

    init {
        if (minChunkSize <= 0) {
            throw ValidationError(
                "min_chunk_size must be positive, got $minChunkSize",
                field = "min_chunk_size",
                value = minChunkSize,
            )
        }
        if (maxChunkSize < minChunkSize) {
            throw ValidationError(
                "max_chunk_size ($maxChunkSize) must be greater than or equal to min_chunk_size ($minChunkSize)",
                field = "max_chunk_size",
                value = maxChunkSize,
            )
        }
    }

    /** Segment [text] into semantic units respecting natural boundaries. */
    override fun chunk(
        text: String,
        documentId: String,
        metadata: Map<String, Any?>,
    ): List<SourceChunk> {
        requireChunkable(text, documentId)

        val rawUnits = splitIntoUnits(text)
        if (rawUnits.isEmpty()) {
            // The entire text is a single span.
            return listOf(buildChunk(text, documentId, 0, TextOffsets.length(text), metadata))
        }

        // Merge adjacent units up to max_chunk_size.
        val merged = mutableListOf<Pair<Int, Int>>()
        var currentStart = rawUnits[0].first
        var currentEnd = rawUnits[0].second

        for ((nextStart, nextEnd) in rawUnits.drop(1)) {
            val candidateLength = nextEnd - currentStart
            if (candidateLength <= maxChunkSize) {
                currentEnd = nextEnd
            } else {
                merged.add(currentStart to currentEnd)
                currentStart = nextStart
                currentEnd = nextEnd
            }
        }
        merged.add(currentStart to currentEnd)

        // A last merged span that is smaller than min_chunk_size merges
        // backwards, when the result still fits.
        if (merged.size > 1) {
            val last = merged[merged.size - 1]
            val previous = merged[merged.size - 2]
            if ((last.second - last.first) < minChunkSize &&
                (last.second - previous.first) <= maxChunkSize
            ) {
                merged[merged.size - 2] = previous.first to last.second
                merged.removeAt(merged.size - 1)
            }
        }

        val chunks = mutableListOf<SourceChunk>()
        for ((chunkStart, chunkEnd) in merged) {
            val chunk = buildChunk(text, documentId, chunkStart, chunkEnd, metadata)
            verifyBoundaryIntegrity(text, chunk)
            chunks.add(chunk)
        }
        return chunks
    }

    /** Segment [text] into semantic units respecting natural boundaries. */
    fun chunk(text: String, documentId: String): List<SourceChunk> =
        chunk(text, documentId, emptyMap())

    /** Split [text] into atomic spans, preserving character offsets. */
    private fun splitIntoUnits(text: String): List<Pair<Int, Int>> {
        // Candidate split boundaries: paragraphs first, then sentences.
        val spans = mutableListOf<Pair<Int, Int>>()
        var lastIndex = 0

        for (match in SEMANTIC_DELIMITERS.findAll(text)) {
            val matchStart = TextOffsets.codePointOffset(text, match.range.first)
            val matchEnd = TextOffsets.codePointOffset(text, match.range.last + 1)
            if (matchStart > lastIndex) spans.add(lastIndex to matchStart)
            lastIndex = matchEnd
        }

        val length = TextOffsets.length(text)
        if (lastIndex < length) spans.add(lastIndex to length)

        // Subdivide any span exceeding max_chunk_size.
        val refined = mutableListOf<Pair<Int, Int>>()
        for ((spanStart, spanEnd) in spans) {
            if (spanEnd - spanStart <= maxChunkSize) {
                refined.add(spanStart to spanEnd)
                continue
            }
            val subText = TextOffsets.slice(text, spanStart, spanEnd)
            var subLast = 0
            for (subMatch in WHITESPACE_RUN.findAll(subText)) {
                // The match offsets are UTF-16 indices into `subText`, but the
                // span this chunker works in is code points. Converting here
                // keeps the two spaces apart: a text with an astral character
                // before the split point would otherwise shift every boundary
                // that follows it.
                val subStart = TextOffsets.codePointOffset(subText, subMatch.range.first)
                val subEnd = TextOffsets.codePointOffset(subText, subMatch.range.last + 1)
                if (subStart > subLast) {
                    val candidateStart = spanStart + subLast
                    val candidateEnd = spanStart + subStart
                    if (candidateEnd - candidateStart > maxChunkSize) {
                        // Hard split when a single word is too large.
                        var cursor = candidateStart
                        while (cursor < candidateEnd) {
                            val hardEnd = minOf(cursor + maxChunkSize, candidateEnd)
                            refined.add(cursor to hardEnd)
                            cursor = hardEnd
                        }
                    } else {
                        refined.add(candidateStart to candidateEnd)
                    }
                }
                subLast = subEnd
            }
            val subLength = TextOffsets.length(subText)
            if (subLast < subLength) {
                val candidateStart = spanStart + subLast
                val candidateEnd = spanEnd
                if (candidateEnd - candidateStart > maxChunkSize) {
                    var cursor = candidateStart
                    while (cursor < candidateEnd) {
                        val hardEnd = minOf(cursor + maxChunkSize, candidateEnd)
                        refined.add(cursor to hardEnd)
                        cursor = hardEnd
                    }
                } else {
                    refined.add(candidateStart to candidateEnd)
                }
            }
        }

        return refined.filter { it.second > it.first }
    }
}

/**
 * Chunker that packs whole sentences into each chunk.
 *
 * The sliding window cuts wherever the character count says so, which lands
 * in the middle of a sentence more often than not. That matters here more
 * than it would in a summariser: a chunk is the unit a citation points at,
 * so a chunk that starts "...because the model hallucinates" and ends "...the
 * model hallucinates less often" makes the citation span unreconstructable
 * and the quoted passage unreadable.
 *
 * This chunker therefore packs whole *units* -- sentences, and paragraphs
 * where a paragraph break falls -- into each chunk, and never splits one.
 * The only exception is a single unit longer than the window itself (a
 * sentence of four thousand characters); that one is cut, because a window
 * is a hard limit on a chunk's size.
 *
 * The overlap between two consecutive chunks is also made of whole units:
 * it is the shortest tail of the previous chunk that is at least
 * `overlap_size` characters long, snapped outwards to a unit boundary so
 * that the repeated text is never a fragment of a sentence.
 *
 * The units are *contiguous*: every character of the source belongs to
 * exactly one unit, and the chunks are contiguous substrings of the source
 * text, so `text[chunk.charStart:chunk.charEnd] == chunk.text` holds for
 * every chunk.
 */
class SentenceAwareChunker private constructor(
    /** The effective window size, in characters. */
    val windowSize: Int,
    /** The number of characters two consecutive chunks share. */
    val overlapSize: Int,
) : Chunker {

    constructor(
        windowSize: Int = DEFAULT_WINDOW_SIZE,
        overlapSize: Int = DEFAULT_OVERLAP_SIZE,
        chunkSize: Int? = null,
    ) : this(
        windowSize = chunkSize ?: windowSize,
        overlapSize = overlapSize,
    )

    init {
        if (windowSize <= 0) {
            throw ValidationError(
                "window_size must be positive, got $windowSize",
                field = "window_size",
                value = windowSize,
            )
        }
        if (overlapSize < 0 || overlapSize >= windowSize) {
            throw ValidationError(
                "overlap_size must be non-negative and strictly less than window_size ($windowSize), got $overlapSize",
                field = "overlap_size",
                value = overlapSize,
            )
        }
    }

    /**
     * Convenience method accepting a document title, which is recorded in the
     * chunk metadata.
     */
    fun chunkText(
        text: String,
        documentId: String,
        documentTitle: String = "",
        metadata: Map<String, Any?> = emptyMap(),
    ): List<SourceChunk> {
        val meta = metadata.toMutableMap()
        if (documentTitle.isNotEmpty()) meta["document_title"] = documentTitle
        return chunk(text, documentId, meta)
    }

    /** Segment [text] into chunks that respect sentence boundaries. */
    override fun chunk(
        text: String,
        documentId: String,
        metadata: Map<String, Any?>,
    ): List<SourceChunk> {
        requireChunkable(text, documentId)

        val units = splitLongUnits(splitIntoUnits(text))
        if (units.isEmpty()) {
            return listOf(buildChunk(text, documentId, 0, TextOffsets.length(text), metadata))
        }

        val chunks = mutableListOf<SourceChunk>()
        var index = 0
        val total = units.size
        while (index < total) {
            val start = units[index].first
            var end = units[index].second
            var cursor = index + 1
            while (cursor < total && units[cursor].second - start <= windowSize) {
                end = units[cursor].second
                cursor++
            }

            val chunk = buildChunk(text, documentId, start, end, metadata)
            verifyBoundaryIntegrity(text, chunk)
            chunks.add(chunk)

            if (cursor >= total) break

            if (cursor - 1 <= index) {
                // A single unit filled the chunk: continue after it, with no
                // overlap, because repeating the unit would not advance.
                index = cursor
                continue
            }

            // The next chunk starts at the earliest unit whose tail is at
            // least overlap_size characters long.
            var nextIndex = cursor - 1
            while (nextIndex > index + 1 &&
                units[cursor - 1].second - units[nextIndex - 1].first >= overlapSize
            ) {
                nextIndex--
            }
            index = nextIndex
        }

        return chunks
    }

    /** Segment [text] into chunks that respect sentence boundaries. */
    fun chunk(text: String, documentId: String): List<SourceChunk> =
        chunk(text, documentId, emptyMap())

    /** Split [text] into contiguous sentence / paragraph units. */
    private fun splitIntoUnits(text: String): List<Pair<Int, Int>> {
        val boundaries = sortedSetOf<Int>()
        boundaries.add(0)
        for (pattern in listOf(PARAGRAPH_BREAK, SENTENCE_END)) {
            for (match in pattern.findAll(text)) {
                boundaries.add(TextOffsets.codePointOffset(text, match.range.last + 1))
            }
        }
        val ordered = boundaries.toList()

        val units = mutableListOf<Pair<Int, Int>>()
        var previous = 0
        for (boundary in ordered) {
            if (boundary > previous) {
                units.add(previous to boundary)
                previous = boundary
            }
        }
        val length = TextOffsets.length(text)
        if (previous < length) units.add(previous to length)
        return units
    }

    /** Cut units that are longer than the window into window-sized pieces. */
    private fun splitLongUnits(units: List<Pair<Int, Int>>): List<Pair<Int, Int>> {
        val refined = mutableListOf<Pair<Int, Int>>()
        for ((start, end) in units) {
            var cursor = start
            while (end - cursor > windowSize) {
                refined.add(cursor to (cursor + windowSize))
                cursor += windowSize
            }
            if (end > cursor) refined.add(cursor to end)
        }
        return refined
    }
}

/**
 * Compatibility alias.
 *
 * The name used to point at the sliding window, which is not source-aware at
 * all; it now points at the chunker that is, so every caller of
 * `SourceChunker` -- including the ones outside this module -- gets
 * sentence-aligned chunks.
 */
typealias SourceChunker = SentenceAwareChunker

/** A paragraph break: a blank line, optionally with spaces between the two newlines. */
private val PARAGRAPH_BREAK: Regex = Regex("\\n[ \\t]*\\n")

/**
 * A sentence end: a terminator, any closing quotes or brackets, and the
 * whitespace that follows it.
 *
 * The whitespace is part of the unit so that the units -- and therefore the
 * chunks -- stay contiguous substrings of the source text.
 */
private val SENTENCE_END: Regex = Regex("[.!?][\"'\\)\\]]*[ \\t\\r\\n]+")

/** Paragraph breaks and sentence ends, used by the semantic chunker. */
private val SEMANTIC_DELIMITERS: Regex = Regex("(?U)(\\n\\n+|(?<=[.!?])\\s+)")

/** A run of whitespace. */
private val WHITESPACE_RUN: Regex = Regex("(?U)\\s+")

/** Reject the input a chunker cannot work with. */
private fun requireChunkable(text: String, documentId: String) {
    if (text.isBlank()) {
        throw ValidationError(
            "text cannot be empty or only whitespace",
            field = "text",
        )
    }
    if (documentId.isBlank()) {
        throw ValidationError(
            "document_id cannot be empty or only whitespace",
            field = "document_id",
        )
    }
}

/** Build the chunk for `text[start:end]`, with its token estimate filled in. */
private fun buildChunk(
    text: String,
    documentId: String,
    start: Int,
    end: Int,
    metadata: Map<String, Any?>,
): SourceChunk {
    val chunkText = TextOffsets.slice(text, start, end)
    val (byteStart, byteEnd) = getByteOffsets(text, start, end)
    val chunk = SourceChunk.create(
        documentId = documentId,
        text = chunkText,
        charStart = start,
        charEnd = end,
        byteStart = byteStart,
        byteEnd = byteEnd,
        metadata = metadata.toMap(),
    )
    // `copy` rather than a second constructor call: the chunk id is derived
    // from the text and the offsets, so it must not be recomputed.
    return chunk.copy(tokenCount = estimateTokenCount(chunk.text))
}

/**
 * Assert that the chunk really is the slice of the source it claims to be.
 *
 * A chunk that does not carry its own source text cannot be cited, so this
 * is a hard failure rather than a warning.
 */
private fun verifyBoundaryIntegrity(text: String, chunk: SourceChunk) {
    if (TextOffsets.slice(text, chunk.charStart, chunk.charEnd) != chunk.text) {
        throw SpanAlignmentError(
            spanStart = chunk.charStart,
            spanEnd = chunk.charEnd,
            docLength = TextOffsets.length(text),
            message = "Boundary integrity violation: chunk text mismatch at " +
                "[${chunk.charStart}:${chunk.charEnd}]",
        )
    }
}
