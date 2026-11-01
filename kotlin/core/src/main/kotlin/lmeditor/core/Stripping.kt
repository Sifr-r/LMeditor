package lmeditor.core

/**
 * Python's whitespace, as the Pydantic models see it.
 *
 * The Python tier's models are declared with `str_strip_whitespace=True`, so a
 * string field never keeps the whitespace around it. The Kotlin tier's models
 * have to behave the same way, or the two tiers disagree about what a section
 * says -- and a disagreement about the text is a disagreement about every
 * character offset measured against it.
 *
 * This is **not** `String.trim()`. Kotlin's `trim()` uses
 * `Character.isWhitespace`, which leaves the non-breaking spaces alone
 * (U+00A0, U+2007, U+202F) and the next-line control (U+0085). Python's
 * `str.strip()` removes all of them, because Python's `str.isspace()` says
 * they are whitespace. The set is Python's, and it lives in
 * [CodePoints.isPythonWhitespace] so that a rule which ports a Python regular
 * expression and this one cannot drift apart.
 */
private fun isPythonWhitespace(ch: Char): Boolean = CodePoints.isPythonWhitespace(ch)

/**
 * Return this string without its leading and trailing whitespace, the way
 * Python's `str.strip()` removes it.
 *
 * The Kotlin tier's schema models call this on every string field when they
 * are constructed, mirroring Pydantic's `str_strip_whitespace=True`. It is
 * deliberately **not** applied by `copy()`: Pydantic's `model_copy()` does not
 * re-validate either, so a copied model keeps exactly the values it was
 * copied with.
 */
fun String.stripModelWhitespace(): String {
    var start = 0
    var end = length
    while (start < end && isPythonWhitespace(this[start])) start++
    while (end > start && isPythonWhitespace(this[end - 1])) end--
    return if (start == 0 && end == length) this else substring(start, end)
}

/**
 * Return these strings without their leading and trailing whitespace.
 *
 * Pydantic strips the items of a `list[str]` field too, because the items are
 * validated by the same string validator.
 */
fun List<String>.stripModelWhitespace(): List<String> = map { it.stripModelWhitespace() }
