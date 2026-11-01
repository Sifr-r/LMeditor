"""The bibliography resolver chain, as a door onto the Kotlin tier.

A source can name itself -- through a DOI, an arXiv id, a PMID, or the
``/Info`` dictionary of the PDF it is -- and the resolver chain turns that
into a bibliographic record. The chain runs five strategies in order:

``PdfHeader`` reads the metadata the document carries; ``CrossRef``, ``arXiv``,
``PubMed`` and ``OpenAlex`` each ask an upstream that can *verify* an
identifier; ``User`` returns a record the operator typed by hand and is never
part of the default chain.

Every one of those strategies is implemented by the Kotlin tier, together
with the rules that decide which upstream is asked, which identifier is safe
to put in a request line, how large an answer may be and how a malformed
document is reported. This module is the *door* onto those rules: it keeps
the Python API its callers already use -- the classes, their signatures, the
exceptions they raise -- and answers with the tier's answers.

The original implementation is frozen in
:mod:`lmeditor._reference.ingestion_resolvers`, where the parity harness can
still compare the two sides.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from lmeditor.core.exceptions import BibliographyResolutionError  # noqa: F401 - re-exported
from lmeditor.core.schemas import BibliographicRecord
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.ingestion.limits import MAX_RESOLVER_RESPONSE_BYTES
from lmeditor.tier import ingest as tier_ingest

#: The largest response body a resolver will read.
#:
#: The same number as the Kotlin tier's ``Limits.MAX_RESOLVER_RESPONSE_BYTES``;
#: it is written out rather than read across the seam because a constant that
#: could not be read without a running JVM would be useless to a module that
#: is imported at startup.
_MAX_RESOLVER_RESPONSE_BYTES: int = MAX_RESOLVER_RESPONSE_BYTES


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

    The extraction is the Kotlin tier's; this resolver is the door onto it.
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
        return tier_ingest.resolve_bibliography(
            self.kind,
            source_id=source_id,
            source_kind=source_kind,
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
            pdf_metadata=_metadata_wire(pdf_metadata),
        )


class _HttpHibliResolver(BibliographyResolver):
    """Base for CrossRef / arXiv / PubMed / OpenAlex (all fetched over HTTP).

    The class flag :attr:`expects_atom` says which body the tier decodes: JSON
    for CrossRef / PubMed / OpenAlex, Atom XML for arXiv. It is a flag rather
    than per-resolver guesswork so the JSON resolvers cannot be dragged into
    the XML path by accident.

    The fetch, the redirect discipline, the size cap and the identifier
    validation are the Kotlin tier's, which fetches with the JDK's own HTTP
    client. ``timeout_seconds`` and ``max_response_bytes`` keep their values,
    which are the tier's own defaults; the seam does not carry a per-call
    override, so setting them no longer changes what is fetched.
    """

    kind: BibliographyResolverKind = BibliographyResolverKind.NONE  # override

    #: arXiv is the only upstream served over HTTP that answers with Atom XML.
    expects_atom: bool = False

    #: Largest response body this resolver will read. The upstreams answer
    #: with one record (or a short Atom feed); anything bigger is refused
    #: rather than buffered.
    max_response_bytes: int = _MAX_RESOLVER_RESPONSE_BYTES

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
        return tier_ingest.resolve_bibliography(
            self.kind,
            source_id=source_id,
            source_kind=source_kind,
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
        )


class CrossRefBibliographyResolver(_HttpHibliResolver):
    """Resolves a DOI against the Crossref ``/works/{doi}`` endpoint."""

    kind = BibliographyResolverKind.CROSSREF


class ArxivBibliographyResolver(_HttpHibliResolver):
    """Resolves an arXiv id against the Atom feed at ``export.arxiv.org``.

    This is the one resolver whose upstream does not speak JSON: the query API
    answers ``application/atom+xml``, which is why it sets
    :attr:`expects_atom`.
    """

    kind = BibliographyResolverKind.ARXIV
    expects_atom = True


class PubMedBibliographyResolver(_HttpHibliResolver):
    """Resolves a PMID against the PubMed ``esummary`` endpoint."""

    kind = BibliographyResolverKind.PUBMED


class OpenAlexBibliographyResolver(_HttpHibliResolver):
    """Resolves a DOI against the OpenAlex ``works`` endpoint."""

    kind = BibliographyResolverKind.OPENALEX


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
        return tier_ingest.resolve_bibliography(
            self.kind,
            source_id=str(kwargs.get("source_id") or ""),
            source_kind=kwargs.get("source_kind", SourceKind.PDF),
            record=_record_wire(self._record) if self._record is not None else None,
            source_ids=sorted(self._source_ids) if self._source_ids is not None else None,
        )


class BibliographyResolverChain:
    """Runs resolvers in order; returns the first non-None result, or None.

    The order is the one the resolvers were declared in, with one exception:
    when an identifier (a DOI, an arXiv id or a PMID) is known, the resolver
    that can *verify* it is tried before the PDF-header resolver, which can
    only report what the document says about itself. A PDF whose metadata
    carries a DOI is therefore resolved by CrossRef -- with a year, a
    container and a canonical URL -- and falls back to the header resolver only
    when no upstream answers.

    The order, the enrichment of a request with an identifier the document's
    own metadata carries, and the fall-through from a failed strategy to the
    next one are the Kotlin tier's rules. This chain is the door onto them.
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
        return tier_ingest.resolve_bibliography_chain(
            [_resolver_spec(resolver) for resolver in self._resolvers],
            source_id=str(kwargs.get("source_id") or ""),
            source_kind=kwargs.get("source_kind", SourceKind.PDF),
            doi=kwargs.get("doi"),
            arxiv_id=kwargs.get("arxiv_id"),
            pmid=kwargs.get("pmid"),
            pdf_metadata=_metadata_wire(kwargs.get("pdf_metadata")),
        )


# ============================================================================
# The wire forms
# ============================================================================
#
# The two helpers below are the only place the door knows how a request is
# spelled. Everything else -- which upstream is asked, what the answer means,
# when a strategy has failed -- is answered by the tier.


def _metadata_wire(metadata: Optional[dict[str, Any]]) -> Optional[dict[str, str]]:
    """Return the metadata entries the tier can read.

    Only the text entries are forwarded: the rule that reads them looks for
    text, so an entry that is not text is not metadata a resolver can use.
    """
    if not metadata:
        return None
    return {
        str(key): value
        for key, value in metadata.items()
        if isinstance(value, str)
    }


def _record_wire(record: BibliographicRecord) -> dict[str, Any]:
    """Return a record in the wire form the tier reads."""
    return {
        "title": record.title,
        "authors": list(record.authors),
        "year": record.year,
        "publication_date": record.publication_date,
        "container_title": record.container_title,
        "volume": record.volume,
        "issue": record.issue,
        "pages": record.pages,
        "doi": record.doi,
        "url": record.url,
        "type": record.type,
        "resolver": record.resolver.value,
    }


def _resolver_spec(resolver: BibliographyResolver) -> dict[str, Any]:
    """Return the wire description of one strategy in a chain.

    A strategy is named by its kind. The user resolver is the one that needs
    more than a kind: it carries the record the operator typed and the
    sources that record applies to.
    """
    spec: dict[str, Any] = {"kind": resolver.kind.value}
    if isinstance(resolver, UserBibliographyResolver):
        if resolver._record is not None:
            spec["record"] = _record_wire(resolver._record)
        if resolver._source_ids is not None:
            spec["source_ids"] = sorted(resolver._source_ids)
    return spec
