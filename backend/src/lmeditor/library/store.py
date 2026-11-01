"""The library's store -- a **door** to the Kotlin tier.

A book is kept on disk as a directory: a manifest that indexes it, a file per
section, the corpus chunks it is grounded in, and the document it was imported
from.  Those rules -- the shape of every file, the counts, the ordering, the
identifiers that are safe to write -- are the Kotlin tier's now: they are
implemented once, in ``kotlin/library``, and served across the NDJSON seam.

This module is the **door**: it keeps the exact Python API the rest of the
backend calls, and answers with the tier's answers.  The implementation it used
to carry now lives in ``lmeditor._reference.library_store``, frozen.

No parity check compares the two sides yet: the store is a directory on disk,
so a comparison would need one library on both sides and would measure the
filesystem as much as the rules.  The gap is declared rather than silent: the
ledger ``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py``
names the module against its Kotlin counterpart
(``kotlin/library/BookStore.kt``) and the harness fails if that declaration
stops matching the tree.

The root travels with every request.  A store opened on a temporary directory
reads and writes *that* directory, never the configured one, and never the
directory of another store.

The one gap in the seam is named in :meth:`BookStore.add_section`: the bridge
serves the tier's ``writeSection`` but not its ``addSection``, so a section
that does not exist yet cannot be created across the seam.  Until the bridge
serves it, that one method writes the index entry itself, in the tier's own
format, and then hands the section to the tier to write.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import (
    BookNotFoundError,
    LMeditorError,
    LibraryError,
    SectionNotFoundError,
)
from lmeditor.core.schemas import BookManifest, BookSection, SourceChunk
from lmeditor.tier import library as tier_library
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordComment,
    WordEndnote,
    WordFootnote,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordProperties,
    WordSection,
    WordStyle,
    WordTable,
    WordTableCell,
    WordTableRow,
    WordRun,
)

#: The file that holds a book's manifest.
MANIFEST_FILENAME: Final[str] = "manifest.json"

#: The directory that holds a book's section files.
SECTIONS_DIRNAME: Final[str] = "sections"

#: The file that holds a book's corpus chunks, one JSON object per line.
CHUNKS_FILENAME: Final[str] = "chunks.jsonl"

#: The file that holds the document a book was imported from.
#:
#: It carries the document-level records only -- the notes, the media, the
#: styles, the page setup.  The blocks live in the section files they belong
#: to, so that reading a section never costs a second file.
ORIGIN_FILENAME: Final[str] = "origin.json"

#: The longest a book or section id may be.
MAX_IDENTIFIER_LENGTH: Final[int] = 128

#: Characters that may not appear in an id: they either separate path segments
#: or are refused by Windows in a file name.
_FORBIDDEN_ID_CHARS: Final[frozenset[str]] = frozenset('/\\:*?"<>|')

#: Device names Windows reserves; ``NUL.json`` is the null device, not a file.
_WINDOWS_RESERVED_NAMES: Final[frozenset[str]] = (
    frozenset({"CON", "PRN", "AUX", "NUL"})
    .union(f"COM{index}" for index in range(1, 10))
    .union(f"LPT{index}" for index in range(1, 10))
)

_T = TypeVar("_T")


class UnsafeIdentifierError(LibraryError, ValueError):
    """Raised when a book or section id is not a safe path segment.

    The error is both a :class:`~lmeditor.core.exceptions.LibraryError` -- so a
    caller of the library can catch the domain error -- and a
    :class:`ValueError`, because an unsafe identifier is a bad argument value
    and must not be mistaken for a missing book.
    """

    def __init__(self, kind: str, value: str, reason: str) -> None:
        self.kind = kind
        self.value = value
        self.reason = reason
        super().__init__(
            f"unsafe {kind} {value!r}: {reason}",
            details={"kind": kind, "value": value, "reason": reason},
        )


def validate_identifier(value: str, kind: str) -> str:
    """Return ``value`` as a safe path segment, or raise when it is not one.

    A book id becomes a directory and a section id becomes a file name, so
    both have to be a single segment of a path: no separator, no ``.`` or
    ``..``, no drive letter, no absolute path, no NUL, and none of the
    characters Windows refuses in a file name.

    The rule is the tier's (``BookStore.validateIdentifier``); it is kept here
    as well because it is the boundary of the Python API: an id that could
    escape the directory it is used in must be refused before it is ever put
    on the wire, and the tier's refusal cannot be turned into *this* error
    once it comes back.

    Args:
        value: The identifier to validate.
        kind: What the identifier names, used in the error message.

    Returns:
        The identifier, stripped of surrounding whitespace. The stripped form
        is the one written to disk, so the file name and the manifest always
        agree.

    Raises:
        UnsafeIdentifierError: the identifier is empty, too long, or could
            escape the directory it is used in.
    """
    if not isinstance(value, str) or not value.strip():
        raise UnsafeIdentifierError(kind, str(value), "it must be a non-empty string")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise UnsafeIdentifierError(
            kind, value, f"it exceeds the maximum length of {MAX_IDENTIFIER_LENGTH} characters"
        )
    if "\x00" in value:
        raise UnsafeIdentifierError(kind, value, "it contains a NUL character")
    forbidden = sorted(_FORBIDDEN_ID_CHARS.intersection(value))
    if forbidden:
        raise UnsafeIdentifierError(
            kind, value, f"it contains the forbidden character(s) {forbidden}"
        )
    stripped = value.strip()
    if stripped in {".", ".."}:
        raise UnsafeIdentifierError(kind, value, "it is a relative path segment")
    if PurePath(stripped).is_absolute() or PureWindowsPath(stripped).is_absolute():
        raise UnsafeIdentifierError(kind, value, "it is an absolute path")
    if PureWindowsPath(stripped).drive:
        raise UnsafeIdentifierError(kind, value, "it carries a drive letter")
    if stripped.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise UnsafeIdentifierError(kind, value, "it is a reserved device name")
    return stripped


class SectionOrigin(BaseModel):
    """The Word blocks a section was imported from, and the text they rendered to.

    A section that was imported from a Word document keeps both: the blocks
    are the structure the author wrote -- the table, the image, the tracked
    change, the list -- and ``text`` is the plain projection of those blocks,
    which is the text the editor works on.

    The heading is held apart from the body because the heading is the
    section's *title*, not its text: the body is what the editor edits, and
    ``text`` is therefore the text of the body alone.  Keeping the heading
    block as it was written means an export can put its level and its style
    back, instead of inventing a heading of its own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    heading: WordBlock | None = Field(
        default=None,
        description="The heading block that starts the section, when it has one",
    )
    blocks: tuple[WordBlock, ...] = Field(
        default=(),
        description="The blocks of the section's body, in document order",
    )
    text: str = Field(
        default="",
        description="The text the body blocks rendered to: the section's editable text",
    )


