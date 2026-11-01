package lmeditor.pdf

import kotlin.math.floor

/**
 * The PDF object model.
 *
 * A PDF file is a sequence of *indirect objects*, each holding one of these
 * values. The model deliberately mirrors the file format rather than the
 * domain: a dictionary entry keeps the name it was written with, and a string
 * keeps its bytes, because the bytes are what the encodings (`/Differences`,
 * `/ToUnicode`) are defined against.
 */
sealed interface PdfObject

/** The PDF `null` object, written as `null`. */
data object PdfNull : PdfObject

/** A PDF boolean. */
data class PdfBoolean(val value: Boolean) : PdfObject

/** A PDF number. PDF has a single numeric type, so integers are whole-valued doubles. */
data class PdfNumber(val value: Double) : PdfObject {

    /** True when the number has no fractional part. */
    val isInteger: Boolean get() = value.isFinite() && value == floor(value)

    /** The number as an `Int`, truncated towards zero. */
    val intValue: Int get() = value.toInt()
}

/**
 * A PDF string: a sequence of bytes.
 *
 * The bytes are kept raw because the encoding that applies to them is fixed
 * by the consumer -- the font for a text string, the `/Differences` table for
 * a code -- and not by the string itself.
 */
class PdfString(val bytes: ByteArray) : PdfObject {

    /** The bytes as a document text string: UTF-16 when the string carries a BOM, Latin-1 otherwise. */
    val text: String get() = decodeTextString(bytes)

    override fun equals(other: Any?): Boolean = other is PdfString && bytes.contentEquals(other.bytes)

    override fun hashCode(): Int = bytes.contentHashCode()

    override fun toString(): String = "PdfString($text)"
}

/** A PDF name. The leading slash is not part of [value]. */
data class PdfName(val value: String) : PdfObject

/** A PDF array. */
data class PdfArray(val items: List<PdfObject>) : PdfObject

/** A PDF dictionary. */
data class PdfDictionary(val entries: Map<String, PdfObject>) : PdfObject {

    /** The entry stored under [name], or `null`. */
    operator fun get(name: String): PdfObject? = entries[name]

    /** The value of the name entry [name], or `null`. */
    fun name(name: String): String? = (entries[name] as? PdfName)?.value

    /** The value of the numeric entry [name], or `null`. */
    fun number(name: String): Double? = (entries[name] as? PdfNumber)?.value

    /** The value of the dictionary entry [name], or `null`. */
    fun dictionary(name: String): PdfDictionary? = entries[name] as? PdfDictionary

    /** The value of the array entry [name], or `null`. */
    fun array(name: String): PdfArray? = entries[name] as? PdfArray
}
/** A PDF stream: its dictionary and its still-encoded data. */
class PdfStream(val dictionary: PdfDictionary, val rawData: ByteArray) : PdfObject {

    override fun equals(other: Any?): Boolean =
        other is PdfStream && dictionary == other.dictionary && rawData.contentEquals(other.rawData)

    override fun hashCode(): Int = 31 * dictionary.hashCode() + rawData.contentHashCode()

    override fun toString(): String = "PdfStream(${dictionary.entries.keys})"
}

/** An indirect reference: `number generation R`. */
data class PdfReference(val number: Int, val generation: Int = 0) : PdfObject

/** The names of the filters applied to a stream's data, in application order. */
internal fun PdfStream.filterNames(): List<String> = when (val filter = dictionary["Filter"]) {
    is PdfName -> listOf(filter.value)
    is PdfArray -> filter.items.mapNotNull { (it as? PdfName)?.value }
    else -> emptyList()
}

/** The decode parameters that belong to each filter of a stream. */
internal fun PdfStream.decodeParms(): List<PdfDictionary?> = when (val parms = dictionary["DecodeParms"]) {
    is PdfDictionary -> listOf(parms)
    is PdfArray -> parms.items.map { it as? PdfDictionary }
    else -> emptyList()
}
