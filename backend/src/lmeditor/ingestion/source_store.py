"""A bounded, self-cleaning store for the uploads the server persists.

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
abandoned one goes.

Every one of those rules is the Kotlin tier's, which owns the directory on
disk. This module is the *door* onto them: it keeps the Python API its
callers already use and answers with the tier's answers.

The original implementation is frozen in
:mod:`lmeditor._reference.ingestion_source_store`.

No parity check compares the two sides yet: the store owns a directory on
disk, so a comparison would need two stores over one corpus and would measure
the filesystem as much as the budget. The gap is declared rather than silent:
the ledger ``REFERENCE_WITNESSES`` in
``backend/scripts/verify_kotlin_parity.py`` names this module against its
Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt``) and the
harness fails if that declaration stops matching the tree.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from lmeditor.ingestion.limits import MAX_SOURCE_STORE_BYTES, validate_upload_size
from lmeditor.ingestion.url_policy import safe_upload_path
from lmeditor.tier import ingest as tier_ingest

logger = logging.getLogger(__name__)


class SourceStore:
    """Persists uploaded sources under a total-bytes budget."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = MAX_SOURCE_STORE_BYTES,
    ) -> None:
        self._root = Path(root) if root is not None else _configured_upload_root()
        self._max_bytes = max_bytes

    @classmethod
    def default(cls) -> "SourceStore":
        """Return a store rooted at the configured upload directory."""
        return cls(_configured_upload_root())

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
        return Path(
            tier_ingest.source_store_save(
                str(self._root),
                source_id,
                filename,
                content,
                self._max_bytes,
            )
        )

    def total_bytes(self) -> int:
        """Return the number of bytes the store currently holds."""
        _files, total = tier_ingest.source_store_list(str(self._root))
        return total

    def list_files(self) -> list[Path]:
        """Return every stored file, oldest first.

        "Oldest" is the modification time: a file written earlier is the one the
        budget reclaims first.
        """
        files, _total = tier_ingest.source_store_list(str(self._root))
        return [Path(item) for item in files]

    def evict(self) -> list[Path]:
        """Delete the oldest files until the store fits its budget.

        Returns the paths that were deleted.
        """
        removed = tier_ingest.source_store_evict(str(self._root), self._max_bytes)
        return [Path(item) for item in removed]

    def touch(self, source_id: str, filename: str) -> Optional[Path]:
        """Mark a stored file as recently used and return its path.

        Returns ``None`` when the file is not in the store.
        """
        path = tier_ingest.source_store_touch(str(self._root), source_id, filename)
        return Path(path) if path is not None else None

    def remove(self, source_id: str) -> bool:
        """Remove every file stored for ``source_id``.

        Returns True when something was removed.
        """
        return tier_ingest.source_store_remove(str(self._root), source_id)


#: The process-wide store the HTTP boundary writes through.
_DEFAULT_STORE: Optional[SourceStore] = None


def default_store() -> SourceStore:
    """Return the process-wide source store, creating it on first use."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = SourceStore.default()
    return _DEFAULT_STORE


def _configured_upload_root() -> Path:
    """Return the upload root, as the tier resolves it for *this* process.

    The tier runs in its own process, so its environment is not this
    process's environment: a ``LMEDITOR_UPLOAD_DIR`` set here is invisible to
    a tier that was started without it. The configured value is therefore
    forwarded and the tier resolves it, so both tiers agree on the directory
    an upload is written to.
    """
    return Path(tier_ingest.upload_root(base=os.environ.get("LMEDITOR_UPLOAD_DIR")))


__all__ = [
    "SourceStore",
    "default_store",
]
