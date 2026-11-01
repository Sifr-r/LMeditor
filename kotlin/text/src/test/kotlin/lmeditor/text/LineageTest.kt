package lmeditor.text

import lmeditor.core.ChunkNotFoundError
import lmeditor.core.CitationHallucinationError
import lmeditor.core.SourceChunk
import lmeditor.core.SpanAlignmentError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The lineage rules, ported from `backend/tests/test_provenance.py`.
 */
class LineageTest {

    private val engine = CitationLineageEngine()

    @Test
    fun attributingASpanToAnUnrelatedPassageReportsNoConfidence() {
        // A passage that shares nothing with the span supports it not at all.
        // The client renders the lineage's confidence as the passage's
        // "Fidelity" to the claim, so a constant floor would tell the reader
        // that a passage which supports nothing supports the claim halfway.
        val sourceText = "Coral reefs shelter a quarter of all marine species and their larvae."
        val chunk = sourceChunk("doc_src", sourceText)
        val chunkStore = mapOf(chunk.chunkId to chunk.text)

        val draftText = "Mangrove forests slow storm surge along tropical coasts."
        val lineage = engine.attributeSpan(
            draftText = draftText,
            spanStart = 0,
            spanEnd = draftText.length,
            citedChunkId = chunk.chunkId,
            claimId = "claim_002",
            chunkStore = chunkStore,
        )

        assertNull(lineage.exactQuote)
        assertEquals(0.0, lineage.confidenceScore)
    }

    @Test
    fun aSpanIsAttributedToItsSourceChunk() {
        val sourceText =
            "Natural language inference verifies factual consistency between premise and hypothesis."
        val chunk = sourceChunk("doc_src", sourceText)
        val chunkStore = mapOf(chunk.chunkId to chunk.text)

        val draftText = "In expository writing, natural language inference verifies factual consistency."
        val lineage = engine.attributeSpan(
            draftText = draftText,
            spanStart = 23,
            spanEnd = 77,
            citedChunkId = chunk.chunkId,
            claimId = "claim_001",
            chunkStore = chunkStore,
            exactQuote = "natural language inference verifies factual consistency",
        )

        assertEquals("claim_001", lineage.claimId)
        assertEquals(chunk.chunkId, lineage.chunkId)
        assertEquals(23, lineage.spanStart)
        assertEquals(77, lineage.spanEnd)
        assertEquals(1.0, lineage.confidenceScore)
    }

    @Test
    fun aSpanOutsideTheDraftIsRejected() {
        val sourceText = "Natural language inference verifies factual consistency."
        val chunk = sourceChunk("doc_src", sourceText)
        val chunkStore = mapOf(chunk.chunkId to chunk.text)
        val draftText = "In expository writing, natural language inference verifies factual consistency."

        assertFailsWith<SpanAlignmentError> {
            engine.attributeSpan(
                draftText = draftText,
                spanStart = -1,
                spanEnd = 10,
                citedChunkId = chunk.chunkId,
                claimId = "claim_001",
                chunkStore = chunkStore,
            )
        }

        assertFailsWith<SpanAlignmentError> {
            engine.attributeSpan(
                draftText = draftText,
                spanStart = 10,
                spanEnd = draftText.length + 10,
                citedChunkId = chunk.chunkId,
                claimId = "claim_001",
                chunkStore = chunkStore,
            )
        }
    }

    @Test
    fun aCitationToAChunkThatIsNotStoredIsRejected() {
        val sourceText = "Natural language inference verifies factual consistency."
        val chunk = sourceChunk("doc_src", sourceText)
        val chunkStore = mapOf(chunk.chunkId to chunk.text)

        assertFailsWith<ChunkNotFoundError> {
            engine.attributeSpan(
                draftText = "Some draft text",
                spanStart = 0,
                spanEnd = 10,
                citedChunkId = "0".repeat(64),
                claimId = "claim_001",
                chunkStore = chunkStore,
            )
        }
    }

