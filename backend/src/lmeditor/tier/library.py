"""The book library rules, as the Kotlin tier implements them.

The on-disk store, the bounded digest of a book, and the bounded slice of a
book that ever reaches the model.

The store's root is a parameter of every call, not a setting of the process:
a caller that opens a book in its own directory must get *that* book. A root
left as ``None`` means the request names no root, and the tier's configured
root -- ``$LMEDITOR_LIBRARY_DIR``, or ``<tempdir>/lmeditor_books`` -- applies.
"""

from __future__ import annotations

from typing import Any

from lmeditor.bridge.client import decode_bytes, encode_bytes
from lmeditor.core.schemas import BookManifest, SourceChunk, WorkingSet
from lmeditor.tier.bridge import tier_bridge


def _root(root: str | None) -> dict[str, Any]:
    """The root a request names, when it names one."""
    return {"root": root} if root is not None else {}


def import_book(
    book_id: str,
    title: str = "",
    path: str | None = None,
    data: bytes | None = None,
    root: str | None = None,
) -> BookManifest:
    """A Word document becomes a book of editable sections."""
    params: dict[str, Any] = {"book_id": book_id, "title": title, **_root(root)}
    if path is not None:
        params["path"] = path
    if data is not None:
        params["bytes_b64"] = encode_bytes(data)
    result = tier_bridge().call("library.import_book", params)
    return BookManifest.model_validate(result["manifest"])


def get_manifest(book_id: str, root: str | None = None) -> BookManifest:
    """The index of a book: enough to navigate it without loading it."""
    result = tier_bridge().call(
        "library.get_manifest",
        {"book_id": book_id, **_root(root)},
    )
    return BookManifest.model_validate(result["manifest"])


def get_section(
    book_id: str,
    section_id: str,
    root: str | None = None,
) -> dict[str, Any]:
    """One section of a book, with its text."""
    return tier_bridge().call(
        "library.get_section",
        {"book_id": book_id, "section_id": section_id, **_root(root)},
    )


def save_section(
    book_id: str,
    section_id: str,
    content: str,
    root: str | None = None,
) -> dict[str, Any]:
    """Store the operator's own text for one section of a book."""
    result = tier_bridge().call(
        "library.save_section",
        {
            "book_id": book_id,
            "section_id": section_id,
            "content": content,
            **_root(root),
        },
    )
    saved = result.get("result")
    if isinstance(saved, dict):
        return saved
    return {key: value for key, value in result.items() if key != "result"}


def build_working_set(
    book_id: str,
    section_id: str,
    neighbours: int = 1,
    max_passages: int = 8,
    max_brief_chars: int = 2000,
    max_section_chars: int = 4000,
    ranked_passages: list[SourceChunk] | None = None,
    root: str | None = None,
) -> WorkingSet:
    """The bounded slice of the book that reaches the model.

    The retrieval is model-dependent and stays in Python: the passages it
    ranked travel with the request, and the tier assembles the bounded set
    around them.
    """
    params: dict[str, Any] = {
        "book_id": book_id,
        "section_id": section_id,
        "neighbours": neighbours,
        "max_passages": max_passages,
        "max_brief_chars": max_brief_chars,
        "max_section_chars": max_section_chars,
        **_root(root),
    }
    if ranked_passages:
        params["ranked_passages"] = [
            chunk.model_dump(mode="json") for chunk in ranked_passages
        ]
    result = tier_bridge().call("library.build_working_set", params)
    return WorkingSet.model_validate(result["working_set"])


def read_count(root: str | None = None) -> int:
    """The number of section bodies the store has read.

    It is what makes the bounded-context property measurable rather than
    merely asserted, so it is answered by the tier that does the reading.
    """
    result = tier_bridge().call("library.read_count", _root(root))
    return int(result.get("read_count", 0))


def build_brief(book_id: str, max_chars: int = 2000, root: str | None = None) -> str:
    """The bounded digest of what a book contains.

    The bound belongs to the tier: it is the tier that writes the digest, so
    the Python side does not get to decide how long it may be.
    """
    result = tier_bridge().call(
        "library.build_brief",
        {"book_id": book_id, "max_chars": max_chars, **_root(root)},
    )
    return str(result.get("brief", ""))


def merge_brief(current: str, addition: str, max_chars: int = 2000) -> str:
    """The digest of a book, with something added to it.

    The merge is the tier's rule: the bound and the way the two halves are
    joined are one rule, not two.
    """
    result = tier_bridge().call(
        "library.merge_brief",
        {"current": current, "addition": addition, "max_chars": max_chars},
    )
    return str(result.get("brief", ""))


