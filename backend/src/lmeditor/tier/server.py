"""The server's source library, as the Kotlin tier implements it.

The library is the server's memory of what has been ingested. It used to be a
Python dictionary in the server process; it is now one registry in the
long-lived tier, and these functions are the only way in and out of it.

The functions speak the *wire form* of a stored source -- plain mappings -- and
not the Python dataclass. The dataclass lives in ``lmeditor.server.sources``,
which is a door to this module, so importing it here would be a circle. The
door owns the Python model; this module owns the seam.

The rules are the tier's, and this module holds none of them:

* the archive and cache rule -- cache eviction retains durable records;
* the ordering -- every stored source comes back oldest first; and
* the chunk lookup -- the chunks of the named sources, in the order the ids
  were given, with an id the library does not hold skipped rather than
  refused.

Each function is deliberately thin. A rule that lived here would be a rule that
exists in two places, which is the one thing this package must never become.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from lmeditor.core.exceptions import ValidationError
from lmeditor.tier.bridge import tier_bridge


SOURCES_DIR_ENV = "LMEDITOR_SOURCES_DIR"
DEFAULT_SOURCES_DIR = Path(__file__).resolve().parents[3] / ".lmeditor" / "sources"


def source_archive_root() -> str:
    """Resolve the archive per request so workspace/test root changes take effect."""
    configured = os.environ.get(SOURCES_DIR_ENV)
    if configured and not configured.strip():
        raise ValidationError("the source archive root is empty", field="root", value=configured)
    root = Path(configured) if configured else DEFAULT_SOURCES_DIR
    if not root.is_absolute():
        raise ValidationError(
            "the source archive root must be an absolute path",
            field="root",
            value=str(root),
        )
    # Normalize without resolving symlinks: the storage boundary must see and
    # reject a configured symlink rather than inheriting its hidden target.
    return os.path.abspath(root)


def _params(**values: Any) -> dict[str, Any]:
    return {"root": source_archive_root(), **values}


def add_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """Store a source in the tier's library.

    The tier commits the record to disk before acknowledging it; cache
    eviction does not remove it from the archive.

    Args:
        source: the stored source, in its wire form.

    Returns:
        The stored source, in its wire form.
    """
    result = tier_bridge().call("server.sources.add", _params(source=_wire_map(source)))
    return _wire_map(result.get("source"))


def get_source(source_id: str) -> dict[str, Any] | None:
    """The stored source with this id, if the tier's library holds it.

    Args:
        source_id: the identifier to look up.

    Returns:
        The stored source in its wire form, or ``None``.
    """
    result = tier_bridge().call("server.sources.get", _params(source_id=source_id))
    source = result.get("source")
    return _wire_map(source) if source is not None else None


def remove_source(source_id: str) -> bool:
    """Drop a source from the tier's library.

    Args:
        source_id: the identifier to drop.

    Returns:
        Whether the library held it.
    """
    result = tier_bridge().call("server.sources.remove", _params(source_id=source_id))
    return bool(result.get("removed", False))


def list_sources() -> list[dict[str, Any]]:
    """Every stored source, oldest first.

    The tier holds the timestamps, so the tier decides the order.
    """
    result = tier_bridge().call("server.sources.list", _params())
    return _wire_list(result.get("sources"))


def chunks_for(source_ids: list[str]) -> list[dict[str, Any]]:
    """The chunks of the named sources, in the order the ids were given.

    An id the library does not hold is skipped rather than refused: a client
    may name a source it uploaded before the server restarted, and a missing
    source should not abort a request that has other, valid material.
    """
    result = tier_bridge().call(
        "server.sources.chunks_for",
        _params(source_ids=list(source_ids)),
    )
    return _wire_list(result.get("chunks"))


def clear_sources() -> bool:
    """Forget every source in the tier's library."""
    result = tier_bridge().call("server.sources.clear", _params())
    return bool(result.get("ok", False))


def source_count() -> int:
    """How many sources the tier's library holds."""
    result = tier_bridge().call("server.sources.len", _params())
    count = result.get("count", 0)
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else 0


def _wire_map(value: Any) -> dict[str, Any]:
    """Return a wire object as a mapping, whatever the tier sent."""
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _wire_list(value: Any) -> list[dict[str, Any]]:
    """Return a wire array of objects as a list of mappings."""
    if not isinstance(value, (list, tuple)):
        return []
    return [_wire_map(item) for item in value]


__all__ = [
    "add_source",
    "chunks_for",
    "clear_sources",
    "get_source",
    "list_sources",
    "remove_source",
    "source_count",
    "source_archive_root",
]
