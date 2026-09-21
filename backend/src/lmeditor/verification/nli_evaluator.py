"""Premise-Hypothesis NLI entailment scoring models with resilient fallbacks."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Coroutine, Sequence
from lmeditor.core.exceptions import NLIModelError
from lmeditor.core.types import EntailmentStatus, NLIEvaluatorProtocol


class HeuristicNLIEvaluator:
    """Fast, deterministic lexical and semantic entailment evaluator with negation awareness."""

    def __init__(self, entailment_threshold: float = 0.65) -> None:
        self.entailment_threshold = entailment_threshold
        self._negation_words = {"not", "never", "no", "neither", "nor", "cannot", "hardly", "scarcely"}

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate premise-hypothesis entailment via token overlap and negation analysis."""
        if not isinstance(premise, str) or not isinstance(hypothesis, str):
            raise NLIModelError("Both premise and hypothesis must be string instances.")

        p_clean = premise.strip().lower()
        h_clean = hypothesis.strip().lower()

        if not h_clean:
            raise NLIModelError("Hypothesis cannot be empty.")
        if not p_clean:
            return EntailmentStatus.UNGROUNDED, 0.0

        # Exact substring match
        if h_clean in p_clean:
            return EntailmentStatus.ENTAILED, 1.0

        # Tokenization & Stopword filtering
        p_tokens = set(re.findall(r"\b[a-z0-9]+\b", p_clean))
        h_tokens = [t for t in re.findall(r"\b[a-z0-9]+\b", h_clean)]

        if not h_tokens:
            return EntailmentStatus.NEUTRAL, 0.0

        # Check for negation polarity mismatch
        p_has_neg = any(w in p_tokens for w in self._negation_words)
        h_has_neg = any(w in set(h_tokens) for w in self._negation_words)
        negation_clash = p_has_neg != h_has_neg

        # Content word overlap (ignoring simple function words)
        stopwords = {
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "is", "are", "was", "were", "be", "been", "being", "that", "this", "these", "those",
            "it", "its", "and", "or", "as",
        }
        content_h_tokens = [t for t in h_tokens if t not in stopwords and t not in self._negation_words]
        if not content_h_tokens:
            content_h_tokens = h_tokens

        matched = sum(1 for t in content_h_tokens if t in p_tokens)
        overlap_ratio = matched / len(content_h_tokens)

        if negation_clash and overlap_ratio >= 0.5:
            # High lexical similarity but reversed polarity -> Contradiction
            score = round(max(0.1, 1.0 - overlap_ratio), 4)
            return EntailmentStatus.CONTRADICTED, score

        if overlap_ratio >= self.entailment_threshold:
            calibrated_score = round(min(1.0, overlap_ratio), 4)
            return EntailmentStatus.ENTAILED, calibrated_score

        if overlap_ratio >= 0.35:
            return EntailmentStatus.NEUTRAL, round(overlap_ratio, 4)

        return EntailmentStatus.UNGROUNDED, round(overlap_ratio, 4)


class CrossEncoderNLIEvaluator:
    """DeBERTa / Cross-Encoder premise-hypothesis inference model."""

    def __init__(self, model_name: str = "cross-encoder/nli-deberta-v3-small") -> None:
        self.model_name = model_name
        self._model: Any = None
        self._is_available: bool | None = None

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

            # CrossEncoder NLI models typically output logits: [contradiction, entailment, neutral]
            # Convert logits via softmax
            import numpy as np

            exp_scores = np.exp(scores - np.max(scores))
            probs = exp_scores / exp_scores.sum()

            # Mapping for standard NLI: index 0 = contradiction, index 1 = entailment, index 2 = neutral
            contradiction_prob = float(probs[0])
            entailment_prob = float(probs[1])
            neutral_prob = float(probs[2])

            if entailment_prob > 0.6:
                return EntailmentStatus.ENTAILED, round(entailment_prob, 4)
            elif contradiction_prob > 0.5:
                return EntailmentStatus.CONTRADICTED, round(contradiction_prob, 4)
            else:
                return EntailmentStatus.NEUTRAL, round(neutral_prob, 4)

        except Exception as exc:
            raise NLIModelError(f"CrossEncoder inference failed: {exc}") from exc


class LLMJudgeNLIEvaluator:
    """LLM-based premise-hypothesis entailment evaluator with custom callable injection."""

    def __init__(
        self,
        judge_callable: Callable[[str, str], Coroutine[Any, Any, tuple[EntailmentStatus, float]]] | None = None,
    ) -> None:
        self._judge_callable = judge_callable
        self._heuristic = HeuristicNLIEvaluator()

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate premise-hypothesis via LLM judge or fallback heuristic."""
        if self._judge_callable is not None:
            try:
                return await self._judge_callable(premise, hypothesis)
            except Exception as exc:
                raise NLIModelError(f"LLM judge invocation failed: {exc}") from exc

        # Default fallback to heuristic evaluator
        return await self._heuristic.evaluate(premise, hypothesis)


class ResilientNLIEvaluator:
    """Composite NLI evaluator with multi-tier fallback, concurrency, and caching."""

    def __init__(
        self,
        primary_evaluator: NLIEvaluatorProtocol | None = None,
        fallback_evaluator: NLIEvaluatorProtocol | None = None,
    ) -> None:
        self.primary = primary_evaluator or CrossEncoderNLIEvaluator()
        self.fallback = fallback_evaluator or HeuristicNLIEvaluator()
        self._cache: dict[tuple[str, str], tuple[EntailmentStatus, float]] = {}

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        """Evaluate with automatic fallback and caching."""
        cache_key = (premise.strip(), hypothesis.strip())
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            status, score = await self.primary.evaluate(premise, hypothesis)
        except Exception:
            # Fallback to secondary evaluator
            try:
                status, score = await self.fallback.evaluate(premise, hypothesis)
            except Exception as exc:
                raise NLIModelError(f"Both primary and fallback evaluators failed: {exc}") from exc

        self._cache[cache_key] = (status, score)
        return status, score

    async def evaluate_batch(
        self, pairs: Sequence[tuple[str, str]]
    ) -> list[tuple[EntailmentStatus, float]]:
        """Evaluate multiple (premise, hypothesis) pairs concurrently."""
        tasks = [self.evaluate(premise, hypothesis) for premise, hypothesis in pairs]
        return await asyncio.gather(*tasks)


# Module-level default resilient evaluator
default_nli_evaluator = ResilientNLIEvaluator()
NLIEvaluationEngine = ResilientNLIEvaluator

__all__ = [
    "CrossEncoderNLIEvaluator",
    "HeuristicNLIEvaluator",
    "LLMJudgeNLIEvaluator",
    "NLIEvaluationEngine",
    "ResilientNLIEvaluator",
    "default_nli_evaluator",
]
