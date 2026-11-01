"""The book library: books on disk, and the bounded context that edits them.

These tests pin the two properties the library exists for. The first is
*laziness*: a book is a directory, its manifest is an index, and opening,
listing or counting the sections of a book never reads a section body. The
second is *boundedness*: editing one section of a two-hundred-section book
puts the target, its two neighbours, the retrieved passages and the brief into
the context, and touches nothing else -- which the store's read counter makes
measurable rather than merely asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import BookNotFoundError, SectionNotFoundError
from lmeditor.core.types import WorkingSetRole
from lmeditor.library import (
    BookStore,
    WorkingSetBuilder,
    build_brief,
    import_sections,
    import_word_document,
    merge_brief,
    sections_from_word_document,
)
from lmeditor.provenance.chunker import SourceChunker
from lmeditor.provenance.indexer import DenseVectorIndex, HybridIndexer
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.word.model import WordBlock, WordBlockKind, WordDocument, WordRun
from lmeditor.word.text import normalize_whitespace


def _block(block_id: str, kind: WordBlockKind, text: str, level: int = 0) -> WordBlock:
    """Return a Word block whose text is the normalisation of its run."""
    runs = (WordRun(text=text),) if text else ()
    return WordBlock(
        block_id=block_id,
        kind=kind,
        text=normalize_whitespace(text),
        level=level,
        runs=runs,
    )


def _word_document() -> WordDocument:
    """Return a document with two chapters, each a heading and a paragraph."""
    return WordDocument(
        blocks=(
            _block("b1", WordBlockKind.HEADING, "First Chapter", level=1),
            _block("b2", WordBlockKind.PARAGRAPH, "The body of the first chapter."),
            _block("b3", WordBlockKind.HEADING, "Second Chapter", level=1),
            _block("b4", WordBlockKind.PARAGRAPH, "The body of the second chapter."),
        )
    )


def test_creating_opening_and_listing_books_round_trips(tmp_path: Path) -> None:
    """A book that is created can be opened again and is listed by the store."""
    store = BookStore(tmp_path)

    created = store.create("book", "The Book")

    assert created.book_id == "book"
    assert created.title == "The Book"
    assert created.section_count == 0
    assert store.open("book").title == "The Book"
    assert [manifest.book_id for manifest in store.list_books()] == ["book"]


def test_open_on_an_unknown_book_raises_book_not_found(tmp_path: Path) -> None:
    """A book that was never created is reported as missing, not as empty."""
    store = BookStore(tmp_path)

    with pytest.raises(BookNotFoundError):
        store.open("no-such-book")


def test_create_is_idempotent(tmp_path: Path) -> None:
    """Creating a book that exists returns it instead of overwriting it."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    store.add_section("book", "s1", "A body.")

    again = store.create("book", "Another Title")

    assert again.title == "The Book"
    assert again.section_count == 1


def test_add_section_is_idempotent(tmp_path: Path) -> None:
    """Adding a section that exists updates it rather than duplicating it."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")

    store.add_section("book", "s1", "The first body.", title="First")
    store.add_section("book", "s1", "The replacement body.", title="First")

    assert store.section_count("book") == 1
    assert store.read_section("book", "s1") == "The replacement body."
    assert [section.section_id for section in store.open("book").sections] == ["s1"]


def test_read_section_returns_the_stored_text(tmp_path: Path) -> None:
    """A section's body is read back exactly as it was written."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    body = "A body with several words in it."
    store.add_section("book", "s1", body, title="First")

    assert store.read_section("book", "s1") == body


def test_read_section_on_an_unknown_section_raises_section_not_found(tmp_path: Path) -> None:
    """Reading a section the book does not hold is an error."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")

    with pytest.raises(SectionNotFoundError):
        store.read_section("book", "no-such-section")


def test_write_section_on_an_unknown_section_raises_section_not_found(tmp_path: Path) -> None:
    """Writing a section the book does not hold is an error, not a creation."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")

    with pytest.raises(SectionNotFoundError):
        store.write_section("book", "no-such-section", "A body.")

    assert store.section_count("book") == 0


