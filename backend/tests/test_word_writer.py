"""Whitebox tests for the Word writer.

The writer is tested at the OOXML level, not through ``python-docx``'s own
accessors alone: the point is that the emitted package carries real heading
styles, real numbering, real footnote parts and real hyperlink relationships,
because that is what Word actually reads.

Expectations are written against the contract
(``WORD_PROCESSING_CONTRACT.md`` §6, requirements W1-W10).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any

import pytest

from lmeditor.core.exceptions import WordRenderError
from lmeditor.word.model import (
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordDocument,
    WordFootnote,
    WordRun,
    WordTable,
    WordTableCell,
    WordTableRow,
)
from lmeditor.word.writer import WordWriter

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _sample_document() -> WordDocument:
    """A document exercising every block kind the writer supports."""
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
                kind=WordBlockKind.HEADING,
                text="Nested Subsection",
                level=2,
                runs=(WordRun(text="Nested Subsection"),),
            ),
            WordBlock(
                block_id="b4",
                kind=WordBlockKind.PARAGRAPH,
                text="A grounded claim with emphasis and a source1",
                runs=(
                    WordRun(text="A grounded claim "),
                    WordRun(text="with emphasis", bold=True, italic=True),
                    WordRun(text=" and a source", underline=True),
                    WordRun(text="1", superscript=True, footnote_ref="f1"),
                ),
            ),
            WordBlock(
                block_id="b5",
                kind=WordBlockKind.LIST_ITEM,
                text="First bullet",
                list_marker=ListMarker.BULLET,
                level=0,
                runs=(WordRun(text="First bullet"),),
            ),
            WordBlock(
                block_id="b6",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered one",
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text="Numbered one"),),
            ),
            WordBlock(
                block_id="b7",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered two",
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                level=0,
                runs=(WordRun(text="Numbered two"),),
            ),
            WordBlock(
                block_id="b8",
                kind=WordBlockKind.LIST_ITEM,
                text="Numbered one again",
                list_marker=ListMarker.NUMBER,
                list_group="g2",
                level=0,
                runs=(WordRun(text="Numbered one again"),),
            ),
            WordBlock(
                block_id="b9",
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
            WordBlock(
                block_id="b10",
                kind=WordBlockKind.PAGE_BREAK,
            ),
            WordBlock(
                block_id="b11",
                kind=WordBlockKind.PARAGRAPH,
                text="See the source",
                runs=(
                    WordRun(
                        text="See the source",
                        hyperlink="https://example.org/source",
                    ),
                ),
            ),
        ),
        footnotes=(WordFootnote(footnote_id="f1", text="the cited passage"),),
        properties=WordDocument(
            blocks=()
        ).properties.model_copy(
            update={
                "title": "A Grounded Manuscript",
                "author": "LMeditor",
                "subject": "Word processing",
                "keywords": "grounded",
                "comments": "written by the test suite",
                "revision": 7,
            }
        ),
    )


def _parts(payload: bytes) -> dict[str, bytes]:
    """Return the package's parts by name."""
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _document_xml(payload: bytes) -> str:
    """Return the package's ``word/document.xml`` as text."""
    return _parts(payload)["word/document.xml"].decode("utf-8")


# ============================================================================
# W1 - a valid package
# ============================================================================


def test_output_is_a_zip_package() -> None:
    """W1: the writer emits an OPC package."""
    payload = WordWriter().write(_sample_document())
    assert payload[:2] == b"PK"


def test_output_has_the_expected_parts() -> None:
    """W1: the package carries the parts Word requires."""
    parts = _parts(WordWriter().write(_sample_document()))
    assert "[Content_Types].xml" in parts
    assert "word/document.xml" in parts


def test_output_is_reopenable_by_python_docx() -> None:
    """W1: the package opens again, so it is structurally sound."""
    docx = pytest.importorskip("docx")
    payload = WordWriter().write(_sample_document())
    reopened = docx.Document(BytesIO(payload))
    assert reopened.paragraphs


