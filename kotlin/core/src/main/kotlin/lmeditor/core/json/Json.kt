package lmeditor.core.json

import lmeditor.core.ValidationError

/**
 * A minimal, dependency-free JSON codec.
 *
 * The Kotlin tier ships with an empty runtime dependency set, so JSON is
 * implemented here rather than pulled from a library. The object model is the
 * plain Kotlin one, chosen so it maps onto Python's `dict`/`list` without a
 * translation layer across the NDJSON seam:
 *
 * * object  -> `Map<String, Any?>`
 * * array   -> `List<Any?>`
 * * string  -> `String`
 * * number  -> `Long` when integral, `Double` otherwise
 * * boolean -> `Boolean`
 * * null    -> `null`
 */
object Json {

    /** Parse a JSON document. */
    fun parse(text: String): Any? = Parser(text).parseDocument()

    /** Parse a JSON document that must be an object. */
    fun parseObject(text: String): Map<String, Any?> {
        val parsed = parse(text)
        @Suppress("UNCHECKED_CAST")
        return parsed as? Map<String, Any?>
            ?: throw ValidationError("Expected a JSON object at the document root", field = "json")
    }

    /** Serialise a value to JSON text. */
    fun write(value: Any?): String {
        val out = StringBuilder()
        writeValue(value, out)
        return out.toString()
    }

    /** Serialise a value to JSON text, with the trailing newline the NDJSON seam uses. */
    fun writeLine(value: Any?): String = write(value) + "\n"

    private fun writeValue(value: Any?, out: StringBuilder) {
        when (value) {
            null -> out.append("null")
            is String -> writeString(value, out)
            is Boolean -> out.append(if (value) "true" else "false")
            is Int -> out.append(value.toString())
            is Long -> out.append(value.toString())
            is Short -> out.append(value.toString())
            is Byte -> out.append(value.toString())
            is Double -> writeDouble(value, out)
            is Float -> writeDouble(value.toDouble(), out)
            is Number -> out.append(value.toString())
            is lmeditor.core.WireEnum -> writeString(value.wire, out)
            is Map<*, *> -> {
                out.append('{')
                var first = true
                for ((k, v) in value) {
                    if (!first) out.append(',')
                    first = false
                    writeString(k.toString(), out)
                    out.append(':')
                    writeValue(v, out)
                }
                out.append('}')
            }
            is Iterable<*> -> {
                out.append('[')
                var first = true
                for (v in value) {
                    if (!first) out.append(',')
                    first = false
                    writeValue(v, out)
                }
                out.append(']')
            }
            is Array<*> -> writeValue(value.asList(), out)
            is IntArray -> writeValue(value.asList(), out)
            is LongArray -> writeValue(value.asList(), out)
            is DoubleArray -> writeValue(value.asList(), out)
            is BooleanArray -> writeValue(value.asList(), out)
            else -> writeString(value.toString(), out)
        }
    }

    private fun writeDouble(value: Double, out: StringBuilder) {
        if (value.isNaN() || value.isInfinite()) {
            // JSON has no NaN or Infinity; null is the only honest encoding.
            out.append("null")
        } else {
            // A double always keeps its decimal form, so the Python side reads
            // a float rather than an int. `1.0` and `1` are the same JSON
            // number, but they are not the same Python type, and a field typed
            // as a float should arrive as one.
            out.append(value.toString())
        }
    }

    private fun writeString(value: String, out: StringBuilder) {
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                else ->
                    if (ch < ' ' || ch == '\u2028' || ch == '\u2029') {
                        out.append("\\u").append(ch.code.toString(16).padStart(4, '0'))
                    } else {
                        out.append(ch)
                    }
            }
        }
        out.append('"')
    }
}

/**
 * Builds a JSON object in insertion order.
 *
 * The Kotlin tier writes its wire payloads by hand -- there is no reflection
 * and no serialization framework -- so this builder is what keeps those
 * hand-written payloads readable.
 */
class JsonObjectBuilder internal constructor() {

    private val entries = LinkedHashMap<String, Any?>()

    /** Add a field, including a null one. */
    fun put(key: String, value: Any?) {
        entries[key] = value
    }

