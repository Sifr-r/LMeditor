"""The frozen per-kind source loaders.

This is the original Python implementation of ``ingestion.loaders``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Its imports are rewired onto the frozen siblings in this package -- the limits,
the HTTP-safety helpers, the PDF metadata and the URL policy -- so that it
cannot reach the seam by accident: a witness that leaned on a door would be
measuring the tier against itself.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Per-kind source loaders that turn bytes on disk into normalized text."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from lmeditor.core.exceptions import SourceLoadError, WordParseError
from lmeditor.core.types import SourceKind
from lmeditor._reference.ingestion_http_safety import (
    connected_peer_ip as _connected_peer_ip,
)
from lmeditor._reference.ingestion_http_safety import (
    detached_response as _detached_response,
)
from lmeditor._reference.ingestion_http_safety import read_limited
from lmeditor._reference.ingestion_limits import (
    MAX_URL_RESPONSE_BYTES,
    PayloadTooLargeError,
)
from lmeditor._reference.ingestion_pdf_metadata import extract_pdf_metadata
from lmeditor._reference.ingestion_url_policy import (
    UnsafeSourceError,
    assert_public_url,
    iter_redirect_hosts,
    resolve_public_addresses,
)

MAX_REDIRECT_HOPS: int = 5


def _file_byte_size(path: Path) -> Optional[int]:
    """Return the size of ``path`` in bytes, or ``None`` when it cannot be read."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _read_omissions(reader: Any, document: Any) -> list[str]:
    """Return the Word reader's register of content it could not preserve.

    The register is read *defensively*: the reader owns it, and the shape it
    takes (a list on the reader, a list on the document, or absent altogether)
    is the reader's business, not the loader's. A reader that exposes no
    register simply produces no omissions -- which is the honest answer for a
    document that dropped nothing.
    """
    for candidate in (reader, document):
        register = getattr(candidate, "last_omissions", None)
        if not register:
            continue
        if isinstance(register, str):
            return [register]
        try:
            return [str(entry) for entry in register]
        except TypeError:
            continue
    return []


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


class TextSourceLoader(SourceLoader):
    """Loads plain UTF-8 text. Page count is undefined (returns None)."""

    kind = SourceKind.TEXT

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.is_file():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist or is not a regular file: {path}",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file is not valid UTF-8: {exc}",
            ) from exc
        except OSError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file could not be read: {exc}",
            ) from exc
        return text, None

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text and report the artifact's size on disk."""
        text, page_count = self.load(origin)
        return LoadedSource(
            text=text,
            page_count=page_count,
            byte_size=_file_byte_size(Path(origin)),
        )


class MarkdownSourceLoader(SourceLoader):
    """Loads a Markdown file and converts it to plain text.

    Stripping Markdown syntax requires *rendering* it first, so the pipeline
    runs Markdown -> HTML (``markdown``) -> text (tag strip). The direction is
    the whole point: a converter that went HTML -> Markdown would re-emit the
    very syntax this loader exists to remove, and Markdown offers no plain-text
    rendering primitive that a tag stripper could replace.
    """

    kind = SourceKind.MARKDOWN

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.is_file():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist or is not a regular file: {path}",
            )
        try:
            import re  # local import; stdlib only, cheap

            # Optional soft dependency, imported lazily and handled by the
            # ImportError branch below. The package ships no py.typed marker
            # and no stubs are declared for it; the mypy configuration names
            # ``markdown.*`` as an untyped module, so the import needs no
            # inline suppression here.
            from markdown import Markdown

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
        except OSError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file could not be read: {exc}",
            ) from exc
        return text, None

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the rendered text and report the artifact's size on disk."""
        text, page_count = self.load(origin)
        return LoadedSource(
            text=text,
            page_count=page_count,
            byte_size=_file_byte_size(Path(origin)),
        )


