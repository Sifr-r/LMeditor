"""Hybrid provenance retrieval index, as the Kotlin tier implements it.

The scoring is a rule -- BM25 Okapi, a cosine similarity over dense vectors,
and the reciprocal rank fusion of the two -- and the rule is Kotlin's: it is
implemented once, in ``lmeditor.text.Indexer``, and the tier serves it across
the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.provenance_indexer``, frozen, so the
parity harness can still ask both sides the same question.

What stays in Python, and why:

* **The embedder.** ``sentence_transformers`` and any injected
  ``DenseEmbedderProtocol`` are models, and a model is not a rule. The
  semantic model is loaded lazily -- on the first embedding, not at
  construction -- so importing this module never touches the model and never
  touches the network. When an embedder resolves, this side computes the
  vectors and normalises them, and hands them to the tier, which uses them as
  they are. When no embedder resolves, no vectors are sent, and the tier uses
  its own deterministic term-frequency hash instead.
* **The name of the embedder.** ``embedder_name`` reports the model's name,
  the injected embedder's name, or ``FALLBACK_EMBEDDER_NAME`` -- so a
  degradation is visible rather than silent.
* **The chunk store.** The seam carries data, not a live index, so the hybrid
  index keeps the chunks it was given and hands the tier their ids and their
  text. The corpus is handed over once, when it changes: after that the door
  names the corpus -- by its digest -- and the tier answers from the index it
  built when the corpus arrived. A corpus that changed is a different digest,
  so the tier never answers a query about the old corpus with the new one.

A door never silently falls back to the Python implementation: if the tier
cannot be reached, ``BridgeUnavailableError`` propagates unwrapped.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any, Final, Optional

from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.core.exceptions import ChunkNotFoundError
from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import DenseEmbedderProtocol, HybridIndexerProtocol, SparseSearchProtocol
from lmeditor.provenance.embeddings import (
    SEMANTIC_MODEL_NAME,
    SentenceTransformerEmbedder,
)
from lmeditor.tier import text as tier_text

logger = logging.getLogger(__name__)

#: The name reported when the dense index could not load a semantic model and
#: is using the deterministic term-frequency hash instead.
FALLBACK_EMBEDDER_NAME: Final[str] = "tf-hash-fallback"

#: The id of the chunk that names a corpus the tier holds.
#:
#: A chunk id is the SHA-256 digest of a passage, and the digest of a
#: non-empty payload is never all zeros, so this id names no passage. The
#: tier reads it as the head of a request that is about a corpus rather than
#: about a list of passages.
CORPUS_DESCRIPTOR_CHUNK_ID: Final[str] = "0" * 64

#: The document the descriptor chunk claims to belong to.
CORPUS_DESCRIPTOR_DOCUMENT_ID: Final[str] = "corpus"


def corpus_digest(chunks: Sequence[SourceChunk]) -> str:
    """The name of a corpus: the SHA-256 of its passages, in order.

    The digest is what a corpus is called. Two corpora with the same digest
    are the same corpus, and a corpus that changed has a different digest,
    so a name can never point at a corpus that is not the one it was minted
    for. The tier computes the same digest over the passages it receives,
    so the two sides agree on what a corpus is called.
    """
    digest = hashlib.sha256()
    for chunk in chunks:
        chunk_id = chunk.chunk_id.encode("utf-8")
        text = chunk.text.encode("utf-8")
        digest.update(len(chunk_id).to_bytes(4, "big"))
        digest.update(chunk_id)
        digest.update(len(text).to_bytes(4, "big"))
        digest.update(text)
    return digest.hexdigest()


class _CorpusHandle:
    """The corpus the tier holds, and the name it holds it under.

    The tier indexes a corpus when the corpus arrives and answers the
    queries that name it. The door therefore ships the corpus once -- when
    the corpus changes -- and names it from then on, so a query costs the
    query and not the corpus.
    """

    def __init__(self) -> None:
        self._chunks: list[SourceChunk] = []
        self._name: str = ""
        self._resident: bool = False

    def set(self, chunks: Sequence[SourceChunk]) -> None:
        """Take a new corpus: it is a different corpus, under a new name.

        The name is the digest of the corpus, and the tier is told the
        corpus again, so a corpus that changed is never answered from the
        tables of the corpus it replaced.
        """
        self._chunks = list(chunks)
        self._name = corpus_digest(self._chunks)
        self._resident = False

    def payload(self) -> list[SourceChunk]:
        """The chunks to send: the name, and the corpus when it is not held.

        A corpus with no passages has nothing to hand over, so it travels as
        the empty request it has always been.
        """
        if not self._chunks:
            return []
        if self._resident:
            return [self._descriptor()]
        return [self._descriptor(), *self._chunks]

    def mark_resident(self) -> None:
        """Record that the tier holds the corpus under its name."""
        self._resident = True

    def mark_absent(self) -> None:
        """Record that the tier does not hold the corpus under its name."""
        self._resident = False

    def _descriptor(self) -> SourceChunk:
        """The chunk that names the corpus."""
        return SourceChunk(
            chunk_id=CORPUS_DESCRIPTOR_CHUNK_ID,
            document_id=CORPUS_DESCRIPTOR_DOCUMENT_ID,
            text=self._name,
        )


def _rank(handle: _CorpusHandle, query: str, **kwargs: Any) -> list[tuple[str, float]]:
    """Ask the tier to rank the corpus the handle names.

    The corpus travels with the request until the tier holds it. A tier
    that does not -- it restarted, or it pushed the corpus out of its cache
    -- says so, and the corpus is sent again.
    """
    try:
        ranked = tier_text.rank_retrieval(handle.payload(), query, **kwargs)
    except KotlinDomainError as exc:
        if exc.error_type != "CorpusNotResidentError":
            raise
        handle.mark_absent()
        ranked = tier_text.rank_retrieval(handle.payload(), query, **kwargs)
    handle.mark_resident()
    return ranked


class BM25Index(SparseSearchProtocol):
    """The BM25 Okapi sparse index, as the Kotlin tier scores it.

    The scoring is the tier's. This class keeps the two knobs the rule has --
    the term-frequency saturation ``k1`` and the length normalisation ``b`` --
    and the chunks the index was given.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 < 0:
            raise ValueError(f"k1 must be non-negative, got {k1}")
        if b < 0 or b > 1:
            raise ValueError(f"b must be between 0.0 and 1.0 inclusive, got {b}")
        self.k1 = k1
        self.b = b
        self._chunks: list[SourceChunk] = []
        self._corpus = _CorpusHandle()

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index a collection of SourceChunks."""
        self._chunks = list(chunks)
        self._corpus.set(chunks)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search indexed chunks using BM25Okapi scoring."""
        return _rank(
            self._corpus,
            query,
            mode="bm25",
            top_k=top_k,
            k1=self.k1,
            b=self.b,
        )


