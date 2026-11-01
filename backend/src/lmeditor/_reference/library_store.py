"""The frozen on-disk format of the book library.

This is the original Python implementation of ``library.store``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The models come with it: the live ``lmeditor.library.store`` is now a door to
the Kotlin tier, and a witness that leaned on the door it is supposed to
witness would prove nothing.
"""

from __future__ import annotations

import base64
import binascii
import logging
import shutil
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePath, PureWindowsPath
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from lmeditor.core.exceptions import BookNotFoundError, LibraryError, SectionNotFoundError
from lmeditor.core.schemas import BookManifest, BookSection, SourceChunk
from lmeditor.word.model import (
    WordBlock,
    WordComment,
    WordEndnote,
    WordFootnote,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordProperties,
    WordSection,
    WordStyle,
)


logger = logging.getLogger(__name__)

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


class _StoredMedia(BaseModel):
    """A media part as it is written to disk.

    The bytes of an image are not text, and JSON has no way to hold them, so
    they travel base64-encoded.  A package part that was read as bytes and
    written as text would come back corrupted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_id: str = Field(min_length=1)
    part_name: str = Field(min_length=1)
    content_type: str = ""
    data_base64: str = ""
    relationship_id: str = ""


class _StoredPart(BaseModel):
    """A package part as it is written to disk, with its bytes base64-encoded.

    The relationships the part holds travel with it.  A chart's colour style
    and the workbook embedded behind it are reached from the chart's *own*
    relationship list rather than from ``document.xml``, so a part stored
    without them would come back disconnected from the package around it and
    the exported file would lose them.

    The relationship itself is already plain text, so it is kept in the shape
    the model gives it: a small object with the id, the type, the target and
    the flag that says the target lies outside the package.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    part_name: str = Field(min_length=1)
    content_type: str = ""
    data_base64: str = ""
    relationship_id: str = ""
    relationship_type: str = ""
    relationships: tuple[WordPartRelationship, ...] = Field(
        default=(),
        description="Relationships the part holds to other parts of the package",
    )


class _OriginFile(BaseModel):
    """The on-disk form of a book's origin record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    properties: WordProperties = Field(default_factory=WordProperties)
    footnotes: tuple[WordFootnote, ...] = Field(default=())
    endnotes: tuple[WordEndnote, ...] = Field(default=())
    comments: tuple[WordComment, ...] = Field(default=())
    headers: tuple[str, ...] = Field(default=())
    footers: tuple[str, ...] = Field(default=())
    sections: tuple[WordSection, ...] = Field(default=())
    styles: tuple[WordStyle, ...] = Field(default=())
    media: tuple[_StoredMedia, ...] = Field(default=())
    parts: tuple[_StoredPart, ...] = Field(default=())


class _SectionFile(BaseModel):
    """The on-disk body of one section: its text, its counts and its origin.

    The chunk ids of a section are *not* stored here: they belong to the
    manifest, which is the index of the book. Keeping them out of the section
    file means a section can be re-indexed without rewriting its body.

    The origin is stored here rather than in a file of its own, so that a
    section stays one file: reading a section's original blocks costs no
    second read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(min_length=1)
    title: str = ""
    order: int = Field(default=0, ge=0)
    word_count: int = Field(default=0, ge=0)
    char_count: int = Field(default=0, ge=0)
    content: str = ""
    origin: SectionOrigin | None = Field(
        default=None,
        description="The blocks the section was imported from, when it has any",
    )