    /** Add a field only when it has a value. */
    fun putIfPresent(key: String, value: Any?) {
        if (value != null) entries[key] = value
    }

    /** Add every field of another object. */
    fun putAll(values: Map<String, Any?>) {
        entries.putAll(values)
    }

    /** The finished object. */
    fun build(): Map<String, Any?> = entries
}

/** Build a JSON object. */
fun jsonObject(block: JsonObjectBuilder.() -> Unit): Map<String, Any?> {
    val builder = JsonObjectBuilder()
    builder.block()
    return builder.build()
}

/** Build a JSON array. */
fun jsonArray(block: ArrayList<Any?>.() -> Unit): List<Any?> {
    val builder = ArrayList<Any?>()
    builder.block()
    return builder
}

/** Recursive-descent JSON parser. */
private class Parser(private val text: String) {

    private var index = 0

    fun parseDocument(): Any? {
        skipWhitespace()
        if (index >= text.length) {
            throw ValidationError("Unexpected end of JSON input", field = "json")
        }
        val value = parseValue()
        skipWhitespace()
        if (index != text.length) {
            throw ValidationError(
                "Trailing content after the JSON document at offset $index",
                field = "json",
            )
        }
        return value
    }

    private fun parseValue(): Any? {
        skipWhitespace()
        if (index >= text.length) {
            throw ValidationError("Unexpected end of JSON input", field = "json")
        }
        return when (val ch = text[index]) {
            '{' -> parseObject()
            '[' -> parseArray()
            '"' -> parseString()
            't' -> parseLiteral("true", true)
            'f' -> parseLiteral("false", false)
            'n' -> parseLiteral("null", null)
            else -> {
                if (ch == '-' || ch in '0'..'9') parseNumber()
                else throw ValidationError(
                    "Unexpected character '$ch' at offset $index",
                    field = "json",
                )
            }
        }
    }

    private fun parseObject(): Map<String, Any?> {
        expect('{')
        val result = LinkedHashMap<String, Any?>()
        skipWhitespace()
        if (peek() == '}') {
            index++
            return result
        }
        while (true) {
            skipWhitespace()
            if (peek() != '"') {
                throw ValidationError(
                    "Expected a string key at offset $index",
                    field = "json",
                )
            }
            val key = parseString()
            skipWhitespace()
            expect(':')
            result[key] = parseValue()
            skipWhitespace()
            when (peek()) {
                ',' -> index++
                '}' -> {
                    index++
                    return result
                }
                else -> throw ValidationError(
                    "Expected ',' or '}' at offset $index",
                    field = "json",
                )
            }
        }
    }

    private fun parseArray(): List<Any?> {
        expect('[')
        val result = mutableListOf<Any?>()
        skipWhitespace()
        if (peek() == ']') {
            index++
            return result
        }
        while (true) {
            result.add(parseValue())
            skipWhitespace()
            when (peek()) {
                ',' -> index++
                ']' -> {
                    index++
                    return result
                }
                else -> throw ValidationError(
                    "Expected ',' or ']' at offset $index",
                    field = "json",
                )
            }
        }
    }

    private fun parseString(): String {
        expect('"')
        val out = StringBuilder()
        while (true) {
            if (index >= text.length) {
                throw ValidationError("Unterminated JSON string", field = "json")
            }
            when (val ch = text[index]) {
                '"' -> {
                    index++
                    return out.toString()
                }
                '\\' -> {
                    index++
                    if (index >= text.length) {
                        throw ValidationError("Unterminated JSON escape", field = "json")
                    }
                    when (val esc = text[index]) {
                        '"' -> out.append('"')
                        '\\' -> out.append('\\')
                        '/' -> out.append('/')
                        'b' -> out.append('\b')
                        'f' -> out.append('\u000C')
                        'n' -> out.append('\n')
                        'r' -> out.append('\r')
                        't' -> out.append('\t')
                        'u' -> {
                            if (index + 4 >= text.length) {
                                throw ValidationError("Truncated unicode escape", field = "json")
                            }
                            val hex = text.substring(index + 1, index + 5)
                            val code = hex.toIntOrNull(16)
                                ?: throw ValidationError(
                                    "Invalid unicode escape '\\u$hex'",
                                    field = "json",
                                )
                            out.append(code.toChar())
                            index += 4
                        }
                        else -> throw ValidationError(
                            "Invalid escape character '\\$esc'",
                            field = "json",
                        )
                    }
                    index++
                }
                else -> {
                    out.append(ch)
                    index++
                }
            }
        }
    }

