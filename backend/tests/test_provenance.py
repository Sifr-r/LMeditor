"""Comprehensive test suite for LMeditor provenance chunker, indexer, and lineage engine."""

from __future__ import annotations

import re
import pytest

from lmeditor.core.exceptions import (
    ChunkNotFoundError,
    CitationHallucinationError,
    SpanAlignmentError,
)
from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import DenseEmbedderProtocol
from lmeditor.provenance.chunker import (
    SemanticChunker,
    SlidingWindowChunker,
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


# ============================================================================
# 1. Hashing and Byte Offset Tests
# ============================================================================


def test_compute_chunk_id_determinism() -> None:
    """Verify compute_chunk_id returns deterministic 64-char lowercase hex string."""
    doc_id = "doc_01"
    text = "The Stanford STORM paper presents multi-perspective discovery."
    cid1 = compute_chunk_id(doc_id, 0, len(text), text)
    cid2 = compute_chunk_id(doc_id, 0, len(text), text)

    assert cid1 == cid2
    assert len(cid1) == 64
    assert re.match(r"^[a-f0-9]{64}$", cid1)

    # Different bounds must yield different chunk IDs
    cid3 = compute_chunk_id(doc_id, 1, len(text), text)
    assert cid1 != cid3


def test_get_byte_offsets_ascii_and_multibyte() -> None:
    """Verify get_byte_offsets correctly calculates UTF-8 byte spans."""
    ascii_text = "Hello world"
    b_start, b_end = get_byte_offsets(ascii_text, 0, 5)
    assert b_start == 0
    assert b_end == 5
    assert ascii_text.encode("utf-8")[b_start:b_end] == b"Hello"

    # Multi-byte UTF-8: 'café' (é is 2 bytes), '🚀' is 4 bytes
    mb_text = "café 🚀 rocket"
    # 'café' chars [0:4] -> bytes: 'c'(1) + 'a'(1) + 'f'(1) + 'é'(2) = 5 bytes
    b_start, b_end = get_byte_offsets(mb_text, 0, 4)
    assert b_start == 0
    assert b_end == 5
    assert mb_text.encode("utf-8")[b_start:b_end] == "café".encode("utf-8")

    # Span covering emoji: char 5 is '🚀'
    b_start, b_end = get_byte_offsets(mb_text, 5, 6)
    assert mb_text.encode("utf-8")[b_start:b_end] == "🚀".encode("utf-8")


# ============================================================================
# 2. SlidingWindowChunker Tests
# ============================================================================


def test_sliding_window_chunker_validation() -> None:
    """Verify input parameter validation on SlidingWindowChunker."""
    with pytest.raises(ValueError, match="window_size must be positive"):
        SlidingWindowChunker(window_size=0)

    with pytest.raises(ValueError, match="window_size must be positive"):
        SlidingWindowChunker(window_size=-10)

    with pytest.raises(ValueError, match="overlap_size must be non-negative"):
        SlidingWindowChunker(window_size=100, overlap_size=-1)

    with pytest.raises(ValueError, match="overlap_size must be non-negative"):
        SlidingWindowChunker(window_size=100, overlap_size=100)

    chunker = SlidingWindowChunker(window_size=100, overlap_size=20)
    with pytest.raises(ValueError, match="text cannot be empty"):
        chunker.chunk("", "doc_1")

    with pytest.raises(ValueError, match="text cannot be empty"):
        chunker.chunk("   ", "doc_1")

    with pytest.raises(ValueError, match="document_id cannot be empty"):
        chunker.chunk("Some valid content", "")


def test_sliding_window_boundary_integrity() -> None:
    """Verify exact character and byte slicing boundary integrity."""
    text = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
        "nu xi omicron pi rho sigma tau upsilon phi chi psi omega. "
        "A second sentence exploring LLM citation mechanisms and verifiable provenance."
    )
    chunker = SlidingWindowChunker(window_size=50, overlap_size=15)
    chunks = chunker.chunk(text, document_id="doc_integrity", metadata={"source": "test"})

    assert len(chunks) > 1
    for chunk in chunks:
        # Crucial invariant
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        # Byte boundary invariant
        encoded_text = text.encode("utf-8")
        assert encoded_text[chunk.byte_start : chunk.byte_end] == chunk.text.encode("utf-8")
        assert chunk.document_id == "doc_integrity"
        assert chunk.metadata == {"source": "test"}


# ============================================================================
# 3. SemanticChunker Tests
# ============================================================================


def test_semantic_chunker_validation() -> None:
    """Verify validation in SemanticChunker."""
    with pytest.raises(ValueError, match="min_chunk_size must be positive"):
        SemanticChunker(max_chunk_size=100, min_chunk_size=0)

    with pytest.raises(ValueError, match="greater than or equal to min_chunk_size"):
        SemanticChunker(max_chunk_size=50, min_chunk_size=100)

    chunker = SemanticChunker(max_chunk_size=200, min_chunk_size=50)
    with pytest.raises(ValueError, match="text cannot be empty"):
        chunker.chunk("", "doc_1")

    with pytest.raises(ValueError, match="document_id cannot be empty"):
        chunker.chunk("Valid text", "  ")


