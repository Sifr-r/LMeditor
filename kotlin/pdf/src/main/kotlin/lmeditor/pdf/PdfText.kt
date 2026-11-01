package lmeditor.pdf

import java.nio.file.Path
import kotlin.math.abs

/**
 * The text of a PDF.
 *
 * The text is read out of the content streams of the pages, through the text
 * operators a producer writes them with: `BT`/`ET` open and close a text
 * object, `Tj`, `TJ`, `'` and `"` show text, and `Tf`, `Td`, `TD`, `TL`,
 * `Tm` and `T*` say where the text sits. Where the layout puts the next run
 * on another line, a line break is written; where it leaves a gap wide enough
 * to be a word space, a space is written.
 *
 * The width of a run is taken from the font's own widths where the file has
 * them, and from the average width of a letter where it does not, so the
 * spacing of a document whose fonts carry no widths is an approximation. The
 * text itself is not an approximation: it is what the page draws.
 */
object PdfText {

    /** The separator the Python tier puts between the pages of a PDF. */
    const val PAGE_SEPARATOR: String = "\n\n"

    /** The text of every page of the PDF at [path], in page order. */
    fun pageTexts(path: Path): List<String> = pageTexts(PdfDocument.open(path))

    /** The text of every page of [bytes], in page order. */
    fun pageTexts(bytes: ByteArray): List<String> = pageTexts(PdfDocument.parse(bytes))

    /** The text of every page of [document], in page order. */
    fun pageTexts(document: PdfDocument): List<String> = document.pages.map { it.text }

    /** The whole text of the PDF at [path]. */
    fun text(path: Path): String = text(PdfDocument.open(path))

    /** The whole text of [bytes]. */
    fun text(bytes: ByteArray): String = text(PdfDocument.parse(bytes))

    /** The whole text of [document]: its pages joined by [PAGE_SEPARATOR]. */
    fun text(document: PdfDocument): String =
        document.pages.joinToString(PAGE_SEPARATOR) { it.text }.trim()
}

/** The width a glyph is given when the file carries no widths for it: half an em. */
private const val DEFAULT_GLYPH_WIDTH = 500.0

/** The code a simple font uses for a space. */
private const val SPACE_CODE = 32

/** The smallest vertical difference, in text-space units, that counts as a new line. */
private const val LINE_EPSILON = 0.5

/** The gap, in ems, that counts as a word space. */
private const val SPACE_GAP_EM = 0.2

/**
 * Extracts the text of a page from its content streams.
 */
internal object PageText {

    /** The text of the page described by [dictionary], with its issues reported into [issues]. */
    fun extract(
        resolver: PdfObjectResolver,
        dictionary: PdfDictionary,
        resources: PdfDictionary?,
        issues: MutableList<PdfIssue>,
    ): String {
        val content = contentBytes(resolver, dictionary, issues) ?: return ""
        val machine = TextMachine(resolver, resources, issues, HashSet())
        return machine.run(content)
    }

    private fun contentBytes(
        resolver: PdfObjectResolver,
        dictionary: PdfDictionary,
        issues: MutableList<PdfIssue>,
    ): ByteArray? {
        val contents = resolver.resolve(dictionary.get("Contents")) ?: return null
        if (contents is PdfStream) return decoded(contents, issues)
        if (contents !is PdfArray) return null
        val out = java.io.ByteArrayOutputStream()
        for (item in contents.items) {
            val stream = resolver.resolve(item) as? PdfStream ?: continue
            val data = decoded(stream, issues) ?: continue
            out.write(data)
            out.write('\n'.code)
        }
        return out.toByteArray()
    }

    private fun decoded(stream: PdfStream, issues: MutableList<PdfIssue>): ByteArray? =
        when (val outcome = PdfFilters.decode(stream)) {
            is PdfFilters.Outcome.Decoded -> outcome.data
            is PdfFilters.Outcome.Failed -> {
                issues.add(PdfIssue(PdfIssueKind.UNSUPPORTED_FILTER, outcome.reason))
                null
            }
        }
}

/**
 * The operators of a content stream, in the order they appear.
 *
 * A content stream is a sequence of operands followed by an operator, so the
 * reader walks the stream collecting values until it meets a keyword.
 */
internal object PdfContent {

