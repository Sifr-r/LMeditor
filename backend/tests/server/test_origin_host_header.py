"""What the own-origin rule does when the ``Host`` header is not there.

The rule compares the origin a page claims with the origin the request
was addressed to. The second of the two is read from the request's URL,
and Starlette's ``URL`` falls back to the address the connection was
accepted on -- the transport address -- when the ``Host`` header is
missing or is not a host name.

So a request with no usable ``Host`` header is not refused outright: the
comparison falls back to the transport address, which is the server's
own and not the caller's to choose. What the rule then makes of such a
request is pinned here, so that a change to it cannot alter it
unnoticed:

* the comparison is made against the transport address, and the rule is
  applied to it as it is applied to any other served origin -- so a
  transport address on the loopback interface with an origin that
  agrees with it is the API's own origin, and a transport address that
  is not on the loopback interface is not;
* a mutating request from a foreign origin is refused with a 403 and the
  change does not happen, whether the ``Host`` header is absent or
  malformed;
* a request from the API's own origin is not refused, because the rule
  refuses a foreign origin and not a missing header.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lmeditor.server.app import create_app
from lmeditor.server.vault import active_vault_root, close_active_vault
from tests.http_support import auth_headers

#: The address the connection was accepted on when the API is served on
#: the loopback interface. It is not port 8000 on purpose: the port the
#: API is served on is not a constant anywhere in the server, and a rule
#: that names one is a rule that can be wrong when the operator serves
#: the API on another port.
LOOPBACK_TRANSPORT: tuple[str, int] = ("127.0.0.1", 60804)

#: The same API reached over the network rather than from the machine
#: itself. The address the connection was accepted on is then not a
#: loopback name, and the API has no own origin.
LAN_TRANSPORT: tuple[str, int] = ("192.168.1.10", 60804)

#: The origin of the API served on the loopback transport.
OWN_ORIGIN = "http://127.0.0.1:60804"

#: The origin of a page served on the address the API was reached at.
LAN_ORIGIN = "http://192.168.1.10:60804"

#: An origin that is not the API's own.
FOREIGN_ORIGIN = "https://evil.example"

#: Headers that are not host names. Starlette's ``URL`` takes the
#: ``Host`` header for the netloc only when it is a host name, so both
#: of these fall back to the transport address, exactly as an absent
#: header does.
MALFORMED_HOSTS: tuple[str, ...] = ("not a host", "127.0.0.1:60804/api")

#: The suite's credential, in the spelling an ASGI scope carries headers
#: in. The header name keeps the capitalisation a client would send, so
#: that the scope is spelled the way a real request is.
_CREDENTIAL_HEADERS: list[tuple[bytes, bytes]] = [
    (key.encode("latin-1"), value.encode("latin-1"))
    for key, value in auth_headers().items()
]


def _call(
    method: str,
    path: str,
    *,
    headers: list[tuple[bytes, bytes]],
    transport: tuple[str, int] = LOOPBACK_TRANSPORT,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    """Make one request against the application, with a scope of our own.

    A client fills the ``Host`` header in from the URL it is talking
    to, so a request without one cannot be made through a client. The
    request is therefore made against the ASGI application directly:
    the headers are the ones given here -- and no ``Host`` header among
    them is a request with no ``Host`` header -- and ``transport`` is
    the address the connection was accepted on, which is what the
    own-origin rule falls back to when there is no ``Host`` header to
    read.

    Every route under ``/api/`` refuses a request that carries no
    credential, so the scope carries the suite's own. The request here
    is about the ``Host`` header and not about the credential, and one
    answered with a refusal would say nothing about the origin rule.
    """
    app = create_app()
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "headers": [*_CREDENTIAL_HEADERS, *headers],
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "server": transport,
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
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    status = 0
    response_headers: dict[str, str] = {}
    response_body = b""
    for message in messages:
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            response_body += message.get("body", b"")
    return status, response_headers, response_body


@pytest.fixture(autouse=True)
def _no_open_vault() -> Iterator[None]:
    """Start every test with no vault open."""
    close_active_vault()
    yield
    close_active_vault()


def test_a_request_with_no_host_header_is_compared_with_the_transport_address() -> None:
    """With no ``Host`` header, the transport address is what is compared.

    The rule is not refused for want of a header: the host is taken
    from the request's URL, and Starlette's ``URL`` falls back to the
    address the connection was accepted on when the header is missing.
    The transport address on the loopback interface is the API's own
    address, so an origin that agrees with it is the API's own origin
    and is answered with itself.
    """
    status, headers, _ = _call(
        "GET",
        "/api/v1/health",
        headers=[(b"origin", OWN_ORIGIN.encode())],
        transport=LOOPBACK_TRANSPORT,
    )

    assert status == 200
    assert headers.get("access-control-allow-origin") == OWN_ORIGIN


def test_a_request_with_no_host_header_to_an_api_off_the_loopback_is_not_its_own_origin() -> None:
    """The fallback gives the API no own origin where it has none.

    The address the connection was accepted on is the server's own and
    not the caller's to choose, so the rule may fall back to it; but it
    is still the rule of the loopback interface, and an address that is
    not a loopback name is not on it.
    """
    status, headers, _ = _call(
        "GET",
        "/api/v1/health",
        headers=[(b"origin", LAN_ORIGIN.encode())],
        transport=LAN_TRANSPORT,
    )

    assert status == 200
    assert "access-control-allow-origin" not in headers
    assert "access-control-allow-credentials" not in headers


def test_a_request_with_a_malformed_host_header_is_compared_with_the_transport_address() -> None:
    """A ``Host`` header that is not a host name falls back as well.

    Starlette's ``URL`` takes the header for the netloc only when it is
    a host name; when it is not, the transport address is used in its
    place, exactly as it is when the header is missing.
    """
    for malformed in MALFORMED_HOSTS:
        status, headers, _ = _call(
            "GET",
            "/api/v1/health",
            headers=[
                (b"host", malformed.encode()),
                (b"origin", OWN_ORIGIN.encode()),
            ],
            transport=LOOPBACK_TRANSPORT,
        )

        assert status == 200
        assert headers.get("access-control-allow-origin") == OWN_ORIGIN


def test_a_foreign_origin_cannot_change_anything_without_a_host_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutating request from a foreign origin is refused, header or no header.

    The folder below would be accepted -- the base is named and it holds
    it -- so what refuses the request is the origin it comes from, and
    nothing else. A missing ``Host`` header does not make the rule
    kinder: the origin is still a foreign one, and the change still
    does not happen.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    status, _, body = _call(
        "POST",
        "/api/v1/vault/open",
        headers=[
            (b"origin", FOREIGN_ORIGIN.encode()),
            (b"content-type", b"application/json"),
        ],
        transport=LOOPBACK_TRANSPORT,
        body=json.dumps({"root": str(tmp_path)}).encode(),
    )

    assert status == 403
    assert "origin" in json.loads(body)["detail"].lower()
    assert active_vault_root() is None


def test_a_foreign_origin_cannot_change_anything_with_a_malformed_host_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same, with a ``Host`` header that is not a host name.

    A malformed header falls back to the transport address, so it names
    no foreign host and buys the request nothing: the origin is still a
    foreign one, and the change still does not happen.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    for malformed in MALFORMED_HOSTS:
        status, _, body = _call(
            "POST",
            "/api/v1/vault/open",
            headers=[
                (b"host", malformed.encode()),
                (b"origin", FOREIGN_ORIGIN.encode()),
                (b"content-type", b"application/json"),
            ],
            transport=LOOPBACK_TRANSPORT,
            body=json.dumps({"root": str(tmp_path)}).encode(),
        )

        assert status == 403
        assert "origin" in json.loads(body)["detail"].lower()
        assert active_vault_root() is None


def test_the_api_s_own_origin_may_change_anything_without_a_host_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule refuses a foreign origin; it does not refuse a missing header.

    The origin is the API's own -- the transport address is the address
    the API is served on, and the origin agrees with it -- so the
    request is answered and the change happens. A rule that refused
    every request whose ``Host`` header was missing would refuse this
    one too, and it would not be describing what the server does.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    status, _, body = _call(
        "POST",
        "/api/v1/vault/open",
        headers=[
            (b"origin", OWN_ORIGIN.encode()),
            (b"content-type", b"application/json"),
        ],
        transport=LOOPBACK_TRANSPORT,
        body=json.dumps({"root": str(tmp_path)}).encode(),
    )

    assert status == 200, body
    assert active_vault_root() == str(tmp_path.resolve())
