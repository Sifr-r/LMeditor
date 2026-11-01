"""Retrieval over the ingested corpus: the API the workflow binds sections to.

Everything in this package was already implemented -- BM25, a dense vector
index, a hybrid index that fuses the two with Reciprocal Rank Fusion, and a
citation-lineage engine that attributes a span of a draft to an exact source
passage -- and none of it was called from anywhere outside its own module.
The live path bound a section to its passages by counting how many of the
outline's hard-coded keywords appeared in a chunk, and fell back to "the first
three chunks" when that produced nothing. A writer grounded on the first three
chunks is not grounded on the passages that answer the section.

This module is the seam that makes the real retrieval reachable:

    index = build_provenance_index(chunks)
    passages = index.rank_for_section("How does the citation gate work?")
    lineage = index.lineage_for_claim("claim_1", "The gate rejects unquoted spans.")

The ranking is the hybrid index's own fusion of BM25 and dense retrieval; the
lineage is produced by the real :class:`~lmeditor.provenance.lineage.CitationLineageEngine`,
with the confidence it computes from the evidence it actually found. The
composition that decides *which* passage supports *which* claim is asked of
the Kotlin tier in one call.

What stays in Python, and why:

* **The rule is the Kotlin tier's.** The composition -- rank the corpus for
  the claim's own text, find what each candidate and the claim say in
  common, attribute the claim's span to the candidate, and scale the
  confidence by the share of the claim the quote covers -- is a rule, and the
  rule is implemented once, in ``lmeditor.text.ClaimLineage``. The
  implementation this module used to carry now lives in
  ``lmeditor._reference.provenance_claim_lineage``, frozen, so the parity
  harness can still ask both sides the same question.
* **The embedder.** ``sentence_transformers`` and any injected
  ``DenseEmbedderProtocol`` are models, and a model is not a rule. This side
  computes the dense vectors and hands them to the tier, which uses them as
  they are; when no model resolves, no vectors are sent and the tier
  computes its own deterministic term-frequency hash.
* **The chunk store.** The seam carries data, not a live index, so the index
  keeps the chunks it was given and hands the tier their ids and their text.
* **The validation of the caller's own arguments.** The claim id, the claim
  text and the retrieval knobs are checked here, where the Python API's own
  messages live.
"""

from __future__ import annotations

from typing import Optional, Sequence

from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import CitationLineage, SourceChunk
from lmeditor.provenance.indexer import HybridIndexer
from lmeditor.provenance.lineage import CitationLineageEngine
from lmeditor.tier import text as tier_text


