"""Contract clauses that the first pass of the suite did not cover.

Every test in this module exists because an independent audit of the word
processing pass found the clause **uncovered** or the assertion **unable to
fail**. The requirement id is named in each test's docstring so a future
reader can trace a clause to its test and back.

The clauses are grouped by the contract section they come from:
§4 (text rules), §5 (reader), §6 (writer), §7 (manuscript bridge).
"""

from __future__ import annotations

import ast
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from lmeditor.core.exceptions import WordParseError, WordRenderError
from lmeditor.core.schemas import (
    BibliographicRecord,
    CitationLineage,
    Claim,
    DocumentDraft,
    SectionDraft,
)
from lmeditor.core.types import (
    BibliographyResolverKind,
    EntailmentStatus,
    SectionStatus,
)
from lmeditor.word.manuscript import build_manuscript_document
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordProperties,
    WordRun,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter
from tests.word_fixtures import (
    build_blank_row_table_package,
    build_breaks_and_fields_package,
    build_link_target_package,
    build_malformed_document_package,
    build_malformed_styles_package,
    build_merged_table_package,
    build_multi_header_footer_package,
    build_precise_core_properties_package,
    build_style_chain_package,
    build_style_name_and_id_package,
    build_style_numbering_package,
    build_word365_fixture,
)


def _reader() -> WordReader:
    """A fresh reader, so the omission register is not shared."""
    return WordReader()


def _module_path(name: str) -> Path:
    """The file of a module of the package, given a name relative to it."""
    import lmeditor._reference as reference_package

    root = Path(reference_package.__file__).parent
    return (root / name).resolve()


def _public_api(path: Path) -> dict[str, Any]:
    """The public shape of a module, as ``ast`` sees it.

    The shape, not the text: the names the module defines for its callers,
    the signature of each of them, what they return, the exceptions the module
    raises, and the list it exports. A door that answers with the same shape
    answers the callers that existed before.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    names: list[str] = []
    signatures: dict[str, str] = {}
    returns: dict[str, str] = {}
    raises: set[str] = set()
    exported: list[str] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
                signatures[node.name] = ast.unparse(node.args)
                returns[node.name] = ast.unparse(node.returns) if node.returns else ""
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "__all__":
                    exported = [ast.literal_eval(element) for element in node.value.elts]
                elif not target.id.startswith("_"):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.append(node.target.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(raised, ast.Name):
                raises.add(raised.id)
            elif isinstance(raised, ast.Attribute):
                raises.add(raised.attr)

    return {
        "names": sorted(names),
        "all": exported,
        "signatures": signatures,
        "returns": returns,
        "raises": raises,
    }


# ============================================================================
# §5 R4 — table structure: spans, merges and multi-paragraph cells
# ============================================================================


def test_grid_span_becomes_col_span() -> None:
    """R4: a w:gridSpan of 2 makes the cell occupy two columns."""
    document = _reader().read(build_merged_table_package())
    table = document.blocks[0].table
    assert table is not None
    first_row = table.rows[0]
    assert first_row.cells[0].col_span == 1
    assert first_row.cells[1].col_span == 2


def test_two_row_merge_origin_carries_the_span() -> None:
    """R4: the cell that starts a merge carries the span and keeps its text."""
    document = _reader().read(build_merged_table_package())
    table = document.blocks[0].table
    assert table is not None
    origin = table.rows[0].cells[0]
    assert origin.row_span == 2
    assert origin.text == "Two row origin"
    assert origin.is_merge_continuation is False


def test_two_row_merge_continuation_is_flagged() -> None:
    """R4: the cell below a merge origin is a continuation with no text."""
    document = _reader().read(build_merged_table_package())
    table = document.blocks[0].table
    assert table is not None
    continuation = table.rows[1].cells[0]
    assert continuation.is_merge_continuation is True
    assert continuation.row_span == 1
    assert continuation.text == ""


def test_three_row_merge_origin_carries_the_full_span() -> None:
    """R4: a merge covering three rows reports a span of three, not two."""
    document = _reader().read(build_merged_table_package())
    three_row_table = document.blocks[1].table
    assert three_row_table is not None
    origin = three_row_table.rows[0].cells[0]
    assert origin.row_span == 3
    assert origin.text == "Three row origin"


def test_three_row_merge_has_two_continuations() -> None:
    """R4: a merge covering three rows produces two continuation cells."""
    document = _reader().read(build_merged_table_package())
    three_row_table = document.blocks[1].table
    assert three_row_table is not None
    continuations = [
        cell
        for row in three_row_table.rows[1:]
        for cell in row.cells
        if cell.is_merge_continuation
    ]
    assert len(continuations) == 2
    assert all(cell.row_span == 1 for cell in continuations)


def test_a_cell_with_two_paragraphs_joins_with_a_single_space() -> None:
    """R4: a cell's paragraphs are concatenated with one space."""
    document = _reader().read(build_merged_table_package())
    cell_table = document.blocks[2].table
    assert cell_table is not None
    assert (
        cell_table.rows[0].cells[0].text
        == "First paragraph of the cell Second paragraph of the cell"
    )


