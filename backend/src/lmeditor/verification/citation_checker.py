"""Mathematical calculation of citation precision, recall, groundedness, and section auditing."""

from __future__ import annotations

from typing import Mapping, Sequence
from lmeditor.core.exceptions import CitationHallucinationError
from lmeditor.core.schemas import Claim, SectionDraft, SourceChunk, VerificationScorecard
from lmeditor.core.types import EntailmentStatus, NLIEvaluatorProtocol, SectionStatus
from lmeditor.verification.claim_extractor import extract_claims
from lmeditor.verification.nli_evaluator import default_nli_evaluator


def compute_citation_precision(supported_cited_claims: int, total_cited_claims: int, total_claims: int) -> float:
    """Calculate Citation Precision (P_cite).

    P_cite = (supported cited claims) / (total cited claims)
    Edge cases:
      - If total cited claims == 0: return 1.0 if total claims == 0 else 0.0
    """
    if total_cited_claims == 0:
        return 1.0 if total_claims == 0 else 0.0

    return round(supported_cited_claims / total_cited_claims, 4)


def compute_citation_recall(supported_claims: int, total_claims: int) -> float:
    """Calculate Citation Recall (R_cite).

    R_cite = (supported claims) / (total claims)
    Edge cases:
      - If total claims == 0: return 1.0
    """
    if total_claims == 0:
        return 1.0

    return round(supported_claims / total_claims, 4)


def compute_groundedness_f_beta(precision: float, recall: float, beta: float = 0.5) -> float:
    """Calculate Groundedness (F_beta) harmonic mean weighting precision vs recall.

    F_beta = (1 + beta^2) * (P * R) / (beta^2 * P + R)
    For beta = 0.5:
      F_0.5 = 1.25 * (P * R) / (0.25 * P + R)
    Edge case:
      - If P + R == 0, returns 0.0
    """
    if precision <= 0.0 or recall <= 0.0:
        return 0.0

    beta_sq = beta ** 2
    denominator = (beta_sq * precision) + recall
    if denominator == 0.0:
        return 0.0

    f_beta = (1.0 + beta_sq) * (precision * recall) / denominator
    return round(f_beta, 4)


class CitationChecker:
    """Auditor for citation precision, recall, and groundedness."""

    def __init__(self, evaluator: NLIEvaluatorProtocol | None = None, beta: float = 0.5) -> None:
        self.evaluator: NLIEvaluatorProtocol = evaluator or default_nli_evaluator
        self.beta: float = beta

    async def audit_claims(
        self,
        claims: Sequence[Claim],
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool = True,
        section_id: str = "",
    ) -> tuple[list[Claim], VerificationScorecard]:
        """Verify each claim against cited source chunks and compute verification metrics."""
        evaluated_claims: list[Claim] = []
        supported_cited = 0
        total_cited = 0
        supported_total = 0
        ungrounded_total = 0
        contradicted_total = 0
        neutral_total = 0

        for claim in claims:
            # Check for hallucinated chunk citations
            if claim.cited_chunk_ids:
                total_cited += 1
                for cid in claim.cited_chunk_ids:
                    if cid not in chunks_by_id:
                        if strict_hallucination_check:
                            raise CitationHallucinationError(
                                chunk_id=cid,
                                section_id=section_id or claim.section_id,
                                quote=claim.text,
                                reason=f"Cited chunk '{cid}' is missing from source index.",
                            )

                # Assemble premises
                premises = [
                    chunks_by_id[cid].text for cid in claim.cited_chunk_ids if cid in chunks_by_id
                ]
                premise_text = " ".join(premises)

                # Evaluate entailment
                status, score = await self.evaluator.evaluate(premise_text, claim.text)

                if status == EntailmentStatus.ENTAILED:
                    supported_cited += 1
                    supported_total += 1
                elif status == EntailmentStatus.CONTRADICTED:
                    contradicted_total += 1
                elif status == EntailmentStatus.NEUTRAL:
                    neutral_total += 1
                else:
                    ungrounded_total += 1

                evaluated_claims.append(
                    Claim(
                        claim_id=claim.claim_id,
                        section_id=claim.section_id,
                        sentence_idx=claim.sentence_idx,
                        text=claim.text,
                        char_start=claim.char_start,
                        char_end=claim.char_end,
                        cited_chunk_ids=claim.cited_chunk_ids,
                        entailment_status=status,
                        entailment_score=score,
                        premise_text=premise_text,
                        reasoning=f"NLI evaluation returned {status.value} with confidence {score:.4f}",
                    )
                )
            else:
                # No citations attached
                ungrounded_total += 1
                evaluated_claims.append(
                    Claim(
                        claim_id=claim.claim_id,
                        section_id=claim.section_id,
                        sentence_idx=claim.sentence_idx,
                        text=claim.text,
                        char_start=claim.char_start,
                        char_end=claim.char_end,
                        cited_chunk_ids=[],
                        entailment_status=EntailmentStatus.UNGROUNDED,
                        entailment_score=0.0,
                        premise_text=None,
                        reasoning="Claim has no inline citations.",
                    )
                )

        total_claims = len(claims)
        precision = compute_citation_precision(supported_cited, total_cited, total_claims)
        recall = compute_citation_recall(supported_total, total_claims)
        f_beta = compute_groundedness_f_beta(precision, recall, beta=self.beta)

        scorecard = VerificationScorecard(
            precision=precision,
            recall=recall,
            f_beta=f_beta,
            beta=self.beta,
            total_claims=total_claims,
            supported_claims=supported_total,
            ungrounded_claims=ungrounded_total,
            contradicted_claims=contradicted_total,
            neutral_claims=neutral_total,
            details=evaluated_claims,
        )

        return evaluated_claims, scorecard

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, VerificationScorecard]:
        """Audit all claims in a manuscript section and update section status."""
        claims = section.claims
        if not claims:
            claims = extract_claims(section.content, section_id=section.section_id)

        evaluated_claims, scorecard = await self.audit_claims(
            claims=claims,
            chunks_by_id=chunks_by_id,
            strict_hallucination_check=strict_hallucination_check,
            section_id=section.section_id,
        )

        # Determine section status
        is_verified = (
            scorecard.f_beta >= 0.85
            and scorecard.contradicted_claims == 0
            and scorecard.ungrounded_claims == 0
        )
        status = SectionStatus.VERIFIED if is_verified else SectionStatus.NEEDS_REVISION

        critique = None
        if not is_verified:
            critique = (
                f"Section verification failed: F_beta={scorecard.f_beta:.3f} (< 0.85). "
                f"Ungrounded claims: {scorecard.ungrounded_claims}, "
                f"Contradictions: {scorecard.contradicted_claims}."
            )

        updated_section = SectionDraft(
            section_id=section.section_id,
            title=section.title,
            content=section.content,
            order=section.order,
            citations=section.citations,
            claims=evaluated_claims,
            is_verified=is_verified,
            status=status,
            revision_count=section.revision_count,
            critique=critique,
        )

        return updated_section, scorecard


# Global module instance
default_citation_checker = CitationChecker()
