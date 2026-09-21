"""Comprehensive test suite for LMeditor NLI Verification Engine and Citation Checker."""

from __future__ import annotations

import pytest

from lmeditor.core.exceptions import CitationHallucinationError, ClaimExtractionError
from lmeditor.core.schemas import Claim, SectionDraft, SourceChunk
from lmeditor.core.types import EntailmentStatus, SectionStatus
from lmeditor.verification.citation_checker import (
    CitationChecker,
    compute_citation_precision,
    compute_citation_recall,
    compute_groundedness_f_beta,
)
from lmeditor.verification.claim_extractor import (
    ClaimExtractor,
    extract_citations,
    extract_claims,
    segment_sentences,
)
from lmeditor.verification.nli_evaluator import (
    HeuristicNLIEvaluator,
    LLMJudgeNLIEvaluator,
    ResilientNLIEvaluator,
)


# ============================================================================
# 1. Mathematical Metric Tests (P_cite, R_cite, F_beta)
# ============================================================================


def test_compute_citation_precision_standard() -> None:
    """Validate standard precision calculation."""
    # 8 supported out of 10 cited
    p = compute_citation_precision(supported_cited_claims=8, total_cited_claims=10, total_claims=10)
    assert p == 0.8

    # All cited claims supported
    p_all = compute_citation_precision(supported_cited_claims=5, total_cited_claims=5, total_claims=5)
    assert p_all == 1.0

    # None supported
    p_zero = compute_citation_precision(supported_cited_claims=0, total_cited_claims=5, total_claims=5)
    assert p_zero == 0.0


def test_compute_citation_precision_edge_cases() -> None:
    """Validate precision edge cases when total_cited_claims is zero."""
    # Vacuously true: no claims exist in manuscript
    p_empty = compute_citation_precision(supported_cited_claims=0, total_cited_claims=0, total_claims=0)
    assert p_empty == 1.0

    # Ungrounded failure: claims exist in draft but none were cited
    p_uncited = compute_citation_precision(supported_cited_claims=0, total_cited_claims=0, total_claims=5)
    assert p_uncited == 0.0


def test_compute_citation_recall_standard_and_edges() -> None:
    """Validate recall calculation and boundary conditions."""
    # Standard: 7 supported out of 10 claims
    r = compute_citation_recall(supported_claims=7, total_claims=10)
    assert r == 0.7

    # Edge: 0 claims in document -> recall is vacuously 1.0
    r_empty = compute_citation_recall(supported_claims=0, total_claims=0)
    assert r_empty == 1.0

    # 0 supported out of 8 claims
    r_zero = compute_citation_recall(supported_claims=0, total_claims=8)
    assert r_zero == 0.0


def test_compute_groundedness_f_beta_boundary_values() -> None:
    """Validate F_beta calculations with default beta=0.5."""
    # Perfect alignment
    f_perfect = compute_groundedness_f_beta(precision=1.0, recall=1.0, beta=0.5)
    assert f_perfect == 1.0

    # Zero precision or recall
    assert compute_groundedness_f_beta(precision=0.0, recall=1.0, beta=0.5) == 0.0
    assert compute_groundedness_f_beta(precision=1.0, recall=0.0, beta=0.5) == 0.0
    assert compute_groundedness_f_beta(precision=0.0, recall=0.0, beta=0.5) == 0.0


def test_compute_groundedness_f_beta_asymmetric_weighting() -> None:
    """Verify beta=0.5 weights precision higher than recall."""
    # Case A: High precision (0.8), low recall (0.4)
    f_a = compute_groundedness_f_beta(precision=0.8, recall=0.4, beta=0.5)

    # Case B: Low precision (0.4), high recall (0.8)
    f_b = compute_groundedness_f_beta(precision=0.4, recall=0.8, beta=0.5)

    # With beta=0.5, Case A must score higher than Case B
    assert f_a > f_b
    assert f_a == 0.6667
    assert f_b == 0.4444


# ============================================================================
# 2. Sentence Segmentation & Claim Extraction Tests
# ============================================================================


def test_segment_sentences_abbreviations_and_formatting() -> None:
    """Verify sentence boundary detector avoids splitting on abbreviations and floats."""
    text = (
        "Dr. Smith visited the U.S. facility, e.g. the lab in Boston. "
        "The estimated cost was $3.14 million for Phase 1. "
        "Another sentence follows."
    )
    sentences = segment_sentences(text)
    assert len(sentences) == 3
    assert "Dr. Smith visited the U.S. facility, e.g. the lab in Boston." in sentences[0]
    assert "$3.14 million" in sentences[1]
    assert sentences[2] == "Another sentence follows."


def test_segment_sentences_markdown_structures() -> None:
    """Verify segmentation handles markdown headings and list items."""
    text = (
        "# Introduction\n\n"
        "This is paragraph one.\n\n"
        "## Key Objectives\n"
        "- First objective is verifiable citations.\n"
        "- Second objective is deterministic state transitions.\n"
    )
    sentences = segment_sentences(text)
    assert len(sentences) == 5
    assert sentences[0] == "Introduction"
    assert sentences[1] == "This is paragraph one."
    assert sentences[2] == "Key Objectives"
    assert "First objective" in sentences[3]
    assert "Second objective" in sentences[4]


