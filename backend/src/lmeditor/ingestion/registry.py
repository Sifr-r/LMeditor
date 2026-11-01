"""Dispatch from SourceKind to the right SourceLoader.

This module is a *door*: the rule that says how a source of each kind is read
lives in the Kotlin tier, and the Python API answers with the tier's answers.
The original implementation is frozen in
:mod:`lmeditor._reference.ingestion_registry`.

No parity check compares the two sides yet: the dispatch is visible only
through the loader it picks, so a comparison would measure the loaders rather
than the registry. The gap is declared rather than silent: the ledger
``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py`` names
this module against its Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/LoaderRegistry.kt``) and the
harness fails if that declaration stops matching the tree.

What stays on this side is what is a *type* matter rather than a rule: a URL
source is named by a URL string and never by a file path, and the mistake is
surfaced as a domain error at the dispatch boundary rather than deep inside a
loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.loaders import (
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

        The loader this hands back is a door onto the Kotlin tier: it is a thin
        Python shim whose ``load`` and ``load_detailed`` ask the tier for the
        answer, so the rule that says how a source is read lives in one place.

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

        Raises:
            SourceLoadError: no loader is registered for the kind, or the
                origin is not usable for it.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is never
                swallowed.
        """
        self._guard_url_origin(kind, origin)
        loader = self.resolve(kind)
        return loader.load(origin)

    def load_detailed(self, kind: SourceKind, origin: str | Path) -> LoadedSource:
        """Dispatch to the loader and return everything it learned.

        Same dispatch rules as :meth:`load`; the difference is the return
        value, which carries the omissions the loader recorded, the size of
        the artifact and -- for a PDF -- the document metadata the
        bibliography resolver chain needs.

        Raises:
            SourceLoadError: no loader is registered for the kind, or the
                origin is not usable for it.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is never
                swallowed.
        """
        self._guard_url_origin(kind, origin)
        loader = self.resolve(kind)
        return loader.load_detailed(origin)

    @staticmethod
    def _guard_url_origin(kind: SourceKind, origin: str | Path) -> None:
        """Refuse a file path handed to the URL kind.

        This is a type guard, not a rule: the tier fetches the URL natively and
        must never be handed a file path, because the mistake is exactly the
        one the SSRF guard exists to prevent.
        """
        if kind == SourceKind.URL and isinstance(origin, Path):
            raise SourceLoadError(
                kind=kind.value,
                origin=str(origin),
                reason="URL loader requires a URL string, not a Path",
            )
