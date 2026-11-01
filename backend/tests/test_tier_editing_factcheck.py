"""The editing and fact-check doors are doors: they ask the Kotlin tier.

These tests prove two things and nothing else:

* the doors reach the seam -- the rules they used to hold are asked of the
  Kotlin tier, and the tier's answer is what comes back; and
* a seam that is not there is reported as such. ``BridgeUnavailableError``
  is an infrastructure failure and must never be turned into a domain error
  or swallowed into a Python answer.

The stand-in seam below answers from a table, so the tests do not depend on
which methods the tier jar on disk happens to serve.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.schemas import (
    FactCheckRequest,
    InlineEditRequest,
    SectionDraft,
)
from lmeditor.core.types import EntailmentStatus
from lmeditor.editing.editor import InlineEditor
from lmeditor.factcheck.service import FactChecker
from lmeditor.tier import editing as tier_editing
from lmeditor.tier import factcheck as tier_factcheck
from lmeditor.tier import text as tier_text
from lmeditor.verification.citation_checker import CitationChecker

#: The section both doors are asked about.
SECTION = SectionDraft(
    section_id="sec-1",
    title="Drafted",
    content="Mangrove forests slow storm surge.",
    order=0,
)

#: The claim the fact-check door is asked about, in the wire form.
CLAIM_WIRE = {
    "claim_id": "claim-1",
    "section_id": "sec-1",
    "text": "Mangrove forests slow storm surge.",
    "char_start": 0,
    "char_end": 33,
    "cited_chunk_ids": [],
    "sentence_idx": 0,
}

#: The report the tier answers a fact-check with, in the wire form.
REPORT_WIRE: dict[str, Any] = {
    "doc_id": "doc-1",
    "text": "Mangrove forests slow storm surge.",
    "verdict": "supported",
    "claims": [],
    "scorecard": None,
    "web_searched": False,
    "summary": "Verdict: SUPPORTED.",
}


class _StandInBridge:
    """A seam that answers from a table and records what it was asked."""

    def __init__(self, answers: Mapping[str, Any]) -> None:
        self._answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Answer one call, or refuse to."""
        self.calls.append((method, dict(params or {})))
        answer = self._answers.get(method)
        if answer is None:
            raise AssertionError(f"the seam was asked an unanswered question: {method}")
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)

    def close(self) -> None:
        """The stand-in has no process to close."""

    @property
    def methods(self) -> list[str]:
        """The methods the seam was asked for, in order."""
        return [method for method, _ in self.calls]


class _StubChecker:
    """A checker that judges nothing.

    The entailment model is the Python tier's, and it is not what these tests
    are about: the doors are.
    """

    beta = 0.5
    f_beta_threshold = 0.85
    evaluator = None

    async def audit_claims(
        self,
        claims: Any,
        chunks_by_id: Any,
        strict_hallucination_check: bool = True,
        section_id: str = "",
    ) -> tuple[list[Any], Any]:
        """Return the claims unchanged."""
        return list(claims), None


class _StubEvaluator:
    """An evaluator that always agrees."""

    evaluator_name = "stub"

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Return an entailed verdict."""
        return EntailmentStatus.ENTAILED, 0.9


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a stand-in for the seam and hand back the installer."""
    installed: list[_StandInBridge] = []

    def install(answers: Mapping[str, Any]) -> _StandInBridge:
        bridge = _StandInBridge(answers)
        installed.append(bridge)
        monkeypatch.setattr(tier_editing, "tier_bridge", lambda: bridge)
        monkeypatch.setattr(tier_factcheck, "tier_bridge", lambda: bridge)
        monkeypatch.setattr(tier_text, "tier_bridge", lambda: bridge)
        return bridge

    return install


