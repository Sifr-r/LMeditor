package lmeditor.pdf

/**
 * The cross-reference table of a PDF.
 *
 * @property offsets the file offset of every object the table knows.
 * @property trailer the trailer dictionary, which names the catalog and the information dictionary.
 * @property usesObjectStreams true when the table points at compressed object streams.
 */
internal class PdfXref(
    val offsets: Map<Int, Int>,
    val trailer: PdfDictionary?,
    val usesObjectStreams: Boolean,
)

/**
 * The reader for a PDF's cross-reference table.
 *
 * Both flavours are read: the classic table (`xref` plus a `trailer`
 * dictionary) and the cross-reference stream a PDF 1.5 file carries. A file
 * whose table is missing or damaged is not an error here -- the object scan
 * is the fallback, and the document says so in its issues.
 */
internal object PdfXrefParser {

    /** Read the cross-reference table of [bytes], or `null` when there is none. */
    fun parse(bytes: ByteArray): PdfXref? {
        val start = findStartXref(bytes) ?: return null
        return parseSection(bytes, start, HashSet())
    }

    private fun findStartXref(bytes: ByteArray): Int? {
        val index = lastIndexOfToken(bytes, "startxref")
        if (index < 0) return null
        val parser = PdfParser(bytes, index + "startxref".length)
        return parser.readNumber()?.intValue
    }

    private fun parseSection(bytes: ByteArray, offset: Int, visited: MutableSet<Int>): PdfXref? {
        if (offset < 0 || offset >= bytes.size || !visited.add(offset)) return null
        val parser = PdfParser(bytes, offset)
        parser.skipWhitespace()
        val section = if (parser.atKeyword("xref")) {
            parseClassicTable(parser)
        } else {
            parseXrefStream(bytes, offset)
        } ?: return null

        val offsets = LinkedHashMap(section.offsets)
        var usesObjectStreams = section.usesObjectStreams
        val trailer = section.trailer

        // A hybrid-reference file keeps the compressed objects in a stream the
        // classic trailer points at separately.
        val hybrid = (trailer?.get("XRefStm") as? PdfNumber)?.intValue
        if (hybrid != null) {
            parseSection(bytes, hybrid, visited)?.let { hybridSection ->
                for ((number, at) in hybridSection.offsets) offsets.putIfAbsent(number, at)
                usesObjectStreams = usesObjectStreams || hybridSection.usesObjectStreams
            }
        }

        // An incremental update chains to the table it supersedes.
        val previous = (trailer?.get("Prev") as? PdfNumber)?.intValue
        if (previous != null) {
            parseSection(bytes, previous, visited)?.let { older ->
                for ((number, at) in older.offsets) offsets.putIfAbsent(number, at)
                usesObjectStreams = usesObjectStreams || older.usesObjectStreams
            }
        }
        return PdfXref(offsets, trailer, usesObjectStreams)
    }

    private fun parseClassicTable(parser: PdfParser): PdfXref? {
        parser.pos += 4
        val offsets = LinkedHashMap<Int, Int>()
        while (true) {
            parser.skipWhitespace()
            if (parser.atKeyword("trailer")) {
                parser.pos += 7
                break
            }
            val first = parser.readNumber() ?: break
            val count = parser.readNumber() ?: break
            for (index in 0 until count.intValue) {
                parser.skipWhitespace()
                val offset = parser.readNumber() ?: return null
                parser.skipWhitespace()
                parser.readNumber()
                parser.skipWhitespace()
                val type = parser.peekByte()
                parser.pos += 1
                if (type == 'n'.code) offsets[first.intValue + index] = offset.intValue
            }
        }
        val trailer = parser.parseValue() as? PdfDictionary
        return PdfXref(offsets, trailer, false)
    }

