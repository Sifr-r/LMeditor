"""The frozen ingestion orchestration.

This is the original Python implementation of ``ingestion.service``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Its imports are rewired onto the frozen siblings in this package -- the loader registry, the resolver chain and the chunker
-- so that it cannot reach the seam by accident: a witness that leaned on a
door would be measuring the tier against itself.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

High-level ingestion orchestration: load → resolve bibliography → chunk → return."""

from __future__ import annotations

from typing import Any, Optional

from lmeditor.core.schemas import (
    BibliographicRecord,
    Source,
    SourceChunk,
    SourceIngestResult,
)
from lmeditor.core.types import SourceKind
from lmeditor._reference.ingestion_registry import SourceLoaderRegistry
from lmeditor._reference.ingestion_resolvers import BibliographyResolverChain
from lmeditor._reference.chunker import SentenceAwareChunker


class IngestionService:
    """Orchestrates the full ingestion pipeline for one Source."""

    def __init__(
        self,
        registry: SourceLoaderRegistry,
        resolvers: BibliographyResolverChain,
        chunker: SentenceAwareChunker,
    ) -> None:
        self._registry = registry
        self._resolvers = resolvers
        self._chunker = chunker

    @classmethod
    def default(cls) -> "IngestionService":
        return cls(
            registry=SourceLoaderRegistry.default(),
            resolvers=BibliographyResolverChain.default(),
            chunker=SentenceAwareChunker(chunk_size=120, overlap_size=25),
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
        loaded = self._registry.load_detailed(kind, origin)

        # The loader's own metadata wins over nothing, and an explicitly
        # supplied one wins over the loader's: the caller knows more than the
        # document does.
        merged_metadata: dict[str, Any] = dict(loaded.pdf_metadata)
        if pdf_metadata:
            merged_metadata.update(pdf_metadata)

        # Build bibliography record via resolver chain.
        bibliography: Optional[BibliographicRecord] = None
        if any([doi, arxiv_id, pmid, merged_metadata]):
            bibliography = self._resolvers.resolve(
                source_id=source_id,
                source_kind=kind,
                doi=doi,
                arxiv_id=arxiv_id,
                pmid=pmid,
                pdf_metadata=merged_metadata,
            )

        source = Source(
            id=source_id,
            kind=kind,
            origin=origin,
            title=title,
            bibliography=bibliography,
            page_count=loaded.page_count,
            byte_size=byte_size if byte_size is not None else loaded.byte_size,
            omissions=list(loaded.omissions),
        )

        chunks: list[SourceChunk] = self._chunker.chunk_text(
            text=loaded.text,
            document_id=source_id,
            document_title=title,
        )

        return SourceIngestResult(
            source=source,
            chunks=chunks,
            omissions=list(loaded.omissions),
        )
