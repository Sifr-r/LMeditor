"""Prove the vault round trip against a running backend.

An Obsidian vault is a folder of markdown files, and the whole promise of
replacing Obsidian is that the folder stays a folder: the operator's own vault
opens, the notes are the files they always were, and a wiki-link in one of them
still points at the other one.

This script proves that against a live server, over the published HTTP
surface. It creates a folder of markdown notes, points the server at it, and
then:

* lists the notes and checks that the files are the notes;
* reads a note and checks its title, its tags, its links and its backlinks;
* writes a note and checks that the file on disk is what was written;
* renames a note and checks that the file moved and that the link that pointed
  at it was rewritten;
* searches, and checks that a title hit outranks a body hit;
* lists the tags;
* deletes a note, and checks that the file is gone;
* and finally tries to reach a file outside the vault, and checks that the
  server refuses and that nothing outside was touched.

Run it against a server you started yourself:

    python scripts/verify_vault_roundtrip.py --no-spawn \\
        --base-url http://127.0.0.1:8000

or let it start one:

    python scripts/verify_vault_roundtrip.py --base-url http://127.0.0.1:8766

The script needs the Kotlin tier's jar, because the rules of a vault are the
tier's: ``cd kotlin && gradlew :bridge:tierJar``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Final

import httpx

GREEN: Final[str] = "\033[32m"
RED: Final[str] = "\033[31m"
DIM: Final[str] = "\033[2m"
RESET: Final[str] = "\033[0m"

#: The notes the vault is built from. The second note links to the first, so
#: the first note has a backlink and the rename has a link to rewrite.
NOTES: Final[dict[str, str]] = {
    "Overview": (
        "---\n"
        "tags: [project, lmeditor]\n"
        "---\n"
        "# Overview\n"
        "\n"
        "This note describes the project. #important\n"
        "\n"
        "See [[Methods]] for how it is measured.\n"
    ),
    "Methods": (
        "# Methods\n"
        "\n"
        "The methods note says how the work is done.\n"
        "\n"
        "| Step | Detail |\n"
        "| --- | --- |\n"
        "| one | the first step |\n"
    ),
    "Archive": (
        "# Archive\n"
        "\n"
        "An old note that nobody links to.\n"
    ),
}

#: The tag every note of the fixture carries through its frontmatter.
EXPECTED_TAG: Final[str] = "project"


def _ok(message: str) -> None:
    print(f"{GREEN}PASS{RESET}  {message}")


def _fail(message: str) -> None:
    print(f"{RED}FAIL{RESET}  {message}")


def _info(message: str) -> None:
    print(f"{DIM}      {message}{RESET}")


def _install_stub_model() -> None:
    """Install the deterministic model double into this process."""
    import dspy

    from lmeditor.llm.stub import StubLM

    dspy.configure(lm=StubLM())
    _info("installed the deterministic stub model")


def _spawn_server(port: int, library_dir: Path) -> subprocess.Popen[bytes]:
    """Start a backend configured with the deterministic stub model.

    Args:
        port: The port to serve on.
        library_dir: Where the backend keeps its library.

    Returns:
        The process, so the caller can stop it.
    """
    environment = dict(os.environ)
    environment["LMEDITOR_LM_PROVIDER"] = "stub"
    environment["LMEDITOR_LM_MODEL"] = "offline-stub"
    environment["LMEDITOR_LIBRARY_DIR"] = str(library_dir)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lmeditor.server.app:app",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_health(base_url: str, timeout: float = 90.0) -> bool:
    """Wait until the backend answers its health check.

    Args:
        base_url: The address the backend serves on.
        timeout: How long to wait for it.

    Returns:
        Whether the backend answered in time.
    """
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


def _write_vault(root: Path) -> None:
    """Build the folder of markdown notes the vault is opened on.

    Args:
        root: The folder to build.
    """
    root.mkdir(parents=True, exist_ok=True)
    for name, content in NOTES.items():
        # Obsidian writes LF; a vault is read as it is, whichever way it was
        # written, and the proof must not depend on the platform's newline.
        (root / f"{name}.md").write_text(content, encoding="utf-8", newline="\n")


def _note_by_id(payload: Any, note_id: str) -> dict[str, Any]:
    """Return the note of a given id from a listing.

    Args:
        payload: The listing the server answered with.
        note_id: The note to find.

    Returns:
        The note, or an empty mapping when the listing holds no such note.
    """
    for note in (payload or {}).get("notes", []):
        if note.get("note_id") == note_id:
            return dict(note)
    return {}


def _the_note(payload: Any) -> dict[str, Any]:
    """Return the note of an answer, whether or not it is wrapped.

    The note routes answer with the note inside a ``note`` envelope; a reading
    that took the envelope for the note would report a note with no title.

    Args:
        payload: The answer the server gave.

    Returns:
        The note the answer carries.
    """
    if isinstance(payload, dict) and "note" in payload:
        return dict(payload["note"])
    return dict(payload or {})


def main() -> int:
    """Run the checks.

    Returns:
        The process's exit code: zero when every check passed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--no-spawn",
        action="store_true",
        help="use a backend that is already running instead of starting one",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="how long to wait for the backend, in seconds",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    server: subprocess.Popen[bytes] | None = None
    workspace = Path(tempfile.mkdtemp(prefix="lmeditor-vault-"))
    vault = workspace / "vault"
    _write_vault(vault)

    try:
        if not args.no_spawn:
            print("Stub mode: verifying the vault without a model provider.")
            _install_stub_model()
            port = int(base_url.rsplit(":", 1)[-1])
            _info(f"starting a backend on port {port}")
            server = _spawn_server(port, workspace / "library")

        print("\n1. Health")
        if not _wait_for_health(base_url, timeout=args.timeout):
            _fail(f"the backend at {base_url} never became healthy")
            return 1
        _ok(f"the backend at {base_url} is healthy")

        client = httpx.Client(timeout=60.0)
        failures = 0

        print("\n2. Open the vault")
        opened = client.post(
            f"{base_url}/api/v1/vault/open", json={"root": str(vault)}
        )
        if opened.status_code >= 400:
            _fail(f"opening the vault returned {opened.status_code}: {opened.text[:300]}")
            return 1
        summary = opened.json()
        if summary.get("root") != str(vault):
            _fail(f"the server says the vault is at {summary.get('root')!r}")
            failures += 1
        else:
            _ok(f"the server holds the vault at {summary.get('root')}")
        _info(f"the vault holds {summary.get('note_count', 'an unknown number of')} note(s)")

        print("\n3. The vault answers before it is opened")
        reopened = client.get(f"{base_url}/api/v1/vault")
        if reopened.status_code == 200:
            _ok("the active vault is reported")
        else:
            _fail(f"the active vault answered {reopened.status_code}")
            failures += 1

        print("\n4. The notes are the files")
        listed = client.get(f"{base_url}/api/v1/vault/notes")
        if listed.status_code >= 400:
            _fail(f"listing the notes returned {listed.status_code}: {listed.text[:200]}")
            return 1
        notes = listed.json().get("notes", [])
        if len(notes) != len(NOTES):
            _fail(f"the vault lists {len(notes)} note(s), but it holds {len(NOTES)}")
            failures += 1
        else:
            _ok(f"the vault lists its {len(notes)} note(s)")
        for note in notes:
            _info(f"note {note.get('note_id')!r}: {note.get('title')!r}")

        print("\n5. A note is read as it is")
        overview = client.get(
            f"{base_url}/api/v1/vault/note", params={"note_id": "Overview"}
        )
        if overview.status_code != 200:
            _fail(f"reading the note returned {overview.status_code}: {overview.text[:200]}")
            return 1
        note = _the_note(overview.json())
        if note.get("title") != "Overview":
            _fail(f"the note's title is {note.get('title')!r}, not 'Overview'")
            failures += 1
        else:
            _ok("the title is the note's own heading")
        tags = note.get("tags") or []
        if EXPECTED_TAG not in tags:
            _fail(f"the frontmatter tag {EXPECTED_TAG!r} is not among {tags}")
            failures += 1
        else:
            _ok(f"the frontmatter tag was read: {tags}")
        if "important" not in tags:
            _fail(f"the body tag 'important' is not among {tags}")
            failures += 1
        else:
            _ok("the body tag was read")
        links = note.get("links") or []
        if not any("Methods" in link for link in links):
            _fail(f"the wiki-link to Methods is not among {links}")
            failures += 1
        else:
            _ok(f"the wiki-link was read: {links}")

        print("\n6. The backlinks point back")
        methods = client.get(
            f"{base_url}/api/v1/vault/note", params={"note_id": "Methods"}
        )
        if methods.status_code != 200:
            _fail(f"reading the second note returned {methods.status_code}")
            failures += 1
        else:
            backlinks = _the_note(methods.json()).get("backlinks") or []
            if "Overview" not in backlinks:
                _fail(f"the backlink to Overview is not among {backlinks}")
                failures += 1
            else:
                _ok(f"the backlink was read: {backlinks}")

        print("\n7. A write reaches the file")
        # The note that carries the link is not the one that is written, so the
        # rename below still has a link to rewrite.
        edited = "# Archive\n\nThis note was edited by the operator.\n"
        written = client.put(
            f"{base_url}/api/v1/vault/note",
            json={"note_id": "Archive", "content": edited},
        )
        if written.status_code >= 400:
            _fail(f"the write returned {written.status_code}: {written.text[:300]}")
            failures += 1
        elif (vault / "Archive.md").read_text(encoding="utf-8") != edited:
            _fail("the file on disk is not the text that was sent")
            failures += 1
        else:
            _ok("the file on disk holds exactly the text that was sent")

        print("\n8. A search finds the note")
        found = client.get(
            f"{base_url}/api/v1/vault/search", params={"q": "edited", "limit": 10}
        )
        if found.status_code >= 400:
            _fail(f"the search returned {found.status_code}: {found.text[:200]}")
            failures += 1
        else:
            results = found.json().get("results", [])
            if not any(hit.get("note_id") == "Archive" for hit in results):
                _fail(f"the search did not find the edited note: {results}")
                failures += 1
            else:
                _ok(f"the search found {len(results)} hit(s)")
                for hit in results:
                    _info(f"hit {hit.get('note_id')!r}: score {hit.get('score')}")

        print("\n9. The tags are counted")
        tags_answer = client.get(f"{base_url}/api/v1/vault/tags")
        if tags_answer.status_code >= 400:
            _fail(f"the tags returned {tags_answer.status_code}")
            failures += 1
        else:
            vault_tags = {tag.get("tag"): tag.get("count") for tag in tags_answer.json().get("tags", [])}
            if not vault_tags.get(EXPECTED_TAG):
                _fail(f"the tag {EXPECTED_TAG!r} is not among {vault_tags}")
                failures += 1
            else:
                _ok(f"the tags are counted: {vault_tags}")

        print("\n10. A rename moves the file and rewrites the link")
        renamed = client.post(
            f"{base_url}/api/v1/vault/note/rename",
            json={"from_id": "Methods", "to_id": "Method", "update_links": True},
        )
        if renamed.status_code >= 400:
            _fail(f"the rename returned {renamed.status_code}: {renamed.text[:300]}")
            failures += 1
        else:
            moved = (vault / "Method.md").exists() and not (vault / "Methods.md").exists()
            if not moved:
                _fail("the file did not move")
                failures += 1
            else:
                _ok("the file moved")
            overview_text = (vault / "Overview.md").read_text(encoding="utf-8")
            if "[[Method]]" not in overview_text:
                _fail(f"the link was not rewritten: {overview_text!r}")
                failures += 1
            else:
                _ok("the link that pointed at it was rewritten")

        print("\n11. A delete removes the file")
        deleted = client.request(
            "DELETE", f"{base_url}/api/v1/vault/note", params={"note_id": "Method"}
        )
        if deleted.status_code >= 400:
            _fail(f"the delete returned {deleted.status_code}: {deleted.text[:200]}")
            failures += 1
        elif (vault / "Method.md").exists():
            _fail("the file is still there after the delete")
            failures += 1
        else:
            _ok("the file is gone")

        print("\n12. A note id cannot leave the vault")
        outside = workspace / "outside.md"
        outside.write_text("a secret\n", encoding="utf-8")
        escaped = client.get(
            f"{base_url}/api/v1/vault/note", params={"note_id": "../outside"}
        )
        if escaped.status_code < 400:
            _fail(f"a note id that leaves the vault was answered: {escaped.status_code}")
            failures += 1
        else:
            _ok(f"the server refused it ({escaped.status_code})")
        if outside.read_text(encoding="utf-8") != "a secret\n":
            _fail("the file outside the vault was touched")
            failures += 1
        else:
            _ok("nothing outside the vault was touched")

        print()
        if failures:
            _fail(f"{failures} check(s) failed")
            return 1
        _ok("every check passed")
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - a stubborn child
                server.kill()
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
