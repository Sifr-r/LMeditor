"""Proof that the disclosure checks can fail.

The server takes the machine's own detail out of what it tells the
client: an absolute path, the tier's own rendering of a map of details,
the class the JVM died with. A check that holds it to that is only
worth anything if the check *can* fail -- and a check made against the
text of a JSON body cannot.

The body of an answer is JSON, and JSON escapes the backslash of a
Windows path. The text of the body therefore holds the path in its
escaped spelling, and a check for the unescaped spelling made against
that text passes whether the path is there or not. It proves nothing.

This file is the proof of that, and the proof of the fix:

* the check as it is now written -- against the value the client parses
  -- is shown passing with the sanitiser in place and failing with the
  sanitiser taken out of the way;
* the check as it was written -- against the text of the body -- is
  shown passing *either way*, which is why it was replaced and not
  merely moved.

The checks themselves live with the thing they check:
``test_disclosure_residuals.py`` and ``test_trust_boundary.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from lmeditor.core.exceptions import (
    LibraryError,
    LMeditorError,
    SourceUploadTooLargeError,
    ValidationError,
)
from lmeditor.server import routes, routes_helpers
from lmeditor.server.app import create_app
from tests.http_support import async_client

#: A path on a machine that is not the caller's business.
SECRET_PATH = r"C:\Users\victim\AppData\Local\Temp\lmeditor_uploads\secret\notes.md"

#: The same, in the spelling the trust-boundary checks use.
BOUNDARY_PATH = r"D:\Users\victim\secret\notes.md"

#: The head of the boundary path, in the spelling the checks use: one
#: backslash, which is what the *value* holds and what the text of the
#: body does not.
BOUNDARY_HEAD = r"D:\Users\victim"


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


def _call_with_details(error: type[Exception], details: dict[str, Any]) -> httpx.Response:
    """Make one request against an application that fails with these details."""
    app = create_app()

    @app.get("/_test/failure")
    async def _failure() -> dict[str, Any]:
        raise error("the chunker could not read the document", details=details)  # type: ignore[call-arg]

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.get("/_test/failure")

    return asyncio.run(run())


def _revert_the_sanitiser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the machine's own detail back into what the client is told.

    The sanitiser is :func:`routes_helpers._without_machine_detail`, and
    everything the server says passes through it. Taking it out of the
    way is how a check is shown to be able to fail: with it in place the
    detail is gone, and with it out of the way the detail is there and
    the check has something to catch.
    """
    monkeypatch.setattr(routes_helpers, "_without_machine_detail", lambda text: text)


def _strings(body: Any) -> list[str]:
    """Every string in the body, as the client parses it."""
    if isinstance(body, str):
        return [body]
    if isinstance(body, dict):
        return [text for value in body.values() for text in _strings(value)]
    if isinstance(body, (list, tuple)):
        return [text for value in body for text in _strings(value)]
    return []


def _stub_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the boundary checks off the tier, so the test is about the answer."""
    monkeypatch.setattr(routes, "validate_source_id", lambda source_id: source_id)
    monkeypatch.setattr(routes, "assert_public_url", lambda url: url)


# ---------------------------------------------------------------------------
# Why the text of the body cannot carry the check
# ---------------------------------------------------------------------------


def test_the_text_of_a_json_body_cannot_tell_a_path_that_is_there_from_one_that_is_not() -> None:
    """The body of an answer is JSON, and JSON escapes the backslash.

    The body here holds the path. The text of the body holds it in its
    escaped spelling, so the unescaped spelling is in neither the body
    that has the path nor the body that has not: a check for it against
    the text of the body cannot fail.
    """
    body_with_the_path = {
        "error": "LMeditorError",
        "message": f"the tier failed while reading {SECRET_PATH}",
    }
    body_without_the_path = {
        "error": "LMeditorError",
        "message": "the tier failed while reading <path>",
    }

    text_with = json.dumps(body_with_the_path)
    text_without = json.dumps(body_without_the_path)

    # The path is there, in the value the client parses.
    assert SECRET_PATH in body_with_the_path["message"]
    assert SECRET_PATH not in body_without_the_path["message"]

    # The path is in neither text, so the check against the text passes
    # whether the path is there or not.
    assert SECRET_PATH not in text_with
    assert SECRET_PATH not in text_without
    assert SECRET_PATH in body_with_the_path["message"]


def test_the_check_on_the_decoded_value_catches_an_unsanitised_message() -> None:
    """The positive control: the same round trip, the same check, caught.

    A known-unsanitised message is put through the same JSON round trip
    the server's answer goes through, and the check the tests now make
    catches it.
    """
    body = {"error": "LMeditorError", "message": f"the tier failed while reading {SECRET_PATH}"}

    assert SECRET_PATH in body["message"]
    assert SECRET_PATH not in json.dumps(body)


# ---------------------------------------------------------------------------
# The check on the message of a failure (test_trust_boundary.py)
# ---------------------------------------------------------------------------


def test_the_check_on_the_message_of_a_failure_can_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message check passes with the sanitiser in place and fails without it."""
    message = f"the tier failed while reading {BOUNDARY_PATH}"

    response = _call_with_failure(LMeditorError(message))

    assert response.status_code == 500
    assert BOUNDARY_HEAD not in response.json()["message"]
    assert BOUNDARY_PATH not in response.json()["message"]

    _revert_the_sanitiser(monkeypatch)

    response = _call_with_failure(LMeditorError(message))

    # The very same check now fails: the detail is there.
    assert BOUNDARY_HEAD in response.json()["message"]
    assert BOUNDARY_PATH in response.json()["message"]
    # And the check as it was written -- against the text of the body --
    # still passes, which is why it was replaced and not merely moved.
    assert BOUNDARY_HEAD not in response.text


