"""Premise-hypothesis NLI entailment scoring models with resilient fallbacks.

Four evaluators live here, each answering the same question -- *does the
premise entail, contradict, or merely sit beside the hypothesis?*:

``HeuristicNLIEvaluator``
    A dependency-free lexical fallback. It is deliberately *directionally
    sound*: a flipped polarity ("rates fell" vs "rates rose"), a negated
    premise ("demonstrated" vs "did not demonstrate") and a quantity that the
    premise does not contain ("30 percent" vs "60 percent") are all treated as
    contradictions rather than entailments.

``CrossEncoderNLIEvaluator``
    The primary evaluator (``cross-encoder/nli-deberta-v3-small``). The label
    order is read from the model's own ``id2label`` mapping rather than
    assumed, and a relevance gate downgrades a ``CONTRADICTED`` verdict to
    ``NEUTRAL`` when the premise and the hypothesis are not about the same
    subject at all.

``LLMJudgeNLIEvaluator``
    A judge that asks the *configured* language model. It never falls back to
    a heuristic on its own: with no model configured it raises
    :class:`~lmeditor.core.exceptions.LMNotConfiguredError`.

``ResilientNLIEvaluator``
    The composite that owns the cache, the fallback and the bookkeeping. It
    records which evaluator actually produced each verdict and exposes it as
    ``evaluator_name`` so a degraded verdict set is visible in the scorecard
    instead of being silently indistinguishable from a model-backed one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from typing import Any, Callable, Coroutine

from lmeditor.core.exceptions import NLIModelError
from lmeditor.core.types import EntailmentStatus, NLIEvaluatorProtocol
from lmeditor.llm import require_lm

logger = logging.getLogger(__name__)

#: Hard ceiling on the entailment cache. ``default_nli_evaluator`` is a
#: module-level singleton, so its cache is shared by every document and every
#: request for the life of the process; an unbounded dict there is a slow leak
#: that grows with total traffic rather than with the working set. 4096 entries
#: is far more than one document's working set (a 50-section manuscript has
#: ~600 claims) while keeping the resident size fixed.
MAX_CACHE_ENTRIES: int = 4096

#: Minimum share of content words the premise and the hypothesis must share
#: before the pair is considered to be about the same subject at all. Below
#: this, a cross-encoder ``CONTRADICTED`` verdict is downgraded to
#: ``NEUTRAL``: a hypothesis about quantum networking cannot be *contradicted*
#: by a premise about municipal waste collection.
MIN_TOPICAL_RELEVANCE: float = 0.2

#: Overlap ratio at or above which two texts are considered to be about the
#: same subject (used to decide whether a polarity / quantity conflict is a
#: contradiction or merely two unrelated sentences).
_TOPICAL_OVERLAP: float = 0.35

#: Function words excluded from the content-token sets.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "is", "are", "was", "were", "be", "been", "being", "that", "this", "these", "those",
        "it", "its", "and", "or", "as", "according",
    }
)

#: Negation markers. Single-word markers are matched as whole tokens; the
#: multi-word markers are matched as phrases so that "did not" is detected
#: even when "not" also appears as a content word elsewhere.
_NEGATION_WORDS: frozenset[str] = frozenset(
    {"not", "never", "no", "none", "neither", "nor", "cannot", "hardly", "scarcely", "without"}
)

_NEGATION_PHRASES: tuple[str, ...] = (
    r"\bdid\s+not\b",
    r"\bdoes\s+not\b",
    r"\bdo\s+not\b",
    r"\bwas\s+not\b",
    r"\bwere\s+not\b",
    r"\bis\s+not\b",
    r"\bare\s+not\b",
    r"\bhas\s+not\b",
    r"\bhave\s+not\b",
    r"\bhad\s+not\b",
    r"\bwill\s+not\b",
    r"\bwould\s+not\b",
    r"\bshould\s+not\b",
    r"\bcan\s+not\b",
    r"\bcould\s+not\b",
    r"\bfail(?:s|ed)?\s+to\b",
)

#: Direction markers. ``+1`` means "up / more / better", ``-1`` means
#: "down / less / worse". A premise that talks about "up" and a hypothesis
#: that talks about "down" are talking past each other at best, and at worst
#: assert the opposite.
_DIRECTION_POLARITY: dict[str, int] = {
    # upward / increasing
    "rose": 1, "roses": 1, "risen": 1, "rising": 1, "rise": 1,
    "increase": 1, "increases": 1, "increased": 1, "increasing": 1,
    "grew": 1, "grows": 1, "growing": 1, "growth": 1,
    "gain": 1, "gains": 1, "gained": 1, "gaining": 1,
    "up": 1, "upward": 1, "upwards": 1,
    "higher": 1, "more": 1, "greater": 1,
    "above": 1, "exceed": 1, "exceeds": 1, "exceeded": 1, "exceeding": 1,
    "ascend": 1, "ascended": 1, "ascending": 1, "ascent": 1,
    "expand": 1, "expanded": 1, "expanding": 1, "expansion": 1,
    "climb": 1, "climbed": 1, "climbing": 1,
    "improve": 1, "improved": 1, "improving": 1, "improvement": 1,
    "win": 1, "wins": 1, "won": 1, "winning": 1,
    "raise": 1, "raised": 1, "raising": 1,
    "elevate": 1, "elevated": 1,
    "boost": 1, "boosted": 1,
    "surge": 1, "surged": 1, "surging": 1,
    # downward / decreasing
    "fell": -1, "fall": -1, "falls": -1, "fallen": -1, "falling": -1,
    "decline": -1, "declines": -1, "declined": -1, "declining": -1,
    "decrease": -1, "decreases": -1, "decreased": -1, "decreasing": -1,
    "shrank": -1, "shrunk": -1, "shrink": -1, "shrinks": -1, "shrinking": -1,
    "loss": -1, "losses": -1, "lost": -1, "lose": -1, "loses": -1, "losing": -1,
    "down": -1, "downward": -1, "downwards": -1,
    "lower": -1, "less": -1, "fewer": -1,
    "below": -1,
    "descend": -1, "descended": -1, "descending": -1, "descent": -1,
    "contract": -1, "contracted": -1, "contracting": -1, "contraction": -1,
    "drop": -1, "drops": -1, "dropped": -1, "dropping": -1,
    "reduce": -1, "reduces": -1, "reduced": -1, "reducing": -1,
    "reduction": -1, "reductions": -1,
    "worsen": -1, "worsened": -1, "worsening": -1,
    "slump": -1, "slumped": -1, "slumping": -1,
    "plunge": -1, "plunged": -1, "plunging": -1,
    "sink": -1, "sank": -1, "sinking": -1,
    "diminish": -1, "diminished": -1, "diminishing": -1,
    "weaken": -1, "weakened": -1, "weakening": -1,
}

#: Word tokenizer that keeps contractions intact ("doesn't" stays one token)
#: so that negations written with an apostrophe are still detected.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['\u2019][a-z]+)*")

#: Number tokenizer. The unit is captured so "30 percent" and "30%" normalise
#: to the same quantity.
_NUMBER_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|percent|percentage|pct|per\s?cent)?",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[str]:
    """Lower-cased word tokens, contractions kept intact."""
    return _TOKEN_RE.findall(text.lower())


def _content_tokens(text: str) -> set[str]:
    """Tokens that carry content: no function words, no negation markers."""
    return {
        token
        for token in _tokens(text)
        if token not in _STOPWORDS and token not in _NEGATION_WORDS
    }


def _lexical_overlap(premise: str, hypothesis: str) -> float:
    """Share of content words the premise and the hypothesis have in common.

    The score is symmetric in spirit: it is the larger of "how much of the
    hypothesis is covered by the premise" and "how much of the premise is
    covered by the hypothesis", so a short hypothesis dropped into a long
    premise is not scored as unrelated.
    """
    p_content = _content_tokens(premise)
    h_content = _content_tokens(hypothesis)
    if not p_content or not h_content:
        return 0.0
    shared = p_content & h_content
    hypothesis_coverage = len(shared) / len(h_content)
    premise_coverage = len(shared) / len(p_content)
    return max(hypothesis_coverage, premise_coverage)


def _has_negation(text: str) -> bool:
    """Whether the text carries an explicit negation."""
    lowered = text.lower()
    if any(token.endswith("n't") for token in _tokens(lowered)):
        return True
    if any(token in _NEGATION_WORDS for token in _tokens(lowered)):
        return True
    return any(re.search(pattern, lowered) for pattern in _NEGATION_PHRASES)


def _extract_numbers(text: str) -> set[float]:
    """Every quantity asserted by the text, normalised to floats."""
    quantities: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        try:
            quantities.add(float(raw))
        except ValueError:  # pragma: no cover - the regex only yields digits
            continue
    return quantities


def _direction_polarities(text: str) -> set[int]:
    """The directional polarities the text asserts."""
    return {
        polarity
        for token in _tokens(text)
        if (polarity := _DIRECTION_POLARITY.get(token)) is not None
    }


class HeuristicNLIEvaluator:
    """Fast, deterministic lexical evaluator with negation and polarity awareness.

    The evaluator answers three questions before it answers the entailment
    question:

    1. **Is the polarity reversed?** A premise that says a quantity *fell* and
       a hypothesis that says it *rose* assert opposite worlds.
    2. **Is the negation reversed?** "demonstrated" and "did not demonstrate"
       are opposites.
    3. **Does the hypothesis assert a quantity the premise never mentions?**
       "30 percent" and "60 percent" are different claims, and the premise
       does not support the second one.

    Any of the three is a contradiction, never an entailment.
    """

    def __init__(self, entailment_threshold: float = 0.65) -> None:
        self.entailment_threshold = entailment_threshold

    @property
    def evaluator_name(self) -> str:
        """Identifier of this evaluator, recorded in the verification scorecard."""
        return "heuristic"

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate premise-hypothesis entailment via token overlap and polarity analysis."""
        if not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise NLIModelError("Both premise and hypothesis must be string instances.")

        p_clean = premise.strip().lower()
        h_clean = hypothesis.strip().lower()

        if not h_clean:
            raise NLIModelError("Hypothesis cannot be empty.")
        if not p_clean:
            return EntailmentStatus.UNGROUNDED, 0.0

        p_tokens = set(_tokens(p_clean))
        h_tokens = _tokens(h_clean)
        if not h_tokens:
            return EntailmentStatus.NEUTRAL, 0.0

        # Content word overlap (ignoring simple function words)
        content_h_tokens = [t for t in h_tokens if t not in _STOPWORDS and t not in _NEGATION_WORDS]
        if not content_h_tokens:
            content_h_tokens = h_tokens

        matched = sum(1 for t in content_h_tokens if t in p_tokens)
        overlap_ratio = matched / len(content_h_tokens)

        negation_clash = _has_negation(p_clean) != _has_negation(h_clean)
        numeric_mismatch = bool(_extract_numbers(h_clean) - _extract_numbers(p_clean))
        p_polarities = _direction_polarities(p_clean)
        h_polarities = _direction_polarities(h_clean)
        direction_flip = bool(p_polarities and h_polarities and p_polarities.isdisjoint(h_polarities))

        # 1. Reversed negation: same content, opposite assertion.
        if negation_clash and overlap_ratio >= 0.5:
            return EntailmentStatus.CONTRADICTED, round(max(0.1, 1.0 - overlap_ratio), 4)

        # 2. A quantity the premise does not contain.
        if numeric_mismatch:
            if overlap_ratio >= _TOPICAL_OVERLAP:
                return EntailmentStatus.CONTRADICTED, round(max(0.1, 1.0 - overlap_ratio), 4)
            return EntailmentStatus.NEUTRAL, round(overlap_ratio, 4)

        # 3. A reversed direction ("fell" vs "rose").
        if direction_flip:
            if overlap_ratio >= _TOPICAL_OVERLAP:
                return EntailmentStatus.CONTRADICTED, round(max(0.1, 1.0 - overlap_ratio), 4)
            return EntailmentStatus.NEUTRAL, round(overlap_ratio, 4)

        # 4. The hypothesis quotes the premise verbatim.
        if h_clean in p_clean:
            return EntailmentStatus.ENTAILED, 1.0

        # 5. Paraphrase: most of the content words are present.
        if overlap_ratio >= self.entailment_threshold:
            return EntailmentStatus.ENTAILED, round(min(1.0, overlap_ratio), 4)

        # 6. Related but not entailed, or unrelated: the premise neither
        #    entails nor contradicts the hypothesis.
        return EntailmentStatus.NEUTRAL, round(overlap_ratio, 4)


