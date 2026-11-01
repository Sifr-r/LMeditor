package lmeditor.pdf

/**
 * The encodings a PDF uses to turn bytes into characters.
 *
 * Three of them matter for the text this module extracts: the *base encoding*
 * of a simple font (WinAnsi, MacRoman or Standard), the `/Differences` table
 * that overrides single codes of it, and the `/ToUnicode` CMap that maps a
 * subsetted font's codes to Unicode.
 */
internal fun decodeTextString(bytes: ByteArray): String {
    if (bytes.size >= 2) {
        val first = bytes[0].toInt() and 0xff
        val second = bytes[1].toInt() and 0xff
        if (first == 0xfe && second == 0xff) return String(bytes, 2, bytes.size - 2, Charsets.UTF_16BE)
        if (first == 0xff && second == 0xfe) return String(bytes, 2, bytes.size - 2, Charsets.UTF_16LE)
    }
    return String(bytes, Charsets.ISO_8859_1)
}

/** The base encoding of a simple font. */
internal enum class PdfBaseEncoding {
    WIN_ANSI,
    MAC_ROMAN,
    STANDARD,
    ;

    /** The character the code stands for, or `null` when the code is unused. */
    fun decode(code: Int): Char? = when (this) {
        WIN_ANSI -> decodeWinAnsi(code)
        MAC_ROMAN -> decodeMacRoman(code)
        STANDARD -> decodeStandard(code)
    }
}

/** The base encoding a PDF names, defaulting to Standard. */
internal fun baseEncodingFor(name: String): PdfBaseEncoding = when (name) {
    "WinAnsiEncoding" -> PdfBaseEncoding.WIN_ANSI
    "MacRomanEncoding" -> PdfBaseEncoding.MAC_ROMAN
    else -> PdfBaseEncoding.STANDARD
}

/** WinAnsi is CP1252: Latin-1 with thirty-two characters in the 0x80-0x9F range. */
private val WIN_ANSI_SPECIALS = intArrayOf(
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
    0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
    0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
)

private fun decodeWinAnsi(code: Int): Char? = when {
    code in 0x20..0x7e -> code.toChar()
    code in 0x80..0x9f -> WIN_ANSI_SPECIALS[code - 0x80].toChar()
    code in 0xa0..0xff -> code.toChar()
    else -> null
}

/**
 * MacRoman is approximated by Latin-1.
 *
 * The two agree on the whole ASCII range and on the accented letters that
 * matter for a paper's text; they differ in a handful of symbols in the
 * 0xa0-0xbf range, which this parser does not attempt to reproduce.
 */
private fun decodeMacRoman(code: Int): Char? = when {
    code in 0x20..0x7e -> code.toChar()
    code in 0xa0..0xff -> code.toChar()
    else -> null
}

/**
 * Standard encoding is Latin-1 with the two quote marks swapped.
 *
 * Standard encoding puts a right single quote at 0x27 and a left single quote
 * at 0x60, where Latin-1 has the straight apostrophe and the grave accent.
 */
private fun decodeStandard(code: Int): Char? = when (code) {
    0x27 -> '’'
    0x60 -> '‘'
    in 0x20..0x7e -> code.toChar()
    in 0xa1..0xff -> code.toChar()
    else -> null
}

/** The Unicode character a glyph name stands for, or `null` when it is unknown. */
internal fun glyphNameToChar(name: String): Char? {
    if (name.length == 1) return name[0]
    return GLYPH_NAMES[name]
}

/**
 * The glyph names this parser knows.
 *
 * The list covers the ASCII names, the common typographic names and the
 * Latin-1 accented letters. A name outside the list is not decoded: the code
 * it stands for falls back to the base encoding, which is what a producer
 * that used a name outside the Adobe Glyph List would have meant anyway.
 */
