"""The compatibility corpus and its manifest must agree, mechanically.

Roadmap requirement R.01 asks for a corpus of real DOCX files and vaults that
the later compatibility, migration and recovery work is measured against.
A corpus is only worth something if two things hold: the fixtures are the
ones the requirement asks for, and each fixture behaves the way the manifest
says it behaves.

The second half is the one that rots.  A manifest that claims a corrupt
fixture is refused, while the fixture has quietly been repaired, would let
the M1 agents believe that the corruption path is covered when nothing is
testing it any more.  So this file checks the manifest against the fixtures
in both directions, and it checks the fixtures against the *repository's own
readers* -- :class:`lmeditor.word.reader.WordReader` for the Word packages
and the :mod:`lmeditor.vault` facade for the notes -- rather than against a
second implementation of the same rules that would agree with the first one
by accident.

Nothing here re-implements a reader.  Every claim about what a fixture does
is made by asking the reader, and the manifest's own ``expected`` block says
what the answer must be: a fixture whose entry says ``read`` must be read,
and a fixture whose entry says ``unsupported`` must be refused.  A fixture
that the reader refuses on purpose -- the corrupt ones -- says so in its own
entry, so the test never expects a corrupt file to open.

The manifest is read from ``LMEDITOR_COMPAT_MANIFEST`` when that variable is
set, so a mutated copy can be put on the stand and this file can be shown to
have teeth without touching the committed manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Final

import pytest
from lxml import etree

from lmeditor.core.exceptions import WordParseError
from lmeditor.notebooks import list_notebooks, read_notebook
from lmeditor.vault import list_notes, read_note
from lmeditor.word.model import (
    ListMarker,
    WordBlockKind,
    WordDocument,
    WordRevisionKind,
)
from lmeditor.word.reader import WordReader

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The corpus, and the manifest that describes it.
CORPUS_DIR: Final[Path] = REPO_ROOT / "backend" / "tests" / "fixtures" / "compatibility"

#: The environment variable that points the test at another manifest.
MANIFEST_ENV_VAR: Final = "LMEDITOR_COMPAT_MANIFEST"

#: The generator that writes the corpus and the manifest together.
GENERATOR_NAME: Final = "generate_corpus.py"

#: The files in the corpus directory that are not fixtures.
NON_FIXTURE_FILES: Final[frozenset[str]] = frozenset({"MANIFEST.json", GENERATOR_NAME})

#: The kinds a fixture may carry.
KNOWN_KINDS: Final[frozenset[str]] = frozenset(
    {
        "docx",
        "docx-corrupt",
        "not-docx",
        "vault",
        "vault-note",
        "notebook",
    }
)

#: The compatibility statuses of blueprint section 6, plus ``not-applicable``
#: for the dimensions the corpus does not measure.
KNOWN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "read",
        "display",
        "edit",
        "preserve",
        "export",
        "limited",
        "unsupported",
        "unverified",
        "not-applicable",
    }
)

#: The dimensions every fixture's ``expected`` block carries.
EXPECTED_DIMENSIONS: Final[tuple[str, ...]] = (
    "read",
    "display",
    "edit",
    "preserve",
    "export",
    "limitation",
)

#: The cases the corpus must cover, and the fixture that covers each.
REQUIRED_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("a minimal valid DOCX with headings, bold and italic runs", "docx-minimal"),
    ("a DOCX with a table, merged cells and a repeating header row", "docx-table"),
    ("a DOCX with a numbered list and a bulleted list", "docx-lists"),
    ("a DOCX with a hyperlink and a footnote", "docx-link-footnote"),
    ("a DOCX with an inline image", "docx-image"),
    ("a DOCX with tracked changes", "docx-tracked-changes"),
    ("a DOCX with comments", "docx-comments"),
    ("a Unicode-heavy DOCX", "docx-unicode"),
    ("a DOCX with a very long paragraph and a long table", "docx-difficult-layout"),
    ("a corrupt DOCX: a truncated zip", "docx-corrupt-truncated"),
    ("a corrupt DOCX: malformed document XML", "docx-corrupt-malformed-xml"),
    ("a legacy .doc file", "legacy-word-doc"),
    ("a renamed file that is not a DOCX", "renamed-docx"),
    ("a vault fixture directory", "vault"),
    ("a note with frontmatter and a wiki link", "vault-note-index"),
    ("a note with a non-ASCII name", "vault-note-non-ascii"),
    ("a note with a UTF-8 BOM", "vault-note-bom"),
    ("a notebook fixture with sources and a note membership", "notebook"),
)


# ============================================================================
# Reading the manifest
# ============================================================================


def manifest_path() -> Path:
    """The manifest under test.

    The environment variable is read on every call rather than at import, so
    a run that points it at a mutated copy does not need a different process,
    and the committed manifest is never written to.
    """
    override = os.environ.get(MANIFEST_ENV_VAR)
    if override:
        return Path(override).resolve()
    return CORPUS_DIR / "MANIFEST.json"


def load_manifest() -> dict[str, Any]:
    """The manifest document under test."""
    path = manifest_path()
    assert path.is_file(), (
        f"the compatibility manifest {path} is missing; the corpus cannot be "
        "checked without it"
    )
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def fixtures() -> list[dict[str, Any]]:
    """Every fixture the manifest declares."""
    return list(load_manifest()["fixtures"])


def fixture_by_id(fixture_id: str) -> dict[str, Any]:
    """The manifest entry with the given id."""
    for entry in fixtures():
        if entry.get("id") == fixture_id:
            return entry
    raise AssertionError(f"the manifest has no fixture with the id {fixture_id!r}")


def corpus_path(relative: str) -> Path:
    """The path of a fixture inside the corpus."""
    return CORPUS_DIR / relative


# ============================================================================
# Digests
# ============================================================================


def _sha256(data: bytes) -> str:
    """The hex digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def _tree_digest(root: Path) -> str:
    """The digest of a directory tree, as the generator computes it.

    Every file's path, relative to the tree, and the file's own digest, one
    line per file, in path order.
    """
    lines: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        lines.append(f"{relative}\t{_sha256(path.read_bytes())}\n")
    return _sha256("".join(lines).encode("utf-8"))


