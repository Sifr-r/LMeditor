"""What an error says when it leaves the server.

The gateway is local, but the answers it gives are read by anything that can
reach it, and a message that names the machine behind the failure -- an
absolute path, the JVM's own class names, the stderr it died with -- tells a
reader more about the operator's disk than the failure itself. That shape
belongs in the server's log, where the operator can read it; the client is
told *which* operation failed and nothing about where it happened.

These tests hold one property at every site where a failure becomes an
answer: the answer names the operation, and it describes no folder and no
JVM. The diagnostic is not lost -- it reaches the log -- and that is proved
here too, because a message that is sanitised everywhere and nowhere at all
is a message that is simply gone.

The sites are the ones the first repair missed: the 500 handler was fixed,
and the same class of leak was left open in the 400s, the 422s, the 502 and
the session's own ``error`` field.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from lmeditor import notebooks
from lmeditor.bridge.errors import BridgeUnavailableError
from lmeditor.core.exceptions import (
    InvalidStateTransitionError,
    LibraryError,
    LMeditorError,
    SourceLoadError,
    WordParseError,
)
from lmeditor.core.schemas import HITLReviewPayload, InlineEditResult, Source
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.url_policy import UnsafeSourceError
from lmeditor.server import routes, routes_helpers
from lmeditor.server.app import create_app
from lmeditor.server.notebooks import close_notebooks_root
from lmeditor.server.session import session_manager
from lmeditor.server.sources import StoredSource, source_library
from lmeditor.tier import server as tier_server

#: A path on a machine that is not the caller's business.
SECRET_PATH = r"C:\Users\victim\AppData\Local\Temp\lmeditor_uploads\secret\notes.md"

#: What the JVM says when the class it was asked for is not on the classpath.
JVM_TEXT = (
    "ClassNotFoundException: lmeditor.bridge.BridgeMain\n"
    "\tat lmeditor.bridge.BridgeMain.main(BridgeMain.kt:42)"
)


def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make one request against the application."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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

    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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


# ---------------------------------------------------------------------------
# F2: the upload that was refused
# ---------------------------------------------------------------------------


def test_a_refused_upload_names_the_upload_and_no_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal of an upload says what was refused, and no folder."""
    _stub_the_policy(monkeypatch)

    async def refusing(file: Any, source_id: str) -> Any:
        raise UnsafeSourceError(f"resolved upload path escapes the upload root: {SECRET_PATH}")

    monkeypatch.setattr(routes_helpers, "persist_source_to_disk", refusing)

    response = _call(
        "POST",
        "/api/v1/sources/ingest",
        data={"source_id": "src_one", "title": "Notes"},
        files={"file": ("notes.txt", b"body", "text/plain")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Rejected upload" in detail
    assert "escapes the upload root" in detail
    # Checked against the value the client parses: JSON escapes the
    # backslash of a path, so the text of the body cannot carry one.
    assert r"C:\Users\victim" not in detail
    assert "lmeditor_uploads" not in response.text


def test_a_refused_upload_keeps_the_reason_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The path that was refused is still readable -- by the operator."""
    _stub_the_policy(monkeypatch)

    async def refusing(file: Any, source_id: str) -> Any:
        raise UnsafeSourceError(f"resolved upload path escapes the upload root: {SECRET_PATH}")

    monkeypatch.setattr(routes_helpers, "persist_source_to_disk", refusing)

    with caplog.at_level(logging.WARNING, logger="lmeditor.server.routes"):
        _call(
            "POST",
            "/api/v1/sources/ingest",
            data={"source_id": "src_one", "title": "Notes"},
            files={"file": ("notes.txt", b"body", "text/plain")},
        )

    assert SECRET_PATH in caplog.text


# ---------------------------------------------------------------------------
# F3: the session that could not be resumed
# ---------------------------------------------------------------------------


def _run_a_review_that_cannot_be_resumed(monkeypatch: pytest.MonkeyPatch, doc_id: str) -> Any:
    """Drive a review whose run is gone, and return the session it failed on.

    The review is opened by the server, so the decision is recorded once the
    review is pending -- which is what an operator's decision does.
    """

    async def unresumable(session: Any, decision: Any) -> dict[str, Any]:
        raise InvalidStateTransitionError(
            f"no resumable workflow is registered for '{SECRET_PATH}'",
            details={"path": SECRET_PATH},
        )

    monkeypatch.setattr(routes, "_resume_suspended_workflow", unresumable)

    async def run() -> Any:
        session = await session_manager.create_session(doc_id=doc_id, topic="resume")
        try:
            pending = asyncio.create_task(
                routes._await_review_decision(session, {"hitl_reason": "review required"})
            )
            for _ in range(100):
                if session.hitl_pending:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("the review was never opened")
            session.record_hitl_decision(HITLReviewPayload(doc_id=doc_id, approved=True))
            await pending
            return session
        finally:
            # The registry is process-wide: the session this test opened is
            # taken out of it again, whether the body succeeded or not.
            await session_manager.remove_session(doc_id)

    return asyncio.run(run())


def test_a_session_that_cannot_resume_names_the_operation_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session's own error says which operation failed, and no folder."""
    session = _run_a_review_that_cannot_be_resumed(monkeypatch, "doc_cannot_resume")

    assert session.error is not None
    assert "cannot resume the suspended workflow" in session.error
    assert r"C:\Users\victim" not in session.error
    assert "Details:" not in session.error


def test_a_session_that_cannot_resume_keeps_the_reason_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The reason the run could not be resumed is still in the log."""
    with caplog.at_level(logging.WARNING, logger="lmeditor.server.routes"):
        _run_a_review_that_cannot_be_resumed(monkeypatch, "doc_cannot_resume_logged")

    assert SECRET_PATH in caplog.text


# ---------------------------------------------------------------------------
# F4: the tier that is not there
# ---------------------------------------------------------------------------


def test_a_missing_tier_names_the_operation_and_no_jvm_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A 502 says which operation was lost, and nothing about the JVM."""
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(tmp_path))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_DIR", str(tmp_path / "notebooks"))
    close_notebooks_root()

    def unavailable(root: str) -> list[Any]:
        raise BridgeUnavailableError("the bridge process exited before answering", JVM_TEXT)

    monkeypatch.setattr(notebooks, "list_notebooks", unavailable)

    response = _call("GET", "/api/v1/notebooks")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "not available" in detail
    assert "listing the notebooks" in detail
    assert "ClassNotFoundException" not in response.text
    assert "BridgeMain" not in response.text


def test_a_missing_tier_keeps_the_reason_in_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The JVM's own words stay in the log, where the operator can read them."""
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(tmp_path))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_DIR", str(tmp_path / "notebooks"))
    close_notebooks_root()

    def unavailable(root: str) -> list[Any]:
        raise BridgeUnavailableError("the bridge process exited before answering", JVM_TEXT)

    monkeypatch.setattr(notebooks, "list_notebooks", unavailable)

    with caplog.at_level(logging.ERROR, logger="lmeditor.server.routes"):
        _call("GET", "/api/v1/notebooks")

    assert "ClassNotFoundException" in caplog.text
    assert "BridgeMain" in caplog.text


