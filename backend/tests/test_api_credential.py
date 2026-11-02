"""The API's bearer credential.

The API used to answer any caller that reached it. A browser page was held
to the origin rules, but a caller that is not a browser need only omit the
``Origin`` header, and then every route under ``/api/`` was open -- including
the one that approves a workflow.

The credential closes that. This file is the proof that it does: that every
route under ``/api/`` is refused without it, that a missing credential and a
wrong one are refused the same way, that the credential is served when it is
right, and that the token never appears anywhere it should not.

The contract this file holds to is ``docs/api-authentication-blueprint.md``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from lmeditor.server.app import create_app
from lmeditor.server.credential import (
    API_TOKEN_ENV,
    API_TOKEN_FILE_ENV,
    CredentialUnavailableError,
    resolve_credential,
)
from tests.http_support import TEST_TOKEN

#: The refusal, exactly as the server answers it.
REFUSAL_DETAIL = "This API requires a bearer token."

#: The header the refusal carries.
CHALLENGE = 'Bearer realm="lmeditor"'


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """The routes the API is made of.

    Starlette keeps an included router behind a holder rather than
    flattening it into the application's own route list, so the routes are
    reached through the router the application included.
    """
    found: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            found.extend(entry for entry in original.routes if isinstance(entry, APIRoute))
    return found


def _probe_path(route: APIRoute) -> str:
    """The path a probe of this route takes.

    The parameters are filled with a placeholder. The probe never reaches
    the route, so what the route would have made of the value does not
    matter -- and a placeholder that the route could not accept would be a
    weaker proof, because a rejected path and a refused credential are both
    answers that are not the route's own.
    """
    return re.sub(r"\{[^}]+\}", "probe", route.path)


def _free_port() -> int:
    """A port nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _live_api(tmp_path: Path) -> Iterator[str]:
    """The API served over a real socket, in a process of its own.

    The credential is a thing that travels over a connection, and a test
    that drives the application in-process proves that the middleware
    refuses; this proves that a connection is refused.

    The server is a **subprocess**, and not a thread of this process. The
    application's lifespan configures the language model on the way up and
    releases it on the way down, and DSPy binds its configuration to
    whichever thread configures it first -- so a server in a thread of
    this process would take that binding away from every test that runs
    after it. A subprocess has its own DSPy and its own binding.
    """
    port = _free_port()
    token_file = tmp_path / "live-api-token"
    environment = dict(os.environ)
    environment["LMEDITOR_API_TOKEN"] = TEST_TOKEN
    environment["LMEDITOR_API_TOKEN_FILE"] = str(token_file)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lmeditor.server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_the_server(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


def _wait_for_the_server(base_url: str, timeout: float = 60.0) -> None:
    """Wait until the server answers its health check."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(
                f"{base_url}/api/v1/health",
                timeout=5.0,
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"the API did not start at {base_url}")


def _test_the_refusal(response: httpx.Response) -> None:
    """That this answer is the refusal, and nothing else."""
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": REFUSAL_DETAIL}
    assert response.headers["www-authenticate"] == CHALLENGE


def test_every_api_route_is_refused_without_the_credential() -> None:
    """Every route under ``/api/`` is closed without the credential.

    The routes are taken from the application itself rather than from a
    list written out here, so a route added later is covered by this test
    without anyone remembering to extend it.
    """
    app = create_app()
    routes = _api_routes(app)
    assert routes, "the API has no routes, so this proves nothing"

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for route in routes:
                for method in sorted((route.methods or set()) - {"OPTIONS"}):
                    response = await client.request(method, _probe_path(route))
                    assert response.status_code == 401, f"{method} {route.path}"
                    assert response.json() == {"detail": REFUSAL_DETAIL}, f"{method} {route.path}"

    asyncio.run(run())


def test_a_wrong_credential_is_refused_exactly_as_a_missing_one() -> None:
    """A refusal does not say which half of the credential was wrong.

    A caller that could tell a missing credential from a wrong one would
    learn whether the token it guessed was the right length, and a refusal
    that differs tells a caller what to fix.
    """
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            missing = await client.get("/api/v1/health")
            _test_the_refusal(missing)

            wrong_tokens = [
                "Bearer wrong-token",
                "Bearer " + "x" * len(TEST_TOKEN),
                "Bearer short",
                "Bearer",
                "Bearer ",
                "Basic " + TEST_TOKEN,
                TEST_TOKEN,
                "Bearer " + TEST_TOKEN[:-1],
            ]
            for header in wrong_tokens:
                wrong = await client.get("/api/v1/health", headers={"Authorization": header})
                assert wrong.status_code == missing.status_code, header
                assert wrong.content == missing.content, header
                assert wrong.headers["www-authenticate"] == missing.headers["www-authenticate"], header

    asyncio.run(run())


def test_the_credential_is_served() -> None:
    """A request with the right credential is answered."""
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/health", headers={"Authorization": f"Bearer {TEST_TOKEN}"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "healthy"

    asyncio.run(run())


def test_the_workflow_approval_needs_the_credential() -> None:
    """The approval of a workflow is closed without it.

    This is the finding the credential was raised for: the approval is what
    releases a run, so an unauthenticated approval is an unauthenticated
    release.

    With the credential the request reaches the route, and the route
    answers with its own complaint about the body it was given -- which is
    proof that the request got that far.
    """
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            refused = await client.post("/api/v1/workflow/probe/approve", json={})
            _test_the_refusal(refused)

            served = await client.post(
                "/api/v1/workflow/probe/approve",
                json={},
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            assert served.status_code == 422, served.text
            assert served.json() != {"detail": REFUSAL_DETAIL}

    asyncio.run(run())


def test_the_stream_needs_the_credential() -> None:
    """The event stream is closed without it.

    The stream is where a run reports itself, and a run reports its
    sections. It is guarded by the same rule as the rest of the API, and
    it is guarded before the stream is opened: a refusal is an answer in
    its own right, not a stream that says nothing.
    """
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            refused = await client.get("/api/v1/stream/probe")
            _test_the_refusal(refused)

            served = await client.get(
                "/api/v1/stream/probe", headers={"Authorization": f"Bearer {TEST_TOKEN}"}
            )
            assert served.status_code == 404, served.text

    asyncio.run(run())


def test_the_credential_is_required_over_real_http(tmp_path: Path) -> None:
    """The credential is required of a real connection, not only in-process.

    The credential travels in a header, and a header is something a
    connection carries and an in-process call does not. This is the same
    server, reached over a socket, and it refuses the same way.
    """
    with _live_api(tmp_path) as base_url:
        refused = httpx.get(f"{base_url}/api/v1/health", timeout=30.0)
        assert refused.status_code == 401, refused.text
        assert refused.json() == {"detail": REFUSAL_DETAIL}
        assert refused.headers["www-authenticate"] == CHALLENGE

        served = httpx.get(
            f"{base_url}/api/v1/health",
            timeout=30.0,
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
        assert served.status_code == 200, served.text
        assert served.json()["status"] == "healthy"

        refused_stream = httpx.get(f"{base_url}/api/v1/stream/probe", timeout=30.0)
        assert refused_stream.status_code == 401, refused_stream.text
        assert refused_stream.json() == {"detail": REFUSAL_DETAIL}


def _status_of_a_scope(
    app: FastAPI,
    headers: list[tuple[bytes, bytes]],
    *,
    method: str = "GET",
    path: str = "/api/v1/health",
) -> int:
    """The status of one request made with a scope of our own.

    A client lowercases the header names it sends, so the only way to put
    a capitalised name into a scope is to write the scope. That is the
    whole point of this helper: a client would never reproduce the case
    this test is about.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "headers": headers,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
    }
    messages: list[dict[str, Any]] = []
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    return next(
        message["status"]
        for message in messages
        if message["type"] == "http.response.start"
    )


