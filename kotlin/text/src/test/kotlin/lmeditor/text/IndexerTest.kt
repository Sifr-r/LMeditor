package lmeditor.text

import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The retrieval rules, ported from `backend/tests/test_provenance.py` and from
 * the frozen witness in `backend/src/lmeditor/_reference/provenance_indexer.py`.
 */
class IndexerTest {

    private val langgraph = RetrievalChunk(
        "chunk-langgraph",
        "The LangGraph framework provides stateful multi-actor cyclic execution.",
    )
    private val storm = RetrievalChunk(
        "chunk-storm",
        "Stanford STORM enables automated research via multi-perspective inquiries.",
    )
    private val dspy = RetrievalChunk(
        "chunk-dspy",
        "DSPy replaces brittle prompt strings with declarative programming modules.",
    )
    private val corpus = listOf(langgraph, storm, dspy)

    @Test
    fun theSparseHalfRanksThePassageThatCarriesTheQueryTerms() {
        val ranked = Indexer.rank(
            chunks = corpus,
            query = "STORM perspective inquiries",
            mode = "bm25",
            topK = 5,
        )

        assertEquals(1, ranked.size)
        assertEquals("chunk-storm", ranked[0].chunkId)
        assertTrue(ranked[0].score > 0.0)
    }

    @Test
    fun theBm25ScoreIsTheSharedFactBothTiersAgreeOn() {
        // The three query terms each occur once in a nine-token passage, so
        // the length normalisation is one and every term contributes its
        // inverse document frequency whole. Both tiers compute
        // `log(1 + (3 - 1 + 0.5) / (1 + 0.5))` and add it three times.
        val ranked = Indexer.rank(
            chunks = corpus,
            query = "STORM perspective inquiries",
            mode = "bm25",
            topK = 5,
        )

        assertEquals(2.942487759035179, ranked[0].score)
    }

    @Test
    fun theDenseHalfScoresEveryPassageItWasGiven() {
        // A passage that matches nothing still gets a score: it is a passage
        // the reader was shown, and hiding it would make the ranking a lie
        // about what was considered.
        val ranked = Indexer.rank(
            chunks = corpus,
            query = "STORM perspective inquiries",
            mode = "dense",
            topK = 5,
        )

        assertEquals(3, ranked.size)
        assertEquals("chunk-storm", ranked[0].chunkId)
        assertEquals(0.5773502691896257, ranked[0].score)
        assertEquals(0.0, ranked[1].score)
        assertEquals(0.0, ranked[2].score)
    }

    @Test
    fun theTermFrequencyHashPlacesATokenInItsSharedBucket() {
        // The bucket of a token is the first eight hexadecimal characters of
        // its SHA-256, read as an unsigned 64-bit integer, modulo the width
        // of the vector; the sign is bit eight of the same hash. Both tiers
        // read the digest the same way, so the bucket is a shared fact.
        val ranked = Indexer.rank(
            chunks = listOf(RetrievalChunk("chunk-only", "model")),
            query = "model",
            mode = "dense",
            topK = 1,
            embeddingDim = 128,
        )

        assertEquals(1, ranked.size)
        assertEquals(1.0, ranked[0].score)
    }

    @Test
    fun theHybridHalfFusesTheTwoRankings() {
        val ranked = Indexer.rank(
            chunks = corpus,
            query = "STORM perspective inquiries",
            mode = "hybrid",
            topK = 5,
        )

        assertEquals(3, ranked.size)
        assertEquals("chunk-storm", ranked[0].chunkId)
        assertEquals(0.03278688524590164, ranked[0].score)
        assertEquals(0.016129032258064516, ranked[1].score)
        assertEquals(0.015873015873015872, ranked[2].score)
    }

    @Test
    fun theSparseHalfFindsNothingForAQueryThatMatchesNothing() {
        val ranked = Indexer.rank(
            chunks = corpus,
            query = "zebra",
            mode = "bm25",
            topK = 5,
        )

        assertTrue(ranked.isEmpty())
    }

    @Test
    fun anEmptyQueryIsRefusedByTheHybridHalf() {
        val failure = assertFailsWith<ValidationError> {
            Indexer.rank(chunks = corpus, query = "   ", mode = "hybrid")
        }

        assertEquals("query cannot be empty or whitespace", failure.message)
    }

    @Test
    fun aTopKOfZeroIsRefused() {
        val failure = assertFailsWith<ValidationError> {
            Indexer.rank(chunks = corpus, query = "storm", mode = "bm25", topK = 0)
        }

        assertEquals("top_k must be positive, got 0", failure.message)
    }

    @Test
    fun anRrfKOfZeroIsRefused() {
        val failure = assertFailsWith<ValidationError> {
            Indexer.rank(chunks = corpus, query = "storm", mode = "hybrid", rrfK = 0)
        }

        assertEquals("rrf_k must be positive, got 0", failure.message)
    }

    @Test
    fun aNegativeK1IsRefused() {
        val failure = assertFailsWith<ValidationError> {
            Indexer.rank(chunks = corpus, query = "storm", mode = "bm25", k1 = -1.0)
        }

        assertEquals("k1 must be non-negative, got -1.0", failure.message)
    }

    @Test
    fun theSuppliedVectorsAreUsedAsTheyAre() {
        // The sender normalised them already, so the tier must not normalise
        // them again: a vector of a different length is compared over the
        // part it shares with the query vector.
        val ranked = Indexer.rank(
            chunks = listOf(
                RetrievalChunk("chunk-a", "first"),
                RetrievalChunk("chunk-b", "second"),
            ),
            query = "ignored",
            mode = "dense",
            topK = 5,
            denseVectors = mapOf(
                "chunk-a" to listOf(1.0, 0.0),
                "chunk-b" to listOf(0.0, 1.0),
            ),
            queryVector = listOf(1.0, 0.0),
        )

        assertEquals(2, ranked.size)
        assertEquals("chunk-a", ranked[0].chunkId)
        assertEquals(1.0, ranked[0].score)
        assertEquals(0.0, ranked[1].score)
    }

    @Test
    fun aUnicodeLetterIsAWordCharacter() {
        // Python's `\w` is `str.isalnum()` or the underscore, so "café" and
        // "文献" are words; Java's ASCII `\w` is not Python's `\w`.
        assertEquals(
            listOf("café", "文献", "42"),
            Indexer.tokenize("Café 文献 42"),
        )
    }

    @Test
    fun aCombiningMarkIsNotAWordCharacter() {
        // Python's `str.isalnum()` is false for a combining mark, so the mark
        // ends the word rather than joining it.
        assertEquals(listOf("cafe"), Indexer.tokenize("cafe\u0301"))
    }

    @Test
    fun anUnderscoreIsAWordCharacter() {
        assertEquals(listOf("chunk_id"), Indexer.tokenize("chunk_id"))
    }
}