def add_section(
    book_id: str,
    section_id: str,
    content: str,
    title: str = "",
    order: int | None = None,
    origin: dict[str, Any] | None = None,
    root: str | None = None,
) -> dict[str, Any]:
    """Store one section of a book, creating it when it is not there yet.

    The tier's upsert: it writes the section's file, its counts and the
    manifest in one go, so the on-disk format has exactly one writer.
    """
    params: dict[str, Any] = {
        "book_id": book_id,
        "section_id": section_id,
        "content": content,
        "title": title,
        **_root(root),
    }
    if order is not None:
        params["order"] = order
    if origin is not None:
        params["origin"] = origin
    result = tier_bridge().call("library.add_section", params)
    saved = result.get("result")
    if isinstance(saved, dict):
        return saved
    return {key: value for key, value in result.items() if key != "result"}


def import_sections(
    book_id: str,
    title: str,
    sections: list[tuple[str, str, str]],
    root: str | None = None,
) -> BookManifest:
    """Sections become a book: stored, chunked and indexed."""
    result = tier_bridge().call(
        "library.import_sections",
        {
            "book_id": book_id,
            "title": title,
            "sections": [
                {"section_id": section_id, "title": section_title, "content": section_content}
                for section_id, section_title, section_content in sections
            ],
            **_root(root),
        },
    )
    return BookManifest.model_validate(result["manifest"])


def export_book(book_id: str, root: str | None = None) -> bytes:
    """A book becomes a Word document again."""
    result = tier_bridge().call(
        "library.export_book",
        {"book_id": book_id, **_root(root)},
    )
    return decode_bytes(str(result.get("bytes_b64", "")))


def create_book(
    book_id: str,
    title: str = "",
    root: str | None = None,
) -> BookManifest:
    """Create an empty book and return its manifest."""
    result = tier_bridge().call(
        "library.create",
        {"book_id": book_id, "title": title, **_root(root)},
    )
    return BookManifest.model_validate(result["manifest"])


def list_books(root: str | None = None) -> list[BookManifest]:
    """Every book in the store."""
    result = tier_bridge().call("library.list_books", _root(root))
    return [BookManifest.model_validate(item) for item in result.get("books", [])]


def delete_book(book_id: str, root: str | None = None) -> None:
    """Remove a book and everything it holds."""
    tier_bridge().call("library.delete", {"book_id": book_id, **_root(root)})


def read_section_record(
    book_id: str,
    section_id: str,
    root: str | None = None,
) -> dict[str, Any]:
    """One section of a book, with the document it was imported from."""
    result = tier_bridge().call(
        "library.read_section_record",
        {"book_id": book_id, "section_id": section_id, **_root(root)},
    )
    record = result.get("record")
    return record if isinstance(record, dict) else {}


def set_section_chunks(
    book_id: str,
    section_id: str,
    chunk_ids: list[str],
    root: str | None = None,
) -> dict[str, Any]:
    """Attach the passages a section was written from."""
    result = tier_bridge().call(
        "library.set_section_chunks",
        {
            "book_id": book_id,
            "section_id": section_id,
            "chunk_ids": list(chunk_ids),
            **_root(root),
        },
    )
    saved = result.get("result")
    return saved if isinstance(saved, dict) else {}


def put_chunks(
    book_id: str,
    chunks: list[SourceChunk],
    root: str | None = None,
) -> None:
    """Store the passages of a book."""
    tier_bridge().call(
        "library.put_chunks",
        {
            "book_id": book_id,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            **_root(root),
        },
    )


def chunks(book_id: str, root: str | None = None) -> list[SourceChunk]:
    """The passages of a book."""
    result = tier_bridge().call(
        "library.chunks",
        {"book_id": book_id, **_root(root)},
    )
    return [SourceChunk.model_validate(item) for item in result.get("chunks", [])]


def read_book_origin(
    book_id: str,
    root: str | None = None,
) -> dict[str, Any] | None:
    """The document a book was imported from, when it has one."""
    result = tier_bridge().call(
        "library.read_book_origin",
        {"book_id": book_id, **_root(root)},
    )
    origin = result.get("origin")
    return origin if isinstance(origin, dict) else None


def set_book_origin(
    book_id: str,
    origin: dict[str, Any],
    root: str | None = None,
) -> None:
    """Record the document a book was imported from."""
    tier_bridge().call(
        "library.set_book_origin",
        {"book_id": book_id, "origin": dict(origin), **_root(root)},
    )


def update_brief(
    book_id: str,
    brief: str,
    root: str | None = None,
) -> None:
    """Replace the bounded digest of what a book contains."""
    tier_bridge().call(
        "library.update_brief",
        {"book_id": book_id, "brief": brief, **_root(root)},
    )


__all__ = [
    "import_book",
    "get_manifest",
    "get_section",
    "save_section",
    "build_working_set",
    "read_count",
    "build_brief",
    "export_book",
    "create_book",
    "list_books",
    "delete_book",
    "read_section_record",
    "set_section_chunks",
    "put_chunks",
    "chunks",
    "read_book_origin",
    "set_book_origin",
    "update_brief",
]