# ============================================================================
# The manifest and the directory agree
# ============================================================================


def test_the_manifest_has_a_schema_version_and_a_fixture_list() -> None:
    """The manifest is the document the blueprint's schema describes."""
    manifest = load_manifest()
    assert manifest.get("schema_version") == "1.0", (
        "the compatibility manifest must carry the schema version 1.0"
    )
    assert isinstance(manifest.get("fixtures"), list), (
        "the compatibility manifest must carry a list of fixtures"
    )
    assert manifest["fixtures"], "the compatibility manifest declares no fixture"


def test_every_fixture_declares_the_fields_the_schema_fixes() -> None:
    """Every entry carries the fields the manifest schema names."""
    for entry in fixtures():
        missing = [
            field
            for field in (
                "id",
                "path",
                "kind",
                "exercises",
                "sha256",
                "expected",
                "generator",
                "notes",
            )
            if field not in entry
        ]
        assert not missing, (
            f"the fixture {entry.get('id')!r} does not declare: {missing}"
        )
        expected = entry["expected"]
        assert isinstance(expected, dict), (
            f"the expected block of {entry['id']!r} is not an object"
        )
        absent = [field for field in EXPECTED_DIMENSIONS if field not in expected]
        assert not absent, (
            f"the expected block of {entry['id']!r} does not declare: {absent}"
        )


