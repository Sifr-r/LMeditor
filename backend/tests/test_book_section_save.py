"""Tests for saving a book section by hand, and for the index keeping up.

A book is edited one section at a time, and the operator's own hand has to be
able to do that editing: the instruction box asks the machine to rewrite, and
this is the operator's own text.

The second half of this file pins the part that is easy to get wrong. A book's
retrieval index is the "cached important info" of the objective, and it is
built from the book's corpus of passages. When a section's text changes, the
passages built from the *old* text have to go: an index that still serves a
paragraph the operator deleted is not a cache, it is a lie.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

import httpx
import pytest

from lmeditor.server.app import create_app
from lmeditor.server.library import (
    book_index,
    book_passages,
    book_store,
    invalidate_book_index,
    reset_book_store,
)


def _client() -> httpx.AsyncClient:
    """An HTTP client bound to the application under test."""
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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


def _create_book(book_id: str) -> dict[str, Any]:
    """The body that creates a two-section book."""
    return {
        "book_id": book_id,
        "title": "A Book Being Edited",
        "sections": [
            {
                "section_id": "sec_1",
                "title": "Chapter One",
                "content": "The first chapter body, which the operator will rewrite.",
            },
            {
                "section_id": "sec_2",
                "title": "Chapter Two",
                "content": "The second chapter body, which the operator will not touch.",
            },
        ],
    }


def test_saving_a_book_section_stores_the_operator_text(library_dir: Any) -> None:
    """The operator's words are stored in the book, not only in the panel."""
    book_id = "book_save_text"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        return await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )

    response = _run(run)
    assert response.status_code == 200
    body = response.json()
    assert body["section_id"] == "sec_1"
    assert body["content"] == "A paragraph the operator wrote themselves."
    assert body["chunk_count"] >= 1
    assert body["word_count"] > 0


def test_the_saved_section_is_what_the_library_reads_back(library_dir: Any) -> None:
    """Reading the section back returns the operator's text, not the draft's."""
    book_id = "book_save_read_back"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )
        return await client.get(f"/api/v1/library/books/{book_id}/sections/sec_1")

    response = _run(run)
    assert response.status_code == 200
    assert response.json()["content"] == "A paragraph the operator wrote themselves."


def test_the_index_drops_the_passages_the_operator_deleted(library_dir: Any) -> None:
    """A saved section must not keep serving the text it no longer contains.

    The book's corpus is built once and cached as the retrieval index. Adding
    the new passages is not enough: the old ones are built from text that is
    no longer in the book, and a passage that is still retrievable is a
    passage the operator believes they deleted.
    """
    book_id = "book_save_index"

    async def run(client: httpx.AsyncClient) -> tuple[set[str], set[str], dict[str, Any]]:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        store = book_store()
        manifest = store.open(book_id)
        before = {
            chunk_id
            for section in manifest.sections
            for chunk_id in section.chunk_ids
        }
        assert before, "the book was indexed with no passages at all"

        await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )
        manifest = store.open(book_id)
        after = {
            chunk_id
            for section in manifest.sections
            for chunk_id in section.chunk_ids
        }
        section = next(
            entry for entry in manifest.sections if entry.section_id == "sec_1"
        )
        return before, after, section.model_dump(mode="json")

    before, after, section = _run(run)

    # The passages built from the old text are no longer referenced by any
    # section, so nothing points at them and nothing can retrieve them.
    assert before - after, "the deleted text is still referenced by the book"
    # And the section's own index entry points at the passages that are there.
    assert set(section["chunk_ids"]) <= after
    assert section["chunk_ids"], "the section is indexed as no passages at all"


def test_the_retrieval_index_does_not_serve_the_deleted_text(library_dir: Any) -> None:
    """The index a rewrite is grounded in must not carry the deleted text.

    The corpus file keeps a passage after its section is edited, so that two
    sections carrying the same text share one. The index is built through the
    manifest, which is what keeps the inert passages out of it.
    """
    book_id = "book_save_index_serving"

    async def run(client: httpx.AsyncClient) -> tuple[set[str], set[str]]:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        store = book_store()
        before = {chunk.chunk_id for chunk in book_passages(store, book_id)}
        assert before

        await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )
        invalidate_book_index(book_id)
        index = book_index(book_id, store)
        indexed = set(index.chunk_ids) if index is not None else set()
        after = {chunk.chunk_id for chunk in book_passages(store, book_id)}
        return before, indexed | after

    before, served = _run(run)
    assert before - served, "the index still carries the deleted text"


def test_editing_one_section_leaves_the_others_alone(library_dir: Any) -> None:
    """A book is edited one section at a time; the rest of it must not move."""
    book_id = "book_save_isolated"

    async def run(client: httpx.AsyncClient) -> tuple[httpx.Response, httpx.Response]:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )
        return (
            await client.get(f"/api/v1/library/books/{book_id}/sections/sec_1"),
            await client.get(f"/api/v1/library/books/{book_id}/sections/sec_2"),
        )

    first, second = _run(run)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["content"] == "A paragraph the operator wrote themselves."
    assert second.json()["content"] == (
        "The second chapter body, which the operator will not touch."
    )


def test_the_book_export_carries_the_operator_text(library_dir: Any) -> None:
    """What the operator wrote is what the exported file contains."""
    book_id = "book_save_export"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=_create_book(book_id))
        await client.put(
            f"/api/v1/library/books/{book_id}/sections/sec_1",
            json={"content": "A paragraph the operator wrote themselves."},
        )
        return await client.get(f"/api/v1/library/books/{book_id}/export.docx")

    response = _run(run)
    assert response.status_code == 200
    package = zipfile.ZipFile(io.BytesIO(response.content))
    document_xml = package.read("word/document.xml").decode("utf-8")
    assert "A paragraph the operator wrote themselves." in document_xml
    assert "which the operator will rewrite" not in document_xml


def test_saving_an_unknown_section_is_a_404(library_dir: Any) -> None:
    """A section the book does not hold cannot be saved."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=_create_book("book_save_missing"))
        return await client.put(
            "/api/v1/library/books/book_save_missing/sections/sec_nope",
            json={"content": "Text."},
        )

    response = _run(run)
    assert response.status_code == 404


def test_saving_into_an_unknown_book_is_a_404(library_dir: Any) -> None:
    """A book the library does not hold cannot be saved into."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.put(
            "/api/v1/library/books/no_such_book/sections/sec_1",
            json={"content": "Text."},
        )

    response = _run(run)
    assert response.status_code == 404


def test_a_save_that_names_an_unknown_field_is_refused(library_dir: Any) -> None:
    """The server's model forbids fields it does not understand."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await client.post("/api/v1/library/books", json=_create_book("book_save_extra"))
        return await client.put(
            "/api/v1/library/books/book_save_extra/sections/sec_1",
            json={"content": "Text.", "title": "not allowed"},
        )

    response = _run(run)
    assert response.status_code == 422
