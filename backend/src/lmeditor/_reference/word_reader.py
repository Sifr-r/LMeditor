"""The frozen witness of the Word reader.

This is the implementation that used to live in ``lmeditor.word.reader``, kept
exactly as it was when the live module became a door to the Kotlin tier. It is
the yardstick the tier is measured against: ``backend/scripts/verify_kotlin_parity.py``
asks this module and the Kotlin tier the same questions and compares the answers.

Two things make it a witness rather than a mirror:

* it is plain Python, and it never calls across the NDJSON seam -- so the
  comparison is between two independent implementations; and
* it is re-aligned to the tier's *current* semantics only when the tier's
  rule deliberately changes, and every such change is recorded in the
  harness's ``REFERENCE_WITNESSES`` -- so a divergence that is not a
  deliberate change is a finding, not a silent edit to the ruler.

The rules frozen here are the reader's whole behaviour: how a ``.docx`` package
is walked, how styles and numbering are resolved, how tables and merges are
read, how footnotes, endnotes, comments, revisions, media and carried parts
are represented, and the omission register that names what could not be kept.

Reader for Word (OOXML / ``.docx``) packages.

This module is the *whitebox* half of LMeditor's word-processing element: it
maps a ``.docx`` OPC package onto the shared, format-neutral model defined in
:mod:`lmeditor.word.model`. The package is parsed directly with ``zipfile`` and
``lxml`` rather than through ``python-docx``'s ``Document.paragraphs`` /
``Document.tables`` accessors, because those walk only the direct children of
``w:body`` and silently discard content controls (``w:sdt``), text boxes
(``w:txbxContent``), table-cell paragraphs and tracked changes -- exactly the
places a research manuscript hides its evidence.

Behaviour is fixed by ``WORD_PROCESSING_CONTRACT.md`` §5:

* **R1** body children are walked in document order (``w:p``, ``w:tbl``,
  ``w:sdt`` recursed into ``w:sdtContent``); bookmarks, ``w:sectPr`` and
  ``w:proofErr`` are skipped.
* **R2** a paragraph's style is resolved through ``word/styles.xml``,
  including ``w:basedOn`` chains: ``heading 1``..``heading 9`` (or the
  ``Heading1``..``Heading9`` style ids) become headings, ``Title`` becomes a
  title, ``Quote``/``Intense Quote`` become quotes and styles naming ``code``
  or ``preformatted`` become code.
* **R3** ``w:numPr`` (on the paragraph *or* inherited from its style) turns a
  paragraph into a list item; ``w:ilvl`` becomes the nesting level and
  ``w:numId`` the continuity group. The marker is resolved through
  ``word/numbering.xml``.
* **R4** tables are read recursively: cell text is the cell's paragraphs
  joined by a single space, ``w:gridSpan`` sets ``col_span`` and a vertical
  merge is read the way Word writes it -- the cell carrying
  ``w:vMerge w:val="restart"`` keeps the text and reports the number of rows
  the merge occupies, and the cells below it are continuation cells that carry
  no text of their own. A merge covers every grid column its origin spans, and
  the cell that continues it *is* that region: it covers the same columns, so
  the cells that follow it start after the merged region and keep their own
  text. A continuation takes only the columns its row has room for, and a cell
  that then lands inside the region belongs to the merged region even when it
  carries no ``w:vMerge`` of its own -- Word never displays its text. Header
  rows are counted in ``WordTable.header_rows``.
* **R5** hyperlinks resolve their target through
  ``word/_rels/document.xml.rels``; an absolute target is used verbatim, a
  relative one is resolved against the caller's ``base_uri`` -- or preserved
  verbatim and recorded in ``last_omissions`` when there is no base -- and an
  internal anchor resolves to ``#anchor``.
* **R6** footnotes are read from ``word/footnotes.xml`` and referenced from
  runs through ``w:footnoteReference``.
* **R7** tracked revisions are represented explicitly rather than flattened:
  ``w:ins``/``w:moveTo`` content becomes an insertion and ``w:del``/
  ``w:moveFrom`` content becomes a deletion, so deleted text is recoverable
  from the model instead of vanishing on read.
* **R8** text boxes and content controls contribute their paragraphs in place.
* **R9** field codes contribute no text; a page break yields a
  ``PAGE_BREAK`` block, other breaks and tabs are whitespace.
* **R10** headers and footers are read through the section references that
  name them (``default``/``even``/``first``); a document that carries no
  reference at all falls back to part-name order.
* **R11** core properties populate ``WordDocument.properties`` with
  timezone-aware UTC timestamps.
* **R12** the reader is deterministic: identical bytes produce an identical
  document with stable ``b0001``-style block ids.
* **R13** every malformed-package condition surfaces as
  :class:`~lmeditor.core.exceptions.WordParseError`.
* **R14** the package is refused when it is a legacy binary ``.doc``
  document, when it holds more members than the limit allows, when it
  decompresses to more than the size limit allows, or when a member is
  compressed beyond the allowed ratio.

Content that has no representation in the model is not thrown away: the
package parts the reader cannot model -- a chart, an embedded object, a
glossary document -- are *carried through* with their bytes in
:attr:`~lmeditor.word.model.WordDocument.parts`, and the body reference that
anchored them travels on the run, so the writer can put both back into the
package.  What is still lost is recorded in
:attr:`WordReader.last_omissions` instead of disappearing without a trace:
a part that is missing from the package, a relationship that points at
nothing, a body reference that reaches outside the package.

A carried part keeps the relationships it holds itself: a chart is reached
from the document, but its colour style, its quick style and the workbook
behind it are reached from the chart's *own* relationship part, so those
relationships travel with the part.  A relationship part is never carried
itself -- a ``.rels`` file is the relationship list of a sibling part, never
content -- and the size a drawing is drawn at travels on the run that
anchors it, so that Word draws the element at the size it was written at.

The package root reaches parts of its own -- the custom document
properties, a thumbnail -- and those relationships travel with the parts
they reach: a part the document does not reach still has a path into the
package, through the root rather than through ``document.xml``.

Images, equations and symbols are the constructs the reader now *keeps*: the
media part travels in :attr:`~lmeditor.word.model.WordDocument.media` and the
run that embeds it carries the media id, so the image can be written back out;
an equation keeps both its OMML markup and the text the formula reads as, so
it can be searched, quoted and checked; and a symbol is translated into the
character it stands for.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from urllib.parse import urljoin, urlsplit

from lxml import etree
from pydantic import ValidationError

from lmeditor.core.exceptions import WordParseError
from lmeditor.word.formats import describe_unsupported_format
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordHeaderFooterRef,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordProperties,
    WordRevision,
    WordRevisionKind,
    WordRun,
    WordSection,
    WordStyle,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.text import normalize_whitespace, strip_control_characters

#: Namespace URIs of the parts this reader understands.
_NS_W: Final[str] = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PR: Final[str] = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CP: Final[str] = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_NS_DC: Final[str] = "http://purl.org/dc/elements/1.1/"
_NS_DCTERMS: Final[str] = "http://purl.org/dc/terms/"
_NS_M: Final[str] = "http://schemas.openxmlformats.org/officeDocument/2006/math"

#: The namespace of the anchors DrawingML wraps a drawing in.
_NS_WP: Final[str] = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

#: The namespace of the shapes VML draws a legacy graphic with.
_NS_V: Final[str] = "urn:schemas-microsoft-com:vml"

#: Inline elements that carry no readable content.
_INLINE_SKIP: Final[frozenset[str]] = frozenset(
    {
        "pPr",
        "rPr",
        "fldSimple",
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "permStart",
        "permEnd",
        "lastRenderedPageBreak",
        "tbl",
        "instrText",
        "delInstrText",
        "fldChar",
        "separator",
        "continuationSeparator",
        "endnoteRef",
        "sectPr",
    }
)

#: Block-level elements that carry no readable content.
_BLOCK_SKIP: Final[frozenset[str]] = frozenset(
    {
        "bookmarkStart",
        "bookmarkEnd",
        "sectPr",
        "proofErr",
        "permStart",
        "permEnd",
        "pPr",
    }
)

#: The characters the legacy symbol fonts stand for, keyed by font name.
#:
#: Word stores a symbol run as ``<w:sym w:font="Symbol" w:char="F0B7"/>``: the
#: code is the character's position in the font's own character set, not a
#: Unicode value, so it has to be translated through that character set before
#: it can become text again.
#:
#: The ``Symbol`` font keeps the Adobe Symbol encoding -- the one the Unicode
#: Consortium publishes as "Adobe Symbol Encoding to Unicode" -- in which the
#: Greek letters occupy ``0x41``-``0x5A`` and ``0x61``-``0x7A`` and the
#: mathematical signs follow.  The ``Wingdings`` entries are the handful of
#: pictographs that have a Unicode equivalent; ``Wingdings 2``, ``Wingdings 3``
#: and ``Webdings`` carry pictographs that have none, so their codes are
#: reported rather than guessed.
#:
#: A code that is absent from a font's table has no known character.  The
#: reader reports such a code instead of inventing one: a wrong character in a
#: manuscript is worse than a reported gap.
_SYMBOL_CHARS: Final[dict[str, dict[str, str]]] = {
    "symbol": {
        # Greek letters, uppercase.
        "41": "Α",
        "42": "Β",
        "43": "Χ",
        "44": "Δ",
        "45": "Ε",
        "46": "Φ",
        "47": "Γ",
        "48": "Η",
        "49": "Ι",
        "4A": "ϑ",
        "4B": "Κ",
        "4C": "Λ",
        "4D": "Μ",
        "4E": "Ν",
        "4F": "Ο",
        "50": "Π",
        "51": "Θ",
        "52": "Ρ",
        "53": "Σ",
        "54": "Τ",
        "55": "Υ",
        "56": "ς",
        "57": "Ω",
        "58": "Ξ",
        "59": "Ψ",
        "5A": "Ζ",
        # Greek letters, lowercase.
        "61": "α",
        "62": "β",
        "63": "χ",
        "64": "δ",
        "65": "ε",
        "66": "φ",
        "67": "γ",
        "68": "η",
        "69": "ι",
        "6A": "ϕ",
        "6B": "κ",
        "6C": "λ",
        "6D": "μ",
        "6E": "ν",
        "6F": "ο",
        "70": "π",
        "71": "θ",
        "72": "ρ",
        "73": "σ",
        "74": "τ",
        "75": "υ",
        "76": "ϖ",
        "77": "ω",
        "78": "ξ",
        "79": "ψ",
        "7A": "ζ",
        # Arithmetic and set operators.
        "22": "∀",
        "24": "∃",
        "27": "∋",
        "2A": "∗",
        "2D": "−",
        "40": "≅",
        "5C": "∴",
        "5E": "⊥",
        "A3": "≤",
        "B1": "±",
        "B3": "≥",
        "B4": "×",
        "B5": "∝",
        "B6": "∂",
        "B8": "÷",
        "B9": "≠",
        "BA": "≡",
        "BB": "≈",
        "C4": "⊗",
        "C5": "⊕",
        "C6": "∅",
        "C7": "∩",
        "C8": "∪",
        "C9": "⊃",
        "CA": "⊇",
        "CB": "⊄",
        "CC": "⊂",
        "CD": "⊆",
        "CE": "∈",
        "CF": "∉",
        "D8": "¬",
        "D9": "∧",
        "DA": "∨",
        # Arrows, single and double.
        "AB": "↔",
        "AC": "←",
        "AD": "↑",
        "AE": "→",
        "AF": "↓",
        "DB": "⇔",
        "DC": "⇐",
        "DD": "⇑",
        "DE": "⇒",
        "DF": "⇓",
        # Punctuation, ornaments and the remaining common signs.
        "A2": "′",
        "A5": "∞",
        "A7": "♣",
        "A8": "♦",
        "A9": "♥",
        "AA": "♠",
        "B0": "°",
        "B2": "″",
        "B7": "•",
        "BC": "…",
        "BF": "↵",
        "D0": "∠",
        "D1": "∇",
        "D5": "∏",
        "D6": "√",
        "D7": "⋅",
        "E0": "◊",
        "E5": "∑",
        "F2": "∫",
    },
    "wingdings": {
        "6C": "●",
        "6E": "■",
        "E3": "↖",
        "E5": "↙",
        "E7": "←",
        "ED": "↘",
        "F0": "↔",
        "F5": "↗",
        "FB": "✗",
        "FC": "✓",
    },
}

#: The revision elements Word wraps content in, and the kind each one means.
_REVISION_KINDS: Final[dict[str, WordRevisionKind]] = {
    "ins": WordRevisionKind.INSERTION,
    "del": WordRevisionKind.DELETION,
    "moveFrom": WordRevisionKind.MOVE_FROM,
    "moveTo": WordRevisionKind.MOVE_TO,
}

#: The revision kinds whose text is not part of the document's final text.
_DELETION_REVISION_KINDS: Final[frozenset[WordRevisionKind]] = frozenset(
    {WordRevisionKind.DELETION, WordRevisionKind.MOVE_FROM}
)

#: The elements that carry the relationship of an embedded object, in the
#: order they are preferred.
#:
#: A ``w:object`` holds both the embedded object and, usually, the VML shape
#: that draws its preview; the preview image has a relationship of its own, so
#: the object itself has to be picked out of the two.
_OLE_RELATIONSHIP_ELEMENTS: Final[tuple[str, ...]] = (
    "OLEObject",
    "objectEmbed",
    "objectLink",
)

#: The attributes a graphic data element can carry a relationship in, in the
#: order they are preferred.
#:
#: A chart points at its part with ``r:id``; a diagram points at four parts at
#: once -- its data (``r:dm``), its layout, its quick style and its colours --
#: of which the data part is the one the drawing is anchored by.
_GRAPHIC_RELATIONSHIP_ATTRIBUTES: Final[tuple[str, ...]] = ("dm", "id", "embed", "link")

#: The number of English Metric Units in one point.  A DrawingML extent is
#: already written in EMU; a VML size is written in points.
_EMU_PER_POINT: Final[int] = 12700

#: The number of EMU in one unit of a VML size, keyed by the unit's name.
#: Only the units that convert exactly are listed: a size written in a unit
#: that does not convert exactly is no size at all.
_EMU_PER_UNIT: Final[dict[str, int]] = {
    "pt": _EMU_PER_POINT,
    "pc": 12 * _EMU_PER_POINT,
    "in": 72 * _EMU_PER_POINT,
    "cm": 360000,
    "mm": 36000,
}

#: Guard rails against a decompression bomb disguised as a ``.docx`` package.
#: A genuine Word package is a few megabytes; these limits are generous.
#:
#: The size and the ratio are **pinned to the Kotlin tier**. The tier holds a
#: package part to the same size and to the same ratio, and it holds them in
#: ``lmeditor.core.DecompressionLimits``
#: (``kotlin/core/src/main/kotlin/lmeditor/core/DecompressionLimits.kt``),
#: where they are shared with the PDF reader as well -- a package part and a
#: content stream are the same threat, so they are held to the same numbers.
#:
#: A witness that asked the tier what the bound is would agree with it by
#: construction and would prove nothing, so the numbers are restated here.
#: What keeps the two copies together is
#: ``backend/tests/test_witness_decompression_bounds.py``, which fails when
#: they drift apart. Change them **with** the tier's, never on their own.
#:
#: The member count and the package total are the tier's own ``WordReader``
#: limits; no other reader shares them.
_MAX_PACKAGE_MEMBERS: Final[int] = 2048
_MAX_TOTAL_UNCOMPRESSED_BYTES: Final[int] = 256 * 1024 * 1024
_MAX_MEMBER_UNCOMPRESSED_BYTES: Final[int] = 64 * 1024 * 1024
_MAX_COMPRESSION_RATIO: Final[int] = 200

#: The number of bytes read from a package part in one go.
_READ_CHUNK_BYTES: Final[int] = 64 * 1024

#: Fallback content types for media parts, used when the package declares
#: none or the declaration cannot be read.
_DEFAULT_CONTENT_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "emf": "image/x-emf",
    "wmf": "image/x-wmf",
    "svg": "image/svg+xml",
}

#: Package parts that carry no content the model cannot represent.
_IGNORED_PARTS: Final[frozenset[str]] = frozenset(
    {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/numbering.xml",
        "word/settings.xml",
        "word/fontTable.xml",
        "word/webSettings.xml",
        "word/footnotes.xml",
        "word/endnotes.xml",
        "word/comments.xml",
        "docProps/core.xml",
        "docProps/app.xml",
    }
)

_HEADING_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^heading\s*([1-9])$")
_HEADING_ID_RE: Final[re.Pattern[str]] = re.compile(r"^heading([1-9])$")
_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"(\d+)")

#: The ``w:vMerge`` state of the cell that starts a vertical merge.
_MERGE_RESTART: Final[str] = "restart"

#: The ``w:vMerge`` state of the cells a vertical merge covers.
_MERGE_CONTINUE: Final[str] = "continue"


def _w(tag: str) -> str:
    """Return the qualified name of a WordprocessingML element."""
    return f"{{{_NS_W}}}{tag}"


def _r(tag: str) -> str:
    """Return the qualified name of a relationships attribute."""
    return f"{{{_NS_R}}}{tag}"


def _wp(tag: str) -> str:
    """Return the qualified name of a wordprocessingDrawing element."""
    return f"{{{_NS_WP}}}{tag}"


def _v(tag: str) -> str:
    """Return the qualified name of a VML element."""
    return f"{{{_NS_V}}}{tag}"


def _dc(tag: str) -> str:
    """Return the qualified name of a Dublin Core element."""
    return f"{{{_NS_DC}}}{tag}"


def _cp(tag: str) -> str:
    """Return the qualified name of a core-properties element."""
    return f"{{{_NS_CP}}}{tag}"


def _dcterms(tag: str) -> str:
    """Return the qualified name of a Dublin Core terms element."""
    return f"{{{_NS_DCTERMS}}}{tag}"


def _local(element: etree._Element) -> str:
    """Return the local name of an element, or an empty string for comments."""
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _safe_int(value: str | None, default: int) -> int:
    """Parse an integer attribute, falling back to ``default``."""
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _on_off(element: etree._Element | None) -> bool:
    """Interpret a Word on/off element such as ``w:b``."""
    if element is None:
        return False
    value = element.get(_w("val"))
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "on"}


def _underline(element: etree._Element | None) -> bool:
    """Interpret the ``w:u`` underline element."""
    if element is None:
        return False
    value = element.get(_w("val"))
    if value is None:
        return True
    return value.strip().lower() not in {"none", "0", "false"}


def _element_text(element: etree._Element | None) -> str:
    """Return the stripped text of an element, or an empty string."""
    if element is None:
        return ""
    return str(element.text or "").strip()


def _val_attribute(element: etree._Element | None) -> str:
    """Return the ``w:val`` attribute of an element, or an empty string.

    Word carries the value of elements such as ``w:name`` and ``w:basedOn`` in
    an attribute rather than in element text.
    """
    if element is None:
        return ""
    return str(element.get(_w("val")) or "").strip()


def _natural_key(value: str) -> tuple[tuple[int, int, str], ...]:
    """Build a natural sort key so ``header2`` sorts before ``header10``."""
    key: list[tuple[int, int, str]] = []
    for part in _DIGITS_RE.split(value):
        if part.isdigit():
            key.append((0, int(part), ""))
        elif part:
            key.append((1, 0, part))
    return tuple(key)


def _numeric_key(value: str) -> tuple[int, int, str]:
    """Sort numeric ids numerically and non-numeric ids lexicographically."""
    try:
        return (0, int(value), "")
    except ValueError:
        return (1, 0, value)


def _has_uri_scheme(target: str) -> bool:
    """Report whether a target carries a URI scheme and is therefore absolute."""
    try:
        return bool(urlsplit(target).scheme)
    except ValueError:
        # A target that cannot be parsed is kept as it is rather than being
        # resolved against a base it does not belong to.
        return True


def _is_relationship_part(name: str) -> bool:
    """Report whether a package part name names a relationship part.

    A relationship part is the one that lives in a ``_rels`` directory: it is
    the relationship list of a sibling part, never content of its own.  A
    package that offers one as content is a package Word calls corrupt.
    """
    directory = posixpath.dirname(name)
    if not directory:
        return False
    return posixpath.basename(directory) == "_rels"


def _relationship_part_name(part_name: str) -> str:
    """Return the name of the relationship part that belongs to a part.

    The relationship part of a part at ``<dir>/<name>`` is the part at
    ``<dir>/_rels/<name>.rels``.
    """
    directory = posixpath.dirname(part_name)
    base = posixpath.basename(part_name)
    if not directory:
        return f"_rels/{base}.rels"
    return f"{directory}/_rels/{base}.rels"


def _emu_size(width: str | None, height: str | None) -> tuple[int, int] | None:
    """Return a size written in EMU as a pair of integers, or ``None``.

    A size that is not a whole number of EMU cannot be drawn, so it is
    reported as no size at all rather than as a guessed one.
    """
    if width is None or height is None:
        return None
    try:
        return (int(width.strip()), int(height.strip()))
    except ValueError:
        return None


def _emu_from_measure(measure: str | None) -> int | None:
    """Return the number of EMU a VML measure declares, or ``None``.

    A measure in a unit that does not convert exactly -- a pixel, say --
    cannot be turned into EMU without guessing, and a guessed size is worse
    than no size.
    """
    if not measure:
        return None
    for unit, factor in _EMU_PER_UNIT.items():
        if not measure.endswith(unit):
            continue
        number = measure[: -len(unit)].strip()
        try:
            return int(round(float(number) * factor))
        except ValueError:
            return None
    return None


def _vml_size(style: str | None) -> tuple[int, int] | None:
    """Return the size a VML style declares, in EMU.

    VML writes the size of a shape in its ``style`` attribute, in points
    (``width:100pt;height:50pt``).  A size that is not written in both
    directions is no size: the run then carries ``None``, and the writer
    falls back to its default.
    """
    if not style:
        return None
    measures: dict[str, str] = {}
    for declaration in style.split(";"):
        name, separator, value = declaration.partition(":")
        if not separator:
            continue
        measures[name.strip().lower()] = value.strip().lower()
    width = _emu_from_measure(measures.get("width"))
    height = _emu_from_measure(measures.get("height"))
    if width is None or height is None:
        return None
    return (width, height)


def _resolve_against(directory: str, target: str) -> str:
    """Resolve a relationship target against the directory that holds it.

    A target is written relative to the part that holds the relationship, so
    it is resolved against that part's own directory rather than against
    ``word/``: the chart's ``colors1.xml`` is a sibling of the chart, not a
    child of ``word/``.  The package root holds its relationships in the root
    of the package, which is the base every package part name is written
    against.
    """
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(directory, target))


def _symbol_code(raw: str) -> str | None:
    """Return the font code a ``w:sym`` character code stands for.

    Word writes the code in the font's private-use range (``F0B7``), but
    producers also write the bare code (``B7``).  Both name the same
    character, so the private-use offset is removed before the lookup.  A
    code that is not a hexadecimal byte has no font code at all.
    """
    text = raw.strip().upper()
    if text.startswith("0X"):
        text = text[2:]
    if not text:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    if 0xF000 <= value <= 0xF0FF:
        value &= 0xFF
    if value > 0xFF:
        return None
    return f"{value:02X}"


def _equation_text(element: etree._Element) -> str:
    """Flatten an OMML element into the text its formula reads as.

    The formula's own text lives in ``m:t`` elements; concatenating them in
    document order is what makes the equation searchable, quotable and
    checkable like any other text in the document.
    """
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{{{_NS_M}}}t":
            parts.append(strip_control_characters(str(node.text or "")))
    return "".join(parts)


def _classify_style(name: str, style_id: str) -> tuple[WordBlockKind, int] | None:
    """Classify a style by name and id, or return ``None`` when unclassified."""
    name_norm = name.strip().lower()
    id_norm = style_id.strip().lower()

    match = _HEADING_NAME_RE.match(name_norm)
    if match is not None:
        return (WordBlockKind.HEADING, int(match.group(1)))
    match = _HEADING_ID_RE.match(id_norm)
    if match is not None:
        return (WordBlockKind.HEADING, int(match.group(1)))
    if name_norm == "title" or id_norm == "title":
        return (WordBlockKind.TITLE, 0)
    if name_norm in {"quote", "intense quote"} or id_norm in {"quote", "intensequote"}:
        return (WordBlockKind.QUOTE, 0)
    if "code" in name_norm or "preformatted" in name_norm:
        return (WordBlockKind.CODE, 0)
    if "code" in id_norm or "preformatted" in id_norm:
        return (WordBlockKind.CODE, 0)
    return None


@dataclass(frozen=True)
class _RunProps:
    """The formatting a run inherits or carries directly."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    strike: bool = False
    font: str | None = None
    size: int | None = None
    color: str | None = None
    highlight: str | None = None

    def merged_with(self, other: "_RunProps") -> "_RunProps":
        """Return these properties overridden by ``other``.

        A run inherits the formatting of its style and then overrides it with
        its own direct formatting; Word resolves the two in that order.
        """
        return _RunProps(
            bold=other.bold or self.bold,
            italic=other.italic or self.italic,
            underline=other.underline or self.underline,
            superscript=other.superscript or self.superscript,
            subscript=other.subscript or self.subscript,
            strike=other.strike or self.strike,
            font=other.font or self.font,
            size=other.size if other.size is not None else self.size,
            color=other.color or self.color,
            highlight=other.highlight or self.highlight,
        )


