package lmeditor.discovery

import lmeditor.core.CodePoints

/**
 * Deterministic lexical analysis of source material for the discovery layer.
 *
 * Port of `backend/src/lmeditor/discovery/source_analysis.py`.
 *
 * The discovery layer has to say something *about the corpus it was given*.
 * Three modules need the same three primitives -- the salient vocabulary of a
 * passage, the findings a passage actually states, and the headings a
 * document already carries -- so they live here instead of being
 * re-implemented three times with three different notions of "salient".
 *
 * Everything in this module is deterministic and dependency-free: the same
 * text always yields the same terms, findings and headings. That is what
 * makes the fallback path of the discovery layer reproducible and testable.
 */
object SourceAnalysis {
    /** Function words excluded from the content vocabulary. */
    val STOPWORDS: Set<String> = setOf(
        "the", "a", "an", "and", "or", "but", "if", "then", "than", "so", "because",
        "of", "in", "on", "at", "to", "for", "with", "by", "from", "into", "onto",
        "over", "under", "about", "after", "before", "during", "while", "as",
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
        "do", "does", "did", "will", "would", "can", "could", "should", "may",
        "might", "must", "shall", "that", "this", "these", "those", "it", "its",
        "they", "them", "their", "we", "us", "our", "you", "your", "he", "she",
        "his", "her", "not", "no", "never", "also", "such", "there", "here",
        "which", "who", "what", "when", "where", "how", "why", "all", "any",
        "each", "other", "more", "most", "some", "only", "own", "same", "both",
        "very", "however", "therefore", "thus", "hence", "furthermore", "moreover",
    )

    /** Tokens that describe the act of writing rather than the subject matter. */
    val GENERIC_TERMS: Set<String> = setOf(
        "study", "studies", "paper", "papers", "article", "articles", "research",
        "researcher", "researchers", "authors", "author", "data", "results",
        "result", "method", "methods", "methodology", "analysis", "analyses",
        "evidence", "findings", "finding", "conclusion", "conclusions", "report",
        "reports", "section", "sections", "document", "documents", "text",
        "passage", "passages", "source", "sources", "example", "examples",
        "figure", "figures", "table", "tables", "introduction", "background",
        "overview", "summary", "abstract", "discussion", "appendix", "chapter",
        "paper's", "study's",
    )

    /**
     * Verb families that mark a sentence as a statement of a finding.
     *
     * Each family lists the forms a writer actually uses. Listing the
     * families rather than a flat bag of words is what keeps the lexicon
     * honest: a family that carried only its past tense (`reduced`) silently
     * ignored the present tense (`reduce`, `reduces`) that most source prose
     * is written in, so a corpus of plain present-tense sentences produced
     * no findings at all and the discovery layer fell back to bare
     * vocabulary.
     */
    private val FINDING_VERB_FAMILIES: List<List<String>> = listOf(
        // Verbs of discovery, assertion and measurement.
        listOf("show", "shows", "showed", "shown"),
        listOf("find", "finds", "found"),
        listOf("reveal", "reveals", "revealed"),
        listOf("demonstrate", "demonstrates", "demonstrated"),
        listOf("report", "reports", "reported"),
        listOf("observe", "observes", "observed"),
        listOf("measure", "measures", "measured"),
        listOf("record", "records", "recorded"),
        listOf("identify", "identifies", "identified"),
        listOf("document", "documents", "documented"),
        listOf("conclude", "concludes", "concluded"),
        listOf("estimate", "estimates", "estimated"),
        listOf("confirm", "confirms", "confirmed"),
        listOf("indicate", "indicates", "indicated"),
        listOf("suggest", "suggests", "suggested"),
        listOf("establish", "establishes", "established"),
        listOf("yield", "yields", "yielded"),
        listOf("result", "results", "resulted"),
        // Verbs of change and direction.
        listOf("reduce", "reduces", "reduced"),
        listOf("increase", "increases", "increased"),
        listOf("fall", "falls", "fell"),
        listOf("rise", "rises", "rose"),
        listOf("grow", "grows", "grew"),
        listOf("decline", "declines", "declined"),
        listOf("improve", "improves", "improved"),
        listOf("worsen", "worsens", "worsened"),
        listOf("deteriorate", "deteriorates", "deteriorated"),
        listOf("remain", "remains", "remained"),
        listOf("double", "doubles", "doubled"),
        listOf("halve", "halves", "halved"),
        listOf("triple", "triples", "tripled"),
        listOf("exceed", "exceeds", "exceeded"),
        listOf("drop", "drops", "dropped"),
        listOf("climb", "climbs", "climbed"),
        listOf("peak", "peaks", "peaked"),
        listOf("lower", "lowers", "lowered"),
        listOf("raise", "raises", "raised"),
        listOf("reach", "reaches", "reached"),
        listOf("expand", "expands", "expanded"),
        listOf("contract", "contracts", "contracted"),
        listOf("accelerate", "accelerates", "accelerated"),
        listOf("delay", "delays", "delayed"),
        listOf("prevent", "prevents", "prevented"),
        listOf("cause", "causes", "caused"),
        listOf("produce", "produces", "produced"),
        listOf("achieve", "achieves", "achieved"),
        listOf("maintain", "maintains", "maintained"),
        listOf("shift", "shifts", "shifted"),
    )

