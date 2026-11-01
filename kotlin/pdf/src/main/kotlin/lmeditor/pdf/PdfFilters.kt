package lmeditor.pdf

import lmeditor.core.DecompressionLimits
import lmeditor.core.LMeditorError
import java.io.ByteArrayOutputStream
import java.util.zip.DataFormatException
import java.util.zip.Inflater

/**
 * Raised when a stream expands beyond the cap the tier applies.
 *
 * It is a typed failure rather than a truncation: a stream that cannot be
 * decoded within the cap is not decoded at all, and the document that carries
 * it is refused with a [lmeditor.core.SourceLoadError] rather than read with a
 * hole in it.
 */
internal class PdfStreamTooLargeError(
    /** The reason the stream was refused. */
    val reason: String,
) : LMeditorError(reason, mapOf("reason" to reason))

/**
 * The stream filters a PDF applies to its data.
 *
 * Only the filters real documents use are implemented: `FlateDecode` (which
 * is the JDK's zlib), `ASCIIHexDecode`, `ASCII85Decode` and `RunLengthDecode`,
 * together with the PNG and TIFF predictors that sit on top of them. A filter
 * that is not implemented is *reported* rather than ignored, because a stream
 * that was not decoded is a stream whose text was not read.
 *
 * Every filter holds its output to the same bound, the shared one in
 * `lmeditor.core.DecompressionLimits`. A stream that expands past the bound
 * is refused with a typed error rather than truncated, and the refusal
 * happens before the bytes are written, so a file that stores a bomb cannot
 * make the reader allocate one.
 */
internal object PdfFilters {

    /** The result of decoding a stream. */
    sealed interface Outcome {
        /** The stream was decoded. */
        data class Decoded(val data: ByteArray) : Outcome

        /** The stream could not be decoded, and the reason why. */
        data class Failed(val reason: String) : Outcome
    }

    /** Decode the data of [stream] by applying its filters in order. */
    fun decode(stream: PdfStream): Outcome {
        var data = stream.rawData
        val filters = stream.filterNames()
        val parms = stream.decodeParms()
        for (index in filters.indices) {
            when (val filter = filters[index]) {
                "FlateDecode", "Fl" -> {
                    val inflated = inflate(data)
                        ?: return Outcome.Failed("the $filter stream could not be inflated")
                    data = inflated
                }
                "ASCIIHexDecode", "AHx" -> data = asciiHexDecode(data)
                "ASCII85Decode", "A85" -> data = ascii85Decode(data)
                "RunLengthDecode", "RL" -> data = runLengthDecode(data)
                else -> return Outcome.Failed("the $filter filter is not supported")
            }
            val parm = parms.getOrNull(index)
            val predictor = (parm?.number("Predictor") ?: 1.0).toInt()
            if (predictor > 1) {
                data = undoPredictor(
                    data,
                    predictor,
                    parm?.number("Colors")?.toInt() ?: 1,
                    parm?.number("BitsPerComponent")?.toInt() ?: 8,
                    parm?.number("Columns")?.toInt() ?: 1,
                )
            }
        }
        return Outcome.Decoded(data)
    }

    /**
     * Inflate a zlib stream.
     *
     * Returns `null` when the data is not a valid zlib stream. A producer that
     * wrote raw deflate data instead of zlib data is common enough to be
     * worth a second attempt.
     *
     * The stream is refused when it expands beyond the cap the tier applies, so
     * a file that stores a bomb cannot make the reader allocate it.
     */
    private fun inflate(data: ByteArray): ByteArray? =
        inflate(data, nowrap = false) ?: inflate(data, nowrap = true)

    private fun inflate(data: ByteArray, nowrap: Boolean): ByteArray? {
        val inflater = Inflater(nowrap)
        try {
            inflater.setInput(data)
            val out = ByteArrayOutputStream(initialCapacity(data.size))
            val buffer = ByteArray(16 * 1024)
            var expanded = 0L
            while (!inflater.finished()) {
                val read = inflater.inflate(buffer)
                if (read > 0) {
                    expanded += read
                    refuseTooLarge(expanded, data.size)
                    out.write(buffer, 0, read)
                } else if (inflater.needsInput() || inflater.needsDictionary()) {
                    return null
                }
            }
            return out.toByteArray()
        } catch (e: DataFormatException) {
            return null
        } finally {
            inflater.end()
        }
    }

    /**
     * The capacity a filter's output buffer starts with.
     *
     * The size is computed in `Long`, because twice the size of a stream of
     * more than a gigabyte does not fit in an `Int` and a negative capacity is
     * refused by the JDK with an `IllegalArgumentException` of its own.
     */
    private fun initialCapacity(compressed: Int): Int =
        (compressed.toLong() * 2).coerceAtMost(DecompressionLimits.MAX_EXPANDED_BYTES).toInt()