    /** The operands and the operator of every operation in [content]. */
    fun operations(content: ByteArray): List<Pair<List<PdfObject>, String>> {
        val parser = PdfParser(content)
        val result = ArrayList<Pair<List<PdfObject>, String>>()
        var operands = ArrayList<PdfObject>()
        while (true) {
            parser.skipWhitespace()
            if (parser.isAtEnd) break
            val b = parser.peekByte()
            if (isLetter(b) || b == QUOTE || b == DOUBLE_QUOTE) {
                val operator = parser.readKeyword()
                if (operator == "BI") {
                    skipInlineImage(parser)
                    operands = ArrayList()
                    continue
                }
                result.add(operands to operator)
                operands = ArrayList()
            } else {
                val before = parser.pos
                operands.add(parser.parseValue())
                if (parser.pos == before) parser.pos++
            }
        }
        return result
    }

    /** Skip the data of an inline image, which is not text and may look like it. */
    private fun skipInlineImage(parser: PdfParser) {
        val index = indexOfToken(parser.buffer, "EI", parser.pos)
        parser.pos = if (index < 0) parser.buffer.size else index + 2
    }
}

/**
 * Interprets the text operators of a content stream.
 *
 * The machine keeps the pen where the last run of text ended, so that the
 * next run can be compared with it: a different height means a new line, a
 * wide gap means a word space.
 */
internal class TextMachine(
    private val resolver: PdfObjectResolver,
    private val resources: PdfDictionary?,
    private val issues: MutableList<PdfIssue>,
    private val visitedForms: MutableSet<Int>,
) {

    private val out = StringBuilder()
    private val fonts = LinkedHashMap<String, PdfFont>()
    private var font: PdfFont? = null
    private var fontSize = 0.0
    private var charSpacing = 0.0
    private var wordSpacing = 0.0
    private var horizontalScale = 1.0
    private var leading = 0.0
    private var penX = 0.0
    private var penY = 0.0
    private var lineX = 0.0
    private var lineY = 0.0
    private var hasRun = false
    private var lastRunY = 0.0
    private var lastRunEndX = 0.0

    /** The text the content stream draws. */
    fun run(content: ByteArray): String {
        fonts.putAll(PdfFonts.build(resources, resolver, issues))
        for ((operands, operator) in PdfContent.operations(content)) {
            execute(operands, operator)
        }
        return normalize(out.toString())
    }

    private fun execute(operands: List<PdfObject>, operator: String) {
        when (operator) {
            "BT" -> {
                penX = 0.0
                penY = 0.0
                lineX = 0.0
                lineY = 0.0
            }
            "Tf" -> {
                val name = (operands.getOrNull(0) as? PdfName)?.value
                font = name?.let { fonts[it] }
                fontSize = numberAt(operands, 1)
            }
            "Td" -> moveTo(numberAt(operands, 0), numberAt(operands, 1))
            "TD" -> {
                leading = -numberAt(operands, 1)
                moveTo(numberAt(operands, 0), numberAt(operands, 1))
            }
            "Tm" -> {
                val x = numberAt(operands, 4)
                val y = numberAt(operands, 5)
                lineX = x
                lineY = y
                penX = x
                penY = y
            }
            "T*" -> moveTo(0.0, -leading)
            "TL" -> leading = numberAt(operands, 0)
            "Tc" -> charSpacing = numberAt(operands, 0)
            "Tw" -> wordSpacing = numberAt(operands, 0)
            "Tz" -> horizontalScale = numberAt(operands, 0) / 100.0
            "Tj" -> (operands.getOrNull(0) as? PdfString)?.let { show(it.bytes) }
            "TJ" -> {
                val array = operands.getOrNull(0) as? PdfArray ?: return
                for (item in array.items) {
                    when (item) {
                        is PdfString -> show(item.bytes)
                        is PdfNumber -> penX -= item.value / 1000.0 * fontSize * horizontalScale
                        else -> Unit
                    }
                }
            }
            "'" -> {
                moveTo(0.0, -leading)
                (operands.getOrNull(0) as? PdfString)?.let { show(it.bytes) }
            }
            "\"" -> {
                wordSpacing = numberAt(operands, 0)
                charSpacing = numberAt(operands, 1)
                moveTo(0.0, -leading)
                (operands.getOrNull(2) as? PdfString)?.let { show(it.bytes) }
            }
            "Do" -> runForm(operands)
            else -> Unit
        }
    }

    private fun moveTo(dx: Double, dy: Double) {
        lineX += dx
        lineY += dy
        penX = lineX
        penY = lineY
    }

    private fun show(bytes: ByteArray) {
        val active = font ?: return
        val glyphs = active.decoder.decode(bytes)
        val builder = StringBuilder()
        var advance = 0.0
        for (glyph in glyphs) {
            builder.append(glyph.text)
            var width = glyph.width / 1000.0 * fontSize * horizontalScale
            width += charSpacing * horizontalScale
            if (glyph.code == SPACE_CODE && !active.isComposite) width += wordSpacing * horizontalScale
            advance += width
        }
        val text = builder.toString()
        if (text.isNotEmpty()) {
            appendRun(text, penX, penY, penX + advance)
        }
        penX += advance
    }

    private fun appendRun(text: String, startX: Double, y: Double, endX: Double) {
        if (hasRun) {
            if (abs(y - lastRunY) > LINE_EPSILON) {
                out.append('\n')
            } else if (startX - lastRunEndX > SPACE_GAP_EM * fontSize) {
                out.append(' ')
            }
        }
        out.append(text)
        hasRun = true
        lastRunY = y
        lastRunEndX = endX
    }

    /** Draw the text of a form XObject, which a page may hold its text in. */
    private fun runForm(operands: List<PdfObject>) {
        val name = (operands.getOrNull(0) as? PdfName)?.value ?: return
        val value = resources?.dictionary("XObject")?.get(name) ?: return
        val stream = resolver.resolve(value) as? PdfStream ?: return
        if (stream.dictionary.name("Subtype") != "Form") return
        val identity = (value as? PdfReference)?.number ?: stream.hashCode()
        if (!visitedForms.add(identity)) return
        val data = when (val outcome = PdfFilters.decode(stream)) {
            is PdfFilters.Outcome.Decoded -> outcome.data
            is PdfFilters.Outcome.Failed -> {
                issues.add(PdfIssue(PdfIssueKind.UNSUPPORTED_FILTER, outcome.reason))
                return
            }
        }
        val formResources = stream.dictionary.dictionary("Resources") ?: resources
        val machine = TextMachine(resolver, formResources, issues, visitedForms)
        out.append(machine.run(data))
    }

    private fun numberAt(operands: List<PdfObject>, index: Int): Double =
        (operands.getOrNull(index) as? PdfNumber)?.value ?: 0.0
}

