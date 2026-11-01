"""The claim-lineage door: one call to the tier, and what stays in Python.

The composition -- rank the corpus for the claim's own text, find what each
candidate and the claim say in common, attribute the claim's span to the
candidate, and scale the confidence by the share of the claim the quote
covers -- is the Kotlin tier's. These tests pin what the door still owns: the
validation of the caller's own arguments, the knobs and the model's vectors
that travel with the request, and the refusal to swallow a tier that cannot
be reached.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import SourceChunk
from lmeditor.provenance.indexer import DenseVectorIndex, HybridIndexer
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.tier import text as tier_text

CORPUS_TEXTS: tuple[str, ...] = (
    "Coral reefs shelter a quarter of all marine species and their larvae.",
    "Coral reefs shelter a quarter of all marine species",
    "Mangrove forests slow storm surge along tropical coasts.",
)

CLAIM: str = "Coral reefs shelter a quarter of all marine species"


def _corpus() -> list[SourceChunk]:
    """The corpus the claim is asked about."""
    return [
        SourceChunk(
            chunk_id=f"{index:064d}",
            document_id="doc-claim",
            text=text,
            char_start=0,
            char_end=len(text),
            byte_start=0,
            byte_end=len(text.encode("utf-8")),
        )
        for index, text in enumerate(CORPUS_TEXTS, start=1)
    ]


class _RecordingBridge:
    """A bridge that records the calls made to it."""

    def __init__(self, result: Mapping[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = dict(result or {})

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        return dict(self._result)


class _StubEmbedder:
    """A tiny deterministic embedder: the model's half, in miniature."""

    name = "stub-embedder"

    def embed(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.5]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


# --- the door asks the tier once ------------------------------------------------


def test_the_claim_is_attributed_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """One claim costs one round trip across the seam.

    The composition used to be four calls per claim: one to rank the corpus
    and two per candidate. It is one rule now, and one rule is one call.
    """
    bridge = _RecordingBridge({"lineages": []})
    monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)

    index = ProvenanceIndex.build(_corpus())
    index.lineage_for_claim("claim_1", CLAIM)

    assert [method for method, _params in bridge.calls] == ["text.lineage.attribute_claim"]


def test_the_knobs_travel_with_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retrieval knobs are the index's own, and they travel with the claim."""
    bridge = _RecordingBridge({"lineages": []})
    monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)

    index = ProvenanceIndex.build(_corpus())
    index.lineage_for_claim("claim_1", CLAIM, span_start=3, top_k=2)

    _method, params = bridge.calls[0]
    assert params["claim_id"] == "claim_1"
    assert params["claim_text"] == CLAIM
    assert params["span_start"] == 3
    assert params["top_k"] == 2
    assert params["rrf_k"] == 60
    assert params["alpha"] == 0.5
    assert params["k1"] == 1.5
    assert params["b"] == 0.75
    assert params["embedding_dim"] == 128
    # The corpus travels as data: the id of every passage and its text.
    assert params["chunks"] == [
        {"chunk_id": chunk.chunk_id, "text": chunk.text} for chunk in _corpus()
    ]
    # The span end and the draft are left out when the caller did not name them.
    assert "span_end" not in params
    assert "draft_text" not in params


def test_the_draft_and_the_span_end_travel_when_they_are_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The span the caller named travels with the request."""
    bridge = _RecordingBridge({"lineages": []})
    monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)

    index = ProvenanceIndex.build(_corpus())
    index.lineage_for_claim(
        "claim_1",
        CLAIM,
        draft_text="A draft about coral reefs.",
        span_start=0,
        span_end=12,
    )

    _method, params = bridge.calls[0]
    assert params["draft_text"] == "A draft about coral reefs."
    assert params["span_end"] == 12


def test_the_models_vectors_travel_when_a_model_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embedder is the model's half, so the door computes the vectors.

    When a model resolves, the vectors are computed here, normalised here, and
    handed to the tier as they are.
    """
    bridge = _RecordingBridge({"lineages": []})
    monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)

    index = ProvenanceIndex.build(
        _corpus(),
        indexer=HybridIndexer(vector_index=DenseVectorIndex(embedder=_StubEmbedder())),
    )
    index.lineage_for_claim("claim_1", CLAIM)

    _method, params = bridge.calls[0]
    vectors = params["dense_vectors"]
    assert set(vectors) == {chunk.chunk_id for chunk in _corpus()}
    for vector in vectors.values():
        assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0)
    query_vector = params["query_vector"]
    assert math.isclose(math.sqrt(sum(value * value for value in query_vector)), 1.0)


