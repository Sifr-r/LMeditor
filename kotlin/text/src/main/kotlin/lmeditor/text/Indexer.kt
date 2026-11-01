package lmeditor.text

import lmeditor.core.CodePoints
import lmeditor.core.Hashing
import lmeditor.core.ProvenanceError
import lmeditor.core.ValidationError
import lmeditor.core.stripModelWhitespace
import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.nio.charset.StandardCharsets

/**
 * A passage the retrieval rule ranks: the id it is known by and the text it
 * carries.
 *
 * The rule needs the text and the id and nothing else, so the seam carries
 * these two rather than whole source chunks.
 */
data class RetrievalChunk(val chunkId: String, val text: String)

/**
 * One ranked passage and the score the rule gave it.
 */
data class RankedChunk(val chunkId: String, val score: Double)

/**
 * The id of the chunk that names a corpus.
 *
 * A chunk id is the SHA-256 digest of a passage, and the digest of a
 * non-empty payload is never all zeros, so this id names no passage. It
 * names the corpus, and the name of that corpus is the text it carries.
 *
 * A request that begins with such a chunk is a request about a corpus:
 * the chunks that follow it are the corpus, or -- when it carries none --
 * the corpus the tier already holds under the name it names.
 */
val CORPUS_DESCRIPTOR_CHUNK_ID: String = "0".repeat(64)

/**
 * The corpus a request named is not one the tier holds.
 *
 * The tier holds the corpora it has indexed until they are pushed out of its
 * cache. A request that names a corpus the tier does not hold is told so,
 * rather than answered from a corpus that is not the one it asked about.
 */
class CorpusNotResidentError(
    val corpusKey: String,
    message: String? = null,
) : ProvenanceError(
    message ?: "CorpusNotResidentError: the corpus '$corpusKey' is not held by the tier.",
    mapOf("corpus_key" to corpusKey),
)

/**
 * The index of one corpus: the terms its passages hold, and their lengths.
 *
 * The tables are built when the corpus arrives and are read on every query.
 * Building them is the expensive half of the rule -- every passage is
 * tokenised, and the dense half hashes every one of its tokens -- and it is
 * paid once per corpus rather than once per query.
 */
private class CorpusIndex(
    /** The ids of the passages, in the order the corpus gave them. */
    val chunkIds: List<String>,
    /** The text of every passage, for the dense half's hash vectors. */
    val texts: Map<String, String>,
    /** The number of tokens of every passage. */
    val docLen: Map<String, Int>,
    /** The term frequencies of every passage. */
    val docTermFreqs: Map<String, Map<String, Int>>,
    /** The inverse document frequency of every term. */
    val idf: Map<String, Double>,
    /** The mean length of a passage, in tokens. */
    val avgDocLen: Double,
) {
    /** The hash vectors of the passages, by width, computed on first use. */
    private val hashVectors: MutableMap<Int, Map<String, List<Double>>> = HashMap()

    /**
     * The hash vectors of the passages, [embeddingDim] wide.
     *
     * The vector of a passage is the same for every query, so it is computed
     * once per corpus rather than once per query.
     */
    fun hashVectors(embeddingDim: Int): Map<String, List<Double>> =
        hashVectors.getOrPut(embeddingDim) {
            val vectors = LinkedHashMap<String, List<Double>>()
            for (chunkId in chunkIds) {
                vectors[chunkId] = fallbackEmbed(texts[chunkId] ?: "", embeddingDim)
            }
            vectors
        }
}

/**
 * The retrieval rule: BM25 Okapi, a dense cosine index, and their fusion.
 *
 * Port of `backend/src/lmeditor/provenance/indexer.py`. The Python module this
 * replaces is a *door*: it keeps the API its callers have and answers with the
 * answers computed here.
 *
 * Three rules live in this file, and they are the three halves of the original
 * module:
 *
 * - **BM25 Okapi.** A document is scored by its term frequencies, damped by
 *   its length and weighted by the inverse document frequency of the term.
 * - **The dense index.** A passage and a query are vectors, and the score is
 *   their cosine similarity. The vectors come from a model when the Python
 *   tier supplies them, and from the deterministic term-frequency hash when it
 *   does not.
 * - **Reciprocal rank fusion.** The two ranked lists are merged by rank, not
 *   by score, because the two scores are not on the same scale.
 *
 * The arithmetic is deliberately written the way the Python code writes it,
 * operation by operation: the summations are in the same order, the divisions
 * are the same divisions, and the ties are broken the same way. A score that
 * is *nearly* the same is not the same score.
 */
