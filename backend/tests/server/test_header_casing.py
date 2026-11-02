"""The spelling of a header's name.

A header's name is case-insensitive. The scope of an ASGI request carries
the names exactly as the server spelled them, and a server that preserved
the client's capitalisation would hide a header that is really there.

That matters in both directions, and both directions are security-relevant.
A header the server cannot find is a header the server does not act on: a
credential it cannot read is a request it refuses, and an ``Origin`` it
cannot read is a mutation it lets through that it should have refused.

The rules are proved here for the ``Origin`` header, which is the one the
server *refuses* on. The credential's own spelling is proved in
``tests/test_api_credential.py``.

A client cannot reproduce these cases: ``httpx`` lowercases the header
names it sends, as the ASGI specification asks of every server. So the
scopes below are written by hand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lmeditor.server.app import create_app
from lmeditor.server.vault import active_vault_root, close_active_vault
from tests.http_support import auth_headers

#: An origin that is not the API's own.
FOREIGN_ORIGIN = "https://evil.example"

#: The API's own origin, as the scope below is addressed to it.
OWN_ORIGIN = "http://127.0.0.1:8000"


def _status_of_a_scope(
    app: Any,
    headers: list[tuple[bytes, bytes]],
    *,
    method: str = "POST",
    path: str = "/api/v1/vault/open",
) -> int:
    """The status of one request made with a scope of our own.

    The headers are the ones given here -- and no ``Host`` header among
    them is a request with no ``Host`` header -- and the address the
    connection was accepted on is the one the scope names, which is what
    the own-origin rule reads.
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


def _scope_headers(origin: str) -> list[tuple[bytes, bytes]]:
    """The headers of a request from a page on ``origin``.

    The origin's name is spelled with a capital ``O``, which is how a
    browser spells it and how the ASGI specification says it must not be
    carried. The credential is spelled as the suite spells it.
    """
    return [
        (key.encode("latin-1"), value.encode("latin-1"))
        for key, value in auth_headers().items()
    ] + [(b"Origin", origin.encode("latin-1"))]


@pytest.fixture(autouse=True)
def _no_open_vault() -> Any:
    """Start every test with no vault open."""
    close_active_vault()
    yield
    close_active_vault()


def test_a_foreign_origin_is_refused_however_its_header_is_spelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign origin spelled ``Origin`` is still a foreign origin.

    The rule is a refusal, so it fails closed: a header the server cannot
    find would leave the request looking like one from no origin at all,
    and the mutation would be let through.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    status = _status_of_a_scope(create_app(), _scope_headers(FOREIGN_ORIGIN))

    assert status == 403
    assert active_vault_root() is None


def test_the_api_s_own_origin_is_allowed_however_its_header_is_spelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API's own origin, spelled ``Origin``, is still its own origin.

    The other half of the rule: reading the header case-insensitively must
    not make the server refuse a page it serves.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    status = _status_of_a_scope(create_app(), _scope_headers(OWN_ORIGIN))

    assert status != 403
