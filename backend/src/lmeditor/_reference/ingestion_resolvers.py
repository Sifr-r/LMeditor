"""The frozen bibliography resolver chain.

This is the original Python implementation of ``ingestion.resolvers``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Its imports are rewired onto the frozen siblings in this package -- the HTTP-safety helpers,
the limits and the URL policy
-- so that it cannot reach the seam by accident: a witness that leaned on a
door would be measuring the tier against itself.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

Bibliography metadata resolver chain (PDF header → CrossRef → arXiv → PubMed → OpenAlex).

Each resolver is written against the structure its upstream *actually* serves
rather than a convenient synthetic dict (AUDIT.md finding 14):

* CrossRef, PubMed and OpenAlex answer JSON, decoded once by the shared
  :meth:`_HttpHibliResolver._fetch` and interpreted by a per-resolver ``_parse``.
* arXiv answers **Atom XML** from ``export.arxiv.org/api/query``, so it opts
  into the XML path with :attr:`_HttpHibliResolver.expects_atom` and parses the
  document with :mod:`xml.etree.ElementTree`. No other resolver touches XML.

An incomplete or malformed upstream document is an expected outcome rather
than an exception: ``_parse`` / ``_parse_atom`` return ``None`` for anything
without a non-empty title, and :class:`~lmeditor.core.exceptions.
BibliographyResolutionError` stays reserved for a failed fetch, so
:class:`BibliographyResolverChain` can move on to the next resolver.

The identifiers that select an endpoint -- ``doi``, ``arxiv_id`` and ``pmid``
-- are likewise validated before they are interpolated into a URL, because
they arrive from the HTTP boundary as bare strings. They are checked against
a per-identifier character allow-list that *keeps* the structural characters a
real identifier needs (a DOI's ``/``, an arXiv legacy category's ``/``) and
rejects the ones a URL would treat as structure. What survives the allow-list
is then percent-encoded rather than interpolated raw, so a well-formed
identifier still produces byte-identical URLs, a legacy SICI DOI with
``( ) ; : < >`` in its suffix is requestable instead of rejected, and a
crafted one can no longer add a query parameter, rewrite the path or break the
request line. See the identifier-validation section below for the exact rules."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Final, NoReturn, Optional
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.schemas import BibliographicRecord
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor._reference.ingestion_http_safety import fetch_limited
from lmeditor._reference.ingestion_limits import (
    MAX_RESOLVER_RESPONSE_BYTES,
    PayloadTooLargeError,
)
from lmeditor._reference.ingestion_url_policy import UnsafeSourceError

#: The namespace arXiv declares on its Atom feed.
_ATOM_NAMESPACE: Final[str] = "http://www.w3.org/2005/Atom"
_ATOM_ENTRY_TAG: Final[str] = f"{{{_ATOM_NAMESPACE}}}entry"


def _local_name(tag: str) -> str:
    """Return an ElementTree tag with its ``{namespace}`` prefix stripped."""
    return tag.rsplit("}", 1)[-1]


def _children_named(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Return the direct children whose local name is ``name``."""
    return [
        child
        for child in element
        if isinstance(child.tag, str) and _local_name(child.tag) == name
    ]


def _first_text(element: ElementTree.Element, name: str) -> str:
    """Return the whitespace-collapsed text of the first child named ``name``."""
    for child in _children_named(element, name):
        return " ".join((child.text or "").split())
    return ""


def _leading_year(value: str) -> Optional[int]:
    """Return the four-digit year prefix of ``value``, or ``None``."""
    return int(value[:4]) if value[:4].isdigit() else None


#: A DOI as it appears inside free text: the ``10.`` prefix, a registrant code
#: and a suffix. Used to find an identifier a PDF's metadata mentions without
#: declaring one under a ``doi`` key.
_DOI_IN_TEXT: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}/[^\s\"'<>\]]+")


def _metadata_value(metadata: Mapping[str, Any], key: str) -> Optional[str]:
    """Return a metadata entry, accepting the plain and the ``/Info`` spellings.

    A PDF's ``/Info`` dictionary spells its keys ``/Title``, ``/Author`` and
    so on; the extraction in :mod:`lmeditor.ingestion.pdf_metadata` normalises
    them to ``title`` and ``author``. Both shapes are accepted so a caller
    that hands the raw ``/Info`` dictionary to the chain still works.
    """
    for candidate in (key, f"/{key[:1].upper()}{key[1:]}", f"/{key.title()}"):
        value = metadata.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _metadata_doi(metadata: Mapping[str, Any]) -> Optional[str]:
    """Return the DOI a PDF's metadata declares, or ``None``.

    The declaration is either an explicit ``doi`` entry or a DOI written into
    one of the free-text entries (``/Subject``, ``/Keywords``, ...), which is
    where a publisher usually puts it.
    """
    declared = _metadata_value(metadata, "doi")
    if declared:
        return declared
    for value in metadata.values():
        if not isinstance(value, str):
            continue
        match = _DOI_IN_TEXT.search(value)
        if match:
            return match.group(0).rstrip(".,;:")
    return None


