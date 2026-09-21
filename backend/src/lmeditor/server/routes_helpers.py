"""Helpers for source-ingestion routes."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


@dataclass
class SavedFile:
    """A persisted uploaded file plus its on-disk path."""

    path: Path


async def persist_source_to_disk(file: UploadFile, source_id: str) -> SavedFile:
    """Save an uploaded file under a stable per-source directory.

    Uses ``tempfile.gettempdir()`` so the helper is portable across Linux,
    macOS, and Windows (the platform temp directory on Windows is typically
    ``C:\\Users\\<user>\\AppData\\Local\\Temp`` rather than ``/tmp``).
    """
    base = Path(tempfile.gettempdir()) / "lmeditor_uploads"
    target_dir = base / source_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / (file.filename or "upload")
    content = await file.read()
    target_path.write_bytes(content)
    return SavedFile(path=target_path)