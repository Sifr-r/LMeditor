"""Canonical inline-citation grammar, as the Kotlin tier implements it.

The drafter emits inline citations as ``[#chunk_id]`` (see
``drafting.modules.SectionDrafter`` and the DSPy prompt in
``drafting/modules.py``). Every consumer that needs to read a citation marker out
of manuscript text must use this module so that the drafting side and the
verification side can never drift apart again.

**The grammar and the parsing rules live in the Kotlin tier**, in
``lmeditor.core.Citations``. This module is the door to them: each function
below calls across the NDJSON seam and returns the answer. There is one
implementation of the rule, and it is not this one.

Accepted marker forms (all parse to the bare chunk id, without the marker
prefix):

* ``[#chunk_id]`` -- the primary form emitted by the drafter
* ``[chunk_id]``
* ``[cite: chunk_id]``
* comma-separated lists of any of the above, e.g. ``[#a, #b]``

Markdown links ``[text](url)`` and images ``![alt](url)`` are never citations.

What stays in Python is the compiled pattern. The drafter needs a regular
expression it can apply to the language model's output without leaving the
process, and a compiled pattern is a Python object rather than data. The
pattern and the Kotlin tier's are the same grammar -- ``backend/scripts/
verify_kotlin_parity.py`` asks both tiers to parse the same corpus and
compares their answers, so a divergence between them fails the build.
"""

from __future__ import annotations

import re

from lmeditor.core.exceptions import ValidationError

# The id alphabet actually produced by ``provenance.chunker`` (hex digests) plus
# the non-hex ids used by tests and by ``HarnessState`` fixtures.
CHUNK_ID_PATTERN: str = r"[A-Za-z0-9_.\-]+"

#: ``#`` and ``cite:`` are marker prefixes and are never part of the chunk id.
_ITEM_PATTERN: str = rf"(?:cite:\s*)?#?{CHUNK_ID_PATTERN}"

#: The model-facing form of the canonical grammar.
#:
#: The Kotlin tier owns the grammar itself; this pattern is the same one, kept
#: here because the drafter needs to apply it to model output in-process. The
#: parity harness keeps the two from drifting apart.
CITATION_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!!)\[(?P<cite_body>"
    rf"{_ITEM_PATTERN}"
    rf"(?:\s*,\s*{_ITEM_PATTERN})*"
    r")\](?!\()",
    re.UNICODE,
)


def _validate_text(text: str) -> None:
    """Reject non-string input at the module boundary."""
    if not isinstance(text, str):
        raise ValidationError(
            f"Expected str input for citation parsing, got {type(text).__name__}",
            field="text",
            value=text,
        )


def iter_citation_spans(text: str) -> list[tuple[str, int, int]]:
    """Return ``(chunk_id, start, end)`` for every citation marker in ``text``.

    ``start``/``end`` are the offsets of the whole bracketed marker, so a
    comma-separated group such as ``[#a, #b]`` reports the same span for both
    chunk ids. Order follows the order of appearance in ``text``.

    The offsets are **code points**, which is what Python's string indices
    count and what the Kotlin tier reports.

    Args:
        text: Manuscript or section text containing bracketed citations.

    Returns:
        Ordered list of ``(chunk_id, start, end)`` triples.

    Raises:
        ValidationError: If ``text`` is not a string.
    """
    _validate_text(text)

    from lmeditor.tier import core as tier_core

    return tier_core.iter_citation_spans(text)


def extract_chunk_ids(text: str) -> list[str]:
    """Return the ordered, de-duplicated chunk ids cited in ``text``.

    Args:
        text: Manuscript or section text containing bracketed citations.

    Returns:
        Ordered list of unique bare chunk ids.

    Raises:
        ValidationError: If ``text`` is not a string.
    """
    _validate_text(text)

    from lmeditor.tier import core as tier_core

    return tier_core.extract_chunk_ids(text)


def strip_citations(text: str) -> str:
    """Return ``text`` with every citation marker removed.

    Args:
        text: Manuscript or section text containing bracketed citations.

    Returns:
        The same text without citation markers.

    Raises:
        ValidationError: If ``text`` is not a string.
    """
    _validate_text(text)

    from lmeditor.tier import core as tier_core

    return tier_core.strip_citations(text)


__all__ = [
    "CHUNK_ID_PATTERN",
    "CITATION_PATTERN",
    "extract_chunk_ids",
    "iter_citation_spans",
    "strip_citations",
]
