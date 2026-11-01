"""Microsoft Word checks the documents the *application* produces.

The conformance harness in :mod:`tests.word_real_word_check` proves that the
writer builds the constructs the **model** describes.  That is not the same
question.  The operator receives what the application exports: the manuscript
bridge's output, with the markdown syntax the editor writes, a table, and a
citation whose footnote names the passage it came from and the chunk it was
taken from.

A package-level assertion can prove the XML says what the model says.  Only
Word can say whether the document the operator opens is a real Word document:
whether the table is a table, whether the list is a list, and whether the
footnote that carries the provenance is a footnote Word renders at the bottom
of the page.

These checks are skipped when Microsoft Word is not installed, so the suite
stays green on a machine without Office.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from lmeditor.core.schemas import (
    CitationLineage,
    DocumentDraft,
    SectionDraft,
)
from lmeditor.word.manuscript import build_manuscript_document
from lmeditor.word.writer import WordWriter

from tests.word_real_word_check import (
    build_feature_document,
    run_word_check,
    word_is_available,
)

pytestmark = pytest.mark.skipif(
    not word_is_available(),
    reason="Microsoft Word is not installed on this machine",
)

#: The chunk a citation in the fixture points at.  It is a real SHA-256 of a
#: real passage, because the footnote has to carry the same id the operator
#: sees in the application.
CHUNK_ID: Final[str] = (
    "8409dc1cd18b3b76442189f08940fcd07f65e07390a83ef01eefad716d24fd11"
)

#: The passage the footnote must quote.
PASSAGE: Final[str] = (
    "Windrow composting reaches thermophilic temperatures within days."
)

#: The markdown the editor writes, and the bridge has to turn into structure.
CONTENT: Final[str] = f"""A paragraph with **bold**, *italic* and `code` in it.

- first bullet
- second bullet

1. numbered one
2. numbered two

> A quoted line.

```python
print("fenced")
```

| Column A | Column B |
| -------- | -------- |
| a1       | b1       |
| a2       | b2       |

