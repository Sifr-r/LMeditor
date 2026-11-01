"""The shipped package must say what it contains, and the harness must be honest.

Three things are checked here, and all three are claims that a reader of the
repository cannot verify by reading a docstring:

* the frozen witnesses under ``lmeditor._reference`` are a development asset
  and must not travel inside the distribution;
* the Python bridge's fallback classpath must name exactly the Gradle modules
  of the Kotlin tier, so a module cannot be missing from the classpath while
  the build still succeeds;
* the parity harness must declare a witness for every frozen module, and must
  fail -- not report success -- when a check it claims to cover is skipped.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
REFERENCE_ROOT = BACKEND_ROOT / "src" / "lmeditor" / "_reference"
SETTINGS_GRADLE = REPO_ROOT / "kotlin" / "settings.gradle.kts"
HARNESS = BACKEND_ROOT / "scripts" / "verify_kotlin_parity.py"


def _load_harness() -> ModuleType:
    """Import the parity harness as a module.

    The module is registered in ``sys.modules`` before it is executed: the
    dataclasses it declares resolve their annotations through the module they
    live in, and a module that is not registered has no dict to resolve them
    against.
    """
    spec = importlib.util.spec_from_file_location("verify_kotlin_parity", HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gradle_modules() -> list[str]:
    """Return the Gradle modules of the Kotlin tier, in the order they are declared."""
    modules: list[str] = []
    for line in SETTINGS_GRADLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("include(\":"):
            continue
        name = stripped.removeprefix("include(\":").split("\"")[0]
        modules.append(name)
    return modules


def _excludes(target: str) -> list[str]:
    """Return the exclude patterns a build target declares."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]
    assert target in targets, f"{target} is not declared in pyproject.toml"
    return list(targets[target].get("exclude", []))


def _pattern_names_the_witnesses(pattern: str) -> bool:
    """Whether an exclude pattern names the frozen-witness directory.

    Hatchling matches an ``exclude`` pattern with gitignore semantics, so a
    pattern without a slash names the directory at any depth and a pattern
    with a slash is anchored to the root it is matched against. Both spellings
    are accepted here, which is why the pyproject declares both.
    """
    trimmed = pattern.rstrip("/")
    return trimmed in {"_reference", "src/lmeditor/_reference"}


# ---------------------------------------------------------------------------
# The distribution must not carry the frozen witnesses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["wheel", "sdist"])
def test_the_distribution_excludes_the_frozen_witnesses(target: str) -> None:
    """The oracle the harness measures against must not ship to users."""
    excludes = _excludes(target)
    assert excludes, f"the {target} target declares no exclude at all"
    assert any(_pattern_names_the_witnesses(pattern) for pattern in excludes), (
        f"the {target} target does not exclude src/lmeditor/_reference: {excludes}"
    )


def test_every_frozen_witness_is_covered_by_an_exclude_pattern() -> None:
    """Every file of the frozen tree must be named by an exclude pattern."""
    excludes = _excludes("wheel")
    assert excludes
    uncovered = [
        path.name
        for path in sorted(REFERENCE_ROOT.glob("*.py"))
        if not any(_pattern_names_the_witnesses(pattern) for pattern in excludes)
    ]
    assert uncovered == []


def test_no_shipped_module_imports_a_frozen_witness() -> None:
    """Nothing outside the frozen tree may import a witness."""
    package_root = BACKEND_ROOT / "src" / "lmeditor"
    offenders: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if path.is_relative_to(REFERENCE_ROOT):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "lmeditor._reference"
            ):
                offenders.append(path.relative_to(package_root).as_posix())
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("lmeditor._reference"):
                        offenders.append(path.relative_to(package_root).as_posix())
    assert offenders == [], f"the shipped package reaches the frozen witnesses: {offenders}"


# ---------------------------------------------------------------------------
# The fallback classpath must name the whole tier
# ---------------------------------------------------------------------------


def test_the_fallback_classpath_names_every_tier_module() -> None:
    """The Python list must match ``kotlin/settings.gradle.kts`` exactly."""
    from lmeditor.bridge.client import TIER_MODULES

    declared = _gradle_modules()
    assert ":desktop" not in [module for module in declared if module.startswith(":")]
    expected = [module for module in declared if module != "desktop"]
    assert list(TIER_MODULES) == expected


def test_the_fallback_classpath_carries_the_modules_the_bridge_needs() -> None:
    """``:vault`` and ``:notebooks`` are tier modules the seam depends on."""
    from lmeditor.bridge.client import TIER_MODULES

    build_file = (REPO_ROOT / "kotlin" / "bridge" / "build.gradle.kts").read_text(
        encoding="utf-8"
    )
    required = set()
    for line in build_file.splitlines():
        marker = 'project(":'
        if marker in line:
            required.add(line.split(marker)[1].split('")')[0])
    missing = sorted(required - set(TIER_MODULES))
    assert missing == [], f"the fallback classpath is missing: {missing}"


# ---------------------------------------------------------------------------
# The harness must be honest about what it covers
# ---------------------------------------------------------------------------


def test_the_witness_ledger_covers_every_frozen_module() -> None:
    """Every frozen module must have a declared witness."""
    harness = _load_harness()

    result = harness._check_ledger()
    assert result.status == "passed", result.detail

    on_disk = {path.stem for path in REFERENCE_ROOT.glob("*.py")} - {"__init__"}
    assert on_disk == set(harness.REFERENCE_WITNESSES)


def test_the_ledger_names_the_kotlin_counterpart_of_every_gap() -> None:
    """A module no check compares must name the Kotlin file that holds its rule."""
    harness = _load_harness()

    for module, witness in harness.REFERENCE_WITNESSES.items():
        if witness.checks:
            assert not witness.counterpart, module
            continue
        assert witness.counterpart, f"{module} declares neither a check nor a counterpart"
        assert (REPO_ROOT / "kotlin" / witness.counterpart).is_file(), module


def test_a_skipped_check_makes_the_run_fail() -> None:
    """An absent comparison is not evidence of agreement."""
    harness = _load_harness()

    assert harness._exit_code([harness.CheckResult("a", "passed")]) == 0
    assert harness._exit_code([harness.CheckResult("a", "skipped")]) == 1
    assert harness._exit_code([harness.CheckResult("a", "failed")]) == 1


def test_the_harness_covers_the_rules_the_doors_claim_it_covers() -> None:
    """The two rules the doors name must be among the checks."""
    harness = _load_harness()

    names = {check.name for check in harness.CHECKS}
    assert "the ingestion limits" in names
    assert "the bounded brief" in names
