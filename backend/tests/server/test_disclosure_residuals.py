"""What the residual disclosure paths say when they are closed.

The first repair lanes closed the paths they named. This file holds the four
that were left open, and the one place where a failure was *dropped* instead
of being logged:

* a tier error's own JVM rendering -- a class name, a stack frame (O-1);
* a POSIX absolute path under a root the server was never told about (O-2);
* the reason a destination was refused, with its addresses (O-3);
* the language-model client's own exception text (O-5);
* the oversized upload, whose diagnostic was never logged.

Every site is held to the same property, and the property is proved from the
outside: the answer names the operation that failed, it describes no folder,
no address and no class of the JVM, it carries no ``| Details:`` fragment,
and the diagnostic is not lost -- it reaches the log, where the operator can
read it.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from dspy.clients.base_lm import BaseLM

from lmeditor.bridge.client import KotlinBridge
from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.core.exceptions import (
    InlineEditRejectedError,
    LMeditorError,
    SourceUploadTooLargeError,
)
from lmeditor.core.schemas import InlineEditRequest
from lmeditor.editing.editor import InlineEditor
from lmeditor.editing.rewrite import rewrite_with_lm
from lmeditor.ingestion.url_policy import UnsafeSourceError
from lmeditor.server import routes, routes_helpers
from lmeditor.server.app import create_app
from lmeditor.server.routes_helpers import safe_detail_value, safe_message
from lmeditor.server.session import session_manager
from tests.http_support import async_client

#: A path on a machine that is not the caller's business.
SECRET_PATH = r"C:\Users\victim\AppData\Local\Temp\lmeditor_uploads\secret\notes.md"

#: The same, on a POSIX machine, under a root the server was never told
#: about -- so the roots the old rule knew about do not cover it.
POSIX_SECRET_PATH = "/data/secrets/key.pem"

#: What the JVM says when the class it was asked for is not on the
#: classpath.
JVM_TEXT = (
    "ClassNotFoundException: lmeditor.bridge.BridgeMain\n"
    "\tat lmeditor.bridge.BridgeMain.main(BridgeMain.kt:42)"
)

#: The tier's own rendering of an unexpected failure: what went wrong, and
#: then the JVM's own words about it.
TIER_MESSAGE = (
    "the provenance indexer query failed at turn 3: java.lang.NullPointerException"
)

#: What a model provider says when its own call fails.
PROVIDER_TEXT = (
    "anthropic.APIConnectionError: Connection error. "
    "url=https://api.anthropic.com/v1/messages"
)


def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make one request against the application."""
    app = create_app()

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _call_with_failure(error: Exception) -> httpx.Response:
    """Make one request against an application that fails on purpose.

    The route is added to a throwaway application, so the failure is a
    failure of the server under test and not a mock of it.
    """
    app = create_app()

    @app.get("/_test/failure")
    async def _failure() -> dict[str, Any]:
        raise error

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.get("/_test/failure")

    return asyncio.run(run())


def _stub_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the boundary checks off the tier, so the test is about the answer.

    The source-id policy and the destination policy are the tier's rules and
    are proved there. Here they would only decide whether the request reaches
    the site under test.
    """
    monkeypatch.setattr(routes, "validate_source_id", lambda source_id: source_id)
    monkeypatch.setattr(routes, "assert_public_url", lambda url: url)


def _url_body(source_id: str = "src_one") -> dict[str, Any]:
    """The body of a URL ingestion request."""
    return {
        "source_id": source_id,
        "kind": "url",
        "origin": "https://93.184.216.34/paper",
        "title": "Example paper",
    }


def _client_texts(body: Any) -> list[str]:
    """Every string the client is told, as the client parses it.

    The body of an answer is JSON, and JSON escapes the backslash of a
    Windows path: the text of the body holds the path in its escaped
    spelling, so a check made against that text passes whether the path
    is there or not. The values are what the client parses, so the check
    is made against them.

    The keys of the details are not walked: the server keeps the keys as
    they are and takes the machine's own detail out of the values only,
    so a check here would be a check on something the server does not
    promise.
    """
    if isinstance(body, str):
        return [body]
    if isinstance(body, dict):
        return [text for value in body.values() for text in _client_texts(value)]
    if isinstance(body, (list, tuple)):
        return [text for value in body for text in _client_texts(value)]
    return []


def _no_machine_detail(response: httpx.Response) -> None:
    """Assert the answer describes no folder, no address and no JVM class."""
    for text in _client_texts(response.json()):
        assert SECRET_PATH not in text
        assert POSIX_SECRET_PATH not in text
        assert "Details:" not in text
        assert "java.io" not in text
        assert "java.lang" not in text
        assert "NullPointerException" not in text
        assert "ClassNotFoundException" not in text
        assert "BridgeMain" not in text


# ---------------------------------------------------------------------------
# O-1: the tier's own message, and the JVM's words in it
# ---------------------------------------------------------------------------


def test_a_tier_error_names_the_operation_and_no_jvm_text() -> None:
    """The tier's own failure says what failed, and no class of the JVM."""
    response = _call_with_failure(
        KotlinDomainError(
            "BridgeError",
            f"the tier could not read the manifest at {POSIX_SECRET_PATH}: "
            "java.lang.NullPointerException",
            {"exception": "NullPointerException"},
        )
    )

    assert response.status_code == 500
    assert response.json()["error"] == "LMeditorError"
    message = response.json()["message"]
    assert "the tier could not read the manifest" in message
    _no_machine_detail(response)


