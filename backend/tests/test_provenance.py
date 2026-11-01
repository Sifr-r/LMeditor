"""Comprehensive test suite for LMeditor provenance chunker, indexer, and lineage engine."""

from __future__ import annotations

import re
import pytest

from lmeditor.core.citations import iter_citation_spans
from lmeditor.core.exceptions import (
    ChunkNotFoundError,
    CitationHallucinationError,
    SpanAlignmentError,
)
from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import DenseEmbedderProtocol
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
    FALLBACK_EMBEDDER_NAME,
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
    """Verify DenseVectorIndex fallback vectorizer and custom embedder support.

    The fallback has to be asked for explicitly: the index now prefers a real
    sentence-transformers model and only falls back to the deterministic hash
    when no model can be loaded (and says which one it is using).
    """
    chunker = SlidingWindowChunker(window_size=100, overlap_size=20)
    chunks = chunker.chunk(
        "Verifiable source citations prevent hallucinated claims in generated drafts.",
        document_id="doc_dense",
    )

    # 1. Fallback vectorizer (no external dependencies)
    fallback_index = DenseVectorIndex(enable_semantic=False)
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


def test_attributing_a_span_to_an_unrelated_passage_reports_no_confidence() -> None:
    """A passage that shares nothing with the span supports it not at all.

    The client renders the lineage's confidence as the passage's "Fidelity" to
    the claim, so a constant floor would tell the reader that a passage which
    supports nothing supports the claim halfway. The confidence is the
    measured overlap, so a passage with no overlap reports none.
    """
    engine = CitationLineageEngine()

    source_text = "Coral reefs shelter a quarter of all marine species and their larvae."
    chunk = SourceChunk.create(
        document_id="doc_src",
        text=source_text,
        char_start=0,
        char_end=len(source_text),
        byte_start=0,
        byte_end=len(source_text.encode("utf-8")),
    )
    chunk_store = {chunk.chunk_id: chunk}

    draft_text = "Mangrove forests slow storm surge along tropical coasts."

    lineage = engine.attribute_span(
        draft_text=draft_text,
        span_start=0,
        span_end=len(draft_text),
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim_002",
        chunk_store=chunk_store,
    )

    assert lineage.exact_quote is None
    assert lineage.confidence_score == 0.0, (
        "a passage with no shared wording must report no confidence"
    )


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



def _chunk(chunk_id: str, text: str = "Source text") -> SourceChunk:
    """A source chunk with a known id."""
    return SourceChunk(
        chunk_id=chunk_id,
        document_id="doc1",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text),
    )


def _chunk_id(label: str) -> str:
    """A realistic chunk id: the deterministic digest the chunker produces.

    ``SourceChunk`` constrains ``chunk_id`` to a 64-character lower-case hex
    digest, so a test chunk can only ever carry an id of that shape.
    """
    return compute_chunk_id("doc1", 0, len(label), label)


def test_a_citation_group_yields_one_record_per_cited_chunk() -> None:
    """A marker group cites several chunks, and each gets its own record.

    The engine used to carry its own regex, which accepted exactly one
    64-character hex id per bracket. A group such as ``[#a, #b]`` was
    dropped here in its entirety, even though the verifier checks such a
    group chunk by chunk and the manuscript bridge writes one footnote per
    chunk in it: the section carried no lineage while its footnotes were
    written.
    """
    engine = CitationLineageEngine()
    first = _chunk_id("first")
    second = _chunk_id("second")
    store = {first: _chunk(first), second: _chunk(second)}

    citations = engine.extract_citations_from_draft(
        f"A claim [#{first}, #{second}].", store
    )

    assert [citation.chunk_id for citation in citations] == [first, second]
    # Both records point at the same marker.
    assert citations[0].span_start == citations[1].span_start
    assert citations[0].span_end == citations[1].span_end


def test_the_lineage_engine_finds_exactly_what_the_canonical_grammar_finds() -> None:
    """The engine and the canonical grammar never disagree about a citation.

    ``lmeditor.core.citations`` is the single source of truth for the
    marker grammar: the drafting side writes it, the verifier reads it and
    the manuscript bridge consumes it. If the lineage engine disagreed, a
    section could cite a chunk the verifier never checked.
    """
    engine = CitationLineageEngine()
    alpha = _chunk_id("alpha")
    beta = _chunk_id("beta")
    gamma = _chunk_id("gamma")
    delta = _chunk_id("delta")
    draft = (
        f"A claim [#{alpha}]. Another [#{beta}, #{gamma}]. "
        f"A link [text](https://example.org). A bare [{delta}] "
        "and a picture ![alt](image.png)."
    )
    store = {
        chunk_id: _chunk(chunk_id)
        for chunk_id in (alpha, beta, gamma, delta)
    }

    found = engine.extract_citations_from_draft(draft, store)
    canonical = [
        chunk_id
        for chunk_id, _start, _end in iter_citation_spans(draft)
        if chunk_id in store
    ]

    assert [citation.chunk_id for citation in found] == canonical
    # Pinned so the comparison above cannot pass vacuously.
    assert canonical == [alpha, beta, gamma, delta]
    # The markdown link and the image are not citations.
    assert "text" not in canonical
    assert "alt" not in canonical