# ============================================================================
# §5 R5 — hyperlink targets
# ============================================================================


def test_internal_anchor_resolves_to_a_fragment() -> None:
    """R5: a w:anchor with no relationship resolves to '#anchor'."""
    document = _reader().read(build_link_target_package())
    block = next(b for b in document.blocks if b.text == "Internal anchor")
    assert [run.hyperlink for run in block.runs] == ["#section-two"]


def test_absolute_target_is_used_verbatim() -> None:
    """R5: an absolute relationship target is kept as it is."""
    document = _reader().read(build_link_target_package())
    block = next(b for b in document.blocks if b.text == "Absolute target")
    assert [run.hyperlink for run in block.runs] == ["https://example.org/absolute"]


def test_relative_target_is_preserved_and_reported() -> None:
    """R5: without a base, a relative target is kept and reported, not dropped."""
    reader = _reader()
    document = reader.read(build_link_target_package())
    block = next(b for b in document.blocks if b.text == "Relative target")
    assert [run.hyperlink for run in block.runs] == ["docs/page.html"]
    omissions = [o for o in reader.last_omissions if "docs/page.html" in o]
    assert len(omissions) == 1, f"expected exactly one omission, got {reader.last_omissions}"


def test_no_omission_is_recorded_when_a_base_resolves_the_target() -> None:
    """R5: with a base, the target resolves and the register stays empty."""
    reader = _reader()
    reader.read(build_link_target_package(), base_uri="https://example.org/docs/report.html")
    assert reader.last_omissions == []


def test_relative_target_is_resolved_against_a_base_uri() -> None:
    """R5: with a base, a relative target becomes an absolute one."""
    document = _reader().read(
        build_link_target_package(), base_uri="https://example.org/docs/report.html"
    )
    block = next(b for b in document.blocks if b.text == "Relative target")
    assert [run.hyperlink for run in block.runs] == [
        "https://example.org/docs/docs/page.html"
    ]


def test_an_absolute_target_is_not_touched_by_a_base() -> None:
    """R5: a base never rewrites a target that is already absolute."""
    document = _reader().read(
        build_link_target_package(), base_uri="https://example.org/docs/report.html"
    )
    block = next(b for b in document.blocks if b.text == "Absolute target")
    assert [run.hyperlink for run in block.runs] == ["https://example.org/absolute"]


# ============================================================================
# §5 R2 / R3 — style resolution
# ============================================================================


def test_heading_style_inherited_through_a_based_on_chain() -> None:
    """R2: a style with no name of its own inherits its heading level."""
    document = _reader().read(build_style_chain_package())
    heading = document.blocks[0]
    assert heading.kind is WordBlockKind.HEADING
    assert heading.level == 1
    assert heading.text == "Inherited heading"


def test_a_heading_is_recognised_by_its_style_id_or_its_name() -> None:
    """R2: the style id *or* the style name may identify a heading.

    In the main fixture both match, so neither branch is exercised alone.
    Here one heading matches only by id and the other only by name — and
    the name is lower case, which is how Word writes a heading style.
    """
    document = _reader().read(build_style_name_and_id_package())
    by_id = document.blocks[0]
    assert by_id.kind is WordBlockKind.HEADING
    assert by_id.level == 3
    assert by_id.text == "Heading by style id"

    by_name = document.blocks[1]
    assert by_name.kind is WordBlockKind.HEADING
    assert by_name.level == 4
    assert by_name.text == "Heading by style name"