class BookOrigin(BaseModel):
    """The document-level record of the Word document a book was imported from.

    It holds everything of the document that is *not* a block: the core
    properties, the notes, the comments, the headers and footers, the page
    setup, the styles, the media and the package parts.  An export carries it
    through, so the file the writer receives still holds the document's
    images, its charts and its notes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    properties: WordProperties = Field(
        default_factory=WordProperties,
        description="The document's core properties",
    )
    footnotes: tuple[WordFootnote, ...] = Field(default=(), description="Footnotes by id")
    endnotes: tuple[WordEndnote, ...] = Field(default=(), description="Endnotes by id")
    comments: tuple[WordComment, ...] = Field(default=(), description="Comments by id")
    headers: tuple[str, ...] = Field(default=(), description="Header texts, in part order")
    footers: tuple[str, ...] = Field(default=(), description="Footer texts, in part order")
    sections: tuple[WordSection, ...] = Field(default=(), description="Sections, in order")
    styles: tuple[WordStyle, ...] = Field(default=(), description="Style definitions")
    media: tuple[WordMedia, ...] = Field(default=(), description="Embedded media parts")
    parts: tuple[WordPart, ...] = Field(
        default=(),
        description="Package parts carried through without modelling their content",
    )


class SectionRecord(BaseModel):
    """What one read of a section's file yields.

    The body and the origin come from the same file and are read together, so
    a caller that needs both -- an export that has to decide whether a
    section was edited -- pays for one read and not two.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(min_length=1)
    title: str = ""
    order: int = Field(default=0, ge=0)
    content: str = Field(default="", description="The section's editable text")
    origin: SectionOrigin | None = Field(
        default=None,
        description="The blocks the section was imported from, when it has any",
    )


