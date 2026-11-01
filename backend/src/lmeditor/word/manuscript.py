"""Manuscript bridge: LMeditor drafts -> the shared Word document model.

This module used to *be* the bridge: it mapped
``lmeditor.core.schemas.DocumentDraft`` -- the manuscript the agent drafted --
onto ``lmeditor.word.model.WordDocument``, so that
:mod:`lmeditor.word.writer` could serialise it to a ``.docx`` without dropping
the grounding evidence.  The rules are the Kotlin tier's now
(``kotlin/word/WordManuscript.kt``), and this module keeps only the Python
API: the same function, the same signature, the same defaults and the same
exceptions, answering with the tier's document.

What stays here is what is genuinely Python's:

* the **boundary**.  The arguments are checked before anything crosses the
  seam: a ``document`` that is not a ``DocumentDraft`` and a ``bibliography``
  that is not a sequence of ``BibliographicRecord`` are refused here, with the
  same :class:`~lmeditor.core.exceptions.WordProcessingError` and the same
  details they always were.
* the **model**.  The tier's answer comes back in the wire form
  ``kotlin/word/WIRE.md`` §2 pins, and is rebuilt into
  :class:`~lmeditor.word.model.WordDocument` by the model itself.

There is deliberately **no fallback** to the Python implementation.  A silent
fallback would hide the very drift the tier exists to remove: two
implementations that disagree, with only one of them in the request path.  If
the tier is not there, the bridge says so.

The frozen original lives in :mod:`lmeditor._reference.word_manuscript`.  It is
the yardstick the parity harness measures the tier against, and it is never
rewritten to follow a Kotlin change.

The rules the frozen original implements -- the binding contract's §7 (M1-M6) --
are these:

* **M1** the document title becomes a ``TITLE`` block; every section
  becomes a ``HEADING`` block at level 1 followed by one block per
  paragraph, heading, list, quote or code block of its content.
* **M2** sections are ordered by ``(order, section_id)``; with
  ``verified_only=True`` only sections that passed verification survive.
* **M3** inline citation markers are consumed from the body text and
  replaced by superscript numeric runs.
* **M4** markers are numbered in first-appearance order and carry a
  footnote holding the cited passage and the id of the chunk it came from.
* **M5** a non-empty bibliography is appended as a ``References`` heading
  plus one paragraph per record.
* **M6** the inputs are never mutated.
"""

from __future__ import annotations

from collections.abc import Sequence

from lmeditor.core.exceptions import WordProcessingError
from lmeditor.core.schemas import BibliographicRecord, DocumentDraft
from lmeditor.tier.word import build_manuscript
from lmeditor.word.model import WordDocument

__all__ = ["build_manuscript_document"]


def build_manuscript_document(
    document: DocumentDraft,
    bibliography: Sequence[BibliographicRecord] = (),
    *,
    verified_only: bool = False,
) -> WordDocument:
    """Build a Word document from a manuscript draft.

    Implements the contract's M1-M6:

    * **M1** the document title becomes a ``TITLE`` block; every section
      becomes a ``HEADING`` block at level 1 followed by one block per
      paragraph, heading, list, quote or code block of its content.
    * **M2** sections are ordered by ``(order, section_id)``; with
      ``verified_only=True`` only sections that passed verification survive.
    * **M3** inline citation markers are consumed from the body text and
      replaced by superscript numeric runs.
    * **M4** markers are numbered in first-appearance order and carry a
      footnote holding the cited passage and the id of the chunk it came from.
    * **M5** a non-empty bibliography is appended as a ``References`` heading
      plus one paragraph per record.
    * **M6** the inputs are never mutated.

    The content of a section is read as the markdown-flavoured syntax the
    editor writes, so a heading, a list, a quote or a code block in the
    manuscript reaches the document as the structure it stands for.

    Args:
        document: The manuscript draft to render.
        bibliography: Bibliographic records for the reference list, in the
            order they should appear.
        verified_only: When true, drop every section that did not pass
            verification -- heading included.

    Returns:
        A :class:`~lmeditor.word.model.WordDocument` ready for the writer.

    Raises:
        WordProcessingError: If ``document`` is not a ``DocumentDraft`` or
            ``bibliography`` is not a sequence of ``BibliographicRecord``.
    """
    _validate_document(document)
    records = _validate_bibliography(bibliography)

    return build_manuscript(
        document,
        records,
        verified_only=verified_only,
    ).document


def _validate_document(document: DocumentDraft) -> None:
    """Reject a non-``DocumentDraft`` argument at the module boundary."""
    if not isinstance(document, DocumentDraft):
        raise WordProcessingError(
            "build_manuscript_document expects a DocumentDraft",
            details={"received": type(document).__name__},
        )


def _validate_bibliography(
    bibliography: Sequence[BibliographicRecord],
) -> tuple[BibliographicRecord, ...]:
    """Return the bibliography as a tuple, rejecting malformed entries.

    The caller's sequence is copied, never mutated.
    """
    if not isinstance(bibliography, Sequence):
        raise WordProcessingError(
            "build_manuscript_document expects a sequence of BibliographicRecord",
            details={"received": type(bibliography).__name__},
        )
    records: list[BibliographicRecord] = []
    for index, record in enumerate(bibliography):
        if not isinstance(record, BibliographicRecord):
            raise WordProcessingError(
                "bibliography entries must be BibliographicRecord instances",
                details={"index": index, "received": type(record).__name__},
            )
        records.append(record)
    return tuple(records)