class DenseVectorIndex:
    """Dense vector index with cosine similarity and a semantic embedder.

    The index uses a real sentence-transformers model when it can load one and
    falls back to the deterministic term-frequency hash only when it cannot.
    Which of the two is in use is never a secret: :attr:`embedder_name` names
    it, and the fallback's name says so.
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
        self._semantic: SentenceTransformerEmbedder | None = None
        self._semantic_unavailable = False
        self._chunks: list[SourceChunk] = []
        self._vectors: dict[str, list[float]] = {}
        self._corpus = _CorpusHandle()

    @property
    def embedder_name(self) -> str:
        """Return the name of the embedder this index is using.

        One of the model's name, the name of the embedder that was supplied,
        or :data:`FALLBACK_EMBEDDER_NAME` when the deterministic hash is in
        use because no model could be loaded.
        """
        embedder = self._resolve_embedder()
        if embedder is None:
            return FALLBACK_EMBEDDER_NAME
        return str(getattr(embedder, "name", type(embedder).__name__))

    def _resolve_embedder(self) -> DenseEmbedderProtocol | None:
        """Return the embedder to use, or ``None`` for the hash fallback.

        The semantic model is loaded lazily -- on the first embedding, not at
        construction -- so importing this module never touches the model and
        never touches the network.
        """
        if self.embedder is not None:
            return self.embedder
        if not self._enable_semantic or self._semantic_unavailable:
            return None
        if self._semantic is None:
            self._semantic = SentenceTransformerEmbedder(
                self._semantic_model, allow_download=self._allow_download
            )
        if not self._semantic.is_available():
            self._semantic_unavailable = True
            logger.warning(
                "Falling back to the deterministic hash embedder: %s",
                self._semantic.load_error,
            )
            return None
        return self._semantic

    def _embed(self, text: str) -> Optional[list[float]]:
        """The normalised vector of ``text``, or ``None`` for the hash fallback.

        ``None`` means the tier's own deterministic term-frequency hash is in
        use, and the tier computes it.
        """
        embedder = self._resolve_embedder()
        if embedder is None:
            return None
        raw = embedder.embed(text)
        norm = math.sqrt(sum(x * x for x in raw))
        if norm > 0:
            return [x / norm for x in raw]
        return raw

    def _tier_vectors(self) -> Optional[dict[str, list[float]]]:
        """The vectors to hand the tier, or ``None`` when the hash is in use."""
        if self._resolve_embedder() is None:
            return None
        return dict(self._vectors)

    def _tier_query_vector(self, query: str) -> Optional[list[float]]:
        """The query's vector, or ``None`` when the hash is in use."""
        if self._resolve_embedder() is None:
            return None
        return self._embed(query)

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index dense vector embeddings for chunks."""
        self._chunks = list(chunks)
        self._vectors = {}
        self._corpus.set(chunks)
        if self._resolve_embedder() is None:
            # The tier computes the deterministic hash vectors itself.
            return
        for chunk in chunks:
            vector = self._embed(chunk.text)
            if vector is not None:
                self._vectors[chunk.chunk_id] = vector

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search indexed chunks by cosine similarity."""
        return _rank(
            self._corpus,
            query,
            mode="dense",
            top_k=top_k,
            embedding_dim=self.embedding_dim,
            dense_vectors=self._tier_vectors(),
            query_vector=self._tier_query_vector(query),
        )


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
        self._corpus = _CorpusHandle()

    @property
    def embedder_name(self) -> str:
        """The name the dense half is using."""
        return self._vector.embedder_name

    @property
    def k1(self) -> float:
        """The term-frequency saturation of the sparse half.

        The knob is part of the index's configuration, so a caller that wants
        the tier to score with *this* index's settings has to be able to name
        them.
        """
        return self._bm25.k1

    @property
    def b(self) -> float:
        """The length normalisation of the sparse half."""
        return self._bm25.b

    @property
    def embedding_dim(self) -> int:
        """The width of the dense vectors."""
        return self._vector.embedding_dim

    def dense_vectors(self) -> Optional[dict[str, list[float]]]:
        """The dense vectors of the indexed corpus, or ``None`` for the hash.

        The vectors are the *model's* half, so they are computed here and
        handed to the tier as they are. ``None`` means the tier is to compute
        its own deterministic term-frequency hash.
        """
        return self._vector._tier_vectors()

    def query_vector(self, query: str) -> Optional[list[float]]:
        """The dense vector of ``query``, or ``None`` for the hash."""
        return self._vector._tier_query_vector(query)

    def index(self, chunks: list[SourceChunk]) -> None:
        """Index chunks across both sparse BM25 and dense vector indices."""
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        # The corpus is kept as it was given, duplicates and all: the sparse
        # rule counts the documents it was handed, and the de-duplicated
        # store would be a different number.
        self._corpus.set(chunks)
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
        ranked = _rank(
            self._corpus,
            query,
            mode="hybrid",
            top_k=top_k,
            rrf_k=rrf_k,
            alpha=alpha,
            k1=self._bm25.k1,
            b=self._bm25.b,
            embedding_dim=self._vector.embedding_dim,
            dense_vectors=self._vector._tier_vectors(),
            query_vector=self._vector._tier_query_vector(query),
        )
        return [(self._chunks[chunk_id], score) for chunk_id, score in ranked]


__all__ = [
    "BM25Index",
    "CORPUS_DESCRIPTOR_CHUNK_ID",
    "DenseVectorIndex",
    "FALLBACK_EMBEDDER_NAME",
    "HybridIndexer",
    "corpus_digest",
]
