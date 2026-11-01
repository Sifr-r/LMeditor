"""The source-library door must reach the Kotlin tier, and must not fake it.

``lmeditor.server.sources`` is a *door*: it keeps the Python API the backend
calls and answers with the Kotlin tier's answers. These tests prove that is
what actually happens -- that the answers come from the tier, that the tier
agrees with the frozen Python witness, and that a tier that is not there fails
loudly instead of quietly answering from a second, Python copy of the rule.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from lmeditor._reference import server_sources as py_sources
from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.schemas import BibliographicRecord, Source, SourceChunk
from lmeditor.core.types import SourceKind
from lmeditor.server.sources import (
    MAX_SOURCES,
    SourceLibrary,
    StoredSource,
    source_library,
)
from lmeditor.tier import server as tier_server

pytestmark = pytest.mark.usefixtures("kotlin_tier")

#: The moment every stored source in these tests was added, unless a test says
#: otherwise. A fixed clock keeps the orderings the tests check deterministic.
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@contextmanager
def _empty_library() -> Iterator[None]:
    """Hold the tier's library empty for the length of one test.

    The tier's library is one registry for the whole process, so a test that
    left sources behind would decide what the next test sees. The library is
    emptied before the test and again after it, and both calls are made in the
    test's own body rather than in a fixture, so that a tier which refuses the
    call fails -- or skips -- the test that asked for it and not the one that
    ran before it.
    """
    source_library.clear()
    try:
        yield
    finally:
        source_library.clear()


def _chunk(document_id: str, text: str) -> SourceChunk:
    """A chunk of a document."""
    return SourceChunk.create(
        document_id=document_id,
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _source(source_id: str = "src_1", title: str = "A Paper") -> Source:
    """An ingested source."""
    return Source(
        id=source_id,
        kind=SourceKind.PDF,
        origin=f"C:/docs/{source_id}.pdf",
        title=title,
    )


def _stored(
    source_id: str,
    *,
    title: str = "A Paper",
    kind: SourceKind = SourceKind.PDF,
    origin: str | None = None,
    chunks: tuple[SourceChunk, ...] = (),
    record: BibliographicRecord | None = None,
    byte_size: int | None = 1024,
    omissions: tuple[str, ...] = (),
    added_at: datetime | None = None,
) -> StoredSource:
    """A stored source, built directly rather than from an ingested source."""
    return StoredSource(
        source_id=source_id,
        title=title,
        kind=kind,
        origin=origin if origin is not None else f"C:/docs/{source_id}.pdf",
        chunks=chunks,
        record=record,
        byte_size=byte_size,
        omissions=omissions,
        added_at=added_at if added_at is not None else BASE,
    )


def _as_witness(stored: StoredSource) -> py_sources.StoredSource:
    """The same stored source, in the frozen witness's own class.

    The two classes hold the same fields and are structurally identical, but
    they are two classes: the witness has to be given a witness.
    """
    return py_sources.StoredSource(
        source_id=stored.source_id,
        title=stored.title,
        kind=stored.kind,
        origin=stored.origin,
        chunks=stored.chunks,
        record=stored.record,
        byte_size=stored.byte_size,
        omissions=stored.omissions,
        added_at=stored.added_at,
    )


# ---------------------------------------------------------------------------
# The door calls the tier
# ---------------------------------------------------------------------------


def test_the_door_asks_the_tier(monkeypatch: Any) -> None:
    """The source is stored by the tier, not by this module."""
    calls: list[Mapping[str, Any]] = []
    original = tier_server.add_source

    def _spy(source: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(source)
        return original(source)

    monkeypatch.setattr(tier_server, "add_source", _spy)

    with _empty_library():
        source_library.add(_stored("src_spy"))

        assert len(calls) == 1
        assert calls[0]["source_id"] == "src_spy"


def test_the_door_sends_the_source_in_its_wire_form(monkeypatch: Any) -> None:
    """What crosses the seam is the stored source, field for field."""
    calls: list[Mapping[str, Any]] = []
    original = tier_server.add_source

    def _spy(source: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(source)
        return original(source)

    monkeypatch.setattr(tier_server, "add_source", _spy)

    with _empty_library():
        source_library.add(_stored("src_wire", chunks=(_chunk("doc_wire", "alpha"),)))

        assert set(calls[0]) == {
            "source_id",
            "title",
            "kind",
            "origin",
            "chunks",
            "record",
            "byte_size",
            "omissions",
            "added_at",
        }
        assert calls[0]["kind"] == "pdf"


# ---------------------------------------------------------------------------
# The library answers
# ---------------------------------------------------------------------------


def test_a_stored_source_comes_back_from_the_tier() -> None:
    """What went in is what comes out."""
    record = BibliographicRecord(title="A Paper", authors=["A. Writer"], year=2026)
    stored = _stored(
        "src_roundtrip",
        chunks=(_chunk("doc_roundtrip", "alpha"),),
        record=record,
        omissions=("images",),
    )

    with _empty_library():
        source_library.add(stored)

        assert source_library.get("src_roundtrip") == stored


def test_an_unknown_source_is_not_there() -> None:
    """A miss is a miss, not an error."""
    assert source_library.get("src_never_added") is None


def test_a_source_can_be_removed() -> None:
    """Removing says whether it was there."""
    with _empty_library():
        source_library.add(_stored("src_removable"))

        assert source_library.remove("src_removable") is True
        assert source_library.remove("src_removable") is False
        assert source_library.get("src_removable") is None


def test_the_library_can_be_cleared() -> None:
    """Clearing forgets every source."""
    with _empty_library():
        source_library.add(_stored("src_to_clear"))

        source_library.clear()

        assert len(source_library) == 0
        assert source_library.list() == []


def test_the_library_counts_its_sources() -> None:
    """The count is the tier's count."""
    with _empty_library():
        source_library.add(_stored("src_count_a"))
        source_library.add(_stored("src_count_b"))

        assert len(source_library) == 2


