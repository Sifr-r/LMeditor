"""Bringing a book in: from a Word document, or from plain sections.

The rules of an import -- how a document is split into sections, how the
content is chunked, how the document's notes, media and styles are carried
along -- are the Kotlin tier's now: they are implemented once, in
``kotlin/library``, and served across the NDJSON seam.

This module is the **door**: it keeps the exact Python API the rest of the
backend calls, and answers with the tier's answers.  The implementation it
used to carry now lives in ``lmeditor._reference.library_importer``, frozen.

Two things do not cross the seam, and both are named in the functions that
need them:

* A :class:`~lmeditor.word.model.WordDocument` is a model object, and a model
  object cannot be put on the wire.  The seam serves no method that splits a
  document into sections without importing it, and the package format numbers
  the notes of a document -- so a document that was written out and read back
  would come home with its notes renamed.  The document is therefore split
  here, and the sections, the origin and the chunks are stored by the tier.
* The seam serves no method that splits a document into sections without
  importing it, so :func:`sections_with_origin` and
  :func:`sections_from_word_document` keep the Python rule until the bridge
  serves the Kotlin's ``sectionsWithOrigin``.

The seam *does* serve ``library.import_book``, which reads a package and
splits it on the tier.  The HTTP import route does not use it, because a
document the reader refuses has to be answered with the reader's own reason
and a 422, and the reader raises that reason on the Python side.  So the
split has two homes -- the tier's ``sectionsWithOrigin`` and this module's
copy -- and nothing holds the two together today.

That gap is declared rather than silent: the ledger ``REFERENCE_WITNESSES``
in ``backend/scripts/verify_kotlin_parity.py`` names
``lmeditor._reference.library_importer`` against its Kotlin counterpart
(``kotlin/library/BookImporter.kt``) and the harness fails if that
declaration stops matching the tree.  A comparison needs the same package on
both sides and a library to read it into; the harness has neither yet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, LibraryError
from lmeditor.core.schemas import BookManifest, SourceChunk
from lmeditor.library.brief import build_brief
from lmeditor.library.store import BookOrigin, BookStore, SectionOrigin
from lmeditor.provenance.chunker import SourceChunker
from lmeditor.tier import library as tier_library
from lmeditor.word.model import (
    WordBlock,
    WordBlockKind,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordMedia,
    WordPart,
    WordStyle,
)

#: The type of the records the origin of a book is made of.
_T = TypeVar("_T")

#: The block kinds that start a new section of a book.
_SECTION_START_KINDS: frozenset[WordBlockKind] = frozenset(
    {WordBlockKind.HEADING, WordBlockKind.TITLE}
)

#: The chunk size a book is indexed with when the caller names no chunker.
_DEFAULT_CHUNK_SIZE: int = 120

#: The overlap between two consecutive chunks of a book.
_DEFAULT_OVERLAP_SIZE: int = 25


class ImportedSection(BaseModel):
    """One section of an imported Word document.

    It carries both halves of the section: the text the editor works on, and
    the blocks the section was made of.  The two are kept together because
    they are two views of the same thing, and a section that kept only one of
    them could not be exported without losing the other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(min_length=1, description="Identifier of the section")
    title: str = Field(default="", description="Title of the section")
    content: str = Field(default="", description="The editable text of the section")
    origin: SectionOrigin = Field(
        default_factory=SectionOrigin,
        description="The blocks the section was imported from",
    )