def test_a_citation_to_a_chunk_that_is_not_in_the_store_is_not_recorded() -> None:
    """Only chunks that are actually in the store produce a lineage record."""
    engine = CitationLineageEngine()
    known = _chunk_id("known")
    unknown = _chunk_id("unknown")
    store = {known: _chunk(known)}

    citations = engine.extract_citations_from_draft(
        f"Grounded [#{known}] but ungrounded [#{unknown}].", store
    )

    assert [citation.chunk_id for citation in citations] == [known]


def test_an_upper_case_chunk_id_resolves_to_its_stored_form() -> None:
    """A marker may spell the id in upper case; the store holds lower case."""
    engine = CitationLineageEngine()
    chunk_id = _chunk_id("upper")
    store = {chunk_id: _chunk(chunk_id)}

    citations = engine.extract_citations_from_draft(
        f"A claim [#{chunk_id.upper()}].", store
    )

    assert [citation.chunk_id for citation in citations] == [chunk_id]


# ============================================================================
# 8. Semantic embeddings, token estimates and source-aware chunking
# ============================================================================


def test_the_dense_index_names_the_fallback_embedder() -> None:
    """The index says which embedder it is using, so a degradation is visible."""
    index = DenseVectorIndex(enable_semantic=False)
    assert index.embedder_name == FALLBACK_EMBEDDER_NAME


def test_a_model_that_cannot_be_loaded_is_reported_as_the_fallback() -> None:
    """A model that is not on disk degrades to the hash, and says so."""
    chunker = SlidingWindowChunker(window_size=100, overlap_size=20)
    chunks = chunker.chunk("A passage about grounding.", document_id="doc_embed")

    index = DenseVectorIndex(semantic_model="not-a-real-model-for-tests")
    index.index(chunks)

    assert index.embedder_name == FALLBACK_EMBEDDER_NAME
    assert index.search("grounding", top_k=1)


def test_the_semantic_model_is_used_when_it_is_available() -> None:
    """A real sentence-transformers model is used when the cache has it."""
    embedder = SentenceTransformerEmbedder(SEMANTIC_MODEL_NAME)
    if not embedder.is_available():
        pytest.skip("the sentence-transformers model is not in the local cache")

    chunker = SlidingWindowChunker(window_size=100, overlap_size=20)
    chunks = chunker.chunk("A passage about grounding.", document_id="doc_embed")

    index = DenseVectorIndex()
    index.index(chunks)

    assert index.embedder_name == SEMANTIC_MODEL_NAME


def test_the_hybrid_indexer_reports_its_embedder() -> None:
    """The hybrid index names the embedder its dense half uses."""
    hybrid = HybridIndexer(vector_index=DenseVectorIndex(enable_semantic=False))
    assert hybrid.embedder_name == FALLBACK_EMBEDDER_NAME


def test_a_chunk_carries_a_token_estimate() -> None:
    """A chunk reports a token estimate instead of the default zero."""
    chunker = SentenceAwareChunker(chunk_size=200, overlap_size=20)
    text = "A sentence with a handful of words in it. Another sentence follows it."
    chunks = chunker.chunk(text=text, document_id="doc_tokens")

    assert chunks
    for chunk in chunks:
        assert chunk.token_count > 0
        assert chunk.token_count == estimate_token_count(chunk.text)


def test_the_token_estimate_is_documented_as_an_estimate() -> None:
    """The estimate is a character-based approximation, not a tokenizer count."""
    assert estimate_token_count("") == 0
    assert estimate_token_count("four") == 1
    assert estimate_token_count("x" * 400) == 100


def test_the_source_chunker_never_cuts_a_sentence_in_half() -> None:
    """The chunks are whole sentences, not fragments of them."""
    sentences = [
        f"Sentence number {index} carries a little weight." for index in range(1, 9)
    ]
    text = " ".join(sentences)

    chunker = SentenceAwareChunker(chunk_size=60, overlap_size=0)
    chunks = chunker.chunk(text=text, document_id="doc_sentences")

    assert chunks
    for chunk in chunks:
        assert chunk.text.strip() in sentences


def test_the_overlap_repeats_whole_sentences() -> None:
    """The repeated text between two chunks is a sentence, not a fragment."""
    sentences = [
        f"Sentence number {index} carries a little weight." for index in range(1, 9)
    ]
    text = " ".join(sentences)

    chunker = SentenceAwareChunker(chunk_size=60, overlap_size=35)
    chunks = chunker.chunk(text=text, document_id="doc_overlap")

    assert len(chunks) > 1
    for chunk in chunks:
        for sentence in sentences:
            if sentence in chunk.text:
                # A sentence that appears must appear whole.
                assert chunk.text.count(sentence) == 1


def test_the_source_chunker_alias_points_at_the_source_aware_chunker() -> None:
    """The old name no longer promises more than the chunker delivers."""
    assert SourceChunker is SentenceAwareChunker


def test_the_source_aware_chunker_rejects_an_impossible_window() -> None:
    """The window and overlap are validated as the other chunkers validate them."""
    with pytest.raises(ValueError):
        SentenceAwareChunker(chunk_size=0)
    with pytest.raises(ValueError):
        SentenceAwareChunker(chunk_size=100, overlap_size=100)
