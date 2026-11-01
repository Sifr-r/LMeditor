package lmeditor.pdf

/**
 * The PDF object grammar: a hand-written parser for the syntax a PDF file is
 * written in.
 *
 * The parser covers the whole object language -- numbers, names, strings,
 * arrays, dictionaries, streams and indirect references -- and nothing else:
 * it reads bytes, it does not interpret them. Decoding a stream and
 * interpreting a content stream are the jobs of [PdfFilters] and [PdfText].
 *
 * The parser never throws for malformed input. A file that is not a PDF, or a
 * file whose grammar is broken, produces empty objects and a shorter parse;
 * deciding that the file is unusable is the job of [PdfDocument], which turns
 * that into a typed error.
 */
internal class PdfParser(
    private val bytes: ByteArray,
    start: Int = 0,
) {

    /** The current position of the parser inside [bytes]. */
    var pos: Int = start

    /** The buffer the parser reads from. */
    val buffer: ByteArray get() = bytes

    /** True when the parser has consumed the whole buffer. */
    val isAtEnd: Boolean get() = pos < 0 || pos >= bytes.size

    /** The byte at the current position, or `-1` at the end of the buffer. */
    fun peekByte(): Int = byteAt(pos)

    /** The byte [offset] positions ahead of the current one, or `-1` past the end. */
    fun peekByteAt(offset: Int): Int = byteAt(pos + offset)

    /**
     * The byte at [index], or `-1` when the index is outside the buffer.
     *
     * Every read of the buffer goes through here, so a position that points
     * before the start of the buffer reads as the end of it rather than as an
     * `ArrayIndexOutOfBoundsException`.
     */
    private fun byteAt(index: Int): Int =
        if (index >= 0 && index < bytes.size) bytes[index].toInt() and 0xff else -1

    /** Skip whitespace and comments. */
    fun skipWhitespace() {
        while (!isAtEnd) {
            val b = byteAt(pos)
            when {
                b == COMMENT -> while (!isAtEnd && !isEol(byteAt(pos))) pos++
                isWhitespace(b) -> pos++
                else -> return
            }
        }
    }

    /** True when the bytes at the current position are [keyword] followed by a delimiter. */
    fun atKeyword(keyword: String): Boolean {
        if (pos < 0 || pos + keyword.length > bytes.size) return false
        for (i in keyword.indices) {
            if (byteAt(pos + i) != keyword[i].code) return false
        }
        val after = pos + keyword.length
        val b = byteAt(after)
        if (b != -1 && !isWhitespace(b) && !isDelimiter(b)) return false
        return true
    }

    /** Read a bare keyword such as `obj`, `R` or `stream`. */
    fun readKeyword(): String {
        if (pos < 0) return ""
        val start = pos
        while (!isAtEnd) {
            val b = byteAt(pos)
            if (isWhitespace(b) || isDelimiter(b)) break
            pos++
        }
        if (pos <= start) return ""
        return String(bytes, start, pos - start, Charsets.ISO_8859_1)
    }

    /** Read a number, or `null` when the position does not hold one. */
    fun readNumber(): PdfNumber? {
        skipWhitespace()
        if (isAtEnd) return null
        val start = pos
        val sign = byteAt(pos)
        if (sign == PLUS || sign == MINUS) pos++
        var digits = 0
        while (!isAtEnd && isDigit(byteAt(pos))) {
            pos++
            digits++
        }
        if (!isAtEnd && byteAt(pos) == DOT) {
            pos++
            while (!isAtEnd && isDigit(byteAt(pos))) {
                pos++
                digits++
            }
        }
        if (digits == 0) {
            pos = start
            return null
        }
        val text = String(bytes, start, pos - start, Charsets.ISO_8859_1)
        return PdfNumber(text.toDoubleOrNull() ?: 0.0)
    }

    /** Read one value of the object language. */
    fun parseValue(): PdfObject {
        skipWhitespace()
        if (isAtEnd) return PdfNull
        return when (val b = peekByte()) {
            SLASH -> parseName()
            LPAREN -> parseLiteralString()
            LESS -> if (peekByteAt(1) == LESS) parseDictionary() else parseHexString()
            LBRACKET -> parseArray()
            RBRACKET -> PdfNull
            TRUE_T -> { readKeyword(); PdfBoolean(true) }
            FALSE_T -> { readKeyword(); PdfBoolean(false) }
            NULL_T -> { readKeyword(); PdfNull }
            else -> if (isNumberStart(b)) parseNumberOrReference() else PdfNull
        }
    }

    /**
     * Parse the indirect object that starts at [offset].
     *
     * Returns the object number and the object, or `null` when the position
     * does not hold an indirect object.
     */
    fun parseIndirectObjectAt(offset: Int): Pair<Int, PdfObject>? {
        pos = offset
        skipWhitespace()
        val number = readNumber() ?: return null
        skipWhitespace()
        val generation = readNumber() ?: return null
        skipWhitespace()
        if (!atKeyword("obj")) return null
        pos += 3
        val value = parseValue()
        return number.intValue to value
    }

    private fun parseNumberOrReference(): PdfObject {
        val first = readNumber() ?: return PdfNull
        if (!first.isInteger) return first
        val save = pos
        skipWhitespace()
        val second = readNumber()
        if (second != null && second.isInteger) {
            skipWhitespace()
            if (atKeyword("R")) {
                pos += 1
                return PdfReference(first.intValue, second.intValue)
            }
        }
        pos = save
        return first
    }

    private fun parseName(): PdfName {
        pos++
        val builder = StringBuilder()
        while (pos < bytes.size) {
            val b = bytes[pos].toInt() and 0xff
            if (b == HASH) {
                val hex = if (pos + 2 < bytes.size) String(bytes, pos + 1, 2, Charsets.ISO_8859_1) else null
                val value = hex?.toIntOrNull(16)
                if (value != null) {
                    builder.append(value.toChar())
                    pos += 3
                } else {
                    pos++
                }
                continue
            }
            if (b <= SPACE || isDelimiter(b)) break
            builder.append(b.toChar())
            pos++
        }
        return PdfName(builder.toString())
    }

    private fun parseLiteralString(): PdfString {
        pos++
        val out = java.io.ByteArrayOutputStream()
        var depth = 1
        while (pos < bytes.size) {
            val b = bytes[pos].toInt() and 0xff
            when {
                b == BACKSLASH -> {
                    pos++
                    if (pos >= bytes.size) break
                    when (val escape = bytes[pos].toInt() and 0xff) {
                        LF -> pos++
                        CR -> {
                            pos++
                            if (pos < bytes.size && bytes[pos].toInt() and 0xff == LF) pos++
                        }
                        in ZERO..SEVEN -> {
                            var value = 0
                            var count = 0
                            while (count < 3 && pos < bytes.size) {
                                val digit = bytes[pos].toInt() and 0xff
                                if (digit !in ZERO..SEVEN) break
                                value = value * 8 + (digit - ZERO)
                                pos++
                                count++
                            }
                            out.write(value and 0xff)
                        }
                        else -> {
                            out.write(escape)
                            pos++
                        }
                    }
                }
                b == LPAREN -> {
                    depth++
                    out.write(b)
                    pos++
                }
                b == RPAREN -> {
                    depth--
                    if (depth == 0) {
                        pos++
                        return PdfString(out.toByteArray())
                    }
                    out.write(b)
                    pos++
                }
                else -> {
                    out.write(b)
                    pos++
                }
            }
        }
        return PdfString(out.toByteArray())
    }

    private fun parseHexString(): PdfString {
        pos++
        val digits = StringBuilder()
        while (pos < bytes.size) {
            val b = bytes[pos].toInt() and 0xff
            pos++
            if (b == GREATER) break
            if (isHexDigit(b)) digits.append(b.toChar())
        }
        var text = digits.toString()
        if (text.length % 2 == 1) text += "0"
        val out = ByteArray(text.length / 2)
        for (i in out.indices) {
            out[i] = text.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
        return PdfString(out)
    }

    private fun parseArray(): PdfArray {
        pos++
        val items = ArrayList<PdfObject>()
        while (true) {
            skipWhitespace()
            if (isAtEnd) break
            if (peekByte() == RBRACKET) {
                pos++
                break
            }
            val before = pos
            items.add(parseValue())
            if (pos == before) pos++
        }
        return PdfArray(items)
    }

    private fun parseDictionary(): PdfObject {
        pos += 2
        val entries = LinkedHashMap<String, PdfObject>()
        while (true) {
            skipWhitespace()
            if (isAtEnd) break
            if (peekByte() == GREATER && peekByteAt(1) == GREATER) {
                pos += 2
                break
            }
            if (peekByte() != SLASH) {
                val before = pos
                parseValue()
                if (pos == before) pos++
                continue
            }
            val key = parseName().value
            entries[key] = parseValue()
        }
        val dictionary = PdfDictionary(entries)
        val save = pos
        skipWhitespace()
        if (atKeyword("stream")) {
            pos += 6
            if (pos < bytes.size && bytes[pos].toInt() and 0xff == CR) pos++
            if (pos < bytes.size && bytes[pos].toInt() and 0xff == LF) pos++
            return PdfStream(dictionary, readStreamData(dictionary))
        }
        pos = save
        return dictionary
    }

    /**
     * Read the data of a stream.
     *
     * The declared `/Length` is used when it agrees with the file; otherwise
     * the data ends at the `endstream` keyword, which is where a producer that
     * wrote a wrong length leaves it.
     */
    private fun readStreamData(dictionary: PdfDictionary): ByteArray {
        val declared = (dictionary["Length"] as? PdfNumber)?.value?.toInt()
        if (declared != null && declared >= 0 && pos + declared <= bytes.size) {
            val probe = PdfParser(bytes, pos + declared)
            probe.skipWhitespace()
            if (probe.atKeyword("endstream")) {
                val data = bytes.copyOfRange(pos, pos + declared)
                pos += declared
                return data
            }
        }
        val index = indexOfToken(bytes, "endstream", pos)
        if (index < 0) {
            val data = bytes.copyOfRange(pos, bytes.size)
            pos = bytes.size
            return data
        }
        var end = index
        if (end > pos && bytes[end - 1].toInt() and 0xff == LF) {
            end--
            if (end > pos && bytes[end - 1].toInt() and 0xff == CR) end--
        } else if (end > pos && bytes[end - 1].toInt() and 0xff == CR) {
            end--
        }
        val data = bytes.copyOfRange(pos, end)
        pos = index + ENDSTREAM.length
        return data
    }
}

/**
 * Index every indirect object of a PDF by scanning it.
 *
 * The scan is the fallback for a file whose cross-reference table is missing
 * or wrong: the objects are found by their `number generation obj` markers,
 * and a stream's data is skipped using its `/Length`, so the marker of an
 * object inside a content stream is never mistaken for an object.
 */
internal object PdfObjectScanner {

    fun scan(bytes: ByteArray): Map<Int, PdfObject> {
        val objects = LinkedHashMap<Int, PdfObject>()
        var position = 0
        while (position < bytes.size) {
            val start = indexOfIndirectObject(bytes, position)
            if (start < 0) break
            val parser = PdfParser(bytes, start)
            val parsed = parser.parseIndirectObjectAt(start)
            if (parsed == null) {
                position = start + 3
                continue
            }
            objects[parsed.first] = parsed.second
            position = maxOf(parser.pos, start + 3)
        }
        return objects
    }

    private fun indexOfIndirectObject(bytes: ByteArray, from: Int): Int {
        var index = from
        while (index + 2 < bytes.size) {
            if (bytes[index].toInt() == 'o'.code &&
                bytes[index + 1].toInt() == 'b'.code &&
                bytes[index + 2].toInt() == 'j'.code
            ) {
                val after = index + 3
                if (after >= bytes.size || isDelimiter(bytes[after].toInt() and 0xff) || isWhitespace(bytes[after].toInt() and 0xff)) {
                    val start = markerStart(bytes, index)
                    if (start >= 0) return start
                }
            }
            index++
        }
        return -1
    }

    /** The offset of the `number generation` that precedes the `obj` at [objIndex]. */
    private fun markerStart(bytes: ByteArray, objIndex: Int): Int {
        var cursor = objIndex - 1
        while (cursor >= 0 && isWhitespace(bytes[cursor].toInt() and 0xff)) cursor--
        val generationEnd = cursor + 1
        while (cursor >= 0 && isDigit(bytes[cursor].toInt() and 0xff)) cursor--
        val generationStart = cursor + 1
        if (generationStart == generationEnd) return -1
        while (cursor >= 0 && isWhitespace(bytes[cursor].toInt() and 0xff)) cursor--
        val numberEnd = cursor + 1
        while (cursor >= 0 && isDigit(bytes[cursor].toInt() and 0xff)) cursor--
        val numberStart = cursor + 1
        if (numberStart == numberEnd) return -1
        return numberStart
    }
}

/** The offset of [token] in [bytes] at or after [from], or `-1`. */
internal fun indexOfToken(bytes: ByteArray, token: String, from: Int): Int {
    val needle = token.toByteArray(Charsets.ISO_8859_1)
    if (needle.isEmpty() || bytes.size < needle.size) return -1
    outer@ for (index in maxOf(from, 0)..bytes.size - needle.size) {
        for (offset in needle.indices) {
            if (bytes[index + offset] != needle[offset]) continue@outer
        }
        return index
    }
    return -1
}

internal fun isWhitespace(b: Int): Boolean =
    b == 0 || b == 0x09 || b == 0x0a || b == 0x0c || b == 0x0d || b == 0x20

internal fun isDelimiter(b: Int): Boolean =
    b == LPAREN || b == RPAREN || b == LESS || b == GREATER || b == LBRACKET ||
        b == RBRACKET || b == LBRACE || b == RBRACE || b == SLASH || b == PERCENT

internal fun isEol(b: Int): Boolean = b == 0x0a || b == 0x0d

internal fun isDigit(b: Int): Boolean = b in ZERO..NINE

internal fun isHexDigit(b: Int): Boolean =
    b in ZERO..NINE || b in 'a'.code..'f'.code || b in 'A'.code..'F'.code

private fun isNumberStart(b: Int): Boolean =
    b == PLUS || b == MINUS || b == DOT || isDigit(b)

private const val SPACE = 0x20
private const val PERCENT = 0x25
private const val LPAREN = 0x28
private const val RPAREN = 0x29
private const val PLUS = 0x2b
private const val MINUS = 0x2d
private const val DOT = 0x2e
private const val ZERO = 0x30
private const val NINE = 0x39
private const val SLASH = 0x2f
private const val SEVEN = 0x37
private const val TRUE_T = 0x74
private const val FALSE_T = 0x66
private const val NULL_T = 0x6e
private const val LESS = 0x3c
private const val GREATER = 0x3e
private const val LBRACKET = 0x5b
private const val RBRACKET = 0x5d
private const val LBRACE = 0x7b
private const val RBRACE = 0x7d
private const val BACKSLASH = 0x5c
private const val HASH = 0x23
private const val COMMENT = 0x25
private const val CR = 0x0d
private const val LF = 0x0a
private const val ENDSTREAM = "endstream"