def test_every_fixture_id_is_unique() -> None:
    """No two fixtures share an id."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in fixtures():
        fixture_id = str(entry.get("id"))
        if fixture_id in seen:
            duplicates.append(fixture_id)
        seen.add(fixture_id)
    assert not duplicates, "the manifest repeats a fixture id: " + ", ".join(duplicates)


def test_every_fixture_kind_is_known() -> None:
    """Every fixture carries a kind from the known set."""
    unknown = sorted(
        {
            str(entry.get("kind"))
            for entry in fixtures()
            if entry.get("kind") not in KNOWN_KINDS
        }
    )
    assert not unknown, (
        "the manifest declares fixtures of an unknown kind: "
        + ", ".join(unknown)
        + f"; the known kinds are {sorted(KNOWN_KINDS)}"
    )


def test_every_expected_status_is_known_and_not_blank() -> None:
    """Every cell of an expected block carries a known status, and no cell is blank."""
    offenders: list[str] = []
    for entry in fixtures():
        for dimension in EXPECTED_DIMENSIONS:
            value = entry["expected"].get(dimension)
            if not isinstance(value, str) or not value.strip():
                offenders.append(f"{entry['id']}: {dimension} is blank")
            elif dimension != "limitation" and value not in KNOWN_STATUSES:
                offenders.append(f"{entry['id']}: {dimension} is {value!r}")
    assert not offenders, (
        "the manifest carries an unknown or blank status: " + "; ".join(offenders)
    )


def test_every_fixture_exists_and_matches_its_digest() -> None:
    """Every path the manifest names is there, is not empty and is what it says it is."""
    offenders: list[str] = []
    for entry in fixtures():
        path = corpus_path(str(entry["path"]))
        if not path.exists():
            offenders.append(f"{entry['id']}: {path} is missing")
            continue
        if entry["kind"] == "vault":
            if not path.is_dir():
                offenders.append(f"{entry['id']}: {path} is not a directory")
            elif not any(item.is_file() for item in path.rglob("*")):
                offenders.append(f"{entry['id']}: {path} is empty")
            elif _tree_digest(path) != entry["sha256"]:
                offenders.append(f"{entry['id']}: {path} does not match its digest")
            continue
        if not path.is_file():
            offenders.append(f"{entry['id']}: {path} is not a file")
            continue
        data = path.read_bytes()
        if not data:
            offenders.append(f"{entry['id']}: {path} is empty")
        elif _sha256(data) != entry["sha256"]:
            offenders.append(f"{entry['id']}: {path} does not match its digest")
    assert not offenders, "; ".join(offenders)


def test_the_corpus_and_the_manifest_agree_in_both_directions() -> None:
    """No fixture is unnamed, and no name points at nothing.

    A file in the corpus that the manifest does not name is a fixture nothing
    measures; a manifest entry that names nothing is a claim nothing backs.
    """
    named_files: set[str] = set()
    named_trees: set[str] = set()
    missing: list[str] = []
    for entry in fixtures():
        relative = str(entry["path"])
        path = corpus_path(relative)
        if not path.exists():
            missing.append(f"{entry['id']}: {relative}")
        elif path.is_dir():
            named_trees.add(relative)
        else:
            named_files.add(relative)
    assert not missing, "the manifest names fixtures that are not there: " + ", ".join(missing)

    on_disk = {
        path.relative_to(CORPUS_DIR).as_posix()
        for path in CORPUS_DIR.rglob("*")
        if path.is_file()
    }
    orphans = sorted(on_disk - named_files - NON_FIXTURE_FILES)
    assert not orphans, (
        "these files are in the corpus but no fixture names them: "
        + ", ".join(orphans)
    )
    for tree in sorted(named_trees):
        assert corpus_path(tree).is_dir(), f"{tree} is named as a tree but is not one"


def test_the_corpus_covers_every_required_case() -> None:
    """The corpus holds a fixture for each case the requirement names."""
    ids = {str(entry.get("id")) for entry in fixtures()}
    missing = [
        f"{case} (expected the fixture {fixture_id!r})"
        for case, fixture_id in REQUIRED_CASES
        if fixture_id not in ids
    ]
    assert not missing, "the corpus does not cover: " + "; ".join(missing)


# ============================================================================
# The corpus is what the generator writes
# ============================================================================


def test_the_committed_corpus_is_what_the_generator_produces(tmp_path: Path) -> None:
    """The generator regenerates the corpus byte for byte.

    The generator writes the manifest from the bytes it has just written, so
    the manifest cannot drift from the fixtures.  This is that promise
    checked from the other side: the generator is run again, in a scratch
    folder, and what it writes must be what is committed.
    """
    generator = CORPUS_DIR / GENERATOR_NAME
    assert generator.is_file(), f"the corpus generator {generator} is missing"
    scratch_generator = tmp_path / GENERATOR_NAME
    shutil.copyfile(generator, scratch_generator)
    result = subprocess.run(
        [sys.executable, str(scratch_generator)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "the corpus generator failed:\n" + result.stdout + result.stderr
    )
    committed = (CORPUS_DIR / "MANIFEST.json").read_bytes()
    regenerated = (tmp_path / "MANIFEST.json").read_bytes()
    assert regenerated == committed, (
        "the generator no longer produces the committed corpus; the fixtures "
        "have to be regenerated and the manifest rewritten"
    )


# ============================================================================
# The corrupt fixtures are corrupt
# ============================================================================


def test_the_truncated_zip_fixture_is_not_a_zip_file() -> None:
    """The truncated package fails to open, and fails as a broken archive."""
    path = corpus_path("docx-corrupt-truncated.docx")
    with pytest.raises(zipfile.BadZipFile):
        with zipfile.ZipFile(path) as archive:
            archive.namelist()


def test_the_malformed_xml_fixture_is_not_well_formed() -> None:
    """The malformed package's document part fails to parse as XML."""
    path = corpus_path("docx-corrupt-malformed-xml.docx")
    with zipfile.ZipFile(path) as archive:
        document = archive.read("word/document.xml")
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(document)