def test_every_emitted_xml_part_is_well_formed() -> None:
    """W1: the parts are valid XML, not merely present.

    The closest whitebox proxy for "Word opens it without a repair
    prompt" is that every XML part *parses* and that the package carries
    the parts an OPC reader expects.  A part that is present but not
    well-formed is exactly what makes Word offer to repair a file, and
    python-docx will not always notice, because it reads the parts it
    needs and ignores the rest.
    """
    parts = _parts(WordWriter().write(_sample_document()))
    for name in (
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/_rels/document.xml.rels",
        "word/styles.xml",
    ):
        assert name in parts, f"the package must carry {name}"
    for name, payload in parts.items():
        if name.endswith(".xml") or name.endswith(".rels"):
            ElementTree.fromstring(payload)


# ============================================================================
# W2 - headings
# ============================================================================


def test_headings_use_the_real_heading_styles() -> None:
    """W2: a heading is a real Heading N style, so Word's navigation works."""
    docx = pytest.importorskip("docx")
    payload = WordWriter().write(_sample_document())
    reopened = docx.Document(BytesIO(payload))
    styles = [paragraph.style.name for paragraph in reopened.paragraphs]
    assert "Title" in styles
    assert "Heading 1" in styles
    assert "Heading 2" in styles


def test_heading_levels_reach_the_style_definition() -> None:
    """W2: the heading level is carried by the style, not by bold text."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert "Heading1" in xml
    assert "Heading2" in xml


# ============================================================================
# W3 - lists
# ============================================================================


def test_numbering_part_is_created() -> None:
    """W3: real numbering is emitted, not a fake bullet character."""
    parts = _parts(WordWriter().write(_sample_document()))
    assert "word/numbering.xml" in parts
    assert b"abstractNum" in parts["word/numbering.xml"]


def test_list_items_reference_a_numbering_id() -> None:
    """W3: a list paragraph carries a numPr with an ilvl and a numId."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert "numPr" in xml
    assert "ilvl" in xml


def test_a_nested_list_item_writes_its_level_as_ilvl() -> None:
    """W3: the nesting level is written as ``w:ilvl``.

    A nested list item must stay nested in Word.  Writing every item at
    level 0 would flatten the outline while the text still looked right,
    so the level has to be read back out of the emitted ``w:ilvl`` — not
    merely be present in the file.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="n1",
                kind=WordBlockKind.LIST_ITEM,
                text="Top level",
                list_marker=ListMarker.BULLET,
                level=0,
                runs=(WordRun(text="Top level"),),
            ),
            WordBlock(
                block_id="n2",
                kind=WordBlockKind.LIST_ITEM,
                text="Nested item",
                list_marker=ListMarker.BULLET,
                level=2,
                runs=(WordRun(text="Nested item"),),
            ),
        )
    )
    xml = _document_xml(WordWriter().write(document))

    top = next(chunk for chunk in xml.split("<w:p>") if "Top level" in chunk)
    nested = next(chunk for chunk in xml.split("<w:p>") if "Nested item" in chunk)
    assert _ilvl_of(top) == "0"
    assert _ilvl_of(nested) == "2"


def _ilvl_of(paragraph_xml: str) -> str:
    """The ``w:ilvl`` value carried by one paragraph's ``w:numPr``."""
    match = re.search(r'<w:ilvl w:val="(\d+)"', paragraph_xml)
    assert match is not None, f"no w:ilvl in {paragraph_xml!r}"
    return match.group(1)


def test_a_shared_numbering_group_uses_one_numbering_id() -> None:
    """W3: consecutive items of one ordered list share a numbering id."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    body = xml.split("<w:body>")[-1]
    groups: dict[str, list[str]] = {}
    for chunk in body.split("<w:p>")[1:]:
        text = "".join(
            part.split("</w:t>")[0]
            for part in chunk.split("<w:t>")[1:]
        )
        if "numId" not in chunk:
            continue
        num_id = chunk.split('w:numId w:val="')[1].split('"')[0]
        groups.setdefault(text, []).append(num_id)
    assert groups.get("Numbered one") == groups.get("Numbered two")
    assert groups.get("Numbered one") != groups.get("Numbered one again")


# ============================================================================
# W4 - tables
# ============================================================================


def test_table_is_emitted_with_its_rows_and_cells() -> None:
    """W4: the table survives as a real table."""
    docx = pytest.importorskip("docx")
    payload = WordWriter().write(_sample_document())
    reopened = docx.Document(BytesIO(payload))
    assert len(reopened.tables) == 1
    table = reopened.tables[0]
    assert len(table.rows) == 2
    assert len(table.columns) == 2
    assert table.cell(0, 0).text == "Evidence"
    assert table.cell(1, 1).text == "entailed"


def test_header_row_is_flagged_for_repetition() -> None:
    """W4: the header row repeats across pages, as Word does."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert "tblHeader" in xml