def test_numbering_inherited_from_the_style() -> None:
    """R3: a paragraph with no w:numPr still inherits the style's numbering."""
    document = _reader().read(build_style_numbering_package())
    item = document.blocks[0]
    assert item.kind is WordBlockKind.LIST_ITEM
    assert item.list_marker is ListMarker.BULLET
    assert item.level == 0
    assert item.list_group is not None


# ============================================================================
# §5 R9 — breaks, tabs and field codes
# ============================================================================


def test_soft_break_is_whitespace() -> None:
    """R9: a w:br without a type is whitespace, not a block boundary."""
    document = _reader().read(build_breaks_and_fields_package())
    assert document.blocks[0].text == "Before After"


def test_tab_is_whitespace() -> None:
    """R9: a w:tab is whitespace."""
    document = _reader().read(build_breaks_and_fields_package())
    assert document.blocks[1].text == "Tabbed Columns"


def test_carriage_return_is_whitespace() -> None:
    """R9: a w:cr is whitespace."""
    document = _reader().read(build_breaks_and_fields_package())
    assert document.blocks[2].text == "Carriage Return"


def test_field_instruction_text_is_not_content() -> None:
    """R9: a field's instruction text contributes nothing to the document."""
    document = _reader().read(build_breaks_and_fields_package())
    assert document.blocks[3].text == "Visible"
    assert "MERGEFORMAT" not in document.plain_text()


def test_a_simple_field_contributes_no_text() -> None:
    """R9: a ``w:fldSimple`` contributes no text — not even its cached result.

    Word keeps the last computed value of a field *inside* the element, so
    the element carries visible text.  The contract says a field code
    contributes no text, so that cached result must not reach the
    extracted document.  With an empty field the two behaviours are
    indistinguishable, so the fixture's field carries a result.
    """
    document = _reader().read(build_word365_fixture())
    assert "Cached table of contents" not in document.plain_text()


# ============================================================================
# §5 R10 — header and footer parts
# ============================================================================


def test_headers_are_read_in_part_order() -> None:
    """R10: several header parts are read in natural part-name order.

    This is the *fallback*: a document that names its headers through section
    references is matched by reference type instead (see G8 in
    ``tests/test_word_gaps.py``), because the part name says nothing about
    the role a header plays.  This package carries no reference at all, so
    part-name order is all the information it offers.
    """
    document = _reader().read(build_multi_header_footer_package())
    assert document.headers == ("Header one", "Header two", "Header three")


def test_footers_are_read_in_part_order() -> None:
    """R10: several footer parts are read in natural part-name order.

    As with the headers, this is the fallback for a package that carries no
    section reference.
    """
    document = _reader().read(build_multi_header_footer_package())
    assert document.footers == ("Footer one", "Footer two")


# ============================================================================
# §5 R11 — exact core property values
# ============================================================================


def test_core_properties_carry_their_exact_values() -> None:
    """R11: the core properties part maps onto the model field by field."""
    document = _reader().read(build_precise_core_properties_package())
    properties = document.properties
    assert properties.title == "Precise Title"
    assert properties.author == "Precise Author"
    assert properties.subject == "Precise Subject"
    assert properties.keywords == "one two"
    assert properties.comments == "Precise description"
    assert properties.revision == 11


def test_core_timestamps_are_the_exact_utc_instants() -> None:
    """R11: the timestamps are the exact instants, expressed in UTC."""
    document = _reader().read(build_precise_core_properties_package())
    assert document.properties.created == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc
    )
    assert document.properties.modified == datetime(
        2026, 6, 7, 8, 9, 10, tzinfo=timezone.utc
    )


# ============================================================================
# §5 R13 — malformed parts
# ============================================================================


def test_malformed_document_xml_is_refused() -> None:
    """R13: a malformed word/document.xml is refused with a typed error."""
    with pytest.raises(WordParseError):
        _reader().read(build_malformed_document_package())


def test_malformed_styles_part_is_refused() -> None:
    """R13: a malformed secondary part is refused, not ignored."""
    with pytest.raises(WordParseError):
        _reader().read(build_malformed_styles_package())


# ============================================================================
# §4 T7 — one line per table row
# ============================================================================