class BookStore:
    """A library of books kept on disk, one directory per book.

    The store is the door to the Kotlin tier: every call is answered by the
    tier, and the root the store was opened with travels with the request, so
    two stores on two directories never see each other's books.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """Return the directory the store keeps its books in."""
        return self._root

    @property
    def read_count(self) -> int:
        """Return the number of section bodies read from disk so far.

        The count is the tier's: it is what makes the bound on the working
        set measurable rather than asserted.
        """
        return _call(
            "count the reads of the library",
            lambda: tier_library.read_count(root=self._root_arg()),
        )

    def create(self, book_id: str, title: str = "") -> BookManifest:
        """Create the book ``book_id`` and return its manifest.

        Creating a book that already exists does not overwrite it: the
        manifest that is already there is returned unchanged.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            LibraryError: the title is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        _require_text(title, "a book title")
        return _call(
            f"create the book {book_id!r}",
            lambda: tier_library.create_book(book_id, title, root=self._root_arg()),
        )

    def open(self, book_id: str) -> BookManifest:
        """Return the manifest of the book ``book_id``.

        The manifest is the index of the book: it names the sections, their
        order and their counts, and it holds no body at all.  Opening a book
        therefore reads no body.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        book_id = validate_identifier(book_id, "book_id")
        return _call(
            f"open the book {book_id!r}",
            lambda: tier_library.get_manifest(book_id, root=self._root_arg()),
        )

    def list_books(self) -> list[BookManifest]:
        """Return the manifest of every book in the library, ordered by book id.

        Raises:
            LibraryError: the library cannot be read.
        """
        return _call(
            "list the books of the library",
            lambda: tier_library.list_books(root=self._root_arg()),
        )

    def delete(self, book_id: str) -> None:
        """Delete the book ``book_id`` and everything it holds.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        book_id = validate_identifier(book_id, "book_id")
        _call(
            f"delete the book {book_id!r}",
            lambda: tier_library.delete_book(book_id, root=self._root_arg()),
        )

    def add_section(
        self,
        book_id: str,
        section_id: str,
        content: str,
        *,
        title: str = "",
        order: int | None = None,
        origin: SectionOrigin | None = None,
    ) -> BookSection:
        """Add a section to a book, or update it when it is already there.

        Adding a section that already exists updates it in place: the body is
        replaced, the word and character counts are recomputed, and the chunk
        ids the section is indexed by are kept. An empty ``title`` keeps the
        title the section already has and an ``order`` of ``None`` keeps the
        position it already occupies, so a caller that only wants to replace
        the text does not have to restate the index.

        The section's origin is *replaced* as well: passing one stores it,
        passing none clears it.  An origin that outlives the body it was
        imported with would resurrect the old structure on export, so the two
        are always written together.

        Args:
            book_id: The book to write into.
            section_id: The section's id; it names the file it is stored in.
            content: The section's text.
            title: The section's title.
            order: The section's position in the book. ``None`` appends the
                section after the last one.
            origin: The blocks the section was imported from, when it has
                any.

        Returns:
            The section's entry in the manifest's index.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the content, the title or the order is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        _require_text(content, "section content")
        _require_text(title, "a section title")
        _require_order(order)
        # The tier's upsert writes the section's file, its counts and the
        # manifest in one go, so the on-disk format has exactly one writer.
        saved = _call(
            f"add the section {section_id!r} to the book {book_id!r}",
            lambda: tier_library.add_section(
                book_id,
                section_id,
                content,
                title=title,
                order=order,
                origin=_section_origin_to_tier(origin) if origin is not None else None,
                root=self._root_arg(),
            ),
        )
        return BookSection.model_validate(saved)

    def read_section(self, book_id: str, section_id: str) -> str:
        """Return the body of one section of a book.

        This is the only method that reads a section body, and it reads
        exactly one. :attr:`read_count` is incremented, so the bound on the
        working set can be measured rather than asserted.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            LibraryError: the section file cannot be read or is not valid.
        """
        return self.read_section_record(book_id, section_id).content

    def read_section_record(self, book_id: str, section_id: str) -> SectionRecord:
        """Return a section's body *and* its origin from a single read.

        The body and the origin live in the same file, so a caller that needs
        both -- an export deciding whether a section was edited -- pays for
        one read of the file and not two. :attr:`read_count` is incremented
        once, because one section body was pulled into the process.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            LibraryError: the section file cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        record = _call(
            f"read the section {section_id!r} of the book {book_id!r}",
            lambda: tier_library.read_section_record(
                book_id, section_id, root=self._root_arg()
            ),
        )
        origin = record.get("origin")
        return SectionRecord(
            section_id=str(record.get("section_id", section_id)),
            title=str(record.get("title", "")),
            order=int(record.get("order", 0) or 0),
            content=str(record.get("content", "")),
            origin=(
                _section_origin_from_tier(origin)
                if isinstance(origin, Mapping)
                else None
            ),
        )

    def write_section(self, book_id: str, section_id: str, content: str) -> BookSection:
        """Replace the body of an existing section and return its index entry.

        The counts are recomputed by the tier, and the section's origin -- the
        blocks it was imported from -- is kept, so that an export can still
        tell an edited section from an untouched one.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            LibraryError: the content is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        _require_text(content, "section content")
        saved = _call(
            f"write the section {section_id!r} of the book {book_id!r}",
            lambda: tier_library.save_section(
                book_id, section_id, content, root=self._root_arg()
            ),
        )
        return BookSection.model_validate(saved)

    def section_count(self, book_id: str) -> int:
        """Return the number of sections in a book, without reading any body.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        return len(self.open(book_id).sections)

    def set_section_chunks(
        self, book_id: str, section_id: str, chunk_ids: Sequence[str]
    ) -> BookSection:
        """Attach the passages a section was written from.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            LibraryError: a chunk id is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        ids = [str(chunk_id) for chunk_id in chunk_ids]
        for chunk_id in ids:
            _require_text(chunk_id, "a chunk id")
        saved = _call(
            f"index the section {section_id!r} of the book {book_id!r}",
            lambda: tier_library.set_section_chunks(
                book_id, section_id, ids, root=self._root_arg()
            ),
        )
        return BookSection.model_validate(saved)

    def put_chunks(self, book_id: str, chunks: Sequence[SourceChunk]) -> None:
        """Store the corpus chunks of a book and record how many there are.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            LibraryError: a chunk is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        stored = list(chunks)
        for chunk in stored:
            if not isinstance(chunk, SourceChunk):
                raise LibraryError(
                    f"expected a corpus chunk, got {type(chunk).__name__}"
                )
        _call(
            f"store the chunks of the book {book_id!r}",
            lambda: tier_library.put_chunks(book_id, stored, root=self._root_arg()),
        )

    def chunks(self, book_id: str) -> list[SourceChunk]:
        """Return the corpus chunks stored for a book.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the chunk file cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        return _call(
            f"read the chunks of the book {book_id!r}",
            lambda: tier_library.chunks(book_id, root=self._root_arg()),
        )

    def brief(self, book_id: str) -> str:
        """Return the book's running brief: the cached digest of its contents.

        The digest is bounded: it names the book and the most recent section
        titles, and it never holds the body of a section.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        return self.open(book_id).brief

    def read_book_origin(self, book_id: str) -> BookOrigin | None:
        """Return the document a book was imported from, or ``None``.

        A book made of plain sections has no origin: it was not imported from
        a document, and there is nothing to carry through an export.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the origin file cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        origin = _call(
            f"read the origin of the book {book_id!r}",
            lambda: tier_library.read_book_origin(book_id, root=self._root_arg()),
        )
        if origin is None:
            return None
        return _origin_from_tier(origin)

    def set_book_origin(self, book_id: str, origin: BookOrigin) -> None:
        """Record the document a book was imported from.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the origin is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        if not isinstance(origin, BookOrigin):
            raise LibraryError(
                f"expected a book origin, got {type(origin).__name__}"
            )
        _call(
            f"write the origin of the book {book_id!r}",
            lambda: tier_library.set_book_origin(
                book_id, _origin_to_tier(origin), root=self._root_arg()
            ),
        )

    def update_brief(self, book_id: str, brief: str) -> None:
        """Replace the book's running brief.

        Raises:
            UnsafeIdentifierError: the id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the brief is not usable.
        """
        book_id = validate_identifier(book_id, "book_id")
        _require_text(brief, "a brief")
        _call(
            f"write the brief of the book {book_id!r}",
            lambda: tier_library.update_brief(
                book_id, brief, root=self._root_arg()
            ),
        )

    # ------------------------------------------------------------------
    # The gap in the seam
    # ------------------------------------------------------------------

    def _root_arg(self) -> str:
        """Return the root the tier is asked to work in."""
        return str(self._root)

    def _write_manifest(self, manifest: BookManifest) -> None:
        """Write a book's manifest, in the tier's own on-disk format.

        Only the index entry of a section is written here; the tier rewrites
        the manifest as soon as it has written the section itself.
        """
        path = self._manifest_path(manifest.book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, manifest.model_dump_json(indent=2))

    def _write_section_file(
        self,
        book_id: str,
        entry: BookSection,
        content: str,
        *,
        origin: SectionOrigin | None,
    ) -> None:
        """Write one section's file, in the tier's own on-disk format.

        The counts are placeholders: the tier recomputes them when it writes
        the section, and the origin travels with the file so that the tier
        carries it through.
        """
        path = self._section_path(book_id, entry.section_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "section_id": entry.section_id,
            "title": entry.title,
            "order": entry.order,
            "word_count": entry.word_count,
            "char_count": entry.char_count,
            "content": content,
            "origin": _section_origin_to_tier(origin) if origin is not None else None,
        }
        _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _manifest_path(self, book_id: str) -> Path:
        """Return the path of a book's manifest."""
        return self._root / book_id / MANIFEST_FILENAME

    def _section_path(self, book_id: str, section_id: str) -> Path:
        """Return the path of one section's file."""
        return self._root / book_id / SECTIONS_DIRNAME / f"{section_id}.json"


