package lmeditor.vault

import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * The markdown grammar of a vault: frontmatter, wiki-links and tags.
 */
class MarkdownTest {

    /** The title of a note is its first heading. */
    @Test
    fun theTitleIsTheFirstHeading() {
        val content = "# Note Title\n\nthe body\n\n## Second heading\n"
        assertEquals("Note Title", Markdown.headingTitle(content))
    }

    /** A line that begins with a tag is not a heading. */
    @Test
    fun aLineThatBeginsWithATagIsNotAHeading() {
        assertEquals("", Markdown.headingTitle("#tag\n\nthe body\n"))
    }

    /** The title the frontmatter declares is read. */
    @Test
    fun theTitleTheFrontmatterDeclaresIsRead() {
        val content = "---\ntitle: From Frontmatter\n---\n\n# From Heading\n\nthe body\n"
        assertEquals("From Frontmatter", Markdown.frontmatter(content).title)
        assertEquals("From Heading", Markdown.headingTitle(content))
    }

    /** The tags of the frontmatter are read, inline and as a list. */
    @Test
    fun theTagsOfTheFrontmatterAreRead() {
        val inline = "---\ntags: [Alpha, beta]\n---\n\nthe body\n"
        assertEquals(listOf("alpha", "beta"), Markdown.frontmatter(inline).tags)
        val list = "---\ntags:\n  - one\n  - Two\n---\n\nthe body\n"
        assertEquals(listOf("one", "two"), Markdown.frontmatter(list).tags)
    }

    /** The wiki-links of a note are read, with their alias and their heading. */
    @Test
    fun theWikiLinksOfANoteAreRead() {
        val content = "# Links\n\nSee [[One]], [[Two|alias]] and [[Three#Heading]].\n"
        assertEquals(listOf("One", "Two", "Three"), Markdown.links(content))
    }

    /** A heading is not a tag. */
    @Test
    fun aHeadingIsNotATag() {
        assertEquals(emptyList(), Markdown.bodyTags("# Title\n\n## Another\n"))
    }

    /** A URL fragment is not a tag. */
    @Test
    fun aUrlFragmentIsNotATag() {
        val content = "See http://example.com/page#section and https://example.com/#fragment.\n"
        assertEquals(emptyList(), Markdown.bodyTags(content))
    }

    /** A tag inside a word is not a tag. */
    @Test
    fun aTagInsideAWordIsNotATag() {
        assertEquals(emptyList(), Markdown.bodyTags("The C# language and a#b are not tags.\n"))
    }

    /** A tag is lower-cased. */
    @Test
    fun aTagIsLowerCased() {
        assertEquals(listOf("idea", "project/topic"), Markdown.bodyTags("#Idea and #Project/topic\n"))
    }

    /** The tags of a note are the frontmatter's and the body's. */
    @Test
    fun theTagsOfANoteAreTheFrontmattersAndTheBodys() {
        val content = "---\ntags: [one]\n---\n\n# A Heading\n\n#two\n"
        assertEquals(listOf("one", "two"), Markdown.tags(content))
    }

    /** The word count counts the words of the body. */
    @Test
    fun theWordCountCountsTheWordsOfTheBody() {
        assertEquals(4, Markdown.wordCount("Note Title\n\nthe body\n"))
        // The heading marker is a token of its own, as it is in the body.
        assertEquals(5, Markdown.wordCount("# Note Title\n\nthe body\n"))
    }

    /** A link that points at a note is rewritten, and keeps its alias and heading. */
    @Test
    fun aLinkThatPointsAtANoteIsRewritten() {
        val content = "See [[Old]] and [[Old|alias]] and [[Old#Heading]] and [[Other]].\n"
        val rewritten = Markdown.rewriteLinks(content, "New") { it == "Old" }
        assertEquals("See [[New]] and [[New|alias]] and [[New#Heading]] and [[Other]].\n", rewritten)
    }

    /** The links that point at a note are counted, one link at a time. */
    @Test
    fun theLinksThatPointAtANoteAreCounted() {
        val content = "See [[Old]] and [[Old|alias]] and [[Other]].\n"
        assertEquals(2, Markdown.countLinks(content) { it == "Old" })
    }
}
