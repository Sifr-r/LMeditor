"""The gateway's trust boundary: origins, roots, spend, and what an error says.

The gateway is local, but it is not private: a page in the operator's browser
and any other process on the machine can reach it. These tests hold four
properties:

* a page from another origin is answered without a permissive CORS header,
  and it cannot change anything on the server -- the side effect of a
  cross-origin request never happens, because CORS alone never stops it.
  The origin that is the API's own is worked out from the request, not
  from a constant: the API may be served on any port, and a page served
  on another port of the loopback interface is another server's page,
  even though the machine is the same one. That rule is anchored on the
  loopback interface: the request has to have been addressed to a
  loopback name for its origin to count as the API's own, so a page on a
  name that resolves to the API -- a rebinding page -- is a foreign page
  still, however its ``Host`` header and its ``Origin`` agree;
* a folder the server is pointed at is confined to a base the operator
  configured: a system path, a network share and a root outside the base
  are refused, and a read, a write and a delete are all confined by the
  same rule. Every test here that names a folder names the base as well,
  because the server falls back on the operator's home directory when no
  base is configured: where a machine keeps its temporary folders is not
  a fact a confinement test may lean on, so the base is always named and
  the answer is always the base's doing;
* a request that would spend without limit is refused, not trimmed;
* an error that reaches the client says what failed without describing the
  disk the server runs on.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from lmeditor.core.exceptions import LibraryError, LMeditorError, ValidationError
from lmeditor.core.schemas import Source, SourceChunk
from lmeditor.core.types import SourceKind
from lmeditor.server.app import create_app, safe_details
from lmeditor.server.notebooks import close_notebooks_root
from lmeditor.server.roots import refusal_reason
from lmeditor.server.routes import (
    MAX_INLINE_SOURCE_CHARS,
    MAX_INLINE_SOURCE_CHUNKS,
    MAX_REQUEST_RETRIES,
    MAX_REQUEST_TEXT_CHARS,
)
from lmeditor.server.sources import StoredSource, source_library
from lmeditor.server.vault import active_vault_root, close_active_vault

#: An origin that is not the API's own.
FOREIGN_ORIGIN = "https://evil.example"

#: The base URL the API is served on in the tests that name one. It is not
#: port 8000 on purpose: the port the API is served on is not a constant
#: anywhere in the server, and a rule that names one is a rule that can be
#: wrong when the operator serves the API on another port.
API_BASE_URL = "http://127.0.0.1:60804"

#: The origin the API is served from under that base URL.
OWN_ORIGIN = "http://127.0.0.1:60804"

#: The same machine and the same port, spelled the other way round.
SPELLED_ORIGIN = "http://localhost:60804"

#: A page served on another port of the loopback interface: another server,
#: and therefore a foreign page.
OTHER_PORT_ORIGIN = "http://127.0.0.1:8000"

#: The API served under the other spelling of the loopback address. The
#: name is a loopback one, so a page on it is the API's own page.
LOCALHOST_BASE_URL = "http://localhost:60804"

#: A name that is not a loopback name but resolves to the machine the API
#: is served on. A page served from such a name reaches the API with a
#: ``Host`` header and an ``Origin`` of its own, in agreement -- which is
#: the shape of a DNS-rebinding attack.
REBINDING_BASE_URL = "http://attacker.example:60804"

#: The origin that page claims, which is the host it was served from.
REBINDING_ORIGIN = "http://attacker.example:60804"

#: Another address of the same machine, inside ``127.0.0.0/8`` but not the
#: name a browser uses to reach the loopback interface: another server.
OTHER_LOOPBACK_ORIGIN = "http://127.0.0.2:60804"

#: An address of the machine that is not the loopback interface at all:
#: the API reached over the network rather than from the machine itself.
LAN_BASE_URL = "http://192.168.1.10:60804"

#: A path on a machine that is not the caller's business, under a root the
#: server was never told about. The roots a rule knows about are not the
#: roots a machine has, so a path under any other root is a folder of the
#: machine just the same.
POSIX_SECRET_PATH = "/data/secrets/key.pem"

#: The origin of a page served on that address.
LAN_ORIGIN = "http://192.168.1.10:60804"


def _call(
    method: str,
    path: str,
    *,
    base_url: str = "http://test",
    **kwargs: Any,
) -> httpx.Response:
    """Make one request against the application.

    The base URL is the URL the request is addressed to. The API's own
    origin is worked out from the request, so a test that names a base URL
    is a test that says which port the API was served on.
    """
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _call_with_failure(message: str, error: type[Exception]) -> httpx.Response:
    """Make one request against an application that fails on purpose.

    The route is added to a throwaway application, so the failure is a
    failure of the server under test and not a mock of it.
    """
    app = create_app()

    @app.get("/_test/failure")
    async def _failure() -> dict[str, Any]:
        raise error(message)  # type: ignore[call-arg]

    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/_test/failure")

    return asyncio.run(run())


def _call_with_details(error: type[Exception], details: dict[str, Any]) -> httpx.Response:
    """Make one request against an application that fails with these details.

    The route is added to a throwaway application, so the failure is a
    failure of the server under test and not a mock of it.
    """
    app = create_app()

    @app.get("/_test/failure")
    async def _failure() -> dict[str, Any]:
        raise error("the chunker could not read the document", details=details)  # type: ignore[call-arg]

    transport = httpx.ASGITransport(app=app)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/_test/failure")

    return asyncio.run(run())


def _system_folder() -> Path:
    """A folder the operating system keeps for itself."""
    if os.name == "nt":
        return Path(os.environ.get("SystemRoot", "C:/Windows"))
    return Path("/usr")


def _chunk(text: str = "a passage about the subject.") -> SourceChunk:
    """One source chunk the way a caller would send it."""
    return SourceChunk.create(
        document_id="doc_1",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
        metadata={"document_title": "The Source"},
    )


@pytest.fixture(autouse=True)
def _no_open_workspace() -> Iterator[None]:
    """Start every test with no vault and no notebooks folder open."""
    close_active_vault()
    close_notebooks_root()
    yield
    close_active_vault()
    close_notebooks_root()


# ---------------------------------------------------------------------------
# 1. The origin of a request
# ---------------------------------------------------------------------------


def test_a_foreign_origin_is_answered_without_a_permissive_allow_origin() -> None:
    """A page on another origin gets no ``Access-Control-Allow-Origin``.

    The header is what tells a browser whether the page may read the
    answer, so a server that reflects every origin hands every page on the
    network the operator's reach.
    """
    response = _call("GET", "/api/v1/health", headers={"Origin": FOREIGN_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_the_api_s_own_origin_is_answered_with_its_own_origin() -> None:
    """The origin the API is served from is answered with its own origin.

    The API's own origin is not a constant: it is the port the request was
    addressed to. The origin that is answered here is the one the request
    was made to, on that port.
    """
    response = _call(
        "GET",
        "/api/v1/health",
        base_url=API_BASE_URL,
        headers={"Origin": OWN_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == OWN_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


def test_the_api_s_own_origin_spelled_the_other_way_round_is_answered() -> None:
    """The same API on the same port, spelled the other way round.

    ``127.0.0.1`` and ``localhost`` name the same machine, and the port is
    the API's own, so this is the API's own origin too.
    """
    response = _call(
        "GET",
        "/api/v1/health",
        base_url=API_BASE_URL,
        headers={"Origin": SPELLED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == SPELLED_ORIGIN


def test_an_origin_the_operator_named_is_answered_with_its_own_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An origin the operator named in ``LMEDITOR_ALLOWED_ORIGINS`` is answered.

    This is the allowance for a page that really is on another origin. An
    origin is named in full, so the name covers that origin and not
    another one on the same host.
    """
    monkeypatch.setenv("LMEDITOR_ALLOWED_ORIGINS", "http://localhost:5173")

    response = _call("GET", "/api/v1/health", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


def test_a_page_on_another_port_is_answered_without_a_permissive_allow_origin() -> None:
    """A page served on another port of the loopback is a foreign page.

    It is another server: the API is not served on that port, so the
    header that would let the page read this API's answers is not given.
    """
    response = _call(
        "GET",
        "/api/v1/health",
        base_url=API_BASE_URL,
        headers={"Origin": OTHER_PORT_ORIGIN},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_the_browser_s_own_question_is_answered_for_the_api_s_own_origin() -> None:
    """A browser asks what it may do before it does it.

    The question is answered for the API's own origin -- under either
    spelling of the loopback address, because the port is the API's own --
    and refused for a page served on another port, with no origin header
    in the refusal.
    """
    for origin in (OWN_ORIGIN, SPELLED_ORIGIN):
        response = _call(
            "OPTIONS",
            "/api/v1/vault/open",
            base_url=API_BASE_URL,
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )

        assert response.status_code == 200, response.text
        assert response.headers.get("access-control-allow-origin") == origin

    refused = _call(
        "OPTIONS",
        "/api/v1/vault/open",
        base_url=API_BASE_URL,
        headers={
            "Origin": OTHER_PORT_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers


def test_the_desktop_client_is_not_a_browser_request() -> None:
    """The desktop client sends no ``Origin``, so the origin rule does not touch it.

    It is a Compose application talking to the gateway over HTTP, not a page
    in a browser, and it is answered exactly as it was before.
    """
    response = _call("GET", "/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"]


def test_a_foreign_origin_cannot_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-origin request never reaches the side effect.

    CORS decides whether the browser lets the page read the answer; it does
    not stop the request from arriving. The request itself is refused, so
    the vault that was open is still the vault that was open.

    The base is named here and it holds the folder, so the folder below
    would be accepted: what refuses the request is its origin, and nothing
    else.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        headers={"Origin": FOREIGN_ORIGIN},
    )

    assert response.status_code == 403
    assert "origin" in response.json()["detail"].lower()
    assert active_vault_root() is None


def test_a_page_on_another_port_of_the_loopback_cannot_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page served on another port of the loopback cannot change anything.

    The API is served on the port the request was addressed to, and a page
    served on another port of the same machine is another server's page.
    The folder below would be accepted -- the base is named and it holds
    it -- so what refuses the request is the port the page is served on and
    nothing else.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=API_BASE_URL,
        headers={"Origin": OTHER_PORT_ORIGIN},
    )

    assert response.status_code == 403
    assert "origin" in response.json()["detail"].lower()
    assert active_vault_root() is None


def test_a_page_the_api_serves_may_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API's own origin is not a foreign origin.

    The base is named and it holds the folder, so the request is refused
    for its origin alone -- never for the folder it names, and never for
    where the host happens to keep its temporary folders.

    The base is a loopback name, because that is what the own-origin rule
    rests on: the request has to have been addressed to the machine's own
    loopback interface for its origin to count as the API's own. See
    ``test_an_api_reached_at_a_name_that_is_not_a_loopback_name_needs_the_operator_s_allowance``.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=LOCALHOST_BASE_URL,
        headers={"Origin": LOCALHOST_BASE_URL},
    )

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_the_api_s_own_origin_may_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API's own origin may change things, on the port it is served on."""
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=API_BASE_URL,
        headers={"Origin": OWN_ORIGIN},
    )

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_the_same_api_spelled_the_other_way_round_may_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same API on the same port, spelled the other way round.

    ``127.0.0.1`` and ``localhost`` name the same machine, and the port is
    the one the request was addressed to, so this is the API's own origin
    and not a foreign one. A page served on *another* port is refused, and
    that is the rule that keeps the two apart.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=API_BASE_URL,
        headers={"Origin": SPELLED_ORIGIN},
    )

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_an_origin_the_operator_named_may_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page on an origin the operator named may change things."""
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))
    monkeypatch.setenv("LMEDITOR_ALLOWED_ORIGINS", "http://localhost:5173")

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_the_api_served_as_localhost_may_change_anything_from_the_ip_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API served as ``localhost``, called from ``127.0.0.1``.

    The two spellings name the same machine, and the port is the one the
    request was addressed to, so the page is the API's own page whichever
    way round the two are spelled.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=LOCALHOST_BASE_URL,
        headers={"Origin": OWN_ORIGIN},
    )

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_a_page_on_a_name_that_resolves_to_the_api_cannot_change_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page whose name resolves to the API is still a foreign page.

    The name is not a loopback name, so the request was not addressed to
    the machine's own loopback interface: it was addressed to a host that
    happens to point at it. The page's ``Host`` header and its ``Origin``
    agree, and they are still refused -- the folder below would be
    accepted, for the base is named and it holds it -- so what refuses the
    request is the name the request was addressed to, and nothing else.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=REBINDING_BASE_URL,
        headers={"Origin": REBINDING_ORIGIN},
    )

    assert response.status_code == 403
    assert "origin" in response.json()["detail"].lower()
    assert active_vault_root() is None


def test_a_page_on_a_name_that_resolves_to_the_api_cannot_read_the_answer() -> None:
    """The answer of such a page carries no header that lets it read it.

    The request itself is answered -- the server does not pretend to be
    unreachable -- but the header that would let the page read the answer
    is not given, so the page learns nothing of it.
    """
    response = _call(
        "GET",
        "/api/v1/health",
        base_url=REBINDING_BASE_URL,
        headers={"Origin": REBINDING_ORIGIN},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_another_address_of_the_loopback_range_is_not_the_same_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``127.0.0.2`` is the same machine and another server.

    A browser reaches the loopback interface as ``localhost``,
    ``127.0.0.1`` or ``::1``, and by no other name, so a server on
    ``127.0.0.2`` is a server of its own and its page is not a page this
    API serves.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=OTHER_LOOPBACK_ORIGIN,
        headers={"Origin": OTHER_LOOPBACK_ORIGIN},
    )

    assert response.status_code == 403
    assert "origin" in response.json()["detail"].lower()
    assert active_vault_root() is None


def test_a_page_on_another_address_of_the_loopback_range_is_a_foreign_page() -> None:
    """The API on ``127.0.0.1`` is not the API on ``127.0.0.2``.

    The port is the API's own, and the two addresses are both of the one
    machine, but they are two servers, and the page on one of them is not
    a page the other serves.
    """
    response = _call(
        "GET",
        "/api/v1/health",
        base_url=API_BASE_URL,
        headers={"Origin": OTHER_LOOPBACK_ORIGIN},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_an_api_reached_at_a_name_that_is_not_a_loopback_name_needs_the_operator_s_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An API on a network address has no own origin: the operator names it.

    The own-origin rule is the loopback interface's rule, and an API
    reached over the network is not on it. The operator's allowance is an
    exact-origin one, so naming the origin the page is served from is
    what lets that page reach the API, and nothing else does.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    refused = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=LAN_BASE_URL,
        headers={"Origin": LAN_ORIGIN},
    )

    assert refused.status_code == 403
    assert "origin" in refused.json()["detail"].lower()
    assert active_vault_root() is None

    monkeypatch.setenv("LMEDITOR_ALLOWED_ORIGINS", LAN_ORIGIN)

    allowed = _call(
        "POST",
        "/api/v1/vault/open",
        json={"root": str(tmp_path)},
        base_url=LAN_BASE_URL,
        headers={"Origin": LAN_ORIGIN},
    )

    assert allowed.status_code == 200, allowed.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_the_preflight_answer_carries_the_headers_the_browser_needs() -> None:
    """The answer to the browser's own question says what it may do.

    The methods and the lifetime of the answer are pinned here, and so is
    the absence of the header that would let a page send the API's
    credentials: the API has none of its own, and a page that could send
    them would be acting as the operator.
    """
    response = _call(
        "OPTIONS",
        "/api/v1/vault/open",
        base_url=API_BASE_URL,
        headers={
            "Origin": OWN_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == OWN_ORIGIN
    assert response.headers.get("access-control-allow-methods") == (
        "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"
    )
    assert response.headers.get("access-control-max-age") == "600"
    assert response.headers.get("vary") == "Origin"
    assert "access-control-allow-credentials" not in response.headers


# ---------------------------------------------------------------------------
# 2. The folder the server is pointed at
# ---------------------------------------------------------------------------


def test_a_system_path_is_refused_as_a_vault_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder the operating system keeps for itself is not a vault.

    The base is set to that very folder, so the base would accept it: what
    refuses it is the protected location it sits in, and nothing else.
    """
    system_folder = _system_folder()
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(system_folder))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(system_folder)})

    assert response.status_code == 400
    assert "protected system location" in response.json()["detail"]
    assert active_vault_root() is None