# ---------------------------------------------------------------------------
# The wire forms
# ---------------------------------------------------------------------------


def _origin_to_tier(origin: BookOrigin) -> dict[str, Any]:
    """Return the tier's form of the document a book was imported from.

    The tier's form is the authority: a book written here is the same book
    the tier would have written.  Three things the Python model holds do not
    travel -- the relationships of a package part, the header and footer
    references of a page setup, and the list bookkeeping of a block -- because
    the tier's own format does not carry them.
    """
    return {
        "properties": {
            "title": origin.properties.title,
            "author": origin.properties.author,
            "subject": origin.properties.subject,
            "keywords": origin.properties.keywords,
            "comments": origin.properties.comments,
            "created": _timestamp(origin.properties.created),
            "modified": _timestamp(origin.properties.modified),
            "revision": origin.properties.revision,
        },
        "footnotes": [
            {
                "footnote_id": note.footnote_id,
                "text": note.text,
                "runs": [_run_to_tier(run) for run in note.runs],
            }
            for note in origin.footnotes
        ],
        "endnotes": [
            {
                "endnote_id": note.endnote_id,
                "text": note.text,
                "runs": [_run_to_tier(run) for run in note.runs],
            }
            for note in origin.endnotes
        ],
        "comments": [
            {
                "comment_id": comment.comment_id,
                "author": comment.author,
                "initials": comment.initials,
                "date": _timestamp(comment.date),
                "text": comment.text,
                "runs": [_run_to_tier(run) for run in comment.runs],
                "anchor_block_id": comment.anchor_block_id,
                "anchor_text": comment.anchor_text,
            }
            for comment in origin.comments
        ],
        "headers": list(origin.headers),
        "footers": list(origin.footers),
        "sections": [
            {
                "page_width": section.page_width,
                "page_height": section.page_height,
                "orientation": section.orientation,
                "margin_top": section.margin_top,
                "margin_right": section.margin_right,
                "margin_bottom": section.margin_bottom,
                "margin_left": section.margin_left,
                "end_block_id": section.end_block_id,
            }
            for section in origin.sections
        ],
        "styles": [
            {
                "style_id": style.style_id,
                "name": style.name,
                "based_on": style.based_on,
                "style_type": style.style_type,
                "num_id": style.num_id,
                "num_level": style.num_level,
            }
            for style in origin.styles
        ],
        "media": [
            {
                "media_id": media.media_id,
                "part_name": media.part_name,
                "content_type": media.content_type,
                "data_base64": _encode_bytes(media.data),
                "relationship_id": media.relationship_id,
            }
            for media in origin.media
        ],
        "parts": [
            {
                "part_name": part.part_name,
                "content_type": part.content_type,
                "data_base64": _encode_bytes(part.data),
                "relationship_id": part.relationship_id,
                "relationship_type": part.relationship_type,
                # The relationships of a part are carried along: the tier's
                # own origin format does not hold them, but its store keeps
                # the origin verbatim, and a part stored without them would
                # come back disconnected from the package around it.
                "relationships": [
                    {
                        "relationship_id": relationship.relationship_id,
                        "relationship_type": relationship.relationship_type,
                        "target": relationship.target,
                        "is_external": relationship.is_external,
                    }
                    for relationship in part.relationships
                ],
            }
            for part in origin.parts
        ],
    }


