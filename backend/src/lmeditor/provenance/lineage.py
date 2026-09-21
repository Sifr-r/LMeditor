"""Citation lineage and token-span attribution engine linking claims to source chunks."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lmeditor.core.exceptions import (
    ChunkNotFoundError,
    CitationHallucinationError,
    SpanAlignmentError,
)
from lmeditor.core.schemas import CitationLineage, SourceChunk
from lmeditor.core.types import HybridIndexerProtocol


class CitationLineageEngine:
    """Engine for attributing draft text spans to ground truth source chunks."""

    # Regex matching citation tags such as [#<sha256>], [cite: <sha256>], or [<sha256>]
    CITATION_MARKER_PATTERN = re.compile(r"\[(?:#|cite:\s*)?([a-f0-9]{64})\]", re.IGNORECASE)

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
                # Token overlap heuristic between draft span and chunk
                span_tokens = set(re.findall(r"\b\w+\b", span_text.lower()))
                chunk_tokens = set(re.findall(r"\b\w+\b", chunk.text.lower()))
                if span_tokens and chunk_tokens:
                    overlap = len(span_tokens & chunk_tokens) / len(span_tokens)
                    confidence_score = max(0.5, min(1.0, round(overlap, 3)))
                else:
                    confidence_score = 0.5

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
        """Extract inline citation tags from draft text and construct CitationLineage records."""
        citations: list[CitationLineage] = []

        for match in self.CITATION_MARKER_PATTERN.finditer(draft_text):
            chunk_id = match.group(1).lower()
            span_start, span_end = match.span()

            if chunk_id in chunk_store:
                claim_id = f"claim_{chunk_id[:8]}_{span_start}"
                lineage = CitationLineage(
                    lineage_id=f"lin_{span_start}_{chunk_id[:8]}",
                    claim_id=claim_id,
                    chunk_id=chunk_id,
                    span_start=span_start,
                    span_end=span_end,
                    exact_quote=None,
                    confidence_score=1.0,
                    created_at=datetime.now(timezone.utc),
                )
                citations.append(lineage)

        return citations


__all__ = ["CitationLineageEngine"]
