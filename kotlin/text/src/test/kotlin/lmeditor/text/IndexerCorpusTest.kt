package lmeditor.text

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The corpus the tier holds, and the name it holds it under.
 *
 * A request about a corpus either carries the corpus with it or names the
 * corpus the tier already holds. These tests are about that second kind of
 * request: the ranking it gives, the name it is answered under, and what
 * happens when the corpus changes.
 */
class IndexerCorpusTest {

    private val mangrove = RetrievalChunk(
        "a".repeat(64),
        "Mangrove forests slow storm surge.",
    )
    private val reef = RetrievalChunk(
        "b".repeat(64),
        "reef coral bleaching.",
    )
    private val corpus = listOf(mangrove, reef)

    private fun descriptor(name: String): RetrievalChunk =
        RetrievalChunk(CORPUS_DESCRIPTOR_CHUNK_ID, name)

    @Test
    fun aNamedCorpusIsAnsweredFromTheIndexTheTierBuilt() {
        val name = "corpus-name"
        val first = Indexer.rank(
            chunks = listOf(descriptor(name)) + corpus,
            query = "mangrove forests",
            mode = "bm25",
            topK = 5,
        )
        val second = Indexer.rank(
            chunks = listOf(descriptor(name)),
            query = "mangrove forests",
            mode = "bm25",
            topK = 5,
        )

        assertEquals(first, second)
        assertEquals("a".repeat(64), first[0].chunkId)
    }

    @Test
    fun aNamedCorpusIsAnsweredWithTheScoresTheRuleHasAlwaysGiven() {
        // The scores are the shared fact: a corpus that travels with the
        // request and a corpus the tier holds are the same corpus, and the
        // rule gives it the same answer.
        val carried = Indexer.rank(
            chunks = corpus,
            query = "STORM perspective inquiries",
            mode = "bm25",
            topK = 5,
        )
        val name = "corpus-scores"
        val held = Indexer.rank(
            chunks = listOf(descriptor(name)) + corpus,
            query = "STORM perspective inquiries",
            mode = "bm25",
            topK = 5,
        )
        val named = Indexer.rank(
            chunks = listOf(descriptor(name)),
            query = "STORM perspective inquiries",
            mode = "bm25",
            topK = 5,
        )

        assertEquals(carried, held)
        assertEquals(carried, named)
    }

    @Test
    fun aCorpusTheTierDoesNotHoldIsReportedRatherThanAnswered() {
        val failure = assertFailsWith<CorpusNotResidentError> {
            Indexer.rank(
                chunks = listOf(descriptor("a-corpus-the-tier-never-held")),
                query = "mangrove forests",
                mode = "bm25",
                topK = 5,
            )
        }

        assertEquals("a-corpus-the-tier-never-held", failure.corpusKey)
    }

    @Test
    fun aCorpusThatChangedIsAnsweredFromTheCorpusThatReplacedIt() {
        // The same name, a different corpus: the tier holds what it was
        // given last, so the answer is about the corpus that is there now.
        val name = "corpus-that-changed"
        Indexer.rank(
            chunks = listOf(descriptor(name)) + corpus,
            query = "mangrove forests",
            mode = "bm25",
            topK = 5,
        )
        val changed = listOf(reef, mangrove)
        Indexer.rank(
            chunks = listOf(descriptor(name)) + changed,
            query = "reef coral",
            mode = "bm25",
            topK = 5,
        )
        val ranked = Indexer.rank(
            chunks = listOf(descriptor(name)),
            query = "reef coral",
            mode = "bm25",
            topK = 5,
        )

        assertEquals("b".repeat(64), ranked[0].chunkId)
        assertTrue(ranked[0].score > 0.0)
    }

    @Test
    fun aCorpusIsNamedByItsDigest() {
        // The digest is the name of the corpus, and the Python door computes
        // the same digest over the same passages. The two sides have to agree
        // on it, so the value is pinned here and there.
        assertEquals(
            "b3ae327aac050fb6fddb2319dadc8160bb480a5c5edd951612a8f71a4dde8bfb",
            Indexer.corpusDigest(listOf(mangrove)),
        )
        assertEquals(
            "4b84bfb59c6ebbcdd75619432fa092f40af53eb2e08d55743bc37982d066b8e9",
            Indexer.corpusDigest(corpus),
        )
    }

    @Test
    fun aCorpusThatTravelsWithTheRequestIsIndexedOnceToo() {
        // The first request builds the tables; the second finds them.
        val first = Indexer.rank(
            chunks = corpus,
            query = "mangrove forests",
            mode = "hybrid",
            topK = 5,
        )
        val second = Indexer.rank(
            chunks = corpus,
            query = "mangrove forests",
            mode = "hybrid",
            topK = 5,
        )

        assertEquals(first, second)
        assertTrue(first.isNotEmpty())
    }

    @Test
    fun aCorpusWithNoPassagesAnswersNothing() {
        val ranked = Indexer.rank(
            chunks = emptyList(),
            query = "mangrove forests",
            mode = "bm25",
            topK = 5,
        )

        assertEquals(emptyList(), ranked)
    }
}
