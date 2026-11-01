"""Regenerate the shared whitespace and format corpus.

The corpus is the one file that holds the Kotlin tier and the Python tier to
the same rule: the answers in it are Python's answers, because Python is the
yardstick. This script writes them, from the Python implementations themselves,
so that no expected value is ever written by hand.

Usage:
    python backend/scripts/generate_word_shared_corpus.py

The file it writes is read by the Kotlin test as a classpath resource and by
the Python test by path; see :mod:`tests.word_shared_corpus` for the format.

Regenerating the corpus re-bases the Kotlin tier on Python's current answers.
That is the right thing to do when the rule changes on purpose, and the wrong
thing to do to make a failing test pass: a test that fails because the two
tiers disagree is the drift detector this corpus exists to be.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from lmeditor.word.formats import (  # noqa: E402
    describe_unsupported_format,
    is_legacy_doc,
    is_zip_package,
)
from lmeditor.word.text import (  # noqa: E402
    join_texts,
    normalize_whitespace,
    strip_control_characters,
)
from tests.word_shared_corpus import CORPUS_PATH, encode_field  # noqa: E402

#: The header of the corpus: what the file is, and how to read it.
HEADER: tuple[str, ...] = (
    "# The shared whitespace and format corpus.",
    "#",
    "# One file, read by both tiers: the Kotlin test reads it as a classpath",
    "# resource and the Python test reads it by path. The expected values are",
    "# Python's answers -- Python is the yardstick -- and they are written by",
    "# `backend/scripts/generate_word_shared_corpus.py`, never by hand.",
    "#",
    "# One record per line, tab-separated. The first field names the kind:",
    "#   normalize  name  input  expected",
    "#   join       name  separator  fragments  expected",
    "#   strip      name  input  expected",
    "#   format     name  bytes-hex  is_legacy_doc  is_zip_package  description",
    "#",
    "# A text field is ASCII: a tab is \\t, a line feed is \\n, a carriage return",
    "# is \\r, a backslash is \\\\, a pipe is \\|, a code point inside the Basic",
    "# Multilingual Plane is \\uXXXX and one above it is \\UXXXXXXXX. The",
    "# fragments of a join record are separated by a pipe.",
    "#",
    "# A blank line is ignored and a line that starts with # is a comment.",
)


def _python_whitespace() -> tuple[int, ...]:
    """Every code point Python calls whitespace, in order.

    The scan stops at the Basic Multilingual Plane because Unicode has no
    whitespace above it: a character that high would have to be added to this
    list for the corpus to stay exhaustive.
    """
    return tuple(
        code_point
        for code_point in range(0x10000)
        if chr(code_point).isspace()
    )


#: The inputs whose shape matters more than the characters they carry.
_NORMALIZE_INPUTS: tuple[tuple[str, str], ...] = (
    ("empty", ""),
    ("a-single-space", " "),
    ("leading-and-trailing", "  leading and trailing  "),
    ("nel-between-words", "abc\u0085def"),
    ("nel-only", "\u0085"),
    ("nel-leading", "\u0085abc"),
    ("nel-trailing", "abc\u0085"),
    ("nel-run", "a\u0085\u0085b"),
    ("nel-around-spaces", "a \u0085 b"),
    ("carriage-return-and-line-feed", "a\r\nb"),
    ("quad-through-hair-space", "a\u2000b\u2001c\u2002d\u2003e\u2004f\u2005g\u2006h\u2007i\u2008j\u2009k\u200al"),
    ("line-and-paragraph-separator", "a\u2028b\u2029c"),
    ("every-replaced-space", "a\u00a0\u2007\u202f\u2009\u200b\u00ad\u3000b"),
    ("astral-character", "a\U0001f600b"),
    ("combining-mark", "e\u0301"),
    ("control-character", "a\u0007b"),
    ("mixed-whitespace", "  a\u00a0\u0009b\u0085  "),
    ("ordinary-text", "Grounded claim."),
)

#: The inputs of the stripping rule whose shape matters.
_STRIP_INPUTS: tuple[tuple[str, str], ...] = (
    ("empty", ""),
    ("a-bell", "a\u0007b"),
    ("tabs-and-newlines-kept", "a\tb\nc\rd"),
    ("every-control-removed", "\u0000\u0001\u0002\u001f"),
    ("nel-kept", "a\u0085b"),
    ("no-break-space-kept", "a\u00a0b"),
    ("astral-character-kept", "a\U0001f600b"),
    ("combining-mark-kept", "e\u0301"),
    ("ordinary-text-kept", "Grounded claim."),
)

#: The joins: the separator and the fragments.
_JOIN_INPUTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nothing", " ", ()),
    ("default-separator", " ", ("a", "b")),
    ("empty-fragments-dropped", " ", ("first", "", "second")),
    ("only-empty-fragments", " ", ("", "")),
    ("custom-separator", " | ", ("a", "b")),
    ("pipe-separator", "|", ("a", "b")),
    ("tab-separator-collapses", "\t", ("a", "b")),
    ("empty-separator", "", ("a", "b")),
    ("normalises-the-parts", " ", ("  first ", " second  ")),
    ("nel-in-a-fragment", " ", ("a\u0085b", "c")),
    ("a-fragment-with-a-pipe", " ", ("a|b", "c")),
)

#: The bytes the format rules are asked about.
_FORMAT_INPUTS: tuple[tuple[str, bytes], ...] = (
    ("ole2-magic", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
    ("ole2-magic-with-a-tail", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\xff\x00"),
    ("zip-magic", b"PK\x03\x04"),
    ("zip-magic-with-a-tail", b"PK\x03\x04\x14\x00\x00\x00"),
    ("truncated-ole2-magic", b"\xd0\xcf\x11\xe0"),
    ("truncated-zip-magic", b"PK\x03"),
    ("neither", b"Hello"),
    ("empty", b""),
)


def _normalize_records() -> list[str]:
    """The ``normalize`` records, in the order they are written."""
    records: list[str] = []
    for code_point in _python_whitespace():
        raw = f"a{chr(code_point)}b"
        records.append(
            f"normalize\twhitespace-U+{code_point:04X}\t"
            f"{encode_field(raw)}\t{encode_field(normalize_whitespace(raw))}"
        )
    for name, raw in _NORMALIZE_INPUTS:
        records.append(
            f"normalize\t{name}\t{encode_field(raw)}\t"
            f"{encode_field(normalize_whitespace(raw))}"
        )
    return records


def _strip_records() -> list[str]:
    """The ``strip`` records, in the order they are written."""
    records: list[str] = []
    for code_point in range(0x20):
        raw = f"a{chr(code_point)}b"
        records.append(
            f"strip\tcontrol-U+{code_point:04X}\t"
            f"{encode_field(raw)}\t{encode_field(strip_control_characters(raw))}"
        )
    for name, raw in _STRIP_INPUTS:
        records.append(
            f"strip\t{name}\t{encode_field(raw)}\t"
            f"{encode_field(strip_control_characters(raw))}"
        )
    return records


def _join_records() -> list[str]:
    """The ``join`` records, in the order they are written."""
    records: list[str] = []
    for name, separator, parts in _JOIN_INPUTS:
        fragments = "|".join(encode_field(part) for part in parts)
        expected = join_texts(parts, separator=separator)
        records.append(
            f"join\t{name}\t{encode_field(separator)}\t{fragments}\t"
            f"{encode_field(expected)}"
        )
    return records


def _format_records() -> list[str]:
    """The ``format`` records, in the order they are written."""
    records: list[str] = []
    for name, data in _FORMAT_INPUTS:
        records.append(
            f"format\t{name}\t{data.hex()}\t"
            f"{'true' if is_legacy_doc(data) else 'false'}\t"
            f"{'true' if is_zip_package(data) else 'false'}\t"
            f"{encode_field(describe_unsupported_format(data))}"
        )
    return records


def render() -> str:
    """The whole corpus, as the text of the file."""
    lines = list(HEADER)
    lines.extend(_normalize_records())
    lines.extend(_join_records())
    lines.extend(_strip_records())
    lines.extend(_format_records())
    return "\n".join(lines) + "\n"


def main() -> None:
    """Write the corpus."""
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {CORPUS_PATH}")


if __name__ == "__main__":
    main()