object Indexer {

    /**
     * The name the Python tier reports when the dense half is using the
     * deterministic term-frequency hash rather than a model.
     */
    const val FALLBACK_EMBEDDER_NAME: String = "tf-hash-fallback"

    /** The default number of results a search returns. */
    const val DEFAULT_TOP_K: Int = 10

    /** The default constant of the reciprocal rank fusion. */
    const val DEFAULT_RRF_K: Int = 60

    /** The default weight of the dense half in the fusion. */
    const val DEFAULT_ALPHA: Double = 0.5

    /** The default term-frequency saturation of BM25. */
    const val DEFAULT_K1: Double = 1.5

    /** The default length normalisation of BM25. */
    const val DEFAULT_B: Double = 0.75

    /** The default width of the deterministic hash vector. */
    const val DEFAULT_EMBEDDING_DIM: Int = 128

    /** The retrieval modes the rule knows. */
    val MODES: List<String> = listOf("bm25", "dense", "hybrid")

    /**
     * The corpora the tier holds, keyed by the name each was given.
     *
     * The tier is one long-lived process, so a corpus it has indexed does
     * not have to be indexed again: the tables built when the corpus
     * arrived answer the queries that name it.
     *
     * The key is the digest of the corpus, so a corpus that changed is a
     * different entry, and an entry is never answered from the tables of
     * the corpus it replaced. A request that carries a corpus also carries
     * the name it is held under, and the tier holds it under that name as
     * well, so the sender's name and the tier's own digest are one key
     * space.
     */
    private val resident: MutableMap<String, CorpusIndex> = LinkedHashMap()

    /** How many corpora the tier holds at once. */
    private const val RESIDENT_LIMIT: Int = 4

    /**
     * Rank [chunks] for [query].
     *
     * @param mode one of [MODES]: the sparse half, the dense half, or their
     *   reciprocal rank fusion.
     * @param denseVectors the dense vectors of the corpus, already normalised
     *   by the sender, or `null` when the tier is to compute its own
     *   term-frequency hash vectors.
     * @param queryVector the dense vector of the query, already normalised by
     *   the sender, or `null` when the tier is to compute its own.
     */
    fun rank(
        chunks: List<RetrievalChunk>,
        query: String,
        mode: String = "hybrid",
        topK: Int = DEFAULT_TOP_K,
        rrfK: Int = DEFAULT_RRF_K,
        alpha: Double = DEFAULT_ALPHA,
        k1: Double = DEFAULT_K1,
        b: Double = DEFAULT_B,
        embeddingDim: Int = DEFAULT_EMBEDDING_DIM,
        denseVectors: Map<String, List<Double>>? = null,
        queryVector: List<Double>? = null,
    ): List<RankedChunk> {
        if (k1 < 0.0) {
            throw ValidationError("k1 must be non-negative, got $k1")
        }
        if (b < 0.0 || b > 1.0) {
            throw ValidationError("b must be between 0.0 and 1.0 inclusive, got $b")
        }
        if (embeddingDim <= 0) {
            throw ValidationError("embedding_dim must be positive, got $embeddingDim")
        }
        return when (mode) {
            "bm25" -> bm25Search(corpusOf(chunks), query, topK, k1, b)
            "dense" -> denseSearch(
                corpus = corpusOf(chunks),
                query = query,
                topK = topK,
                embeddingDim = embeddingDim,
                denseVectors = denseVectors,
                queryVector = queryVector,
            )
            "hybrid" -> hybridSearch(
                corpus = corpusOf(chunks),
                query = query,
                topK = topK,
                rrfK = rrfK,
                alpha = alpha,
                k1 = k1,
                b = b,
                embeddingDim = embeddingDim,
                denseVectors = denseVectors,
                queryVector = queryVector,
            )
            else -> throw ValidationError(
                "mode must be one of ${MODES.joinToString(", ")}, got '$mode'",
                field = "mode",
            )
        }
    }

    // --- the resident corpus -----------------------------------------------

    /**
     * The index of the corpus a request is about.
     *
     * A request either carries the corpus with it, or names a corpus the
     * tier already holds. A request that carries a corpus *and* names it is
     * the request that put it there.
     */
    private fun corpusOf(chunks: List<RetrievalChunk>): CorpusIndex {
        val descriptor = chunks.firstOrNull()
        if (descriptor != null && descriptor.chunkId == CORPUS_DESCRIPTOR_CHUNK_ID) {
            val key = descriptor.text
            val corpus = chunks.drop(1)
            if (corpus.isEmpty()) {
                val held = resident[key]
                if (held == null) throw CorpusNotResidentError(key)
                return held
            }
            val index = buildIndex(corpus)
            hold(key, index)
            return index
        }
        // The corpus travels with the request. It is held under its own
        // digest, so the next request about the same corpus finds the tables
        // already built.
        val index = buildIndex(chunks)
        hold(corpusDigest(chunks), index)
        return index
    }

