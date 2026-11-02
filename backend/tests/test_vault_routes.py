"""The vault routes: a folder of markdown files the client works in.

These tests drive the routes the way the desktop client does, against a real
folder of markdown files, and they hold three properties:

* a vault that is not there is refused, with a message that says which of
  the three ways it was wrong it was;
* a route that cannot be answered says so -- with a 409 while no vault is
  open -- instead of answering with an empty result that looks like success;
* a note id that tries to leave the vault is refused, and nothing outside
  the vault is written.

Every test here that opens a folder names the base the root may live in
(``LMEDITOR_VAULT_BASE``), because the server falls back on the operator's
home directory when no base is configured: where a machine keeps its
temporary folders is not a fact a route test may lean on, so the base is
always named and the answer is always the base's doing.

The rules themselves -- the path safety, the frontmatter, the link and tag
grammar, the backlink index and the search order -- are the Kotlin tier's,
so the tests that exercise them go through the seam, against the real tier.
There is no stub of the tier here: a stubbed tier would prove that the
route calls a function, not that the vault works.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from lmeditor.server.app import create_app
from lmeditor.server.roots import refusal_reason
from lmeditor.server.vault import active_vault_root, close_active_vault
from tests.http_support import async_client

#: Every vault route, with a request that names a note. The routes that
#: need a vault answer 409 while none is open.
_VAULT_ROUTES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("GET", "/api/v1/vault", {}),
    ("GET", "/api/v1/vault/notes", {}),
    ("GET", "/api/v1/vault/note", {"params": {"note_id": "note"}}),
    (
        "PUT",
        "/api/v1/vault/note",
        {"json": {"note_id": "note", "content": "# The Note\n"}},
    ),
    (
        "POST",
        "/api/v1/vault/note",
        {"json": {"note_id": "note", "title": "The Note", "content": "# The Note\n"}},
    ),
    ("DELETE", "/api/v1/vault/note", {"params": {"note_id": "note"}}),
    (
        "POST",
        "/api/v1/vault/note/rename",
        {"json": {"from_id": "note", "to_id": "other", "update_links": False}},
    ),
    ("GET", "/api/v1/vault/search", {"params": {"q": "note"}}),
    ("GET", "/api/v1/vault/tags", {}),
)


def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make one request against the application."""
    app = create_app()

    async def run() -> httpx.Response:
        async with async_client(app) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _open(vault_root: Path) -> None:
    """Open a vault through the route, and fail loudly when it does not open."""
    response = _call("POST", "/api/v1/vault/open", json={"root": str(vault_root)})
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def _vault_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Name the base a vault root may live in.

    The server falls back on the operator's home directory when no base is
    configured, so a test that opens a root without naming a base has an
    answer that depends on where the host keeps its temporary folders.
    The base is named here and it holds the folder these tests use, so the
    answer is the same on every platform.
    """
    monkeypatch.setenv("LMEDITOR_VAULT_BASE", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_active_vault() -> Iterator[None]:
    """Start every test with no vault open, and leave none open behind."""
    close_active_vault()
    yield
    close_active_vault()


def test_opening_a_vault_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """A root that leads nowhere is refused, and the message says so."""
    missing = tmp_path / "no-such-vault"

    response = _call("POST", "/api/v1/vault/open", json={"root": str(missing)})

    assert response.status_code == 400, response.text
    assert "does not exist" in response.json()["detail"]


def test_opening_a_vault_with_a_relative_root_is_refused(tmp_path: Path) -> None:
    """A root that is not an absolute path is refused, and the message says so."""
    response = _call("POST", "/api/v1/vault/open", json={"root": "notes"})

    assert response.status_code == 400, response.text
    assert "not an absolute path" in response.json()["detail"]


def test_opening_a_vault_that_is_a_file_is_refused(tmp_path: Path) -> None:
    """A root that is a file is refused, and the message says so."""
    a_file = tmp_path / "note.md"
    a_file.write_text("# Not a vault\n", encoding="utf-8", newline="\n")

    response = _call("POST", "/api/v1/vault/open", json={"root": str(a_file)})

    assert response.status_code == 400, response.text
    assert "not a directory" in response.json()["detail"]


def test_opening_a_vault_makes_it_the_active_vault(tmp_path: Path) -> None:
    """The folder the operator chose is the vault the server holds."""
    response = _call("POST", "/api/v1/vault/open", json={"root": str(tmp_path)})

    assert response.status_code == 200, response.text
    assert Path(response.json()["root"]) == tmp_path.resolve()
    assert active_vault_root() == str(tmp_path.resolve())


def test_the_named_base_decides_where_a_vault_may_be_not_the_host(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vault opens wherever the named base points, not where the host keeps its files.

    The server falls back on the operator's home directory when no base is
    configured, so a test that opens a root without naming a base has an
    answer that depends on where the host keeps its temporary folders.
    Here the home is moved out of the way and the base is named, and the
    root is still accepted: the answer is the same on every platform.
    """
    home = tmp_path_factory.mktemp("a-home-elsewhere")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # With the home as the base this root would be refused, so the
    # acceptance below is the base's doing and not the home's.
    assert refusal_reason(tmp_path.resolve(), home.resolve()) is not None

    response = _call("POST", "/api/v1/vault/open", json={"root": str(tmp_path)})

    assert response.status_code == 200, response.text
    assert active_vault_root() == str(tmp_path.resolve())


