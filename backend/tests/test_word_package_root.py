"""A package-level part survives the round trip.

A package has two levels of relationship.  ``word/_rels/document.xml.rels``
says what the *document* reaches; ``_rels/.rels`` says what the *package root*
reaches.  The custom document properties of a Word document -- the ones a
person fills in by hand, "Status: Draft", "Reviewed by" -- are reached from
the package root and not from the document.

A part the package root reaches is still a part of the document, and reading
the document and writing it back must not cost it.  It cannot be reached
through the document's relationship list either: the document does not
reference it.  The relationship that reaches it is the package root's, and the
writer has to put that relationship back where it was.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Final

import pytest

from lmeditor.word.reader import WordReader
from lmeditor.word.writer import WordWriter

pytestmark = pytest.mark.skipif(
    not (Path(__file__).parent / "fixtures" / "word_chart_fixture.docx").exists(),
    reason="the Word-authored fixture is missing",
)

#: A document Word wrote, carrying a real chart.
FIXTURE: Final[Path] = Path(__file__).parent / "fixtures" / "word_chart_fixture.docx"

#: The relationship type Word uses for custom document properties.
CUSTOM_PROPERTIES_TYPE: Final[str] = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    "custom-properties"
)

#: The id the package root uses for the properties part.
CUSTOM_PROPERTIES_ID: Final[str] = "rIdCustomProperties"

CUSTOM_PROPERTIES_XML: Final[bytes] = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    b'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/'
    b'officeDocument/2006/docPropsVTypes">'
    b'<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" '
    b'name="Status"><vt:lpwstr>Draft</vt:lpwstr></property></Properties>'
)


def _with_custom_properties(target: Path) -> Path:
    """Write the fixture out with the custom properties a Word document has."""
    with zipfile.ZipFile(FIXTURE) as source:
        members = [(name, source.read(name)) for name in source.namelist()]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            if name == "_rels/.rels":
                text = payload.decode("utf-8")
                payload = text.replace(
                    "</Relationships>",
                    f'<Relationship Id="{CUSTOM_PROPERTIES_ID}" '
                    f'Type="{CUSTOM_PROPERTIES_TYPE}" '
                    'Target="docProps/custom.xml"/></Relationships>',
                ).encode("utf-8")
            elif name == "[Content_Types].xml":
                text = payload.decode("utf-8")
                payload = text.replace(
                    "</Types>",
                    '<Override PartName="/docProps/custom.xml" ContentType='
                    '"application/vnd.openxmlformats-officedocument.'
                    'custom-properties+xml"/></Types>',
                ).encode("utf-8")
            archive.writestr(name, payload)
        archive.writestr("docProps/custom.xml", CUSTOM_PROPERTIES_XML)
    return target


def test_the_reader_remembers_how_the_package_root_reaches_the_part(
    tmp_path: Path,
) -> None:
    """The relationship of the package root is part of what the part is."""
    package = _with_custom_properties(tmp_path / "source.docx")

    reader = WordReader()
    document = reader.read(package)

    carried = {part.part_name: part for part in document.parts}
    assert "docProps/custom.xml" in carried, (
        f"the properties part was not carried: {sorted(carried)}"
    )
    relationship = carried["docProps/custom.xml"].package_relationship
    assert relationship is not None, (
        "the part does not remember the package root relationship that reaches it"
    )
    assert relationship.relationship_id == CUSTOM_PROPERTIES_ID
    assert relationship.relationship_type == CUSTOM_PROPERTIES_TYPE
    assert relationship.target == "docProps/custom.xml"
    assert relationship.is_external is False
    # The document itself does not reach the part: the package root does.
    assert carried["docProps/custom.xml"].relationship_id == ""
    assert reader.last_omissions == []


def test_the_properties_part_survives_the_round_trip(tmp_path: Path) -> None:
    """A part the package root reaches travels back into the package."""
    source = _with_custom_properties(tmp_path / "source.docx")

    reader = WordReader()
    document = reader.read(source)
    writer = WordWriter()
    target = tmp_path / "written.docx"
    target.write_bytes(writer.write(document))

    assert writer.last_omissions == [], writer.last_omissions
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "docProps/custom.xml" in names, (
            f"the properties part was lost: {sorted(names)}"
        )
        assert archive.read("docProps/custom.xml") == CUSTOM_PROPERTIES_XML
        root_rels = archive.read("_rels/.rels").decode("utf-8")
        document_rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    assert CUSTOM_PROPERTIES_ID in root_rels, (
        "the package root no longer reaches the properties part"
    )
    assert CUSTOM_PROPERTIES_TYPE in root_rels
    assert "docProps/custom.xml" not in document_rels, (
        "the properties part was hung off the document, which does not reach it"
    )


def test_the_chart_is_still_there_afterwards(tmp_path: Path) -> None:
    """Adding a package-level part costs the document nothing else."""
    source = _with_custom_properties(tmp_path / "source.docx")

    reader = WordReader()
    document = reader.read(source)
    writer = WordWriter()
    target = tmp_path / "written.docx"
    target.write_bytes(writer.write(document))

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "word/charts/chart1.xml" in names
        assert "word/embeddings/Microsoft_Excel_Worksheet.xlsx" in names
        assert "word/charts/_rels/chart1.xml.rels" in names
