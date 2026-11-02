"""The path names the thing; the body does not name it again.

A request that puts a document id in the URL *and* in the body has two
sources of truth for one identifier, and they will disagree the first time a
caller is built wrong, a URL is rewritten, or an id is copied from another
request.  The path is the address of the resource, so the path is what counts.

The body therefore does not *require* the id, and when a body carries one that
contradicts the path, the path wins.
"""

from __future__ import annotations

import pytest

from lmeditor.core.schemas import (
    BookSectionEditRequest,
    FactCheckRequest,
    HITLReviewPayload,
    InlineEditRequest,
)
from lmeditor.server.app import create_app
from tests.http_support import sync_client


def test_a_review_decision_does_not_have_to_name_the_document() -> None:
    """The document is named by the path alone."""
    request = HITLReviewPayload(approved=True)
    assert request.doc_id == ""


def test_an_edit_request_does_not_have_to_name_the_document() -> None:
    """The document is named by the path alone."""
    request = InlineEditRequest(
        section_id="sec_1",
        instruction="tighten this",
    )
    assert request.doc_id == "", (
        "the body model must not demand a document id the path already carries"
    )


def test_a_fact_check_request_does_not_have_to_name_the_document() -> None:
    """The document is named by the path alone."""
    request = FactCheckRequest(text="A passage.")
    assert request.doc_id == ""


def test_a_book_edit_request_does_not_have_to_name_the_book_or_the_section() -> None:
    """The book and the section are named by the path alone."""
    request = BookSectionEditRequest(instruction="tighten this")
    assert request.book_id == ""
    assert request.section_id == ""


def test_the_path_wins_over_a_body_that_names_another_document() -> None:
    """A body that contradicts the path cannot redirect the request.

    The route fills the identifier from the path, so a stale or copied id in
    the body is inert: the edit lands on the document the URL names, and the
    refusal for a document that does not exist names *that* document.
    """
    with sync_client(create_app()) as client:
        response = client.post(
            "/api/v1/documents/doc_from_the_path/edits",
            json={
                "doc_id": "doc_from_the_body",
                "section_id": "sec_1",
                "instruction": "tighten this",
                "scope": "paragraph",
                "paragraph_index": 0,
            },
        )

    assert response.status_code == 404, response.text
    assert "doc_from_the_path" in response.json()["detail"], response.text
    assert "doc_from_the_body" not in response.json()["detail"], (
        "the body redirected the request to another document"
    )


def test_the_fact_check_route_reads_the_document_from_the_path() -> None:
    """A body that names another document cannot redirect the check."""
    with sync_client(create_app()) as client:
        response = client.post(
            "/api/v1/documents/doc_from_the_path/factcheck",
            json={
                "doc_id": "doc_from_the_body",
                "text": "A passage.",
                "include_web": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["doc_id"] == "doc_from_the_path", response.text


def test_a_request_without_the_body_id_is_accepted() -> None:
    """A body that omits the id entirely is not a validation error."""
    with sync_client(create_app()) as client:
        response = client.post(
            "/api/v1/documents/doc_that_does_not_exist/factcheck",
            json={"text": "A passage.", "include_web": False},
        )

    assert response.status_code == 200, response.text
    assert response.json()["doc_id"] == "doc_that_does_not_exist"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/documents/doc_that_does_not_exist/edits",
        "/api/v1/library/books/book_that_does_not_exist/sections/sec_1/edits",
    ],
)
def test_a_request_without_the_body_ids_is_accepted(path: str) -> None:
    """Neither the edit nor the book edit demands the id in the body."""
    with sync_client(create_app()) as client:
        response = client.post(
            path,
            json={
                "section_id": "sec_1",
                "instruction": "tighten this",
                "scope": "paragraph",
                "paragraph_index": 0,
            },
        )

    assert response.status_code in (200, 404), response.text


def test_the_path_wins_over_a_body_that_names_another_book() -> None:
    """A body that contradicts the path cannot redirect a book edit."""
    with sync_client(create_app()) as client:
        response = client.post(
            "/api/v1/library/books/book_from_the_path/sections/sec_1/edits",
            json={
                "book_id": "book_from_the_body",
                "section_id": "sec_from_the_body",
                "instruction": "tighten this",
                "scope": "paragraph",
                "paragraph_index": 0,
            },
        )

    assert response.status_code == 404, response.text
    detail = response.json().get("detail", "")
    assert "book_from_the_path" in detail, (
        f"the refusal names {detail!r} instead of the book the path names"
    )
    assert "book_from_the_body" not in detail, (
        f"the refusal names the book the body carried: {detail!r}"
    )
