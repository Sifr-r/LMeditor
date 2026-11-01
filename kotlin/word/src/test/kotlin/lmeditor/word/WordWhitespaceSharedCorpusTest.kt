package lmeditor.word

import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * The whitespace and format rules, pinned against the Python tier.
 *
 * The Python tier carries these rules in `lmeditor.word.text` and
 * `lmeditor.word.formats`; this tier carries them in [WordText] and
 * [WordFormats]. Two implementations of one rule are worth nothing unless they
 * are *shown* to agree, and a door between them is not an option: the rules are
 * consulted on every block of every document, so a seam round trip per block
 * would cost more than the rules are worth.
 *
 * The shared corpus is what holds the two together. It is one file -- the test
 * resources' `fixtures/word_whitespace_corpus.tsv` -- whose expected values
 * are Python's answers, and the Python test reads the very same file. When the
 * two tiers disagree, one of the tests below fails: that is the drift detector.
 */
class WordWhitespaceSharedCorpusTest {

    private val corpus: Corpus by lazy { readCorpus() }

    // ------------------------------------------------------------------
    // The shared rules
    // ------------------------------------------------------------------

    @Test
    fun theWhitespaceRuleAgreesWithThePythonTier() {
        val disagreements = mutableListOf<String>()
        for (record in corpus.normalize) {
            val actual = WordText.normalizeWhitespace(record.input)
            if (actual != record.expected) {
                disagreements.add(
                    "normalize ${record.name}: python='${record.expected}' kotlin='$actual'",
                )
            }
        }
        assertEquals(
            emptyList(),
            disagreements,
            "the Kotlin tier must normalise exactly what the Python tier normalises",
        )
    }

    @Test
    fun theJoinRuleAgreesWithThePythonTier() {
        val disagreements = mutableListOf<String>()
        for (record in corpus.join) {
            val actual = WordText.joinTexts(record.parts, record.separator)
            if (actual != record.expected) {
                disagreements.add(
                    "join ${record.name}: python='${record.expected}' kotlin='$actual'",
                )
            }
        }
        assertEquals(
            emptyList(),
            disagreements,
            "the Kotlin tier must join exactly what the Python tier joins",
        )
    }

    @Test
    fun theStrippingRuleAgreesWithThePythonTier() {
        val disagreements = mutableListOf<String>()
        for (record in corpus.strip) {
            val actual = WordText.stripControlCharacters(record.input)
            if (actual != record.expected) {
                disagreements.add(
                    "strip ${record.name}: python='${record.expected}' kotlin='$actual'",
                )
            }
        }
        assertEquals(
            emptyList(),
            disagreements,
            "the Kotlin tier must strip exactly what the Python tier strips",
        )
    }

    @Test
    fun theFormatDetectionAgreesWithThePythonTier() {
        val disagreements = mutableListOf<String>()
        for (record in corpus.formats) {
            val legacy = WordFormats.isLegacyDoc(record.data)
            val zip = WordFormats.isZipPackage(record.data)
            val description = WordFormats.describeUnsupportedFormat(record.data)
            if (legacy != record.isLegacyDoc || zip != record.isZipPackage ||
                description != record.description
            ) {
                disagreements.add(
                    "format ${record.name}: python=" +
                        "(${record.isLegacyDoc}, ${record.isZipPackage}, '${record.description}') " +
                        "kotlin=($legacy, $zip, '$description')",
                )
            }
        }
        assertEquals(
            emptyList(),
            disagreements,
            "the Kotlin tier must recognise exactly the formats the Python tier recognises",
        )
    }

    // ------------------------------------------------------------------
    // The reader, end to end
    // ------------------------------------------------------------------

    @Test
    fun theReaderAgreesWithThePythonTierOnTheNextLineControl() {
        // The text the document carries, and the answer the corpus -- Python's
        // answer -- holds for it.
        val raw = "abc\u0085def"
        val expected = corpus.normalize.first { it.input == raw }.expected
        val body = "<w:p><w:r><w:t>$raw</w:t></w:r></w:p>"
        val document = WordReader().readBytes(WordFixtures.buildPackage(body))
        val block = document.blocks.first()
        assertEquals(
            expected,
            block.text,
            "the reader must normalise the next-line control the way the Python tier does",
        )
        assertEquals(
            raw,
            block.runs.first().text,
            "the run keeps the text the document carried, control and all",
        )
    }
}

// ---------------------------------------------------------------------------
// The corpus
// ---------------------------------------------------------------------------

private class Corpus(
    val normalize: List<NormalizeRecord>,
    val join: List<JoinRecord>,
    val strip: List<StripRecord>,
    val formats: List<FormatRecord>,
)

private class NormalizeRecord(
    val name: String,
    val input: String,
    val expected: String,
)