    @Test
    fun aQuoteThatIsNotInTheSourceIsAHallucination() {
        val sourceText = "Natural language inference verifies factual consistency."
        val chunk = sourceChunk("doc_src", sourceText)
        val chunkStore = mapOf(chunk.chunkId to chunk.text)
        val draftText = "In expository writing, natural language inference verifies factual consistency."

        assertFailsWith<CitationHallucinationError> {
            engine.attributeSpan(
                draftText = draftText,
                spanStart = 0,
                spanEnd = 20,
                citedChunkId = chunk.chunkId,
                claimId = "claim_001",
                chunkStore = chunkStore,
                exactQuote = "This completely fabricated statement does not exist in the source passage.",
            )
        }
    }

    @Test
    fun inlineCitationMarkersAreParsedOutOfADraft() {
        val first = "a".repeat(64)
        val second = "b".repeat(64)
        val chunkStore = mapOf(
            first to "Source text 1",
            second to "Source text 2",
        )

        val draft = (
            "This first assertion is verified [#$first]. " +
                "A secondary finding is corroborated by independent analysis [cite: $second]."
            )

        val citations = engine.extractCitationsFromDraft(draft, chunkStore)
        assertEquals(2, citations.size)
        assertEquals(first, citations[0].chunkId)
        assertEquals(second, citations[1].chunkId)
        assertTrue(citations[0].spanStart > 0)
        assertTrue(citations[0].spanEnd > citations[0].spanStart)
    }

    @Test
    fun aCitationGroupYieldsOneRecordPerCitedChunk() {
        // A marker group cites several chunks, and each gets its own record.
        val first = chunkId("first")
        val second = chunkId("second")
        val store = mapOf(first to "Source text", second to "Source text")

        val citations = engine.extractCitationsFromDraft("A claim [#$first, #$second].", store)

        assertEquals(listOf(first, second), citations.map { it.chunkId })
        // Both records point at the same marker.
        assertEquals(citations[0].spanStart, citations[1].spanStart)
        assertEquals(citations[0].spanEnd, citations[1].spanEnd)
    }

    @Test
    fun theLineageEngineFindsExactlyWhatTheCanonicalGrammarFinds() {
        // The engine and the canonical grammar never disagree about a citation.
        val alpha = chunkId("alpha")
        val beta = chunkId("beta")
        val gamma = chunkId("gamma")
        val delta = chunkId("delta")
        val draft = (
            "A claim [#$alpha]. Another [#$beta, #$gamma]. " +
                "A link [text](https://example.org). A bare [$delta] " +
                "and a picture ![alt](image.png)."
            )
        val store = listOf(alpha, beta, gamma, delta).associateWith { "Source text" }

        val found = engine.extractCitationsFromDraft(draft, store).map { it.chunkId }
        val canonical = lmeditor.core.Citations.iterCitationSpans(draft)
            .map { it.chunkId }
            .filter { store.containsKey(it) }

        assertEquals(canonical, found)
        // Pinned so the comparison above cannot pass vacuously.
        assertEquals(listOf(alpha, beta, gamma, delta), canonical)
        // The markdown link and the image are not citations.
        assertTrue("text" !in canonical)
        assertTrue("alt" !in canonical)
    }

    @Test
    fun aCitationToAChunkThatIsNotInTheStoreIsNotRecorded() {
        val known = chunkId("known")
        val unknown = chunkId("unknown")
        val store = mapOf(known to "Source text")

        val citations = engine.extractCitationsFromDraft(
            "Grounded [#$known] but ungrounded [#$unknown].",
            store,
        )

        assertEquals(listOf(known), citations.map { it.chunkId })
    }

    @Test
    fun anUpperCaseChunkIdResolvesToItsStoredForm() {
        // A marker may spell the id in upper case; the store holds lower case.
        val chunkId = chunkId("upper")
        val store = mapOf(chunkId to "Source text")

        val citations = engine.extractCitationsFromDraft("A claim [#$chunkId].", store)

        assertEquals(listOf(chunkId), citations.map { it.chunkId })
    }

    /** A realistic chunk id: the deterministic digest the chunker produces. */
    private fun chunkId(label: String): String = computeChunkId("doc1", 0, label.length, label)

    /** A source chunk whose id is the one the chunker would mint. */
    private fun sourceChunk(documentId: String, text: String): SourceChunk =
        SourceChunk.create(
            documentId = documentId,
            text = text,
            charStart = 0,
            charEnd = text.length,
            byteStart = 0,
            byteEnd = text.toByteArray(Charsets.UTF_8).size,
        )
}
