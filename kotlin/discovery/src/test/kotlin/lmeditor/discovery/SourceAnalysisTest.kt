package lmeditor.discovery

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The deterministic lexical primitives of the discovery layer.
 *
 * Port of the deterministic assertions of
 * `backend/src/lmeditor/discovery/source_analysis.py`, whose behaviour the
 * whole discovery tier rests on.
 */
class SourceAnalysisTest {

    @Test
    fun `tokens are lower-cased words`() {
        assertEquals(
            listOf("formal", "definitions", "and", "problem"),
            SourceAnalysis.tokenize("Formal DEFINITIONS and Problem"),
        )
    }

    @Test
    fun `a token that starts with a letter or a digit is kept`() {
        assertEquals(listOf("30", "percent"), SourceAnalysis.tokenize("30 percent"))
    }

    @Test
    fun `content tokens drop the function words`() {
        val tokens = SourceAnalysis.contentTokens("The committee and the report of the year")

        assertTrue("the" !in tokens)
        assertTrue("and" !in tokens)
        assertTrue("committee" in tokens)
    }

    @Test
    fun `content tokens drop the short tokens and the bare numbers`() {
        val tokens = SourceAnalysis.contentTokens("An ox 2024 recycling")

        assertTrue("ox" !in tokens)
        assertTrue("2024" !in tokens)
        assertTrue("recycling" in tokens)
    }

    @Test
    fun `an identifier is never a content token`() {
        val digest = "a".repeat(64)
        val tokens = SourceAnalysis.contentTokens("$digest recycling")

        assertTrue(digest !in tokens)
        assertTrue("recycling" in tokens)
    }

    @Test
    fun `the most frequent term wins and ties go to the longer word`() {
        val terms = SourceAnalysis.salientTerms("collection collection kerbside", limit = 3)

        assertEquals("collection", terms[0])
    }

    @Test
    fun `salient terms are limited to the requested count`() {
        val terms = SourceAnalysis.salientTerms("alpha beta gamma delta epsilon", limit = 2)

        assertEquals(2, terms.size)
    }

    @Test
    fun `salient terms across passages are ordered by how many passages carry them`() {
        val terms = SourceAnalysis.salientTermsAcross(
            listOf("waste collection", "waste recycling", "landfill waste"),
            limit = 3,
        )

        assertEquals(listOf("waste", "collection", "landfill"), terms)
    }

    @Test
    fun `sentences are split on terminal punctuation followed by a capital`() {
        val sentences = SourceAnalysis.splitSentences(
            "Collection rose in 2023. Landfill charges rose too. But throughput doubled",
        )

        assertEquals(3, sentences.size)
        assertEquals("Collection rose in 2023.", sentences[0])
        assertEquals("Landfill charges rose too.", sentences[1])
    }

    @Test
    fun `a sentence with a quantity is a finding`() {
        val findings = SourceAnalysis.extractFindings("Collection frequency rose to weekly in 2023.")

        assertEquals(1, findings.size)
        assertTrue("Collection frequency rose to weekly in 2023." in findings)
    }

    @Test
    fun `a sentence with a finding verb is a finding`() {
        val findings = SourceAnalysis.extractFindings("The trial shows a measurable reduction in latency.")

        assertEquals(1, findings.size)
    }

    @Test
    fun `the finding verb must stand as a word even next to an accented letter`() {
        // Python's `\b` is Unicode-aware for a `str` pattern, so the accented
        // letter is part of the word and "shown" is not a word here. Java's
        // `\b` is ASCII-only unless the pattern asks for Unicode character
        // classes, so without `(?U)` this sentence would be reported as a
        // finding it is not.
        val findings = SourceAnalysis.extractFindings("déjàshown here")

        assertEquals(emptyList(), findings)
    }

    @Test
    fun `a sentence with neither is not a finding`() {
        val findings = SourceAnalysis.extractFindings("Kerbside bins stand near the kerb.")

        assertTrue(findings.isEmpty())
    }

    @Test
    fun `a markdown heading is recognised`() {
        assertEquals(
            listOf("Kerbside Collection"),
            SourceAnalysis.detectHeadings("## Kerbside Collection\nCollection frequency rose to weekly in 2023."),
        )
    }

    @Test
    fun `a numbered heading is recognised`() {
        assertEquals(
            listOf("1. Kerbside Collection"),
            SourceAnalysis.detectHeadings("1. Kerbside Collection"),
        )
    }

    @Test
    fun `a decimal number is not a heading form`() {
        // The numbered form needs whitespace after the separator, so a
        // decimal such as "1.1" is not mistaken for a heading.
        assertTrue(SourceAnalysis.detectHeadings("1.1 Kerbside Collection").isEmpty())
    }

    @Test
    fun `a chapter heading is recognised whatever its case`() {
        assertEquals(
            listOf("2 Kerbside Collection"),
            SourceAnalysis.detectHeadings("Chapter 2 Kerbside Collection"),
        )
    }

    @Test
    fun `a shouted heading is recognised`() {
        assertEquals(
            listOf("KERBSIDE COLLECTION"),
            SourceAnalysis.detectHeadings("KERBSIDE COLLECTION"),
        )
    }

    @Test
    fun `a sentence that merely starts with a capital is not a heading`() {
        assertTrue(SourceAnalysis.detectHeadings("Collection frequency rose to weekly.").isEmpty())
    }

    @Test
    fun `duplicate headings are reported once`() {
        val headings = SourceAnalysis.detectHeadings("## Kerbside Collection\n## kerbside collection")

        assertEquals(listOf("Kerbside Collection"), headings)
    }

    @Test
    fun `terms are title cased and joined`() {
        assertEquals("Waste & Recycling", SourceAnalysis.titleCaseTerms(listOf("waste", "recycling")))
    }