class HtmlSourceLoader(SourceLoader):
    """Loads an HTML file and extracts visible text via trafilatura."""

    kind = SourceKind.HTML

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.is_file():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist or is not a regular file: {path}",
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
        except OSError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file could not be read: {exc}",
            ) from exc
        return text or "", None

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the extracted text and report the artifact's size on disk."""
        text, page_count = self.load(origin)
        return LoadedSource(
            text=text,
            page_count=page_count,
            byte_size=_file_byte_size(Path(origin)),
        )


class PdfSourceLoader(SourceLoader):
    """Loads a PDF file via ``pypdf`` and returns its page count.

    Text is extracted via :meth:`pypdf.PageObject.extract_text`, which yields
    an empty string for scanned/blank pages. The ``page_count`` is always
    returned when the PDF can be opened; ``text`` may be empty.

    The loader also extracts the document's ``/Info`` metadata (title, author,
    subject, keywords) and looks for a DOI or an arXiv id in it or on the first
    page. That metadata is what makes
    :class:`~lmeditor.ingestion.resolvers.PdfHeaderBibliographyResolver` -- and
    through it the DOI resolvers -- able to resolve a PDF at all: before this,
    the resolver chain was handed an empty dictionary and every PDF resolved
    to nothing.
    """

    kind = SourceKind.PDF

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        if not path.is_file():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist or is not a regular file: {path}",
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

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text, the page count, the size and the document metadata."""
        path = Path(origin)
        text, page_count = self.load(path)
        return LoadedSource(
            text=text,
            page_count=page_count,
            byte_size=_file_byte_size(path),
            pdf_metadata=extract_pdf_metadata(path),
        )


class DocxSourceLoader(SourceLoader):
    """Loads a DOCX file through the Word reader and returns its plain text.

    DOCX has no native pagination concept, so ``page_count`` is always
    ``None``.

    The extraction is delegated to :class:`lmeditor.word.reader.WordReader`
    rather than to ``python-docx``'s ``Document.paragraphs``. That accessor
    walks only the direct children of ``w:body``, so it silently discarded
    tables, list structure, headers/footers, footnotes, content controls,
    text boxes and tracked changes: a document whose evidence lives in a
    table used to ingest as an empty string. The reader recovers the
    document's structure and renders it to plain text with tables flattened
    to pipe-separated rows, which the chunker can consume.

    The reader also keeps a register of the content it could not preserve
    (dropped images, comments, endnotes, ...). That register is read here and
    carried into the ingest result: a warning that dies inside the process is
    a warning the operator never sees, and "the document contained a table
    that is not in the text" is exactly the kind of thing the operator needs
    to know before citing the passage.

    Structural callers that need the blocks themselves should use
    :class:`lmeditor.word.reader.WordReader` directly; this loader exists to
    feed the text-oriented ingestion pipeline.
    """

    kind = SourceKind.DOCX

    def load(self, origin: str | Path) -> tuple[str, Optional[int]]:
        path = Path(origin)
        text, _omissions = self._read_document(path)
        return text, None

    def load_detailed(self, origin: str | Path) -> LoadedSource:
        """Load the text and surface the reader's omission register."""
        path = Path(origin)
        text, omissions = self._read_document(path)
        return LoadedSource(
            text=text,
            page_count=None,
            omissions=omissions,
            byte_size=_file_byte_size(path),
        )

    def _read_document(self, path: Path) -> tuple[str, list[str]]:
        """Return the document's plain text and the reader's omission register."""
        if not path.is_file():
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file does not exist or is not a regular file: {path}",
            )
        try:
            from lmeditor._reference.word_reader import WordReader

            reader = WordReader()
            document = reader.read(path)
            text = document.plain_text()
        except SourceLoadError:
            raise
        except WordParseError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"Word document could not be parsed: {exc.reason}",
            ) from exc
        except OSError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"file could not be read: {exc}",
            ) from exc
        except Exception as exc:
            # The reader is contracted to refuse a malformed package with a
            # WordParseError, so anything else arriving here is unexpected.
            # It is still a failure to load the source, and the loader's
            # contract — the same one the PDF loader follows — is that every
            # such failure arrives as a SourceLoadError. The server turns a
            # SourceLoadError into a 422 (the file is the client's problem);
            # anything else would surface as a 500 for a bad upload.
            raise SourceLoadError(
                kind=self.kind.value,
                origin=str(path),
                reason=f"Word document could not be read: {exc!r}",
            ) from exc
        return text, _read_omissions(reader, document)