A cited claim [#{CHUNK_ID}].
"""


def _application_document() -> DocumentDraft:
    """The draft the application hands to the bridge."""
    return DocumentDraft(
        document_id="app_conformance",
        title="Application Conformance",
        sections=(
            SectionDraft(
                section_id="sec_01_01",
                title="Temperatures & Thermophilic",
                content=CONTENT,
                order=0,
                # The lineage the pipeline established: it says which passage
                # the claim came from, and the footnote has to carry it.
                citations=(
                    CitationLineage(
                        lineage_id="lin_1",
                        claim_id="claim_1",
                        chunk_id=CHUNK_ID,
                        span_start=0,
                        span_end=len(PASSAGE),
                        exact_quote=PASSAGE,
                    ),
                ),
                cited_chunk_ids=(CHUNK_ID,),
            ),
        ),
    )


def _write(tmp_path: Path, document: DocumentDraft) -> Path:
    """Write the draft out through the application's own path."""
    target = tmp_path / "application.docx"
    target.write_bytes(WordWriter().write(build_manuscript_document(document)))
    return target


def test_word_opens_the_document_the_application_exports(tmp_path: Path) -> None:
    """Word accepts the application's export without repairing it.

    A package Word has to repair is a package the operator will be asked
    about, and one whose content may not survive the repair.
    """
    target = _write(tmp_path, _application_document())
    report = run_word_check(target)

    assert report.opened, f"Word refused the document: {report.error}"


def test_word_sees_the_table_the_editor_wrote(tmp_path: Path) -> None:
    """A markdown table in the editor is a table in the document.

    Before the tables were rendered, a table typed in the editor reached the
    document as loose prose.  Only Word can say whether the cells are cells.
    """
    target = _write(tmp_path, _application_document())
    report = run_word_check(target)

    assert report.value("TABLE_COUNT") == "1", (
        f"Word reported {report.value('TABLE_COUNT')!r} tables, expected '1'"
    )
    assert "TABLE1|rows=3|cols=2" in report.lines, (
        f"Word did not report a three-by-two table: {report.lines}"
    )
    for cell in ("'Column A'", "'Column B'", "'a1'", "'b2'"):
        assert cell in "".join(report.lines), (
            f"Word did not see the cell {cell}"
        )


def test_word_sees_the_lists_the_editor_wrote(tmp_path: Path) -> None:
    """A bullet is a bullet and a numbered item is numbered.

    The markers Word renders are the only way to see whether the numbering
    survived; the text of the item is the same either way.
    """
    target = _write(tmp_path, _application_document())
    report = run_word_check(target)

    # The bullet is written as an escape on purpose: a literal U+2022 in the
    # source would make this check depend on how the file was encoded.
    assert "marker=\u2022" in "".join(report.lines), (
        "Word did not render the bullet as a bullet"
    )
    assert "marker=1." in "".join(report.lines), (
        "Word did not render the numbered item as numbered"
    )


def test_word_sees_the_footnote_that_carries_the_provenance(tmp_path: Path) -> None:
    """The footnote names the passage and the chunk it came from.

    This is the heart of the application: a claim that can be traced to the
    passage it was written from.  A footnote in the package is not yet a
    footnote Word shows.
    """
    target = _write(tmp_path, _application_document())
    report = run_word_check(target)

    assert report.value("FOOTNOTE_COUNT") == "1", (
        f"Word reported {report.value('FOOTNOTE_COUNT')!r} footnotes, expected '1'"
    )
    assert f"{PASSAGE} [{CHUNK_ID}]" in "".join(report.lines), (
        "the footnote Word renders does not carry the passage and the chunk id"
    )


def test_word_sees_the_heading_the_section_carries(tmp_path: Path) -> None:
    """The section's title is a heading, not a paragraph of text."""
    target = _write(tmp_path, _application_document())
    report = run_word_check(target)

    assert report.paragraph_style("Temperatures & Thermophilic") == "Heading 1", (
        "the section's title is not styled 'Heading 1'"
    )


def test_word_reports_non_ascii_text_exactly(tmp_path: Path) -> None:
    """The harness must not mangle the characters a document is checked on.

    PowerShell writes UTF-8 to a redirected pipe.  A harness that decodes the
    answer with the machine's locale encoding -- cp1252 on most Windows
    machines -- turns an em dash into three characters and a bullet into three
    more, and then a document with an em dash in its heading fails a check it
    should pass.  The characters are written as escapes here so that this
    file's own encoding is not part of what is being tested.
    """
    heading = "L\u2019\u00e9t\u00e9 \u2014 \u00abquoted\u00bb"
    passage = "A passage with an em dash \u2014 and an accent: caf\u00e9."
    chunk_id = "b" * 64

    document = DocumentDraft(
        document_id="app_conformance_unicode",
        title="Rapport \u2014 caf\u00e9",
        sections=(
            SectionDraft(
                section_id="sec_01_01",
                title=heading,
                content=f"A cited claim [#{chunk_id}].",
                order=0,
                citations=(
                    CitationLineage(
                        lineage_id="lin_1",
                        claim_id="claim_1",
                        chunk_id=chunk_id,
                        span_start=0,
                        span_end=len(passage),
                        exact_quote=passage,
                    ),
                ),
                cited_chunk_ids=(chunk_id,),
            ),
        ),
    )
    target = tmp_path / "unicode.docx"
    target.write_bytes(WordWriter().write(build_manuscript_document(document)))
    report = run_word_check(target)

    assert report.opened, f"Word refused the document: {report.error}"
    assert report.paragraph_style(heading) == "Heading 1", (
        "the heading with an em dash and an accent did not survive the harness"
    )
    assert f"{passage} [{chunk_id}]" in "".join(report.lines), (
        "the footnote with an em dash and an accent did not survive the harness"
    )


def test_the_word_harness_still_passes_its_own_check(tmp_path: Path) -> None:
    """The check has teeth: the model-level document Word used to accept.

    A new conformance check that only ever sees good files cannot tell a good
    file from a bad one.  This asserts the harness still reports the
    properties it was written to report, so the application-level checks above
    are measured against a working baseline.
    """
    target = tmp_path / "feature.docx"
    target.write_bytes(WordWriter().write(build_feature_document()))
    report = run_word_check(target)

    assert report.opened, f"Word refused the document: {report.error}"
    assert report.value("TABLE_COUNT") == "2"
    assert report.value("FOOTNOTE_COUNT") == "1"


def test_the_application_path_is_the_one_under_test() -> None:
    """The document under test came through the bridge, not the model.

    Without this, a change to the bridge could leave these checks testing the
    model instead of the application.
    """
    document = build_manuscript_document(_application_document())
    kinds = {block.kind.value for block in document.blocks}

    assert "table" in kinds, "the bridge did not render the table"
    assert "heading" in kinds, "the bridge did not render the section heading"
    assert any(
        run.footnote_ref is not None
        for block in document.blocks
        for run in block.runs
    ), "the bridge did not create the footnote that carries the provenance"