def test_a_refused_root_leaves_the_open_vault_alone(tmp_path: Path) -> None:
    """A root that is refused does not close the vault that was open."""
    _open(tmp_path)

    response = _call("POST", "/api/v1/vault/open", json={"root": str(tmp_path / "nope")})

    assert response.status_code == 400, response.text
    assert active_vault_root() == str(tmp_path.resolve())


@pytest.mark.parametrize(("method", "path", "kwargs"), _VAULT_ROUTES)
def test_every_vault_route_answers_409_when_no_vault_is_open(
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    """The client has to open a vault before it can ask anything of one."""
    response = _call(method, path, **kwargs)

    assert response.status_code == 409, response.text
    assert "vault" in response.json()["detail"].lower()


def test_the_active_vault_reports_its_root_and_its_note_count(tmp_path: Path) -> None:
    """The open vault is reported with the folder it is and what it holds."""
    (tmp_path / "note.md").write_text("# The Note\n", encoding="utf-8", newline="\n")
    (tmp_path / "other.md").write_text("# Other\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault")

    assert response.status_code == 200, response.text
    body = response.json()
    assert Path(body["root"]) == tmp_path.resolve()
    assert body["note_count"] == 2


def test_listing_the_notes_answers_with_their_summaries(tmp_path: Path) -> None:
    """Every note is listed as a summary: enough to navigate by, never the body."""
    (tmp_path / "note.md").write_text("# The Note\n\nthe body\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault/notes")

    assert response.status_code == 200, response.text
    notes = response.json()["notes"]
    assert [note["note_id"] for note in notes] == ["note"]
    assert notes[0]["title"] == "The Note"
    assert notes[0]["path"] == "note.md"
    assert notes[0]["word_count"] == 5


def test_reading_a_note_answers_with_the_file_as_it_is(tmp_path: Path) -> None:
    """A note is read from the file, not from what the app would like it to be."""
    (tmp_path / "note.md").write_text("# The Note\n\nthe body\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault/note", params={"note_id": "note"})

    assert response.status_code == 200, response.text
    note = response.json()["note"]
    assert note["note_id"] == "note"
    assert note["title"] == "The Note"
    assert note["content"] == "# The Note\n\nthe body\n"


def test_a_note_carries_its_links_and_its_backlinks(tmp_path: Path) -> None:
    """The links of a note and the notes that link back to it are the file's."""
    (tmp_path / "note.md").write_text("# The Note\n\nsee [[Other]]\n", encoding="utf-8", newline="\n")
    (tmp_path / "other.md").write_text("# Other\n\nback to [[The Note]]\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault/note", params={"note_id": "note"})

    assert response.status_code == 200, response.text
    note = response.json()["note"]
    assert [link.lower() for link in note["links"]] == ["other"]
    assert [backlink.lower() for backlink in note["backlinks"]] == ["other"]


def test_writing_a_note_changes_the_file_and_nothing_else(tmp_path: Path) -> None:
    """Saving the operator's text changes the file it names, and nothing else."""
    (tmp_path / "note.md").write_text("# The Note\n\nthe body\n", encoding="utf-8", newline="\n")
    (tmp_path / "other.md").write_text("# Other\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call(
        "PUT",
        "/api/v1/vault/note",
        json={"note_id": "note", "content": "# The Note\n\nnew body\n"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["note"]["content"] == "# The Note\n\nnew body\n"
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "# The Note\n\nnew body\n"
    assert (tmp_path / "other.md").read_text(encoding="utf-8") == "# Other\n"


def test_creating_a_note_writes_the_file(tmp_path: Path) -> None:
    """A created note is a real file in the vault."""
    _open(tmp_path)

    response = _call(
        "POST",
        "/api/v1/vault/note",
        json={"note_id": "folder/new", "title": "New", "content": "# New\n"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["note"]["note_id"] == "folder/new"
    assert (tmp_path / "folder" / "new.md").read_text(encoding="utf-8") == "# New\n"


def test_deleting_a_note_removes_the_file(tmp_path: Path) -> None:
    """A deleted note is gone from the disk, and the answer says so."""
    (tmp_path / "note.md").write_text("# The Note\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("DELETE", "/api/v1/vault/note", params={"note_id": "note"})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}
    assert not (tmp_path / "note.md").exists()


def test_renaming_a_note_moves_the_file(tmp_path: Path) -> None:
    """A renamed note is another file, and the answer carries both ids."""
    (tmp_path / "note.md").write_text("# The Note\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call(
        "POST",
        "/api/v1/vault/note/rename",
        json={"from_id": "note", "to_id": "folder/other", "update_links": False},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_id"] == "note"
    assert body["to_id"] == "folder/other"
    assert body["updated_links"] == 0
    assert (tmp_path / "folder" / "other.md").exists()
    assert not (tmp_path / "note.md").exists()


def test_searching_the_vault_answers_with_the_ranked_hits(tmp_path: Path) -> None:
    """The search answers with the hits, in the tier's order."""
    (tmp_path / "note.md").write_text("# The Note\n\nthe body\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault/search", params={"q": "note"})

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results
    assert results[0]["note_id"] == "note"
    assert results[0]["title"] == "The Note"


def test_listing_the_tags_answers_with_their_counts(tmp_path: Path) -> None:
    """Every tag is answered with the number of notes that carry it."""
    (tmp_path / "note.md").write_text("# The Note\n\n#idea\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    response = _call("GET", "/api/v1/vault/tags")

    assert response.status_code == 200, response.text
    tags = response.json()["tags"]
    assert tags
    assert tags[0]["tag"] == "idea"
    assert tags[0]["count"] == 1


def test_a_note_id_that_leaves_the_vault_is_refused(tmp_path: Path) -> None:
    """A note id that tries to leave the vault is refused, and nothing is written."""
    outside = tmp_path.parent / "outside.md"
    _open(tmp_path)

    response = _call(
        "PUT",
        "/api/v1/vault/note",
        json={"note_id": "../outside", "content": "# Escaped\n"},
    )

    assert response.status_code in (400, 404, 409, 422), response.text
    assert not outside.exists()


def test_a_failed_write_keeps_the_file_as_it_was(tmp_path: Path) -> None:
    """A write that is refused leaves the file exactly as it was."""
    (tmp_path / "note.md").write_text("# The Note\n\nthe body\n", encoding="utf-8", newline="\n")
    _open(tmp_path)

    refused = _call(
        "PUT",
        "/api/v1/vault/note",
        json={"note_id": "../outside", "content": "# Escaped\n"},
    )

    assert refused.status_code in (400, 404, 409, 422), refused.text
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "# The Note\n\nthe body\n"
