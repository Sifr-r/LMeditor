"""The frozen bounded store for the persisted uploads.

This is the original Python implementation of ``ingestion.source_store``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Its imports are rewired onto the frozen siblings in this package -- the limits and the URL policy
-- so that it cannot reach the seam by accident: a witness that leaned on a
door would be measuring the tier against itself.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

A bounded, self-cleaning store for the uploads the server persists.

The server writes an uploaded file to disk so that the ingestion pipeline can
read it back. Nothing ever removed those files: every upload was kept until
the machine ran out of disk. This store gives the persisted files a budget
and enforces it.

The rules are deliberately simple:

* one directory per source, so a source's files are found together and can be
  removed together;
* a total-bytes budget for the whole store;
* when the budget is exceeded, the files that were written longest ago are
  deleted until the store fits again.

Deletion order is by modification time, and :meth:`SourceStore.touch` moves a
file's timestamp forward, so a source that is still being read stays while an
abandoned one goes."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from lmeditor._reference.ingestion_limits import MAX_SOURCE_STORE_BYTES, validate_upload_size
from lmeditor._reference.ingestion_url_policy import safe_upload_path, upload_root

logger = logging.getLogger(__name__)


class SourceStore:
    """Persists uploaded sources under a total-bytes budget."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = MAX_SOURCE_STORE_BYTES,
    ) -> None:
        self._root = Path(root) if root is not None else upload_root()
        self._max_bytes = max_bytes

    @classmethod
    def default(cls) -> "SourceStore":
        """Return a store rooted at the configured upload directory."""
        return cls(upload_root())

    @property
    def root(self) -> Path:
        """Return the directory the store writes into."""
        return self._root

    @property
    def max_bytes(self) -> int:
        """Return the total-bytes budget of the store."""
        return self._max_bytes

    def path_for(self, source_id: str, filename: str) -> Path:
        """Return the path an upload of ``filename`` is stored at.

        The path is proven to stay inside the store's root by
        :func:`~lmeditor.ingestion.url_policy.safe_upload_path`, so a
        client-supplied filename cannot escape it.
        """
        return safe_upload_path(source_id, filename, base=self._root)

    def save(self, source_id: str, filename: str, content: bytes) -> Path:
        """Persist ``content`` and evict old files until the store fits its budget.

        Raises:
            ValidationError: the content is larger than the upload cap.
            UnsafeSourceError: the source id or the filename is not safe.
        """
        validate_upload_size(len(content))
        path = self.path_for(source_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.evict()
        return path

    def total_bytes(self) -> int:
        """Return the number of bytes the store currently holds."""
        return sum(path.stat().st_size for path in self.list_files())

    def list_files(self) -> list[Path]:
        """Return every stored file, oldest first.

        "Oldest" is the modification time: a file written earlier is the one
        the budget reclaims first.
        """
        if not self._root.exists():
            return []
        files: list[Path] = []
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            try:
                files.append(path)
            except OSError:
                continue
        files.sort(key=lambda item: (self._mtime(item), str(item)))
        return files

    def evict(self) -> list[Path]:
        """Delete the oldest files until the store fits its budget.

        Returns the paths that were deleted.
        """
        files = self.list_files()
        total = 0
        sizes: dict[Path, int] = {}
        for path in files:
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                sizes[path] = 0
            total += sizes[path]

        removed: list[Path] = []
        for path in files:
            if total <= self._max_bytes:
                break
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("Could not remove the stored upload %s: %s", path, exc)
                continue
            total -= sizes.get(path, 0)
            removed.append(path)
        if removed:
            self._prune_empty_directories()
        return removed

    def touch(self, source_id: str, filename: str) -> Optional[Path]:
        """Mark a stored file as recently used and return its path.

        Returns ``None`` when the file is not in the store.
        """
        path = self.path_for(source_id, filename)
        if not path.is_file():
            return None
        try:
            os.utime(path, None)
        except OSError as exc:
            logger.warning("Could not update the timestamp of %s: %s", path, exc)
        return path

    def remove(self, source_id: str) -> bool:
        """Remove every file stored for ``source_id``.

        Returns True when something was removed.
        """
        removed = False
        directory = self._root / source_id
        if not directory.is_dir():
            return False
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            try:
                os.remove(path)
                removed = True
            except OSError as exc:
                logger.warning("Could not remove the stored upload %s: %s", path, exc)
        self._prune_empty_directories()
        return removed

    def _prune_empty_directories(self) -> None:
        """Remove the directories a removal left empty."""
        if not self._root.exists():
            return
        for path in sorted(self._root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not path.is_dir():
                continue
            try:
                next(path.iterdir())
            except StopIteration:
                try:
                    path.rmdir()
                except OSError:
                    continue
            except OSError:
                continue

    @staticmethod
    def _mtime(path: Path) -> float:
        """Return a path's modification time, or 0.0 when it cannot be read."""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


#: The process-wide store the HTTP boundary writes through.
_DEFAULT_STORE: Optional[SourceStore] = None


def default_store() -> SourceStore:
    """Return the process-wide source store, creating it on first use."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = SourceStore.default()
    return _DEFAULT_STORE


__all__ = [
    "SourceStore",
    "default_store",
]