def test_the_root_of_a_filesystem_is_refused_as_a_vault_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root of a filesystem holds everything, so it is never a vault.

    The base is set to that very root, so the base would accept it: what
    refuses it is that it is the root of a filesystem, and nothing else.
    """
    filesystem_root = Path(_system_folder().anchor)
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(filesystem_root))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(filesystem_root)})

    assert response.status_code == 400
    assert "root of a filesystem" in response.json()["detail"]
    assert active_vault_root() is None


def test_the_folder_that_holds_the_user_profiles_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder that holds every account is never one operator's vault.

    The home is moved into the temporary folder first, so the folder that
    holds the user profiles is the same on every platform: the temporary
    folder, whose parent is the home that was named.
    """
    home = tmp_path / "a-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(tmp_path)})

    assert response.status_code == 400
    assert "user profiles" in response.json()["detail"]
    assert active_vault_root() is None


def test_the_folder_that_holds_the_user_profiles_is_refused_however_the_home_is_spelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule holds when the home is spelled differently from how it resolves.

    The home is named with a step back into itself, so the folder it
    lives in is spelled one way here and resolves to another. That is the
    same asymmetry a symbolic link makes, and the rule has to hold for it
    as well: the folder that holds every account is still refused.
    """
    profiles = tmp_path / "profiles"
    home = profiles / "bob"
    home.mkdir(parents=True)
    spelled = home / ".." / "bob"
    monkeypatch.setenv("HOME", str(spelled))
    monkeypatch.setenv("USERPROFILE", str(spelled))
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(profiles)})

    assert response.status_code == 400
    assert "user profiles" in response.json()["detail"]
    assert active_vault_root() is None


def test_the_folder_that_holds_the_user_profiles_is_refused_through_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule holds when the folder is reached through a symbolic link.

    The folder that holds the profiles is reached through a link, so the
    path the home is named by and the path that home resolves to are two
    spellings of one folder. Where the machine will not make a link, the
    same asymmetry is made with a step back into the home itself; either
    way the rule fires, and it fires for the profile folder and not for
    some other reason.
    """
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    link = tmp_path / "link"
    linked = True
    try:
        os.symlink(profiles, link, target_is_directory=True)
    except OSError:
        # The machine will not make a link; the same asymmetry is made
        # with a step back into the home itself.
        linked = False
    home = (link if linked else profiles) / "bob"
    home.mkdir()
    spelled = home if linked else home / ".." / "bob"
    monkeypatch.setenv("HOME", str(spelled))
    monkeypatch.setenv("USERPROFILE", str(spelled))
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(profiles)})

    assert response.status_code == 400
    assert "user profiles" in response.json()["detail"]
    assert active_vault_root() is None