# ============================================================================
# W5 - run formatting
# ============================================================================


@pytest.mark.parametrize("marker", ["<w:b/>", "<w:i/>", "<w:u ", "superscript"])
def test_run_formatting_reaches_the_xml(marker: str) -> None:
    """W5: bold, italic, underline and superscript are emitted."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert marker in xml


def test_bold_lands_on_the_intended_run_only() -> None:
    """W5: bold is applied to the bold run, not to every run of the block."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    paragraph = next(
        chunk for chunk in xml.split("<w:p>") if "with emphasis" in chunk
    )
    runs = paragraph.split("<w:r>")[1:]
    assert len(runs) >= 3
    assert sum(1 for run in runs if "<w:b/>" in run) == 1
    assert "<w:b/>" in runs[1]


# ============================================================================
# W6 - hyperlinks
# ============================================================================


def test_hyperlink_relationship_is_external() -> None:
    """W6: the hyperlink is a relationship with TargetMode External."""
    parts = _parts(WordWriter().write(_sample_document()))
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8")
    assert "https://example.org/source" in rels
    assert 'TargetMode="External"' in rels


def test_the_hyperlink_relationship_has_the_hyperlink_type() -> None:
    """W6: the relationship is a *hyperlink* relationship.

    The target and the external mode are not enough on their own: a
    relationship of another type (an image, say) pointing at the same URL
    is a different thing in OPC, and Word resolves the two differently.
    """
    parts = _parts(WordWriter().write(_sample_document()))
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8")
    assert (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
        in rels
    )
    assert "relationships/image" not in rels