def test_a_table_renders_one_line_per_row_including_blank_rows() -> None:
    """T7: a three-row table renders three lines, even with a blank row."""
    document = _reader().read(build_blank_row_table_package())
    rendered = document.plain_text()
    assert rendered.count("\n") + 1 == 3
    assert rendered.split("\n")[0] == "alpha"
    assert rendered.split("\n")[1] == ""
    assert rendered.split("\n")[2] == "omega"


# ============================================================================
# §6 W2 / W3 / W5 / W9 / W10 — writer
# ============================================================================


def _writer_document() -> WordDocument:
    """A document with a title, headings, runs, two ordered groups and a table."""
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="b1",
                kind=WordBlockKind.TITLE,
                text="A Grounded Manuscript",
                runs=(WordRun(text="A Grounded Manuscript"),),
            ),
            WordBlock(
                block_id="b2",
                kind=WordBlockKind.HEADING,
                text="Grounding Section",
                level=1,
                runs=(WordRun(text="Grounding Section"),),
            ),
            WordBlock(
                block_id="b3",
                kind=WordBlockKind.PARAGRAPH,
                text="plain bold plain",
                runs=(
                    WordRun(text="plain "),
                    WordRun(text="bold", bold=True),
                    WordRun(text=" plain"),
                ),
            ),
            WordBlock(
                block_id="b4",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered one",
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text="Numbered one"),),
            ),
            WordBlock(
                block_id="b5",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered two",
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text="Numbered two"),),
            ),
            WordBlock(
                block_id="b6",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered one again",
                list_marker=ListMarker.NUMBER,
                list_group="g2",
                level=0,
                runs=(WordRun(text="Numbered one again"),),
            ),
            WordBlock(
                block_id="b7",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Evidence", is_header=True),
                                WordTableCell(text="Verdict", is_header=True),
                            )
                        ),
                        WordTableRow(
                            cells=(
                                WordTableCell(text="chunk-abc"),
                                WordTableCell(text="entailed"),
                            )
                        ),
                    ),
                    header_rows=1,
                ),
            ),
        ),
        properties=WordProperties(title="A Grounded Manuscript"),
    )


def _document_xml(payload: bytes) -> str:
    """The package's word/document.xml as text."""
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _numbering_xml(payload: bytes) -> str:
    """The package's word/numbering.xml as text."""
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return archive.read("word/numbering.xml").decode("utf-8")


def test_title_style_is_emitted() -> None:
    """W2: the title block emits the real Title style."""
    payload = WordWriter().write(_writer_document())
    assert 'w:pStyle w:val="Title"' in _document_xml(payload)


def test_bold_lands_on_the_intended_run_only() -> None:
    """W5: bold is applied to the bold run, not to every run of the block."""
    payload = WordWriter().write(_writer_document())
    xml = _document_xml(payload)
    paragraph = next(chunk for chunk in xml.split("<w:p>") if "bold" in chunk)
    runs = paragraph.split("<w:r>")[1:]
    assert len(runs) == 3
    assert sum(1 for run in runs if "<w:b/>" in run) == 1
    assert "<w:b/>" in runs[1]


def test_a_new_numbering_group_restarts_at_one() -> None:
    """W3: a new list group restarts the sequence, so it carries a start override."""
    payload = WordWriter().write(_writer_document())
    numbering = _numbering_xml(payload)
    assert "startOverride" in numbering
    assert 'w:startOverride w:val="1"' in numbering


def test_continuing_and_new_groups_use_different_numbering_ids() -> None:
    """W3: items of one group share a numbering id; a new group gets its own."""
    payload = WordWriter().write(_writer_document())
    xml = _document_xml(payload)
    num_ids: dict[str, str] = {}
    for chunk in xml.split("<w:p>")[1:]:
        text = "".join(part.split("</w:t>")[0] for part in chunk.split("<w:t>")[1:])
        if "numId" not in chunk:
            continue
        num_ids[text] = chunk.split('w:numId w:val="')[1].split('"')[0]
    assert num_ids["Numbered one"] == num_ids["Numbered two"]
    assert num_ids["Numbered one"] != num_ids["Numbered one again"]


def test_the_deterministic_writer_pins_the_epoch() -> None:
    """W9: deterministic output carries the pinned timestamp, not the wall clock."""
    payload = WordWriter(deterministic=True).write(_writer_document())
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        timestamps = {info.date_time for info in archive.infolist()}
    assert timestamps == {(1980, 1, 1, 0, 0, 0)}