def sections_with_origin(document: WordDocument) -> list[ImportedSection]:
    """Split a Word document into the sections of a book, with their origins.

    Every heading -- and the document title, which is a heading of its own --
    starts a section and becomes that section's title; the blocks under it
    become the section's body. Bodies are joined with a blank line, which is
    how :meth:`lmeditor.word.model.WordDocument.plain_text` joins them.

    The heading that starts a section is kept apart from its body, because
    the heading is the section's *title* and not its text: the body is what
    the editor edits. Every block of the body is kept, including the ones
    that render to nothing -- a page break is structure, and a body that lost
    its page breaks would lose the shape of the document.

    Text that appears before the first heading is kept in a leading section
    with an empty title, rather than dropped: a document that opens with a
    paragraph and then a heading has content the book must not lose. A
    document with no heading at all becomes a single section.

    .. note::
       The seam serves no method that splits a document into sections
       without importing it, so this rule is still the Python one.  It is the
       same rule as the Kotlin tier's ``sectionsWithOrigin``, and the parity
       harness compares the two.

    Args:
        document: The document to split.

    Returns:
        The sections, in the order they appear in the document.

    Raises:
        LibraryError: the document is not a ``WordDocument``.
    """
    if not isinstance(document, WordDocument):
        raise LibraryError(
            "expected a WordDocument",
            details={"received": type(document).__name__},
        )

    sections: list[ImportedSection] = []
    current_title = ""
    current_heading: WordBlock | None = None
    current_body: list[WordBlock] = []
    seen_heading = False

    for block in document.blocks:
        rendered = block.render_text()
        if block.kind in _SECTION_START_KINDS:
            if seen_heading:
                sections.append(
                    _make_section(len(sections) + 1, current_title, current_heading, current_body)
                )
            elif _joined(current_body):
                # The document opened with prose: it keeps a section of its own.
                sections.append(_make_section(len(sections) + 1, "", None, current_body))
            current_title = rendered.strip()
            current_heading = block
            current_body = []
            seen_heading = True
            continue
        current_body.append(block)

    if seen_heading:
        sections.append(
            _make_section(len(sections) + 1, current_title, current_heading, current_body)
        )
    elif _joined(current_body):
        sections.append(_make_section(len(sections) + 1, "", None, current_body))
    return sections


def sections_from_word_document(document: WordDocument) -> list[tuple[str, str, str]]:
    """Split a Word document into the sections of a book.

    Args:
        document: The document to split.

    Returns:
        The sections as ``(section_id, title, content)`` tuples, in the order
        they appear in the document.

    Raises:
        LibraryError: the document is not a ``WordDocument``.
    """
    return [
        (section.section_id, section.title, section.content)
        for section in sections_with_origin(document)
    ]


