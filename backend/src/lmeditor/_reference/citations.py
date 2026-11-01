"""The frozen inline-citation grammar.

This is the original Python implementation of the canonical marker grammar,
kept here so that the parity harness has a witness to compare the Kotlin tier
against. It is plain Python on purpose: see ``lmeditor._reference``.

Accepted marker forms (all parse to the bare chunk id, without the marker
prefix):

* ``[#chunk_id]`` -- the primary form emitted by the drafter
* ``[chunk_id]``
* ``[cite: chunk_id]``
* comma-separated lists of any of the above, e.g. ``[#a, #b]``

Markdown links ``[text](url)`` and images ``![alt](url)`` are never citations.

The offsets reported by [iter_citation_spans] are **code points**, because
Python string indices count code points.
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
CITATION_PATTERN: re.Pattern[str] = re.compile(
    r"(?<!!)\[(?P<cite_body>"
    rf"{_ITEM_PATTERN}"
    rf"(?:\s*,\s*{_ITEM_PATTERN})*"
    r")\](?!\()",
    re.UNICODE,
)

#: Splits one bracketed body into its comma-separated items.
_ITEM_SEPARATOR_PATTERN: re.Pattern[str] = re.compile(r"\s*,\s*")

#: Strips the ``cite:`` and ``#`` marker prefixes from a single item.
_MARKER_PREFIX_PATTERN: re.Pattern[str] = re.compile(r"^(?:cite:\s*)?#?")


def _validate_text(text: str) -> None:
    """Reject non-string input at the module boundary."""
    if not isinstance(text, str):
        raise ValidationError(
            f"Expected str input for citation parsing, got {type(text).__name__}",
            field="text",
            value=text,
        )


def _body_to_chunk_ids(body: str) -> list[str]:
    """Parse one bracketed body into its bare chunk ids."""
    chunk_ids: list[str] = []
    for raw_item in _ITEM_SEPARATOR_PATTERN.split(body):
        chunk_id = _MARKER_PREFIX_PATTERN.sub("", raw_item.strip(), count=1).strip()
        if chunk_id:
            chunk_ids.append(chunk_id)
    return chunk_ids


def iter_citation_spans(text: str) -> list[tuple[str, int, int]]:
    """Return ``(chunk_id, start, end)`` for every citation marker in ``text``.

    ``start``/``end`` are the offsets of the whole bracketed marker, so a
    comma-separated group such as ``[#a, #b]`` reports the same span for both
    chunk ids. Order follows the order of appearance in ``text``.
    """
    _validate_text(text)
    spans: list[tuple[str, int, int]] = []
    for match in CITATION_PATTERN.finditer(text):
        start = match.start()
        end = match.end()
        for chunk_id in _body_to_chunk_ids(match.group("cite_body")):
            spans.append((chunk_id, start, end))
    return spans


def extract_chunk_ids(text: str) -> list[str]:
    """Return the ordered, de-duplicated chunk ids cited in ``text``."""
    _validate_text(text)
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk_id, _start, _end in iter_citation_spans(text):
        if chunk_id not in seen:
            seen.add(chunk_id)
            ordered.append(chunk_id)
    return ordered


def strip_citations(text: str) -> str:
    """Return ``text`` with every citation marker removed."""
    _validate_text(text)
    return CITATION_PATTERN.sub("", text)


__all__ = [
    "CHUNK_ID_PATTERN",
    "CITATION_PATTERN",
    "extract_chunk_ids",
    "iter_citation_spans",
    "strip_citations",
]