def test_write_section_updates_the_word_and_character_counts(tmp_path: Path) -> None:
    """The counts in the index follow the body that is on disk."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    store.add_section("book", "s1", "one two three", title="First")

    section = store.write_section("book", "s1", "one two three four five")

    assert section.word_count == 5
    assert section.char_count == len("one two three four five")
    assert store.open("book").sections[0].word_count == 5
    assert store.open("book").sections[0].char_count == len("one two three four five")


def test_a_crafted_section_id_cannot_escape_the_book_directory(tmp_path: Path) -> None:
    """A traversing id is refused, and nothing is written outside the book."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")

    for crafted in ("../escape", "..\\escape", "sub/escape", "/absolute", "C:\\absolute"):
        with pytest.raises(ValueError):
            store.add_section("book", crafted, "A payload.")
        with pytest.raises(ValueError):
            store.read_section("book", crafted)

    with pytest.raises(ValueError):
        store.create("../evil")

    assert not (tmp_path.parent / "escape").exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["book"]


def test_the_manifest_never_holds_the_body_of_a_section(tmp_path: Path) -> None:
    """The manifest is an index: the text of the book lives in the sections."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    body = "SENTINEL-BODY-TEXT that must never appear in the manifest."
    store.add_section("book", "s1", body, title="First")

    serialised = (tmp_path / "book" / "manifest.json").read_text(encoding="utf-8")

    assert "SENTINEL-BODY-TEXT" not in serialised
    assert body not in serialised
    # The index does hold the title, the count and the order.
    assert "First" in serialised


def test_the_working_set_reads_only_the_target_and_its_neighbours(tmp_path: Path) -> None:
    """Editing one section of a big book touches three sections and no more."""
    store = BookStore(tmp_path)
    store.create("book", "A Long Book")
    total_chars = 0
    for index in range(200):
        body = f"Section {index}. " + ("filler text for the section. " * 20)
        total_chars += len(body)
        store.add_section("book", f"s{index:03d}", body, title=f"Chapter {index}")
    store.update_brief("book", build_brief(store, "book"))

    builder = WorkingSetBuilder(store)
    before = store.read_count
    working_set = builder.build("book", "s100", neighbours=1)
    reads = store.read_count - before

    assert reads == 2 * 1 + 1
    assert [section.section_id for section in working_set.sections] == [
        "s099",
        "s100",
        "s101",
    ]
    assert [section.role for section in working_set.sections] == [
        WorkingSetRole.PREVIOUS,
        WorkingSetRole.TARGET,
        WorkingSetRole.NEXT,
    ]
    assert working_set.target_section_id == "s100"
    assert working_set.context_chars < total_chars // 10


def test_the_working_set_of_an_unknown_book_or_section_raises(tmp_path: Path) -> None:
    """A working set cannot be built for a book or a section that is not there."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    store.add_section("book", "s1", "A body.")
    builder = WorkingSetBuilder(store)

    with pytest.raises(BookNotFoundError):
        builder.build("no-such-book", "s1")
    with pytest.raises(SectionNotFoundError):
        builder.build("book", "no-such-section")