@dataclass(frozen=True)
class _StyleInfo:
    """One entry of ``word/styles.xml``."""

    style_id: str
    name: str
    based_on: str
    num_id: str | None
    num_level: int
    run_props: _RunProps = _RunProps()


@dataclass(frozen=True)
class _NumberingInfo:
    """The numbering definitions of ``word/numbering.xml``."""

    num_to_abstract: dict[str, str]
    abstract_levels: dict[str, dict[int, str]]
    abstract_level_texts: dict[str, dict[int, str]]
    overrides: dict[str, dict[int, str]]
    override_level_texts: dict[str, dict[int, str]]


@dataclass(frozen=True)
class _Relationship:
    """One entry of ``word/_rels/document.xml.rels``."""

    rel_id: str
    target: str
    rel_type: str
    is_external: bool = False


@dataclass(frozen=True)
class _Draft:
    """A block before it is given its final id and validated."""

    kind: WordBlockKind
    runs: tuple[WordRun, ...] = ()
    text: str = ""
    level: int = 0
    list_marker: ListMarker | None = None
    list_group: str | None = None
    number_format: str | None = None
    number_text: str | None = None
    style: str | None = None
    table: WordTable | None = None


@dataclass(frozen=True)
class _Segment:
    """A piece of inline content: runs, a page break or nested blocks."""

    runs: tuple[WordRun, ...] = ()
    is_break: bool = False
    blocks: tuple[_Draft, ...] = ()


@dataclass(frozen=True)
class _CellLayout:
    """One cell of a table row, positioned on the table grid."""

    element: etree._Element
    grid_column: int
    col_span: int


@dataclass(frozen=True)
class _MergeState:
    """The vertical-merge state of one table cell."""

    row_span: int = 1
    is_continuation: bool = False


@dataclass
class _ResolvedRow:
    """One row of a table: where its cells sit on the grid, and their merge states."""

    layout: list[_CellLayout]
    states: dict[int, _MergeState]


@dataclass(eq=False)
class _OpenMerge:
    """A vertical merge that is still open on the current row.

    A merge occupies every grid column its origin spans: a merge that starts
    at grid column ``c`` and spans ``M`` columns covers the grid columns
    ``c .. c+M-1``. The dataclass compares by identity -- two merges are the
    same merge only when they are the same object -- so that the registry can
    tell a merge that a row has already continued from one it has not.
    """

    origin_key: tuple[int, int]
    origin_row: int
    start_column: int
    column_span: int
    row_span: int = 1

    def covers(self, column: int) -> bool:
        """Report whether a grid column belongs to the merged region."""
        return self.start_column <= column < self.start_column + self.column_span

    def overlaps(self, column: int, span: int) -> bool:
        """Report whether a grid range overlaps the merged region."""
        return (
            column < self.start_column + self.column_span
            and self.start_column < column + span
        )


