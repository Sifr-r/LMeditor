"""The notebook routes: a folder of notebooks on the operator's disk, and the Ask.

These tests drive the routes the way the desktop client does, against a real
folder of notebooks, and they hold four properties:

* a notebook the operator made is there to be read back, listed, renamed, and
  filled with the sources and the notes it points at;
* a notebook id that is not a safe path segment is refused, and so is a
  request for a source or a note the notebook does not hold;
* a note id -- a path with ``/`` separators -- survives the route, which is
  the one thing that breaks when a note id is put in a URL path;
* the Ask answers from the notebook's own sources and from nothing else, and
  says so plainly when those sources cannot answer the question.

The rules themselves -- the safety of a notebook id, the manifest, the
idempotence of a write, the membership of a source or a note -- are the Kotlin
tier's, so the tests that exercise them go through the seam, against the real
tier. There is no stub of the tier here: a stubbed tier would prove that the
route calls a function, not that a notebook works.

The model is stubbed, because the Ask has to be written by *something*, and
the offline double quotes the passage it was given: that is enough to prove
the answer leans on the notebook's own material.

Every test here that opens a folder names the base the root may live in
(``LMEDITOR_NOTEBOOKS_BASE``), because the server falls back on the
operator's home directory when no base is configured: where a machine keeps
its temporary folders is not a fact a route test may lean on, so the base is
always named and the answer is always the base's doing.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import dspy
import httpx
import pytest

from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import SourceKind
from lmeditor.llm.stub import StubLM
from lmeditor.server.app import create_app
from lmeditor.server.notebooks import close_notebooks_root
from lmeditor.server.roots import refusal_reason
from lmeditor.server.sources import StoredSource, source_library
from tests.http_support import async_client

#: A passage the notebook's own source holds.
PASSAGE = "The median household income in Riverside was 54000 dollars in 2019."

#: A second passage, in a source the notebook does not hold.
OTHER_PASSAGE = "The median household income in Ashford was 61000 dollars in 2019."


def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make one request against the application."""
    app = create_app()

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _open(root: Path) -> httpx.Response:
    """Open a notebooks folder through the route."""
    return _call("POST", "/api/v1/notebooks/open", json={"root": str(root)})


def _create(title: str, *, description: str = "", notebook_id: str | None = None) -> httpx.Response:
    """Create a notebook through the route."""
    body: dict[str, Any] = {"title": title, "description": description}
    if notebook_id is not None:
        body["notebook_id"] = notebook_id
    return _call("POST", "/api/v1/notebooks", json=body)


def _notebook_id(response: httpx.Response) -> str:
    """The id of the notebook a create answered with."""
    return str(response.json()["notebook"]["notebook_id"])


def _store_source(source_id: str, title: str, text: str) -> str:
    """Store a source with one passage in the library, and return its chunk id."""
    chunk = SourceChunk.create(
        document_id=source_id,
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
        metadata={"document_title": title},
    )
    source_library.add(
        StoredSource(
            source_id=source_id,
            title=title,
            kind=SourceKind.PDF,
            origin=f"C:/docs/{source_id}.pdf",
            chunks=(chunk,),
        )
    )
    return chunk.chunk_id


@contextmanager
def _stub_model() -> Iterator[StubLM]:
    """Install the offline double as the model, and take it away again."""
    previous = dspy.settings.lm
    stub = StubLM()
    dspy.configure(lm=stub)
    try:
        yield stub
    finally:
        dspy.configure(lm=previous)


@pytest.fixture(autouse=True)
def _notebooks_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the base a notebooks root may live in.

    The server falls back on the operator's home directory when no base is
    configured, so a test that opens a root without naming a base has an
    answer that depends on where the host keeps its temporary folders.
    The base is named here and it holds the folder these tests use, so the
    answer is the same on every platform.
    """
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(tmp_path))


@pytest.fixture(autouse=True)
def _credential_file_outside_the_notebooks(
    suite_credential: None,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the API's token file out of the folder the notebooks live in.

    The suite's own fixture points the token file at the test's temporary
    folder, and this file's temporary folder is the notebooks' folder: a
    test that says the folder holds nothing would then be saying it about
    the credential's file rather than about the notebooks. The file goes
    somewhere of its own; the token it holds is the suite's either way.
    """
    monkeypatch.setenv(
        "LMEDITOR_API_TOKEN_FILE",
        str(tmp_path_factory.mktemp("api-token") / "api-token"),
    )


