"""Per-kind source loaders that turn bytes on disk into normalized text.

This module is a *door*: the rule that says how a source of each kind is read
-- what it loses, how many pages it has, how large it is -- lives in the Kotlin
tier, and the Python API answers with the tier's answers. The original
implementation is frozen in :mod:`lmeditor._reference.ingestion_loaders`.

No parity check compares the two sides yet: a loader reads the artifact
itself, so a comparison would need the same artifact on both sides and a
corpus of them. The gap is declared rather than silent: the ledger
``REFERENCE_WITNESSES`` in ``backend/scripts/verify_kotlin_parity.py`` names
this module against its Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/Loaders.kt``) and the
harness fails if that declaration stops matching the tree.

What stays on this side is what is a *type* matter rather than a rule: the
loaders keep their names, their signatures and the fact that a URL source is
named by a URL string and never by a file path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.limits import MAX_URL_RESPONSE_BYTES
from lmeditor.ingestion.url_policy import UnsafeSourceError
from lmeditor.tier import ingest as tier_ingest

#: How many redirect hops a URL fetch may follow.
#:
#: The same number as the Kotlin tier's ``HttpSafety.MAX_RESOLVER_REDIRECT_HOPS``,
#: which its URL loader applies. It is written out rather than read across the
#: seam because the seam does not serve it and a constant that could not be
#: read without a running JVM would be useless to a module that is imported at
#: startup. The two numbers are the same today, and the tier's own test pins
#: the tier's number; nothing compares the two numbers across languages, so the
#: duplication is a declared gap rather than a held one.
MAX_REDIRECT_HOPS: int = 5


def _loaded_from_wire(wire: dict[str, Any]) -> LoadedSource:
    """Rebuild the Python result from the tier's wire answer.

    The wire carries ``text``, ``page_count``, ``byte_size``, ``metadata`` and
    ``omissions``. A count or a size the tier did not report is ``None`` here
    and not ``0``: the tier says "this kind does not paginate" or "the size is
    unknown", and the Python API has always told that apart from a real zero.
    """
    page_count = wire.get("page_count")
    byte_size = wire.get("byte_size")
    metadata = wire.get("metadata")
    omissions = wire.get("omissions")
    return LoadedSource(
        text=str(wire.get("text", "")),
        page_count=int(page_count) if page_count is not None else None,
        omissions=(
            [str(entry) for entry in omissions] if isinstance(omissions, list) else []
        ),
        byte_size=int(byte_size) if byte_size is not None else None,
        pdf_metadata=(
            {str(key): value for key, value in metadata.items()}
            if isinstance(metadata, dict)
            else {}
        ),
    )


def _source_load_error(exc: SourceLoadError, kind: str, origin: str) -> SourceLoadError:
    """Rebuild the tier's refusal as the Python error the callers catch.

    The tier's ``SourceLoadError`` carries its kind, its origin and its reason
    in its *message*; the seam reads them from the structured details, which
    the tier does not fill, so what arrives here has an empty kind, an empty
    origin and an empty reason. The kind and the origin are known here -- they
    are the ones this loader asked for -- so they are put back. The reason is
    the tier's when it survived the seam, and otherwise says that the tier
    refused the artifact rather than inventing one.
    """
    reason = exc.reason or "the Kotlin tier refused the artifact"
    return SourceLoadError(kind=kind, origin=origin, reason=reason)


@dataclass(frozen=True)
class LoadedSource:
    """Everything a loader learned about one artifact.

    ``load()`` returns only the text and the page count because that is the
    contract every caller already depends on. The remaining fields are the
    facts the loader used to have nowhere to put: the content it could not
    preserve, the size of the artifact on disk or on the wire, and the
    document metadata a PDF carries.
    """

    text: str
    page_count: Optional[int] = None
    omissions: list[str] = field(default_factory=list)
    byte_size: Optional[int] = None
    pdf_metadata: dict[str, Any] = field(default_factory=dict)


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

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the artifact and everything the loader learned about it.

        The default implementation defers to :meth:`load` and adds nothing, so
        a loader that has no extra facts to report needs no override. Loaders
        that *do* have facts -- the DOCX reader's omission register, the PDF
        document metadata, the size of the artifact -- override this method.
        """
        text, page_count = self.load(origin)
        return LoadedSource(text=text, page_count=page_count)

    def _load_from_tier(self, origin: str) -> LoadedSource:
        """Ask the tier to read the artifact and rebuild the Python answer.

        The tier owns the rule; this is the seam call and the translation of
        its wire answer into :class:`LoadedSource`.

        Raises:
            SourceLoadError: the tier refused the artifact.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is never
                swallowed.
        """
        try:
            wire = tier_ingest.load_source(self.kind, origin=origin)
        except SourceLoadError as exc:
            raise _source_load_error(exc, self.kind.value, origin) from exc
        return _loaded_from_wire(wire)


class TextSourceLoader(SourceLoader):
    """Loads plain UTF-8 text. Page count is undefined (returns None)."""

    kind = SourceKind.TEXT

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load the file's text. A plain text file does not paginate."""
        loaded = self._load_from_tier(str(Path(origin)))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text and report the artifact's size on disk."""
        return self._load_from_tier(str(Path(origin)))


