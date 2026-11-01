package lmeditor.word

import lmeditor.core.BibliographicRecord
import lmeditor.core.BibliographyResolverKind
import lmeditor.core.CitationLineage
import lmeditor.core.DocumentDraft
import lmeditor.core.DocumentStatus
import lmeditor.core.EntailmentStatus
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import lmeditor.core.bibliographicRecordFromWire
import lmeditor.core.documentDraftFromWire
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The wire form of the manuscript seam.
 *
 * Two things travel over the seam for `word.manuscript`:
 *
 * * the **request**: the draft and the bibliography, as core schemas, in the
 *   core wire form `kotlin/core/WireDecode.kt` reads; and
 * * the **response**: the document the manuscript rules built, in the wire form
 *   `kotlin/word/WIRE.md` §2 pins.
 *
 * The encoders of the request live in the Python tier
 * (`lmeditor.core.schemas`), so this file pins the *reading* half here and the
 * Python side pins the *writing* half against the same literal keys. Two
 * halves pinned by one literal cannot drift apart without a test failing.
 */
class WordManuscriptWireTest {

    // ------------------------------------------------------------------
    // The request: the core wire form
    // ------------------------------------------------------------------

    @Test
    fun `the draft reads back from the core wire form`() {
        val draft = documentDraftFromWire(
            mapOf(
                "document_id" to "doc-1",
                "title" to "The Manuscript",
                "status" to "drafting",
                "sections" to listOf(
                    mapOf(
                        "section_id" to "sec-1",
                        "title" to "First",
                        "content" to "Some prose.",
                        "order" to 3,
                        "is_verified" to true,
                        "status" to "verified",
                        "revision_count" to 2,
                        "critique" to "Needs more.",
                        "cited_chunk_ids" to listOf("chunk-1"),
                        "citations" to listOf(
                            mapOf(
                                "lineage_id" to "lin-1",
                                "claim_id" to "claim-1",
                                "chunk_id" to "chunk-1",
                                "span_start" to 0,
                                "span_end" to 11,
                                "exact_quote" to "Some prose.",
                                "confidence_score" to 0.75,
                                "created_at" to "2024-01-01T00:00:00+00:00",
                            ),
                        ),
                        "claims" to listOf(
                            mapOf(
                                "claim_id" to "claim-1",
                                "section_id" to "sec-1",
                                "text" to "Some prose.",
                                "char_start" to 0,
                                "char_end" to 11,
                                "cited_chunk_ids" to listOf("chunk-1"),
                                "entailment_status" to "entailed",
                                "entailment_score" to 0.9,
                                "reasoning" to "The passage says so.",
                                "sentence_idx" to 1,
                                "premise_text" to "A premise.",
                            ),
                        ),
                    ),
                ),
            ),
        )

        assertEquals("doc-1", draft.documentId)
        assertEquals("The Manuscript", draft.title)
        assertEquals(DocumentStatus.DRAFTING, draft.status)

        val section = draft.sections.single()
        assertEquals("sec-1", section.sectionId)
        assertEquals("First", section.title)
        assertEquals("Some prose.", section.content)
        assertEquals(3, section.order)
        assertTrue(section.isVerified)
        assertEquals(SectionStatus.VERIFIED, section.status)
        assertEquals(2, section.revisionCount)
        assertEquals("Needs more.", section.critique)
        assertEquals(listOf("chunk-1"), section.citedChunkIds)

        val lineage = section.citations.single()
        assertEquals("lin-1", lineage.lineageId)
        assertEquals("claim-1", lineage.claimId)
        assertEquals("chunk-1", lineage.chunkId)
        assertEquals(0, lineage.spanStart)
        assertEquals(11, lineage.spanEnd)
        assertEquals("Some prose.", lineage.exactQuote)
        assertEquals(0.75, lineage.confidenceScore)
        assertEquals("2024-01-01T00:00:00+00:00", lineage.createdAt)

        val claim = section.claims.single()
        assertEquals("claim-1", claim.claimId)
        assertEquals("sec-1", claim.sectionId)
        assertEquals("Some prose.", claim.text)
        assertEquals(0, claim.charStart)
        assertEquals(11, claim.charEnd)
        assertEquals(listOf("chunk-1"), claim.citedChunkIds)
        assertEquals(EntailmentStatus.ENTAILED, claim.entailmentStatus)
        assertEquals(0.9, claim.entailmentScore)
        assertEquals("The passage says so.", claim.reasoning)
        assertEquals(1, claim.sentenceIdx)
        assertEquals("A premise.", claim.premiseText)
    }

    @Test
    fun `the bibliography reads back from the core wire form`() {
        val record = bibliographicRecordFromWire(
            mapOf(
                "title" to "Reef census findings",
                "authors" to listOf("Ada Lovelace", "Charles Babbage"),
                "year" to 1843,
                "publication_date" to "1843-01-01",
                "container_title" to "The Journal",
                "volume" to "12",
                "issue" to "3",
                "pages" to "100-120",
                "doi" to "10.1234/reef.5678",
                "url" to "https://example.org/reef",
                "type" to "journal-article",
                "resolver" to "crossref",
            ),
        )

        assertEquals("Reef census findings", record.title)
        assertEquals(listOf("Ada Lovelace", "Charles Babbage"), record.authors)
        assertEquals(1843, record.year)
        assertEquals("1843-01-01", record.publicationDate)
        assertEquals("The Journal", record.containerTitle)
        assertEquals("12", record.volume)
        assertEquals("3", record.issue)
        assertEquals("100-120", record.pages)
        assertEquals("10.1234/reef.5678", record.doi)
        assertEquals("https://example.org/reef", record.url)
        assertEquals("journal-article", record.type)
        assertEquals(BibliographyResolverKind.CROSSREF, record.resolver)
    }

