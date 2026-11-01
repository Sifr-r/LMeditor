"""Whitebox tests for the shared Word text-normalisation rules.

The rules implemented in :mod:`lmeditor.word.text` are the contract's §4
(T3, T4 and the helpers). They must be idempotent: a reader normalises, the
writer's expectations assume normalised text, and a round trip must not drift.
"""

from __future__ import annotations

import pytest

from lmeditor.word.text import (
    join_texts,
    normalize_whitespace,
    strip_control_characters,
)


# ============================================================================
# T3 - exotic Word spaces become plain spaces
# ============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("no\u00a0break", "no break"),
        ("zero\u200bwidth", "zero width"),
        ("soft\u00adhyphen", "soft hyphen"),
        ("thin\u2009space", "thin space"),
        ("figure\u2007space", "figure space"),
        ("narrow\u202fspace", "narrow space"),
        ("ideographic\u3000space", "ideographic space"),
    ],
)
def test_exotic_spaces_become_plain_spaces(raw: str, expected: str) -> None:
    """T3: every non-breaking or invisible space normalises to a plain space."""
    assert normalize_whitespace(raw) == expected


def test_a_no_break_space_inside_a_citation_marker_does_not_split_it() -> None:
    """T3: the marker survives normalisation, which is what the grammar needs."""
    assert normalize_whitespace("claim [#abc]") == "claim [#abc]"
    assert normalize_whitespace("claim [#a\u00a0b]") == "claim [#a b]"


# ============================================================================
# T4 - whitespace runs collapse and the ends are stripped
# ============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  leading and trailing  ", "leading and trailing"),
        ("multiple   spaces", "multiple spaces"),
        ("tabs\tand\ttabs", "tabs and tabs"),
        ("line\nbreak", "line break"),
        ("carriage\r\nreturn", "carriage return"),
        ("mixed \t \n whitespace", "mixed whitespace"),
    ],
)
def test_whitespace_runs_collapse(raw: str, expected: str) -> None:
    """T4: whitespace runs collapse to a single space and the ends are stripped."""
    assert normalize_whitespace(raw) == expected


def test_normalisation_is_idempotent() -> None:
    """T4: normalising already-normalised text changes nothing."""
    raw = "  A\u00a0 grounded   claim\u200b.  "
    once = normalize_whitespace(raw)
    assert normalize_whitespace(once) == once


def test_empty_text_normalises_to_empty_text() -> None:
    """Normalisation of an empty string is the empty string."""
    assert normalize_whitespace("") == ""


def test_whitespace_only_text_normalises_to_empty_text() -> None:
    """Normalisation of a whitespace-only string is the empty string."""
    assert normalize_whitespace(" \t\n ") == ""


# ============================================================================
# join_texts
# ============================================================================


def test_join_texts_drops_empty_fragments() -> None:
    """An empty fragment must not leave a stray separator behind."""
    assert join_texts(["first", "", "second"]) == "first second"


def test_join_texts_normalises_the_result() -> None:
    """The joined result is normalised like any other text."""
    assert join_texts(["  first ", "second  "]) == "first second"


def test_join_texts_of_nothing_is_empty() -> None:
    """Joining no fragments yields the empty string."""
    assert join_texts([]) == ""


def test_join_texts_honours_a_custom_separator() -> None:
    """A caller can choose the separator, e.g. a pipe for table rows."""
    assert join_texts(["a", "b"], separator=" | ") == "a | b"


# ============================================================================
# strip_control_characters
# ============================================================================


def test_control_characters_are_removed() -> None:
    """Characters XML forbids cannot survive into a saved document."""
    assert strip_control_characters("bell\x07text") == "belltext"


def test_tabs_and_newlines_are_kept() -> None:
    """Tab, newline and carriage return are legal XML content."""
    assert strip_control_characters("a\tb\nc\rd") == "a\tb\nc\rd"


def test_ordinary_text_is_untouched() -> None:
    """Ordinary text passes through unchanged."""
    assert strip_control_characters("Grounded claim.") == "Grounded claim."