# ---------------------------------------------------------------------------
# The check on the message of a validation failure (test_trust_boundary.py)
# ---------------------------------------------------------------------------


def test_the_check_on_the_message_of_a_validation_failure_can_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validation message check passes with the sanitiser and fails without it."""
    message = f"the root is not a folder the server may use: {BOUNDARY_PATH}"

    response = _call_with_failure(ValidationError(message))

    assert response.status_code == 400
    assert BOUNDARY_HEAD not in response.json()["message"]
    assert BOUNDARY_PATH not in response.json()["message"]

    _revert_the_sanitiser(monkeypatch)

    response = _call_with_failure(ValidationError(message))

    assert BOUNDARY_HEAD in response.json()["message"]
    assert BOUNDARY_PATH in response.json()["message"]
    assert BOUNDARY_HEAD not in response.text


# ---------------------------------------------------------------------------
# The check on the details of a failure (test_trust_boundary.py)
# ---------------------------------------------------------------------------


def test_the_check_on_the_details_of_a_failure_can_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The details check passes with the sanitiser in place and fails without it."""
    details = {"document_id": "doc_1", "reason": f"the file {BOUNDARY_PATH} is not text"}

    response = _call_with_details(LibraryError, details)

    assert response.status_code == 400
    assert BOUNDARY_HEAD not in response.json()["details"]["reason"]
    assert BOUNDARY_PATH not in response.json()["details"]["reason"]

    _revert_the_sanitiser(monkeypatch)

    response = _call_with_details(LibraryError, details)

    assert BOUNDARY_HEAD in response.json()["details"]["reason"]
    assert BOUNDARY_PATH in response.json()["details"]["reason"]
    assert BOUNDARY_HEAD not in response.text


# ---------------------------------------------------------------------------
# The check on the detail of a refused upload (test_disclosure_residuals.py)
# ---------------------------------------------------------------------------


def test_the_check_on_the_detail_of_a_refused_upload_can_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload detail check passes with the sanitiser and fails without it."""
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
    assert SECRET_PATH not in response.json()["detail"]

    _revert_the_sanitiser(monkeypatch)

    response = _call(
        "POST",
        "/api/v1/sources/ingest",
        data={"source_id": "src_one", "title": "Notes"},
        files={"file": ("notes.txt", b"body", "text/plain")},
    )

    assert SECRET_PATH in response.json()["detail"]
    assert SECRET_PATH not in response.text


# ---------------------------------------------------------------------------
# The whole of the answer, field by field (test_disclosure_residuals.py)
# ---------------------------------------------------------------------------


def test_the_check_over_the_whole_of_the_answer_can_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field-by-field check passes with the sanitiser and fails without it.

    This is the check ``_no_machine_detail`` makes: every string the
    client is told, as the client parses it, is free of the machine's
    own detail.
    """
    message = f"the tier could not read the manifest at {SECRET_PATH}"

    response = _call_with_failure(LMeditorError(message))

    assert response.status_code == 500
    texts = _strings(response.json())
    assert texts
    for text in texts:
        assert SECRET_PATH not in text

    _revert_the_sanitiser(monkeypatch)

    response = _call_with_failure(LMeditorError(message))

    texts = _strings(response.json())
    assert any(SECRET_PATH in text for text in texts)
