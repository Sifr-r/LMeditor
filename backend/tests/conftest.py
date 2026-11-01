"""Shared fixtures for the backend test suite.

The Python tier delegates the deterministic domain rules that cross the seam
to the Kotlin tier, so a test that exercises one of those rules needs the tier
built.

The marker below skips such a test when the tier is absent. It *skips* it
rather than passing it: an absent comparison is not evidence of agreement. The
harness that proves the two tiers agree is
``backend/scripts/verify_kotlin_parity.py``, and its own test is
``tests/test_kotlin_tier_parity.py``.

A module that has been swapped onto the tier is listed in
:data:`TIER_BACKED_MODULES`. Swapping a module removes its Python
implementation, so the tests that exercise it can only pass with the tier
present. Those tests are not marked by hand -- they are recognised at run time:
:func:`pytest_runtest_call` turns the bridge's "the tier is not there" error
into a skip, so a test that never reaches the tier still runs, and a test
that does is skipped rather than failed.

To build the tier::

    cd kotlin && gradlew build
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lmeditor.bridge import BridgeUnavailableError


@pytest.fixture(autouse=True)
def isolated_source_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test clear or populate the operator's source archive."""
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", str(tmp_path / "source-archive"))

REPO_ROOT = Path(__file__).resolve().parents[2]
TIER_LIB_DIR = REPO_ROOT / "kotlin" / "bridge" / "build" / "libs"

#: The Python modules that have been swapped onto the Kotlin tier.
#:
#: Each of these is a *door*: it keeps the Python API and answers with the
#: tier's answers. The implementation it replaced is frozen in
#: ``lmeditor._reference``, where the parity harness can still compare the
#: two sides -- for the modules the harness covers. Which check witnesses
#: which frozen module is the ledger
#: ``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py``;
#: a frozen module no check compares yet is declared there against the
#: Kotlin file that holds its counterpart.
TIER_BACKED_MODULES: tuple[str, ...] = (
    "lmeditor.verification.claim_extractor",
    "lmeditor.verification.citation_checker",
    "lmeditor.provenance.chunker",
    "lmeditor.provenance.lineage",
    "lmeditor.provenance.indexer",
    "lmeditor.provenance.service",
    "lmeditor.workflow.state",
    "lmeditor.workflow.edges",
    "lmeditor.discovery.outliner",
    "lmeditor.discovery.perspective",
    "lmeditor.discovery.source_analysis",
    "lmeditor.discovery.dialogue",
    "lmeditor.editing.editor",
    "lmeditor.factcheck.service",
    "lmeditor.factcheck.web",
    "lmeditor.ingestion.limits",
    "lmeditor.ingestion.loaders",
    "lmeditor.ingestion.registry",
    "lmeditor.ingestion.pdf_metadata",
    "lmeditor.ingestion.url_policy",
    "lmeditor.ingestion.http_safety",
    "lmeditor.ingestion.resolvers",
    "lmeditor.ingestion.service",
    "lmeditor.ingestion.source_store",
    "lmeditor.word.reader",
    "lmeditor.word.writer",
    "lmeditor.server.sources",
)


def tier_jars() -> list[Path]:
    """The Kotlin tier's jars, if it has been built."""
    if not TIER_LIB_DIR.is_dir():
        return []
    return sorted(
        jar
        for jar in TIER_LIB_DIR.glob("*.jar")
        if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar")
    )


def tier_is_built() -> bool:
    """Whether the Kotlin tier is available to the Python tier."""
    return bool(tier_jars())


requires_kotlin_tier = pytest.mark.skipif(
    not tier_is_built(),
    reason=(
        "the Kotlin tier has not been built; "
        "run `cd kotlin && gradlew build` to enable this test"
    ),
)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> object:
    """Skip, rather than fail, a test that needed the tier and could not have it.

    The bridge raises :class:`BridgeUnavailableError` when the Kotlin tier is
    not there. A test that hit that error has not proved anything -- it was
    never run -- so it is reported as skipped. An absent comparison is not a
    failing comparison, and it is certainly not a passing one.
    """
    try:
        return (yield)
    except BridgeUnavailableError as exc:
        if ("require_live_kotlin_tier", True) in item.user_properties:
            pytest.fail(f"the required Kotlin tier failed during the integration gate: {exc}")
        pytest.skip(
            f"the Kotlin tier has not been built, so this test could not run: {exc}"
        )


@pytest.fixture(scope="session")
def kotlin_tier() -> bool:
    """Whether the Kotlin tier is built. Skips the test when it is not."""
    if not tier_is_built():
        pytest.skip(
            "the Kotlin tier has not been built; "
            "run `cd kotlin && gradlew build` to enable this test"
        )
    return True


@pytest.fixture(scope="session")
def java_executable() -> str:
    """The java executable the Kotlin tier runs on."""
    import shutil

    found = shutil.which("java")
    if found is None:
        pytest.skip("no java executable is on PATH")
    return found


def gradle_wrapper() -> Path:
    """The Gradle wrapper of the Kotlin tier."""
    name = "gradlew.bat" if sys.platform == "win32" else "gradlew"
    return REPO_ROOT / "kotlin" / name