private class JoinRecord(
    val name: String,
    val separator: String,
    val parts: List<String>,
    val expected: String,
)

private class StripRecord(
    val name: String,
    val input: String,
    val expected: String,
)

private class FormatRecord(
    val name: String,
    val data: ByteArray,
    val isLegacyDoc: Boolean,
    val isZipPackage: Boolean,
    val description: String,
)

/**
 * Read the shared corpus from the test resources.
 *
 * The format is the one the corpus header describes: one record per line,
 * tab-separated, the first field naming the kind of record. A text field is
 * ASCII, with the characters that are not printable written as escapes.
 */
private fun readCorpus(): Corpus {
    val stream = WordWhitespaceSharedCorpusTest::class.java.classLoader
        .getResourceAsStream("fixtures/word_whitespace_corpus.tsv")
        ?: error("the shared corpus is missing from the test resources")
    val text = stream.use { it.readBytes().toString(Charsets.UTF_8) }
    val normalize = mutableListOf<NormalizeRecord>()
    val join = mutableListOf<JoinRecord>()
    val strip = mutableListOf<StripRecord>()
    val formats = mutableListOf<FormatRecord>()
    for (line in text.lineSequence()) {
        if (line.isBlank() || line.startsWith("#")) continue
        val fields = line.split('\t')
        when (fields[0]) {
            "normalize" -> normalize.add(
                NormalizeRecord(
                    name = fields[1],
                    input = decodeField(fields[2]),
                    expected = decodeField(fields[3]),
                ),
            )

            "join" -> join.add(
                JoinRecord(
                    name = fields[1],
                    separator = decodeField(fields[2]),
                    parts = splitFragments(fields[3]),
                    expected = decodeField(fields[4]),
                ),
            )

            "strip" -> strip.add(
                StripRecord(
                    name = fields[1],
                    input = decodeField(fields[2]),
                    expected = decodeField(fields[3]),
                ),
            )

            "format" -> formats.add(
                FormatRecord(
                    name = fields[1],
                    data = decodeHex(fields[2]),
                    isLegacyDoc = fields[3] == "true",
                    isZipPackage = fields[4] == "true",
                    description = decodeField(fields[5]),
                ),
            )

            else -> error("unknown kind of corpus record: ${fields[0]}")
        }
    }
    return Corpus(
        normalize = normalize,
        join = join,
        strip = strip,
        formats = formats,
    )
}

/** Read the text a corpus field holds. */
private fun decodeField(field: String): String {
    val out = StringBuilder(field.length)
    var index = 0
    while (index < field.length) {
        val char = field[index]
        if (char != '\\') {
            out.append(char)
            index++
            continue
        }
        index = decodeEscape(field, index, out)
    }
    return out.toString()
}

/**
 * Read the fragments a `join` field holds.
 *
 * The fragments are separated by a pipe; a pipe that belongs to a fragment is
 * written `\|` and is not a separator.
 */
private fun splitFragments(field: String): List<String> {
    val fragments = mutableListOf<String>()
    val current = StringBuilder()
    var index = 0
    while (index < field.length) {
        val char = field[index]
        if (char == '\\') {
            index = decodeEscape(field, index, current)
            continue
        }
        if (char == '|') {
            fragments.add(current.toString())
            current.setLength(0)
            index++
            continue
        }
        current.append(char)
        index++
    }
    fragments.add(current.toString())
    return fragments
}

/** Decode the escape that starts at [index], and return the index after it. */
private fun decodeEscape(field: String, index: Int, out: StringBuilder): Int {
    require(index + 1 < field.length) {
        "the corpus field ends with an unfinished escape: $field"
    }
    return when (val marker = field[index + 1]) {
        '\\' -> {
            out.append('\\')
            index + 2
        }

        '|' -> {
            out.append('|')
            index + 2
        }

        't' -> {
            out.append('\t')
            index + 2
        }

        'n' -> {
            out.append('\n')
            index + 2
        }

        'r' -> {
            out.append('\r')
            index + 2
        }

        'u' -> {
            out.append(field.substring(index + 2, index + 6).toInt(16).toChar())
            index + 6
        }

        'U' -> {
            val codePoint = field.substring(index + 2, index + 10).toInt(16)
            out.append(Character.toChars(codePoint))
            index + 10
        }

        else -> error("unknown escape in the corpus: \\$marker")
    }
}

/** Read the bytes a `format` field holds, written as hexadecimal. */
private fun decodeHex(hex: String): ByteArray {
    val out = ByteArray(hex.length / 2)
    for (index in out.indices) {
        val high = hex[index * 2].digitToInt(16)
        val low = hex[index * 2 + 1].digitToInt(16)
        out[index] = ((high shl 4) or low).toByte()
    }
    return out
}