    private fun parseXrefStream(bytes: ByteArray, offset: Int): PdfXref? {
        val parser = PdfParser(bytes, offset)
        val parsed = parser.parseIndirectObjectAt(offset) ?: return null
        val stream = parsed.second as? PdfStream ?: return null
        val dictionary = stream.dictionary
        val widths = (dictionary["W"] as? PdfArray)?.items?.mapNotNull { (it as? PdfNumber)?.intValue }
            ?: return null
        if (widths.size < 3) return null
        val data = when (val outcome = PdfFilters.decode(stream)) {
            is PdfFilters.Outcome.Decoded -> outcome.data
            is PdfFilters.Outcome.Failed -> return null
        }
        val ranges = (dictionary["Index"] as? PdfArray)?.items?.mapNotNull { (it as? PdfNumber)?.intValue }
            ?: listOf(0, (dictionary.number("Size") ?: 0.0).toInt())
        val offsets = LinkedHashMap<Int, Int>()
        var usesObjectStreams = false
        var cursor = 0
        var range = 0
        while (range + 1 < ranges.size && cursor < data.size) {
            val first = ranges[range]
            val count = ranges[range + 1]
            for (step in 0 until count) {
                if (cursor + widths.sum() > data.size) break
                val type = readField(data, cursor, widths[0])
                cursor += widths[0]
                val field = readField(data, cursor, widths[1])
                cursor += widths[1]
                cursor += widths[2]
                when (type) {
                    1 -> offsets[first + step] = field
                    2 -> usesObjectStreams = true
                }
            }
            range += 2
        }
        return PdfXref(offsets, dictionary, usesObjectStreams)
    }

    private fun readField(data: ByteArray, offset: Int, width: Int): Int {
        if (width <= 0) return 0
        var value = 0
        for (index in 0 until width) {
            val at = offset + index
            if (at >= data.size) break
            value = (value shl 8) or (data[at].toInt() and 0xff)
        }
        return value
    }
}

/** The offset of the last occurrence of [token] in [bytes], or `-1`. */
internal fun lastIndexOfToken(bytes: ByteArray, token: String): Int {
    val needle = token.toByteArray(Charsets.ISO_8859_1)
    if (needle.isEmpty() || bytes.size < needle.size) return -1
    var index = bytes.size - needle.size
    while (index >= 0) {
        var matched = true
        for (offset in needle.indices) {
            if (bytes[index + offset] != needle[offset]) {
                matched = false
                break
            }
        }
        if (matched) return index
        index--
    }
    return -1
}

/**
 * Resolves the objects of a PDF.
 *
 * The cross-reference table is the fast path. When it is missing, damaged or
 * simply wrong, the file is scanned once for its `number generation obj`
 * markers and the scan answers instead -- a PDF that a reader can open must
 * not be refused over a broken table.
 */
internal class PdfObjectResolver(
    private val bytes: ByteArray,
    private val offsets: Map<Int, Int>,
) {

    private var scanned: Map<Int, PdfObject>? = null

    /** The object with the given number, or `null`. */
    fun objectByNumber(number: Int): PdfObject? {
        val offset = offsets[number]
        // An offset that does not point into the file is not an offset: the
        // table is wrong, and the scan answers instead of the table.
        if (offset != null && offset in bytes.indices) {
            val parsed = PdfParser(bytes, offset).parseIndirectObjectAt(offset)
            if (parsed != null && parsed.first == number) return parsed.second
        }
        return scan()[number]
    }

    /** The object a reference points at, or the value itself when it is not a reference. */
    fun resolve(value: PdfObject?): PdfObject? = when (value) {
        null -> null
        is PdfReference -> objectByNumber(value.number)
        else -> value
    }

    /** The dictionary a value names, or `null`. */
    fun dictionary(value: PdfObject?): PdfDictionary? = resolve(value) as? PdfDictionary

    /** The first object in the file whose `/Type` is [type], or `null`. */
    fun findByType(type: String): PdfDictionary? {
        for (value in scan().values) {
            val dictionary = value as? PdfDictionary ?: continue
            if (dictionary.name("Type") == type) return dictionary
        }
        return null
    }

    /**
     * The first object in the file whose dictionary carries one of [keys], or `null`.
     *
     * The information dictionary of a PDF carries no `/Type` -- only the page
     * tree, the catalog, the pages and the fonts have one -- so it cannot be
     * found by its type and is found by the keys it holds instead. A dictionary
     * that does carry a `/Type` is a structural one and is never a candidate.
     */
    fun findByKeys(keys: List<String>): PdfDictionary? {
        for (value in scan().values) {
            val dictionary = value as? PdfDictionary ?: continue
            if (dictionary.name("Type") != null) continue
            if (keys.any { dictionary[it] != null }) return dictionary
        }
        return null
    }

    private fun scan(): Map<Int, PdfObject> = scanned ?: PdfObjectScanner.scan(bytes).also { scanned = it }
}