def _metadata_arxiv_id(metadata: Mapping[str, Any]) -> Optional[str]:
    """Return the arXiv id a PDF's metadata declares, or ``None``."""
    declared = _metadata_value(metadata, "arxiv_id")
    if declared:
        return declared
    for value in metadata.values():
        if isinstance(value, str) and "arXiv:" in value:
            tail = value.split("arXiv:", 1)[1].strip()
            if tail:
                return tail.split()[0]
    return None


# ============================================================================
# Identifier validation
# ============================================================================
#
# ``doi``, ``arxiv_id`` and ``pmid`` reach the resolvers as unvalidated strings
# from the HTTP boundary and were interpolated straight into an outbound URL.
# The literal prefix of each URL closes the authority at the first ``/`` or
# ``?``, so an identifier cannot change the destination *host* -- but it could
# still add a query parameter (``1&retmode=xml``), rewrite the path
# (``10.1234/../works/W1``) or break the request line (CR / LF). Each resolver
# therefore validates its own identifier through the helpers below, encodes
# what survives, and rejects with
# :class:`~lmeditor.core.exceptions.BibliographyResolutionError` -- the error
# :class:`BibliographyResolverChain` already treats as "this strategy cannot
# resolve", so a bad identifier falls through to the next resolver instead of
# aborting the lookup.
#
# The single rule the allow-lists implement is: **reject what a URL would treat
# as structure or as a request-line terminator; encode what it would merely
# misread.** A character such as ``<`` is neither an injection vector nor
# illegitimate -- the DOI Handbook admits it in a suffix, and a Wiley SICI DOI
# like ``10.1002/(SICI)1097-0258(19980815/30)17:15/16<1661::AID-SIM968>3.0.CO;2-2``
# is a real, widely published identifier -- so it is percent-encoded (``%3C``)
# on the way into the URL instead of banned. Banning it would not make the
# request safer, since the encoding already neutralises it; it would only turn
# a resolvable DOI into a missing bibliography entry.

#: Printable US-ASCII, the range a DOI is drawn from. Everything below
#: ``U+0020`` (every control character, CR and LF included) and ``U+007F`` is
#: excluded by construction, so a request line cannot be terminated and a NUL
#: cannot be smuggled in.
_PRINTABLE_ASCII: Final[str] = "".join(chr(code) for code in range(0x20, 0x7F))

#: The complete reject-list for a DOI: printable ASCII minus exactly these
#: five characters. Each is here because it can change the *structure* of the
#: request rather than merely need escaping:
#:
#: * ``" "`` -- any whitespace. An interior space in a DOI is a paste
#:   accident or an attempt to smuggle a token past a reader, never part of a
#:   real identifier. (Surrounding whitespace is stripped before the
#:   allow-list is applied, so a clean paste is unaffected.)
#: * ``?`` -- would truncate the path and start a query string.
#: * ``#`` -- would start a fragment, retargeting the request.
#: * ``%`` -- the one character whose meaning depends on what happens
#:   *before* :func:`quote` runs: admitting it would make a percent-encoded
#:   traversal such as ``%2e%2e`` indistinguishable from a literal suffix, and
#:   would allow double-encoding ambiguity.
#: * ``\`` -- a path separator to some intermediaries and a literal to others,
#:   so it can be used to smuggle a path segment past the check below.
#:
#: Everything else a DOI suffix may legitimately use -- ``( ) ; : < > ! $ & ' *
#: + , ~ @ [ ] ^ ` { } |`` and the rest of printable ASCII -- is admitted and
#: percent-encoded by :func:`_encode_identifier` instead.
_DOI_UNSAFE_CHARS: Final[frozenset[str]] = frozenset(" ?#%\\")

#: A DOI: all printable ASCII except the structurally unsafe characters in
#: :data:`_DOI_UNSAFE_CHARS`. ``/`` is deliberately left in -- it is legal in a
#: DOI prefix *and* inside a suffix, and percent-encoding it would change the
#: request for every real DOI -- so it is handled as a real, validated path
#: separator by :func:`_reject_unsafe_segments` instead.
_DOI_ID_CHARS: Final[frozenset[str]] = frozenset(_PRINTABLE_ASCII) - _DOI_UNSAFE_CHARS

#: An arXiv id keeps a narrow charset: ASCII letters, digits, the three
#: punctuation characters real ids use (``.``, ``-``, ``_``) and the ``/`` of
#: the legacy category form. It is a query *value* rather than a path and has
#: no legacy punctuation to accommodate, so the strictest charset is also the
#: honest one -- a pasted arXiv URL is not an id and is rejected. Every member
#: except ``/`` is a character :func:`urllib.parse.quote` never rewrites, and
#: ``/`` is kept because arXiv's ``id_list`` wants the legacy form's separator
#: literal (pinned by the byte-identity oracle in the tests).
_ARXIV_ID_CHARS: Final[frozenset[str]] = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    ".-_/"
)