def test_a_tier_error_keeps_the_jvm_text_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The JVM's own words stay in the log, where the operator can read them."""
    with caplog.at_level(logging.ERROR, logger="lmeditor.server.app"):
        _call_with_failure(
            KotlinDomainError(
                "BridgeError",
                f"the tier could not read the manifest at {POSIX_SECRET_PATH}: "
                "java.lang.NullPointerException",
                {"exception": "NullPointerException"},
            )
        )

    assert "java.lang.NullPointerException" in caplog.text
    assert POSIX_SECRET_PATH in caplog.text


#: A bridge process that answers one method with the tier's own envelope
#: for an unexpected failure -- the shape ``Bridge.kt`` produces when a
#: handler raises something the tier did not expect.
FAKE_BRIDGE = '''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    response = {
        "ok": False,
        "error": {
            "type": "BridgeError",
            "message": "the provenance indexer query failed at turn 3: "
                       "java.lang.NullPointerException",
            "details": {"exception": "NullPointerException"},
        },
    }
    response["id"] = request.get("id")
    sys.stdout.write(json.dumps(response) + "\\n")
    sys.stdout.flush()
'''


def test_a_bridge_error_over_the_wire_reaches_the_client_sanitised(
    tmp_path: Path,
) -> None:
    """The seam's own envelope is sanitised on the way to the client.

    This is the whole chain: the JVM raises something the tier did not
    expect, the tier's envelope crosses the wire, the client rebuilds the
    error, and the answer the client gets names the operation and no class
    of the JVM.
    """
    script = tmp_path / "fake_bridge.py"
    script.write_text(FAKE_BRIDGE, encoding="utf-8")

    bridge = KotlinBridge(command=[sys.executable, str(script)])
    try:
        with pytest.raises(KotlinDomainError) as caught:
            bridge.call("provenance.index")
    finally:
        bridge.close()

    assert caught.value.error_type == "BridgeError"

    response = _call_with_failure(caught.value)

    assert response.status_code == 500
    assert response.json()["error"] == "LMeditorError"
    assert "provenance indexer query" in response.json()["message"]
    _no_machine_detail(response)


def test_a_workflow_run_that_dies_of_a_jvm_error_names_no_class(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The session's own error says what failed, and no class of the JVM.

    The run's failure is written into the session and read back by
    ``GET /workflow/{doc_id}/status``, so it is a path to the client of its
    own.
    """

    async def dying(session: Any, request: Any) -> Any:
        raise LMeditorError(TIER_MESSAGE)

    monkeypatch.setattr(routes, "_invoke_workflow", dying)

    async def run() -> Any:
        session = await session_manager.create_session(doc_id="doc_jvm", topic="topic")
        await routes._run_workflow_worker(session, object())
        return session

    with caplog.at_level(logging.ERROR, logger="lmeditor.server.routes"):
        session = asyncio.run(run())

    assert session.error is not None
    assert "provenance indexer query" in session.error
    assert "java.lang.NullPointerException" not in session.error
    assert "NullPointerException" not in session.error
    assert "java.lang.NullPointerException" in caplog.text


# ---------------------------------------------------------------------------
# O-2: a POSIX path under a root the server was never told about
# ---------------------------------------------------------------------------


def test_a_posix_path_outside_the_known_roots_is_taken_out() -> None:
    """A path under any root is the server's own, and does not leave it."""
    response = _call_with_failure(
        LMeditorError(f"the tier could not read the manifest at {POSIX_SECRET_PATH}")
    )

    assert response.status_code == 500
    assert "the tier could not read the manifest" in response.json()["message"]
    assert POSIX_SECRET_PATH not in response.text
    assert "/data/secrets" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/data", "/workspace", "/custom-storage", "/\u7814\u7a76", "/workspace/", "/custom-storage/secrets"],
)
def test_single_posix_roots_are_masked_without_losing_the_reason(path: str) -> None:
    message = f"cannot open '{path}': permission denied"
    expected = "cannot open '<path>': permission denied"
    assert safe_message(message) == expected
    assert safe_detail_value(message) == expected
    assert safe_message(expected) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/workspace",
        "https://example.org:8443//workspace/file",
        "https://[2001:db8::1]/workspace",
        "https://[2001:db8::1]//workspace/file",
        "HTTPS://example.org/workspace",
    ],
)
def test_public_urls_survive_beside_a_redacted_posix_root(url: str) -> None:
    message = f"cannot save {url} to '/workspace': permission denied"
    expected = f"cannot save {url} to '<path>': permission denied"
    assert safe_message(message) == expected
    assert safe_detail_value(message) == expected