def test_the_corrupt_fixtures_say_so_in_their_manifest_entries() -> None:
    """A fixture the reader refuses has to say so in its own entry."""
    for fixture_id in ("docx-corrupt-truncated", "docx-corrupt-malformed-xml"):
        entry = fixture_by_id(fixture_id)
        assert entry["kind"] == "docx-corrupt", (
            f"{fixture_id} is a corrupt fixture and must be of the kind "
            "'docx-corrupt'"
        )
        assert entry["expected"]["read"] == "unsupported", (
            f"{fixture_id} is refused by the reader, so its manifest entry "
            "must say that reading it is unsupported"
        )
        assert entry["expected"]["limitation"].strip(), (
            f"{fixture_id} must state the concrete restriction"
        )


# ============================================================================
# The Word fixtures behave as the manifest says
# ============================================================================


def _read_document(path: Path) -> WordDocument:
    """Read a package with the repository's own reader."""
    return WordReader().read(path)


def test_every_docx_fixture_behaves_as_its_manifest_entry_says() -> None:
    """Each Word fixture is read, or refused, exactly as the manifest says.

    The expectation comes from the manifest, so a fixture that starts being
    refused -- or starts being read when it was not -- fails here until the
    manifest is corrected.
    """
    offenders: list[str] = []
    for entry in fixtures():
        if entry["kind"] not in ("docx", "docx-corrupt", "not-docx"):
            continue
        path = corpus_path(str(entry["path"]))
        expectation = str(entry["expected"]["read"])
        if expectation in ("read", "limited"):
            try:
                document = _read_document(path)
            except WordParseError as exc:
                offenders.append(f"{entry['id']}: the reader refused it ({exc})")
                continue
            if not document.blocks:
                offenders.append(f"{entry['id']}: the reader read no block")
            continue
        if expectation == "unsupported":
            try:
                _read_document(path)
            except WordParseError:
                continue
            offenders.append(
                f"{entry['id']}: the manifest says it is unsupported, but the "
                "reader read it"
            )
            continue
        offenders.append(
            f"{entry['id']}: the read expectation {expectation!r} is neither "
            "'read' nor 'unsupported'"
        )
    assert not offenders, "; ".join(offenders)


def test_the_reader_loses_nothing_of_the_valid_docx_fixtures() -> None:
    """A valid fixture is read without a single omission."""
    offenders: list[str] = []
    for entry in fixtures():
        if entry["kind"] != "docx":
            continue
        reader = WordReader()
        reader.read(corpus_path(str(entry["path"])))
        if reader.last_omissions:
            offenders.append(f"{entry['id']}: {reader.last_omissions}")
    assert not offenders, "the reader lost something: " + "; ".join(offenders)


def test_the_minimal_fixture_carries_its_headings_and_its_formatting() -> None:
    """The minimal package is read as headings, a paragraph and two formats."""
    document = _read_document(corpus_path("docx-minimal.docx"))
    headings = {
        block.text: block.level
        for block in document.blocks
        if block.kind is WordBlockKind.HEADING
    }
    assert headings.get("Heading Level One") == 1
    assert headings.get("Heading Level Two") == 2
    assert headings.get("Heading Level Three") == 3
    paragraph = next(
        block for block in document.blocks if block.kind is WordBlockKind.PARAGRAPH
    )
    assert any(run.bold for run in paragraph.runs), "the bold run was lost"
    assert any(run.italic for run in paragraph.runs), "the italic run was lost"