#: A PMID is a bare decimal number, with no prefix, separator or suffix.
_PMID_CHARS: Final[frozenset[str]] = frozenset("0123456789")

#: NCBI assigns PMIDs of at most eight decimal digits; the cap bounds the work
#: one identifier can cause without rejecting any ID that exists.
_MAX_PMID_DIGITS: Final[int] = 8

#: No real DOI, arXiv id or PMID approaches this, so it only bounds a crafted
#: identifier's contribution to an upstream URL.
_MAX_IDENTIFIER_LENGTH: Final[int] = 255

#: How many offending characters an error message names before summarising.
_MAX_REPORTED_CHARS: Final[int] = 8

#: How much of an identifier an error message echoes before truncating.
_MAX_ECHOED_IDENTIFIER: Final[int] = 80


def _encode_identifier(value: str) -> str:
    """Return ``value`` percent-encoded for interpolation into a request URL.

    ``safe="/"`` keeps the ``/`` that a real DOI and a legacy arXiv id both
    carry literal -- encoding it would change the request for every one of
    them -- and :func:`urllib.parse.quote` leaves its always-safe set (ASCII
    letters, digits, ``_``, ``.``, ``-``, ``~``) untouched regardless. An
    ordinary identifier therefore still produces the byte-identical URL the
    pre-validation f-string built, while every other character the allow-lists
    admit is emitted as ``%XX``. That is what makes a legacy SICI DOI's
    ``( ) : ; < >`` requestable -- ``%28 %29 %3A %3B %3C %3E`` -- instead of
    rejected, without any of them being able to add a query parameter, a
    fragment or a path segment of their own.

    A *query* value would nominally use ``safe=""``, which additionally
    rewrites ``/``. That is not applied to the arXiv identifier, which is the
    one query-valued field here: arXiv's ``id_list`` wants the legacy category
    form's ``/`` literal (``hep-th/9901001``), and the byte-identity oracle
    pins that URL. The difference is confined to ``/`` -- every other character
    :data:`_ARXIV_ID_CHARS` admits is left untouched by :func:`quote` either
    way (pinned by a test) -- and a digits-only PMID needs no escaping at all.
    """
    return quote(value, safe="/")


def _render_identifier(value: str) -> str:
    """Return a short, escaped rendering of ``value`` for an error message.

    ``repr`` escapes the CR / LF and NUL a request-line injection carries, so
    the value can be echoed back without planting a line in the error body or
    the log it reaches; the truncation keeps a multi-kilobyte identifier out of
    both.
    """
    clipped = value
    if len(clipped) > _MAX_ECHOED_IDENTIFIER:
        clipped = clipped[:_MAX_ECHOED_IDENTIFIER] + "..."
    return repr(clipped)


def _reject_identifier(
    kind: BibliographyResolverKind,
    field: str,
    value: str,
    reason: str,
) -> NoReturn:
    """Reject ``field`` with the typed domain error the chain falls back on.

    ``origin`` carries the offending value rather than a URL, because no
    request is made: there is no endpoint to blame, only the input.
    """
    raise BibliographyResolutionError(
        resolver=kind.value,
        origin=f"{field}={_render_identifier(value)}",
        reason=reason,
    )


def _normalise_identifier(
    kind: BibliographyResolverKind,
    field: str,
    value: str,
    *,
    allowed: frozenset[str],
) -> str:
    """Return ``value`` stripped of surrounding whitespace, or reject it.

    Surrounding whitespace is stripped because these identifiers are routinely
    pasted with a trailing space or newline, and stripping it is safe: the
    result is shorter, so it cannot carry the CR / LF / space it came with.
    *Interior* whitespace is a different matter and is rejected, by the
    allow-list, because a space in the middle of a DOI or a PMID is always a
    paste accident or an attempt to inject -- never a real identifier.
    """
    candidate = value.strip()
    if not candidate:
        _reject_identifier(kind, field, value, "identifier is empty once stripped")
    if len(candidate) > _MAX_IDENTIFIER_LENGTH:
        _reject_identifier(
            kind,
            field,
            value,
            f"identifier is longer than {_MAX_IDENTIFIER_LENGTH} characters",
        )
    illegal = sorted({char for char in candidate if char not in allowed})
    if illegal:
        shown = [repr(char) for char in illegal[:_MAX_REPORTED_CHARS]]
        if len(illegal) > _MAX_REPORTED_CHARS:
            shown.append(f"...+{len(illegal) - _MAX_REPORTED_CHARS} more")
        _reject_identifier(
            kind,
            field,
            value,
            f"identifier contains characters outside the allowed set: {', '.join(shown)}",
        )
    return candidate


