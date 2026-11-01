"""Microsoft Word checks that a carried chart is still a chart.

A chart is not text: it is a package part of its own, reached through a
relationship, with parts of its own behind it (a colour style, a quick style,
the workbook the data lives in).  Reading a document that carries one must not
cost it the chart, and writing that document back must not cost it either.

This is where the package model has to be right about the relationship graph.
A part the document does not reach is reached from another part's relationship
list, and a relationship file is never content.  Getting that wrong produces a
package Word refuses with "the file appears to be corrupted" -- and a package
Word silently repairs is a package whose content the operator cannot trust.

The fixture is a document Microsoft Word itself wrote, committed under
``tests/fixtures``.  It is regenerated with ``tests/word_make_chart.ps1``.

These checks are skipped when Microsoft Word is not installed, so the suite
stays green on a machine without Office.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Final

import pytest

from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter

from tests.word_real_word_check import WordReport, run_word_check, word_is_available

pytestmark = pytest.mark.skipif(
    not word_is_available(),
    reason="Microsoft Word is not installed on this machine",
)

#: A document Word wrote, carrying a real chart.
FIXTURE: Final[Path] = Path(__file__).parent / "fixtures" / "word_chart_fixture.docx"

#: ``wdInlineShapeChart`` -- the shape type Word reports for a chart.
CHART_SHAPE_TYPE: Final[str] = "12"

#: How far the size may drift, in points, before the chart is not the chart.
#: One point is 12700 EMU, so this is well below what a reader can see.
SIZE_TOLERANCE_POINTS: Final[float] = 1.0


def _shape(report: WordReport, index: int = 1) -> tuple[str, float, float]:
    """Return the type and the size of one inline shape, as Word reports it."""
    key = f"INLINE_SHAPE{index}"
    shape_type = report.field(key, "type")
    if not shape_type:
        raise AssertionError(f"Word reported no inline shape {index}: {report.lines}")
    return (
        shape_type,
        float(report.field(key, "width")),
        float(report.field(key, "height")),
    )


def _relationship_part_names(package: Path) -> list[str]:
    """Return the names of the relationship parts inside a package."""
    with zipfile.ZipFile(package) as archive:
        return [
            name
            for name in archive.namelist()
            if "/_rels/" in name and name.endswith(".rels")
        ]


@pytest.fixture(scope="module")
def round_trip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Read the Word-authored chart document and write it back out."""
    target = tmp_path_factory.mktemp("chart") / "roundtrip.docx"
    document = WordReader().read(FIXTURE)
    target.write_bytes(WordWriter().write(document))
    return target


@pytest.fixture(scope="module")
def original_report() -> WordReport:
    """Ask Word what the authored document holds."""
    return run_word_check(FIXTURE)


@pytest.fixture(scope="module")
def round_trip_report(round_trip: Path) -> WordReport:
    """Ask Word what our round trip holds."""
    return run_word_check(round_trip)


def test_word_opens_the_document_we_wrote_back(
    round_trip_report: WordReport,
) -> None:
    """Word accepts the package we rebuilt from its own document.

    This is the check that failed before the relationship graph was fixed: the
    document carried a relationship that pointed at a relationship file, and
    Word refused the package outright.
    """
    assert round_trip_report.opened, f"Word refused the document: {round_trip_report.error}"


def test_word_still_sees_the_chart(
    original_report: WordReport,
    round_trip_report: WordReport,
) -> None:
    """The chart is still a chart after the round trip."""
    assert original_report.opened, (
        f"Word refused the fixture: {original_report.error}"
    )
    assert _shape(original_report)[0] == CHART_SHAPE_TYPE, (
        "the fixture does not hold a chart, so the check would prove nothing"
    )
    assert round_trip_report.value("INLINE_SHAPE_COUNT") == "1", (
        f"Word reported {round_trip_report.value('INLINE_SHAPE_COUNT')!r} inline "
        "shapes, expected '1'"
    )
    assert _shape(round_trip_report)[0] == CHART_SHAPE_TYPE, (
        f"the shape is no longer a chart: {round_trip_report.lines}"
    )


def test_the_chart_keeps_the_size_it_was_written_at(
    original_report: WordReport,
    round_trip_report: WordReport,
) -> None:
    """A chart comes back at the size it was drawn at.

    Word scales a chart by rewriting the extent of the drawing that holds it.
    A reader that does not keep the extent hands the chart back at a default
    size, and the operator sees a chart that no longer says what it said.
    """
    _, original_width, original_height = _shape(original_report)
    _, width, height = _shape(round_trip_report)

    assert abs(width - original_width) <= SIZE_TOLERANCE_POINTS, (
        f"the chart is {width} points wide, Word wrote it at {original_width}"
    )
    assert abs(height - original_height) <= SIZE_TOLERANCE_POINTS, (
        f"the chart is {height} points high, Word wrote it at {original_height}"
    )


def test_no_relationship_points_at_a_relationship_part(round_trip: Path) -> None:
    """A relationship file is the relationship list of a sibling, not content.

    Pointing at one is what made Word call the package corrupt, so the check
    is made at the level of the package: no relationship, anywhere in the
    package, may target a ``.rels`` part.
    """
    with zipfile.ZipFile(round_trip) as archive:
        for name in archive.namelist():
            if not (name.endswith(".rels") and "/_rels/" in name):
                continue
            content = archive.read(name).decode("utf-8")
            for chunk in content.split("<Relationship ")[1:]:
                target = chunk.split('Target="', 1)[-1].split('"', 1)[0]
                assert not target.endswith(".rels"), (
                    f"{name} points at the relationship part {target}"
                )


def test_the_chart_keeps_the_parts_it_owns(round_trip: Path) -> None:
    """The chart's colour style, quick style and workbook travel with it.

    A chart whose workbook is missing is a chart Word cannot edit, and a chart
    whose colour style is missing is a chart Word re-styles on open.
    """
    with zipfile.ZipFile(round_trip) as archive:
        names = set(archive.namelist())

    assert "word/charts/chart1.xml" in names, (
        f"the chart part is missing from the package: {sorted(names)}"
    )
    assert "word/embeddings/Microsoft_Excel_Worksheet.xlsx" in names, (
        "the workbook behind the chart was lost"
    )
    assert "word/charts/colors1.xml" in names, (
        "the colour style behind the chart was lost"
    )
    assert "word/charts/style1.xml" in names, (
        "the quick style behind the chart was lost"
    )
    assert "word/charts/_rels/chart1.xml.rels" in names, (
        "the chart lost the relationships that reach its own parts"
    )


def test_the_package_holds_no_relationship_as_content(round_trip: Path) -> None:
    """Every relationship part in the package is a relationship part.

    A relationship file written as content is what Word rejects; this asserts
    that no ``.rels`` file in the package sits anywhere but beside the part it
    belongs to.
    """
    for name in _relationship_part_names(round_trip):
        directory, _, file = name.rpartition("/")
        assert directory.endswith("/_rels"), (
            f"{name} is a relationship part outside a _rels directory"
        )
        assert file.endswith(".rels"), f"{name} is not a relationship part"
