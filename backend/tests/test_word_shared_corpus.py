"""The whitespace and format rules, pinned against the Kotlin tier.

The Python tier carries these rules in :mod:`lmeditor.word.text` and
:mod:`lmeditor.word.formats`; the Kotlin tier carries the same rules in
``lmeditor.word.WordText`` and ``lmeditor.word.WordFormats``. Two
implementations of one rule are worth nothing unless they are *shown* to
agree, and a door between them is not an option here: the rules are consulted
on every block of every document, so a seam round trip per block would cost
more than the rule is worth.

So the two are held together by one file: the shared corpus, whose expected
values are Python's answers. The Kotlin test reads the very same file and
asserts the tier gives the same answers. One file, not two: two copies of the
answers could drift apart, which is the failure this corpus exists to
prevent.

These tests assert the Python side of that bargain: that the rules here still
give the answers the corpus pins. When they stop, the corpus is stale and the
Kotlin tier is being measured against a yardstick that has moved.
"""

from __future__ import annotations

from lmeditor.word.formats import (
    describe_unsupported_format,
    is_legacy_doc,
    is_zip_package,
)
from lmeditor.word.text import (
    join_texts,
    normalize_whitespace,
    strip_control_characters,
)
from tests.word_shared_corpus import read_corpus

#: The corpus, read once: it is a file, and it does not change while the
#: suite runs.
_CORPUS = read_corpus()


def test_the_python_whitespace_rule_agrees_with_the_kotlin_tier() -> None:
    """The normalisation the corpus pins is the normalisation Python gives."""
    disagreements = [
        f"normalize {record.name}: corpus={record.expected!r} "
        f"python={normalize_whitespace(record.input)!r}"
        for record in _CORPUS.normalize
        if normalize_whitespace(record.input) != record.expected
    ]
    assert not disagreements, "\n".join(disagreements)


def test_the_python_join_rule_agrees_with_the_kotlin_tier() -> None:
    """The joins the corpus pins are the joins Python gives."""
    disagreements = [
        f"join {record.name}: corpus={record.expected!r} "
        f"python={join_texts(record.parts, separator=record.separator)!r}"
        for record in _CORPUS.join
        if join_texts(record.parts, separator=record.separator) != record.expected
    ]
    assert not disagreements, "\n".join(disagreements)


def test_the_python_stripping_rule_agrees_with_the_kotlin_tier() -> None:
    """The strippings the corpus pins are the strippings Python gives."""
    disagreements = [
        f"strip {record.name}: corpus={record.expected!r} "
        f"python={strip_control_characters(record.input)!r}"
        for record in _CORPUS.strip
        if strip_control_characters(record.input) != record.expected
    ]
    assert not disagreements, "\n".join(disagreements)


def test_the_python_format_rules_agree_with_the_kotlin_tier() -> None:
    """The formats the corpus pins are the formats Python recognises."""
    disagreements = [
        f"format {record.name}: corpus="
        f"({record.is_legacy_doc}, {record.is_zip_package}, {record.description!r}) "
        f"python=({is_legacy_doc(record.data)}, {is_zip_package(record.data)}, "
        f"{describe_unsupported_format(record.data)!r})"
        for record in _CORPUS.formats
        if is_legacy_doc(record.data) != record.is_legacy_doc
        or is_zip_package(record.data) != record.is_zip_package
        or describe_unsupported_format(record.data) != record.description
    ]
    assert not disagreements, "\n".join(disagreements)


def test_the_shared_corpus_covers_every_code_point_python_calls_whitespace() -> None:
    """The corpus reaches every character Python calls whitespace.

    The corpus is what says the two tiers agree, so a character Python calls
    whitespace but the corpus never mentions is a character the two tiers are
    never compared over. The scan stops at the Basic Multilingual Plane
    because Unicode has no whitespace above it.
    """
    covered = {
        ord(char)
        for record in _CORPUS.normalize
        for char in record.input
    }
    uncovered = sorted(
        code_point
        for code_point in range(0x10000)
        if chr(code_point).isspace() and code_point not in covered
    )
    assert not uncovered, (
        "the corpus must mention every code point Python calls whitespace; "
        f"these are missing: {[f'U+{code:04X}' for code in uncovered]}"
    )