def _reject_unsafe_segments(
    kind: BibliographyResolverKind,
    field: str,
    value: str,
) -> None:
    """Reject a hierarchical identifier with an empty, ``.`` or ``..`` segment.

    ``/`` is a legal DOI and arXiv character, so the traversal check is on
    whole segments rather than on a ``..`` substring: ``10.1234/a..b`` is a
    real DOI, while ``10.1234/../works/W1`` is not and would otherwise walk
    the path out of the endpoint the resolver meant to call.
    """
    segments = value.split("/")
    if any(segment in (".", "..") for segment in segments):
        _reject_identifier(
            kind, field, value, "identifier contains a '.' or '..' path segment"
        )
    if any(not segment for segment in segments):
        _reject_identifier(
            kind, field, value, "identifier contains an empty path segment"
        )


def _validated_doi(kind: BibliographyResolverKind, doi: str) -> str:
    """Return ``doi`` normalised, or raise if it could not be requested safely.

    Legal: any printable US-ASCII character except the five in
    :data:`_DOI_UNSAFE_CHARS` (space, ``?``, ``#``, ``%``, ``\\``), at least
    one ``/`` separating a non-empty prefix from a non-empty suffix, no empty
    or ``.`` / ``..`` segment, no interior whitespace, and at most
    :data:`_MAX_IDENTIFIER_LENGTH` characters. The ``10.`` stem is *not*
    required: a DOI is routinely pasted as ``1000/jrp.2024.001``, and
    rejecting that form would break a lookup that works today. A legacy SICI
    DOI -- ``10.1002/(SICI)1097-0258(...)17:15/16<...>3.0.CO;2-2`` -- is legal
    precisely because its punctuation is printable ASCII; the caller encodes
    it with :func:`_encode_identifier` afterwards.

    Returns the value with surrounding whitespace stripped and every other
    character preserved verbatim, so a well-formed DOI produces exactly the
    URL it produced before validation existed.
    """
    candidate = _normalise_identifier(kind, "doi", doi, allowed=_DOI_ID_CHARS)
    if "/" not in candidate:
        _reject_identifier(
            kind,
            "doi",
            doi,
            "a DOI must contain the '/' that separates its prefix from its suffix",
        )
    _reject_unsafe_segments(kind, "doi", candidate)
    return candidate


def _validated_arxiv_id(kind: BibliographyResolverKind, arxiv_id: str) -> str:
    """Return ``arxiv_id`` normalised, or raise if it could not be requested safely.

    Legal: ASCII letters, digits, ``.``, ``-``, ``_`` and ``/``, with no empty
    or ``.`` / ``..`` segment and no interior whitespace. The charset covers
    both real arXiv shapes -- the modern ``2401.01234`` / ``2401.01234v2`` and
    the legacy category form ``hep-th/9901001`` / ``math.GT/0309136`` -- and
    deliberately does not encode ``/``, which arXiv's ``id_list`` expects
    literally in the legacy form. A pasted arXiv *URL*
    (``https://arxiv.org/abs/2401.01234``) is not an identifier and is
    rejected: the field contract is an id, and unwrapping it would be a
    separate normalisation decision.
    """
    candidate = _normalise_identifier(
        kind, "arxiv_id", arxiv_id, allowed=_ARXIV_ID_CHARS
    )
    _reject_unsafe_segments(kind, "arxiv_id", candidate)
    return candidate


def _validated_pmid(kind: BibliographyResolverKind, pmid: str) -> str:
    """Return ``pmid`` normalised, or raise if it could not be requested safely.

    Legal: decimal digits only, at most :data:`_MAX_PMID_DIGITS` of them, with
    surrounding whitespace stripped. A PMID has no prefix, separator or
    suffix, so the strictest of the three charsets applies: anything that
    could add a parameter to the esummary query -- ``&``, ``=``, ``?``,
    whitespace, CR / LF -- is rejected rather than encoded, because a PMID
    that needs encoding was never a PMID.
    """
    candidate = _normalise_identifier(kind, "pmid", pmid, allowed=_PMID_CHARS)
    if len(candidate) > _MAX_PMID_DIGITS:
        _reject_identifier(
            kind,
            "pmid",
            pmid,
            f"a PMID is at most {_MAX_PMID_DIGITS} decimal digits",
        )
    return candidate