    /**
     * The tables of a corpus, built once.
     *
     * The arithmetic is the arithmetic the rule has always had: the term
     * frequencies of every passage, the number of documents a term is in,
     * and the inverse document frequency that follows from them.
     */
    private fun buildIndex(chunks: List<RetrievalChunk>): CorpusIndex {
        val chunkIds = ArrayList<String>(chunks.size)
        val texts = LinkedHashMap<String, String>()
        val docLen = LinkedHashMap<String, Int>()
        val docTermFreqs = LinkedHashMap<String, Map<String, Int>>()
        val docFreqs = LinkedHashMap<String, Int>()
        var totalLength = 0
        for (chunk in chunks) {
            val tokens = tokenize(chunk.text)
            val length = tokens.size
            chunkIds.add(chunk.chunkId)
            texts[chunk.chunkId] = chunk.text
            docLen[chunk.chunkId] = length
            totalLength += length
            val termFreqs = LinkedHashMap<String, Int>()
            for (token in tokens) {
                termFreqs[token] = (termFreqs[token] ?: 0) + 1
            }
            docTermFreqs[chunk.chunkId] = termFreqs
            for (token in termFreqs.keys) {
                docFreqs[token] = (docFreqs[token] ?: 0) + 1
            }
        }
        val totalDocs = chunks.size
        val avgDocLen = if (totalDocs > 0) totalLength.toDouble() / totalDocs.toDouble() else 0.0
        val idf = LinkedHashMap<String, Double>()
        for ((token, df) in docFreqs) {
            idf[token] = Math.log(1.0 + (totalDocs - df + 0.5) / (df + 0.5))
        }
        return CorpusIndex(
            chunkIds = chunkIds,
            texts = texts,
            docLen = docLen,
            docTermFreqs = docTermFreqs,
            idf = idf,
            avgDocLen = avgDocLen,
        )
    }

    /**
     * Hold a corpus, and forget the oldest one when the tier is full.
     *
     * Holding a corpus under a name it is already held under replaces it,
     * so a corpus that changed under a name that did not is still the
     * corpus the tier answers with.
     */
    private fun hold(key: String, index: CorpusIndex) {
        resident.remove(key)
        resident[key] = index
        while (resident.size > RESIDENT_LIMIT) {
            val oldest = resident.keys.firstOrNull() ?: return
            resident.remove(oldest)
        }
    }

    /**
     * The digest of a corpus: the SHA-256 of its passages, in order.
     *
     * The digest is the name of the corpus. Two corpora with the same
     * digest are the same corpus, and a corpus that changed has a different
     * digest, so a name can never point at a corpus that is not the one it
     * was minted for.
     *
     * The encoding is length-prefixed, so no two different corpora encode to
     * the same bytes, and it is the encoding the Python door uses.
     */
    internal fun corpusDigest(chunks: List<RetrievalChunk>): String {
        val bytes = ByteArrayOutputStream()
        for (chunk in chunks) {
            val chunkId = chunk.chunkId.toByteArray(StandardCharsets.UTF_8)
            val text = chunk.text.toByteArray(StandardCharsets.UTF_8)
            bytes.write(intToBytes(chunkId.size))
            bytes.write(chunkId)
            bytes.write(intToBytes(text.size))
            bytes.write(text)
        }
        return Hashing.sha256Hex(bytes.toByteArray())
    }

    /** The four bytes of [value], most significant first. */
    private fun intToBytes(value: Int): ByteArray = byteArrayOf(
        (value ushr 24).toByte(),
        (value ushr 16).toByte(),
        (value ushr 8).toByte(),
        value.toByte(),
    )

    // --- BM25 Okapi --------------------------------------------------------

