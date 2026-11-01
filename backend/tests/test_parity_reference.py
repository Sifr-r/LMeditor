"""The parity harness must compare the Kotlin tier against a witness, not a mirror.

``backend/scripts/verify_kotlin_parity.py`` exists to prove that the Kotlin tier
and the Python tier agree. That proof is only worth something if the Python
side is an *independent* implementation. If the Python side asked the Kotlin
tier what the answer is, the two would agree by construction, the harness
would report a pass, and every real divergence would sail through.

The live modules under ``lmeditor.core`` and ``lmeditor.verification`` are
*doors* to the Kotlin tier: they answer by calling across the NDJSON seam. The
frozen copies in ``lmeditor._reference`` are the witnesses.

These tests enforce that mechanically, so a future swap cannot quietly turn the
harness into a self-comparison.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "backend" / "src"
PACKAGE_ROOT = SRC_ROOT / "lmeditor"
REFERENCE_ROOT = PACKAGE_ROOT / "_reference"
HARNESS = REPO_ROOT / "backend" / "scripts" / "verify_kotlin_parity.py"

#: The Python face of the Kotlin tier. A module that reaches this package
#: reaches the implementation it is supposed to be compared against.
TIER_PACKAGE = "lmeditor.tier"


def _module_name(path: Path) -> str:
    """The dotted name of the module a source file holds."""
    parts = list(path.relative_to(SRC_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_name_or_stem(path: Path) -> str:
    """The dotted name of a module, or its file stem when it lives outside the package."""
    try:
        return _module_name(path)
    except ValueError:
        return path.stem


def _package_of(module: str) -> str:
    """The package a module belongs to."""
    parent, _, _name = module.rpartition(".")
    return parent


def _resolve_relative(module: str, node: ast.ImportFrom) -> set[str]:
    """Resolve a relative import into the absolute names it can reach."""
    if not node.module:
        return set()
    package = _package_of(module)
    for _ in range(node.level - 1):
        package = _package_of(package)
    base = f"{package}.{node.module}" if package else node.module
    return {base}


def _imports_of(path: Path) -> set[str]:
    """Every module name the file can reach by importing."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name_or_stem(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names |= _resolve_relative(module, node)
            elif node.module:
                names.add(node.module)
                # ``from pkg import thing`` also reaches ``pkg.thing``.
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


def _all_modules() -> dict[str, Path]:
    """Every module in the ``lmeditor`` package, by dotted name."""
    modules: dict[str, Path] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        modules[_module_name(path)] = path
    return modules


def _doors(modules: dict[str, Path]) -> set[str]:
    """The modules that can reach the Kotlin tier, directly or transitively."""
    edges: dict[str, set[str]] = {}
    for name, path in modules.items():
        reachable: set[str] = set()
        for imported in _imports_of(path):
            if imported in modules:
                reachable.add(imported)
            else:
                # A package import reaches the modules inside it.
                prefix = f"{imported}."
                reachable.update(m for m in modules if m.startswith(prefix))
        edges[name] = reachable

    doors: set[str] = set()
    for name in modules:
        seen: set[str] = set()
        stack = list(edges[name])
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(edges.get(current, ()))
        if any(current == TIER_PACKAGE or current.startswith(f"{TIER_PACKAGE}.") for current in seen):
            doors.add(name)
    return doors


REFERENCE_MODULES = _all_modules()
REFERENCE_DOORS = _doors(REFERENCE_MODULES)


def test_the_reference_package_has_no_witness_that_reaches_the_tier() -> None:
    """Nothing in the frozen reference may call across the NDJSON seam."""
    offenders = sorted(
        name for name in REFERENCE_DOORS if name.startswith("lmeditor._reference")
    )

    assert offenders == [], (
        "the frozen reference must stay pure Python, but these modules reach the "
        f"Kotlin tier: {offenders}"
    )


def test_the_harness_draws_its_python_side_only_from_the_frozen_reference() -> None:
    """The harness may talk to the tier; it may not use a door as its witness."""
    doors = _doors(REFERENCE_MODULES)
    harness_imports = _imports_of(HARNESS)

    offenders = sorted(
        imported
        for imported in harness_imports
        if imported in doors
        # The bridge client is how the harness reaches the tier on purpose.
        and imported != "lmeditor.bridge"
        and not imported.startswith("lmeditor.bridge.")
    )

    assert offenders == [], (
        "the parity harness compares the Kotlin tier against these modules, but "
        f"they reach the tier themselves, so the comparison proves nothing: {offenders}"
    )


