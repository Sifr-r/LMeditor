"""Whitespace and text normalisation rules shared by the Word reader and writer.

The rules implemented here are the contract's §4 (T1-T9). They exist as a single
module because both sides of the round trip must agree on them: a reader that
normalises differently from the writer's expectation produces documents that
change on every round trip, which makes the format untestable.

This module is **not** a door. The Kotlin tier carries the same rules in
``lmeditor.word.WordText``, and the two are held together by a shared corpus:
one file, read by both sides, whose expected values are Python's answers.

What stays here, and why:

* **The rule, in Python.** ``normalize_whitespace`` is called inside a
  Pydantic validator on *every* ``WordBlock`` the backend builds. A door
  would turn each of those calls into a round trip across the NDJSON seam,
  and a round trip per block is a regression, not an implementation. The
  corpus is what keeps the two copies from drifting apart, and it costs one
  file read.
* **Python's answers, as the yardstick.** The corpus holds the answers this
  module gives, and the Kotlin test asserts that the tier gives the same
  ones. The corpus is generated from this module, so a rule that changes
  here moves the yardstick deliberately -- and the Kotlin test fails until
  the tier follows.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Characters Word uses for non-breaking or invisible spacing, mapped to a plain
#: space. A no-break space inside a citation marker would otherwise split the
#: marker across two words and defeat the citation grammar.
REPLACED_SPACES: dict[int, str] = {
    0x00A0: " ",  # no-break space
    0x2007: " ",  # figure space
    0x202F: " ",  # narrow no-break space
    0x2009: " ",  # thin space
    0x200B: " ",  # zero-width space
    0x00AD: " ",  # soft hyphen
    0x3000: " ",  # ideographic space
}

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Collapse a text fragment to single-spaced, end-stripped text.

    Applies the contract's T3 and T4: exotic Word spaces become plain spaces and
    every whitespace run collapses to a single space. The function is idempotent,
    so applying it to already-normalised text changes nothing.
    """
    if not text:
        return ""
    translated = text.translate(REPLACED_SPACES)
    return _WHITESPACE_RUN.sub(" ", translated).strip()


def join_texts(parts: Iterable[str], *, separator: str = " ") -> str:
    """Join text fragments with ``separator`` and normalise the result.

    Empty fragments are dropped so that a paragraph with an empty first run does
    not begin with a stray separator.
    """
    kept = [part for part in parts if part]
    if not kept:
        return ""
    return normalize_whitespace(separator.join(kept))


def strip_control_characters(text: str) -> str:
    """Remove XML-illegal control characters that Word sometimes leaves behind.

    Characters below ``U+0020`` other than tab, newline and carriage return are
    not permitted in XML 1.0 content and cannot be written back out, so they are
    removed rather than carried into a document model that could never be saved.
    """
    return "".join(
        char
        for char in text
        if char in "\t\n\r" or ord(char) >= 0x20
    )


__all__ = [
    "REPLACED_SPACES",
    "join_texts",
    "normalize_whitespace",
    "strip_control_characters",
]
