"""The frozen web evidence seam and the providers behind it.

This is the original Python implementation of ``factcheck.web``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It
is plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Its three ingestion imports are rewired onto the frozen siblings --
``_reference.ingestion_http_safety``, ``_reference.ingestion_limits`` and
``_reference.ingestion_url_policy``. The live ``lmeditor.ingestion`` modules
are doors to the Kotlin tier now, so importing one of them would make this
witness reach the implementation it is meant to be compared against.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

import httpx

from lmeditor.core.exceptions import WebEvidenceError
from lmeditor._reference.ingestion_http_safety import fetch_limited
from lmeditor._reference.ingestion_limits import PayloadTooLargeError
from lmeditor._reference.ingestion_url_policy import (
    UnsafeSourceError,
    assert_public_url,
)

logger = logging.getLogger(__name__)

#: The literal host the search request is sent to. It is a constant rather
#: than a parameter because the host is *not* part of the caller's authority:
#: only the query is. Every URL this module builds from it therefore carries a
#: host no caller can influence.
DUCKDUCKGO_HOST: Final[str] = "html.duckduckgo.com"

#: The path of the DuckDuckGo HTML endpoint.
DUCKDUCKGO_SEARCH_PATH: Final[str] = "/html/"

#: The base the search request is built from.
DUCKDUCKGO_SEARCH_BASE: Final[str] = f"https://{DUCKDUCKGO_HOST}{DUCKDUCKGO_SEARCH_PATH}"

#: Default timeout for a web request, in seconds.
DEFAULT_WEB_TIMEOUT_SECONDS: Final[float] = 8.0

#: Largest web response body that will be read, in bytes. A search result page
#: or a single article is far below this; anything larger is refused rather
#: than buffered.
DEFAULT_WEB_MAX_BYTES: Final[int] = 262_144

#: Longest page text kept as the premise for a thin web snippet.
MAX_PAGE_TEXT_CHARS: Final[int] = 4_000

#: Matches an HTML tag, used by the fallback text extractor.
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class WebResult:
    """One hit returned by a web search.

    Attributes:
        url: The address of the page. It is the address the search engine
            reported, not a URL this module has opened.
        title: The page's title, when the engine reported one.
        snippet: The excerpt the engine reported for the page.
    """

    url: str
    title: str = ""
    snippet: str = ""


@runtime_checkable
class WebSearchProvider(Protocol):
    """The contract every web-search provider satisfies.

    A provider returns *candidates*. It does not judge them: whether a
    candidate supports a claim is decided by the NLI evaluator, on the
    provider's text, in :mod:`lmeditor.factcheck.service`.
    """

    def search(self, query: str, limit: int = 5) -> list[WebResult]:
        """Return at most ``limit`` candidate pages for ``query``."""
        ...


class _DuckDuckGoResultParser(HTMLParser):
    """A tolerant parser for the DuckDuckGo HTML result list.

    The endpoint's markup is not a contract, so the parser looks for the two
    classes it has always used -- ``result__a`` for the title and the link,
    ``result__snippet`` for the excerpt -- and simply ignores anything it
    does not recognise. A page whose markup changed yields no results rather
    than an exception.

    The state this parser keeps is named ``_pending_result`` rather than
    ``_pending`` on purpose. ``html.parser.HTMLParser`` acquired an
    internal ``_pending`` buffer in CPython 3.13.16 and 3.14.6, and it
    joins that buffer in ``close()``. A subclass that shadows the name
    replaces the buffer with its own state, and the stdlib then joins
    whatever the subclass left there -- which is not iterable, and the
    parse dies with ``TypeError: can only join an iterable`` after the
    first result was already recorded. The parse lost the last result and
    the tiers disagreed about how many there were.

    Nothing else here changes. The rule this witness states is the rule it
    always stated; only the name of a private attribute moved, so that the
    rule survives a change in the standard library it never depended on.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebResult] = []
        self._mode: str = ""
        self._depth: int = 0
        self._parts: list[str] = []
        self._href: str = ""
        self._pending_result: WebResult | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Begin capturing a title or a snippet when the tag is one of ours."""
        if self._mode:
            self._depth += 1
            return
        if tag != "a":
            return
        attributes = {key: (value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if "result__a" in classes:
            self._flush()
            self._mode = "title"
            self._depth = 1
            self._parts = []
            self._href = attributes.get("href", "")
        elif "result__snippet" in classes:
            self._mode = "snippet"
            self._depth = 1
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        """Close the capture that is open and record what it held."""
        if not self._mode:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        text = " ".join(" ".join(self._parts).split())
        if self._mode == "title":
            self._pending_result = WebResult(
                url=normalise_result_url(self._href),
                title=text,
            )
        elif self._pending_result is not None:
            self._pending_result = WebResult(
                url=self._pending_result.url,
                title=self._pending_result.title,
                snippet=text,
            )
            self.results.append(self._pending_result)
            self._pending_result = None
        self._mode = ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        """Collect the text of the capture that is open."""
        if self._mode:
            self._parts.append(data)

    def close(self) -> None:
        """Finish parsing and record a title that never got its snippet."""
        super().close()
        self._flush()

    def _flush(self) -> None:
        """Record a result whose snippet never arrived."""
        if self._pending_result is not None:
            self.results.append(self._pending_result)
            self._pending_result = None


def normalise_result_url(href: str) -> str:
    """Return the address a search result actually points at.

    DuckDuckGo wraps its results in a redirect that carries the real address
    in the ``uddg`` query parameter; a result whose link is already absolute
    is returned unchanged.
    """
    candidate = href.strip()
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not candidate.startswith(("http://", "https://")):
        candidate = urljoin(DUCKDUCKGO_SEARCH_BASE, candidate)
    parameters = parse_qs(urlsplit(candidate).query)
    targets = parameters.get("uddg")
    if targets and targets[0].strip():
        return targets[0].strip()
    return candidate


class DuckDuckGoSearchProvider:
    """Search the open web through DuckDuckGo's HTML endpoint.

    The provider is deliberately dull about the network: the request URL is
    assembled from :data:`DUCKDUCKGO_HOST` and a percent-encoded query
    string, the response is read through the shared, redirect-validating
    fetch, and the markup is parsed defensively.

    Every failure -- a blocked URL, a transport error, an oversized body, a
    body that is not text at all -- is reported as a
    :class:`~lmeditor.core.exceptions.WebEvidenceError` so the caller can
    contain it. The provider never raises a bare exception.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_WEB_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_WEB_MAX_BYTES,
    ) -> None:
        """Store the timeout and body cap the provider's requests run under.

        Args:
            timeout: Seconds a single request may take.
            max_bytes: Largest response body that will be read.
        """
        self._timeout = timeout
        self._max_bytes = max_bytes

    @property
    def timeout(self) -> float:
        """Seconds a single request may take."""
        return self._timeout

    @property
    def max_bytes(self) -> int:
        """Largest response body that will be read."""
        return self._max_bytes

    def search(self, query: str, limit: int = 5) -> list[WebResult]:
        """Return at most ``limit`` candidate pages for ``query``.

        Args:
            query: The text to search for. It is percent-encoded into the
                query string of a URL built from :data:`DUCKDUCKGO_HOST`.
            limit: How many results to return.

        Returns:
            The parsed hits, in the order the endpoint reported them.

        Raises:
            WebEvidenceError: the query is empty, the URL was refused, the
                request failed, the body was too large, or the body could not
                be read as text.
        """
        if not isinstance(query, str) or not query.strip():
            raise WebEvidenceError(
                reason="the search query must be a non-empty string",
                query=str(query),
            )
        if limit <= 0:
            return []

        url = f"{DUCKDUCKGO_SEARCH_BASE}?q={quote_plus(query)}"
        body = self._fetch(url, query)
        results = parse_search_results(body)
        return results[:limit]

    def _fetch(self, url: str, query: str) -> bytes:
        """Return the body at ``url``, or raise a typed web error."""
        try:
            assert_public_url(url)
        except UnsafeSourceError as exc:
            raise WebEvidenceError(
                reason=f"the search URL was refused: {exc}",
                query=query,
            ) from exc

        try:
            return fetch_limited(
                url,
                timeout=self._timeout,
                max_bytes=self._max_bytes,
            )
        except UnsafeSourceError as exc:
            raise WebEvidenceError(
                reason=f"a redirect target was refused: {exc}",
                query=query,
            ) from exc
        except PayloadTooLargeError as exc:
            raise WebEvidenceError(
                reason=f"the response was too large: {exc}",
                query=query,
            ) from exc
        except httpx.HTTPError as exc:
            raise WebEvidenceError(
                reason=f"the search request failed: {exc}",
                query=query,
            ) from exc


