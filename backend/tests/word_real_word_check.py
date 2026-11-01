"""Real-Word conformance check: build the artifacts, then ask Word about them.

The whitebox suite checks the package against the contract.  It cannot check
the package against *Word*, and Word has behaviours a perfectly well-formed
package can still violate: it reads two adjacent ``w:tbl`` elements as one
table (W11), and it silently re-lays out a table whose rows do not fill the
grid (W12).  Both were found here, and neither is visible to a reader that
parses the XML.

This module drives Microsoft Word over COM:

* it builds a feature document that touches every construct the writer emits;
* it builds two **negative controls** -- a truncated package and a package
  whose ``word/document.xml`` is not XML -- because a check that only ever
  sees good files proves nothing;
* it runs Word over all three and checks the answers.

Requires Word to be installed.  ``tests/test_word_real_word.py`` skips when it
is not.

Run it directly for a human-readable report::

    python backend/tests/word_real_word_check.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

from lmeditor.word.model import (  # noqa: E402
    ListMarker,
    WordBlock,
    WordBlockKind,
    WordComment,
    WordDocument,
    WordEndnote,
    WordFootnote,
    WordHeaderFooterRef,
    WordMedia,
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
from lmeditor.word.writer import WordWriter  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "word_real_word_check.ps1"

#: The word count Word reports for the feature document, once it is correct.
_FEATURE_TITLE = "LMeditor Word Feature Check"
_FEATURE_FOOTNOTE = "The footnote body that Word must show."
_FEATURE_LINK = "https://example.org/lmeditor"

#: The constructs the second document exercises, by name.
_NEW_COMMENT = "The reviewer asks for a source."
_NEW_ENDNOTE = "The closing note Word must show."
_NEW_INSERTED = "The inserted sentence."
_NEW_DELETED = "The deleted sentence."
_NEW_CUSTOM_STYLE = "Research Note"
_NEW_DEFAULT_HEADER = "Default header for the check."
_NEW_EVEN_HEADER = "Even header for the check."
_NEW_FIRST_HEADER = "First header for the check."

#: A one-pixel PNG, so the document embeds a picture Word can display.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)


def word_is_available() -> bool:
    """Whether Microsoft Word can be driven on this machine."""
    if sys.platform != "win32":
        return False
    if not shutil.which("powershell"):
        return False
    try:
        probe = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$w = New-Object -ComObject Word.Application; $v = $w.Version; "
                "$w.Quit(); Write-Output $v",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def build_feature_document() -> WordDocument:
    """A document touching every construct the writer must get right."""
    narrow_merge = WordTable(
        rows=(
            WordTableRow(
                cells=(
                    WordTableCell(text="Merged cell", row_span=2, is_header=True),
                    WordTableCell(text="B", is_header=True),
                )
            ),
            WordTableRow(
                cells=(
                    WordTableCell(is_merge_continuation=True),
                    WordTableCell(text="D"),
                )
            ),
        ),
        header_rows=1,
    )
    wide_merge = WordTable(
        rows=(
            WordTableRow(
                cells=(
                    WordTableCell(text="Wide merge across two columns", col_span=2),
                    WordTableCell(text="C"),
                )
            ),
            WordTableRow(
                cells=(
                    WordTableCell(text="E"),
                    WordTableCell(text="F", col_span=2),
                )
            ),
        ),
    )
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="b-title",
                kind=WordBlockKind.TITLE,
                text=_FEATURE_TITLE,
                runs=(WordRun(text=_FEATURE_TITLE),),
            ),
            WordBlock(
                block_id="b-h1",
                kind=WordBlockKind.HEADING,
                level=1,
                text="Heading level one",
                runs=(WordRun(text="Heading level one"),),
            ),
            WordBlock(
                block_id="b-h2",
                kind=WordBlockKind.HEADING,
                level=2,
                text="Heading level two",
                runs=(WordRun(text="Heading level two"),),
            ),
            WordBlock(
                block_id="b-para",
                kind=WordBlockKind.PARAGRAPH,
                text="Body text with bold, italic and a superscript marker1.",
                runs=(
                    WordRun(text="Body text with "),
                    WordRun(text="bold", bold=True),
                    WordRun(text=", "),
                    WordRun(text="italic", italic=True),
                    WordRun(text=" and a superscript marker"),
                    WordRun(text="1", superscript=True),
                    WordRun(text="."),
                ),
            ),
            WordBlock(
                block_id="b-footnote",
                kind=WordBlockKind.PARAGRAPH,
                text="A footnote reference1.",
                runs=(
                    WordRun(text="A footnote reference"),
                    WordRun(text="1", superscript=True, footnote_ref="fn-1"),
                    WordRun(text="."),
                ),
            ),
            WordBlock(
                block_id="b-link",
                kind=WordBlockKind.PARAGRAPH,
                text="A hyperlink to the project.",
                runs=(
                    WordRun(text="A hyperlink to the "),
                    WordRun(text="project", hyperlink=_FEATURE_LINK),
                    WordRun(text="."),
                ),
            ),
            WordBlock(
                block_id="b-bullet",
                kind=WordBlockKind.LIST_ITEM,
                level=0,
                list_marker=ListMarker.BULLET,
                text="A bulleted item",
                runs=(WordRun(text="A bulleted item"),),
            ),
            WordBlock(
                block_id="b-number-1",
                kind=WordBlockKind.LIST_ITEM,
                level=0,
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                text="A numbered item",
                runs=(WordRun(text="A numbered item"),),
            ),
            WordBlock(
                block_id="b-number-2",
                kind=WordBlockKind.LIST_ITEM,
                level=1,
                list_marker=ListMarker.NUMBER,
                list_group="g1",
                text="A nested numbered item",
                runs=(WordRun(text="A nested numbered item"),),
            ),
            WordBlock(
                block_id="b-table-narrow",
                kind=WordBlockKind.TABLE,
                table=narrow_merge,
            ),
            WordBlock(
                block_id="b-table-wide",
                kind=WordBlockKind.TABLE,
                table=wide_merge,
            ),
            WordBlock(block_id="b-break", kind=WordBlockKind.PAGE_BREAK, text=""),
            WordBlock(
                block_id="b-after",
                kind=WordBlockKind.HEADING,
                level=1,
                text="After the page break",
                runs=(WordRun(text="After the page break"),),
            ),
        ),
        properties=WordProperties(
            title=_FEATURE_TITLE,
            author="LMeditor",
            subject="Writer conformance check",
            keywords="lmeditor, docx, conformance",
        ),
        footnotes=(WordFootnote(footnote_id="fn-1", text=_FEATURE_FOOTNOTE),),
    )


def build_new_constructs_document() -> WordDocument:
    """A document exercising the constructs the audit gaps added.

    The first document proves the writer still builds what it always built.
    This one proves the *new* constructs are real Word constructs and not
    merely well-formed XML: a comment Word shows, an endnote Word collects, a
    tracked revision Word marks up, a page setup Word applies, a roman list
    Word renders in roman, a custom style Word knows, and a picture Word
    displays.
    """
    revision_author = "Reviewer"
    revision_date = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    return WordDocument(
        blocks=(
            WordBlock(
                block_id="b-comment",
                kind=WordBlockKind.PARAGRAPH,
                text="The anchored sentence.",
                runs=(WordRun(text="The anchored sentence.", comment_refs=("c1",)),),
            ),
            WordBlock(
                block_id="b-endnote",
                kind=WordBlockKind.PARAGRAPH,
                text="A sentence with an endnote.",
                runs=(
                    WordRun(text="A sentence with an endnote."),
                    WordRun(text="", endnote_ref="e1"),
                ),
            ),
            WordBlock(
                block_id="b-revision",
                kind=WordBlockKind.PARAGRAPH,
                text=_NEW_INSERTED,
                runs=(
                    WordRun(
                        text=_NEW_INSERTED,
                        revision=WordRevision(
                            kind=WordRevisionKind.INSERTION,
                            author=revision_author,
                            date=revision_date,
                            revision_id="1",
                        ),
                    ),
                    WordRun(
                        text=_NEW_DELETED,
                        revision=WordRevision(
                            kind=WordRevisionKind.DELETION,
                            author=revision_author,
                            date=revision_date,
                            revision_id="2",
                        ),
                    ),
                ),
            ),
            WordBlock(
                block_id="b-custom-style",
                kind=WordBlockKind.PARAGRAPH,
                text="A paragraph in a custom style.",
                style=_NEW_CUSTOM_STYLE,
                runs=(WordRun(text="A paragraph in a custom style."),),
            ),
            WordBlock(
                block_id="b-roman-1",
                kind=WordBlockKind.LIST_ITEM,
                level=0,
                list_marker=ListMarker.NUMBER,
                list_group="roman",
                number_format="lowerRoman",
                number_text="%1.",
                text="Roman one",
                runs=(WordRun(text="Roman one"),),
            ),
            WordBlock(
                block_id="b-roman-2",
                kind=WordBlockKind.LIST_ITEM,
                level=1,
                list_marker=ListMarker.NUMBER,
                list_group="roman",
                number_format="lowerLetter",
                number_text="%2)",
                text="Alpha one",
                runs=(WordRun(text="Alpha one"),),
            ),
            WordBlock(
                block_id="b-picture",
                kind=WordBlockKind.PARAGRAPH,
                text="",
                runs=(WordRun(text="", image_ref="word/media/image1.png"),),
            ),
        ),
        comments=(
            WordComment(
                comment_id="c1",
                author="Reviewer",
                initials="RV",
                date=revision_date,
                text=_NEW_COMMENT,
                runs=(WordRun(text=_NEW_COMMENT),),
                anchor_block_id="b-comment",
                anchor_text="The anchored sentence.",
            ),
        ),
        endnotes=(
            WordEndnote(
                endnote_id="e1",
                text=_NEW_ENDNOTE,
                runs=(WordRun(text=_NEW_ENDNOTE),),
            ),
        ),
        styles=(
            WordStyle(
                style_id="ResearchNote",
                name=_NEW_CUSTOM_STYLE,
                based_on="Normal",
                style_type="paragraph",
            ),
        ),
        media=(
            WordMedia(
                media_id="word/media/image1.png",
                part_name="word/media/image1.png",
                content_type="image/png",
                data=_TINY_PNG,
            ),
        ),
        sections=(
            WordSection(
                page_width=15840,
                page_height=12240,
                orientation="landscape",
                margin_top=720,
                margin_right=720,
                margin_bottom=720,
                margin_left=720,
                header_refs=(
                    WordHeaderFooterRef(
                        ref_type="default", text=_NEW_DEFAULT_HEADER
                    ),
                    WordHeaderFooterRef(ref_type="first", text=_NEW_FIRST_HEADER),
                    WordHeaderFooterRef(ref_type="even", text=_NEW_EVEN_HEADER),
                ),
            ),
        ),
        properties=WordProperties(
            title="LMeditor Word New Constructs",
            author="LMeditor",
        ),
    )


def check_new_constructs_document(report: WordReport) -> list[str]:
    """The properties Word must report for the new-constructs document."""
    problems: list[str] = []
    if not report.opened:
        problems.append(f"Word refused the document: {report.error}")
        return problems

    def expect(label: str, actual: str, wanted: str) -> None:
        if actual != wanted:
            problems.append(f"{label}: Word reported {actual!r}, expected {wanted!r}")

    expect("comment count", report.value("COMMENT_COUNT"), "1")
    if not report.contains(_NEW_COMMENT):
        problems.append("Word does not show the comment body")
    if not report.contains("author=Reviewer"):
        problems.append("Word does not show the comment author")

    expect("endnote count", report.value("ENDNOTE_COUNT"), "1")
    if not report.contains(f"ENDNOTE1={_NEW_ENDNOTE}"):
        problems.append("Word does not show the endnote body")

    expect("revision count", report.value("REVISION_COUNT"), "2")
    if not report.contains(_NEW_INSERTED):
        problems.append("Word does not show the inserted text as a revision")
    if not report.contains(_NEW_DELETED):
        problems.append("Word does not keep the deleted text as a revision")

    expect("section count", report.value("SECTION_COUNT"), "1")
    expect("orientation", report.field("SECTION1", "orientation"), "1")
    expect("page width", report.field("SECTION1", "page_width"), "792.0")
    expect("page height", report.field("SECTION1", "page_height"), "612.0")
    expect("top margin", report.field("SECTION1", "top_margin"), "36.0")
    expect("inline shape count", report.value("INLINE_SHAPE_COUNT"), "1")

    if not report.contains(f"header_default={_NEW_DEFAULT_HEADER}"):
        problems.append("the default header did not keep its role")
    if not report.contains(f"header_first={_NEW_FIRST_HEADER}"):
        problems.append("the first-page header did not keep its role")
    if not report.contains(f"header_even={_NEW_EVEN_HEADER}"):
        problems.append("the even-page header did not keep its role")

    if report.paragraph_style("A paragraph in a custom style.") != _NEW_CUSTOM_STYLE:
        problems.append("the custom style did not survive into the package")

    if not report.contains("marker=i."):
        problems.append("the roman list is not rendered in roman")
    if not report.contains("marker=a)"):
        problems.append("the alpha list level is not rendered as 'a)'")
    return problems


def build_negative_controls(source: Path, target_dir: Path) -> list[Path]:
    """Build the two packages Word must refuse."""
    truncated = target_dir / "corrupt-truncated.docx"
    truncated.write_bytes(source.read_bytes()[: source.stat().st_size // 2])

    bad_xml = target_dir / "corrupt-document-xml.docx"
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["word/document.xml"] = b"<w:document><w:body><w:p>truncated"
    with zipfile.ZipFile(bad_xml, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return [truncated, bad_xml]


@dataclass(frozen=True)
class WordReport:
    """What Word said about a package."""

    opened: bool
    lines: tuple[str, ...]
    error: str

    def value(self, key: str) -> str:
        """The value of a ``KEY=VALUE`` line, or an empty string."""
        prefix = f"{key}="
        for line in self.lines:
            if line.startswith(prefix):
                return line[len(prefix) :]
        return ""

    def field(self, key: str, name: str) -> str:
        """The value of one named field of a ``KEY|field=value|...`` line.

        The harness packs several fields onto one line, so the field is
        located by its marker rather than at the start of the line.
        """
        marker = f"|{name}="
        for line in self.lines:
            if not line.startswith(f"{key}|"):
                continue
            index = line.find(marker)
            if index < 0:
                continue
            return line[index + len(marker) :].split("|", 1)[0]
        return ""

    def contains(self, needle: str) -> bool:
        """Whether any line contains ``needle``."""
        return any(needle in line for line in self.lines)

    def paragraph_style(self, text: str) -> str:
        """The style Word reports for the paragraph carrying ``text``."""
        for line in self.lines:
            if line.startswith("P") and f"text={text}" in line:
                match = re.search(r"\|style=([^|]*)\|", line)
                if match is not None:
                    return match.group(1)
        return ""


def run_word_check(path: Path) -> WordReport:
    """Ask Word about a package.

    The child's output is decoded as **UTF-8**, not as the machine's locale
    encoding.  PowerShell writes UTF-8 to a redirected pipe, and the locale
    encoding on a Windows machine is usually cp1252, which turns the bullet
    marker into three characters and an em dash into three more.  A harness
    that mis-reads what Word said would mis-read exactly the characters a
    document is most likely to be checked on.
    """
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HARNESS),
            "-Path",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    lines = tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
    opened = any(line == "OPENED=yes" for line in lines)
    error = ""
    for line in lines:
        if line.startswith("ERROR="):
            error = line[len("ERROR=") :]
    return WordReport(opened=opened, lines=lines, error=error)


def check_feature_document(report: WordReport) -> list[str]:
    """The properties Word must report for the feature document."""
    problems: list[str] = []
    if not report.opened:
        problems.append(f"Word refused the document: {report.error}")
        return problems

    def expect(label: str, actual: str, wanted: str) -> None:
        if actual != wanted:
            problems.append(f"{label}: Word reported {actual!r}, expected {wanted!r}")

    expect("table count", report.value("TABLE_COUNT"), "2")
    expect("footnote count", report.value("FOOTNOTE_COUNT"), "1")
    expect("hyperlink count", report.value("HYPERLINK_COUNT"), "1")
    expect("title property", report.value("PROP_TITLE"), _FEATURE_TITLE)
    expect("author property", report.value("PROP_AUTHOR"), "LMeditor")

    if report.paragraph_style(_FEATURE_TITLE) != "Title":
        problems.append("the title paragraph is not styled 'Title'")
    if report.paragraph_style("Heading level one") != "Heading 1":
        problems.append("the first heading is not styled 'Heading 1'")
    if report.paragraph_style("Heading level two") != "Heading 2":
        problems.append("the second heading is not styled 'Heading 2'")
    if not report.contains(f"FOOTNOTE1={_FEATURE_FOOTNOTE}"):
        problems.append("the footnote body is not what the model carried")
    if not report.contains(f"address={_FEATURE_LINK}"):
        problems.append("the hyperlink address did not survive")
    if not report.contains("TABLE1|vertical_merge=yes"):
        problems.append("Word does not see the vertical merge")
    if not report.contains("'Wide merge across two columns' (w=312.0pt)"):
        problems.append("the wide merge is not twice the width of its neighbour")
    if not report.contains("level=2"):
        problems.append("the nested list item is not at level 2 in Word")
    if not report.contains("page=2|text=After the page break"):
        problems.append("the content after the page break is not on page 2")
    return problems


def main() -> int:
    """Build the artifacts, run Word over them, and report."""
    if not word_is_available():
        print("Microsoft Word is not available on this machine; nothing to check.")
        return 0

    target_dir = BACKEND / ".word_real_word_check"
    target_dir.mkdir(exist_ok=True)
    try:
        feature = target_dir / "feature.docx"
        feature.write_bytes(WordWriter().write(build_feature_document()))
        constructs = target_dir / "new-constructs.docx"
        constructs.write_bytes(WordWriter().write(build_new_constructs_document()))
        controls = build_negative_controls(feature, target_dir)

        report = run_word_check(feature)
        problems = check_feature_document(report)

        print("=== feature document, as Word sees it ===")
        for line in report.lines:
            print(f"  {line}")

        constructs_report = run_word_check(constructs)
        print("=== new constructs, as Word sees it ===")
        for line in constructs_report.lines:
            print(f"  {line}")
        problems.extend(check_new_constructs_document(constructs_report))

        for control in controls:
            control_report = run_word_check(control)
            if control_report.opened:
                problems.append(
                    f"Word accepted the broken package {control.name}; "
                    "the check cannot tell a good package from a bad one"
                )
            else:
                print(f"=== {control.name}: refused as expected ===")
                print(f"  {control_report.error}")

        if problems:
            print("\nFAILED:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("\nOK: Word opened the document and saw what the model describes.")
        return 0
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
