"""The working set: the bounded slice of a book that reaches the model.

A book is too large to hold in context, but the editor working on one section
needs the section, its immediate neighbours, the passages that bear on it and
the running brief of the book -- and nothing else.  Which sections are read,
how they are truncated, how many passages there are and how large the whole
thing is: those rules are the Kotlin tier's now, and this module is the
**door** over them.

The retrieval itself stays in Python: the index over the book's corpus is
Python's, so the passages it ranks travel with the request and the tier
assembles the bounded set around them.

The implementation this module used to carry now lives in
``lmeditor._reference.library_working_set``, frozen.

No parity check compares the two sides yet: the passages the set is built
around come from a Python index, so a comparison would need one corpus on both
sides.  The gap is declared rather than silent: the ledger
``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py`` names
the module against its Kotlin counterpart
(``kotlin/library/WorkingSet.kt``) and the harness fails if that declaration
stops matching the tree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, LibraryError
from lmeditor.core.schemas import SourceChunk, WorkingSet
from lmeditor.library.store import BookStore
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.tier import library as tier_library

#: What a seam call answers with.
_T = TypeVar("_T")


class WorkingSetBuilder:
    """Builds the bounded context used to edit one section of a book.

    The builder is given the store the book lives in and, optionally, an index
    over the book's corpus. The index is what turns the working set from a
    slice of the book into a *grounded* slice: without it the passages are
    empty, and with it they are the passages that actually bear on the section
    being edited.

    Args:
        store: The library the book is kept in.
        index: An index over the book's chunks. When it is absent the working
            set carries no passages.
    """

    def __init__(self, store: BookStore, *, index: ProvenanceIndex | None = None) -> None:
        self._store = store
        self._index = index

    @property
    def store(self) -> BookStore:
        """Return the library the books are read from."""
        return self._store

    @property
    def index(self) -> ProvenanceIndex | None:
        """Return the index the passages are retrieved from, when there is one."""
        return self._index

    def build(
        self,
        book_id: str,
        section_id: str,
        *,
        neighbours: int = 1,
        max_passages: int = 8,
        max_brief_chars: int = 2000,
        max_section_chars: int = 4000,
    ) -> WorkingSet:
        """Return the bounded working set for editing one section of a book.

        The target section and at most ``neighbours`` sections on either side
        of it are read from disk; no other section of the book is touched.
        Each loaded section is truncated to ``max_section_chars``, the brief
        is truncated to ``max_brief_chars``, and at most ``max_passages``
        passages are retrieved for the section.

        .. note::
           Without an index the builder reads exactly ``2 * neighbours + 1``
           section bodies, and the store's ``read_count`` says so.  With an
           index the door has to read the target's text once more, because
           the retrieval query is built from it and the query is built before
           the tier is asked for the working set; the tier counts that read
           as well.

        Args:
            book_id: The book to edit.
            section_id: The section being edited.
            neighbours: How many sections on either side of the target to
                include.
            max_passages: The most passages to retrieve.
            max_brief_chars: The longest brief to include.
            max_section_chars: The longest section body to include.

        Returns:
            The working set: the target, its neighbours, the retrieved
            passages and the brief, with the total size of all of it in
            ``context_chars``.

        Raises:
            ValueError: a bound is negative.
            UnsafeIdentifierError: an id is not a safe path segment.
            BookNotFoundError: the library holds no such book.
            SectionNotFoundError: the book holds no such section.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is never
                swallowed.
        """
        _require_non_negative(neighbours, "neighbours")
        _require_non_negative(max_passages, "max_passages")
        _require_non_negative(max_brief_chars, "max_brief_chars")
        _require_non_negative(max_section_chars, "max_section_chars")

        ranked = self._rank_passages(book_id, section_id, max_passages, max_section_chars)
        return _call(
            f"build the working set of the section {section_id!r} of the book {book_id!r}",
            lambda: tier_library.build_working_set(
                book_id,
                section_id,
                neighbours,
                max_passages,
                max_brief_chars,
                max_section_chars,
                ranked_passages=ranked,
                root=str(self._store.root),
            ),
        )

    def _rank_passages(
        self,
        book_id: str,
        section_id: str,
        max_passages: int,
        max_section_chars: int,
    ) -> list[SourceChunk] | None:
        """Return the passages the index ranks for the section, or ``None``.

        ``None`` means the tier is to retrieve nothing itself: there is no
        index, or the budget of the passages is zero.
        """
        if self._index is None or max_passages <= 0:
            return None
        target = _call(
            f"read the section {section_id!r} of the book {book_id!r}",
            lambda: tier_library.get_section(
                book_id, section_id, root=str(self._store.root)
            ),
        )
        query = _retrieval_query(
            str(target.get("title", "") or ""),
            str(target.get("content", "") or "")[:max_section_chars],
        )
        if not query.strip():
            return []
        return list(self._index.rank_for_section(query, top_k=max_passages))[:max_passages]


def _retrieval_query(title: str, content: str) -> str:
    """Return the query the passages for a section are retrieved with.

    It is the tier's query, character for character: the title of the section
    and the text of its body, each stripped, joined by a newline.
    """
    parts = [title.strip(), content.strip()]
    return "\n".join(part for part in parts if part)


def _require_non_negative(value: int, name: str) -> None:
    """Raise when a bound is negative."""
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")


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


__all__ = [
    "WorkingSetBuilder",
]
