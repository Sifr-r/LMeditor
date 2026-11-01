"""Fault injection for release evidence: incomplete checks must stay red."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest


def gate() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_release.py"
    spec = importlib.util.spec_from_file_location("verify_release", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("xml", [
    "<testsuites/>",
    '<testsuite tests="0"/>',
    '<testsuite tests="2"><testcase/></testsuite>',
    '<testsuite tests="one"><testcase/></testsuite>',
    '<testsuite tests="1" skipped="1"><testcase/></testsuite>',
    '<testsuite tests="1"><testcase><skipped/></testcase></testsuite>',
    '<testsuite tests="1"><testcase><failure/></testcase></testsuite>',
    '<testsuite tests="1" errors="1"><testcase/></testsuite>',
    '<testsuite tests="1" failures="-1"><testcase/></testsuite>',
    '<testsuites skipped="1"><testsuite tests="1"><testcase/></testsuite></testsuites>',
    "not xml",
])
def test_incomplete_or_failed_evidence_is_rejected(tmp_path: Path, xml: str) -> None:
    module = gate()
    report = tmp_path / "junit.xml"
    report.write_text(xml, encoding="utf-8")
    with pytest.raises(module.ReleaseGateError):
        module.verify_junit([report], "fault injection")


def test_missing_evidence_is_rejected(tmp_path: Path) -> None:
    module = gate()
    for reports in ([], [tmp_path / "absent.xml"]):
        with pytest.raises(module.ReleaseGateError):
            module.verify_junit(reports, "missing")


def test_real_pytest_and_gradle_junit_shapes_are_counted(tmp_path: Path) -> None:
    module = gate()
    pytest_xml = tmp_path / "pytest.xml"
    pytest_xml.write_text('<testsuites><testsuite tests="2" errors="0" failures="0" skipped="0"><testcase/><testcase/></testsuite></testsuites>', encoding="utf-8")
    gradle_xml = tmp_path / "TEST-example.xml"
    gradle_xml.write_text('<testsuite tests="1" errors="0" failures="0" skipped="0"><testcase/><system-out/></testsuite>', encoding="utf-8")
    assert module.verify_junit([pytest_xml, gradle_xml], "passing") == 3


#: A Gradle report for a suite that holds one Windows-only test. The test
#: states a guarantee that only Windows gives, so it skips elsewhere.
WINDOWS_ONLY_REPORT = (
    '<testsuite name="lmeditor.server.SourceArchiveTest" tests="2" skipped="1" failures="0" errors="0">'
    '<testcase name="a test that runs everywhere()" classname="lmeditor.server.SourceArchiveTest"/>'
    '<testcase name="Windows mandatory file lock produces typed write failure without losing old record()" '
    'classname="lmeditor.server.SourceArchiveTest"><skipped/></testcase>'
    "</testsuite>"
)
WINDOWS_ONLY_SKIP = (
    "lmeditor.server.SourceArchiveTest",
    "Windows mandatory file lock produces typed write failure without losing old record()",
)


def test_a_skip_that_nothing_declares_is_rejected(tmp_path: Path) -> None:
    """A skip nobody declared is evidence that quietly did less than it claims."""
    module = gate()
    report = tmp_path / "TEST-example.xml"
    report.write_text(WINDOWS_ONLY_REPORT, encoding="utf-8")
    with pytest.raises(module.ReleaseGateError, match="undeclared skipped test"):
        module.verify_junit([report], "kotlin server")


def test_a_declared_skip_that_happened_is_accepted(tmp_path: Path) -> None:
    """The one legitimate skip on a platform is the one the declaration names."""
    module = gate()
    report = tmp_path / "TEST-example.xml"
    report.write_text(WINDOWS_ONLY_REPORT, encoding="utf-8")
    observed: set[tuple[str, str]] = set()
    assert module.verify_junit([report], "kotlin server", [WINDOWS_ONLY_SKIP], observed) == 2
    assert observed == {WINDOWS_ONLY_SKIP}
    module.verify_expected_skips([WINDOWS_ONLY_SKIP], observed, "kotlin server")


def test_a_declared_skip_that_did_not_happen_is_rejected(tmp_path: Path) -> None:
    """A declaration that is not true of the run it is attached to is refused.

    Otherwise a platform could declare every test it likes and never have
    to prove that any of them was actually skipped.
    """
    module = gate()
    report = tmp_path / "TEST-example.xml"
    report.write_text(
        '<testsuite name="lmeditor.server.SourceArchiveTest" tests="1" skipped="0" failures="0" errors="0">'
        '<testcase name="a test that runs everywhere()" classname="lmeditor.server.SourceArchiveTest"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    observed: set[tuple[str, str]] = set()
    module.verify_junit([report], "kotlin server", [WINDOWS_ONLY_SKIP], observed)
    with pytest.raises(module.ReleaseGateError, match="did not happen"):
        module.verify_expected_skips([WINDOWS_ONLY_SKIP], observed, "kotlin server")


def test_the_platform_declaration_names_exactly_the_windows_guarantee() -> None:
    """The declaration is a checked-in list, and it is read from the repository.

    A platform the declaration does not describe has no expected skips at
    all, which is the strict reading: an undeclared skip fails the gate.
    """
    module = gate()
    declaration = json.loads(module.PLATFORM_CHECKS.read_text(encoding="utf-8"))
    assert set(declaration) >= {"windows", "linux"}
    for platform in ("windows", "linux"):
        assert set(declaration[platform]) >= {"kotlin", "python"}
    assert declaration["windows"]["kotlin"] == []
    assert declaration["linux"]["python"] == []
    linux = [(entry["class"], entry["test"]) for entry in declaration["linux"]["kotlin"]]
    assert linux == [WINDOWS_ONLY_SKIP]


def test_the_platform_declaration_is_read_from_the_repository_not_the_environment() -> None:
    """The declaration cannot be substituted from outside the checkout."""
    module = gate()
    assert module.PLATFORM_CHECKS.is_file()
    assert module.expected_skips("kotlin") == module.expected_skips("kotlin")
    assert isinstance(module.platform_key(), str)


def test_nonzero_command_keeps_failure_and_output(tmp_path: Path) -> None:
    module = gate()
    log = tmp_path / "failure.log"
    with pytest.raises(module.ReleaseGateError, match="exited 7"):
        module.run_check("broken check", [sys.executable, "-c", "print('failure evidence'); raise SystemExit(7)"], tmp_path, dict(os.environ), log, 10)
    assert "failure evidence" in log.read_text(encoding="utf-8")


def test_timeout_cannot_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = gate()
    cleaned: list[object] = []

    class BlockedProcess:
        def wait(self, *, timeout: int) -> int:
            raise subprocess.TimeoutExpired("blocked check", timeout)

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: BlockedProcess())
    monkeypatch.setattr(module, "terminate_check_tree", cleaned.append)
    with pytest.raises(module.ReleaseGateError, match="timed out"):
        module.run_check("blocked check", [sys.executable], tmp_path, {}, tmp_path / "timeout.log", 1)
    assert len(cleaned) == 1


def test_operator_overrides_cannot_substitute_release_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = gate()
    monkeypatch.setenv("LMEDITOR_KOTLIN_BRIDGE", "old.jar")
    monkeypatch.setenv("LMEDITOR_REQUIREMENTS_REGISTRY", "different.json")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k no_tests")
    monkeypatch.setenv("GRADLE_USER_HOME", "existing-gradle-cache")
    monkeypatch.setenv("UV_CACHE_DIR", "existing-uv-cache")
    environment = module.release_environment(tmp_path)
    assert "LMEDITOR_KOTLIN_BRIDGE" not in environment
    assert "LMEDITOR_REQUIREMENTS_REGISTRY" not in environment
    assert "PYTEST_ADDOPTS" not in environment
    assert environment["GRADLE_USER_HOME"] == "existing-gradle-cache"
    assert environment["UV_CACHE_DIR"] == "existing-uv-cache"
    assert Path(environment["TEMP"]).is_relative_to(tmp_path)
    assert Path(environment["LMEDITOR_LIBRARY_DIR"]).is_relative_to(tmp_path)


def test_invalid_timeout_is_rejected_before_execution() -> None:
    module = gate()
    with pytest.raises(module.ReleaseGateError, match="positive"):
        module.verify_release(offline=True, timeout=0)


def test_portable_exclusions_are_exact_and_auditable(tmp_path: Path) -> None:
    module = gate()
    command = module.pytest_command(tmp_path, portable=True)
    assert command[:3] == [sys.executable, "-m", "pytest"]
    expected = [
        "--ignore", "tests/test_word_real_word.py",
        "--ignore", "tests/test_word_app_conformance.py",
        "--ignore", "tests/test_word_chart_conformance.py",
        "--deselect", "tests/test_provenance.py::test_the_semantic_model_is_used_when_it_is_available",
        "--deselect", "tests/test_workflow_e2e.py::test_a_grounded_claim_must_verify_against_the_chunk_it_cites",
        "--deselect", "tests/test_llm_nli_fallback.py::test_cross_encoder_unrelated_pair_is_neutral",
        "--deselect", "tests/test_llm_nli_fallback.py::test_cross_encoder_preserves_entailment_and_contradiction",
        "--deselect", "tests/test_provenance_service.py::test_ranking_returns_the_passages_that_answer_the_query",
    ]
    assert command[command.index("--ignore"):] == expected
    assert "-k" not in command and "--ignore-glob" not in command
    assert command.count("-m") == 1  # Python's module selector, no pytest marker filter.
    scope = json.loads((tmp_path / "external-checks.json").read_text(encoding="utf-8"))
    assert scope["mode"] == "portable"
    assert scope["excluded_modules"] == expected[1:6:2]
    assert scope["excluded_tests"] == expected[7::2]


def test_full_mode_has_no_coverage_exclusions(tmp_path: Path) -> None:
    module = gate()
    command = module.pytest_command(tmp_path, portable=False)
    assert command == [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "pytest-temp"), "--junitxml", str(tmp_path / "pytest.xml")]
    scope = json.loads((tmp_path / "external-checks.json").read_text(encoding="utf-8"))
    assert scope == {"mode": "full", "excluded_modules": [], "excluded_tests": []}


def test_portable_model_execution_is_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = gate()
    monkeypatch.setenv("HF_HOME", "provisioned-hf-cache")
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", "provisioned-embedding-cache")
    environment = module.release_environment(tmp_path, portable=True)
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["HF_HOME"] == "provisioned-hf-cache"
    assert environment["SENTENCE_TRANSFORMERS_HOME"] == "provisioned-embedding-cache"


def test_portable_cli_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = gate()
    calls: list[dict[str, object]] = []

    def verify(**kwargs: object) -> Path:
        calls.append(kwargs)
        return tmp_path

    monkeypatch.setattr(module, "verify_release", verify)
    assert module.main([]) == 0
    assert module.main(["--portable", "--offline", "--timeout", "30"]) == 0
    assert calls == [
        {"offline": False, "timeout": 1800, "portable": False},
        {"offline": True, "timeout": 30, "portable": True},
    ]


def test_timeout_terminates_a_real_descendant(tmp_path: Path) -> None:
    module = gate()
    ready = tmp_path / "ready"
    survived = tmp_path / "survived"
    descendant = (
        "import time; from pathlib import Path; "
        f"Path({str(ready)!r}).write_text('ready'); "
        "time.sleep(5); "
        f"Path({str(survived)!r}).write_text('orphan')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
        "time.sleep(30)"
    )
    with pytest.raises(module.ReleaseGateError, match="timed out") as failure:
        module.run_check("descendant probe", [sys.executable, "-c", parent], tmp_path, dict(os.environ), tmp_path / "tree.log", 2)
    assert "cleanup failed" not in str(failure.value), str(failure.value)
    assert ready.exists(), "descendant did not start, so cleanup was not exercised"
    time.sleep(4)
    assert not survived.exists(), "timed-out check left its descendant running"
