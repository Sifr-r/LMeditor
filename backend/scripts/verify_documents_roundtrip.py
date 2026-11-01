"""Verification of the Word round trip against a running LMeditor backend.

LMeditor is meant to replace Microsoft Word for the operator, so the library
has to survive a full round trip: a ``.docx`` file is imported as a book of
editable sections, the operator writes their own text into one of those
sections, and the book is exported back as a ``.docx`` that still holds the
document it started from.

This script proves that round trip against a live server, over HTTP only. It
imports a real ``.docx`` fixture, edits the first section with a marker token
nobody would write by accident, exports the book, and then reads the exported
package apart to check that

* the package is a real Word package, with the parts Word needs,
* the edit is in ``word/document.xml``,
* the **control** section -- the second section, which was not edited -- is
  still in ``word/document.xml``,
* the structure survived: the media and the styles the original had are still
  in the package, and the paragraph count is reported against the original's,
* the edit was **stored**, not merely rendered: reading the section back
  returns the edited text.

Two modes:

``--stub-model`` (default)
    The backend is started with the deterministic, offline model double, so
    the whole round trip can be verified on a machine with no model provider
    configured and no API key.

``--no-spawn``
    The backend already running at ``--base-url`` is used as it is.

The fixture is ``tests/fixtures/roundtrip_fixture.docx``: a document with a
title, two headings, a table and an image, so that the import produces
several sections and the export has something to preserve. The two fixtures
that were already there (``minimal.docx`` and ``word_chart_fixture.docx``)
hold a single paragraph each, so a book imported from either has one section
and there is no control section to compare the edit against.

Usage::

    python scripts/verify_documents_roundtrip.py --base-url http://127.0.0.1:8765
    python scripts/verify_documents_roundtrip.py --no-spawn
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

#: The token appended to the section the script edits. It is unmistakable,
#: so finding it in the exported package can only mean one thing.
MARKER = "LMeditorRoundTripMarker"

#: The OOXML parts every Word package has to carry to be a Word package.
REQUIRED_PARTS: tuple[str, ...] = (
    "word/document.xml",
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
)

#: The fixture imported by default.
DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "roundtrip_fixture.docx"
)

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def _ok(message: str) -> None:
    print(f"{GREEN}PASS{RESET}  {message}")


def _fail(message: str) -> None:
    print(f"{RED}FAIL{RESET}  {message}")


def _info(message: str) -> None:
    print(f"{DIM}      {message}{RESET}")


def install_stub_model() -> None:
    """Install the deterministic model double into this process."""
    import dspy

    from lmeditor.llm.stub import StubLM

    dspy.configure(lm=StubLM())
    _info("installed the deterministic stub model")


def spawn_server(port: int, library_dir: Path) -> subprocess.Popen[bytes]:
    """Start a backend configured with the deterministic stub model.

    Args:
        port: The port the backend listens on.
        library_dir: The directory the imported books are written into.

    Returns:
        The running backend process.
    """
    env = dict(os.environ)
    env["LMEDITOR_LM_PROVIDER"] = "stub"
    env["LMEDITOR_LM_MODEL"] = "offline-stub"
    env["LMEDITOR_LIBRARY_DIR"] = str(library_dir)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "lmeditor.server.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    _info(f"starting a backend on port {port} with LMEDITOR_LM_PROVIDER=stub")
    return subprocess.Popen(
        command, env=env, cwd=str(Path(__file__).resolve().parents[1])
    )


def wait_for_health(base_url: str, timeout: float = 60.0) -> bool:
    """Wait until the backend answers its health check."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/v1/health", timeout=5.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _free_port() -> int:
    """Return a port on the loopback interface nothing is listening on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_free(port: int) -> bool:
    """Report whether a port can still be bound on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port_from(base_url: str) -> int | None:
    """Return the port a base url names, or ``None`` when it names none."""
    _, _, tail = base_url.rpartition("://")
    _, _, port = tail.partition(":")
    if not port.isdigit():
        return None
    return int(port)


def _paragraph_count(xml: str) -> int:
    """Return the number of paragraphs in a WordprocessingML part.

    Only ``<w:p>`` and ``<w:p ...>`` are counted: a paragraph element is the
    only one whose tag starts with ``w:p`` and is followed by a space or the
    closing bracket, so ``<w:pPr>`` and ``<w:proofErr>`` are not paragraphs.

    Args:
        xml: The WordprocessingML part.

    Returns:
        The number of paragraphs the part holds.
    """
    return len(re.findall(r"<w:p[ >]", xml))


