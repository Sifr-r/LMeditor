"""Source records must survive a real bridge process restart."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator

import dspy
import pytest

from lmeditor.bridge import BridgeUnavailableError, KotlinBridge
from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import BibliographicRecord, SourceChunk
from lmeditor.core.types import SourceKind
from lmeditor.server.roots import refusal_reason
from lmeditor.server.sources import StoredSource, source_library
from lmeditor.tier import bridge as shared_bridge
from lmeditor.tier.server import source_archive_root
from tests.http_support import async_client


@pytest.fixture(autouse=True)
def _require_persistence_bridge(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[None]:
    """This integration gate must fail, rather than skip, without its real tier."""
    request.node.user_properties.append(("require_live_kotlin_tier", True))
    try:
        with KotlinBridge() as bridge:
            bridge.call("server.sources.len", {"root": str(tmp_path / "probe")})
        yield
    except BridgeUnavailableError as exc:
        pytest.fail(f"source persistence requires the rebuilt Kotlin bridge: {exc}")


def _stored() -> StoredSource:
    text = "The river survey recorded twelve species of fish."
    chunk = SourceChunk.create(
        document_id="survey/2026",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
        metadata={"page": 3, "document_title": "River survey"},
    )
    return StoredSource(
        source_id="survey/2026",
        title="River survey",
        kind=SourceKind.PDF,
        origin="survey.pdf",
        chunks=(chunk,),
        record=BibliographicRecord(title="River survey", authors=["A. Researcher"], year=2026),
        byte_size=4096,
        omissions=("decorative images",),
        added_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
    )


def test_source_record_and_chunks_survive_a_fresh_jvm(tmp_path: Path) -> None:
    root = str(tmp_path / "persistent-sources")
    stored = _stored()
    with KotlinBridge() as first:
        first.call("server.sources.add", {"root": root, "source": stored.to_wire()})
        assert first.is_running
    assert not first.is_running

    with KotlinBridge() as second:
        wire = second.call(
            "server.sources.get", {"root": root, "source_id": stored.source_id}
        )["source"]
        assert StoredSource.from_wire(wire) == stored
        chunks = second.call(
            "server.sources.chunks_for", {"root": root, "source_ids": [stored.source_id]}
        )["chunks"]
        assert [SourceChunk.model_validate(chunk) for chunk in chunks] == list(stored.chunks)
        assert second.call("server.sources.len", {"root": root})["count"] == 1
        assert second.call(
            "server.sources.remove", {"root": root, "source_id": stored.source_id}
        )["removed"] is True

    with KotlinBridge() as third:
        assert third.call(
            "server.sources.get", {"root": root, "source_id": stored.source_id}
        )["source"] is None


def test_normal_backend_facade_restores_sources_after_bridge_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The facade restores its sources after the bridge restarts.

    The archive is a temporary directory, so the test never writes into the
    repository's own source archive, and the library is cleared afterwards, so
    that no other test inherits the source this one stored.
    """
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", str(tmp_path / "facade-sources"))
    stored = _stored()
    try:
        source_library.add(stored)
        shared_bridge._close()
        assert source_library.get(stored.source_id) == stored
        assert source_library.chunks_for([stored.source_id]) == list(stored.chunks)
        assert [source.source_id for source in source_library.list()] == [stored.source_id]
    finally:
        source_library.clear()


def test_switching_archive_roots_isolates_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = str(tmp_path / "one")
    second = str(tmp_path / "two")
    stored = _stored()
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", first)
    try:
        source_library.add(stored)
        monkeypatch.setenv("LMEDITOR_SOURCES_DIR", second)
        assert source_library.get(stored.source_id) is None
        monkeypatch.setenv("LMEDITOR_SOURCES_DIR", first)
        assert source_library.get(stored.source_id) == stored
    finally:
        source_library.clear()


def test_relative_archive_configuration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", "relative-sources")
    with pytest.raises(ValidationError, match="absolute path"):
        source_archive_root()


def test_bridge_refuses_a_relative_archive_root() -> None:
    with KotlinBridge() as bridge:
        with pytest.raises(ValidationError, match="absolute path"):
            bridge.call("server.sources.list", {"root": "relative-sources"})


def test_blank_archive_configuration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", "   ")
    with pytest.raises(ValidationError, match="empty"):
        source_archive_root()


def test_invalid_archive_root_keeps_its_domain_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("existing file", encoding="utf-8")
    monkeypatch.setenv("LMEDITOR_SOURCES_DIR", str(blocked))
    with pytest.raises(ValidationError) as failure:
        source_library.add(_stored())
    assert failure.value.details["field"] == "source_archive"
    assert blocked.read_text(encoding="utf-8") == "existing file"


def test_notebook_ask_restores_its_sources_after_bridge_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from typing import Any

    import httpx

    from lmeditor.llm.stub import StubLM
    from lmeditor.server.app import create_app
    from lmeditor.server.notebooks import close_notebooks_root

    app = create_app()

    def call(method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        async def request() -> httpx.Response:
            async with async_client(app) as client:
                return await client.request(method, path, json=body)

        return asyncio.run(request())

    root = tmp_path / "notebooks"
    root.mkdir()
    # The base is named, so the folder is accepted because the base holds
    # it and not because the host's home happened to hold it.
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(tmp_path))
    stored = _stored()
    close_notebooks_root()
    previous = dspy.settings.lm
    try:
        # The registration is inside the try, so a failure after it still
        # leaves the library as it was found.
        source_library.add(stored)
        assert call("POST", "/api/v1/notebooks/open", {"root": str(root)}).status_code == 200
        created = call("POST", "/api/v1/notebooks", {"title": "Persistent research"})
        assert created.status_code == 200, created.text
        notebook_id = created.json()["notebook"]["notebook_id"]
        attached = call(
            "POST",
            f"/api/v1/notebooks/{notebook_id}/sources",
            {"source_id": stored.source_id},
        )
        assert attached.status_code == 200, attached.text
        shared_bridge._close()
        dspy.configure(lm=StubLM())
        response = call(
            "POST",
            f"/api/v1/notebooks/{notebook_id}/ask",
            {"question": "How many species of fish did the river survey record?"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answered"] is True
        assert {citation["chunk_id"] for citation in body["citations"]} == {
            stored.chunks[0].chunk_id
        }
    finally:
        dspy.configure(lm=previous)
        close_notebooks_root()
        source_library.clear()


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
    import asyncio
    from typing import Any

    import httpx

    from lmeditor.server.app import create_app
    from lmeditor.server.notebooks import close_notebooks_root

    app = create_app()

    def call(method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        async def request() -> httpx.Response:
            async with async_client(app) as client:
                return await client.request(method, path, json=body)

        return asyncio.run(request())

    home = tmp_path_factory.mktemp("a-home-elsewhere")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LMEDITOR_NOTEBOOKS_BASE", str(tmp_path))
    root = tmp_path / "notebooks"
    root.mkdir()

    # With the home as the base this folder would be refused, so the
    # acceptance below is the base's doing and not the home's.
    assert refusal_reason(root.resolve(), home.resolve()) is not None

    try:
        response = call("POST", "/api/v1/notebooks/open", {"root": str(root)})

        assert response.status_code == 200, response.text
    finally:
        close_notebooks_root()