def test_semantic_chunker_paragraph_and_sentence_integrity() -> None:
    """Verify SemanticChunker splits on paragraphs and preserves slicing integrity."""
    text = (
        "First paragraph sentence one. First paragraph sentence two with more details.\n\n"
        "Second paragraph begins here. It contains another factual claim about DSPy.\n\n"
        "Third paragraph discusses LangGraph orchestration and human in the loop checkpoints."
    )
    chunker = SemanticChunker(max_chunk_size=120, min_chunk_size=30)
    chunks = chunker.chunk(text, document_id="doc_semantic")

    assert len(chunks) >= 2
    for chunk in chunks:
        # Invariant: text[chunk.char_start:chunk.char_end] == chunk.text
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        # Invariant: byte offsets match
        encoded = text.encode("utf-8")
        assert encoded[chunk.byte_start : chunk.byte_end] == chunk.text.encode("utf-8")


# ============================================================================
# 4. BM25Index Tests
# ============================================================================


def test_bm25_index_scoring() -> None:
    """Verify BM25 Okapi indexing and retrieval ordering."""
    chunker = SlidingWindowChunker(window_size=80, overlap_size=10)
    doc1 = "The LangGraph framework provides stateful multi-actor cyclic execution."
    doc2 = "Stanford STORM enables automated research via multi-perspective inquiries."
    doc3 = "DSPy replaces brittle prompt strings with declarative programming modules."

    chunks1 = chunker.chunk(doc1, "doc_lg")
    chunks2 = chunker.chunk(doc2, "doc_storm")
    chunks3 = chunker.chunk(doc3, "doc_dspy")
    all_chunks = chunks1 + chunks2 + chunks3

    index = BM25Index(k1=1.5, b=0.75)
    index.index(all_chunks)

    # Search for STORM
    results_storm = index.search("STORM perspective inquiries", top_k=5)
    assert len(results_storm) > 0
    top_chunk_id, top_score = results_storm[0]
    assert top_score > 0.0
    matching_chunk = next(c for c in all_chunks if c.chunk_id == top_chunk_id)
    assert "STORM" in matching_chunk.text

    # Search for LangGraph
    results_lg = index.search("LangGraph stateful cyclic", top_k=5)
    assert len(results_lg) > 0
    lg_top_id, _ = results_lg[0]
    matching_lg = next(c for c in all_chunks if c.chunk_id == lg_top_id)
    assert "LangGraph" in matching_lg.text

    # Edge cases
    assert index.search("", top_k=5) == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        index.search("test", top_k=0)


# ============================================================================
# 5. DenseVectorIndex Tests
# ============================================================================


class MockEmbedder(DenseEmbedderProtocol):
    """Mock embedder implementing DenseEmbedderProtocol."""

    def embed(self, text: str) -> list[float]:
        # Simple bag-of-words 4-dim embedding
        t = text.lower()
        return [
            1.0 if "storm" in t else 0.0,
            1.0 if "langgraph" in t else 0.0,
            1.0 if "dspy" in t else 0.0,
            1.0 if "citation" in t else 0.0,
        ]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def test_dense_vector_index_with_fallback_and_custom_embedder() -> None:
    """Verify DenseVectorIndex fallback vectorizer and custom embedder support."""
    chunker = SlidingWindowChunker(window_size=100, overlap_size=20)
    chunks = chunker.chunk(
        "Verifiable source citations prevent hallucinated claims in generated drafts.",
        document_id="doc_dense",
    )

    # 1. Fallback vectorizer (no external dependencies)
    fallback_index = DenseVectorIndex()
    fallback_index.index(chunks)
    res = fallback_index.search("verifiable citations", top_k=2)
    assert len(res) == len(chunks)
    assert res[0][1] > 0.0

    # 2. Custom embedder
    custom_embedder = MockEmbedder()
    custom_index = DenseVectorIndex(embedder=custom_embedder, embedding_dim=4)
    custom_index.index(chunks)
    res_custom = custom_index.search("citations in drafts", top_k=2)
    assert len(res_custom) == len(chunks)
    assert res_custom[0][1] > 0.0


# ============================================================================
# 6. HybridIndexer Tests
# ============================================================================


