"""Teleprompter optimization harness for the drafting modules.

The compiler uses the optimizer that the installed DSPy actually offers:
MIPROv2 -- which searches over instructions *and* demonstrations -- when the
installed version provides it, and BootstrapFewShot when it does not. Both
runs are scored with the same citation-grounding metric, so an optimisation
run is judged on whether the draft it produces is anchored in the passages
the section was given.

The metric is deliberately more than a citation count. A draft is only
grounded when every marker it carries names one of the passages it was
given, when it actually uses those passages, and when its factual sentences
carry a marker of their own.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Sequence

import dspy

from lmeditor.drafting.modules import SectionDrafter

#: Identifier of the MIPROv2 optimizer.
OPTIMIZER_MIPRO_V2 = "mipro_v2"

#: Identifier of the BootstrapFewShot optimizer.
OPTIMIZER_BOOTSTRAP_FEW_SHOT = "bootstrap_few_shot"

#: Identifier of the automatic choice between the two.
OPTIMIZER_AUTO = "auto"

#: Score ceiling for a draft whose grounding cannot be verified because the
#: training example names no passages. An unverifiable draft must never
#: outrank a verifiable one.
UNVERIFIABLE_SCORE_CEILING = 0.25

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentence_count(text: str) -> int:
    """Number of sentences in ``text`` (at least one for non-empty text)."""
    parts = [part for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    return max(1, len(parts))


def _citation_density(raw_draft: str) -> float:
    """Share of sentences that carry at least one citation marker.

    A draft in which every sentence is anchored is better grounded than one
    in which a single sentence carries all the markers.
    """
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(raw_draft.strip()) if part.strip()]
    if not sentences:
        return 0.0
    anchored = sum(1 for sentence in sentences if SectionDrafter.extract_citation_ids(sentence))
    return anchored / len(sentences)


def citation_precision_metric(
    gold: Any,
    pred: Any,
    trace: Any = None,
) -> float:
    """Score how well a predicted draft is grounded in its assigned passages.

    The score is a weighted combination of three measurable properties:

    * **precision** -- every marker in the draft names one of the passages
      the example was given. A single invented marker is penalised hard,
      because a phantom citation is a fabricated citation.
    * **coverage** -- the draft actually uses the passages it was given,
      rather than citing one of them once and ignoring the rest.
    * **density** -- the factual sentences of the draft carry a marker of
      their own.

    Returns a score in ``[0.0, 1.0]``:

    * ``0.0`` -- no draft, no citation, or a hallucinated marker.
    * ``< 0.25`` -- the example names no passages, so grounding could not be
      verified at all; only the observable properties are scored.
    * ``> 0.8`` -- every marker is grounded, the assigned passages are used,
      and the sentences are anchored.
    """
    raw_draft = getattr(pred, "section_draft", "") or ""
    if not raw_draft.strip():
        return 0.0

    cited_ids = SectionDrafter.extract_citation_ids(raw_draft)
    if not cited_ids:
        return 0.0

    # Extract valid chunk IDs from gold example
    assigned_chunks_text = getattr(gold, "assigned_chunks_text", "")
    valid_ids = {cid.lower() for cid in SectionDrafter.extract_citation_ids(assigned_chunks_text)}

    if not valid_ids:
        # Fallback: check if chunk IDs were specified as a list
        gold_cids = getattr(gold, "assigned_chunk_ids", []) or []
        valid_ids = {str(c).lower() for c in gold_cids}

    density = _citation_density(raw_draft)

    if not valid_ids:
        # The example does not say which passages are allowed, so the
        # grounding of this prediction cannot be checked. Score only what is
        # observable and cap it well below a verifiable prediction.
        return round(min(UNVERIFIABLE_SCORE_CEILING, UNVERIFIABLE_SCORE_CEILING * density), 3)

    # Precision: fraction of cited IDs that belong to valid_ids
    valid_cited = [cid for cid in cited_ids if cid.lower() in valid_ids]
    precision = len(valid_cited) / len(cited_ids)

    # Penalise severely for any hallucinated ID
    if precision < 1.0:
        return round(precision * 0.5, 3)

    # Coverage: how much of the assigned material the draft actually used.
    coverage = len({cid.lower() for cid in valid_cited}) / len(valid_ids)

    # Reward adequate length, but never punish a short section into oblivion.
    word_count = len(raw_draft.split())
    length_multiplier = min(1.0, max(0.5, word_count / 150.0))

    score = (0.5 * precision + 0.3 * coverage + 0.2 * density) * length_multiplier
    return round(min(1.0, score), 3)


class DraftingTeleprompterCompiler:
    """Harness for algorithmically compiling and optimizing DSPy drafting modules.

    Args:
        metric: The metric used to score a candidate draft. Defaults to
            :func:`citation_precision_metric`.
        max_bootstrapped_demos: Maximum number of demonstrations bootstrapped
            from the training set.
        max_labeled_demos: Maximum number of labelled demonstrations kept.
        optimizer: Which optimizer to use. ``"auto"`` (the default) picks
            MIPROv2 when the installed DSPy provides it and BootstrapFewShot
            otherwise.
        auto: The MIPROv2 run mode (``"light"``, ``"medium"`` or ``"heavy"``).
    """

    def __init__(
        self,
        metric: Callable[[Any, Any, Any], float] | None = None,
        max_bootstrapped_demos: int = 3,
        max_labeled_demos: int = 2,
        optimizer: str = OPTIMIZER_AUTO,
        auto: str = "light",
    ) -> None:
        self.metric = metric or citation_precision_metric
        self.max_bootstrapped_demos = max_bootstrapped_demos
        self.max_labeled_demos = max_labeled_demos
        self.optimizer = optimizer
        self.auto = auto

    @staticmethod
    def available_optimizers() -> list[str]:
        """The optimizers the installed DSPy actually provides."""
        available: list[str] = []
        if hasattr(dspy, "MIPROv2"):
            available.append(OPTIMIZER_MIPRO_V2)
        if hasattr(dspy, "BootstrapFewShot"):
            available.append(OPTIMIZER_BOOTSTRAP_FEW_SHOT)
        return available

    def select_optimizer(self) -> str:
        """The optimizer this compiler will use.

        An explicitly requested optimizer is honoured when the installed
        DSPy provides it and rejected loudly when it does not. The automatic
        choice prefers MIPROv2, because it searches over instructions as
        well as demonstrations.

        Raises:
            ValueError: If the requested optimizer is not available.
        """
        available = self.available_optimizers()
        if self.optimizer != OPTIMIZER_AUTO:
            if self.optimizer not in available:
                raise ValueError(
                    f"Optimizer '{self.optimizer}' is not provided by the installed DSPy. "
                    f"Available optimizers: {available}."
                )
            return self.optimizer
        if OPTIMIZER_MIPRO_V2 in available:
            return OPTIMIZER_MIPRO_V2
        if OPTIMIZER_BOOTSTRAP_FEW_SHOT in available:
            return OPTIMIZER_BOOTSTRAP_FEW_SHOT
        raise ValueError("The installed DSPy provides no supported teleprompter optimizer.")

    def compile(
        self,
        module: SectionDrafter,
        trainset: Sequence[dspy.Example],
        valset: Sequence[dspy.Example] | None = None,
    ) -> SectionDrafter:
        """Compile ``module`` with the selected optimizer.

        Args:
            module: The drafting module to optimize.
            trainset: The examples the optimizer may learn from.
            valset: Optional validation examples. MIPROv2 needs at least
                two training examples when no validation set is supplied.

        Returns:
            The compiled module.

        Raises:
            ValueError: If the optimizer is unavailable or the training set
                is too small for it.
        """
        optimizer = self.select_optimizer()
        if optimizer == OPTIMIZER_MIPRO_V2:
            return self.compile_mipro_v2(module, trainset, valset)
        return self.compile_fewshot(module, trainset)

    def compile_fewshot(
        self,
        module: SectionDrafter,
        trainset: Sequence[dspy.Example],
    ) -> SectionDrafter:
        """Compile module using the BootstrapFewShot teleprompter."""
        teleprompter = dspy.BootstrapFewShot(
            metric=self.metric,
            max_bootstrapped_demos=self.max_bootstrapped_demos,
            max_labeled_demos=self.max_labeled_demos,
        )
        compiled = teleprompter.compile(module, trainset=list(trainset))
        return compiled  # type: ignore[no-any-return]

    def compile_mipro_v2(
        self,
        module: SectionDrafter,
        trainset: Sequence[dspy.Example],
        valset: Sequence[dspy.Example] | None = None,
    ) -> SectionDrafter:
        """Compile module using the MIPROv2 teleprompter.

        MIPROv2 needs a language model of its own to propose instructions;
        with none configured it raises, which is the correct outcome.
        """
        teleprompter = dspy.MIPROv2(
            metric=self.metric,
            auto=self.auto,
            max_bootstrapped_demos=self.max_bootstrapped_demos,
            max_labeled_demos=self.max_labeled_demos,
        )
        compiled = teleprompter.compile(
            module,
            trainset=list(trainset),
            valset=list(valset) if valset is not None else None,
        )
        return compiled  # type: ignore[no-any-return]


__all__ = [
    "OPTIMIZER_AUTO",
    "OPTIMIZER_BOOTSTRAP_FEW_SHOT",
    "OPTIMIZER_MIPRO_V2",
    "UNVERIFIABLE_SCORE_CEILING",
    "DraftingTeleprompterCompiler",
    "citation_precision_metric",
]
