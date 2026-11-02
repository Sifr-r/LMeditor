"""Tests for the targeted-editing, fact-checking and book-library routes.

These are the HTTP surface the three new domains are reached through, so they
test the contract the client depends on: the routes exist, they answer with
the documented shapes, and they refuse what they cannot support instead of
returning a reassuring but unfounded answer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from lmeditor.server.app import create_app
from lmeditor.server.library import reset_book_store
from tests.http_support import async_client


def _client() -> httpx.AsyncClient:
    """An HTTP client bound to the application under test."""
    app = create_app()
    return async_client(app)


def _run(coro: Any) -> Any:
    """Run one coroutine to completion."""

    async def runner() -> Any:
        async with _client() as client:
            return await coro(client)

    return asyncio.run(runner())


@pytest.fixture()
def library_dir(tmp_path, monkeypatch) -> Any:
    """Point the book library at a temporary directory."""
    root = tmp_path / "books"
    monkeypatch.setenv("LMEDITOR_LIBRARY_DIR", str(root))
    reset_book_store()
    yield root
    reset_book_store()


def test_create_book_returns_a_manifest_without_body_text(library_dir: Any) -> None:
    """A created book is described by its manifest, and the manifest has no body."""
    body = {
        "book_id": "book_route_1",
        "title": "A Small Book",
        "sections": [
            {"section_id": "sec_1", "title": "One", "content": "The first section body."},
            {"section_id": "sec_2", "title": "Two", "content": "The second section body."},
        ],
    }

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/v1/library/books", json=body)

    response = _run(run)
    assert response.status_code == 201
    manifest = response.json()
    assert manifest["book_id"] == "book_route_1"
    assert manifest["title"] == "A Small Book"
    assert manifest["section_count"] == 2
    assert [section["section_id"] for section in manifest["sections"]] == ["sec_1", "sec_2"]
    # The manifest is an index, not a copy of the book.
    assert "The first section body." not in response.text


def test_book_routes_read_one_section_at_a_time(library_dir: Any) -> None:
    """Listing, reading a section and reading a section that does not exist."""
    create = {
        "book_id": "book_route_2",
        "title": "Navigable",
        "sections": [
            {"section_id": "sec_a", "title": "A", "content": "Body of A."},
            {"section_id": "sec_b", "title": "B", "content": "Body of B."},
        ],
    }

    async def run(client: httpx.AsyncClient) -> tuple[Any, ...]:
        created = await client.post("/api/v1/library/books", json=create)
        listed = await client.get("/api/v1/library/books")
        manifest = await client.get("/api/v1/library/books/book_route_2")
        section = await client.get("/api/v1/library/books/book_route_2/sections/sec_a")
        missing = await client.get("/api/v1/library/books/book_route_2/sections/nope")
        unknown = await client.get("/api/v1/library/books/does_not_exist")
        return created, listed, manifest, section, missing, unknown

    created, listed, manifest, section, missing, unknown = _run(run)

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert manifest.status_code == 200
    assert manifest.json()["section_count"] == 2
    assert section.status_code == 200
    assert section.json()["content"] == "Body of A."
    assert section.json()["title"] == "A"
    assert missing.status_code == 404
    assert unknown.status_code == 404


def test_working_set_is_bounded_and_reports_its_size(library_dir: Any) -> None:
    """The working set holds the target section and its neighbours, and says how big it is."""
    sections = [
        {"section_id": f"sec_{index:03d}", "title": f"S{index}", "content": f"Body number {index}."}
        for index in range(30)
    ]

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post(
            "/api/v1/library/books",
            json={"book_id": "book_route_3", "title": "Long", "sections": sections},
        )
        return await client.get(
            "/api/v1/library/books/book_route_3/working-set/sec_015",
            params={"neighbours": 1, "max_passages": 4},
        )

    response = _run(run)
    assert response.status_code == 200
    working_set = response.json()
    assert working_set["target_section_id"] == "sec_015"
    loaded = [section["section_id"] for section in working_set["sections"]]
    assert loaded == ["sec_014", "sec_015", "sec_016"]
    assert working_set["context_chars"] > 0
    # The whole book was thirty sections; the working set is three of them.
    assert len(loaded) < len(sections)


def test_working_set_for_an_unknown_section_is_a_404(library_dir: Any) -> None:
    """A working set for a section that is not there is refused."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post(
            "/api/v1/library/books",
            json={
                "book_id": "book_route_4",
                "title": "Small",
                "sections": [{"section_id": "sec_1", "title": "One", "content": "Body."}],
            },
        )
        return await client.get("/api/v1/library/books/book_route_4/working-set/missing")

    response = _run(run)
    assert response.status_code == 404


