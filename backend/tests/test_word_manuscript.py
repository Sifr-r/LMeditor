"""Whitebox tests for the manuscript bridge.

The bridge is where LMeditor's grounding evidence is carried into a Word
document, so the tests assert the evidence itself: that a citation marker is
consumed rather than printed, that it is numbered once per cited chunk, that the
cited passage becomes a footnote, and that the marker text never survives into
the body.

The bridge also reads the manuscript's own syntax -- headings, lists, quotes,
fenced code and inline emphasis -- and the tests assert that the structure
reaches the document instead of being flattened into prose.

Expectations are written against the contract
(``WORD_PROCESSING_CONTRACT.md`` §7, requirements M1-M6).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from lmeditor.core.exceptions import WordProcessingError
from lmeditor.core.schemas import (
    BibliographicRecord,
    CitationLineage,
    DocumentDraft,
    SectionDraft,
)
from lmeditor.core.types import SectionStatus
from lmeditor.word.manuscript import build_manuscript_document
from lmeditor.word.model import ListMarker, WordBlockKind
from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter


def _section(
    section_id: str = "s1",
    *,
    title: str = "Grounding Section",
    content: str = "A grounded claim.",
    order: int = 0,
    status: SectionStatus = SectionStatus.VERIFIED,
    is_verified: bool = True,
    citations: tuple[CitationLineage, ...] = (),
) -> SectionDraft:
    """Build a section draft for the bridge."""
    return SectionDraft(
        section_id=section_id,
        title=title,
        content=content,
        order=order,
        status=status,
        is_verified=is_verified,
        citations=list(citations),
    )


def _document(*sections: SectionDraft, title: str = "A Grounded Manuscript") -> DocumentDraft:
    """Build a document draft for the bridge."""
    return DocumentDraft(
        document_id="d1",
        title=title,
        sections=list(sections),
    )


def _blocks_of_kind(document, kind: WordBlockKind) -> list:  # type: ignore[no-untyped-def]
    """Return the document's blocks of one kind."""
    return [block for block in document.blocks if block.kind is kind]


def _runs_of(document) -> list:  # type: ignore[no-untyped-def]
    """Return every run of the document, in order."""
    return [run for block in document.blocks for run in block.runs]


def _package_parts(payload: bytes) -> dict[str, str]:
    """Return the text of every part of a written Word package."""
    parts: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            parts[name] = archive.read(name).decode("utf-8", errors="replace")
    return parts


# ============================================================================
# M1 - structure
# ============================================================================


def test_the_title_becomes_a_title_block() -> None:
    """M1: the manuscript title is the document's title block."""
    document = build_manuscript_document(_document(_section()))
    titles = _blocks_of_kind(document, WordBlockKind.TITLE)
    assert len(titles) == 1
    assert titles[0].text == "A Grounded Manuscript"


def test_a_section_becomes_a_heading_and_paragraphs() -> None:
    """M1: each section becomes a heading followed by its paragraphs."""
    section = _section(content="First paragraph.\n\nSecond paragraph.")
    document = build_manuscript_document(_document(section))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [h.text for h in headings] == ["Grounding Section"]
    assert headings[0].level == 1
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert [p.text for p in paragraphs] == ["First paragraph.", "Second paragraph."]


def test_an_empty_section_says_so() -> None:
    """M1: a section with no prose still produces a readable paragraph."""
    document = build_manuscript_document(_document(_section(content="")))
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert len(paragraphs) == 1
    assert paragraphs[0].text == "Section has no drafted content."


# ============================================================================
# M2 - ordering and verification filtering
# ============================================================================


def test_sections_are_ordered_by_order_then_id() -> None:
    """M2: sections render in (order, section_id) order."""
    later = _section("s2", title="Later", order=1)
    earlier = _section("s1", title="Earlier", order=0)
    document = build_manuscript_document(_document(later, earlier))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [h.text for h in headings] == ["Earlier", "Later"]


def test_sections_with_the_same_order_are_broken_by_id() -> None:
    """M2: the section_id breaks a tie on order.

    Two sections can carry the same ``order``.  The tie is broken on
    ``section_id``, so the rendering does not depend on the order the
    caller happened to list the sections in.
    """
    second = _section("s2", title="Second", order=0)
    first = _section("s1", title="First", order=0)
    document = build_manuscript_document(_document(second, first))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [h.text for h in headings] == ["First", "Second"]


