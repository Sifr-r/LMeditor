package lmeditor.core

import lmeditor.core.json.Json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The contract the Python tier and this tier must both satisfy.
 *
 * These tests are the ones that would catch a drift between the two
 * implementations of the same rule, so their expectations are written in
 * terms of shared facts (wire strings, digest values) rather than in terms of
 * one implementation's internals.
 */
class CoreContractTest {

    // --- Chunk identity ---------------------------------------------------

    @Test
    fun `chunk id is the sha256 of document start end and text`() {
        val chunk = SourceChunk.create(
            documentId = "doc",
            text = "Hello",
            charStart = 0,
            charEnd = 5,
            byteStart = 0,
            byteEnd = 5,
        )
        // The exact digest the Python tier produces for the same payload.
        assertEquals(
            "ed5f5a656f33e7e095be7064d3af8b49b87dc337b0939433be9eedc66165cfa2",
            chunk.chunkId,
        )
    }

    @Test
    fun `chunk start and end aliases follow the character offsets`() {
        val chunk = SourceChunk.create("doc", "Hello", 3, 8, 3, 8)
        assertEquals(3, chunk.startChar)
        assertEquals(8, chunk.endChar)
        assertEquals(3, chunk.charOffsetStart)
        assertEquals(8, chunk.charOffsetEnd)
        assertEquals("doc", chunk.docId)
    }

    @Test
    fun `a chunk with an inverted range is refused`() {
        assertFailsWith<ValidationError> {
            SourceChunk(
                chunkId = "a".repeat(64),
                documentId = "doc",
                text = "Hello",
                charStart = 9,
                charEnd = 4,
            )
        }
    }

    @Test
    fun `a chunk id that is not a sha256 digest is refused`() {
        assertFailsWith<ValidationError> {
            SourceChunk(chunkId = "not-a-digest", documentId = "doc", text = "Hello")
        }
    }

    // --- Citation grammar -------------------------------------------------

    @Test
    fun `the primary marker form yields the bare chunk id`() {
        assertEquals(listOf("chunk_123"), Citations.extractChunkIds("Claim [#chunk_123] follows."))
    }

    @Test
    fun `the cite prefix is stripped`() {
        assertEquals(listOf("abc"), Citations.extractChunkIds("Claim [cite: abc] follows."))
    }

    @Test
    fun `the hash prefix is optional`() {
        assertEquals(listOf("abc"), Citations.extractChunkIds("Claim [abc] follows."))
    }

    @Test
    fun `a comma separated group yields every id in order`() {
        assertEquals(
            listOf("a", "b"),
            Citations.extractChunkIds("First [#a], then [#a, #b]."),
        )
    }

    @Test
    fun `ids are de-duplicated but keep their first appearance order`() {
        assertEquals(listOf("b", "a"), Citations.extractChunkIds("[#b] then [#a] then [#b]"))
    }

    @Test
    fun `a markdown link is not a citation`() {
        assertEquals(emptyList(), Citations.extractChunkIds("See [the docs](https://example.com)."))
    }

    @Test
    fun `an image is not a citation`() {
        assertEquals(emptyList(), Citations.extractChunkIds("![alt](https://example.com/a.png)"))
    }

    @Test
    fun `a group reports the span of the whole marker`() {
        val spans = Citations.iterCitationSpans("[#a, #b]")
        assertEquals(2, spans.size)
        assertEquals(0, spans[0].start)
        assertEquals(8, spans[0].end)
        assertEquals(spans[0].start, spans[1].start)
        assertEquals(spans[0].end, spans[1].end)
    }

    @Test
    fun `stripping removes the marker and keeps the sentence`() {
        // The marker is removed, not the whitespace around it: the Python tier
        // produces the same two-space result, and the operator's text is not
        // reflowed behind their back.
        assertEquals("Claim  follows.", Citations.stripCitations("Claim [#chunk_123] follows."))
    }

    @Test
    fun `a citation span is counted in code points, not UTF-16 units`() {
        // The emoji is one code point and two UTF-16 units. The Python tier
        // indexes strings by code point, so a span after an astral character
        // must not be shifted by the extra unit -- otherwise the span would
        // point one character to the right of the marker it names.
        val text = "Rocket 🚀 goes up [#astral] and lands."
        val spans = Citations.iterCitationSpans(text)
        assertEquals(1, spans.size)
        assertEquals(17, spans[0].start)
        assertEquals(26, spans[0].end)
        assertEquals(37, CodePoints.length(text))
        assertEquals(38, text.length)
    }

    @Test
    fun `a code-point slice returns the same characters the python slice does`() {
        val text = "Café 🚀 naïve"
        assertEquals("🚀", CodePoints.slice(text, 5, 6))
        assertEquals("Café", CodePoints.slice(text, 0, 4))
        assertEquals("naïve", CodePoints.slice(text, 7, 12))
    }

    // --- The operator-edit rule -------------------------------------------