class BookStore:
    """A library of books kept on disk, one directory per book.

    The store is the only thing that touches the filesystem in this domain.
    Everything else -- the brief, the working set, the importer -- goes through
    it, so the rules about what is read when, and what is written atomically,
    live in one place.

    Args:
        root: The directory the books are kept in. It is created when the
            first book is written, not when the store is constructed.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._read_count = 0

    @property
    def root(self) -> Path:
        """Return the directory the store keeps its books in."""
        return self._root

    @property
    def read_count(self) -> int:
        """Return the number of section bodies read from disk so far.

        The counter is what makes the bounded-context property measurable
        rather than merely asserted: a working set built for one section of a
        thousand-section book reads ``2 * neighbours + 1`` bodies, and this is
        where that is counted.

        Only *section bodies* are counted, and the count is the bound on how
        many section bodies are pulled into the process.  Reading a book's
        origin record is not one: it is a single file that holds no section
        body, so it costs one file read and no count.  A write that carries a
        section's origin forward does read that section's body, and is
        counted like any other read.
        """
        return self._read_count

    def create(self, book_id: str, title: str = "") -> BookManifest:
        """Create the book ``book_id`` and return its manifest.

        Creating a book that already exists returns the existing manifest
        instead of overwriting it: the call is idempotent, so an import that
        runs twice does not discard the book the first run produced.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            LibraryError: the title is not a string.
        """
        book_id = validate_identifier(book_id, "book_id")
        _require_text(title, "a book title")
        manifest_path = self._manifest_path(book_id)
        if manifest_path.is_file():
            return self._read_manifest(manifest_path, book_id)
        now = datetime.now(timezone.utc)
        manifest = BookManifest(
            book_id=book_id,
            title=title,
            created_at=now,
            modified_at=now,
        )
        self._sections_dir(book_id).mkdir(parents=True, exist_ok=True)
        self._write_manifest(manifest)
        return manifest

    def open(self, book_id: str) -> BookManifest:
        """Return the manifest of the book ``book_id``.

        The manifest is the index of the book: the section titles, the counts
        and the running brief. No section body is read.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the manifest cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        manifest_path = self._manifest_path(book_id)
        if not manifest_path.is_file():
            raise BookNotFoundError(book_id)
        return self._read_manifest(manifest_path, book_id)

    def list_books(self) -> list[BookManifest]:
        """Return the manifest of every book in the library, ordered by book id.

        A directory that holds no manifest is not a book and is skipped; a
        manifest that cannot be parsed is an error, because a book the
        library cannot read must not be silently omitted from the list.
        """
        if not self._root.is_dir():
            return []
        manifests: list[BookManifest] = []
        for entry in sorted(self._root.iterdir(), key=lambda item: item.name):
            if not entry.is_dir():
                continue
            manifest_path = entry / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            manifests.append(self._read_manifest(manifest_path, entry.name))
        return manifests

    def delete(self, book_id: str) -> None:
        """Delete the book ``book_id`` and everything it holds.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the book could not be removed.
        """
        book_id = validate_identifier(book_id, "book_id")
        book_dir = self._book_dir(book_id)
        if not (book_dir / MANIFEST_FILENAME).is_file():
            raise BookNotFoundError(book_id)
        try:
            shutil.rmtree(book_dir)
        except OSError as exc:
            raise LibraryError(
                f"could not delete the book '{book_id}': {exc}",
                details={"book_id": book_id, "path": str(book_dir)},
            ) from exc

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
        manifest = self._load_manifest(book_id)
        updated, section = self._upsert_section(
            manifest, section_id, content, title=title, order=order, origin=origin
        )
        self._write_manifest(self._touched(updated))
        return section

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
        path = self._section_path(book_id, section_id)
        if not path.is_file():
            if not self._manifest_path(book_id).is_file():
                raise BookNotFoundError(book_id)
            raise SectionNotFoundError(book_id, section_id)
        payload = self._read_section_file(path, book_id, section_id)
        self._read_count += 1
        return SectionRecord(
            section_id=payload.section_id,
            title=payload.title,
            order=payload.order,
            content=payload.content,
            origin=payload.origin,
        )

    def write_section(self, book_id: str, section_id: str, content: str) -> BookSection:
        """Replace the body of an existing section and return its index entry.

        The word and character counts are recomputed from the new body. The
        section's title, its position and its chunk ids are kept.

        The section's origin is kept as well: the body is what the editor
        changed, and the origin is the record of what the document looked
        like before.  Carrying it forward costs one read of the section's
        file, which is counted, and it lets an export tell an edited section
        from an untouched one without guessing.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            LibraryError: the content is not a string.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        _require_text(content, "section content")
        manifest = self._load_manifest(book_id)
        existing = _find_section(manifest, section_id)
        if existing is None:
            raise SectionNotFoundError(book_id, section_id)
        origin = self._read_section_origin(book_id, section_id)
        section = existing.model_copy(
            update={
                "word_count": _count_words(content),
                "char_count": len(content),
            }
        )
        self._write_section_file(book_id, section, content, origin=origin)
        sections = [
            entry if entry.section_id != section_id else section for entry in manifest.sections
        ]
        updated = _with_sections(manifest, sections)
        self._write_manifest(self._touched(updated))
        return section

    def section_count(self, book_id: str) -> int:
        """Return the number of sections in a book, without reading any body.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        manifest = self._load_manifest(book_id)
        return len(manifest.sections)

    def set_section_chunks(
        self, book_id: str, section_id: str, chunk_ids: Sequence[str]
    ) -> BookSection:
        """Record the corpus chunks that belong to a section.

        The chunk ids live in the manifest, which is the index of the book, so
        re-indexing a section never rewrites its body.

        Raises:
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
        """
        book_id = validate_identifier(book_id, "book_id")
        section_id = validate_identifier(section_id, "section_id")
        manifest = self._load_manifest(book_id)
        existing = _find_section(manifest, section_id)
        if existing is None:
            raise SectionNotFoundError(book_id, section_id)
        section = existing.model_copy(update={"chunk_ids": list(chunk_ids)})
        sections = [
            entry if entry.section_id != section_id else section for entry in manifest.sections
        ]
        updated = _with_sections(manifest, sections)
        self._write_manifest(self._touched(updated))
        return section

    def put_chunks(self, book_id: str, chunks: Sequence[SourceChunk]) -> None:
        """Store the corpus chunks of a book and record how many there are.

        The chunks are added to the book's corpus rather than replacing it: a
        chunk id is a hash of the chunk's text and its offsets, so two
        sections that carry the same text produce the same chunk id, and a
        chunk that is already stored is kept once. Indexing one section
        therefore never drops the chunks of another, and indexing the same
        section twice changes nothing.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: a chunk is not a ``SourceChunk``.
        """
        book_id = validate_identifier(book_id, "book_id")
        manifest = self._load_manifest(book_id)
        stored: dict[str, SourceChunk] = {chunk.chunk_id: chunk for chunk in self.chunks(book_id)}
        for chunk in _unique_chunks(chunks):
            stored.setdefault(chunk.chunk_id, chunk)
        ordered = list(stored.values())
        self._write_chunks_file(book_id, ordered)
        updated = manifest.model_copy(update={"chunk_count": len(ordered)})
        self._write_manifest(self._touched(updated))

    def chunks(self, book_id: str) -> list[SourceChunk]:
        """Return the corpus chunks stored for a book.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the chunk file cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        path = self._chunks_path(book_id)
        if not path.is_file():
            if not self._manifest_path(book_id).is_file():
                raise BookNotFoundError(book_id)
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LibraryError(
                f"could not read the chunks of book '{book_id}': {exc}",
                details={"book_id": book_id, "path": str(path)},
            ) from exc
        chunks: list[SourceChunk] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                chunks.append(SourceChunk.model_validate_json(line))
            except PydanticValidationError as exc:
                raise LibraryError(
                    f"line {number} of the chunk file of book '{book_id}' is not valid: {exc}",
                    details={"book_id": book_id, "line": number},
                ) from exc
        return chunks

    def brief(self, book_id: str) -> str:
        """Return the book's running brief: the cached digest of its contents.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
        """
        manifest = self._load_manifest(book_id)
        return manifest.brief

    def read_book_origin(self, book_id: str) -> BookOrigin | None:
        """Return the document a book was imported from, or ``None``.

        A book that was created from a list of sections has no origin: there
        is no document behind it, and the export renders it from its text.
        A book that was imported from a Word document has one, and the export
        rebuilds the document from it.

        The origin holds no section body, so reading it is not a read of the
        book and :attr:`read_count` does not move: the counter stays the
        bound on how many section bodies are pulled into the process.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the origin file cannot be read or is not valid.
        """
        book_id = validate_identifier(book_id, "book_id")
        if not self._manifest_path(book_id).is_file():
            raise BookNotFoundError(book_id)
        path = self._origin_path(book_id)
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LibraryError(
                f"could not read the origin of book '{book_id}': {exc}",
                details={"book_id": book_id, "path": str(path)},
            ) from exc
        try:
            stored = _OriginFile.model_validate_json(text)
        except PydanticValidationError as exc:
            raise LibraryError(
                f"the origin of book '{book_id}' is not valid: {exc}",
                details={"book_id": book_id, "path": str(path)},
            ) from exc
        return _origin_from_file(stored)

    def set_book_origin(self, book_id: str, origin: BookOrigin) -> None:
        """Record the document a book was imported from.

        The record replaces any the book held: an import that brings a new
        document brings new notes, new media and new parts, and the old ones
        would otherwise be written into the exported file alongside them.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the origin could not be written.
        """
        book_id = validate_identifier(book_id, "book_id")
        if not self._manifest_path(book_id).is_file():
            raise BookNotFoundError(book_id)
        path = self._origin_path(book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _origin_to_file(origin)
        _atomic_write_text(path, payload.model_dump_json(indent=2))

    def update_brief(self, book_id: str, brief: str) -> None:
        """Replace the book's running brief.

        Raises:
            UnsafeIdentifierError: the book id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            LibraryError: the brief is not a string.
        """
        book_id = validate_identifier(book_id, "book_id")
        _require_text(brief, "a brief")
        manifest = self._load_manifest(book_id)
        updated = manifest.model_copy(update={"brief": brief})
        self._write_manifest(self._touched(updated))

    def _upsert_section(
        self,
        manifest: BookManifest,
        section_id: str,
        content: str,
        *,
        title: str,
        order: int | None,
        origin: SectionOrigin | None = None,
    ) -> tuple[BookManifest, BookSection]:
        """Return the manifest and the section entry after an upsert."""
        existing = _find_section(manifest, section_id)
        if existing is None:
            section = BookSection(
                section_id=section_id,
                title=title,
                order=order if order is not None else _next_order(manifest.sections),
                word_count=_count_words(content),
                char_count=len(content),
            )
            sections = [*manifest.sections, section]
        else:
            section = BookSection(
                section_id=section_id,
                title=existing.title if not title else title,
                order=existing.order if order is None else order,
                word_count=_count_words(content),
                char_count=len(content),
                chunk_ids=list(existing.chunk_ids),
            )
            sections = [
                entry if entry.section_id != section_id else section for entry in manifest.sections
            ]
        self._write_section_file(manifest.book_id, section, content, origin=origin)
        updated = _with_sections(manifest, _sorted_sections(sections))
        return updated, section

    def _load_manifest(self, book_id: str) -> BookManifest:
        """Return the manifest of a book that is known to exist."""
        path = self._manifest_path(book_id)
        if not path.is_file():
            raise BookNotFoundError(book_id)
        return self._read_manifest(path, book_id)

    def _read_manifest(self, path: Path, book_id: str) -> BookManifest:
        """Read and validate a manifest file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LibraryError(
                f"could not read the manifest of book '{book_id}': {exc}",
                details={"book_id": book_id, "path": str(path)},
            ) from exc
        try:
            return BookManifest.model_validate_json(text)
        except PydanticValidationError as exc:
            raise LibraryError(
                f"the manifest of book '{book_id}' is not valid: {exc}",
                details={"book_id": book_id, "path": str(path)},
            ) from exc

    def _read_section_file(self, path: Path, book_id: str, section_id: str) -> _SectionFile:
        """Read and validate a section file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LibraryError(
                f"could not read section '{section_id}' of book '{book_id}': {exc}",
                details={"book_id": book_id, "section_id": section_id, "path": str(path)},
            ) from exc
        try:
            return _SectionFile.model_validate_json(text)
        except PydanticValidationError as exc:
            raise LibraryError(
                f"the file of section '{section_id}' of book '{book_id}' is not valid: {exc}",
                details={"book_id": book_id, "section_id": section_id, "path": str(path)},
            ) from exc

    def _write_manifest(self, manifest: BookManifest) -> None:
        """Write a book's manifest atomically."""
        path = self._manifest_path(manifest.book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, manifest.model_dump_json(indent=2))

    def _write_section_file(
        self,
        book_id: str,
        section: BookSection,
        content: str,
        *,
        origin: SectionOrigin | None = None,
    ) -> None:
        """Write one section's file atomically.

        The origin travels in the same file as the body: a section stays one
        file, so reading a section's blocks costs no second read.
        """
        path = self._section_path(book_id, section.section_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _SectionFile(
            section_id=section.section_id,
            title=section.title,
            order=section.order,
            word_count=section.word_count,
            char_count=section.char_count,
            content=content,
            origin=origin,
        )
        _atomic_write_text(path, payload.model_dump_json(indent=2))

    def _read_section_origin(
        self, book_id: str, section_id: str
    ) -> SectionOrigin | None:
        """Return a section's origin, reading its file once.

        A section whose file is not there has no origin to keep: the file is
        written again by the caller, and a section that is written without a
        file behind it simply starts again. The read is counted when it
        happens, because the section's body is pulled into the process with
        its origin, and the counter is the bound on how many bodies that may
        be.
        """
        path = self._section_path(book_id, section_id)
        if not path.is_file():
            return None
        payload = self._read_section_file(path, book_id, section_id)
        self._read_count += 1
        return payload.origin

    def _write_chunks_file(self, book_id: str, chunks: Sequence[SourceChunk]) -> None:
        """Write a book's chunk file atomically, one JSON object per line."""
        path = self._chunks_path(book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{chunk.model_dump_json()}\n" for chunk in chunks)
        _atomic_write_text(path, body)

    @staticmethod
    def _touched(manifest: BookManifest) -> BookManifest:
        """Return the manifest with its modification time moved forward."""
        return manifest.model_copy(update={"modified_at": datetime.now(timezone.utc)})

    def _book_dir(self, book_id: str) -> Path:
        """Return the directory of a book."""
        return self._root / book_id

    def _manifest_path(self, book_id: str) -> Path:
        """Return the path of a book's manifest."""
        return self._book_dir(book_id) / MANIFEST_FILENAME

    def _sections_dir(self, book_id: str) -> Path:
        """Return the directory of a book's section files."""
        return self._book_dir(book_id) / SECTIONS_DIRNAME

    def _section_path(self, book_id: str, section_id: str) -> Path:
        """Return the path of one section's file."""
        return self._sections_dir(book_id) / f"{section_id}.json"

    def _chunks_path(self, book_id: str) -> Path:
        """Return the path of a book's chunk file."""
        return self._book_dir(book_id) / CHUNKS_FILENAME

    def _origin_path(self, book_id: str) -> Path:
        """Return the path of the file that holds a book's origin."""
        return self._book_dir(book_id) / ORIGIN_FILENAME


def _origin_to_file(origin: BookOrigin) -> _OriginFile:
    """Return the on-disk form of a book's origin record.

    The bytes of the media and of the package parts are base64-encoded on
    the way out: JSON holds text, and an image that was read as bytes must
    come back as the same bytes.
    """
    return _OriginFile(
        properties=origin.properties,
        footnotes=origin.footnotes,
        endnotes=origin.endnotes,
        comments=origin.comments,
        headers=origin.headers,
        footers=origin.footers,
        sections=origin.sections,
        styles=origin.styles,
        media=tuple(_media_to_stored(media) for media in origin.media),
        parts=tuple(_part_to_stored(part) for part in origin.parts),
    )


def _origin_from_file(stored: _OriginFile) -> BookOrigin:
    """Return the origin record a book holds, from its on-disk form."""
    return BookOrigin(
        properties=stored.properties,
        footnotes=stored.footnotes,
        endnotes=stored.endnotes,
        comments=stored.comments,
        headers=stored.headers,
        footers=stored.footers,
        sections=stored.sections,
        styles=stored.styles,
        media=tuple(_media_from_stored(media) for media in stored.media),
        parts=tuple(_part_from_stored(part) for part in stored.parts),
    )


def _media_to_stored(media: WordMedia) -> _StoredMedia:
    """Return a media part in its on-disk form."""
    return _StoredMedia(
        media_id=media.media_id,
        part_name=media.part_name,
        content_type=media.content_type,
        data_base64=base64.b64encode(media.data).decode("ascii"),
        relationship_id=media.relationship_id,
    )


def _media_from_stored(stored: _StoredMedia) -> WordMedia:
    """Return a media part from its on-disk form."""
    return WordMedia(
        media_id=stored.media_id,
        part_name=stored.part_name,
        content_type=stored.content_type,
        data=_decode_bytes(stored.data_base64),
        relationship_id=stored.relationship_id,
    )


def _part_to_stored(part: WordPart) -> _StoredPart:
    """Return a package part in its on-disk form.

    The relationships of the part are carried along unchanged: they are plain
    text already, and dropping them here would lose the parts a chart or an
    embedded object is made of.
    """
    return _StoredPart(
        part_name=part.part_name,
        content_type=part.content_type,
        data_base64=base64.b64encode(part.data).decode("ascii"),
        relationship_id=part.relationship_id,
        relationship_type=part.relationship_type,
        relationships=part.relationships,
    )


def _part_from_stored(stored: _StoredPart) -> WordPart:
    """Return a package part from its on-disk form.

    A part stored before the relationships were kept carries none, and comes
    back with an empty list rather than with an error: the file is older than
    the field, not malformed.
    """
    return WordPart(
        part_name=stored.part_name,
        content_type=stored.content_type,
        data=_decode_bytes(stored.data_base64),
        relationship_id=stored.relationship_id,
        relationship_type=stored.relationship_type,
        relationships=stored.relationships,
    )


def _decode_bytes(encoded: str) -> bytes:
    """Return the bytes a base64 string carries, or empty bytes when it does not.

    A part whose bytes cannot be decoded is lost, and losing it silently
    would hand the writer a package that is missing an image without saying
    so.  The failure is reported instead.
    """
    if not encoded:
        return b""
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LibraryError(
            f"a stored package part carries bytes that are not valid base64: {exc}",
            details={"reason": str(exc)},
        ) from exc


def _find_section(manifest: BookManifest, section_id: str) -> BookSection | None:
    """Return the section with ``section_id``, or ``None`` when there is none."""
    for section in manifest.sections:
        if section.section_id == section_id:
            return section
    return None


def _with_sections(manifest: BookManifest, sections: Sequence[BookSection]) -> BookManifest:
    """Return the manifest with a new section index and the matching count.

    The manifest carries the number of sections as well as the sections
    themselves, and the two must not drift apart.
    """
    return manifest.model_copy(
        update={"sections": list(sections), "section_count": len(sections)}
    )


def _sorted_sections(sections: Sequence[BookSection]) -> list[BookSection]:
    """Return the sections in book order: by position, then by id."""
    return sorted(sections, key=lambda section: (section.order, section.section_id))


def _next_order(sections: Sequence[BookSection]) -> int:
    """Return the position a newly added section takes."""
    if not sections:
        return 0
    return max(section.order for section in sections) + 1


def _count_words(content: str) -> int:
    """Return the number of whitespace-separated words in ``content``."""
    return len(content.split())


def _require_text(value: str, what: str) -> None:
    """Raise when ``value`` is not a string."""
    if not isinstance(value, str):
        raise LibraryError(
            f"{what} must be a string",
            details={"received": type(value).__name__},
        )


def _require_order(order: int | None) -> None:
    """Raise when ``order`` is not a usable position."""
    if order is None:
        return
    if isinstance(order, bool) or not isinstance(order, int):
        raise LibraryError(
            "a section order must be an integer",
            details={"received": type(order).__name__},
        )
    if order < 0:
        raise LibraryError(
            "a section order must not be negative",
            details={"order": order},
        )


def _unique_chunks(chunks: Sequence[SourceChunk]) -> list[SourceChunk]:
    """Return the chunks with their duplicate ids removed, order preserved."""
    unique: dict[str, SourceChunk] = {}
    for chunk in chunks:
        if not isinstance(chunk, SourceChunk):
            raise LibraryError(
                "chunks must be SourceChunk instances",
                details={"received": type(chunk).__name__},
            )
        unique.setdefault(chunk.chunk_id, chunk)
    return list(unique.values())


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` in UTF-8, atomically.

    The text is written to a temporary sibling and then moved into place with
    :meth:`pathlib.Path.replace`, which is atomic on every platform LMeditor
    runs on. An interrupted write therefore leaves the previous version of the
    file intact instead of a half-written book.
    """
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        try:
            temp_path.unlink()
        except OSError as cleanup_error:
            logger.warning(
                "Could not remove the temporary file %s: %s", temp_path, cleanup_error
            )
        raise


__all__ = [
    "CHUNKS_FILENAME",
    "MANIFEST_FILENAME",
    "MAX_IDENTIFIER_LENGTH",
    "ORIGIN_FILENAME",
    "SECTIONS_DIRNAME",
    "BookOrigin",
    "BookStore",
    "SectionOrigin",
    "SectionRecord",
    "UnsafeIdentifierError",
    "validate_identifier",
]
