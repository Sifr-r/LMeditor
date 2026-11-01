"""The book library: books on disk, an index over them, and a bounded working set.

LMeditor writes books that are too long to hold in a context window. This
package is what makes that possible:

* :class:`~lmeditor.library.store.BookStore` keeps a book as a directory --
  a manifest that indexes it, a file per section, and the corpus chunks the
  book is grounded in. Opening a book reads the index, never the body.
* :func:`~lmeditor.library.importer.import_word_document` and
  :func:`~lmeditor.library.importer.import_sections` bring a book in, one
  section at a time, and index its content. An imported document is kept:
  its blocks travel with the sections they belong to, and its notes, media
  and styles travel with the book.
* :func:`~lmeditor.library.brief.build_brief` and
  :func:`~lmeditor.library.brief.merge_brief` keep the running brief: a
  bounded digest of what the book contains, so an editor always knows what
  they are working inside.
* :class:`~lmeditor.library.working_set.WorkingSetBuilder` assembles the
  *working set*: the target section, its immediate neighbours, the passages
  retrieval considers relevant and the brief. That is the whole of the book
  the language model ever sees, and it is bounded however long the book is.
* :func:`~lmeditor.library.export.build_book_document` renders a book back out
  as a Word document: through the manuscript bridge for a book made of plain
  sections, and from the document it was imported from for a book that keeps
  its tables, its images and its notes.

The package is a leaf: it reads ``core``, ``provenance`` and ``word``, and
nothing in it imports anything else in LMeditor.
"""

from lmeditor.library.brief import build_brief, merge_brief
from lmeditor.library.export import build_book_document
from lmeditor.library.importer import (
    ImportedSection,
    import_sections,
    import_word_document,
    sections_from_word_document,
    sections_with_origin,
)
from lmeditor.library.store import (
    BookOrigin,
    BookStore,
    SectionOrigin,
    SectionRecord,
)
from lmeditor.library.working_set import WorkingSetBuilder

__all__ = [
    "BookOrigin",
    "BookStore",
    "ImportedSection",
    "SectionOrigin",
    "SectionRecord",
    "WorkingSetBuilder",
    "build_book_document",
    "build_brief",
    "import_sections",
    "import_word_document",
    "merge_brief",
    "sections_from_word_document",
    "sections_with_origin",
]
