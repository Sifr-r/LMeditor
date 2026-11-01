"""The real-Word conformance check, as a test.

The check itself lives in ``tests/word_real_word_check.py``; this module runs
it and turns its answer into a test result.  It is skipped when Microsoft
Word is not installed, so the suite stays green on a machine without Office.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.word_real_word_check import (  # noqa: E402
    HARNESS,
    build_feature_document,
    build_negative_controls,
    build_new_constructs_document,
    check_feature_document,
    check_new_constructs_document,
    run_word_check,
    word_is_available,
)
from lmeditor.word.writer import WordWriter  # noqa: E402

pytestmark = pytest.mark.skipif(
    not word_is_available(),
    reason="Microsoft Word is not installed on this machine",
)


def test_word_is_reachable() -> None:
    """The precondition of every check below: Word can be driven."""
    assert word_is_available()
    assert HARNESS.exists()


def test_word_opens_the_document_and_sees_what_the_model_describes(
    tmp_path: Path,
) -> None:
    """Word opens the package and reports the structure the model describes.

    This is the check a package-level test cannot make.  Word has behaviours
    that a perfectly well-formed package still violates -- it reads two
    adjacent tables as one (W11), and it re-lays out a table whose rows do
    not fill the grid (W12) -- so only Word can say whether the document is
    really what the model claims.
    """
    feature = tmp_path / "feature.docx"
    feature.write_bytes(WordWriter().write(build_feature_document()))

    report = run_word_check(feature)
    problems = check_feature_document(report)
    assert not problems, "\n".join(problems)
    assert report.opened


def test_word_sees_the_constructs_the_audit_gaps_added(tmp_path: Path) -> None:
    """Word sees the comment, the endnote, the revision, the section and the picture.

    A package-level test can only prove the XML says what the model says.
    Word is the only implementation that can say whether a comment is a
    *comment*, whether a deleted run is a *revision* Word marks up, whether a
    header keeps the *role* its reference type gives it, and whether a roman
    list is rendered in roman.
    """
    document = tmp_path / "new-constructs.docx"
    document.write_bytes(WordWriter().write(build_new_constructs_document()))

    report = run_word_check(document)
    problems = check_new_constructs_document(report)
    assert not problems, "\n".join(problems)
    assert report.opened


def test_word_refuses_a_broken_package(tmp_path: Path) -> None:
    """The check has teeth: a broken package is refused, not waved through.

    A conformance check that only ever sees good files cannot tell a good
    file from a bad one.  These two controls -- a truncated package and a
    package whose document part is not XML -- are what the good package is
    measured against.
    """
    feature = tmp_path / "feature.docx"
    feature.write_bytes(WordWriter().write(build_feature_document()))
    for control in build_negative_controls(feature, tmp_path):
        report = run_word_check(control)
        assert not report.opened, f"Word accepted the broken package {control.name}"
        assert report.error, "a refusal must say why"


def test_the_suite_can_run_the_check_as_a_script() -> None:
    """The check is runnable on its own, for a human-readable report."""
    script = Path(__file__).resolve().parent / "word_real_word_check.py"
    assert script.exists()
    assert shutil.which("powershell") is not None