@pytest.mark.asyncio
async def test_the_editing_door_asks_the_tier_for_the_plan_and_the_acceptance(
    seam: Any,
) -> None:
    """The editor's rules are asked of the tier, and its answer is the result."""
    bridge = seam(
        {
            "editing.plan_edit": {
                "instruction": "Make this tighter.",
                "original_text": "Mangrove forests slow storm surge.",
                "passages": [],
            },
            "editing.validate_revision": {
                "accepted": True,
                "reason": "The revision cites only sources the document has.",
                "cited_chunk_ids": [],
                "claims": [],
            },
            "editing.apply_edit": {
                "result": {
                    "doc_id": "doc-1",
                    "section_id": "sec-1",
                    "scope": "paragraph",
                    "original_text": "Mangrove forests slow storm surge.",
                    "revised_text": "Mangroves slow storm surge.",
                    "applied": True,
                    "reason": "The revision is supported by the sources it cites.",
                    "cited_chunk_ids": [],
                    "claims": [],
                    "scorecard": None,
                    "section": None,
                }
            },
        }
    )
    editor = InlineEditor(
        rewrite=lambda **_: "Mangroves slow storm surge.",
        checker=cast("CitationChecker", _StubChecker()),
    )

    result = await editor.edit(
        InlineEditRequest(
            doc_id="doc-1",
            section_id="sec-1",
            instruction="Make this tighter.",
        ),
        SECTION,
    )

    assert bridge.methods == [
        "editing.plan_edit",
        "editing.validate_revision",
        "editing.apply_edit",
    ]
    assert result.revised_text == "Mangroves slow storm surge."
    assert result.applied is True


@pytest.mark.asyncio
async def test_the_fact_check_door_asks_the_tier_to_aggregate_the_verdicts(seam: Any) -> None:
    """The report is the tier's answer, not this module's."""
    bridge = seam(
        {
            "text.claims.extract": {"claims": [CLAIM_WIRE]},
            "factcheck.validate_max_evidence": {"valid": True},
            "factcheck.evidence_limit": {"limit": 5},
            "factcheck.relevance": {"relevance": 1.0},
            "factcheck.aggregate": {"report": REPORT_WIRE, "scorecard": None},
        }
    )
    checker = FactChecker(evaluator=_StubEvaluator(), chunks=())

    report = await checker.check(
        FactCheckRequest(
            doc_id="doc-1",
            text="Mangrove forests slow storm surge.",
        )
    )

    assert "factcheck.aggregate" in bridge.methods
    assert report.verdict.value == "supported"
    assert report.summary == "Verdict: SUPPORTED."


@pytest.mark.asyncio
async def test_a_seam_that_is_not_there_is_not_swallowed_by_the_editing_door(
    seam: Any,
) -> None:
    """A tier that cannot be reached is an infrastructure failure, not an error."""
    seam({"editing.plan_edit": BridgeUnavailableError("the tier is not there")})
    editor = InlineEditor(
        rewrite=lambda **_: "Mangroves slow storm surge.",
        checker=cast("CitationChecker", _StubChecker()),
    )

    with pytest.raises(BridgeUnavailableError):
        await editor.edit(
            InlineEditRequest(
                doc_id="doc-1",
                section_id="sec-1",
                instruction="Make this tighter.",
            ),
            SECTION,
        )


@pytest.mark.asyncio
async def test_a_seam_that_is_not_there_is_not_swallowed_by_the_fact_check_door(
    seam: Any,
) -> None:
    """A tier that cannot be reached is an infrastructure failure, not an error."""
    seam(
        {
            "text.claims.extract": {"claims": [CLAIM_WIRE]},
            "factcheck.validate_max_evidence": {"valid": True},
            "factcheck.evidence_limit": {"limit": 5},
            "factcheck.relevance": {"relevance": 1.0},
            "factcheck.aggregate": BridgeUnavailableError("the tier is not there"),
        }
    )
    checker = FactChecker(evaluator=_StubEvaluator(), chunks=())

    with pytest.raises(BridgeUnavailableError):
        await checker.check(
            FactCheckRequest(
                doc_id="doc-1",
                text="Mangrove forests slow storm surge.",
            )
        )
