@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The schema models must behave like the Python tier's Pydantic models.
 *
 * The Python models are declared with `str_strip_whitespace=True`, so a string
 * field never keeps the whitespace around it and the constraints are checked
 * on the trimmed value. Pydantic has two ways to make a model and they
 * differ: the constructor validates and strips, `model_copy()` does neither.
 *
 * These tests pin both halves of that contract, because the second half is
 * the one that is easy to lose: a `copy()` that trimmed would quietly differ
 * from `model_copy()` on every value the operator hands the editor.
 */
class SchemaStrippingTest {

    // --- the stripper ------------------------------------------------------

    @Test
    fun `the stripper removes the whitespace python removes`() {
        assertEquals("hello", "  hello  ".stripModelWhitespace())
        assertEquals("hello", "\thello\r\n".stripModelWhitespace())
        assertEquals("hello", "\u000Bhello\u000C".stripModelWhitespace())
    }

    @Test
    fun `the stripper removes the whitespace java leaves alone`() {
        // The no-break space, the narrow no-break space, the figure space and
        // the next-line control are whitespace in Python and not in Java, so
        // `String.trim()` is not the same function.
        assertEquals("hello", "\u00A0hello\u00A0".stripModelWhitespace())
        assertEquals("hello", "\u202Fhello\u202F".stripModelWhitespace())
        assertEquals("hello", "\u2007hello\u2007".stripModelWhitespace())
        assertEquals("hello", "\u0085hello\u0085".stripModelWhitespace())
    }

    @Test
    fun `the stripper leaves the whitespace inside the string alone`() {
        assertEquals("hello  world", "  hello  world  ".stripModelWhitespace())
        assertEquals("a b", "a b".stripModelWhitespace())
    }

    @Test
    fun `the stripper leaves a string without surrounding whitespace alone`() {
        val text = "hello"
        assertTrue(text.stripModelWhitespace() === text)
    }

    @Test
    fun `the stripper strips the items of a list`() {
        assertEquals(listOf("a", "b"), listOf(" a ", "b\t").stripModelWhitespace())
    }

    // --- construction ------------------------------------------------------

    @Test
    fun `a constructed model strips its string fields`() {
        val draft = SectionDraft(sectionId = "  sec-1  ", title = "  Results  ", content = "  Hello.  ")

        assertEquals("sec-1", draft.sectionId)
        assertEquals("Results", draft.title)
        assertEquals("Hello.", draft.content)
    }

    @Test
    fun `a constructed model strips the items of its string lists`() {
        val claim = Claim(
            claimId = "claim-1",
            sectionId = "sec-1",
            text = "  A claim.  ",
            citedChunkIds = listOf("  chunk_a  ", "chunk_b"),
        )

        assertEquals("A claim.", claim.text)
        assertEquals(listOf("chunk_a", "chunk_b"), claim.citedChunkIds)
    }

    @Test
    fun `a constructed model strips its nullable string fields`() {
        val claim = Claim(claimId = "claim-1", sectionId = "sec-1", text = "A claim.", reasoning = "  why  ")

        assertEquals("why", claim.reasoning)
    }

    @Test
    fun `the constraints are checked on the trimmed value`() {
        // A field that is nothing but whitespace is empty once trimmed, and an
        // empty field is refused.
        assertFailsWith<ValidationError> {
            SectionDraft(sectionId = "  sec-1  ", title = "   ")
        }
        assertFailsWith<ValidationError> {
            Claim(claimId = "claim-1", sectionId = "sec-1", text = "   ")
        }
    }

    @Test
    fun `a model that does not strip in python does not strip here`() {
        // `SourceChunk` has no `str_strip_whitespace` in the Python tier, so
        // its whitespace is part of the chunk's identity: the chunk id is a
        // digest of the text, and the text must be the text.
        val chunk = SourceChunk.create(
            documentId = "doc-1",
            text = "  padded  ",
            charStart = 0,
            charEnd = 10,
            byteStart = 0,
            byteEnd = 10,
        )

        assertEquals("  padded  ", chunk.text)
    }

    // --- copying -----------------------------------------------------------

    @Test
    fun `a copied model does not strip`() {
        // Pydantic's `model_copy()` does not re-validate, so it does not
        // strip either. This is the behaviour the operator's edit depends on:
        // the text the operator wrote is stored as they wrote it.
        val draft = SectionDraft(sectionId = "sec-1", title = "Results", content = "Hello.")

        val merged = draft.copy(content = "  The operator's words.  ")

        assertEquals("  The operator's words.  ", merged.content)
    }

    @Test
    fun `a copied model keeps every other field`() {
        val draft = SectionDraft(
            sectionId = "sec-1",
            title = "Results",
            content = "Hello.",
            order = 3,
            isVerified = true,
            status = SectionStatus.VERIFIED,
            revisionCount = 2,
        )

        val merged = draft.copy(content = "Changed.")

        assertEquals("sec-1", merged.sectionId)
        assertEquals("Results", merged.title)
        assertEquals(3, merged.order)
        assertTrue(merged.isVerified)
        assertEquals(SectionStatus.VERIFIED, merged.status)
        assertEquals(2, merged.revisionCount)
    }

    @Test
    fun `a copied model is still equal to a model built the same way`() {
        val one = SectionDraft(sectionId = "sec-1", title = "Results", content = "Hello.")
        val other = SectionDraft(sectionId = "sec-1", title = "Results", content = "Hello.")

        assertEquals(one, other)
        assertEquals(one.hashCode(), other.hashCode())
        assertEquals(one, one.copy())
    }

    // --- the operator's edit -----------------------------------------------

    @Test
    fun `the operator edit keeps the text the operator wrote`() {
        val drafted = SectionDraft(
            sectionId = "sec-1",
            title = "Results",
            content = "The pipeline's words.",
            isVerified = true,
            status = SectionStatus.VERIFIED,
        )

        val merged = Sections.mergeOperatorText(drafted, content = "  The operator's words.  ")

        assertEquals("  The operator's words.  ", merged.content)
    }
}