/** Trim the trailing space of every line and collapse the blank lines between paragraphs. */
private fun normalize(text: String): String {
    val trimmed = text.split('\n').joinToString("\n") { it.trimEnd() }
    return trimmed.replace(Regex("\n{3,}"), "\n\n").trim()
}

/**
 * A font a page draws with.
 *
 * @property decoder turns the bytes of a text string into glyphs.
 * @property isComposite true for a Type0 font, whose codes are two bytes wide.
 */
internal class PdfFont(
    val decoder: PdfFontDecoder,
    val isComposite: Boolean,
)

/** One glyph: the text it stands for and its advance in 1/1000 em. */
internal class PdfGlyph(val text: String, val code: Int, val width: Double)

/** Turns the bytes of a text string into the glyphs they stand for. */
internal interface PdfFontDecoder {

    /** The glyphs the bytes stand for. */
    fun decode(bytes: ByteArray): List<PdfGlyph>

    /** The width of a code in 1/1000 em. */
    fun width(code: Int): Double
}

/** A font whose codes are one byte wide: Type1, TrueType and Type3 fonts. */
internal class SimpleFontDecoder(
    private val base: PdfBaseEncoding,
    private val differences: Map<Int, String>,
    private val widths: Map<Int, Double>,
    private val toUnicode: Map<Int, String>,
) : PdfFontDecoder {

    override fun decode(bytes: ByteArray): List<PdfGlyph> = bytes.map { byte ->
        val code = byte.toInt() and 0xff
        val text = toUnicode[code]
            ?: differences[code]?.let { glyphNameToChar(it)?.toString() }
            ?: base.decode(code)?.toString()
            ?: ""
        PdfGlyph(text, code, width(code))
    }

    override fun width(code: Int): Double = widths[code] ?: DEFAULT_GLYPH_WIDTH
}

/** A font whose codes are two bytes wide: a Type0 font with a CMap encoding. */
internal class CompositeFontDecoder(
    private val toUnicode: Map<Int, String>,
    private val widths: Map<Int, Double>,
    private val defaultWidth: Double,
) : PdfFontDecoder {

    override fun decode(bytes: ByteArray): List<PdfGlyph> {
        val glyphs = ArrayList<PdfGlyph>()
        var index = 0
        while (index + 1 < bytes.size) {
            val code = ((bytes[index].toInt() and 0xff) shl 8) or (bytes[index + 1].toInt() and 0xff)
            index += 2
            glyphs.add(PdfGlyph(toUnicode[code] ?: "", code, width(code)))
        }
        return glyphs
    }

    override fun width(code: Int): Double = widths[code] ?: defaultWidth
}

/** Reads the fonts a page draws with out of its resources. */
internal object PdfFonts {