def test_unsafe_section_identifier_is_refused(library_dir: Any) -> None:
    """A section id that tries to escape the book directory is a client error."""
    body = {
        "book_id": "book_route_5",
        "title": "Guarded",
        "sections": [{"section_id": "../escape", "title": "Bad", "content": "Body."}],
    }

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post("/api/v1/library/books", json=body)

    response = _run(run)
    # The identifier never reaches the filesystem.
    assert response.status_code in {400, 422}
    assert not (library_dir / "escape").exists()


def test_a_book_exports_as_a_word_document(library_dir: Any) -> None:
    """A book can be handed back: the export is a real ``.docx`` package.

    Without this the work would be trapped in the library — a book could be
    edited but never given to the operator.
    """
    import io
    import zipfile

    create = {
        "book_id": "book_export_1",
        "title": "An Exportable Book",
        "sections": [
            {
                "section_id": "sec_1",
                "title": "Chapter One",
                "content": "## A heading\n\nSome **bold** prose.\n\n- A bullet",
            },
            {"section_id": "sec_2", "title": "Chapter Two", "content": "More prose."},
        ],
    }

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=create)
        return await client.get("/api/v1/library/books/book_export_1/export.docx")

    response = _run(run)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "book_export_1.docx" in response.headers["content-disposition"]

    package = zipfile.ZipFile(io.BytesIO(response.content))
    document_xml = package.read("word/document.xml").decode("utf-8")
    # The book's structure survived the export.
    assert "Chapter One" in document_xml
    assert "Chapter Two" in document_xml
    assert "A heading" in document_xml
    assert "word/document.xml" in package.namelist()


def test_exporting_an_unknown_book_is_a_404(library_dir: Any) -> None:
    """A book that is not in the library has nothing to export."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.get("/api/v1/library/books/no_such_book/export.docx")

    response = _run(run)
    assert response.status_code == 404


def test_editing_an_unknown_document_is_a_404() -> None:
    """An edit against a document the server does not hold is refused."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            "/api/v1/documents/doc_that_does_not_exist/edits",
            json={
                "doc_id": "doc_that_does_not_exist",
                "section_id": "sec_1",
                "instruction": "tighten this",
                "scope": "paragraph",
                "paragraph_index": 0,
            },
        )

    response = _run(run)
    assert response.status_code == 404


def test_fact_checking_a_passage_with_no_corpus_reports_it_unsupported() -> None:
    """A passage checked against a document the server does not hold is honest.

    The check is not refused: a passage can be pasted in and checked against
    the web. But with no corpus behind it the verdict must be *unsupported*,
    never supported, and no evidence may be invented to make it look better.
    """

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            "/api/v1/documents/doc_that_does_not_exist/factcheck",
            json={
                "doc_id": "doc_that_does_not_exist",
                "text": "Retrieval quality drives answer quality.",
                "include_web": False,
            },
        )

    response = _run(run)
    assert response.status_code == 200
    report = response.json()
    assert report["verdict"] == "unsupported"
    assert report["web_searched"] is False
    assert report["claims"], "the passage's claim must be reported, not dropped"
    for claim in report["claims"]:
        assert claim["status"] == "ungrounded"
        assert claim["evidence"] == []
    assert "No evidence" in report["summary"]