    @Test
    fun `an operator edit keeps the citations and drops the verdict`() {
        val lineage = CitationLineage(
            lineageId = "lin-1",
            claimId = "claim-1",
            chunkId = "c".repeat(64),
            spanStart = 0,
            spanEnd = 5,
        )
        val claim = Claim(
            claimId = "claim-1",
            sectionId = "sec-1",
            text = "A claim.",
            charStart = 0,
            charEnd = 8,
        )
        val drafted = SectionDraft(
            sectionId = "sec-1",
            title = "Drafted",
            content = "The pipeline's words.",
            order = 3,
            citations = listOf(lineage),
            claims = listOf(claim),
            isVerified = true,
            status = SectionStatus.VERIFIED,
            revisionCount = 2,
            critique = "tighten this",
        )

        val merged = Sections.mergeOperatorText(drafted, content = "The operator's words.")

        assertEquals("The operator's words.", merged.content)
        assertEquals("Drafted", merged.title)
        assertEquals(3, merged.order)
        assertEquals(listOf(lineage), merged.citations)
        assertEquals(emptyList(), merged.claims)
        assertEquals(SectionStatus.PENDING, merged.status)
        assertEquals(false, merged.isVerified)
        assertEquals(null, merged.critique)
        assertEquals(2, merged.revisionCount)
    }

    @Test
    fun `an operator edit may rename and reorder the section`() {
        val drafted = SectionDraft(sectionId = "sec-1", title = "Drafted", content = "x", order = 1)
        val merged = Sections.mergeOperatorText(drafted, content = "y", title = "Renamed", order = 9)
        assertEquals("Renamed", merged.title)
        assertEquals(9, merged.order)
    }

    // --- Wire vocabulary ---------------------------------------------------

    @Test
    fun `every enum exposes the wire string the client already speaks`() {
        assertEquals("entailed", EntailmentStatus.ENTAILED.wire)
        assertEquals("ready_for_hitl", DocumentStatus.READY_FOR_HITL.wire)
        assertEquals("needs_revision", SectionStatus.NEEDS_REVISION.wire)
        assertEquals("guided_research", WorkflowStep.GUIDED_RESEARCH.wire)
        assertEquals("claim_verified", EventType.CLAIM_VERIFIED.wire)
        assertEquals("citation_created", EventType.CITATION_CREATED.wire)
        assertEquals("partially_supported", FactCheckVerdict.PARTIALLY_SUPPORTED.wire)
        assertEquals("pdf-header", BibliographyResolverKind.PDF_HEADER.wire)
    }

    @Test
    fun `the historical enum aliases still resolve`() {
        assertEquals(EntailmentStatus.ENTAILED, EntailmentStatus.ENTAILMENT)
        assertEquals(EntailmentStatus.CONTRADICTED, EntailmentStatus.CONTRADICTION)
        assertEquals(SectionStatus.DRAFTING, SectionStatus.DRAFTED)
        assertEquals(SectionStatus.NEEDS_REVISION, SectionStatus.FLAGGED)
    }

    @Test
    fun `an unknown wire value is refused with a typed error`() {
        assertFailsWith<ValidationError> { EntailmentStatus.fromWire("nonsense") }
    }

    @Test
    fun `a saved book section is read back from the server's own payload`() {
        // The payload is the one the Python tier's `BookSectionSaveResult`
        // produces, so the decoder is proved against the wire and not
        // against a map built by hand.
        val payload = Json.parseObject(
            """
            {"section_id":"sec_1","title":"Chapter One","content":"The operator's words.",
             "word_count":3,"char_count":20,"chunk_count":2}
            """.trimIndent(),
        )
        val result = bookSectionSaveResultFromWire(payload)
        assertEquals("sec_1", result.sectionId)
        assertEquals("Chapter One", result.title)
        assertEquals("The operator's words.", result.content)
        assertEquals(3, result.wordCount)
        assertEquals(20, result.charCount)
        assertEquals(2, result.chunkCount)
    }

    // --- Outline integrity -------------------------------------------------

    @Test
    fun `a cycle in the outline is refused`() {
        val nodeId = "n1"
        val cyclic = OutlineNode(
            nodeId = nodeId,
            title = "Cycle",
            children = listOf(OutlineNode(nodeId = nodeId, title = "Again")),
        )
        assertFailsWith<OutlineGenerationError> {
            TopicOutline(topic = "t", nodes = listOf(cyclic)).validateDag()
        }
    }

    @Test
    fun `a duplicate node id is refused`() {
        val outline = TopicOutline(
            topic = "t",
            nodes = listOf(
                OutlineNode(nodeId = "n1", title = "One"),
                OutlineNode(nodeId = "n1", title = "One again"),
            ),
        )
        assertFailsWith<OutlineGenerationError> { outline.validateDag() }
    }

    @Test
    fun `the leaves and their chunks are collected in order`() {
        val outline = TopicOutline(
            topic = "t",
            nodes = listOf(
                OutlineNode(
                    nodeId = "root",
                    title = "Root",
                    children = listOf(
                        OutlineNode(nodeId = "a", title = "A", assignedChunkIds = listOf("c1")),
                        OutlineNode(
                            nodeId = "b",
                            title = "B",
                            children = listOf(
                                OutlineNode(nodeId = "b1", title = "B1", assignedChunkIds = listOf("c2", "c1")),
                            ),
                        ),
                    ),
                ),
            ),
        )
        assertEquals(listOf("a", "b1"), outline.getLeaves().map { it.nodeId })
        assertEquals(listOf("c1", "c2"), outline.getAllAssignedChunkIds())
        assertEquals("b1", outline.findNode("b1")?.nodeId)
        assertTrue(outline.findNode("missing") == null)
    }
}