def test_a_bridge_failure_that_reaches_the_gateway_carries_no_jvm_text() -> None:
    """A failure of the seam that escapes every route is still sanitised."""
    response = _call_with_failure(
        BridgeUnavailableError("the bridge process exited before answering", JVM_TEXT)
    )

    assert response.status_code == 500
    assert response.json()["error"] == "LMeditorError"
    assert "not available" in response.json()["message"]
    assert "ClassNotFoundException" not in response.text
    assert "BridgeMain" not in response.text


# ---------------------------------------------------------------------------
# F5: the source id that was refused
# ---------------------------------------------------------------------------


def test_a_refused_source_id_names_the_id_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal of a source id says what was refused, and no folder."""
    _stub_the_policy(monkeypatch)

    def refusing(source_id: str) -> str:
        raise UnsafeSourceError(f"resolved upload path escapes the upload root: {SECRET_PATH}")

    monkeypatch.setattr(routes, "validate_source_id", refusing)

    response = _call("POST", "/api/v1/sources/url", json=_url_body())

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Invalid source_id" in detail
    # Checked against the value the client parses: JSON escapes the
    # backslash of a path, so the text of the body cannot carry one.
    assert r"C:\Users\victim" not in detail
    assert "lmeditor_uploads" not in response.text


# ---------------------------------------------------------------------------
# F6: the source that could not be loaded
# ---------------------------------------------------------------------------


def test_a_source_that_cannot_be_loaded_names_the_operation_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 422 says which operation failed, and describes no folder."""
    from lmeditor.ingestion.service import IngestionService

    _stub_the_policy(monkeypatch)

    class _RefusingService:
        def ingest(self, **kwargs: Any) -> Any:
            raise SourceLoadError(
                SourceKind.PDF.value,
                SECRET_PATH,
                "the file could not be read: java.io.IOException",
            )

    monkeypatch.setattr(
        IngestionService, "default", staticmethod(lambda: _RefusingService())
    )

    response = _call("POST", "/api/v1/sources/url", json=_url_body())

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Source could not be loaded" in detail
    # Checked against the value the client parses: JSON escapes the
    # backslash of a path, so the text of the body cannot carry one.
    assert r"C:\Users\victim" not in detail
    assert "lmeditor_uploads" not in response.text
    assert "Details:" not in response.text