def test_hybrid_indexer_rrf_fusion() -> None:
    """Verify HybridIndexer Reciprocal Rank Fusion combines sparse and dense retrieval."""
    chunker = SlidingWindowChunker(window_size=100, overlap_size=10)
    c1 = chunker.chunk("Transformers and attention mechanisms revolutionize natural language processing.", "d1")
    c2 = chunker.chunk("Reciprocal Rank Fusion fuses ranked lists from multiple search algorithms.", "d2")
    c3 = chunker.chunk("DSPy assertions enforce mathematical citation precision and recall.", "d3")
    all_chunks = c1 + c2 + c3

    hybrid = HybridIndexer()
    hybrid.index(all_chunks)

    # Test get_chunk and has_chunk
    target_id = c2[0].chunk_id
    assert hybrid.has_chunk(target_id) is True
    assert hybrid.get_chunk(target_id).chunk_id == target_id

    with pytest.raises(ChunkNotFoundError):
        hybrid.get_chunk("non_existent_chunk_id" * 2)

    # Test hybrid RRF search
    results = hybrid.search("Reciprocal Rank Fusion search algorithms", top_k=2, rrf_k=60, alpha=0.5)
    assert len(results) > 0
    top_chunk, rrf_score = results[0]
    assert "Reciprocal Rank Fusion" in top_chunk.text
    assert rrf_score > 0.0

    # Test query helper
    chunks_only = hybrid.query("DSPy assertions citation", top_k=1)
    assert len(chunks_only) == 1
    assert "DSPy" in chunks_only[0].text

    # Validation errors
    with pytest.raises(ValueError, match="top_k must be positive"):
        hybrid.search("test", top_k=0)
    with pytest.raises(ValueError, match="rrf_k must be positive"):
        hybrid.search("test", top_k=5, rrf_k=-1)
    with pytest.raises(ValueError, match="query cannot be empty"):
        hybrid.search("   ", top_k=5)


# ============================================================================
# 7. CitationLineageEngine Tests
# ============================================================================


def test_citation_lineage_attribute_span_and_hallucination_guard() -> None:
    """Verify span attribution, boundary validation, and citation hallucination prevention."""
    engine = CitationLineageEngine()

    source_text = "Natural language inference verifies factual consistency between premise and hypothesis."
    chunk = SourceChunk.create(
        document_id="doc_src",
        text=source_text,
        char_start=0,
        char_end=len(source_text),
        byte_start=0,
        byte_end=len(source_text.encode("utf-8")),
    )
    chunk_store = {chunk.chunk_id: chunk}

    draft_text = "In expository writing, natural language inference verifies factual consistency."

    # 1. Successful exact quote attribution
    lineage = engine.attribute_span(
        draft_text=draft_text,
        span_start=23,
        span_end=77,
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim_001",
        chunk_store=chunk_store,
        exact_quote="natural language inference verifies factual consistency",
    )
    assert lineage.claim_id == "claim_001"
    assert lineage.chunk_id == chunk.chunk_id
    assert lineage.span_start == 23
    assert lineage.span_end == 77
    assert lineage.confidence_score == 1.0

    # 2. Boundary validation errors
    with pytest.raises(SpanAlignmentError):
        engine.attribute_span(
            draft_text=draft_text,
            span_start=-1,
            span_end=10,
            cited_chunk_id=chunk.chunk_id,
            claim_id="claim_001",
            chunk_store=chunk_store,
        )

    with pytest.raises(SpanAlignmentError):
        engine.attribute_span(
            draft_text=draft_text,
            span_start=10,
            span_end=len(draft_text) + 10,
            cited_chunk_id=chunk.chunk_id,
            claim_id="claim_001",
            chunk_store=chunk_store,
        )

    # 3. Chunk not found error
    with pytest.raises(ChunkNotFoundError):
        engine.attribute_span(
            draft_text=draft_text,
            span_start=0,
            span_end=10,
            cited_chunk_id="0" * 64,
            claim_id="claim_001",
            chunk_store=chunk_store,
        )

    # 4. Citation hallucination error
    with pytest.raises(CitationHallucinationError):
        engine.attribute_span(
            draft_text=draft_text,
            span_start=0,
            span_end=20,
            cited_chunk_id=chunk.chunk_id,
            claim_id="claim_001",
            chunk_store=chunk_store,
            exact_quote="This completely fabricated statement does not exist in the source passage.",
        )


def test_citation_lineage_extract_citations_from_draft() -> None:
    """Verify parsing inline citation markers from draft text."""
    engine = CitationLineageEngine()
    cid1 = "a" * 64
    cid2 = "b" * 64

    chunk1 = SourceChunk.create("doc1", "Source text 1", 0, 13, 0, 13)
    # Reassign chunk_ids to match known cid1 and cid2
    chunk1_named = SourceChunk(
        chunk_id=cid1,
        document_id="doc1",
        text="Source text 1",
        char_start=0,
        char_end=13,
        byte_start=0,
        byte_end=13,
    )
    chunk2_named = SourceChunk(
        chunk_id=cid2,
        document_id="doc2",
        text="Source text 2",
        char_start=0,
        char_end=13,
        byte_start=0,
        byte_end=13,
    )
    chunk_store = {cid1: chunk1_named, cid2: chunk2_named}

    draft = (
        f"This first assertion is verified [#{cid1}]. "
        f"A secondary finding is corroborated by independent analysis [cite: {cid2}]."
    )

    citations = engine.extract_citations_from_draft(draft, chunk_store)
    assert len(citations) == 2
    assert citations[0].chunk_id == cid1
    assert citations[1].chunk_id == cid2
    assert citations[0].span_start > 0
    assert citations[0].span_end > citations[0].span_start
