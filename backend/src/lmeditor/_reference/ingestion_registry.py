"""The frozen dispatch from SourceKind to the right SourceLoader.

This is the original Python implementation of ``ingestion.registry``, kept here
so that the parity harness has a witness to compare the Kotlin tier against. It
is plain Python on purpose: see ``lmeditor._reference``.

Its import of the loaders is rewired onto the frozen sibling in this package,
so that it cannot reach the seam by accident.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor._reference.ingestion_loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    LoadedSource,
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

    def load_detailed(self, kind: SourceKind, origin: str | Path) -> LoadedSource:
        """Dispatch to the loader and return everything it learned.

        Same dispatch rules as :meth:`load`; the difference is the return
        value, which carries the omissions the loader recorded, the size of
        the artifact and -- for a PDF -- the document metadata the
        bibliography resolver chain needs.
        """
        if kind == SourceKind.URL and isinstance(origin, Path):
            raise SourceLoadError(
                kind=kind.value,
                origin=str(origin),
                reason="URL loader requires a URL string, not a Path",
            )
        loader = self.resolve(kind)
        return loader.load_detailed(origin)
