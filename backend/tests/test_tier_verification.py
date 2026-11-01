"""The citation audit is a door: the rules come from the Kotlin tier.

``lmeditor.verification.citation_checker`` keeps the Python API the backend
calls, but the arithmetic, the premise rule and the section verdict are the
Kotlin tier's. These tests prove the door actually crosses the NDJSON seam --
that the tier's arithmetic is what comes back, that the tier's audit produced
the scorecard, that the tier is the one that applies the strict hallucination
check, and that an unreachable tier fails loudly instead of quietly falling
back to a Python implementation.
"""

from __future__ import annotations

from typing import Any

import pytest

from lmeditor._reference import citation_checker as reference_checker
from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.schemas import Claim, SectionDraft, SourceChunk
from lmeditor.core.types import EntailmentStatus, SectionStatus
from lmeditor.tier import text as tier_text
from lmeditor.verification import citation_checker as checker_module
from lmeditor.verification.citation_checker import (
    CitationChecker,
    compute_citation_precision,
    compute_citation_recall,
    compute_groundedness_f_beta,
)

#: ``SourceChunk.chunk_id`` is constrained to a SHA-256 hex digest.
_CHUNK_ID = "b" * 64


def _chunk() -> SourceChunk:
    """A source chunk the claims can cite."""
    text = "Grounded writing requires verifiable citations."
    return SourceChunk(
        chunk_id=_CHUNK_ID,
        document_id="doc_tier",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


class _StubEvaluator:
    """An evaluator with a name, so the scorecard has something to record.

    The verdict is keyed off the claim's own text, so a claim's result never
    depends on how many calls happened before it.
    """

    evaluator_name = "stub/nli"

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        if "neutral" in hypothesis:
            return EntailmentStatus.NEUTRAL, 0.4
        if "contradicted" in hypothesis:
            return EntailmentStatus.CONTRADICTED, 0.8
        return EntailmentStatus.ENTAILED, 0.9


def _claims() -> list[Claim]:
    """Three claims: two cited (one entailed, one neutral), one uncited."""
    return [
        Claim(
            claim_id="c0",
            section_id="sec_tier",
            text="A supported assertion.",
            cited_chunk_ids=[_CHUNK_ID],
        ),
        Claim(
            claim_id="c1",
            section_id="sec_tier",
            text="A neutral assertion.",
            cited_chunk_ids=[_CHUNK_ID],
        ),
        Claim(claim_id="c2", section_id="sec_tier", text="An uncited assertion."),
    ]


# ---------------------------------------------------------------------------
# The arithmetic is the tier's
# ---------------------------------------------------------------------------


def test_the_arithmetic_is_the_tier_s() -> None:
    """The three pure functions answer with the tier's numbers.

    They are also checked against the frozen witness -- the original pure
    Python implementation -- because the witness and the tier are what the
    parity harness holds against each other, and the door has to land on the
    same number as both.
    """
    assert compute_citation_precision(8, 10, 12) == tier_text.citation_precision(8, 10, 12)
    assert compute_citation_recall(7, 12) == tier_text.citation_recall(7, 12)
    assert compute_groundedness_f_beta(0.8, 0.58, 0.5) == tier_text.groundedness_f_beta(
        0.8, 0.58, 0.5
    )

    assert compute_citation_precision(8, 10, 12) == reference_checker.compute_citation_precision(
        8, 10, 12
    )
    assert compute_citation_recall(7, 12) == reference_checker.compute_citation_recall(7, 12)
    assert compute_groundedness_f_beta(
        0.8, 0.58, 0.5
    ) == reference_checker.compute_groundedness_f_beta(0.8, 0.58, 0.5)


def test_the_arithmetic_keeps_the_edge_cases() -> None:
    """The empty-collection conventions survive the swap."""
    assert compute_citation_precision(0, 0, 0) == 1.0
    assert compute_citation_precision(0, 0, 3) == 0.0
    assert compute_citation_recall(0, 0) == 1.0
    assert compute_groundedness_f_beta(0.0, 0.5) == 0.0
    assert compute_groundedness_f_beta(0.5, 0.0) == 0.0


# ---------------------------------------------------------------------------
# The audit is the tier's
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_reaches_the_tier(monkeypatch: Any) -> None:
    """The premises and the scorecard both come from the tier.

    The strict hallucination check is the tier's, so it has to travel to the
    tier with the claims -- a door that duplicated the check in Python would
    have two rules to drift apart.
    """
    seen: list[tuple[str, bool]] = []
    resolve_premises = tier_text.resolve_premises
    audit_citations = tier_text.audit_citations

    def spy_resolve(
        claims: list[Claim],
        chunks: list[SourceChunk],
        strict_hallucination_check: bool = False,
        section_id: str = "",
    ) -> tuple[list[bool], list[str]]:
        seen.append(("resolve_premises", strict_hallucination_check))
        return resolve_premises(claims, chunks, strict_hallucination_check, section_id)

    def spy_audit(
        claims: list[Claim],
        chunks: list[SourceChunk],
        beta: float = 0.5,
        evaluator_name: str = "",
        strict_hallucination_check: bool = False,
        section_id: str = "",
    ) -> Any:
        seen.append(("audit_citations", strict_hallucination_check))
        return audit_citations(
            claims,
            chunks,
            beta=beta,
            evaluator_name=evaluator_name,
            strict_hallucination_check=strict_hallucination_check,
            section_id=section_id,
        )

    monkeypatch.setattr(tier_text, "resolve_premises", spy_resolve)
    monkeypatch.setattr(tier_text, "audit_citations", spy_audit)

    chunk = _chunk()
    evaluated, scorecard = await CitationChecker(evaluator=_StubEvaluator()).audit_claims(
        claims=_claims(),
        chunks_by_id={chunk.chunk_id: chunk},
        section_id="sec_tier",
    )

    assert [name for name, _strict in seen] == ["resolve_premises", "audit_citations"]
    assert all(strict for _name, strict in seen)

    assert scorecard.total_claims == 3
    assert scorecard.supported_claims == 1
    assert scorecard.neutral_claims == 1
    assert scorecard.ungrounded_claims == 1

    # The tier's arithmetic, on the counts the audit actually produced.
    assert scorecard.precision == tier_text.citation_precision(1, 2, 3)
    assert scorecard.recall == tier_text.citation_recall(1, 3)
    assert scorecard.f_beta == tier_text.groundedness_f_beta(
        scorecard.precision, scorecard.recall, 0.5
    )

    # The scorecard names the model that produced the verdicts, which is the
    # Python side's evaluator -- the tier only counted them.
    assert scorecard.evaluator == "stub/nli"

    assert [claim.claim_id for claim in evaluated] == ["c0", "c1", "c2"]
    assert evaluated[0].entailment_status is EntailmentStatus.ENTAILED
    assert evaluated[1].entailment_status is EntailmentStatus.NEUTRAL
    assert evaluated[2].entailment_status is EntailmentStatus.UNGROUNDED
    assert evaluated[2].premise_text is None


@pytest.mark.asyncio
async def test_the_section_verdict_is_the_tier_s(monkeypatch: Any) -> None:
    """A section's status and critique are decided by the tier."""
    seen: list[str] = []
    audit_section = tier_text.audit_section

    def spy_audit_section(*args: Any, **kwargs: Any) -> Any:
        seen.append("audit_section")
        return audit_section(*args, **kwargs)

    monkeypatch.setattr(tier_text, "audit_section", spy_audit_section)

    chunk = _chunk()
    section = SectionDraft(
        section_id="sec_tier",
        title="Tier",
        content="A supported assertion.",
        claims=[
            Claim(
                claim_id="c0",
                section_id="sec_tier",
                text="A supported assertion.",
                cited_chunk_ids=[_CHUNK_ID],
            )
        ],
    )

    updated, scorecard = await CitationChecker(evaluator=_StubEvaluator()).audit_section(
        section, chunks_by_id={chunk.chunk_id: chunk}
    )

    assert seen == ["audit_section"]
    assert scorecard.evaluator == "stub/nli"
    assert scorecard.f_beta == 1.0
    assert updated.is_verified is True
    assert updated.status is SectionStatus.VERIFIED
    assert updated.critique is None


def test_the_premise_rules_are_the_tier_s() -> None:
    """The live module holds no Python rule; the frozen witness still does.

    The premise rules -- which chunk a claim is judged against, and how that
    chunk is narrowed -- live in the Kotlin tier now. The live module must
    not keep a Python implementation of them alongside it, and the frozen
    witness must still be the original's, because the parity harness measures
    the tier against it.
    """
    assert not hasattr(checker_module.CitationChecker, "_resolve_premises")

    premise = (
        "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
        "Composting rose over the same period."
    )
    claim_text = "Municipal waste collection rates fell by 30 percent between 2019 and 2023"

    focused = checker_module.CitationChecker._focus_premise(premise, claim_text)

    assert focused != premise, "the premise must be narrowed to the sentence the claim speaks to"
    assert "Composting" not in focused
    assert focused == reference_checker.CitationChecker._focus_premise(premise, claim_text)


# ---------------------------------------------------------------------------
# A missing tier is an infrastructure failure, and says so
# ---------------------------------------------------------------------------


def _unavailable(*args: Any, **kwargs: Any) -> Any:
    """Stand in for a tier that is not there."""
    raise BridgeUnavailableError("the bridge process exited before answering")


@pytest.mark.asyncio
async def test_the_audit_does_not_swallow_a_missing_tier(monkeypatch: Any) -> None:
    """An unreachable tier propagates; it is never dressed up as a domain error."""
    monkeypatch.setattr(tier_text, "resolve_premises", _unavailable)

    chunk = _chunk()
    with pytest.raises(BridgeUnavailableError):
        await CitationChecker(evaluator=_StubEvaluator()).audit_claims(
            claims=_claims(),
            chunks_by_id={chunk.chunk_id: chunk},
        )


def test_the_arithmetic_does_not_swallow_a_missing_tier(monkeypatch: Any) -> None:
    """The same holds for the pure functions."""
    monkeypatch.setattr(tier_text, "citation_precision", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        compute_citation_precision(1, 2, 3)
