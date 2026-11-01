package lmeditor.core

/**
 * The runtime interfaces the domain modules are written against.
 *
 * These are the Kotlin form of the Python tier's `typing.Protocol` classes in
 * `lmeditor/core/types.py`. They exist so that the model-dependent
 * implementations (the dense embedder, the sparse index, the NLI evaluator)
 * can stay in Python while the deterministic domain logic around them moves
 * to Kotlin.
 */

/** Segments text into traceable source chunks. */
interface Chunker {
    fun chunk(text: String, documentId: String, metadata: Map<String, Any?> = emptyMap()): List<SourceChunk>
}

/** Generates dense vector embeddings. */
interface DenseEmbedder {
    fun embed(text: String): List<Double>
    fun embedBatch(texts: List<String>): List<List<Double>>
}

/** Sparse keyword/lexical search. */
interface SparseSearch {
    fun index(chunks: List<SourceChunk>)
    fun search(query: String, topK: Int = 10): List<Pair<String, Double>>
}

/** Hybrid (dense vector + sparse BM25) index with reciprocal-rank fusion. */
interface HybridIndexer {
    fun index(chunks: List<SourceChunk>)
    fun getChunk(chunkId: String): SourceChunk?
    fun search(
        query: String,
        topK: Int = 10,
        rrfK: Int = 60,
        alpha: Double = 0.5,
    ): List<Pair<SourceChunk, Double>>
}

/** The retrieval seam the workflow binds sections to. */
interface ProvenanceIndexer {
    fun query(query: String, topK: Int = 5): List<SourceChunk>
    fun getChunk(chunkId: String): SourceChunk?
    fun hasChunk(chunkId: String): Boolean
    fun getAllChunkIds(): List<String>
}

/** Premise-hypothesis entailment evaluation. */
interface NliEvaluator {
    /**
     * Evaluate whether [hypothesis] is entailed by [premise].
     *
     * The model-dependent implementations live in the Python tier; the
     * deterministic rules around them that cross the seam are Kotlin. The
     * rules the seam does not carry are named in ARCHITECTURE.md §18.2.1,
     * not implied: the drift-pinned Python twins, the two rules with no
     * Kotlin counterpart, and the model-facing half.
     */
    fun evaluate(premise: String, hypothesis: String): Pair<EntailmentStatus, Double>
}

/** A metric callable by the prompt-optimisation loop. */
interface MetricCallable {
    /** Score a prediction against the gold answer, with the trace that produced it. */
    fun score(gold: Any?, pred: Any?, trace: Any?): Double

    /** Score a prediction against the gold answer, with no trace. */
    fun score(gold: Any?, pred: Any?): Double = score(gold, pred, null)
}