    /**
     * Score the corpus with BM25 Okapi.
     *
     * A document is scored by summing, over the query's tokens *in the order
     * the query gives them*, the term's inverse document frequency times the
     * saturated term frequency. The order matters: the sum is a floating
     * point sum, and a floating point sum is not associative.
     */
    private fun bm25Search(
        corpus: CorpusIndex,
        query: String,
        topK: Int,
        k1: Double,
        b: Double,
    ): List<RankedChunk> {
        if (topK <= 0) {
            throw ValidationError("top_k must be positive, got $topK")
        }
        if (query.isEmpty() || query.stripModelWhitespace().isEmpty() || corpus.chunkIds.isEmpty()) {
            return emptyList()
        }

        val queryTokens = tokenize(query)
        if (queryTokens.isEmpty()) return emptyList()

        val avgdl = if (corpus.avgDocLen > 0.0) corpus.avgDocLen else 1.0
        val scores = LinkedHashMap<String, Double>()
        for ((chunkId, termFreqs) in corpus.docTermFreqs) {
            val docLength = corpus.docLen[chunkId] ?: 0
            val lengthNorm = 1.0 - b + b * (docLength / avgdl)
            var docScore = 0.0
            for (token in queryTokens) {
                val tf = termFreqs[token] ?: continue
                val termIdf = corpus.idf[token] ?: 0.0
                val numerator = tf * (k1 + 1.0)
                val denominator = tf + k1 * lengthNorm
                docScore += termIdf * (numerator / denominator)
            }
            if (docScore > 0.0) {
                scores[chunkId] = docScore
            }
        }
        return rank(scores, topK)
    }

    // --- the dense index ---------------------------------------------------

    /**
     * Score the corpus by the cosine similarity of the dense vectors.
     *
     * Every document gets a score, including the ones that match nothing: a
     * document that is not in the list is a document the reader never saw.
     */
    private fun denseSearch(
        corpus: CorpusIndex,
        query: String,
        topK: Int,
        embeddingDim: Int,
        denseVectors: Map<String, List<Double>>?,
        queryVector: List<Double>?,
    ): List<RankedChunk> {
        if (topK <= 0) {
            throw ValidationError("top_k must be positive, got $topK")
        }
        if (query.isEmpty() || query.stripModelWhitespace().isEmpty() || corpus.chunkIds.isEmpty()) {
            return emptyList()
        }

        val qVec = queryVector ?: fallbackEmbed(query, embeddingDim)
        val hashVectors = if (denseVectors == null) corpus.hashVectors(embeddingDim) else emptyMap()
        val scores = LinkedHashMap<String, Double>()
        for (chunkId in corpus.chunkIds) {
            val docVec = when {
                denseVectors == null -> hashVectors[chunkId] ?: zeroVector(qVec.size)
                else -> denseVectors[chunkId] ?: zeroVector(qVec.size)
            }
            scores[chunkId] = clampSimilarity(dot(qVec, docVec))
        }
        return rank(scores, topK)
    }

    // --- reciprocal rank fusion --------------------------------------------

    /**
     * Fuse the sparse and the dense rankings by rank.
     *
     * The two scores are not on the same scale -- one is a term frequency, the
     * other a cosine similarity -- so they are not added. What is added is
     * each list's contribution `weight / (rrf_k + rank)`, where the rank is
     * one-based.
     */
    private fun hybridSearch(
        corpus: CorpusIndex,
        query: String,
        topK: Int,
        rrfK: Int,
        alpha: Double,
        k1: Double,
        b: Double,
        embeddingDim: Int,
        denseVectors: Map<String, List<Double>>?,
        queryVector: List<Double>?,
    ): List<RankedChunk> {
        if (topK <= 0) {
            throw ValidationError("top_k must be positive, got $topK")
        }
        if (rrfK <= 0) {
            throw ValidationError("rrf_k must be positive, got $rrfK")
        }
        if (query.isEmpty() || query.stripModelWhitespace().isEmpty()) {
            throw ValidationError("query cannot be empty or whitespace")
        }
        if (corpus.chunkIds.isEmpty()) return emptyList()

        val candidateK = maxOf(topK * 3, 50, corpus.chunkIds.size)
        val sparseHits = bm25Search(corpus, query, candidateK, k1, b)
        val denseHits = denseSearch(corpus, query, candidateK, embeddingDim, denseVectors, queryVector)

        val sparseRanks = ranksOf(sparseHits)
        val denseRanks = ranksOf(denseHits)
        val candidateIds = LinkedHashSet<String>()
        candidateIds.addAll(denseRanks.keys)
        candidateIds.addAll(sparseRanks.keys)
        if (candidateIds.isEmpty()) {
            // Nothing matched on either side: the dense list over the whole
            // corpus is the answer.
            return denseSearch(corpus, query, topK, embeddingDim, denseVectors, queryVector)
        }

        val weightDense = 2.0 * alpha
        val weightSparse = 2.0 * (1.0 - alpha)
        val rrfScores = LinkedHashMap<String, Double>()
        for (chunkId in candidateIds) {
            var score = 0.0
            val denseRank = denseRanks[chunkId]
            if (denseRank != null) {
                score += weightDense / (rrfK + denseRank)
            }
            val sparseRank = sparseRanks[chunkId]
            if (sparseRank != null) {
                score += weightSparse / (rrfK + sparseRank)
            }
            rrfScores[chunkId] = score
        }
        return rank(rrfScores, topK)
    }