def test_the_table_fixture_carries_its_merges_and_its_header_row() -> None:
    """The table package is read as a table with a repeating header row."""
    document = _read_document(corpus_path("docx-table.docx"))
    tables = [block.table for block in document.blocks if block.kind is WordBlockKind.TABLE]
    assert len(tables) == 1, "the table was not read as one table"
    table = tables[0]
    assert table is not None
    assert table.header_rows == 1, "the repeating header row was lost"
    assert len(table.rows) == 4, "the table lost a row"


def test_the_lists_fixture_carries_both_kinds_of_list() -> None:
    """The lists package is read as a numbered list and a bulleted list."""
    document = _read_document(corpus_path("docx-lists.docx"))
    markers = {
        block.list_marker
        for block in document.blocks
        if block.kind is WordBlockKind.LIST_ITEM
    }
    assert ListMarker.NUMBER in markers, "the numbered list was lost"
    assert ListMarker.BULLET in markers, "the bulleted list was lost"


def test_the_link_fixture_carries_its_hyperlink_and_its_footnote() -> None:
    """The hyperlink package is read with the target and the footnote text."""
    document = _read_document(corpus_path("docx-link-footnote.docx"))
    targets = [
        run.hyperlink
        for block in document.blocks
        for run in block.runs
        if run.hyperlink
    ]
    assert targets == ["https://example.org/compatibility-corpus"]
    assert [footnote.text for footnote in document.footnotes] == [
        "The cited compatibility passage."
    ]


def test_the_image_fixtures_carry_their_media_part() -> None:
    """The image packages are read with the media part they embed."""
    for fixture_id in ("docx-image", "docx-large-image"):
        document = _read_document(corpus_path(f"{fixture_id}.docx"))
        assert document.media, f"{fixture_id} lost its media part"
        referenced = [
            run.image_ref
            for block in document.blocks
            for run in block.runs
            if run.image_ref
        ]
        assert referenced, f"{fixture_id} lost the run that points at the image"


def test_the_tracked_changes_fixture_carries_both_revisions() -> None:
    """The tracked-changes package is read with the insertion and the deletion."""
    document = _read_document(corpus_path("docx-tracked-changes.docx"))
    kinds = {
        run.revision.kind
        for block in document.blocks
        for run in block.runs
        if run.revision is not None
    }
    assert WordRevisionKind.INSERTION in kinds, "the tracked insertion was lost"
    assert WordRevisionKind.DELETION in kinds, "the tracked deletion was lost"


def test_the_comments_fixture_carries_its_comment() -> None:
    """The comments package is read with the comment and its anchor."""
    document = _read_document(corpus_path("docx-comments.docx"))
    assert len(document.comments) == 1, "the comment was lost"
    comment = document.comments[0]
    assert comment.author == "Corpus Reviewer"
    assert "comment the corpus anchors" in comment.text
    assert comment.anchor_text == "The commented run."


def test_the_unicode_fixture_keeps_its_text_exactly() -> None:
    """The Unicode package is read without normalising or losing anything."""
    document = _read_document(corpus_path("docx-unicode.docx"))
    text = document.plain_text()
    for needle in (
        "兼容性与迁移语料库",  # CJK
        "مرحبا بالعالم",  # Arabic, right to left
        "שלום עולם",  # Hebrew, right to left
        "e\u0301",  # a combining acute, left decomposed
        "A\u030a",  # a combining ring, left decomposed
        "\u00c5",  # the precomposed ring
        "\U0001f600",  # an emoji outside the BMP
        "\U0001f9d1\u200d\U0001f9d1",  # a zero width joiner
    ):
        assert needle in text, f"the reader lost {needle!r}"


def test_the_difficult_layout_fixture_carries_its_long_paragraph_and_table() -> None:
    """The difficult-layout package is read whole."""
    document = _read_document(corpus_path("docx-difficult-layout.docx"))
    paragraphs = [
        block for block in document.blocks if block.kind is WordBlockKind.PARAGRAPH
    ]
    assert any(len(block.text) > 10_000 for block in paragraphs), (
        "the long paragraph was not read whole"
    )
    tables = [block for block in document.blocks if block.kind is WordBlockKind.TABLE]
    assert len(tables) == 1
    assert tables[0].table is not None
    assert len(tables[0].table.rows) == 151, "the long table lost a row"