def test_extract_citations_various_syntaxes() -> None:
    """Verify extraction of [chunk_1], [cite: chunk_2], and multi-citations."""
    text = (
        "First statement [chunk_01]. "
        "Second statement with prefix [cite: chunk_02]. "
        "Compound statement [chunk_03, chunk_04] and adjacent [chunk_05][chunk_06]. "
        "Markdown link [Link text](https://example.com) should be ignored."
    )
    citations = extract_citations(text)
    chunk_ids = [c.chunk_id for c in citations]

    assert "chunk_01" in chunk_ids
    assert "chunk_02" in chunk_ids
    assert "chunk_03" in chunk_ids
    assert "chunk_04" in chunk_ids
    assert "chunk_05" in chunk_ids
    assert "chunk_06" in chunk_ids
    assert "Link text" not in chunk_ids
    assert len(citations) == 6


def test_extract_claims_proposition_deconstruction() -> None:
    """Verify compound sentence decomposition into clean atomic factual claims."""
    text = (
        "LMeditor enforces chunk provenance [chunk_01], and all propositions "
        "are verified against ground truth passages [chunk_02]."
    )
    claims = extract_claims(text, section_id="sec_01")
    assert len(claims) >= 2

    # Verify citation brackets were stripped from proposition text
    for claim in claims:
        assert "[" not in claim.text
        assert "]" not in claim.text
        assert claim.section_id == "sec_01"
        assert claim.entailment_status == EntailmentStatus.UNGROUNDED


def test_claim_extractor_invalid_input() -> None:
    """Verify ClaimExtractor raises ClaimExtractionError on non-string inputs."""
    extractor = ClaimExtractor()
    with pytest.raises(ClaimExtractionError):
        extractor.extract_claims(12345)  # type: ignore[arg-type]

    assert extractor.extract_claims("") == []
    assert extractor.extract_claims("   ") == []


# ============================================================================
# 3. NLI Evaluator Tests
# ============================================================================


@pytest.mark.asyncio
async def test_heuristic_nli_evaluator() -> None:
    """Verify heuristic NLI evaluation for entailment, contradiction, and neutral."""
    evaluator = HeuristicNLIEvaluator()

    # Exact entailment
    premise = "The study demonstrated a 45% reduction in error rates."
    hypo_entailed = "Error rates decreased by 45% according to the study."
    status, score = await evaluator.evaluate(premise, hypo_entailed)
    assert status == EntailmentStatus.ENTAILED
    assert score >= 0.65

    # Negation clash -> Contradiction
    hypo_contra = "The study did not demonstrate a 45% reduction in error rates."
    status_c, score_c = await evaluator.evaluate(premise, hypo_contra)
    assert status_c == EntailmentStatus.CONTRADICTED

    # Neutral / irrelevant
    hypo_neutral = "Quantum computing requires cryogenic cooling systems."
    status_n, _ = await evaluator.evaluate(premise, hypo_neutral)
    assert status_n in (EntailmentStatus.NEUTRAL, EntailmentStatus.UNGROUNDED)


@pytest.mark.asyncio
async def test_resilient_nli_evaluator_fallback() -> None:
    """Verify resilient composite evaluator falls back when primary fails."""
    # Primary evaluator that fails intentionally
    class FailingEvaluator:
        async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
            raise RuntimeError("Model server unreachable")

    fallback = HeuristicNLIEvaluator()
    resilient = ResilientNLIEvaluator(primary_evaluator=FailingEvaluator(), fallback_evaluator=fallback)  # type: ignore[arg-type]

    premise = "DNA replication is catalyzed by DNA polymerase enzymes."
    hypothesis = "DNA polymerase enzymes catalyze replication."

    status, score = await resilient.evaluate(premise, hypothesis)
    assert status == EntailmentStatus.ENTAILED
    assert score > 0.6


# ============================================================================
# 4. Citation Checker & Section Audit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_audit_section_perfect_verification() -> None:
    """Verify section audit workflow when all claims are entailed."""
    text_passage = "Stanford STORM explores diverse perspectives before outline generation."
    chunk_1 = SourceChunk.create(
        document_id="doc_1",
        text=text_passage,
        char_start=0,
        char_end=len(text_passage),
        byte_start=0,
        byte_end=len(text_passage.encode("utf-8")),
    )
    chunks_map = {chunk_1.chunk_id: chunk_1}

    section = SectionDraft(
        section_id="sec_storm",
        title="Perspective Discovery",
        content=f"Stanford STORM explores diverse perspectives before outline generation [{chunk_1.chunk_id}].",
    )

    checker = CitationChecker(evaluator=HeuristicNLIEvaluator(), beta=0.5)
    audited_section, scorecard = await checker.audit_section(section, chunks_map)

    assert scorecard.precision == 1.0
    assert scorecard.recall == 1.0
    assert scorecard.f_beta == 1.0
    assert scorecard.supported_claims == 1
    assert scorecard.ungrounded_claims == 0
    assert scorecard.contradicted_claims == 0
    assert audited_section.is_verified is True
    assert audited_section.status == SectionStatus.VERIFIED


@pytest.mark.asyncio
async def test_audit_section_hallucinated_chunk_raises_error() -> None:
    """Verify audit raises CitationHallucinationError for non-existent chunk IDs."""
    section = SectionDraft(
        section_id="sec_err",
        title="Error Section",
        content="This claim references a phantom chunk [chunk_nonexistent_999].",
    )
    checker = CitationChecker(evaluator=HeuristicNLIEvaluator())

    with pytest.raises(CitationHallucinationError) as exc_info:
        await checker.audit_section(section, chunks_by_id={}, strict_hallucination_check=True)

    assert "chunk_nonexistent_999" in str(exc_info.value)