@pytest.mark.parametrize(
    "path",
    ["file:///workspace", "file:///custom-storage/secrets", "//custom-storage/secrets"],
)
def test_file_urls_and_double_slash_paths_remain_redacted(path: str) -> None:
    result = safe_message(f"cannot open '{path}': permission denied")
    assert "workspace" not in result
    assert "custom-storage" not in result
    assert "permission denied" in result


def test_relative_words_and_useful_error_text_are_preserved() -> None:
    message = "read/write failed: retry the import/export operation"
    assert safe_message(message) == message
    assert safe_detail_value(message) == message


@pytest.mark.parametrize("separator", [")", "):"])
def test_a_url_does_not_hide_a_path_after_its_closing_delimiter(separator: str) -> None:
    message = f"see (https://example.org{separator}/workspace"
    expected = f"see (https://example.org{separator}<path>"
    assert safe_message(message) == expected
    assert safe_detail_value(message) == expected


def test_a_posix_path_outside_the_known_roots_keeps_the_path_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The path that was refused is still readable -- by the operator."""
    with caplog.at_level(logging.ERROR, logger="lmeditor.server.app"):
        _call_with_failure(
            LMeditorError(f"the tier could not read the manifest at {POSIX_SECRET_PATH}")
        )

    assert POSIX_SECRET_PATH in caplog.text


def test_the_path_of_a_url_is_the_callers_own_and_survives() -> None:
    """The path of a URL is not a folder of the machine, and stays in the answer."""
    message = "the origin https://example.com/paper was refused"

    assert safe_message(message) == message

    response = _call_with_failure(LMeditorError(message))

    assert response.status_code == 500
    assert "https://example.com/paper" in response.json()["message"]


def test_a_path_of_a_url_that_names_a_folder_is_still_stripped() -> None:
    """A URL that names a folder of the machine loses the folder."""
    response = _call_with_failure(
        LMeditorError(f"the origin file://{POSIX_SECRET_PATH} was refused")
    )

    assert response.status_code == 500
    assert POSIX_SECRET_PATH not in response.text
    assert "file:" in response.json()["message"]


# ---------------------------------------------------------------------------
# O-3: the reason a destination was refused
# ---------------------------------------------------------------------------


def _refusing_origin(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    """Make the destination policy refuse the origin, with ``reason``."""
    monkeypatch.setattr(routes, "validate_source_id", lambda source_id: source_id)

    def refusing(url: str) -> str:
        raise UnsafeSourceError(reason)

    monkeypatch.setattr(routes, "assert_public_url", refusing)


def test_a_rejected_origin_names_the_reason_and_no_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal says why the origin was refused, and names no address."""
    _refusing_origin(
        monkeypatch,
        "host 'example.com' resolves only to internal address(es): 10.0.0.5",
    )

    response = _call("POST", "/api/v1/sources/url", json=_url_body())

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Rejected origin" in detail
    assert "resolves only to internal address" in detail
    assert "10.0.0.5" not in response.text
    assert "example.com" in detail


def test_a_rejected_origin_keeps_the_address_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The address the host resolved to is still readable -- by the operator."""
    _refusing_origin(
        monkeypatch,
        "host 'example.com' resolves only to internal address(es): 10.0.0.5",
    )

    with caplog.at_level(logging.WARNING, logger="lmeditor.server.routes"):
        _call("POST", "/api/v1/sources/url", json=_url_body())

    assert "10.0.0.5" in caplog.text


# ---------------------------------------------------------------------------
# O-5: the language-model client's own exception text
# ---------------------------------------------------------------------------


class _FailingLM(BaseLM):  # type: ignore[misc]
    """A model client that fails the way a provider's client fails."""

    model_name = "failing"

    def __init__(self) -> None:
        super().__init__(self.model_name, "chat", 0.0, 1000, True)

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        raise RuntimeError(PROVIDER_TEXT)

    async def aforward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        raise RuntimeError(PROVIDER_TEXT)


