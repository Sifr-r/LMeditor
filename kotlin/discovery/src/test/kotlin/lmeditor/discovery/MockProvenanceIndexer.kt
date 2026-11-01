package lmeditor.discovery

import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SourceChunk

/**
 * A provenance indexer that answers from a fixed list of chunks.
 *
 * The double mirrors `MockProvenanceIndexer` in
 * `backend/tests/test_discovery.py`: it ranks the chunks by how many of the
 * query's words they contain and can be told to fail.
 */
class MockProvenanceIndexer(
    private val chunks: List<SourceChunk> = emptyList(),
    private val failQueries: Boolean = false,
) : ProvenanceIndexer {
    private val chunkMap = chunks.associateBy { it.chunkId }

    /** The queries the loop asked, in order. */
    val recordedQueries: MutableList<String> = mutableListOf()

    override fun query(query: String, topK: Int): List<SourceChunk> {
        recordedQueries.add(query)
        if (failQueries) {
            throw IllegalStateException("Database connection timed out during vector search")
        }
        if (chunks.isEmpty()) return emptyList()

        val queryWords = query.lowercase()
            .split(Regex("\\s+"))
            .filter { it.isNotEmpty() }
            .toSet()

        val scored = chunks.map { chunk ->
            val lowered = chunk.text.lowercase()
            queryWords.count { it in lowered } to chunk
        }
        return scored.sortedByDescending { it.first }.take(topK).map { it.second }
    }

    override fun getChunk(chunkId: String): SourceChunk? = chunkMap[chunkId]

    override fun hasChunk(chunkId: String): Boolean = chunkMap.containsKey(chunkId)

    override fun getAllChunkIds(): List<String> = chunkMap.keys.toList()
}
