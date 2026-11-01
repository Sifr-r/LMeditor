"""The text rules, as the Kotlin tier implements them.

Chunking, claim segmentation, token-span lineage, the citation audit and the
retrieval rules. The audit takes *scored* claims: the entailment model stays
in Python, and the arithmetic that decides whether a document is grounded is
Kotlin's. The retrieval rules are the same kind of thing: the scoring is
Kotlin's, and the model that produces the dense vectors stays here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lmeditor.core.schemas import (
    CitationLineage,
    Claim,
    InlineCitation,
    SectionDraft,
    SourceChunk,
    VerificationScorecard,
)
from lmeditor.tier.bridge import tier_bridge


def chunk(
    text: str,
    document_id: str,
    metadata: dict[str, Any] | None = None,
    window_size: int | None = None,
    overlap_size: int | None = None,
) -> list[SourceChunk]:
    """Segment ``text`` into traceable source chunks.

    The window is a parameter of the chunking rule, so a chunker configured
    with another window answers with that window's chunks. A window left as
    ``None`` is not sent, and the tier's own default applies.
    """
    params: dict[str, Any] = {"text": text, "document_id": document_id}
    if metadata:
        params["metadata"] = metadata
    if window_size is not None:
        params["window_size"] = window_size
    if overlap_size is not None:
        params["overlap_size"] = overlap_size
    result = tier_bridge().call("text.chunk", params)
    return [SourceChunk.model_validate(item) for item in result.get("chunks", [])]


def extract_claims(text: str, section_id: str) -> list[Claim]:
    """Split ``text`` into atomic propositions.

    The claims carry no entailment verdict: the model that produces one lives
    in the Python tier, and its answer is handed back to Kotlin for the audit.
    """
    result = tier_bridge().call(
        "text.claims.extract",
        {"text": text, "section_id": section_id},
    )
    return [Claim.model_validate(item) for item in result.get("claims", [])]


def segment_sentences(text: str) -> list[str]:
    """Split ``text`` into the sentences the claim extractor works on."""
    result = tier_bridge().call("text.claims.segment_sentences", {"text": text})
    return [str(item) for item in result.get("sentences", [])]


def extract_citations(text: str) -> list[InlineCitation]:
    """Return the inline citation tokens ``text`` carries."""
    result = tier_bridge().call("text.citations.extract", {"text": text})
    return [InlineCitation.model_validate(item) for item in result.get("citations", [])]


def attribute_span(
    draft_text: str,
    span_start: int,
    span_end: int,
    cited_chunk_id: str,
    claim_id: str,
    chunks: list[SourceChunk],
    exact_quote: str | None = None,
) -> CitationLineage:
    """Attribute a span of a draft to the source chunk it came from.

    The rule -- the boundary check, the grounding check and the confidence
    score -- is the Kotlin tier's; this call names the payload and reads the
    answer back.
    """
    params: dict[str, Any] = {
        "draft_text": draft_text,
        "span_start": span_start,
        "span_end": span_end,
        "chunk_id": cited_chunk_id,
        "claim_id": claim_id,
        "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
    }
    if exact_quote is not None:
        params["exact_quote"] = exact_quote
    result = tier_bridge().call("text.lineage.attribute_span", params)
    lineages = result.get("lineages") or []
    if not lineages:
        raise ValueError("the Kotlin tier returned no lineage")
    return CitationLineage.model_validate(lineages[0])


def attribute_claim(
    claim_id: str,
    claim_text: str,
    chunks: Sequence[SourceChunk],
    *,
    draft_text: str | None = None,
    span_start: int = 0,
    span_end: int | None = None,
    top_k: int = 5,
    rrf_k: int = 60,
    alpha: float = 0.5,
    k1: float = 1.5,
    b: float = 0.75,
    embedding_dim: int = 128,
    dense_vectors: Mapping[str, Sequence[float]] | None = None,
    query_vector: Sequence[float] | None = None,
) -> list[CitationLineage]:
    """Attribute a claim to the passages that support it.

    The whole composition is the Kotlin tier's: the ranking of the corpus for
    the claim's own text, the text the claim and a passage share, the span the
    claim occupies in the draft, and the confidence the quote's share of the
    claim scales it to. One call, one round trip across the seam.

    The dense vectors are the *model's* half, so they come from the caller:
    when they are given, the tier uses them as they are, and when they are
    not, the tier computes its own deterministic term-frequency hash vectors.
    """
    params: dict[str, Any] = {
        "claim_id": claim_id,
        "claim_text": claim_text,
        "chunks": [{"chunk_id": chunk.chunk_id, "text": chunk.text} for chunk in chunks],
        "span_start": span_start,
        "top_k": top_k,
        "rrf_k": rrf_k,
        "alpha": alpha,
        "k1": k1,
        "b": b,
        "embedding_dim": embedding_dim,
    }
    if draft_text is not None:
        params["draft_text"] = draft_text
    if span_end is not None:
        params["span_end"] = span_end
    if dense_vectors is not None:
        params["dense_vectors"] = {
            chunk_id: [float(value) for value in vector]
            for chunk_id, vector in dense_vectors.items()
        }
    if query_vector is not None:
        params["query_vector"] = [float(value) for value in query_vector]
    result = tier_bridge().call("text.lineage.attribute_claim", params)
    return [CitationLineage.model_validate(item) for item in result.get("lineages", [])]


def extract_citations_from_draft(
    draft_text: str,
    chunks: list[SourceChunk],
) -> list[CitationLineage]:
    """Bind the citation markers of a draft back to the source chunks.

    The marker grammar is the one the drafting side writes and the manuscript
    bridge reads, so a section's lineage records and its footnotes can never
    disagree about which chunks the section cites.
    """
    result = tier_bridge().call(
        "text.lineage.extract_from_draft",
        {
            "draft_text": draft_text,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        },
    )
    return [CitationLineage.model_validate(item) for item in result.get("lineages", [])]


def resolve_premises(
    claims: list[Claim],
    chunks: list[SourceChunk],
    strict_hallucination_check: bool = False,
    section_id: str = "",
) -> tuple[list[bool], list[str]]:
    """The premise each claim is judged against, and whether it is cited.

    The premise is a rule -- a chunk is narrowed to the sentence that speaks
    to the claim -- and the rule is Kotlin's. The entailment model lives in
    the Python tier and needs the premise before it can judge the claim, so
    the premises travel out and the verdicts travel back.
    """
    result = tier_bridge().call(
        "text.citation_check.resolve_premises",
        {
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "strict_hallucination_check": strict_hallucination_check,
            "section_id": section_id,
        },
    )
    cited = [bool(item) for item in result.get("cited", [])]
    premises = [str(item) for item in result.get("premises", [])]
    return cited, premises


def citation_precision(
    supported_cited_claims: int,
    total_cited_claims: int,
    total_claims: int,
) -> float:
    """The citation precision of some counts.

    The arithmetic is Kotlin's, so a caller that holds counts but no claims
    gets the very number the audit would have produced for them.
    """
    result = tier_bridge().call(
        "text.citation_check.precision",
        {
            "supported_cited_claims": supported_cited_claims,
            "total_cited_claims": total_cited_claims,
            "total_claims": total_claims,
        },
    )
    return float(result.get("precision", 0.0))


def citation_recall(supported_claims: int, total_claims: int) -> float:
    """The citation recall of some counts."""
    result = tier_bridge().call(
        "text.citation_check.recall",
        {
            "supported_claims": supported_claims,
            "total_claims": total_claims,
        },
    )
    return float(result.get("recall", 0.0))


def groundedness_f_beta(precision: float, recall: float, beta: float = 0.5) -> float:
    """The groundedness score of a precision and a recall."""
    result = tier_bridge().call(
        "text.citation_check.f_beta",
        {
            "precision": precision,
            "recall": recall,
            "beta": beta,
        },
    )
    return float(result.get("f_beta", 0.0))


def audit_citations(
    claims: list[Claim],
    chunks: list[SourceChunk],
    beta: float = 0.5,
    evaluator_name: str = "",
    strict_hallucination_check: bool = False,
    section_id: str = "",
) -> tuple[VerificationScorecard, list[Claim]]:
    """The citation precision/recall audit of scored claims against chunks.

    Returns the scorecard and the claims as the audit left them. The claims
    arrive already carrying their entailment verdicts: the model that produced
    them lives in the Python tier.
    """
    result = tier_bridge().call(
        "text.citation_check.audit",
        {
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "beta": beta,
            "evaluator_name": evaluator_name,
            "strict_hallucination_check": strict_hallucination_check,
            "section_id": section_id,
        },
    )
    scorecard = VerificationScorecard.model_validate(result["scorecard"])
    audited = [Claim.model_validate(item) for item in result.get("claims", [])]
    return scorecard, audited


def audit_section(
    section: SectionDraft,
    chunks: list[SourceChunk],
    beta: float = 0.5,
    threshold_f_beta: float = 0.85,
    evaluator_name: str = "",
    strict_hallucination_check: bool = False,
) -> tuple[SectionDraft, VerificationScorecard]:
    """The verdict over a whole section: its metrics, its status and its critique.

    The claims the section carries have already been judged by the model in
    the Python tier, so they arrive carrying their verdicts.
    """
    result = tier_bridge().call(
        "text.citation_check.audit_section",
        {
            "section": section.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "beta": beta,
            "threshold_f_beta": threshold_f_beta,
            "evaluator_name": evaluator_name,
            "strict_hallucination_check": strict_hallucination_check,
        },
    )
    updated = SectionDraft.model_validate(result["section"])
    scorecard = VerificationScorecard.model_validate(result["scorecard"])
    return updated, scorecard


def rank_retrieval(
    chunks: Sequence[SourceChunk],
    query: str,
    *,
    mode: str = "hybrid",
    top_k: int = 10,
    rrf_k: int = 60,
    alpha: float = 0.5,
    k1: float = 1.5,
    b: float = 0.75,
    embedding_dim: int = 128,
    dense_vectors: Mapping[str, Sequence[float]] | None = None,
    query_vector: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Rank the chunks for a query, the way the retrieval rule does.

    ``mode`` is ``"bm25"``, ``"dense"`` or ``"hybrid"``; the hybrid mode is
    the reciprocal rank fusion of the other two.

    The dense vectors are the *model's* half, so they come from the caller:
    when they are given, the tier uses them as they are, and when they are
    not, the tier computes its own deterministic term-frequency hash vectors.
    """
    params: dict[str, Any] = {
        "chunks": [{"chunk_id": chunk.chunk_id, "text": chunk.text} for chunk in chunks],
        "query": query,
        "mode": mode,
        "top_k": top_k,
        "rrf_k": rrf_k,
        "alpha": alpha,
        "k1": k1,
        "b": b,
        "embedding_dim": embedding_dim,
    }
    if dense_vectors is not None:
        params["dense_vectors"] = {
            chunk_id: [float(value) for value in vector]
            for chunk_id, vector in dense_vectors.items()
        }
    if query_vector is not None:
        params["query_vector"] = [float(value) for value in query_vector]
    result = tier_bridge().call("text.retrieval.rank", params)
    return [
        (str(item["chunk_id"]), float(item["score"]))
        for item in result.get("results", [])
    ]


def supporting_text(claim_text: str, chunk_text: str) -> tuple[str | None, float]:
    """The text a claim and a passage share, and the claim's coverage.

    The rule -- the longest run of shared words, the rejection of a run of
    function words, and the coverage -- is the Kotlin tier's. This call names
    the two texts and reads the answer back.
    """
    result = tier_bridge().call(
        "text.lineage.supporting_text",
        {"claim_text": claim_text, "chunk_text": chunk_text},
    )
    quote = result.get("quote")
    return (
        str(quote) if quote is not None else None,
        float(result.get("coverage", 0.0)),
    )


__all__ = [
    "chunk",
    "extract_claims",
    "extract_citations",
    "segment_sentences",
    "attribute_span",
    "attribute_claim",
    "extract_citations_from_draft",
    "resolve_premises",
    "citation_precision",
    "citation_recall",
    "groundedness_f_beta",
    "audit_citations",
    "audit_section",
    "rank_retrieval",
    "supporting_text",
]
