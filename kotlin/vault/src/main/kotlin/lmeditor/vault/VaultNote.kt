package lmeditor.vault

/**
 * The frontmatter of a note: the metadata a file declares before its body.
 *
 * @property title the title the frontmatter declares, empty when it declares none
 * @property tags the tags the frontmatter declares, lower-cased and sorted
 */
data class Frontmatter(
    /** The title the frontmatter declares. */
    val title: String = "",
    /** The tags the frontmatter declares, lower-cased and sorted. */
    val tags: List<String> = emptyList(),
)

/**
 * What the markdown grammar reads off a note.
 *
 * This is the tier's own reading of a file. The note the caller receives is
 * the core model, and this is what it is built from.
 *
 * @property title the title of the note: its heading, its frontmatter title or its file name
 * @property tags the tags the note carries, lower-cased and sorted
 * @property links the wiki-link targets the note holds, in the order they were written
 * @property body the text of the note without its frontmatter
 * @property wordCount the number of words in the body
 */
data class ParsedNote(
    /** The title of the note. */
    val title: String = "",
    /** The tags the note carries, lower-cased and sorted. */
    val tags: List<String> = emptyList(),
    /** The wiki-link targets the note holds, in the order they were written. */
    val links: List<String> = emptyList(),
    /** The text of the note without its frontmatter. */
    val body: String = "",
    /** The number of words in the body. */
    val wordCount: Int = 0,
)
