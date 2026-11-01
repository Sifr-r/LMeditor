"""Web evidence: the search seam, the providers behind it, and the page reader.

DuckDuckGo's HTML endpoint is the one search this application speaks to. The
request it makes is deliberately dull: the host is a constant rather than a
parameter, because the host is *not* part of the caller's authority -- only
the query is -- and the query only ever reaches the server as a query-string
value. The response is read through the shared, redirect-validating fetch, and
the markup is parsed defensively, because the endpoint's markup is not a
contract.

This module is a **door** to the Kotlin tier. Every rule it used to hold is
the tier's now: the address the request goes to, the query it carries, the
parsing of the response, the address a result points at, and the text a page
is read as. What is left here is the part that cannot be moved -- the shape
of the API these callers already use, and the one check the tier's entry
point cannot express, which is whether the caller handed over a string at all.

There is deliberately no fallback to a Python implementation of those rules: a
silent fallback would hide exactly the drift the Kotlin tier exists to remove.

The original implementation is frozen in :mod:`lmeditor._reference.factcheck_web`,
where the parity harness can still compare the two sides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from lmeditor.core.exceptions import WebEvidenceError
from lmeditor.tier import factcheck as tier_factcheck

logger = logging.getLogger(__name__)

#: The literal host the search request is sent to. It is a constant rather
#: than a parameter because the host is *not* part of the caller's authority:
#: only the query is. Every URL this module builds from it therefore carries a
#: host no caller can influence.
#:
#: The same host as the Kotlin tier's ``WebSearchEndpoints.HOST``. It is
#: written out rather than read across the seam because a constant that could
#: not be read without a running JVM would be useless to a module that is
#: imported at startup. The two copies are pinned separately -- the Kotlin side
#: by its own test on ``WebSearchEndpoints.HOST``, and this side by
#: ``tests/test_factcheck.py`` -- and nothing compares them across the seam:
#: the parity harness's ``the web evidence`` check compares the URL
#: normalisation, the result parsing and the page text, not this constant. So a
#: change to one copy alone would not be caught.
DUCKDUCKGO_HOST: Final[str] = "html.duckduckgo.com"

#: The path of the DuckDuckGo HTML endpoint.
DUCKDUCKGO_SEARCH_PATH: Final[str] = "/html/"

#: The base the search request is built from.
DUCKDUCKGO_SEARCH_BASE: Final[str] = f"https://{DUCKDUCKGO_HOST}{DUCKDUCKGO_SEARCH_PATH}"

#: Default timeout for a web request, in seconds.
#:
#: The same number as the Kotlin tier's ``DEFAULT_WEB_TIMEOUT_SECONDS``; see
#: :data:`DUCKDUCKGO_HOST` for why it is written out rather than read across
#: the seam.
DEFAULT_WEB_TIMEOUT_SECONDS: Final[float] = 8.0

#: Largest web response body that will be read, in bytes. A search result page
#: or a single article is far below this; anything larger is refused rather
#: than buffered.
#:
#: The same number as the Kotlin tier's ``DEFAULT_WEB_MAX_BYTES``; see
#: :data:`DUCKDUCKGO_HOST` for why it is written out rather than read across
#: the seam.
DEFAULT_WEB_MAX_BYTES: Final[int] = 262_144

#: Longest page text kept as the premise for a thin web snippet.
#:
#: The same number as the Kotlin tier's ``MAX_PAGE_TEXT_CHARS``; see
#: :data:`DUCKDUCKGO_HOST` for why it is written out rather than read across
#: the seam.
MAX_PAGE_TEXT_CHARS: Final[int] = 4_000


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


def normalise_result_url(href: str) -> str:
    """Return the address a search result actually points at.

    DuckDuckGo wraps its results in a redirect that carries the real address
    in the ``uddg`` query parameter; a result whose link is already absolute
    is returned unchanged.
    """
    return tier_factcheck.web_normalise_result_url(href)


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
        _require_text(query, "the search query must be a non-empty string")
        hits = tier_factcheck.web_search(
            query,
            limit,
            timeout=self._timeout,
            max_bytes=self._max_bytes,
        )
        return [_result(hit) for hit in hits]


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
    hits = tier_factcheck.web_parse_search_results(body)
    return [_result(hit) for hit in hits]


def extract_page_text(body: bytes) -> str:
    """Return the visible text of an HTML document.

    The tags are replaced by spaces and the whitespace that is left is
    collapsed, so the text of a document reads as one line.

    The tier has no third-party library to strip the navigation and the
    boilerplate that no claim is ever about, so this is the tag-stripping
    fallback the original implementation used when ``trafilatura`` was not
    installed.
    """
    return tier_factcheck.web_extract_page_text(body)


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
    _require_text(url, "the page URL must be a non-empty string", raw=True)
    return tier_factcheck.web_fetch_page_text(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
    )


def _require_text(value: Any, reason: str, *, raw: bool = False) -> None:
    """Refuse a value that is not a string at all.

    The one rule that stays on this side of the seam is the *type* check: the
    Kotlin tier takes a string, so "not a string at all" is a question only
    the caller can be told about here. Whether the string is empty is the
    tier's rule, and the tier is the one that answers it.
    """
    if not isinstance(value, str):
        raise WebEvidenceError(reason=reason, query=value if raw else str(value))


def _result(hit: dict[str, Any]) -> WebResult:
    """Return the web result the seam carried."""
    return WebResult(
        url=str(hit.get("url", "")),
        title=str(hit.get("title", "")),
        snippet=str(hit.get("snippet", "")),
    )


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
