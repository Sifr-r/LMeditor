"""Dispatch from SourceKind to the right SourceLoader."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    MarkdownSourceLoader,
    PdfSourceLoader,
    SourceLoader,
    TextSourceLoader,
    UrlSourceLoader,
)


class SourceLoaderRegistry:
    """Maps `SourceKind` to a concrete `SourceLoader` instance."""

    def __init__(self, loaders: dict[SourceKind, SourceLoader]) -> None:
        self._loaders = loaders

    @classmethod
    def default(cls) -> "SourceLoaderRegistry":
        """Construct a registry pre-populated with one of each loader."""
        return cls({
            SourceKind.PDF: PdfSourceLoader(),
            SourceKind.DOCX: DocxSourceLoader(),
            SourceKind.MARKDOWN: MarkdownSourceLoader(),
            SourceKind.HTML: HtmlSourceLoader(),
            SourceKind.TEXT: TextSourceLoader(),
            SourceKind.URL: UrlSourceLoader(),
        })

    def resolve(self, kind: SourceKind) -> SourceLoader:
        """Return the loader registered for ``kind``.

        Wraps both ``KeyError`` (missing mapping) and ``ValueError`` (invalid
        ``SourceKind`` value) as ``SourceLoadError`` so callers have a single
        domain exception to catch.
        """
        try:
            return self._loaders[SourceKind(kind)]
        except (KeyError, ValueError) as exc:
            raise SourceLoadError(
                kind=str(kind),
                origin="(unknown)",
                reason=f"no loader registered for kind {kind!r}",
            ) from exc

    def load(
        self, kind: SourceKind, origin: str | Path
    ) -> tuple[str, Optional[int]]:
        """Resolve the loader for ``kind`` and dispatch ``load(origin)``.

        Adds a runtime guard for the URL kind: ``UrlSourceLoader.load`` narrows
        its parameter to ``str`` (the ABC signature accepts ``str | Path``),
        so a Path passed here would otherwise surface a confusing type error
        deep inside the loader. We surface it as ``SourceLoadError`` at the
        dispatch boundary instead.
        """
        if kind == SourceKind.URL and isinstance(origin, Path):
            raise SourceLoadError(
                kind=kind.value,
                origin=str(origin),
                reason="URL loader requires a URL string, not a Path",
            )
        loader = self.resolve(kind)
        return loader.load(origin)