def test_a_network_share_is_refused_as_a_vault_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share is refused on its name, before the filesystem is touched.

    Nothing is asked of the server that holds the share, so no credential is
    ever offered for it. The base is a local folder that does not hold the
    share, so what refuses the share is its name and not the base.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))

    response = _call(
        "POST", "/api/v1/vault/open", json={"root": "\\\\127.0.0.1\\admin\\share"}
    )

    assert response.status_code == 400
    assert "network" in response.json()["detail"].lower()
    assert "outside the permitted base" not in response.json()["detail"]
    assert active_vault_root() is None


def test_a_vault_root_outside_the_permitted_base_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base the operator configured is the authority."""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(base))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(outside)})

    assert response.status_code == 400
    assert "outside the permitted base" in response.json()["detail"]
    assert active_vault_root() is None


def test_a_vault_root_inside_the_permitted_base_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator still chooses the folder: the base only says where from."""
    base = tmp_path / "base"
    base.mkdir()
    vault_root = base / "notes"
    vault_root.mkdir()
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(base))

    response = _call("POST", "/api/v1/vault/open", json={"root": str(vault_root)})

    assert response.status_code == 200, response.text
    assert Path(response.json()["root"]) == vault_root.resolve()


def test_a_read_a_write_and_a_delete_are_all_confined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confinement holds every time the vault is used, not only when it opens."""
    base = tmp_path / "base"
    base.mkdir()
    vault_root = base / "vault"
    vault_root.mkdir()
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(base))
    assert _call("POST", "/api/v1/vault/open", json={"root": str(vault_root)}).status_code == 200

    # The base moves under the open vault, so the vault is now outside it.
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(other))

    read = _call("GET", "/api/v1/vault/notes")
    write = _call("PUT", "/api/v1/vault/note", json={"note_id": "note", "content": "# Note\n"})
    delete = _call("DELETE", "/api/v1/vault/note", params={"note_id": "note"})

    assert read.status_code == 400, read.text
    assert write.status_code == 400, write.text
    assert delete.status_code == 400, delete.text
    assert not (vault_root / "note.md").exists()


def test_a_notebooks_root_outside_the_permitted_base_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notebooks folder is confined by the same rule as the vault."""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(base))

    response = _call("POST", "/api/v1/notebooks/open", json={"root": str(outside)})

    assert response.status_code == 400
    assert "outside the permitted base" in response.json()["detail"]