def test_the_working_set_carries_the_passages_the_index_retrieves(tmp_path: Path) -> None:
    """With an index the working set carries the passages that bear on the section."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    store.add_section("book", "s1", "The citation gate rejects unquoted spans.", title="The Gate")
    chunks = SourceChunker(chunk_size=200, overlap_size=20).chunk_text(
        text=(
            "The citation gate rejects any span that is not quoted from a source. "
            "Gradient descent minimises a loss surface by following its slope."
        ),
        document_id="book",
        document_title="The Book",
    )
    store.put_chunks("book", chunks)

    builder = WorkingSetBuilder(
        store,
        index=ProvenanceIndex.build(
            chunks,
            indexer=HybridIndexer(vector_index=DenseVectorIndex(enable_semantic=False)),
        ),
    )
    working_set = builder.build("book", "s1", max_passages=2)

    assert working_set.passages
    assert len(working_set.passages) <= 2
    assert any("citation gate" in passage.text for passage in working_set.passages)
    assert working_set.context_chars > 0


def test_import_sections_chunks_the_content_and_records_the_chunk_ids(tmp_path: Path) -> None:
    """An import chunks every section and records the chunks against it."""
    store = BookStore(tmp_path)
    body = "The citation gate rejects any span that is not quoted from a source. " * 12

    manifest = import_sections(
        store,
        "book",
        "The Book",
        [
            ("s1", "First", body),
            ("s2", "Second", "A short second section about the gate."),
        ],
    )

    assert manifest.section_count == 2
    assert manifest.chunk_count > 1
    stored = store.chunks("book")
    assert len(stored) == manifest.chunk_count
    assert all(chunk.document_id == "book" for chunk in stored)
    assert all(chunk.document_title == "The Book" for chunk in stored)
    assert any("citation gate" in chunk.text for chunk in stored)

    first = manifest.sections[0]
    assert first.section_id == "s1"
    assert first.chunk_ids
    assert set(first.chunk_ids) <= {chunk.chunk_id for chunk in stored}


def test_sections_from_word_document_splits_at_headings() -> None:
    """A heading starts a section and becomes that section's title."""
    sections = sections_from_word_document(_word_document())

    assert len(sections) == 2
    assert sections[0][1] == "First Chapter"
    assert sections[0][2] == "The body of the first chapter."
    assert sections[1][1] == "Second Chapter"
    assert sections[1][2] == "The body of the second chapter."
    assert sections[0][0] != sections[1][0]


def test_a_document_without_headings_becomes_one_section() -> None:
    """A document with no heading at all is still imported, not dropped."""
    document = WordDocument(
        blocks=(
            _block("b1", WordBlockKind.PARAGRAPH, "Only a paragraph, no headings."),
        )
    )

    sections = sections_from_word_document(document)

    assert len(sections) == 1
    assert sections[0][1] == ""
    assert sections[0][2] == "Only a paragraph, no headings."


def test_import_word_document_creates_a_book_with_one_section_per_heading(tmp_path: Path) -> None:
    """Importing a Word document stores its sections and indexes their content."""
    store = BookStore(tmp_path)

    manifest = import_word_document(store, "book", "The Book", _word_document())

    assert manifest.section_count == 2
    assert manifest.chunk_count > 0
    assert store.read_section("book", "section_0001") == "The body of the first chapter."
    assert store.read_section("book", "section_0002") == "The body of the second chapter."
    assert store.open("book").sections[0].title == "First Chapter"


def test_build_brief_stays_bounded_and_holds_no_body_text(tmp_path: Path) -> None:
    """The brief is a digest of the index: small, and free of the book's text."""
    store = BookStore(tmp_path)
    store.create("book", "The Big Book")
    for index in range(150):
        store.add_section(
            "book",
            f"s{index:03d}",
            f"BODY-{index} secret content",
            title=f"Chapter {index}",
        )

    brief = build_brief(store, "book", max_chars=500)

    assert len(brief) <= 500
    assert "secret content" not in brief
    assert "BODY-" not in brief
    assert "The Big Book" in brief
    # The most recent chapter is kept; the oldest is not.
    assert "Chapter 149" in brief
    assert "Chapter 0" not in brief


def test_merge_brief_trims_from_the_front_and_stays_bounded() -> None:
    """A brief that grows too large loses its front, not its newest note."""
    current = "old note. " * 100
    addition = "the newest note."

    merged = merge_brief(current, addition, max_chars=120)

    assert len(merged) <= 120
    assert merged.endswith(addition)
    assert not merged.startswith("old note. old note.")


def test_delete_removes_the_book(tmp_path: Path) -> None:
    """A book that is deleted is gone from the library."""
    store = BookStore(tmp_path)
    store.create("book", "The Book")
    store.add_section("book", "s1", "A body.")

    store.delete("book")

    assert store.list_books() == []
    with pytest.raises(BookNotFoundError):
        store.open("book")
