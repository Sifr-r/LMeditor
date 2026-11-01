"""The frozen witness and the Kotlin tier must hold to the same bound.

The Kotlin tier declares the bound it holds a compressed stream to in one
place -- ``lmeditor.core.DecompressionLimits`` -- and both the DOCX reader
and the PDF reader are held to it. The frozen Python witness cannot import
that constant: a witness that asked the tier what the bound is would agree
with it by construction and would prove nothing, so the witness restates the
number.

Restating a number is exactly how two copies drift apart, so these tests
hold the copies together:

* the first two compare the *numbers*, read out of the tier's own source.
  That is a value comparison: it proves the two copies hold the same number,
  and it is the strongest claim a pure-Python witness can make about a
  Kotlin constant it may not import;
* the third asks both tiers to refuse the same bomb and checks that they
  refuse it the same way, which is the behavioural half of the claim.

The witness stays plain Python. It may not import the tier and it may not
reach for the Kotlin side at all -- ``tests/test_parity_reference.py``
enforces that -- so the numbers are read out of the tier's source, not out
of a running tier.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lmeditor._reference.word_reader import (
    _MAX_COMPRESSION_RATIO,
    _MAX_MEMBER_UNCOMPRESSED_BYTES,
    _MAX_PACKAGE_MEMBERS,
    _MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from lmeditor._reference.word_reader import WordReader as WitnessWordReader
from lmeditor.core.exceptions import WordParseError
from lmeditor.tier import word as tier_word
from tests.word_fixtures import build_compression_bomb_package

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The Kotlin source that holds the shared bound. Both the DOCX reader and
#: the PDF reader are held to it, which is why it is written down once.
SHARED_LIMITS = (
    REPO_ROOT
    / "kotlin"
    / "core"
    / "src"
    / "main"
    / "kotlin"
    / "lmeditor"
    / "core"
    / "DecompressionLimits.kt"
)

#: The Kotlin source that holds the DOCX reader's own package limits.
WORD_READER = (
    REPO_ROOT
    / "kotlin"
    / "word"
    / "src"
    / "main"
    / "kotlin"
    / "lmeditor"
    / "word"
    / "WordReader.kt"
)


def _kotlin_constant(source: Path, name: str) -> int:
    """The value of the Kotlin constant ``name`` declared in ``source``.

    The constant is a ``const val`` whose value is an arithmetic expression of
    integer literals. The expression is evaluated here, so the comparison is
    between the tier's number and the witness's number and not between two
    spellings of the same expression.
    """
    text = source.read_text(encoding="utf-8")
    match = re.search(rf"const val {name}\s*:\s*(?:Int|Long)\s*=\s*([^;\n]+)", text)
    assert match is not None, f"{name} is not declared in {source}"
    # A Kotlin literal may carry a `L` suffix; Python's does not.
    expression = re.sub(r"(\d)L\b", r"\1", match.group(1).strip())
    return _value_of(ast.parse(expression, mode="eval").body)


def _value_of(node: ast.expr) -> int:
    """The value of an arithmetic expression of integer literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp):
        left = _value_of(node.left)
        right = _value_of(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
    raise AssertionError(f"the constant is not an arithmetic expression: {ast.dump(node)}")


def test_the_witness_holds_the_tier_shared_size_bound() -> None:
    """The witness holds a package part to the size the tier holds it to.

    The tier's bound is the shared one: a package part and a PDF content
    stream are the same threat, so they are held to the same number.
    """
    assert _MAX_MEMBER_UNCOMPRESSED_BYTES == _kotlin_constant(
        SHARED_LIMITS, "MAX_EXPANDED_BYTES"
    ), (
        "the witness's member bound has drifted from the tier's shared bound; "
        "change them together"
    )


def test_the_witness_holds_the_tier_shared_ratio_bound() -> None:
    """The witness holds a package part to the ratio the tier holds it to."""
    assert _MAX_COMPRESSION_RATIO == _kotlin_constant(
        SHARED_LIMITS, "MAX_COMPRESSION_RATIO"
    ), (
        "the witness's compression ratio has drifted from the tier's shared "
        "bound; change them together"
    )


def test_the_witness_holds_the_tier_package_limits() -> None:
    """The witness holds a package to the member count and the total the tier holds it to.

    These two are the DOCX reader's own limits rather than the shared ones,
    but they are restated in the witness for the same reason, so they are
    pinned here too.
    """
    assert _MAX_PACKAGE_MEMBERS == _kotlin_constant(WORD_READER, "MAX_PACKAGE_MEMBERS")
    assert _MAX_TOTAL_UNCOMPRESSED_BYTES == _kotlin_constant(
        WORD_READER, "MAX_TOTAL_UNCOMPRESSED_BYTES"
    )


def test_both_tiers_refuse_the_same_bomb() -> None:
    """The witness and the tier refuse the same bomb in the same way.

    The witness is the oracle the tier is compared against, so a bomb the
    witness refuses must be a bomb the tier refuses as well -- and both must
    refuse it for the same reason, naming the same bound.
    """
    payload = build_compression_bomb_package()
    ratio = _kotlin_constant(SHARED_LIMITS, "MAX_COMPRESSION_RATIO")

    with pytest.raises(WordParseError) as witness_failure:
        WitnessWordReader().read_bytes(payload)
    with pytest.raises(WordParseError) as tier_failure:
        tier_word.read_document(payload)

    for failure in (witness_failure.value, tier_failure.value):
        message = str(failure)
        assert "compressed" in message, message
        assert re.search(rf"more than the {ratio} allowed", message), message
