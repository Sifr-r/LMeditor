"""Bibliography metadata resolver chain (PDF header → CrossRef → arXiv → PubMed → OpenAlex)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional, cast

import httpx

from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.schemas import BibliographicRecord
from lmeditor.core.types import BibliographyResolverKind, SourceKind


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
    """Best-effort: extract /Title and /Author from PDF metadata."""

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
        title = pdf_metadata.get("/Title") or pdf_metadata.get("Title")
        author_raw = pdf_metadata.get("/Author") or pdf_metadata.get("Author")
        if not title:
            return None
        authors: list[str] = []
        if author_raw and isinstance(author_raw, str):
            authors = [a.strip() for a in author_raw.replace(";", ",").split(",") if a.strip()]
        return BibliographicRecord(
            title=str(title),
            authors=authors,
            resolver=BibliographyResolverKind.PDF_HEADER,
        )


class _HttpHibliResolver(BibliographyResolver):
    """Base for CrossRef / arXiv / PubMed / OpenAlex (all HTTP JSON)."""

    kind: BibliographyResolverKind = BibliographyResolverKind.NONE  # override

    def _fetch(self, url: str) -> dict[str, Any]:
        try:
            response = httpx.get(url, follow_redirects=True, timeout=self.timeout_seconds)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise BibliographyResolutionError(
                resolver=self.kind.value,
                origin=url,
                reason=str(exc),
            ) from exc

    @abstractmethod
    def _url(
        self,
        *,
        doi: Optional[str],
        arxiv_id: Optional[str],
        pmid: Optional[str],
    ) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def _parse(self, payload: dict[str, Any]) -> Optional[BibliographicRecord]:
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
        payload = self._fetch(url)
        rec = self._parse(payload)
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
        if not doi:
            return None
        return f"https://api.crossref.org/works/{doi}"

    def _parse(self, payload: dict[str, Any]) -> Optional[BibliographicRecord]:
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
    kind = BibliographyResolverKind.ARXIV

    def _url(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str],
        pmid: Optional[str] = None,
    ) -> Optional[str]:
        if not arxiv_id:
            return None
        return f"http://export.arxiv.org/api/query?id_list={arxiv_id}"

    def _parse(self, payload: dict[str, Any]) -> Optional[BibliographicRecord]:
        # arXiv returns Atom XML; we rely on the caller having parsed it to JSON-Like dict.
        # For brevity, expect {"entries": [{"title": "...", "authors": [...], "published": "YYYY-MM-DD..."}]}
        entries = payload.get("entries") or []
        if not entries:
            return None
        e = entries[0]
        title = e.get("title")
        if not title:
            return None
        authors_raw = e.get("authors") or []
        authors = [a.get("name", "") for a in authors_raw if a.get("name")]
        published = e.get("published") or ""
        year: Optional[int] = int(published[:4]) if published[:4].isdigit() else None
        return BibliographicRecord(
            title=title.strip(),
            authors=authors,
            year=year,
            publication_date=published or None,
            url=e.get("id"),
            type="preprint",
            resolver=self.kind,
        )


class PubMedBibliographyResolver(_HttpHibliResolver):
    kind = BibliographyResolverKind.PUBMED

    def _url(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str],
    ) -> Optional[str]:
        if not pmid:
            return None
        return f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={pmid}&retmode=json"

    def _parse(self, payload: dict[str, Any]) -> Optional[BibliographicRecord]:
        # Walrus operator binds the first key of `result` to `pmid` and looks up its value
        # in the same expression. `pmid` here is the local re-extraction, not the input arg.
        result = (payload.get("result") or {}).get(
            pmid := next(iter((payload.get("result") or {})), "")
        )
        if not result:
            return None
        title = result.get("title")
        if not title:
            return None
        authors_raw = result.get("authors") or []
        authors = [a.get("name", "") for a in authors_raw if a.get("name")]
        year: Optional[int] = None
        pubdate = result.get("pubdate") or ""
        if pubdate[:4].isdigit():
            year = int(pubdate[:4])
        return BibliographicRecord(
            title=title,
            authors=[a for a in authors if a],
            year=year,
            publication_date=pubdate or None,
            container_title=result.get("source"),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            type="journal-article",
            resolver=self.kind,
        )


class OpenAlexBibliographyResolver(_HttpHibliResolver):
    kind = BibliographyResolverKind.OPENALEX

    def _url(
        self,
        *,
        doi: Optional[str],
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
    ) -> Optional[str]:
        if not doi:
            return None
        return f"https://api.openalex.org/works/doi:{doi}"

    def _parse(self, payload: dict[str, Any]) -> Optional[BibliographicRecord]:
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
    """Always returns None; populated manually via client UI (out of plan scope)."""

    kind = BibliographyResolverKind.USER

    def resolve(self, **_kwargs: Any) -> Optional[BibliographicRecord]:
        return None


class BibliographyResolverChain:
    """Runs resolvers in order; returns the first non-None result, or None."""

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
        for resolver in self._resolvers:
            try:
                rec = resolver.resolve(**kwargs)
            except BibliographyResolutionError:
                # Move to the next resolver on failure.
                continue
            if rec is not None:
                return rec
        return None