def test_the_non_deterministic_writer_uses_the_current_time() -> None:
    """W9: deterministic=False stamps the package with the current time."""
    payload = WordWriter(deterministic=False).write(_writer_document())
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        timestamps = {info.date_time for info in archive.infolist()}
    assert (1980, 1, 1, 0, 0, 0) not in timestamps
    newest = max(timestamps)
    assert newest[0] >= 2020


def test_an_invalid_model_is_refused_by_the_writer() -> None:
    """W10: a TABLE block carrying text is a rendering error, not a silent drop."""
    illegal = WordBlock.model_construct(
        block_id="t1",
        kind=WordBlockKind.TABLE,
        text="ILLEGAL TEXT",
        table=WordTable(),
    )
    document = WordDocument.model_construct(
        blocks=(illegal,),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)


def test_a_model_whose_text_disagrees_with_its_runs_is_refused() -> None:
    """W10: text that disagrees with the runs is a rendering error."""
    illegal = WordBlock.model_construct(
        block_id="p1",
        kind=WordBlockKind.PARAGRAPH,
        text="ONE TEXT",
        runs=(WordRun(text="ANOTHER TEXT"),),
    )
    document = WordDocument.model_construct(
        blocks=(illegal,),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)


def test_a_merge_continuation_carrying_text_is_refused_by_the_writer() -> None:
    """W10: the writer re-checks the merge rule, not only the model.

    The model refuses a continuation cell that carries text at
    construction time, so this state can only be reached by building the
    model around its own validation.  The writer re-validates what it was
    handed, so it refuses the model too rather than dropping the text on
    the floor — a continuation cell's text is never rendered by Word, so
    accepting it would lose content silently.
    """
    illegal_cell = WordTableCell.model_construct(
        text="LEAKED",
        row_span=1,
        col_span=1,
        is_header=False,
        is_merge_continuation=True,
    )
    illegal_block = WordBlock.model_construct(
        block_id="t1",
        kind=WordBlockKind.TABLE,
        text="",
        runs=(),
        table=WordTable.model_construct(
            rows=(WordTableRow.model_construct(cells=(illegal_cell,)),),
            header_rows=0,
        ),
    )
    document = WordDocument.model_construct(
        blocks=(illegal_block,),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)


def test_duplicate_block_ids_are_refused_by_the_writer() -> None:
    """W10: a document with two blocks sharing an id is a rendering error."""
    block = WordBlock(
        block_id="same",
        kind=WordBlockKind.PARAGRAPH,
        text="Body.",
    )
    document = WordDocument.model_construct(
        blocks=(block, block),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)


def test_an_unknown_footnote_reference_is_refused_by_the_writer() -> None:
    """W10: a run pointing at a footnote that does not exist is a rendering error."""
    block = WordBlock.model_construct(
        block_id="p1",
        kind=WordBlockKind.PARAGRAPH,
        text="Claim",
        runs=(WordRun(text="Claim", footnote_ref="missing"),),
    )
    document = WordDocument.model_construct(
        blocks=(block,),
        properties=WordProperties(),
        footnotes=(),
        headers=(),
        footers=(),
    )
    with pytest.raises(WordRenderError):
        WordWriter().write(document)


# ============================================================================
# §7 M2 / M6 — manuscript bridge
# ============================================================================


def test_is_verified_keeps_a_section_whose_status_is_not_verified() -> None:
    """M2: the verification filter accepts is_verified even for a PENDING status."""
    section = SectionDraft(
        section_id="s1",
        title="Manually verified",
        content="Body.",
        order=0,
        status=SectionStatus.PENDING,
        is_verified=True,
    )
    draft = DocumentDraft(
        document_id="d1", title="T", sections=[section]
    )
    document = build_manuscript_document(draft, verified_only=True)
    headings = [b for b in document.blocks if b.kind is WordBlockKind.HEADING]
    assert [h.text for h in headings] == ["Manually verified"]


def test_the_bibliography_is_not_mutated() -> None:
    """M6: the bridge reads the bibliography and never rewrites it."""
    record = BibliographicRecord(
        title="A Grounded Paper",
        authors=["Ada Lovelace"],
        year=1843,
    )
    bibliography = [record]
    section = SectionDraft(
        section_id="s1", title="Section", content="Body.", order=0
    )
    draft = DocumentDraft(document_id="d1", title="T", sections=[section])
    build_manuscript_document(draft, bibliography)
    assert bibliography == [record]
    assert record.title == "A Grounded Paper"
    assert record.authors == ["Ada Lovelace"]
    assert record.year == 1843


