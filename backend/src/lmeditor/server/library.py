"""The process-wide book library and its retrieval indexes.

A book is edited one section at a time, so the server needs two things that a
single document run does not: somewhere durable to keep the book's sections,
and a retrieval index over the book that is built once and reused.

The store lives on disk (see :func:`library_root`), so a book survives a
restart. The index is the cached "important info" of the objective: it is what
lets a section be written with the passages that concern it without ever
loading the whole book into the model context.

The index is invalidated whenever a book's sections change, because a stale
index would retrieve passages that no longer match the text they were built
from.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from lmeditor.library import BookStore

logger = logging.getLogger(__name__)

#: Environment variable that redirects the library root.
LIBRARY_DIR_ENV = "LMEDITOR_LIBRARY_DIR"

#: The book store, cached per root so repeated calls share one instance.
_STORE: BookStore | None = None
_STORE_ROOT: Path | None = None

#: Retrieval indexes for books, keyed by book id.
_INDEXES: dict[str, Any] = {}


def library_root() -> Path:
    """The directory that holds the persisted books.

    Defaults to ``<tempdir>/lmeditor_books`` (portable across Linux, macOS
    and Windows). ``LMEDITOR_LIBRARY_DIR`` overrides it so tests can redirect
    the root without writing into the real temp directory.
    """
    override = os.environ.get(LIBRARY_DIR_ENV)
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "lmeditor_books"


def book_store() -> BookStore:
    """The book store for the current library root.

    The store is cached, but it is rebuilt when the root changes so a test
    that redirects ``LMEDITOR_LIBRARY_DIR`` does not keep writing into the
    previous directory.
    """
    global _STORE, _STORE_ROOT
    root = library_root()
    if _STORE is None or _STORE_ROOT != root:
        _STORE = BookStore(root)
        _STORE_ROOT = root
    return _STORE


def reset_book_store() -> None:
    """Forget the cached store and indexes. Intended for tests."""
    global _STORE, _STORE_ROOT
    _STORE = None
    _STORE_ROOT = None
    _INDEXES.clear()


def invalidate_book_index(book_id: str) -> None:
    """Drop the cached retrieval index for a book.

    Call this after the book's text changes: an index built from the old
    passages would retrieve material that no longer matches the book.
    """
    _INDEXES.pop(book_id, None)


def book_index(book_id: str, store: BookStore | None = None) -> Any | None:
    """The retrieval index for a book, built once and then cached.

    The index is built from the passages the book's sections **currently**
    reference, not from every passage the corpus file happens to hold. A
    section's text changes when it is edited, and the passages built from the
    old text are then no longer true of the book: an index that still served
    them would retrieve a paragraph the editor deleted, which is worse than
    having no index at all, because it looks like the book still says what it
    no longer says.

    Returns ``None`` when the index cannot be built. Retrieval is an
    enhancement, not a precondition: a book must still be readable and
    editable when the embedder is unavailable.
    """
    cached = _INDEXES.get(book_id)
    if cached is not None:
        return cached

    active = store or book_store()
    chunks = _referenced_chunks(active, book_id)
    if not chunks:
        return None
    try:
        from lmeditor.provenance.service import ProvenanceIndex

        index = ProvenanceIndex.build(chunks)
    except Exception as exc:  # noqa: BLE001 - retrieval must never break a book
        logger.warning("Book index unavailable for '%s': %s", book_id, exc)
        return None
    _INDEXES[book_id] = index
    return index


def _referenced_chunks(store: BookStore, book_id: str) -> list[Any]:
    """The passages the book's sections currently point at, in corpus order.

    The manifest is the index of the book: it says which passages belong to
    which section. Reading the corpus through it is what keeps a passage the
    editor deleted out of the retrieval index.
    """
    try:
        manifest = store.open(book_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable book has no index
        logger.warning("Book manifest unreadable for '%s': %s", book_id, exc)
        return []

    referenced: set[str] = set()
    for section in manifest.sections:
        referenced.update(section.chunk_ids)
    if not referenced:
        return []
    return [
        chunk for chunk in store.chunks(book_id) if chunk.chunk_id in referenced
    ]


def book_passages(store: BookStore, book_id: str) -> list[Any]:
    """The passages of a book that its sections currently reference.

    The corpus file keeps a passage after the section that produced it has
    been edited, so that two sections carrying the same text share one
    passage. Those passages are inert — nothing points at them — but they must
    not be handed to a caller as the book's material, because one of them may
    be a paragraph the editor deleted.
    """
    return _referenced_chunks(store, book_id)


__all__ = [
    "LIBRARY_DIR_ENV",
    "book_index",
    "book_passages",
    "book_store",
    "invalidate_book_index",
    "library_root",
    "reset_book_store",
]
