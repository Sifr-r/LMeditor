"""Hybrid provenance retrieval index combining BM25 Okapi and dense vector search with RRF."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Sequence

from lmeditor.core.exceptions import ChunkNotFoundError
from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import DenseEmbedderProtocol, HybridIndexerProtocol, SparseSearchProtocol


class BM25Index(SparseSearchProtocol):
    """Pure-Python BM25Okapi sparse retrieval index with zero external dependencies."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0:
            raise ValueError(f"k1 must be non-negative, got {k1}")
        if b < 0 or b > 1:
            raise ValueError(f"b must be between 0.0 and 1.0 inclusive, got {b}")
        self.k1 = k1
        self.b = b
        self.doc_len: dict[str, int] = {}
        self.avg_doc_len: float = 0.0
        self.doc_term_freqs: dict[str, dict[str, int]] = {}
        self.doc_freqs: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self._total_docs: int = 0

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercased alphanumeric words."""
        return re.findall(r"\b\w+\b", text.lower())

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index a collection of SourceChunks."""
        self.doc_len.clear()
        self.doc_term_freqs.clear()
        self.doc_freqs.clear()
        self.idf.clear()
        self._total_docs = len(chunks)

        if self._total_docs == 0:
            self.avg_doc_len = 0.0
            return

        total_length = 0
        for chunk in chunks:
            tokens = self._tokenize(chunk.text)
            length = len(tokens)
            self.doc_len[chunk.chunk_id] = length
            total_length += length

            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            self.doc_term_freqs[chunk.chunk_id] = tf

            for token in tf.keys():
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        self.avg_doc_len = total_length / self._total_docs

        # Calculate BM25 IDF for all observed terms
        for token, df in self.doc_freqs.items():
            # Robust BM25Okapi IDF with smoothing to guarantee positive values
            self.idf[token] = math.log(1.0 + (self._total_docs - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search indexed chunks using BM25Okapi scoring."""
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        if not query or not query.strip() or self._total_docs == 0:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: dict[str, float] = {}
        k1 = self.k1
        b = self.b
        avgdl = self.avg_doc_len if self.avg_doc_len > 0 else 1.0

        for chunk_id, term_freqs in self.doc_term_freqs.items():
            doc_l = self.doc_len.get(chunk_id, 0)
            length_norm = 1.0 - b + b * (doc_l / avgdl)
            doc_score = 0.0

            for token in query_tokens:
                if token in term_freqs:
                    tf = term_freqs[token]
                    idf = self.idf.get(token, 0.0)
                    numerator = tf * (k1 + 1.0)
                    denominator = tf + k1 * length_norm
                    doc_score += idf * (numerator / denominator)

            if doc_score > 0.0:
                scores[chunk_id] = doc_score

        # Sort descending by score, tie-break by chunk_id
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]


class DenseVectorIndex:
    """Dense vector index with cosine similarity and deterministic fallback vectorizer."""

    def __init__(
        self,
        embedder: DenseEmbedderProtocol | None = None,
        embedding_dim: int = 128,
    ) -> None:
        self.embedder = embedder
        self.embedding_dim = embedding_dim
        self._vectors: dict[str, list[float]] = {}

    def _fallback_embed(self, text: str) -> list[float]:
        """Deterministic TF-hash feature vectorizer fallback for dependency-free environments."""
        tokens = re.findall(r"\b\w+\b", text.lower())
        vec = [0.0] * self.embedding_dim
        if not tokens:
            return vec

        for token in tokens:
            h = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.embedding_dim
            sign = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += sign

        # Unit L2 normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def _embed(self, text: str) -> list[float]:
        """Generate embedding vector using the provided embedder or deterministic fallback."""
        if self.embedder is not None:
            raw = self.embedder.embed(text)
            norm = math.sqrt(sum(x * x for x in raw))
            if norm > 0:
                return [x / norm for x in raw]
            return raw
        return self._fallback_embed(text)

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index dense vector embeddings for chunks."""
        self._vectors.clear()
        for chunk in chunks:
            self._vectors[chunk.chunk_id] = self._embed(chunk.text)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search indexed chunks by cosine similarity."""
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        if not query or not query.strip() or not self._vectors:
            return []

        q_vec = self._embed(query)
        scores: dict[str, float] = {}

        for chunk_id, doc_vec in self._vectors.items():
            # Dot product of unit-normalized vectors equals cosine similarity
            sim = sum(q * d for q, d in zip(q_vec, doc_vec))
            scores[chunk_id] = max(0.0, min(1.0, sim))

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]


class HybridIndexer(HybridIndexerProtocol):
    """Hybrid sparse (BM25) and dense vector retrieval index with Reciprocal Rank Fusion (RRF)."""

    def __init__(
        self,
        bm25_index: BM25Index | None = None,
        vector_index: DenseVectorIndex | None = None,
    ) -> None:
        self._bm25: BM25Index = bm25_index if bm25_index is not None else BM25Index()
        self._vector: DenseVectorIndex = vector_index if vector_index is not None else DenseVectorIndex()
        self._chunks: dict[str, SourceChunk] = {}

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index chunks across both sparse BM25 and dense vector indices."""
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._bm25.index(chunks)
        self._vector.index(chunks)

    def get_chunk(self, chunk_id: str) -> SourceChunk:
        """Retrieve a chunk by ID or raise ChunkNotFoundError."""
        if chunk_id not in self._chunks:
            raise ChunkNotFoundError(chunk_id)
        return self._chunks[chunk_id]

    def has_chunk(self, chunk_id: str) -> bool:
        """Check whether a chunk exists in the index."""
        return chunk_id in self._chunks

    def get_all_chunk_ids(self) -> list[str]:
        """Return all registered chunk IDs."""
        return list(self._chunks.keys())

    def query(self, query: str, top_k: int = 5) -> list[SourceChunk]:
        """Execute hybrid search returning matched SourceChunk instances."""
        results = self.search(query, top_k=top_k)
        return [chunk for chunk, _ in results]

    def search(
        self,
        query: str,
        top_k: int = 10,
        rrf_k: int = 60,
        alpha: float = 0.5,
    ) -> list[tuple[SourceChunk, float]]:
        """Search using Reciprocal Rank Fusion combining dense and sparse results.

        RRF formula:
            score(d) = (2 * (1 - alpha)) / (rrf_k + rank_sparse(d)) + (2 * alpha) / (rrf_k + rank_dense(d))
        When alpha=0.5, this simplifies to:
            score(d) = 1.0 / (rrf_k + rank_sparse(d)) + 1.0 / (rrf_k + rank_dense(d))
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        if not query or not query.strip():
            raise ValueError("query cannot be empty or whitespace")
        if not self._chunks:
            return []

        # Retrieve candidates from both systems
        candidate_k = max(top_k * 3, 50, len(self._chunks))
        sparse_hits = self._bm25.search(query, top_k=candidate_k)
        dense_hits = self._vector.search(query, top_k=candidate_k)

        sparse_ranks: dict[str, int] = {cid: rank + 1 for rank, (cid, _) in enumerate(sparse_hits)}
        dense_ranks: dict[str, int] = {cid: rank + 1 for rank, (cid, _) in enumerate(dense_hits)}

        candidate_ids = set(sparse_ranks.keys()) | set(dense_ranks.keys())
        if not candidate_ids:
            # Fallback: if no keyword hits, use dense rank over all indexed chunks
            dense_all = self._vector.search(query, top_k=top_k)
            return [(self._chunks[cid], score) for cid, score in dense_all]

        weight_dense = 2.0 * alpha
        weight_sparse = 2.0 * (1.0 - alpha)

        rrf_scores: dict[str, float] = {}
        for cid in candidate_ids:
            score = 0.0
            if cid in dense_ranks:
                score += weight_dense / (rrf_k + dense_ranks[cid])
            if cid in sparse_ranks:
                score += weight_sparse / (rrf_k + sparse_ranks[cid])
            rrf_scores[cid] = score

        # Sort descending by RRF score, tie-break by chunk_id
        ranked = sorted(rrf_scores.items(), key=lambda item: (-item[1], item[0]))
        return [(self._chunks[cid], score) for cid, score in ranked[:top_k]]


__all__ = [
    "BM25Index",
    "DenseVectorIndex",
    "HybridIndexer",
]