# ============================================================================
# §7 — the manuscript bridge is a door
# ============================================================================
#
# The bridge used to be a Python implementation of the manuscript rules; the
# rules are the Kotlin tier's now (``kotlin/word/WordManuscript.kt``). Three
# things have to hold for that to be a door and not a hole:
#
# * the **core wire form** travels: the draft and the bibliography cross the
#   seam in the form ``kotlin/core/WireDecode.kt`` reads;
# * the **seam answers with the document**, so the door can rebuild the model
#   instead of re-implementing the rules; and
# * the **public API** is unchanged, so the callers that existed before still
#   work without touching them.


def test_the_core_draft_wire_form_is_the_one_the_tier_reads() -> None:
    """The draft crosses the seam in the keys the tier's decoder reads.

    The literal below is the same one ``WordManuscriptWireTest`` pins on the
    Kotlin side, so the two halves of the contract cannot drift apart without
    one of the two tests failing.
    """
    lineage = CitationLineage(
        lineage_id="lin-1",
        claim_id="claim-1",
        chunk_id="chunk-1",
        span_start=0,
        span_end=20,
        exact_quote="The cited passage.",
        confidence_score=0.75,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    claim = Claim(
        claim_id="claim-1",
        section_id="sec-1",
        text="A cited fact.",
        char_start=0,
        char_end=14,
        cited_chunk_ids=["chunk-1"],
        entailment_status=EntailmentStatus.ENTAILED,
        entailment_score=0.9,
        reasoning="The passage says so.",
        sentence_idx=1,
        premise_text="A premise.",
    )
    section = SectionDraft(
        section_id="sec-1",
        title="First",
        content="Some prose.",
        order=3,
        citations=[lineage],
        claims=[claim],
        is_verified=True,
        status=SectionStatus.VERIFIED,
        revision_count=2,
        critique="Needs more.",
        cited_chunk_ids=["chunk-1"],
    )
    draft = DocumentDraft(document_id="doc-1", title="The Manuscript", sections=[section])

    wire = draft.to_wire()

    assert list(wire) == ["document_id", "title", "sections", "status"]
    assert wire["document_id"] == "doc-1"
    assert wire["title"] == "The Manuscript"
    assert wire["status"] == "drafting"

    section_wire = wire["sections"][0]
    assert list(section_wire) == [
        "section_id",
        "title",
        "content",
        "order",
        "citations",
        "claims",
        "is_verified",
        "status",
        "revision_count",
        "critique",
        "cited_chunk_ids",
    ]
    assert section_wire["status"] == "verified"
    assert section_wire["is_verified"] is True
    assert section_wire["revision_count"] == 2
    assert section_wire["critique"] == "Needs more."
    assert section_wire["cited_chunk_ids"] == ["chunk-1"]

    assert list(section_wire["citations"][0]) == [
        "lineage_id",
        "claim_id",
        "chunk_id",
        "span_start",
        "span_end",
        "exact_quote",
        "confidence_score",
        "created_at",
    ]
    assert section_wire["citations"][0]["created_at"] == "2024-01-01T00:00:00+00:00"

    assert list(section_wire["claims"][0]) == [
        "claim_id",
        "section_id",
        "text",
        "char_start",
        "char_end",
        "cited_chunk_ids",
        "entailment_status",
        "entailment_score",
        "reasoning",
        "sentence_idx",
        "premise_text",
    ]
    assert section_wire["claims"][0]["entailment_status"] == "entailed"


def test_the_core_bibliography_wire_form_is_the_one_the_tier_reads() -> None:
    """The record crosses the seam in the keys the tier's decoder reads."""
    record = BibliographicRecord(
        title="Reef census findings",
        authors=["Ada Lovelace", "Charles Babbage"],
        year=1843,
        publication_date="1843-01-01",
        container_title="The Journal",
        volume="12",
        issue="3",
        pages="100-120",
        doi="10.1234/reef.5678",
        url="https://example.org/reef",
        type="journal-article",
        resolver=BibliographyResolverKind.CROSSREF,
    )

    wire = record.to_wire()

    assert list(wire) == [
        "title",
        "authors",
        "year",
        "publication_date",
        "container_title",
        "volume",
        "issue",
        "pages",
        "doi",
        "url",
        "type",
        "resolver",
    ]
    assert wire["resolver"] == "crossref"
    assert wire["authors"] == ["Ada Lovelace", "Charles Babbage"]


def test_an_optional_record_field_travels_as_null_and_not_as_a_missing_key() -> None:
    """An absent optional field is ``null``, which the decoder reads as absent."""
    wire = BibliographicRecord(title="Only a title").to_wire()

    assert wire["year"] is None
    assert wire["doi"] is None
    assert wire["container_title"] is None
    assert wire["resolver"] == "none"
    assert list(wire) == [
        "title",
        "authors",
        "year",
        "publication_date",
        "container_title",
        "volume",
        "issue",
        "pages",
        "doi",
        "url",
        "type",
        "resolver",
    ]


def test_the_enum_aliases_travel_as_their_wire_string() -> None:
    """An alias is an alias, not an entry: both travel as the same wire string.

    ``SectionStatus.DRAFTED`` is ``SectionStatus.DRAFTING`` under a second
    name, and the tier only knows the wire string.
    """
    assert SectionStatus.DRAFTED is SectionStatus.DRAFTING
    assert SectionStatus.FLAGGED is SectionStatus.NEEDS_REVISION
    assert EntailmentStatus.ENTAILMENT is EntailmentStatus.ENTAILED
    assert EntailmentStatus.CONTRADICTION is EntailmentStatus.CONTRADICTED

    section = SectionDraft(
        section_id="s1",
        title="T",
        content="Body.",
        order=0,
        status=SectionStatus.DRAFTED,
    )
    assert section.to_wire()["status"] == "drafting"

    claim = Claim(
        claim_id="c1",
        section_id="s1",
        text="T.",
        char_start=0,
        char_end=2,
        entailment_status=EntailmentStatus.ENTAILMENT,
    )
    assert claim.to_wire()["entailment_status"] == "entailed"


@pytest.mark.usefixtures("kotlin_tier")
def test_the_manuscript_seam_answers_with_the_document_and_the_bytes() -> None:
    """The seam answers with both halves: the document and its package."""
    from lmeditor.bridge import KotlinBridge

    draft = DocumentDraft(
        document_id="doc-1",
        title="The Manuscript",
        sections=[
            SectionDraft(section_id="s1", title="First", content="A cited fact.", order=0)
        ],
    )

    result = KotlinBridge().call(
        "word.manuscript",
        {"draft": draft.to_wire(), "bibliography": [], "verified_only": False},
    )

    assert set(result) == {"bytes_b64", "document"}
    document = result["document"]
    assert isinstance(document, dict)
    assert document["properties"]["title"] == "The Manuscript"
    assert isinstance(result["bytes_b64"], str)
    assert result["bytes_b64"]


@pytest.mark.usefixtures("kotlin_tier")
def test_the_door_answers_with_the_tiers_document() -> None:
    """The document the door returns is the document the tier built.

    The door must not answer with an answer of its own: a document that
    differs from the tier's is a second implementation, which is what this
    wave retires.
    """
    from lmeditor.tier import word as tier_word

    draft = DocumentDraft(
        document_id="doc-1",
        title="The Manuscript",
        sections=[
            SectionDraft(section_id="s1", title="First", content="A cited fact.", order=0)
        ],
    )

    door = build_manuscript_document(draft)
    tier = tier_word.build_manuscript(draft).document

    assert door == tier
    assert door.properties.title == "The Manuscript"


def test_the_door_keeps_the_public_api_of_the_frozen_original() -> None:
    """The door answers with the same names, the same signature and the same errors.

    The frozen original is the yardstick: the door may change its inside, but
    a caller that worked before must work now.  The comparison is made with
    ``ast``, so it sees the shape of the API and not the text of it.
    """
    original = _public_api(_module_path("word_manuscript.py"))
    door = _public_api(_module_path("../word/manuscript.py"))

    assert door["names"] == original["names"]
    assert door["all"] == original["all"] == ["build_manuscript_document"]
    assert door["signatures"] == original["signatures"]
    assert door["returns"] == original["returns"]
    assert door["raises"] == original["raises"] == {"WordProcessingError"}


def test_the_door_refuses_what_the_original_refused() -> None:
    """A bad argument is refused at the boundary, with the original's error."""
    from lmeditor.core.exceptions import WordProcessingError

    draft = DocumentDraft(document_id="doc-1", title="T")

    with pytest.raises(WordProcessingError):
        build_manuscript_document("not a draft")  # type: ignore[arg-type]

    with pytest.raises(WordProcessingError):
        build_manuscript_document(draft, ["not a record"])  # type: ignore[list-item]


# ============================================================================
# Audit gaps G1-G13 — what this pass closed
# ============================================================================
#
# An independent audit of the word-processing pass found thirteen constructs
# the subsystem lost, dropped or mis-mapped.  Each one is now closed, and
# each closure has a test of its own in ``tests/test_word_gaps.py``.  The
# table below is the index: the gap, what it was, and where the proof lives.
#
#   G1  comments were absent end to end
#       -> the model carries ``WordComment``, the reader parses
#          ``word/comments.xml`` and the anchors, the writer emits the part,
#          its relationship and the in-document anchors.
#          Proof: test_word_gaps.py::test_a_comment_survives_a_round_trip
#   G2  tracked changes were destroyed on read and never re-emitted
#       -> ``WordRun.revision`` carries the revision; a deleted run keeps its
#          text and is excluded from the document's *final* text; the writer
#          emits ``w:ins``/``w:del``.
#          Proof: test_word_gaps.py::test_a_deletion_survives_a_round_trip
#   G3  endnotes were absent
#       -> ``WordEndnote`` mirrors the footnote support on both sides.
#          Proof: test_word_gaps.py::test_an_endnote_survives_a_round_trip
#   G4  section properties were skipped
#       -> ``WordSection`` carries page size, orientation, margins and the
#          header/footer references; the writer emits them.
#          Proof: test_word_gaps.py::test_the_page_setup_is_written_out
#   G5  run formatting was lost beyond bold, italic, underline, superscript
#       -> font, size, colour, strike, highlight and subscript are on the
#          model and survive the round trip.
#          Proof: test_word_gaps.py::test_the_colour_and_the_highlight_survive_a_round_trip
#   G6  custom styles were silently dropped
#       -> the reader carries the style definitions the document uses; the
#          writer defines a style the template lacks, and reports one it
#          cannot represent.
#          Proof: test_word_gaps.py::test_a_custom_style_survives_a_round_trip
#   G7  the numbering format was hardcoded
#       -> the reader carries ``numFmt`` and ``lvlText`` per level; the
#          writer builds the abstract numbering from them.
#          Proof: test_word_gaps.py::test_the_number_format_survives_a_round_trip
#   G8  headers and footers were matched by part order
#       -> they are matched by section reference type; part-name order stays
#          as the fallback for a document with no reference.
#          Proof: test_word_gaps.py::test_the_headers_keep_their_roles_after_a_round_trip
#   G9  there was no zip-bomb or size defence
#       -> the reader refuses a package with too many members, too large a
#          member, too large a total, or too high a compression ratio.
#          Proof: test_word_gaps.py::test_a_compressed_bomb_is_refused
#   G10 a legacy .doc was misrouted to the OOXML reader
#       -> ``lmeditor.word.formats`` recognises the OLE2 magic number and the
#          reader refuses it with a reason the operator can act on.
#          Proof: test_word_gaps.py::test_a_legacy_document_is_refused_with_an_actionable_reason
#   G11 tables lost their content structure
#       -> a cell carries its runs and its nested tables; a table carries its
#          column widths.
#          Proof: test_word_gaps.py::test_a_nested_table_survives_a_round_trip
#   G12 images were reported as opaque omissions
#       -> the media part travels in the model and is written back out; an
#          image the reader cannot reach is reported with its relationship id
#          and its part name.
#          Proof: test_word_gaps.py::test_an_image_the_reader_cannot_reach_is_named
#   G13 the omission register was the reader's alone
#       -> the writer has a register of its own, reset per render.
#          Proof: test_word_gaps.py::test_the_writer_reports_a_style_it_cannot_represent
#
# The two changes that belong to the HTTP layer rather than to this package
# are named in the handover report: the upload size cap in
# ``server/routes_helpers.py`` and the ``.doc`` rejection in
# ``server/routes.py``.
