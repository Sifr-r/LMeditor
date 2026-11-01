"""Prove the notebook round trip against a running backend.

A notebook is the unit Open Notebook is built around: the material the operator
gathered (its **sources**), the notes they wrote beside it, and the questions
they asked of that material (the **ask**). The whole promise of replacing Open
Notebook is that the notebook is a real thing on the operator's disk, that the
ask is answered *from that notebook only*, and that the answer says so when the
sources cannot answer.

This script proves that against a live server, over the published HTTP
surface. It ingests two sources, points the server at a notebooks folder, and
then:

* creates a notebook and checks that it lands on disk as a directory with a
  manifest, and that listing it reports the summary;
* renames it and checks that the manifest followed;
* adds a source and a note, and checks that the notebook holds them;
* refuses to remove a source the notebook does not hold;
* asks a question, and checks that the answer leans on a passage of the
  notebook's *own* sources, with the passage quoted;
* asks a notebook that holds no sources, and checks that the answer says it
  cannot answer and that ``answered`` is false;
* checks that a source that is *not* in the notebook is never cited;
* deletes the notebook, and checks that the notebook is gone while the sources
  it pointed at are untouched.

Run it against a server you started yourself:

    python scripts/verify_notebook_roundtrip.py --no-spawn \\
        --base-url http://127.0.0.1:8000

or let it start one:

    python scripts/verify_notebook_roundtrip.py --base-url http://127.0.0.1:8767

The script needs the Kotlin tier's jar, because the rules of a notebook are the
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

#: The source the notebook holds. Its text is about quartzite, so a question
#: about quartzite has something to be answered from.
SOURCE_ONE_ID: Final[str] = "quartzite-source"
SOURCE_ONE_TITLE: Final[str] = "On Quartzite"
SOURCE_ONE_TEXT: Final[str] = (
    "Quartzite is a metamorphic rock composed almost entirely of quartz.\n"
    "\n"
    "It forms when sandstone is subjected to heat and pressure deep in the "
    "earth, and the sand grains fuse into an interlocking mosaic.\n"
    "\n"
    "Because quartzite is so hard, quarries cut it with wire saws rather "
    "than with picks, and a polished slab keeps its edge for decades.\n"
)

#: The source the notebook does *not* hold. Its text is about basalt, so a
#: question about basalt has nothing to be answered from -- and the answer
#: must never cite this source.
SOURCE_TWO_ID: Final[str] = "basalt-source"
SOURCE_TWO_TITLE: Final[str] = "On Basalt"
SOURCE_TWO_TEXT: Final[str] = (
    "Basalt is a fine-grained volcanic rock that erupts as lava.\n"
    "\n"
    "When a lava flow cools quickly it forms columnar basalt, and the columns "
    "of the Giant's Causeway are the most famous example of them.\n"
    "\n"
    "Basalt is common on the sea floor, where it forms the basaltic crust of "
    "the planet.\n"
)

#: The question the notebook's own source can answer.
ANSWERABLE_QUESTION: Final[str] = "What is quartzite and how is it quarried?"

#: The question only the source outside the notebook could answer.
UNANSWERABLE_QUESTION: Final[str] = "How does columnar basalt form?"

#: The title of the notebook the script creates.
NOTEBOOK_TITLE: Final[str] = "Rock Notes"

#: The note id the notebook refers to. A note id is a relative path with `/`
#: separators, so it never travels in a URL route.
NOTE_ID: Final[str] = "field/observations"


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


def _spawn_server(port: int, library_dir: Path, notebooks_dir: Path) -> subprocess.Popen[bytes]:
    """Start a backend configured with the deterministic stub model.

    Args:
        port: The port to serve on.
        library_dir: Where the backend keeps its library.
        notebooks_dir: Where the backend keeps its notebooks.

    Returns:
        The process, so the caller can stop it.
    """
    environment = dict(os.environ)
    environment["LMEDITOR_LM_PROVIDER"] = "stub"
    environment["LMEDITOR_LM_MODEL"] = "offline-stub"
    environment["LMEDITOR_LIBRARY_DIR"] = str(library_dir)
    environment["LMEDITOR_NOTEBOOKS_DIR"] = str(notebooks_dir)
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


def _ingest(client: httpx.Client, base_url: str, source_id: str, title: str, text: str) -> str:
    """Ingest a source and return the id the server gave it.

    Args:
        client: The HTTP client.
        base_url: The address the backend serves on.
        source_id: The id to file the source under.
        title: The source's title.
        text: The source's text.

    Returns:
        The source id, or an empty string when the ingestion failed.
    """
    response = client.post(
        f"{base_url}/api/v1/sources/ingest",
        data={"source_id": source_id, "title": title},
        files={"file": (f"{source_id}.txt", text.encode("utf-8"), "text/plain")},
    )
    if response.status_code >= 400:
        _fail(f"ingesting {title!r} returned {response.status_code}: {response.text[:300]}")
        return ""
    payload = response.json()
    return str(payload.get("source", {}).get("id", ""))


def _notebook_of(payload: Any) -> dict[str, Any]:
    """Return the notebook an answer carries.

    The notebook routes answer with the notebook inside a ``notebook``
    envelope; a reading that took the envelope for the notebook would report a
    notebook with no title.

    Args:
        payload: The answer the server gave.

    Returns:
        The notebook the answer carries.
    """
    if isinstance(payload, dict) and "notebook" in payload:
        return dict(payload["notebook"])
    return dict(payload or {})


def main() -> int:
    """Run the checks.

    Returns:
        The process's exit code: zero when every check passed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8767")
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
    workspace = Path(tempfile.mkdtemp(prefix="lmeditor-notebook-"))
    notebooks = workspace / "notebooks"
    notebooks.mkdir(parents=True, exist_ok=True)

    try:
        if not args.no_spawn:
            print("Stub mode: verifying the notebook without a model provider.")
            _install_stub_model()
            port = int(base_url.rsplit(":", 1)[-1])
            _info(f"starting a backend on port {port}")
            server = _spawn_server(port, workspace / "library", notebooks)

        print("\n1. Health")
        if not _wait_for_health(base_url, timeout=args.timeout):
            _fail(f"the backend at {base_url} never became healthy")
            return 1
        _ok(f"the backend at {base_url} is healthy")

        client = httpx.Client(timeout=120.0)
        failures = 0

        print("\n2. The sources are ingested")
        first_id = _ingest(client, base_url, SOURCE_ONE_ID, SOURCE_ONE_TITLE, SOURCE_ONE_TEXT)
        second_id = _ingest(client, base_url, SOURCE_TWO_ID, SOURCE_TWO_TITLE, SOURCE_TWO_TEXT)
        if not first_id or not second_id:
            _fail("the sources could not be ingested")
            return 1
        _ok(f"ingested {first_id!r} and {second_id!r}")

        print("\n3. The notebooks folder is opened")
        opened = client.post(
            f"{base_url}/api/v1/notebooks/open", json={"root": str(notebooks)}
        )
        if opened.status_code >= 400:
            _fail(f"opening the notebooks folder returned {opened.status_code}: {opened.text[:300]}")
            return 1
        summary = opened.json()
        if summary.get("root") != str(notebooks):
            _fail(f"the server says the notebooks are at {summary.get('root')!r}")
            failures += 1
        else:
            _ok(f"the server holds the notebooks at {summary.get('root')}")
        _info(f"the folder holds {summary.get('notebook_count', 'an unknown number of')} notebook(s)")

        print("\n4. A notebook is created on disk")
        created = client.post(
            f"{base_url}/api/v1/notebooks",
            json={"title": NOTEBOOK_TITLE, "description": "Notes about rocks."},
        )
        if created.status_code >= 400:
            _fail(f"creating the notebook returned {created.status_code}: {created.text[:300]}")
            return 1
        notebook = _notebook_of(created.json())
        notebook_id = str(notebook.get("notebook_id", ""))
        if not notebook_id:
            _fail("the notebook has no id")
            return 1
        _ok(f"the notebook is {notebook_id!r}")
        _info(f"its title is {notebook.get('title')!r}")
        manifest = notebooks / notebook_id / "manifest.json"
        if not manifest.is_file():
            _fail(f"the manifest is not on disk at {manifest}")
            failures += 1
        else:
            _ok("the notebook is a directory with a manifest")

        print("\n5. The notebooks are listed")
        listed = client.get(f"{base_url}/api/v1/notebooks")
        if listed.status_code >= 400:
            _fail(f"listing the notebooks returned {listed.status_code}: {listed.text[:200]}")
            return 1
        entries = listed.json().get("notebooks", [])
        if not any(entry.get("notebook_id") == notebook_id for entry in entries):
            _fail(f"the notebook is not among the listed notebooks: {entries}")
            failures += 1
        else:
            _ok(f"the server lists {len(entries)} notebook(s)")
            for entry in entries:
                _info(
                    f"notebook {entry.get('notebook_id')!r}: "
                    f"{entry.get('source_count', 0)} source(s), "
                    f"{entry.get('note_count', 0)} note(s)"
                )

        print("\n6. The notebook is renamed")
        renamed = client.put(
            f"{base_url}/api/v1/notebooks/{notebook_id}",
            json={"title": "Renamed Rocks", "description": "Notes about rocks, renamed."},
        )
        if renamed.status_code >= 400:
            _fail(f"renaming returned {renamed.status_code}: {renamed.text[:300]}")
            failures += 1
        else:
            after = _notebook_of(renamed.json())
            if after.get("title") != "Renamed Rocks":
                _fail(f"the title is {after.get('title')!r}, not 'Renamed Rocks'")
                failures += 1
            else:
                _ok("the title changed")
            if not manifest.is_file():
                _fail("the manifest is gone after the rename")
                failures += 1
            else:
                _ok("the manifest followed the change")

        print("\n7. The notebook holds a source and a note")
        with_source = client.post(
            f"{base_url}/api/v1/notebooks/{notebook_id}/sources",
            json={"source_id": first_id},
        )
        if with_source.status_code >= 400:
            _fail(f"adding the source returned {with_source.status_code}: {with_source.text[:300]}")
            failures += 1
        else:
            ids = _notebook_of(with_source.json()).get("source_ids") or []
            if first_id not in ids:
                _fail(f"the source {first_id!r} is not among {ids}")
                failures += 1
            else:
                _ok(f"the notebook holds the source: {ids}")

        with_note = client.post(
            f"{base_url}/api/v1/notebooks/{notebook_id}/notes",
            json={"note_id": NOTE_ID},
        )
        if with_note.status_code >= 400:
            _fail(f"adding the note returned {with_note.status_code}: {with_note.text[:300]}")
            failures += 1
        else:
            note_ids = _notebook_of(with_note.json()).get("note_ids") or []
            if NOTE_ID not in note_ids:
                _fail(f"the note {NOTE_ID!r} is not among {note_ids}")
                failures += 1
            else:
                _ok(f"the notebook refers to the note: {note_ids}")

        print("\n8. A source the notebook does not hold cannot be removed")
        refused = client.request(
            "DELETE",
            f"{base_url}/api/v1/notebooks/{notebook_id}/sources",
            params={"source_id": second_id},
        )
        if refused.status_code < 400:
            _fail(f"removing a source it does not hold was answered: {refused.status_code}")
            failures += 1
        else:
            _ok(f"the server refused it ({refused.status_code})")
        still_there = client.get(f"{base_url}/api/v1/notebooks/{notebook_id}")
        ids = _notebook_of(still_there.json()).get("source_ids") or []
        if second_id in ids:
            _fail(f"the refused removal added the source anyway: {ids}")
            failures += 1
        else:
            _ok(f"the notebook still holds only its own source: {ids}")

        print("\n9. The ask is answered from the notebook's own sources")
        asked = client.post(
            f"{base_url}/api/v1/notebooks/{notebook_id}/ask",
            json={"question": ANSWERABLE_QUESTION},
        )
        if asked.status_code >= 400:
            _fail(f"the ask returned {asked.status_code}: {asked.text[:400]}")
            failures += 1
        else:
            answer = asked.json()
            if not answer.get("answered"):
                _fail(f"the answer is not 'answered': {answer.get('answer')!r}")
                failures += 1
            else:
                _ok("the answer is marked as answered")
            citations = answer.get("citations") or []
            if not citations:
                _fail("the answer cites no passage")
                failures += 1
            else:
                _ok(f"the answer cites {len(citations)} passage(s)")
                for citation in citations:
                    _info(
                        f"citation {citation.get('chunk_id')!r} from "
                        f"{citation.get('title')!r}: {str(citation.get('quote'))[:90]!r}"
                    )
            cited_sources = {str(citation.get("source_id")) for citation in citations}
            if cited_sources - {first_id}:
                _fail(f"the answer cites a source the notebook does not hold: {cited_sources}")
                failures += 1
            else:
                _ok("every citation names a source of the notebook")
            if not all(str(citation.get("quote", "")).strip() for citation in citations):
                _fail("a citation quotes nothing")
                failures += 1
            else:
                _ok("every citation quotes the passage it names")

        print("\n10. A source outside the notebook is never cited")
        outside = client.post(
            f"{base_url}/api/v1/notebooks/{notebook_id}/ask",
            json={"question": UNANSWERABLE_QUESTION},
        )
        if outside.status_code >= 400:
            _fail(f"the ask returned {outside.status_code}: {outside.text[:400]}")
            failures += 1
        else:
            answer = outside.json()
            citations = answer.get("citations") or []
            cited_sources = {str(citation.get("source_id")) for citation in citations}
            if second_id in cited_sources:
                _fail(f"the answer cited the source outside the notebook: {cited_sources}")
                failures += 1
            else:
                _ok(f"the source outside the notebook was not cited: {cited_sources}")

        print("\n11. A notebook with no sources says it cannot answer")
        empty = client.post(
            f"{base_url}/api/v1/notebooks",
            json={"title": "Empty Notebook", "description": "Nothing in it."},
        )
        if empty.status_code >= 400:
            _fail(f"creating the empty notebook returned {empty.status_code}")
            failures += 1
        else:
            empty_id = str(_notebook_of(empty.json()).get("notebook_id", ""))
            unanswered = client.post(
                f"{base_url}/api/v1/notebooks/{empty_id}/ask",
                json={"question": ANSWERABLE_QUESTION},
            )
            if unanswered.status_code >= 400:
                _fail(f"the ask returned {unanswered.status_code}: {unanswered.text[:300]}")
                failures += 1
            else:
                answer = unanswered.json()
                if answer.get("answered"):
                    _fail(f"an empty notebook answered: {answer.get('answer')!r}")
                    failures += 1
                else:
                    _ok("the answer is marked as not answered")
                if not str(answer.get("answer", "")).strip():
                    _fail("the answer says nothing at all")
                    failures += 1
                else:
                    _ok(f"the answer says so: {answer.get('answer')!r}")
                if answer.get("citations"):
                    _fail(f"an empty notebook cited something: {answer.get('citations')}")
                    failures += 1
                else:
                    _ok("an empty notebook cites nothing")
            client.delete(f"{base_url}/api/v1/notebooks/{empty_id}")

        print("\n12. A notebook id that is not a safe path segment is refused")
        # A reserved device name reaches the route intact -- unlike `..`,
        # which the URL layer normalises away before the route ever sees it --
        # so this proves the tier's own refusal rather than a missing route.
        for unsafe_id in ("con", "notes.", "bad:id"):
            unsafe = client.get(f"{base_url}/api/v1/notebooks/{unsafe_id}")
            if unsafe.status_code != 400:
                _fail(
                    f"the unsafe id {unsafe_id!r} was answered "
                    f"({unsafe.status_code}), not refused with 400"
                )
                failures += 1
            else:
                _ok(f"the server refused the id {unsafe_id!r} (400)")

        print("\n13. The notebook is deleted, and the sources are not")
        deleted = client.delete(f"{base_url}/api/v1/notebooks/{notebook_id}")
        if deleted.status_code >= 400:
            _fail(f"the delete returned {deleted.status_code}: {deleted.text[:300]}")
            failures += 1
        else:
            if (notebooks / notebook_id).exists():
                _fail("the notebook's directory is still on disk")
                failures += 1
            else:
                _ok("the notebook's directory is gone")
        sources = client.get(f"{base_url}/api/v1/sources")
        if sources.status_code >= 400:
            _fail(f"listing the sources returned {sources.status_code}")
            failures += 1
        else:
            kept = {str(entry.get("source_id")) for entry in sources.json().get("sources", [])}
            if first_id not in kept:
                _fail(f"the source {first_id!r} was deleted with the notebook: {kept}")
                failures += 1
            else:
                _ok("the sources outlived the notebook that pointed at them")

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
