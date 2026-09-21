"""Per-kind source loaders that turn bytes on disk into normalized text."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind


class SourceLoader(ABC):
    """Abstract loader. Each subclass handles one SourceKind."""

    kind: SourceKind

    @abstractmethod
    def load(self, path: Path) -> tuple[str, Optional[int]]:
        """Load text and (optional) page count from a path on disk.

        Returns:
            (text, page_count). page_count is None for kinds that don't paginate.
        """
        raise NotImplementedError


class TextSourceLoader(SourceLoader):
    """Loads plain UTF-8 text. Page count is undefined (returns None)."""

    kind = SourceKind.TEXT

    def load(self, path: Path) -> tuple[str, Optional[int]]:
        if not path.exists():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist: {path}",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file is not valid UTF-8: {exc}",
            ) from exc
        return text, None