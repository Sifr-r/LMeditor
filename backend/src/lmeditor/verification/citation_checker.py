"""Citation precision, recall and groundedness, as the Kotlin tier implements them.

Every deterministic rule of the audit -- the premise a claim is judged against,
the strict hallucination check, the precision/recall/groundedness arithmetic,
the scorecard's counters, and the section's status and critique -- is a *rule*,
and the rules live in the Kotlin tier, in ``lmeditor.text``, exactly once.
This module is the **door**: it keeps the Python API the rest of the backend
calls and answers with the tier's answers.

What stays in Python is the entailment **model**. Judging a premise against a
claim is model-bound work, not a rule, so the audit is a conversation rather
than a single call:

1. the tier resolves the premises and applies the strict hallucination check;
2. Python runs the model against those premises, concurrently, through the
   process-wide admission gate;
3. the tier turns the verdicts back into the scorecard, the evaluated claims
   and the section's status.

There is deliberately no fallback to a Python implementation. A silent fallback
would hide exactly the drift the tier exists to remove: two implementations of
one rule, with only one of them in the request path.

The implementation this module used to carry is frozen in
``lmeditor._reference.citation_checker``, so the parity harness can still ask
both sides the same question.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from typing import Final, Iterator

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import (
    CitationHallucinationError,
    LMeditorError,
    ValidationError,
    VerificationError,
)
from lmeditor.core.schemas import Claim, SectionDraft, SourceChunk, VerificationScorecard
from lmeditor.core.types import EntailmentStatus, NLIEvaluatorProtocol
from lmeditor.tier import text as tier_text
from lmeditor.verification.claim_extractor import extract_claims
from lmeditor.verification.nli_evaluator import default_nli_evaluator

#: Single source of truth for the groundedness threshold that separates a VERIFIED
#: section from a NEEDS_REVISION one. Imported by ``workflow.nodes`` so the graph
#: gate and the checker can never disagree.
DEFAULT_F_BETA_THRESHOLD: float = 0.85

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

#: The id of the placeholder chunk a bare premise is carried by when it is
#: narrowed on its own. The tier addresses chunks by id, and a chunk id is a
#: SHA-256 hex digest.
_FOCUS_CHUNK_ID: Final[str] = "0" * 64

#: The text that stands in for a blank premise or a blank claim. It is one
#: character long, so the tier finds no content word in it -- which is what a
#: blank claim means -- and it is never blank itself, which is what both the
#: chunk and the claim schemas require.
_FOCUS_PLACEHOLDER: Final[str] = "x"


def _nli_gate() -> asyncio.Semaphore:
    """Return the running loop's admission gate for NLI model calls."""
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(MAX_CONCURRENT_NLI_EVALUATIONS)
        _GATES[loop] = gate
    return gate


@contextmanager
def _tier_call(what: str) -> Iterator[None]:
    """Run a call across the seam, naming the call if it fails.

    The tier's own typed errors are the contract and are re-raised untouched:
    a hallucinated citation is a domain verdict about the manuscript, and a
    missing tier is an infrastructure failure that must never be dressed up as
    one. Anything else is wrapped in a :class:`VerificationError` that says
    which call failed, because a bare exception from a subprocess is not a
    diagnosis.

    Args:
        what: A short description of the call, used in the error message.
    """
    try:
        yield
    except (BridgeUnavailableError, CitationHallucinationError):
        raise
    except LMeditorError:
        raise
    except Exception as exc:
        raise VerificationError(f"{what} failed: {exc}") from exc


def _require_count(value: object, field: str) -> int:
    """Return ``value`` as a count of claims.

    The tier counts claims in integers, so a float would be truncated on the
    other side of the seam. It is refused here rather than quietly rounded.

    Raises:
        ValidationError: If ``value`` is not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"Expected an integer for {field}, got {type(value).__name__}",
            field=field,
            value=value,
        )
    return value


def _require_number(value: object, field: str) -> float:
    """Return ``value`` as a real number.

    Raises:
        ValidationError: If ``value`` is not a number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"Expected a number for {field}, got {type(value).__name__}",
            field=field,
            value=value,
        )
    return float(value)


