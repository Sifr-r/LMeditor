"""The frozen witness of the manuscript builder.

This is the implementation that used to live in ``lmeditor.word.manuscript``,
kept exactly as it was when the live module became a door to the Kotlin tier.
It is the yardstick the tier is measured against:
``backend/scripts/verify_kotlin_parity.py`` asks this module and the Kotlin tier
the same question -- build the same document from the same draft -- and
compares the two documents' wire forms.

Two things make it a witness rather than a mirror:

* it is plain Python, and it never calls across the NDJSON seam -- so the
  comparison is between two independent implementations; and
* it is re-aligned to the tier's *current* semantics only when the tier's
  rule deliberately changes, and every such change is recorded in the
  harness's ``REFERENCE_WITNESSES`` -- so a divergence that is not a
  deliberate change is a finding, not a silent edit to the ruler.

The only line that differs from the original is the import of the citation
grammar: the original reached ``lmeditor.core.citations``, which is a door to
the Kotlin tier now, and a witness that leaned on the door it is supposed to
witness would prove nothing.  The grammar therefore comes from the frozen
sibling ``lmeditor._reference.citations``, which is the same grammar.

----

 LMeditor drafts -> the shared Word document model.

This module implements the binding contract's §7 (M1-M6). It maps
``lmeditor.core.schemas.DocumentDraft`` -- the manuscript the agent drafted --
onto ``lmeditor.word.model.WordDocument``, so that
:mod:`lmeditor.word.writer` can serialise it to a ``.docx`` without dropping
the grounding evidence.

The bridge is where LMeditor's central promise is carried into a Word
document: every inline citation marker emitted by the drafter
(``[#chunk_id]``) becomes a real Word artefact -- a superscript numbered marker
in the body plus a footnote carrying the cited source passage -- and a
reference list is appended at the end.

Design notes (the places where the contract leaves room, resolved once here
and documented so the behaviour is not accidental):

* **Citation grammar is never re-implemented.** Markers are located with
  :func:`lmeditor._reference.citations.iter_citation_spans`, the single source of
  truth shared with the drafting and verification layers, so the two sides
  cannot drift apart.
* **Numbering is per chunk id, assigned on first appearance** across the whole
  document, starting at 1. A chunk cited again later reuses its number, which
  is what a numbered reference list means to a reader.
* **One footnote per chunk id.** The first citation of a chunk that carries an
  ``exact_quote`` creates the footnote; later citations of the same chunk
  re-use it. A chunk without a matching lineage still gets its superscript
  marker -- the reference is not lost -- but no footnote. The footnote carries
  the cited passage *and* the id of the chunk it was taken from, because a
  reader of the exported ``.docx`` must be able to walk a claim back to the
  exact stored passage, not only to the document it came from.
* **Spacing around consumed markers.** A marker behaves like a word: when it
  directly abuts a word character on either side, a single space is inserted so
  that prose is neither glued together nor split by a double space. Whitespace
  already present in the source is preserved as-is and normalised once, at
  block level, by the shared model.
* **References are plain text, not CSL.** A record is rendered from the fields
  it actually has, in the contract's order, joined by single spaces. Nothing is
  invented: no labels, no emphasis, no punctuation the fields do not carry,
  and no ``None`` placeholders.
* **The manuscript's own syntax is honoured.** The editor writes the section
  content as markdown-flavoured text: headings, bullet and numbered lists,
  block quotes, fenced code and inline emphasis. The bridge reads that syntax
  instead of flattening it, so the exported document carries the structure the
  operator sees rather than one unformatted paragraph per block.
* **The author's text is never swallowed.** An inline marker without a
  partner -- ``**bold`` with no closing ``**`` -- stays literal text, and a
  fenced code block keeps its content verbatim. A manuscript that loses words
  on the way out is worse than one that keeps them unformatted.
* **A markdown table becomes a real Word table.** A pipe table is recognised
  only when a delimiter row follows the line that starts it, so a line that
  merely carries a pipe stays prose. The recognition is deliberately
  conservative: prose turned into a table loses the author's structure,
  while a table that stays prose merely loses its grid -- and losing a grid
  is the smaller harm.
* **The bridge is pure.** Inputs are never mutated: the section list is
  copied before it is filtered and sorted, and lineage entries are read, never
  modified.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lmeditor._reference.citations import iter_citation_spans
from lmeditor.core.exceptions import WordProcessingError
from lmeditor.core.schemas import BibliographicRecord, DocumentDraft, SectionDraft
from lmeditor.core.types import SectionStatus
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordFootnote,
    WordProperties,
    WordRun,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.text import normalize_whitespace, strip_control_characters

#: Section statuses that count as "passed verification" for ``verified_only``.
_VERIFIED_STATUSES: tuple[SectionStatus, ...] = (
    SectionStatus.VERIFIED,
    SectionStatus.APPROVED,
)

#: Paragraph emitted for a section that carries no drafted prose.
_EMPTY_SECTION_TEXT: str = "Section has no drafted content."

#: Heading of the appended reference list.
_REFERENCES_HEADING: str = "References"

#: Author placeholder for a bibliographic record without authors.
_ANONYMOUS_AUTHOR: str = "Anon."

#: Spaces of indentation that make a list item one level deeper.
_LIST_INDENT_WIDTH: int = 2

#: Deepest nesting a list item may carry; the model allows no more.
_MAX_LIST_LEVEL: int = 8

#: Width of a tab when the indentation of a list item is measured.
_TAB_WIDTH: int = 4

#: Font a code run is rendered in.
_CODE_FONT: str = "Consolas"

#: Colour a highlighted run is marked with.
_HIGHLIGHT_COLOR: str = "yellow"


@dataclass(frozen=True)
class _Formatting:
    """The character formatting that applies to a piece of inline text."""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    highlight: bool = False


@dataclass(frozen=True)
class _InlineMarker:
    """One inline marker of the manuscript syntax and the style it carries."""

    text: str
    style: _Formatting


@dataclass(frozen=True)
class _OpaqueSpan:
    """A span of the paragraph that the inline formatter must not split.

    ``chunk_ids`` is empty for a span that is not a citation -- the editor's
    ``{{cite:...}}`` marker, which survives the export as it was written.
    """

    start: int
    end: int
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class _TextPiece:
    """A piece of literal text with the formatting that applies to it."""

    text: str
    formatting: _Formatting


@dataclass(frozen=True)
class _CitationPiece:
    """A citation marker found in the text, with the formatting around it."""

    chunk_ids: tuple[str, ...]
    start: int
    end: int
    formatting: _Formatting


@dataclass(frozen=True)
class _ListItem:
    """A list item found in one line of the manuscript."""

    marker: ListMarker
    text: str
    level: int
    number: int | None = None


@dataclass(frozen=True)
class _BlockSpec:
    """One block of the manuscript, before it becomes a Word model block."""

    kind: WordBlockKind
    text: str
    level: int = 0
    list_marker: ListMarker | None = None
    list_group: str | None = None


@dataclass(frozen=True)
class _TableSpec(_BlockSpec):
    """A markdown table, before its cells become Word runs.

    The cells keep their raw text here and are turned into runs later, so
    that a citation marker inside a cell is consumed by the same ledger as a
    marker in a paragraph: the bridge must not have two grammars for one
    marker.

    The first row is the header row, because a table is only recognised when
    a delimiter row follows the line that starts it.
    """

    rows: tuple[tuple[str, ...], ...] = ()


#: A piece of a parsed paragraph: either literal text or a citation marker.
_Piece = _TextPiece | _CitationPiece

#: A heading: one to six ``#`` followed by whitespace and the heading's text.
_HEADING_PATTERN: re.Pattern[str] = re.compile(
    r"^[ \t]{0,3}(?P<marks>#{1,6})[ \t]+(?P<text>.*)$"
)

#: A bullet list item: a bullet marker, whitespace, and the item's text.
_BULLET_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+(?P<text>.*\S.*)$"
)

#: A numbered list item: a number, a dot, whitespace, and the item's text.
_NUMBERED_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<indent>[ \t]*)(?P<number>\d+)\.[ \t]+(?P<text>.*\S.*)$"
)

#: A block quote: a ``>`` marker and the quoted text.
_QUOTE_PATTERN: re.Pattern[str] = re.compile(r"^[ \t]{0,3}>[ \t]?(?P<text>.*)$")

#: The fence that opens and closes a code block.
_FENCE_PATTERN: re.Pattern[str] = re.compile(r"^[ \t]{0,3}```")

#: The cell of a delimiter row: hyphens, optionally framed by colons.
_DELIMITER_CELL_PATTERN: re.Pattern[str] = re.compile(r"^:?-{1,}:?$")

#: The editor's own citation marker, which the bridge keeps as it stands.
_TEMPLATE_CITATION_PATTERN: re.Pattern[str] = re.compile(r"\{\{cite:[^{}]*\}\}")

#: The inline markers of the manuscript syntax.
#:
#: Two-character markers come first so that ``**`` is matched before ``*``,
#: and the underscore pair before the single underscore.
_INLINE_MARKERS: tuple[_InlineMarker, ...] = (
    _InlineMarker(text="**", style=_Formatting(bold=True)),
    _InlineMarker(text="__", style=_Formatting(underline=True)),
    _InlineMarker(text="~~", style=_Formatting(strike=True)),
    _InlineMarker(text="==", style=_Formatting(highlight=True)),
    _InlineMarker(text="*", style=_Formatting(italic=True)),
    _InlineMarker(text="_", style=_Formatting(italic=True)),
    _InlineMarker(text="`", style=_Formatting(code=True)),
)


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

    ledger = _CitationLedger()
    list_groups = _ListGroups()
    blocks: list[WordBlock] = []

    def add(
        kind: WordBlockKind,
        runs: Sequence[WordRun],
        level: int = 0,
        list_marker: ListMarker | None = None,
        list_group: str | None = None,
    ) -> None:
        blocks.append(
            _make_block(
                _next_block_id(len(blocks)),
                kind,
                runs,
                level=level,
                list_marker=list_marker,
                list_group=list_group,
            )
        )

    def add_table(table: WordTable) -> None:
        """Append a table block to the document.

        A table block carries its table and nothing else: the model's shape
        rule forbids inline text on a table, because the text of a table is
        the text of its cells.
        """
        blocks.append(
            WordBlock(
                block_id=_next_block_id(len(blocks)),
                kind=WordBlockKind.TABLE,
                table=table,
            )
        )

    add(WordBlockKind.TITLE, (WordRun(text=strip_control_characters(document.title)),))

    for section in _selected_sections(document.sections, verified_only=verified_only):
        add(WordBlockKind.HEADING, (WordRun(text=strip_control_characters(section.title)),), level=1)
        quotes = _section_quotes(section)
        specs = _split_blocks(section.content, list_groups)
        if not specs:
            add(WordBlockKind.PARAGRAPH, (WordRun(text=_EMPTY_SECTION_TEXT),))
            continue
        for spec in specs:
            if isinstance(spec, _TableSpec):
                add_table(_build_table(spec, quotes, ledger))
                continue
            runs = (
                _code_runs(spec.text)
                if spec.kind is WordBlockKind.CODE
                else _paragraph_runs(spec.text, quotes, ledger)
            )
            if not runs:
                continue
            add(
                spec.kind,
                runs,
                level=spec.level,
                list_marker=spec.list_marker,
                list_group=spec.list_group,
            )

    if records:
        add(WordBlockKind.HEADING, (WordRun(text=_REFERENCES_HEADING),), level=1)
        for record in records:
            reference = _format_reference(record)
            add(WordBlockKind.PARAGRAPH, (WordRun(text=strip_control_characters(reference)),))

    return WordDocument(
        blocks=tuple(blocks),
        properties=WordProperties(title=document.title),
        footnotes=ledger.footnotes(),
    )


class _CitationLedger:
    """Assigns citation numbers and footnotes in first-appearance order.

    The ledger is the document-wide state of the bridge: it numbers chunk ids
    as they first appear, creates one footnote per chunk that carries a cited
    passage, and hands out the resulting footnote ids to the runs.
    """

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}
        self._footnote_ids: dict[str, str] = {}
        self._footnote_texts: list[str] = []

    def number_for(self, chunk_id: str) -> int:
        """Return the citation number of ``chunk_id``, assigning it on first use."""
        existing = self._numbers.get(chunk_id)
        if existing is not None:
            return existing
        number = len(self._numbers) + 1
        self._numbers[chunk_id] = number
        return number

    def footnote_ref_for(self, chunk_id: str, quote: str | None) -> str | None:
        """Return the footnote id for ``chunk_id``, creating it when a quote exists.

        Args:
            chunk_id: The cited chunk.
            quote: The verbatim source passage recorded for the chunk, if any.

        Returns:
            The footnote id to attach to the marker run, or ``None`` when the
            chunk carries no passage.
        """
        existing = self._footnote_ids.get(chunk_id)
        if existing is not None:
            return existing
        if not quote or not quote.strip():
            return None
        footnote_id = f"f{len(self._footnote_texts) + 1}"
        self._footnote_ids[chunk_id] = footnote_id
        self._footnote_texts.append(_footnote_text(chunk_id, quote))
        return footnote_id

    def footnotes(self) -> tuple[WordFootnote, ...]:
        """Return the footnotes created so far, in creation order."""
        return tuple(
            WordFootnote(
                footnote_id=f"f{index}",
                text=text,
                runs=(WordRun(text=text),),
            )
            for index, text in enumerate(self._footnote_texts, start=1)
        )


class _ListGroups:
    """Allocates the continuity groups of the numbered lists of a document.

    A group is what tells the writer that two numbered items continue one
    sequence.  The ids are allocated per document, because the writer groups
    every numbered block of the document by its group id: two lists of
    different sections must not share one number sequence.
    """

    def __init__(self) -> None:
        self._allocated = 0

    def allocate(self) -> str:
        """Return a group id that no other list of the document carries."""
        self._allocated += 1
        return f"ol{self._allocated}"


def _footnote_text(chunk_id: str, quote: str) -> str:
    """Return the text of the footnote that carries a cited passage.

    The passage comes first and the id of the chunk it was taken from
    follows it in brackets.  The id is written verbatim, so that a search of
    the exported package finds the passage it belongs to, and the format is
    fixed, so that the same chunk always produces the same footnote.
    """
    return f"{normalize_whitespace(quote)} [{chunk_id}]"


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


def _selected_sections(
    sections: Sequence[SectionDraft],
    *,
    verified_only: bool,
) -> list[SectionDraft]:
    """Return the sections to render, filtered and ordered.

    Ordering is ``(order, section_id)``; the input sequence is copied, so the
    draft's own list is never reordered in place.
    """
    selected = [
        section
        for section in sections
        if not verified_only or _has_passed_verification(section)
    ]
    return sorted(selected, key=lambda section: (section.order, section.section_id))


def _has_passed_verification(section: SectionDraft) -> bool:
    """Report whether a section passed verification."""
    if section.is_verified:
        return True
    status = section.status
    return any(status == candidate for candidate in _VERIFIED_STATUSES)


def _section_quotes(section: SectionDraft) -> dict[str, str]:
    """Map chunk id -> cited passage for one section's lineages.

    The first non-empty ``exact_quote`` wins when a chunk is cited by more
    than one lineage of the same section.
    """
    quotes: dict[str, str] = {}
    for lineage in section.citations:
        quote = lineage.exact_quote
        if quote is None:
            continue
        cleaned = quote.strip()
        if not cleaned:
            continue
        quotes.setdefault(lineage.chunk_id, cleaned)
    return quotes


def _split_blocks(content: str, groups: _ListGroups) -> list[_BlockSpec]:
    """Split section content into the blocks the manuscript carries.

    The content is read as the markdown-flavoured syntax the editor writes: a
    heading, a list item, a quote and a fenced code block are blocks of their
    own, and a line that carries none of them continues the prose above it.
    A pipe table is a block of its own as well, but only when a delimiter row
    follows the line that starts it.
    Blank lines still separate paragraphs.

    The draft schema strips the leading whitespace of a section's content, so
    the indentation of the very first line of a section is not available here;
    the nesting of a list therefore starts to count from its second line.

    Args:
        content: The raw content of one section.
        groups: The allocator of the numbered list groups of the document.

    Returns:
        The blocks of the section, in the order they appear.
    """
    specs: list[_BlockSpec] = []
    paragraph: list[str] = []
    lines = content.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if _FENCE_PATTERN.match(line) is not None:
            _flush_paragraph(paragraph, specs)
            index, code = _collect_code_block(lines, index)
            specs.append(_BlockSpec(kind=WordBlockKind.CODE, text=code))
            continue
        heading = _match_heading(line)
        if heading is not None:
            _flush_paragraph(paragraph, specs)
            specs.append(heading)
            index += 1
            continue
        item = _match_list_item(line)
        if item is not None:
            _flush_paragraph(paragraph, specs)
            specs.append(_list_block(item, specs, groups))
            index += 1
            continue
        quote = _match_quote(line)
        if quote is not None:
            _flush_paragraph(paragraph, specs)
            specs.append(quote)
            index += 1
            continue
        table = _match_table(lines, index)
        if table is not None:
            _flush_paragraph(paragraph, specs)
            table_spec, next_index = table
            specs.append(table_spec)
            index = next_index
            continue
        if not line.strip():
            _flush_paragraph(paragraph, specs)
            index += 1
            continue
        paragraph.append(line)
        index += 1
    _flush_paragraph(paragraph, specs)
    return specs


def _flush_paragraph(lines: list[str], specs: list[_BlockSpec]) -> None:
    """Emit the prose collected so far as one paragraph block.

    The lines are joined with a newline, so that a soft line break inside a
    paragraph survives the way it does today.
    """
    if not lines:
        return
    specs.append(_BlockSpec(kind=WordBlockKind.PARAGRAPH, text="\n".join(lines)))
    lines.clear()


def _match_heading(line: str) -> _BlockSpec | None:
    """Return the heading a line carries, or ``None`` when it carries none.

    The level of the heading is the number of its ``#`` signs; a heading
    inside a section therefore keeps the level the author wrote.
    """
    match = _HEADING_PATTERN.match(line)
    if match is None:
        return None
    return _BlockSpec(
        kind=WordBlockKind.HEADING,
        text=match.group("text"),
        level=len(match.group("marks")),
    )


def _match_list_item(line: str) -> _ListItem | None:
    """Return the list item a line carries, or ``None`` when it carries none.

    The nesting level of the item is taken from its indentation: two spaces
    of indentation make one level deeper, and the depth is capped at the
    deepest level the model allows.
    """
    bullet = _BULLET_PATTERN.match(line)
    if bullet is not None:
        return _ListItem(
            marker=ListMarker.BULLET,
            text=bullet.group("text"),
            level=_list_level(bullet.group("indent")),
        )
    numbered = _NUMBERED_PATTERN.match(line)
    if numbered is not None:
        return _ListItem(
            marker=ListMarker.NUMBER,
            text=numbered.group("text"),
            level=_list_level(numbered.group("indent")),
            number=int(numbered.group("number")),
        )
    return None


def _match_quote(line: str) -> _BlockSpec | None:
    """Return the block quote a line carries, or ``None`` when it carries none."""
    match = _QUOTE_PATTERN.match(line)
    if match is None:
        return None
    return _BlockSpec(kind=WordBlockKind.QUOTE, text=match.group("text"))


def _match_table(lines: list[str], index: int) -> tuple[_TableSpec, int] | None:
    """Return the table a line starts and the index after it, or ``None``.

    A table is recognised only when a delimiter row follows the line that
    starts it: a line that merely carries a pipe is prose, and so is a header
    row without a delimiter row.  The recognition is deliberately
    conservative, because prose turned into a table loses the author's
    structure, while a table that stays prose merely loses its grid.

    The line that starts the table is its header row, the delimiter row
    separates the header from the body, and the rows that follow the
    delimiter row are the body.  A line that carries no pipe ends the table.
    """
    if not _has_table_separator(lines[index]):
        return None
    if index + 1 >= len(lines) or not _is_delimiter_row(lines[index + 1]):
        return None
    rows: list[list[str]] = [_split_table_cells(lines[index])]
    cursor = index + 2
    while cursor < len(lines) and _has_table_separator(lines[cursor]):
        rows.append(_split_table_cells(lines[cursor]))
        cursor += 1
    spec = _TableSpec(
        kind=WordBlockKind.TABLE,
        text="",
        rows=_rectangular_rows(rows),
    )
    return spec, cursor


def _is_delimiter_row(line: str) -> bool:
    """Report whether a line is the delimiter row of a markdown table.

    A delimiter row is made of cells whose only content is hyphens,
    optionally framed by colons.  A row that carries anything else does not
    separate a header from a body and is therefore not a delimiter row.
    """
    cells = _split_table_cells(line)
    if not cells:
        return False
    return all(_DELIMITER_CELL_PATTERN.match(cell) is not None for cell in cells)


def _split_table_cells(line: str) -> list[str]:
    """Split one table row into its cells.

    The outer pipes are optional, so ``a | b`` and ``| a | b |`` are the same
    row.  An escaped pipe (``\\|``) is a literal pipe inside a cell and never
    separates two cells.  The whitespace around a cell's content is trimmed,
    because the padding of a table is layout and not content.
    """
    stripped = line.strip()
    cells: list[str] = []
    buffer: list[str] = []
    leading_pipe = stripped.startswith("|")
    trailing_pipe = False
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            buffer.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(buffer))
            buffer = []
            trailing_pipe = True
            index += 1
            continue
        buffer.append(char)
        trailing_pipe = False
        index += 1
    cells.append("".join(buffer))
    if leading_pipe and cells and cells[0] == "":
        cells = cells[1:]
    if trailing_pipe and cells and cells[-1] == "":
        cells = cells[:-1]
    if not cells:
        # A row that carries nothing but pipes still is a row: dropping it
        # would take a line of the manuscript with it.
        return [""]
    return [cell.strip() for cell in cells]


def _has_table_separator(line: str) -> bool:
    """Report whether a line carries a pipe that separates two cells.

    A pipe that is escaped belongs to the content of a cell and does not make
    the line a table row, which is why a line of prose may carry a pipe and
    still be prose.
    """
    stripped = line.strip()
    index = 0
    while index < len(stripped):
        if stripped[index] == "\\":
            index += 2
            continue
        if stripped[index] == "|":
            return True
        index += 1
    return False


def _rectangular_rows(rows: list[list[str]]) -> tuple[tuple[str, ...], ...]:
    """Return the rows of a table, padded so that every row is the same width.

    A Word table's grid is rectangular, and its width is the number of cells
    of its widest row: a row with fewer cells is padded on the right with
    empty cells, and a row with more cells widens the table for every row.
    """
    width = max((len(row) for row in rows), default=0)
    return tuple(tuple(row) + ("",) * (width - len(row)) for row in rows)


def _build_table(
    spec: _TableSpec,
    quotes: Mapping[str, str],
    ledger: _CitationLedger,
) -> WordTable:
    """Build the Word table of a markdown table block.

    The cells of the first row are the header row: they are marked as such
    and the table repeats them on every page, because a table that runs over
    a page break without its header cannot be read.

    The cells are parsed with the same inline machinery as a paragraph, so
    that emphasis and citation markers inside a cell behave exactly as they
    do in the prose around the table.  The text of a cell is the normalised
    projection of its runs, which is what the model expects of every piece
    of content it carries.
    """
    rows: list[WordTableRow] = []
    for row_index, cells in enumerate(spec.rows):
        row_cells: list[WordTableCell] = []
        for cell_text in cells:
            runs = _paragraph_runs(cell_text, quotes, ledger)
            row_cells.append(
                WordTableCell(
                    text=normalize_whitespace("".join(run.text for run in runs)),
                    runs=runs,
                    is_header=row_index == 0,
                )
            )
        rows.append(WordTableRow(cells=tuple(row_cells)))
    return WordTable(rows=tuple(rows), header_rows=1 if rows else 0)


def _list_level(indent: str) -> int:
    """Return the nesting level an indented list item asks for."""
    width = len(indent.expandtabs(_TAB_WIDTH))
    return min(width // _LIST_INDENT_WIDTH, _MAX_LIST_LEVEL)


def _list_block(item: _ListItem, specs: list[_BlockSpec], groups: _ListGroups) -> _BlockSpec:
    """Return the block of a list item, with the group its number continues.

    Two numbered items continue one list when nothing else came between them
    and the numbering did not restart at one; anything else starts a new
    group, so that every list numbers from one.
    """
    group: str | None = None
    if item.marker is ListMarker.NUMBER:
        group = _number_group(item.number, specs, groups)
    return _BlockSpec(
        kind=WordBlockKind.LIST_ITEM,
        text=item.text,
        level=item.level,
        list_marker=item.marker,
        list_group=group,
    )


def _number_group(
    number: int | None,
    specs: list[_BlockSpec],
    groups: _ListGroups,
) -> str:
    """Return the continuity group a numbered list item belongs to."""
    previous = specs[-1] if specs else None
    continues = (
        previous is not None
        and previous.kind is WordBlockKind.LIST_ITEM
        and previous.list_marker is ListMarker.NUMBER
        and number != 1
    )
    if continues and previous is not None and previous.list_group is not None:
        return previous.list_group
    return groups.allocate()


def _collect_code_block(lines: list[str], index: int) -> tuple[int, str]:
    """Return the index after a fenced code block and the block's content.

    The fence markers are removed and the content is kept verbatim: a code
    block is code, not prose, and its ``**`` is not emphasis.
    """
    index += 1
    content: list[str] = []
    while index < len(lines) and _FENCE_PATTERN.match(lines[index]) is None:
        content.append(lines[index])
        index += 1
    if index < len(lines):
        index += 1
    return index, "\n".join(content)


def _code_runs(text: str) -> tuple[WordRun, ...]:
    """Return the runs of a code block, whose content is kept verbatim."""
    cleaned = strip_control_characters(text)
    if not cleaned:
        return ()
    return (WordRun(text=cleaned, font=_CODE_FONT),)


def _paragraph_runs(
    text: str,
    quotes: Mapping[str, str],
    ledger: _CitationLedger,
) -> tuple[WordRun, ...]:
    """Build the runs of one paragraph, consuming its citation markers.

    The paragraph is first parsed into pieces that carry their formatting;
    the citation markers are pieces of their own, so that an emphasised span
    may hold a marker without the marker splitting the span.

    Args:
        text: The raw paragraph text, citation markers included.
        quotes: The section's chunk id -> cited passage map.
        ledger: Document-wide numbering and footnote state.

    Returns:
        The runs of the paragraph, in order. Citation markers are replaced by
        superscript numeric runs; the marker text itself never survives.
    """
    pieces = _parse_inline(text)
    runs: list[WordRun] = []
    pending = ""
    for piece in pieces:
        if isinstance(piece, _TextPiece):
            content = pending + piece.text
            pending = ""
            cleaned = strip_control_characters(content)
            if cleaned:
                runs.append(_run_for(piece.formatting, cleaned))
            continue
        if piece.start > 0 and _is_word_char(text[piece.start - 1]):
            _append_space(runs)
        for position, chunk_id in enumerate(piece.chunk_ids):
            if position:
                # Keep two adjacent markers apart so that their numbers are
                # not read as one larger number.
                runs.append(WordRun(text=" "))
            number = ledger.number_for(chunk_id)
            footnote_ref = ledger.footnote_ref_for(chunk_id, quotes.get(chunk_id))
            runs.append(
                _run_for(
                    piece.formatting,
                    str(number),
                    superscript=True,
                    footnote_ref=footnote_ref,
                )
            )
        if piece.end < len(text) and _is_word_char(text[piece.end]):
            pending = " "
    return tuple(runs)


def _run_for(
    formatting: _Formatting,
    text: str,
    *,
    superscript: bool = False,
    footnote_ref: str | None = None,
) -> WordRun:
    """Return a run that carries ``formatting``."""
    return WordRun(
        text=text,
        bold=formatting.bold,
        italic=formatting.italic,
        underline=formatting.underline,
        strike=formatting.strike,
        superscript=superscript,
        font=_CODE_FONT if formatting.code else None,
        highlight=_HIGHLIGHT_COLOR if formatting.highlight else None,
        footnote_ref=footnote_ref,
    )


def _append_space(runs: list[WordRun]) -> None:
    """Append a separating space to the last run, or add a run carrying it."""
    if not runs or runs[-1].superscript:
        runs.append(WordRun(text=" "))
        return
    last = runs[-1]
    runs[-1] = last.model_copy(update={"text": last.text + " "})


def _parse_inline(text: str) -> list[_Piece]:
    """Parse a paragraph's text into the pieces that carry its formatting.

    The citation markers are opaque: they are located first and handed to
    the citation machinery untouched, so that a marker is never split and
    never has emphasis applied to it.
    """
    pieces: list[_Piece] = []
    _parse_inline_range(text, 0, len(text), _Formatting(), _opaque_spans(text), pieces)
    return pieces


def _opaque_spans(text: str) -> dict[int, _OpaqueSpan]:
    """Return the spans the inline formatter must not split, by their start.

    Two kinds of span are opaque: a citation marker, which the citation
    machinery owns, and the editor's ``{{cite:...}}`` marker, which is not a
    citation of the shared grammar and therefore survives as it was written.
    """
    spans: dict[int, _OpaqueSpan] = {}
    for start, end, chunk_ids in _citation_groups(text):
        spans[start] = _OpaqueSpan(start=start, end=end, chunk_ids=tuple(chunk_ids))
    for match in _TEMPLATE_CITATION_PATTERN.finditer(text):
        spans.setdefault(
            match.start(),
            _OpaqueSpan(start=match.start(), end=match.end(), chunk_ids=()),
        )
    return spans


def _parse_inline_range(
    text: str,
    start: int,
    end: int,
    formatting: _Formatting,
    opaque: Mapping[int, _OpaqueSpan],
    pieces: list[_Piece],
) -> None:
    """Parse ``text[start:end]`` into ``pieces`` under ``formatting``."""
    literal: list[str] = []
    index = start
    while index < end:
        span = opaque.get(index)
        if span is not None:
            _flush_literal(literal, formatting, pieces)
            if span.chunk_ids:
                pieces.append(
                    _CitationPiece(
                        chunk_ids=span.chunk_ids,
                        start=span.start,
                        end=span.end,
                        formatting=formatting,
                    )
                )
            else:
                pieces.append(
                    _TextPiece(text=text[span.start : span.end], formatting=formatting)
                )
            index = span.end
            continue
        marker = _marker_at(text, index, end)
        if marker is not None:
            content_start = index + len(marker.text)
            closing = _find_closing(text, marker, content_start, end, opaque)
            if closing is not None:
                _flush_literal(literal, formatting, pieces)
                _parse_inline_range(
                    text,
                    content_start,
                    closing,
                    _merge_formatting(formatting, marker.style),
                    opaque,
                    pieces,
                )
                index = closing + len(marker.text)
                continue
        literal.append(text[index])
        index += 1
    _flush_literal(literal, formatting, pieces)


def _flush_literal(
    literal: list[str],
    formatting: _Formatting,
    pieces: list[_Piece],
) -> None:
    """Emit the text collected so far as one piece."""
    if not literal:
        return
    pieces.append(_TextPiece(text="".join(literal), formatting=formatting))
    literal.clear()


def _merge_formatting(base: _Formatting, extra: _Formatting) -> _Formatting:
    """Return the combination of two formatting sets."""
    return _Formatting(
        bold=base.bold or extra.bold,
        italic=base.italic or extra.italic,
        underline=base.underline or extra.underline,
        strike=base.strike or extra.strike,
        code=base.code or extra.code,
        highlight=base.highlight or extra.highlight,
    )


def _marker_at(text: str, index: int, end: int) -> _InlineMarker | None:
    """Return the inline marker that opens at ``index``, if there is one.

    A marker opens only on a run of its own character of exactly its own
    length, so that ``***`` stays text instead of becoming a bold marker with
    a stray star, and only where the text around it can carry it: the
    character after the marker must not be whitespace, and an underscore may
    not open in the middle of a word, where it belongs to an identifier
    rather than to the formatting.
    """
    character = text[index]
    run = _character_run(text, index, end)
    for marker in _INLINE_MARKERS:
        if marker.text[0] != character or len(marker.text) != run:
            continue
        if not _opens_cleanly(text, index, end, marker):
            continue
        return marker
    return None


def _opens_cleanly(text: str, index: int, end: int, marker: _InlineMarker) -> bool:
    """Report whether a marker may open at ``index``."""
    after = index + len(marker.text)
    if after >= end or text[after].isspace():
        return False
    if marker.text[0] == "_" and index > 0 and text[index - 1].isalnum():
        return False
    return True


def _closes_cleanly(text: str, index: int, end: int, marker: _InlineMarker) -> bool:
    """Report whether a marker may close at ``index``."""
    if index == 0 or text[index - 1].isspace():
        return False
    after = index + len(marker.text)
    if marker.text[0] == "_" and after < end and text[after].isalnum():
        return False
    return True


def _find_closing(
    text: str,
    marker: _InlineMarker,
    content_start: int,
    end: int,
    opaque: Mapping[int, _OpaqueSpan],
) -> int | None:
    """Return the index at which ``marker`` closes, or ``None``.

    The search steps over the opaque spans, so that a marker inside a
    citation marker can never close an emphasised span, and it only accepts a
    closing marker whose run is as long as the opening one.
    """
    index = content_start
    while index < end:
        span = opaque.get(index)
        if span is not None:
            index = span.end
            continue
        if text[index] == marker.text[0]:
            run = _character_run(text, index, end)
            if (
                run == len(marker.text)
                and index > content_start
                and _closes_cleanly(text, index, end, marker)
            ):
                return index
            index += run
            continue
        index += 1
    return None


def _character_run(text: str, index: int, end: int) -> int:
    """Return the length of the run of equal characters starting at ``index``."""
    character = text[index]
    length = 0
    while index + length < end and text[index + length] == character:
        length += 1
    return length


def _citation_groups(text: str) -> list[tuple[int, int, list[str]]]:
    """Group the citation markers of ``text`` by their bracketed span.

    ``iter_citation_spans`` reports one entry per cited chunk id, with the
    span of the whole bracketed marker; a comma list such as ``[#a, #b]``
    therefore yields several entries sharing one span and becomes one group.
    """
    groups: list[tuple[int, int, list[str]]] = []
    for chunk_id, start, end in iter_citation_spans(text):
        if groups and groups[-1][0] == start and groups[-1][1] == end:
            groups[-1][2].append(chunk_id)
            continue
        groups.append((start, end, [chunk_id]))
    return groups


def _is_word_char(char: str) -> bool:
    """Report whether a character belongs to a word.

    Word characters are the ones that must not be glued to a marker that
    consumed the text between them.
    """
    return char.isalnum()


def _format_reference(record: BibliographicRecord) -> str:
    """Render one bibliographic record as a single plain-text line.

    Fields are rendered in the contract's order -- authors, title, container
    title, volume, issue, year, pages, DOI, URL -- and joined by single
    spaces. Absent fields are omitted rather than rendered as a placeholder.
    """
    authors = [author.strip() for author in record.authors if author.strip()]
    parts: list[str] = [", ".join(authors) if authors else _ANONYMOUS_AUTHOR]
    parts.append(record.title.strip())
    for optional in (record.container_title, record.volume, record.issue):
        if optional is not None and optional.strip():
            parts.append(optional.strip())
    if record.year is not None:
        parts.append(str(record.year))
    for optional in (record.pages, record.doi, record.url):
        if optional is not None and optional.strip():
            parts.append(optional.strip())
    return " ".join(part for part in parts if part)


def _make_block(
    block_id: str,
    kind: WordBlockKind,
    runs: Sequence[WordRun],
    *,
    level: int = 0,
    list_marker: ListMarker | None = None,
    list_group: str | None = None,
) -> WordBlock:
    """Build a block whose text is derived from its runs.

    The shared model enforces that a block's text equals the normalised
    concatenation of its runs (contract §3.1 I8); deriving the text here
    keeps the two from drifting apart.
    """
    block_runs = tuple(runs)
    text = normalize_whitespace("".join(run.text for run in block_runs))
    return WordBlock(
        block_id=block_id,
        kind=kind,
        text=text,
        level=level,
        list_marker=list_marker,
        list_group=list_group,
        runs=block_runs,
    )


def _next_block_id(index: int) -> str:
    """Return the deterministic block id for the block at ``index``."""
    return f"m{index + 1:04d}"


__all__ = ["build_manuscript_document"]