def test_the_rewrite_reason_carries_no_provider_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reason a rewrite failed names no provider, and no endpoint."""
    with (
        caplog.at_level(logging.WARNING, logger="lmeditor.editing.rewrite"),
        pytest.raises(InlineEditRejectedError) as caught,
    ):
        rewrite_with_lm(
            lm=_FailingLM(),
            instruction="tighten the sentence",
            text="Mangrove forests slow storm surge.",
            passages=(),
        )

    reason = caught.value.reason
    assert "the rewrite model call failed" in reason
    assert "anthropic" not in reason
    assert "api.anthropic.com" not in reason
    assert "APIConnectionError" not in reason
    assert "anthropic.APIConnectionError" in caplog.text


@pytest.mark.asyncio
async def test_the_editor_reason_carries_no_provider_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reason an edit was refused names no provider, and no endpoint.

    The rewrite itself is called directly: the full edit asks the Kotlin
    tier for its plan first, and this is about the text the model call
    produced, not about the plan.
    """

    def failing(*, instruction: str, text: str, passages: Any) -> str:
        raise RuntimeError(PROVIDER_TEXT)

    editor = InlineEditor(rewrite=failing)
    request = InlineEditRequest(
        section_id="section_0001",
        instruction="tighten the sentence",
        selected_text="Mangrove forests slow storm surge.",
    )

    with (
        caplog.at_level(logging.WARNING, logger="lmeditor.editing.editor"),
        pytest.raises(InlineEditRejectedError) as caught,
    ):
        await editor._run_rewrite(failing, request, "tighten it", "the text", ())

    reason = caught.value.reason
    assert "the rewrite call failed" in reason
    assert "anthropic" not in reason
    assert "api.anthropic.com" not in reason
    assert "APIConnectionError" not in reason
    assert "anthropic.APIConnectionError" in caplog.text


def test_a_refused_rewrite_reaches_the_client_with_no_provider_text() -> None:
    """The answer a refusal gets carries no provider text either."""
    with pytest.raises(InlineEditRejectedError) as caught:
        rewrite_with_lm(
            lm=_FailingLM(),
            instruction="tighten the sentence",
            text="Mangrove forests slow storm surge.",
            passages=(),
        )

    response = _call_with_failure(caught.value)

    assert response.status_code == 422
    assert response.json()["error"] == "InlineEditRejectedError"
    assert "the rewrite model call failed" in response.json()["details"]["reason"]
    assert "anthropic" not in response.text
    assert "api.anthropic.com" not in response.text
    assert "APIConnectionError" not in response.text


# ---------------------------------------------------------------------------
# The logging gap: the oversized upload
# ---------------------------------------------------------------------------


def test_an_oversized_upload_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The upload that was too large is still readable -- by the operator."""
    _stub_the_policy(monkeypatch)

    async def too_large(file: Any, source_id: str) -> Any:
        raise SourceUploadTooLargeError(
            f"Upload of '{source_id}' exceeds the 52428800 byte limit."
        )

    monkeypatch.setattr(routes_helpers, "persist_source_to_disk", too_large)

    with caplog.at_level(logging.WARNING, logger="lmeditor.server.routes"):
        response = _call(
            "POST",
            "/api/v1/sources/ingest",
            data={"source_id": "src_one", "title": "Notes"},
            files={"file": ("notes.txt", b"body", "text/plain")},
        )

    assert response.status_code == 413
    assert "exceeds the 52428800 byte limit" in caplog.text


def test_an_oversized_upload_names_the_operation_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal of an oversized upload says what was too large, and no folder."""
    _stub_the_policy(monkeypatch)

    async def too_large(file: Any, source_id: str) -> Any:
        raise SourceUploadTooLargeError(
            f"Upload of '{source_id}' exceeds the 52428800 byte limit: {SECRET_PATH}"
        )

    monkeypatch.setattr(routes_helpers, "persist_source_to_disk", too_large)

    response = _call(
        "POST",
        "/api/v1/sources/ingest",
        data={"source_id": "src_one", "title": "Notes"},
        files={"file": ("notes.txt", b"body", "text/plain")},
    )

    assert response.status_code == 413
    assert "exceeds the 52428800 byte limit" in response.json()["detail"]
    _no_machine_detail(response)