def _require_claims(claims: object) -> list[Claim]:
    """Return the claims to audit, as a list.

    Raises:
        ValidationError: If ``claims`` is not a sequence of claims.
    """
    if isinstance(claims, (str, bytes)) or not isinstance(claims, Sequence):
        raise ValidationError(
            f"Expected a sequence of claims, got {type(claims).__name__}",
            field="claims",
            value=claims,
        )
    audited = list(claims)
    for claim in audited:
        if not isinstance(claim, Claim):
            raise ValidationError(
                f"Expected a Claim, got {type(claim).__name__}",
                field="claims",
                value=claim,
            )
    return audited


def _require_chunks(chunks_by_id: object) -> list[SourceChunk]:
    """Return the source chunks the claims are checked against.

    The tier indexes the chunks it is given by their own ``chunk_id``, which
    is the id a claim's ``cited_chunk_ids`` holds, so the mapping is read for
    its values. The keys are not consulted: a mapping that indexes a chunk
    under another name still hands the tier the chunk itself.

    Raises:
        ValidationError: If the mapping is not chunk id -> source chunk.
    """
    if not isinstance(chunks_by_id, Mapping):
        raise ValidationError(
            "Expected a mapping of chunk id to SourceChunk, got "
            f"{type(chunks_by_id).__name__}",
            field="chunks_by_id",
            value=chunks_by_id,
        )
    chunks: list[SourceChunk] = []
    for chunk_id, chunk in chunks_by_id.items():
        if not isinstance(chunk, SourceChunk):
            raise ValidationError(
                f"Expected a SourceChunk for {chunk_id!r}, got {type(chunk).__name__}",
                field="chunks_by_id",
                value=chunk,
            )
        chunks.append(chunk)
    return chunks


def compute_citation_precision(supported_cited_claims: int, total_cited_claims: int, total_claims: int) -> float:
    """Calculate Citation Precision (P_cite).

    P_cite = (supported cited claims) / (total cited claims)
    Edge cases:
      - If total cited claims == 0: return 1.0 if total claims == 0 else 0.0

    The arithmetic is the Kotlin tier's, so a caller that holds counts but no
    claims gets the very number the audit would have produced for them.

    Raises:
        ValidationError: If a count is not an integer.
    """
    supported = _require_count(supported_cited_claims, "supported_cited_claims")
    total_cited = _require_count(total_cited_claims, "total_cited_claims")
    total = _require_count(total_claims, "total_claims")

    with _tier_call("The citation precision call to the Kotlin tier"):
        return tier_text.citation_precision(supported, total_cited, total)


def compute_citation_recall(supported_claims: int, total_claims: int) -> float:
    """Calculate Citation Recall (R_cite).

    R_cite = (supported claims) / (total claims)
    Edge cases:
      - If total claims == 0: return 1.0

    The arithmetic is the Kotlin tier's, so a caller that holds counts but no
    claims gets the very number the audit would have produced for them.

    Raises:
        ValidationError: If a count is not an integer.
    """
    supported = _require_count(supported_claims, "supported_claims")
    total = _require_count(total_claims, "total_claims")

    with _tier_call("The citation recall call to the Kotlin tier"):
        return tier_text.citation_recall(supported, total)


