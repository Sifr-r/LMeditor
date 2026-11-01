"""The frozen witness of the Word writer.

This is the implementation that used to live in ``lmeditor.word.writer``, kept
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

The rules frozen here are the writer's whole behaviour: which Word construct
every block kind maps onto, how the package's relationships and carried parts
are written back, and the refusals that keep a malformed document from being
rendered at all.

Word document writer: the shared model rendered back into a ``.docx`` package.

This module is the write half of LMeditor's word-processing element (contract
§6).  It maps the format-neutral :class:`~lmeditor.word.model.WordDocument`
produced by the reader or by the manuscript bridge onto an OPC package that
Word opens without a repair prompt.

``python-docx`` is the package builder.  Raw OOXML is injected where
``python-docx`` has no API at all: multi-level ``numbering.xml``,
``footnotes.xml``, hyperlink relationships, the OMML of an equation and the
core properties.

The writer is deliberately *structural*.  Every block kind in the model maps
onto a real Word construct — built-in styles, a real numbering definition, a
real table grid, real footnote references — so that a document survives the
round trip with its shape intact instead of being flattened into headings and
plain paragraphs.  The package parts the reader carried through are written
back under their own names, with their bytes, their content types and their
relationship ids, so that a document whose evidence is a chart opens with
that chart still in it.

The relationships of the package are as much a part of what a document is as
its parts are.  A part the document reached is related from the document
under the id it was reached by; a part the document did not reach is related
from the part that owns it, because a chart's colour style and the workbook
embedded behind it belong to the chart and to nothing else.  A part the
*package root* reached -- the custom document properties, for one -- is
related from the package root, which is the other root of the package's
relationship graph.  A part that no relationship path reaches cannot travel
at all, and the writer names it rather than losing it in silence.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from typing import Any, Final
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.oxml import serialize_part_xml
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, nsmap, qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.shared import Emu, Pt, RGBColor, Twips
from lxml import etree
from pydantic import ValidationError

from lmeditor.core.exceptions import WordRenderError
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordMedia,
    WordPart,
    WordPartRelationship,
    WordRevision,
    WordRevisionKind,
    WordRun,
    WordSection,
    WordStyle,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.text import strip_control_characters

#: Number of list levels (0-8) defined for every abstract numbering definition.
_LIST_LEVEL_COUNT: Final[int] = 9

#: Lowest ``w:id`` that a model footnote may be mapped onto.  Ids 0 and 1 are
#: reserved for the separator and continuation-separator footnotes that Word
#: requires to be present.
_FIRST_FOOTNOTE_ID: Final[int] = 2

#: Zip member timestamp used for deterministic output (the DOS epoch).
_FIXED_ZIP_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)

#: Part name and content type of the hand-built footnotes part.
_FOOTNOTES_PARTNAME: Final[str] = "/word/footnotes.xml"
_FOOTNOTES_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
)

#: Part name and content type of the hand-built endnotes part.
_ENDNOTES_PARTNAME: Final[str] = "/word/endnotes.xml"
_ENDNOTES_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"
)

#: Part name and content type of the hand-built comments part.
_COMMENTS_PARTNAME: Final[str] = "/word/comments.xml"
_COMMENTS_CONTENT_TYPE: Final[str] = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)

#: Lowest ``w:id`` that a model endnote may be mapped onto.
_FIRST_ENDNOTE_ID: Final[int] = 2

#: Lowest ``w:id`` that a model comment may be mapped onto.
_FIRST_COMMENT_ID: Final[int] = 1

#: Total width of a table in twips (US Letter minus one-inch margins).
_TABLE_TOTAL_WIDTH: Final[int] = 9360

#: The extent an image is given when its pixel size cannot be read.
_DEFAULT_IMAGE_EXTENT: Final[int] = 1905000

#: The extent a chart or a diagram is drawn at when its size was not
#: recorded.  Word scales an anchored element by rewriting the extent of the
#: drawing that holds it, so an element that comes back at a default size is
#: an element that came back the wrong size.
_DEFAULT_DRAWING_EXTENT: Final[tuple[int, int]] = (1905000, 1905000)

#: The extent an embedded object is drawn at when its size was not recorded:
#: a hundred by fifty points, the shape size Word writes for one.
_DEFAULT_OBJECT_EXTENT: Final[tuple[int, int]] = (1270000, 635000)

#: The number of English Metric Units in one inch.
_EMU_PER_INCH: Final[int] = 914400

#: The number of English Metric Units in one point.  The shape of an
#: embedded object is sized in points, so a recorded extent has to be
#: converted before it can be written into one.
_EMU_PER_POINT: Final[int] = 12700

#: Indentation step, in twips, applied per list nesting level.
_LIST_INDENT_STEP: Final[int] = 720

#: Hanging indent, in twips, applied to list paragraphs.
_LIST_HANGING: Final[int] = 360

#: A single section can carry at most one header and one footer of each of the
#: three reference types (default, first page, even page).
_MAX_HEADERS: Final[int] = 3
_MAX_FOOTERS: Final[int] = 3

#: OOXML number format for each marker of the shared model.  The model's
#: ``number`` marker is spelled ``decimal`` in WordprocessingML.
_NUM_FORMAT: Final[dict[ListMarker, str]] = {
    ListMarker.BULLET: "bullet",
    ListMarker.NUMBER: "decimal",
}

#: The revision kinds whose text is not part of the document's final text.
_DELETION_KINDS: Final[frozenset[WordRevisionKind]] = frozenset(
    {WordRevisionKind.DELETION, WordRevisionKind.MOVE_FROM}
)

#: The cell a synthesised merge continuation is rendered with.
_EMPTY_CELL: Final[WordTableCell] = WordTableCell(text="")

#: The OMML namespace.  ``python-docx``'s namespace map already carries it,
#: but the map is shared by the whole package and must not be mutated, so
#: the value is copied here and bound onto the fragments this module parses.
_NS_M: Final[str] = nsmap.get(
    "m", "http://schemas.openxmlformats.org/officeDocument/2006/math"
)

#: The VML namespace, used by the shape that draws an embedded object.
_NS_V: Final[str] = "urn:schemas-microsoft-com:vml"

#: The Office namespace, used by the ``o:OLEObject`` element.
_NS_O: Final[str] = "urn:schemas-microsoft-com:office:office"

#: The body reference each kind of carried part is anchored by, keyed by the
#: last segment of the relationship that reaches the part.
#:
#: The relationship is what says how a part belongs in the body: an OLE
#: object is anchored by ``w:object``, a chart by a DrawingML graphic, an
#: alternative-format chunk by ``w:altChunk``.  A part the model carries but
#: that the body never points at -- a glossary document, say -- is not in
#: this table, and no body reference is written for it.
_PART_ANCHOR_KINDS: Final[dict[str, str]] = {
    "oleObject": "object",
    "package": "object",
    "chart": "chart",
    "diagramData": "diagram",
    "aFChunk": "altChunk",
    "subDocument": "subDoc",
}

#: The body references that stand beside the paragraphs rather than inside a
#: run: Word expects them in the body of the document, not in a run.
_BLOCK_LEVEL_ANCHORS: Final[frozenset[str]] = frozenset({"altChunk", "subDoc"})

#: The relationship types of the parts a diagram is drawn from, keyed by the
#: attribute the diagram names them with.
_DIAGRAM_RELATIONSHIPS: Final[dict[str, str]] = {
    "dm": "diagramData",
    "lo": "diagramLayout",
    "qs": "diagramQuickStyle",
    "cs": "diagramColors",
}


class _NotesPart(Part):
    """Package part that serialises a notes or comments part on demand.

    ``python-docx`` has no footnote, endnote or comment support, so the part
    is built by hand.  Serialising lazily (rather than passing a fixed blob)
    lets hyperlink relationships inside the runs be resolved against this part
    while the XML tree is still being assembled.
    """

    def __init__(
        self,
        partname: PackURI,
        content_type: str,
        package: Any,
        element: Any,
    ) -> None:
        super().__init__(partname, content_type, None, package)
        self._notes_element = element

    @classmethod
    def load(
        cls,
        partname: PackURI,
        content_type: str,
        blob: bytes,
        package: Any,
    ) -> Part:
        """Load an existing notes part as a plain :class:`Part`.

        ``python-docx`` registers no part class for the footnotes content type,
        so this path is only reachable if the content type is ever mapped.
        """
        return Part(partname, content_type, blob, package)

    @property
    def blob(self) -> bytes:
        """Serialised XML of the notes part."""
        return serialize_part_xml(self._notes_element)


class _NumberingPlan:
    """Owns the ``numbering.xml`` mutations required by one document.

    One ``w:abstractNum`` is created per *level signature* -- the per-level
    ``w:numFmt`` and ``w:lvlText`` the source document used -- and one
    ``w:num`` is created per continuity group, so that a new group restarts
    its numbering at 1 while items of the same group continue a single
    sequence.

    The format and the level text are the source document's, not a guess: a
    roman or alpha list keeps looking the way it did.  A block that carries
    neither falls back to the model's own default.
    """

    def __init__(self, doc: Any, document: WordDocument) -> None:
        try:
            numbering = doc.part.numbering_part.element
        except Exception as exc:  # pragma: no cover - defensive
            raise WordRenderError(
                f"the package template exposes no usable numbering part: {exc!r}"
            ) from exc
        self._numbering = numbering
        self._abstract_ids: dict[tuple[tuple[int, str, str], ...], int] = {}
        self._num_ids: dict[tuple[str, str], int] = {}
        self._specs: dict[tuple[str, str], dict[int, tuple[str, str]]] = {}
        self._prepare(document)

    def _prepare(self, document: WordDocument) -> None:
        """Work out the numbering every list block needs.

        The blocks are grouped by their continuity group first, because the
        levels of one group share an abstract numbering definition: a list
        whose first level is arabic and whose second level is lower-case
        letters is one definition with two levels, not two definitions.
        """
        groups: dict[tuple[str, str], list[WordBlock]] = {}
        for block in document.blocks:
            if block.kind is not WordBlockKind.LIST_ITEM or block.list_marker is None:
                continue
            owner = self._owner_key(block)
            groups.setdefault(owner, []).append(block)

        for owner, blocks in groups.items():
            spec: dict[int, tuple[str, str]] = {}
            for block in blocks:
                level = max(0, min(block.level, _LIST_LEVEL_COUNT - 1))
                spec[level] = self._level_spec(block, level)
            self._specs[owner] = spec

    def _owner_key(self, block: WordBlock) -> tuple[str, str]:
        """Return the key that decides which ``w:num`` a block belongs to.

        A numbered list is identified by its continuity group: two lists of
        the same group continue one sequence.  A bullet list carries no
        sequence, so bullets share one instance as long as they agree on the
        way their marker is rendered.
        """
        marker = block.list_marker
        if marker is ListMarker.NUMBER:
            return ("number", block.list_group or "")
        signature = self._level_signature(block)
        return ("bullet", signature)

    def _level_signature(self, block: WordBlock) -> str:
        """Return a stable signature of the marker a bullet block renders."""
        level = max(0, min(block.level, _LIST_LEVEL_COUNT - 1))
        num_format, level_text = self._level_spec(block, level)
        return f"{num_format}|{level_text}"

    def _level_spec(self, block: WordBlock, level: int) -> tuple[str, str]:
        """Return the ``(numFmt, lvlText)`` a list block is rendered with."""
        marker = block.list_marker
        default_format = _NUM_FORMAT[marker] if marker is not None else "decimal"
        num_format = (block.number_format or "").strip() or default_format
        level_text = (block.number_text or "").strip()
        if not level_text:
            if num_format == "bullet":
                level_text = "\u2022"
            else:
                level_text = "".join(f"%{index + 1}." for index in range(level + 1))
        return (num_format, level_text)

    def num_id_for(self, block: WordBlock) -> int:
        """Return the ``w:numId`` that renders ``block`` as a list paragraph."""
        marker = block.list_marker
        if marker is None:
            raise WordRenderError(
                f"list item {block.block_id!r} carries no list marker"
            )
        owner = self._owner_key(block)
        if owner not in self._num_ids:
            self._create_num(owner)
        return self._num_ids[owner]

    def _create_num(self, owner: tuple[str, str]) -> None:
        spec = self._specs.get(owner, {})
        abstract_id = self._abstract_id_for(spec)
        try:
            num = self._numbering.add_num(abstract_id)
        except Exception as exc:
            raise WordRenderError(
                f"could not create a numbering instance for {owner!r}: {exc!r}"
            ) from exc
        num_id_text = num.get(qn("w:numId"))
        if num_id_text is None:
            raise WordRenderError("the created numbering instance carries no w:numId")
        # An explicit start override makes every continuity group begin at 1,
        # at every nesting depth.
        for level in range(_LIST_LEVEL_COUNT):
            override = OxmlElement("w:lvlOverride")
            override.set(qn("w:ilvl"), str(level))
            start = OxmlElement("w:startOverride")
            start.set(qn("w:val"), "1")
            override.append(start)
            num.append(override)
        self._num_ids[owner] = int(num_id_text)

    def _abstract_id_for(self, spec: dict[int, tuple[str, str]]) -> int:
        signature = self._signature(spec)
        if signature in self._abstract_ids:
            return self._abstract_ids[signature]
        existing = [
            int(value)
            for value in self._numbering.xpath("./w:abstractNum/@w:abstractNumId")
        ]
        abstract_id = max(existing) + 1 if existing else 0
        element = self._build_abstract_num(abstract_id, spec)
        self._insert_abstract_num(element)
        self._abstract_ids[signature] = abstract_id
        return abstract_id

    @staticmethod
    def _signature(spec: dict[int, tuple[str, str]]) -> tuple[tuple[int, str, str], ...]:
        """Return the identity of a numbering specification."""
        return tuple(sorted((level, fmt, text) for level, (fmt, text) in spec.items()))

    def _insert_abstract_num(self, element: Any) -> None:
        # Schema order inside w:numbering is numPicBullet*, abstractNum*,
        # num*, so the new definition must precede every w:num.
        numbers = self._numbering.findall(qn("w:num"))
        if numbers:
            numbers[0].addprevious(element)
        else:
            self._numbering.append(element)

    def _build_abstract_num(
        self, abstract_id: int, spec: dict[int, tuple[str, str]]
    ) -> Any:
        """Build one abstract numbering definition from a level specification."""
        default = self._default_level(spec)
        levels: list[str] = []
        for level in range(_LIST_LEVEL_COUNT):
            num_format, level_text = spec.get(level, self._derived_level(default, level))
            left = _LIST_INDENT_STEP * (level + 1)
            levels.append(
                f'<w:lvl w:ilvl="{level}">'
                f'<w:start w:val="1"/>'
                f'<w:numFmt w:val="{escape(num_format)}"/>'
                f'<w:lvlText w:val="{escape(level_text)}"/>'
                f'<w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{left}" w:hanging="{_LIST_HANGING}"/></w:pPr>'
                f"</w:lvl>"
            )
        nsid = f"{0x5A000000 + abstract_id:08X}"
        template = f"{0x5B000000 + abstract_id:08X}"
        xml = (
            f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
            f'<w:nsid w:val="{nsid}"/>'
            f'<w:multiLevelType w:val="multilevel"/>'
            f'<w:tmpl w:val="{template}"/>'
            f'{"".join(levels)}'
            f"</w:abstractNum>"
        )
        return parse_xml(xml)

    @staticmethod
    def _default_level(spec: dict[int, tuple[str, str]]) -> tuple[str, str]:
        """Return the specification a level without one of its own falls back to."""
        if not spec:
            return ("decimal", "%1.")
        return spec[min(spec)]

    @staticmethod
    def _derived_level(default: tuple[str, str], level: int) -> tuple[str, str]:
        """Return the format and text of a level the source did not define."""
        num_format, level_text = default
        if num_format == "bullet":
            return (num_format, level_text)
        if "%" in level_text:
            return (num_format, level_text)
        return (num_format, f"%{level + 1}.")


class _RenderState:
    """Mutable state shared by the helpers of a single render pass."""

    def __init__(
        self,
        numbering: _NumberingPlan,
        footnote_ids: dict[str, int],
        endnote_ids: dict[str, int],
        comment_ids: dict[str, int],
        media_rels: dict[str, str],
    ) -> None:
        self.numbering = numbering
        self.footnote_ids = footnote_ids
        self.endnote_ids = endnote_ids
        self.comment_ids = comment_ids
        self.media_rels = media_rels
        #: The relationship id each carried part was written under, keyed by
        #: its part name.
        self.part_rels: dict[str, str] = {}
        #: The relationship id of each carried part, keyed by the kind of
        #: relationship that reaches it.
        self.part_rels_by_kind: dict[str, str] = {}
        #: The package part each carried part was written as, keyed by the
        #: normalised part name.  A carried part that no relationship reaches
        #: is in here too: it is written as a part, and it is the relationship
        #: graph that decides whether it travels.
        self.part_blobs: dict[str, Part] = {}
        #: The carried part each normalised part name stands for.
        self.part_models: dict[str, WordPart] = {}
        #: The normalised names of the carried parts the document reached, and
        #: that are therefore related from the document part.
        self.document_reached: set[str] = set()
        #: The normalised names of the carried parts the package root
        #: reached, and that are therefore related from the package root.
        self.package_reached: set[str] = set()
        #: The parts each carried part reaches by a relationship of its own,
        #: keyed by the normalised name of the part that holds them.
        self.part_edges: dict[str, list[str]] = {}
        #: The comment ranges that have been opened but not yet closed.
        self.open_comments: dict[str, int] = {}
        #: The paragraph each block was rendered into, keyed by block id.
        self.block_paragraphs: dict[str, Any] = {}
        #: The revision ids already used, so that every one is unique.
        self.used_revision_ids: set[str] = set()


class WordWriter:
    """Render a :class:`WordDocument` into the bytes of a ``.docx`` package.

    The writer is stateless between calls: the same document always produces
    the same package.  With ``deterministic=True`` (the default) the output is
    byte-identical across runs, which is what makes the round-trip property
    testable.

    Anything the writer cannot represent is recorded in
    :attr:`last_omissions`, the same register the reader uses.  A construct
    the model cannot carry is never dropped in silence: it is either rendered
    or reported.
    """

    def __init__(self, *, deterministic: bool = True) -> None:
        """Create a writer.

        Args:
            deterministic: When true, every timestamp in the package is pinned
                to a fixed epoch so that the same document always yields the
                same bytes.  When false, the current time is used.
        """
        self._deterministic = deterministic
        self.last_omissions: list[str] = []
        self._omission_counts: dict[str, int] = {}
        self._document_styles: tuple[WordStyle, ...] = ()
        self._media_by_id: dict[str, WordMedia] = {}
        self._parts_by_name: dict[str, WordPart] = {}

    def write(self, document: WordDocument) -> bytes:
        """Render ``document`` and return the bytes of a ``.docx`` package.

        Raises:
            WordRenderError: If the document cannot be rendered into a valid
                package.  The writer never emits a malformed package to make
                a document fit.
        """
        self.last_omissions = []
        self._omission_counts = {}
        try:
            return self._render(document)
        except WordRenderError:
            raise
        except Exception as exc:
            raise WordRenderError(
                f"unexpected failure while rendering the document: {exc!r}"
            ) from exc

    def _omit(self, description: str) -> None:
        """Record that something could not be represented, loudly."""
        self._omission_counts[description] = self._omission_counts.get(description, 0) + 1
        self.last_omissions = [
            entry if count == 1 else f"{entry} x{count}"
            for entry, count in sorted(self._omission_counts.items())
        ]

    # -- rendering ---------------------------------------------------------

    def _render(self, document: WordDocument) -> bytes:
        document = self._revalidate(document)
        self._validate(document)
        self._document_styles = document.styles
        self._media_by_id = {media.media_id: media for media in document.media}
        self._parts_by_name = {part.part_name: part for part in document.parts}
        doc = Document()
        state = _RenderState(
            numbering=_NumberingPlan(doc, document),
            footnote_ids=self._map_footnote_ids(document),
            endnote_ids=self._map_endnote_ids(document),
            comment_ids=self._map_comment_ids(document),
            media_rels={},
        )
        # The carried parts are written first: a part keeps the id of the
        # relationship it was reached by, and the package builder has ids of
        # its own to hand out.
        self._render_parts(doc, document, state)
        self._render_media(doc, document, state)
        # The relationships a carried part holds to other parts are put back
        # once every part exists, so a part that the document did not reach
        # is reached from the part that owns it.
        self._relate_carried_parts(doc, state)
        self._report_unreachable_parts(state)
        previous_was_table = False
        for block in document.blocks:
            if block.kind is WordBlockKind.TABLE and previous_was_table:
                # OOXML has no table boundary: Word reads two adjacent
                # `w:tbl` elements as ONE table, which silently destroys the
                # second table's grid (and any merge in it).  An empty
                # paragraph between them is what Word itself writes when it
                # splits a table, and it is the only separator Word honours.
                doc.add_paragraph()
            self._render_block(doc, block, state)
            previous_was_table = block.kind is WordBlockKind.TABLE
        self._render_footnotes(doc, document, state)
        self._render_endnotes(doc, document, state)
        self._render_comments(doc, document, state)
        self._close_open_comment_ranges(doc, state)
        self._render_sections(doc, document)
        self._render_headers_and_footers(doc, document)
        self._apply_core_properties(doc, document)

        buffer = io.BytesIO()
        doc.save(buffer)
        raw = buffer.getvalue()
        if not self._deterministic:
            return raw
        return _rebuild_zip_deterministically(raw)

    def _revalidate(self, document: WordDocument) -> WordDocument:
        """Re-validate ``document`` against the model's own validators.

        The contract (W10) makes the writer responsible for the model it was
        handed: a document that bypassed construction-time validation (for
        example through ``model_construct``) must be refused rather than
        rendered with its illegal parts silently dropped.  Re-validating the
        dumped model through :class:`WordDocument` re-runs every field and
        model validator of §3.1 and §3.2, so the check cannot drift away
        from the model it guards.

        The check runs before the package builder is touched, so a rejected
        document never yields bytes.  The re-validated document is returned
        and rendered: it is the model that actually passed the check, so a
        value the validators coerce (an enum spelled as a string, say) is
        rendered as the value the model defines.
        """
        try:
            # ``warnings=False`` silences the serializer's complaint about a
            # hand-built model holding plain dictionaries; the model is
            # rejected right afterwards, so the diagnostic adds nothing.
            payload = document.model_dump(warnings=False)
        except Exception as exc:
            raise WordRenderError(
                f"the document model could not be read for validation: {exc!r}"
            ) from exc
        try:
            return WordDocument.model_validate(payload)
        except ValidationError as exc:
            raise WordRenderError(
                "the document model violates the contract invariants: "
                f"{_format_validation_errors(exc, document)}"
            ) from exc
        except Exception as exc:
            raise WordRenderError(
                f"the document model could not be validated: {exc!r}"
            ) from exc

    def _validate(self, document: WordDocument) -> None:
        """Reject documents that cannot be rendered into a valid package."""
        seen_footnotes: set[str] = set()
        for footnote in document.footnotes:
            if footnote.footnote_id in seen_footnotes:
                raise WordRenderError(
                    f"duplicate footnote id {footnote.footnote_id!r}"
                )
            seen_footnotes.add(footnote.footnote_id)

        known = {footnote.footnote_id for footnote in document.footnotes}
        for block in document.blocks:
            for run in block.runs:
                if run.footnote_ref is None:
                    continue
                if run.footnote_ref not in known:
                    raise WordRenderError(
                        f"block {block.block_id!r} references unknown footnote "
                        f"{run.footnote_ref!r}"
                    )
            self._validate_table_cells(block)
            self._validate_table_grid(block)

        if len(document.headers) > _MAX_HEADERS or len(document.footers) > _MAX_HEADERS:
            # The reference path writes one header per *type*, so a document
            # with several sections is not the problem this limit guards
            # against: it is a document that names more headers than Word has
            # roles for. The positional path has no such distinction.
            if not self._is_reference_driven(document):
                raise WordRenderError(
                    f"a single-section document can carry at most {_MAX_HEADERS} "
                    f"headers, got {len(document.headers)}"
                )

    @staticmethod
    def _validate_table_cells(block: WordBlock) -> None:
        """Refuse a table cell that breaks the merge rules (D4, I9).

        A continuation cell carries the content of the cell that *starts* the
        merge; text stored in one is content no renderer can display, and
        Word ignores it.  The model refuses such a cell at construction time
        (W10's re-validation catches a hand-built one), and this check makes
        the writer's own refusal explicit rather than a silent drop.
        """
        table = block.table
        if table is None:
            return
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                if not cell.is_merge_continuation:
                    continue
                if cell.row_span != 1:
                    raise WordRenderError(
                        f"table cell {row_index}:{cell_index} continues a merge "
                        f"and spans {cell.row_span} rows; a continuation spans "
                        "a single row"
                    )
                if cell.text:
                    raise WordRenderError(
                        f"table cell {row_index}:{cell_index} continues a merge "
                        "and must not carry text of its own"
                    )

    @staticmethod
    def _validate_table_grid(block: WordBlock) -> None:
        """Refuse a table whose rows do not all fill the grid (W12).

        A Word table's grid is as wide as its widest row, and **every** row
        must fill that grid.  A row that falls short is a malformed table, and
        Word does not refuse it: it silently re-lays the table out, so the
        document the reader opens is not the document the model describes.
        The writer refuses it instead of emitting something Word will change.

        The check uses the same layout the renderer emits, so the two can
        never disagree about what the table looks like.
        """
        table = block.table
        if table is None:
            return
        layout = _layout_table(table)
        grid_width = max(
            (
                placement.column + placement.col_span
                for row in layout
                for placement in row
            ),
            default=0,
        )
        for row_index, placements in enumerate(layout):
            covered = max(
                (placement.column + placement.col_span for placement in placements),
                default=0,
            )
            if covered != grid_width:
                raise WordRenderError(
                    f"table block {block.block_id!r} row {row_index} covers "
                    f"{covered} of {grid_width} grid columns; every row of a "
                    "Word table must fill the grid"
                )

    @staticmethod
    def _map_footnote_ids(document: WordDocument) -> dict[str, int]:
        """Map model footnote ids onto the positive ids Word expects.

        Ids 0 and 1 belong to the separator footnotes, so model footnotes
        start at 2.
        """
        mapping: dict[str, int] = {}
        next_id = _FIRST_FOOTNOTE_ID
        for footnote in document.footnotes:
            mapping[footnote.footnote_id] = next_id
            next_id += 1
        return mapping

    @staticmethod
    def _map_endnote_ids(document: WordDocument) -> dict[str, int]:
        """Map model endnote ids onto the positive ids Word expects."""
        mapping: dict[str, int] = {}
        next_id = _FIRST_ENDNOTE_ID
        for endnote in document.endnotes:
            mapping[endnote.endnote_id] = next_id
            next_id += 1
        return mapping

    @staticmethod
    def _map_comment_ids(document: WordDocument) -> dict[str, int]:
        """Map model comment ids onto the numeric ids Word expects."""
        mapping: dict[str, int] = {}
        next_id = _FIRST_COMMENT_ID
        for comment in document.comments:
            mapping[comment.comment_id] = next_id
            next_id += 1
        return mapping

    def _render_block(
        self,
        doc: Any,
        block: WordBlock,
        state: _RenderState,
    ) -> None:
        kind = block.kind
        if kind is WordBlockKind.TABLE:
            table = block.table
            if table is None:  # pragma: no cover - guarded by the model
                raise WordRenderError(
                    f"table block {block.block_id!r} carries no table payload"
                )
            self._render_table(doc, table, state)
            return
        if kind is WordBlockKind.PAGE_BREAK:
            paragraph = doc.add_paragraph()
            paragraph.add_run().add_break(WD_BREAK.PAGE)
            state.block_paragraphs[block.block_id] = paragraph
            return

        anchor = self._block_anchor(block, state)
        if anchor is not None:
            self._render_block_anchor(doc, anchor, state)
            return

        paragraph = doc.add_paragraph()
        state.block_paragraphs[block.block_id] = paragraph
        style = self._style_for_block(doc, block)
        if style is not None:
            paragraph.style = style
        if kind is WordBlockKind.LIST_ITEM:
            self._apply_numbering(paragraph, block, state)
        self._render_runs(doc, paragraph, block, state)

    def _block_anchor(self, block: WordBlock, state: _RenderState) -> WordPart | None:
        """Return the part a block anchors, when the block is a body reference.

        Word expects an alternative-format chunk and a subdocument reference
        beside the paragraphs rather than inside a run, so the block that
        holds one of them is written as the reference itself.
        """
        runs = block.runs
        if len(runs) != 1:
            return None
        run = runs[0]
        if run.part_ref is None or run.text:
            return None
        part = self._parts_by_name.get(run.part_ref)
        if part is None:
            return None
        kind = _PART_ANCHOR_KINDS.get(_relationship_kind(part.relationship_type))
        if kind not in _BLOCK_LEVEL_ANCHORS:
            return None
        return part

    def _render_block_anchor(
        self,
        doc: Any,
        part: WordPart,
        state: _RenderState,
    ) -> None:
        """Write a body-level reference into the document body."""
        rel_id = state.part_rels.get(part.part_name)
        if rel_id is None:
            self._omit(
                f"package part ({part.part_name}) was not written into the package"
            )
            return
        kind = _PART_ANCHOR_KINDS.get(_relationship_kind(part.relationship_type))
        if kind == "altChunk":
            element = parse_xml(f'<w:altChunk {nsdecls("w", "r")} r:id="{rel_id}"/>')
        elif kind == "subDoc":
            element = parse_xml(f'<w:subDoc {nsdecls("w", "r")} r:id="{rel_id}"/>')
        else:
            self._omit(
                f"package part ({part.part_name}) is not a body-level reference"
            )
            return
        doc.element.body.insert_element_before(element, "w:sectPr")

    def _style_for_block(self, doc: Any, block: WordBlock) -> Any | None:
        """Resolve the paragraph style for ``block``, or ``None`` for Normal.

        The kind mandates the style for headings, titles, quotes and code; a
        missing built-in style falls back rather than crashing the render.

        A style the source document defined but the template does not carry is
        *defined* in the output package rather than dropped: a style that
        silently became ``Normal`` is a style the reader cannot find again.
        """
        kind = block.kind
        source = self._resolve_source_style(doc, block.style)
        if source is not None:
            return source
        if kind is WordBlockKind.TITLE:
            return _first_style(doc, ("Title",))
        if kind is WordBlockKind.HEADING:
            return _first_style(doc, (f"Heading {block.level}",))
        if kind is WordBlockKind.QUOTE:
            return _first_style(doc, ("Intense Quote", "Quote"))
        if kind is WordBlockKind.CODE:
            return _first_style(doc, ("No Spacing", "Normal"))
        if kind is WordBlockKind.LIST_ITEM:
            # The source style wins when it survives the round trip; otherwise
            # fall back to Word's own list-paragraph style.
            return _first_style(doc, ("List Paragraph",))
        return None

    def _resolve_source_style(self, doc: Any, name: str | None) -> Any | None:
        """Return the style a block's source style names, defining it if needed.

        A style that is already in the template is used as it is.  A style the
        template does not carry is added to the output package, with the
        definition the document carries, so that it survives the round trip.
        """
        if not name:
            return None
        existing = _first_style(doc, (name,))
        if existing is not None:
            return existing
        definition = self._style_definition(name)
        if definition is None:
            self._omit(f"style ({name}) is not defined in the source document")
            return None
        return self._define_style(doc, definition)

    def _style_definition(self, name: str) -> WordStyle | None:
        """Return the definition of the style named ``name``, if the document has one."""
        lowered = name.strip().lower()
        for style in self._document_styles:
            if style.name.strip().lower() == lowered:
                return style
            if style.style_id.strip().lower() == lowered:
                return style
        return None

    def _define_style(self, doc: Any, definition: WordStyle) -> Any | None:
        """Add a style definition to the output package and return it."""
        style_name = definition.name or definition.style_id
        try:
            style = doc.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH, builtin=False)
        except Exception as exc:
            self._omit(f"style ({style_name}) could not be defined in the output: {exc!r}")
            return None
        try:
            style.style_id = definition.style_id
        except Exception:
            # A style id that clashes with one the template already uses is
            # left as ``python-docx`` generated it: the name is what the
            # reader resolves the style by.
            self._omit(f"style ({style_name}) keeps a generated id")
        if definition.based_on:
            base = _first_style(doc, (definition.based_on,))
            if base is not None:
                try:
                    style.base_style = base
                except Exception:
                    self._omit(f"style ({style_name}) lost its base style")
        self._omit(f"style ({style_name}) was written without its source formatting")
        return style

    @staticmethod
    def _apply_numbering(
        paragraph: Any,
        block: WordBlock,
        state: _RenderState,
    ) -> None:
        num_id = state.numbering.num_id_for(block)
        level = max(0, min(block.level, _LIST_LEVEL_COUNT - 1))
        p_pr = paragraph._p.get_or_add_pPr()
        num_pr = p_pr.get_or_add_numPr()
        ilvl = OxmlElement("w:ilvl")
        ilvl.set(qn("w:val"), str(level))
        num_pr.append(ilvl)
        num_id_element = OxmlElement("w:numId")
        num_id_element.set(qn("w:val"), str(num_id))
        num_pr.append(num_id_element)
        # Direct indentation wins over the paragraph style, so the nesting
        # depth stays visible whatever style the list paragraph carries.
        # The values are twips, which is what ``w:ind`` expects.
        indent = p_pr.get_or_add_ind()
        indent.left = Twips(_LIST_INDENT_STEP * (level + 1))
        indent.hanging = Twips(_LIST_HANGING)

    def _render_runs(
        self,
        doc: Any,
        paragraph: Any,
        block: WordBlock,
        state: _RenderState,
    ) -> None:
        if not block.runs:
            # A block without runs still carries text; emit it as one run.
            paragraph.add_run().text = block.text
            return
        runs = block.runs
        index = 0
        while index < len(runs):
            run = runs[index]
            revision = run.revision
            if revision is None or revision.kind is WordRevisionKind.FORMAT_CHANGE:
                # A formatting revision is a revision of the run's
                # *properties*: the run itself is not inserted or deleted, so
                # it is written as an ordinary run that carries the revision
                # in its run properties.
                self._open_comment_ranges(paragraph, run, state)
                self._render_run(doc, paragraph, run, state)
                index += 1
                continue
            group = [run]
            index += 1
            while index < len(runs) and _same_revision(runs[index].revision, revision):
                group.append(runs[index])
                index += 1
            self._render_revision_group(doc, paragraph, revision, group, state)

    def _open_comment_ranges(
        self, paragraph: Any, run: WordRun, state: _RenderState
    ) -> None:
        """Open the comment ranges a run belongs to."""
        for comment_id in run.comment_refs:
            if comment_id in state.open_comments:
                continue
            numeric_id = state.comment_ids.get(comment_id)
            if numeric_id is None:
                self._omit(f"comment range references unknown comment {comment_id!r}")
                continue
            marker = OxmlElement("w:commentRangeStart")
            marker.set(qn("w:id"), str(numeric_id))
            paragraph._p.append(marker)
            state.open_comments[comment_id] = numeric_id

    def _render_revision_group(
        self,
        doc: Any,
        paragraph: Any,
        revision: WordRevision,
        group: list[WordRun],
        state: _RenderState,
    ) -> None:
        """Render a group of runs that belong to one tracked revision."""
        deleted = revision.kind in _DELETION_KINDS
        container = OxmlElement("w:del" if deleted else "w:ins")
        container.set(qn("w:id"), self._revision_id(revision, state))
        container.set(qn("w:author"), revision.author)
        if revision.date is not None:
            container.set(qn("w:date"), revision.date.isoformat())
        paragraph._p.append(container)
        for run in group:
            self._open_comment_ranges(paragraph, run, state)
            before = len(paragraph._p)
            self._render_run(doc, paragraph, run, state, deleted=deleted)
            for child in list(paragraph._p)[before:]:
                container.append(child)

    def _revision_id(self, revision: WordRevision, state: _RenderState) -> str:
        """Return a unique numeric id for a revision element."""
        candidate = revision.revision_id.strip()
        if not candidate.isdigit() or candidate in state.used_revision_ids:
            candidate = str(len(state.used_revision_ids) + 1)
            while candidate in state.used_revision_ids:
                candidate = str(int(candidate) + 1)
        state.used_revision_ids.add(candidate)
        return candidate

    def _render_run(
        self,
        doc: Any,
        paragraph: Any,
        run: WordRun,
        state: _RenderState,
        *,
        deleted: bool = False,
    ) -> None:
        if run.hyperlink is not None:
            self._render_hyperlink_run(doc, paragraph, run, state, deleted=deleted)
            return
        if run.equation_xml is not None and self._append_equation(paragraph._p, run):
            return
        run_object = paragraph.add_run()
        _apply_run_formatting(run_object, run)
        run_object.text = run.text
        self._append_references(run_object, run, state)
        self._append_image(run_object, run, state)
        self._append_part_anchor(run_object._r, run, state)
        self._append_format_change(run_object, run)
        if deleted:
            _mark_run_deleted(run_object)

    def _render_hyperlink_run(
        self,
        doc: Any,
        paragraph: Any,
        run: WordRun,
        state: _RenderState,
        *,
        deleted: bool = False,
    ) -> None:
        url = (run.hyperlink or "").strip()
        if not url:
            raise WordRenderError(
                "a hyperlink run requires a non-empty target URL"
            )
        try:
            rel_id = doc.part.relate_to(url, RT.HYPERLINK, is_external=True)
        except Exception as exc:
            raise WordRenderError(
                f"could not create a hyperlink relationship for {url!r}: {exc!r}"
            ) from exc
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), rel_id)
        if run.equation_xml is not None and self._append_equation(hyperlink, run):
            paragraph._p.append(hyperlink)
            return
        run_object = paragraph.add_run()
        # Move the run inside the hyperlink element, keeping document order.
        run_object._r.addprevious(hyperlink)
        hyperlink.append(run_object._r)
        _apply_run_formatting(run_object, run)
        run_object.text = run.text
        self._append_references(run_object, run, state)
        self._append_image(run_object, run, state)
        self._append_part_anchor(run_object._r, run, state)
        self._append_format_change(run_object, run)
        if deleted:
            _mark_run_deleted(run_object)

    def _equation_element(self, run: WordRun) -> BaseOxmlElement | None:
        """Return the run's OMML as an element, or ``None`` when it is broken.

        The markup is bound to the math namespace before it is parsed, because
        ``parse_xml`` needs every prefix a fragment uses to be declared on it.
        A markup that cannot be parsed is reported and ``None`` is returned:
        the caller falls back to the run's text, because a single broken
        equation must not cost the whole write.
        """
        try:
            return parse_xml(_bind_math_namespace(run.equation_xml or ""))
        except (etree.XMLSyntaxError, ValueError) as exc:
            self._omit(f"equation markup could not be parsed: {exc!r}")
            return None

    def _append_equation(self, container: Any, run: WordRun) -> bool:
        """Insert the run's raw OMML into ``container``.

        Word renders the equation itself, so the markup takes the place of the
        run's text element rather than sitting beside it.  ``False`` is
        returned when the markup cannot be parsed, so the caller can fall
        back to writing the run's flattened text.
        """
        element = self._equation_element(run)
        if element is None:
            return False
        container.append(element)
        return True

    def _append_note_run(
        self,
        paragraph: Any,
        run: WordRun,
        part: Any,
        state: _RenderState,
    ) -> None:
        """Append a run to a hand-built paragraph (a note or comment body).

        An equation is not a run but a paragraph-level element, so it is
        appended to the paragraph itself; every other run is appended element
        by element.
        """
        if run.equation_xml is not None and self._append_equation(paragraph, run):
            return
        _append_raw_run(
            paragraph,
            run,
            part,
            state,
            anchor=self._run_anchor_xml(run, state),
            change=self._format_change_element(run),
        )

    def _append_references(
        self,
        run_object: Any,
        run: WordRun,
        state: _RenderState,
    ) -> None:
        """Append the footnote, endnote and comment references of a run."""
        if run.footnote_ref is not None:
            numeric_id = state.footnote_ids.get(run.footnote_ref)
            if numeric_id is None:
                raise WordRenderError(
                    f"run references unknown footnote {run.footnote_ref!r}"
                )
            reference = OxmlElement("w:footnoteReference")
            reference.set(qn("w:id"), str(numeric_id))
            run_object._r.append(reference)
        if run.endnote_ref is not None:
            numeric_id = state.endnote_ids.get(run.endnote_ref)
            if numeric_id is None:
                raise WordRenderError(
                    f"run references unknown endnote {run.endnote_ref!r}"
                )
            reference = OxmlElement("w:endnoteReference")
            reference.set(qn("w:id"), str(numeric_id))
            run_object._r.append(reference)

    def _append_image(
        self,
        run_object: Any,
        run: WordRun,
        state: _RenderState,
    ) -> None:
        """Append the picture a run embeds, when the document carries one."""
        if run.image_ref is None:
            return
        rel_id = state.media_rels.get(run.image_ref)
        if rel_id is None:
            self._omit(f"image ({run.image_ref}) is not embedded in the output package")
            return
        media = self._media_by_id.get(run.image_ref)
        name = media.part_name.rsplit("/", 1)[-1] if media is not None else "image"
        extent = _drawing_extent(run.anchor_extent, _image_extent(media))
        run_object._r.append(parse_xml(_drawing_xml(rel_id, name, extent)))

    def _append_part_anchor(
        self,
        container: Any,
        run: WordRun,
        state: _RenderState,
    ) -> None:
        """Append the body reference a run anchors, with its relationship.

        The reference points at the relationship the carried part was written
        under, so a body reference that pointed at ``rId7`` before the round
        trip points at the same part afterwards.
        """
        anchor = self._run_anchor_xml(run, state)
        if not anchor:
            return
        container.append(parse_xml(anchor))

    def _run_anchor_xml(self, run: WordRun, state: _RenderState) -> str:
        """Return the body reference a run anchors, as XML."""
        if run.part_ref is None:
            return ""
        part = self._parts_by_name.get(run.part_ref)
        if part is None:
            self._omit(
                f"package part ({run.part_ref}) is not carried in the output package"
            )
            return ""
        rel_id = state.part_rels.get(part.part_name)
        if rel_id is None:
            self._omit(
                f"package part ({part.part_name}) was not written into the package"
            )
            return ""
        anchor = self._anchor_xml(part, rel_id, state, run.anchor_extent)
        return anchor or ""

    def _anchor_xml(
        self,
        part: WordPart,
        rel_id: str,
        state: _RenderState,
        extent: tuple[int, int] | None,
    ) -> str | None:
        """Build the body reference that anchors a carried part.

        The extent is the size the anchored element is drawn at, in EMUs: it
        is what the drawing markup of a chart, a diagram or a picture is
        scaled by, so it has to travel with the reference.
        """
        kind = _PART_ANCHOR_KINDS.get(_relationship_kind(part.relationship_type))
        if kind is None:
            self._omit(
                f"package part ({part.part_name}) is not anchored by a body reference"
            )
            return None
        if kind == "object":
            return _object_xml(rel_id, extent)
        if kind == "chart":
            return _chart_xml(rel_id, extent)
        if kind == "diagram":
            return _diagram_xml(self._diagram_rel_ids(state), extent)
        if kind == "altChunk":
            return f'<w:altChunk {nsdecls("w", "r")} r:id="{rel_id}"/>'
        return f'<w:subDoc {nsdecls("w", "r")} r:id="{rel_id}"/>'

    @staticmethod
    def _diagram_rel_ids(state: _RenderState) -> dict[str, str]:
        """Return the relationship ids of the parts a diagram is drawn from."""
        rel_ids: dict[str, str] = {}
        for attribute, kind in _DIAGRAM_RELATIONSHIPS.items():
            rel_id = state.part_rels_by_kind.get(kind)
            if rel_id:
                rel_ids[attribute] = rel_id
        return rel_ids

    def _append_format_change(self, run_object: Any, run: WordRun) -> None:
        """Append the tracked formatting revision of a run, verbatim.

        The revision is written back as Word wrote it, so the run properties
        from before the change survive the round trip: they are what the
        revision exists to record.
        """
        element = self._format_change_element(run)
        if element is None:
            return
        properties = run_object._r.get_or_add_rPr()
        properties.append(element)

    def _format_change_element(self, run: WordRun) -> Any | None:
        """Return the tracked formatting revision of a run as an element."""
        if run.format_change_xml is None:
            return None
        try:
            return parse_xml(run.format_change_xml)
        except (etree.XMLSyntaxError, ValueError) as exc:
            self._omit(f"formatting change could not be parsed: {exc!r}")
            return None

    # -- carried parts -----------------------------------------------------

    def _render_parts(
        self,
        doc: Any,
        document: WordDocument,
        state: _RenderState,
    ) -> None:
        """Put the document's carried parts back into the package.

        A part the reader carried through is written under its own name, with
        its bytes and its content type, so that Word accepts the package
        instead of reporting it as corrupt.  A part the *document* reached is
        related from the document under the id it was reached by, so a body
        reference that points at ``rId7`` still resolves.  A part the document
        did not reach is related from the part that owns it instead -- see
        :meth:`_relate_carried_parts`.
        """
        taken = {str(part.partname) for part in doc.part.package.iter_parts()}
        for part in doc.part.package.iter_parts():
            # The package builder writes the relationships of a part beside
            # it, so a carried part must not claim that name either: two
            # members of the same name make Word report the package as
            # corrupt.
            if len(part.rels):
                taken.add(str(part.partname.rels_uri))
        for part in document.parts:
            self._render_part(doc, part, state, taken)

    def _render_part(
        self,
        doc: Any,
        part: WordPart,
        state: _RenderState,
        taken: set[str],
    ) -> None:
        """Write one carried part into the package.

        A part that cannot be written is reported and the write carries on: a
        single unwritable part must not cost the caller the whole document.

        Only a part the document reached is related from the document.  A
        part it did not reach gets no relationship from the document: it is
        reached from the relationship list of the part that owns it, or from
        the package root, and inventing a relationship for it here is what
        makes Word report the package as corrupt.
        """
        name = part.part_name.strip()
        if not _is_writable_part_name(name):
            self._omit(f"package part ({part.part_name}) has a name Word would refuse")
            return
        key = _normalise_part_name(name)
        if _is_relationship_part_name(key):
            self._omit(
                f"package part ({part.part_name}) is a relationship part and is not "
                "content"
            )
            return
        part_name = "/" + key
        if part_name in taken:
            self._omit(f"package part ({part.part_name}) is already in the output package")
            return
        content_type = part.content_type or "application/octet-stream"
        blob = Part(
            PackURI(part_name),
            content_type,
            part.data,
            doc.part.package,
        )
        taken.add(part_name)
        state.part_blobs[key] = blob
        state.part_models[key] = part
        if self._document_reaches(part):
            rel_id = self._relate_part(doc, part, blob)
            state.part_rels[part.part_name] = rel_id
            state.document_reached.add(key)
            kind = _relationship_kind(part.relationship_type)
            if kind:
                state.part_rels_by_kind[kind] = rel_id
            return
        package_relationship = part.package_relationship
        if package_relationship is not None:
            self._relate_package_part(doc, blob, package_relationship, state, key)

    @staticmethod
    def _document_reaches(part: WordPart) -> bool:
        """Report whether the document reached a part.

        A part the document reached is named by a relationship of the document
        part: it carries the id of that relationship and the type of it.  A
        part the document did not reach carries neither, and is reached from
        the relationship list of the part that owns it instead.
        """
        return bool(part.relationship_id.strip() and part.relationship_type.strip())

    def _relate_part(self, doc: Any, part: WordPart, blob: Part) -> str:
        """Attach a carried part to the document, keeping its relationship id.

        The id the part was reached by is the id a body reference points at,
        so it is kept where it can be.  Where the package builder has already
        taken it, that relationship is moved aside -- it is one of the
        builder's own, which nothing in the document refers to by id.
        """
        rels = doc.part.rels
        desired = part.relationship_id.strip()
        if desired in rels:
            self._displace_relationship(rels, desired)
        rels.add_relationship(part.relationship_type, blob, desired)
        return desired

    def _relate_package_part(
        self,
        doc: Any,
        blob: Part,
        relationship: WordPartRelationship,
        state: _RenderState,
        key: str,
    ) -> None:
        """Attach a part to the package root, keeping the relationship id.

        The package root is what reaches the parts the document does not: a
        custom document properties part, for instance.  The relationship
        belongs to the root's own list, so the document's relationship list
        stays exactly what the body needs.

        An external target is a URL rather than a part of the package, so it
        is added as an external relationship and reaches nothing: the bytes
        the model carried for it have no place in the package, and the part
        is named as lost rather than dropped in silence.
        """
        rels = doc.part.package.rels
        rel_id = relationship.relationship_id.strip()
        if rel_id in rels:
            self._displace_relationship(rels, rel_id)
        if relationship.is_external:
            rels.add_relationship(
                relationship.relationship_type,
                relationship.target,
                rel_id,
                is_external=True,
            )
            return
        rels.add_relationship(relationship.relationship_type, blob, rel_id)
        state.package_reached.add(key)

    def _relate_carried_parts(
        self,
        doc: Any,
        state: _RenderState,
    ) -> None:
        """Put the relationships a carried part holds to other parts back.

        A chart's colour style, its quick style and the workbook embedded
        behind it are reached from the chart's *own* relationship part, not
        from ``document.xml``.  Those relationships belong to the chart, so
        they are added to the chart: the package builder then writes the
        chart's ``.rels`` file, with the targets relative to the chart's own
        directory.
        """
        known = self._known_parts(doc, state)
        for key, part in state.part_models.items():
            owner = state.part_blobs.get(key)
            if owner is None:  # pragma: no cover - both maps are filled together
                continue
            for relationship in part.relationships:
                target = self._add_part_relationship(owner, relationship, known)
                if target is not None:
                    state.part_edges.setdefault(key, []).append(target)

    @staticmethod
    def _known_parts(doc: Any, state: _RenderState) -> dict[str, Part]:
        """Return every part the package can hold, keyed by part name.

        A relationship of a carried part may point at a part the package
        builder wrote -- a theme, an image -- as well as at another carried
        part, so both are looked up here.
        """
        known: dict[str, Part] = {}
        for part in doc.part.package.iter_parts():
            known[_normalise_part_name(str(part.partname))] = part
        known.update(state.part_blobs)
        return known

    def _add_part_relationship(
        self,
        owner: Part,
        relationship: WordPartRelationship,
        known: dict[str, Part],
    ) -> str | None:
        """Add one relationship of a carried part to the part that owns it.

        The id and the type are the ones the relationship was read with: they
        are what the owner's own markup points at.  The name of the part that
        is reached is returned, so that the part is known to travel; an
        external target is not a part of the package and reaches nothing.
        """
        rel_id = relationship.relationship_id.strip()
        if rel_id in owner.rels:
            self._omit(
                f"relationship ({rel_id}) of package part ({owner.partname}) is "
                "already used by another relationship"
            )
            return None
        if relationship.is_external:
            try:
                owner.rels.add_relationship(
                    relationship.relationship_type,
                    relationship.target,
                    rel_id,
                    is_external=True,
                )
            except Exception as exc:
                self._omit(
                    f"relationship ({rel_id}) of package part ({owner.partname}) "
                    f"could not be added: {exc!r}"
                )
            return None
        target_name = _normalise_part_name(relationship.target)
        target = known.get(target_name)
        if target is None:
            self._omit(
                f"relationship ({rel_id}) of package part ({owner.partname}) points "
                f"at a part that is not in the package ({relationship.target})"
            )
            return None
        try:
            owner.rels.add_relationship(relationship.relationship_type, target, rel_id)
        except Exception as exc:
            self._omit(
                f"relationship ({rel_id}) of package part ({owner.partname}) could "
                f"not be added: {exc!r}"
            )
            return None
        return target_name

    def _report_unreachable_parts(self, state: _RenderState) -> None:
        """Name every carried part that no relationship path reaches.

        A package cannot hold a part that nothing points at: the package
        builder writes the parts the relationship graph reaches, so a part
        nothing reaches is silently absent from the zip.  Inventing a
        relationship for it is exactly what Word rejects, so the loss is
        reported rather than hidden.
        """
        reachable = self._reachable_parts(state)
        for key in state.part_models:
            if key in reachable:
                continue
            self._omit(
                f"package part ({key}) is not reached by any relationship and "
                "cannot travel in the package"
            )

    @staticmethod
    def _reachable_parts(state: _RenderState) -> set[str]:
        """Return the carried parts the package's relationship graph reaches.

        The graph has two roots: the document, which reaches the parts the
        body draws, and the package root, which reaches the parts of the
        package that are not part of the document at all -- the custom
        document properties, for one.  A part is reachable when one of those
        roots reaches it, or when a part that is reachable reaches it.
        """
        reachable: set[str] = set()
        pending = list(state.document_reached) + list(state.package_reached)
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            pending.extend(state.part_edges.get(key, ()))
        return reachable

    def _displace_relationship(self, rels: Any, rel_id: str) -> None:
        """Move a relationship aside so that another part can take its id.

        The relationship that is moved aside is one of the package builder's
        own: nothing in the document refers to it by id, and the id it held
        is one the model expects to keep.
        """
        existing = rels[rel_id]
        new_id = self._free_rel_id(rels)
        if existing.is_external:
            rels.add_relationship(
                existing.reltype, existing.target_ref, new_id, is_external=True
            )
        else:
            rels.add_relationship(existing.reltype, existing.target_part, new_id)
        del rels[rel_id]

    @staticmethod
    def _free_rel_id(rels: Any) -> str:
        """Return a relationship id no relationship uses."""
        index = len(rels) + 1
        while True:
            candidate = f"rId{index}"
            if candidate not in rels:
                return candidate
            index += 1

    # -- media -------------------------------------------------------------

    def _render_media(
        self,
        doc: Any,
        document: WordDocument,
        state: _RenderState,
    ) -> None:
        """Put the document's media parts back into the package.

        A media part that no run refers to is not written: an unused part in
        the package is dead weight, and the reader reports it as an
        unreferenced media part anyway.
        """
        referenced = {
            run.image_ref
            for block in document.blocks
            for run in block.runs
            if run.image_ref is not None
        }
        for media in document.media:
            if media.media_id not in referenced:
                self._omit(f"image media part ({media.part_name}) is not referenced")
                continue
            if not media.data:
                self._omit(f"image media part ({media.part_name}) carries no data")
                continue
            content_type = media.content_type or "application/octet-stream"
            part = Part(
                PackURI("/" + media.part_name.lstrip("/")),
                content_type,
                media.data,
                doc.part.package,
            )
            try:
                rel_id = doc.part.relate_to(part, RT.IMAGE)
            except Exception as exc:
                raise WordRenderError(
                    f"could not attach the media part {media.part_name!r} to the "
                    f"document: {exc!r}"
                ) from exc
            state.media_rels[media.media_id] = rel_id

    # -- comments ----------------------------------------------------------

    def _render_comments(
        self,
        doc: Any,
        document: WordDocument,
        state: _RenderState,
    ) -> None:
        """Write the document's comments into the package."""
        if not document.comments:
            return
        root = OxmlElement(
            "w:comments",
            nsdecls={"w": nsmap["w"], "r": nsmap["r"]},
        )
        part = _NotesPart(
            PackURI(_COMMENTS_PARTNAME),
            _COMMENTS_CONTENT_TYPE,
            doc.part.package,
            root,
        )
        try:
            doc.part.relate_to(part, RT.COMMENTS)
        except Exception as exc:
            raise WordRenderError(
                f"could not attach the comments part to the document: {exc!r}"
            ) from exc
        for comment in document.comments:
            root.append(self._render_comment(comment, part, state))

    def _render_comment(
        self,
        comment: WordComment,
        part: _NotesPart,
        state: _RenderState,
    ) -> Any:
        """Build one comment element."""
        numeric_id = state.comment_ids[comment.comment_id]
        element = OxmlElement("w:comment")
        element.set(qn("w:id"), str(numeric_id))
        element.set(qn("w:author"), comment.author)
        if comment.initials:
            element.set(qn("w:initials"), comment.initials)
        if comment.date is not None:
            element.set(qn("w:date"), comment.date.isoformat())

        paragraph = OxmlElement("w:p")
        runs = comment.runs or (WordRun(text=comment.text),)
        for run in runs:
            self._append_note_run(paragraph, run, part, state)
        element.append(paragraph)
        return element

    def _close_open_comment_ranges(self, doc: Any, state: _RenderState) -> None:
        """Close the comment ranges that are still open at the end of the body."""
        if not state.open_comments:
            return
        paragraph = doc.add_paragraph()
        for comment_id, numeric_id in state.open_comments.items():
            marker = OxmlElement("w:commentRangeEnd")
            marker.set(qn("w:id"), str(numeric_id))
            paragraph._p.append(marker)
            reference_run = OxmlElement("w:r")
            reference = OxmlElement("w:commentReference")
            reference.set(qn("w:id"), str(numeric_id))
            reference_run.append(reference)
            paragraph._p.append(reference_run)
        state.open_comments.clear()

    # -- endnotes ----------------------------------------------------------

    def _render_endnotes(
        self,
        doc: Any,
        document: WordDocument,
        state: _RenderState,
    ) -> None:
        """Write the document's endnotes into the package."""
        if not document.endnotes:
            return
        root = OxmlElement(
            "w:endnotes",
            nsdecls={"w": nsmap["w"], "r": nsmap["r"]},
        )
        root.append(_separator_note("w:endnote", "separator", "0"))
        root.append(_separator_note("w:endnote", "continuationSeparator", "1"))
        part = _NotesPart(
            PackURI(_ENDNOTES_PARTNAME),
            _ENDNOTES_CONTENT_TYPE,
            doc.part.package,
            root,
        )
        try:
            doc.part.relate_to(part, RT.ENDNOTES)
        except Exception as exc:
            raise WordRenderError(
                f"could not attach the endnotes part to the document: {exc!r}"
            ) from exc
        for endnote in document.endnotes:
            root.append(self._render_endnote(endnote, part, state))

    def _render_endnote(
        self,
        endnote: WordEndnote,
        part: _NotesPart,
        state: _RenderState,
    ) -> Any:
        """Build one endnote element."""
        numeric_id = state.endnote_ids[endnote.endnote_id]
        element = OxmlElement("w:endnote")
        element.set(qn("w:id"), str(numeric_id))

        paragraph = OxmlElement("w:p")
        marker_run = OxmlElement("w:r")
        marker_properties = OxmlElement("w:rPr")
        vertical_align = OxmlElement("w:vertAlign")
        vertical_align.set(qn("w:val"), "superscript")
        marker_properties.append(vertical_align)
        marker_run.append(marker_properties)
        marker_run.append(OxmlElement("w:endnoteRef"))
        paragraph.append(marker_run)

        element.append(paragraph)

        runs = endnote.runs or (WordRun(text=endnote.text),)
        for run in runs:
            self._append_note_run(paragraph, run, part, state)
        return element

    # -- sections ----------------------------------------------------------

    def _render_sections(self, doc: Any, document: WordDocument) -> None:
        """Write the document's page setup into the package.

        The flat model describes the sections of a document in order, but the
        package builder writes one section: the properties of the first
        section are applied to the body, and every further section is
        reported, because a section that is not written is a section the
        operator has to know about.
        """
        if not document.sections:
            return
        section = doc.sections[0]
        first = document.sections[0]
        section.page_width = Twips(first.page_width)
        section.page_height = Twips(first.page_height)
        section.orientation = (
            WD_ORIENT.LANDSCAPE
            if first.orientation.strip().lower() == "landscape"
            else WD_ORIENT.PORTRAIT
        )
        section.top_margin = Twips(first.margin_top)
        section.right_margin = Twips(first.margin_right)
        section.bottom_margin = Twips(first.margin_bottom)
        section.left_margin = Twips(first.margin_left)
        for extra in document.sections[1:]:
            self._omit(
                "only the first section's page setup was written "
                f"({len(document.sections)} sections in the document)"
            )
            break

    # -- tables ------------------------------------------------------------

    def _render_table(self, doc: Any, table: WordTable, state: _RenderState) -> None:
        table_element = parse_xml(self._table_xml(doc, table, state))
        body = doc.element.body
        body._insert_tbl(table_element)

    def _table_xml(self, doc: Any, table: WordTable, state: _RenderState) -> str:
        """Build the XML of one table."""
        layout = _layout_table(table)
        grid_width = max(
            (
                placement.column + placement.col_span
                for row in layout
                for placement in row
            ),
            default=0,
        )
        column_widths = self._column_widths(table, grid_width)
        style_id = _table_style_id(doc)

        rows_xml: list[str] = []
        for row_index, (row, placements) in enumerate(
            zip(table.rows, layout)
        ):
            rows_xml.append(
                self._render_table_row(
                    doc,
                    placements,
                    column_widths,
                    state,
                    is_header=row_index < table.header_rows
                    or any(cell.is_header for cell in row.cells),
                )
            )

        grid_xml = "".join(
            f'<w:gridCol w:w="{width}"/>' for width in column_widths
        )
        if style_id is not None:
            borders_xml = ""
        else:
            borders_xml = (
                "<w:tblBorders>"
                '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                "</w:tblBorders>"
            )
        style_xml = (
            f'<w:tblStyle w:val="{style_id}"/>' if style_id is not None else ""
        )
        return (
            f'<w:tbl {nsdecls("w")}>'
            "<w:tblPr>"
            f"{style_xml}"
            '<w:tblW w:w="0" w:type="auto"/>'
            f"{borders_xml}"
            '<w:tblLayout w:type="fixed"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0"'
            ' w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
            "</w:tblPr>"
            f"<w:tblGrid>{grid_xml}</w:tblGrid>"
            f'{"".join(rows_xml)}'
            "</w:tbl>"
        )

    def _column_widths(self, table: WordTable, grid_width: int) -> tuple[int, ...]:
        """Return the width of every grid column of a table, in twips.

        The widths the document declares are the source document's own; when
        the declaration does not match the grid the table falls back to an
        even split and says so, because a grid that does not add up is a
        table Word will re-lay out on its own.
        """
        if grid_width == 0:
            return ()
        declared = table.column_widths
        if len(declared) == grid_width:
            return declared
        if declared:
            self._omit(
                f"table declares {len(declared)} column widths for a grid of "
                f"{grid_width} columns"
            )
        width = max(1, _TABLE_TOTAL_WIDTH // grid_width)
        return tuple(width for _ in range(grid_width))

    def _render_table_row(
        self,
        doc: Any,
        placements: list[_CellPlacement],
        column_widths: tuple[int, ...],
        state: _RenderState,
        *,
        is_header: bool,
    ) -> str:
        header_xml = "<w:trPr><w:tblHeader/></w:trPr>" if is_header else ""
        cells_xml = "".join(
            self._table_cell_xml(doc, placement, column_widths, state)
            for placement in placements
        )
        return f"<w:tr>{header_xml}{cells_xml}</w:tr>"

    def _table_cell_xml(
        self,
        doc: Any,
        placement: _CellPlacement,
        column_widths: tuple[int, ...],
        state: _RenderState,
    ) -> str:
        """Render one table cell.

        A cell carries its own content: the runs of its paragraphs, with their
        formatting and their hyperlinks, and the tables nested inside it.  The
        flat text is only the fallback for a cell that has no runs of its own.
        """
        cell = placement.cell
        col_span = placement.col_span
        properties: list[str] = []
        width = cell.width or sum(
            column_widths[
                placement.column : placement.column + col_span
            ]
        )
        properties.append(f'<w:tcW w:w="{max(1, width)}" w:type="dxa"/>')
        if col_span > 1:
            properties.append(f'<w:gridSpan w:val="{col_span}"/>')
        if placement.vertical_merge == "restart":
            properties.append('<w:vMerge w:val="restart"/>')
        elif placement.vertical_merge == "continue":
            properties.append("<w:vMerge/>")

        content: list[str] = []
        if cell.runs:
            content.append(
                "<w:p>"
                + "".join(self._run_xml(doc, run, state) for run in cell.runs)
                + "</w:p>"
            )
        elif cell.text:
            content.append(f"<w:p>{_text_run_xml(strip_control_characters(cell.text))}</w:p>")
        for nested in cell.tables:
            content.append(self._table_xml(doc, nested, state))
        if not content:
            content.append("<w:p/>")
        elif content[-1].startswith("<w:tbl"):
            # Word requires a cell to end with a paragraph, and a nested table
            # is not one.
            content.append("<w:p/>")

        return (
            f"<w:tc {nsdecls('w', 'r')}>"
            f"<w:tcPr>{''.join(properties)}</w:tcPr>"
            f"{''.join(content)}"
            "</w:tc>"
        )

    def _run_xml(self, doc: Any, run: WordRun, state: _RenderState) -> str:
        """Render one run as XML, for the hand-built content of a table cell."""
        if run.equation_xml is not None:
            # An equation is a paragraph-level element, not a run, so it takes
            # the place of the run rather than sitting inside it.
            element = self._equation_element(run)
            if element is not None:
                return str(etree.tostring(element, encoding="unicode", with_tail=False))
        text = strip_control_characters(run.text)
        properties = _run_properties_xml(run)
        anchor = self._run_anchor_xml(run, state)
        text_element = ""
        if text:
            pieces = text.split("\n")
            parts: list[str] = []
            for piece in pieces:
                if parts:
                    parts.append("<w:br/>")
                parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
            text_element = "".join(parts)
        if run.hyperlink is not None:
            url = run.hyperlink.strip()
            if not url:
                raise WordRenderError("a hyperlink run requires a non-empty target URL")
            try:
                rel_id = doc.part.relate_to(url, RT.HYPERLINK, is_external=True)
            except Exception as exc:
                raise WordRenderError(
                    f"could not create a hyperlink relationship for {url!r}: {exc!r}"
                ) from exc
            return (
                f'<w:hyperlink r:id="{rel_id}">'
                f"<w:r>{properties}{text_element}{anchor}</w:r>"
                "</w:hyperlink>"
            )
        return f"<w:r>{properties}{text_element}{anchor}</w:r>"

    # -- footnotes ---------------------------------------------------------

    def _render_footnotes(
        self,
        doc: Any,
        document: WordDocument,
        state: _RenderState,
    ) -> None:
        if not document.footnotes:
            return
        root = OxmlElement(
            "w:footnotes",
            nsdecls={"w": nsmap["w"], "r": nsmap["r"]},
        )
        root.append(_separator_footnote("separator", "0"))
        root.append(_separator_footnote("continuationSeparator", "1"))
        part = _NotesPart(
            PackURI(_FOOTNOTES_PARTNAME),
            _FOOTNOTES_CONTENT_TYPE,
            doc.part.package,
            root,
        )
        try:
            doc.part.relate_to(part, RT.FOOTNOTES)
        except Exception as exc:
            raise WordRenderError(
                f"could not attach the footnotes part to the document: {exc!r}"
            ) from exc
        for footnote in document.footnotes:
            root.append(self._render_footnote(footnote, part, state))

    def _render_footnote(
        self,
        footnote: WordFootnote,
        part: _NotesPart,
        state: _RenderState,
    ) -> Any:
        numeric_id = state.footnote_ids[footnote.footnote_id]
        element = OxmlElement("w:footnote")
        element.set(qn("w:id"), str(numeric_id))

        paragraph = OxmlElement("w:p")
        marker_run = OxmlElement("w:r")
        marker_properties = OxmlElement("w:rPr")
        vertical_align = OxmlElement("w:vertAlign")
        vertical_align.set(qn("w:val"), "superscript")
        marker_properties.append(vertical_align)
        marker_run.append(marker_properties)
        marker_run.append(OxmlElement("w:footnoteRef"))
        paragraph.append(marker_run)

        element.append(paragraph)

        runs = footnote.runs or (WordRun(text=footnote.text),)
        for run in runs:
            self._append_note_run(paragraph, run, part, state)
        return element

    # -- headers, footers and core properties ------------------------------

    def _render_headers_and_footers(
        self,
        doc: Any,
        document: WordDocument,
    ) -> None:
        """Write the document's headers and footers into the package.

        A header is placed by the *reference type* the document gives it --
        ``default``, ``first`` or ``even`` -- and not by the order the parts
        happen to have.  A document whose ``header1.xml`` holds the even-page
        header must not come back with its headers swapped.
        """
        headers = self._headers_by_type(document)
        footers = self._footers_by_type(document)
        if not headers and not footers:
            return
        section = doc.sections[0]
        header_accessors = {
            "default": section.header,
            "first": section.first_page_header,
            "even": section.even_page_header,
        }
        footer_accessors = {
            "default": section.footer,
            "first": section.first_page_footer,
            "even": section.even_page_footer,
        }
        for ref_type, text in headers.items():
            accessor = header_accessors.get(ref_type)
            if accessor is None:
                self._omit(f"header reference type {ref_type!r} is not supported")
                continue
            _set_header_footer_text(accessor, text)
        for ref_type, text in footers.items():
            accessor = footer_accessors.get(ref_type)
            if accessor is None:
                self._omit(f"footer reference type {ref_type!r} is not supported")
                continue
            _set_header_footer_text(accessor, text)
        if "first" in headers or "first" in footers:
            section.different_first_page_header_footer = True
        if "even" in headers or "even" in footers:
            doc.settings.odd_and_even_pages_header_footer = True

    @staticmethod
    def _is_reference_driven(document: WordDocument) -> bool:
        """Report whether the document names its headers through sections.

        A document that does is matched by reference type, so it has no
        "more headers than Word has roles for" failure mode.
        """
        return bool(document.sections) and bool(
            document.sections[0].header_refs or document.sections[0].footer_refs
        )

    @staticmethod
    def _headers_by_type(document: WordDocument) -> dict[str, str]:
        """Return the header texts of the document, keyed by reference type."""
        if document.sections:
            refs = document.sections[0].header_refs
            if refs:
                return {ref.ref_type: ref.text for ref in refs if ref.text}
        # Without a reference the only information the document offers is the
        # order of its parts, which is what the model carries.
        positional = ("default", "first", "even")
        return {
            positional[index]: text
            for index, text in enumerate(document.headers)
            if index < len(positional)
        }

    @staticmethod
    def _footers_by_type(document: WordDocument) -> dict[str, str]:
        """Return the footer texts of the document, keyed by reference type."""
        if document.sections:
            refs = document.sections[0].footer_refs
            if refs:
                return {ref.ref_type: ref.text for ref in refs if ref.text}
        positional = ("default", "first", "even")
        return {
            positional[index]: text
            for index, text in enumerate(document.footers)
            if index < len(positional)
        }

    def _apply_core_properties(self, doc: Any, document: WordDocument) -> None:
        properties = document.properties
        core = doc.core_properties
        core.title = properties.title
        core.author = properties.author
        core.subject = properties.subject
        core.keywords = properties.keywords
        core.comments = properties.comments
        core.revision = properties.revision

        # A timestamp the model does not carry must not be invented: a stamped
        # creation date is a false statement about the document.  The elements
        # are removed rather than left at the template's default, which is as
        # deterministic as a pinned one — and each of the two is decided on
        # its own, so a document that carries a creation date but no
        # modification date says exactly that.  A non-deterministic writer is
        # writing the document at this moment, so it may say so.
        now = (
            None
            if self._deterministic
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )
        if properties.created is not None:
            core.created = _naive_utc(properties.created)
        elif now is not None:
            core.created = _naive_utc(now)
        else:
            _remove_core_timestamp(core, "dcterms:created")
        if properties.modified is not None:
            core.modified = _naive_utc(properties.modified)
        elif now is not None:
            core.modified = _naive_utc(now)
        else:
            _remove_core_timestamp(core, "dcterms:modified")


# -- module helpers -------------------------------------------------------


def _bind_math_namespace(markup: str) -> str:
    """Return ``markup`` with the OMML namespace bound on its root element.

    ``parse_xml`` needs every prefix a fragment uses to be declared on it, so
    a fragment that names ``m:oMath`` without declaring the namespace would
    not parse.  The declaration is added to the root element only; the markup
    itself is otherwise untouched, and a fragment that already declares the
    namespace is returned unchanged.
    """
    if "xmlns:m=" in markup:
        return markup
    start = markup.find("<m:")
    if start == -1:
        return markup
    end = markup.find(">", start)
    if end == -1:
        return markup
    return f'{markup[:end]} xmlns:m="{_NS_M}"{markup[end:]}'


def _format_validation_errors(exc: ValidationError, document: WordDocument) -> str:
    """Summarise a validation failure, naming every offending part."""
    parts: list[str] = []
    for error in exc.errors():
        location = _error_location(error["loc"], document)
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _error_location(
    location: tuple[int | str, ...],
    document: WordDocument,
) -> str:
    """Name the part of the document a validation error points at."""
    path = ".".join(str(item) for item in location)
    if len(location) >= 2 and location[0] == "blocks" and isinstance(location[1], int):
        index = location[1]
        if 0 <= index < len(document.blocks):
            return f"{path} (block_id={document.blocks[index].block_id!r})"
    return path or "document"


def _first_style(doc: Any, names: tuple[str | None, ...]) -> Any | None:
    """Return the first style of ``names`` that exists in ``doc``.

    A missing style is a fallback, not an error: the document still renders.
    """
    for name in names:
        if not name:
            continue
        try:
            return doc.styles[name]
        except KeyError:
            continue
    return None


def _table_style_id(doc: Any) -> str | None:
    """Return the style id of the ``Table Grid`` style, when it exists."""
    style = _first_style(doc, ("Table Grid",))
    if style is None:
        return None
    style_id = getattr(style, "style_id", None)
    if not style_id:
        return None
    return str(style_id)


def _apply_run_formatting(run_object: Any, run: WordRun) -> None:
    """Apply the model's run formatting to a python-docx run."""
    if run.bold:
        run_object.bold = True
    if run.italic:
        run_object.italic = True
    if run.underline:
        run_object.underline = True
    if run.superscript:
        run_object.font.superscript = True
    if run.subscript:
        run_object.font.subscript = True
    if run.strike:
        run_object.font.strike = True
    if run.font:
        run_object.font.name = run.font
    if run.size:
        run_object.font.size = Pt(run.size / 2)
    if run.color:
        run_object.font.color.rgb = RGBColor.from_string(run.color)
    if run.highlight:
        _set_run_highlight(run_object, run.highlight)


def _set_run_highlight(run_object: Any, highlight: str) -> None:
    """Set the highlight of a run, keeping the colour name Word uses.

    The value is written straight into the attribute rather than through
    ``python-docx``'s enumeration, because the enumeration would re-spell the
    colour and the round trip has to give back exactly what it read.
    """
    properties = run_object._r.get_or_add_rPr()
    element = properties.get_or_add_highlight()
    element.set(qn("w:val"), highlight)


def _run_properties_xml(run: WordRun) -> str:
    """Render the run properties of a run as XML.

    The elements are emitted in the order the schema requires, so that Word
    accepts the result without a repair prompt.  A tracked formatting
    revision is written last, which is where the schema expects it.
    """
    elements: list[str] = []
    if run.font:
        escaped = escape(run.font)
        elements.append(f'<w:rFonts w:ascii="{escaped}" w:hAnsi="{escaped}"/>')
    if run.bold:
        elements.append("<w:b/>")
    if run.italic:
        elements.append("<w:i/>")
    if run.strike:
        elements.append("<w:strike/>")
    if run.color:
        elements.append(f'<w:color w:val="{escape(run.color)}"/>')
    if run.size:
        elements.append(f'<w:sz w:val="{run.size}"/>')
    if run.highlight:
        elements.append(f'<w:highlight w:val="{escape(run.highlight)}"/>')
    if run.underline:
        elements.append('<w:u w:val="single"/>')
    if run.superscript:
        elements.append('<w:vertAlign w:val="superscript"/>')
    elif run.subscript:
        elements.append('<w:vertAlign w:val="subscript"/>')
    if run.format_change_xml is not None:
        elements.append(run.format_change_xml)
    if not elements:
        return ""
    return "<w:rPr>" + "".join(elements) + "</w:rPr>"


def _same_revision(left: WordRevision | None, right: WordRevision | None) -> bool:
    """Report whether two revisions describe the same revision element."""
    if left is None or right is None:
        return False
    if left.kind is not right.kind:
        return False
    if left.revision_id or right.revision_id:
        return left.revision_id == right.revision_id
    return left == right


def _mark_run_deleted(run_object: Any) -> None:
    """Turn the text of a run into deleted text.

    Word keeps the text of a deleted run in ``w:delText`` rather than in
    ``w:t``: the run is still there, but Word renders it as struck-through
    revision markup instead of as content.
    """
    for element in run_object._r.findall(qn("w:t")):
        element.tag = qn("w:delText")


def _drawing_xml(rel_id: str, name: str, extent: tuple[int, int]) -> str:
    """Build the DrawingML of an inline picture."""
    width, height = extent
    return (
        f'<w:drawing {nsdecls("w", "r", "wp", "a", "pic")}>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="1" name="Picture 1"/>'
        "<wp:cNvGraphicFramePr>"
        '<a:graphicFrameLocks noChangeAspect="1"/>'
        "</wp:cNvGraphicFramePr>"
        "<a:graphic>"
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic>"
        "<pic:nvPicPr>"
        f'<pic:cNvPr id="1" name="{escape(name)}"/>'
        "<pic:cNvPicPr/>"
        "</pic:nvPicPr>"
        "<pic:blipFill>"
        f'<a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill>"
        "<pic:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</pic:spPr>"
        "</pic:pic>"
        "</a:graphicData>"
        "</a:graphic>"
        "</wp:inline>"
        "</w:drawing>"
    )


def _relationship_kind(rel_type: str) -> str:
    """Return the kind of relationship a relationship type names.

    The kind is the last segment of the type: the namespace every
    relationship shares says nothing about the part, the last segment says
    what the part is.
    """
    return rel_type.rsplit("/", 1)[-1].strip()


def _is_writable_part_name(name: str) -> bool:
    """Report whether a part name is one a package may hold.

    A package part name is a relative path: no leading slash, no empty
    segment, and no ``..`` that would climb out of the package.  A name that
    breaks those rules is refused rather than written: Word would report the
    package as corrupt.
    """
    if not name or name.startswith("/"):
        return False
    if "\\" in name or any(character in name for character in "\r\n\t"):
        return False
    segments = name.split("/")
    return all(segment and segment not in {".", ".."} for segment in segments)


def _is_relationship_part_name(name: str) -> bool:
    """Report whether a part name is the relationship part of a sibling part.

    A ``.rels`` file is the relationship list of the part beside it, not
    content: handing one to Word as content is what makes Word report the
    package as corrupt.
    """
    segments = name.split("/")
    return len(segments) >= 2 and segments[-2] == "_rels"


def _normalise_part_name(name: str) -> str:
    """Return the part name in the form a package names parts by.

    A package part name is a relative path with POSIX separators and no
    leading slash; the model carries the name the reader saw, which may
    carry either.
    """
    return name.strip().lstrip("/")


def _drawing_extent(
    extent: tuple[int, int] | None, default: tuple[int, int]
) -> tuple[int, int]:
    """Return the extent an anchored element is drawn at, in EMUs.

    The recorded extent wins over the default: Word scales a chart by
    rewriting the extent of the drawing that holds it, so an element that
    comes back at a default size is an element that came back the wrong
    size.
    """
    if extent is None:
        return default
    width, height = extent
    return (max(1, int(width)), max(1, int(height)))


def _points(emu: int) -> int:
    """Return the whole number of points an extent is measured in.

    A shape is sized in points, and Word writes a size as a whole number of
    them: ``100pt``, never ``100.0pt``.
    """
    return max(1, int(round(emu / _EMU_PER_POINT)))


def _object_xml(rel_id: str, extent: tuple[int, int] | None) -> str:
    """Build the body reference of an embedded object.

    The shape of the object is sized in points, so the recorded extent is
    converted from EMUs into them before it is written into the shape.
    """
    width, height = _drawing_extent(extent, _DEFAULT_OBJECT_EXTENT)
    return (
        f'<w:object {nsdecls("w", "r")} xmlns:v="{_NS_V}" xmlns:o="{_NS_O}">'
        '<v:shape id="_x0000_i1025" type="#_x0000_t75" '
        f'style="width:{_points(width)}pt;height:{_points(height)}pt" o:ole=""/>'
        '<o:OLEObject Type="Embed" ProgID="Package" ShapeID="_x0000_i1025" '
        'DrawAspect="Content" ObjectID="_1" r:id="' + rel_id + '"/>'
        "</w:object>"
    )


def _chart_xml(rel_id: str, extent: tuple[int, int] | None) -> str:
    """Build the body reference of a chart."""
    width, height = _drawing_extent(extent, _DEFAULT_DRAWING_EXTENT)
    return (
        f'<w:drawing {nsdecls("w", "r", "wp", "a", "c")}>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="1" name="Chart 1"/>'
        "<wp:cNvGraphicFramePr/>"
        "<a:graphic>"
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        f'<c:chart r:id="{rel_id}"/>'
        "</a:graphicData>"
        "</a:graphic>"
        "</wp:inline>"
        "</w:drawing>"
    )


def _diagram_xml(rel_ids: dict[str, str], extent: tuple[int, int] | None) -> str:
    """Build the body reference of a diagram.

    A diagram is drawn from four parts: its data, its layout, its quick
    style and its colours.  The relationship of each is named by the
    attribute it is reached through, and a part that is not in the package
    is left out rather than pointed at.
    """
    width, height = _drawing_extent(extent, _DEFAULT_DRAWING_EXTENT)
    attributes = "".join(
        f' {attribute}="{rel_id}"'
        for attribute, rel_id in sorted(rel_ids.items())
        if rel_id
    )
    return (
        f'<w:drawing {nsdecls("w", "r", "wp", "a", "dgm")}>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="1" name="Diagram 1"/>'
        "<wp:cNvGraphicFramePr/>"
        "<a:graphic>"
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram">'
        f"<dgm:relIds{attributes}/>"
        "</a:graphicData>"
        "</a:graphic>"
        "</wp:inline>"
        "</w:drawing>"
    )


def _image_extent(media: WordMedia | None) -> tuple[int, int]:
    """Return the extent an embedded image is drawn at, in EMUs.

    The pixel size of the image is read from its header when the format
    allows it; an image whose size cannot be read is drawn at a default
    size rather than at nothing at all.
    """
    default = (_DEFAULT_IMAGE_EXTENT, _DEFAULT_IMAGE_EXTENT)
    if media is None or not media.data:
        return default
    size = _image_pixel_size(media.data)
    if size is None:
        return default
    width, height = size
    return (
        max(1, int(width / 96 * _EMU_PER_INCH)),
        max(1, int(height / 96 * _EMU_PER_INCH)),
    )


def _image_pixel_size(data: bytes) -> tuple[int, int] | None:
    """Read the pixel size of a PNG or JPEG image, when it can be read."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if width > 0 and height > 0:
            return (width, height)
        return None
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB}:
                height = int.from_bytes(data[index + 5 : index + 7], "big")
                width = int.from_bytes(data[index + 7 : index + 9], "big")
                if width > 0 and height > 0:
                    return (width, height)
                return None
            if marker in {0xD8, 0xD9}:
                index += 2
                continue
            length = int.from_bytes(data[index + 2 : index + 4], "big")
            index += 2 + length
        return None
    return None


def _append_raw_run(
    paragraph: Any,
    run: WordRun,
    owner: Any,
    state: _RenderState,
    *,
    anchor: str = "",
    change: Any | None = None,
) -> None:
    """Append a run to a hand-built paragraph (a footnote body).

    The paragraph is raw OOXML, so the run is built element by element.  A
    hyperlink run is wrapped in a ``w:hyperlink`` whose relationship is
    created against ``owner``, the part that will contain the paragraph.
    """
    text = strip_control_characters(run.text)
    if run.hyperlink is not None:
        url = run.hyperlink.strip()
        if not url:
            raise WordRenderError("a hyperlink run requires a non-empty target URL")
        try:
            rel_id = owner.relate_to(url, RT.HYPERLINK, is_external=True)
        except Exception as exc:
            raise WordRenderError(
                f"could not create a hyperlink relationship for {url!r}: {exc!r}"
            ) from exc
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), rel_id)
        paragraph.append(hyperlink)
        run_element = OxmlElement("w:r")
        hyperlink.append(run_element)
    else:
        run_element = OxmlElement("w:r")
        paragraph.append(run_element)

    if (
        run.bold
        or run.italic
        or run.underline
        or run.superscript
        or run.subscript
        or change is not None
    ):
        properties = OxmlElement("w:rPr")
        if run.bold:
            properties.append(OxmlElement("w:b"))
        if run.italic:
            properties.append(OxmlElement("w:i"))
        if run.underline:
            underline = OxmlElement("w:u")
            underline.set(qn("w:val"), "single")
            properties.append(underline)
        if run.superscript:
            vertical_align = OxmlElement("w:vertAlign")
            vertical_align.set(qn("w:val"), "superscript")
            properties.append(vertical_align)
        if run.subscript:
            vertical_align = OxmlElement("w:vertAlign")
            vertical_align.set(qn("w:val"), "subscript")
            properties.append(vertical_align)
        if change is not None:
            properties.append(change)
        run_element.append(properties)

    if text:
        text_element = OxmlElement("w:t")
        text_element.set(qn("xml:space"), "preserve")
        text_element.text = text
        run_element.append(text_element)

    if run.footnote_ref is not None:
        numeric_id = state.footnote_ids.get(run.footnote_ref)
        if numeric_id is None:
            raise WordRenderError(
                f"run references unknown footnote {run.footnote_ref!r}"
            )
        reference = OxmlElement("w:footnoteReference")
        reference.set(qn("w:id"), str(numeric_id))
        run_element.append(reference)

    if run.endnote_ref is not None:
        numeric_id = state.endnote_ids.get(run.endnote_ref)
        if numeric_id is None:
            raise WordRenderError(
                f"run references unknown endnote {run.endnote_ref!r}"
            )
        reference = OxmlElement("w:endnoteReference")
        reference.set(qn("w:id"), str(numeric_id))
        run_element.append(reference)

    anchor_xml = anchor
    if anchor_xml:
        run_element.append(parse_xml(anchor_xml))


def _separator_note(
    tag: str, note_type: str, note_id: str
) -> Any:
    """Build the separator notes Word requires to be present."""
    marker = "w:separator" if note_type == "separator" else "w:continuationSeparator"
    xml = (
        f'<{tag} {nsdecls("w")} w:type="{note_type}" w:id="{note_id}">'
        "<w:p>"
        '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
        f"<w:r><{marker}/></w:r>"
        "</w:p>"
        f"</{tag}>"
    )
    return parse_xml(xml)


def _separator_footnote(footnote_type: str, footnote_id: str) -> Any:
    """Build the separator footnotes Word requires to be present."""
    return _separator_note("w:footnote", footnote_type, footnote_id)


class _CellPlacement:
    """One table cell, positioned in the grid."""

    __slots__ = ("column", "col_span", "cell", "vertical_merge")

    def __init__(
        self,
        cell: WordTableCell,
        col_span: int,
        vertical_merge: str | None,
        column: int = 0,
    ) -> None:
        self.cell = cell
        self.col_span = col_span
        self.vertical_merge = vertical_merge
        self.column = column


class _MergeOrigin:
    """A vertical merge started by a cell, tracked over the rows it covers."""

    __slots__ = ("column", "col_span", "last_row")

    def __init__(self, column: int, col_span: int, last_row: int) -> None:
        self.column = column
        self.col_span = col_span
        self.last_row = last_row


def _layout_table(table: WordTable) -> list[list[_CellPlacement]]:
    """Lay every row of the table out on the grid, in column order.

    A table's grid is made of *grid columns*, not of cells: a cell occupies
    ``col_span`` columns starting at the column it is placed on, and the grid
    is exactly as wide as the widest row needs it to be.

    A vertical merge is described twice in the model and the two mechanisms
    cooperate here:

    * the cell that *starts* a merge carries ``row_span > 1`` and keeps its
      text; it marks its columns as covered in the rows below;
    * the cells below it are flagged ``is_merge_continuation`` and carry no
      text of their own.

    A continuation cell is placed on the column its merge started in — that
    is the column the merge continues in — and the merged region covers the
    columns its origin spans, less the columns the continuation cells that
    follow it inside the region cover, so the cells that follow it start after
    the region and keep their own text.  When the model supplies the
    continuation cells, they are consumed at the covered grid position, so the
    two mechanisms never double up.  When it does not — a hand-authored merge
    that only names the origin — the writer synthesises the continuation cells,
    so the merge is rendered as a merge instead of being flattened away.

    The layout is the reader's layout, read backwards.  The reader reads a
    cell that lands inside a merged region back as a continuation of that
    region, so the model holds the cells of a ragged region as continuations:
    the region the reader gave the model was that much narrower than the origin
    it continues.  The writer lays the region out that much narrower, and
    writes those cells as ordinary cells inside it, so the row tiles the grid
    and reading it back gives the model the writer was handed.  A cell the
    model does not mark as a continuation is never written inside a region:
    the reader would swallow its text.
    """
    origins: list[_MergeOrigin] = []
    layout: list[list[_CellPlacement]] = []
    for row_index, row in enumerate(table.rows):
        placements, new_origins = _place_row(row, origins, row_index)
        layout.append(placements)
        origins.extend(new_origins)
    return layout


def _width_inside(cells: tuple[WordTableCell, ...], start: int) -> int:
    """The width the continuation cells that follow a merged region cover inside it.

    The reader reads a cell that lands inside a merged region back as a
    continuation of that region, so the model holds those cells as
    continuations: the region the reader gave the model was that much narrower
    than the origin it continues.  The writer lays the region out that much
    narrower, so the cells that follow tile the rest of the region and the row
    keeps the width the grid has.

    Only the continuations that follow the region *immediately* are counted: a
    cell that is not a continuation ends the region, because the writer places
    such a cell outside the region, where the reader keeps its text.
    """
    width = 0
    for cell in cells[start:]:
        if not cell.is_merge_continuation:
            break
        width += cell.col_span
    return width


def _place_row(
    row: WordTableRow,
    origins: list[_MergeOrigin],
    row_index: int,
) -> tuple[list[_CellPlacement], list[_MergeOrigin]]:
    """Place the cells of one row on the grid.

    Returns the row's placements in column order together with the merges
    the row starts.
    """
    open_merges = sorted(
        (origin for origin in origins if row_index < origin.last_row),
        key=lambda origin: origin.column,
    )

    taken: set[int] = set()
    matched: list[tuple[int, int]] = []
    loose: list[WordTableCell] = []
    covered: list[tuple[WordTableCell, int]] = []
    used: set[int] = set()
    region: _MergeOrigin | None = None
    column = 0
    for cell_index, cell in enumerate(row.cells):
        if cell.is_merge_continuation:
            index = _match_merge(open_merges, used, column)
            if index is None:
                open_region = region
                if open_region is not None and column < open_region.column + open_region.col_span:
                    # A continuation cell that continues nothing but lands
                    # inside the merged region the row has already continued
                    # belongs to that region: the reader reads a cell inside a
                    # merged region back as a continuation of it, so it is
                    # written as an ordinary cell -- inside the region, so the
                    # row keeps the width the grid has.
                    covered.append((cell, column))
                    taken.update(range(column, column + cell.col_span))
                    column += cell.col_span
                    continue
                # A continuation cell without a merge above it continues
                # nothing: it is placed like any other cell and is still
                # rendered as a continuing merge rather than dropped.
                loose.append(cell)
                column += cell.col_span
                continue
            used.add(index)
            merge = open_merges[index]
            # The merged region covers the columns its origin spans, less the
            # columns the continuation cells that follow it inside the region
            # cover: the reader read those cells back as continuations of the
            # region, so the region it gave the model was that much narrower
            # than the origin, and the cells that follow tile the rest of it. A
            # row that follows a region with an ordinary cell keeps the region
            # as wide as its origin, so that the ordinary cell lands outside it
            # and keeps its text.
            width = max(1, merge.col_span - _width_inside(row.cells, cell_index + 1))
            matched.append((merge.column, width))
            taken.update(range(merge.column, merge.column + width))
            region = merge
            column = max(column, merge.column + width)
            continue
        loose.append(cell)
        column += cell.col_span

    # A merge the row does not continue receives a synthesised continuation,
    # so that a model naming only the origin still renders as a merge.
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        matched.append((merge.column, merge.col_span))
        taken.update(range(merge.column, merge.column + merge.col_span))

    placements: list[_CellPlacement] = []
    new_origins: list[_MergeOrigin] = []
    cursor = 0
    for cell in loose:
        position = _next_free(taken, cursor)
        taken.update(range(position, position + cell.col_span))
        cursor = position + cell.col_span
        if cell.is_merge_continuation:
            placements.append(
                _CellPlacement(cell, cell.col_span, "continue", position)
            )
            continue
        starts_merge = cell.row_span > 1
        placements.append(
            _CellPlacement(
                cell,
                cell.col_span,
                "restart" if starts_merge else None,
                position,
            )
        )
        if starts_merge:
            new_origins.append(
                _MergeOrigin(position, cell.col_span, row_index + cell.row_span)
            )
    for position, col_span in matched:
        placements.append(_CellPlacement(_EMPTY_CELL, col_span, "continue", position))
    for cell, position in covered:
        # The cell sits inside the merged region it belongs to, so it is
        # written as an ordinary cell: the reader reads a cell inside a merged
        # region back as a continuation of that region.
        placements.append(_CellPlacement(cell, cell.col_span, None, position))
    placements.sort(key=lambda placement: placement.column)
    return placements, new_origins


def _match_merge(
    open_merges: list[_MergeOrigin],
    used: set[int],
    column: int,
) -> int | None:
    """Return the index of the merge a continuation cell continues.

    The merge whose region contains ``column`` wins.  A merge that starts at
    or after the column comes next, and the leftmost merge still free is the
    last resort — the merge survives even when the model puts the
    continuation cell somewhere else in its row.  ``None`` means that the
    cell continues no merge at all.
    """
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        if merge.column <= column < merge.column + merge.col_span:
            return index
    for index, merge in enumerate(open_merges):
        if index in used:
            continue
        if merge.column >= column:
            return index
    for index in range(len(open_merges)):
        if index not in used:
            return index
    return None


def _next_free(taken: set[int], cursor: int) -> int:
    """Return the first column at or after ``cursor`` that is still free."""
    column = cursor
    while column in taken:
        column += 1
    return column


def _text_run_xml(text: str) -> str:
    """Render plain text as a single run, turning newlines into breaks."""
    if not text:
        return ""
    parts: list[str] = []
    for piece in text.split("\n"):
        if parts:
            parts.append("<w:br/>")
        parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return f"<w:r>{''.join(parts)}</w:r>"


def _set_header_footer_text(accessor: Any, text: str) -> None:
    """Write ``text`` into a header or footer definition."""
    accessor.is_linked_to_previous = False
    clean_text = strip_control_characters(text)
    if not accessor.paragraphs:
        accessor.add_paragraph()
    accessor.paragraphs[0].text = clean_text


def _naive_utc(value: datetime) -> datetime:
    """Return ``value`` as a naive UTC datetime."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _remove_core_timestamp(core: Any, tag: str) -> None:
    """Remove one ``dcterms`` timestamp from the core properties.

    The package template carries a creation and a modification date of its
    own.  A document whose model holds no such timestamp must not inherit
    one: the element is removed, so the package states nothing it cannot
    support.
    """
    element = core._element
    child = element.find(qn(tag))
    if child is not None:
        element.remove(child)


def _rebuild_zip_deterministically(raw: bytes) -> bytes:
    """Rewrite a package with fixed member timestamps.

    ``python-docx`` writes zip members with the current time, which would make
    two renders of the same document differ.  The member order and the
    compression type of the original archive are preserved; only the timestamp
    changes.
    """
    source = zipfile.ZipFile(io.BytesIO(raw))
    target = io.BytesIO()
    with source, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=_FIXED_ZIP_TIMESTAMP)
            entry.compress_type = info.compress_type
            entry.external_attr = info.external_attr
            entry.create_system = info.create_system
            archive.writestr(entry, source.read(info.filename))
    return target.getvalue()


__all__ = ["WordWriter"]
