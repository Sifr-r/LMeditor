"""The frozen claim-lineage composition.

This is the original ``ProvenanceIndex.lineage_for_claim``, frozen as it was
before the Kotlin tier took the composition over. It is the **witness** the
parity harness measures the Kotlin tier against, and it is plain Python on
purpose: it never calls across the NDJSON seam, so a comparison against it
proves something. See ``lmeditor._reference`` for why that matters.

The composition is four rules in one:

1. **Rank.** The candidates are the passages that best answer the claim's own
   text, by the hybrid index's fusion of BM25 and dense retrieval.
2. **Find the support.** Each candidate is asked what it and the claim say in
   common, and how much of the claim that shared text accounts for.
3. **Attribute the span.** The claim's span in the draft is attributed to the
   candidate, with the shared text as the exact quote.
4. **Scale the confidence.** The engine scored the *quote*: it is verbatim in
   the passage. The claim is only as well supported as the share of it the
   quote covers, so the score is scaled by that share.

The three rules it composes are frozen beside it: the retrieval index and the
supporting-text rule in ``lmeditor._reference.provenance_indexer``, and the
lineage engine in ``lmeditor._reference.lineage``. Nothing here reaches the
Kotlin tier.
"""

from __future__ import annotations

from typing import Optional, Sequence

from lmeditor._reference.lineage import CitationLineageEngine
from lmeditor._reference.provenance_indexer import HybridIndexer, supporting_text
from lmeditor.core.schemas import CitationLineage, SourceChunk


class ClaimLineageIndex:
    """The frozen composition of the retrieval, supporting-text and lineage rules."""

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
        is produced by the citation-lineage engine.
        """
        candidates = self._indexer.query(claim_text, top_k=top_k)
        if not candidates:
            return []

        text = draft_text if draft_text is not None else claim_text
        start = span_start
        end = span_end if span_end is not None else start + len(claim_text)
        if end > len(text):
            end = len(text)
        if end <= start:
            end = min(len(text), start + len(claim_text))

        lineage: list[CitationLineage] = []
        for chunk in candidates:
            quote, coverage = supporting_text(claim_text, chunk.text)
            record = self._lineage.attribute_span(
                draft_text=text,
                span_start=start,
                span_end=end,
                cited_chunk_id=chunk.chunk_id,
                claim_id=claim_id,
                chunk_store=self._chunks,
                indexer=self._indexer,
                exact_quote=quote,
            )
            if quote is not None and coverage < 1.0:
                # The engine scored the *quote*: it is verbatim in the passage.
                # The claim is only as well supported as the share of it the
                # quote covers, so the score is scaled by that share.
                record = record.model_copy(
                    update={
                        "confidence_score": round(
                            record.confidence_score * coverage, 3
                        )
                    }
                )
            lineage.append(record)
        return lineage


__all__ = ["ClaimLineageIndex"]