def test_verified_only_drops_unverified_sections() -> None:
    """M2: an unverified section is dropped, heading included."""
    verified = _section("s1", title="Verified", status=SectionStatus.VERIFIED)
    pending = _section(
        "s2",
        title="Pending",
        status=SectionStatus.PENDING,
        is_verified=False,
    )
    document = build_manuscript_document(
        _document(verified, pending), verified_only=True
    )
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [h.text for h in headings] == ["Verified"]


def test_verified_only_keeps_approved_sections() -> None:
    """M2: an approved section counts as verified."""
    approved = _section("s1", title="Approved", status=SectionStatus.APPROVED)
    document = build_manuscript_document(_document(approved), verified_only=True)
    assert len(_blocks_of_kind(document, WordBlockKind.HEADING)) == 1


def test_unverified_sections_are_kept_by_default() -> None:
    """M2: without the filter, every section renders."""
    pending = _section("s1", title="Pending", status=SectionStatus.PENDING)
    document = build_manuscript_document(_document(pending))
    assert len(_blocks_of_kind(document, WordBlockKind.HEADING)) == 1


# ============================================================================
# M3 - citation markers are consumed
# ============================================================================


def test_a_marker_never_survives_into_the_body() -> None:
    """M3: the marker text is consumed, not printed."""
    section = _section(content="A grounded claim [#abc123].")
    document = build_manuscript_document(_document(section))
    body = "\n\n".join(block.text for block in document.blocks)
    assert "abc123" not in body
    assert "[#" not in body


@pytest.mark.parametrize(
    "marker",
    ["[#abc123]", "[abc123]", "[cite: abc123]"],
)
def test_every_accepted_marker_form_is_consumed(marker: str) -> None:
    """M3: all the forms the citation grammar accepts are consumed."""
    section = _section(content=f"A grounded claim {marker}.")
    document = build_manuscript_document(_document(section))
    body = "\n\n".join(block.text for block in document.blocks)
    assert "abc123" not in body


def test_a_marker_becomes_a_superscript_number() -> None:
    """M3: the consumed marker becomes a superscript numeric run."""
    section = _section(content="A grounded claim [#abc123].")
    document = build_manuscript_document(_document(section))
    superscripts = [
        run
        for block in document.blocks
        for run in block.runs
        if run.superscript
    ]
    assert [run.text for run in superscripts] == ["1"]


def test_a_marker_group_yields_one_superscript_per_chunk() -> None:
    """M3: a comma list of markers becomes one superscript per cited chunk."""
    section = _section(content="A claim [#a, #b].")
    document = build_manuscript_document(_document(section))
    superscripts = [
        run
        for block in document.blocks
        for run in block.runs
        if run.superscript
    ]
    assert [run.text for run in superscripts] == ["1", "2"]


def test_a_markdown_link_is_not_a_citation() -> None:
    """M3: a markdown link is left alone; it is not a citation marker."""
    section = _section(content="See [the docs](https://example.org).")
    document = build_manuscript_document(_document(section))
    body = "\n\n".join(block.text for block in document.blocks)
    assert "[the docs](https://example.org)" in body


def test_prose_around_a_marker_is_not_glued_or_split() -> None:
    """M3: consuming a marker must not damage the surrounding words."""
    section = _section(content="A grounded claim[#abc123]follows.")
    document = build_manuscript_document(_document(section))
    body = "\n\n".join(block.text for block in document.blocks)
    assert "A grounded claim 1 follows." == body.split("\n\n")[-1].strip()


# ============================================================================
# M4 - numbering and footnotes
# ============================================================================


def test_citations_are_numbered_in_first_appearance_order() -> None:
    """M4: the second chunk cited gets the next number."""
    section = _section(content="First [#a] then second [#b].")
    document = build_manuscript_document(_document(section))
    superscripts = [
        run
        for block in document.blocks
        for run in block.runs
        if run.superscript
    ]
    assert [run.text for run in superscripts] == ["1", "2"]


def test_a_recited_chunk_reuses_its_number() -> None:
    """M4: the same chunk cited twice is one reference, not two."""
    section = _section(content="First [#a] and again [#a].")
    document = build_manuscript_document(_document(section))
    superscripts = [
        run
        for block in document.blocks
        for run in block.runs
        if run.superscript
    ]
    assert [run.text for run in superscripts] == ["1", "1"]


