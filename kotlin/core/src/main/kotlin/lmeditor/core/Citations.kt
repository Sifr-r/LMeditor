package lmeditor.core

/**
 * The canonical inline-citation grammar, shared by the drafting and
 * verification layers.
 *
 * Accepted marker forms (all parse to the bare chunk id, without the marker
 * prefix):
 *
 * * `[#chunk_id]` — the primary form emitted by the drafter
 * * `[chunk_id]`
 * * `[cite: chunk_id]`
 * * comma-separated lists of any of the above, e.g. `[#a, #b]`
 *
 * Markdown links `[text](url)` and images `![alt](url)` are never citations.
 *
 * The pattern is the same one the Python tier compiles, so a marker written
 * by one side is read identically by the other.
 */
object Citations {

    /** The id alphabet produced by the chunker (hex digests) plus test ids. */
    const val CHUNK_ID_PATTERN: String = "[A-Za-z0-9_.\\-]+"

    /** `#` and `cite:` are marker prefixes and are never part of the chunk id. */
    private const val ITEM_PATTERN: String = "(?:cite:\\s*)?#?$CHUNK_ID_PATTERN"

    /**
     * Single source of truth for the inline-citation marker grammar.
     *
     * `(?U)` makes `\s` Unicode-aware, matching Python's `re.UNICODE` behaviour
     * on `str` patterns.
     */
    val CITATION_PATTERN: Regex = Regex(
        "(?U)(?<!!)\\[(?<citeBody>" +
            "$ITEM_PATTERN(?:\\s*,\\s*$ITEM_PATTERN)*" +
            ")\\](?!\\()",
    )

    /** Splits one bracketed body into its comma-separated items. */
    private val ITEM_SEPARATOR_PATTERN: Regex = Regex("\\s*,\\s*")

    /** Strips the `cite:` and `#` marker prefixes from a single item. */
    private val MARKER_PREFIX_PATTERN: Regex = Regex("^(?:cite:\\s*)?#?")

    /** One citation marker found in a text. */
    data class CitationSpan(
        val chunkId: String,
        val start: Int,
        val end: Int,
    )

    /**
     * The bare chunk ids of one bracketed citation body.
     *
     * The body is the text between the brackets: `#a`, `cite: a`, or
     * `a, b`. The marker prefixes are stripped, and an item that is nothing
     * but whitespace is refused.
     *
     * This is the reading half of [CITATION_PATTERN], exposed so that a
     * consumer of a marker -- a client that draws it, for instance -- reads
     * the body with the same rule that wrote it.
     */
    fun chunkIdsOf(body: String): List<String> = bodyToChunkIds(body)

    /**
     * Parse one bracketed citation body into its bare chunk ids.
     */
    private fun bodyToChunkIds(body: String): List<String> {
        val chunkIds = mutableListOf<String>()
        for (rawItem in ITEM_SEPARATOR_PATTERN.split(body)) {
            val chunkId = MARKER_PREFIX_PATTERN.replace(rawItem.trim(), "").trim()
            if (chunkId.isNotEmpty()) chunkIds.add(chunkId)
        }
        return chunkIds
    }

    /**
     * Return the `(chunkId, start, end)` triple for every citation marker in
     * [text].
     *
     * `start`/`end` are the offsets of the whole bracketed marker, so a
     * comma-separated group such as `[#a, #b]` reports the same span for both
     * chunk ids. Order follows the order of appearance in [text].
     *
     * The offsets are **code points**, not UTF-16 units, so they are the same
     * offsets the Python tier reports and can be compared with a chunk's or a
     * claim's character offsets directly.
     */
    fun iterCitationSpans(text: String): List<CitationSpan> {
        val spans = mutableListOf<CitationSpan>()
        for (match in CITATION_PATTERN.findAll(text)) {
            val start = CodePoints.offsetOf(text, match.range.first)
            val end = CodePoints.offsetOf(text, match.range.last + 1)
            val body = match.groups["citeBody"]?.value ?: continue
            for (chunkId in bodyToChunkIds(body)) {
                spans.add(CitationSpan(chunkId, start, end))
            }
        }
        return spans
    }

    /** Return the ordered, de-duplicated chunk ids cited in [text]. */
    fun extractChunkIds(text: String): List<String> {
        val seen = LinkedHashSet<String>()
        for (span in iterCitationSpans(text)) seen.add(span.chunkId)
        return seen.toList()
    }

    /** Return [text] with every citation marker removed. */
    fun stripCitations(text: String): String = CITATION_PATTERN.replace(text, "")
}