def _origin_from_tier(origin: Mapping[str, Any]) -> BookOrigin:
    """Return the Python model of the document a book was imported from."""
    properties = origin.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    return BookOrigin(
        properties=WordProperties(
            title=str(properties.get("title", "") or ""),
            author=str(properties.get("author", "") or ""),
            subject=str(properties.get("subject", "") or ""),
            keywords=str(properties.get("keywords", "") or ""),
            comments=str(properties.get("comments", "") or ""),
            created=_parse_timestamp(properties.get("created")),
            modified=_parse_timestamp(properties.get("modified")),
            revision=max(1, int(properties.get("revision", 0) or 0)),
        ),
        footnotes=tuple(
            WordFootnote(
                footnote_id=str(entry.get("footnote_id", "") or ""),
                text=str(entry.get("text", "") or ""),
                runs=_runs_from_tier(entry.get("runs")),
            )
            for entry in _entries(origin.get("footnotes"))
        ),
        endnotes=tuple(
            WordEndnote(
                endnote_id=str(entry.get("endnote_id", "") or ""),
                text=str(entry.get("text", "") or ""),
                runs=_runs_from_tier(entry.get("runs")),
            )
            for entry in _entries(origin.get("endnotes"))
        ),
        comments=tuple(
            WordComment(
                comment_id=str(entry.get("comment_id", "") or ""),
                author=str(entry.get("author", "") or ""),
                initials=str(entry.get("initials", "") or ""),
                date=_parse_timestamp(entry.get("date")),
                text=str(entry.get("text", "") or ""),
                runs=_runs_from_tier(entry.get("runs")),
                anchor_block_id=(
                    str(entry["anchor_block_id"])
                    if entry.get("anchor_block_id") is not None
                    else None
                ),
                anchor_text=str(entry.get("anchor_text", "") or ""),
            )
            for entry in _entries(origin.get("comments"))
        ),
        headers=tuple(str(text) for text in origin.get("headers", []) or ()),
        footers=tuple(str(text) for text in origin.get("footers", []) or ()),
        sections=tuple(
            WordSection(
                page_width=int(entry.get("page_width", 0) or 0),
                page_height=int(entry.get("page_height", 0) or 0),
                orientation=str(entry.get("orientation", "") or ""),
                margin_top=int(entry.get("margin_top", 0) or 0),
                margin_right=int(entry.get("margin_right", 0) or 0),
                margin_bottom=int(entry.get("margin_bottom", 0) or 0),
                margin_left=int(entry.get("margin_left", 0) or 0),
                end_block_id=(
                    str(entry["end_block_id"])
                    if entry.get("end_block_id") is not None
                    else None
                ),
            )
            for entry in _entries(origin.get("sections"))
        ),
        styles=tuple(
            WordStyle(
                style_id=str(entry.get("style_id", "") or ""),
                name=str(entry.get("name", "") or ""),
                based_on=str(entry.get("based_on", "") or ""),
                style_type=str(entry.get("style_type", "") or ""),
                num_id=(str(entry["num_id"]) if entry.get("num_id") is not None else None),
                num_level=int(entry.get("num_level", 0) or 0),
            )
            for entry in _entries(origin.get("styles"))
        ),
        media=tuple(
            WordMedia(
                media_id=str(entry.get("media_id", "") or ""),
                part_name=str(entry.get("part_name", "") or ""),
                content_type=str(entry.get("content_type", "") or ""),
                data=_decode_bytes(str(entry.get("data_base64", "") or "")),
                relationship_id=str(entry.get("relationship_id", "") or ""),
            )
            for entry in _entries(origin.get("media"))
        ),
        parts=tuple(
            WordPart(
                part_name=str(entry.get("part_name", "") or ""),
                content_type=str(entry.get("content_type", "") or ""),
                data=_decode_bytes(str(entry.get("data_base64", "") or "")),
                relationship_id=str(entry.get("relationship_id", "") or ""),
                relationship_type=str(entry.get("relationship_type", "") or ""),
                relationships=tuple(
                    WordPartRelationship(
                        relationship_id=str(item.get("relationship_id", "") or ""),
                        relationship_type=str(item.get("relationship_type", "") or ""),
                        target=str(item.get("target", "") or ""),
                        is_external=bool(item.get("is_external", False)),
                    )
                    for item in _entries(entry.get("relationships"))
                ),
            )
            for entry in _entries(origin.get("parts"))
        ),
    )