def test_the_cited_passage_becomes_a_footnote() -> None:
    """M4: the exact quote recorded for a chunk becomes the footnote.

    The footnote carries the quoted passage *and* the id of the chunk it was
    taken from: a reader of the exported document must be able to walk the
    claim back to the exact stored passage.
    """
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    assert len(document.footnotes) == 1
    footnote = document.footnotes[0]
    assert "the cited passage" in footnote.text
    assert "abc123" in footnote.text


def test_a_cited_chunk_without_a_passage_gets_no_footnote() -> None:
    """M4: the marker survives even when there is no passage to quote."""
    section = _section(content="A grounded claim [#abc123].")
    document = build_manuscript_document(_document(section))
    assert document.footnotes == ()
    superscripts = [
        run
        for block in document.blocks
        for run in block.runs
        if run.superscript
    ]
    assert superscripts, "the marker must survive"


def test_the_footnote_is_attached_to_the_marker_run() -> None:
    """M4: the marker run points at the footnote that carries the passage."""
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    refs = [
        run.footnote_ref
        for block in document.blocks
        for run in block.runs
        if run.footnote_ref is not None
    ]
    assert len(refs) == 1


# ============================================================================
# M5 - the reference list
# ============================================================================


def test_a_bibliography_produces_a_references_section() -> None:
    """M5: the bibliography becomes a References heading and one line per record."""
    record = BibliographicRecord(
        title="A Grounded Paper",
        authors=["Ada Lovelace"],
        year=1843,
    )
    document = build_manuscript_document(_document(_section()), [record])
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert headings[-1].text == "References"
    references = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert references[-1].text == "Ada Lovelace A Grounded Paper 1843"


def test_reference_fields_are_rendered_in_order() -> None:
    """M5: the fields appear in the contract's order."""
    record = BibliographicRecord(
        title="A Grounded Paper",
        authors=["Ada Lovelace", "Alan Turing"],
        year=1843,
        container_title="Journal of Grounding",
        volume="12",
        issue="3",
        pages="10-20",
        doi="10.1000/xyz",
        url="https://example.org/paper",
    )
    document = build_manuscript_document(_document(_section()), [record])
    reference = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)[-1].text
    assert reference == (
        "Ada Lovelace, Alan Turing A Grounded Paper Journal of Grounding "
        "12 3 1843 10-20 10.1000/xyz https://example.org/paper"
    )


def test_absent_reference_fields_are_omitted() -> None:
    """M5: a record without a year does not render a placeholder."""
    record = BibliographicRecord(title="A Grounded Paper")
    document = build_manuscript_document(_document(_section()), [record])
    reference = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)[-1].text
    assert reference == "Anon. A Grounded Paper"
    assert "None" not in reference


def test_no_bibliography_means_no_references_section() -> None:
    """M5: an empty bibliography adds nothing."""
    document = build_manuscript_document(_document(_section()))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert all(heading.text != "References" for heading in headings)


# ============================================================================
# M6 - purity
# ============================================================================


def test_the_inputs_are_not_mutated() -> None:
    """M6: the bridge reads its inputs and never rewrites them.

    More than one section, and a bibliography that is *not* in
    alphabetical order: with a single element an in-place sort would be a
    no-op and the mutation would go unnoticed.  The bridge orders the
    sections it renders and must do so on a copy — the caller's list is
    not its to reorder.
    """
    later = _section("s2", title="Later", order=1)
    earlier = _section("s1", title="Earlier", order=0)
    document = _document(later, earlier)
    original_sections = list(document.sections)
    original_content = earlier.content

    zeta = BibliographicRecord(title="Zeta Paper")
    alpha = BibliographicRecord(title="Alpha Paper")
    bibliography = [zeta, alpha]
    original_bibliography = list(bibliography)

    build_manuscript_document(document, bibliography)

    assert document.sections == original_sections
    assert earlier.content == original_content
    assert bibliography == original_bibliography
    assert [record.title for record in bibliography] == ["Zeta Paper", "Alpha Paper"]


# ============================================================================
# Boundary validation
# ============================================================================


def test_a_non_document_argument_is_refused() -> None:
    """The module boundary rejects a wrong argument type with a typed error."""
    with pytest.raises(WordProcessingError):
        build_manuscript_document("not a draft")  # type: ignore[arg-type]


def test_a_non_record_in_the_bibliography_is_refused() -> None:
    """The module boundary rejects a malformed bibliography entry."""
    with pytest.raises(WordProcessingError):
        build_manuscript_document(_document(_section()), ["not a record"])  # type: ignore[list-item]