    /**
     * The finding verbs, as one alternation.
     *
     * Matching whole words rather than substrings keeps "find" from matching
     * inside "findings" and "report" from matching inside "reported" for
     * reasons that have nothing to do with the word actually being a verb.
     *
     * The alternatives are ordered longest-first so the pattern is
     * deterministic; the alphabetical tiebreak on equal lengths is what makes
     * the compiled pattern reproducible from one run to the next.
     *
     * The word boundary is *not* in the pattern. Python's `\b` is Unicode-aware
     * for a `str` pattern, and it is computed from Python's `\w`, which is
     * "alphanumeric or underscore". Java's `\w` is not that set, even with
     * `(?U)`: Java adds the combining marks, and it reads a surrogate pair as
     * two characters that are not word characters. The boundary is therefore
     * decided in code, from Python's own definition of a word character, and
     * the pattern carries the verbs alone.
     */
    private val FINDING_VERB_RE: Regex = Regex(
        FINDING_VERB_FAMILIES.flatten()
            .distinct()
            .sortedWith(compareByDescending<String> { it.length }.thenBy { it })
            .joinToString("|", prefix = "(?:", postfix = ")"),
    )

    /** The characters a finding verb can start with. */
    private val VERB_INITIALS: Set<Char> =
        FINDING_VERB_FAMILIES.flatten().map { it.first() }.toSet()

    /**
     * Heading forms recognised in a source document.
     *
     * The classes are Python's, not Java's: Python's `\d` is every Unicode
     * decimal digit, Python's `\S` is "not whitespace" over the Unicode
     * whitespace set, and Python's `.` is "any character but a newline" --
     * where Java's `.` also refuses a carriage return, a NEL and the line
     * separators. Python's `re.IGNORECASE` also folds case over the whole
     * Unicode range, which is what the `(?u)` flag asks Java for.
     */
    private val HEADING_PATTERNS: List<Regex> = listOf(
        Regex("^#{1,6}[$PY_SPACE_CLASS]+($PY_DOT_CLASS+?)[$PY_SPACE_CLASS]*$"),
        Regex("^((?:[IVXLC]+|\\p{Nd}+)[.)][$PY_SPACE_CLASS]+[^$PY_SPACE_CLASS]$PY_DOT_CLASS*?)[$PY_SPACE_CLASS]*$"),
        Regex(
            "(?u)^(?:chapter|section|part)[$PY_SPACE_CLASS]+([^$PY_SPACE_CLASS]$PY_DOT_CLASS*?)[$PY_SPACE_CLASS]*$",
            RegexOption.IGNORE_CASE,
        ),
        Regex("^([A-Z][A-Z0-9 ,'\\-]{6,})$"),
    )

    /** Lower-cased word tokens of [text]. */
    fun tokenize(text: String): List<String> =
        TOKEN_RE.findAll(text.lowercase()).map { it.value }.toList()

    /**
     * Tokens of [text] that carry content, in order of appearance.
     *
     * Identifiers are excluded: they are not vocabulary, and the discovery
     * layer reads this list to decide what a corpus is about.
     */
    fun contentTokens(text: String): List<String> = tokenize(text).filter { token ->
        token !in STOPWORDS &&
            CodePoints.length(token) > 2 &&
            !isDigits(token) &&
            CodePoints.length(token) <= MAX_WORD_LENGTH
    }

