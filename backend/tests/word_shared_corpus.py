"""The shared whitespace and format corpus: one file, read by both tiers.

The Kotlin tier carries the whitespace rules in ``lmeditor.word.WordText`` and
the Python tier carries the same rules in :mod:`lmeditor.word.text`. Two
implementations of one rule are worth nothing unless they are *shown* to
agree, and a door between them is not an option here:
``normalize_whitespace`` runs inside a Pydantic validator on every
``WordBlock`` the backend builds, so a seam round trip per block would cost
more than the rule is worth.

So the two are held together by a file instead. It holds the inputs and the
answers Python gives for them; the Kotlin test reads the same file and asserts
the tier gives the same answers. One file, not two: two copies of the answers
could drift apart, which is the failure the corpus exists to prevent.

The format is line-oriented and tab-separated, so that neither side needs a
parser it does not have:

* every line is a record whose first field names its kind;
* the remaining fields are separated by a tab;
* a text field is written in ASCII, with every character outside printable
  ASCII written as an escape -- ``\\t``, ``\\n``, ``\\r``, ``\\uXXXX`` for a
  code point inside the Basic Multilingual Plane and ``\\UXXXXXXXX`` for one
  above it;
* a backslash and a pipe are written ``\\\\`` and ``\\|``, so that a pipe can
  be told apart from the separator that joins the fragments of a ``join``
  record;
* a line that starts with ``#`` is a comment, and a blank line is ignored.

The file is read from the Kotlin side as a classpath resource and from this
side by path, but it is the same file either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: The root of the repository, three levels up from this file.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The corpus itself. It lives under the Kotlin module's test resources so
#: that the Kotlin test can read it as a classpath resource; this side reaches
#: it by path, the way the Kotlin dependency and parity tests already do.
CORPUS_PATH: Final[Path] = (
    REPO_ROOT
    / "kotlin"
    / "word"
    / "src"
    / "test"
    / "resources"
    / "fixtures"
    / "word_whitespace_corpus.tsv"
)


@dataclass(frozen=True)
class NormalizeRecord:
    """One answer of ``normalize_whitespace``."""

    name: str
    input: str
    expected: str


@dataclass(frozen=True)
class JoinRecord:
    """One answer of ``join_texts``."""

    name: str
    separator: str
    parts: tuple[str, ...]
    expected: str


@dataclass(frozen=True)
class StripRecord:
    """One answer of ``strip_control_characters``."""

    name: str
    input: str
    expected: str


@dataclass(frozen=True)
class FormatRecord:
    """One answer of the format-detection rules."""

    name: str
    data: bytes
    is_legacy_doc: bool
    is_zip_package: bool
    description: str


@dataclass(frozen=True)
class Corpus:
    """Every record of the corpus, grouped by the rule it pins."""

    normalize: tuple[NormalizeRecord, ...]
    join: tuple[JoinRecord, ...]
    strip: tuple[StripRecord, ...]
    formats: tuple[FormatRecord, ...]


#: The fields of each kind of record, in the order they are written.
_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "normalize": ("name", "input", "expected"),
    "join": ("name", "separator", "parts", "expected"),
    "strip": ("name", "input", "expected"),
    "format": ("name", "bytes", "is_legacy_doc", "is_zip_package", "description"),
}


def encode_field(text: str) -> str:
    """Write [text] the way a corpus field writes it.

    Every character outside printable ASCII becomes an escape, so the file
    itself is ASCII and cannot be read differently by two readers.
    """
    out: list[str] = []
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char == "|":
            out.append("\\|")
        elif char == "\t":
            out.append("\\t")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif "\x20" <= char <= "\x7e":
            out.append(char)
        elif ord(char) <= 0xFFFF:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(f"\\U{ord(char):08X}")
    return "".join(out)


def decode_field(field: str) -> str:
    """Read the text a corpus field holds."""
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index = _decode_escape(field, index, out)
    return "".join(out)


def split_fragments(field: str) -> list[str]:
    """Read the fragments a ``join`` field holds.

    The fragments are separated by a pipe; a pipe that belongs to a fragment
    is written ``\\|`` and is not a separator.
    """
    fragments: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\":
            index = _decode_escape(field, index, current)
            continue
        if char == "|":
            fragments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    fragments.append("".join(current))
    return fragments


def _decode_escape(field: str, index: int, out: list[str]) -> int:
    """Decode the escape that starts at [index], and return the index after it."""
    if index + 1 >= len(field):
        raise ValueError(f"the field ends with an unfinished escape: {field!r}")
    marker = field[index + 1]
    if marker == "\\":
        out.append("\\")
        return index + 2
    if marker == "|":
        out.append("|")
        return index + 2
    if marker == "t":
        out.append("\t")
        return index + 2
    if marker == "n":
        out.append("\n")
        return index + 2
    if marker == "r":
        out.append("\r")
        return index + 2
    if marker == "u":
        out.append(chr(int(field[index + 2 : index + 6], 16)))
        return index + 6
    if marker == "U":
        out.append(chr(int(field[index + 2 : index + 10], 16)))
        return index + 10
    raise ValueError(f"unknown escape in the corpus: \\{marker}")


def read_corpus() -> Corpus:
    """Read the corpus, decoded and grouped by the rule it pins."""
    normalize: list[NormalizeRecord] = []
    join: list[JoinRecord] = []
    strip: list[StripRecord] = []
    formats: list[FormatRecord] = []
    for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        kind = fields[0]
        names = _FIELDS.get(kind)
        if names is None:
            raise ValueError(f"unknown kind of corpus record: {kind!r}")
        if len(fields) != len(names) + 1:
            raise ValueError(
                f"the {kind} record {fields[1]!r} has {len(fields) - 1} fields, "
                f"expected {len(names)}"
            )
        values = fields[1:]
        if kind == "normalize":
            normalize.append(
                NormalizeRecord(
                    name=values[0],
                    input=decode_field(values[1]),
                    expected=decode_field(values[2]),
                )
            )
        elif kind == "join":
            join.append(
                JoinRecord(
                    name=values[0],
                    separator=decode_field(values[1]),
                    parts=tuple(split_fragments(values[2])),
                    expected=decode_field(values[3]),
                )
            )
        elif kind == "strip":
            strip.append(
                StripRecord(
                    name=values[0],
                    input=decode_field(values[1]),
                    expected=decode_field(values[2]),
                )
            )
        else:
            formats.append(
                FormatRecord(
                    name=values[0],
                    data=bytes.fromhex(values[1]),
                    is_legacy_doc=values[2] == "true",
                    is_zip_package=values[3] == "true",
                    description=decode_field(values[4]),
                )
            )
    return Corpus(
        normalize=tuple(normalize),
        join=tuple(join),
        strip=tuple(strip),
        formats=tuple(formats),
    )