def test_hyperlink_element_is_emitted() -> None:
    """W6: the run is wrapped in a hyperlink element."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert "w:hyperlink" in xml


# ============================================================================
# W7 - footnotes
# ============================================================================


def test_footnotes_part_is_created() -> None:
    """W7: footnotes live in their own part."""
    parts = _parts(WordWriter().write(_sample_document()))
    assert "word/footnotes.xml" in parts
    assert b"the cited passage" in parts["word/footnotes.xml"]


def test_footnote_reference_is_emitted() -> None:
    """W7: the run carries a footnote reference."""
    xml = _document_xml(WordWriter().write(_sample_document()))
    assert "footnoteReference" in xml


def test_footnotes_part_is_declared_in_the_content_types() -> None:
    """W7: the footnotes part is declared, or Word refuses the package."""
    parts = _parts(WordWriter().write(_sample_document()))
    content_types = parts["[Content_Types].xml"].decode("utf-8")
    assert "/word/footnotes.xml" in content_types


def test_footnotes_part_is_related_from_the_document() -> None:
    """W7: the document part points at the footnotes part."""
    parts = _parts(WordWriter().write(_sample_document()))
    rels = parts["word/_rels/document.xml.rels"].decode("utf-8")
    assert "footnotes.xml" in rels


# ============================================================================
# W8 - core properties
# ============================================================================


def test_core_properties_are_written() -> None:
    """W8: the document properties reach docProps/core.xml."""
    parts = _parts(WordWriter().write(_sample_document()))
    core = parts["docProps/core.xml"].decode("utf-8")
    assert "A Grounded Manuscript" in core
    assert "LMeditor" in core


def _core_timestamps(payload: bytes) -> set[str]:
    """The ``dcterms`` timestamp elements present in ``docProps/core.xml``."""
    core = _parts(payload)["docProps/core.xml"].decode("utf-8")
    return {
        name
        for name in ("dcterms:created", "dcterms:modified")
        if name in core
    }


def _document_with_properties(**updates: Any) -> WordDocument:
    """The sample document with its core properties overridden."""
    document = _sample_document()
    return document.model_copy(
        update={
            "properties": document.properties.model_copy(update=updates),
        }
    )


def test_a_document_without_timestamps_claims_none() -> None:
    """W8: a model with no timestamp must not be given a false creation date.

    The package template carries its own creation and modification date.  A
    document whose model says nothing about its dates must not inherit them,
    or Word's file properties will claim the document was created in 1980.
    """
    payload = WordWriter().write(_document_with_properties(created=None, modified=None))
    assert _core_timestamps(payload) == set()


def test_a_document_with_one_timestamp_claims_only_that_one() -> None:
    """W8: each timestamp is decided on its own.

    A model that carries a creation date but no modification date says
    exactly that — the missing one is dropped rather than filled with a
    pinned epoch that would misstate the document.
    """
    payload = WordWriter().write(
        _document_with_properties(
            created=datetime(2024, 5, 1, 12, 0, 0),
            modified=None,
        )
    )
    assert _core_timestamps(payload) == {"dcterms:created"}
    core = _parts(payload)["docProps/core.xml"].decode("utf-8")
    assert "2024-05-01T12:00:00Z" in core


def test_a_document_with_both_timestamps_claims_both() -> None:
    """W8: the timestamps the model carries reach the package."""
    payload = WordWriter().write(
        _document_with_properties(
            created=datetime(2024, 5, 1, 12, 0, 0),
            modified=datetime(2024, 6, 2, 8, 30, 0),
        )
    )
    assert _core_timestamps(payload) == {"dcterms:created", "dcterms:modified"}
    core = _parts(payload)["docProps/core.xml"].decode("utf-8")
    assert "2024-05-01T12:00:00Z" in core
    assert "2024-06-02T08:30:00Z" in core


# ============================================================================
# W9 - determinism
# ============================================================================


def test_writing_twice_produces_identical_bytes() -> None:
    """W9: the deterministic writer is byte-stable."""
    document = _sample_document()
    assert WordWriter(deterministic=True).write(document) == WordWriter(
        deterministic=True
    ).write(document)


def test_determinism_holds_across_writer_instances() -> None:
    """W9: determinism is a property of the output, not of one instance."""
    document = _sample_document()
    assert WordWriter().write(document) == WordWriter().write(document)


def test_deterministic_output_pins_the_epoch() -> None:
    """W9: the zip members carry the pinned epoch, not the current time."""
    payload = WordWriter(deterministic=True).write(_sample_document())
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        timestamps = {info.date_time for info in archive.infolist()}
    assert timestamps == {(1980, 1, 1, 0, 0, 0)}


# ============================================================================
# W11 - adjacent tables
# ============================================================================


def _two_table_document() -> WordDocument:
    """A document whose two tables are adjacent, with no block between them."""
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="First"),
                                WordTableCell(text="table"),
                            )
                        ),
                    )
                ),
            ),
            WordBlock(
                block_id="t2",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Second"),
                                WordTableCell(text="table"),
                            )
                        ),
                    )
                ),
            ),
        )
    )


def _body_children(payload: bytes) -> list[str]:
    """The top-level element names of the document body, in order."""
    body = ElementTree.fromstring(_document_xml(payload)).find(f"{{{W_NS}}}body")
    assert body is not None
    return [child.tag.rsplit("}", 1)[-1] for child in body]


def test_two_adjacent_tables_are_separated_by_a_paragraph() -> None:
    """W11: two adjacent tables are separated, so Word keeps them apart.

    OOXML has no table boundary.  Word reads two adjacent ``w:tbl`` elements
    as **one** table, so writing them side by side silently destroys the
    second table's grid and every merge in it.  The package stays
    well-formed, and a reader that parses the XML still sees two tables, so
    nothing but Word itself reveals the defect.
    """
    payload = WordWriter().write(_two_table_document())
    children = _body_children(payload)
    assert children.count("tbl") == 2
    first = children.index("tbl")
    second = children.index("tbl", first + 1)
    assert second == first + 2, f"no paragraph between the tables: {children}"
    assert children[first + 1] == "p"


def test_a_table_followed_by_a_paragraph_is_not_given_a_second_one() -> None:
    """W11: the separator is added only where one is needed.

    A document that already separates its tables must not gain an extra
    blank paragraph: the separator is a repair for adjacency, not a feature.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="First"),
                                WordTableCell(text="table"),
                            )
                        ),
                    )
                ),
            ),
            WordBlock(
                block_id="p1",
                kind=WordBlockKind.PARAGRAPH,
                text="Between",
                runs=(WordRun(text="Between"),),
            ),
            WordBlock(
                block_id="t2",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Second"),
                                WordTableCell(text="table"),
                            )
                        ),
                    )
                ),
            ),
        )
    )
    children = _body_children(WordWriter().write(document))
    assert children == ["tbl", "p", "tbl", "sectPr"]