def test_a_notebooks_read_and_write_are_confined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read and a write of the notebooks are confined by the base as well."""
    base = tmp_path / "base"
    base.mkdir()
    notebooks_root = base / "notebooks"
    notebooks_root.mkdir()
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(base))
    assert _call("POST", "/api/v1/notebooks/open", json={"root": str(notebooks_root)}).status_code == 200

    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(other))

    read = _call("GET", "/api/v1/notebooks")
    write = _call("POST", "/api/v1/notebooks", json={"title": "A notebook"})

    assert read.status_code == 400, read.text
    assert write.status_code == 400, write.text
    assert list(notebooks_root.iterdir()) == []


def test_a_folder_that_merely_starts_with_the_base_is_outside_it(tmp_path: Path) -> None:
    """Containment is not a prefix of a string: ``base-other`` is not ``base``."""
    base = tmp_path / "base"
    base.mkdir()
    sibling = tmp_path / "base-other"
    sibling.mkdir()

    assert refusal_reason(sibling.resolve(), base.resolve()) is not None
    assert refusal_reason((base / "notes").resolve(), base.resolve()) is None


def test_a_root_is_judged_by_where_it_points(tmp_path: Path) -> None:
    """The judgement is made on the resolved path, not on the text of it."""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    assert refusal_reason(outside.resolve(), base.resolve()) is not None
    assert refusal_reason((base / "notes").resolve(), base.resolve()) is None


def test_the_named_base_decides_where_a_vault_may_be_not_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vault opens wherever the named base points.

    The server falls back on the operator's home directory when no base is
    configured, so a test that opens a root without naming a base has an
    answer that depends on where the host keeps its temporary folders.
    Here the home is moved out of the way and the base is named, and the
    root is still accepted: the answer is the same on every platform.
    """
    home = tmp_path / "a-home-elsewhere"
    home.mkdir()
    base = tmp_path / "a-base-outside-that-home"
    base.mkdir()
    vault_root = base / "vault"
    vault_root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(base))

    # With the home as the base this root would be refused, so the
    # acceptance below is the base's doing and not the home's.
    assert refusal_reason(vault_root.resolve(), home.resolve()) is not None

    response = _call("POST", "/api/v1/vault/open", json={"root": str(vault_root)})

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(vault_root.resolve())