def test_the_legacy_doc_fixture_is_refused_as_a_legacy_file() -> None:
    """The legacy binary file is refused, and the refusal says why."""
    with pytest.raises(WordParseError) as failure:
        _read_document(corpus_path("legacy-word-doc.doc"))
    assert "legacy" in str(failure.value).lower(), (
        "the refusal of the legacy file must say that it is a legacy file"
    )


def test_the_renamed_package_is_still_read() -> None:
    """A package under a ``.txt`` name is read: the extension decides nothing."""
    document = _read_document(corpus_path("renamed.txt"))
    assert document.blocks, "the renamed package was not read"
    renamed = corpus_path("renamed.txt").read_bytes()
    minimal = corpus_path("docx-minimal.docx").read_bytes()
    assert renamed == minimal, (
        "the renamed fixture must be the minimal package under another name, "
        "so that the extension is the only difference"
    )


def test_the_plain_text_fixture_is_refused() -> None:
    """A file that was never a package is refused."""
    with pytest.raises(WordParseError):
        _read_document(corpus_path("plain-text.txt"))


# ============================================================================
# The vault and the notebook
# ============================================================================


def test_the_vault_is_read_by_the_vault_facade() -> None:
    """The vault's notes are listed and read by the repository's own vault."""
    root = str(corpus_path("vault"))
    notes = list_notes(root)
    ids = sorted(note.note_id for note in notes)
    assert ids == ["index", "notes/bom", "notes/日本語"], (
        f"the vault holds {ids}"
    )


def test_the_index_note_carries_its_frontmatter_and_both_of_its_links() -> None:
    """The index note's title, tags and links are read as they were written."""
    note = read_note(str(corpus_path("vault")), "index")
    assert note.title == "Compatibility Vault Index", (
        "the title must come from the frontmatter"
    )
    assert note.tags == ["corpus", "vault"]
    assert "日本語" in note.links, "the wiki-link to the note that exists was lost"
    assert "No Such Note" in note.links, (
        "the link to the note that does not exist must be kept as a link"
    )


def test_the_non_ascii_note_is_read() -> None:
    """A note whose name is not ASCII is read, with its own title and tags."""
    note = read_note(str(corpus_path("vault")), "notes/日本語")
    assert note.title == "日本語のノート"
    assert note.tags == ["corpus", "unicode"]
    assert "index" in note.backlinks, "the backlink to the index was lost"


def test_the_bom_note_is_read_and_its_frontmatter_is_not_recognised() -> None:
    """The note with a byte order mark is read, and its frontmatter is lost.

    The byte order mark stands in front of the frontmatter fence, so the
    fence is not the first line of the file and the frontmatter is not
    recognised.  The title falls back to the note's heading, and the
    frontmatter's tags are lost.  This is the restriction the manifest
    states.
    """
    note = read_note(str(corpus_path("vault")), "notes/bom")
    assert note.content.startswith("\ufeff"), "the byte order mark was stripped"
    assert note.title == "Note With A Byte Order Mark"
    assert note.tags == [], "the frontmatter's tags must not be recognised"
    entry = fixture_by_id("vault-note-bom")
    assert entry["expected"]["read"] == "limited", (
        "the BOM note is read, but only partly, so its manifest entry must "
        "say so"
    )
    assert "frontmatter" in entry["expected"]["limitation"], (
        "the BOM note's limitation must name the frontmatter"
    )


def test_the_notebook_is_read_by_the_notebook_facade() -> None:
    """The notebook's manifest is read, with its sources and its notes."""
    root = str(corpus_path("notebook"))
    summaries = list_notebooks(root)
    assert [summary.notebook_id for summary in summaries] == ["research-notebook"]
    notebook = read_notebook(root, "research-notebook")
    assert notebook.source_ids == ["src-corpus-001", "src-corpus-002", "src-corpus-003"]
    assert notebook.note_ids == ["index", "notes/日本語", "notes/bom"]


def test_the_notebook_says_so_in_its_manifest_entry() -> None:
    """The notebook fixture's entry states that it is read."""
    entry = fixture_by_id("notebook")
    assert entry["kind"] == "notebook"
    assert entry["expected"]["read"] == "read"
