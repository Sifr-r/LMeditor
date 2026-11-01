"""Rendering a book back out as a Word document.

A book is rendered the way it was made: a book that was imported from a Word
document is rendered from the document it was imported from -- its tables, its
images, its notes and its tracked changes are all put back -- and a book made
of plain sections is rendered through the manuscript bridge.

Those rules are the Kotlin tier's now: they are implemented once, in
``kotlin/library``, and served across the NDJSON seam.  This module is the
**door**: it keeps the exact Python API the rest of the backend calls, and
answers with the tier's answers.  The implementation it used to carry now lives
in ``lmeditor._reference.library_export``, frozen.

No parity check compares the two sides yet: the render ends in the *bytes* of
a ``.docx`` package, so a comparison would need one book on both sides and
would measure the two writers as much as the export rule.  The gap is declared
rather than silent: the ledger ``REFERENCE_WITNESSES`` in
``backend/scripts/verify_kotlin_parity.py`` names the module against its
Kotlin counterpart (``kotlin/library/BookExporter.kt``) and the harness fails
if that declaration stops matching the tree.

The tier answers with the *bytes* of a ``.docx`` package, and the door reads
those bytes back with the reader.  The bytes are the canonical form of the
package, so the round trip is faithful: what comes out is the document the
tier rendered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, LibraryError
from lmeditor.library.store import BookStore
from lmeditor.tier import library as tier_library
from lmeditor.word.model import WordBlock, WordDocument, WordRun, WordTable
from lmeditor.word.reader import WordReader


def build_book_document(store: BookStore, book_id: str) -> WordDocument:
    """Build the Word document for a book.

    A book that was imported from a Word document is rendered from the
    document it was imported from: the blocks of every section, the notes, the
    media, the styles and the page setup all travel back into the file, so an
    export is the document the author wrote, with the sections that were
    edited in between.

    A book made of plain sections has no such document behind it, and is
    rendered through the manuscript bridge instead: a title, the sections as
    headings, and their text as prose.

    Args:
        store: The library the book is kept in.
        book_id: The book to render.

    Returns:
        The document the book is rendered as.

    Raises:
        BookNotFoundError: the library holds no such book.
        SectionNotFoundError: the book names a section that is not there.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    data = _call(
        f"export the book {book_id!r}",
        lambda: tier_library.export_book(book_id, root=str(store.root)),
    )
    document = WordReader().read_bytes(data)
    return _restore_note_ids(store, book_id, document)


def _restore_note_ids(
    store: BookStore, book_id: str, document: WordDocument
) -> WordDocument:
    """Return the document with the names the book holds for its notes.

    A ``.docx`` package numbers its notes: the id of a note in the package is
    an integer, and the reader names the note after it.  The ids the book
    holds are the document's own, and they are what a caller of the export
    expects to see, so they are put back -- and the runs that referred to a
    note are pointed at the name it has in the book again.
    """
    origin = store.read_book_origin(book_id)
    if origin is None:
        return document
    footnote_map = _id_map(
        [note.footnote_id for note in document.footnotes],
        [note.footnote_id for note in origin.footnotes],
    )
    endnote_map = _id_map(
        [note.endnote_id for note in document.endnotes],
        [note.endnote_id for note in origin.endnotes],
    )
    comment_map = _id_map(
        [comment.comment_id for comment in document.comments],
        [comment.comment_id for comment in origin.comments],
    )
    if not (footnote_map or endnote_map or comment_map):
        return document
    blocks = tuple(
        _restore_block(block, footnote_map, endnote_map, comment_map)
        for block in document.blocks
    )
    return document.model_copy(
        update={
            "blocks": blocks,
            "footnotes": tuple(
                note.model_copy(
                    update={"footnote_id": footnote_map.get(note.footnote_id, note.footnote_id)}
                )
                for note in document.footnotes
            ),
            "endnotes": tuple(
                note.model_copy(
                    update={"endnote_id": endnote_map.get(note.endnote_id, note.endnote_id)}
                )
                for note in document.endnotes
            ),
            "comments": tuple(
                comment.model_copy(
                    update={
                        "comment_id": comment_map.get(
                            comment.comment_id, comment.comment_id
                        )
                    }
                )
                for comment in document.comments
            ),
        }
    )


def _id_map(found: list[str], held: list[str]) -> dict[str, str]:
    """Return the map from the name a note has in a package to the one it has.

    The notes of a package and the notes of the book are the same notes in
    the same order; when they are not, nothing is mapped: a wrong name is
    worse than the package's own.
    """
    if len(found) != len(held):
        return {}
    return {
        new: old for new, old in zip(found, held) if new != old
    }


def _restore_block(
    block: WordBlock,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordBlock:
    """Return a block whose note references point at the notes of the book."""
    update: dict[str, object] = {
        "runs": tuple(
            _remap_run(run, footnote_map, endnote_map, comment_map)
            for run in block.runs
        )
    }
    if block.table is not None:
        update["table"] = _restore_table(
            block.table, footnote_map, endnote_map, comment_map
        )
    return block.model_copy(update=update)


def _restore_table(
    table: WordTable,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordTable:
    """Return a table whose cells refer to the notes of the book."""
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
                            _restore_table(
                                nested, footnote_map, endnote_map, comment_map
                            )
                            for nested in cell.tables
                        ),
                    }
                )
            )
        rows.append(row.model_copy(update={"cells": tuple(cells)}))
    return table.model_copy(update={"rows": tuple(rows)})


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

    .. note::
       The seam serves no method that hands out ids, so this rule is still
       the Python one.  It is the same rule as the Kotlin tier's
       ``IdAllocator``, and the parity harness compares the two.
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


def _remap_run(
    run: WordRun,
    footnote_map: Mapping[str, str],
    endnote_map: Mapping[str, str],
    comment_map: Mapping[str, str],
) -> WordRun:
    """Return a run whose note references point at the notes of this document.

    .. note::
       The seam serves no method that remaps the references of a run, so
       this rule is still the Python one.  It is the same rule as the Kotlin
       tier's, and the parity harness compares the two.
    """
    footnote_ref = run.footnote_ref
    if footnote_ref is not None:
        footnote_ref = footnote_map.get(footnote_ref, footnote_ref)
    endnote_ref = run.endnote_ref
    if endnote_ref is not None:
        endnote_ref = endnote_map.get(endnote_ref, endnote_ref)
    comment_refs = tuple(
        comment_map.get(comment_id, comment_id) for comment_id in run.comment_refs
    )
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


__all__ = ["build_book_document"]