def _table_rows(xml: str) -> list[list[str]]:
    """Return the rows of the first table in a WordprocessingML part.

    A table is the part of a Word document a plain-text editor cannot see, and
    the cells of a table are what a section's text carries of it. So the cells
    are how the section that holds the table is found.

    Args:
        xml: The WordprocessingML part.

    Returns:
        The cells of each row of the first table, or an empty list when the
        part holds no table.
    """
    match = re.search(r"<w:tbl[ >].*?</w:tbl>", xml, re.DOTALL)
    if match is None:
        return []
    rows: list[list[str]] = []
    for row in re.findall(r"<w:tr[ >].*?</w:tr>", match.group(0), re.DOTALL):
        cells = [
            "".join(re.findall(r"<w:t[ >](.*?)</w:t>", cell, re.DOTALL))
            for cell in re.findall(r"<w:tc[ >].*?</w:tc>", row, re.DOTALL)
        ]
        rows.append(cells)
    return rows


def _document_xml(path: Path) -> str:
    """Return the body of a Word package's document part.

    Args:
        path: The package.

    Returns:
        The document part, or an empty string when the package cannot be read.
    """
    try:
        with zipfile.ZipFile(path) as package:
            return package.read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        return ""


def _fragments(content: str) -> list[str]:
    """Return the pieces of a section's text that have to survive an edit.

    A section's text is its blocks joined by a blank line, and a table block
    renders as one pipe-separated line per row. The exported package holds the
    cells of a table as separate paragraphs, so a table line is checked cell
    by cell.

    Args:
        content: The text of a section.

    Returns:
        The fragments that must be found in the exported package.
    """
    fragments: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if " | " in stripped:
            fragments.extend(
                cell.strip() for cell in stripped.split(" | ") if cell.strip()
            )
        else:
            fragments.append(stripped)
    return fragments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--no-spawn",
        action="store_true",
        help="do not start a backend; use the one already at --base-url",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="the .docx fixture to import",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="how long to wait for the backend to become healthy",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    server: subprocess.Popen[bytes] | None = None
    library_dir: Path | None = None

    if args.no_spawn:
        print(f"No-spawn mode: using the backend already running at {base_url}.")
    else:
        print("Stub mode: verifying the round trip without a model provider.")
        install_stub_model()
        port = _port_from(base_url)
        if port is None or not _port_is_free(port):
            port = _free_port()
            base_url = f"http://127.0.0.1:{port}"
            print(f"The requested port is in use; using {port} instead.")
        library_dir = Path(tempfile.mkdtemp(prefix="lmeditor_roundtrip_books_"))
        server = spawn_server(port, library_dir)
        if not wait_for_health(base_url, timeout=args.timeout):
            _fail("the backend did not become healthy in time")
            if server is not None:
                server.terminate()
            if library_dir is not None:
                shutil.rmtree(library_dir, ignore_errors=True)
            return 1

    try:
        return _run_checks(args, base_url)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                server.kill()
        if library_dir is not None:
            shutil.rmtree(library_dir, ignore_errors=True)


