"""The credential the test suite presents to the API.

Every route under ``/api/`` requires the API's bearer credential, so a test
that talks to the app has to present it. The token here is the one the
suite's own fixture puts in the environment, so the two cannot drift apart:
:mod:`tests.http_support` and the fixture in :mod:`tests.conftest` name the
same value, and the client factories below present it on every request.

The helpers exist so that the credential is presented the same way
everywhere. A test that built its own client without the header would be
answering 401 for a reason that has nothing to do with what it is testing,
and a test that grew its own way of presenting the token would be a second
place for the contract to live.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lmeditor.server.app import create_app

#: The token the suite presents.
#:
#: It is a fixed value and not a random one, so that a failure is
#: reproducible and so that a test can name it when it means to present the
#: wrong one. It is a test's token and is not a secret: it is written into a
#: temporary file that no other process reads.
TEST_TOKEN = "lmeditor-suite-token-4d2b9e7c1a6f8053"

#: The header that carries the token.
AUTH_HEADERS: dict[str, str] = {"Authorization": f"Bearer {TEST_TOKEN}"}


def auth_headers() -> dict[str, str]:
    """The header that carries the suite's credential."""
    return dict(AUTH_HEADERS)


def test_app() -> FastAPI:
    """An application that is served with the suite's credential."""
    return create_app()


def async_client(
    app: FastAPI,
    *,
    base_url: str = "http://test",
) -> httpx.AsyncClient:
    """An async client that presents the suite's credential on every request.

    The credential is a default header rather than something each call has
    to pass, so that a test's own requests read as what they are testing
    and not as a test of the credential.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base_url,
        headers=AUTH_HEADERS,
    )


def sync_client(app: FastAPI) -> TestClient:
    """A sync client that presents the suite's credential on every request."""
    return TestClient(app, headers=AUTH_HEADERS)
