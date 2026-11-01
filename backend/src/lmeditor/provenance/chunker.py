"""Text chunking, as the Kotlin tier implements it.

Chunking is a rule -- where a source is cut into the passages a citation
points at -- and the rule is Kotlin's: it is implemented once, in
``lmeditor.text``, and the tier serves it across the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.chunker``, frozen, so the parity
harness can still ask both sides the same question.

What is still in Python, and why:

* :class:`SlidingWindowChunker` and :class:`SemanticChunker` are two other
  chunking *algorithms*. The seam serves one chunking method, and it serves
  the sentence-aware one; answering a sliding-window request with
  sentence-aware chunks would be a different rule, not a faster one, so these
  two keep their implementation until the seam serves them too.
* :func:`get_byte_offsets` and :func:`estimate_token_count` are two
  arithmetic helpers the Kotlin tier also implements (``getByteOffsets``,
  ``estimateTokenCount``) but that the seam does not expose as methods of
  their own. They stay here until it does.

The window of :class:`SentenceAwareChunker` is a parameter of the chunking
rule, so it travels across the seam: the chunker sends the window and the
overlap it was built with, and a chunker configured with another window
answers with that window's chunks.
"""

from __future__ import annotations

import math
import re
from typing import Any, Final

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, ValidationError
from lmeditor.core.schemas import SourceChunk
from lmeditor.tier import core as tier_core
from lmeditor.tier import text as tier_text

#: Characters per token used by :func:`estimate_token_count`. Four is the
#: commonly cited average for English prose.
CHARS_PER_TOKEN: Final[int] = 4


def _require_text(text: str, field: str = "text") -> None:
    """Refuse anything that is not text."""
    if not isinstance(text, str):
        raise ValidationError(
            f"Expected str input for {field}, got {type(text).__name__}",
            field=field,
            value=text,
        )


def _require_document_id(document_id: str) -> None:
    """Refuse a document id that is not a non-empty string."""
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValidationError(
            f"Expected a non-empty str input for document_id, got {document_id!r}",
            field="document_id",
            value=document_id,
        )


def _require_chunkable(text: str, document_id: str) -> None:
    """Refuse the input a chunker cannot work with."""
    _require_text(text)
    _require_document_id(document_id)
    if not text.strip():
        raise ValidationError("text cannot be empty or only whitespace", field="text")
    if not document_id.strip():
        raise ValidationError(
            "document_id cannot be empty or only whitespace", field="document_id"
        )


def compute_chunk_id(
    document_id: str,
    char_start: int,
    char_end: int,
    text: str,
) -> str:
    """Compute deterministic SHA-256 hash formatted as lowercase hex string.

    Payload format: "{document_id}:{char_start}:{char_end}:{text}"

    The digest is a rule, and the rule is the tier's: the Kotlin side mints
    the id, so that a chunk id minted on one side of the seam resolves on the
    other. This call names the payload and reads the id back.

    Raises:
        ValidationError: The payload does not describe a chunk that could
            exist -- an empty text, or a span that ends where it starts.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    _require_document_id(document_id)
    _require_text(text)
    try:
        chunk = tier_core.create_source_chunk(
            document_id=document_id,
            text=text,
            char_start=char_start,
            char_end=char_end,
            byte_start=0,
            byte_end=0,
        )
    except BridgeUnavailableError:
        # The tier is not there. That is an infrastructure failure, not a
        # bad input, and it must not be dressed up as one: the caller needs
        # to know that the rule could not be answered at all.
        raise
    except LMeditorError:
        raise
    return chunk.chunk_id


def get_byte_offsets(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    """Compute UTF-8 byte offsets for a character span in a string.

    Returns (byte_start, byte_end).

    The span is given in code points, as Python string indices count them;
    the byte offsets are the UTF-8 lengths of the two halves of the span.
    They are *not* the character offsets: one character can be several bytes.
    """
    _require_text(text)
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
    _require_text(text)
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
    """Sliding-window text chunker with character offset and byte offset preservation.

    The sliding window cuts wherever the character count says so, which lands
    in the middle of a sentence more often than not. Use
    :class:`SentenceAwareChunker` when the chunks are meant to be cited.

    The seam does not serve this algorithm yet, so this chunker still carries
    its own: see the module docstring.
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
    """Semantic chunker respecting natural paragraph and sentence boundaries.

    The seam does not serve this algorithm yet, so this chunker still carries
    its own: see the module docstring.
    """

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

    The packing is a rule, and the rule is the tier's: this class is the
    door to ``lmeditor.text.SentenceAwareChunker``. The window and the
    overlap travel across the seam with the request, because they are
    parameters of the rule and not a setting of the caller.
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

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[SourceChunk]:
        """Segment text into chunks that respect sentence boundaries.

        Raises:
            ValidationError: The text or the document id is not something a
                chunker can work with.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is
                never swallowed.
        """
        _require_chunkable(text, document_id)
        try:
            return tier_text.chunk(
                text,
                document_id,
                dict(metadata) if metadata else None,
                window_size=self.window_size,
                overlap_size=self.overlap_size,
            )
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except LMeditorError:
            raise


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
