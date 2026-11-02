"""Maintained documentation tools catch drift without rewriting source evidence."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "backend/scripts"
NAMES = (
    "build_replacement_matrix",
    "check_replacement_links",
    "check_inventory_cells",
    "check_compatibility_vocabulary",
    "verify_corpus_digests",
)


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> dict[str, ModuleType]:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return {name: importlib.import_module(name) for name in NAMES}


def test_renderer_is_deterministic_and_check_refuses_drift_without_writing(
    tools: dict[str, ModuleType], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tools["build_replacement_matrix"]
    registry = json.loads((REPO / "docs/replacement/requirements.json").read_text(encoding="utf-8"))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    matrix = tmp_path / "matrix.md"
    monkeypatch.setattr(tool, "REGISTRY", registry_path)
    monkeypatch.setattr(tool, "MATRIX", matrix)
    monkeypatch.setattr(tool, "REPO", tmp_path)
    assert tool.main([]) == 0
    first = matrix.read_bytes()
    assert b"`R.08`" in first and b"| `complete` | 1 |" in first
    assert tool.main([]) == 0
    assert matrix.read_bytes() == first
    assert tool.main(["--check"]) == 0
    registry["requirements"][0]["gate"] = "A newly amended acceptance gate."
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    assert tool.main(["--check"]) == 1
    assert matrix.read_bytes() == first
    assert tool.main([]) == 0
    assert "A newly amended acceptance gate." in matrix.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["unknown-track", "duplicate-id", "unknown-status"])
def test_renderer_refuses_registry_rows_it_cannot_render(
    tools: dict[str, ModuleType], mutation: str
) -> None:
    tool = tools["build_replacement_matrix"]
    registry = tool.load()
    if mutation == "unknown-track":
        registry["requirements"][0]["track"] = "lost"
    elif mutation == "duplicate-id":
        registry["requirements"].append(registry["requirements"][0])
    else:
        registry["requirements"][0]["status"] = "done"
    with pytest.raises(ValueError):
        tool.render(registry)


def test_links_check_files_fragments_and_encoded_paths(
    tools: dict[str, ModuleType], tmp_path: Path
) -> None:
    tool = tools["check_replacement_links"]
    target = tmp_path / "a document.md"
    target.write_text("# A heading\n# A heading\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text(
        "[ok](a%20document.md#a-heading-1)\n[web](https://example.com/missing)\n",
        encoding="utf-8",
    )
    assert tool.check(source) == []
    source.write_text("[bad](a%20document.md#missing)\n[absent](missing.md)\n", encoding="utf-8")
    problems = tool.check(source)
    assert len(problems) == 2
    assert "missing fragment" in problems[0]
    assert "missing target" in problems[1]


def test_evidence_check_refuses_missing_out_of_range_and_escaping_references(
    tools: dict[str, ModuleType], tmp_path: Path
) -> None:
    directory = tmp_path / "docs/replacement"
    directory.mkdir(parents=True)
    code = tmp_path / "code.py"
    code.write_text("one line\n", encoding="utf-8")
    manifest = directory / "requirements.json"
    manifest.write_text(json.dumps({"requirements": [{"id": "F.01", "evidence": [
        "code.py:1", "code.py:2", "absent.py:1", "../escape.py:1"
    ]}]}), encoding="utf-8")
    problems = tools["check_replacement_links"].check_evidence(tmp_path)
    assert len(problems) == 3
    assert any("outside file" in problem for problem in problems)
    assert any("file missing" in problem for problem in problems)
    assert any("malformed" in problem for problem in problems)


def inventory_text(row: str) -> str:
    return "## 2. DOCX feature matrix\n| Feature | Read | Display | Edit | Preserve | Export | Known limitation |\n| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n" + row + "\n### Coverage\n| Track | Count |\n| Word | 1 |\n"


def test_inventory_accepts_escaped_pipes_and_excludes_coverage(
    tools: dict[str, ModuleType], tmp_path: Path
) -> None:
    path = tmp_path / "inventory.md"
    path.write_text(inventory_text(r"| Example | read | display | edit | preserve | export | pattern a\|b |"), encoding="utf-8")
    assert tools["check_inventory_cells"].check(path) == []


@pytest.mark.parametrize("row", [
    "| Example | read | display | edit | preserve | export | |",
    "| Example | read | display | edit | preserve | export |",
    "",
])
def test_inventory_refuses_blank_malformed_or_missing_rows(
    tools: dict[str, ModuleType], tmp_path: Path, row: str
) -> None:
    path = tmp_path / "inventory.md"
    path.write_text(inventory_text(row), encoding="utf-8")
    assert tools["check_inventory_cells"].check(path)


def test_vocabulary_checks_both_inventory_and_manifest(
    tools: dict[str, ModuleType], tmp_path: Path
) -> None:
    tool = tools["check_compatibility_vocabulary"]
    inventory = tmp_path / "inventory.md"
    inventory.write_text(inventory_text("| Example | done | display | edit | preserve | export | limitation |"), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    expected = {column: column for column in tool.STATUS_CELLS}
    expected["edit"] = "done"
    manifest.write_text(json.dumps({"fixtures": [{"id": "example", "expected": expected}]}), encoding="utf-8")
    problems = tool.check(inventory, manifest)
    assert len(problems) == 2
    assert "inventory" in problems[0] and "manifest" in problems[1]


def test_corpus_digests_detect_changed_files_trees_and_path_escape(
    tools: dict[str, ModuleType], tmp_path: Path
) -> None:
    tool = tools["verify_corpus_digests"]
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    file = corpus / "file.txt"
    file.write_bytes(b"original")
    tree = corpus / "tree"
    tree.mkdir()
    (tree / "note.md").write_bytes(b"note")
    fixtures = [
        {"id": "file", "path": "file.txt", "sha256": tool.sha(b"original")},
        {"id": "tree", "path": "tree", "sha256": tool.tree_digest(tree)},
    ]
    manifest = corpus / "MANIFEST.json"
    manifest.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
    assert tool.check(corpus) == []
    file.write_bytes(b"changed")
    (tree / "new.md").write_bytes(b"added")
    assert len(tool.check(corpus)) == 2
    fixtures.append({"id": "escape", "path": "../outside.txt", "sha256": "unused"})
    fixtures.append({"id": "missing", "path": "missing.txt", "sha256": "unused"})
    manifest.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
    problems = tool.check(corpus)
    assert any("escapes corpus" in problem for problem in problems)
    assert any("path missing" in problem for problem in problems)


@pytest.mark.parametrize("name", NAMES)
def test_tools_are_import_safe_and_work_from_another_directory(name: str, tmp_path: Path) -> None:
    code = "import runpy,sys; sys.path.insert(0,sys.argv[1]); runpy.run_path(sys.argv[2],run_name='import_probe')"
    imported = subprocess.run(
        [sys.executable, "-c", code, str(SCRIPTS), str(SCRIPTS / f"{name}.py")],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == "" and imported.stderr == ""
    arguments = ["--check"] if name == "build_replacement_matrix" else []
    checked = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), *arguments],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert list(tmp_path.iterdir()) == []