    /**
     * Refuse a stream that has expanded too far.
     *
     * The bounds are the shared ones in `lmeditor.core.DecompressionLimits`:
     * the expanded size divided by the compressed size, counted in whole
     * numbers, must not exceed the shared ratio, and the expanded size must
     * not exceed the shared size. The DOCX reader is held to the same numbers,
     * because a package part and a content stream are the same threat.
     */
    private fun refuseTooLarge(expanded: Long, compressed: Int) {
        if (expanded > DecompressionLimits.MAX_EXPANDED_BYTES) {
            throw PdfStreamTooLargeError(
                "the stream expands to more than ${DecompressionLimits.MAX_EXPANDED_BYTES} bytes",
            )
        }
        if (compressed > 0 && expanded / compressed > DecompressionLimits.MAX_COMPRESSION_RATIO) {
            throw PdfStreamTooLargeError(
                "the stream expands more than ${DecompressionLimits.MAX_COMPRESSION_RATIO} times " +
                    "its compressed size of $compressed bytes",
            )
        }
    }

    /** Undo the PNG or TIFF predictor a filter applied to its data. */
    private fun undoPredictor(data: ByteArray, predictor: Int, colors: Int, bits: Int, columns: Int): ByteArray {
        // The row length comes from the file, so it is computed in `Long` and
        // held to the shared expansion cap: a file that claims a row of a
        // gigabyte must not make the reader allocate one. The cap is shared
        // rather than restated because a predictor row is read out of the
        // decoded stream, so it cannot be longer than the largest stream the
        // tier is willing to hold. The *rule* is the PDF filter chain's alone
        // -- only a PDF has predictors -- but its number is the shared one.
        val bytesPerPixel = (colors.toLong() * bits / 8)
            .coerceIn(1L, DecompressionLimits.MAX_EXPANDED_BYTES)
            .toInt()
        val rowLength = (columns.toLong() * colors * bits + 7) / 8
        if (rowLength > DecompressionLimits.MAX_EXPANDED_BYTES) {
            throw PdfStreamTooLargeError(
                "a predictor row of $rowLength bytes exceeds the maximum of " +
                    "${DecompressionLimits.MAX_EXPANDED_BYTES} bytes",
            )
        }
        if (predictor == 2) {
            return undoTiffPredictor(data, bytesPerPixel, rowLength.toInt())
        }
        return undoPngPredictor(data, bytesPerPixel, rowLength.toInt())
    }

    private fun undoTiffPredictor(data: ByteArray, bytesPerPixel: Int, rowLength: Int): ByteArray {
        if (rowLength <= 0) return data
        val out = data.copyOf()
        var row = 0
        while (row * rowLength < out.size) {
            val base = row * rowLength
            for (index in bytesPerPixel until rowLength) {
                val at = base + index
                if (at >= out.size) break
                out[at] = (out[at] + out[at - bytesPerPixel]).toByte()
            }
            row++
        }
        return out
    }

    private fun undoPngPredictor(data: ByteArray, bytesPerPixel: Int, rowLength: Int): ByteArray {
        if (rowLength <= 0) return data
        val out = ByteArrayOutputStream(data.size)
        // A row cannot be longer than the data it is read from, so the buffer
        // that holds the previous row is never larger than the data itself.
        val previousSize = minOf(rowLength.toLong(), data.size.toLong() + 1)
            .coerceAtMost(Int.MAX_VALUE.toLong())
            .toInt()
        var previous = ByteArray(previousSize)
        var offset = 0
        while (offset < data.size) {
            val filter = data[offset].toInt() and 0xff
            offset++
            val available = minOf(rowLength, data.size - offset)
            if (available <= 0) break
            val row = data.copyOfRange(offset, offset + available)
            offset += available
            for (index in row.indices) {
                val left = if (index >= bytesPerPixel) row[index - bytesPerPixel].toInt() and 0xff else 0
                val up = previous[index].toInt() and 0xff
                val upLeft = if (index >= bytesPerPixel) previous[index - bytesPerPixel].toInt() and 0xff else 0
                val value = when (filter) {
                    1 -> (row[index].toInt() and 0xff) + left
                    2 -> (row[index].toInt() and 0xff) + up
                    3 -> (row[index].toInt() and 0xff) + (left + up) / 2
                    4 -> (row[index].toInt() and 0xff) + paeth(left, up, upLeft)
                    else -> row[index].toInt() and 0xff
                }
                row[index] = (value and 0xff).toByte()
            }
            previous = row
            out.write(row)
        }
        return out.toByteArray()
    }

