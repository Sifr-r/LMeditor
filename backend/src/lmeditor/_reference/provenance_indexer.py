"""The frozen retrieval index and the frozen supporting-text rule.

Port of ``backend/src/lmeditor/provenance/indexer.py`` and of
``backend/src/lmeditor/provenance/service.py``'s ``_supporting_text``, frozen
as they were before the Kotlin tier took the rules over.

This module is the **witness** the parity harness measures the Kotlin tier
against. It is plain Python on purpose: it never calls across the NDJSON seam,
so a comparison against it proves something. See ``lmeditor._reference`` for
why that matters.

What is frozen here is the *rule*: the BM25 scoring, the deterministic
term-frequency hash, the cosine similarity, the reciprocal rank fusion and the
longest-run-of-shared-words. What is **not** frozen here is the
sentence-transformers model: a model is not a rule, it cannot be witnessed
deterministically, and it stays in the Python tier. The dense index below
therefore resolves an injected embedder or nothing at all, and falls back to
the deterministic hash, which is the half the harness compares.

The chunk type and the not-found error come from ``lmeditor.core``, which is
plain Python too; nothing here reaches the Kotlin tier.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Final, Optional, Protocol, runtime_checkable

from lmeditor.core.exceptions import ChunkNotFoundError
from lmeditor.core.schemas import SourceChunk

#: The name reported when the dense index could not load a semantic model and
#: is using the deterministic term-frequency hash instead.
FALLBACK_EMBEDDER_NAME: Final[str] = "tf-hash-fallback"

#: The model the Python tier uses for the dense half. The witness never loads
#: it -- see the module docstring -- but the name is part of the index's
#: vocabulary.
SEMANTIC_MODEL_NAME: Final[str] = "sentence-transformers/all-MiniLM-L6-v2"

#: Function words. A claim and a passage that share only "the" share no
#: evidence, and a quote made of such words would earn the confidence of a
#: verbatim quotation.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
        "had", "has", "have", "he", "her", "his", "in", "is", "it", "its", "of",
        "on", "or", "she", "that", "the", "their", "them", "there", "these",
        "they", "this", "to", "was", "were", "which", "who", "will", "with",
    }
)


@runtime_checkable
class DenseEmbedderProtocol(Protocol):
    """The embedder the dense index accepts, when one is supplied."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class BM25Index:
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
    """Dense vector index with cosine similarity and an injected embedder.

    The witness keeps the deterministic half of the original: an injected
    embedder, or the term-frequency hash. The sentence-transformers loader is
    not frozen here -- see the module docstring.
    """

    def __init__(
        self,
        embedder: DenseEmbedderProtocol | None = None,
        embedding_dim: int = 128,
        *,
        enable_semantic: bool = True,
        semantic_model: str = SEMANTIC_MODEL_NAME,
        allow_download: bool = False,
    ) -> None:
        self.embedder = embedder
        self.embedding_dim = embedding_dim
        self._enable_semantic = enable_semantic
        self._semantic_model = semantic_model
        self._allow_download = allow_download
        self._vectors: dict[str, list[float]] = {}

    @property
    def embedder_name(self) -> str:
        """Return the name of the embedder this index is using."""
        embedder = self._resolve_embedder()
        if embedder is None:
            return FALLBACK_EMBEDDER_NAME
        return str(getattr(embedder, "name", type(embedder).__name__))

    def _resolve_embedder(self) -> DenseEmbedderProtocol | None:
        """Return the embedder to use, or ``None`` for the hash fallback.

        The witness never loads a model: the semantic model is not a rule, so
        it is not frozen here.
        """
        if self.embedder is not None:
            return self.embedder
        return None

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
        """Generate embedding vector using the resolved embedder or the fallback."""
        embedder = self._resolve_embedder()
        if embedder is not None:
            raw = embedder.embed(text)
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


class HybridIndexer:
    """Hybrid sparse (BM25) and dense vector retrieval index with Reciprocal Rank Fusion (RRF)."""

    def __init__(
        self,
        bm25_index: BM25Index | None = None,
        vector_index: DenseVectorIndex | None = None,
    ) -> None:
        self._bm25: BM25Index = bm25_index if bm25_index is not None else BM25Index()
        self._vector: DenseVectorIndex = vector_index if vector_index is not None else DenseVectorIndex()
        self._chunks: dict[str, SourceChunk] = {}

    @property
    def embedder_name(self) -> str:
        """The name the dense half is using."""
        return self._vector.embedder_name

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


def supporting_text(claim_text: str, chunk_text: str) -> tuple[Optional[str], float]:
    """Return the text the claim and the passage share, and the claim's coverage.

    The quote is a verbatim substring of ``chunk_text``: the longest run of
    words the two have in common. A run of function words is not evidence and
    is rejected, so the lineage engine never certifies a quotation that says
    nothing.

    The coverage is the share of the claim's content words the run accounts
    for: ``1.0`` when the whole claim is in the passage, less when only part
    of it is.
    """
    claim_spans = list(re.finditer(r"\S+", claim_text))
    chunk_spans = list(re.finditer(r"\S+", chunk_text))
    if not claim_spans or not chunk_spans:
        return None, 0.0

    claim_words = [match.group(0).lower() for match in claim_spans]
    chunk_words = [match.group(0).lower() for match in chunk_spans]

    best_length = 0
    best_claim_start = 0
    best_chunk_start = 0
    for claim_index, claim_word in enumerate(claim_words):
        for chunk_index, chunk_word in enumerate(chunk_words):
            if claim_word != chunk_word:
                continue
            length = 0
            while (
                claim_index + length < len(claim_words)
                and chunk_index + length < len(chunk_words)
                and claim_words[claim_index + length]
                == chunk_words[chunk_index + length]
            ):
                length += 1
            if length > best_length:
                best_length = length
                best_claim_start = claim_index
                best_chunk_start = chunk_index

    if best_length == 0:
        return None, 0.0

    matched_words = claim_words[best_claim_start : best_claim_start + best_length]
    if best_length < 2 or not any(word not in _STOPWORDS for word in matched_words):
        # A single word, or a run of function words, is not a quotation.
        return None, 0.0

    content_words = [word for word in claim_words if word not in _STOPWORDS]
    matched_content = [word for word in matched_words if word not in _STOPWORDS]
    if not content_words:
        coverage = 1.0
    else:
        coverage = min(1.0, len(matched_content) / len(content_words))

    first = chunk_spans[best_chunk_start]
    last = chunk_spans[best_chunk_start + best_length - 1]
    return chunk_text[first.start() : last.end()], coverage


__all__ = [
    "BM25Index",
    "DenseEmbedderProtocol",
    "DenseVectorIndex",
    "FALLBACK_EMBEDDER_NAME",
    "HybridIndexer",
    "SEMANTIC_MODEL_NAME",
    "supporting_text",
]