    /** The one-based rank of every chunk of a ranked list. */
    private fun ranksOf(ranked: List<RankedChunk>): Map<String, Int> {
        val ranks = LinkedHashMap<String, Int>()
        for ((index, entry) in ranked.withIndex()) {
            ranks[entry.chunkId] = index + 1
        }
        return ranks
    }

    // --- shared helpers ----------------------------------------------------

    /**
     * The tokens of [text]: the maximal runs of word characters, lower-cased.
     *
     * This is Python's `re.findall(r"\b\w+\b", text.lower())`. The
     * lower-casing happens first, as it does there, and the word characters
     * are Python's, so a Unicode letter is a letter here too.
     */
    internal fun tokenize(text: String): List<String> {
        val lowered = text.lowercase()
        val tokens = mutableListOf<String>()
        val current = StringBuilder()
        var index = 0
        while (index < lowered.length) {
            val codePoint = lowered.codePointAt(index)
            if (CodePoints.isPythonWordChar(codePoint)) {
                current.appendCodePoint(codePoint)
            } else if (current.isNotEmpty()) {
                tokens.add(current.toString())
                current.setLength(0)
            }
            index += Character.charCount(codePoint)
        }
        if (current.isNotEmpty()) {
            tokens.add(current.toString())
        }
        return tokens
    }

    /**
     * Order the scores: highest first, and by chunk id when the scores are
     * equal.
     */
    private fun rank(scores: Map<String, Double>, topK: Int): List<RankedChunk> =
        scores.entries
            .sortedWith(
                compareByDescending<Map.Entry<String, Double>> { it.value }
                    .thenBy { it.key },
            )
            .take(topK)
            .map { entry -> RankedChunk(entry.key, entry.value) }

    /**
     * The dot product of two vectors, over their common prefix.
     *
     * Python's `zip` truncates to the shorter of the two, so a query vector
     * and a document vector of different lengths are compared over the part
     * they share.
     */
    private fun dot(left: List<Double>, right: List<Double>): Double {
        val length = minOf(left.size, right.size)
        var sum = 0.0
        for (index in 0 until length) {
            sum += left[index] * right[index]
        }
        return sum
    }

    /** A vector of [size] zeros. */
    private fun zeroVector(size: Int): List<Double> = List(size) { 0.0 }

    /** The cosine similarity, held between zero and one. */
    private fun clampSimilarity(similarity: Double): Double {
        val upper = if (similarity > 1.0) 1.0 else similarity
        return if (upper > 0.0) upper else 0.0
    }
}

/**
 * The deterministic term-frequency hash vector of [text].
 *
 * The vector is the signed count of the tokens that hash to each bucket,
 * normalised to unit length. The hash is the first eight hexadecimal
 * characters of the SHA-256 of the token, read as an unsigned 64-bit
 * integer, so the bucket is `hash % embeddingDim` and the sign is bit 8
 * of the same hash.
 *
 * The vector of a passage is the same for every query, so the corpus index
 * keeps them rather than computing them again for every query.
 */
private fun fallbackEmbed(text: String, embeddingDim: Int): List<Double> {
    val tokens = Indexer.tokenize(text)
    val vector = DoubleArray(embeddingDim)
    if (tokens.isEmpty()) return vector.toList()

    val modulus = BigInteger.valueOf(embeddingDim.toLong())
    for (token in tokens) {
        val hash = BigInteger(Hashing.sha256Hex(token).substring(0, 8), 16)
        val index = hash.mod(modulus).toInt()
        val sign = if (hash.shiftRight(8).and(BigInteger.ONE).toInt() == 1) 1.0 else -1.0
        vector[index] += sign
    }

    var norm = 0.0
    for (value in vector) {
        norm += value * value
    }
    norm = Math.sqrt(norm)
    if (norm > 0.0) {
        for (index in vector.indices) {
            vector[index] = vector[index] / norm
        }
    }
    return vector.toList()
}