def test_the_named_base_decides_where_the_notebooks_may_be_not_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notebooks folder is confined by the named base as well.

    The home is moved out of the way here too, so the folder is accepted
    because the base holds it and not because the host's home did.
    """
    home = tmp_path / "a-home-elsewhere"
    home.mkdir()
    base = tmp_path / "a-base-outside-that-home"
    base.mkdir()
    notebooks_root = base / "notebooks"
    notebooks_root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(base))

    response = _call("POST", "/api/v1/notebooks/open", json={"root": str(notebooks_root)})

    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# 3. What one request may spend
# ---------------------------------------------------------------------------


def test_a_generation_request_with_too_many_chunks_is_refused() -> None:
    """More chunks than one run may read is refused, not trimmed."""
    chunks = [_chunk(f"passage number {index}") for index in range(MAX_INLINE_SOURCE_CHUNKS + 1)]

    response = _call(
        "POST",
        "/api/v1/documents/generate",
        json={
            "topic": "The subject",
            "source_chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        },
    )

    assert response.status_code == 422
    assert "chunks" in response.text


def test_a_generation_request_that_asks_for_too_many_retry_loops_is_refused() -> None:
    """More revision loops than one run may buy is refused."""
    response = _call(
        "POST",
        "/api/v1/documents/generate",
        json={
            "topic": "The subject",
            "source_chunks": [_chunk().model_dump(mode="json")],
            "max_retries": MAX_REQUEST_RETRIES + 1,
        },
    )

    assert response.status_code == 422
    assert "max_retries" in response.text


def test_a_question_that_is_too_long_is_refused() -> None:
    """The text of a question goes into a paid prompt, so it is bounded too."""
    response = _call(
        "POST",
        "/api/v1/notebooks/a_notebook/ask",
        json={"question": "x" * (MAX_REQUEST_TEXT_CHARS + 1)},
    )

    assert response.status_code == 413
    assert "characters" in response.json()["detail"]


def test_a_generation_request_with_too_much_text_is_refused() -> None:
    """More source text than one run may read is refused, not trimmed."""
    chunk = _chunk("x" * (MAX_INLINE_SOURCE_CHARS + 1))

    response = _call(
        "POST",
        "/api/v1/documents/generate",
        json={
            "topic": "The subject",
            "source_chunks": [chunk.model_dump(mode="json")],
        },
    )

    assert response.status_code == 422
    assert "characters" in response.text


def test_material_resolved_from_the_library_is_bounded_too() -> None:
    """The bound holds on the material the run will actually read.

    The chunks a request names by id are resolved by the server, so the
    bound is checked again on what came back: a request cannot reach the
    spend by naming a source instead of sending the chunks.
    """
    source_library.clear()
    chunks = [_chunk(f"passage number {index}") for index in range(MAX_INLINE_SOURCE_CHUNKS + 1)]
    source_library.add(
        StoredSource.from_source(
            Source(
                id="src_bounded",
                kind=SourceKind.TEXT,
                origin="memory://src_bounded",
                title="A Large Source",
            ),
            chunks,
        )
    )

    try:
        response = _call(
            "POST",
            "/api/v1/documents/generate",
            json={"topic": "The subject", "source_ids": ["src_bounded"]},
        )
    finally:
        source_library.clear()

    assert response.status_code == 413
    assert "chunks" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 4. What an error says
# ---------------------------------------------------------------------------


def test_an_internal_error_carries_no_filesystem_path() -> None:
    """A failure of the server says what failed, and describes no folder.

    The path is checked against the message as the client parses it. The
    body of the answer is JSON, and JSON escapes the backslash of a
    Windows path, so the text of the body holds the path in its escaped
    spelling and a check made against that text passes whether the path
    is there or not.
    """
    response = _call_with_failure(
        "the tier failed while reading D:\\Users\\victim\\secret\\notes.md "
        "| Details: {path=D:\\Users\\victim\\secret\\notes.md, cause=java.io.IOException}",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "D:\\Users\\victim" not in message
    assert "D:\\Users\\victim\\secret\\notes.md" not in message
    assert "secret" not in response.text
    assert "Details:" not in response.text
    assert "java.io.IOException" not in response.text


def test_an_internal_error_keeps_what_failed() -> None:
    """The useful half of the message survives: what failed is still said."""
    response = _call_with_failure(
        "the tier could not read the manifest of the notebook 'mybook'",
        LMeditorError,
    )

    assert response.status_code == 500
    assert "mybook" in response.json()["message"]


def test_a_validation_error_is_a_400_and_not_a_500() -> None:
    """A value that does not satisfy a constraint is the caller's mistake.

    The path is checked against the message as the client parses it: the
    body of the answer is JSON, and JSON escapes the backslash of a
    Windows path, so a check made against the text of the body passes
    whether the path is there or not.
    """
    response = _call_with_failure(
        "the root is not a folder the server may use: D:\\Users\\victim\\notes",
        ValidationError,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "ValidationError"
    message = response.json()["message"]
    assert "D:\\Users\\victim" not in message
    assert "D:\\Users\\victim\\notes" not in message


def test_the_details_of_an_error_carry_no_filesystem_path() -> None:
    """The details of a failure describe no folder of the machine.

    The details are the client's to read, and a detail that names the
    folder the server failed on is the server's own business.

    The path is checked against the reason as the client parses it: the
    body of the answer is JSON, and JSON escapes the backslash of a
    Windows path, so a check made against the text of the body passes
    whether the path is there or not.
    """
    response = _call_with_details(
        LibraryError,
        {
            "document_id": "doc_1",
            "reason": "the file D:\\Users\\victim\\secret\\notes.md is not text",
        },
    )

    assert response.status_code == 400
    reason = response.json()["details"]["reason"]
    assert "D:\\Users\\victim" not in reason
    assert "D:\\Users\\victim\\secret\\notes.md" not in reason
    assert "notes.md" not in response.text


def test_the_details_of_an_error_carry_no_tier_rendering() -> None:
    """The tier's own rendering of a map of details does not leave the server."""
    response = _call_with_details(
        LibraryError,
        {
            "reason": (
                "the tier failed while reading the manifest "
                "| Details: {path=D:\\Users\\victim\\secret\\notes.md, "
                "cause=java.io.IOException}"
            )
        },
    )

    details = response.json()["details"]
    assert "Details:" not in details["reason"]
    assert "D:\\Users\\victim" not in details["reason"]
    assert "java.io.IOException" not in details["reason"]


