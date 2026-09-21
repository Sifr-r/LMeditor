"""Tests for SourceLoaderRegistry dispatch and runtime validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.registry import SourceLoaderRegistry


# ============================================================================
# 1. Coverage tests
# ============================================================================


def test_registry_resolves_every_kind() -> None:
    """Every SourceKind must map to a non-None loader whose .kind matches."""
    reg = SourceLoaderRegistry.default()
    for kind in SourceKind:
        loader = reg.resolve(kind)
        assert loader is not None
        assert loader.kind == kind


def test_registry_unknown_kind_raises() -> None:
    """An unknown kind string must raise SourceLoadError, not KeyError/ValueError."""
    reg = SourceLoaderRegistry.default()
    with pytest.raises(SourceLoadError):
        reg.resolve("nonexistent_kind")  # type: ignore[arg-type]


# ============================================================================
# 2. Dispatch test
# ============================================================================


def test_registry_loads_dispatches_to_correct_loader(tmp_path) -> None:
    """reg.load must delegate to the loader mapped from SourceKind."""
    reg = SourceLoaderRegistry.default()
    text_file = tmp_path / "note.txt"
    text_file.write_text("dispatched to text loader", encoding="utf-8")
    text, page_count = reg.load(SourceKind.TEXT, text_file)
    assert "dispatched to text loader" in text
    assert page_count is None
