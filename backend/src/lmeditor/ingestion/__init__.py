"""Source ingestion: loaders, registry, resolvers, and orchestration service."""

from lmeditor.ingestion.loaders import (
    DocxSourceLoader,
    HtmlSourceLoader,
    MarkdownSourceLoader,
    PdfSourceLoader,
    SourceLoader,
    TextSourceLoader,
    UrlSourceLoader,
)
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

__all__ = [
    "ArxivBibliographyResolver",
    "BibliographyResolver",
    "BibliographyResolverChain",
    "CrossRefBibliographyResolver",
    "DocxSourceLoader",
    "HtmlSourceLoader",
    "IngestionService",
    "MarkdownSourceLoader",
    "OpenAlexBibliographyResolver",
    "PdfHeaderBibliographyResolver",
    "PdfSourceLoader",
    "PubMedBibliographyResolver",
    "SourceLoader",
    "SourceLoaderRegistry",
    "TextSourceLoader",
    "UrlSourceLoader",
    "UserBibliographyResolver",
]