def test_the_door_detection_actually_detects_the_swapped_modules() -> None:
    """A guard that cannot fail is not a guard.

    The swapped modules are doors today. If this test ever fails, the detector
    has stopped working -- and the two tests above would be passing vacuously.
    """
    assert "lmeditor.core.citations" in REFERENCE_DOORS
    assert "lmeditor.core.sections" in REFERENCE_DOORS


@pytest.mark.parametrize(
    "module_name",
    sorted(name for name in REFERENCE_MODULES if name.startswith("lmeditor._reference")),
)
def test_every_reference_module_is_importable(module_name: str) -> None:
    """A frozen module that cannot be imported cannot witness anything."""
    import importlib

    importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# The witness must answer the way the original answered
# ---------------------------------------------------------------------------


def test_the_frozen_grammar_reads_every_accepted_marker_form() -> None:
    """The frozen witness must still be the original's grammar.

    These are the assertions the original grammar was pinned by. If a rewrite
    of the frozen copy changed an answer, the harness would be measuring the
    Kotlin tier against a *different* rule and would report agreement that
    means nothing.
    """
    from lmeditor._reference.citations import extract_chunk_ids, iter_citation_spans

    assert extract_chunk_ids("A supported fact [#abc123].") == ["abc123"]
    assert extract_chunk_ids("A supported fact [abc123].") == ["abc123"]
    assert extract_chunk_ids("A supported fact [cite: abc123].") == ["abc123"]
    assert extract_chunk_ids("A supported fact [#a, #b].") == ["a", "b"]
    assert extract_chunk_ids("Data from [chunk_1] and [cite: s2].") == ["chunk_1", "s2"]


def test_the_frozen_grammar_never_reads_markdown_as_a_citation() -> None:
    """Markdown links and images are not citations."""
    from lmeditor._reference.citations import extract_chunk_ids

    markdown = "See [the source paper](https://example.org/paper) and ![a chart](https://example.org/c.png)."

    assert extract_chunk_ids(markdown) == []


def test_the_frozen_grammar_reports_the_whole_marker_span() -> None:
    """A comma group reports the whole marker span for each of its ids."""
    from lmeditor._reference.citations import iter_citation_spans

    text = "First [#a, #b]. Then [c]."
    spans = iter_citation_spans(text)

    assert [chunk_id for chunk_id, _s, _e in spans] == ["a", "b", "c"]
    assert spans[0][1] == text.index("[#a, #b]")
    assert spans[0][2] == text.index("[#a, #b]") + len("[#a, #b]")
    assert spans[2][1] == text.index("[c]")


def test_the_frozen_extractor_keeps_the_citation_attached_to_its_sentence() -> None:
    """The claim extractor the witness uses still reads the marker."""
    from lmeditor._reference.claim_extractor import extract_claims

    claims = extract_claims("A supported fact [#abc123].", "s")

    assert len(claims) == 1
    assert claims[0].cited_chunk_ids == ["abc123"]
    assert claims[0].text == "A supported fact"


def test_the_frozen_operator_rule_keeps_the_provenance_and_drops_the_verdict() -> None:
    """The operator-edit rule the witness uses is still the original's rule."""
    from lmeditor._reference.sections import merge_operator_text
    from lmeditor.core.schemas import CitationLineage, Claim, SectionDraft
    from lmeditor.core.types import SectionStatus

    drafted = SectionDraft(
        section_id="sec-1",
        title="Drafted",
        content="The pipeline drafted this.",
        citations=[CitationLineage(lineage_id="l1", claim_id="c1", chunk_id="a" * 64, span_start=0, span_end=5)],
        claims=[Claim(claim_id="c1", section_id="sec-1", text="A claim.")],
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )

    merged = merge_operator_text(drafted, content="The operator's own words.")

    assert merged.content == "The operator's own words."
    assert merged.citations == drafted.citations
    assert merged.claims == []
    assert merged.is_verified is False
    assert merged.status == SectionStatus.PENDING
    assert drafted.content == "The pipeline drafted this."