    /** The fonts of [resources], keyed by the name the content stream uses them under. */
    fun build(
        resources: PdfDictionary?,
        resolver: PdfObjectResolver,
        issues: MutableList<PdfIssue>,
    ): Map<String, PdfFont> {
        val fonts = resources?.dictionary("Font") ?: return emptyMap()
        val result = LinkedHashMap<String, PdfFont>()
        for ((name, value) in fonts.entries) {
            val dictionary = resolver.dictionary(value) ?: continue
            result[name] = build(dictionary, resolver, issues)
        }
        return result
    }

    private fun build(
        dictionary: PdfDictionary,
        resolver: PdfObjectResolver,
        issues: MutableList<PdfIssue>,
    ): PdfFont {
        val toUnicode = readToUnicode(dictionary, resolver)
        if (dictionary.name("Subtype") == "Type0") {
            val descendant = descendantFont(dictionary, resolver)
            val defaultWidth = descendant?.number("DW") ?: 1000.0
            if (toUnicode == null) {
                issues.add(
                    PdfIssue(
                        PdfIssueKind.UNDECODABLE_FONT,
                        "a composite font carries no /ToUnicode CMap; the text it draws is not read",
                    ),
                )
            }
            return PdfFont(
                CompositeFontDecoder(toUnicode ?: emptyMap(), readCidWidths(descendant), defaultWidth),
                true,
            )
        }
        var base = PdfBaseEncoding.STANDARD
        val differences = LinkedHashMap<Int, String>()
        when (val encoding = dictionary["Encoding"]) {
            is PdfName -> base = baseEncodingFor(encoding.value)
            is PdfDictionary -> {
                val baseName = encoding.name("BaseEncoding")
                if (baseName != null) base = baseEncodingFor(baseName)
                readDifferences(encoding.array("Differences"), differences)
            }
            else -> Unit
        }
        return PdfFont(
            SimpleFontDecoder(base, differences, readWidths(dictionary), toUnicode ?: emptyMap()),
            false,
        )
    }

    private fun readToUnicode(dictionary: PdfDictionary, resolver: PdfObjectResolver): Map<Int, String>? {
        val stream = resolver.resolve(dictionary.get("ToUnicode")) as? PdfStream ?: return null
        val data = when (val outcome = PdfFilters.decode(stream)) {
            is PdfFilters.Outcome.Decoded -> outcome.data
            is PdfFilters.Outcome.Failed -> return null
        }
        return PdfCMap.parse(data)
    }

    private fun descendantFont(dictionary: PdfDictionary, resolver: PdfObjectResolver): PdfDictionary? {
        val descendants = dictionary.array("DescendantFonts") ?: return null
        val first = descendants.items.firstOrNull() ?: return null
        return resolver.dictionary(first)
    }

    private fun readWidths(dictionary: PdfDictionary): Map<Int, Double> {
        val widths = dictionary.array("Widths") ?: return emptyMap()
        val first = dictionary.number("FirstChar")?.toInt() ?: 0
        val result = LinkedHashMap<Int, Double>()
        widths.items.forEachIndexed { index, item ->
            val width = (item as? PdfNumber)?.value ?: return@forEachIndexed
            result[first + index] = width
        }
        return result
    }

    private fun readCidWidths(descendant: PdfDictionary?): Map<Int, Double> {
        val array = descendant?.array("W") ?: return emptyMap()
        val items = array.items
        val result = LinkedHashMap<Int, Double>()
        var index = 0
        while (index < items.size) {
            val first = (items[index] as? PdfNumber)?.intValue ?: break
            index++
            if (index >= items.size) break
            val next = items[index]
            if (next is PdfArray) {
                index++
                next.items.forEachIndexed { offset, item ->
                    val width = (item as? PdfNumber)?.value ?: return@forEachIndexed
                    result[first + offset] = width
                }
            } else {
                val last = (next as? PdfNumber)?.intValue ?: break
                index++
                val width = if (index < items.size) (items[index] as? PdfNumber)?.value else null
                index++
                if (width != null) {
                    for (code in first..last) result[code] = width
                }
            }
        }
        return result
    }

    private fun readDifferences(array: PdfArray?, into: MutableMap<Int, String>) {
        if (array == null) return
        var code = 0
        for (item in array.items) {
            when (item) {
                is PdfNumber -> code = item.intValue
                is PdfName -> {
                    into[code] = item.value
                    code++
                }
                else -> Unit
            }
        }
    }
}

private const val QUOTE = 0x27
private const val DOUBLE_QUOTE = 0x22

private fun isLetter(b: Int): Boolean = b in 'a'.code..'z'.code || b in 'A'.code..'Z'.code