def test_no_model_means_no_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a model the tier computes its own term-frequency hash."""
    bridge = _RecordingBridge({"lineages": []})
    monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)

    index = ProvenanceIndex.build(
        _corpus(),
        indexer=HybridIndexer(vector_index=DenseVectorIndex(enable_semantic=False)),
    )
    index.lineage_for_claim("claim_1", CLAIM)

    _method, params = bridge.calls[0]
    assert "dense_vectors" not in params
    assert "query_vector" not in params


# --- the door refuses what the caller got wrong --------------------------------


def test_the_claim_id_must_not_be_empty() -> None:
    """The claim id is the caller's own argument, and the door checks it."""
    index = ProvenanceIndex.build(_corpus())

    with pytest.raises(ValidationError) as excinfo:
        index.lineage_for_claim("   ", CLAIM)

    assert excinfo.value.message == "a claim must carry a non-empty id"
    assert excinfo.value.field == "claim_id"


def test_the_claim_text_must_not_be_empty() -> None:
    """The claim text is the caller's own argument, and the door checks it."""
    index = ProvenanceIndex.build(_corpus())

    with pytest.raises(ValidationError) as excinfo:
        index.lineage_for_claim("claim_1", "   ")

    assert excinfo.value.message == "a claim must carry non-empty text"
    assert excinfo.value.field == "claim_text"


def test_the_top_k_must_be_positive() -> None:
    """The retrieval rule's own check is the door's too."""
    index = ProvenanceIndex.build(_corpus())

    with pytest.raises(ValidationError) as excinfo:
        index.lineage_for_claim("claim_1", CLAIM, top_k=0)

    assert excinfo.value.message == "top_k must be positive, got 0"
    assert excinfo.value.field == "top_k"


def test_a_tier_that_cannot_be_reached_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A door never falls back to the Python implementation.

    The tier being unreachable is an infrastructure failure, and it is
    reported as one.
    """

    class _BrokenBridge:
        def call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
            raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_text, "tier_bridge", lambda: _BrokenBridge())

    index = ProvenanceIndex.build(_corpus())
    with pytest.raises(BridgeUnavailableError):
        index.lineage_for_claim("claim_1", CLAIM)


# --- the answers the door gives back --------------------------------------------


def test_an_empty_corpus_produces_no_lineage() -> None:
    """There is nothing to support a claim with, so there is no lineage."""
    index = ProvenanceIndex.build([])

    assert index.lineage_for_claim("claim_1", CLAIM) == []


def test_the_confidence_is_scaled_by_the_coverage() -> None:
    """The claim is only as well supported as the share of it that was found."""
    index = ProvenanceIndex.build(_corpus())

    lineages = index.lineage_for_claim(
        "claim_1",
        "Coral reefs shelter a quarter of all marine species and their larvae",
    )

    assert lineages
    # The second passage carries every word of the claim but the last one, so
    # the quote covers seven of the claim's eight content words.
    partial = next(lineage for lineage in lineages if lineage.chunk_id == f"{2:064d}")
    assert partial.exact_quote == "Coral reefs shelter a quarter of all marine species"
    assert partial.confidence_score == 0.875


def test_the_offsets_are_code_points() -> None:
    """The offsets are code points, the way Python's ``len`` counts them.

    The draft holds an astral character before the claim, and the claim runs
    past the end of the draft, so the span has to end where the draft ends:
    at code point 34. A tier that counted UTF-16 units would end the span at
    35 -- one past the end of the draft.
    """
    index = ProvenanceIndex.build(_corpus())
    draft = "\U0001F680 Coral reefs shelter a quarter of"

    lineages = index.lineage_for_claim(
        "claim_1",
        "Coral reefs shelter a quarter of all marine species",
        draft_text=draft,
    )

    assert lineages
    assert lineages[0].span_start == 0
    assert lineages[0].span_end == 34
    # Python counts code points; the UTF-16 count is one more.
    assert len(draft) == 34
    assert len(draft.encode("utf-16-le")) // 2 == 35