private val GLYPH_NAMES: Map<String, Char> = buildMap {
    put("space", ' ')
    put("exclam", '!')
    put("quotedbl", '"')
    put("numbersign", '#')
    put("dollar", '$')
    put("percent", '%')
    put("ampersand", '&')
    put("quotesingle", '\'')
    put("parenleft", '(')
    put("parenright", ')')
    put("asterisk", '*')
    put("plus", '+')
    put("comma", ',')
    put("hyphen", '-')
    put("period", '.')
    put("slash", '/')
    put("zero", '0')
    put("one", '1')
    put("two", '2')
    put("three", '3')
    put("four", '4')
    put("five", '5')
    put("six", '6')
    put("seven", '7')
    put("eight", '8')
    put("nine", '9')
    put("colon", ':')
    put("semicolon", ';')
    put("less", '<')
    put("equal", '=')
    put("greater", '>')
    put("question", '?')
    put("at", '@')
    put("bracketleft", '[')
    put("backslash", '\\')
    put("bracketright", ']')
    put("asciicircum", '^')
    put("underscore", '_')
    put("grave", '`')
    put("braceleft", '{')
    put("bar", '|')
    put("braceright", '}')
    put("asciitilde", '~')
    put("quoteleft", '‘')
    put("quoteright", '’')
    put("quotedblleft", '“')
    put("quotedblright", '”')
    put("endash", '–')
    put("emdash", '—')
    put("bullet", '•')
    put("ellipsis", '…')
    put("dagger", '†')
    put("daggerdbl", '‡')
    put("perthousand", '‰')
    put("fi", 'ﬁ')
    put("fl", 'ﬂ')
    put("Euro", '€')
    put("euro", '€')
    put("sterling", '£')
    put("degree", '°')
    put("trademark", '™')
    put("copyright", '©')
    put("registered", '®')
    put("section", '§')
    put("paragraph", '¶')
    put("middot", '·')
    put("minus", '−')
    put("plusminus", '±')
    put("mu", 'µ')
    put("onehalf", '½')
    put("onequarter", '¼')
    put("threequarters", '¾')
    put("multiply", '×')
    put("divide", '÷')
    put("lessequal", '≤')
    put("greaterequal", '≥')
    put("notequal", '≠')
    put("infinity", '∞')
    put("partialdiff", '∂')
    put("summation", '∑')
    put("product", '∏')
    put("radical", '√')
    put("integral", '∫')
    put("approxequal", '≈')
    put("logicalnot", '¬')
    put("brokenbar", '¦')
    put("exclamdown", '¡')
    put("questiondown", '¿')
    put("cent", '¢')
    put("currency", '¤')
    put("yen", '¥')
    put("florin", 'ƒ')
    put("germandbls", 'ß')
    put("ae", 'æ')
    put("oe", 'œ')
    put("AE", 'Æ')
    put("OE", 'Œ')
    put("ordfeminine", 'ª')
    put("ordmasculine", 'º')
    put("guilsinglleft", '‹')
    put("guilsinglright", '›')
    put("guillemotleft", '«')
    put("guillemotright", '»')
    put("nbspace", ' ')
    put("adieresis", 'ä')
    put("odieresis", 'ö')
    put("udieresis", 'ü')
    put("Adieresis", 'Ä')
    put("Odieresis", 'Ö')
    put("Udieresis", 'Ü')
    put("eacute", 'é')
    put("egrave", 'è')
    put("agrave", 'à')
    put("aacute", 'á')
    put("acircumflex", 'â')
    put("atilde", 'ã')
    put("aring", 'å')
    put("ccedilla", 'ç')
    put("iacute", 'í')
    put("igrave", 'ì')
    put("icircumflex", 'î')
    put("ntilde", 'ñ')
    put("oacute", 'ó')
    put("ograve", 'ò')
    put("ocircumflex", 'ô')
    put("otilde", 'õ')
    put("uacute", 'ú')
    put("ugrave", 'ù')
    put("ucircumflex", 'û')
    put("yacute", 'ý')
    put("ydieresis", 'ÿ')
    put("Ydieresis", 'Ÿ')
    put("zcaron", 'ž')
    put("Zcaron", 'Ž')
    put("scaron", 'š')
    put("Scaron", 'Š')
    put("ccaron", 'č')
    put("Ccaron", 'Č')
    put("rcaron", 'ř')
    put("eogonek", 'ę')
    put("Eogonek", 'Ę')
    put("aogonek", 'ą')
    put("Aogonek", 'Ą')
    put("scedilla", 'ş')
    put("Scedilla", 'Ş')
    put("gbreve", 'ğ')
    put("Gbreve", 'Ğ')
    put("abreve", 'ă')
    put("Abreve", 'Ă')
    put("dotlessi", 'ı')
    put("Lslash", 'Ł')
    put("lslash", 'ł')
    put("Eth", 'Ð')
    put("eth", 'ð')
    put("Thorn", 'Þ')
    put("thorn", 'þ')
}