def test_a_header_name_is_case_insensitive() -> None:
    """A header spelled ``Authorization`` is the header.

    A header's name is case-insensitive, and an ASGI scope carries the
    names exactly as the server spelled them. A scope that kept the
    client's capitalisation would hide the header from a lookup that
    compares names as they come, and a caller that spelled the header
    properly would be refused for it.
    """
    app = create_app()

    status = _status_of_a_scope(
        app, [(b"Authorization", f"Bearer {TEST_TOKEN}".encode("latin-1"))]
    )

    assert status == 200


def test_a_preflight_is_recognised_whatever_its_header_is_spelled() -> None:
    """A preflight is recognised however the browser spelled its header.

    The preflight exemption is a bypass, so a header that was not found
    would not fail safe: it would make the browser unable to make the
    request the preflight was about.
    """
    app = create_app()

    status = _status_of_a_scope(
        app,
        [
            (b"Origin", b"http://127.0.0.1:8000"),
            (b"Access-Control-Request-Method", b"GET"),
        ],
        method="OPTIONS",
    )

    assert status == 200


def test_a_request_is_authenticated_before_it_is_authorized() -> None:
    """The credential is checked before the origin rules.

    A foreign origin and a missing credential together are answered with
    the credential's refusal and not with the origin's. The caller is told
    it is not authenticated, and it learns nothing about the origin rules
    from a request it was never authenticated for.

    With the credential the same request is answered by the origin rules
    instead, which is the other half of the same fact: the credential gate
    let it through, and the origin rule is what refused it.
    """
    app = create_app()
    foreign = {"Origin": "https://evil.example"}

    async def run() -> tuple[httpx.Response, httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            refused = await client.post(
                "/api/v1/vault/open", json={"root": "."}, headers=foreign
            )
            authorized = await client.post(
                "/api/v1/vault/open",
                json={"root": "."},
                headers={**foreign, "Authorization": f"Bearer {TEST_TOKEN}"},
            )
            return refused, authorized

    refused, authorized = asyncio.run(run())

    _test_the_refusal(refused)
    assert authorized.status_code == 403


def test_the_documentation_is_not_credentialed() -> None:
    """The API's own documentation is open.

    It describes the API and acts on nothing, and the Swagger UI fetches
    ``openapi.json`` without an ``Authorization`` header, so credentialing
    it would break the page rather than protect anything.
    """
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                response = await client.get(path)
                assert response.status_code == 200, path

    asyncio.run(run())


def test_a_preflight_is_answered_without_a_credential() -> None:
    """A browser's preflight is answered.

    A browser attaches the ``Authorization`` header to the request its
    preflight is about, not to the preflight itself. A preflight that was
    refused would stop the browser from ever making the request it was
    asking about, so the preflight is answered without one.
    """
    app = create_app()

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.options(
                "/api/v1/health",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code == 200, response.text
            assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8000"

    asyncio.run(run())


def test_the_token_is_written_to_the_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The effective token is written to the token file.

    The server and the desktop client are separate processes, and the file
    is how one hands the credential to the other.
    """
    path = tmp_path / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.setenv(API_TOKEN_ENV, "the-operator-s-own-token")

    resolved = resolve_credential()

    assert resolved.token == "the-operator-s-own-token"
    assert resolved.path == path
    assert path.read_text(encoding="utf-8").strip() == "the-operator-s-own-token"


def test_the_token_file_is_reused_across_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token that was made once survives the server being started again.

    Without this the client would be handed a different token every time
    the server was restarted, and the two would never agree.
    """
    path = tmp_path / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)

    first = resolve_credential()
    assert first.source == "generated"

    second = resolve_credential()
    assert second.token == first.token
    assert second.source == "file"


def test_the_token_file_is_replaced_when_the_operator_names_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token the operator names replaces the one in the file.

    The file is how the client is told, so the file has to carry the token
    the server is actually using.
    """
    path = tmp_path / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    generated = resolve_credential()

    monkeypatch.setenv(API_TOKEN_ENV, "a-token-the-operator-chose")
    named = resolve_credential()

    assert named.token == "a-token-the-operator-chose"
    assert named.token != generated.token
    assert path.read_text(encoding="utf-8").strip() == "a-token-the-operator-chose"


def test_the_token_file_is_not_rewritten_when_it_already_holds_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that already holds the token is left alone.

    The write is idempotent, so the file's modification time is evidence
    of when the token last changed and of nothing else.
    """
    path = tmp_path / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.setenv(API_TOKEN_ENV, "a-token-that-does-not-change")
    resolve_credential()
    before = path.stat().st_mtime_ns

    resolve_credential()

    assert path.stat().st_mtime_ns == before


def test_the_token_file_is_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The token file is readable by the operator alone.

    On a platform that has modes, the file is ``0600`` and its directory
    is ``0700``. On a platform that does not, the file inherits the
    operator's own profile, which is that platform's answer to the same
    question.
    """
    path = tmp_path / "private" / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.setenv(API_TOKEN_ENV, "a-token-that-must-stay-private")

    resolve_credential()

    assert path.exists()
    # The operator can read it back: that is what the client does with it.
    assert path.read_text(encoding="utf-8").strip() == "a-token-that-must-stay-private"
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    else:
        # Windows has no modes: the file inherits the operator's profile
        # ACL. What can be checked here is that the file is not marked
        # read-only, which would be the platform's way of saying the
        # opposite -- the owner-write bit is what Windows reports for a
        # file the operator may change.
        assert path.stat().st_mode & 0o200 != 0


def test_a_generated_token_that_cannot_be_written_stops_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generated token that cannot be kept stops the server.

    A generated token that cannot be handed to the client is a server the
    client cannot talk to. A server that refuses to start says so; a
    server that starts and answers 401 for ever does not.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(blocker / "api-token"))
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)

    with pytest.raises(CredentialUnavailableError):
        resolve_credential()


def test_a_named_token_that_cannot_be_written_is_only_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A named token that cannot be kept is a warning, not a failure.

    The operator may be running the client with the same environment
    variable, so the server can serve without the file.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(blocker / "api-token"))
    monkeypatch.setenv(API_TOKEN_ENV, "a-token-the-operator-chose")

    with caplog.at_level(logging.WARNING, logger="lmeditor.server.credential"):
        resolved = resolve_credential()

    assert resolved.token == "a-token-the-operator-chose"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


def test_the_token_never_appears_in_a_response_or_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The token appears nowhere it should not.

    It is never logged, never in a response body and never in a URL. A
    credential in a log is a credential in a file that outlives the
    operator's patience with it; a credential in a URL lands in every
    access log between the client and the server.
    """
    path = tmp_path / "api-token"
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(path))
    monkeypatch.setenv(API_TOKEN_ENV, "a-token-that-must-not-be-seen")
    app = create_app()
    answers: list[httpx.Response] = []

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            answers.append(await client.get("/api/v1/health"))
            answers.append(
                await client.get(
                    "/api/v1/health",
                    headers={"Authorization": "Bearer a-token-that-must-not-be-seen"},
                )
            )

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())

    refused, served = answers
    secret = "a-token-that-must-not-be-seen"
    assert secret not in refused.text
    assert secret not in served.text
    assert str(path) not in refused.text
    for record in caplog.records:
        assert secret not in record.getMessage()