class ProvenanceIndex:
    """A searchable view of one document's chunks.

    The index owns the chunk store, so a caller never has to keep a second
    copy of the corpus around to resolve a chunk id it was handed.

    The claim rule is the tier's: :meth:`lineage_for_claim` asks the Kotlin
    tier for the whole composition in one call. The lineage engine the index
    holds is the *door* to the tier's engine, and it stays for a caller that
    wants to attribute a span of its own.
    """

    def __init__(
        self,
        chunks: Sequence[SourceChunk],
        indexer: HybridIndexer | None = None,
        lineage_engine: CitationLineageEngine | None = None,
    ) -> None:
        self._chunks: dict[str, SourceChunk] = {
            chunk.chunk_id: chunk for chunk in chunks
        }
        self._indexer = indexer if indexer is not None else HybridIndexer()
        self._indexer.index(list(self._chunks.values()))
        self._lineage = (
            lineage_engine if lineage_engine is not None else CitationLineageEngine()
        )

    @classmethod
    def build(
        cls,
        chunks: Sequence[SourceChunk],
        *,
        indexer: HybridIndexer | None = None,
    ) -> "ProvenanceIndex":
        """Build an index over ``chunks``."""
        return cls(chunks, indexer=indexer)

    @property
    def chunk_ids(self) -> list[str]:
        """Return the ids of every indexed chunk."""
        return list(self._chunks.keys())

    @property
    def embedder_name(self) -> str:
        """Return the name of the embedder the dense half is using."""
        return self._indexer.embedder_name

    @property
    def chunk_count(self) -> int:
        """Return the number of indexed chunks."""
        return len(self._chunks)

    def get(self, chunk_id: str) -> Optional[SourceChunk]:
        """Return the chunk with ``chunk_id``, or ``None`` when there is none."""
        return self._chunks.get(chunk_id)

    def get_chunk(self, chunk_id: str) -> Optional[SourceChunk]:
        """Return the chunk with ``chunk_id``, or ``None`` when there is none.

        The :class:`~lmeditor.core.types.ProvenanceIndexerProtocol` spelling
        of :meth:`get`, so the index the document is drafted from can also be
        the one the guided research loop questions.
        """
        return self.get(chunk_id)

    def has_chunk(self, chunk_id: str) -> bool:
        """Whether the index holds a chunk with ``chunk_id``.

        Part of :class:`~lmeditor.core.types.ProvenanceIndexerProtocol`.
        """
        return chunk_id in self._chunks

    def get_all_chunk_ids(self) -> Sequence[str]:
        """The ids of every chunk the index holds.

        Part of :class:`~lmeditor.core.types.ProvenanceIndexerProtocol`.
        """
        return self.chunk_ids

    def rank_for_section(self, query: str, top_k: int = 5) -> list[SourceChunk]:
        """Return the passages that best answer ``query``.

        The ranking is the hybrid index's Reciprocal Rank Fusion of BM25 and
        dense retrieval, so a passage that shares the section's vocabulary and
        a passage that is semantically close to it are both considered.

        Raises:
            ValidationError: the query is empty or ``top_k`` is not positive.
        """
        _validate_retrieval(query, top_k)
        return self._indexer.query(query, top_k=top_k)

    def query(self, query: str, top_k: int = 5) -> Sequence[SourceChunk]:
        """Return the passages that best answer ``query``.

        The :class:`~lmeditor.core.types.ProvenanceIndexerProtocol` spelling
        of :meth:`rank_for_section`: it is the same retrieval, asked in the
        shape the guided research loop asks it in, so the index a document
        is drafted from is the index that is questioned.

        Raises:
            ValidationError: the query is empty or ``top_k`` is not positive.
        """
        return self.rank_for_section(query, top_k=top_k)

    def lineage_for_claim(
        self,
        claim_id: str,
        claim_text: str,
        *,
        draft_text: Optional[str] = None,
        span_start: int = 0,
        span_end: Optional[int] = None,
        top_k: int = 5,
    ) -> list[CitationLineage]:
        """Map a claim to the source passages that support it.

        The candidates are the passages that best match the claim; each is then
        checked for the text that actually supports it, and the lineage record
        is produced by the real citation-lineage engine. The confidence is
        therefore meaningful: ``1.0`` when the claim's words appear verbatim
        in the passage, a little less when they appear after whitespace
        normalisation, and the token-overlap ratio when they do not appear at
        all.

        ``draft_text`` is the text the claim was written in; the returned
        lineage records point into it. When it is not given, the claim text
        itself is used and the span covers the whole claim.

        The rule is the Kotlin tier's, and it is asked in one call: the
        ranking, the supporting text, the span and the confidence scaling are
        one composition, and the composition used to cost one round trip to
        rank the corpus and two more for every candidate. What stays here is
        what is not a rule: the embedder, the chunk store, and the validation
        of the caller's own arguments.

        Raises:
            ValidationError: the claim id or the claim text is empty.
        """
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValidationError(
                "a claim must carry a non-empty id",
                field="claim_id",
                value=claim_id,
            )
        if not isinstance(claim_text, str) or not claim_text.strip():
            raise ValidationError(
                "a claim must carry non-empty text",
                field="claim_text",
                value=claim_text,
            )
        _validate_retrieval(claim_text, top_k)

        return tier_text.attribute_claim(
            claim_id=claim_id,
            claim_text=claim_text,
            chunks=list(self._chunks.values()),
            draft_text=draft_text,
            span_start=span_start,
            span_end=span_end,
            top_k=top_k,
            rrf_k=60,
            alpha=0.5,
            k1=self._indexer.k1,
            b=self._indexer.b,
            embedding_dim=self._indexer.embedding_dim,
            dense_vectors=self._indexer.dense_vectors(),
            query_vector=self._indexer.query_vector(claim_text),
        )


def build_provenance_index(
    chunks: Sequence[SourceChunk],
    *,
    indexer: HybridIndexer | None = None,
) -> ProvenanceIndex:
    """Build a hybrid index over ``chunks`` and return the retrieval view of it."""
    return ProvenanceIndex.build(chunks, indexer=indexer)


def _validate_retrieval(query: str, top_k: int) -> None:
    """Refuse a retrieval that cannot be answered.

    The check the retrieval rule makes before it ranks anything: an empty
    query has nothing to rank against, and a non-positive ``top_k`` asks for
    no passages at all. Both the ranking and the claim lineage make it, and
    both make it here, where the Python API's own messages live.

    Raises:
        ValidationError: the query is empty or ``top_k`` is not positive.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValidationError(
            "a retrieval query must be a non-empty string",
            field="query",
            value=query,
        )
    if top_k <= 0:
        raise ValidationError(
            f"top_k must be positive, got {top_k}",
            field="top_k",
            value=top_k,
        )


def _supporting_text(claim_text: str, chunk_text: str) -> tuple[Optional[str], float]:
    """Return the text the claim and the passage share, and the claim's coverage.

    The rule is the Kotlin tier's: the longest run of words the two have in
    common, the rejection of a run of function words, and the share of the
    claim's content words the run accounts for. The implementation this
    function used to carry now lives in
    ``lmeditor._reference.provenance_indexer``, frozen.

    This call names the two texts and reads the answer back. A tier that
    cannot be reached is an infrastructure failure, not a missing quote, and
    it is never swallowed.
    """
    return tier_text.supporting_text(claim_text, chunk_text)


__all__ = [
    "ProvenanceIndex",
    "build_provenance_index",
]