    private fun paeth(left: Int, up: Int, upLeft: Int): Int {
        val estimate = left + up - upLeft
        val distLeft = kotlin.math.abs(estimate - left)
        val distUp = kotlin.math.abs(estimate - up)
        val distUpLeft = kotlin.math.abs(estimate - upLeft)
        return when {
            distLeft <= distUp && distLeft <= distUpLeft -> left
            distUp <= distUpLeft -> up
            else -> upLeft
        }
    }

    /**
     * Decode hexadecimal data.
     *
     * The pairs are decoded straight out of the stream's bytes. A decoder
     * that first collected every digit into a string would hold a second copy
     * of the whole stream, and the streams that have to be refused for being
     * too large are exactly the streams whose second copy does not fit.
     *
     * The output is held to the shared bound: it is checked before every byte
     * is written, so a stream that expands past the bound is refused rather
     * than truncated.
     */
    private fun asciiHexDecode(data: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(initialCapacity(data.size))
        var expanded = 0L
        var high = -1
        for (byte in data) {
            val b = byte.toInt() and 0xff
            if (b == GREATER) break
            if (!isHexDigit(b)) continue
            val digit = hexValue(b)
            if (high < 0) {
                high = digit
                continue
            }
            refuseTooLarge(expanded + 1, data.size)
            out.write((high shl 4) or digit)
            expanded++
            high = -1
        }
        if (high >= 0) {
            // A stream that ends on half a byte keeps the high nibble and
            // pads it with a zero, the way the filter has always read it.
            refuseTooLarge(expanded + 1, data.size)
            out.write(high shl 4)
        }
        return out.toByteArray()
    }

    /**
     * Decode ASCII85 data.
     *
     * The four bytes a group decodes to are held to the shared bound before
     * they are written, so a stream that expands past the bound is refused
     * rather than truncated.
     */
    private fun ascii85Decode(data: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(initialCapacity(data.size))
        val group = IntArray(5)
        var count = 0
        var expanded = 0L
        for (byte in data) {
            val b = byte.toInt() and 0xff
            if (b == 0x7e) break
            if (isWhitespace(b)) continue
            if (b == 'z'.code && count == 0) {
                refuseTooLarge(expanded + 4, data.size)
                out.write(0)
                out.write(0)
                out.write(0)
                out.write(0)
                expanded += 4
                continue
            }
            if (b < '!'.code || b > 'u'.code) continue
            group[count++] = b - '!'.code
            if (count == 5) {
                refuseTooLarge(expanded + 4, data.size)
                var value = 0L
                for (digit in group) value = value * 85 + digit
                out.write((value shr 24).toInt())
                out.write((value shr 16).toInt())
                out.write((value shr 8).toInt())
                out.write(value.toInt())
                expanded += 4
                count = 0
            }
        }
        if (count > 1) {
            val missing = 5 - count
            for (index in count until 5) group[index] = 84
            var value = 0L
            for (digit in group) value = value * 85 + digit
            val bytes = byteArrayOf(
                (value shr 24).toByte(),
                (value shr 16).toByte(),
                (value shr 8).toByte(),
                value.toByte(),
            )
            refuseTooLarge(expanded + missing, data.size)
            out.write(bytes, 0, missing)
        }
        return out.toByteArray()
    }

    /**
     * Decode RunLength data.
     *
     * This is the filter of the three that can expand the most -- a two-byte
     * run decodes to a hundred and twenty-eight bytes -- so the shared bound
     * is checked before every run is written: a stream that expands past the
     * bound is refused rather than truncated.
     */
    private fun runLengthDecode(data: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(initialCapacity(data.size))
        var expanded = 0L
        var index = 0
        while (index < data.size) {
            val length = data[index].toInt() and 0xff
            index++
            if (length == 128) break
            if (length < 128) {
                val available = minOf(length + 1, data.size - index)
                refuseTooLarge(expanded + available, data.size)
                for (offset in 0 until available) {
                    out.write(data[index].toInt())
                    index++
                }
                expanded += available
            } else {
                if (index >= data.size) break
                val byte = data[index].toInt()
                index++
                val count = 257 - length
                refuseTooLarge(expanded + count, data.size)
                repeat(count) { out.write(byte) }
                expanded += count
            }
        }
        return out.toByteArray()
    }
}

private const val GREATER = 0x3e

/**
 * The value of a hexadecimal digit.
 *
 * The caller has already established that the byte is a hexadecimal digit,
 * so the byte that is left over is an upper-case letter.
 */
private fun hexValue(b: Int): Int = when {
    b <= 0x39 -> b - 0x30
    b >= 0x61 -> b - 0x61 + 10
    else -> b - 0x41 + 10
}