def test_a_source_that_cannot_be_loaded_keeps_the_reason_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The origin that could not be loaded is still in the log."""
    from lmeditor.ingestion.service import IngestionService

    _stub_the_policy(monkeypatch)

    class _RefusingService:
        def ingest(self, **kwargs: Any) -> Any:
            raise SourceLoadError(
                SourceKind.PDF.value,
                SECRET_PATH,
                "the file could not be read: java.io.IOException",
            )

    monkeypatch.setattr(
        IngestionService, "default", staticmethod(lambda: _RefusingService())
    )

    with caplog.at_level(logging.WARNING, logger="lmeditor.server.routes"):
        _call("POST", "/api/v1/sources/url", json=_url_body())

    assert SECRET_PATH in caplog.text


# ---------------------------------------------------------------------------
# F7: the notebooks folder that could not be made
# ---------------------------------------------------------------------------


def test_a_notebooks_root_that_cannot_be_made_names_the_folder_and_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The refusal says which folder failed, and describes no folder."""
    base = tmp_path / "base"
    base.mkdir()
    blocker = base / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")

    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(base))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_DIR", str(blocker / "notebooks"))
    close_notebooks_root()

    response = _call("GET", "/api/v1/notebooks")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "notebooks root" in detail
    assert "could not be created" in detail
    # Checked against the value the client parses: JSON escapes the
    # backslash of a path, so the text of the body cannot carry one.
    assert str(base) not in detail
    assert "blocker" not in response.text


def test_a_notebooks_root_that_cannot_be_made_keeps_the_reason_in_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The folder that could not be made is still in the log."""
    base = tmp_path / "base"
    base.mkdir()
    blocker = base / "blocker"
    blocker.write_text("not a folder", encoding="utf-8")

    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(base))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_DIR", str(blocker / "notebooks"))
    close_notebooks_root()

    with caplog.at_level(logging.ERROR, logger="lmeditor.server.notebooks"):
        _call("GET", "/api/v1/notebooks")

    assert str(base) in caplog.text


# ---------------------------------------------------------------------------
# F8: the tier's own failure
# ---------------------------------------------------------------------------


def test_a_tier_failure_names_the_operation_and_no_jvm_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The tier's own failure says what could not be done, and no JVM."""
    def failing_add(source: Any) -> dict[str, Any]:
        raise RuntimeError(JVM_TEXT)

    monkeypatch.setattr(tier_server, "add_source", failing_add)

    stored = StoredSource.from_source(
        Source(
            id="src_one",
            kind=SourceKind.TEXT,
            origin="memory://src_one",
            title="A Source",
        ),
        (),
    )

    with caplog.at_level(logging.ERROR, logger="lmeditor.server.sources"), pytest.raises(
        LMeditorError
    ) as excinfo:
        source_library.add(stored)

    assert excinfo.value.message == "the Kotlin tier could not store the source"
    assert "ClassNotFoundException" not in excinfo.value.message
    assert "ClassNotFoundException" in caplog.text


def test_a_tier_failure_that_reaches_the_gateway_carries_no_jvm_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The domain error the tier's failure becomes is sanitised at the boundary."""

    def failing_add(source: Any) -> dict[str, Any]:
        raise RuntimeError(JVM_TEXT)

    monkeypatch.setattr(tier_server, "add_source", failing_add)

    stored = StoredSource.from_source(
        Source(
            id="src_one",
            kind=SourceKind.TEXT,
            origin="memory://src_one",
            title="A Source",
        ),
        (),
    )

    app = create_app()

    @app.get("/_test/tier")
    async def _tier() -> dict[str, Any]:
        source_library.add(stored)
        return {}

    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/_test/tier")

    response = asyncio.run(run())

    assert response.status_code == 500
    assert response.json()["error"] == "LMeditorError"
    assert response.json()["message"] == "the Kotlin tier could not store the source"
    assert "ClassNotFoundException" not in response.text
    assert "BridgeMain" not in response.text


# ---------------------------------------------------------------------------
# The sweep: the sites the first repair did not name
# ---------------------------------------------------------------------------


