"""Fact-checking: a passage judged against the corpus, the web, or both."""

from lmeditor.factcheck.service import FactChecker
from lmeditor.factcheck.web import (
    DuckDuckGoSearchProvider,
    NullWebSearchProvider,
    WebResult,
    WebSearchProvider,
)

__all__ = [
    "DuckDuckGoSearchProvider",
    "FactChecker",
    "NullWebSearchProvider",
    "WebResult",
    "WebSearchProvider",
]