    /**
     * The most distinctive vocabulary of [text].
     *
     * Terms are scored by frequency and ties are broken towards the longer,
     * more specific word. Tokens too long to be words never enter the
     * ranking at all, because a reader never names a passage by its digest.
     */
    fun salientTerms(text: String, limit: Int = 12): List<String> {
        val tokens = contentTokens(text)
        if (tokens.isEmpty()) return emptyList()

        val counts = LinkedHashMap<String, Int>()
        for (token in tokens) counts[token] = (counts[token] ?: 0) + 1

        return counts.entries
            .sortedWith(
                compareByDescending<Map.Entry<String, Int>> { it.value }
                    .thenByDescending { CodePoints.length(it.key) }
                    .thenBy { it.key },
            )
            .let { takePython(it, limit) }
            .map { it.key }
    }

    /**
     * Salient terms of several passages at once.
     *
     * A term that occurs in every passage is demoted: it is part of the
     * shared vocabulary of the corpus rather than a finding of any one
     * passage.
     */
    fun salientTermsAcross(texts: List<String>, limit: Int = 12): List<String> {
        if (texts.isEmpty()) return emptyList()

        val counts = LinkedHashMap<String, Int>()
        val documentFrequency = LinkedHashMap<String, Int>()
        for (text in texts) {
            // A set, so a term repeated inside one passage counts once.
            for (token in contentTokens(text).toSet()) {
                counts[token] = (counts[token] ?: 0) + 1
                documentFrequency[token] = (documentFrequency[token] ?: 0) + 1
            }
        }

        val total = texts.size
        return counts.entries
            .sortedWith(
                compareByDescending<Map.Entry<String, Int>> {
                    it.value * (
                        1.0 + (total - (documentFrequency[it.key] ?: 0)).toDouble() / total.toDouble()
                        )
                }.thenBy { it.key },
            )
            .let { takePython(it, limit) }
            .map { it.key }
    }

    /** Split [text] into sentences on terminal punctuation. */
    fun splitSentences(text: String): List<String> =
        SENTENCE_SPLIT_RE.split(strip(text))
            .map { strip(it) }
            .filter { it.isNotEmpty() }

    /**
     * The sentences of [text] that state a finding.
     *
     * A finding sentence is one that carries a quantity or an explicit
     * finding verb -- those are the sentences a source-grounded persona can
     * interrogate.
     */
    fun extractFindings(text: String, limit: Int = 6): List<String> {
        val findings = mutableListOf<String>()
        for (sentence in splitSentences(text)) {
            val lowered = sentence.lowercase()
            val hasNumber = lowered.codePoints().anyMatch { Character.isDigit(it) }
            val hasVerb = containsFindingVerb(lowered)
            if (hasNumber || hasVerb) findings.add(strip(sentence))
            if (findings.size >= limit) break
        }
        return findings
    }

    /**
     * Headings that the document itself carries.
     *
     * Only line-initial heading forms are recognised, so a sentence that
     * merely starts with a capital letter is not mistaken for a heading.
     */
    fun detectHeadings(text: String): List<String> {
        val headings = mutableListOf<String>()
        for (rawLine in splitLines(text)) {
            val line = strip(rawLine)
            if (line.isEmpty()) continue
            for (pattern in HEADING_PATTERNS) {
                val match = pattern.matchEntire(line) ?: continue
                val title = strip(match.groupValues[1])
                if (CodePoints.length(title) in 3..120) headings.add(title)
                break
            }
        }
        // Preserve order while removing duplicates.
        val seen = mutableSetOf<String>()
        val unique = mutableListOf<String>()
        for (heading in headings) {
            val key = heading.lowercase()
            if (seen.add(key)) unique.add(heading)
        }
        return unique
    }

    /** Join [terms] into a readable section heading fragment. */
    fun titleCaseTerms(terms: List<String>): String =
        terms.joinToString(" & ") { capitalize(it) }