def test_list_returns_the_oldest_first() -> None:
    """The order is the tier's, and it is the order the witness gives."""
    with _empty_library():
        for index in (2, 0, 1):
            source_library.add(
                _stored(f"src_ordered_{index}", added_at=BASE + timedelta(seconds=index))
            )

        assert [stored.source_id for stored in source_library.list()] == [
            "src_ordered_0",
            "src_ordered_1",
            "src_ordered_2",
        ]


def test_chunks_for_keeps_the_order_of_the_ids_and_skips_unknown_ones() -> None:
    """The chunks come back in the order the ids were given."""
    with _empty_library():
        source_library.add(_stored("src_chunk_a", chunks=(_chunk("doc_a", "alpha"),)))
        source_library.add(_stored("src_chunk_b", chunks=(_chunk("doc_b", "beta"),)))

        chunks = source_library.chunks_for(["src_chunk_b", "src_missing", "src_chunk_a"])

        assert [chunk.text for chunk in chunks] == ["beta", "alpha"]


def test_chunks_for_an_empty_request_is_empty() -> None:
    """Asking for nothing is not an error."""
    assert source_library.chunks_for([]) == []


# ---------------------------------------------------------------------------
# The eviction rule is the tier's
# ---------------------------------------------------------------------------


def test_the_oldest_source_survives_memory_cache_eviction() -> None:
    """The cache bound never removes archived evidence."""
    with _empty_library():
        for index in range(MAX_SOURCES + 1):
            source_library.add(
                _stored(f"src_evict_{index:04d}", added_at=BASE + timedelta(seconds=index))
            )

        assert len(source_library) == MAX_SOURCES + 1
        assert source_library.get("src_evict_0000") is not None
        assert source_library.get("src_evict_0001") is not None


def test_archive_listing_keeps_the_oldest_timestamp_after_cache_eviction() -> None:
    """Archive discovery is independent of cache residency."""
    with _empty_library():
        for index in range(MAX_SOURCES + 1):
            source_library.add(
                _stored(f"src_stamp_{index:04d}", added_at=BASE + timedelta(seconds=index))
            )

        kept = source_library.list()

        assert min(stored.added_at for stored in kept) == BASE
        assert kept[0].source_id == "src_stamp_0000"


# ---------------------------------------------------------------------------
# The door and the frozen witness agree
# ---------------------------------------------------------------------------


def test_the_library_agrees_with_the_frozen_witness() -> None:
    """The door, the tier and the frozen witness answer alike."""
    with _empty_library():
        witness = py_sources.SourceLibrary()

        for index in range(3):
            stored = _stored(
                f"src_witness_{index}", added_at=BASE + timedelta(seconds=index)
            )
            source_library.add(stored)
            witness.add(_as_witness(stored))

        assert len(source_library) == len(witness)
        assert [s.source_id for s in source_library.list()] == [
            s.source_id for s in witness.list()
        ]
        assert (source_library.get("src_witness_1") is None) == (
            witness.get("src_witness_1") is None
        )
        assert source_library.remove("src_witness_1") == witness.remove("src_witness_1")
        assert source_library.remove("src_witness_1") == witness.remove("src_witness_1")


def test_archive_retention_intentionally_extends_the_memory_only_witness() -> None:
    """The archive preserves the oldest source that the frozen witness evicts."""
    with _empty_library():
        witness = py_sources.SourceLibrary()

        for index in range(MAX_SOURCES + 1):
            stored = _stored(
                f"src_witness_evict_{index:04d}", added_at=BASE + timedelta(seconds=index)
            )
            source_library.add(stored)
            witness.add(_as_witness(stored))

        assert len(source_library) == len(witness) + 1
        assert [s.source_id for s in source_library.list()] == [
            "src_witness_evict_0000",
            *[s.source_id for s in witness.list()],
        ]


