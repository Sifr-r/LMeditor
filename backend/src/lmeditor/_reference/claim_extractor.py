"""The frozen proposition-level claim extractor and sentence segmentation.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.
"""

from __future__ import annotations

import re
from typing import Sequence
from lmeditor._reference.citations import CITATION_PATTERN, iter_citation_spans, strip_citations
from lmeditor.core.exceptions import ClaimExtractionError
from lmeditor.core.schemas import CitationLineage, Claim, InlineCitation
from lmeditor.core.types import EntailmentStatus

# Common abbreviations that should not trigger sentence boundaries
_ABBREVIATIONS: set[str] = {
    "e.g",
    "i.e",
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "vs",
    "al",
    "etc",
    "inc",
    "ltd",
    "u.s",
    "u.k",
    "fig",
    "tab",
    "eq",
    "no",
    "approx",
    "dept",
    "est",
    "vol",
}


class ClaimExtractor:
    """Sentence segmentation and atomic proposition extraction engine."""

    def __init__(self) -> None:
        self._abbrevs: set[str] = _ABBREVIATIONS

    def segment_sentences(self, text: str) -> list[str]:
        """Split text into sentences while respecting markdown, abbreviations, and citations.

        Args:
            text: Raw manuscript section markdown or plain text.

        Returns:
            List of non-empty sentence strings.

        Raises:
            ClaimExtractionError: If input is invalid.
        """
        if not isinstance(text, str):
            raise ClaimExtractionError(f"Expected str input for text, got {type(text).__name__}")

        cleaned = text.strip()
        if not cleaned:
            return []

        try:
            # Split text by lines first to cleanly isolate headers, list items, and blockquotes
            raw_lines = cleaned.splitlines()
            processed_blocks: list[str] = []
            curr_block: list[str] = []

            for raw_line in raw_lines:
                line = raw_line.strip()
                if not line:
                    if curr_block:
                        processed_blocks.append(" ".join(curr_block))
                        curr_block = []
                    continue

                # Markdown heading or bullet point forms its own block
                if re.match(r"^#{1,6}\s+", line) or re.match(r"^[-*+]\s+", line) or re.match(r"^\d+\.\s+", line):
                    if curr_block:
                        processed_blocks.append(" ".join(curr_block))
                        curr_block = []
                    # Strip markdown marker for cleaner sentence processing
                    clean_marker_line = re.sub(r"^(#{1,6}|[-*+]|\d+\.)\s+", "", line).strip()
                    if clean_marker_line:
                        processed_blocks.append(clean_marker_line)
                else:
                    curr_block.append(line)

            if curr_block:
                processed_blocks.append(" ".join(curr_block))

            sentences: list[str] = []
            for block in processed_blocks:
                sentences.extend(self._split_into_sentences(block))

            return [s for s in sentences if s.strip()]

        except Exception as exc:
            if isinstance(exc, ClaimExtractionError):
                raise
            raise ClaimExtractionError(f"Error during sentence segmentation: {exc}") from exc

    def _split_into_sentences(self, text: str) -> list[str]:
        """Heuristic sentence tokenizer handling periods, abbreviations, and floats."""
        norm = re.sub(r"\s+", " ", text).strip()
        if not norm:
            return []

        # Lookahead pattern splits after . ! ? followed by space and capital letter or quote
        potential_splits = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'\[])", norm)
        result: list[str] = []
        carry: str = ""

        for piece in potential_splits:
            candidate = f"{carry} {piece}".strip() if carry else piece
            tokens = candidate.split()
            last_word = tokens[-1] if tokens else ""
            # Check if last word is an abbreviation
            clean_word = last_word.rstrip(".!?,;:\"'").lower()
            if clean_word in self._abbrevs:
                carry = candidate
                continue

            result.append(candidate)
            carry = ""

        if carry:
            result.append(carry)

        return self._reattach_trailing_markers(result)

    @staticmethod
    def _reattach_trailing_markers(sentences: list[str]) -> list[str]:
        """Move a citation marker back onto the sentence it follows.

        A citation marks the claim it follows: "rates fell by 30 percent
        [#chunk]." The sentence splitter, however, breaks after the full stop
        and before the marker, because the marker starts with ``[``. The
        marker therefore landed at the *start* of the next sentence, and the
        sentence it actually supports was left with no citation at all -- so a
        perfectly grounded claim was recorded as having no premise.

        Moving a leading marker back to the end of the preceding sentence
        restores the attribution. A marker that already has a sentence of its
        own to sit in is left alone.
        """
        result: list[str] = []
        for sentence in sentences:
            leading = re.match(r"^\s*(\[[^\]]*\])\s*", sentence)
            if leading is not None and result:
                marker = leading.group(1)
                previous = result[-1]
                if not CITATION_PATTERN.search(previous):
                    result[-1] = f"{previous.rstrip()} {marker}"
                    sentence = sentence[leading.end() :]
            result.append(sentence)
        return [s for s in result if s.strip()]

    def extract_citations(self, text: str) -> list[InlineCitation]:
        """Extract all inline citations ([#chunk_id], [chunk_id], [cite: chunk_id])
        with exact span offsets.

        The marker grammar is owned by :mod:`lmeditor._reference.citations` so
        that the drafter and the verifier can never disagree on what a citation
        is.

        Args:
            text: Manuscript text containing bracketed citations.

        Returns:
            List of InlineCitation models in order of appearance.
        """
        if not isinstance(text, str):
            raise ClaimExtractionError(f"Expected str input for text, got {type(text).__name__}")

        citations: list[InlineCitation] = []
        cit_counter = 0

        for chunk_id, span_start, span_end in iter_citation_spans(text):
            cit_counter += 1
            citations.append(
                InlineCitation(
                    citation_id=f"cit_{cit_counter:04d}",
                    chunk_id=chunk_id,
                    span_start=span_start,
                    span_end=span_end,
                )
            )

        return citations

    def extract_claims(self, text: str, section_id: str = "") -> list[Claim]:
        """Deconstruct manuscript text into atomic, verifiable propositional claims.

        Args:
            text: Section draft text.
            section_id: Identifier for parent section.

        Returns:
            List of Claim models with assigned sentence indexes and cited chunks.
        """
        if not isinstance(text, str):
            raise ClaimExtractionError(f"Expected str input for text, got {type(text).__name__}")

        if not text.strip():
            return []

        sentences = self.segment_sentences(text)
        claims: list[Claim] = []
        sec_id = section_id or "sec_default"
        # Search cursor: the offset in ``text`` from which the next sentence is
        # looked up. Advancing it across the document is what makes a repeated
        # sentence map to its own occurrence instead of always to the first one.
        cursor = 0

        for sent_idx, sentence in enumerate(sentences):
            # 1. Extract cited chunks referenced in this sentence
            citations = self.extract_citations(sentence)
            cited_chunk_ids = list(dict.fromkeys(c.chunk_id for c in citations))

            # 2. Strip citation markers from the sentence to get clean text
            clean_sentence = strip_citations(sentence)
            clean_sentence = re.sub(r"\s+", " ", clean_sentence).strip()
            clean_sentence = clean_sentence.rstrip(".")

            if not clean_sentence:
                continue

            # 3. Deconstruct compound clauses into atomic propositions
            propositions = self._deconstruct_propositions(clean_sentence)

            for prop_idx, prop in enumerate(propositions):
                prop_text = prop.strip()
                if not prop_text:
                    continue

                claim_id = f"claim_{sec_id}_{sent_idx:03d}_{prop_idx:02d}"

                # Calculate relative character spans if found in original sentence.
                # The lookup resumes where the previous sentence ended, so a
                # sentence that occurs more than once is attributed to the
                # occurrence that follows its predecessor.
                char_start = text.find(sentence, cursor)
                if char_start < 0:
                    # The sentence was normalised during segmentation (joined
                    # lines, collapsed whitespace); fall back to a global lookup.
                    char_start = text.find(sentence)
                if char_start < 0:
                    char_start = 0
                    char_end = 0
                else:
                    char_end = char_start + len(sentence)
                    cursor = char_end

                claims.append(
                    Claim(
                        claim_id=claim_id,
                        section_id=sec_id,
                        sentence_idx=sent_idx,
                        text=prop_text,
                        char_start=char_start,
                        char_end=char_end,
                        cited_chunk_ids=cited_chunk_ids,
                        entailment_status=EntailmentStatus.UNGROUNDED,
                        entailment_score=0.0,
                    )
                )

        return claims

    def _deconstruct_propositions(self, sentence: str) -> list[str]:
        """Break down a compound sentence into distinct verifiable propositions."""
        # If sentence contains semicolons, split
        if ";" in sentence:
            parts = [p.strip() for p in sentence.split(";") if p.strip()]
            return parts

        # Split on coordinating conjunctions with commas: ", and ", ", but ", ", however "
        conjunction_split = re.split(r",\s+(?:and|but|while|whereas|however)\s+", sentence, flags=re.IGNORECASE)
        if len(conjunction_split) > 1:
            propositions: list[str] = []
            for part in conjunction_split:
                part = part.strip()
                if len(part.split()) >= 3:  # Must have minimal clause length
                    propositions.append(part)
                elif propositions:
                    # Append short fragment back to previous clause
                    propositions[-1] = f"{propositions[-1]}, {part}"
                else:
                    propositions.append(part)
            if propositions:
                return propositions

        return [sentence]


# Module top-level convenience functions
_DEFAULT_EXTRACTOR = ClaimExtractor()


def extract_claims(text: str, section_id: str = "") -> list[Claim]:
    """Top-level convenience function for claim extraction."""
    return _DEFAULT_EXTRACTOR.extract_claims(text, section_id=section_id)


def extract_citations(text: str) -> list[InlineCitation]:
    """Top-level convenience function for inline citation extraction."""
    return _DEFAULT_EXTRACTOR.extract_citations(text)


def segment_sentences(text: str) -> list[str]:
    """Top-level convenience function for sentence segmentation."""
    return _DEFAULT_EXTRACTOR.segment_sentences(text)


PropositionClaimExtractor = ClaimExtractor

__all__ = [
    "ClaimExtractor",
    "PropositionClaimExtractor",
    "extract_citations",
    "extract_claims",
    "segment_sentences",
]