def test_the_details_of_an_error_keep_their_keys_and_their_shape() -> None:
    """The keys and the shape are the client's to rely on, and they stay."""
    response = _call_with_details(
        LibraryError,
        {
            "document_id": "doc_1",
            "attempts": 3,
            "sections": [{"id": "s1", "title": "One"}],
        },
    )

    details = response.json()["details"]
    assert list(details) == ["document_id", "attempts", "sections"]
    assert details["document_id"] == "doc_1"
    assert details["attempts"] == 3
    assert details["sections"] == [{"id": "s1", "title": "One"}]


def test_a_detail_that_names_no_folder_is_left_exactly_as_it_was() -> None:
    """A detail that is safe is not tidied on its way out.

    Nothing is collapsed, nothing is trimmed and nothing is reworded: only
    a path and a ``| Details:`` fragment are taken out.
    """
    response = _call_with_details(
        LibraryError,
        {"reason": "  the page   is  not text  "},
    )

    assert response.json()["details"]["reason"] == "  the page   is  not text  "


def test_a_url_in_a_detail_survives_intact() -> None:
    """A URL in a detail is the caller's own, and survives byte-identical.

    The drive-letter shape of a Windows path is not the shape of a URL:
    the ``s://`` of a scheme is not a drive, and the URL names a place on
    the caller's side of the wire and not a folder of the machine.
    """
    url = "https://example.com/paper"

    response = _call_with_details(
        LibraryError,
        {"reason": f"the origin {url} was refused"},
    )

    assert response.json()["details"]["reason"] == f"the origin {url} was refused"


