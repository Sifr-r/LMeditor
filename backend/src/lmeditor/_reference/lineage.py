"""The frozen citation lineage engine.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.

The marker grammar comes from :mod:`lmeditor._reference.citations` -- the
frozen copy of the canonical grammar -- and not from
:mod:`lmeditor.core.citations`, because the live module is a door to the
Kotlin tier and a witness that asks the tier what the answer is proves
nothing.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lmeditor._reference.citations import CITATION_PATTERN as CANONICAL_CITATION_PATTERN
from lmeditor._reference.citations import iter_citation_spans
from lmeditor.core.exceptions import (
    ChunkNotFoundError,
    CitationHallucinationError,
    SpanAlignmentError,
)
from lmeditor.core.schemas import CitationLineage, SourceChunk
from lmeditor.core.types import HybridIndexerProtocol


class CitationLineageEngine:
    """Engine for attributing draft text spans to ground truth source chunks."""

    # The marker grammar is owned by ``lmeditor.core.citations``; this alias
    # keeps the historical class attribute without a second, divergent regex.
    # The canonical grammar accepts a marker group (``[#a, #b]``) and a bare
    # ``[id]``, and its first group is the whole bracketed body rather than a
    # single chunk id: use ``iter_citation_spans`` to read the chunk ids out
    # of a draft.
    CITATION_MARKER_PATTERN = CANONICAL_CITATION_PATTERN

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Collapse whitespace and lowercase text for robust fuzzy comparison."""
        return " ".join(text.lower().split())

    def attribute_span(
        self,
        draft_text: str,
        span_start: int,
        span_end: int,
        cited_chunk_id: str,
        claim_id: str,
        chunk_store: dict[str, SourceChunk] | None = None,
        indexer: HybridIndexerProtocol | None = None,
        exact_quote: str | None = None,
    ) -> CitationLineage:
        """Attribute a span in draft text to an indexed SourceChunk.

        Validations:
        - Boundary check: 0 <= span_start < span_end <= len(draft_text).
        - Chunk existence: cited_chunk_id must exist in chunk_store or indexer.
        - Exact quote validation: if provided, quote must be grounded in chunk text.
        """
        doc_length = len(draft_text)

        # 1. Boundary validation
        if span_start < 0 or span_end > doc_length or span_end <= span_start:
            raise SpanAlignmentError(
                span_start=span_start,
                span_end=span_end,
                doc_length=doc_length,
            )

        # 2. Chunk existence validation
        chunk: SourceChunk | None = None
        if chunk_store is not None and cited_chunk_id in chunk_store:
            chunk = chunk_store[cited_chunk_id]
        elif indexer is not None:
            try:
                chunk = indexer.get_chunk(cited_chunk_id)
            except ChunkNotFoundError:
                raise ChunkNotFoundError(cited_chunk_id)
            except Exception:
                chunk = None

        if chunk is None:
            raise ChunkNotFoundError(cited_chunk_id)

        # 3. Exact quote validation and confidence scoring
        confidence_score = 1.0
        normalized_chunk_text = self._normalize_text(chunk.text)

        if exact_quote is not None:
            if not exact_quote.strip():
                raise CitationHallucinationError(
                    chunk_id=cited_chunk_id,
                    quote=exact_quote,
                    reason="Exact quote cannot be empty",
                )

            # Check verbatim presence
            if exact_quote in chunk.text or exact_quote.lower() in chunk.text.lower():
                confidence_score = 1.0
            else:
                # Check normalized presence
                norm_quote = self._normalize_text(exact_quote)
                if norm_quote in normalized_chunk_text:
                    confidence_score = 0.95
                else:
                    raise CitationHallucinationError(
                        chunk_id=cited_chunk_id,
                        quote=exact_quote,
                        reason="Quoted text does not appear in source chunk",
                    )
        else:
            # Evaluate span text alignment if no explicit quote is given
            span_text = draft_text[span_start:span_end]
            if span_text in chunk.text:
                confidence_score = 1.0
            elif self._normalize_text(span_text) in normalized_chunk_text:
                confidence_score = 0.95
            else:
                # Token overlap heuristic between draft span and chunk.
                #
                # The score is the *measured* overlap, not a floor above it.
                # The client renders this number as the passage's "Fidelity"
                # to the claim, so a fixed 0.5 for a passage that shares
                # nothing with the claim would tell the user that a passage
                # which supports nothing supports the claim halfway. The
                # contract in ``ProvenanceIndex.lineage_for_claim`` promises
                # the ratio, so the ratio is what is reported.
                span_tokens = set(re.findall(r"\b\w+\b", span_text.lower()))
                chunk_tokens = set(re.findall(r"\b\w+\b", chunk.text.lower()))
                if span_tokens and chunk_tokens:
                    overlap = len(span_tokens & chunk_tokens) / len(span_tokens)
                    confidence_score = min(1.0, round(overlap, 3))
                else:
                    confidence_score = 0.0

        lineage_id = f"lin_{uuid.uuid4().hex[:12]}"
        return CitationLineage(
            lineage_id=lineage_id,
            claim_id=claim_id,
            chunk_id=cited_chunk_id,
            span_start=span_start,
            span_end=span_end,
            exact_quote=exact_quote,
            confidence_score=confidence_score,
            created_at=datetime.now(timezone.utc),
        )

    def extract_citations_from_draft(
        self,
        draft_text: str,
        chunk_store: dict[str, SourceChunk],
    ) -> list[CitationLineage]:
        """Extract inline citation tags from draft text and construct CitationLineage records.

        The markers are found with :func:`lmeditor.core.citations.iter_citation_spans`
        — the same grammar the drafting side writes, the verifier reads and
        the manuscript bridge consumes — so the lineage records and the
        footnotes of an exported document can never disagree about which
        chunks a section cites.

        This engine used to carry its own regex, which accepted exactly one
        64-character hex id per bracket. A marker group such as
        ``[#chunk_a, #chunk_b]`` was therefore dropped here in its entirety,
        even though the verifier checks such a group chunk by chunk and the
        manuscript bridge writes one footnote per chunk in it: a section
        could carry no lineage at all while its footnotes were written.
        """
        citations: list[CitationLineage] = []

        for chunk_id, span_start, span_end in iter_citation_spans(draft_text):
            # The stored ids are the chunker's lower-case hex digests, so a
            # marker that spells the id in upper case must still resolve.
            # Look the id up as written first, then fall back to the
            # lower-cased form.
            resolved = next(
                (
                    candidate
                    for candidate in (chunk_id, chunk_id.lower())
                    if candidate in chunk_store
                ),
                None,
            )
            if resolved is None:
                continue
            citations.append(
                CitationLineage(
                    lineage_id=f"lin_{span_start}_{resolved[:8]}",
                    claim_id=f"claim_{resolved[:8]}_{span_start}",
                    chunk_id=resolved,
                    span_start=span_start,
                    span_end=span_end,
                    exact_quote=None,
                    confidence_score=1.0,
                    created_at=datetime.now(timezone.utc),
                )
            )

        return citations


__all__ = ["CitationLineageEngine"]