class NullWebSearchProvider:
    """The provider used when no web access is configured.

    It consults nothing and returns nothing, so a fact-check that asks for
    web evidence degrades to a corpus-only check instead of failing.
    """

    def search(self, query: str, limit: int = 5) -> list[WebResult]:
        """Return no results: this provider never consults the web."""
        logger.debug(
            "Web evidence was requested for %r but no web provider is configured.",
            query,
        )
        return []


def parse_search_results(body: bytes) -> list[WebResult]:
    """Return the results parsed out of a DuckDuckGo HTML response.

    A body that is not text at all yields no results rather than an error:
    the endpoint's markup is not a contract, and an empty result is the
    honest answer to "this page held nothing we recognise".
    """
    document = body.decode("utf-8", errors="replace")

    parser = _DuckDuckGoResultParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - malformed markup must not raise
        logger.warning("Could not parse the search response: %s", exc)
    return [result for result in parser.results if result.url]


def extract_page_text(body: bytes) -> str:
    """Return the visible text of an HTML document.

    ``trafilatura`` is used when it is installed, because it strips the
    navigation and the boilerplate that no claim is ever about; the
    tag-stripping fallback keeps this working when it is not.
    """
    try:
        import trafilatura  # local import; soft dep

        extracted = trafilatura.extract(
            body,
            include_comments=False,
            include_tables=True,
        )
        if extracted:
            return " ".join(extracted.split())
    except ImportError:
        logger.debug("trafilatura is not installed; falling back to tag stripping.")
    except Exception as exc:  # noqa: BLE001 - a soft dependency must not break the fetch
        logger.warning("Could not extract the page text with trafilatura: %s", exc)

    raw = body.decode("utf-8", errors="replace")
    return " ".join(_TAG_RE.sub(" ", raw).split())


