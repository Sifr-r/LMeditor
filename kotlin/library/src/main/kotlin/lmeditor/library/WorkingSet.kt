package lmeditor.library

import lmeditor.core.BookSection
import lmeditor.core.SectionNotFoundError
import lmeditor.core.SourceChunk
import lmeditor.core.WorkingSet
import lmeditor.core.WorkingSetRole
import lmeditor.core.WorkingSetSection

/**
 * An index over a book's corpus, used to retrieve the passages that bear on a
 * section.
 *
 * The seam exists because the retrieval itself is model-dependent: the Python
 * tier indexes the corpus with an embedding model, and the Kotlin tier must not
 * depend on one. The builder asks the index for the passages that answer a
 * query and takes the first [rankForSection] of them.
 */
fun interface PassageIndex {
    /**
     * Return the passages that answer [query], most relevant first.
     *
     * @param query the text the passages are retrieved for.
     * @param topK the most passages to return.
     */
    fun rankForSection(query: String, topK: Int): List<SourceChunk>
}

/**
 * Builds the bounded context used to edit one section of a book.
 *
 * This is where the promise of the library is kept. A book of any size is
 * edited one section at a time, and the only part of it that is ever held in
 * the language-model context is:
 *
 * * the target section,
 * * the sections immediately before and after it,
 * * the passages retrieval considers relevant to it, and
 * * the running brief of the book.
 *
 * Everything else stays on disk. [build] reads at most `2 * neighbours + 1`
 * section bodies, whatever the size of the book, and the store's `readCount`
 * makes that bound measurable: a caller can assert that editing a section of a
 * five-hundred-page book touched three sections and not one more.
 *
 * @param store the library the book lives in.
 * @param index an index over the book's chunks. When it is absent the working
 *   set carries no passages.
 */
class WorkingSetBuilder(
    /** The library the books are read from. */
    val store: BookStore,
    /** The index the passages are retrieved from, when there is one. */
    val index: PassageIndex? = null,
) {
    /**
     * Return the bounded working set for editing one section of a book.
     *
     * The target section and at most [neighbours] sections on either side of it
     * are read from disk; no other section of the book is touched. Each loaded
     * section is truncated to [maxSectionChars], the brief is truncated to
     * [maxBriefChars], and at most [maxPassages] passages are retrieved for the
     * section.
     *
     * @param bookId the book to edit.
     * @param sectionId the section being edited.
     * @param neighbours how many sections on either side of the target to
     *   include.
     * @param maxPassages the most passages to retrieve.
     * @param maxBriefChars the longest brief to include.
     * @param maxSectionChars the longest section body to include.
     * @return the working set: the target, its neighbours, the retrieved
     *   passages and the brief, with the total size of all of it in
     *   `contextChars`.
     * @throws IllegalArgumentException a bound is negative.
     * @throws UnsafeIdentifierError an id is not a safe path segment.
     * @throws lmeditor.core.BookNotFoundError the library holds no such book.
     * @throws SectionNotFoundError the book holds no such section.
     */
    fun build(
        bookId: String,
        sectionId: String,
        neighbours: Int = 1,
        maxPassages: Int = 8,
        maxBriefChars: Int = 2000,
        maxSectionChars: Int = 4000,
    ): WorkingSet {
        requireNonNegative(neighbours, "neighbours")
        requireNonNegative(maxPassages, "max_passages")
        requireNonNegative(maxBriefChars, "max_brief_chars")
        requireNonNegative(maxSectionChars, "max_section_chars")

        val manifest = store.open(bookId)
        val sections = sortedSections(manifest.sections)
        val targetIndex = indexOf(sections, sectionId)
            ?: throw SectionNotFoundError(bookId, sectionId)

        val previous = sections.subList(
            maxOf(0, targetIndex - neighbours),
            targetIndex,
        )
        val following = sections.subList(
            targetIndex + 1,
            minOf(sections.size, targetIndex + 1 + neighbours),
        )

        val loaded = mutableListOf<WorkingSetSection>()
        for (section in previous) {
            loaded.add(loadSection(bookId, section, WorkingSetRole.PREVIOUS, maxSectionChars))
        }
        val target = sections[targetIndex]
        val targetContent = loadSection(bookId, target, WorkingSetRole.TARGET, maxSectionChars)
        loaded.add(targetContent)
        for (section in following) {
            loaded.add(loadSection(bookId, section, WorkingSetRole.NEXT, maxSectionChars))
        }

        val passages = retrievePassages(target, targetContent.content, maxPassages)
        val brief = store.brief(bookId).take(maxBriefChars)

        val contextChars = loaded.sumOf { it.content.length } +
            passages.sumOf { it.text.length } +
            brief.length
        return WorkingSet(
            bookId = bookId,
            targetSectionId = sectionId,
            sections = loaded,
            passages = passages,
            brief = brief,
            contextChars = contextChars,
        )
    }

    /** Read one section body and return it as a working-set section. */
    private fun loadSection(
        bookId: String,
        section: BookSection,
        role: WorkingSetRole,
        maxSectionChars: Int,
    ): WorkingSetSection {
        val content = store.readSection(bookId, section.sectionId)
        return WorkingSetSection(
            sectionId = section.sectionId,
            title = section.title,
            order = section.order,
            role = role,
            content = content.take(maxSectionChars),
        )
    }

    /**
     * Return the passages that bear on the target section.
     *
     * Without an index there is nothing to retrieve from, and the working set
     * carries no passages. The query is the section's own title and text,
     * which is what makes the retrieved passages the ones that answer this
     * section rather than the ones that happen to be first.
     */
    private fun retrievePassages(
        target: BookSection,
        content: String,
        maxPassages: Int,
    ): List<SourceChunk> {
        val activeIndex = index ?: return emptyList()
        if (maxPassages <= 0) {
            return emptyList()
        }
        val query = retrievalQuery(target, content)
        if (query.isBlank()) {
            return emptyList()
        }
        return activeIndex.rankForSection(query, maxPassages).take(maxPassages)
    }
}

/** Return the query the passages for a section are retrieved with. */
internal fun retrievalQuery(section: BookSection, content: String): String {
    val parts = listOf(section.title.trim(), content.trim())
    return parts.filter { it.isNotEmpty() }.joinToString("\n")
}

/** Return the position of a section in the book, or null. */
internal fun indexOf(sections: List<BookSection>, sectionId: String): Int? {
    for (index in sections.indices) {
        if (sections[index].sectionId == sectionId) {
            return index
        }
    }
    return null
}

/** Raise when a bound is negative. */
private fun requireNonNegative(value: Int, name: String) {
    if (value < 0) {
        throw IllegalArgumentException("$name must not be negative, got $value")
    }
}