    @Test
    fun `a title case term keeps only the first letter up`() {
        assertEquals("Waste", SourceAnalysis.titleCaseTerms(listOf("wASTE")))
    }

    @Test
    fun `clustering is deterministic and partitions the passages`() {
        val texts = listOf(
            "waste collection kerbside recycling",
            "waste collection kerbside recycling",
            "quantum lattice photon decoy",
            "quantum lattice photon decoy",
        )

        val first = SourceAnalysis.clusterByTerms(texts, 2)
        val second = SourceAnalysis.clusterByTerms(texts, 2)

        assertEquals(first, second)
        assertEquals(2, first.size)
        assertEquals(setOf(0, 1, 2, 3), first.flatten().toSet())
    }

    @Test
    fun `a single passage is one cluster`() {
        val clusters = SourceAnalysis.clusterByTerms(listOf("waste collection kerbside"), 3)

        assertEquals(listOf(listOf(0)), clusters)
    }

    @Test
    fun `no passages means no clusters`() {
        assertTrue(SourceAnalysis.clusterByTerms(emptyList(), 2).isEmpty())
    }

    @Test
    fun `whitespace is stripped the python way`() {
        assertEquals("kerbside", SourceAnalysis.strip("  kerbside \n"))
        assertEquals("", SourceAnalysis.strip("   "))
    }

    @Test
    fun `whitespace runs collapse to a single space`() {
        assertEquals("a b c", SourceAnalysis.collapseWhitespace("  a \n\t b   c  "))
    }

    // ------------------------------------------------------------------
    // The tokenisation of non-ASCII prose
    //
    // The corpus this runs over is scientific prose, so the tokenisation has
    // to be Python's tokenisation, not an approximation of it. The expected
    // lists below are the Python module's own answers, taken from
    // `lmeditor.discovery.source_analysis` before it became a door.
    // ------------------------------------------------------------------

    @Test
    fun `non-ascii prose is tokenised the python way`() {
        val text = "Le café thermodynamique mesure déjà 30 % -- déjà-vu, αβγ and 中文."

        assertEquals(
            listOf(
                "le", "caf", "thermodynamique", "mesure", "d", "j", "30",
                "d", "j", "vu", "and",
            ),
            SourceAnalysis.tokenize(text),
        )
    }

    @Test
    fun `the content tokens of non-ascii prose are the python ones`() {
        val text = "Le café thermodynamique mesure déjà 30 % -- déjà-vu, αβγ and 中文."

        assertEquals(
            listOf("caf", "thermodynamique", "mesure"),
            SourceAnalysis.contentTokens(text),
        )
    }

    @Test
    fun `the salient terms of non-ascii prose are the python ones`() {
        val text = "Le café thermodynamique mesure déjà 30 % -- déjà-vu, αβγ and 中文."

        assertEquals(
            listOf("thermodynamique", "mesure", "caf"),
            SourceAnalysis.salientTerms(text, limit = 4),
        )
    }

    @Test
    fun `a finding verb stands as a word next to an accented letter`() {
        // Python's `\b` is Unicode-aware for a `str` pattern, so the accented
        // letter is part of the word and "yields" is not a word here.
        assertTrue(SourceAnalysis.extractFindings("déjàyields ici.").isEmpty())
        assertEquals(
            listOf("Le protocole yields déjà 30 %."),
            SourceAnalysis.extractFindings("Le protocole yields déjà 30 %."),
        )
    }

    @Test
    fun `a finding verb next to a combining mark is a finding`() {
        // Python's `\w` is "alphanumeric or underscore", and a combining
        // mark is neither: the mark does not join the word, so "shown" is a
        // whole word here. Java's `(?U)\w` counts the combining mark as part
        // of the word and would miss the finding.
        assertEquals(
            listOf("e\u0301shown ici."),
            SourceAnalysis.extractFindings("e\u0301shown ici."),
        )
    }

    @Test
    fun `headings survive a windows line ending`() {
        // Python's `str.splitlines()` treats "\r\n" as one boundary, not two.
        assertEquals(
            listOf("Kerbside Collection", "2 Landfill"),
            SourceAnalysis.detectHeadings(
                "## Kerbside Collection\r\nCharges rose.\r\n\r\nChapter 2 Landfill",
            ),
        )
    }

    @Test
    fun `a heading may be numbered with a non-ascii digit`() {
        // Python's `\d` is every Unicode decimal digit; Java's is the ASCII
        // one unless the pattern says otherwise.
        assertEquals(
            listOf("١. Kerbside Collection"),
            SourceAnalysis.detectHeadings("١. Kerbside Collection"),
        )
    }

    @Test
    fun `a non-breaking space separates a chapter heading from its title`() {
        assertEquals(
            listOf("2 Kerbside Collection"),
            SourceAnalysis.detectHeadings("Chapter 2 Kerbside Collection"),
        )
    }

    @Test
    fun `a shouted heading is not recognised outside ascii`() {
        // The shouted form is an ASCII form; a shouted French heading is not
        // one of them.
        assertTrue(SourceAnalysis.detectHeadings("ÉTUDE SUR LES DÉCHETS").isEmpty())
    }

    @Test
    fun `a negative limit counts from the end of the list`() {
        // Python's `items[:limit]` drops the last items; Kotlin's `take`
        // would refuse a negative count instead.
        assertEquals(
            listOf("alpha", "delta", "gamma"),
            SourceAnalysis.salientTerms("alpha beta gamma delta", limit = -1),
        )
        assertTrue(SourceAnalysis.salientTerms("alpha beta gamma delta", limit = -9).isEmpty())
    }
}
