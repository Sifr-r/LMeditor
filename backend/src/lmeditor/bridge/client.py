"""The Python side of the NDJSON seam to the Kotlin tier.

The Kotlin tier owns the deterministic domain rules that cross the seam; this
module is the narrow door through which the Python tier asks it to apply one.
The rules the seam does not carry are named in ARCHITECTURE.md §18.2.1 rather
than implied: the drift-pinned Python twins, the two rules that have no Kotlin
counterpart at all, and the model-facing half, which is Python by design.

The wire format is one JSON document per line, in both directions, over the
JVM process's standard input and standard output. The full contract is in
``kotlin/bridge/README.md``; this module implements the client half of it.

The client is deliberately boring: it spawns one long-lived JVM process,
writes a request, reads a response, and hands back the result. It holds a
lock for the whole exchange, because a pipe is a single stream and two
threads writing to it would interleave their documents.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from lmeditor.bridge.errors import (
    BridgeProtocolError,
    BridgeUnavailableError,
    KotlinDomainError,
)
from lmeditor.core.exceptions import (
    BookNotFoundError,
    ChunkNotFoundError,
    CitationHallucinationError,
    DraftingError,
    EditTargetNotFoundError,
    ExportError,
    FactCheckError,
    InlineEditRejectedError,
    IncompleteOperationError,
    LMeditorError,
    LMNotConfiguredError,
    OutlineGenerationError,
    PerspectiveGenerationError,
    RevisionConflictError,
    SectionNotFoundError,
    SourceLoadError,
    SourceUploadTooLargeError,
    SpanAlignmentError,
    StorageWriteError,
    UngroundedClaimError,
    ValidationError,
    WebEvidenceError,
    WorkflowDeadlockError,
    WordParseError,
    WordRenderError,
)

#: The Gradle modules of the Kotlin tier, in dependency order. The order
#: matters only for a human reading a classpath; the JVM resolves it anyway.
#:
#: This is the module list of ``kotlin/settings.gradle.kts`` minus ``:desktop``,
#: which is not a tier module (it sits above the tier and depends downward on
#: ``:core`` alone). ``:vault`` and ``:notebooks`` are tier modules: the seam
#: serves their rules, and ``kotlin/bridge/build.gradle.kts`` depends on both.
#: ``backend/tests/test_bridge_tier_modules.py`` holds this list to that file.
TIER_MODULES: Final[tuple[str, ...]] = (
    "core",
    "text",
    "word",
    "pdf",
    "ingest",
    "library",
    "workflow",
    "editing",
    "factcheck",
    "discovery",
    "server",
    "vault",
    "notebooks",
    "bridge",
)

#: The main class the JVM process runs.
BRIDGE_MAIN_CLASS: Final[str] = "lmeditor.bridge.BridgeMain"

#: Environment variable that overrides the bridge classpath outright.
BRIDGE_CLASSPATH_ENV: Final[str] = "LMEDITOR_KOTLIN_BRIDGE"

#: Environment variable that overrides the java executable.
BRIDGE_JAVA_ENV: Final[str] = "LMEDITOR_JAVA"


def _rehydrate(error_type: str, message: str, details: Mapping[str, Any]) -> BaseException:
    """Rebuild the Python exception a Kotlin error corresponds to.

    Most LMeditor errors are constructed from a message and a details map, so
    the generic path works for them. The ones with a bespoke constructor are
    listed explicitly, because their Python signature is part of the contract
    their callers already rely on.
    """
    if error_type == "StorageWriteError":
        return StorageWriteError(str(details.get("path", "")), str(details.get("reason", "io_error")))
    if error_type == "RevisionConflictError":
        return RevisionConflictError(
            str(details.get("artifact_id", "")),
            int(details.get("expected_revision", 0)),
            int(details.get("actual_revision", 0)),
            str(details.get("outcome", "")),
        )
    if error_type == "IncompleteOperationError":
        paths = details.get("affected_paths", [])
        return IncompleteOperationError(
            str(details.get("operation_id", "")),
            str(details.get("operation_kind", "")),
            [str(path) for path in paths] if isinstance(paths, (list, tuple)) else [],
        )
    if error_type == "WordParseError":
        return WordParseError(str(details.get("origin", "")), str(details.get("reason", "")))
    if error_type == "WordRenderError":
        return WordRenderError(str(details.get("reason", "")))
    if error_type == "ValidationError":
        field = details.get("field")
        value = details.get("value")
        return ValidationError(message, str(field) if field is not None else None, value)
    if error_type == "ChunkNotFoundError":
        return ChunkNotFoundError(str(details.get("chunk_id", "")))
    if error_type == "SpanAlignmentError":
        doc_length = details.get("doc_length")
        return SpanAlignmentError(
            int(details.get("span_start", 0)),
            int(details.get("span_end", 0)),
            int(doc_length) if doc_length is not None else None,
        )
    if error_type == "BookNotFoundError":
        return BookNotFoundError(str(details.get("book_id", "")))
    if error_type == "SectionNotFoundError":
        return SectionNotFoundError(
            str(details.get("book_id", "")), str(details.get("section_id", ""))
        )
    if error_type == "SourceLoadError":
        return SourceLoadError(
            str(details.get("kind", "")),
            str(details.get("origin", "")),
            str(details.get("reason", "")),
        )
    if error_type == "SourceUploadTooLargeError":
        return SourceUploadTooLargeError(message, limit_bytes=int(details.get("limit_bytes", 0)))
    if error_type == "InlineEditRejectedError":
        return InlineEditRejectedError(
            str(details.get("section_id", "")), str(details.get("reason", ""))
        )
    if error_type == "EditTargetNotFoundError":
        paragraph_index = details.get("paragraph_index")
        return EditTargetNotFoundError(
            str(details.get("section_id", "")),
            int(paragraph_index) if paragraph_index is not None else None,
            message=message,
        )
    if error_type == "FactCheckError":
        return FactCheckError(message, details=dict(details))
    if error_type == "LMNotConfiguredError":
        return LMNotConfiguredError(str(details.get("operation", "")))
    if error_type == "DraftingError":
        return DraftingError(str(details.get("section_id", "")), str(details.get("reason", "")))
    if error_type == "ExportError":
        return ExportError(str(details.get("doc_id", "")), str(details.get("reason", "")))
    if error_type == "WebEvidenceError":
        return WebEvidenceError(str(details.get("reason", "")), str(details.get("query", "")))
    if error_type == "WorkflowDeadlockError":
        return WorkflowDeadlockError(
            current_node=str(details.get("current_node", "")),
            state_summary=str(details.get("state_summary", "")),
            doc_id=str(details.get("doc_id", "")),
            retry_count=int(details.get("retry_count", 0)),
            max_retries=int(details.get("max_retries", 0)),
        )
    if error_type == "CitationHallucinationError":
        return CitationHallucinationError(
            chunk_id=str(details.get("chunk_id", "")),
            quote=str(details.get("quote", "")),
            reason=str(details.get("reason", "")),
            section_id=str(details.get("section_id", "")),
        )
    if error_type == "UngroundedClaimError":
        return UngroundedClaimError(
            claim_id=str(details.get("claim_id", "")),
            claim_text=str(details.get("claim_text", "")),
            score=float(details.get("score", 0.0)),
        )
    if error_type == "UnsafeSourceError":
        # The SSRF guard's own error. A caller that catches it must still
        # catch it, so it is rebuilt as the Python class rather than as the
        # generic domain error.
        from lmeditor.ingestion.url_policy import UnsafeSourceError

        return UnsafeSourceError(message)
    if error_type == "OutlineGenerationError":
        # The outline rules are the tier's, so the refusal is the tier's. It is
        # rebuilt as the Python class rather than as the generic domain error,
        # because a caller that catches it must still catch it.
        return OutlineGenerationError(message, details=dict(details))
    if error_type == "PerspectiveGenerationError":
        return PerspectiveGenerationError(message, details=dict(details))
    return KotlinDomainError(error_type, message, details)


class KotlinBridge:
    """A client for the Kotlin tier's NDJSON seam.

    The JVM process is started on the first call and lives until [close] is
    called or the process is garbage collected. Every call is serialised by a
    lock, because the pipe is one stream.

    Example:
        >>> with KotlinBridge() as bridge:               # doctest: +SKIP
        ...     chunks = bridge.call("text.chunk", {"text": "Hello", "document_id": "d"})
    """

    def __init__(
        self,
        classpath: str | None = None,
        java_executable: str | None = None,
        startup_timeout: float = 30.0,
        command: list[str] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._classpath = classpath
        self._java_executable = java_executable
        self._startup_timeout = startup_timeout
        self._command = list(command) if command else None
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0

    # --- lifecycle --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the JVM process is up."""
        return self._process is not None and self._process.poll() is None

    def close(self) -> None:
        """Stop the JVM process, if it is running."""
        with self._lock:
            self._shutdown()

    def __enter__(self) -> "KotlinBridge":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- the call ---------------------------------------------------------

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a Kotlin method and return its result.

        Args:
            method: The fully qualified method name, e.g. ``text.chunk``.
            params: The method's parameters, as a JSON object.

        Returns:
            The ``result`` object of the response.

        Raises:
            BridgeUnavailableError: The JVM process could not be started or died.
            BridgeProtocolError: The response was not a valid response document.
            LMeditorError: The Kotlin tier refused the operation; the exception
                is the Python counterpart of the Kotlin one.
        """
        with self._lock:
            process = self._ensure_process()
            request = {
                "id": str(self._next_id),
                "method": method,
                "params": dict(params or {}),
            }
            self._next_id += 1
            self._write(process, request)
            response = self._read(process)
            return self._unwrap(response)

    # --- wire helpers -----------------------------------------------------

    def _write(self, process: subprocess.Popen[str], request: Mapping[str, Any]) -> None:
        line = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        stdin = process.stdin
        if stdin is None:
            self._shutdown()
            raise BridgeUnavailableError("the bridge process has no standard input")
        try:
            stdin.write(line + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._shutdown()
            raise BridgeUnavailableError("the bridge process closed its input", str(exc)) from exc

    def _read(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        stdout = process.stdout
        if stdout is None:
            self._shutdown()
            raise BridgeUnavailableError("the bridge process has no standard output")
        raw = stdout.readline()
        if not raw:
            stderr = self._drain_stderr(process)
            self._shutdown()
            raise BridgeUnavailableError(
                "the bridge process exited before answering", stderr
            )
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError("the response was not JSON", raw.strip()) from exc
        if not isinstance(decoded, dict):
            raise BridgeProtocolError("the response was not a JSON object", raw.strip())
        return decoded

    def _unwrap(self, response: Mapping[str, Any]) -> dict[str, Any]:
        ok = response.get("ok")
        if ok is True:
            result = response.get("result")
            if result is None:
                return {}
            if not isinstance(result, dict):
                raise BridgeProtocolError("the result was not a JSON object", str(result))
            return result
        if ok is False:
            error = response.get("error")
            if not isinstance(error, dict):
                raise BridgeProtocolError("the error was not a JSON object", str(error))
            error_type = str(error.get("type", "KotlinDomainError"))
            message = str(error.get("message", ""))
            details = error.get("details")
            detail_map: Mapping[str, Any] = details if isinstance(details, dict) else {}
            raise _rehydrate(error_type, message, detail_map)
        raise BridgeProtocolError("the response had no 'ok' field", str(sorted(response)))

    # --- process management ----------------------------------------------

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._shutdown()
        if self._command:
            command = list(self._command)
        else:
            classpath = self._resolve_classpath()
            java = self._java_executable or os.environ.get(BRIDGE_JAVA_ENV) or "java"
            command = [java, "-cp", classpath, BRIDGE_MAIN_CLASS]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            self._process = None
            raise BridgeUnavailableError(
                f"could not start '{java}'", str(exc)
            ) from exc
        return self._process

    def _shutdown(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _drain_stderr(self, process: subprocess.Popen[str]) -> str:
        stderr = process.stderr
        if stderr is None:
            return ""
        try:
            stderr.flush()
        except (OSError, ValueError):
            pass
        try:
            return stderr.read() or ""
        except (OSError, ValueError):
            return ""

    # --- classpath discovery ---------------------------------------------

    def _resolve_classpath(self) -> str:
        if self._classpath:
            return self._classpath
        from_env = os.environ.get(BRIDGE_CLASSPATH_ENV)
        if from_env:
            return from_env
        tier_jar = _tier_jar()
        if tier_jar is not None:
            return str(tier_jar)
        jars = _discover_tier_jars()
        if not jars:
            raise BridgeUnavailableError(
                "no Kotlin tier jars were found",
                "tried " + ", ".join(str(path) for path in _tier_lib_dirs()),
            )
        return os.pathsep.join(str(jar) for jar in jars)


def _repo_root() -> Path:
    """The repository root, derived from this file's location."""
    # .../backend/src/lmeditor/bridge/client.py -> parents[0]=bridge, [1]=lmeditor,
    # [2]=src, [3]=backend, [4]=repository root.
    return Path(__file__).resolve().parents[4]