def test_three_adjacent_tables_each_get_their_own_separator() -> None:
    """W11: the separator is applied between every pair, not only the first."""
    table = WordTable(
        rows=(
            WordTableRow(
                cells=(
                    WordTableCell(text="A"),
                    WordTableCell(text="B"),
                )
            ),
        )
    )
    document = WordDocument(
        blocks=(
            WordBlock(block_id="t1", kind=WordBlockKind.TABLE, table=table),
            WordBlock(block_id="t2", kind=WordBlockKind.TABLE, table=table),
            WordBlock(block_id="t3", kind=WordBlockKind.TABLE, table=table),
        )
    )
    children = _body_children(WordWriter().write(document))
    assert children == ["tbl", "p", "tbl", "p", "tbl", "sectPr"]


# ============================================================================
# W12 - every row fills the grid
# ============================================================================


def test_a_table_whose_row_does_not_fill_the_grid_is_refused() -> None:
    """W12: a malformed table is refused, not rendered into something else.

    The grid of a Word table is as wide as its widest row, and every row
    must fill it.  A row that falls short is malformed, and Word does not
    refuse it: it silently re-lays the table out, so the document the reader
    opens is no longer the document the model describes.  The writer raises
    a typed error instead of emitting a table Word will change.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Wide", col_span=2),
                                WordTableCell(text="C"),
                            )
                        ),
                        WordTableRow(
                            cells=(WordTableCell(text="E"),),
                        ),
                    )
                ),
            ),
        )
    )
    with pytest.raises(WordRenderError) as excinfo:
        WordWriter().write(document)
    assert "grid" in str(excinfo.value)


def test_a_table_whose_rows_all_fill_the_grid_is_accepted() -> None:
    """W12: the refusal is about inconsistency, not about width.

    A table whose first row has a merged cell is perfectly valid, as long as
    every row still fills the grid.  The check must not reject it.
    """
    document = WordDocument(
        blocks=(
            WordBlock(
                block_id="t1",
                kind=WordBlockKind.TABLE,
                table=WordTable(
                    rows=(
                        WordTableRow(
                            cells=(
                                WordTableCell(text="Wide", col_span=2),
                                WordTableCell(text="C"),
                            )
                        ),
                        WordTableRow(
                            cells=(
                                WordTableCell(text="E"),
                                WordTableCell(text="F", col_span=2),
                            )
                        ),
                    )
                ),
            ),
        )
    )
    payload = WordWriter().write(document)
    assert _body_children(payload) == ["tbl", "sectPr"]


# ============================================================================
# Error discipline
# ============================================================================


def test_a_failure_inside_the_package_builder_is_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An internal failure surfaces as WordRenderError, not as-is.

    This covers the error-translation path only. W10's actual requirement —
    that the writer re-validates the model before rendering and refuses an
    invalid one — is tested in ``tests/test_word_contract_gaps.py``.

    The live writer is a door to the Kotlin tier, so the translation this
    pins belongs to the *witness*: the frozen original implementation, which
    still owns the python-docx package builder this test breaks. The door's
    own error discipline is pinned in ``tests/test_tier_word.py``.
    """
    from lmeditor._reference import word_writer as writer_module

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("package builder exploded")

    patched = False
    if hasattr(writer_module, "Document"):
        monkeypatch.setattr(writer_module, "Document", explode)
        patched = True
    else:  # pragma: no cover - depends on the writer's import style
        import docx

        monkeypatch.setattr(docx, "Document", explode)
        patched = True
    assert patched

    with pytest.raises(WordRenderError):
        writer_module.WordWriter().write(_sample_document())
