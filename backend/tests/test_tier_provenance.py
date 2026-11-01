"""The provenance doors reach the Kotlin tier.

The chunker and the lineage engine used to carry their own implementations of
rules the Kotlin tier owns. They are doors now: the Python API is the same,
but the answer comes from the tier. These tests prove that -- an answer the
tier did not give is not an answer the door may give -- and that a tier which
cannot be reached is reported as such, rather than quietly answered from
Python.
"""

from __future__ import annotations

from typing import Any

import pytest

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import ChunkNotFoundError
from lmeditor.core.schemas import SourceChunk
from lmeditor.provenance import chunker as provenance_chunker
from lmeditor.provenance import indexer as provenance_indexer
from lmeditor.provenance import lineage as provenance_lineage
from lmeditor.provenance import service as provenance_service
from lmeditor.tier import core as tier_core
from lmeditor.tier import text as tier_text

#: Every test here asks the tier something, so none of them may run without it.
pytestmark = pytest.mark.usefixtures("kotlin_tier")

#: A passage short enough to be one chunk and long enough to be quoted.
_PASSAGE = "Mangrove forests slow storm surge."


def _chunk() -> SourceChunk:
    """A source chunk with the id the tier mints for it."""
    return tier_core.create_source_chunk(
        document_id="doc_door",
        text=_PASSAGE,
        char_start=0,
        char_end=len(_PASSAGE),
        byte_start=0,
        byte_end=len(_PASSAGE.encode("utf-8")),
    )


def test_the_chunker_door_answers_with_the_tiers_chunks() -> None:
    """The chunks the door returns are the chunks the tier returns.

    The window is part of the request, so the comparison asks the tier for
    the same window the door was built with.
    """
    text = "First sentence. Second sentence. Third sentence."

    door = provenance_chunker.SentenceAwareChunker()
    assert door.chunk(text=text, document_id="doc_door") == tier_text.chunk(
        text,
        "doc_door",
        window_size=door.window_size,
        overlap_size=door.overlap_size,
    )


def test_the_chunk_id_door_answers_with_the_tiers_digest() -> None:
    """The chunk id is the tier's digest, minted on the Kotlin side."""
    chunk_id = provenance_chunker.compute_chunk_id(
        "doc_door", 0, len(_PASSAGE), _PASSAGE
    )

    chunk = tier_core.create_source_chunk(
        document_id="doc_door",
        text=_PASSAGE,
        char_start=0,
        char_end=len(_PASSAGE),
        byte_start=0,
        byte_end=len(_PASSAGE.encode("utf-8")),
    )
    assert chunk_id == chunk.chunk_id


def test_the_lineage_door_answers_with_the_tiers_lineage() -> None:
    """The lineage the door returns is the lineage the tier returns.

    The timestamp is the one field that cannot be compared: the tier stamps
    each answer with the moment it was asked.
    """
    chunk = _chunk()
    draft = f"A claim about mangroves. [{chunk.chunk_id}]"

    door = provenance_lineage.CitationLineageEngine().attribute_span(
        draft_text=draft,
        span_start=0,
        span_end=24,
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim_1",
        chunk_store={chunk.chunk_id: chunk},
    )
    tier = tier_text.attribute_span(
        draft, 0, 24, chunk.chunk_id, "claim_1", [chunk]
    )

    assert door.lineage_id == tier.lineage_id
    assert door.claim_id == tier.claim_id
    assert door.chunk_id == tier.chunk_id
    assert (door.span_start, door.span_end) == (tier.span_start, tier.span_end)
    assert door.exact_quote == tier.exact_quote
    assert door.confidence_score == tier.confidence_score
    # The tier mints a deterministic id, so the same span and the same chunk
    # always name the same lineage.
    assert door.lineage_id == f"lin_{0}_{chunk.chunk_id[:8]}"


def test_the_draft_door_answers_with_the_tiers_records() -> None:
    """The citation records the door returns are the tier's records."""
    chunk = _chunk()
    draft = f"A claim. [#{chunk.chunk_id}]"

    door = provenance_lineage.CitationLineageEngine().extract_citations_from_draft(
        draft, {chunk.chunk_id: chunk}
    )
    tier = tier_text.extract_citations_from_draft(draft, [chunk])

    assert [
        (record.chunk_id, record.claim_id, record.span_start, record.span_end)
        for record in door
    ] == [
        (record.chunk_id, record.claim_id, record.span_start, record.span_end)
        for record in tier
    ]