class UrlSourceLoader(SourceLoader):
    """Downloads a URL with httpx, then routes through trafilatura extraction.

    SSRF guard: the origin *and* every redirect target are validated by
    :func:`lmeditor.ingestion.url_policy.assert_public_url` before a request is
    issued, and redirects are followed manually
    (``follow_redirects=False``, at most :data:`MAX_REDIRECT_HOPS` hops) so the
    guard cannot be bypassed by a ``Location`` header.

    DNS rebinding (AUDIT.md "Still open", contract section 7)
    --------------------------------------------------------
    Enforced per hop, and failing closed:

    * Every hop pins the addresses it vetted via
      :func:`lmeditor.ingestion.url_policy.resolve_public_addresses` *before*
      its connection is opened.
    * The hop is issued as a **stream**
      (``httpx.Client(..., follow_redirects=False).send(request, stream=True)``).
      This is what makes the check fire at all: httpx releases the connection
      as soon as the body has been read, and a released socket can no longer
      report the address it reached, so the peer of a fully-read response is
      simply unknowable. A streamed response still has a live socket.
    * The peer address the connection actually landed on is compared against
      the pinned set, and the body is read **only after** that comparison
      approves. A hop that landed anywhere else — the private address the name
      was rebound to, or simply a different public address — raises
      :class:`~lmeditor.core.exceptions.SourceLoadError` with no body read and
      nothing handed to the extractor.
    * **"Cannot tell" is a refusal, not agreement.** A hop that reached the
      network but exposes no peer address is *unproven*, not *clean*, so it
      raises instead of falling back to the pre-flight vet alone. This is a
      deliberate strictness increase, and it is the only reading of "I cannot
      tell where I connected" that does not reopen the window.

    What is still not claimed
    ~~~~~~~~~~~~~~~~~~~~~~~~
    The check is *post-connect*. Binding ``connect()`` to the vetted address
    itself — a pinning transport that keeps the original ``Host`` header and
    TLS SNI — is not implemented, so a rebinding hop is still **contacted**:
    the TCP/TLS handshake and the request line and headers reach the internal
    host before the mismatch is noticed. What is closed is the body. An
    internal response is never read, never extracted and never returned, so
    metadata, internal documents and internal data cannot leak through the
    loader. Blocking the *contact* itself (a link-local metadata endpoint, or
    an internal GET that has a side effect) still needs transport-level
    pinning or an egress proxy, and is out of reach of this call site.
    """

    kind = SourceKind.URL
    timeout_seconds: float = 30.0

    #: Largest response body this loader will read. The body is consumed
    #: incrementally and the read is abandoned the moment the cap is passed,
    #: so an oversized document is never held in memory in full.
    max_response_bytes: int = MAX_URL_RESPONSE_BYTES

    def load(self, origin: str) -> tuple[str, Optional[int]]:  # type: ignore[override]
        response = self._load_response(origin)
        return self._extract(response)

    def load_detailed(self, origin: str) -> LoadedSource:  # type: ignore[override]
        """Load the URL and report the size of the body that was read."""
        response = self._load_response(origin)
        text, page_count = self._extract(response)
        return LoadedSource(
            text=text,
            page_count=page_count,
            byte_size=len(response.content),
        )

    def _load_response(self, origin: str) -> httpx.Response:
        """Fetch ``origin`` and return the accepted response."""
        try:
            current = assert_public_url(origin)
            for _ in range(MAX_REDIRECT_HOPS + 1):
                # Pin this hop: only these vetted (ip, port) pairs may be
                # reached. Re-resolved per hop because a redirect target is a
                # different name entirely, and each hop is vetted on its own.
                vetted = resolve_public_addresses(current)
                response = self._request(current, vetted)
                if response.is_redirect:
                    location = response.headers.get("location", "").strip()
                    if location:
                        current = self._validated_redirect(current, location, response)
                        continue
                if 300 <= response.status_code < 400:
                    raise SourceLoadError(
                        kind=self.kind.value,
                        origin=origin,
                        reason=(
                            f"HTTP {response.status_code} redirect without a Location header"
                        ),
                    )
                response.raise_for_status()
                return response
            raise SourceLoadError(
                kind=self.kind.value,
                origin=origin,
                reason=f"exceeded the maximum of {MAX_REDIRECT_HOPS} redirects",
            )
        except UnsafeSourceError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=origin,
                reason=f"blocked URL: {exc}",
            ) from exc
        except PayloadTooLargeError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=origin,
                reason=f"response too large: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceLoadError(
                kind=self.kind.value,
                origin=origin,
                reason=f"HTTP error: {exc}",
            ) from exc

    def _request(
        self, url: str, vetted: Sequence[tuple[str, int]]
    ) -> httpx.Response:
        """Issue one hop's request and confirm it used a pinned address.

        The request is deliberately *streamed*. httpx reads the whole body and
        releases the connection before it hands a response back, and httpcore
        cannot report a peer address for a released socket, so a non-streamed
        fetch is structurally impossible to verify. Streaming keeps the socket
        live across :meth:`_assert_vetted_peer`, which is the only moment the
        peer is knowable.

        The body is read inside the ``try`` only after the peer was accepted, so
        a rebinding hop has none of its response consumed. The client is closed
        on every path, and a detached copy of the response is returned so the
        caller never holds a reference into a closed stream.

        The body is read through :func:`~lmeditor.ingestion.http_safety.read_limited`,
        which abandons the read the moment the body passes
        :attr:`max_response_bytes`: an oversized document is refused instead
        of being buffered whole.
        """
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
            request = client.build_request("GET", url)
            response = client.send(request, stream=True)
            try:
                self._assert_vetted_peer(url, response, vetted)
                content = read_limited(response, self.max_response_bytes)
            finally:
                # Release the connection on every path, including a peer
                # rejection -- otherwise a refused hop would leak its socket.
                response.close()
        return _detached_response(response, content)

    def _assert_vetted_peer(
        self,
        url: str,
        response: httpx.Response,
        vetted: Sequence[tuple[str, int]],
    ) -> None:
        """Refuse a hop that cannot be shown to have used a pinned address.

        Fails closed on purpose. httpx resolves the hostname itself when it
        opens the connection, a moment after the pre-flight vet resolved it, and
        that gap is the DNS-rebinding window. A hop reporting no peer address is
        therefore *unproven* rather than *clean*, and the pre-flight vet alone
        cannot stand in for a check that did not run -- treating "I cannot tell
        where I connected" as agreement is exactly how the window stayed open.
        A transport that never reports a peer is refused; it is not a licence to
        skip the check.
        """
        host = urlsplit(url).hostname
        allowed = {ip for ip, _ in vetted}
        peer = _connected_peer_ip(response)
        if peer is None:
            raise UnsafeSourceError(
                f"host {host!r} connected without reporting a peer address, so the "
                "connection cannot be shown to have used the vetted address(es) "
                f"{sorted(allowed)}"
            )
        if peer not in allowed:
            raise UnsafeSourceError(
                f"host {host!r} connected to {peer!r}, which is not one of "
                f"the vetted address(es) {sorted(allowed)}"
            )

    def _validated_redirect(
        self, current: str, location: str, response: httpx.Response
    ) -> str:
        """Return the next hop's URL, rejecting any target that is not public."""
        target = urljoin(current, location)
        try:
            return assert_public_url(target)
        except UnsafeSourceError as exc:
            hosts = ", ".join(iter_redirect_hosts(response)) or "unknown"
            raise UnsafeSourceError(
                f"redirect to a disallowed target (host: {hosts}): {exc}"
            ) from exc

    def _extract(self, response: httpx.Response) -> tuple[str, Optional[int]]:
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