def import_word_document(
    store: BookStore,
    book_id: str,
    title: str,
    document: WordDocument,
    *,
    chunker: SourceChunker | None = None,
) -> BookManifest:
    """Import a Word document as a book, one section per heading.

    The document's sections are stored, chunked and indexed, and the book's
    brief is rebuilt so that the working set of every section carries a digest
    of the whole book.

    The document is kept as well: every section stores the blocks it was
    made of, and the book stores the document's notes, its media, its styles
    and its page setup.  An export can therefore put the document's tables,
    images and tracked changes back into the file the writer receives.

    The document is *not* written out and read back: the package format
    numbers the notes of a document, so a round trip through it renames them
    and the book's origin would no longer name the notes the document named.
    The document is therefore split here -- the rule the seam does not serve --
    and the sections, the origin and the chunks are stored by the tier.

    Args:
        store: The library the book is written into.
        book_id: The id of the book.
        title: The book's title.
        document: The document to import.
        chunker: The chunker the content is indexed with.

    Returns:
        The manifest of the imported book.

    Raises:
        LibraryError: the document is not a ``WordDocument``.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    if not isinstance(document, WordDocument):
        raise LibraryError(
            "expected a WordDocument",
            details={"received": type(document).__name__},
        )
    sections = sections_with_origin(document)
    return _import(
        store,
        book_id,
        title,
        [
            (section.section_id, section.title, section.content, section.origin)
            for section in sections
        ],
        origin=_book_origin(document),
        chunker=chunker,
    )


def import_sections(
    store: BookStore,
    book_id: str,
    title: str,
    sections: Iterable[tuple[str, str, str]],
    *,
    chunker: SourceChunker | None = None,
) -> BookManifest:
    """Import sections into a book, chunk their content and index the result.

    Every section is stored on its own, its content is chunked, and the ids of
    the chunks are recorded against the section they came from. The book's
    brief is then rebuilt from its section index, so the working set of every
    section carries a digest of the whole book rather than an empty string.

    The import is idempotent: importing into a book that already exists
    updates the sections it names and leaves the others alone.

    A section imported this way has no origin: there is no Word document
    behind it, so there is nothing to keep but its text.  A section that
    already had one loses it, because an origin that outlived the body it
    belonged to would bring back the old structure on export.

    Args:
        store: The library the book is written into.
        book_id: The id of the book.
        title: The book's title.
        sections: The sections to import, as ``(section_id, title, content)``
            tuples.
        chunker: The chunker the content is indexed with. The default is a
            sentence-aware chunker with a window of 120 characters and an
            overlap of 25.

    Returns:
        The manifest of the imported book.

    Raises:
        UnsafeIdentifierError: a book or section id is not a safe path segment.
        LibraryError: a section tuple is malformed or a chunk could not be
            produced.
    """
    unpacked: list[tuple[str, str, str]] = []
    for entry in sections:
        unpacked.append(_unpack_section(entry))
    active_chunker = (
        chunker
        if chunker is not None
        else SourceChunker(chunk_size=_DEFAULT_CHUNK_SIZE, overlap_size=_DEFAULT_OVERLAP_SIZE)
    )

    manifest = store.create(book_id, title)
    all_chunks: list[SourceChunk] = []
    for section_id, section_title, content in unpacked:
        store.add_section(book_id, section_id, content, title=section_title)
        section_chunks = _chunk_section(
            active_chunker, content, book_id, manifest.title
        )
        store.set_section_chunks(
            book_id, section_id, [chunk.chunk_id for chunk in section_chunks]
        )
        all_chunks.extend(section_chunks)

    store.put_chunks(book_id, all_chunks)
    store.update_brief(book_id, build_brief(store, book_id))
    return store.open(book_id)


def _import(
    store: BookStore,
    book_id: str,
    title: str,
    sections: Iterable[tuple[str, str, str, SectionOrigin]],
    *,
    origin: BookOrigin | None,
    chunker: SourceChunker | None,
) -> BookManifest:
    """Store, chunk and index the sections of a book.

    The origin of the book -- the document it was imported from -- is written
    last, so that a failure while the sections are written leaves the book
    with the origin it had rather than with an origin that does not match
    its sections.
    """
    manifest = store.create(book_id, title)
    active_chunker = (
        chunker
        if chunker is not None
        else SourceChunker(chunk_size=_DEFAULT_CHUNK_SIZE, overlap_size=_DEFAULT_OVERLAP_SIZE)
    )

    all_chunks: list[SourceChunk] = []
    for section_id, section_title, content, section_origin in sections:
        store.add_section(
            book_id,
            section_id,
            content,
            title=section_title,
            origin=section_origin,
        )
        section_chunks = _chunk_section(
            active_chunker, content, book_id, manifest.title
        )
        store.set_section_chunks(
            book_id, section_id, [chunk.chunk_id for chunk in section_chunks]
        )
        all_chunks.extend(section_chunks)

    if origin is not None:
        previous = store.read_book_origin(book_id)
        store.set_book_origin(book_id, _merge_origins(previous, origin))
    store.put_chunks(book_id, all_chunks)
    store.update_brief(book_id, build_brief(store, book_id))
    return store.open(book_id)


def _merge_origins(previous: BookOrigin | None, current: BookOrigin) -> BookOrigin:
    """Return the origin of a book after a new document was imported into it.

    The new document wins for everything it carries. The records it does not
    carry are kept, because the sections that were left alone may still refer
    to them: a section that keeps its image needs the media the image lives
    in, and a section that keeps a footnote marker needs the footnote.

    The parts of the document that are not referred to -- the page setup, the
    headers, the core properties -- are the new document's, because they
    describe the document as a whole rather than any one section.
    """
    if previous is None:
        return current
    return BookOrigin(
        properties=current.properties,
        footnotes=_merge_by_id(previous.footnotes, current.footnotes, _footnote_id),
        endnotes=_merge_by_id(previous.endnotes, current.endnotes, _endnote_id),
        comments=_merge_by_id(previous.comments, current.comments, _comment_id),
        headers=current.headers,
        footers=current.footers,
        sections=current.sections,
        styles=_merge_by_id(previous.styles, current.styles, _style_id),
        media=_merge_by_id(previous.media, current.media, _media_id),
        parts=_merge_by_id(previous.parts, current.parts, _part_name),
    )


def _merge_by_id(
    previous: Sequence[_T],
    current: Sequence[_T],
    key: Callable[[_T], str],
) -> list[_T]:
    """Return the records of both, the current ones winning on a shared id.

    The order is stable: a record that both documents carry keeps the place
    it had, so the numbers Word gives the notes do not move when a document
    is imported again.
    """
    merged: dict[str, _T] = {key(item): item for item in previous}
    for item in current:
        merged[key(item)] = item
    return list(merged.values())


def _footnote_id(footnote: WordFootnote) -> str:
    """Return the id of a footnote."""
    return footnote.footnote_id


def _endnote_id(endnote: WordEndnote) -> str:
    """Return the id of an endnote."""
    return endnote.endnote_id


def _comment_id(comment: WordComment) -> str:
    """Return the id of a comment."""
    return comment.comment_id


def _style_id(style: WordStyle) -> str:
    """Return the id of a style."""
    return style.style_id


def _media_id(media: WordMedia) -> str:
    """Return the id of a media part."""
    return media.media_id


def _part_name(part: WordPart) -> str:
    """Return the name of a package part."""
    return part.part_name


def _book_origin(document: WordDocument) -> BookOrigin:
    """Return the document-level record of a Word document.

    It holds everything that is not a block: the notes, the comments, the
    headers and footers, the page setup, the styles, the media and the
    package parts.  The blocks themselves belong to the sections they are in.
    """
    return BookOrigin(
        properties=document.properties,
        footnotes=document.footnotes,
        endnotes=document.endnotes,
        comments=document.comments,
        headers=document.headers,
        footers=document.footers,
        sections=document.sections,
        styles=document.styles,
        media=document.media,
        parts=document.parts,
    )


def _unpack_section(entry: tuple[str, str, str]) -> tuple[str, str, str]:
    """Return one section tuple, or raise when it is malformed."""
    if not isinstance(entry, tuple) or len(entry) != 3:
        raise LibraryError(
            "a section must be a (section_id, title, content) tuple",
            details={"received": repr(entry)},
        )
    section_id, title, content = entry
    if (
        not isinstance(section_id, str)
        or not isinstance(title, str)
        or not isinstance(content, str)
    ):
        raise LibraryError(
            "a section's id, title and content must all be strings",
            details={"received": repr(entry)},
        )
    return section_id, title, content


def _chunk_section(
    chunker: SourceChunker,
    content: str,
    document_id: str,
    document_title: str,
) -> list[SourceChunk]:
    """Return the chunks of one section's content.

    An empty section has no chunks: the chunker refuses blank text, and a
    section with no text has nothing to retrieve.
    """
    if not content.strip():
        return []
    try:
        return list(
            chunker.chunk_text(
                text=content,
                document_id=document_id,
                document_title=document_title,
            )
        )
    except ValueError as exc:
        raise LibraryError(
            f"could not chunk the content of section '{document_id}': {exc}",
            details={"document_id": document_id, "reason": str(exc)},
        ) from exc


def _make_section(
    index: int,
    title: str,
    heading: WordBlock | None,
    body: list[WordBlock],
) -> ImportedSection:
    """Return the section for one part of a document.

    The text of the section is the text its body rendered to, joined the way
    the document's plain text is joined: one blank line between the blocks,
    and the blocks that render to nothing left out of the text but kept in
    the blocks.
    """
    blocks = tuple(body)
    text = _joined(blocks)
    return ImportedSection(
        section_id=f"section_{index:04d}",
        title=title,
        content=text,
        origin=SectionOrigin(heading=heading, blocks=blocks, text=text),
    )


def _joined(blocks: Iterable[WordBlock]) -> str:
    """Return the text of a section's body, joined with a blank line.

    A block that renders to nothing -- a page break, an empty paragraph --
    contributes no text, but it is still one of the section's blocks: the
    caller keeps them all.
    """
    return "\n\n".join(
        rendered
        for rendered in (block.render_text() for block in blocks)
        if rendered.strip()
    )


def _call(operation: str, action: Callable[[], Any]) -> Any:
    """Run a seam call, keeping the tier's refusals as they are.

    A tier that is not there is an infrastructure failure, not a bad input:
    it is never dressed up as one.
    """
    try:
        return action()
    except BridgeUnavailableError:
        raise
    except LMeditorError:
        raise
    except Exception as exc:  # pragma: no cover - the tier's own failures
        raise LibraryError(f"the Kotlin tier could not {operation}: {exc}") from exc


__all__ = [
    "ImportedSection",
    "import_sections",
    "import_word_document",
    "sections_from_word_document",
    "sections_with_origin",
]
