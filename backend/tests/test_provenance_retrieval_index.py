"""The retrieval door names the corpus it asks about.

The tier indexes a corpus when the corpus arrives and answers the queries
that name it. These tests are about that arrangement: the corpus is handed
over once, the name is the digest of the corpus, a corpus that changed is
never answered from the index of the corpus it replaced, and the answers are
the answers the rule has always given.
"""

from __future__ import annotations

from typing import Any

import pytest

from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.core.schemas import SourceChunk
from lmeditor.provenance import indexer as provenance_indexer
from lmeditor.tier import core as tier_core
from lmeditor.tier import text as tier_text

#: Every test here asks the tier something, so none of them may run without it.
pytestmark = pytest.mark.usefixtures("kotlin_tier")

#: A passage about mangroves and one about reefs.
_MANGROVE = "Mangrove forests slow storm surge near the coast."
_REEF = "Reef coral bleaching accelerates in warm water."


def _chunk(text: str) -> SourceChunk:
    """A source chunk with the id the tier mints for it."""
    return tier_core.create_source_chunk(
        document_id="doc_retrieval",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _corpus() -> list[SourceChunk]:
    """A two-passage corpus."""
    return [_chunk(_MANGROVE), _chunk(_REEF)]


def _recording_rank(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the tier's ranking with one that records what it was asked."""
    calls: list[dict[str, Any]] = []

    def record(chunks: Any, query: str, **kwargs: Any) -> list[tuple[str, float]]:
        calls.append({"chunks": list(chunks), "query": query, **kwargs})
        return []

    monkeypatch.setattr(tier_text, "rank_retrieval", record)
    return calls


def test_the_door_hands_the_tier_the_corpus_once_and_names_it_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corpus travels with the request until the tier holds it.

    A query that carries the whole corpus again costs the corpus on every
    query. The door sends the corpus when it changes and names it after
    that, so a query costs the query.
    """
    calls = _recording_rank(monkeypatch)
    corpus = _corpus()

    index = provenance_indexer.HybridIndexer()
    index.index(corpus)
    index.search("mangrove forests", top_k=3)
    index.search("reef coral", top_k=3)

    first = calls[0]["chunks"]
    second = calls[1]["chunks"]
    # The first request carries the corpus, behind the chunk that names it.
    assert len(first) == len(corpus) + 1
    assert first[0].chunk_id == provenance_indexer.CORPUS_DESCRIPTOR_CHUNK_ID
    assert [chunk.chunk_id for chunk in first[1:]] == [chunk.chunk_id for chunk in corpus]
    # The second request names it and nothing else.
    assert len(second) == 1
    assert second[0].chunk_id == provenance_indexer.CORPUS_DESCRIPTOR_CHUNK_ID
    assert second[0].text == first[0].text


def test_a_corpus_that_changed_is_the_corpus_the_next_query_sees() -> None:
    """A corpus that changed is never answered from the index of the old one.

    The name of a corpus is the digest of its passages, so a corpus that
    changed has a different name, and the tier is handed the new corpus
    rather than the index it built for the old one.
    """
    index = provenance_indexer.HybridIndexer()
    index.index([_chunk(_MANGROVE)])
    first = index.search("mangrove forests", top_k=3)
    assert first and first[0][0].text == _MANGROVE

    index.index([_chunk(_REEF)])
    second = index.search("reef coral", top_k=3)

    assert second
    assert second[0][0].text == _REEF
    assert all(found.text == _REEF for found, _score in second)


def test_a_corpus_that_changed_is_not_the_corpus_the_tier_was_holding() -> None:
    """The tier is told about the change, and told about it by name.

    The name of the new corpus is the digest of its passages, so the tier
    holds the new corpus under a name of its own and never under the name
    of the corpus it replaced.
    """
    index = provenance_indexer.HybridIndexer()
    index.index([_chunk(_MANGROVE)])
    index.search("mangrove forests", top_k=3)
    first_name = provenance_indexer.corpus_digest([_chunk(_MANGROVE)])

    index.index([_chunk(_REEF)])
    index.search("reef coral", top_k=3)
    second_name = provenance_indexer.corpus_digest([_chunk(_REEF)])

    assert first_name != second_name


def test_the_door_answers_with_the_tiers_own_ranking_of_the_same_corpus() -> None:
    """The answers are the answers the tier gives for the same corpus.

    The door names the corpus; the rule is still the tier's, and it gives
    the same passages in the same order with the same scores.
    """
    corpus = _corpus()

    door = provenance_indexer.HybridIndexer()
    door.index(corpus)
    answered = door.search("mangrove forests", top_k=3)

    carried = tier_text.rank_retrieval(corpus, "mangrove forests", mode="hybrid", top_k=3)

    assert [chunk.chunk_id for chunk, _score in answered] == [chunk_id for chunk_id, _s in carried]
    assert [score for _chunk, score in answered] == [score for _chunk_id, score in carried]


def test_the_door_answers_with_the_frozen_rules_answers() -> None:
    """The answers are the answers the frozen rule gives.

    ``lmeditor._reference.provenance_indexer`` is the Python rule the tier
    was ported from, frozen so the parity harness can still ask both sides
    the same question. A change in the speed of the rule must not change
    what the rule says.
    """
    from lmeditor._reference.provenance_indexer import HybridIndexer as ReferenceIndexer

    corpus = _corpus()

    door = provenance_indexer.HybridIndexer()
    door.index(corpus)
    answered = door.search("mangrove forests surge", top_k=3)

    reference = ReferenceIndexer()
    reference.index(corpus)
    expected = reference.search("mangrove forests surge", top_k=3)

    assert [chunk.chunk_id for chunk, _score in answered] == [
        chunk.chunk_id for chunk, _score in expected
    ]
    assert [score for _chunk, score in answered] == pytest.approx(
        [score for _chunk, score in expected]
    )


def test_the_door_sends_the_corpus_again_when_the_tier_does_not_hold_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tier that lost the corpus is told about it again.

    The tier holds the corpora it has indexed until it pushes them out, and
    it may restart. A request that names a corpus the tier does not hold is
    reported, and the door answers it by handing the corpus over again.
    """
    calls: list[list[SourceChunk]] = []
    real = tier_text.rank_retrieval

    def flaky(chunks: Any, query: str, **kwargs: Any) -> list[tuple[str, float]]:
        sent = list(chunks)
        calls.append(sent)
        if len(sent) == 1:
            # A request that names a corpus: the tier does not hold it.
            raise KotlinDomainError(
                "CorpusNotResidentError",
                "the corpus is not held by the tier",
                {"corpus_key": sent[0].text},
            )
        return real(chunks, query, **kwargs)

    monkeypatch.setattr(tier_text, "rank_retrieval", flaky)

    corpus = _corpus()
    index = provenance_indexer.HybridIndexer()
    index.index(corpus)
    first = index.search("mangrove forests", top_k=3)
    second = index.search("reef coral", top_k=3)

    assert first and second
    # The corpus, then the name of it, then the corpus again.
    assert len(calls) == 3
    assert len(calls[0]) == len(corpus) + 1
    assert len(calls[1]) == 1
    assert len(calls[2]) == len(corpus) + 1


def test_the_digest_of_a_corpus_is_its_name() -> None:
    """The digest is the name of a corpus, and both sides compute it.

    The tier computes the same digest over the passages it receives, so
    the two sides agree on what a corpus is called. The value is pinned
    here and in the tier's own test.
    """
    mangrove = SourceChunk(
        chunk_id="a" * 64, document_id="doc_retrieval", text="Mangrove forests slow storm surge."
    )
    reef = SourceChunk(
        chunk_id="b" * 64, document_id="doc_retrieval", text="reef coral bleaching."
    )

    assert (
        provenance_indexer.corpus_digest([mangrove])
        == "b3ae327aac050fb6fddb2319dadc8160bb480a5c5edd951612a8f71a4dde8bfb"
    )
    assert (
        provenance_indexer.corpus_digest([mangrove, reef])
        == "4b84bfb59c6ebbcdd75619432fa092f40af53eb2e08d55743bc37982d066b8e9"
    )