def fetch_page_text(
    url: str,
    *,
    timeout: float = DEFAULT_WEB_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_WEB_MAX_BYTES,
) -> str:
    """Return the visible text of the page at ``url``.

    The address is vetted against the private-IP policy before it is opened,
    so a search result that points at an internal service is refused rather
    than fetched.

    Raises:
        WebEvidenceError: the address was refused, the request failed, or the
            body was too large.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebEvidenceError(reason="the page URL must be a non-empty string", query=url)

    try:
        assert_public_url(url)
    except UnsafeSourceError as exc:
        raise WebEvidenceError(
            reason=f"the page URL was refused: {exc}",
            query=url,
        ) from exc

    try:
        body = fetch_limited(url, timeout=timeout, max_bytes=max_bytes)
    except UnsafeSourceError as exc:
        raise WebEvidenceError(
            reason=f"a redirect target was refused: {exc}",
            query=url,
        ) from exc
    except PayloadTooLargeError as exc:
        raise WebEvidenceError(
            reason=f"the page was too large: {exc}",
            query=url,
        ) from exc
    except httpx.HTTPError as exc:
        raise WebEvidenceError(
            reason=f"the page could not be fetched: {exc}",
            query=url,
        ) from exc

    return extract_page_text(body)


__all__ = [
    "DEFAULT_WEB_MAX_BYTES",
    "DEFAULT_WEB_TIMEOUT_SECONDS",
    "DUCKDUCKGO_HOST",
    "DUCKDUCKGO_SEARCH_BASE",
    "DuckDuckGoSearchProvider",
    "MAX_PAGE_TEXT_CHARS",
    "NullWebSearchProvider",
    "WebResult",
    "WebSearchProvider",
    "extract_page_text",
    "fetch_page_text",
    "normalise_result_url",
    "parse_search_results",
]
