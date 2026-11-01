"""The retrieval API the workflow binds sections to (P5).

Before this module the live path bound a section to its passages by counting
how many of the outline's hard-coded keywords appeared in a chunk, and fell
back to "the first three chunks" when that produced nothing. These tests pin
the replacement: the passages that answer a query are the ones that are
retrieved, and a claim is attributed to the passage that really contains its
supporting text.
"""

from __future__ import annotations

import pytest

from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import SourceChunk
from lmeditor.provenance.chunker import SentenceAwareChunker
from lmeditor.provenance.service import ProvenanceIndex, build_provenance_index


CORPUS: list[tuple[str, str]] = [
    ("doc", "Coral reefs shelter a quarter of all marine species and their larvae."),
    ("doc", "Mangrove forests slow storm surge and trap sediment along tropical coasts."),
    ("doc", "Medieval abbeys kept meticulous accounts of grain harvests and rents."),
    ("doc", "Monastic libraries copied manuscripts by hand in scriptoria."),
    ("doc", "Gradient descent minimises a loss surface by following its negative slope."),
    ("doc", "Attention heads learn to weigh tokens against one another."),
    ("doc", "Reciprocal rank fusion merges ranked lists without calibrating their scores."),
    ("doc", "Citation lineage binds a claim to the exact passage that supports it."),
]


def _corpus() -> list[SourceChunk]:
    """Chunk the corpus into one passage per sentence."""
    chunker = SentenceAwareChunker(chunk_size=400, overlap_size=40)
    chunks: list[SourceChunk] = []
    for document_id, text in CORPUS:
        chunks.extend(chunker.chunk(text=text, document_id=document_id))
    return chunks


def test_ranking_returns_the_passages_that_answer_the_query() -> None:
    """The passages about the queried subject come first, not the first three."""
    index = build_provenance_index(_corpus())

    ranked = index.rank_for_section("How did medieval monasteries record harvests?", top_k=3)

    assert len(ranked) == 3
    assert "Medieval abbeys" in ranked[0].text
    assert "Monastic libraries" in ranked[1].text
    # The passages that are about something else are not in the answer.
    assert all("coral" not in chunk.text.lower() for chunk in ranked)


def test_ranking_is_not_just_the_first_three_chunks() -> None:
    """The answer is not the head of the corpus: it is the relevant passages."""
    index = build_provenance_index(_corpus())

    ranked = index.rank_for_section("Citation lineage and the passages that support a claim", top_k=2)

    assert "Citation lineage" in ranked[0].text
    assert "first three" not in ranked[0].text.lower()


def test_ranking_refuses_an_empty_query() -> None:
    """An empty query is a typed error, not an empty result."""
    index = build_provenance_index(_corpus())

    with pytest.raises(ValidationError):
        index.rank_for_section("   ")
    with pytest.raises(ValidationError):
        index.rank_for_section("anything", top_k=0)


def test_the_index_knows_its_chunks() -> None:
    """A chunk id resolves to the chunk, and an unknown one to nothing."""
    index = build_provenance_index(_corpus())

    chunk = index.chunk_ids[0]
    assert index.get(chunk) is not None
    assert index.get(chunk).chunk_id == chunk
    assert index.get("not-a-chunk-id") is None
    assert index.chunk_count == len(index.chunk_ids)


def test_lineage_binds_a_claim_to_the_passage_that_contains_its_text() -> None:
    """A claim is attributed to the passage that really carries its words."""
    index = build_provenance_index(_corpus())

    claim = "Medieval abbeys kept meticulous accounts of grain harvests and rents."
    lineage = index.lineage_for_claim("claim_1", claim)

    assert lineage, "a grounded claim must produce lineage"
    top = lineage[0]
    assert top.claim_id == "claim_1"
    assert "Medieval abbeys" in index.get(top.chunk_id).text
    assert top.confidence_score == 1.0
    assert top.exact_quote == claim


def test_lineage_does_not_claim_a_passage_that_does_not_support_it() -> None:
    """The confidence falls when the claim's words are not in the passage."""
    index = build_provenance_index(_corpus())

    lineage = index.lineage_for_claim(
        "claim_2", "Sailors charted the trade winds across the Indian Ocean."
    )

    assert lineage
    top = lineage[0]
    assert top.confidence_score < 1.0


def test_lineage_reports_no_confidence_for_a_passage_that_supports_nothing() -> None:
    """Every ranked candidate is still reported, but none claims a false confidence.

    The candidates are the passages the retrieval ranked; the confidence is
    what says whether one of them actually supports the claim. A passage that
    supports nothing must not report a fixed share of confidence, because the
    client renders that number as the passage's "Fidelity" to the claim.
    """
    index = build_provenance_index(_corpus())

    lineage = index.lineage_for_claim(
        "claim_5", "Sailors charted the trade winds across the Indian Ocean."
    )

    assert lineage, "the ranked candidates are still reported"
    assert all(record.confidence_score < 0.5 for record in lineage), (
        "a passage that supports nothing must not report half a confidence"
    )


def test_lineage_refuses_an_empty_claim() -> None:
    """A claim without text or without an id is a typed error."""
    index = build_provenance_index(_corpus())

    with pytest.raises(ValidationError):
        index.lineage_for_claim("", "some text")
    with pytest.raises(ValidationError):
        index.lineage_for_claim("claim_3", "   ")


def test_lineage_points_into_the_draft_it_was_written_in() -> None:
    """The lineage records the span of the draft the claim came from."""
    index = build_provenance_index(_corpus())

    draft = "An opening line.\nMedieval abbeys kept meticulous accounts of grain harvests and rents."
    start = draft.index("Medieval abbeys")
    lineage = index.lineage_for_claim(
        "claim_4",
        "Medieval abbeys kept meticulous accounts of grain harvests and rents.",
        draft_text=draft,
        span_start=start,
    )

    assert lineage
    assert lineage[0].span_start == start
    assert lineage[0].span_end == len(draft)


def test_the_index_reports_the_embedder_it_uses() -> None:
    """The retrieval index names its embedder, so a degradation is visible."""
    index = build_provenance_index(_corpus())

    assert index.embedder_name
    assert isinstance(index.embedder_name, str)


def test_an_empty_corpus_ranks_nothing() -> None:
    """An index over no chunks returns nothing rather than raising."""
    index = ProvenanceIndex.build([])

    assert index.chunk_ids == []
    assert index.rank_for_section("anything") == []
    assert index.lineage_for_claim("claim_1", "anything") == []
