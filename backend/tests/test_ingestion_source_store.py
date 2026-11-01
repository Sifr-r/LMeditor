"""The bounded, self-cleaning store for persisted uploads (P2).

The server persists an uploaded file so the ingestion pipeline can read it
back, and before this store existed nothing ever removed those files. The
store gives them a budget and enforces it: when the budget is exceeded the
files written longest ago are deleted.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lmeditor.core.exceptions import ValidationError
from lmeditor.ingestion.limits import MAX_UPLOAD_BYTES
from lmeditor.ingestion.source_store import SourceStore
from lmeditor.ingestion.url_policy import UnsafeSourceError


def test_store_persists_an_upload_under_its_source_directory(tmp_path: Path) -> None:
    """An upload is written under a per-source directory inside the root."""
    store = SourceStore(tmp_path, max_bytes=10_000)

    path = store.save("src_1", "paper.pdf", b"hello")

    assert path.is_file()
    assert path.read_bytes() == b"hello"
    assert path.parent.name == "src_1"
    assert path.parent.parent == tmp_path.resolve()


def test_store_reports_the_bytes_it_holds(tmp_path: Path) -> None:
    """The store knows how much it holds."""
    store = SourceStore(tmp_path, max_bytes=10_000)
    store.save("src_1", "a.bin", b"12345")
    store.save("src_2", "b.bin", b"123")

    assert store.total_bytes() == 8


def test_store_evicts_the_oldest_file_when_the_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    """The file written longest ago is the one the budget reclaims."""
    store = SourceStore(tmp_path, max_bytes=10)

    first = store.save("src_1", "old.bin", b"0123456789")
    time.sleep(0.01)
    second = store.save("src_2", "new.bin", b"0123456789")

    assert not first.exists()
    assert second.exists()
    assert store.total_bytes() <= store.max_bytes


def test_store_keeps_the_file_that_was_touched_last(tmp_path: Path) -> None:
    """A file that was touched recently survives; the untouched one does not."""
    store = SourceStore(tmp_path, max_bytes=1_000)
    old = store.save("src_1", "old.bin", b"0123456789")
    time.sleep(0.01)
    new = store.save("src_2", "new.bin", b"0123456789")
    time.sleep(0.01)
    store.touch("src_1", "old.bin")

    # A store with a smaller budget over the same root reclaims the older file.
    SourceStore(tmp_path, max_bytes=10).evict()

    assert old.exists()
    assert not new.exists()


def test_store_refuses_an_oversized_upload(tmp_path: Path) -> None:
    """A file over the upload cap is refused before it is written."""
    store = SourceStore(tmp_path, max_bytes=10_000)

    with pytest.raises(ValidationError):
        store.save("src_1", "huge.bin", b"x" * (MAX_UPLOAD_BYTES + 1))

    assert store.total_bytes() == 0


def test_store_refuses_a_source_id_that_would_escape_the_root(
    tmp_path: Path,
) -> None:
    """A traversing source id is refused: the store writes inside its root."""
    store = SourceStore(tmp_path, max_bytes=10_000)

    with pytest.raises(UnsafeSourceError):
        store.save("../../escape", "evil.bin", b"data")

    assert not (tmp_path.parent / "escape").exists()


def test_store_removes_every_file_of_a_source(tmp_path: Path) -> None:
    """A source can be removed as a whole."""
    store = SourceStore(tmp_path, max_bytes=10_000)
    store.save("src_1", "a.bin", b"12345")
    store.save("src_1", "b.bin", b"6789")

    assert store.remove("src_1") is True
    assert store.total_bytes() == 0
    assert store.remove("src_1") is False


def test_store_survives_a_missing_root(tmp_path: Path) -> None:
    """A store whose root does not exist yet reports zero bytes."""
    store = SourceStore(tmp_path / "not-created-yet", max_bytes=10_000)
    assert store.total_bytes() == 0
    assert store.list_files() == []


def test_store_keeps_files_that_fit_the_budget(tmp_path: Path) -> None:
    """Nothing is evicted while the store fits its budget."""
    store = SourceStore(tmp_path, max_bytes=1_000_000)
    store.save("src_1", "a.bin", b"12345")
    store.save("src_2", "b.bin", b"6789")

    assert store.evict() == []
    assert store.total_bytes() == 9


def test_store_directory_is_the_configured_upload_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default store writes under the configured upload directory."""
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(tmp_path))
    store = SourceStore.default()
    assert store.root == Path(tmp_path)
    assert os.path.isdir(store.root) or not store.root.exists()
