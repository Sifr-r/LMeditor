"""Rebuild and verify a release candidate without accepting skipped evidence.

Run with the project's development Python environment from any directory.
Logs and JUnit reports are retained under ``build/release/run-*``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping, Sequence


REPO = Path(__file__).resolve().parents[2]
PORTABLE_EXCLUDED_MODULES = (
    "tests/test_word_real_word.py",
    "tests/test_word_app_conformance.py",
    "tests/test_word_chart_conformance.py",
)
PORTABLE_EXCLUDED_TESTS = (
    "tests/test_provenance.py::test_the_semantic_model_is_used_when_it_is_available",
    "tests/test_workflow_e2e.py::test_a_grounded_claim_must_verify_against_the_chunk_it_cites",
    "tests/test_llm_nli_fallback.py::test_cross_encoder_unrelated_pair_is_neutral",
    "tests/test_llm_nli_fallback.py::test_cross_encoder_preserves_entailment_and_contradiction",
    "tests/test_provenance_service.py::test_ranking_returns_the_passages_that_answer_the_query",
)


class ReleaseGateError(RuntimeError):
    """A required check failed or did not provide complete evidence."""


def verify_junit(paths: Sequence[Path], label: str) -> int:
    """Require nonempty, internally consistent JUnit evidence with no skips."""
    if not paths:
        raise ReleaseGateError(f"{label}: no JUnit reports were produced")
    total = 0
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise ReleaseGateError(f"{label}: cannot read JUnit report {path}: {exc}") from exc
        summaries = [node for node in root.iter() if node.tag in ("testsuite", "testsuites")]
        for summary in summaries:
            try:
                incomplete = any(int(summary.get(name, "0")) != 0 for name in ("failures", "errors", "skipped"))
            except ValueError as exc:
                raise ReleaseGateError(f"{label}: {path} has invalid summary counts") from exc
            if incomplete:
                raise ReleaseGateError(f"{label}: {path} reports failed, errored or skipped tests")
        suites = [node for node in root.iter("testsuite") if not node.findall("testsuite")]
        if not suites:
            raise ReleaseGateError(f"{label}: {path} contains no test suites")
        for suite in suites:
            cases = suite.findall("testcase")
            try:
                counts = {
                    name: int(suite.get(name, "0"))
                    for name in ("tests", "failures", "errors", "skipped")
                }
            except ValueError as exc:
                raise ReleaseGateError(f"{label}: {path} has invalid test counts") from exc
            if counts["tests"] <= 0 or counts["tests"] != len(cases):
                raise ReleaseGateError(f"{label}: {path} has zero or inconsistent test counts")
            if any(value < 0 for value in counts.values()):
                raise ReleaseGateError(f"{label}: {path} has negative test counts")
            if any(counts[name] for name in ("failures", "errors", "skipped")) or any(
                case.find(name) is not None
                for case in cases
                for name in ("failure", "error", "skipped")
            ):
                raise ReleaseGateError(f"{label}: {path} reports failed, errored or skipped tests")
            total += len(cases)
    return total


def run_check(
    label: str,
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    log: Path,
    timeout: int,
) -> None:
    """Run one gate, keeping its complete output and preserving failure context."""
    print(f"Running {label}; log: {log}", flush=True)
    try:
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    terminate_check_tree(process)
                except (OSError, subprocess.SubprocessError, ReleaseGateError) as cleanup_error:
                    raise ReleaseGateError(f"{label}: timed out; process-tree cleanup failed: {cleanup_error}; see {log}") from exc
                raise
    except subprocess.TimeoutExpired as exc:
        raise ReleaseGateError(f"{label}: timed out after {timeout}s; see {log}") from exc
    except OSError as exc:
        raise ReleaseGateError(f"{label}: could not execute check: {exc}; see {log}") from exc
    if returncode != 0:
        raise ReleaseGateError(f"{label}: exited {returncode}; see {log}")


def terminate_check_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate this check and its descendants, then reap the direct child."""
    if sys.platform == "win32":
        # wait(timeout) leaves the parent alive, so taskkill can find its tree.
        result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=30, check=False)
        if result.returncode != 0:
            raise ReleaseGateError(f"taskkill failed for check PID {process.pid}: {result.stderr.decode(errors='replace').strip()}")
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The dedicated process group has already exited.
    process.wait(timeout=30)


def release_environment(run_dir: Path, *, portable: bool = False) -> dict[str, str]:
    """Keep verification data in this run, independent of operator configuration."""
    environment = dict(os.environ)
    # An override can silently substitute an older bridge or a different corpus.
    for name in tuple(environment):
        if name.startswith("LMEDITOR_") or name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            environment.pop(name)
    temp = run_dir / "tmp"
    temp.mkdir()
    environment.update({name: str(temp) for name in ("TMP", "TEMP", "TMPDIR")})
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(REPO / "backend" / "src")
    environment.setdefault("UV_CACHE_DIR", str(REPO / "build" / "uv-cache"))
    environment.setdefault("GRADLE_USER_HOME", str(REPO / "build" / "gradle-home"))
    environment.setdefault("HF_HOME", str(REPO / "build" / "huggingface"))
    environment.setdefault("SENTENCE_TRANSFORMERS_HOME", str(REPO / "build" / "sentence-transformers"))
    if portable:
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["DSPY_CACHEDIR"] = str(run_dir / "dspy-cache")
    for name, directory in (
        ("LMEDITOR_SOURCES_DIR", "sources"),
        ("LMEDITOR_LIBRARY_DIR", "library"),
        ("LMEDITOR_UPLOAD_DIR", "uploads"),
        ("LMEDITOR_VAULT_BASE", "vault-base"),
        ("LMEDITOR_NOTEBOOKS_BASE", "notebooks-base"),
    ):
        target = run_dir / directory
        target.mkdir()
        environment[name] = str(target)
    return environment