class CrossEncoderNLIEvaluator:
    """DeBERTa / Cross-Encoder premise-hypothesis inference model.

    The label order of the checkpoint is read from its own ``id2label``
    mapping instead of being assumed, so a checkpoint that orders its labels
    differently is handled correctly.

    A cross-encoder will happily report ``CONTRADICTED`` with high
    confidence for two sentences that have nothing to do with each other.
    A contradiction, though, requires the two sentences to be about the same
    subject, so a topical-relevance gate downgrades ``CONTRADICTED`` to
    ``NEUTRAL`` when the premise and the hypothesis share too little content.
    """

    def __init__(self, model_name: str = "cross-encoder/nli-deberta-v3-small") -> None:
        self.model_name = model_name
        self._model: Any = None
        self._is_available: bool | None = None
        self._label_indices: dict[str, int] | None = None

    @property
    def evaluator_name(self) -> str:
        """Identifier of this evaluator, recorded in the verification scorecard."""
        return self.model_name

    def _load_model(self) -> None:
        """Lazy load sentence_transformers CrossEncoder."""
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            self._is_available = True
        except Exception as exc:
            self._is_available = False
            raise NLIModelError(f"Failed to load CrossEncoder model '{self.model_name}': {exc}") from exc

    def _resolve_label_indices(self) -> dict[str, int]:
        """Map the checkpoint's label names onto their output indices.

        The mapping is read from the model's own configuration. The fallback
        order matches the published ``cross-encoder/nli-deberta-v3-small``
        layout and is only used when the configuration cannot be read.
        """
        if self._label_indices is not None:
            return self._label_indices

        raw_labels: Any = None
        try:
            config = getattr(self._model, "config", None)
            if config is not None:
                raw_labels = getattr(config, "id2label", None)
        except Exception:  # noqa: BLE001 - a broken config must not be fatal
            raw_labels = None

        indices: dict[str, int] = {}
        if isinstance(raw_labels, dict):
            for index, label in raw_labels.items():
                if isinstance(label, str) and isinstance(index, int):
                    indices[label.strip().lower()] = index

        if not {"contradiction", "entailment"} <= set(indices):
            logger.warning(
                "Could not read the label order of '%s' from its configuration; "
                "falling back to the documented contradiction/entailment/neutral order.",
                self.model_name,
            )
            indices = {"contradiction": 0, "entailment": 1, "neutral": 2}

        self._label_indices = indices
        return indices

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Run cross-encoder sequence classification on premise-hypothesis pair."""
        if not premise.strip() or not hypothesis.strip():
            raise NLIModelError("Premise and hypothesis must both be non-empty strings.")

        try:
            self._load_model()
            loop = asyncio.get_running_loop()
            scores = await loop.run_in_executor(
                None, lambda: self._model.predict([[premise, hypothesis]])[0]
            )

            # Convert logits via softmax
            import numpy as np

            exp_scores = np.exp(scores - np.max(scores))
            probs = exp_scores / exp_scores.sum()

            indices = self._resolve_label_indices()

            def _probability(label: str) -> float:
                index = indices.get(label)
                if index is None or index >= len(probs):
                    return 0.0
                return float(probs[index])

            contradiction_prob = _probability("contradiction")
            entailment_prob = _probability("entailment")
            neutral_prob = _probability("neutral")

            if entailment_prob > 0.6:
                return EntailmentStatus.ENTAILED, round(entailment_prob, 4)

            if contradiction_prob > 0.5:
                relevance = _lexical_overlap(premise, hypothesis)
                if relevance < MIN_TOPICAL_RELEVANCE:
                    # The pair is not about the same subject, so the model is
                    # not contradicting the premise -- it is ignoring it.
                    logger.debug(
                        "Downgrading CONTRADICTED to NEUTRAL for '%s': premise and hypothesis "
                        "share only %.2f of their content words.",
                        self.model_name,
                        relevance,
                    )
                    return EntailmentStatus.NEUTRAL, round(neutral_prob, 4)
                return EntailmentStatus.CONTRADICTED, round(contradiction_prob, 4)

            return EntailmentStatus.NEUTRAL, round(neutral_prob, 4)

        except Exception as exc:
            raise NLIModelError(f"CrossEncoder inference failed: {exc}") from exc


class LLMJudgeNLIEvaluator:
    """Entailment judge backed by the configured language model.

    The judge asks the model to classify the premise/hypothesis pair as
    ``entailed``, ``contradicted`` or ``neutral`` and to report its
    confidence. It never degrades to a heuristic verdict on its own: with no
    model configured it raises
    :class:`~lmeditor.core.exceptions.LMNotConfiguredError`, because a
    fabricated verdict is worse than an explicit failure.
    """

    #: Signature of the judge program. DSPy parses the string form into a
    #: typed signature, so the judge does not need a module-level class.
    _SIGNATURE = "premise: str, hypothesis: str -> verdict: str, score: float"

    #: The judging rubric handed to the model.
    _INSTRUCTIONS = (
        "Decide whether the premise entails, contradicts, or is merely neutral "
        "with respect to the hypothesis. Answer with one of 'entailed', "
        "'contradicted' or 'neutral' and a confidence between 0 and 1. "
        "A flipped direction or a different quantity is a contradiction; an "
        "unrelated hypothesis is neutral."
    )

    def __init__(
        self,
        judge_callable: Callable[[str, str], Coroutine[Any, Any, tuple[EntailmentStatus, float]]] | None = None,
    ) -> None:
        self._judge_callable = judge_callable
        self._program: Any = None

    @property
    def evaluator_name(self) -> str:
        """Identifier of this evaluator, recorded in the verification scorecard."""
        return "llm-judge"

    def _ensure_program(self) -> Any:
        """Build the judge program on first use."""
        if self._program is None:
            import dspy

            signature = dspy.Signature(self._SIGNATURE).with_instructions(self._INSTRUCTIONS)
            self._program = dspy.Predict(signature)
        return self._program

    @staticmethod
    def _parse_verdict(raw: Any) -> EntailmentStatus:
        """Map the judge's answer onto an :class:`EntailmentStatus`."""
        text = str(raw or "").strip().lower()
        token = text.replace("_", " ").replace("-", " ").split()
        for word in token:
            if word in {"entailed", "entailment", "entails"}:
                return EntailmentStatus.ENTAILED
            if word in {"contradicted", "contradiction", "contradicts", "contradict"}:
                return EntailmentStatus.CONTRADICTED
            if word in {"neutral", "unknown", "unrelated"}:
                return EntailmentStatus.NEUTRAL
        raise NLIModelError(f"LLM judge returned an unrecognised verdict: {raw!r}.")

    @staticmethod
    def _parse_score(raw: Any, status: EntailmentStatus) -> float:
        """Clamp the judge's confidence into ``[0, 1]``."""
        try:
            score = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "LLM judge did not return a usable confidence score (%r); "
                "using the canonical score for '%s'.",
                raw,
                status.value,
            )
            return {"entailed": 1.0, "contradicted": 0.0, "neutral": 0.5}.get(status.value, 0.0)
        return round(min(1.0, max(0.0, score)), 4)

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate premise-hypothesis with the configured language model."""
        if not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise NLIModelError("Both premise and hypothesis must be string instances.")
        if not premise.strip() or not hypothesis.strip():
            raise NLIModelError("Premise and hypothesis must both be non-empty strings.")

        if self._judge_callable is not None:
            try:
                status, score = await self._judge_callable(premise, hypothesis)
            except Exception as exc:
                raise NLIModelError(f"LLM judge invocation failed: {exc}") from exc
            return status, self._parse_score(score, status)

        # No injected judge: use the configured language model. ``require_lm``
        # raises LMNotConfiguredError when there is none, which is the honest
        # outcome -- this evaluator has no fallback of its own.
        require_lm("NLI entailment judging")

        program = self._ensure_program()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: program(premise=premise, hypothesis=hypothesis),
            )
        except Exception as exc:
            raise NLIModelError(f"LLM judge invocation failed: {exc}") from exc

        status = self._parse_verdict(getattr(result, "verdict", None))
        score = self._parse_score(getattr(result, "score", None), status)
        return status, score


class ResilientNLIEvaluator:
    """Composite NLI evaluator with multi-tier fallback, concurrency, and caching.

    The composite is also the bookkeeping layer: it remembers which evaluator
    produced each verdict, logs every fallback, and exposes the result as
    ``evaluator_name`` so that a scorecard produced by the degraded heuristic
    is visibly different from one produced by the cross-encoder.
    """

    def __init__(
        self,
        primary_evaluator: NLIEvaluatorProtocol | None = None,
        fallback_evaluator: NLIEvaluatorProtocol | None = None,
    ) -> None:
        self.primary = primary_evaluator or CrossEncoderNLIEvaluator()
        self.fallback = fallback_evaluator or HeuristicNLIEvaluator()
        # LRU, not a plain dict: an insertion-ordered dict only evicts correctly
        # if you also touch every key on a hit, and the process-lifetime
        # singleton this backs must not grow past MAX_CACHE_ENTRIES.
        self._cache: OrderedDict[tuple[str, str], tuple[EntailmentStatus, float]] = OrderedDict()
        self._used_evaluators: list[str] = []
        self._fallback_used: bool = False

    @property
    def evaluator_name(self) -> str:
        """Identifier of the evaluator(s) that produced the verdicts so far.

        A single evaluator is reported by its own name (``heuristic``,
        ``cross-encoder/nli-deberta-v3-small``). When both the primary and the
        fallback produced verdicts, the name says so explicitly instead of
        hiding the degradation.
        """
        if not self._used_evaluators:
            return str(getattr(self.primary, "evaluator_name", "unknown"))
        if len(self._used_evaluators) == 1:
            return self._used_evaluators[0]
        return "mixed:" + "+".join(self._used_evaluators)

    @property
    def used_fallback(self) -> bool:
        """Whether the fallback evaluator has produced at least one verdict."""
        return self._fallback_used

    def cache_size(self) -> int:
        """Number of memoised verdicts currently retained."""
        return len(self._cache)

    def _record_evaluator(self, evaluator: NLIEvaluatorProtocol, fallback: bool) -> None:
        """Remember that ``evaluator`` produced a verdict."""
        name = str(getattr(evaluator, "evaluator_name", "") or type(evaluator).__name__)
        if name not in self._used_evaluators:
            self._used_evaluators.append(name)
        if fallback:
            self._fallback_used = True

    def _cache_get(
        self, key: tuple[str, str]
    ) -> tuple[EntailmentStatus, float] | None:
        """Return a memoised verdict, marking it as the most recently used."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(
        self, key: tuple[str, str], value: tuple[EntailmentStatus, float]
    ) -> None:
        """Memoise a verdict, evicting the least recently used entry if needed."""
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate with automatic fallback and caching."""
        cache_key = (premise.strip(), hypothesis.strip())
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            status, score = await self.primary.evaluate(premise, hypothesis)
            self._record_evaluator(self.primary, fallback=False)
        except Exception as exc:
            primary_name = str(getattr(self.primary, "evaluator_name", type(self.primary).__name__))
            fallback_name = str(getattr(self.fallback, "evaluator_name", type(self.fallback).__name__))
            logger.warning(
                "Primary NLI evaluator '%s' failed (%s); falling back to '%s'. "
                "Verdicts from this evaluator are degraded and are marked as such.",
                primary_name,
                exc,
                fallback_name,
            )
            try:
                status, score = await self.fallback.evaluate(premise, hypothesis)
                self._record_evaluator(self.fallback, fallback=True)
            except Exception as fallback_exc:
                raise NLIModelError(
                    f"Both primary and fallback evaluators failed: {fallback_exc}"
                ) from fallback_exc

        self._cache_put(cache_key, (status, score))
        return status, score


# Module-level default resilient evaluator
default_nli_evaluator = ResilientNLIEvaluator()
NLIEvaluationEngine = ResilientNLIEvaluator

__all__ = [
    "MAX_CACHE_ENTRIES",
    "MIN_TOPICAL_RELEVANCE",
    "CrossEncoderNLIEvaluator",
    "HeuristicNLIEvaluator",
    "LLMJudgeNLIEvaluator",
    "NLIEvaluationEngine",
    "ResilientNLIEvaluator",
    "default_nli_evaluator",
]