    private fun parseNumber(): Any {
        val start = index
        if (peek() == '-') index++
        while (index < text.length && text[index] in '0'..'9') index++
        var isDecimal = false
        if (index < text.length && text[index] == '.') {
            isDecimal = true
            index++
            while (index < text.length && text[index] in '0'..'9') index++
        }
        if (index < text.length && (text[index] == 'e' || text[index] == 'E')) {
            isDecimal = true
            index++
            if (index < text.length && (text[index] == '+' || text[index] == '-')) index++
            while (index < text.length && text[index] in '0'..'9') index++
        }
        val raw = text.substring(start, index)
        if (raw.isEmpty() || raw == "-") {
            throw ValidationError("Invalid number at offset $start", field = "json")
        }
        return if (isDecimal) {
            raw.toDoubleOrNull() ?: throw ValidationError("Invalid number '$raw'", field = "json")
        } else {
            raw.toLongOrNull() ?: raw.toDouble()
        }
    }

    private fun <T> parseLiteral(literal: String, value: T): T {
        if (!text.startsWith(literal, index)) {
            throw ValidationError(
                "Invalid JSON literal at offset $index",
                field = "json",
            )
        }
        index += literal.length
        return value
    }

    private fun peek(): Char =
        if (index < text.length) text[index] else ' '

    private fun expect(ch: Char) {
        if (index >= text.length || text[index] != ch) {
            throw ValidationError(
                "Expected '$ch' at offset $index",
                field = "json",
            )
        }
        index++
    }

    private fun skipWhitespace() {
        while (index < text.length) {
            when (text[index]) {
                ' ', '\t', '\n', '\r' -> index++
                else -> return
            }
        }
    }
}

// --- Typed accessors -------------------------------------------------------

/** Read a string field, falling back to [default] when absent or not a string. */
fun Map<String, Any?>.jsonString(key: String, default: String = ""): String =
    this[key]?.toString() ?: default

/** Read an integer field, falling back to [default]. */
fun Map<String, Any?>.jsonInt(key: String, default: Int = 0): Int = when (val v = this[key]) {
    is Number -> v.toInt()
    is String -> v.toIntOrNull() ?: default
    else -> default
}

/** Read a long field, falling back to [default]. */
fun Map<String, Any?>.jsonLong(key: String, default: Long = 0L): Long = when (val v = this[key]) {
    is Number -> v.toLong()
    is String -> v.toLongOrNull() ?: default
    else -> default
}

/** Read a double field, falling back to [default]. */
fun Map<String, Any?>.jsonDouble(key: String, default: Double = 0.0): Double = when (val v = this[key]) {
    is Number -> v.toDouble()
    is String -> v.toDoubleOrNull() ?: default
    else -> default
}

/** Read a boolean field, falling back to [default]. */
fun Map<String, Any?>.jsonBool(key: String, default: Boolean = false): Boolean = when (val v = this[key]) {
    is Boolean -> v
    is String -> v.toBooleanStrictOrNull() ?: default
    is Number -> v.toInt() != 0
    else -> default
}

/** Read a nested object field, or an empty map when absent. */
fun Map<String, Any?>.jsonObject(key: String): Map<String, Any?> =
    @Suppress("UNCHECKED_CAST")
    (this[key] as? Map<String, Any?>) ?: emptyMap()

/** Read a nested array field, or an empty list when absent. */
fun Map<String, Any?>.jsonArray(key: String): List<Any?> =
    (this[key] as? List<Any?>) ?: emptyList()

/** Read a list-of-objects field. */
fun Map<String, Any?>.jsonObjectList(key: String): List<Map<String, Any?>> =
    jsonArray(key).mapNotNull { it as? Map<String, Any?> }

/** Read a list-of-strings field. */
fun Map<String, Any?>.jsonStringList(key: String): List<String> =
    jsonArray(key).mapNotNull { it?.toString() }
