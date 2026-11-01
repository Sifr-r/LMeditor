"""The frozen witness of the server's source library.

This is the implementation that used to live in ``lmeditor.server.sources``,
kept exactly as it was when the live module became a door to the Kotlin tier. It
is the yardstick the tier is measured against:
``backend/scripts/verify_kotlin_parity.py`` asks this module and the Kotlin tier
the same questions and compares the answers byte for byte.

The rules frozen here are:

* what a stored source holds: the chunks, the bibliographic record, the byte
  size, the omissions and the moment it was added;
* the summary the HTTP API returns for a stored source;
* the eviction rule -- when the library is full, the oldest entry is dropped;
* the ordering -- every stored source, oldest first;
* and the chunk lookup -- the chunks of the named sources, in the order the ids
  were given, with unknown ids skipped rather than raising.

Two things make this a witness rather than a mirror:

* it is plain Python, and it never calls across the NDJSON seam -- so the
  comparison is between two independent implementations; and
* it is re-aligned to the tier's *current* semantics only when the tier's
  rule deliberately changes, and every such change is recorded in the
  harness's ``REFERENCE_WITNESSES`` -- so a divergence that is not a
  deliberate change is a finding, not a silent edit to the ruler.
"""

from __future__ import annotations

import builtins
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from lmeditor.core.schemas import BibliographicRecord, Source, SourceChunk
from lmeditor.core.types import SourceKind

#: How many sources the library keeps. A source library is a working set for
#: one authoring session, not an archive, and every entry holds its chunks in
#: memory.
MAX_SOURCES: int = 256


@dataclass(frozen=True)
class StoredSource:
    """One ingested source and everything derived from it."""

    source_id: str
    title: str
    kind: SourceKind
    origin: str
    chunks: tuple[SourceChunk, ...]
    record: BibliographicRecord | None = None
    byte_size: int | None = None
    omissions: tuple[str, ...] = ()
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_source(
        cls,
        source: Source,
        chunks: Iterable[SourceChunk],
        *,
        record: BibliographicRecord | None = None,
        omissions: Iterable[str] = (),
    ) -> "StoredSource":
        """Build a stored entry from an ingested source and its chunks."""
        return cls(
            source_id=source.id,
            title=source.title,
            kind=source.kind,
            origin=source.origin,
            chunks=tuple(chunks),
            record=record,
            byte_size=source.byte_size,
            omissions=tuple(omissions),
        )

    def to_summary(self) -> dict[str, object]:
        """A compact description of the source for the client."""
        return {
            "source_id": self.source_id,
            "title": self.title,
            "kind": self.kind.value,
            "origin": self.origin,
            "chunk_count": len(self.chunks),
            "byte_size": self.byte_size,
            "added_at": self.added_at.isoformat(),
            "omissions": list(self.omissions),
            "bibliography": self.record.model_dump(mode="json") if self.record else None,
        }


class SourceLibrary:
    """A bounded, thread-safe registry of ingested sources."""

    def __init__(self, max_sources: int = MAX_SOURCES) -> None:
        self._sources: dict[str, StoredSource] = {}
        self._lock = threading.Lock()
        self._max_sources = max_sources

    def add(self, stored: StoredSource) -> StoredSource:
        """Store a source, evicting the oldest entry when the library is full."""
        with self._lock:
            self._sources[stored.source_id] = stored
            while len(self._sources) > self._max_sources:
                oldest = min(self._sources.values(), key=lambda s: s.added_at)
                self._sources.pop(oldest.source_id, None)
            return stored

    def get(self, source_id: str) -> StoredSource | None:
        """The stored source with this id, if the library holds it."""
        with self._lock:
            return self._sources.get(source_id)

    def remove(self, source_id: str) -> bool:
        """Drop a source. Returns whether it was present."""
        with self._lock:
            return self._sources.pop(source_id, None) is not None

    def list(self) -> "builtins.list[StoredSource]":
        """Every stored source, oldest first.

        The annotation names ``builtins.list`` explicitly: this method is
        called ``list``, and inside the class body that name shadows the
        builtin, so a bare ``list[...]`` would resolve to the method.
        """
        with self._lock:
            return sorted(self._sources.values(), key=lambda s: s.added_at)

    def chunks_for(self, source_ids: Iterable[str]) -> "builtins.list[SourceChunk]":
        """The chunks of the named sources, in the order the ids were given.

        Unknown ids are skipped rather than raising: a client may reference a
        source it uploaded before the server restarted, and a missing source
        should not abort a generation request that has other, valid material.
        """
        wanted = list(source_ids)
        with self._lock:
            found = [self._sources[s] for s in wanted if s in self._sources]
        chunks: list[SourceChunk] = []
        for source in found:
            chunks.extend(source.chunks)
        return chunks

    def clear(self) -> None:
        """Forget every source. Intended for tests."""
        with self._lock:
            self._sources.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sources)


#: The process-wide source library.
source_library = SourceLibrary()


__all__ = ["MAX_SOURCES", "SourceLibrary", "StoredSource", "source_library"]
