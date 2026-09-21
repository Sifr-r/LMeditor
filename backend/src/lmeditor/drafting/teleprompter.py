"""MIPROv2 and BootstrapFewShot teleprompter optimization harness for DSPy modules."""

from __future__ import annotations

import re
from typing import Any, Callable, Sequence

import dspy

from lmeditor.drafting.modules import SectionDrafter


def citation_precision_metric(
    gold: Any,
    pred: Any,
    trace: Any = None,
) -> float:
    """Mathematical optimization metric evaluating citation presence and grounding fidelity.

    Returns a score in [0.0, 1.0]:
    - 0.0: No citations or major hallucination
    - 0.5: Citations present, but partial overlap or ungrounded assertions
    - 1.0: All cited IDs are grounded in assigned passages with adequate expository depth
    """
    raw_draft = getattr(pred, "section_draft", "") or ""
    if not raw_draft.strip():
        return 0.0

    cited_ids = SectionDrafter.extract_citation_ids(raw_draft)
    if not cited_ids:
        return 0.0

    # Extract valid chunk IDs from gold example
    assigned_chunks_text = getattr(gold, "assigned_chunks_text", "")
    valid_ids = set(SectionDrafter.extract_citation_ids(assigned_chunks_text))

    if not valid_ids:
        # Fallback: check if chunk IDs were specified as a list
        gold_cids = getattr(gold, "assigned_chunk_ids", [])
        valid_ids = set(c.lower() for c in gold_cids)

    if not valid_ids:
        return 0.5  # Cannot strictly verify without gold passage IDs

    # Precision: fraction of cited IDs that belong to valid_ids
    valid_cited = [cid for cid in cited_ids if cid in valid_ids]
    precision = len(valid_cited) / len(cited_ids)

    # Penalize severely for any hallucinated ID
    if precision < 1.0:
        return precision * 0.5

    # Reward adequate length and density
    word_count = len(raw_draft.split())
    length_multiplier = min(1.0, max(0.5, word_count / 150.0))

    return round(precision * length_multiplier, 3)


class DraftingTeleprompterCompiler:
    """Harness for algorithmically compiling and optimizing DSPy drafting modules."""

    def __init__(
        self,
        metric: Callable[[Any, Any, Any], float] | None = None,
        max_bootstrapped_demos: int = 3,
        max_labeled_demos: int = 2,
    ) -> None:
        self.metric = metric or citation_precision_metric
        self.max_bootstrapped_demos = max_bootstrapped_demos
        self.max_labeled_demos = max_labeled_demos

    def compile_fewshot(
        self,
        module: SectionDrafter,
        trainset: Sequence[dspy.Example],
    ) -> SectionDrafter:
        """Compile module using BootstrapFewShot teleprompter."""
        teleprompter = dspy.BootstrapFewShot(
            metric=self.metric,
            max_bootstrapped_demos=self.max_bootstrapped_demos,
            max_labeled_demos=self.max_labeled_demos,
        )
        compiled = teleprompter.compile(module, trainset=list(trainset))
        return compiled  # type: ignore[no-any-return]


__all__ = [
    "DraftingTeleprompterCompiler",
    "citation_precision_metric",
]
