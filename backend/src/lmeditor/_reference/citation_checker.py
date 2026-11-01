"""The frozen witness of the citation audit.

This is the implementation that used to live in
``lmeditor.verification.citation_checker``, kept exactly as it was when the
live module became a door to the Kotlin tier. It is the yardstick the tier is
measured against: ``backend/scripts/verify_kotlin_parity.py`` asks this module
and the Kotlin tier the same questions and compares the answers byte for byte.

The rules frozen here are:

* the premise a claim is judged against, including the narrowing of a chunk to
  the sentence that speaks to the claim;
* the strict hallucination check, and the order it fires in;
* the citation precision, recall and groundedness arithmetic;
* the scorecard's counters, and the section's status and critique.

Two things make this a witness rather than a mirror:

* it is plain Python, and it never calls across the NDJSON seam -- so the
  comparison is between two independent implementations; and
* it is re-aligned to the tier's *current* semantics only when the tier's
  rule deliberately changes, and every such change is recorded in the
  harness's ``REFERENCE_WITNESSES`` -- so a divergence that is not a
  deliberate change is a finding, not a silent edit to the ruler.

Its one dependency on live code is the entailment *model*, which is model-bound
work and was never a rule: ``lmeditor.verification.nli_evaluator``. The claim
extractor it needs for a section that carries no claims is the frozen copy
beside it, ``lmeditor._reference.claim_extractor``.
"""

from __future__ import annotations

import asyncio
import re
import weakref
from typing import Mapping, Sequence
from lmeditor._reference.claim_extractor import extract_claims
from lmeditor.core.exceptions import CitationHallucinationError
from lmeditor.core.schemas import Claim, SectionDraft, SourceChunk, VerificationScorecard
from lmeditor.core.types import EntailmentStatus, NLIEvaluatorProtocol, SectionStatus
from lmeditor.verification.nli_evaluator import default_nli_evaluator

#: Single source of truth for the groundedness threshold that separates a VERIFIED
#: section from a NEEDS_REVISION one. Imported by ``workflow.nodes`` so the graph
#: gate and the checker can never disagree.
DEFAULT_F_BETA_THRESHOLD: float = 0.85

#: Sentence boundary used when a premise is narrowed to the part that speaks
#: to a claim.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

#: Word pattern used when measuring how much of a claim a sentence covers.
_WORD_RE = re.compile(r"\w+")

#: How much of a claim a sentence must cover before that sentence is used as
#: the premise. Below this the claim is not about that sentence, and the whole
#: chunk stays the premise.
_PREMISE_FOCUS_THRESHOLD: float = 0.5

#: Hard ceiling on the number of NLI model calls that may be in flight at once
#: across the whole process. Every ``evaluate`` performed by
#: :meth:`CitationChecker.audit_claims` is admitted through one semaphore, so a
#: 200-claim document with 50 sections costs 8 concurrent inferences rather than
#: an unbounded fan-out. Also consumed by ``workflow.nodes`` to cap how many
#: sections are audited at once.
MAX_CONCURRENT_NLI_EVALUATIONS: int = 8

# ``asyncio.Semaphore`` binds itself to the first event loop that awaits it and
# raises ``RuntimeError`` if a later loop tries to use the same instance. A
# single module-level semaphore would therefore break as soon as a second
# ``asyncio.run`` appeared in the process -- which happens on every LangGraph
# node dispatch (each runs on its own loop) and in every test. Keying the
# semaphores by the running loop keeps one global cap per loop and lets the
# entry disappear with the loop it belonged to.
_GATES: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