    /**
     * Group the indices of [texts] into topical clusters.
     *
     * The clustering is agglomerative and fully deterministic: the two most
     * similar clusters are merged until the requested number of clusters
     * remains. When no two clusters share any vocabulary the two smallest
     * clusters are merged instead, because a corpus that is genuinely
     * heterogeneous still has to be reported as a hierarchy.
     */
    fun clusterByTerms(texts: List<String>, numClusters: Int): List<List<Int>> {
        val count = texts.size
        if (count == 0) return emptyList()
        val target = maxOf(1, minOf(numClusters, count))

        val tokenSets = texts.map { contentTokens(it).toSet() }
        var clusters: List<List<Int>> = List(count) { listOf(it) }

        while (clusters.size > target) {
            val clusterTokens = clusters.map { indices ->
                indices.flatMap { tokenSets[it] }.toSet()
            }

            var bestSimilarity = -1.0
            var bestLeft = 0
            var bestRight = 1
            for (a in clusters.indices) {
                for (b in a + 1 until clusters.size) {
                    val similarity = jaccard(clusterTokens[a], clusterTokens[b])
                    if (similarity > bestSimilarity) {
                        bestSimilarity = similarity
                        bestLeft = a
                        bestRight = b
                    }
                }
            }

            if (bestSimilarity > 0.0) {
                clusters = mergeClusters(clusters, bestLeft, bestRight)
            } else {
                // No two clusters share any vocabulary: merge the two smallest,
                // ties broken by position, so the hierarchy still has a shape.
                val order = clusters.indices.sortedWith(
                    compareBy({ clusters[it].size }, { it }),
                )
                clusters = mergeClusters(clusters, order[0], order[1])
            }
        }
        return clusters
    }

    /**
     * Strip the leading and trailing whitespace of [text].
     *
     * Python's `str.strip()` treats the full Unicode whitespace set as
     * whitespace, which is a wider set than the JDK's, so the set is spelled
     * out here rather than delegated to [String.trim].
     */
    fun strip(text: String): String {
        var start = 0
        var end = text.length
        while (start < end && text[start] in PY_WHITESPACE) start++
        while (end > start && text[end - 1] in PY_WHITESPACE) end--
        return text.substring(start, end)
    }

    /**
     * Collapse every run of whitespace in [text] into a single space.
     *
     * The equivalent of Python's `" ".join(text.split())`.
     */
    fun collapseWhitespace(text: String): String =
        PY_WHITESPACE_RE.split(text)
            .filter { it.isNotEmpty() }
            .joinToString(" ")

    /**
     * Split [text] on the line boundaries Python's `str.splitlines()` knows.
     *
     * `splitlines()` does not leave a trailing empty element behind, so a text
     * ending in a line break yields one fewer piece than a plain split would.
     * A carriage return and a newline are one boundary, not two: `splitlines()`
     * does not leave an empty line between them.
     */
    fun splitLines(text: String): List<String> {
        val lines = mutableListOf<String>()
        var start = 0
        var index = 0
        while (index < text.length) {
            val ch = text[index]
            when {
                ch == '\r' -> {
                    lines.add(text.substring(start, index))
                    index++
                    if (index < text.length && text[index] == '\n') index++
                    start = index
                }

                ch in LINE_BREAK_CHARS -> {
                    lines.add(text.substring(start, index))
                    index++
                    start = index
                }

                else -> index++
            }
        }
        if (start < text.length) lines.add(text.substring(start))
        return lines
    }

    private fun mergeClusters(
        clusters: List<List<Int>>,
        left: Int,
        right: Int,
    ): List<List<Int>> {
        val merged = clusters[left] + clusters[right]
        val remaining = mutableListOf<List<Int>>()
        for ((index, cluster) in clusters.withIndex()) {
            if (index != left && index != right) remaining.add(cluster)
        }
        remaining.add(merged)
        return remaining
    }

    private fun jaccard(left: Set<String>, right: Set<String>): Double {
        if (left.isEmpty() || right.isEmpty()) return 0.0
        val union = left + right
        if (union.isEmpty()) return 0.0
        return left.intersect(right).size.toDouble() / union.size.toDouble()
    }

    /**
     * Whether [text] holds a finding verb as a whole word.
     *
     * Python's `re` searches for the leftmost position at which the pattern
     * matches, so every start position is tried here.
     */
    private fun containsFindingVerb(text: String): Boolean {
        for (start in text.indices) {
            if (text[start] !in VERB_INITIALS) continue
            val match = FINDING_VERB_RE.matchAt(text, start) ?: continue
            val end = start + match.value.length
            if (!isWordCharBefore(text, start) && !isWordCharAfter(text, end)) return true
        }
        return false
    }