def test_the_document_title_reaches_the_properties() -> None:
    """The document properties carry the manuscript title."""
    document = build_manuscript_document(_document(_section()))
    assert document.properties.title == "A Grounded Manuscript"


# ============================================================================
# The manuscript's own syntax - block structure
# ============================================================================


def test_a_second_level_heading_becomes_a_heading_block() -> None:
    """A ``##`` heading inside a section is a heading at level two."""
    section = _section(content="## A heading\n\nPlain prose.")
    document = build_manuscript_document(_document(section))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [heading.text for heading in headings] == ["Grounding Section", "A heading"]
    assert headings[1].level == 2


def test_a_first_level_heading_does_not_renumber_the_section_title() -> None:
    """A ``#`` heading inside a section is a heading at level one."""
    section = _section(content="# A heading")
    document = build_manuscript_document(_document(section))
    headings = _blocks_of_kind(document, WordBlockKind.HEADING)
    assert [heading.level for heading in headings] == [1, 1]


def test_a_bullet_item_becomes_a_bullet_list_item() -> None:
    """A ``- `` item becomes a list item that carries the bullet marker."""
    section = _section(content="- item")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 1
    assert items[0].list_marker is ListMarker.BULLET
    assert items[0].text == "item"
    assert items[0].level == 0


def test_a_numbered_item_becomes_a_numbered_list_item() -> None:
    """A ``1. `` item becomes a list item that carries the number marker."""
    section = _section(content="1. item")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 1
    assert items[0].list_marker is ListMarker.NUMBER
    assert items[0].text == "item"


def test_consecutive_numbered_items_share_one_list_group() -> None:
    """Two numbered items in a row continue one numbering sequence."""
    section = _section(content="1. one\n2. two")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 2
    assert items[0].list_group is not None
    assert items[0].list_group == items[1].list_group


def test_a_numbered_list_after_a_paragraph_starts_a_new_group() -> None:
    """A paragraph between two lists ends the first numbering sequence."""
    section = _section(content="1. one\n\nSome prose.\n\n2. two")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 2
    assert items[0].list_group != items[1].list_group


def test_a_numbered_list_that_restarts_at_one_starts_a_new_group() -> None:
    """A numbering that starts at one again is a new list."""
    section = _section(content="1. one\n2. two\n\n1. one again")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert len(items) == 3
    assert items[0].list_group == items[1].list_group
    assert items[2].list_group != items[0].list_group


def test_an_indented_list_item_is_nested_deeper() -> None:
    """Two spaces of indentation make a list item one level deeper."""
    section = _section(content="- outer\n  - inner\n    - deepest")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert [item.level for item in items] == [0, 1, 2]


def test_a_deeply_indented_list_item_is_capped() -> None:
    """No list item nests deeper than the model allows.

    The item is not the first line of the section: the draft schema strips
    the leading whitespace of a section's content, so the indentation of the
    very first line is not available to the bridge.
    """
    section = _section(content="- shallow\n" + " " * 20 + "- deep")
    document = build_manuscript_document(_document(section))
    items = _blocks_of_kind(document, WordBlockKind.LIST_ITEM)
    assert [item.level for item in items] == [0, 8]


def test_a_quoted_line_becomes_a_quote_block() -> None:
    """A ``> `` line becomes a block quote."""
    section = _section(content="> quoted")
    document = build_manuscript_document(_document(section))
    quotes = _blocks_of_kind(document, WordBlockKind.QUOTE)
    assert len(quotes) == 1
    assert quotes[0].text == "quoted"


def test_a_fenced_block_becomes_a_code_block_kept_verbatim() -> None:
    """A fenced block keeps its content, and its stars are not emphasis."""
    section = _section(content="```\ndef f(**kwargs):\n    return **kwargs\n```")
    document = build_manuscript_document(_document(section))
    codes = _blocks_of_kind(document, WordBlockKind.CODE)
    assert len(codes) == 1
    assert codes[0].runs[0].text == "def f(**kwargs):\n    return **kwargs"
    assert "**kwargs" in codes[0].text
    assert not any(run.bold for run in codes[0].runs)


# ============================================================================
# The manuscript's own syntax - inline formatting
# ============================================================================


