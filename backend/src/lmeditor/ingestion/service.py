"""High-level ingestion orchestration: load -> resolve bibliography -> chunk -> return.

The pipeline behind one source is deterministic, and all of it is the Kotlin
tier's: the loader that reads the artifact, the resolver chain that finds its
bibliographic record, and the chunker that cuts the text into passages. This
module is the *door* onto that pipeline. It keeps the Python API its callers
already use -- the constructor, the factory and the ``ingest`` signature --
and answers with the tier's answers.

The original implementation is frozen in
:mod:`lmeditor._reference.ingestion_service`.

No parity check compares the two sides yet: the pipeline ends in a chunked
result that carries the resolver's answer with it, so a comparison would need
one frozen corpus of sources and one shared answer for each. The gap is
declared rather than silent: the ledger ``REFERENCE_WITNESSES`` in
``backend/scripts/verify_kotlin_parity.py`` names this module against its
Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/IngestionService.kt``) and
the harness fails if that declaration stops matching the tree.
"""

from __future__ import annotations

from typing import Any, Optional

from lmeditor.core.schemas import (
    BibliographicRecord,
    Source,
    SourceChunk,
    SourceIngestResult,
)
from lmeditor.core.types import SourceKind
from lmeditor.ingestion.registry import SourceLoaderRegistry
from lmeditor.ingestion.resolvers import BibliographyResolverChain
from lmeditor.provenance.chunker import SentenceAwareChunker
from lmeditor.tier import ingest as tier_ingest


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
        """Ingest one source and return the source and the chunks it was cut into.

        The loader's own metadata wins over nothing, and an explicitly
        supplied one wins over the loader's: the caller knows more than the
        document does. Both the merge and the bibliography resolution that
        follows it are the tier's rules.
        """
        return tier_ingest.ingest_source(
            kind=kind,
            origin=origin,
            source_id=source_id,
            title=title,
            doi=doi,
            arxiv_id=arxiv_id,
            pmid=pmid,
            pdf_metadata=dict(pdf_metadata) if pdf_metadata else None,
            byte_size=byte_size,
        )