def _nli_gate() -> asyncio.Semaphore:
    """Return the running loop's admission gate for NLI model calls."""
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(MAX_CONCURRENT_NLI_EVALUATIONS)
        _GATES[loop] = gate
    return gate


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

    def __init__(
        self,
        evaluator: NLIEvaluatorProtocol | None = None,
        beta: float = 0.5,
        f_beta_threshold: float = DEFAULT_F_BETA_THRESHOLD,
    ) -> None:
        self.evaluator: NLIEvaluatorProtocol = evaluator or default_nli_evaluator
        self.beta: float = beta
        self.f_beta_threshold: float = f_beta_threshold

    @staticmethod
    def _resolve_premises(
        claims: Sequence[Claim],
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool,
        section_id: str,
    ) -> tuple[list[bool], list[str]]:
        """Validate chunk references and assemble one premise string per claim.

        Runs as a strictly sequential pre-pass so the strict check keeps its
        original semantics: the *first* offending claim in claim order raises
        ``CitationHallucinationError``, exactly as it did when the check was
        interleaved with the evaluation loop. Doing it up front also avoids
        spending model calls on a section that is going to be rejected anyway.
        """
        cited_flags: list[bool] = []
        premise_texts: list[str] = []

        for claim in claims:
            if not claim.cited_chunk_ids:
                cited_flags.append(False)
                premise_texts.append("")
                continue

            for cid in claim.cited_chunk_ids:
                if cid not in chunks_by_id and strict_hallucination_check:
                    raise CitationHallucinationError(
                        chunk_id=cid,
                        section_id=section_id or claim.section_id,
                        quote=claim.text,
                        reason=f"Cited chunk '{cid}' is missing from source index.",
                    )

            cited_flags.append(True)
            premises = [
                CitationChecker._focus_premise(chunks_by_id[cid].text, claim.text)
                for cid in claim.cited_chunk_ids
                if cid in chunks_by_id
            ]
            premise_texts.append(" ".join(p for p in premises if p))

        return cited_flags, premise_texts

    @staticmethod
    def _focus_premise(premise: str, claim_text: str) -> str:
        """Narrow a premise to the part of it that speaks to the claim.

        An entailment model is trained on pairs of *statements*. A chunk that
        holds three sentences, judged against a claim that restates one of
        them, is a different task from the one the model was trained for -- and
        it answers it badly: a verbatim quote of one sentence came back
        ``NEUTRAL`` at 0.998 because the premise said much more than the
        claim needed.

        So the premise is narrowed to the sentence of the chunk that the claim
        actually speaks to. A claim that appears verbatim in the chunk gets
        that sentence; a paraphrase gets the sentence it overlaps most; a
        claim that speaks to nothing in the chunk keeps the whole chunk as
        its premise, so an unrelated claim is still judged on the full text.
        """
        if not premise.strip() or not claim_text.strip():
            return premise

        sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(premise) if s.strip()]
        if len(sentences) <= 1:
            return premise

        claim_words = {w for w in _WORD_RE.findall(claim_text.lower()) if len(w) > 3}
        if not claim_words:
            return premise

        best_sentence = ""
        best_score = 0.0
        for sentence in sentences:
            sentence_words = {w for w in _WORD_RE.findall(sentence.lower()) if len(w) > 3}
            if not sentence_words:
                continue
            score = len(claim_words & sentence_words) / len(claim_words)
            if score > best_score:
                best_score = score
                best_sentence = sentence

        if best_sentence and best_score >= _PREMISE_FOCUS_THRESHOLD:
            return best_sentence
        return premise

    async def _evaluate_cited(
        self,
        claims: Sequence[Claim],
        cited_flags: Sequence[bool],
        premise_texts: Sequence[str],
    ) -> dict[int, tuple[EntailmentStatus, float]]:
        """Run one NLI call per cited claim, concurrently but order-stably.

        Each call is admitted through the process-wide gate, so the number of
        in-flight inferences is capped no matter how many claims a document
        holds. Results are returned in a dict keyed by claim index, which is
        what makes completion order irrelevant to the caller.
        """
        indices = [idx for idx, cited in enumerate(cited_flags) if cited]
        outcomes: dict[int, tuple[EntailmentStatus, float]] = {}
        if not indices:
            return outcomes

        gate = _nli_gate()

        async def _evaluate(index: int) -> tuple[int, tuple[EntailmentStatus, float]]:
            async with gate:
                verdict = await self.evaluator.evaluate(premise_texts[index], claims[index].text)
            return index, verdict

        for index, verdict in await asyncio.gather(*(_evaluate(i) for i in indices)):
            outcomes[index] = verdict
        return outcomes

    async def audit_claims(
        self,
        claims: Sequence[Claim],
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool = True,
        section_id: str = "",
    ) -> tuple[list[Claim], VerificationScorecard]:
        """Verify each claim against cited source chunks and compute verification metrics.

        The NLI calls for every cited claim are issued concurrently, but the
        result is indistinguishable from the historical serial loop: the
        hallucination pre-pass and the final assembly both walk ``claims`` in
        order, so ``evaluated_claims`` and every scorecard counter are
        bit-identical to what evaluating one claim at a time produced.
        """
        cited_flags, premise_texts = self._resolve_premises(
            claims=claims,
            chunks_by_id=chunks_by_id,
            strict_hallucination_check=strict_hallucination_check,
            section_id=section_id,
        )

        evaluated_status = await self._evaluate_cited(claims, cited_flags, premise_texts)

        evaluated_claims: list[Claim] = []
        supported_cited = 0
        total_cited = 0
        supported_total = 0
        ungrounded_total = 0
        contradicted_total = 0
        neutral_total = 0

        for idx, claim in enumerate(claims):
            # Check for hallucinated chunk citations
            if cited_flags[idx]:
                total_cited += 1
                premise_text = premise_texts[idx]
                status, score = evaluated_status[idx]

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
            evaluator=self._evaluator_name(),
            details=evaluated_claims,
        )

        return evaluated_claims, scorecard

    def _evaluator_name(self) -> str:
        """Identifier of the NLI evaluator that produced these verdicts.

        The resilient evaluator reports the evaluator that actually produced
        the verdicts, so a scorecard produced by the degraded heuristic is
        visibly different from one produced by the cross-encoder.
        """
        name = getattr(self.evaluator, "evaluator_name", "")
        return str(name) if isinstance(name, str) else ""

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

        # A section that was never written cannot be verified.
        #
        # The metrics are defined to be perfect when there is nothing to check:
        # zero claims gives precision 1.0, recall 1.0 and F_beta 1.0. That is
        # the right convention for a section that *was* written but contains no
        # checkable proposition -- but it is wrong for a section whose drafting
        # failed, because the section is empty precisely because there is
        # nothing there. Left alone, an unwritten section was marked VERIFIED,
        # which let a run in which no section could be drafted route straight
        # to macro cohesion and report itself as a finished, fully grounded
        # document.
        has_content = bool(section.content.strip())
        if not has_content:
            scorecard = scorecard.model_copy(
                update={
                    "precision": 0.0,
                    "recall": 0.0,
                    "f_beta": 0.0,
                    # One unsupported unit, so the counts still add up.
                    "total_claims": max(scorecard.total_claims, 1),
                    "ungrounded_claims": max(scorecard.ungrounded_claims, 1),
                }
            )

        # Determine section status. All four conditions must hold: NEUTRAL claims
        # are unverified assertions and must not be waved through by a high
        # F_beta, because F_beta is computed from precision/recall and does not
        # count neutral claims as a failure.
        is_verified = (
            has_content
            and scorecard.f_beta >= self.f_beta_threshold
            and scorecard.contradicted_claims == 0
            and scorecard.ungrounded_claims == 0
            and scorecard.neutral_claims == 0
        )
        status = SectionStatus.VERIFIED if is_verified else SectionStatus.NEEDS_REVISION

        critique = None
        if not is_verified:
            if not has_content:
                critique = (
                    "This section has no content, so there is nothing to verify. "
                    "It was most likely never drafted; the drafting failure is "
                    "recorded in the run's errors."
                )
            else:
                critique = (
                    f"Section verification failed: F_beta={scorecard.f_beta:.3f} "
                    f"(< {self.f_beta_threshold:.2f} required), "
                    f"Ungrounded claims: {scorecard.ungrounded_claims}, "
                    f"Contradictions: {scorecard.contradicted_claims}, "
                    f"Neutral claims: {scorecard.neutral_claims}."
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


__all__ = [
    "DEFAULT_F_BETA_THRESHOLD",
    "MAX_CONCURRENT_NLI_EVALUATIONS",
    "CitationChecker",
    "compute_citation_precision",
    "compute_citation_recall",
    "compute_groundedness_f_beta",
    "default_citation_checker",
]

# Global module instance
default_citation_checker = CitationChecker()