def _section_origin_to_tier(origin: SectionOrigin) -> dict[str, Any]:
    """Return the tier's form of the blocks a section was imported from."""
    return {
        "heading": _block_to_tier(origin.heading) if origin.heading is not None else None,
        "blocks": [_block_to_tier(block) for block in origin.blocks],
        "text": origin.text,
    }


def _section_origin_from_tier(origin: Mapping[str, Any]) -> SectionOrigin:
    """Return the Python model of the blocks a section was imported from."""
    heading = origin.get("heading")
    return SectionOrigin(
        heading=_block_from_tier(heading) if isinstance(heading, Mapping) else None,
        blocks=tuple(
            _block_from_tier(entry) for entry in _entries(origin.get("blocks"))
        ),
        text=str(origin.get("text", "") or ""),
    )


def _block_to_tier(block: WordBlock) -> dict[str, Any]:
    """Return the tier's form of one Word block."""
    return {
        "block_id": block.block_id,
        "kind": block.kind.value,
        "text": block.text,
        "level": block.level,
        "list_marker": block.list_marker.value if block.list_marker is not None else None,
        "style": block.style,
        "runs": [_run_to_tier(run) for run in block.runs],
        "table": _table_to_tier(block.table) if block.table is not None else None,
    }


def _block_from_tier(block: Mapping[str, Any]) -> WordBlock:
    """Return the Python model of one Word block."""
    marker = block.get("list_marker")
    table = block.get("table")
    return WordBlock(
        block_id=str(block.get("block_id", "") or ""),
        kind=WordBlockKind(str(block.get("kind", "paragraph"))),
        text=str(block.get("text", "") or ""),
        level=int(block.get("level", 0) or 0),
        list_marker=(ListMarker(str(marker)) if marker is not None else None),
        style=(str(block["style"]) if block.get("style") is not None else None),
        runs=_runs_from_tier(block.get("runs")),
        table=_table_from_tier(table) if isinstance(table, Mapping) else None,
    )


