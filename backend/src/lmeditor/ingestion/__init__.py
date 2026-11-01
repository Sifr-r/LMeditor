"""Source ingestion: loaders, registry, resolvers, and orchestration service."""

from lmeditor.ingestion.limits import (
    MAX_RESOLVER_RESPONSE_BYTES,
    MAX_SOURCE_STORE_BYTES,
    MAX_UPLOAD_BYTES,
    MAX_URL_RESPONSE_BYTES,
    PayloadTooLargeError,
    validate_upload_size,
)
from lmeditor.ingestion.loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    LoadedSource,
    MarkdownSourceLoader,
    PdfSourceLoader,
    SourceLoader,
    TextSourceLoader,
    UrlSourceLoader,
)
from lmeditor.ingestion.pdf_metadata import extract_pdf_metadata
from lmeditor.ingestion.registry import SourceLoaderRegistry
from lmeditor.ingestion.resolvers import (
    ArxivBibliographyResolver,
    BibliographyResolver,
    BibliographyResolverChain,
    CrossRefBibliographyResolver,
    OpenAlexBibliographyResolver,
    PdfHeaderBibliographyResolver,
    PubMedBibliographyResolver,
    UserBibliographyResolver,
)
from lmeditor.ingestion.service import IngestionService
from lmeditor.ingestion.source_store import SourceStore, default_store

__all__ = [
    "ArxivBibliographyResolver",
    "BibliographyResolver",
    "BibliographyResolverChain",
    "CrossRefBibliographyResolver",
    "DocxSourceLoader",
    "HtmlSourceLoader",
    "IngestionService",
    "LoadedSource",
    "MAX_RESOLVER_RESPONSE_BYTES",
    "MAX_SOURCE_STORE_BYTES",
    "MAX_UPLOAD_BYTES",
    "MAX_URL_RESPONSE_BYTES",
    "MarkdownSourceLoader",
    "OpenAlexBibliographyResolver",
    "PayloadTooLargeError",
    "PdfHeaderBibliographyResolver",
    "PdfSourceLoader",
    "PubMedBibliographyResolver",
    "SourceLoader",
    "SourceLoaderRegistry",
    "SourceStore",
    "TextSourceLoader",
    "UrlSourceLoader",
    "UserBibliographyResolver",
    "default_store",
    "extract_pdf_metadata",
    "validate_upload_size",
]
