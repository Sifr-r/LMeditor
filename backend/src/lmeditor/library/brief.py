"""The brief: the bounded, cached memory of what a book contains.

A book is too large to hold in context, but an editor still has to know what
the book is about while they work on one section of it.  The brief is that
knowledge, kept small on purpose: it is derived from the *index* of the book
-- its title and the titles of its sections -- and never from the body of a
section, so it stays the same size whether the book has three sections or
three thousand.

That rule is the Kotlin tier's now (``kotlin/library/BookBrief.kt``), and this
module is the **door** over it.  The implementation it used to carry now lives
in ``lmeditor._reference.library_brief``, frozen.

The parity harness does ask both sides the same question about the bounded
merge: the check "the bounded brief" in
``backend/scripts/verify_kotlin_parity.py`` merges the same briefs on both
sides and compares the answers.  The digest of a whole book is not compared
yet -- it needs a library on both sides -- so that half of the module is
declared against its counterpart in the harness's ledger
``REFERENCE_WITNESSES``.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, LibraryError
from lmeditor.library.store import BookStore
from lmeditor.tier import library as tier_library

#: What a seam call answers with.
_T = TypeVar("_T")

#: The bound the tier's digest is written with.
#:
#: The seam serves the digest the tier builds with its own default, so a
#: shorter bound cannot be asked of the tier; see :func:`build_brief`.
_TIER_BRIEF_CHARS: int = 2000

#: The marker the tier writes when it leaves section titles out.
_OMISSION_MARKER: str = " earlier section title(s) omitted"


def build_brief(store: BookStore, book_id: str, *, max_chars: int = 2000) -> str:
    """Return a bounded digest of the book, derived from its section index.

    The digest names the book and then every section of it, by title.  It
    reads the index only -- no section body is touched -- so the cost of the
    brief does not grow with the length of the book.

    When the section titles do not all fit in ``max_chars``, the oldest are
    dropped and the most recent are kept, and the digest says how many were
    left out.

    Args:
        store: The library the book is kept in.
        book_id: The book to describe.
        max_chars: The longest brief to return.

    Returns:
        The digest, at most ``max_chars`` characters long.

    Raises:
        BookNotFoundError: the library holds no such book.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    if max_chars <= 0:
        return ""
    brief = _call(
        f"build the brief of the book {book_id!r}",
        lambda: tier_library.build_brief(book_id, root=str(store.root)),
    )
    if max_chars >= _TIER_BRIEF_CHARS:
        return brief
    # The seam serves the digest the tier builds with its own bound, and the
    # bound cannot be carried across it.  The tier's rule -- keep the most
    # recent titles that fit, and say how many were left out -- is therefore
    # applied here, to the digest the tier answered with.  It is the one part
    # of the brief rule the Python side keeps.
    return _bound_brief(brief, store, book_id, max_chars)


def merge_brief(current: str, addition: str, max_chars: int = 2000) -> str:
    """Return the brief with ``addition`` appended, bounded to ``max_chars``.

    When the result is too long the *front* is trimmed, whole lines at a time,
    so the brief keeps the most recent information.  A single line that is
    longer than the budget on its own is cut from the front as well, because a
    brief that exceeds its budget is a brief that will not fit in a context
    window.

    .. note::
       The seam serves no method that merges two briefs, so this rule is
       still the Python one.  It is the same rule as the Kotlin tier's
       ``mergeBrief``, line for line, and the parity harness compares the
       two.

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
    combined = tail if not head else f"{head}\n{tail}"
    return _trim_front(combined, max_chars)


def _bound_brief(brief: str, store: BookStore, book_id: str, max_chars: int) -> str:
    """Return the tier's digest, bounded to a shorter budget.

    The digest the tier answered with is already the rule's answer at the
    tier's own bound; the same rule, applied to the same entries, is what a
    shorter bound asks for.
    """
    lines = brief.split("\n")
    if len(lines) < 2:
        return brief[:max_chars]
    header, summary = lines[0], lines[1]
    rest = lines[2:]
    omitted = 0
    if rest and rest[0].endswith(_OMISSION_MARKER):
        try:
            omitted = int(rest[0].split(" ")[1])
        except (IndexError, ValueError):
            omitted = 0
        rest = rest[1:]
    total = len(rest) + omitted
    if total < _section_count(store, book_id):
        # The tier left the marker out because it did not fit: the count of
        # the sections the book holds is the only way to know how many titles
        # were dropped.
        total = _section_count(store, book_id)
    fixed = f"{header}\n{summary}"
    budget = max_chars - len(fixed)
    kept: list[str] = []
    for entry in reversed(rest):
        cost = len(entry) + 1
        if cost > budget:
            break
        kept.append(entry)
        budget -= cost
    kept.reverse()
    left_out = total - len(kept)
    if left_out > 0:
        marker = f"... {left_out} earlier section title(s) omitted"
        if len(marker) + 1 <= budget:
            kept.insert(0, marker)
    return "\n".join([header, summary, *kept])[:max_chars]


def _section_count(store: BookStore, book_id: str) -> int:
    """Return the number of sections a book holds."""
    try:
        return store.section_count(book_id)
    except LMeditorError:
        return 0


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


def _call(operation: str, action: Callable[[], _T]) -> _T:
    """Run a seam call, keeping the tier's refusals as they are."""
    try:
        return action()
    except BridgeUnavailableError:
        raise
    except LMeditorError:
        raise
    except Exception as exc:  # pragma: no cover - the tier's own failures
        raise LibraryError(f"the Kotlin tier could not {operation}: {exc}") from exc


__all__ = [
    "build_brief",
    "merge_brief",
]