def test_the_chunks_agree_with_the_frozen_witness() -> None:
    """Both sides answer the same chunk lookup the same way."""
    with _empty_library():
        witness = py_sources.SourceLibrary()
        stored = _stored("src_witness_chunk", chunks=(_chunk("doc_w", "alpha"),))
        source_library.add(stored)
        witness.add(_as_witness(stored))

        assert source_library.chunks_for(["src_witness_chunk"]) == witness.chunks_for(
            ["src_witness_chunk"]
        )
        assert source_library.chunks_for(
            ["src_witness_chunk", "src_missing"]
        ) == witness.chunks_for(["src_witness_chunk", "src_missing"])


# ---------------------------------------------------------------------------
# What stays in Python
# ---------------------------------------------------------------------------


def test_from_source_builds_the_stored_source() -> None:
    """The constructor is Python's, and it builds what it always built."""
    source = _source("src_1")
    chunks = (_chunk("doc_1", "alpha"),)
    record = BibliographicRecord(title="A Paper")

    stored = StoredSource.from_source(
        source,
        chunks,
        record=record,
        omissions=("images",),
    )

    assert stored.source_id == "src_1"
    assert stored.title == source.title
    assert stored.kind == source.kind
    assert stored.origin == source.origin
    assert stored.chunks == chunks
    assert stored.record == record
    assert stored.byte_size == source.byte_size
    assert stored.omissions == ("images",)


def test_from_source_agrees_with_the_frozen_witness() -> None:
    """The constructor builds the same entry it always built."""
    source = _source("src_1")
    chunks = (_chunk("doc_1", "alpha"),)
    record = BibliographicRecord(title="A Paper")

    door = StoredSource.from_source(source, chunks, record=record, omissions=("images",))
    witness = dataclasses.replace(
        py_sources.StoredSource.from_source(
            source,
            chunks,
            record=record,
            omissions=("images",),
        ),
        added_at=door.added_at,
    )

    assert door.to_summary() == witness.to_summary()


def test_the_summary_keeps_the_keys_the_http_api_returns() -> None:
    """The summary's keys are the contract the HTTP surface publishes."""
    record = BibliographicRecord(title="A Paper", authors=["A. Writer"], year=2026)
    stored = _stored(
        "src_summary",
        chunks=(_chunk("doc_summary", "alpha"),),
        record=record,
        omissions=("images",),
    )

    summary = stored.to_summary()

    assert set(summary) == {
        "source_id",
        "title",
        "kind",
        "origin",
        "chunk_count",
        "byte_size",
        "added_at",
        "omissions",
        "bibliography",
    }
    assert summary["source_id"] == "src_summary"
    assert summary["kind"] == "pdf"
    assert summary["chunk_count"] == 1
    assert summary["byte_size"] == 1024
    assert summary["omissions"] == ["images"]
    assert summary["bibliography"] == record.model_dump(mode="json")


def test_the_wire_form_round_trips_every_field() -> None:
    """Nothing is lost on the way across the seam."""
    record = BibliographicRecord(title="A Paper", authors=["A. Writer"], year=2026)
    chunks = (_chunk("doc_wire", "alpha"),)
    stored = _stored(
        "src_wire_form",
        chunks=chunks,
        record=record,
        omissions=("images",),
    )

    wire = stored.to_wire()

    assert set(wire) == {
        "source_id",
        "title",
        "kind",
        "origin",
        "chunks",
        "record",
        "byte_size",
        "omissions",
        "added_at",
    }
    assert StoredSource.from_wire(wire) == stored


def test_the_wire_form_spells_a_timestamp_the_way_the_tier_does() -> None:
    """One vocabulary for a timestamp: an ISO-8601 instant in UTC."""
    stored = _stored("src_stamp", added_at=BASE)

    wire = stored.to_wire()

    assert wire["added_at"] == "2026-01-01T00:00:00Z"
    assert StoredSource.from_wire(wire).added_at == BASE


def test_a_timestamp_is_normalised_to_the_instant_it_names() -> None:
    """The tier holds an instant, so the wire carries one."""
    stored = _stored(
        "src_offset",
        added_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=2))),
    )

    wire = stored.to_wire()

    assert wire["added_at"] == "2026-01-01T10:00:00Z"
    assert StoredSource.from_wire(wire).added_at == stored.added_at


# ---------------------------------------------------------------------------
# An unreachable tier fails loudly
# ---------------------------------------------------------------------------


def test_an_unreachable_tier_is_not_swallowed_by_the_door(monkeypatch: Any) -> None:
    """There is no Python fallback: a missing tier is an error, not an answer."""

    def _unavailable(_source: Mapping[str, Any]) -> dict[str, Any]:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_server, "add_source", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        source_library.add(_stored("src_1"))


def test_an_unreachable_tier_is_not_swallowed_by_a_read(monkeypatch: Any) -> None:
    """A read that cannot reach the tier fails the same way."""

    def _unavailable(_source_id: str) -> dict[str, Any] | None:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_server, "get_source", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        source_library.get("src_1")


def test_the_door_holds_a_second_copy_of_nothing() -> None:
    """The door keeps no sources of its own."""
    library = SourceLibrary()

    assert not hasattr(library, "_sources")