class BibliographyResolver(ABC):
    """Single-strategy resolver. Returns None when this strategy cannot resolve."""

    kind: BibliographyResolverKind
    timeout_seconds: float = 10.0

    @abstractmethod
    def resolve(
        self,
        *,
        source_id: str,
        source_kind: SourceKind,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
        pdf_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[BibliographicRecord]:
        raise NotImplementedError


class PdfHeaderBibliographyResolver(BibliographyResolver):
    """Best-effort: extract /Title and /Author from PDF metadata.

    This resolver is the *offline* one: it never makes a request, it reads the
    metadata the PDF itself carries. It used to be dead code -- nothing ever
    populated ``pdf_metadata`` -- and it is reachable again because the PDF
    loader now extracts the ``/Info`` dictionary and hands it to the chain.

    The DOI the metadata carries is put on the record, because the document
    states it and the record should not hide it. The chain, however, tries the
    identifier-bearing resolvers *before* this one whenever a DOI or an arXiv
    id is known, so a PDF with a DOI is resolved by CrossRef (which returns a
    year, a container and a canonical URL) and this resolver is the fallback.
    """

    kind = BibliographyResolverKind.PDF_HEADER

    def resolve(
        self,
        *,
        source_id: str,
        source_kind: SourceKind,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
        pdf_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[BibliographicRecord]:
        if not pdf_metadata:
            return None
        title = _metadata_value(pdf_metadata, "title")
        author_raw = _metadata_value(pdf_metadata, "author")
        if not title:
            return None
        authors: list[str] = []
        if author_raw:
            authors = [a.strip() for a in author_raw.replace(";", ",").split(",") if a.strip()]
        return BibliographicRecord(
            title=title,
            authors=authors,
            doi=doi or _metadata_doi(pdf_metadata),
            resolver=BibliographyResolverKind.PDF_HEADER,
        )


class _HttpHibliResolver(BibliographyResolver):
    """Base for CrossRef / arXiv / PubMed / OpenAlex (all fetched over HTTP).

    The class flag :attr:`expects_atom` decides which body the base class
    decodes: JSON for CrossRef / PubMed / OpenAlex, Atom XML for arXiv. It is a
    flag rather than per-resolver guesswork so the JSON resolvers cannot be
    dragged into the XML path by accident.
    """

    kind: BibliographyResolverKind = BibliographyResolverKind.NONE  # override

    #: arXiv is the only upstream served over HTTP that answers with Atom XML.
    expects_atom: bool = False

    #: Largest response body this resolver will read. The upstreams answer
    #: with one record (or a short Atom feed); anything bigger is refused
    #: rather than buffered.
    max_response_bytes: int = MAX_RESOLVER_RESPONSE_BYTES

    def _request(self, url: str) -> bytes:
        """Perform the HTTP call and return the raw body.

        Split out of :meth:`_fetch` so the Atom path reuses the same status
        handling and timeout instead of duplicating them.

        The body is read through the vetted fetch in
        :mod:`lmeditor.ingestion.http_safety`, which follows redirects by hand
        and re-validates every hop against the private-IP policy. A blind
        ``follow_redirects=True`` would let an upstream answer with a
        ``Location`` pointing at a link-local address and have the request
        followed there.
        """
        try:
            return fetch_limited(
                url,
                timeout=self.timeout_seconds,
                max_bytes=self.max_response_bytes,
            )
        except UnsafeSourceError as exc:
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=f"blocked URL: {exc}",
            ) from exc
        except PayloadTooLargeError as exc:
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=str(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=str(exc),
            ) from exc

    def _fetch(self, url: str) -> dict[str, Any]:
        """Return the decoded JSON object served at ``url``.

        A non-object JSON body is an upstream fault, not a usable payload, so it
        raises the domain error instead of letting a ``cast`` lie about the
        shape further down.
        """
        body = self._request(url)
        try:
            payload = json.loads(body)
        except ValueError as exc:  # json.JSONDecodeError and friends
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=f"response is not valid JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict):
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=f"expected a JSON object, got {type(payload).__name__}",
            )
        return payload

    def _parse(
        self,
        payload: dict[str, Any],
        *,
        requested_id: Optional[str] = None,
    ) -> Optional[BibliographicRecord]:
        """Interpret a JSON payload. Overridden by the JSON resolvers only.

        Not abstract: it is not a valid implementation for the Atom resolver.
        ``requested_id`` is the identifier the caller asked about (a pmid, an
        arXiv id or a DOI); PubMed uses it to pick the right entry out of the
        response, the other resolvers ignore it.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not parse JSON payloads"
        )

    def _parse_atom(self, document: str) -> Optional[BibliographicRecord]:
        """Interpret an Atom document. Overridden by the arXiv resolver only."""
        raise NotImplementedError(
            f"{type(self).__name__} does not parse Atom documents"
        )

    @abstractmethod
    def _url(
        self,
        *,
        doi: Optional[str],
        arxiv_id: Optional[str],
        pmid: Optional[str],
    ) -> Optional[str]:
        """Return the request URL for this resolver, or ``None`` to decline.

        The contract implementations must honour:

        * ``None`` when the identifier this resolver needs is absent -- that is
          "not my strategy", and the chain simply moves on.
        * the matching :func:`_validated_doi` / :func:`_validated_arxiv_id` /
          :func:`_validated_pmid` check and the :func:`_encode_identifier`
          pass before the value is interpolated, so a well-formed identifier
          keeps producing the URL it produced before validation existed while
          a legacy SICI DOI's ``( ) : ; < >`` is percent-encoded rather than
          rejected.
        * :class:`~lmeditor.core.exceptions.BibliographyResolutionError` for an
          identifier that is present but could not be requested safely. The
          error names the resolver and the offending value, and because the
          chain treats it as a failure the lookup continues with the next
          resolver instead of aborting.
        """
        raise NotImplementedError

    def resolve(
        self,
        *,
        source_id: str,
        source_kind: SourceKind,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
        **_kwargs: Any,
    ) -> Optional[BibliographicRecord]:
        url = self._url(doi=doi, arxiv_id=arxiv_id, pmid=pmid)
        if url is None:
            return None
        try:
            if self.expects_atom:
                rec = self._parse_atom(self._request(url).decode("utf-8", errors="replace"))
            else:
                rec = self._parse(
                    self._fetch(url), requested_id=pmid or arxiv_id or doi
                )
        except (ElementTree.ParseError, ValueError) as exc:
            # A malformed upstream document is an upstream failure, not a bug:
            # report it as the domain error the chain knows how to fall back on
            # rather than letting a parse error escape to the HTTP layer.
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=f"malformed response: {exc}",
            ) from exc
        if rec is None:
            return None
        return rec.model_copy(update={"resolver": self.kind})


class CrossRefBibliographyResolver(_HttpHibliResolver):
    kind = BibliographyResolverKind.CROSSREF

    def _url(
        self,
        *,
        doi: Optional[str],
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
    ) -> Optional[str]:
        """Return the CrossRef ``/works/{doi}`` URL for ``doi``.

        The identifier is validated by :func:`_validated_doi` and then encoded
        by :func:`_encode_identifier`, which keeps the ``/`` of a real DOI
        literal -- so a valid DOI reaches CrossRef as the exact path it always
        did -- while percent-encoding the punctuation a legacy suffix uses.
        Nothing that would extend the path (``..``) or start a query or
        fragment (``?``, ``#``) survives the allow-list in the first place.
        """
        if not doi:
            return None
        validated = _validated_doi(self.kind, doi)
        return f"https://api.crossref.org/works/{_encode_identifier(validated)}"

    def _parse(
        self,
        payload: dict[str, Any],
        *,
        requested_id: Optional[str] = None,
    ) -> Optional[BibliographicRecord]:
        msg = payload.get("message") or {}
        title_list = msg.get("title") or []
        if not title_list:
            return None
        authors_raw = msg.get("author") or []
        authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in authors_raw]
        year: Optional[int] = None
        issued = msg.get("issued", {}).get("date-parts", [[None]])[0]
        if issued and issued[0]:
            year = int(issued[0])
        return BibliographicRecord(
            title=str(title_list[0]),
            authors=[a for a in authors if a],
            year=year,
            container_title=(msg.get("container-title") or [None])[0],
            doi=msg.get("DOI"),
            url=msg.get("URL"),
            type=msg.get("type"),
            resolver=self.kind,
        )


class ArxivBibliographyResolver(_HttpHibliResolver):
    """Resolves an arXiv id against the Atom feed at ``export.arxiv.org``.

    This is the one resolver whose upstream does not speak JSON: the query API
    answers ``application/atom+xml``, which is why it sets
    :attr:`expects_atom` and implements :meth:`_parse_atom` instead of
    :meth:`_parse`.
    """

    kind = BibliographyResolverKind.ARXIV
    expects_atom = True

    def _url(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str],
        pmid: Optional[str] = None,
    ) -> Optional[str]:
        """Return the arXiv ``id_list={arxiv_id}`` query URL for ``arxiv_id``.

        The identifier is validated by :func:`_validated_arxiv_id`, which
        covers the modern (``2401.01234v2``) and legacy category
        (``hep-th/9901001``) forms alike. Because the allow-list excludes
        ``&``, ``=`` and ``?``, the identifier can only ever be the single
        ``id_list`` value -- never a second query parameter -- and it is
        encoded on the way in, which changes nothing: nothing it may contain
        needs escaping.
        """
        if not arxiv_id:
            return None
        validated = _validated_arxiv_id(self.kind, arxiv_id)
        return f"http://export.arxiv.org/api/query?id_list={_encode_identifier(validated)}"

    def _parse_atom(self, document: str) -> Optional[BibliographicRecord]:
        """Build a record from the feed's first ``entry``.

        Returns ``None`` for a miss: arXiv answers an unknown id with a
        well-formed but empty feed (``opensearch:totalResults`` of 0 and no
        ``entry`` element at all).
        """
        root = ElementTree.fromstring(document)
        entry = self._first_entry(root)
        if entry is None:
            return None

        title = _first_text(entry, "title")
        if not title:
            # Validate before constructing: BibliographicRecord requires a
            # non-empty title, so a titleless entry resolves to nothing.
            return None

        authors = [
            name
            for name in (
                _first_text(author, "name")
                for author in _children_named(entry, "author")
            )
            if name
        ]
        published = _first_text(entry, "published")
        identifier = _first_text(entry, "id")
        return BibliographicRecord(
            title=title,
            authors=authors,
            year=_leading_year(published),
            publication_date=published or None,
            url=identifier or None,
            type="preprint",
            resolver=self.kind,
        )

    @staticmethod
    def _first_entry(root: ElementTree.Element) -> Optional[ElementTree.Element]:
        """Return the feed's first entry, tolerating a missing namespace.

        The Atom namespace is matched first because that is what arXiv
        declares; a feed served without the declaration still parses through
        the local-name fallback rather than raising.
        """
        namespaced = root.findall(f".//{_ATOM_ENTRY_TAG}")
        if namespaced:
            return namespaced[0]
        for element in root.iter():
            if isinstance(element.tag, str) and _local_name(element.tag) == "entry":
                return element
        return None


class PubMedBibliographyResolver(_HttpHibliResolver):
    kind = BibliographyResolverKind.PUBMED

    def _url(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str],
    ) -> Optional[str]:
        """Return the esummary URL for ``pmid``.

        The identifier is validated by :func:`_validated_pmid` before it lands
        in the query, so a PMID cannot append a parameter of its own
        (``1&retmode=xml``) and cannot carry CR / LF into the request line;
        the encoding that follows is a no-op, because a PMID is digits only
        and needs no escaping. The parameter order is unchanged, so a valid
        PMID still requests the identical URL.
        """
        if not pmid:
            return None
        validated = _validated_pmid(self.kind, pmid)
        return (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&id={_encode_identifier(validated)}&retmode=json"
        )

    def _parse(
        self,
        payload: dict[str, Any],
        *,
        requested_id: Optional[str] = None,
    ) -> Optional[BibliographicRecord]:
        """Build a record from an esummary envelope.

        esummary answers ``{"result": {"uids": ["12345"], "12345": {...}}}``.
        The *first* key of ``result`` is regularly the ``uids`` list rather than
        an article id, so the entry is always selected through ``uids`` and
        looked up explicitly. Nothing here raises: a missing, non-mapping or
        untitled entry resolves to ``None`` so the chain can try the next
        resolver.
        """
        result = payload.get("result")
        if not isinstance(result, Mapping):
            return None

        uid = self._select_uid(result, requested_id)
        if uid is None:
            return None

        entry = result.get(uid)
        if not isinstance(entry, Mapping):
            return None

        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            return None

        authors: list[str] = []
        raw_authors = entry.get("authors")
        if isinstance(raw_authors, list):
            for author in raw_authors:
                if isinstance(author, Mapping):
                    name = author.get("name")
                    if isinstance(name, str) and name.strip():
                        authors.append(name.strip())

        pubdate = entry.get("pubdate")
        pubdate_text = pubdate if isinstance(pubdate, str) else ""
        source = entry.get("source")
        return BibliographicRecord(
            title=title.strip(),
            authors=authors,
            year=_leading_year(pubdate_text),
            publication_date=pubdate_text or None,
            container_title=source if isinstance(source, str) else None,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
            type="journal-article",
            resolver=self.kind,
        )

    @staticmethod
    def _select_uid(result: Mapping[Any, Any], requested_id: Optional[str]) -> Optional[str]:
        """Return the uid to read: the requested one when listed, else the first."""
        uids = result.get("uids")
        candidates = [
            uid for uid in uids if isinstance(uid, str) and uid
        ] if isinstance(uids, list) else []
        if requested_id and requested_id in candidates:
            return requested_id
        if candidates:
            return candidates[0]
        if requested_id:
            # Some esummary responses omit ``uids`` and key the article
            # directly; the requested id is then the only safe lookup.
            return requested_id if requested_id in result else None
        return None


class OpenAlexBibliographyResolver(_HttpHibliResolver):
    kind = BibliographyResolverKind.OPENALEX

    def _url(
        self,
        *,
        doi: Optional[str],
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
    ) -> Optional[str]:
        """Return the OpenAlex ``/works/doi:{doi}`` URL for ``doi``.

        Validated and encoded by :func:`_validated_doi` /
        :func:`_encode_identifier` as CrossRef is, and for the same reason:
        the ``doi:`` prefix is a *path* prefix here, so a DOI starting with
        ``/`` would produce ``/works/doi:/...`` and a ``..`` segment would
        walk out of the endpoint. The segment check in
        :func:`_reject_unsafe_segments` rules out both while leaving a real
        DOI's own ``/`` untouched, and a legacy SICI suffix's ``( ) : ; < >``
        is percent-encoded rather than rejected.
        """
        if not doi:
            return None
        validated = _validated_doi(self.kind, doi)
        return f"https://api.openalex.org/works/doi:{_encode_identifier(validated)}"

    def _parse(
        self,
        payload: dict[str, Any],
        *,
        requested_id: Optional[str] = None,
    ) -> Optional[BibliographicRecord]:
        title = payload.get("title") or payload.get("display_name")
        if not title:
            return None
        authorships = payload.get("authorships") or []
        authors = [a.get("author", {}).get("display_name", "") for a in authorships]
        year: Optional[int] = payload.get("publication_year")
        return BibliographicRecord(
            title=title,
            authors=[a for a in authors if a],
            year=year,
            container_title=(payload.get("primary_location") or {}).get("source", {}).get("display_name"),
            doi=payload.get("doi"),
            url=payload.get("id"),
            type=payload.get("type"),
            resolver=self.kind,
        )


class UserBibliographyResolver(BibliographyResolver):
    """Returns a record the operator supplied by hand.

    Some sources have no DOI, no arXiv id and no metadata: a working paper
    that never got a DOI, a report, a slide deck. The operator is the only
    source of a bibliographic record for those, so this resolver carries the
    record they typed.

    It is deliberately **not** part of
    :meth:`BibliographyResolverChain.default`: the chain resolves what it can
    from the document, and a hand-entered record is an assertion about a
    document that no upstream can confirm. A caller who wants it consulted
    adds it to the chain explicitly, and it is always consulted last so a
    record an upstream can verify wins.
    """

    kind = BibliographyResolverKind.USER

    def __init__(
        self,
        record: Optional[BibliographicRecord] = None,
        *,
        source_ids: Optional[set[str]] = None,
    ) -> None:
        self._record = record
        self._source_ids = set(source_ids) if source_ids else None

    def resolve(self, **kwargs: Any) -> Optional[BibliographicRecord]:
        if self._record is None:
            return None
        source_id = kwargs.get("source_id")
        if self._source_ids is not None and source_id not in self._source_ids:
            return None
        return self._record.model_copy(update={"resolver": BibliographyResolverKind.USER})


class BibliographyResolverChain:
    """Runs resolvers in order; returns the first non-None result, or None.

    The order is the one the resolvers were declared in, with one exception:
    when an identifier (a DOI, an arXiv id or a PMID) is known, the resolver
    that can *verify* it is tried before the PDF-header resolver, which can
    only report what the document says about itself. A PDF whose metadata
    carries a DOI is therefore resolved by CrossRef -- with a year, a
    container and a canonical URL -- and falls back to the header resolver only
    when no upstream answers.
    """

    def __init__(self, resolvers: list[BibliographyResolver]) -> None:
        self._resolvers = resolvers

    @classmethod
    def default(cls) -> "BibliographyResolverChain":
        return cls([
            PdfHeaderBibliographyResolver(),
            CrossRefBibliographyResolver(),
            ArxivBibliographyResolver(),
            PubMedBibliographyResolver(),
            OpenAlexBibliographyResolver(),
        ])

    def resolve(self, **kwargs: Any) -> Optional[BibliographicRecord]:
        enriched = _enrich_with_metadata_identifiers(kwargs)
        for resolver in self._ordered(enriched):
            try:
                rec = resolver.resolve(**enriched)
            except BibliographyResolutionError:
                # Move to the next resolver on failure.
                continue
            if rec is not None:
                return rec
        return None

    def _ordered(self, kwargs: Mapping[str, Any]) -> list[BibliographyResolver]:
        """Return the resolvers in the order they should be tried."""
        if not _has_identifier(kwargs):
            return list(self._resolvers)
        headers = [
            resolver
            for resolver in self._resolvers
            if resolver.kind is BibliographyResolverKind.PDF_HEADER
        ]
        others = [
            resolver
            for resolver in self._resolvers
            if resolver.kind is not BibliographyResolverKind.PDF_HEADER
        ]
        return others + headers


def _has_identifier(kwargs: Mapping[str, Any]) -> bool:
    """Return True when a DOI, an arXiv id or a PMID is known."""
    return any(kwargs.get(key) for key in ("doi", "arxiv_id", "pmid"))


def _enrich_with_metadata_identifiers(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return the call arguments with any identifier the PDF metadata carries.

    A PDF's ``/Info`` dictionary frequently states the paper's own DOI. The
    chain turns that into a real identifier so the upstream that can verify
    it is consulted -- which is what makes the PDF header resolver reachable
    *and* useful rather than the only thing that ever answers.
    """
    enriched = dict(kwargs)
    metadata = enriched.get("pdf_metadata")
    if not isinstance(metadata, Mapping):
        return enriched
    if not enriched.get("doi"):
        doi = _metadata_doi(metadata)
        if doi:
            enriched["doi"] = doi
    if not enriched.get("arxiv_id"):
        arxiv_id = _metadata_arxiv_id(metadata)
        if arxiv_id:
            enriched["arxiv_id"] = arxiv_id
    return enriched