    @Test
    fun `an absent optional field reads as null and an absent enum reads as its default`() {
        val record = bibliographicRecordFromWire(mapOf("title" to "Only a title"))

        assertNull(record.year)
        assertNull(record.doi)
        assertNull(record.containerTitle)
        assertEquals(BibliographyResolverKind.NONE, record.resolver)

        val draft = documentDraftFromWire(mapOf("document_id" to "doc-1", "title" to "T"))
        assertEquals(DocumentStatus.DRAFTING, draft.status)
        assertTrue(draft.sections.isEmpty())
    }

    // ------------------------------------------------------------------
    // The response: the document wire form
    // ------------------------------------------------------------------

    @Test
    fun `the manuscript answers with the document in the wire form the contract pins`() {
        val document = WordManuscript.buildManuscriptDocument(
            document = DocumentDraft(
                documentId = "doc-1",
                title = "The Manuscript",
                sections = listOf(
                    SectionDraft(
                        sectionId = "sec-1",
                        title = "First",
                        content = "A cited fact [#chunk-1].",
                        order = 0,
                        citations = listOf(
                            CitationLineage(
                                lineageId = "lin-1",
                                claimId = "claim-1",
                                chunkId = "chunk-1",
                                spanStart = 0,
                                spanEnd = 12,
                                exactQuote = "The cited passage.",
                            ),
                        ),
                    ),
                ),
            ),
            bibliography = listOf(
                BibliographicRecord(
                    title = "Reef census findings",
                    authors = listOf("Ada Lovelace"),
                    year = 1843,
                ),
            ),
        )

        val wire = wordDocumentToWire(document)

        // The §2.1 keys, always present.
        assertEquals(
            listOf(
                "blocks",
                "properties",
                "footnotes",
                "endnotes",
                "comments",
                "headers",
                "footers",
                "sections",
                "styles",
                "media",
                "parts",
            ),
            wire.keys.toList(),
        )

        // The document the seam answers with is the one the tier built: the
        // title, the section heading, the reference list and the footnote the
        // cited passage earns.
        assertEquals("The Manuscript", wire["properties"]?.let { (it as Map<*, *>)["title"] })
        val blocks = wire["blocks"] as List<*>
        assertEquals(
            listOf("title", "heading", "paragraph", "heading", "paragraph"),
            blocks.map { (it as Map<*, *>)["kind"] },
        )
        val footnotes = wire["footnotes"] as List<*>
        assertEquals(1, footnotes.size)
        assertEquals(
            "The cited passage. [chunk-1]",
            (footnotes.single() as Map<*, *>)["text"],
        )
    }

    @Test
    fun `the document wire form round-trips back into the same document`() {
        val document = WordManuscript.buildManuscriptDocument(
            document = DocumentDraft(
                documentId = "doc-1",
                title = "The Manuscript",
                sections = listOf(
                    SectionDraft(
                        sectionId = "sec-1",
                        title = "First",
                        content = "A cited fact [#chunk-1].",
                        order = 0,
                        citations = listOf(
                            CitationLineage(
                                lineageId = "lin-1",
                                claimId = "claim-1",
                                chunkId = "chunk-1",
                                spanStart = 0,
                                spanEnd = 12,
                                exactQuote = "The cited passage.",
                            ),
                        ),
                    ),
                ),
            ),
        )

        val rebuilt = wordDocumentFromWire(wordDocumentToWire(document))

        assertEquals(document.blocks.size, rebuilt.blocks.size)
        assertEquals(document.blocks.map { it.text }, rebuilt.blocks.map { it.text })
        assertEquals(document.footnotes.map { it.text }, rebuilt.footnotes.map { it.text })
        assertEquals(document.properties.title, rebuilt.properties.title)
    }

    @Test
    fun `the manuscript builder never mutates the draft it was given`() {
        val draft = DocumentDraft(
            documentId = "doc-1",
            title = "The Manuscript",
            sections = listOf(
                SectionDraft(sectionId = "sec-2", title = "Second", content = "b", order = 2),
                SectionDraft(sectionId = "sec-1", title = "First", content = "a", order = 1),
            ),
        )
        val sections = draft.sections
        val order = sections.map { it.sectionId }

        WordManuscript.buildManuscriptDocument(draft)

        assertEquals(order, draft.sections.map { it.sectionId })
        assertEquals(sections, draft.sections)
    }

    @Test
    fun `a claim travels as its enum wire string and never as its name`() {
        // The Python tier writes the enum's wire string; the tier reads it
        // back into the enum entry. The string is the contract, so it is
        // pinned here as well.
        assertEquals("entailed", EntailmentStatus.ENTAILED.wire)
        assertEquals("neutral", EntailmentStatus.NEUTRAL.wire)
        assertEquals("verified", SectionStatus.VERIFIED.wire)
        assertEquals("needs_revision", SectionStatus.NEEDS_REVISION.wire)
        assertEquals("pdf-header", BibliographyResolverKind.PDF_HEADER.wire)
        assertEquals("none", BibliographyResolverKind.NONE.wire)

        // The historical aliases are aliases, not entries: they are the same
        // value under a second name.
        assertEquals(SectionStatus.DRAFTING, SectionStatus.DRAFTED)
        assertEquals(SectionStatus.NEEDS_REVISION, SectionStatus.FLAGGED)
        assertEquals(EntailmentStatus.ENTAILED, EntailmentStatus.ENTAILMENT)
        assertEquals(EntailmentStatus.CONTRADICTED, EntailmentStatus.CONTRADICTION)
    }
}