/**
 * The `/ToUnicode` CMap of a font.
 *
 * A CMap is a PostScript-like program that maps a font's codes to Unicode.
 * Only the two tables that matter are read: `bfchar`, which maps single codes,
 * and `bfrange`, which maps a range of codes to a destination or to a list of
 * destinations.
 */
internal object PdfCMap {

    /** The code-to-text table a CMap declares. */
    fun parse(data: ByteArray): Map<Int, String> {
        val text = String(data, Charsets.ISO_8859_1)
        val result = LinkedHashMap<Int, String>()
        for (section in Regex("beginbfchar(.*?)endbfchar", RegexOption.DOT_MATCHES_ALL).findAll(text)) {
            val tokens = HEX_TOKEN.findAll(section.groupValues[1]).map { it.groupValues[1] }.toList()
            var index = 0
            while (index + 1 < tokens.size) {
                result[codeOf(tokens[index])] = textOf(tokens[index + 1])
                index += 2
            }
        }
        for (section in Regex("beginbfrange(.*?)endbfrange", RegexOption.DOT_MATCHES_ALL).findAll(text)) {
            parseRanges(section.groupValues[1], result)
        }
        return result
    }

    private fun parseRanges(section: String, result: MutableMap<Int, String>) {
        var index = 0
        while (index < section.length) {
            if (section[index] != '<') {
                index++
                continue
            }
            val lowEnd = section.indexOf('>', index)
            if (lowEnd < 0) return
            val low = section.substring(index + 1, lowEnd)
            var cursor = lowEnd + 1
            while (cursor < section.length && section[cursor].isWhitespace()) cursor++
            if (cursor >= section.length || section[cursor] != '<') {
                index = lowEnd + 1
                continue
            }
            val highEnd = section.indexOf('>', cursor)
            if (highEnd < 0) return
            val high = section.substring(cursor + 1, highEnd)
            var after = highEnd + 1
            while (after < section.length && section[after].isWhitespace()) after++
            if (after < section.length && section[after] == '[') {
                val close = section.indexOf(']', after)
                val items = if (close < 0) {
                    emptyList()
                } else {
                    HEX_TOKEN.findAll(section.substring(after + 1, close)).map { it.groupValues[1] }.toList()
                }
                val first = codeOf(low)
                val last = codeOf(high)
                for (offset in 0..(last - first)) {
                    if (offset < items.size) result[first + offset] = textOf(items[offset])
                }
                index = if (close < 0) section.length else close + 1
            } else if (after < section.length && section[after] == '<') {
                val destinationEnd = section.indexOf('>', after)
                if (destinationEnd < 0) return
                val first = codeOf(low)
                val last = codeOf(high)
                var bytes = bytesOf(section.substring(after + 1, destinationEnd))
                for (code in first..last) {
                    result[code] = textOfBytes(bytes)
                    bytes = increment(bytes)
                }
                index = destinationEnd + 1
            } else {
                index = highEnd + 1
            }
        }
    }

    /** The numeric value of a hexadecimal code. */
    private fun codeOf(hex: String): Int {
        var value = 0L
        for (digit in hex.lowercase()) {
            val number = digit.digitToIntOrNull(16) ?: continue
            value = value * 16 + number
        }
        return value.toInt()
    }

    /** The bytes a hexadecimal destination stands for. */
    private fun bytesOf(hex: String): ByteArray {
        var text = hex.filter { it.digitToIntOrNull(16) != null }
        if (text.length % 2 == 1) text += "0"
        val out = ByteArray(text.length / 2)
        for (index in out.indices) {
            out[index] = text.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
        return out
    }

    /** The text a hexadecimal destination stands for. */
    private fun textOf(hex: String): String = textOfBytes(bytesOf(hex))

    private fun textOfBytes(bytes: ByteArray): String =
        if (bytes.size >= 2 && bytes.size % 2 == 0) {
            String(bytes, Charsets.UTF_16BE)
        } else {
            String(bytes, Charsets.ISO_8859_1)
        }

    /** Increment the last byte of a code, carrying into the byte before it. */
    private fun increment(bytes: ByteArray): ByteArray {
        val out = bytes.copyOf()
        var index = out.size - 1
        while (index >= 0) {
            val next = (out[index].toInt() and 0xff) + 1
            out[index] = (next and 0xff).toByte()
            if (next <= 0xff) return out
            index--
        }
        return out
    }

    private val HEX_TOKEN = Regex("<([0-9A-Fa-f\\s]*)>")
}
