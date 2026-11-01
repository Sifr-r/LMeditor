"""The fact-check rules, as the Kotlin tier implements them.

The evidence comes from Python -- the corpus index and the open web -- and so
does the judgement of each claim, because the entailment model is the Python
tier's business. The aggregation of those verdicts into a report is a rule,
and the rule is Kotlin's.

The web half is the other way round: the search request, the parsing of the
response, the address a result points at and the text a page is read as are
all rules, and they are Kotlin's too. What is left for Python is the request
itself: what to search for, how many results to keep and how long a page read
may take.
"""

from __future__ import annotations

import base64
from typing import Any

from lmeditor.core.schemas import (
    ClaimVerdict,
    FactCheckReport,
    VerificationScorecard,
)
from lmeditor.tier.bridge import tier_bridge


def relevance(claim_text: str, evidence_text: str) -> float:
    """The share of the claim's content words the evidence contains.

    The measure is a rule: it decides whether a passage is evidence at all.
    A passage that shares nothing with the claim cannot support it, cannot
    refute it, and is not evidence for anything.
    """
    result = tier_bridge().call(
        "factcheck.relevance",
        {"claim_text": claim_text, "evidence_text": evidence_text},
    )
    return float(result.get("relevance", 0.0))


def evidence_limit(max_evidence: int, requested_max_evidence: int) -> int:
    """How many evidence items each claim is given.

    The request is authoritative when it expresses a preference of its own;
    the checker's own default applies when it does not.
    """
    result = tier_bridge().call(
        "factcheck.evidence_limit",
        {
            "max_evidence": max_evidence,
            "requested_max_evidence": requested_max_evidence,
        },
    )
    return int(result.get("limit", max_evidence))


def validate_max_evidence(max_evidence: int) -> int:
    """Return ``max_evidence`` when it is inside the accepted range.

    The range is the tier's: a value outside it is refused by the Kotlin rule
    and the refusal arrives as the Python ``FactCheckError``.
    """
    tier_bridge().call(
        "factcheck.validate_max_evidence",
        {"max_evidence": max_evidence},
    )
    return max_evidence


def aggregate(
    doc_id: str,
    text: str,
    claims: list[dict[str, Any]],
    verdicts: list[dict[str, Any]],
    *,
    web_searched: bool = False,
    evidence_found: int | None = None,
    notes: list[str] | None = None,
    beta: float = 0.5,
    evaluator_name: str = "",
) -> tuple[FactCheckReport, VerificationScorecard | None]:
    """The verdict over a passage, from the verdicts on its claims.

    ``claims`` are the claims the passage was split into, and ``verdicts`` the
    verdict on each of them, each carrying the evidence that was considered.
    The scorecard that comes back is the one the verdicts imply, so the two
    can never disagree.
    """
    result = tier_bridge().call(
        "factcheck.aggregate",
        {
            "doc_id": doc_id,
            "text": text,
            "claims": claims,
            "verdicts": verdicts,
            "web_searched": web_searched,
            "evidence_found": evidence_found,
            "notes": notes or [],
            "beta": beta,
            "evaluator_name": evaluator_name,
        },
    )
    report = FactCheckReport.model_validate(result["report"])
    raw_scorecard = result.get("scorecard")
    scorecard = (
        VerificationScorecard.model_validate(raw_scorecard)
        if isinstance(raw_scorecard, dict)
        else None
    )
    return report, scorecard


def web_search(
    query: str,
    limit: int = 5,
    *,
    timeout: float = 8.0,
    max_bytes: int = 262_144,
) -> list[dict[str, Any]]:
    """The hits the search engine reported for ``query``.

    The request URL, the query that rides in it and the parsing of the
    response are the tier's rules. What the caller owns is the query, how
    many results it wants and the budget the request runs under.
    """
    result = tier_bridge().call(
        "factcheck.web.search",
        {
            "query": query,
            "limit": limit,
            "timeout": timeout,
            "max_bytes": max_bytes,
        },
    )
    return [dict(item) for item in result.get("results", [])]


def web_fetch_page_text(
    url: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = 262_144,
) -> str:
    """The visible text of the page at ``url``.

    The address is vetted and the body is bounded by the tier, so a page that
    names an internal service is refused rather than fetched.
    """
    result = tier_bridge().call(
        "factcheck.web.fetch_page_text",
        {"url": url, "timeout": timeout, "max_bytes": max_bytes},
    )
    return str(result.get("text", ""))


def web_parse_search_results(body: bytes) -> list[dict[str, Any]]:
    """The results the search response held.

    The body crosses the seam as base64: a search response is bytes, and
    carrying them as text would change them.
    """
    result = tier_bridge().call(
        "factcheck.web.parse_search_results",
        {"body": base64.b64encode(body).decode("ascii")},
    )
    return [dict(item) for item in result.get("results", [])]


def web_normalise_result_url(href: str) -> str:
    """The address a search result actually points at."""
    result = tier_bridge().call(
        "factcheck.web.normalise_result_url",
        {"href": href},
    )
    return str(result.get("url", ""))


def web_extract_page_text(body: bytes) -> str:
    """The visible text of an HTML document."""
    result = tier_bridge().call(
        "factcheck.web.extract_page_text",
        {"body": base64.b64encode(body).decode("ascii")},
    )
    return str(result.get("text", ""))


__all__ = [
    "aggregate",
    "relevance",
    "evidence_limit",
    "validate_max_evidence",
    "web_extract_page_text",
    "web_fetch_page_text",
    "web_normalise_result_url",
    "web_parse_search_results",
    "web_search",
    "ClaimVerdict",
]