@pytest.fixture(autouse=True)
def _no_open_notebooks() -> Iterator[None]:
    """Start every test with no notebooks folder open, and leave none open."""
    close_notebooks_root()
    yield
    close_notebooks_root()


def test_opening_the_notebooks_folder_makes_it_the_one_the_server_holds(tmp_path: Path) -> None:
    """The folder the operator chose is the folder the server holds."""
    response = _open(tmp_path)

    assert response.status_code == 200, response.text
    body = response.json()
    assert Path(body["root"]) == tmp_path.resolve()
    assert body["name"] == tmp_path.resolve().name
    assert body["notebook_count"] == 0


def test_the_named_base_decides_where_the_notebooks_may_be_not_the_host(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notebooks folder is accepted because the base holds it, not because the home did.

    The server falls back on the operator's home directory when no base is
    configured, so a test that opens a root without naming a base has an
    answer that depends on where the host keeps its temporary folders.
    Here the home is moved out of the way and the base is named, and the
    folder is still accepted: the answer is the same on every platform.
    """
    home = tmp_path_factory.mktemp("a-home-elsewhere")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # With the home as the base this folder would be refused, so the
    # acceptance below is the base's doing and not the home's.
    assert refusal_reason(tmp_path.resolve(), home.resolve()) is not None

    response = _open(tmp_path)

    assert response.status_code == 200, response.text
    assert Path(response.json()["root"]) == tmp_path.resolve()


def test_opening_a_notebooks_folder_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """A root that leads nowhere is refused, and the message says so."""
    response = _open(tmp_path / "no-such-folder")

    assert response.status_code == 400, response.text
    assert "does not exist" in response.json()["detail"]


def test_opening_a_notebooks_folder_with_a_relative_root_is_refused() -> None:
    """A root that is not an absolute path is refused, and the message says so."""
    response = _call("POST", "/api/v1/notebooks/open", json={"root": "notebooks"})

    assert response.status_code == 400, response.text
    assert "not an absolute path" in response.json()["detail"]


def test_a_notebook_is_created_read_back_and_listed(tmp_path: Path) -> None:
    """A notebook the operator made is there to be read back and listed."""
    _open(tmp_path)

    created = _create("My Notebook", description="about")
    assert created.status_code == 200, created.text
    notebook = created.json()["notebook"]
    assert notebook["title"] == "My Notebook"
    assert notebook["description"] == "about"
    assert notebook["source_ids"] == []
    assert notebook["note_ids"] == []
    assert notebook["notebook_id"]

    read = _call("GET", f"/api/v1/notebooks/{notebook['notebook_id']}")
    assert read.status_code == 200, read.text
    assert read.json()["notebook"] == notebook

    listed = _call("GET", "/api/v1/notebooks")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["notebook_count"] == 1
    assert [entry["notebook_id"] for entry in body["notebooks"]] == [notebook["notebook_id"]]
    assert body["notebooks"][0]["source_count"] == 0
    assert body["notebooks"][0]["note_count"] == 0


def test_creating_a_notebook_twice_is_idempotent(tmp_path: Path) -> None:
    """Creating the same notebook twice leaves one notebook, unchanged."""
    _open(tmp_path)

    first = _create("My Notebook")
    second = _create("My Notebook")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["notebook"] == second.json()["notebook"]

    listed = _call("GET", "/api/v1/notebooks")
    assert listed.status_code == 200, listed.text
    assert listed.json()["notebook_count"] == 1


def test_a_notebook_is_renamed(tmp_path: Path) -> None:
    """A notebook can be renamed and re-described."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    renamed = _call(
        "PUT",
        f"/api/v1/notebooks/{notebook_id}",
        json={"title": "Renamed", "description": "about something else"},
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["notebook"]["title"] == "Renamed"
    assert renamed.json()["notebook"]["description"] == "about something else"


def test_a_notebook_holds_a_source_and_lets_it_go(tmp_path: Path) -> None:
    """A notebook holds a source it points at, and can let it go."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    added = _call("POST", f"/api/v1/notebooks/{notebook_id}/sources", json={"source_id": "src_1"})
    assert added.status_code == 200, added.text
    assert added.json()["notebook"]["source_ids"] == ["src_1"]

    removed = _call(
        "DELETE",
        f"/api/v1/notebooks/{notebook_id}/sources",
        params={"source_id": "src_1"},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["notebook"]["source_ids"] == []


def test_a_notebook_refers_to_a_note_and_stops_referring_to_it(tmp_path: Path) -> None:
    """A notebook refers to a note it points at, and can let it go."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    added = _call(
        "POST",
        f"/api/v1/notebooks/{notebook_id}/notes",
        json={"note_id": "folder/note.md"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["notebook"]["note_ids"] == ["folder/note.md"]

    removed = _call(
        "DELETE",
        f"/api/v1/notebooks/{notebook_id}/notes",
        params={"note_id": "folder/note.md"},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["notebook"]["note_ids"] == []


def test_a_note_id_with_a_slash_round_trips(tmp_path: Path) -> None:
    """A note id is a path with ``/`` separators, and it survives the route.

    The id travels in the body on the way in and in the query string on the
    way out, never in the path: a percent-encoded slash in a route does not
    survive the routing, and a note id is exactly the kind of id that carries
    one.
    """
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))
    note_id = "deep/folder/note.md"

    added = _call("POST", f"/api/v1/notebooks/{notebook_id}/notes", json={"note_id": note_id})
    assert added.status_code == 200, added.text
    assert added.json()["notebook"]["note_ids"] == [note_id]

    read = _call("GET", f"/api/v1/notebooks/{notebook_id}")
    assert read.status_code == 200, read.text
    assert read.json()["notebook"]["note_ids"] == [note_id]

    removed = _call(
        "DELETE",
        f"/api/v1/notebooks/{notebook_id}/notes",
        params={"note_id": note_id},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["notebook"]["note_ids"] == []


def test_removing_a_source_the_notebook_does_not_hold_is_refused(tmp_path: Path) -> None:
    """A removal that changed nothing is refused, and the message says so."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    response = _call(
        "DELETE",
        f"/api/v1/notebooks/{notebook_id}/sources",
        params={"source_id": "src_1"},
    )

    assert response.status_code == 400, response.text
    assert "does not hold the source" in response.json()["detail"]


def test_reading_a_notebook_that_is_not_there_is_a_404(tmp_path: Path) -> None:
    """A notebook that is not in the folder is a 404, not an empty notebook."""
    _open(tmp_path)

    response = _call("GET", "/api/v1/notebooks/no-such-notebook")

    assert response.status_code == 404, response.text


def test_an_unsafe_notebook_id_is_refused(tmp_path: Path) -> None:
    """A notebook id that is not a safe path segment is refused, and writes nothing."""
    _open(tmp_path)

    for unsafe_id in ("%2E%2E", "bad%3Aid"):
        response = _call("GET", f"/api/v1/notebooks/{unsafe_id}")

        assert response.status_code == 400, response.text
        assert "unsafe notebook_id" in response.json()["detail"]
    assert list(tmp_path.iterdir()) == []


def test_a_notebook_without_a_title_is_refused(tmp_path: Path) -> None:
    """A title with nothing in it is refused: a notebook must be nameable."""
    _open(tmp_path)

    response = _call("POST", "/api/v1/notebooks", json={"title": "   "})

    assert response.status_code == 400, response.text
    assert list(tmp_path.iterdir()) == []


def test_a_body_that_is_malformed_is_refused(tmp_path: Path) -> None:
    """A body the contract does not describe is refused by FastAPI itself."""
    _open(tmp_path)

    response = _call("POST", "/api/v1/notebooks", json={"title": 42})

    assert response.status_code == 422, response.text


def test_the_ask_answers_from_the_notebook_own_sources(tmp_path: Path) -> None:
    """The answer leans on a passage of the notebook's own sources, and quotes it."""
    source_library.clear()
    try:
        _open(tmp_path)
        chunk_id = _store_source("src_1", "Census Notes", PASSAGE)
        notebook_id = _notebook_id(_create("My Notebook"))
        _call("POST", f"/api/v1/notebooks/{notebook_id}/sources", json={"source_id": "src_1"})

        with _stub_model():
            response = _call(
                "POST",
                f"/api/v1/notebooks/{notebook_id}/ask",
                json={"question": "What was the median household income in Riverside?"},
            )

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["answered"] is True
        assert answer["answer"]
        assert [citation["chunk_id"] for citation in answer["citations"]] == [chunk_id]
        citation = answer["citations"][0]
        assert citation["source_id"] == "src_1"
        assert citation["title"] == "Census Notes"
        assert PASSAGE in citation["quote"]
    finally:
        # The library is the server's own and it is process-wide, so a source
        # left in it would be the next test's evidence.
        source_library.clear()


def test_the_ask_of_a_notebook_with_no_sources_is_not_answered(tmp_path: Path) -> None:
    """A notebook with no sources says so, and is not answered."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    with _stub_model():
        response = _call(
            "POST",
            f"/api/v1/notebooks/{notebook_id}/ask",
            json={"question": "What is in this notebook?"},
        )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["answered"] is False
    assert answer["answer"]
    assert answer["citations"] == []


@pytest.mark.parametrize("with_available_source", [True, False])
def test_missing_notebook_sources_refuse_before_model_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_available_source: bool,
) -> None:
    """Missing evidence is visible even when another source could answer."""
    from lmeditor.drafting.notebook import NotebookAnswerer

    source_library.clear()
    try:
        _open(tmp_path)
        notebook_id = _notebook_id(_create("Incomplete Notebook"))
        if with_available_source:
            _store_source("src_available", "Census Notes", PASSAGE)
            added = _call(
                "POST",
                f"/api/v1/notebooks/{notebook_id}/sources",
                json={"source_id": "src_available"},
            )
            assert added.status_code == 200, added.text

        for source_id in ("src_missing_a", "src_missing_b"):
            _store_source(source_id, "Unavailable Notes", OTHER_PASSAGE)
            added = _call(
                "POST",
                f"/api/v1/notebooks/{notebook_id}/sources",
                json={"source_id": source_id},
            )
            assert added.status_code == 200, added.text
            assert source_library.remove(source_id)

        def refuse_inference(*args: Any, **kwargs: Any) -> None:
            pytest.fail("An incomplete source scope must not reach the model")

        monkeypatch.setattr(NotebookAnswerer, "forward", refuse_inference)
        response = _call(
            "POST",
            f"/api/v1/notebooks/{notebook_id}/ask",
            json={"question": "What was the median household income in Riverside?"},
        )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert "missing sources" in detail
        assert "src_missing_a" in detail
        assert "src_missing_b" in detail
        assert "src_available" not in detail
    finally:
        # The library is the server's own and it is process-wide, so a source
        # left in it would be the next test's evidence.
        source_library.clear()


def test_a_source_that_is_not_in_the_notebook_is_never_cited(tmp_path: Path) -> None:
    """The answer leans on the notebook's own sources and on no other."""
    source_library.clear()
    try:
        _open(tmp_path)
        _store_source("src_other", "Other Notes", OTHER_PASSAGE)
        chunk_id = _store_source("src_1", "Census Notes", PASSAGE)
        notebook_id = _notebook_id(_create("My Notebook"))
        _call("POST", f"/api/v1/notebooks/{notebook_id}/sources", json={"source_id": "src_1"})

        with _stub_model():
            response = _call(
                "POST",
                f"/api/v1/notebooks/{notebook_id}/ask",
                json={"question": "What was the median household income?"},
            )

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["answered"] is True
        assert [citation["chunk_id"] for citation in answer["citations"]] == [chunk_id]
        assert [citation["source_id"] for citation in answer["citations"]] == ["src_1"]
    finally:
        # The library is the server's own and it is process-wide, so a source
        # left in it would be the next test's evidence.
        source_library.clear()


def test_a_question_that_is_empty_is_refused(tmp_path: Path) -> None:
    """A question with nothing in it is refused: there is nothing to answer."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    response = _call("POST", f"/api/v1/notebooks/{notebook_id}/ask", json={"question": "  "})

    assert response.status_code == 400, response.text


def test_a_notebook_is_deleted(tmp_path: Path) -> None:
    """A deleted notebook is gone, and the folder says so."""
    _open(tmp_path)
    notebook_id = _notebook_id(_create("My Notebook"))

    deleted = _call("DELETE", f"/api/v1/notebooks/{notebook_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True}

    listed = _call("GET", "/api/v1/notebooks")
    assert listed.json()["notebook_count"] == 0
