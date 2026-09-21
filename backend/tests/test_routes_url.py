"""Tests for the JSON ``POST /api/v1/sources/url`` ingestion route."""

from __future__ import annotations

import asyncio

import httpx

from lmeditor.server.app import create_app


def test_url_endpoint_accepts_json_body(monkeypatch) -> None:
    """The URL endpoint accepts a JSON ``SourceIngestRequest`` and dispatches ingestion."""

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            content=b"<html><body><h1>Fetched</h1><p>Body text from the URL.</p></body></html>",
            request=request,
        )

    # Patch the module-level `httpx.get` that ``UrlSourceLoader`` uses so the
    # test never hits the real network.
    monkeypatch.setattr(httpx, "get", fake_get)

    app = create_app()
    transport = httpx.ASGITransport(app=app)

    payload = {
        "source_id": "src_url_api",
        "kind": "url",
        "origin": "https://example.org/paper",
        "title": "Example paper",
    }

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/sources/url", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["source"]["id"] == "src_url_api"
        assert body["source"]["kind"] == "url"
        assert isinstance(body["chunks"], list)
        assert len(body["chunks"]) >= 1

    asyncio.run(run())