def test_the_details_of_an_error_carry_no_posix_path_under_an_unusual_root() -> None:
    """A path under any root is the server's own, and does not leave it.

    The roots a rule knows about are not the roots a machine has: a folder
    under a root the server was never told about is a folder of the machine
    just the same, and it is taken out of the details as it is taken out of
    the message beside it.
    """
    response = _call_with_details(
        LibraryError,
        {"reason": f"the key {POSIX_SECRET_PATH} could not be read"},
    )

    assert response.status_code == 400
    details = response.json()["details"]
    assert POSIX_SECRET_PATH not in response.text
    assert "/data/secrets" not in response.text
    assert "could not be read" in details["reason"]


def test_the_details_of_an_error_carry_no_class_of_the_jvm() -> None:
    """The class the JVM died with is the machine's own, and does not leave it."""
    response = _call_with_details(
        LibraryError,
        {"reason": "the tier died with java.lang.NullPointerException"},
    )

    assert response.status_code == 400
    assert "java.lang.NullPointerException" not in response.text
    assert "NullPointerException" not in response.text
    assert "the tier died with" in response.json()["details"]["reason"]


def test_the_details_of_an_error_carry_no_stack_frame() -> None:
    """The frames the JVM died in name the layout of the machine's own code."""
    response = _call_with_details(
        LibraryError,
        {"reason": "the tier died at lmeditor.tier.word.Reader.read(Reader.kt:42)"},
    )

    assert response.status_code == 400
    assert "lmeditor.tier.word.Reader" not in response.text
    assert "Reader.kt" not in response.text
    assert "the tier died" in response.json()["details"]["reason"]