def _run_to_tier(run: WordRun) -> dict[str, Any]:
    """Return the tier's form of one Word run."""
    return {
        "text": run.text,
        "bold": run.bold,
        "italic": run.italic,
        "underline": run.underline,
        "superscript": run.superscript,
        "subscript": run.subscript,
        "strike": run.strike,
        "font": run.font,
        "size": run.size,
        "color": run.color,
        "highlight": run.highlight,
        "hyperlink": run.hyperlink,
        "footnote_ref": run.footnote_ref,
        "endnote_ref": run.endnote_ref,
        "comment_refs": list(run.comment_refs),
        "image_ref": run.image_ref,
        "equation_xml": run.equation_xml,
        "part_ref": run.part_ref,
    }


def _run_from_tier(run: Mapping[str, Any]) -> WordRun:
    """Return the Python model of one Word run."""
    return WordRun(
        text=str(run.get("text", "") or ""),
        bold=bool(run.get("bold", False)),
        italic=bool(run.get("italic", False)),
        underline=bool(run.get("underline", False)),
        superscript=bool(run.get("superscript", False)),
        subscript=bool(run.get("subscript", False)),
        strike=bool(run.get("strike", False)),
        font=(str(run["font"]) if run.get("font") is not None else None),
        size=(int(run["size"]) if run.get("size") is not None else None),
        color=(str(run["color"]) if run.get("color") is not None else None),
        highlight=(str(run["highlight"]) if run.get("highlight") is not None else None),
        hyperlink=(str(run["hyperlink"]) if run.get("hyperlink") is not None else None),
        footnote_ref=(
            str(run["footnote_ref"]) if run.get("footnote_ref") is not None else None
        ),
        endnote_ref=(
            str(run["endnote_ref"]) if run.get("endnote_ref") is not None else None
        ),
        comment_refs=tuple(str(ref) for ref in run.get("comment_refs", []) or ()),
        image_ref=(str(run["image_ref"]) if run.get("image_ref") is not None else None),
        equation_xml=(
            str(run["equation_xml"]) if run.get("equation_xml") is not None else None
        ),
        part_ref=(str(run["part_ref"]) if run.get("part_ref") is not None else None),
    )


def _table_to_tier(table: WordTable) -> dict[str, Any]:
    """Return the tier's form of one Word table."""
    return {
        "header_rows": table.header_rows,
        "column_widths": list(table.column_widths),
        "rows": [
            {
                "cells": [
                    _cell_to_tier(cell) for cell in row.cells
                ]
            }
            for row in table.rows
        ],
    }


def _table_from_tier(table: Mapping[str, Any]) -> WordTable:
    """Return the Python model of one Word table."""
    return WordTable(
        rows=tuple(
            WordTableRow(
                cells=tuple(
                    _cell_from_tier(cell)
                    for cell in _entries(row.get("cells"))
                    if isinstance(cell, Mapping)
                )
            )
            for row in _entries(table.get("rows"))
            if isinstance(row, Mapping)
        ),
        header_rows=int(table.get("header_rows", 0) or 0),
        column_widths=tuple(
            int(width) for width in table.get("column_widths", []) or ()
        ),
    )


def _cell_to_tier(cell: WordTableCell) -> dict[str, Any]:
    """Return the tier's form of one Word table cell."""
    return {
        "text": cell.text,
        "row_span": cell.row_span,
        "col_span": cell.col_span,
        "is_header": cell.is_header,
        "is_merge_continuation": cell.is_merge_continuation,
        "width": cell.width,
        "runs": [_run_to_tier(run) for run in cell.runs],
        "tables": [_table_to_tier(table) for table in cell.tables],
    }


