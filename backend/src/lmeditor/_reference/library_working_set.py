"""The frozen bounded-context rules of the book library.

This is the original Python implementation of ``library.working_set``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The store type comes from the frozen copy beside it, not from the live
``lmeditor.library.store``: the live module is now a door to the Kotlin tier,
and a witness that leaned on the door it is supposed to witness would prove
nothing.

The index is spelled as the local protocol below: ``lmeditor.provenance.service``
reaches the Kotlin tier, so a witness may not lean on it either.
"""

from __future__ import annotations

from typing import Protocol

from lmeditor.core.exceptions import SectionNotFoundError
from lmeditor.core.schemas import BookSection, SourceChunk, WorkingSet, WorkingSetSection
from lmeditor.core.types import WorkingSetRole
from lmeditor._reference.library_store import BookStore


class PassageRanker(Protocol):
    """The part of the provenance index the working set reads.

    The live ``lmeditor.provenance.service.ProvenanceIndex`` reaches the Kotlin
    tier, so the witness spells the part it uses instead of leaning on it.
    """

    def rank_for_section(self, query: str, top_k: int = 5) -> list[SourceChunk]:
        """Return the passages that best answer ``query``."""
        ...


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

    def __init__(self, store: BookStore, *, index: PassageRanker | None = None) -> None:
        self._store = store
        self._index = index

    @property
    def store(self) -> BookStore:
        """Return the library the books are read from."""
        return self._store

    @property
    def index(self) -> PassageRanker | None:
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
        """
        _require_non_negative(neighbours, "neighbours")
        _require_non_negative(max_passages, "max_passages")
        _require_non_negative(max_brief_chars, "max_brief_chars")
        _require_non_negative(max_section_chars, "max_section_chars")

        manifest = self._store.open(book_id)
        sections = _sorted_sections(manifest.sections)
        target_index = _index_of(sections, section_id)
        if target_index is None:
            raise SectionNotFoundError(book_id, section_id)

        previous = sections[max(0, target_index - neighbours) : target_index]
        following = sections[target_index + 1 : target_index + 1 + neighbours]

        loaded: list[WorkingSetSection] = []
        for section in previous:
            loaded.append(
                self._load_section(book_id, section, WorkingSetRole.PREVIOUS, max_section_chars)
            )
        target = sections[target_index]
        target_content = self._load_section(
            book_id, target, WorkingSetRole.TARGET, max_section_chars
        )
        loaded.append(target_content)
        for section in following:
            loaded.append(
                self._load_section(book_id, section, WorkingSetRole.NEXT, max_section_chars)
            )

        passages = self._retrieve_passages(target, target_content.content, max_passages)
        brief = self._store.brief(book_id)[:max_brief_chars]

        context_chars = (
            sum(len(section.content) for section in loaded)
            + sum(len(passage.text) for passage in passages)
            + len(brief)
        )
        return WorkingSet(
            book_id=book_id,
            target_section_id=section_id,
            sections=loaded,
            passages=passages,
            brief=brief,
            context_chars=context_chars,
        )

    def _load_section(
        self,
        book_id: str,
        section: BookSection,
        role: WorkingSetRole,
        max_section_chars: int,
    ) -> WorkingSetSection:
        """Read one section body and return it as a working-set section."""
        content = self._store.read_section(book_id, section.section_id)
        return WorkingSetSection(
            section_id=section.section_id,
            title=section.title,
            order=section.order,
            role=role,
            content=content[:max_section_chars],
        )

    def _retrieve_passages(
        self,
        target: BookSection,
        content: str,
        max_passages: int,
    ) -> list[SourceChunk]:
        """Return the passages that bear on the target section.

        Without an index there is nothing to retrieve from, and the working
        set carries no passages. The query is the section's own title and
        text, which is what makes the retrieved passages the ones that answer
        this section rather than the ones that happen to be first.
        """
        if self._index is None or max_passages <= 0:
            return []
        query = _retrieval_query(target, content)
        if not query.strip():
            return []
        return list(self._index.rank_for_section(query, top_k=max_passages))[:max_passages]


def _retrieval_query(section: BookSection, content: str) -> str:
    """Return the query the passages for a section are retrieved with."""
    parts = [section.title.strip(), content.strip()]
    return "\n".join(part for part in parts if part)


def _sorted_sections(sections: list[BookSection]) -> list[BookSection]:
    """Return the sections in book order: by position, then by id."""
    return sorted(sections, key=lambda section: (section.order, section.section_id))


def _index_of(sections: list[BookSection], section_id: str) -> int | None:
    """Return the position of a section in the book, or ``None``."""
    for index, section in enumerate(sections):
        if section.section_id == section_id:
            return index
    return None


def _require_non_negative(value: int, name: str) -> None:
    """Raise when a bound is negative."""
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")


__all__ = [
    "WorkingSetBuilder",
]