def test_the_lineage_door_resolves_a_chunk_through_the_indexer() -> None:
    """A chunk that is not in the store is looked up in the index.

    The seam carries data, not a live index, so the lookup stays in Python
    and the rule about the chunk -- the boundary check, the grounding check
    and the confidence score -- stays in the tier.
    """
    chunk = _chunk()

    class _Indexer:
        """An index that holds exactly one chunk."""

        def get_chunk(self, chunk_id: str) -> SourceChunk:
            if chunk_id != chunk.chunk_id:
                raise ChunkNotFoundError(chunk_id)
            return chunk

    lineage = provenance_lineage.CitationLineageEngine().attribute_span(
        draft_text=_PASSAGE,
        span_start=0,
        span_end=len(_PASSAGE),
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim_1",
        chunk_store={},
        indexer=_Indexer(),  # type: ignore[arg-type]
    )
    assert lineage.chunk_id == chunk.chunk_id
    assert lineage.confidence_score == 1.0


def test_a_chunk_that_neither_the_store_nor_the_index_holds_is_not_found() -> None:
    """The chunk that cannot be resolved is reported as missing."""

    class _EmptyIndexer:
        """An index that holds nothing."""

        def get_chunk(self, chunk_id: str) -> SourceChunk:
            raise ChunkNotFoundError(chunk_id)

    with pytest.raises(ChunkNotFoundError):
        provenance_lineage.CitationLineageEngine().attribute_span(
            draft_text=_PASSAGE,
            span_start=0,
            span_end=len(_PASSAGE),
            cited_chunk_id="0" * 64,
            claim_id="claim_1",
            chunk_store={},
            indexer=_EmptyIndexer(),  # type: ignore[arg-type]
        )


def test_a_missing_tier_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tier being unreachable is an infrastructure failure, not a bad input.

    A door that answered from Python when the tier is down would hide the
    very drift the tier exists to remove, so the failure has to reach the
    caller as it is.
    """

    def unavailable() -> Any:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_text, "tier_bridge", unavailable)

    with pytest.raises(BridgeUnavailableError):
        provenance_chunker.SentenceAwareChunker().chunk(
            text=_PASSAGE, document_id="doc_door"
        )

    with pytest.raises(BridgeUnavailableError):
        provenance_lineage.CitationLineageEngine().attribute_span(
            draft_text=_PASSAGE,
            span_start=0,
            span_end=len(_PASSAGE),
            cited_chunk_id="0" * 64,
            claim_id="claim_1",
            chunk_store={},
        )


# ---------------------------------------------------------------------------
# The retrieval index is a door too
# ---------------------------------------------------------------------------


class _FixedEmbedder:
    """An embedder that maps a text to one of two unit vectors."""

    name = "fixed-embedder"

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "mangrove" in text.lower() else [0.0, 1.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


def _recording_rank(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the tier's ranking with one that records what it was asked.

    The point of these tests is not that the door answers well -- the parity
    harness proves that -- but that the door answers *by asking*. A door that
    computed the answer itself would pass every other test and still be the
    drift the tier exists to remove.
    """
    calls: list[dict[str, Any]] = []

    def record(chunks: Any, query: str, **kwargs: Any) -> list[tuple[str, float]]:
        calls.append({"chunks": list(chunks), "query": query, **kwargs})
        return []

    monkeypatch.setattr(tier_text, "rank_retrieval", record)
    return calls


def test_the_sparse_door_hands_the_tier_its_own_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two knobs of the sparse rule travel with the request."""
    calls = _recording_rank(monkeypatch)

    index = provenance_indexer.BM25Index(k1=2.0, b=0.4)
    index.index([_chunk()])
    index.search("mangrove", top_k=3)

    assert calls[0]["mode"] == "bm25"
    assert calls[0]["k1"] == 2.0
    assert calls[0]["b"] == 0.4
    assert calls[0]["top_k"] == 3


def test_the_dense_door_hands_the_tier_the_vectors_the_model_produced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model is a model: the vectors it produced travel to the tier."""
    calls = _recording_rank(monkeypatch)
    chunk = _chunk()

    index = provenance_indexer.DenseVectorIndex(
        embedder=_FixedEmbedder(), embedding_dim=2
    )
    index.index([chunk])
    index.search("mangrove", top_k=3)

    assert calls[0]["mode"] == "dense"
    assert calls[0]["dense_vectors"] == {chunk.chunk_id: [1.0, 0.0]}
    assert calls[0]["query_vector"] == [1.0, 0.0]