def _run_checks(args: argparse.Namespace, base_url: str) -> int:
    failures = 0
    client = httpx.Client(timeout=120.0)
    fixture = args.fixture.resolve()

    print("\n1. Health")
    try:
        health = client.get(f"{base_url}/api/v1/health")
    except httpx.HTTPError as exc:
        _fail(f"could not reach {base_url}: {exc}")
        return 1
    if health.status_code != 200:
        _fail(f"health returned {health.status_code}")
        return 1
    payload = health.json()
    _ok(f"backend is healthy (version {payload.get('version')})")
    lm = payload.get("lm") or {}
    _info(f"model configured: {lm.get('configured')} ({lm.get('model') or 'none'})")

    print("\n2. Import the Word fixture")
    if not fixture.is_file():
        _fail(f"the fixture is not there: {fixture}")
        return 1
    fixture_bytes = fixture.read_bytes()
    _info(f"fixture: {fixture.name} ({len(fixture_bytes)} bytes)")
    imported = client.post(
        f"{base_url}/api/v1/library/books/import-docx",
        data={"title": "LMeditor Round Trip"},
        files={
            "file": (
                fixture.name,
                fixture_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    if imported.status_code >= 400:
        _fail(f"import returned {imported.status_code}: {imported.text[:300]}")
        return 1
    manifest = imported.json()

    book_id = manifest.get("book_id") or ""
    if not book_id:
        _fail("the manifest carries no book_id")
        return 1
    _ok(f"imported book {book_id}")

    title = manifest.get("title") or ""
    if not title:
        _fail("the manifest carries no title")
        failures += 1
    else:
        _ok(f"the manifest names the book {title!r}")

    sections = manifest.get("sections") or []
    if not sections:
        _fail("the manifest indexes no section at all")
        return 1
    _ok(f"the manifest indexes {len(sections)} section(s)")
    for section in sections:
        _info(
            f"section {section.get('section_id')!r}: {section.get('title')!r}, "
            f"order={section.get('order')}, words={section.get('word_count')}"
        )

    first_id = sections[0].get("section_id") or ""
    if not first_id:
        _fail("the first section of the manifest has no section_id")
        return 1

    print("\n3. Read the first section")
    first_response = client.get(
        f"{base_url}/api/v1/library/books/{book_id}/sections/{first_id}"
    )
    if first_response.status_code != 200:
        _fail(
            f"the first section returned {first_response.status_code}: {first_response.text[:200]}"
        )
        return 1
    original = first_response.json().get("content") or ""
    if not original.strip():
        _fail(f"the first section {first_id!r} carries no text ({len(original)} chars)")
        failures += 1
    else:
        _ok(f"the first section {first_id!r} carries {len(original)} chars of text")
        _info(repr(original[:90]))

    print("\n4. Find the section that holds the table")
    # A table and an image are the parts of a Word document that a plain-text
    # editor cannot see, so the section that holds them is the section whose
    # edit must not lose them. Editing that section is the honest test of the
    # round trip: editing the first one only proves the prose survives.
    table_rows = _table_rows(_document_xml(fixture))
    rich_id = ""
    rich_original = ""
    for section in sections:
        candidate_id = section.get("section_id") or ""
        candidate = client.get(
            f"{base_url}/api/v1/library/books/{book_id}/sections/{candidate_id}"
        )
        if candidate.status_code != 200:
            continue
        text = candidate.json().get("content") or ""
        if table_rows and all(
            cell in text for row in table_rows for cell in row if cell
        ):
            rich_id = candidate_id
            rich_original = text
            break
    if rich_id:
        _ok(f"the section {rich_id!r} holds the table the fixture carries")
    else:
        _info("the fixture holds no table, so the round trip is proved on prose")
        rich_id = first_id
        rich_original = original

    print("\n5. Read the control section")
    control_id = ""
    control_text = ""
    for section in sections:
        candidate_id = section.get("section_id") or ""
        if not candidate_id or candidate_id == rich_id:
            continue
        control_response = client.get(
            f"{base_url}/api/v1/library/books/{book_id}/sections/{candidate_id}"
        )
        if control_response.status_code != 200:
            _fail(
                f"the control section returned {control_response.status_code}: "
                f"{control_response.text[:200]}"
            )
            failures += 1
            continue
        control_id = candidate_id
        control_text = control_response.json().get("content") or ""
        if not control_text.strip():
            _fail(f"the control section {control_id!r} carries no text")
            failures += 1
        else:
            _ok(
                f"the control section {control_id!r} carries {len(control_text)} chars of text"
            )
            _info(repr(control_text[:90]))
        break
    if not control_id:
        _info("the book has one section only, so there is no control section")

    print("\n6. Edit the section")
    edited = f"{rich_original} {MARKER} The operator wrote this sentence into it."
    saved = client.put(
        f"{base_url}/api/v1/library/books/{book_id}/sections/{rich_id}",
        json={"content": edited},
    )
    if saved.status_code >= 400:
        _fail(f"the save returned {saved.status_code}: {saved.text[:300]}")
        return 1
    result = saved.json()

    if result.get("content") != edited:
        _fail("the save returned a content that is not the content that was sent")
        _info(f"sent {len(edited)} chars, got {len(result.get('content') or '')} chars")
        failures += 1
    else:
        _ok(f"the save echoed back the {len(edited)} chars that were sent")

    expected_words = len(edited.split())
    if result.get("word_count") != expected_words:
        _fail(
            f"word_count is {result.get('word_count')}, but the edited text holds "
            f"{expected_words} words"
        )
        failures += 1
    else:
        _ok(f"word_count is {result.get('word_count')}, as the edited text needs")

    if result.get("char_count") != len(edited):
        _fail(
            f"char_count is {result.get('char_count')}, but the edited text holds "
            f"{len(edited)} characters"
        )
        failures += 1
    else:
        _ok(f"char_count is {result.get('char_count')}, as the edited text needs")

    print("\n7. Export the book")
    export = client.get(f"{base_url}/api/v1/library/books/{book_id}/export.docx")
    if export.status_code != 200:
        _fail(f"export returned {export.status_code}: {export.text[:300]}")
        return 1
    package_bytes = export.content
    _ok(f"exported {len(package_bytes)} bytes")

    print("\n8. The exported package")
    if not package_bytes.startswith(b"PK"):
        _fail(f"the export is not a zip package: it starts with {package_bytes[:4]!r}")
        return 1
    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as package:
            names = set(package.namelist())
            document_xml = (
                package.read("word/document.xml").decode("utf-8")
                if "word/document.xml" in names
                else ""
            )
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        _fail(f"the exported package could not be read: {exc}")
        return 1
    _ok("the export is a zip package")

    missing_parts = [part for part in REQUIRED_PARTS if part not in names]
    if missing_parts:
        _fail(f"the exported package is missing {missing_parts}")
        failures += 1
    else:
        _ok(
            f"the package carries the expected OOXML parts ({', '.join(REQUIRED_PARTS)})"
        )

    print("\n9. The edit reached the package")
    if MARKER in document_xml:
        _ok(f"the marker {MARKER} is in word/document.xml")
    else:
        _fail(f"the marker {MARKER} is not in word/document.xml")
        failures += 1

    print("\n10. The control section survived the edit")
    if control_text:
        fragments = _fragments(control_text)
        lost = [fragment for fragment in fragments if fragment not in document_xml]
        if lost:
            _fail(
                f"{len(lost)} of {len(fragments)} fragment(s) of the control section "
                f"are not in word/document.xml"
            )
            for fragment in lost[:3]:
                _info(f"missing: {fragment!r}")
            failures += 1
        else:
            _ok(
                f"all {len(fragments)} fragment(s) of the control section "
                f"{control_id!r} are still in word/document.xml"
            )
            _info(f"control text: {fragments[0]!r}")
    else:
        _info("the book had no second section, so the control check was skipped")

    print("\n11. The structure survived")
    with zipfile.ZipFile(fixture) as original_package:
        original_names = set(original_package.namelist())
        original_xml = original_package.read("word/document.xml").decode("utf-8")
    original_paragraphs = _paragraph_count(original_xml)
    exported_paragraphs = _paragraph_count(document_xml)
    _info(f"paragraphs: original {original_paragraphs}, exported {exported_paragraphs}")
    if exported_paragraphs == 0:
        _fail("the exported word/document.xml holds no paragraph at all")
        failures += 1
    else:
        _ok(
            f"the exported package holds {exported_paragraphs} paragraph(s) "
            f"against the original's {original_paragraphs}"
        )

    media = sorted(name for name in original_names if name.startswith("word/media/"))
    if media:
        lost_media = [name for name in media if name not in names]
        if lost_media:
            _fail(f"the exported package lost the original's media: {lost_media}")
            failures += 1
        else:
            _ok(f"the package still holds the original's media ({len(media)} part(s))")
    else:
        _info("the original held no media part")

    # A media part that nothing anchors any more is a part the reader will
    # not show, so the number of drawings is reported next to the number of
    # media parts: the two together say whether the image survived.
    original_drawings = original_xml.count("<w:drawing>")
    exported_drawings = document_xml.count("<w:drawing>")
    _info(f"drawings: original {original_drawings}, exported {exported_drawings}")
    if original_drawings and exported_drawings == 0:
        _fail("the exported word/document.xml anchors no drawing at all")
        failures += 1
    elif original_drawings:
        _ok(
            f"the exported package anchors {exported_drawings} drawing(s) "
            f"against the original's {original_drawings}"
        )

    if "word/styles.xml" in original_names:
        if "word/styles.xml" in names:
            _ok("the package still holds word/styles.xml")
        else:
            _fail("the exported package lost word/styles.xml")
            failures += 1

    dropped = sorted(original_names - names)
    if dropped:
        _info(f"parts the original had that the export did not carry over: {dropped}")

    print("\n12. The table and the image of the edited section survived")
    if table_rows:
        if "<w:tbl>" not in document_xml:
            _fail("the exported package holds no table at all")
            failures += 1
        else:
            lost_cells = [
                cell
                for row in table_rows
                for cell in row
                if cell and cell not in document_xml
            ]
            if lost_cells:
                _fail(
                    f"{len(lost_cells)} cell(s) of the edited section's table are "
                    f"missing from the export"
                )
                for cell in lost_cells[:3]:
                    _info(f"missing cell: {cell!r}")
                failures += 1
            else:
                _ok(
                    "every cell of the table the edited section held is still in "
                    "word/document.xml"
                )
        if original_drawings != exported_drawings:
            _fail(
                f"the edited section's drawing did not survive: {exported_drawings} "
                f"drawing(s) against the original's {original_drawings}"
            )
            failures += 1
        elif original_drawings:
            _ok(
                f"the edited section still anchors {exported_drawings} drawing(s), "
                f"as the original does"
            )
    else:
        _info("the fixture held no table, so the table check was skipped")

    print("\n13. The edit was stored")
    reread = client.get(
        f"{base_url}/api/v1/library/books/{book_id}/sections/{rich_id}"
    )
    if reread.status_code != 200:
        _fail(f"the edited section could not be read back: {reread.status_code}")
        failures += 1
    else:
        stored = reread.json().get("content") or ""
        if stored == edited:
            _ok("the stored section is the edited text, not a rendering of it")
        else:
            _fail(
                f"the stored section is not the edited text: {len(stored)} chars stored, "
                f"{len(edited)} chars sent"
            )
            failures += 1

    print()
    if failures:
        _fail(f"{failures} check(s) failed")
        return 1
    _ok("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
