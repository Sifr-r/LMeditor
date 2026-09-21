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


class MarkdownSourceLoader(SourceLoader):
    """Loads a Markdown file and converts it to plain text.

    Uses the ``markdown`` package (MD → HTML) followed by a tag-stripping
    pass, because ``markdownify`` is HTML → Markdown (the wrong direction
    for stripping markdown syntax from markdown input).
    """

    kind = SourceKind.MARKDOWN

    def load(self, path: Path) -> tuple[str, Optional[int]]:
        if not path.exists():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist: {path}",
            )
        try:
            import re  # local import; stdlib only, cheap
            from markdown import Markdown  # local import; soft dep
            raw = path.read_text(encoding="utf-8")
            html = Markdown().convert(raw)
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
        except UnicodeDecodeError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file is not valid UTF-8: {exc}",
            ) from exc
        except ImportError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"markdown not installed: {exc}",
            ) from exc
        return text, None


class HtmlSourceLoader(SourceLoader):
    """Loads an HTML file and extracts visible text via trafilatura."""

    kind = SourceKind.HTML

    def load(self, path: Path) -> tuple[str, Optional[int]]:
        if not path.exists():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist: {path}",
            )
        try:
            import trafilatura  # local import; soft dep
            raw = path.read_bytes()
            text = trafilatura.extract(raw, include_comments=False, include_tables=True)
            if text is None:
                # Fallback: strip tags with a simple regex if trafilatura bails
                import re
                text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))
                text = re.sub(r"\s+", " ", text).strip()
        except UnicodeDecodeError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file is not valid UTF-8: {exc}",
            ) from exc
        except ImportError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"trafilatura not installed: {exc}",
            ) from exc
        return text or "", None