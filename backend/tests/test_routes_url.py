"""Tests for the JSON ``POST /api/v1/sources/url`` ingestion route.

The route's own rules belong to this side of the seam: the body it accepts,
the kind it insists on, the origin it vets *before* anything is fetched, and
the fact that the fetch runs off the event loop.

The fetch itself is the Kotlin tier's now, and the tier's own tests prove it
there (``kotlin/ingest/src/test/kotlin/lmeditor/ingest/LoadersTest.kt`` and
``HttpSafetyTest.kt``). A Python test can no longer stub the transport: the
request is issued in the JVM.

What this file therefore stubs is the *route's own dispatch* -- the same seam
``tests/test_sse_fanout.py`` uses for the upload path -- and asserts that the
endpoint accepts the body, vets the origin and hands the right fields to the
ingestion pipeline. The origin it names is a literal public address, so the
vetting needs no resolver and the assertions do not depend on a network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from lmeditor.server import routes
from lmeditor.server.app import create_app
from lmeditor.server.routes import SourceIngestResponse
from tests.http_support import async_client

PUBLIC_ORIGIN = "https://93.184.216.34/paper"


def _stub_dispatch(monkeypatch: Any) -> dict[str, Any]:
    """Replace the route's dispatch and record what it was asked to do."""
    captured: dict[str, Any] = {}

    def fake_ingest_source(**kwargs: Any) -> SourceIngestResponse:
        captured.update(kwargs)
        return SourceIngestResponse(
            source={
                "id": kwargs["source_id"],
                "kind": kwargs["kind"],
                "origin": kwargs["origin"],
                "title": kwargs["title"],
            },
            chunks=[
                {
                    "chunk_id": "0" * 64,
                    "document_id": kwargs["source_id"],
                    "text": "Body text from the URL.",
                    "metadata": {},
                }
            ],
        )

    monkeypatch.setattr(routes, "_ingest_source", fake_ingest_source)
    return captured


def _post(payload: dict[str, Any]) -> httpx.Response:
    app = create_app()

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.post("/api/v1/sources/url", json=payload)

    return asyncio.run(run())


def test_url_endpoint_accepts_json_body(monkeypatch) -> None:
    """The URL endpoint accepts a JSON ``SourceIngestRequest`` and dispatches ingestion."""
    captured = _stub_dispatch(monkeypatch)

    payload = {
        "source_id": "src_url_api",
        "kind": "url",
        "origin": PUBLIC_ORIGIN,
        "title": "Example paper",
    }
    response = _post(payload)

    assert response.status_code == 202
    body = response.json()
    assert body["source"]["id"] == "src_url_api"
    assert body["source"]["kind"] == "url"
    assert isinstance(body["chunks"], list)
    assert len(body["chunks"]) >= 1

    # The fields the route hands to the pipeline are the ones the body named.
    assert captured["source_id"] == "src_url_api"
    assert captured["kind"] == "url"
    assert captured["origin"] == PUBLIC_ORIGIN
    assert captured["title"] == "Example paper"
    assert captured["doi"] is None
    assert captured["arxiv_id"] is None
    assert captured["pmid"] is None


def test_url_endpoint_passes_the_identifiers_the_body_names(monkeypatch) -> None:
    """A DOI, an arXiv id and a PMID travel from the body to the pipeline."""
    captured = _stub_dispatch(monkeypatch)

    payload = {
        "source_id": "src_url_ids",
        "kind": "url",
        "origin": PUBLIC_ORIGIN,
        "title": "Example paper",
        "doi": "10.1234/abc",
        "arxiv_id": "2101.12345v2",
        "pmid": "12345678",
    }
    response = _post(payload)

    assert response.status_code == 202
    assert captured["doi"] == "10.1234/abc"
    assert captured["arxiv_id"] == "2101.12345v2"
    assert captured["pmid"] == "12345678"


def test_url_endpoint_refuses_a_kind_that_is_not_a_url(monkeypatch) -> None:
    """The endpoint only accepts ``kind=url``."""
    _stub_dispatch(monkeypatch)

    response = _post(
        {
            "source_id": "src_url_api",
            "kind": "text",
            "origin": PUBLIC_ORIGIN,
            "title": "Example paper",
        }
    )

    assert response.status_code == 400
    assert "only accepts kind='url'" in response.json()["detail"]


def test_url_endpoint_refuses_an_internal_origin(monkeypatch) -> None:
    """An origin that is not a public http(s) address is refused before any fetch."""
    captured = _stub_dispatch(monkeypatch)

    response = _post(
        {
            "source_id": "src_url_api",
            "kind": "url",
            "origin": "http://169.254.169.254/latest/meta-data/",
            "title": "Example paper",
        }
    )

    assert response.status_code == 400
    assert "Rejected origin" in response.json()["detail"]
    # Nothing was dispatched: the origin is vetted first.
    assert captured == {}


def test_url_endpoint_refuses_an_unsafe_source_id(monkeypatch) -> None:
    """A source id that could escape the upload directory is refused."""
    _stub_dispatch(monkeypatch)

    response = _post(
        {
            "source_id": "../../evil",
            "kind": "url",
            "origin": PUBLIC_ORIGIN,
            "title": "Example paper",
        }
    )

    assert response.status_code == 400
    assert "Invalid source_id" in response.json()["detail"]
