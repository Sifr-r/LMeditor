"""Deterministic lexical analysis of source material for the discovery layer.

The discovery layer has to say something *about the corpus it was given*.
Three modules need the same three primitives -- the salient vocabulary of a
passage, the findings a passage actually states, and the headings a document
already carries -- so they live here instead of being re-implemented three
times with three different notions of "salient".

Everything in this module is deterministic and dependency-free: the same text
always yields the same terms, findings and headings. That is what makes the
fallback path of the discovery layer reproducible and testable.

This module is the **door**: the rules themselves are the Kotlin tier's, they
are implemented once in ``lmeditor.discovery``, and the tier serves them
across the NDJSON seam. The implementation this module used to carry now
lives in ``lmeditor._reference.discovery_source_analysis``, frozen, so the
parity harness can still ask both sides the same question.

There is deliberately no fallback to a Python implementation here: a silent
fallback would hide the exact drift the Kotlin tier exists to remove -- two
implementations that disagree, with only one of them in the request path.
"""

from __future__ import annotations

from lmeditor.tier import discovery as tier_discovery

#: Function words excluded from the content vocabulary.
#:
#: The tier holds the same table (``SourceAnalysis.STOPWORDS``). This constant
#: is kept because callers and tests import it; reading it from the tier would
#: mean a call across the seam at import time, which no module may do.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "then", "than", "so", "because",
        "of", "in", "on", "at", "to", "for", "with", "by", "from", "into", "onto",
        "over", "under", "about", "after", "before", "during", "while", "as",
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
        "do", "does", "did", "will", "would", "can", "could", "should", "may",
        "might", "must", "shall", "that", "this", "these", "those", "it", "its",
        "they", "them", "their", "we", "us", "our", "you", "your", "he", "she",
        "his", "her", "not", "no", "never", "also", "such", "there", "here",
        "which", "who", "what", "when", "where", "how", "why", "all", "any",
        "each", "other", "more", "most", "some", "only", "own", "same", "both",
        "very", "however", "therefore", "thus", "hence", "furthermore", "moreover",
    }
)

#: Tokens that describe the act of writing rather than the subject matter.
#:
#: The tier holds the same table (``SourceAnalysis.GENERIC_TERMS``); this
#: constant is kept for the same reason as [STOPWORDS].
GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "study", "studies", "paper", "papers", "article", "articles", "research",
        "researcher", "researchers", "authors", "author", "data", "results",
        "result", "method", "methods", "methodology", "analysis", "analyses",
        "evidence", "findings", "finding", "conclusion", "conclusions", "report",
        "reports", "section", "sections", "document", "documents", "text",
        "passage", "passages", "source", "sources", "example", "examples",
        "figure", "figures", "table", "tables", "introduction", "background",
        "overview", "summary", "abstract", "discussion", "appendix", "chapter",
        "paper's", "study's",
    }
)


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens of ``text``."""
    return tier_discovery.tokenize(text)


def content_tokens(text: str) -> list[str]:
    """Tokens of ``text`` that carry content, in order of appearance.

    Identifiers are excluded: they are not vocabulary, and the discovery
    layer reads this list to decide what a corpus is about.
    """
    return tier_discovery.content_tokens(text)


def salient_terms(text: str, limit: int = 12) -> list[str]:
    """The most distinctive vocabulary of ``text``.

    Terms are scored by frequency and ties are broken towards the longer,
    more specific word. Tokens too long to be words never enter the ranking
    at all, because a reader never names a passage by its digest.
    """
    return tier_discovery.salient_terms(text, limit)


def salient_terms_across(texts: list[str], limit: int = 12) -> list[str]:
    """Salient terms of several passages at once.

    A term that occurs in every passage is demoted: it is part of the shared
    vocabulary of the corpus rather than a finding of any one passage.
    """
    return tier_discovery.salient_terms_across(texts, limit)


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on terminal punctuation."""
    return tier_discovery.split_sentences(text)


def extract_findings(text: str, limit: int = 6) -> list[str]:
    """The sentences of ``text`` that state a finding.

    A finding sentence is one that carries a quantity or an explicit finding
    verb -- those are the sentences a source-grounded persona can interrogate.
    """
    return tier_discovery.extract_findings(text, limit)


def detect_headings(text: str) -> list[str]:
    """Headings that the document itself carries.

    Only line-initial heading forms are recognised, so a sentence that merely
    starts with a capital letter is not mistaken for a heading.
    """
    return tier_discovery.detect_headings(text)


def title_case_terms(terms: list[str]) -> str:
    """Join ``terms`` into a readable section heading fragment."""
    return tier_discovery.title_case_terms(terms)


def cluster_by_terms(texts: list[str], num_clusters: int) -> list[list[int]]:
    """Group the indices of ``texts`` into topical clusters.

    The clustering is agglomerative and fully deterministic: the two most
    similar clusters are merged until the requested number of clusters
    remains. When no two clusters share any vocabulary the two smallest
    clusters are merged instead, because a corpus that is genuinely
    heterogeneous still has to be reported as a hierarchy.
    """
    return tier_discovery.cluster_by_terms(texts, num_clusters)


__all__ = [
    "GENERIC_TERMS",
    "STOPWORDS",
    "cluster_by_terms",
    "content_tokens",
    "detect_headings",
    "extract_findings",
    "salient_terms",
    "salient_terms_across",
    "split_sentences",
    "title_case_terms",
    "tokenize",
]
