"""Provenance engine for LMeditor providing chunking, indexing, and citation lineage."""

from lmeditor.provenance.chunker import (
    SentenceAwareChunker,
    SemanticChunker,
    SlidingWindowChunker,
    SourceChunker,
    compute_chunk_id,
    estimate_token_count,
    get_byte_offsets,
)
from lmeditor.provenance.embeddings import (
    SEMANTIC_MODEL_NAME,
    SentenceTransformerEmbedder,
)
from lmeditor.provenance.indexer import (
    BM25Index,
    DenseVectorIndex,
    HybridIndexer,
)
from lmeditor.provenance.lineage import (
    CitationLineageEngine,
)
from lmeditor.provenance.service import (
    ProvenanceIndex,
    build_provenance_index,
)

__all__ = [
    "BM25Index",
    "CitationLineageEngine",
    "DenseVectorIndex",
    "HybridIndexer",
    "ProvenanceIndex",
    "SEMANTIC_MODEL_NAME",
    "SentenceAwareChunker",
    "SentenceTransformerEmbedder",
    "SemanticChunker",
    "SlidingWindowChunker",
    "SourceChunker",
    "build_provenance_index",
    "compute_chunk_id",
    "estimate_token_count",
    "get_byte_offsets",
]