def _tier_lib_dirs() -> list[Path]:
    """Every directory that may hold the Kotlin tier's jars."""
    root = _repo_root() / "kotlin"
    return [root / module / "build" / "libs" for module in TIER_MODULES]


def _tier_jar() -> Path | None:
    """The self-contained tier jar, if it has been built.

    The Python tier starts one process with one classpath entry, so the jar
    carries the tier's modules and the Kotlin runtime with it.
    """
    bridge_libs = _repo_root() / "kotlin" / "bridge" / "build" / "libs"
    if not bridge_libs.is_dir():
        return None
    tier_jars = sorted(bridge_libs.glob("*-tier.jar"))
    return tier_jars[-1] if tier_jars else None


def _discover_tier_jars() -> list[Path]:
    """Find the Kotlin tier's jars, in module order.

    A module that has not been built yet contributes nothing; the error the
    caller sees names every directory that was tried.
    """
    jars: list[Path] = []
    for lib_dir in _tier_lib_dirs():
        if not lib_dir.is_dir():
            continue
        module_jars = sorted(
            jar
            for jar in lib_dir.glob("*.jar")
            if not jar.name.endswith("-sources.jar") and not jar.name.endswith("-javadoc.jar")
        )
        jars.extend(module_jars)
    return jars


def encode_bytes(payload: bytes) -> str:
    """Encode a binary payload for the wire."""
    return base64.b64encode(payload).decode("ascii")


def decode_bytes(payload: str) -> bytes:
    """Decode a binary payload from the wire."""
    return base64.b64decode(payload.encode("ascii"), validate=True)


__all__ = [
    "KotlinBridge",
    "TIER_MODULES",
    "BRIDGE_MAIN_CLASS",
    "encode_bytes",
    "decode_bytes",
]
