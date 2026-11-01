"""The frozen export rules of the book library.

This is the original Python implementation of ``library.export``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The store comes from the frozen copy beside it, not from the live
``lmeditor.library.store``: the live module is now a door to the Kotlin tier,
and a witness that leaned on the door it is supposed to witness would prove
othing.

The manuscript bridge comes from the frozen sibling beside it,
``lmeditor._reference.word_manuscript``: ``lmeditor.word.manuscript`` reaches
the Kotlin tier through ``lmeditor.core.citations``, so a witness may not lean
on it. That sibling carries the same citation grammar, from
``lmeditor._reference.citations``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import NamedTuple

from pydantic import ValidationError as PydanticValidationError

from lmeditor.core.exceptions import BookNotFoundError, LibraryError, SectionNotFoundError
from lmeditor.core.schemas import BookManifest, BookSection, DocumentDraft, SectionDraft
from lmeditor._reference.library_store import BookOrigin, BookStore, SectionOrigin, SectionRecord
from lmeditor._reference.word_manuscript import build_manuscript_document
from lmeditor.word.model import (
    WordBlock,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordRun,
    WordTable,
)


#: -------------------------------------------------------------------
#: The export rules
#: -------------------------------------------------------------------
logger = logging.getLogger(__name__)


def build_book_document(store: BookStore, book_id: str) -> WordDocument:
    """Build the Word document for a book.

    A book that was imported from a Word document is rebuilt from that
    document: a section whose text has not changed keeps the blocks it was
    imported with, and a section whose text has changed is rendered again
    from its new text. A book that was created from a list of sections has no
    document behind it and is rendered from its text alone.

    Args:
        store: The library the book lives in.
        book_id: The book to render.

    Returns:
        The document model, ready for
        :meth:`lmeditor.word.writer.WordWriter.write`.

    Raises:
        BookNotFoundError: If the book is not in the library.
        SectionNotFoundError: If a section the manifest names is missing from
            the book directory.
    """
    manifest = store.open(book_id)
    origin = store.read_book_origin(book_id)
    if origin is None:
        return _manuscript_document(store, manifest, book_id)
    try:
        return _origin_document(store, manifest, book_id, origin)
    except (BookNotFoundError, SectionNotFoundError):
        # The book or a section of it is gone: that is not something a
        # re-render can paper over.
        raise
    except (LibraryError, PydanticValidationError) as exc:
        # The origin could not be understood. Rendering the book from its
        # text keeps the words; giving up would keep neither.
        logger.warning(
            "Could not rebuild the book %r from the document it was imported "
            "from (%s); rendering it from its text instead.",
            book_id,
            exc,
        )
        return _manuscript_document(store, manifest, book_id)


def _manuscript_document(
    store: BookStore, manifest: BookManifest, book_id: str
) -> WordDocument:
    """Render a book from its text, through the manuscript bridge.

    This is the path a book without an origin takes, and the path a book with
    an origin falls back to when that origin cannot be understood.
    """
    sections: list[SectionDraft] = []
    for entry in manifest.sections:
        content = store.read_section(book_id, entry.section_id)
        sections.append(
            SectionDraft(
                section_id=entry.section_id,
                title=entry.title or entry.section_id,
                content=content,
                order=entry.order,
                cited_chunk_ids=list(entry.chunk_ids),
            )
        )

    draft = DocumentDraft(
        document_id=book_id,
        title=manifest.title or book_id,
        sections=sections,
    )
    return build_manuscript_document(draft)


def _origin_document(
    store: BookStore,
    manifest: BookManifest,
    book_id: str,
    origin: BookOrigin,
) -> WordDocument:
    """Rebuild a book from the Word document it was imported from.

    Every section is read once, and its file yields both its text and the
    blocks it was made of, so the decision between an untouched section and
    an edited one costs no second read.
    """
    records = [
        store.read_section_record(book_id, entry.section_id) for entry in manifest.sections
    ]
    ids = _IdAllocator(
        {
            "block": {
                block.block_id
                for record in records
                for block in _origin_blocks(record.origin)
            },
            "footnote": {footnote.footnote_id for footnote in origin.footnotes},
            "endnote": {endnote.endnote_id for endnote in origin.endnotes},
            "comment": {comment.comment_id for comment in origin.comments},
            "list_group": {
                block.list_group
                for record in records
                for block in _origin_blocks(record.origin)
                if block.list_group is not None
            },
        }
    )

    blocks: list[WordBlock] = []
    footnotes: list[WordFootnote] = []
    endnotes: list[WordEndnote] = []
    comments: list[WordComment] = []
    for entry, record in zip(manifest.sections, records):
        rendered = _section_blocks(entry, record, origin, ids)
        blocks.extend(rendered.blocks)
        footnotes.extend(rendered.footnotes)
        endnotes.extend(rendered.endnotes)
        comments.extend(rendered.comments)

    return WordDocument(
        blocks=tuple(blocks),
        properties=origin.properties,
        footnotes=(*origin.footnotes, *footnotes),
        endnotes=(*origin.endnotes, *endnotes),
        comments=(*origin.comments, *comments),
        headers=origin.headers,
        footers=origin.footers,
        sections=origin.sections,
        styles=origin.styles,
        media=origin.media,
        parts=origin.parts,
    )


class _SectionBlocks(NamedTuple):
    """The blocks of one rebuilt section, and the notes they refer to."""

    blocks: list[WordBlock]
    footnotes: list[WordFootnote]
    endnotes: list[WordEndnote]
    comments: list[WordComment]


def _section_blocks(
    entry: BookSection,
    record: SectionRecord,
    origin: BookOrigin,
    ids: _IdAllocator,
) -> _SectionBlocks:
    """Return the blocks of one section of a rebuilt book.

    A section whose text still is the text its blocks rendered to is returned
    as it was imported: that is what keeps its table, its image and its
    tracked change alive. A section whose text has changed is rendered again
    from its new text, so the markdown the editor wrote becomes real Word
    structure.
    """
    section_origin = record.origin
    if section_origin is not None and record.content == section_origin.text:
        if _origin_is_usable(section_origin, origin):
            return _SectionBlocks(_origin_blocks(section_origin), [], [], [])
        logger.warning(
            "The origin of section %r refers to notes the document does not "
            "hold; rendering the section from its text instead.",
            entry.section_id,
        )
    return _rendered_section(entry, record, ids)


def _origin_blocks(section_origin: SectionOrigin | None) -> list[WordBlock]:
    """Return the blocks a section was imported from, heading first.

    The heading comes first because it came first in the document: the
    section's title is the block that starts it.
    """
    if section_origin is None:
        return []
    blocks = list(section_origin.blocks)
    if section_origin.heading is not None:
        blocks.insert(0, section_origin.heading)
    return blocks


def _origin_is_usable(section_origin: SectionOrigin, origin: BookOrigin) -> bool:
    """Report whether a section's origin refers only to notes the book holds.

    A run that points at a footnote the document does not have would make the
    exported package invalid, so such a section is rendered from its text
    rather than exported as it was.
    """
    footnotes = {footnote.footnote_id for footnote in origin.footnotes}
    endnotes = {endnote.endnote_id for endnote in origin.endnotes}
    comments = {comment.comment_id for comment in origin.comments}
    for block in _origin_blocks(section_origin):
        for run in _block_runs(block):
            if run.footnote_ref is not None and run.footnote_ref not in footnotes:
                return False
            if run.endnote_ref is not None and run.endnote_ref not in endnotes:
                return False
            if any(comment_id not in comments for comment_id in run.comment_refs):
                return False
    return True


def _rendered_section(
    entry: BookSection,
    record: SectionRecord,
    ids: _IdAllocator,
) -> _SectionBlocks:
    """Render one section from its text, through the manuscript bridge.

    The bridge is what turns the editor's markdown into real Word structure:
    a heading, lists, a table, bold and italic runs, a fenced code block.
    """
    title = entry.title or entry.section_id
    draft = DocumentDraft(
        document_id=entry.section_id,
        title=title,
        sections=[
            SectionDraft(
                section_id=entry.section_id,
                title=title,
                content=record.content,
                order=entry.order,
                cited_chunk_ids=list(entry.chunk_ids),
            )
        ],
    )
    rendered = build_manuscript_document(draft)
    # The bridge puts the document's title first; the section's own blocks
    # are the ones after it.
    blocks = list(rendered.blocks[1:])
    footnotes = list(rendered.footnotes)
    endnotes = list(rendered.endnotes)
    comments = list(rendered.comments)

    footnote_map = {
        footnote.footnote_id: ids.take("footnote", footnote.footnote_id)
        for footnote in footnotes
    }
    endnote_map = {endnote.endnote_id: ids.take("endnote", endnote.endnote_id) for endnote in endnotes}
    comment_map = {comment.comment_id: ids.take("comment", comment.comment_id) for comment in comments}

    new_footnotes = [
        footnote.model_copy(
            update={
                "footnote_id": footnote_map[footnote.footnote_id],
                "runs": tuple(
                    _remap_run(run, footnote_map, endnote_map, comment_map)
                    for run in footnote.runs
                ),
            }
        )
        for footnote in footnotes
    ]
    new_endnotes = [
        endnote.model_copy(
            update={
                "endnote_id": endnote_map[endnote.endnote_id],
                "runs": tuple(
                    _remap_run(run, footnote_map, endnote_map, comment_map)
                    for run in endnote.runs
                ),
            }
        )
        for endnote in endnotes
    ]
    new_comments = [
        comment.model_copy(
            update={
                "comment_id": comment_map[comment.comment_id],
                "runs": tuple(
                    _remap_run(run, footnote_map, endnote_map, comment_map)
                    for run in comment.runs
                ),
            }
        )
        for comment in comments
    ]

    new_blocks: list[WordBlock] = []
    for block in blocks:
        block_id = ids.take("block", f"{entry.section_id}-{block.block_id}")
        list_group = block.list_group
        if list_group is not None:
            list_group = ids.take("list_group", list_group)
        new_blocks.append(
            _remap_block(block, block_id, list_group, footnote_map, endnote_map, comment_map)
        )
    return _SectionBlocks(new_blocks, new_footnotes, new_endnotes, new_comments)


def _remap_block(
    block: WordBlock,
    block_id: str,
    list_group: str | None,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordBlock:
    """Return a block whose ids and note references belong to this document."""
    update: dict[str, object] = {
        "block_id": block_id,
        "runs": tuple(
            _remap_run(run, footnote_map, endnote_map, comment_map) for run in block.runs
        ),
    }
    if list_group is not None:
        update["list_group"] = list_group
    if block.table is not None:
        update["table"] = _remap_table(block.table, footnote_map, endnote_map, comment_map)
    return block.model_copy(update=update)


def _remap_table(
    table: WordTable,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordTable:
    """Return a table whose cells refer to the notes of this document."""
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append(
                cell.model_copy(
                    update={
                        "runs": tuple(
                            _remap_run(run, footnote_map, endnote_map, comment_map)
                            for run in cell.runs
                        ),
                        "tables": tuple(
                            _remap_table(nested, footnote_map, endnote_map, comment_map)
                            for nested in cell.tables
                        ),
                    }
                )
            )
        rows.append(row.model_copy(update={"cells": tuple(cells)}))
    return table.model_copy(update={"rows": tuple(rows)})


def _remap_run(
    run: WordRun,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordRun:
    """Return a run whose note references point at the notes of this document."""
    footnote_ref = run.footnote_ref
    if footnote_ref is not None:
        footnote_ref = footnote_map.get(footnote_ref, footnote_ref)
    endnote_ref = run.endnote_ref
    if endnote_ref is not None:
        endnote_ref = endnote_map.get(endnote_ref, endnote_ref)
    comment_refs = tuple(comment_map.get(comment_id, comment_id) for comment_id in run.comment_refs)
    if (
        footnote_ref == run.footnote_ref
        and endnote_ref == run.endnote_ref
        and comment_refs == run.comment_refs
    ):
        return run
    return run.model_copy(
        update={
            "footnote_ref": footnote_ref,
            "endnote_ref": endnote_ref,
            "comment_refs": comment_refs,
        }
    )


def _block_runs(block: WordBlock) -> list[WordRun]:
    """Return every run of a block, the ones inside its table included."""
    runs = list(block.runs)
    if block.table is not None:
        runs.extend(_table_runs(block.table))
    return runs


def _table_runs(table: WordTable) -> list[WordRun]:
    """Return every run of a table, the ones in its nested tables included."""
    runs: list[WordRun] = []
    for row in table.rows:
        for cell in row.cells:
            runs.extend(cell.runs)
            for nested in cell.tables:
                runs.extend(_table_runs(nested))
    return runs


class _IdAllocator:
    """Hands out ids that are unique within the document being rebuilt.

    The ids of the document that was imported are taken first, so a new id
    can never collide with one the document already carries. The bridge
    numbers its notes ``f1``, ``f2``, ... and its blocks ``m0001``,
    ``m0002``, ... for every section it renders, so two sections rendered
    apart carry the same ids. A collision is resolved deterministically: the
    first free ``<id>-2``, ``<id>-3``, ... is taken, and the runs that
    referred to the old id are pointed at the new one. An id that named two
    different notes would be a corrupt package.
    """

    def __init__(self, pools: Mapping[str, set[str]]) -> None:
        self._pools: dict[str, set[str]] = {
            name: set(ids) for name, ids in pools.items()
        }

    def take(self, pool: str, desired: str) -> str:
        """Return an id of ``pool`` that no other id of that pool carries."""
        taken = self._pools.setdefault(pool, set())
        candidate = desired
        index = 2
        while candidate in taken:
            candidate = f"{desired}-{index}"
            index += 1
        taken.add(candidate)
        return candidate


__all__ = ["build_book_document"]
