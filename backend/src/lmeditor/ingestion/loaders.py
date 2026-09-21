"""Per-kind source loaders that turn bytes on disk into normalized text."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import httpx

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind


class SourceLoader(ABC):
    """Abstract loader. Each subclass handles one SourceKind."""

    kind: SourceKind

    @abstractmethod
    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load text and (optional) page count from an origin.

        ``origin`` may be a filesystem ``Path`` (for on-disk sources) or a
        string (e.g. a URL for the URL kind). File-based loaders normalize
        the value to a ``Path`` internally; the URL loader uses the string
        directly.

        Returns:
            (text, page_count). page_count is None for kinds that don't paginate.
        """
        raise NotImplementedError


class TextSourceLoader(SourceLoader):
    """Loads plain UTF-8 text. Page count is undefined (returns None)."""

    kind = SourceKind.TEXT

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
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

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
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

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
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


class PdfSourceLoader(SourceLoader):
    """Loads a PDF file via ``pypdf`` and returns its page count.

    Text is extracted via :meth:`pypdf.PageObject.extract_text`, which yields
    an empty string for scanned/blank pages. The ``page_count`` is always
    returned when the PDF can be opened; ``text`` may be empty.
    """

    kind = SourceKind.PDF

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.exists():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist: {path}",
            )
        try:
            from pypdf import PdfReader  # local import; soft dep
            reader = PdfReader(str(path))
            page_count: int = len(reader.pages)
            text_parts: list[str] = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                text_parts.append(extracted)
            text = "\n\n".join(text_parts).strip()
        except ImportError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"pypdf not installed: {exc}",
            ) from exc
        except Exception as exc:
            # Catches pypdf.errors.* (e.g., EmptyFileError, PdfReadError)
            # and any other raised exception during parsing.
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"pypdf failed: {exc}",
            ) from exc
        return text, page_count


class DocxSourceLoader(SourceLoader):
    """Loads a DOCX file via ``python-docx`` and returns paragraph text.

    DOCX has no native pagination concept, so ``page_count`` is always
    ``None``. Paragraph text is joined with blank lines.
    """

    kind = SourceKind.DOCX

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.exists():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist: {path}",
            )
        try:
            from docx import Document  # local import; python-docx soft dep
            doc = Document(str(path))
            paragraphs: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n\n".join(paragraphs).strip()
        except ImportError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"python-docx not installed: {exc}",
            ) from exc
        except Exception as exc:
            # Catches docx.opc.exceptions.PackageNotFoundError and friends.
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"python-docx failed: {exc}",
            ) from exc
        return text, None


class UrlSourceLoader(SourceLoader):
    """Downloads a URL with httpx, then routes through trafilatura extraction."""

    kind = SourceKind.URL
    timeout_seconds: float = 30.0

    def load(self, origin: str) -> tuple[str, Optional[int]]:  # type: ignore[override]
        try:
            response = httpx.get(origin, follow_redirects=True, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=origin,
                reason=f"HTTP error: {exc}",
            ) from exc

        # Try trafilatura first (better at extraction), then fall back to regex.
        try:
            import trafilatura
            text = trafilatura.extract(response.content, include_comments=False, include_tables=True)
            if text:
                return text, None
        except ImportError:
            pass

        import re
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text, None
