"""The Python and Kotlin tiers must agree on the rules they share.

`backend/scripts/verify_kotlin_parity.py` asks both tiers the same questions
and compares the answers. This test runs that script and fails when the two
implementations disagree.

The test is skipped, not passed, when the Kotlin tier has not been built: an
absent comparison is not evidence of agreement.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from lmeditor.bridge import TIER_MODULES

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "backend" / "scripts" / "verify_kotlin_parity.py"


def _tier_is_built() -> bool:
    """Whether every module jar the bridge needs has been built.

    The list is the bridge client's own, so this test cannot drift from the
    classpath the bridge actually builds. A second copy of the list here would
    drift silently: it left ``:vault`` and ``:notebooks`` out, so the test
    could have run -- and reported agreement -- while a module jar the bridge
    needs was missing.
    """
    for module in TIER_MODULES:
        lib_dir = REPO_ROOT / "kotlin" / module / "build" / "libs"
        if not lib_dir.is_dir():
            return False
        if not any(
            jar.name.endswith(".jar")
            and not jar.name.endswith("-sources.jar")
            and not jar.name.endswith("-javadoc.jar")
            for jar in lib_dir.glob("*.jar")
        ):
            return False
    return True


pytestmark = pytest.mark.skipif(
    not _tier_is_built(),
    reason="the Kotlin tier has not been built; run `cd kotlin && gradlew build` first",
)


def test_the_two_tiers_agree_on_the_shared_rules() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
        timeout=600,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, f"the tiers disagree:\n{output}"

    # The summary is read, not matched as text: a substring test for "0 agreed"
    # also matches "10 agreed", which would fail the suite for the wrong reason
    # as soon as the harness grew a second check.
    summary = re.search(r"(\d+) agreed, (\d+) disagreed, (\d+) skipped", output)
    assert summary is not None, f"the harness did not report a summary:\n{output}"
    agreed, disagreed, skipped = (int(group) for group in summary.groups())
    assert agreed > 0, f"no rule could be compared:\n{output}"
    assert disagreed == 0, f"the tiers disagree:\n{output}"
    assert skipped == 0, f"at least one rule could not be compared:\n{output}"