def test_every_inline_marker_becomes_a_formatted_run() -> None:
    """Each marker of the syntax becomes a run that carries its style."""
    section = _section(
        content="**bold** *italic* _italic_ __underline__ ~~strike~~ `code` ==mark=="
    )
    document = build_manuscript_document(_document(section))
    runs = _runs_of(document)
    assert [run.text for run in runs if run.bold] == ["bold"]
    assert [run.text for run in runs if run.italic] == ["italic", "italic"]
    assert [run.text for run in runs if run.underline] == ["underline"]
    assert [run.text for run in runs if run.strike] == ["strike"]
    assert [run.text for run in runs if run.font is not None] == ["code"]
    assert [run.text for run in runs if run.highlight is not None] == ["mark"]


def test_an_unmatched_marker_stays_literal_text() -> None:
    """A marker without a partner stays text and loses nothing."""
    section = _section(content="**unmatched and the rest of the paragraph")
    document = build_manuscript_document(_document(section))
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert paragraphs[0].text == "**unmatched and the rest of the paragraph"
    assert not any(run.bold for run in paragraphs[0].runs)


def test_a_citation_marker_still_becomes_a_citation_run_and_a_footnote() -> None:
    """The syntax support does not cost the bridge its citation markers."""
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    superscripts = [run for run in _runs_of(document) if run.superscript]
    assert [run.text for run in superscripts] == ["1"]
    assert len(document.footnotes) == 1
    assert "abc123" in document.footnotes[0].text


def test_a_citation_marker_inside_an_emphasised_span_is_not_split() -> None:
    """A marker inside a bold span is still consumed, and the span survives."""
    section = _section(content="A claim **with [#abc123] inside** emphasis.")
    document = build_manuscript_document(_document(section))
    superscripts = [run for run in _runs_of(document) if run.superscript]
    assert [run.text for run in superscripts] == ["1"]
    body = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)[0].text
    assert body == "A claim with 1 inside emphasis."
    assert "abc123" not in body


def test_a_template_citation_marker_is_untouched() -> None:
    """The editor's own marker survives the export as it was written."""
    section = _section(content="A claim {{cite:cit-1:chunk-001}} stands.")
    document = build_manuscript_document(_document(section))
    body = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)[0].text
    assert body == "A claim {{cite:cit-1:chunk-001}} stands."


def test_plain_prose_still_produces_the_same_blocks() -> None:
    """A prose section is unchanged by the syntax support."""
    section = _section(content="First paragraph.\n\nSecond paragraph.")
    document = build_manuscript_document(_document(section))
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert [p.text for p in paragraphs] == ["First paragraph.", "Second paragraph."]
    assert [run.text for run in paragraphs[0].runs] == ["First paragraph."]


def test_a_soft_line_break_stays_inside_its_paragraph() -> None:
    """A line that carries no marker continues the paragraph above it."""
    section = _section(content="First line.\nSecond line.")
    document = build_manuscript_document(_document(section))
    paragraphs = _blocks_of_kind(document, WordBlockKind.PARAGRAPH)
    assert len(paragraphs) == 1
    assert paragraphs[0].text == "First line. Second line."


# ============================================================================
# Traceability - the chunk id reaches the exported package
# ============================================================================


def test_the_footnote_names_the_chunk_it_quotes() -> None:
    """A footnote carries the passage and the id of the chunk it came from."""
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    footnote = document.footnotes[0]
    assert "the cited passage" in footnote.text
    assert "abc123" in footnote.text


def test_the_exported_package_names_the_cited_chunk() -> None:
    """The chunk id is in the exported package, not only in the model.

    This is the end-to-end form of the promise: a reader of the ``.docx``
    must be able to find the passage a claim was taken from.
    """
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    parts = _package_parts(WordWriter().write(document))
    assert "abc123" in parts["word/footnotes.xml"]


def test_the_chunk_id_survives_a_write_read_round_trip() -> None:
    """The chunk id is still in the document after it went through a file."""
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(content="A grounded claim [#abc123].", citations=(lineage,))
    document = build_manuscript_document(_document(section))
    recovered = WordReader().read(WordWriter().write(document))
    assert any("abc123" in footnote.text for footnote in recovered.footnotes)


def test_a_chunk_cited_twice_still_has_one_footnote() -> None:
    """The chunk id does not multiply the footnotes of a chunk."""
    lineage = CitationLineage(
        lineage_id="l1",
        claim_id="c1",
        chunk_id="abc123",
        span_start=0,
        span_end=10,
        exact_quote="the cited passage",
    )
    section = _section(
        content="A grounded claim [#abc123] and again [#abc123].",
        citations=(lineage,),
    )
    document = build_manuscript_document(_document(section))
    assert len(document.footnotes) == 1