def test_the_dense_door_hands_the_tier_no_vectors_when_no_model_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no model, the tier computes its own deterministic hash vectors."""
    calls = _recording_rank(monkeypatch)

    index = provenance_indexer.DenseVectorIndex(enable_semantic=False)
    index.index([_chunk()])
    index.search("mangrove", top_k=3)

    assert calls[0]["dense_vectors"] is None
    assert calls[0]["query_vector"] is None


def test_the_hybrid_door_asks_the_tier_for_the_fusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fusion is the tier's, and the knobs of the fusion travel too."""
    calls = _recording_rank(monkeypatch)

    index = provenance_indexer.HybridIndexer()
    index.index([_chunk()])
    index.search("mangrove", top_k=3, rrf_k=30, alpha=0.25)

    assert calls[0]["mode"] == "hybrid"
    assert calls[0]["rrf_k"] == 30
    assert calls[0]["alpha"] == 0.25


def test_the_sparse_door_answers_with_the_tiers_ranking() -> None:
    """The ranking the door returns is the ranking the tier returns."""
    chunk = _chunk()

    index = provenance_indexer.BM25Index()
    index.index([chunk])

    assert index.search("mangrove", top_k=3) == tier_text.rank_retrieval(
        [chunk], "mangrove", mode="bm25", top_k=3
    )


def test_the_hybrid_door_answers_with_the_tiers_ranking() -> None:
    """The hybrid door returns the tier's passages, with the tier's scores."""
    chunk = _chunk()

    index = provenance_indexer.HybridIndexer()
    index.index([chunk])
    door = index.search("mangrove", top_k=3)

    assert [found.chunk_id for found, _score in door] == [chunk.chunk_id]
    assert door[0][1] == tier_text.rank_retrieval(
        [chunk], "mangrove", mode="hybrid", top_k=3
    )[0][1]


def test_the_embedder_name_still_names_the_model_or_the_fallback() -> None:
    """A degradation is never silent."""
    assert (
        provenance_indexer.DenseVectorIndex(enable_semantic=False).embedder_name
        == provenance_indexer.FALLBACK_EMBEDDER_NAME
    )
    assert (
        provenance_indexer.DenseVectorIndex(
            embedder=_FixedEmbedder(), enable_semantic=False
        ).embedder_name
        == "fixed-embedder"
    )


def test_a_missing_tier_is_not_swallowed_by_the_retrieval_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retrieval door has no Python answer to fall back to."""
    index = provenance_indexer.BM25Index()
    index.index([_chunk()])

    def unavailable() -> Any:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_text, "tier_bridge", unavailable)

    with pytest.raises(BridgeUnavailableError):
        index.search("mangrove", top_k=3)


def test_the_supporting_text_door_answers_with_the_tiers_answer() -> None:
    """The shared text and the coverage are the tier's answer."""
    claim = "mangrove forests slow storm surge"
    passage = "Mangrove forests slow storm surge near the coast."

    assert provenance_service._supporting_text(claim, passage) == (
        tier_text.supporting_text(claim, passage)
    )


def test_the_confidence_is_still_scaled_by_the_coverage() -> None:
    """The scaling stays in Python: it is arithmetic over the tier's record.

    The claim shares only part of the passage, so the confidence of the
    record is scaled down by the share the quote covers.
    """
    chunk = _chunk()
    draft = "Mangrove forests slow storm surge near the coast"
    index = provenance_service.ProvenanceIndex.build([chunk])

    records = index.lineage_for_claim(
        "claim_1",
        draft,
        draft_text=draft,
    )

    assert records
    record = records[0]
    quote, coverage = provenance_service._supporting_text(draft, chunk.text)
    assert quote is not None
    assert coverage < 1.0

    # The record the engine would have produced without the scaling.
    base = provenance_lineage.CitationLineageEngine().attribute_span(
        draft_text=draft,
        span_start=0,
        span_end=len(draft),
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim_1",
        chunk_store={chunk.chunk_id: chunk},
        exact_quote=quote,
    )
    assert record.confidence_score == round(base.confidence_score * coverage, 3)