def test_a_tier_rendering_that_nests_is_taken_out_whole() -> None:
    """The tier's own rendering is taken out whole, nested map and all.

    The values of the tier's map may themselves be maps, so the rendering
    nests. A rendering taken out only to its innermost brace would leave the
    keys of the outer map behind, and a key is the server's own name for its
    own thing just as much as the value is.
    """
    response = _call_with_failure(
        "the tier failed while reading the manifest | Details: {'a': {'b': 1}}",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "Details:" not in message
    assert "'a'" not in message
    assert "'b'" not in message
    assert "the tier failed while reading the manifest" in message


def test_a_tier_rendering_that_is_never_closed_leaves_the_rest_of_the_message() -> None:
    """A rendering that is never closed is not a rendering.

    Nothing is closed, so there is no fragment to take out, and the words
    after it are the failure's own: taking them with it would take the
    failure away with it.
    """
    response = _call_with_failure(
        "the tier failed while reading the manifest "
        "| Details: {key=value and the rest of the failure",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "the tier failed while reading the manifest" in message
    assert "and the rest of the failure" in message


# ---------------------------------------------------------------------------
# The marker of a rendering that is never closed
# ---------------------------------------------------------------------------


def test_a_closed_rendering_that_nests_a_map_is_taken_out_whole() -> None:
    """A rendering that nests one level is taken out whole.

    The values of the tier's map may themselves be maps, so the rendering
    nests a level of braces, and the whole of it goes: taking out only
    the innermost brace would leave the keys of the outer map behind.
    """
    response = _call_with_failure(
        "the tier failed while reading the manifest "
        "| Details: {path=D:\\Users\\victim\\secret\\notes.md, "
        "cause={class=java.io.IOException}}",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "Details:" not in message
    assert "D:\\Users\\victim" not in message
    assert "java.io" not in message
    assert "the tier failed while reading the manifest" in message


def test_a_rendering_that_is_never_closed_leaves_no_marker_behind() -> None:
    """An unclosed rendering leaves no marker behind it.

    The marker says that a fragment was removed, and nothing was: a reader
    is left looking for a fragment that was never there. The marker goes,
    and the failure's own words after it stay.
    """
    response = _call_with_failure(
        "the tier failed while reading the manifest "
        "| Details: {key=value and the rest of the failure",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "Details:" not in message
    assert "the tier failed while reading the manifest" in message
    assert "and the rest of the failure" in message


def test_a_message_that_names_no_rendering_is_left_exactly_as_it_was() -> None:
    """A message with no rendering in it is not touched at all."""
    message = "the tier could not read the manifest of the notebook 'mybook'"

    response = _call_with_failure(message, LMeditorError)

    assert response.status_code == 500
    assert response.json()["message"] == message


def test_the_text_around_a_tier_rendering_is_untouched() -> None:
    """What the tier rendered is taken out; what the failure said stays."""
    response = _call_with_failure(
        "the tier failed while reading the manifest | Details: {path=C:\\secret} and gave up",
        LMeditorError,
    )

    assert response.status_code == 500
    message = response.json()["message"]
    assert "Details:" not in message
    assert "the tier failed while reading the manifest" in message
    assert "and gave up" in message


def test_a_path_written_with_no_space_after_a_colon_is_taken_out() -> None:
    """A path is a path however it is spelled.

    A colon before the path is the shape of a scheme, but the folder that
    follows it is a folder of the machine all the same, and it is taken
    out.
    """
    response = _call_with_details(
        LibraryError,
        {"reason": "the key path:/data/x could not be read"},
    )

    assert response.status_code == 400
    assert "/data/x" not in response.text
    assert "could not be read" in response.json()["details"]["reason"]


def test_a_root_written_with_no_space_after_a_colon_is_taken_out() -> None:
    """A root is a root however it is spelled, and it goes the same way."""
    response = _call_with_details(
        LibraryError,
        {"reason": "the folder path:/etc could not be read"},
    )

    assert response.status_code == 400
    assert "/etc" not in response.text
    assert "could not be read" in response.json()["details"]["reason"]


def test_the_path_of_a_url_with_a_port_survives_intact() -> None:
    """The path of a URL is the caller's own, and survives byte-identical.

    The port of a URL is a number, so the slash that follows it follows a
    word character -- and a path that follows a word character is not a
    folder of the machine.
    """
    url = "https://example.com:8080/paper"

    response = _call_with_details(
        LibraryError,
        {"reason": f"the origin {url} was refused"},
    )

    assert response.json()["details"]["reason"] == f"the origin {url} was refused"


def test_the_details_of_an_error_that_refer_to_themselves_do_not_raise() -> None:
    """A structure that refers to itself is cut short, not walked for ever.

    The walk happens inside the handler that answers the failure, so a
    structure that never ends would turn a handled failure into a crash
    of its own. The answer is still the answer the caller gets.
    """
    details: dict[str, Any] = {"reason": "the library could not be read"}
    details["itself"] = details

    response = _call_with_details(LibraryError, details)

    assert response.status_code == 400
    assert response.json()["details"]["reason"] == "the library could not be read"


def test_safe_details_cuts_a_structure_that_refers_to_itself() -> None:
    """The walk of the details stops where the structure refers to itself."""
    details: dict[str, Any] = {"reason": "the library could not be read"}
    details["itself"] = details

    result = safe_details(details)

    assert result["reason"] == "the library could not be read"
    assert isinstance(result["itself"], str)


def test_the_reason_a_library_failed_stays_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator keeps the diagnostic the client does not get.

    The answer says what failed and describes no folder of the machine;
    the reason itself -- the path, the tier's own words -- goes to the
    log, where the operator can read it.
    """
    with caplog.at_level(logging.ERROR, logger="lmeditor.server.app"):
        _call_with_failure(
            "the library could not be read at D:\\Users\\victim\\secret\\books.json",
            LibraryError,
        )

    assert "D:\\Users\\victim\\secret\\books.json" in caplog.text


# ---------------------------------------------------------------------------
# 5. The documentation the API serves
# ---------------------------------------------------------------------------


def test_the_documentation_is_served_by_default() -> None:
    """The API's own documentation is served, as it always was."""
    response = _call("GET", "/openapi.json")

    assert response.status_code == 200


def test_the_documentation_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator may take the documentation away without a rebuild."""
    monkeypatch.setenv("LMEDITOR_EXPOSE_DOCS", "off")

    assert _call("GET", "/openapi.json").status_code == 404
    assert _call("GET", "/docs").status_code == 404
    assert _call("GET", "/redoc").status_code == 404