    /**
     * Python's `\w`: a character that is alphanumeric or an underscore.
     *
     * The rule is `lmeditor.core.CodePoints`'s, because a rule that ports a
     * Python regular expression has to have exactly one answer to what
     * Python's `\w` means.
     */
    private fun isPythonWordChar(codePoint: Int): Boolean =
        CodePoints.isPythonWordChar(codePoint)

    /** Whether the character before [index] is a word character. */
    private fun isWordCharBefore(text: String, index: Int): Boolean =
        index > 0 && isPythonWordChar(text.codePointBefore(index))

    /** Whether the character at [index] is a word character. */
    private fun isWordCharAfter(text: String, index: Int): Boolean =
        index < text.length && isPythonWordChar(text.codePointAt(index))

    /** Python's `str.isdigit()` for a token of the tokenizer. */
    private fun isDigits(token: String): Boolean =
        token.isNotEmpty() && token.all { it in '0'..'9' }

    /**
     * Python's `items[:limit]`.
     *
     * A negative limit counts from the end of the list, as Python's slice
     * does; Kotlin's `take` would refuse a negative count instead.
     */
    private fun <T> takePython(items: List<T>, limit: Int): List<T> {
        if (limit >= 0) return items.take(limit)
        val size = items.size + limit
        return if (size <= 0) emptyList() else items.subList(0, size)
    }

    /**
     * Python's `str.capitalize()`: the first character title-cased, the rest
     * lower-cased.
     *
     * Python title-cases the first character rather than upper-casing it, so
     * the digraph "ǳ" becomes "ǲ" rather than "Ǳ".
     */
    private fun capitalize(text: String): String {
        if (text.isEmpty()) return text
        val first = text.codePointAt(0)
        val head = String(Character.toChars(Character.toTitleCase(first)))
        return head + text.substring(Character.charCount(first)).lowercase()
    }

    /** The longest token treated as a word rather than as an identifier. */
    private const val MAX_WORD_LENGTH: Int = 24

    /** The lower-cased word pattern of the Python tokenizer. */
    private val TOKEN_RE: Regex = Regex("[a-z0-9][a-z0-9'\\-]*")

    /**
     * The character class of Python's Unicode whitespace.
     *
     * Python's `\s` in a `str` pattern matches every character for which
     * `str.isspace()` is true, which is wider than the JDK's `\s`.
     */
    private const val PY_SPACE_CLASS: String =
        "\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680\u2000-\u200A" +
            "\u2028\u2029\u202F\u205F\u3000"

    /**
     * The character class of Python's `.`: any character but a newline.
     *
     * Java's `.` is narrower: it also refuses a carriage return, a NEL and
     * the Unicode line separators, so a line that ends in one of those would
     * not be matched to its end.
     */
    private const val PY_DOT_CLASS: String = "[^\\n]"

    /** The line boundaries Python's `str.splitlines()` knows, without the CR. */
    private val LINE_BREAK_CHARS: Set<Char> = setOf(
        '\n', '\u000B', '\u000C', '\u001C', '\u001D', '\u001E', '\u0085', '\u2028', '\u2029',
    )

    /** The same set as a character set, for the string-level helpers. */
    private val PY_WHITESPACE: Set<Char> = buildSet {
        addAll('\u0009'..'\u000D')
        addAll('\u001C'..'\u001F')
        add('\u0020')
        add('\u0085')
        add('\u00A0')
        add('\u1680')
        addAll('\u2000'..'\u200A')
        add('\u2028')
        add('\u2029')
        add('\u202F')
        add('\u205F')
        add('\u3000')
    }

    /** The sentence boundary: terminal punctuation followed by a capital. */
    private val SENTENCE_SPLIT_RE: Regex =
        Regex("(?<=[.!?])[$PY_SPACE_CLASS]+(?=[A-Z0-9\"'\\[])")

    /** A run of whitespace, for [collapseWhitespace]. */
    private val PY_WHITESPACE_RE: Regex = Regex("[$PY_SPACE_CLASS]+")
}