def _cell_from_tier(cell: Mapping[str, Any]) -> WordTableCell:
    """Return the Python model of one Word table cell."""
    return WordTableCell(
        text=str(cell.get("text", "") or ""),
        row_span=int(cell.get("row_span", 1) or 1),
        col_span=int(cell.get("col_span", 1) or 1),
        is_header=bool(cell.get("is_header", False)),
        is_merge_continuation=bool(cell.get("is_merge_continuation", False)),
        width=(int(cell["width"]) if cell.get("width") is not None else None),
        runs=_runs_from_tier(cell.get("runs")),
        tables=tuple(
            _table_from_tier(table)
            for table in _entries(cell.get("tables"))
            if isinstance(table, Mapping)
        ),
    )


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


def _upsert_entry(
    manifest: BookManifest,
    section_id: str,
    content: str,
    *,
    title: str,
    order: int | None,
) -> BookSection:
    """Return the index entry of a section, as the tier would write it.

    The tier's rule: an empty title keeps the title the section already has,
    an order of ``None`` appends the section after the last one, and the
    counts are the counts of the body.
    """
    existing = _find_section(manifest, section_id)
    if existing is None:
        return BookSection(
            section_id=section_id,
            title=title.strip(),
            order=order if order is not None else _next_order(manifest.sections),
            word_count=_count_words(content),
            char_count=len(content),
        )
    return BookSection(
        section_id=section_id,
        title=existing.title if not title else title.strip(),
        order=existing.order if order is None else order,
        word_count=_count_words(content),
        char_count=len(content),
        chunk_ids=list(existing.chunk_ids),
    )


def _count_words(content: str) -> int:
    """Return the number of words in a body.

    The counts are the Python's: they are code points and Python's
    whitespace, where the tier's are UTF-16 units and Unicode's
    ``White_Space``.  The two agree on every text that is plain ASCII.
    """
    return len(content.split())


def _find_section(manifest: BookManifest, section_id: str) -> BookSection | None:
    """Return the index entry of a section, when the book holds one."""
    for entry in manifest.sections:
        if entry.section_id == section_id:
            return entry
    return None


def _next_order(sections: Sequence[BookSection]) -> int:
    """Return the order a new section is appended at."""
    return max((entry.order for entry in sections), default=-1) + 1


def _sorted_sections(sections: Sequence[BookSection]) -> list[BookSection]:
    """Return the sections in book order: by position, then by id."""
    return sorted(sections, key=lambda section: (section.order, section.section_id))


def _with_sections(manifest: BookManifest, sections: Sequence[BookSection]) -> BookManifest:
    """Return the manifest with a new set of sections."""
    return manifest.model_copy(
        update={
            "sections": list(sections),
            "section_count": len(sections),
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(operation: str, action: Callable[[], _T]) -> _T:
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


def _entries(value: Any) -> list[Mapping[str, Any]]:
    """Return the entries of a list of objects, skipping what is not one."""
    if not isinstance(value, (list, tuple)):
        return []
    return [entry for entry in value if isinstance(entry, Mapping)]


def _runs_from_tier(value: Any) -> tuple[WordRun, ...]:
    """Return the runs of a tier list."""
    return tuple(_run_from_tier(entry) for entry in _entries(value))


def _timestamp(value: Any) -> str | None:
    """Return a timestamp in the form the tier writes."""
    if value is None:
        return None
    return str(value)


def _parse_timestamp(value: Any) -> Any:
    """Return the timestamp the tier wrote, as the Python model holds it."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return value
        finally:
            pass
    return value


def _encode_bytes(data: bytes) -> str:
    """Return the bytes of a part, base64-encoded."""
    return base64.b64encode(data).decode("ascii")


def _decode_bytes(data: str) -> bytes:
    """Return the bytes of a part, from their base64 form."""
    if not data:
        return b""
    try:
        return base64.b64decode(data.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise LibraryError(f"a stored part holds data that is not base64: {exc}") from exc


def _require_text(value: str, field: str) -> None:
    """Raise when a value is not a string."""
    if not isinstance(value, str):
        raise LibraryError(f"{field} must be a string, got {type(value).__name__}")


def _require_order(order: int | None) -> None:
    """Raise when an order is not a position."""
    if order is None:
        return
    if not isinstance(order, int) or isinstance(order, bool) or order < 0:
        raise LibraryError(f"a section order must be a non-negative integer, got {order!r}")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write a file atomically, so that a reader never sees half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


__all__ = [
    "BookOrigin",
    "BookStore",
    "CHUNKS_FILENAME",
    "MANIFEST_FILENAME",
    "MAX_IDENTIFIER_LENGTH",
    "ORIGIN_FILENAME",
    "SECTIONS_DIRNAME",
    "SectionOrigin",
    "SectionRecord",
    "UnsafeIdentifierError",
    "validate_identifier",
]
