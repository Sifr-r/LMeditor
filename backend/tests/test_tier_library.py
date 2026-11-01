"""The library's doors are answered by the Kotlin tier.

A door is only a door if the answers come from the other side.  These tests
ask the tier what it holds after a book has been written through the door,
and ask the door what it holds after the tier has written a book, so that a
door that quietly answered for itself would be caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.library import BookStore, build_brief, import_sections
from lmeditor.provenance.chunker import SourceChunker
from lmeditor.tier import library as tier_library

_ROOT = "root"
_BOOK = "book"


def _sections() -> list[tuple[str, str, str]]:
    """Return the sections a book is made of."""
    return [
        ("s1", "First", "The body of the first section."),
        ("s2", "Second", "The body of the second section."),
    ]


def test_the_door_writes_what_the_tier_reads(tmp_path: Path) -> None:
    """A book written through the door is the tier's book."""
    store = BookStore(tmp_path)
    import_sections(store, _BOOK, "The Book", _sections())

    manifest = tier_library.get_manifest(_BOOK, root=str(tmp_path))
    assert manifest.title == "The Book"
    assert [section.section_id for section in manifest.sections] == ["s1", "s2"]
    assert [section.title for section in manifest.sections] == ["First", "Second"]

    body = tier_library.get_section(_BOOK, "s1", root=str(tmp_path))
    assert body["content"] == "The body of the first section."


def test_the_tier_writes_what_the_door_reads(tmp_path: Path) -> None:
    """A book written by the tier is the door's book."""
    tier_library.create_book(_BOOK, "The Book", root=str(tmp_path))
    chunks = SourceChunker(chunk_size=120, overlap_size=25).chunk_text(
        text="A passage of the book.",
        document_id=_BOOK,
        document_title="The Book",
    )
    tier_library.put_chunks(_BOOK, list(chunks), root=str(tmp_path))

    store = BookStore(tmp_path)
    manifest = store.open(_BOOK)
    assert manifest.title == "The Book"
    assert [chunk.chunk_id for chunk in store.chunks(_BOOK)] == [
        chunk.chunk_id for chunk in chunks
    ]


def test_the_read_count_is_the_tier_s(tmp_path: Path) -> None:
    """The bound on the working set is measured by the tier that reads."""
    store = BookStore(tmp_path)
    import_sections(store, _BOOK, "The Book", _sections())

    before = store.read_count
    assert before == tier_library.read_count(root=str(tmp_path))

    store.read_section(_BOOK, "s1")
    assert store.read_count == before + 1


def test_the_brief_comes_from_the_tier(tmp_path: Path) -> None:
    """The digest of a book is the tier's digest."""
    store = BookStore(tmp_path)
    import_sections(store, _BOOK, "The Book", _sections())

    assert build_brief(store, _BOOK) == tier_library.build_brief(
        _BOOK, root=str(tmp_path)
    )


def test_a_tier_that_is_not_there_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable tier is an infrastructure failure, not a bad input."""
    store = BookStore(tmp_path)

    class _Absent:
        """A bridge that is not there."""

        def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
            raise BridgeUnavailableError("the Kotlin tier is not there")

    monkeypatch.setattr("lmeditor.tier.library.tier_bridge", lambda: _Absent())

    with pytest.raises(BridgeUnavailableError):
        store.open(_BOOK)
    with pytest.raises(BridgeUnavailableError):
        build_brief(store, _BOOK)