def _merge_covering(
    merges: list[_OpenMerge],
    column: int,
    skip: set[_OpenMerge] | None = None,
) -> _OpenMerge | None:
    """Return the open merge that covers a grid column, if there is one.

    ``skip`` names the merges that must not be matched again: a merge that a
    row has already continued is finished for that row.
    """
    for merge in merges:
        if skip is not None and merge in skip:
            continue
        if merge.covers(column):
            return merge
    return None


class WordReader:
    """Reads ``.docx`` packages into the shared document model.

    The reader is stateless between calls: every ``read``/``read_bytes`` resets
    the omission register and re-reads the package from scratch, so the same
    bytes always yield the same :class:`~lmeditor.word.model.WordDocument`.
    """

    def __init__(self) -> None:
        self.last_omissions: list[str] = []
        self._omission_counts: dict[str, int] = {}
        self._styles: dict[str, _StyleInfo] = {}
        self._numbering: _NumberingInfo | None = None
        self._rels: dict[str, _Relationship] = {}
        self._footnote_runs: dict[str, tuple[WordRun, ...]] = {}
        self._endnote_runs: dict[str, tuple[WordRun, ...]] = {}
        self._comment_runs: dict[str, tuple[WordRun, ...]] = {}
        self._comment_meta: dict[str, tuple[str, str, datetime | None]] = {}
        self._media: dict[str, WordMedia] = {}
        self._parts: dict[str, WordPart] = {}
        self._part_relationships: dict[str, tuple[str, str]] = {}
        self._package_relationships: dict[str, WordPartRelationship] = {}
        self._used_styles: set[str] = set()
        self._sections: list[WordSection] = []
        self._content_types: dict[str, str] = {}
        self._base_uri: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(
        self, source: str | Path | bytes, *, base_uri: str | None = None
    ) -> WordDocument:
        """Read a document from a path or from raw package bytes.

        Args:
            source: A filesystem path (``str`` or ``Path``) or the raw bytes of
                a ``.docx`` package.
            base_uri: The URI the document is served from. A hyperlink whose
                relationship target is relative is resolved against it; with no
                base the target is preserved verbatim and recorded in
                :attr:`last_omissions`.

        Returns:
            The parsed document.

        Raises:
            WordParseError: If the package is missing, malformed or cannot be
                turned into the document model.
        """
        self._begin_read(base_uri)
        if isinstance(source, (bytes, bytearray, memoryview)):
            return self._read_package(bytes(source), "<bytes>")
        path = Path(source)
        origin = str(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WordParseError(origin, f"the file could not be read: {exc}") from exc
        return self._read_package(data, origin)

    def read_bytes(self, data: bytes, *, base_uri: str | None = None) -> WordDocument:
        """Read a document from the raw bytes of a ``.docx`` package.

        Args:
            data: The bytes of a ``.docx`` package.
            base_uri: The URI the document is served from. A hyperlink whose
                relationship target is relative is resolved against it; with no
                base the target is preserved verbatim and recorded in
                :attr:`last_omissions`.

        Returns:
            The parsed document.

        Raises:
            WordParseError: If the package is missing, malformed or cannot be
                turned into the document model.
        """
        self._begin_read(base_uri)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise WordParseError("<bytes>", "expected the raw bytes of a .docx package")
        return self._read_package(bytes(data), "<bytes>")

    @property
    def last_package_relationships(self) -> tuple[WordPartRelationship, ...]:
        """Return the relationships the package root holds, in a stable order.

        The package root reaches parts the document does not, and a part it
        reaches carries that relationship on
        :attr:`~lmeditor.word.model.WordPart.package_relationship`.  A
        package-root relationship that reaches no part of the package -- an
        external one, say -- has no place in the document model, so it is
        kept here rather than being dropped.
        """
        return tuple(
            sorted(
                self._package_relationships.values(),
                key=lambda relationship: relationship.relationship_id,
            )
        )

    # ------------------------------------------------------------------
    # Package level
    # ------------------------------------------------------------------

    def _begin_read(self, base_uri: str | None = None) -> None:
        """Reset the per-read state, including the omission register."""
        self.last_omissions = []
        self._omission_counts = {}
        self._styles = {}
        self._numbering = None
        self._rels = {}
        self._footnote_runs = {}
        self._endnote_runs = {}
        self._comment_runs = {}
        self._comment_meta = {}
        self._media = {}
        self._parts = {}
        self._part_relationships = {}
        self._package_relationships = {}
        self._used_styles = set()
        self._sections = []
        self._section_ends: list[tuple[etree._Element, int]] = []
        self._content_types = {}
        self._base_uri = base_uri
        self._open_comment_ranges: list[str] = []
        self._archive: zipfile.ZipFile | None = None
        self._origin = ""
        self._bytes_read = 0

    def _read_package(self, data: bytes, origin: str) -> WordDocument:
        """Parse an OPC package into a document.

        Every failure of the model boundary is translated into a
        :class:`~lmeditor.core.exceptions.WordParseError`: a malformed
        document must never reach the caller as a raw
        ``pydantic.ValidationError``.
        """
        try:
            return self._parse_package(data, origin)
        except ValidationError as exc:
            raise WordParseError(
                origin,
                "the document could not be turned into the document model: "
                f"{exc}",
            ) from exc

    def _parse_package(self, data: bytes, origin: str) -> WordDocument:
        """Read the parts of an OPC package and assemble the document."""
        self._origin = origin
        self._bytes_read = 0
        reason = describe_unsupported_format(data)
        if reason.startswith("legacy .doc"):
            raise WordParseError(origin, reason)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise WordParseError(
                origin, f"the file is not a valid .docx package (bad zip container): {exc}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise WordParseError(
                origin, f"the file could not be opened as a zip container: {exc}"
            ) from exc

        with archive:
            self._archive = archive
            names = archive.namelist()
            self._check_package_limits(archive, names, origin)
            if "word/document.xml" not in names:
                raise WordParseError(
                    origin, "the package does not contain the required part word/document.xml"
                )
            document = self._parse_part(archive, "word/document.xml", origin)
            body = document.find(_w("body"))
            if body is None:
                raise WordParseError(
                    origin, "word/document.xml does not contain a w:body element"
                )

            self._load_styles(archive, origin)
            self._load_numbering(archive, origin)
            self._load_relationships(archive, origin)
            self._load_package_relationships(archive, origin)
            self._load_content_types(archive, origin)
            self._load_footnotes(archive, origin)
            self._load_endnotes(archive, origin)
            self._load_comments(archive, origin)

            drafts = self._walk_blocks(body)
            self._carry_parts(names)
            self._read_sections(body, drafts, archive, origin)
            headers, footers = self._read_headers_and_footers(archive, origin)
            properties = self._read_core_properties(archive, origin)

        blocks = self._build_blocks(drafts)
        return WordDocument(
            blocks=blocks,
            properties=properties,
            footnotes=self._build_footnotes(),
            endnotes=self._build_endnotes(),
            comments=self._build_comments(blocks),
            headers=headers,
            footers=footers,
            sections=tuple(self._sections),
            styles=self._build_styles(),
            media=tuple(self._media.values()),
            parts=self._ordered_parts(),
        )

    # ------------------------------------------------------------------
    # Package limits
    # ------------------------------------------------------------------

    def _check_package_limits(
        self, archive: zipfile.ZipFile, names: list[str], origin: str
    ) -> None:
        """Refuse a package that is too large, too many or too compressed.

        A ``.docx`` is a zip container, and a zip container can be crafted to
        expand to an arbitrary size: a few kilobytes of input can decompress
        into gigabytes. The limits are checked against the central directory
        *and* enforced again while the members are read, because a crafted
        archive can lie about the sizes it declares.
        """
        if len(names) > _MAX_PACKAGE_MEMBERS:
            raise WordParseError(
                origin,
                "the package holds "
                f"{len(names)} members, more than the {_MAX_PACKAGE_MEMBERS} allowed",
            )

        total = 0
        for info in archive.infolist():
            declared = max(0, int(info.file_size))
            compressed = max(1, int(info.compress_size))
            if declared > _MAX_MEMBER_UNCOMPRESSED_BYTES:
                raise WordParseError(
                    origin,
                    f"the package part {info.filename!r} declares {declared} bytes, "
                    f"more than the {_MAX_MEMBER_UNCOMPRESSED_BYTES} allowed for one part",
                )
            ratio = declared // compressed
            if ratio > _MAX_COMPRESSION_RATIO:
                raise WordParseError(
                    origin,
                    f"the package part {info.filename!r} is compressed {ratio} times, "
                    f"more than the {_MAX_COMPRESSION_RATIO} allowed",
                )
            total += declared
            if total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise WordParseError(
                    origin,
                    "the package decompresses to more than the "
                    f"{_MAX_TOTAL_UNCOMPRESSED_BYTES} bytes allowed",
                )

    def _read_package_member(
        self, archive: zipfile.ZipFile, info: zipfile.ZipInfo, origin: str
    ) -> bytes:
        """Read one package member, enforcing the decompression limits.

        The size declared in the central directory cannot be trusted: the
        member is read in chunks and the running total is checked as it goes,
        so a member that lies about its size is still refused.
        """
        chunks: list[bytes] = []
        read = 0
        try:
            with archive.open(info) as handle:
                while True:
                    chunk = handle.read(_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > _MAX_MEMBER_UNCOMPRESSED_BYTES:
                        raise WordParseError(
                            origin,
                            f"the package part {info.filename!r} decompresses to more "
                            f"than the {_MAX_MEMBER_UNCOMPRESSED_BYTES} bytes allowed "
                            "for one part",
                        )
                    if self._bytes_read + read > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise WordParseError(
                            origin,
                            "the package decompresses to more than the "
                            f"{_MAX_TOTAL_UNCOMPRESSED_BYTES} bytes allowed",
                        )
                    chunks.append(chunk)
        except WordParseError:
            raise
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise WordParseError(
                origin, f"the package part {info.filename!r} could not be read: {exc}"
            ) from exc
        except Exception as exc:
            # Everything inside this call belongs to the standard library's
            # zip and decompression machinery -- no LMeditor code runs here --
            # so any failure means the package is damaged, whatever its
            # cause: a corrupt deflate stream (``zlib.error``, which CPython's
            # ``zipfile`` does not wrap), an unsupported compression method
            # (``NotImplementedError``), a corrupt LZMA member
            # (``lzma.LZMAError``), a truncated member, an encrypted member
            # without its password. The contract requires every one of them to
            # arrive as a ``WordParseError`` rather than as an exception the
            # caller cannot act on.
            raise WordParseError(
                origin, f"the package part {info.filename!r} could not be read: {exc!r}"
            ) from exc
        self._bytes_read += read
        return b"".join(chunks)

    def _parse_part(self, archive: zipfile.ZipFile, name: str, origin: str) -> etree._Element:
        """Read and parse one package part."""
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise WordParseError(origin, f"the package part {name!r} is missing") from exc
        payload = self._read_package_member(archive, info, origin)
        return self._parse_xml(payload, name, origin)

    def _read_part_bytes(
        self, archive: zipfile.ZipFile, name: str, origin: str
    ) -> bytes:
        """Read the raw bytes of one package part, enforcing the limits."""
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise WordParseError(origin, f"the package part {name!r} is missing") from exc
        return self._read_package_member(archive, info, origin)

    def _parse_xml(self, payload: bytes, name: str, origin: str) -> etree._Element:
        """Parse XML bytes, translating every failure into a parse error."""
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        try:
            root = etree.fromstring(payload, parser)
        except etree.XMLSyntaxError as exc:
            raise WordParseError(
                origin, f"the package part {name!r} is not well-formed XML: {exc}"
            ) from exc
        except (ValueError, LookupError) as exc:
            raise WordParseError(
                origin, f"the package part {name!r} could not be decoded: {exc}"
            ) from exc
        if root is None:
            raise WordParseError(origin, f"the package part {name!r} is empty")
        return root

    # ------------------------------------------------------------------
    # Supporting parts
    # ------------------------------------------------------------------

    def _load_styles(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/styles.xml`` into the style table."""
        if "word/styles.xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/styles.xml", origin)
        for style in root.findall(_w("style")):
            style_id = str(style.get(_w("styleId")) or "")
            if not style_id:
                continue
            name = _val_attribute(style.find(_w("name")))
            based_on = _val_attribute(style.find(_w("basedOn")))
            num_id: str | None = None
            num_level = 0
            properties = style.find(_w("pPr"))
            if properties is not None:
                numbering = properties.find(_w("numPr"))
                if numbering is not None:
                    num_ref = numbering.find(_w("numId"))
                    if num_ref is not None:
                        num_id = str(num_ref.get(_w("val")) or "")
                    level_ref = numbering.find(_w("ilvl"))
                    if level_ref is not None:
                        num_level = _safe_int(level_ref.get(_w("val")), 0)
            self._styles[style_id] = _StyleInfo(
                style_id=style_id,
                name=name,
                based_on=based_on,
                num_id=num_id,
                num_level=num_level,
                run_props=self._read_run_properties(style.find(_w("rPr"))),
            )

    def _read_run_properties(self, properties: etree._Element | None) -> _RunProps:
        """Read a ``w:rPr`` element into the formatting it carries."""
        if properties is None:
            return _RunProps()
        vertical = properties.find(_w("vertAlign"))
        vertical_value = str(vertical.get(_w("val")) or "").strip().lower() if vertical is not None else ""
        fonts = properties.find(_w("rFonts"))
        font: str | None = None
        if fonts is not None:
            font = (
                str(fonts.get(_w("ascii")) or "").strip()
                or str(fonts.get(_w("hAnsi")) or "").strip()
                or str(fonts.get(_w("cs")) or "").strip()
                or None
            )
        size_element = properties.find(_w("sz"))
        size: int | None = None
        if size_element is not None:
            raw = str(size_element.get(_w("val")) or "").strip()
            if raw.isdigit() and int(raw) > 0:
                size = int(raw)
        color_element = properties.find(_w("color"))
        color: str | None = None
        if color_element is not None:
            raw = str(color_element.get(_w("val")) or "").strip().lstrip("#")
            if raw and raw.lower() != "auto":
                color = raw.upper()
        highlight_element = properties.find(_w("highlight"))
        highlight: str | None = None
        if highlight_element is not None:
            raw = str(highlight_element.get(_w("val")) or "").strip()
            if raw and raw.lower() != "none":
                highlight = raw
        return _RunProps(
            bold=_on_off(properties.find(_w("b"))),
            italic=_on_off(properties.find(_w("i"))),
            underline=_underline(properties.find(_w("u"))),
            superscript=vertical_value == "superscript",
            subscript=vertical_value == "subscript",
            strike=_on_off(properties.find(_w("strike"))),
            font=font,
            size=size,
            color=color,
            highlight=highlight,
        )

    def _style_run_properties(self, style_id: str) -> _RunProps:
        """Resolve the formatting a style contributes, following ``w:basedOn``."""
        seen: set[str] = set()
        current = style_id
        merged = _RunProps()
        while current and current not in seen:
            seen.add(current)
            info = self._lookup_style(current)
            if info is None:
                break
            merged = merged.merged_with(info.run_props)
            current = info.based_on
        return merged

    def _load_numbering(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/numbering.xml`` into the numbering table."""
        if "word/numbering.xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/numbering.xml", origin)

        abstract_levels: dict[str, dict[int, str]] = {}
        abstract_level_texts: dict[str, dict[int, str]] = {}
        for abstract in root.findall(_w("abstractNum")):
            abstract_id = str(abstract.get(_w("abstractNumId")) or "")
            levels: dict[int, str] = {}
            level_texts: dict[int, str] = {}
            for level in abstract.findall(_w("lvl")):
                index = _safe_int(level.get(_w("ilvl")), 0)
                format_element = level.find(_w("numFmt"))
                levels[index] = (
                    str(format_element.get(_w("val")) or "") if format_element is not None else ""
                )
                text_element = level.find(_w("lvlText"))
                level_texts[index] = (
                    str(text_element.get(_w("val")) or "") if text_element is not None else ""
                )
            abstract_levels[abstract_id] = levels
            abstract_level_texts[abstract_id] = level_texts

        num_to_abstract: dict[str, str] = {}
        overrides: dict[str, dict[int, str]] = {}
        override_level_texts: dict[str, dict[int, str]] = {}
        for number in root.findall(_w("num")):
            num_id = str(number.get(_w("numId")) or "")
            abstract_ref = number.find(_w("abstractNumId"))
            num_to_abstract[num_id] = (
                str(abstract_ref.get(_w("val")) or "") if abstract_ref is not None else ""
            )
            override_levels: dict[int, str] = {}
            override_texts: dict[int, str] = {}
            for override in number.findall(_w("lvlOverride")):
                index = _safe_int(override.get(_w("ilvl")), 0)
                level = override.find(_w("lvl"))
                if level is None:
                    continue
                format_element = level.find(_w("numFmt"))
                if format_element is None:
                    continue
                override_levels[index] = str(format_element.get(_w("val")) or "")
                text_element = level.find(_w("lvlText"))
                override_texts[index] = (
                    str(text_element.get(_w("val")) or "") if text_element is not None else ""
                )
            overrides[num_id] = override_levels
            override_level_texts[num_id] = override_texts

        self._numbering = _NumberingInfo(
            num_to_abstract=num_to_abstract,
            abstract_levels=abstract_levels,
            abstract_level_texts=abstract_level_texts,
            overrides=overrides,
            override_level_texts=override_level_texts,
        )

    def _load_relationships(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/_rels/document.xml.rels``."""
        if "word/_rels/document.xml.rels" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/_rels/document.xml.rels", origin)
        for relationship in root:
            if _local(relationship) != "Relationship":
                continue
            rel_id = str(relationship.get("Id") or "")
            if not rel_id:
                continue
            target = str(relationship.get("Target") or "")
            rel_type = str(relationship.get("Type") or "")
            is_external = str(relationship.get("TargetMode") or "").strip().lower() == "external"
            self._rels[rel_id] = _Relationship(
                rel_id=rel_id,
                target=target,
                rel_type=rel_type,
                is_external=is_external,
            )
            if is_external:
                # An external target names a file outside the package: there
                # are no bytes to carry through.
                continue
            part_name = self._package_part_name(target)
            if part_name and part_name not in self._part_relationships:
                # The first relationship that reaches a part is the one the
                # part is written back under; a part several relationships
                # point at keeps the first of them.
                self._part_relationships[part_name] = (rel_id, rel_type)

    def _load_package_relationships(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``_rels/.rels``, the relationship part of the package root.

        The package root reaches parts the document does not: the custom
        document properties, a thumbnail, whatever else the producer put
        beside the document.  Such a part has a path into the package that
        does not go through ``document.xml``, and without it the part has
        nowhere to travel and is lost on the round trip.
        """
        if "_rels/.rels" not in archive.namelist():
            return
        try:
            root = self._parse_part(archive, "_rels/.rels", origin)
        except WordParseError:
            self._omit("package relationship part (_rels/.rels) could not be parsed")
            return

        relationships: list[WordPartRelationship] = []
        for element in root:
            if _local(element) != "Relationship":
                continue
            rel_id = str(element.get("Id") or "")
            rel_type = str(element.get("Type") or "")
            target = str(element.get("Target") or "")
            if not rel_id:
                self._omit(
                    "package relationship part (_rels/.rels) holds a relationship without an id"
                )
                continue
            if not rel_type:
                self._omit(
                    "package relationship part (_rels/.rels) holds relationship "
                    f"{rel_id} without a type"
                )
                continue
            if not target:
                self._omit(
                    "package relationship part (_rels/.rels) holds relationship "
                    f"{rel_id} without a target"
                )
                continue
            is_external = str(element.get("TargetMode") or "").strip().lower() == "external"
            relationships.append(
                WordPartRelationship(
                    relationship_id=rel_id,
                    relationship_type=rel_type,
                    target=target if is_external else _resolve_against("", target),
                    is_external=is_external,
                )
            )
        relationships.sort(key=lambda relationship: relationship.relationship_id)
        self._package_relationships = {
            relationship.relationship_id: relationship for relationship in relationships
        }

    def _package_relationship_for(self, part_name: str) -> WordPartRelationship | None:
        """Return the package-root relationship that reaches a part, if any.

        A part several package-root relationships reach keeps the first of
        them, the way a part several document relationships reach keeps the
        first document relationship.
        """
        for relationship in self.last_package_relationships:
            if relationship.is_external:
                continue
            if relationship.target == part_name:
                return relationship
        return None

    def _load_footnotes(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/footnotes.xml``, skipping the separator footnotes."""
        if "word/footnotes.xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/footnotes.xml", origin)
        for footnote in root.findall(_w("footnote")):
            footnote_id = footnote.get(_w("id"))
            if footnote_id is None:
                continue
            footnote_id = str(footnote_id)
            footnote_type = str(footnote.get(_w("type")) or "").strip().lower()
            if footnote_type in {"separator", "continuationseparator", "continuationnotice"}:
                continue
            if footnote_id.lstrip("-").isdigit() and int(footnote_id) <= 0:
                # Word reserves id -1 and id 0 for the separator footnotes.
                continue
            runs: list[WordRun] = []
            for paragraph in footnote.findall(_w("p")):
                for segment in self._walk_inline(paragraph, None):
                    if segment.is_break or segment.blocks:
                        continue
                    runs.extend(segment.runs)
            self._footnote_runs[footnote_id] = tuple(runs)

    def _load_endnotes(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/endnotes.xml``, skipping the separator endnotes."""
        if "word/endnotes.xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/endnotes.xml", origin)
        for endnote in root.findall(_w("endnote")):
            endnote_id = endnote.get(_w("id"))
            if endnote_id is None:
                continue
            endnote_id = str(endnote_id)
            endnote_type = str(endnote.get(_w("type")) or "").strip().lower()
            if endnote_type in {"separator", "continuationseparator", "continuationnotice"}:
                continue
            if endnote_id.lstrip("-").isdigit() and int(endnote_id) <= 0:
                # Word reserves id -1 and id 0 for the separator endnotes.
                continue
            runs: list[WordRun] = []
            for paragraph in endnote.findall(_w("p")):
                for segment in self._walk_inline(paragraph, None):
                    if segment.is_break or segment.blocks:
                        continue
                    runs.extend(segment.runs)
            self._endnote_runs[endnote_id] = tuple(runs)

    def _load_comments(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``word/comments.xml``."""
        if "word/comments.xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "word/comments.xml", origin)
        for comment in root.findall(_w("comment")):
            comment_id = str(comment.get(_w("id")) or "")
            if not comment_id:
                continue
            author = str(comment.get(_w("author")) or "")
            initials = str(comment.get(_w("initials")) or "")
            date = self._read_timestamp_value(
                str(comment.get(_w("date")) or "").strip(), "comment"
            )
            runs: list[WordRun] = []
            for paragraph in comment.findall(_w("p")):
                for segment in self._walk_inline(paragraph, None):
                    if segment.is_break or segment.blocks:
                        continue
                    runs.extend(segment.runs)
            self._comment_runs[comment_id] = tuple(runs)
            self._comment_meta[comment_id] = (author, initials, date)

    def _read_sections(
        self, body: etree._Element, drafts: list[_Draft], archive: zipfile.ZipFile, origin: str
    ) -> None:
        """Read the section properties of the document.

        A section's properties live in a ``w:sectPr``: the ones that end a
        section sit in the ``w:pPr`` of the paragraph that closes it, and the
        properties of the *last* section sit at the end of the body.
        """
        sections: list[WordSection] = []
        for properties, end_count in self._section_ends:
            end_block_id = f"b{end_count:04d}" if end_count else None
            sections.append(self._section_from(properties, end_block_id, archive, origin))
        body_properties = body.find(_w("sectPr"))
        if body_properties is not None:
            end_block_id = f"b{len(drafts):04d}" if drafts else None
            sections.append(self._section_from(body_properties, end_block_id, archive, origin))
        self._sections = sections

    def _section_from(
        self,
        properties: etree._Element,
        end_block_id: str | None,
        archive: zipfile.ZipFile,
        origin: str,
    ) -> WordSection:
        """Build a section from a ``w:sectPr`` element."""
        page_size = properties.find(_w("pgSz"))
        page_width = 12240
        page_height = 15840
        orientation = "portrait"
        if page_size is not None:
            page_width = _safe_int(page_size.get(_w("w")), page_width)
            page_height = _safe_int(page_size.get(_w("h")), page_height)
            raw_orientation = str(page_size.get(_w("orient")) or "").strip().lower()
            if raw_orientation:
                orientation = raw_orientation
            elif page_width > page_height:
                orientation = "landscape"

        margins = properties.find(_w("pgMar"))
        margin_top = 1440
        margin_right = 1440
        margin_bottom = 1440
        margin_left = 1440
        if margins is not None:
            margin_top = _safe_int(margins.get(_w("top")), margin_top)
            margin_right = _safe_int(margins.get(_w("right")), margin_right)
            margin_bottom = _safe_int(margins.get(_w("bottom")), margin_bottom)
            margin_left = _safe_int(margins.get(_w("left")), margin_left)

        header_refs = tuple(
            self._header_footer_ref(reference, "header", archive, origin)
            for reference in properties.findall(_w("headerReference"))
        )
        footer_refs = tuple(
            self._header_footer_ref(reference, "footer", archive, origin)
            for reference in properties.findall(_w("footerReference"))
        )

        return WordSection(
            page_width=max(1, page_width),
            page_height=max(1, page_height),
            orientation=orientation,
            margin_top=max(0, margin_top),
            margin_right=max(0, margin_right),
            margin_bottom=max(0, margin_bottom),
            margin_left=max(0, margin_left),
            header_refs=header_refs,
            footer_refs=footer_refs,
            end_block_id=end_block_id,
        )

    def _header_footer_ref(
        self,
        reference: etree._Element,
        kind: str,
        archive: zipfile.ZipFile,
        origin: str,
    ) -> WordHeaderFooterRef:
        """Build the reference of one header or footer of a section.

        The reference *type* -- ``default``, ``even`` or ``first`` -- is what
        says what the header is for; the part name only says where it lives.
        """
        ref_type = str(reference.get(_w("type")) or "default").strip().lower() or "default"
        rel_id = str(reference.get(_r("id")) or "")
        part_name = ""
        if rel_id:
            relationship = self._rels.get(rel_id)
            if relationship is None:
                self._omit(f"{kind} relationship {rel_id} is missing")
            else:
                part_name = self._part_name_for(relationship.target)
        text = self._header_footer_text(archive, origin, part_name) if part_name else ""
        return WordHeaderFooterRef(ref_type=ref_type, part_name=part_name, text=text)

    def _part_name_for(self, target: str) -> str:
        """Resolve a relationship target to the package part it names."""
        if not target:
            return ""
        if target.startswith("word/"):
            return target
        if target.startswith("/"):
            return target.lstrip("/")
        return f"word/{target}"

    def _read_headers_and_footers(
        self, archive: zipfile.ZipFile, origin: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Read the header and footer texts of the document.

        Headers and footers are matched through the section references that
        name them -- ``default``, ``even`` and ``first`` -- because the part
        name says nothing about the role a header plays. A document that
        carries no reference at all falls back to part-name order, which is
        all the information such a document offers.
        """
        has_references = any(
            section.header_refs or section.footer_refs for section in self._sections
        )
        if has_references:
            return (
                self._ordered_by_ref_type(self._sections, "header"),
                self._ordered_by_ref_type(self._sections, "footer"),
            )

        return (
            self._read_header_footer_parts(archive, origin, "header"),
            self._read_header_footer_parts(archive, origin, "footer"),
        )

    @staticmethod
    def _ordered_by_ref_type(sections: list[WordSection], kind: str) -> tuple[str, ...]:
        """Return the header or footer texts in reference-type order.

        The order is the one Word itself uses -- the default page, the first
        page, then the even pages -- so the tuple means the same thing for
        every document, whatever order the references appear in.
        """
        order = ("default", "first", "even")
        texts: list[str] = []
        for ref_type in order:
            for section in sections:
                refs = section.header_refs if kind == "header" else section.footer_refs
                for ref in refs:
                    if ref.ref_type == ref_type and ref.text and ref.text not in texts:
                        texts.append(ref.text)
        for section in sections:
            refs = section.header_refs if kind == "header" else section.footer_refs
            for ref in refs:
                if ref.text and ref.text not in texts:
                    texts.append(ref.text)
        return tuple(texts)

    def _header_footer_text(
        self, archive: zipfile.ZipFile, origin: str, part_name: str
    ) -> str:
        """Read the text of one header or footer part."""
        if part_name not in archive.namelist():
            self._omit(f"header or footer part ({part_name}) is missing")
            return ""
        root = self._parse_part(archive, part_name, origin)
        return self._part_text(root)

    def _read_header_footer_parts(
        self, archive: zipfile.ZipFile, origin: str, kind: str
    ) -> tuple[str, ...]:
        """Read the header or footer parts of the package in part-name order."""
        prefix = f"word/{kind}"
        names = [
            name
            for name in archive.namelist()
            if name.startswith(prefix)
            and name.endswith(".xml")
            and "/" not in name[len(prefix) :]
        ]
        texts: list[str] = []
        for name in sorted(names, key=_natural_key):
            root = self._parse_part(archive, name, origin)
            texts.append(self._part_text(root))
        return tuple(texts)

    def _part_text(self, root: etree._Element) -> str:
        """Render a header or footer part as a single plain string."""
        parts: list[str] = []
        for draft in self._walk_blocks(root):
            if draft.kind is WordBlockKind.TABLE and draft.table is not None:
                text = "\n".join(
                    " | ".join(cell.text for cell in row.cells) for row in draft.table.rows
                )
            else:
                text = draft.text
            if text.strip():
                parts.append(text)
        return " ".join(parts)

    def _read_core_properties(self, archive: zipfile.ZipFile, origin: str) -> WordProperties:
        """Read ``docProps/core.xml`` into the document properties."""
        if "docProps/core.xml" not in archive.namelist():
            return WordProperties()
        root = self._parse_part(archive, "docProps/core.xml", origin)

        revision = 1
        revision_element = root.find(_cp("revision"))
        if revision_element is not None:
            raw = _element_text(revision_element)
            if raw:
                try:
                    revision = max(1, int(raw))
                except ValueError:
                    self._omit("core property cp:revision is not an integer")

        return WordProperties(
            title=_element_text(root.find(_dc("title"))),
            author=_element_text(root.find(_dc("creator"))),
            subject=_element_text(root.find(_dc("subject"))),
            keywords=_element_text(root.find(_cp("keywords"))),
            comments=_element_text(root.find(_dc("description"))),
            created=self._read_timestamp(root.find(_dcterms("created")), "created"),
            modified=self._read_timestamp(root.find(_dcterms("modified")), "modified"),
            revision=revision,
        )

    def _read_timestamp(self, element: etree._Element | None, label: str) -> datetime | None:
        """Read a core-property timestamp as a timezone-aware UTC datetime."""
        return self._read_timestamp_value(_element_text(element), label)

    def _read_timestamp_value(self, raw: str, label: str) -> datetime | None:
        """Read an ISO timestamp as a timezone-aware UTC datetime."""
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            self._omit(f"core property dcterms:{label} is not a valid timestamp")
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _carry_parts(self, names: list[str]) -> None:
        """Carry the package parts the model does not represent.

        A part the reader cannot model is not a part the reader may throw
        away.  Its bytes travel in
        :attr:`~lmeditor.word.model.WordDocument.parts` under its own name,
        so the writer can put the part back into the package and a document
        whose evidence is a chart keeps that chart.

        The media parts keep their own path: an image the reader can reach
        travels in the media model, and one it cannot reach is reported.

        A relationship part is never carried: a ``.rels`` file is the
        relationship list of a sibling part, never content.  Carrying one as
        content is what makes the writer point a relationship at a
        relationship file, and Word reject the package as corrupt.
        """
        for name in sorted(names):
            if name in _IGNORED_PARTS:
                continue
            if name.startswith("word/theme/"):
                continue
            if _is_relationship_part(name):
                continue
            if name.startswith("word/header") or name.startswith("word/footer"):
                continue
            if name.startswith("word/media/"):
                if name in self._media:
                    # The media travelled into the model and can be written
                    # back out; nothing was lost.
                    continue
                self._omit(f"image media part ({name})")
                continue
            self._carry_part(name)

    def _carry_part(self, name: str) -> None:
        """Carry one package part into the model, with its bytes."""
        if name in self._parts:
            return
        archive = self._archive
        if archive is None or name not in archive.namelist():
            return
        try:
            data = self._read_part_bytes(archive, name, self._origin)
        except WordParseError:
            # A part that cannot be read is a loss, but it must not cost the
            # caller the whole document: the register names the part.
            self._omit(f"package part ({name}) could not be read")
            return
        relationship_id, relationship_type = self._part_relationships.get(name, ("", ""))
        self._parts[name] = WordPart(
            part_name=name,
            content_type=self._content_type_for(name),
            data=data,
            relationship_id=relationship_id,
            relationship_type=relationship_type,
            relationships=self._part_own_relationships(name),
            package_relationship=self._package_relationship_for(name),
        )

    def _part_own_relationships(self, part_name: str) -> tuple[WordPartRelationship, ...]:
        """Read the relationships the part itself holds to other parts.

        A chart is reached from the document, but its colour style, its quick
        style and the workbook behind it are reached from the chart's *own*
        relationship part.  Those relationships travel with the part, so the
        writer can put the package back the way it found it.

        A relationship part that cannot be parsed is a loss, and the register
        names it: the part travels, but what it pointed at does not.
        """
        rels_name = _relationship_part_name(part_name)
        archive = self._archive
        if archive is None or rels_name not in archive.namelist():
            return ()
        try:
            root = self._parse_part(archive, rels_name, self._origin)
        except WordParseError:
            self._omit(f"relationship part ({rels_name}) could not be parsed")
            return ()

        relationships: list[WordPartRelationship] = []
        for element in root:
            if _local(element) != "Relationship":
                continue
            rel_id = str(element.get("Id") or "")
            rel_type = str(element.get("Type") or "")
            target = str(element.get("Target") or "")
            if not rel_id:
                self._omit(f"relationship part ({rels_name}) holds a relationship without an id")
                continue
            if not rel_type:
                self._omit(
                    f"relationship part ({rels_name}) holds relationship {rel_id} without a type"
                )
                continue
            if not target:
                self._omit(
                    f"relationship part ({rels_name}) holds relationship {rel_id} without a target"
                )
                continue
            is_external = str(element.get("TargetMode") or "").strip().lower() == "external"
            relationships.append(
                WordPartRelationship(
                    relationship_id=rel_id,
                    relationship_type=rel_type,
                    target=target
                    if is_external
                    else self._resolve_part_target(part_name, target),
                    is_external=is_external,
                )
            )
        relationships.sort(key=lambda relationship: relationship.relationship_id)
        return tuple(relationships)

    def _resolve_part_target(self, part_name: str, target: str) -> str:
        """Resolve a target a part's own relationship holds to a part name.

        A target is written relative to the part that holds the relationship,
        so it is resolved against *that* part's own directory rather than
        against ``word/``: the chart's ``colors1.xml`` is a sibling of the
        chart, not a child of ``word/``.
        """
        return _resolve_against(posixpath.dirname(part_name), target)

    def _ordered_parts(self) -> tuple[WordPart, ...]:
        """Return the carried parts in a stable order.

        The order is the order of the part names, not the order the parts
        were reached in: the same package must always read to the same
        document, whatever the body happened to reference first.
        """
        return tuple(sorted(self._parts.values(), key=lambda part: part.part_name))

    def _package_part_name(self, target: str) -> str:
        """Resolve a relationship target to the package part it names.

        A target is written relative to the part that holds the relationship,
        so a target that climbs out of ``word/`` names a part of the package
        root; the name is normalised so that the same part is always spelled
        the same way.
        """
        name = self._part_name_for(target)
        if not name:
            return ""
        return posixpath.normpath(name)

    def _part_reference(self, rel_id: str, description: str) -> str | None:
        """Carry the part a body reference points at and return its name.

        ``None`` is returned when the reference cannot be resolved, and the
        omission register says why: a body reference that points at a part the
        package does not hold is a real loss, and the register exists to name
        losses.
        """
        relationship = self._rels.get(rel_id)
        if relationship is None:
            self._omit(f"{description} relationship {rel_id} is missing")
            return None
        if relationship.is_external:
            self._omit(
                f"{description} points at an external target ({relationship.target})"
            )
            return None
        part_name = self._package_part_name(relationship.target)
        if not part_name:
            self._omit(f"{description} relationship {rel_id} has no target")
            return None
        archive = self._archive
        if archive is None or part_name not in archive.namelist():
            self._omit(f"{description} points at a missing package part ({part_name})")
            return None
        self._carry_part(part_name)
        return part_name

    # ------------------------------------------------------------------
    # Block walking
    # ------------------------------------------------------------------

    def _walk_blocks(
        self, container: etree._Element, revision: WordRevision | None = None
    ) -> list[_Draft]:
        """Walk the block-level children of a container in document order.

        ``revision`` is the tracked revision the container sits in: a
        paragraph inside a ``w:del`` holds text Word removed, and that text is
        kept as a deletion rather than thrown away.
        """
        drafts: list[_Draft] = []
        for child in container:
            tag = _local(child)
            if not tag:
                continue
            if tag == "commentRangeStart":
                self._open_comment_range(str(child.get(_w("id")) or ""))
                continue
            if tag == "commentRangeEnd":
                self._close_comment_range(str(child.get(_w("id")) or ""))
                continue
            if tag in _BLOCK_SKIP:
                continue
            if tag == "p":
                drafts.extend(self._paragraph_drafts(child, revision))
                section = self._paragraph_section(child)
                if section is not None:
                    self._section_ends.append((section, len(drafts)))
            elif tag == "tbl":
                drafts.append(self._table_draft(child))
            elif tag == "sdt":
                content = child.find(_w("sdtContent"))
                if content is None:
                    self._omit("content control without content (w:sdt)")
                    continue
                drafts.extend(self._walk_blocks(content, revision))
            elif tag in {"ins", "moveTo"}:
                drafts.extend(self._walk_blocks(child, revision))
            elif tag in {"del", "moveFrom"}:
                # A deleted paragraph is still a paragraph: its text is kept as
                # a deletion so that the revision survives the round trip.
                drafts.extend(
                    self._walk_blocks(
                        child,
                        revision or self._revision_for(child, _REVISION_KINDS[tag]),
                    )
                )
            elif tag == "customXml":
                drafts.extend(self._walk_blocks(child, revision))
            elif tag in {"altChunk", "subDoc"}:
                draft = self._anchor_draft(child, tag)
                if draft is not None:
                    drafts.append(draft)
            else:
                self._omit(f"unsupported body element (w:{tag})")
        return drafts

    def _paragraph_section(self, paragraph: etree._Element) -> etree._Element | None:
        """Return the ``w:sectPr`` that closes a section at this paragraph."""
        properties = paragraph.find(_w("pPr"))
        if properties is None:
            return None
        return properties.find(_w("sectPr"))

    def _open_comment_range(self, comment_id: str) -> None:
        """Record that a comment range has started."""
        if comment_id and comment_id not in self._open_comment_ranges:
            self._open_comment_ranges.append(comment_id)

    def _close_comment_range(self, comment_id: str) -> None:
        """Record that a comment range has ended."""
        if comment_id in self._open_comment_ranges:
            self._open_comment_ranges.remove(comment_id)

    def _paragraph_drafts(
        self, paragraph: etree._Element, revision: WordRevision | None = None
    ) -> list[_Draft]:
        """Turn one paragraph into the blocks it contributes."""
        style_id = ""
        num_id: str | None = None
        num_level = 0
        has_num_pr = False

        properties = paragraph.find(_w("pPr"))
        if properties is not None:
            style_element = properties.find(_w("pStyle"))
            if style_element is not None:
                style_id = str(style_element.get(_w("val")) or "")
            numbering = properties.find(_w("numPr"))
            if numbering is not None:
                has_num_pr = True
                num_ref = numbering.find(_w("numId"))
                if num_ref is not None:
                    num_id = str(num_ref.get(_w("val")) or "")
                level_ref = numbering.find(_w("ilvl"))
                if level_ref is not None:
                    num_level = _safe_int(level_ref.get(_w("val")), 0)

        if not has_num_pr:
            style_num_id, style_num_level = self._style_numbering(style_id)
            num_id, num_level = style_num_id, style_num_level

        if style_id:
            self._used_styles.add(style_id)
        style_name = self._effective_style_name(style_id)
        marker = self._resolve_marker(num_id, num_level, style_name)

        number_format: str | None = None
        number_text: str | None = None
        if marker is not None and num_id is not None:
            kind = WordBlockKind.LIST_ITEM
            level = max(0, min(8, num_level))
            list_marker: ListMarker | None = marker
            list_group: str | None = num_id
            number_format = self._number_format(num_id, num_level)
            number_text = self._number_level_text(num_id, num_level)
        else:
            kind, level = self._style_kind(style_id)
            list_marker = None
            list_group = None

        drafts: list[_Draft] = []
        pending: list[WordRun] = []

        def flush() -> None:
            nonlocal pending
            if pending:
                draft = self._paragraph_draft(
                    kind=kind,
                    level=level,
                    list_marker=list_marker,
                    list_group=list_group,
                    number_format=number_format,
                    number_text=number_text,
                    style=style_name or None,
                    runs=pending,
                )
                if draft is not None:
                    drafts.append(draft)
                pending = []

        for segment in self._walk_inline(paragraph, None, revision=revision):
            if segment.is_break:
                flush()
                drafts.append(_Draft(kind=WordBlockKind.PAGE_BREAK))
            elif segment.blocks:
                flush()
                drafts.extend(segment.blocks)
            else:
                pending.extend(segment.runs)
        flush()
        return drafts

    def _paragraph_draft(
        self,
        *,
        kind: WordBlockKind,
        level: int,
        list_marker: ListMarker | None,
        list_group: str | None,
        number_format: str | None,
        number_text: str | None,
        style: str | None,
        runs: list[WordRun],
    ) -> _Draft | None:
        """Build a paragraph draft, skipping paragraphs that carry no content.

        The text of a paragraph is the text of its *live* runs: a run Word
        deleted is kept in the model but is not part of the document's final
        text, so it does not count here.
        """
        text = normalize_whitespace(
            "".join(run.text for run in runs if not run.is_deleted)
        )
        if not runs:
            return None
        return _Draft(
            kind=kind,
            runs=tuple(runs),
            text=text,
            level=level,
            list_marker=list_marker,
            list_group=list_group,
            number_format=number_format,
            number_text=number_text,
            style=style,
        )

    def _style_kind(self, style_id: str) -> tuple[WordBlockKind, int]:
        """Resolve the structural kind of a style, following ``w:basedOn``."""
        seen: set[str] = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            info = self._lookup_style(current)
            if info is None:
                break
            classified = _classify_style(info.name, info.style_id)
            if classified is not None:
                return classified
            current = info.based_on
        return (WordBlockKind.PARAGRAPH, 0)

    def _effective_style_name(self, style_id: str) -> str:
        """Resolve the effective name of a style, following ``w:basedOn``."""
        seen: set[str] = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            info = self._lookup_style(current)
            if info is None:
                return ""
            if info.name:
                return info.name
            current = info.based_on
        return ""

    def _lookup_style(self, style_id: str) -> _StyleInfo | None:
        """Look a style up by id, falling back to a case-insensitive name match."""
        info = self._styles.get(style_id)
        if info is not None:
            return info
        lowered = style_id.strip().lower()
        if not lowered:
            return None
        for candidate in self._styles.values():
            if candidate.name.strip().lower() == lowered:
                return candidate
        return None

    def _style_numbering(self, style_id: str) -> tuple[str | None, int]:
        """Resolve the numbering a style contributes, following ``w:basedOn``."""
        seen: set[str] = set()
        current = style_id
        while current and current not in seen:
            seen.add(current)
            info = self._lookup_style(current)
            if info is None:
                break
            if info.num_id:
                return (info.num_id, info.num_level)
            current = info.based_on
        return (None, 0)

    def _resolve_marker(
        self, num_id: str | None, level: int, style_name: str
    ) -> ListMarker | None:
        """Resolve the list marker of a numbered paragraph."""
        if not num_id or num_id == "0":
            return None
        number_format = self._number_format(num_id, level)
        if number_format is None:
            if style_name.strip().lower().startswith("list bullet"):
                return ListMarker.BULLET
            return ListMarker.NUMBER
        if number_format.strip().lower() == "bullet":
            return ListMarker.BULLET
        return ListMarker.NUMBER

    def _number_format(self, num_id: str, level: int) -> str | None:
        """Resolve the ``w:numFmt`` of a numbering level, if it is defined."""
        numbering = self._numbering
        if numbering is None:
            return None
        override = numbering.overrides.get(num_id, {}).get(level)
        if override:
            return override
        abstract_id = numbering.num_to_abstract.get(num_id)
        if abstract_id is None:
            return None
        return numbering.abstract_levels.get(abstract_id, {}).get(level)

    def _number_level_text(self, num_id: str, level: int) -> str | None:
        """Resolve the ``w:lvlText`` of a numbering level, if it is defined.

        The level text is the *pattern* Word renders the marker from -- ``%1.``
        for an arabic number, ``%1)`` for a bracketed one. It is what makes a
        roman or alpha list look the way it did in the source document, so it
        has to survive the round trip rather than be replaced by a guess.
        """
        numbering = self._numbering
        if numbering is None:
            return None
        override = numbering.override_level_texts.get(num_id, {}).get(level)
        if override:
            return override
        abstract_id = numbering.num_to_abstract.get(num_id)
        if abstract_id is None:
            return None
        return numbering.abstract_level_texts.get(abstract_id, {}).get(level)

    # ------------------------------------------------------------------
    # Inline content
    # ------------------------------------------------------------------

    def _walk_inline(
        self,
        container: etree._Element,
        hyperlink: str | None,
        revision: WordRevision | None = None,
    ) -> list[_Segment]:
        """Walk the inline content of a container in document order.

        ``revision`` is the tracked revision the content sits in. Word marks
        an insertion or a deletion by wrapping the affected runs in a ``w:ins``
        or a ``w:del`` element; the runs inside keep their text and carry the
        revision, so the revision can be written back out.
        """
        segments: list[_Segment] = []
        for child in container:
            tag = _local(child)
            if not tag:
                continue
            if tag == "commentRangeStart":
                self._open_comment_range(str(child.get(_w("id")) or ""))
                continue
            if tag == "commentRangeEnd":
                self._close_comment_range(str(child.get(_w("id")) or ""))
                continue
            if tag in _REVISION_KINDS:
                segments.extend(
                    self._walk_inline(
                        child,
                        hyperlink,
                        revision=self._revision_for(child, _REVISION_KINDS[tag]),
                    )
                )
                continue
            if tag in _INLINE_SKIP:
                continue
            if tag == "r":
                segments.extend(self._run_segments(child, hyperlink, revision))
            elif tag == "hyperlink":
                segments.extend(
                    self._walk_inline(child, self._hyperlink_target(child), revision)
                )
            elif tag == "sdt":
                content = child.find(_w("sdtContent"))
                if content is None:
                    self._omit("content control without content (w:sdt)")
                    continue
                segments.extend(self._walk_inline(content, hyperlink, revision))
            elif tag == "txbxContent":
                segments.append(_Segment(blocks=tuple(self._walk_blocks(child))))
            elif tag in {"drawing", "pict"}:
                _, part_ref = self._drawing_reference(child)
                blocks = self._drawing_blocks(child)
                if blocks:
                    segments.append(_Segment(blocks=tuple(blocks)))
                if part_ref is not None:
                    segments.append(
                        _Segment(
                            runs=(
                                WordRun(
                                    text="",
                                    part_ref=part_ref,
                                    anchor_extent=self._anchor_extent(child),
                                ),
                            )
                        )
                    )
            elif tag == "object":
                part_ref = self._object_part_ref(child)
                if part_ref is not None:
                    segments.append(
                        _Segment(
                            runs=(
                                WordRun(
                                    text="",
                                    part_ref=part_ref,
                                    anchor_extent=self._anchor_extent(child),
                                ),
                            )
                        )
                    )
            elif tag in {"subDoc", "altChunk"}:
                segment = self._anchor_segment(child, tag)
                if segment is not None:
                    segments.append(segment)
            elif tag in {"oMath", "oMathPara"}:
                segment = self._equation_segment(child)
                if segment is not None:
                    segments.append(segment)
            else:
                # Any other inline container (``w:smartTag``, ``w:ruby``,
                # ...) is transparent: its children are ordinary inline
                # content.
                segments.extend(self._walk_inline(child, hyperlink, revision))
        return segments

    def _equation_segment(self, element: etree._Element) -> _Segment | None:
        """Turn an OMML equation into a run that keeps both its markup and its text.

        An equation is two things at once: the markup Word renders and the
        text the formula reads as.  Both are kept -- the markup verbatim, so
        the equation can be written back out, and the flattened text, so the
        formula can be searched, quoted and checked like any other text.

        ``None`` is returned when the markup cannot be captured, and the
        omission register says so: an equation that cannot be preserved is
        still a loss, and the register exists to name losses.
        """
        try:
            markup = etree.tostring(element, encoding="unicode", with_tail=False)
        except (ValueError, TypeError) as exc:
            self._omit(f"equation (m:oMath) could not be serialised: {exc!r}")
            return None
        if not markup:
            self._omit("equation (m:oMath) carries no markup")
            return None
        run = WordRun(text=_equation_text(element), equation_xml=markup)
        return _Segment(runs=(run,))

    # ------------------------------------------------------------------
    # Carried parts
    # ------------------------------------------------------------------

    def _object_part_ref(self, element: etree._Element) -> str | None:
        """Return the part an embedded object anchors, or ``None``.

        A ``w:object`` holds the embedded object itself and, usually, the
        shape that draws its preview.  The preview has a relationship of its
        own, so the object is picked out of the two rather than guessed at.
        """
        rel_id = self._object_relationship(element)
        if not rel_id:
            self._omit("embedded object (w:object) carries no relationship")
            return None
        return self._part_reference(rel_id, "embedded object (w:object)")

    def _object_relationship(self, node: etree._Element) -> str:
        """Return the relationship id of the object inside a ``w:object``."""
        fallback = ""
        for element in node.iter():
            rel_id = (
                element.get(_r("id"))
                or element.get(_r("embed"))
                or element.get(_r("link"))
            )
            if not rel_id:
                continue
            if _local(element) in _OLE_RELATIONSHIP_ELEMENTS:
                return str(rel_id)
            if not fallback:
                fallback = str(rel_id)
        return fallback

    def _anchor_segment(self, element: etree._Element, tag: str) -> _Segment | None:
        """Turn a body reference that names its part into a run."""
        part_ref = self._anchor_part_ref(element, tag)
        if part_ref is None:
            return None
        return _Segment(runs=(WordRun(text="", part_ref=part_ref),))

    def _anchor_draft(self, element: etree._Element, tag: str) -> _Draft | None:
        """Turn a block-level body reference into the block it anchors."""
        part_ref = self._anchor_part_ref(element, tag)
        if part_ref is None:
            return None
        return _Draft(
            kind=WordBlockKind.PARAGRAPH,
            runs=(WordRun(text="", part_ref=part_ref),),
            text="",
        )

    def _anchor_part_ref(self, element: etree._Element, tag: str) -> str | None:
        """Carry the part a body reference names and return its part name."""
        rel_id = str(element.get(_r("id")) or "")
        description = f"{tag} reference (w:{tag})"
        if not rel_id:
            self._omit(f"{description} carries no relationship")
            return None
        return self._part_reference(rel_id, description)

    def _drawing_reference(self, node: etree._Element) -> tuple[str | None, str | None]:
        """Return the image id and the part name a drawing anchors.

        A drawing holds either a picture -- which travels in the media model --
        or another kind of graphic: a chart, a diagram.  A chart is a package
        part of its own, so it is carried through and anchored on the run
        instead of being reported as a lost image.
        """
        rel_id = self._graphic_data_relationship(node)
        if rel_id:
            return (None, self._part_reference(rel_id, "drawing"))
        return (self._image_reference(node), None)

    def _anchor_extent(self, node: etree._Element) -> tuple[int, int] | None:
        """Return the size the drawing is drawn at, in EMU, or ``None``.

        Word writes the size of a drawing in EMU (``wp:extent``) for the
        DrawingML it writes today, and in points (``v:shape/@style``) for the
        VML it still accepts.  A drawing that states no size has no size:
        the run carries ``None``, and the writer falls back to its default
        rather than to a size Word never asked for.
        """
        for element in node.iter():
            if element.tag not in (_wp("inline"), _wp("anchor")):
                continue
            extent = element.find(_wp("extent"))
            if extent is None:
                continue
            size = _emu_size(extent.get("cx"), extent.get("cy"))
            if size is not None:
                return size
        for element in node.iter():
            if element.tag != _v("shape"):
                continue
            size = _vml_size(element.get("style"))
            if size is not None:
                return size
        return None

    def _graphic_data_relationship(self, node: etree._Element) -> str:
        """Return the relationship id of a drawing's non-picture graphic data.

        A picture is not a carried part: it travels in the media model.  Every
        other graphic -- a chart, a diagram -- is a package part of its own,
        and the relationship that reaches it is what anchors it in the body.

        Where a drawing offers both a chart and its picture fallback, the
        branch Word itself prefers is the one that is anchored.
        """
        found = ""
        best_rank = -1
        for element in node.iter():
            if _local(element) != "graphicData":
                continue
            uri = str(element.get("uri") or "")
            if uri.endswith("/picture"):
                continue
            rel_id = self._graphic_relationship(element)
            if not rel_id:
                continue
            _, rank = self._alternate_branch(element)
            if rank > best_rank:
                best_rank = rank
                found = rel_id
        return found

    @staticmethod
    def _graphic_relationship(element: etree._Element) -> str:
        """Return the relationship id a graphic data element points at."""
        for attribute in _GRAPHIC_RELATIONSHIP_ATTRIBUTES:
            value = element.get(_r(attribute))
            if value:
                return str(value)
        for child in element.iter():
            for attribute in _GRAPHIC_RELATIONSHIP_ATTRIBUTES:
                value = child.get(_r(attribute))
                if value:
                    return str(value)
        return ""

    def _format_change_xml(self, properties: etree._Element | None) -> str | None:
        """Return the tracked formatting revision of a run, verbatim.

        The revision is kept as the markup Word wrote it, because the markup
        holds the run properties from before the change -- the very thing a
        formatting revision exists to record -- and rewriting it would lose
        the properties the model does not know.
        """
        if properties is None:
            return None
        change = properties.find(_w("rPrChange"))
        if change is None:
            return None
        try:
            markup = str(etree.tostring(change, encoding="unicode", with_tail=False))
        except (ValueError, TypeError) as exc:
            self._omit(f"formatting change (w:rPrChange) could not be serialised: {exc!r}")
            return None
        if not markup:
            self._omit("formatting change (w:rPrChange) carries no markup")
            return None
        return markup

    def _format_change_revision(
        self, properties: etree._Element | None
    ) -> WordRevision | None:
        """Build the revision a formatting change records."""
        if properties is None:
            return None
        change = properties.find(_w("rPrChange"))
        if change is None:
            return None
        return WordRevision(
            kind=WordRevisionKind.FORMAT_CHANGE,
            author=str(change.get(_w("author")) or ""),
            date=self._read_timestamp_value(
                str(change.get(_w("date")) or "").strip(), "revision"
            ),
            revision_id=str(change.get(_w("id")) or ""),
        )

    def _revision_for(
        self, element: etree._Element, kind: WordRevisionKind
    ) -> WordRevision:
        """Build the revision an element records."""
        author = str(element.get(_w("author")) or "")
        date = self._read_timestamp_value(
            str(element.get(_w("date")) or "").strip(), "revision"
        )
        revision_id = str(element.get(_w("id")) or "")
        return WordRevision(
            kind=kind,
            author=author,
            date=date,
            revision_id=revision_id,
        )

    def _run_segments(
        self,
        run: etree._Element,
        hyperlink: str | None,
        revision: WordRevision | None = None,
    ) -> list[_Segment]:
        """Turn one run into the segments it contributes."""
        properties = run.find(_w("rPr"))
        formatting = self._read_run_properties(properties)
        format_change_xml = self._format_change_xml(properties)
        if format_change_xml is not None and revision is None:
            # A formatting revision is a revision of the run's *properties*.
            # It is recorded as such when the run is not already part of a
            # text revision, which the model can only hold one of.
            revision = self._format_change_revision(properties)

        segments: list[_Segment] = []
        parts: list[str] = []
        footnote_ref: str | None = None
        endnote_ref: str | None = None
        image_ref: str | None = None
        part_ref: str | None = None
        anchor_extent: tuple[int, int] | None = None
        comment_ids = tuple(self._open_comment_ranges)

        def flush() -> None:
            nonlocal parts, footnote_ref, endnote_ref, image_ref, part_ref, anchor_extent
            if (
                parts
                or footnote_ref is not None
                or endnote_ref is not None
                or image_ref is not None
                or part_ref is not None
            ):
                segments.append(
                    _Segment(
                        runs=(
                            WordRun(
                                text="".join(parts),
                                bold=formatting.bold,
                                italic=formatting.italic,
                                underline=formatting.underline,
                                superscript=formatting.superscript,
                                subscript=formatting.subscript,
                                strike=formatting.strike,
                                font=formatting.font,
                                size=formatting.size,
                                color=formatting.color,
                                highlight=formatting.highlight,
                                hyperlink=hyperlink,
                                footnote_ref=footnote_ref,
                                endnote_ref=endnote_ref,
                                image_ref=image_ref,
                                comment_refs=comment_ids,
                                revision=revision,
                                part_ref=part_ref,
                                anchor_extent=anchor_extent,
                                format_change_xml=format_change_xml,
                            ),
                        )
                    )
                )
                parts = []
                footnote_ref = None
                endnote_ref = None
                image_ref = None
                part_ref = None
                anchor_extent = None

        for child in run:
            tag = _local(child)
            if not tag or tag == "rPr":
                continue
            if tag == "t":
                parts.append(strip_control_characters(str(child.text or "")))
            elif tag == "delText":
                # The text of a deleted run lives in ``w:delText``. It is kept
                # -- the revision is part of the document -- but it is not part
                # of the document's final text.
                parts.append(strip_control_characters(str(child.text or "")))
            elif tag == "tab":
                parts.append(" ")
            elif tag == "br":
                if str(child.get(_w("type")) or "").strip().lower() == "page":
                    flush()
                    segments.append(_Segment(is_break=True))
                else:
                    parts.append(" ")
            elif tag == "cr":
                parts.append(" ")
            elif tag == "noBreakHyphen":
                parts.append("-")
            elif tag == "softHyphen":
                parts.append(" ")
            elif tag == "ptab":
                parts.append(" ")
            elif tag == "footnoteReference":
                reference = str(child.get(_w("id")) or "")
                if not reference:
                    continue
                if reference in self._footnote_runs:
                    footnote_ref = reference
                else:
                    self._omit(f"footnote reference to unknown footnote id {reference}")
            elif tag == "endnoteReference":
                reference = str(child.get(_w("id")) or "")
                if not reference:
                    continue
                if reference in self._endnote_runs:
                    endnote_ref = reference
                else:
                    self._omit(f"endnote reference to unknown endnote id {reference}")
            elif tag in {"oMath", "oMathPara"}:
                # Word keeps an equation beside the runs of a paragraph, but a
                # producer may nest one inside a run.  It must not be lost
                # there either, so it becomes a segment of its own.
                flush()
                equation = self._equation_segment(child)
                if equation is not None:
                    segments.append(equation)
            elif tag == "drawing":
                image_ref, part_ref = self._drawing_reference(child)
                anchor_extent = self._anchor_extent(child)
                blocks = self._drawing_blocks(child)
                if blocks:
                    flush()
                    segments.append(_Segment(blocks=tuple(blocks)))
            elif tag == "pict":
                image_ref, part_ref = self._drawing_reference(child)
                anchor_extent = self._anchor_extent(child)
                blocks = self._drawing_blocks(child)
                if blocks:
                    flush()
                    segments.append(_Segment(blocks=tuple(blocks)))
            elif tag == "object":
                part_ref = self._object_part_ref(child)
                anchor_extent = self._anchor_extent(child)
            elif tag == "commentReference":
                continue
            elif tag == "sym":
                self._append_symbol(child, parts)
            elif tag in {"instrText", "delInstrText", "fldChar", "lastRenderedPageBreak"}:
                continue
        flush()
        return segments

    def _append_symbol(self, element: etree._Element, parts: list[str]) -> None:
        """Append the character a ``w:sym`` stands for to the run's text.

        A symbol run carries a font and a code rather than text, so the code
        has to be translated through the font's character set.  A code the
        table does not know is reported and contributes no text at all: a run
        that carried nothing else stays empty rather than gaining a wrong
        character.
        """
        font = str(element.get(_w("font")) or "").strip()
        raw_code = str(element.get(_w("char")) or "").strip()
        code = _symbol_code(raw_code)
        table = _SYMBOL_CHARS.get(font.casefold())
        character = table.get(code) if table is not None and code is not None else None
        if character is None:
            self._omit(
                f"symbol (w:sym) {font or 'unknown font'} "
                f"{raw_code or 'no code'} has no known character"
            )
            return
        parts.append(character)

    def _hyperlink_target(self, hyperlink: etree._Element) -> str | None:
        """Resolve the target of a ``w:hyperlink`` element."""
        rel_id = hyperlink.get(_r("id"))
        if rel_id:
            relationship = self._rels.get(str(rel_id))
            if relationship is None:
                self._omit(f"hyperlink relationship {rel_id} is missing")
                return None
            return self._resolve_target(relationship.target) or None
        anchor = hyperlink.get(_w("anchor"))
        if anchor:
            return f"#{anchor}"
        return None

    def _resolve_target(self, target: str) -> str:
        """Resolve a relationship target against the caller's base URI.

        An absolute target -- one carrying a URI scheme -- is used verbatim. A
        relative target is resolved against ``base_uri`` when the caller
        supplied one, following ``urllib.parse.urljoin`` semantics. Without a
        base there is no way of knowing where the document lives, so the
        target is preserved as it is and recorded in the omission register
        rather than dropped or resolved against a guess.
        """
        if not target:
            return ""
        if _has_uri_scheme(target):
            return target
        base = self._base_uri
        if not base:
            self._omit(f"relative hyperlink target ({target})")
            return target
        return urljoin(base, target)

    def _report_image(self, rel_id: str, part_name: str) -> None:
        """Record an image the reader could not keep.

        The register names the relationship *and* the part, so an operator
        looking at a report can say exactly which image was lost rather than
        only that "an image" was.
        """
        self._omit(f"image (rel {rel_id}, part {part_name})")

    def _image_relationship(self, node: etree._Element) -> str:
        """Return the relationship id of the image inside a drawing."""
        for element in node.iter():
            if _local(element) not in {"blip", "imagedata"}:
                continue
            rel_id = element.get(_r("embed")) or element.get(_r("id"))
            if rel_id:
                return str(rel_id)
        return ""

    def _image_reference(self, node: etree._Element) -> str | None:
        """Return the media id of the image inside a drawing.

        An image the reader can reach travels in
        :attr:`~lmeditor.word.model.WordDocument.media` and is re-emitted on
        write. An image it *cannot* reach -- a broken relationship, a part
        that is not in the package -- is reported in the omission register
        with its relationship id and part name.
        """
        rel_id = self._image_relationship(node)
        if not rel_id:
            self._report_image("unknown", "unknown")
            return None
        relationship = self._rels.get(rel_id)
        if relationship is None:
            self._report_image(rel_id, "unknown")
            return None
        part_name = self._part_name_for(relationship.target)
        media = self._load_media(part_name, rel_id)
        if media is None:
            self._report_image(rel_id, part_name)
            return None
        return media.media_id

    def _load_media(self, part_name: str, rel_id: str = "") -> WordMedia | None:
        """Load an embedded media part into the model."""
        existing = self._media.get(part_name)
        if existing is not None:
            return existing
        archive = self._archive
        if archive is None or part_name not in archive.namelist():
            return None
        try:
            data = self._read_part_bytes(archive, part_name, self._origin)
        except WordParseError:
            raise
        media = WordMedia(
            media_id=part_name,
            part_name=part_name,
            content_type=self._content_type_for(part_name),
            data=data,
            relationship_id=rel_id,
        )
        self._media[part_name] = media
        return media

    def _content_type_for(self, part_name: str) -> str:
        """Return the content type of a package part."""
        override = self._content_types.get("/" + part_name)
        if override:
            return override
        extension = part_name.rsplit(".", 1)[-1].lower() if "." in part_name else ""
        default = self._content_types.get(extension)
        if default:
            return default
        return _DEFAULT_CONTENT_TYPES.get(extension, "application/octet-stream")

    def _load_content_types(self, archive: zipfile.ZipFile, origin: str) -> None:
        """Load ``[Content_Types].xml`` so media parts can be typed."""
        if "[Content_Types].xml" not in archive.namelist():
            return
        root = self._parse_part(archive, "[Content_Types].xml", origin)
        for element in root:
            tag = _local(element)
            if tag == "Default":
                extension = str(element.get("Extension") or "").strip().lower()
                content_type = str(element.get("ContentType") or "").strip()
                if extension and content_type:
                    self._content_types.setdefault(extension, content_type)
            elif tag == "Override":
                part_name = str(element.get("PartName") or "").strip()
                content_type = str(element.get("ContentType") or "").strip()
                if part_name and content_type:
                    self._content_types.setdefault(part_name, content_type)

    def _drawing_blocks(self, node: etree._Element) -> list[_Draft]:
        """Collect the blocks of the text boxes anchored in a drawing."""
        drafts: list[_Draft] = []
        for text_box in self._select_text_boxes(node):
            drafts.extend(self._walk_blocks(text_box))
        return drafts

    def _select_text_boxes(self, node: etree._Element) -> list[etree._Element]:
        """Select the text boxes of a drawing, preferring ``mc:Choice``."""
        candidates = [element for element in node.iter() if _local(element) == "txbxContent"]
        if not candidates:
            return []
        groups: dict[int, tuple[int, list[etree._Element]]] = {}
        for element in candidates:
            ancestor, rank = self._alternate_branch(element)
            key = id(ancestor) if ancestor is not None else -1
            current = groups.get(key)
            if current is None or rank > current[0]:
                groups[key] = (rank, [element])
            else:
                current[1].append(element)
        selected: list[etree._Element] = []
        for _, elements in groups.values():
            selected.extend(elements)
        return selected

    def _alternate_branch(self, element: etree._Element) -> tuple[etree._Element | None, int]:
        """Return the ``mc:AlternateContent`` ancestor of an element and its rank."""
        child = element
        parent = element.getparent()
        while parent is not None:
            if _local(parent) == "AlternateContent":
                rank = 0
                for branch in parent:
                    name = _local(branch)
                    if name in {"Choice", "Fallback"} and branch is child:
                        rank = 2 if name == "Choice" else 1
                        break
                return parent, rank
            child = parent
            parent = parent.getparent()
        return None, 0

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _table_draft(self, table: etree._Element) -> _Draft:
        """Turn one table into a table block."""
        row_elements = self._live_rows(table)
        resolved = self._resolve_table_rows(
            row_elements, self._table_grid_columns(table)
        )

        rows: list[WordTableRow] = []
        header_rows = 0
        for row_index, row_element in enumerate(row_elements):
            is_header = self._row_is_header(row_element)
            if is_header:
                header_rows += 1
            cells = [
                self._table_cell(
                    layout.element,
                    is_header,
                    col_span=layout.col_span,
                    merge_state=resolved[row_index].states.get(cell_index),
                )
                for cell_index, layout in enumerate(resolved[row_index].layout)
            ]
            rows.append(WordTableRow(cells=tuple(cells)))
        return _Draft(
            kind=WordBlockKind.TABLE,
            table=WordTable(
                rows=tuple(rows),
                header_rows=header_rows,
                column_widths=self._table_column_widths(table),
            ),
        )

    def _table_grid_columns(self, table: etree._Element) -> int:
        """Return the number of grid columns the table declares."""
        grid = table.find(_w("tblGrid"))
        if grid is None:
            return 0
        return len(grid.findall(_w("gridCol")))

    def _table_column_widths(self, table: etree._Element) -> tuple[int, ...]:
        """Return the width of every grid column of a table, in twips."""
        grid = table.find(_w("tblGrid"))
        if grid is None:
            return ()
        widths: list[int] = []
        for column in grid.findall(_w("gridCol")):
            width = _safe_int(column.get(_w("w")), 0)
            if width > 0:
                widths.append(width)
        return tuple(widths)

    def _live_rows(self, table: etree._Element) -> list[etree._Element]:
        """Return the rows of a table that survive a tracked deletion.

        A row marked with ``w:trPr/w:del`` is gone from the document, so it
        must not be counted as a row a vertical merge covers.
        """
        return [row for row in self._table_rows(table) if not self._row_is_deleted(row)]

    def _table_rows(self, table: etree._Element) -> list[etree._Element]:
        """Collect the rows of a table without descending into nested tables."""
        rows: list[etree._Element] = []

        def scan(node: etree._Element) -> None:
            for child in node:
                tag = _local(child)
                if tag == "tr":
                    rows.append(child)
                elif tag == "tbl":
                    continue
                elif tag == "sdt":
                    content = child.find(_w("sdtContent"))
                    if content is not None:
                        scan(content)
                elif tag in {"ins", "moveTo", "customXml"}:
                    scan(child)

        scan(table)
        return rows

    def _row_cells(self, row: etree._Element) -> list[etree._Element]:
        """Collect the cells of a table row."""
        cells: list[etree._Element] = []

        def scan(node: etree._Element) -> None:
            for child in node:
                tag = _local(child)
                if tag == "tc":
                    cells.append(child)
                elif tag == "sdt":
                    content = child.find(_w("sdtContent"))
                    if content is not None:
                        scan(content)
                elif tag in {"ins", "moveTo", "customXml"}:
                    scan(child)

        scan(row)
        return cells

    def _row_is_header(self, row: etree._Element) -> bool:
        """Report whether a row repeats as a table header."""
        properties = row.find(_w("trPr"))
        if properties is None:
            return False
        return properties.find(_w("tblHeader")) is not None

    def _row_is_deleted(self, row: etree._Element) -> bool:
        """Report whether a row is marked as deleted by a tracked change."""
        properties = row.find(_w("trPr"))
        if properties is None:
            return False
        return properties.find(_w("del")) is not None

    def _resolve_table_rows(
        self, rows: list[etree._Element], declared_columns: int
    ) -> list[_ResolvedRow]:
        """Lay the rows of a table out on the grid and resolve their merges.

        A table's grid is as wide as its widest row: the cells of a row tile it
        from left to right, and a row that falls short of it is a malformed
        table (W4, W12).

        A vertical merge occupies *every* grid column its origin spans, and the
        cell that continues the merge is that region: it covers the same
        columns, so the cells that follow it start after the merged region and
        keep their own text. A continuation takes only the columns the row has
        room for -- a row whose other cells already fill the grid leaves the
        continuation the width it declares -- and a cell that then lands inside
        the region belongs to the merge and carries no text of its own.

        Returns:
            One resolved row per row, in order.
        """
        natural = [
            sum(self._cell_grid_span(cell) for cell in self._row_cells(row))
            for row in rows
        ]
        grid_width = max([declared_columns, *natural])
        resolved: list[_ResolvedRow] = []
        open_merges: list[_OpenMerge] = []
        merges: list[_OpenMerge] = []
        for row_index, row in enumerate(rows):
            cells = self._row_cells(row)
            fixed = sum(
                self._cell_grid_span(cell)
                for cell in cells
                if self._cell_merge_state(cell) != _MERGE_CONTINUE
            )
            room = grid_width - fixed
            layout: list[_CellLayout] = []
            states: dict[int, _MergeState] = {}
            continued: set[_OpenMerge] = set()
            cursor = 0
            for cell_index, cell in enumerate(cells):
                own = self._cell_grid_span(cell)
                merge_state = self._cell_merge_state(cell)
                merge = (
                    _merge_covering(open_merges, cursor, skip=continued)
                    if merge_state == _MERGE_CONTINUE
                    else None
                )
                if merge is not None:
                    # The cell continues the merge: it *is* the merged region
                    # in this row, so it covers every column the origin spans.
                    span = max(own, min(merge.column_span, room))
                    room -= span
                    continued.add(merge)
                    merge.row_span += 1
                    states[cell_index] = _MergeState(is_continuation=True)
                    layout.append(
                        _CellLayout(
                            element=cell,
                            grid_column=merge.start_column,
                            col_span=span,
                        )
                    )
                    cursor = merge.start_column + span
                    continue
                column = cursor
                layout.append(
                    _CellLayout(element=cell, grid_column=column, col_span=own)
                )
                cursor += own
                if merge_state == _MERGE_RESTART:
                    # A new merge replaces every merge it overlaps.
                    open_merges = [
                        merge
                        for merge in open_merges
                        if not merge.overlaps(column, own)
                    ]
                    origin = _OpenMerge(
                        origin_key=(row_index, cell_index),
                        origin_row=row_index,
                        start_column=column,
                        column_span=own,
                    )
                    merges.append(origin)
                    open_merges.append(origin)
                    states[cell_index] = _MergeState(row_span=1)
                elif merge_state == _MERGE_CONTINUE:
                    # A continuation without an origin above it belongs to no
                    # merge, so it stays an ordinary cell.
                    room -= own
            # A cell that starts at a grid column the merge already covers
            # belongs to the merged region even when it carries no
            # ``w:vMerge`` of its own: the merge covers that grid position,
            # so Word never displays the cell's text.
            for cell_index, cell_layout in enumerate(layout):
                if cell_index in states:
                    continue
                covering = _merge_covering(open_merges, cell_layout.grid_column)
                if covering is not None and covering in continued:
                    states[cell_index] = _MergeState(is_continuation=True)
            # A merge that no cell continues in this row is closed.
            open_merges = [
                merge
                for merge in open_merges
                if merge.origin_row == row_index or merge in continued
            ]
            resolved.append(_ResolvedRow(layout=layout, states=states))
        for merge in merges:
            resolved[merge.origin_key[0]].states[merge.origin_key[1]] = _MergeState(
                row_span=merge.row_span
            )
        return resolved

    def _cell_grid_span(self, cell: etree._Element) -> int:
        """Return the number of grid columns a cell occupies."""
        properties = cell.find(_w("tcPr"))
        if properties is None:
            return 1
        grid_span = properties.find(_w("gridSpan"))
        if grid_span is None:
            return 1
        return max(1, _safe_int(grid_span.get(_w("val")), 1))

    def _cell_merge_state(self, cell: etree._Element) -> str:
        """Return the vertical-merge state of a cell.

        The state is ``restart`` for the origin of a merge, ``continue`` for
        the rows the merge covers and an empty string for a cell that is part
        of no merge. A bare ``w:vMerge`` -- one without ``w:val`` -- means
        *continue*: that is the default of ``ST_Merge`` and the form Word
        writes below the origin of a merge.
        """
        properties = cell.find(_w("tcPr"))
        if properties is None:
            return ""
        merge = properties.find(_w("vMerge"))
        if merge is None:
            return ""
        value = str(merge.get(_w("val")) or "").strip().lower()
        if value == _MERGE_RESTART:
            return _MERGE_RESTART
        return _MERGE_CONTINUE

    def _table_cell(
        self,
        cell: etree._Element,
        is_header: bool,
        *,
        col_span: int,
        merge_state: _MergeState | None = None,
    ) -> WordTableCell:
        """Build one table cell.

        A vertical merge is represented the way Word represents it: the cell
        that starts the merge keeps its text and reports the number of rows
        the merge occupies, and the cells below it are continuation cells that
        carry no text of their own, because Word ignores the content stored
        in a continuation cell. The model enforces that rule (D4), so a
        continuation cell is built with empty text and a span of one row --
        never with the text of the cell it continues.

        The column span is the one the grid resolved, not the one the cell
        declares: a cell that continues a merge is as wide as the merged
        region it is.
        """
        state = merge_state if merge_state is not None else _MergeState()
        if state.is_continuation:
            return WordTableCell(
                text="",
                row_span=1,
                col_span=col_span,
                is_header=is_header,
                is_merge_continuation=True,
            )
        return WordTableCell(
            text=self._cell_text(cell),
            row_span=state.row_span,
            col_span=col_span,
            is_header=is_header,
            is_merge_continuation=False,
            width=self._cell_width(cell),
            runs=self._cell_runs(cell),
            tables=self._nested_tables(cell),
        )

    def _cell_width(self, cell: etree._Element) -> int | None:
        """Return the width of a cell in twips, when the document declares one."""
        properties = cell.find(_w("tcPr"))
        if properties is None:
            return None
        width = properties.find(_w("tcW"))
        if width is None:
            return None
        raw = str(width.get(_w("w")) or "").strip()
        if not raw.isdigit():
            return None
        value = int(raw)
        return value if value > 0 else None

    def _cell_runs(self, cell: etree._Element) -> tuple[WordRun, ...]:
        """Return the inline runs of a cell.

        A cell holds paragraphs, not a single string: the runs carry the
        formatting, the hyperlinks and the footnote references the flat text
        projection cannot show.
        """
        runs: list[WordRun] = []
        for child in cell:
            tag = _local(child)
            if tag == "p":
                for segment in self._walk_inline(child, None):
                    if segment.is_break or segment.blocks:
                        continue
                    runs.extend(segment.runs)
            elif tag == "sdt":
                content = child.find(_w("sdtContent"))
                if content is None:
                    self._omit("content control without content (w:sdt)")
                    continue
                runs.extend(self._cell_runs(content))
        return tuple(runs)

    def _nested_tables(self, cell: etree._Element) -> tuple[WordTable, ...]:
        """Return the tables nested inside a cell.

        A nested table is a table, not a string: flattening it loses the
        structure an operator has to see, so it travels as a table of its own.
        """
        tables: list[WordTable] = []
        for child in cell:
            tag = _local(child)
            if tag == "tbl":
                draft = self._table_draft(child)
                if draft.table is not None:
                    tables.append(draft.table)
            elif tag == "sdt":
                content = child.find(_w("sdtContent"))
                if content is None:
                    self._omit("content control without content (w:sdt)")
                    continue
                tables.extend(self._nested_tables(content))
        return tuple(tables)

    def _cell_text(self, cell: etree._Element) -> str:
        """Return the text of a table cell."""
        parts = [text for text in self._cell_parts(cell) if text]
        return normalize_whitespace(" ".join(parts))

    def _cell_parts(self, cell: etree._Element) -> list[str]:
        """Collect the text fragments of a table cell."""
        parts: list[str] = []
        for child in cell:
            tag = _local(child)
            if not tag:
                continue
            if tag == "p":
                text = self._paragraph_text(child)
                if text:
                    parts.append(text)
            elif tag == "tbl":
                text = self._nested_table_text(child)
                if text:
                    parts.append(text)
            elif tag == "sdt":
                content = child.find(_w("sdtContent"))
                if content is None:
                    self._omit("content control without content (w:sdt)")
                    continue
                parts.extend(self._cell_parts(content))
            elif tag in {"ins", "moveTo", "customXml"}:
                parts.extend(self._cell_parts(child))
            elif tag in {"del", "moveFrom"}:
                continue
        return parts

    def _nested_table_text(self, table: etree._Element) -> str:
        """Flatten a nested table into text."""
        draft = self._table_draft(table)
        if draft.table is None:
            return ""
        rows = [
            " | ".join(cell.text for cell in row.cells) for row in draft.table.rows
        ]
        return normalize_whitespace(" ".join(rows))

    def _paragraph_text(self, paragraph: etree._Element) -> str:
        """Return the text of a paragraph, including any nested text boxes."""
        parts: list[str] = []
        for segment in self._walk_inline(paragraph, None):
            if segment.is_break:
                continue
            if segment.blocks:
                parts.extend(draft.text for draft in segment.blocks)
            else:
                parts.append("".join(run.text for run in segment.runs))
        return normalize_whitespace(" ".join(parts))

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _build_blocks(self, drafts: list[_Draft]) -> tuple[WordBlock, ...]:
        """Give every draft its stable id and turn it into a model block."""
        blocks: list[WordBlock] = []
        for index, draft in enumerate(drafts, start=1):
            blocks.append(
                WordBlock(
                    block_id=f"b{index:04d}",
                    kind=draft.kind,
                    text=draft.text,
                    level=draft.level,
                    list_marker=draft.list_marker,
                    list_group=draft.list_group,
                    number_format=draft.number_format,
                    number_text=draft.number_text,
                    style=draft.style,
                    runs=draft.runs,
                    table=draft.table,
                )
            )
        return tuple(blocks)

    def _build_footnotes(self) -> tuple[WordFootnote, ...]:
        """Turn the collected footnote runs into model footnotes."""
        footnotes: list[WordFootnote] = []
        for footnote_id in sorted(self._footnote_runs, key=_numeric_key):
            runs = self._footnote_runs[footnote_id]
            footnotes.append(
                WordFootnote(
                    footnote_id=footnote_id,
                    text=normalize_whitespace(
                        "".join(run.text for run in runs if not run.is_deleted)
                    ),
                    runs=runs,
                )
            )
        return tuple(footnotes)

    def _build_endnotes(self) -> tuple[WordEndnote, ...]:
        """Turn the collected endnote runs into model endnotes."""
        endnotes: list[WordEndnote] = []
        for endnote_id in sorted(self._endnote_runs, key=_numeric_key):
            runs = self._endnote_runs[endnote_id]
            endnotes.append(
                WordEndnote(
                    endnote_id=endnote_id,
                    text=normalize_whitespace(
                        "".join(run.text for run in runs if not run.is_deleted)
                    ),
                    runs=runs,
                )
            )
        return tuple(endnotes)

    def _build_comments(self, blocks: tuple[WordBlock, ...]) -> tuple[WordComment, ...]:
        """Turn the collected comments into model comments.

        The anchor of a comment is the range of runs it covers: the block the
        first of those runs sits in, and the text they carry together.
        """
        anchors: dict[str, tuple[str | None, list[str]]] = {}
        for block in blocks:
            for run in block.runs:
                for comment_id in run.comment_refs:
                    block_id, texts = anchors.setdefault(comment_id, (None, []))
                    if block_id is None:
                        block_id = block.block_id
                    texts.append(run.text)
                    anchors[comment_id] = (block_id, texts)

        comments: list[WordComment] = []
        for comment_id in sorted(self._comment_runs, key=_numeric_key):
            runs = self._comment_runs[comment_id]
            author, initials, date = self._comment_meta.get(comment_id, ("", "", None))
            block_id, texts = anchors.get(comment_id, (None, []))
            comments.append(
                WordComment(
                    comment_id=comment_id,
                    author=author,
                    initials=initials,
                    date=date,
                    text=normalize_whitespace(
                        "".join(run.text for run in runs if not run.is_deleted)
                    ),
                    runs=runs,
                    anchor_block_id=block_id,
                    anchor_text=normalize_whitespace("".join(texts)),
                )
            )
        return tuple(comments)

    def _build_styles(self) -> tuple[WordStyle, ...]:
        """Turn the styles the document uses into model styles.

        Only the styles a paragraph actually uses are carried, together with
        the chain they are based on: the writer has to be able to rebuild the
        chain, and an unused style is not worth carrying.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for style_id in sorted(self._used_styles):
            current = style_id
            while current and current not in seen:
                seen.add(current)
                wanted.append(current)
                info = self._lookup_style(current)
                if info is None:
                    break
                current = info.based_on
        styles: list[WordStyle] = []
        for style_id in wanted:
            info = self._lookup_style(style_id)
            if info is None:
                continue
            styles.append(
                WordStyle(
                    style_id=info.style_id,
                    name=info.name,
                    based_on=info.based_on,
                    style_type="paragraph",
                    num_id=info.num_id,
                    num_level=info.num_level,
                )
            )
        return tuple(styles)

    def _omit(self, description: str) -> None:
        """Record that content was skipped, loudly."""
        self._omission_counts[description] = self._omission_counts.get(description, 0) + 1
        self.last_omissions = self._render_omissions()

    def _render_omissions(self) -> list[str]:
        """Render the omission register as human-readable descriptions."""
        return [
            description if count == 1 else f"{description} x{count}"
            for description, count in sorted(self._omission_counts.items())
        ]


__all__ = ["WordReader"]
