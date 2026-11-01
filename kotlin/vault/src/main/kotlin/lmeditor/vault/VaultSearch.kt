package lmeditor.vault

import lmeditor.core.VaultSearchHit
import lmeditor.core.ValidationError

/**
 * A note as the search sees it.
 *
 * The search reads a note's title, its body and its tags, and nothing else:
 * a vault is a folder of markdown files, and what the operator wrote is what
 * is searched.
 *
 * @property noteId the note's id, its path inside the vault without the extension
 * @property title the title of the note
 * @property path the note's path inside the vault, with the extension
 * @property body the text of the note without its frontmatter
 * @property tags the tags the note carries, lower-cased
 */
data class SearchableNote(
    /** The note's id. */
    val noteId: String,
    /** The title of the note. */
    val title: String,
    /** The note's path inside the vault. */
    val path: String,
    /** The text of the note without its frontmatter. */
    val body: String,
    /** The tags the note carries, lower-cased. */
    val tags: List<String> = emptyList(),
)

/**
 * The ranked search over a vault.
 *
 * The search runs over titles, bodies and tags. A title hit outranks a tag
 * hit, which outranks a body hit. The query is a whitespace-separated set of
 * terms, and a note that carries more of them outranks a note that carries
 * fewer -- so a note that carries every term outranks a note that carries
 * some of them. Ties are broken by the note id, so the same vault and the
 * same query always give the same answer in the same order.
 *
 * The ranking is therefore, in order: the number of terms the note carries,
 * then the weight of the hits (a title hit is worth more than a tag hit, and
 * a tag hit more than a body hit), then the note id.
 */
object VaultSearch {

    /** The weight of a term that appears in a note's title. */
    const val TITLE_WEIGHT: Int = 3

    /** The weight of a term that appears in one of a note's tags. */
    const val TAG_WEIGHT: Int = 2

    /** The weight of a term that appears in a note's body. */
    const val BODY_WEIGHT: Int = 1

    /** The whitespace a term boundary falls on. */
    private val WHITESPACE_RUN: Regex = Regex("\\s+")

    /**
     * Return the notes that carry the terms of [query], best first.
     *
     * @param notes the notes to search
     * @param query the query: a whitespace-separated set of terms
     * @param limit the greatest number of hits to return
     * @return the hits, best first
     * @throws ValidationError the limit is not a positive number
     */
    fun search(notes: List<SearchableNote>, query: String, limit: Int): List<VaultSearchHit> {
        if (limit < 1) {
            throw ValidationError("the limit must be a positive number", field = "limit", value = limit)
        }
        val terms = query.split(WHITESPACE_RUN)
            .filter { it.isNotEmpty() }
            .distinctBy { it.lowercase() }
        if (terms.isEmpty()) {
            return emptyList()
        }
        val hits = notes.mapNotNull { hitFor(it, terms) }
        return hits.sortedWith(
            compareByDescending<VaultSearchHit> { it.matchedTerms.size }
                .thenByDescending { it.score }
                .thenBy { it.noteId },
        ).take(limit)
    }

    /**
     * Return the hit a note makes against the terms, or nothing.
     *
     * A term is carried by the note when it appears in its title, in one of
     * its tags or in its body; the strongest of those places decides what the
     * term is worth.
     */
    private fun hitFor(note: SearchableNote, terms: List<String>): VaultSearchHit? {
        val title = note.title.lowercase()
        val body = note.body.lowercase()
        val tags = note.tags.map { it.lowercase() }
        val matched = mutableListOf<String>()
        var score = 0
        for (term in terms) {
            val weight = when {
                title.contains(term.lowercase()) -> TITLE_WEIGHT
                tags.any { it.contains(term.lowercase()) } -> TAG_WEIGHT
                body.contains(term.lowercase()) -> BODY_WEIGHT
                else -> 0
            }
            if (weight > 0) {
                matched.add(term)
                score += weight
            }
        }
        if (matched.isEmpty()) {
            return null
        }
        return VaultSearchHit(
            noteId = note.noteId,
            title = note.title,
            path = note.path,
            score = score,
            matchedTerms = matched,
        )
    }
}
