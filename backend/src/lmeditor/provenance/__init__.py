"""Provenance engine for LMeditor providing chunking, indexing, and citation lineage."""

from lmeditor.provenance.chunker import (
    SemanticChunker,
    SlidingWindowChunker,
    SourceChunker,
    compute_chunk_id,
    get_byte_offsets,
)
from lmeditor.provenance.indexer import (
    BM25Index,
    DenseVectorIndex,
    HybridIndexer,
)
from lmeditor.provenance.lineage import (
    CitationLineageEngine,
)

__all__ = [
    "BM25Index",
    "CitationLineageEngine",
    "DenseVectorIndex",
    "HybridIndexer",
    "SemanticChunker",
    "SlidingWindowChunker",
    "SourceChunker",
    "compute_chunk_id",
    "get_byte_offsets",
]
