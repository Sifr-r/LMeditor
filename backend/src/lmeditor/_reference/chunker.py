"""The frozen text chunking implementations.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Final

from lmeditor.core.schemas import SourceChunk

#: Characters per token used by :func:`estimate_token_count`. Four is the
#: commonly cited average for English prose.
CHARS_PER_TOKEN: Final[int] = 4

#: A paragraph break: a blank line, optionally with spaces between the two
#: newlines.
_PARAGRAPH_BREAK: Final[re.Pattern[str]] = re.compile(r"\n[ \t]*\n")

#: A sentence end: a terminator, any closing quotes or brackets, and the
#: whitespace that follows it. The whitespace is part of the unit so that the
#: units -- and therefore the chunks -- stay contiguous substrings of the
#: source text.
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"[.!?][\"'\)\]]*[ \t\r\n]+")


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


def estimate_token_count(text: str) -> int:
    """Return an *estimate* of the number of tokens in ``text``.

    This is **not** a tokenizer count and must not be used as one: no
    tokenizer is loaded, and the value is derived from the character length
    alone, assuming the commonly cited average of roughly four characters per
    token in English prose. It is accurate to within a factor of about two
    for prose and useless for code or tables.

    It exists so a chunk can report a size without dragging a tokenizer into
    the ingestion path, where the cost would be paid for every chunk of every
    document. Treat the number as an order of magnitude.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def _build_chunk(
    text: str,
    document_id: str,
    start: int,
    end: int,
    metadata: dict[str, Any],
) -> SourceChunk:
    """Build a chunk for ``text[start:end]`` with its token estimate filled in."""
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
    # ``model_copy`` rather than a second constructor call: the chunk id is
    # derived from the text and the offsets, so it must not be recomputed.
    return chunk.model_copy(
        update={"token_count": estimate_token_count(chunk.text)}
    )


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
            ).model_copy(
                update={"token_count": estimate_token_count(chunk_text)}
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
            return [
                _build_chunk(
                    text, document_id, 0, len(text), dict(metadata) if metadata else {}
                )
            ]

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
            chunk = _build_chunk(
                text, document_id, c_start, c_end, dict(metadata) if metadata else {}
            )

            # Invariant assertion
            if text[chunk.char_start : chunk.char_end] != chunk.text:
                raise RuntimeError(
                    f"Boundary integrity violation: chunk text mismatch at [{chunk.char_start}:{chunk.char_end}]"
                )

            chunks.append(chunk)

        return chunks


class SentenceAwareChunker:
    """Chunks text on sentence and paragraph boundaries.

    The sliding window cuts wherever the character count says so, which lands
    in the middle of a sentence more often than not. That matters here more
    than it would in a summariser: a chunk is the unit a citation points at,
    so a chunk that starts "…because the model hallucinates" and ends "…the
    model hallucinates less often" makes the citation span unreconstructable
    and the quoted passage unreadable.

    This chunker therefore packs whole *units* -- sentences, and paragraphs
    where a paragraph break falls -- into each chunk, and never splits one.
    The only exception is a single unit longer than the window itself (a
    sentence of four thousand characters); that one is cut, because a window
    is a hard limit on a chunk's size.

    The overlap between two consecutive chunks is also made of whole units:
    it is the shortest tail of the previous chunk that is at least
    ``overlap_size`` characters long, snapped outwards to a unit boundary so
    that the repeated text is never a fragment of a sentence.

    The units are *contiguous*: every character of the source belongs to
    exactly one unit, and the chunks are contiguous substrings of the source
    text, so ``text[chunk.char_start:chunk.char_end] == chunk.text`` holds for
    every chunk.
    """

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

    def _split_into_units(self, text: str) -> list[tuple[int, int]]:
        """Split ``text`` into contiguous sentence / paragraph units."""
        boundaries: set[int] = {0}
        for pattern in (_PARAGRAPH_BREAK, _SENTENCE_END):
            for match in pattern.finditer(text):
                boundaries.add(match.end())
        ordered = sorted(boundaries)
        units: list[tuple[int, int]] = []
        previous = 0
        for boundary in ordered:
            if boundary > previous:
                units.append((previous, boundary))
                previous = boundary
        if previous < len(text):
            units.append((previous, len(text)))
        return units

    def _split_long_units(
        self, units: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        """Cut units that are longer than the window into window-sized pieces."""
        refined: list[tuple[int, int]] = []
        for start, end in units:
            cursor = start
            while end - cursor > self.window_size:
                refined.append((cursor, cursor + self.window_size))
                cursor += self.window_size
            if end > cursor:
                refined.append((cursor, end))
        return refined

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[SourceChunk]:
        """Segment text into chunks that respect sentence boundaries."""
        if not text or not text.strip():
            raise ValueError("text cannot be empty or only whitespace")
        if not document_id or not document_id.strip():
            raise ValueError("document_id cannot be empty or only whitespace")

        units = self._split_long_units(self._split_into_units(text))
        if not units:
            return [
                _build_chunk(
                    text, document_id, 0, len(text), dict(metadata) if metadata else {}
                )
            ]

        chunks: list[SourceChunk] = []
        index = 0
        total = len(units)
        while index < total:
            start = units[index][0]
            end = units[index][1]
            cursor = index + 1
            while cursor < total and units[cursor][1] - start <= self.window_size:
                end = units[cursor][1]
                cursor += 1

            chunk = _build_chunk(
                text, document_id, start, end, dict(metadata) if metadata else {}
            )

            # Invariant assertion
            if text[chunk.char_start : chunk.char_end] != chunk.text:
                raise RuntimeError(
                    f"Boundary integrity violation: chunk text mismatch at [{chunk.char_start}:{chunk.char_end}]"
                )

            chunks.append(chunk)

            if cursor >= total:
                break

            if cursor - 1 <= index:
                # A single unit filled the chunk: continue after it, with no
                # overlap, because repeating the unit would not advance.
                index = cursor
                continue

            # The next chunk starts at the earliest unit whose tail is at
            # least ``overlap_size`` characters long.
            next_index = cursor - 1
            while (
                next_index > index + 1
                and units[cursor - 1][1] - units[next_index - 1][0] >= self.overlap_size
            ):
                next_index -= 1
            index = next_index

        return chunks


#: Compatibility alias. The name used to point at the sliding window, which
#: is not source-aware at all; it now points at the chunker that is, so every
#: caller of ``SourceChunker`` -- including the ones outside this package --
#: gets sentence-aligned chunks.
SourceChunker = SentenceAwareChunker

__all__ = [
    "CHARS_PER_TOKEN",
    "SentenceAwareChunker",
    "SemanticChunker",
    "SlidingWindowChunker",
    "SourceChunker",
    "compute_chunk_id",
    "estimate_token_count",
    "get_byte_offsets",
]