class MarkdownSourceLoader(SourceLoader):
    """Loads a Markdown file and converts it to plain text.

    Stripping Markdown syntax requires *rendering* it first, so the conversion
    runs Markdown -> HTML -> text. The direction is the whole point: a
    converter that went HTML -> Markdown would re-emit the very syntax this
    loader exists to remove, and Markdown offers no plain-text rendering
    primitive that a tag stripper could replace.

    The conversion itself is the Kotlin tier's; this loader is the door onto
    it.
    """

    kind = SourceKind.MARKDOWN

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load the file's plain text. A Markdown file does not paginate."""
        loaded = self._load_from_tier(str(Path(origin)))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the extracted text and report the artifact's size on disk."""
        return self._load_from_tier(str(Path(origin)))


class HtmlSourceLoader(SourceLoader):
    """Loads an HTML file and extracts its visible text.

    The extraction drops the parts of a document a reader never sees -- the
    scripts, the styles and the markup itself -- so that a page's *content* is
    what reaches the pipeline.

    The extraction is the Kotlin tier's; this loader is the door onto it.
    """

    kind = SourceKind.HTML

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load the file's visible text. An HTML file does not paginate."""
        loaded = self._load_from_tier(str(Path(origin)))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the extracted text and report the artifact's size on disk."""
        return self._load_from_tier(str(Path(origin)))


class PdfSourceLoader(SourceLoader):
    """Loads a PDF file and returns its page count.

    Text is extracted from the document's content streams, which yields an
    empty string for scanned/blank pages. The ``page_count`` is always
    returned when the PDF can be opened; ``text`` may be empty.

    The loader also extracts the document's ``/Info`` metadata (title, author,
    subject, keywords) and looks for a DOI or an arXiv id in it or on the first
    page. That metadata is what makes
    :class:`~lmeditor.ingestion.resolvers.PdfHeaderBibliographyResolver` -- and
    through it the DOI resolvers -- able to resolve a PDF at all: before this,
    the resolver chain was handed an empty dictionary and every PDF resolved
    to nothing.

    The extraction is the Kotlin tier's; this loader is the door onto it.
    """

    kind = SourceKind.PDF

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load the document's text and its page count."""
        loaded = self._load_from_tier(str(Path(origin)))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text, the page count, the size and the document metadata."""
        return self._load_from_tier(str(Path(origin)))


class DocxSourceLoader(SourceLoader):
    """Loads a DOCX file and returns its plain text.

    DOCX has no native pagination concept, so ``page_count`` is always
    ``None``.

    The loader also reports the register of content the reader could not
    preserve -- a table it could not lay out, a field it could not resolve --
    so that the pipeline can say what the document lost instead of quietly
    presenting a shorter text as the whole document.

    The extraction is the Kotlin tier's; this loader is the door onto it.
    """

    kind = SourceKind.DOCX

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        """Load the document's plain text. A DOCX file does not paginate."""
        loaded = self._load_from_tier(str(Path(origin)))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text and report what the reader could not preserve."""
        return self._load_from_tier(str(Path(origin)))


class UrlSourceLoader(SourceLoader):
    """Fetches a URL and extracts the visible text of the answer.

    SSRF guard: the origin *and* every redirect target are validated against
    the private-IP policy before a request is issued, and the redirect chain is
    followed by hand so that the guard cannot be bypassed by a ``Location``
    header. Every hop pins the addresses it vetted before its connection is
    opened, and the peer address the connection actually landed on is compared
    against that pinned set; a hop that landed anywhere else is refused and
    its body is never read.

    All of that is the Kotlin tier's now, which fetches the URL natively.
    What stays here is the type guard: the tier must never be handed a file
    path, because the mistake is exactly the one the SSRF guard exists to
    prevent.

    ``timeout_seconds`` and ``max_response_bytes`` are part of this class's
    published surface and keep their values, which are the tier's own
    defaults. The seam does not carry a per-call override, so setting them no
    longer changes what is fetched.
    """

    kind = SourceKind.URL
    timeout_seconds: float = 30.0

    #: Largest response body this loader will read. The body is consumed
    #: incrementally and the read is abandoned the moment the cap is passed,
    #: so an oversized document is never held in memory in full.
    max_response_bytes: int = MAX_URL_RESPONSE_BYTES

    def load(self, origin: str) -> tuple[str, Optional[int]]:  # type: ignore[override]
        """Fetch the URL and return its text and (optional) page count."""
        loaded = self._load_from_tier(self._url_origin(origin))
        return loaded.text, loaded.page_count

    def load_detailed(self, origin: str) -> LoadedSource:  # type: ignore[override]
        """Load the URL and report the size of the body that was read."""
        return self._load_from_tier(self._url_origin(origin))

    @staticmethod
    def _url_origin(origin: str) -> str:
        """Return the URL to fetch, refusing one that may not be fetched.

        The policy is the tier's: this asks it, and turns its refusal into the
        error shape the Python API has always reported -- a URL the policy
        refuses is a "blocked URL" load failure, not a network failure. The
        tier vets the URL again when it fetches it, so this check is about the
        shape of the answer, not about the safety of the fetch.

        The type guard stays on this side too: the tier must never be handed a
        file path, because the mistake is exactly the one the SSRF guard
        exists to prevent.

        Raises:
            SourceLoadError: the origin is not a URL string, or the policy
                refused it.
        """
        if not isinstance(origin, str):
            raise SourceLoadError(
                kind=SourceKind.URL.value,
                origin=str(origin),
                reason="URL loader requires a URL string, not a Path",
            )
        try:
            tier_ingest.assert_public_url(origin)
        except UnsafeSourceError as exc:
            raise SourceLoadError(
                kind=SourceKind.URL.value,
                origin=origin,
                reason=f"blocked URL: {exc}",
            ) from exc
        return origin
