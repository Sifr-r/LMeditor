"""High-level ingestion orchestration: load → resolve bibliography → chunk → return."""

from __future__ import annotations

from typing import Optional

from lmeditor.core.schemas import (
    BibliographicRecord,
    Source,
    SourceChunk,
    SourceIngestResult,
)
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.registry import SourceLoaderRegistry
from lmeditor.ingestion.resolvers import BibliographyResolverChain
from lmeditor.provenance.chunker import SourceChunker


class IngestionService:
    """Orchestrates the full ingestion pipeline for one Source."""

    def __init__(
        self,
        registry: SourceLoaderRegistry,
        resolvers: BibliographyResolverChain,
        chunker: SourceChunker,
    ) -> None:
        self._registry = registry
        self._resolvers = resolvers
        self._chunker = chunker

    @classmethod
    def default(cls) -> "IngestionService":
        return cls(
            registry=SourceLoaderRegistry.default(),
            resolvers=BibliographyResolverChain.default(),
            chunker=SourceChunker(chunk_size=120, overlap_size=25),
        )

    def ingest(
        self,
        *,
        source_id: str,
        kind: SourceKind,
        origin: str,
        title: str,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        pmid: Optional[str] = None,
        pdf_metadata: Optional[dict[str, object]] = None,
        byte_size: Optional[int] = None,
    ) -> SourceIngestResult:
        text, page_count = self._registry.load(kind, origin)

        # Build bibliography record via resolver chain.
        bibliography: Optional[BibliographicRecord] = None
        if any([doi, arxiv_id, pmid, pdf_metadata]):
            bibliography = self._resolvers.resolve(
                source_id=source_id,
                source_kind=kind,
                doi=doi,
                arxiv_id=arxiv_id,
                pmid=pmid,
                pdf_metadata=pdf_metadata,
            )

        source = Source(
            id=source_id,
            kind=kind,
            origin=origin,
            title=title,
            bibliography=bibliography,
            page_count=page_count,
            byte_size=byte_size,
        )

        chunks: list[SourceChunk] = self._chunker.chunk_text(
            text=text,
            document_id=source_id,
            document_title=title,
        )

        return SourceIngestResult(source=source, chunks=chunks)