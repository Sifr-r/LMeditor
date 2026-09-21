"""Verification package exposing ClaimExtractor, NLIEvaluator, and CitationChecker."""

from lmeditor.verification.citation_checker import (
    CitationChecker,
    compute_citation_precision,
    compute_citation_recall,
    compute_groundedness_f_beta,
    default_citation_checker,
)
from lmeditor.verification.claim_extractor import (
    ClaimExtractor,
    extract_citations,
    extract_claims,
    segment_sentences,
)
from lmeditor.verification.nli_evaluator import (
    CrossEncoderNLIEvaluator,
    HeuristicNLIEvaluator,
    LLMJudgeNLIEvaluator,
    ResilientNLIEvaluator,
    default_nli_evaluator,
)

__all__ = [
    "CitationChecker",
    "ClaimExtractor",
    "CrossEncoderNLIEvaluator",
    "HeuristicNLIEvaluator",
    "LLMJudgeNLIEvaluator",
    "ResilientNLIEvaluator",
    "compute_citation_precision",
    "compute_citation_recall",
    "compute_groundedness_f_beta",
    "default_citation_checker",
    "default_nli_evaluator",
    "extract_citations",
    "extract_claims",
    "segment_sentences",
]
