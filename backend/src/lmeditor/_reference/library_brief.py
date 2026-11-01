"""The frozen bounded digest of a book.

This is the original Python implementation of ``library.brief``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The store type comes from the frozen copy beside it, not from the live
``lmeditor.library.store``: the live module is now a door to the Kotlin tier,
and a witness that leaned on the door it is supposed to witness would prove
othing.
"""

from __future__ import annotations

from lmeditor._reference.library_store import BookStore


def build_brief(store: BookStore, book_id: str, *, max_chars: int = 2000) -> str:
    """Return a bounded digest of the book, derived from its section index.

    The digest names the book and then every section of it, by title. It reads
    the manifest only -- no section body is touched -- so the cost of the
    brief does not grow with the length of the book.

    When the section titles do not all fit in ``max_chars``, the oldest are
    dropped and the most recent are kept, and the digest says how many were
    left out. The rule is the one :func:`merge_brief` applies, so the two
    halves of the brief agree on what "recent" means.

    Args:
        store: The library the book is kept in.
        book_id: The book to describe.
        max_chars: The longest brief to return.

    Returns:
        The digest, at most ``max_chars`` characters long.

    Raises:
        UnsafeIdentifierError: the book id is not a safe path segment.
        BookNotFoundError: the library holds no such book.
    """
    if max_chars <= 0:
        return ""
    manifest = store.open(book_id)
    header = f"Book: {manifest.title or manifest.book_id}"
    summary = f"Sections: {len(manifest.sections)}"
    entries = [
        f"{index}. {section.title or section.section_id}"
        for index, section in enumerate(manifest.sections, start=1)
    ]

    fixed = f"{header}\n{summary}"
    budget = max_chars - len(fixed)
    kept: list[str] = []
    for entry in reversed(entries):
        cost = len(entry) + 1
        if cost > budget:
            break
        kept.append(entry)
        budget -= cost
    kept.reverse()

    omitted = len(entries) - len(kept)
    if omitted:
        marker = f"... {omitted} earlier section title(s) omitted"
        if len(marker) + 1 <= budget:
            kept.insert(0, marker)

    return "\n".join([header, summary, *kept])[:max_chars]


def merge_brief(current: str, addition: str, *, max_chars: int = 2000) -> str:
    """Return the brief with ``addition`` appended, bounded to ``max_chars``.

    When the result is too long the *front* is trimmed, whole lines at a time,
    so the brief keeps the most recent information. A single line that is
    longer than the budget on its own is cut from the front as well, because a
    brief that exceeds its budget is a brief that will not fit in a context
    window.

    Args:
        current: The brief as it stands.
        addition: The information to add to it.
        max_chars: The longest brief to return.

    Returns:
        The merged brief, at most ``max_chars`` characters long.
    """
    if max_chars <= 0:
        return ""
    head = current.strip()
    tail = addition.strip()
    if not tail:
        return _trim_front(head, max_chars)
    combined = f"{head}\n{tail}" if head else tail
    return _trim_front(combined, max_chars)


def _trim_front(text: str, max_chars: int) -> str:
    """Return ``text`` trimmed from the front until it fits in ``max_chars``."""
    if len(text) <= max_chars:
        return text
    lines = text.split("\n")
    while len(lines) > 1:
        candidate = "\n".join(lines[1:])
        if len(candidate) <= max_chars:
            return candidate
        lines.pop(0)
    return lines[0][-max_chars:]


__all__ = [
    "build_brief",
    "merge_brief",
]
