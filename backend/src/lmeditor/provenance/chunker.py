"""Text chunking implementations with deterministic hashing and offset tracking."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from lmeditor.core.schemas import SourceChunk


def compute_chunk_id(
    document_id: str,
    char_start: int,
    char_end: int,
    text: str,
) -> str:
    """Compute deterministic SHA-256 hash formatted as lowercase hex string.

    Payload format: "{document_id}:{char_start}:{char_end}:{text}"
    """
    payload = f"{document_id}:{char_start}:{char_end}:{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_byte_offsets(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """Compute UTF-8 byte offsets for a character span in a string.

    Returns (byte_start, byte_end).
    """
    byte_start = len(text[:char_start].encode("utf-8"))
    byte_end = byte_start + len(text[char_start:char_end].encode("utf-8"))
    return byte_start, byte_end


class SlidingWindowChunker:
    """Sliding-window text chunker with character offset and byte offset preservation."""

    def __init__(
        self,
        window_size: int = 500,
        overlap_size: int = 100,
        chunk_size: int | None = None,
    ) -> None:
        actual_window = chunk_size if chunk_size is not None else window_size
        if actual_window <= 0:
            raise ValueError(f"window_size must be positive, got {actual_window}")
        if overlap_size < 0 or overlap_size >= actual_window:
            raise ValueError(
                f"overlap_size must be non-negative and strictly less than window_size ({actual_window}), "
                f"got {overlap_size}"
            )
        self.window_size = actual_window
        self.overlap_size = overlap_size

    def chunk_text(
        self,
        text: str,
        document_id: str,
        document_title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[SourceChunk]:
        """Convenience method accepting document_title."""
        meta = dict(metadata) if metadata else {}
        if document_title:
            meta["document_title"] = document_title
        return self.chunk(text=text, document_id=document_id, metadata=meta)

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[SourceChunk]:
        """Segment text into overlapping sliding-window chunks."""
        if not text or not text.strip():
            raise ValueError("text cannot be empty or only whitespace")
        if not document_id or not document_id.strip():
            raise ValueError("document_id cannot be empty or only whitespace")

        step = self.window_size - self.overlap_size
        n = len(text)
        start = 0
        chunks: list[SourceChunk] = []

        while start < n:
            end = min(start + self.window_size, n)
            chunk_text = text[start:end]
            byte_start, byte_end = get_byte_offsets(text, start, end)

            chunk = SourceChunk.create(
                document_id=document_id,
                text=chunk_text,
                char_start=start,
                char_end=end,
                byte_start=byte_start,
                byte_end=byte_end,
                metadata=dict(metadata) if metadata else {},
            )

            # Invariant assertion
            if text[chunk.char_start : chunk.char_end] != chunk.text:
                raise RuntimeError(
                    f"Boundary integrity violation: chunk text mismatch at [{chunk.char_start}:{chunk.char_end}]"
                )

            chunks.append(chunk)

            if end >= n:
                break
            start += step

        return chunks


class SemanticChunker:
    """Semantic chunker respecting natural paragraph and sentence boundaries."""

    def __init__(self, max_chunk_size: int = 1000, min_chunk_size: int = 50) -> None:
        if min_chunk_size <= 0:
            raise ValueError(f"min_chunk_size must be positive, got {min_chunk_size}")
        if max_chunk_size < min_chunk_size:
            raise ValueError(
                f"max_chunk_size ({max_chunk_size}) must be greater than or equal to min_chunk_size ({min_chunk_size})"
            )
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    def _split_into_units(self, text: str) -> list[tuple[int, int]]:
        """Split text into atomic spans preserving character offsets."""
        # Find candidate split boundaries: paragraphs first, then sentences
        # Matches of paragraph splits or sentence endings followed by whitespace
        delimiters = re.compile(r"(\n\n+|(?<=[.!?])\s+)")
        spans: list[tuple[int, int]] = []
        last_idx = 0

        for match in delimiters.finditer(text):
            m_start, m_end = match.span()
            if m_start > last_idx:
                spans.append((last_idx, m_start))
            last_idx = m_end

        if last_idx < len(text):
            spans.append((last_idx, len(text)))

        # Subdivide any span exceeding max_chunk_size
        refined_spans: list[tuple[int, int]] = []
        for s_start, s_end in spans:
            if s_end - s_start <= self.max_chunk_size:
                refined_spans.append((s_start, s_end))
            else:
                # Subdivide by sentence boundaries if not already done, or by words
                subtext = text[s_start:s_end]
                sub_delimiters = re.compile(r"(\s+)")
                sub_last = 0
                for sub_m in sub_delimiters.finditer(subtext):
                    sm_start, sm_end = sub_m.span()
                    if sm_start > sub_last:
                        cand_end = s_start + sm_start
                        cand_start = s_start + sub_last
                        if cand_end - cand_start > self.max_chunk_size:
                            # Hard split if single word is too large
                            cur = cand_start
                            while cur < cand_end:
                                h_end = min(cur + self.max_chunk_size, cand_end)
                                refined_spans.append((cur, h_end))
                                cur = h_end
                        else:
                            refined_spans.append((cand_start, cand_end))
                    sub_last = sm_end
                if sub_last < len(subtext):
                    cand_start = s_start + sub_last
                    cand_end = s_end
                    if cand_end - cand_start > self.max_chunk_size:
                        cur = cand_start
                        while cur < cand_end:
                            h_end = min(cur + self.max_chunk_size, cand_end)
                            refined_spans.append((cur, h_end))
                            cur = h_end
                    else:
                        refined_spans.append((cand_start, cand_end))

        return [s for s in refined_spans if s[1] > s[0]]

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[SourceChunk]:
        """Segment text into semantic units respecting natural boundaries."""
        if not text or not text.strip():
            raise ValueError("text cannot be empty or only whitespace")
        if not document_id or not document_id.strip():
            raise ValueError("document_id cannot be empty or only whitespace")

        raw_units = self._split_into_units(text)
        if not raw_units:
            # Entire text is a single span
            start = 0
            end = len(text)
            byte_start, byte_end = get_byte_offsets(text, start, end)
            chunk = SourceChunk.create(
                document_id=document_id,
                text=text[start:end],
                char_start=start,
                char_end=end,
                byte_start=byte_start,
                byte_end=byte_end,
                metadata=dict(metadata) if metadata else {},
            )
            return [chunk]

        # Merge adjacent units up to max_chunk_size
        merged_spans: list[tuple[int, int]] = []
        cur_start, cur_end = raw_units[0]

        for next_start, next_end in raw_units[1:]:
            # Check if merging current span with next unit fits within max_chunk_size
            candidate_length = next_end - cur_start
            if candidate_length <= self.max_chunk_size:
                cur_end = next_end
            else:
                merged_spans.append((cur_start, cur_end))
                cur_start, cur_end = next_start, next_end

        merged_spans.append((cur_start, cur_end))

        # Check if the last merged span is smaller than min_chunk_size and can merge backward
        if len(merged_spans) > 1:
            last_start, last_end = merged_spans[-1]
            if (last_end - last_start) < self.min_chunk_size:
                prev_start, _ = merged_spans[-2]
                if (last_end - prev_start) <= self.max_chunk_size:
                    merged_spans[-2] = (prev_start, last_end)
                    merged_spans.pop()

        chunks: list[SourceChunk] = []
        for c_start, c_end in merged_spans:
            chunk_text = text[c_start:c_end]
            byte_start, byte_end = get_byte_offsets(text, c_start, c_end)

            chunk = SourceChunk.create(
                document_id=document_id,
                text=chunk_text,
                char_start=c_start,
                char_end=c_end,
                byte_start=byte_start,
                byte_end=byte_end,
                metadata=dict(metadata) if metadata else {},
            )

            # Invariant assertion
            if text[chunk.char_start : chunk.char_end] != chunk.text:
                raise RuntimeError(
                    f"Boundary integrity violation: chunk text mismatch at [{chunk.char_start}:{chunk.char_end}]"
                )

            chunks.append(chunk)

        return chunks


SourceChunker = SlidingWindowChunker

__all__ = [
    "SemanticChunker",
    "SlidingWindowChunker",
    "SourceChunker",
    "compute_chunk_id",
    "get_byte_offsets",
]