def pytest_command(run_dir: Path, *, portable: bool) -> list[str]:
    """Record exact coverage exclusions and construct the backend check command."""
    modules = list(PORTABLE_EXCLUDED_MODULES) if portable else []
    tests = list(PORTABLE_EXCLUDED_TESTS) if portable else []
    scope = {"mode": "portable" if portable else "full", "excluded_modules": modules, "excluded_tests": tests}
    (run_dir / "external-checks.json").write_text(json.dumps(scope, indent=2) + "\n", encoding="utf-8")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", str(run_dir / "pytest-temp"), "--junitxml", str(run_dir / "pytest.xml")]
    for module in modules:
        command.extend(["--ignore", module])
    for test in tests:
        command.extend(["--deselect", test])
    return command


def verify_release(*, offline: bool, timeout: int, portable: bool = False) -> Path:
    """Run all required gates, returning the evidence directory only on success."""
    if timeout <= 0:
        raise ReleaseGateError("the per-check timeout must be a positive number of seconds")
    backend = REPO / "backend"
    kotlin = REPO / "kotlin"
    wrapper = kotlin / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not wrapper.is_file() or shutil.which("java") is None:
        raise ReleaseGateError("Kotlin verification requires the Gradle wrapper and java on PATH")
    uv = shutil.which("uv")
    if uv is None:
        raise ReleaseGateError("Python lock verification requires uv on PATH")
    modules = re.findall(r'^\s*include\(":([A-Za-z0-9_-]+)"\)', (kotlin / "settings.gradle.kts").read_text(encoding="utf-8"), re.MULTILINE)
    if not modules:
        raise ReleaseGateError("Kotlin settings declare no modules to verify")
    report_root = REPO / "build" / "release"
    report_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=report_root))
    environment = release_environment(run_dir, portable=portable)
    backend_command = pytest_command(run_dir, portable=portable)
    print(f"Release evidence: {run_dir}", flush=True)
    if portable:
        print("Portable scope excludes Microsoft Word and five real-model checks; see external-checks.json. Passing does not establish full release readiness.", flush=True)
    lock_check = [uv, "lock", "--check"]
    if offline:
        lock_check.append("--offline")
    run_check("Python dependency lock", lock_check, backend, environment, run_dir / "uv-lock.log", timeout)
    gradle = ([str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]) + ["cleanTest", "test", ":bridge:tierJar", "--no-build-cache", "--console=plain"]
    if offline:
        gradle.append("--offline")
    run_check("Kotlin build and tests", gradle, kotlin, environment, run_dir / "kotlin.log", timeout)
    kotlin_reports = run_dir / "kotlin-junit"
    for module in modules:
        reports = sorted((kotlin / module / "build" / "test-results" / "test").glob("TEST-*.xml"))
        count = verify_junit(reports, f"Kotlin {module}")
        destination = kotlin_reports / module
        destination.mkdir(parents=True)
        for report in reports:
            shutil.copy2(report, destination / report.name)
        print(f"Kotlin {module}: {count} tests passed", flush=True)
    tier_jars = list((kotlin / "bridge" / "build" / "libs").glob("*-tier.jar"))
    if len(tier_jars) != 1:
        raise ReleaseGateError("Kotlin build must produce exactly one self-contained tier jar")
    environment["LMEDITOR_KOTLIN_BRIDGE"] = str(tier_jars[0])
    python = sys.executable
    run_check("strict Python typing", [python, "-m", "mypy", "--strict", "src", "scripts/verify_release.py", "--cache-dir", str(run_dir / "mypy-cache")], backend, environment, run_dir / "mypy.log", timeout)
    pytest_report = run_dir / "pytest.xml"
    run_check("portable backend suite" if portable else "complete backend suite", backend_command, backend, environment, run_dir / "pytest.log", timeout)
    print(f"Backend: {verify_junit([pytest_report], 'backend')} tests passed", flush=True)
    for label, script in (
        ("Kotlin parity", "verify_kotlin_parity.py"),
        ("HTTP document round trip", "verify_documents_roundtrip.py"),
    ):
        run_check(label, [python, str(backend / "scripts" / script)], backend, environment, run_dir / f"{Path(script).stem}.log", timeout)
    if portable:
        print(f"Portable checks passed; full release readiness remains unproven. Evidence: {run_dir}", flush=True)
    else:
        print(f"All release checks passed. Evidence: {run_dir}", flush=True)
    return run_dir


def positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive integer") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive integer")
    return seconds


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="require cached Gradle dependencies")
    parser.add_argument("--portable", action="store_true", help="explicitly exclude Microsoft Word and five real-model checks; does not prove full release readiness")
    parser.add_argument("--timeout", type=positive_seconds, default=1800, metavar="SECONDS", help="maximum seconds per check (default: 1800)")
    args = parser.parse_args(argv)
    try:
        verify_release(offline=args.offline, timeout=args.timeout, portable=args.portable)
    except (ReleaseGateError, OSError) as exc:
        print(f"Release gate failed: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
