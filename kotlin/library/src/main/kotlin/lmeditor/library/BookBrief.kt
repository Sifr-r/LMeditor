package lmeditor.library

/**
 * The brief: the bounded, cached memory of what a book contains.
 *
 * A book is too large to hold in context, but an editor still has to know what
 * the book is about while they work on one section of it. The brief is that
 * knowledge, kept small on purpose.
 *
 * It is derived from the *index* of the book -- its title and the titles of its
 * sections -- and never from the body of a section, so it stays the same size
 * whether the book has three sections or three thousand. When the book has
 * more section titles than fit in the budget, the oldest are dropped and the
 * most recent are kept: the brief is about what the editor is working on now.
 *
 * [mergeBrief] applies the same rule to an update: the addition is appended and
 * the front is trimmed, so the brief keeps the most recent information while
 * staying bounded.
 */

/**
 * Return a bounded digest of the book, derived from its section index.
 *
 * The digest names the book and then every section of it, by title. It reads
 * the manifest only -- no section body is touched -- so the cost of the brief
 * does not grow with the length of the book.
 *
 * When the section titles do not all fit in [maxChars], the oldest are dropped
 * and the most recent are kept, and the digest says how many were left out.
 * The rule is the one [mergeBrief] applies, so the two halves of the brief
 * agree on what "recent" means.
 *
 * @param store the library the book is kept in.
 * @param bookId the book to describe.
 * @param maxChars the longest brief to return.
 * @return the digest, at most [maxChars] characters long.
 * @throws UnsafeIdentifierError the book id is not a safe path segment.
 * @throws lmeditor.core.BookNotFoundError the library holds no such book.
 */
fun buildBrief(store: BookStore, bookId: String, maxChars: Int = 2000): String {
    if (maxChars <= 0) {
        return ""
    }
    val manifest = store.open(bookId)
    val header = "Book: ${manifest.title.ifEmpty { manifest.bookId }}"
    val summary = "Sections: ${manifest.sections.size}"
    val entries = manifest.sections.mapIndexed { index, section ->
        "${index + 1}. ${section.title.ifEmpty { section.sectionId }}"
    }

    val fixed = "$header\n$summary"
    var budget = maxChars - fixed.length
    val kept = mutableListOf<String>()
    for (entry in entries.asReversed()) {
        val cost = entry.length + 1
        if (cost > budget) {
            break
        }
        kept.add(entry)
        budget -= cost
    }
    kept.reverse()

    val omitted = entries.size - kept.size
    if (omitted > 0) {
        val marker = "... $omitted earlier section title(s) omitted"
        if (marker.length + 1 <= budget) {
            kept.add(0, marker)
        }
    }

    return (listOf(header, summary) + kept).joinToString("\n").take(maxChars)
}

/**
 * Return the brief with [addition] appended, bounded to [maxChars].
 *
 * When the result is too long the *front* is trimmed, whole lines at a time,
 * so the brief keeps the most recent information. A single line that is longer
 * than the budget on its own is cut from the front as well, because a brief
 * that exceeds its budget is a brief that will not fit in a context window.
 *
 * @param current the brief as it stands.
 * @param addition the information to add to it.
 * @param maxChars the longest brief to return.
 * @return the merged brief, at most [maxChars] characters long.
 */
fun mergeBrief(current: String, addition: String, maxChars: Int = 2000): String {
    if (maxChars <= 0) {
        return ""
    }
    val head = current.trim()
    val tail = addition.trim()
    if (tail.isEmpty()) {
        return trimFront(head, maxChars)
    }
    val combined = if (head.isEmpty()) tail else "$head\n$tail"
    return trimFront(combined, maxChars)
}

/** Return [text] trimmed from the front until it fits in [maxChars]. */
internal fun trimFront(text: String, maxChars: Int): String {
    if (text.length <= maxChars) {
        return text
    }
    val lines = text.split("\n").toMutableList()
    while (lines.size > 1) {
        val candidate = lines.drop(1).joinToString("\n")
        if (candidate.length <= maxChars) {
            return candidate
        }
        lines.removeAt(0)
    }
    return lines[0].takeLast(maxChars)
}
