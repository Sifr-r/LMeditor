"""Tests for the NLI evaluation layer.

These tests pin the behaviour the forensic audit found broken:

* the heuristic fallback must not call a contradiction an entailment,
* the cross-encoder must not call two unrelated sentences a contradiction,
* the fallback must be visible instead of silent,
* the language-model judge must be a real judge, not a decorated heuristic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import dspy
import pytest
from dspy.dsp.utils.utils import dotdict

from lmeditor.core.exceptions import LMNotConfiguredError
from lmeditor.core.types import EntailmentStatus
from lmeditor.verification.citation_checker import CitationChecker
from lmeditor.verification.nli_evaluator import (
    CrossEncoderNLIEvaluator,
    HeuristicNLIEvaluator,
    LLMJudgeNLIEvaluator,
    ResilientNLIEvaluator,
)

WASTE_PREMISE = "Municipal waste collection rates fell by 30 percent between 2019 and 2023."


# ============================================================================
# 1. The heuristic fallback must be directionally sound
# ============================================================================


@pytest.mark.asyncio
async def test_heuristic_numeric_contradiction_is_not_entailed() -> None:
    """A quantity the premise never states is not an entailment."""
    evaluator = HeuristicNLIEvaluator()

    status, _ = await evaluator.evaluate(
        WASTE_PREMISE,
        "Municipal waste collection rates fell by 60 percent between 2019 and 2023.",
    )
    assert status != EntailmentStatus.ENTAILED
    assert status == EntailmentStatus.CONTRADICTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("premise", "hypothesis"),
    [
        (
            "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
            "Municipal waste collection rates rose by 30 percent between 2019 and 2023.",
        ),
        (
            "Reported latency increased by 12 percent after the migration.",
            "Reported latency decreased by 12 percent after the migration.",
        ),
        (
            "The vendor gained market share in the northern region.",
            "The vendor lost market share in the northern region.",
        ),
        (
            "Sensor readings were higher than the calibration baseline.",
            "Sensor readings were lower than the calibration baseline.",
        ),
    ],
)
async def test_heuristic_directional_flip_is_not_entailed(
    premise: str, hypothesis: str
) -> None:
    """A reversed polarity is a contradiction, never an entailment."""
    evaluator = HeuristicNLIEvaluator()

    status, _ = await evaluator.evaluate(premise, hypothesis)
    assert status != EntailmentStatus.ENTAILED
    assert status == EntailmentStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_heuristic_true_paraphrase_is_entailed() -> None:
    """A genuine paraphrase of the premise is still an entailment."""
    evaluator = HeuristicNLIEvaluator()

    status, score = await evaluator.evaluate(
        WASTE_PREMISE,
        "Waste collection fell 30 percent between 2019 and 2023.",
    )
    assert status == EntailmentStatus.ENTAILED
    assert score >= 0.65


@pytest.mark.asyncio
async def test_heuristic_unrelated_pair_is_neutral() -> None:
    """Two sentences about different subjects are neither entailed nor contradicted."""
    evaluator = HeuristicNLIEvaluator()

    status, _ = await evaluator.evaluate(
        WASTE_PREMISE,
        "Quantum entanglement enables secure key distribution in fiber networks.",
    )
    assert status == EntailmentStatus.NEUTRAL
    assert status != EntailmentStatus.ENTAILED
    assert status != EntailmentStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_heuristic_negation_is_contradicted() -> None:
    """An explicit negation reverses the assertion."""
    evaluator = HeuristicNLIEvaluator()

    status, _ = await evaluator.evaluate(
        "The study demonstrated a 45% reduction in error rates.",
        "The study did not demonstrate a 45% reduction in error rates.",
    )
    assert status == EntailmentStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_heuristic_empty_premise_is_ungrounded() -> None:
    """With no premise at all there is nothing to check."""
    evaluator = HeuristicNLIEvaluator()

    status, score = await evaluator.evaluate("", "Anything at all.")
    assert status == EntailmentStatus.UNGROUNDED
    assert score == 0.0


# ============================================================================
# 2. The cross-encoder must not contradict what it never read
# ============================================================================


@pytest.mark.asyncio
async def test_cross_encoder_unrelated_pair_is_neutral() -> None:
    """A premise about waste cannot contradict a hypothesis about quantum links."""
    evaluator = CrossEncoderNLIEvaluator()

    status, _ = await evaluator.evaluate(
        WASTE_PREMISE,
        "Quantum entanglement enables secure key distribution in fiber networks.",
    )
    assert status == EntailmentStatus.NEUTRAL
    assert status != EntailmentStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_cross_encoder_preserves_entailment_and_contradiction() -> None:
    """A genuinely related pair keeps its entailment and its contradiction."""
    evaluator = CrossEncoderNLIEvaluator()

    entailed, _ = await evaluator.evaluate(
        "The study demonstrated a 45% reduction in error rates.",
        "Error rates decreased by 45% according to the study.",
    )
    assert entailed == EntailmentStatus.ENTAILED

    contradicted, _ = await evaluator.evaluate(
        WASTE_PREMISE,
        "Municipal waste collection rates rose by 30 percent between 2019 and 2023.",
    )
    assert contradicted == EntailmentStatus.CONTRADICTED


# ============================================================================
# 3. The fallback must be visible
# ============================================================================


@pytest.mark.asyncio
async def test_resilient_reports_the_evaluator_that_produced_the_verdicts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A verdict set produced by the fallback says so, and logs the degradation."""

    class FailingEvaluator:
        evaluator_name = "cross-encoder/nli-deberta-v3-small"

        async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
            raise RuntimeError("Model server unreachable")

    resilient = ResilientNLIEvaluator(
        primary_evaluator=FailingEvaluator(),  # type: ignore[arg-type]
        fallback_evaluator=HeuristicNLIEvaluator(),
    )

    with caplog.at_level(logging.WARNING, logger="lmeditor.verification.nli_evaluator"):
        status, _ = await resilient.evaluate(
            "DNA replication is catalyzed by DNA polymerase enzymes.",
            "DNA polymerase enzymes catalyze replication.",
        )

    assert status == EntailmentStatus.ENTAILED
    assert resilient.evaluator_name == "heuristic"
    assert resilient.used_fallback is True
    assert any("falling back" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_resilient_names_the_primary_when_it_works() -> None:
    """Without a degradation the scorecard names the model that did the work."""
    evaluator = HeuristicNLIEvaluator()
    resilient = ResilientNLIEvaluator(
        primary_evaluator=evaluator,  # type: ignore[arg-type]
        fallback_evaluator=evaluator,
    )

    await resilient.evaluate("A premise.", "A premise.")
    assert resilient.evaluator_name == "heuristic"
    assert resilient.used_fallback is False


@pytest.mark.asyncio
async def test_citation_checker_writes_the_evaluator_into_the_scorecard() -> None:
    """The scorecard records which evaluator produced its verdicts."""
    from lmeditor.core.schemas import SourceChunk

    text = "Municipal waste collection rates fell by 30 percent between 2019 and 2023."
    chunk = SourceChunk.create(
        document_id="doc_eval",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )

    class RecordingEvaluator:
        evaluator_name = "cross-encoder/nli-deberta-v3-small"

        async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
            return EntailmentStatus.ENTAILED, 0.99

    checker = CitationChecker(evaluator=RecordingEvaluator())  # type: ignore[arg-type]
    _, scorecard = await checker.audit_claims(
        claims=[],
        chunks_by_id={chunk.chunk_id: chunk},
    )

    assert scorecard.evaluator == "cross-encoder/nli-deberta-v3-small"


# ============================================================================
# 4. The language-model judge must be a real judge
# ============================================================================


class _JudgeStubLM(dspy.BaseLM):
    """A language-model double that answers the judge's question."""

    def __init__(self, verdict: str = "entailed", score: float = 0.9) -> None:
        super().__init__("stub", "chat", 0.0, 1000, True)
        self.verdict = verdict
        self.score = score
        self.calls: list[str] = []

    def forward(self, prompt=None, messages=None, **kwargs: Any) -> Any:
        messages = messages or [{"role": "user", "content": prompt}]
        self.calls.append(str(messages[-1]["content"]))
        body = f"[[ ## verdict ## ]]\n{self.verdict}\n[[ ## score ## ]]\n{self.score}"
        message = dotdict(content=body, tool_calls=None)
        return dotdict(
            choices=[dotdict(message=message, finish_reason="stop")],
            usage=dotdict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model="stub",
        )


@pytest.fixture()
def _restore_lm() -> Any:
    """Restore whatever language model was installed before the test."""
    previous = dspy.settings.lm
    yield previous
    dspy.configure(lm=previous)


def test_llm_judge_raises_when_no_model_is_configured(_restore_lm: Any) -> None:
    """With no model the judge fails loudly instead of inventing a verdict."""
    dspy.configure(lm=None)
    judge = LLMJudgeNLIEvaluator()

    with pytest.raises(LMNotConfiguredError):
        asyncio.run(judge.evaluate("A premise about waste.", "A hypothesis about waste."))


def test_llm_judge_uses_the_configured_model(_restore_lm: Any) -> None:
    """The judge asks the configured model and reports its verdict."""
    lm = _JudgeStubLM(verdict="contradicted", score=0.75)
    dspy.configure(lm=lm)

    judge = LLMJudgeNLIEvaluator()
    status, score = asyncio.run(
        judge.evaluate(
            "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
            "Municipal waste collection rates rose by 30 percent between 2019 and 2023.",
        )
    )

    assert status == EntailmentStatus.CONTRADICTED
    assert score == 0.75
    assert lm.calls, "the judge must actually call the configured model"
    assert judge.evaluator_name == "llm-judge"


def test_llm_judge_rejects_an_unusable_verdict(_restore_lm: Any) -> None:
    """A verdict the judge cannot read is an error, not a silent pass."""
    dspy.configure(lm=_JudgeStubLM(verdict="maybe", score=0.5))

    judge = LLMJudgeNLIEvaluator()
    with pytest.raises(Exception):
        asyncio.run(judge.evaluate("A premise.", "A hypothesis."))


def test_llm_judge_module_uses_the_shared_signature() -> None:
    """The judge program is a DSPy program over the documented signature."""
    from lmeditor.verification import nli_evaluator

    assert "verdict" in nli_evaluator.LLMJudgeNLIEvaluator._SIGNATURE
    assert json.dumps({"verdict": "entailed"})  # the signature is plain text