def compute_groundedness_f_beta(precision: float, recall: float, beta: float = 0.5) -> float:
    """Calculate Groundedness (F_beta) harmonic mean weighting precision vs recall.

    F_beta = (1 + beta^2) * (P * R) / (beta^2 * P + R)
    For beta = 0.5:
      F_0.5 = 1.25 * (P * R) / (0.25 * P + R)
    Edge case:
      - If P + R == 0, returns 0.0

    The arithmetic is the Kotlin tier's, so a caller that holds two numbers
    gets the very number the audit would have produced for them.

    Raises:
        ValidationError: If a value is not a number.
    """
    p = _require_number(precision, "precision")
    r = _require_number(recall, "recall")
    b = _require_number(beta, "beta")

    with _tier_call("The groundedness call to the Kotlin tier"):
        return tier_text.groundedness_f_beta(p, r, b)


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
    def _focus_premise(premise: str, claim_text: str) -> str:
        """Narrow a premise to the part of it that speaks to the claim.

        An entailment model is trained on pairs of *statements*. A chunk that
        holds three sentences, judged against a claim that restates one of
        them, is a different task from the one the model was trained for --
        and it answers it badly: a verbatim quote of one sentence came back
        ``NEUTRAL`` at 0.998 because the premise said much more than the
        claim needed.

        So the premise is narrowed to the sentence of the chunk that the claim
        actually speaks to. A claim that appears verbatim in the chunk gets
        that sentence; a paraphrase gets the sentence it overlaps most; a
        claim that speaks to nothing in the chunk keeps the whole chunk as
        its premise, so an unrelated claim is still judged on the full text.

        **The rule is the tier's.** This is the door to it, and it holds no
        rule of its own: the tier resolves the premise, and the tier's
        sentence is the one that comes back. The premise is carried to the
        tier by a placeholder chunk, because the tier addresses chunks by id
        and a chunk id is a digest.

        Args:
            premise: The passage the claim would be judged against.
            claim_text: The claim the passage is narrowed to.

        Returns:
            The passage narrowed to the part that speaks to the claim, or the
            passage unchanged when nothing in it speaks to the claim.
        """
        blank_premise = not premise.strip()
        chunk = SourceChunk(
            chunk_id=_FOCUS_CHUNK_ID,
            document_id="focus",
            text=_FOCUS_PLACEHOLDER if blank_premise else premise,
        )
        claim = Claim(
            claim_id="focus",
            section_id="focus",
            text=_FOCUS_PLACEHOLDER if not claim_text.strip() else claim_text,
            cited_chunk_ids=[chunk.chunk_id],
        )

        with _tier_call("The premise focus call to the Kotlin tier"):
            _cited, premises = tier_text.resolve_premises([claim], [chunk], False, "focus")

        focused = premises[0]
        if blank_premise and focused == _FOCUS_PLACEHOLDER:
            # The tier had nothing to narrow to and left the passage alone.
            return premise
        return focused

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

        This is the model-bound half of the audit, and it stays in Python.
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

    @staticmethod
    def _scored_claims(
        claims: Sequence[Claim],
        cited_flags: Sequence[bool],
        premise_texts: Sequence[str],
        verdicts: Mapping[int, tuple[EntailmentStatus, float]],
    ) -> list[Claim]:
        """Return the claims carrying the model's verdicts.

        A cited claim keeps its citations and carries the verdict the model
        gave it; an uncited claim is ungrounded, with no premise to judge it
        against. The reasoning and the counters are the tier's: it assembles
        the final claims, so the verdict is described once.
        """
        scored: list[Claim] = []
        for idx, claim in enumerate(claims):
            if cited_flags[idx]:
                status, score = verdicts[idx]
                premise_text = premise_texts[idx]
                scored.append(
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
                    )
                )
            else:
                scored.append(
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
                    )
                )
        return scored

    async def audit_claims(
        self,
        claims: Sequence[Claim],
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool = True,
        section_id: str = "",
    ) -> tuple[list[Claim], VerificationScorecard]:
        """Verify each claim against cited source chunks and compute verification metrics.

        The premises, the hallucination check and the metrics are the Kotlin
        tier's; the model calls in between are Python's. The NLI calls for
        every cited claim are issued concurrently, but the result is
        indistinguishable from the historical serial loop: the hallucination
        pre-pass and the final assembly both walk ``claims`` in order, so
        ``evaluated_claims`` and every scorecard counter are bit-identical to
        what evaluating one claim at a time produced.

        Args:
            claims: The claims to audit.
            chunks_by_id: The source chunks, keyed by chunk id.
            strict_hallucination_check: Whether a claim that cites a chunk
                that is not in the index is a hallucination. The tier applies
                the check, and the *first* offending claim in claim order
                raises :class:`CitationHallucinationError`.
            section_id: The section the claims belong to, used when a
                hallucinated citation is reported.

        Returns:
            The evaluated claims and the scorecard they produced.

        Raises:
            ValidationError: If the claims or the chunks are malformed.
            CitationHallucinationError: If a claim cites a chunk that is not in
                the index and the strict check is on.
            BridgeUnavailableError: If the Kotlin tier cannot be reached.
        """
        audited_claims = _require_claims(claims)
        chunks = _require_chunks(chunks_by_id)

        with _tier_call("The premise resolution call to the Kotlin tier"):
            cited_flags, premise_texts = tier_text.resolve_premises(
                audited_claims,
                chunks,
                strict_hallucination_check,
                section_id,
            )

        evaluated_status = await self._evaluate_cited(
            audited_claims, cited_flags, premise_texts
        )

        scored = self._scored_claims(
            audited_claims, cited_flags, premise_texts, evaluated_status
        )

        with _tier_call("The citation audit call to the Kotlin tier"):
            scorecard, evaluated_claims = tier_text.audit_citations(
                scored,
                chunks,
                beta=self.beta,
                evaluator_name=self._evaluator_name(),
                strict_hallucination_check=strict_hallucination_check,
                section_id=section_id,
            )

        return evaluated_claims, scorecard

    def _evaluator_name(self) -> str:
        """Identifier of the NLI evaluator that produced these verdicts.

        The resilient evaluator reports the evaluator that actually produced
        the verdicts, so a scorecard produced by the degraded heuristic is
        visibly different from one produced by the cross-encoder. The name
        travels to the tier with the scored claims, because the model is the
        Python side's: the tier did not produce the verdicts, it only counted
        them.
        """
        name = getattr(self.evaluator, "evaluator_name", "")
        return str(name) if isinstance(name, str) else ""

    async def audit_section(
        self,
        section: SectionDraft,
        chunks_by_id: Mapping[str, SourceChunk],
        strict_hallucination_check: bool = True,
    ) -> tuple[SectionDraft, VerificationScorecard]:
        """Audit all claims in a manuscript section and update section status.

        The section's verdict -- its metrics, its status and the critique the
        operator reads -- is the tier's. The claims it is made of are judged
        by the model in the Python tier, so they travel out carrying their
        verdicts and come back measured.

        Args:
            section: The section to audit. A section that carries no claims
                has them extracted from its content first.
            chunks_by_id: The source chunks, keyed by chunk id.
            strict_hallucination_check: Whether a claim that cites a chunk
                that is not in the index is a hallucination.

        Returns:
            The audited section and the scorecard it was measured with.

        Raises:
            ValidationError: If the chunks are malformed.
            CitationHallucinationError: If a claim cites a chunk that is not in
                the index and the strict check is on.
            BridgeUnavailableError: If the Kotlin tier cannot be reached.
        """
        chunks = _require_chunks(chunks_by_id)

        claims = list(section.claims)
        if not claims:
            claims = _require_claims(
                extract_claims(section.content, section_id=section.section_id)
            )

        with _tier_call("The premise resolution call to the Kotlin tier"):
            cited_flags, premise_texts = tier_text.resolve_premises(
                claims,
                chunks,
                strict_hallucination_check,
                section.section_id,
            )

        evaluated_status = await self._evaluate_cited(claims, cited_flags, premise_texts)

        scored = self._scored_claims(claims, cited_flags, premise_texts, evaluated_status)

        with _tier_call("The section audit call to the Kotlin tier"):
            updated_section, scorecard = tier_text.audit_section(
                section.model_copy(update={"claims": scored}),
                chunks,
                beta=self.beta,
                threshold_f_beta=self.f_beta_threshold,
                evaluator_name=self._evaluator_name(),
                strict_hallucination_check=strict_hallucination_check,
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