def test_an_unreadable_upload_names_the_document_and_no_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .docx that could not be read says what was read, and no folder."""
    import lmeditor.word

    class _RefusingReader:
        def read_bytes(self, data: bytes, **kwargs: Any) -> Any:
            raise WordParseError(
                SECRET_PATH,
                "the file could not be read: "
                f"[Errno 2] No such file or directory: '{SECRET_PATH}'",
            )

    monkeypatch.setattr(lmeditor.word, "WordReader", _RefusingReader)

    response = _call(
        "POST",
        "/api/v1/library/books/import-docx",
        data={"title": "Book"},
        files={"file": ("book.docx", b"not a package", "application/vnd.ms-word")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "could not be read" in detail
    # Checked against the value the client parses: JSON escapes the
    # backslash of a path, so the text of the body cannot carry one.
    assert r"C:\Users\victim" not in detail
    assert "lmeditor_uploads" not in response.text


def test_a_workflow_run_that_dies_names_the_failure_and_no_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A run that died says what failed, and describes no folder."""

    async def dying(session: Any, request: Any) -> dict[str, Any]:
        raise RuntimeError(f"the node could not read {SECRET_PATH}")

    monkeypatch.setattr(routes, "_invoke_workflow", dying)

    async def run() -> Any:
        session = await session_manager.create_session(doc_id="doc_died", topic="topic")
        try:
            await routes._run_workflow_worker(session, _a_request())
            return session
        finally:
            # The registry is process-wide: the session this test opened is
            # taken out of it again, whether the body succeeded or not.
            await session_manager.remove_session("doc_died")

    with caplog.at_level(logging.ERROR, logger="lmeditor.server.routes"):
        session = asyncio.run(run())

    assert session.error is not None
    assert r"C:\Users\victim" not in session.error
    assert SECRET_PATH in caplog.text


def _a_request() -> Any:
    """A stand-in for a generation request.

    The worker hands the request to the workflow, and the workflow is the
    thing that fails here, so the request itself never matters.
    """
    return object()


# ---------------------------------------------------------------------------
# The details of a failure: the keys are held to the same rule as the values
# ---------------------------------------------------------------------------


def test_a_details_key_that_names_the_disk_is_held_to_the_same_rule() -> None:
    """A key that carries the machine's own detail is held to the same rule.

    A key names the server's own thing as much as a value does: a key that
    carried an absolute path would put the machine's disk into the name the
    client reads. The key is not dropped -- the client still finds the
    detail it was sent -- and the value beside it is sanitised as before.
    """
    details = {
        f"the source at {SECRET_PATH}": f"the file is not text: {SECRET_PATH}",
        "document_id": "doc_1",
    }

    response = _call_with_failure(
        LibraryError("the library could not be read", details=details)
    )

    assert response.status_code == 400
    sent = response.json()["details"]

    # The shape is the client's: both keys are still there.
    assert len(sent) == 2
    assert sent["document_id"] == "doc_1"

    # The key that named the disk no longer names it, and it is still a key.
    (key,) = [name for name in sent if name != "document_id"]
    assert SECRET_PATH not in key
    assert r"C:\Users\victim" not in key
    assert "<path>" in key

    # The value beside it is still sanitised.
    assert SECRET_PATH not in sent[key]
    assert r"C:\Users\victim" not in sent[key]
    assert "<path>" in sent[key]
    assert "the file is not text" in sent[key]


# ---------------------------------------------------------------------------
# The reason of a success: held to the same rule as the message of a failure
# ---------------------------------------------------------------------------


def test_a_successful_edit_carries_no_path_in_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reason on a success is held to the same rule as a message.

    The tier may answer a soft refusal with a 200, and the reason of it may
    name a folder of the machine. A refusal that arrives as a success is
    still an answer the client reads, so the reason must not reach it
    unsanitised -- and the answer must still say what happened.
    """
    doc_id = "doc_edit_reason"
    section_id = "sec_edit_reason"

    class _RefusingEditor:
        """Stands in for the editor and returns a refused revision."""

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def edit(self, request: Any, section: Any) -> InlineEditResult:
            return InlineEditResult(
                doc_id=request.doc_id,
                section_id=request.section_id,
                scope=request.scope,
                original_text=section.content,
                revised_text="A tightened paragraph.",
                applied=False,
                reason=(
                    f"the revision was refused: the model could not read {SECRET_PATH}"
                ),
            )

    monkeypatch.setattr("lmeditor.editing.InlineEditor", _RefusingEditor)

    async def run() -> httpx.Response:
        session = await session_manager.create_session(doc_id=doc_id, topic="edit")
        await session.complete(sections=[{"section_id": section_id, "title": "Intro"}])
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                f"/api/v1/documents/{doc_id}/edits",
                json={"section_id": section_id, "instruction": "tighten the wording"},
            )

    try:
        response = asyncio.run(run())
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the assertions passed or not.
        asyncio.run(session_manager.remove_session(doc_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert SECRET_PATH not in body["reason"]
    assert r"C:\Users\victim" not in body["reason"]

    # The operation is still described: what happened, and to what.
    assert "the revision was refused" in body["reason"]
    assert body["doc_id"] == doc_id
    assert body["section_id"] == section_id
    assert body["applied"] is False

