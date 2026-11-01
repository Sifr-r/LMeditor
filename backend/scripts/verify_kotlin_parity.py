"""Prove that the Kotlin tier and the Python tier agree on the shared rules.

The Kotlin tier is a second implementation of the deterministic half of the
backend. Two implementations of the same rule are worth nothing unless they
are *shown* to agree, so this script asks both of them the same questions and
compares the answers byte for byte.

Every check names one shared rule, the Python function that implements it,
the Kotlin method that implements it, and how to compare the two answers. A
rule whose Kotlin method is not available yet is reported as **skipped**, not
as passed: an absent check is not evidence. A skip also makes the run fail,
because a harness that reports success while it measured nothing is the
failure mode this script exists to prevent.

The Python side of every check is a frozen witness under
``lmeditor._reference``. :data:`REFERENCE_WITNESSES` is the ledger that says
which check witnesses which module, and which Kotlin file holds the
counterpart of a module that no check compares yet. The ledger is checked
against the tree on every run, so an unwitnessed rule is a *named* gap rather
than an invisible one.

Usage:
    python backend/scripts/verify_kotlin_parity.py
    python backend/scripts/verify_kotlin_parity.py --list

Exit code is 0 when every check agreed and the ledger held, 1 when a check
disagreed, a check was skipped, or the ledger did not hold.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from lmeditor.bridge import BridgeUnavailableError, KotlinBridge  # noqa: E402
from lmeditor.bridge.client import encode_bytes  # noqa: E402

# The Python side of every check comes from ``lmeditor._reference``: frozen,
# plain-Python implementations that never call across the NDJSON seam. The
# live modules under ``lmeditor.core`` and ``lmeditor.verification`` are doors
# to the Kotlin tier, so asking them would make the harness compare the tier
# with itself and agree by construction. A drift detector that cannot fail is
# not a drift detector.
from lmeditor._reference import citations as py_citations  # noqa: E402
from lmeditor._reference.sections import merge_operator_text  # noqa: E402
from lmeditor.core.schemas import SourceChunk  # noqa: E402

#: Texts used to exercise the citation grammar. They cover every marker form
#: the grammar accepts, plus the two forms it must never accept.
GRAMMAR_TEXTS: tuple[str, ...] = (
    "A plain sentence with no citation at all.",
    "The primary form [#chunk_123] carries the hash prefix.",
    "The bare form [chunk_123] carries no prefix.",
    "The cite form [cite: chunk_123] carries the cite prefix.",
    "A group [#a, #b] carries two ids.",
    "A group with mixed forms [cite: a, #b, c] carries three.",
    "A markdown link [the docs](https://example.com) is not a citation.",
    "An image ![alt](https://example.com/a.png) is not a citation.",
    "Two markers in one sentence [#one] and then [#two].",
    "A repeated marker [#dup] twice [#dup] is de-duplicated.",
    "A marker at the very start [#start] and at the very end [#end]",
    "Punctuation around a marker, like this: [#punct].",
    "Empty brackets [] are not a citation.",
    "A bracket with only a comma [,] is not a citation.",
    "A citation inside parentheses ([#paren]) is still a citation.",
    "Unicode text around it: café — [#uni] — naïve.",
    # An astral-plane character: one code point, two UTF-16 units. A tier that
    # reported UTF-16 offsets would shift every span that follows it.
    "Rocket \U0001F680 goes up [#astral] and lands.",
    "Emoji before the marker \U0001F680 [#after] and after it [#before] \U0001F680.",
)

#: Payload used for the chunk-digest check.
CHUNK_PAYLOAD: tuple[str, str, int, int, int, int] = (
    "doc-parity",
    "The quick brown fox jumps over the lazy dog.",
    0,
    43,
    0,
    43,
)

#: Texts the chunker is asked to segment. They cover a short document, a long
#: one that spans several windows, a document with paragraph breaks, a
#: document with an astral character, and the degenerate one-character case.
CHUNKING_TEXTS: dict[str, str] = {
    "short": "First sentence. Second sentence. Third sentence.",
    "paragraphs": (
        "Alpha beta gamma delta.\n\n"
        "Epsilon zeta eta theta.\n\n"
        "Iota kappa lambda mu."
    ),
    "long": " ".join(f"Sentence number {index} carries some words." for index in range(60)),
    "astral": "Rocket \U0001F680 goes up and comes down. Second sentence here.",
    "single": "x",
    "no-final-stop": "A sentence without a full stop",
    "repeated": "Same sentence. Same sentence. Same sentence. Same sentence.",
}

#: Texts the claim extractor is asked to segment.
CLAIM_TEXTS: dict[str, str] = {
    "sec-1": (
        "The model hallucinates less often with retrieval. "
        "Grounding reduces the error rate [#chunk_a]. "
        "This second claim has no citation."
    ),
    "sec-2": "One claim only.",
    "sec-3": "",
    # A text that arrives with whitespace around it. The Python models strip
    # the whitespace of a string field when they are constructed, and the
    # Kotlin models must do the same -- otherwise the two tiers report
    # different character offsets for the same sentence.
    "sec-padded": (
        "  The model hallucinates less often with retrieval. "
        "Grounding reduces the error rate [#chunk_a].  "
    ),
}


#: The ``.docx`` packages the Word checks read. The first is a plain document;
#: the second carries a chart, and with it the parts and the relationships that
#: a chart brings -- the parts a document model has to carry to put a package
#: back together.  The third is a merge that covers two of the table's three
#: columns: the origin spans two of them, the row below continues the region
#: over those two and carries a plain cell in the third, so every row fills the
#: grid.  It is registered for the harness's coverage -- a real package the
#: word checks read and write end to end -- not because it pins the
#: ragged-region rule: the door and the tier measure the same grid on it
#: (W12, W16).
WORD_FIXTURES: tuple[Path, ...] = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "minimal.docx",
    REPO_ROOT / "backend" / "tests" / "fixtures" / "word_chart_fixture.docx",
    REPO_ROOT / "backend" / "tests" / "fixtures" / "word_merge_region.docx",
)

#: The keys whose wire value is a timestamp. The two tiers spell a timestamp
#: differently -- Python writes ``2024-01-01T00:00:00+00:00`` and Kotlin writes
#: ``2024-01-01T00:00:00Z`` -- so the comparison parses them and lets the
#: ``datetime`` objects decide whether they are the same moment.
TIMESTAMP_KEYS: frozenset[str] = frozenset({"created", "modified", "date", "added_at"})


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one parity check."""

    name: str
    status: str  # "passed", "failed" or "skipped"
    detail: str = ""


@dataclass(frozen=True)
class Check:
    """One shared rule, asked of both tiers."""

    name: str
    method: str
    python: Callable[[], Any]
    kotlin_params: Callable[[], Mapping[str, Any]]
    kotlin: Callable[[KotlinBridge], Any] | None = None
    normalise: Callable[[Any], Any] = lambda value: value
    compare: Callable[[Any, Any], bool] = lambda left, right: left == right


def _chunk_digest_python() -> Any:
    document_id, text, char_start, char_end, byte_start, byte_end = CHUNK_PAYLOAD
    return SourceChunk.create(
        document_id=document_id,
        text=text,
        char_start=char_start,
        char_end=char_end,
        byte_start=byte_start,
        byte_end=byte_end,
    ).chunk_id


def _chunk_digest_params() -> Mapping[str, Any]:
    document_id, text, char_start, char_end, byte_start, byte_end = CHUNK_PAYLOAD
    return {
        "document_id": document_id,
        "text": text,
        "char_start": char_start,
        "char_end": char_end,
        "byte_start": byte_start,
        "byte_end": byte_end,
    }


def _citations_python() -> Any:
    return {
        "chunk_ids": {text: py_citations.extract_chunk_ids(text) for text in GRAMMAR_TEXTS},
        "spans": {
            text: [list(span) for span in py_citations.iter_citation_spans(text)]
            for text in GRAMMAR_TEXTS
        },
        "stripped": {text: py_citations.strip_citations(text) for text in GRAMMAR_TEXTS},
    }


def _citations_params() -> Mapping[str, Any]:
    return {"texts": list(GRAMMAR_TEXTS)}


def _citations_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the Kotlin tier the three questions the grammar answers."""
    answers: dict[str, Any] = {"chunk_ids": {}, "spans": {}, "stripped": {}}
    for text in GRAMMAR_TEXTS:
        ids = bridge.call("core.citations.extract_chunk_ids", {"text": text})
        spans = bridge.call("core.citations.iter_spans", {"text": text})
        stripped = bridge.call("core.citations.strip", {"text": text})
        answers["chunk_ids"][text] = list(ids.get("chunk_ids", []))
        answers["spans"][text] = [
            [span["chunk_id"], span["start"], span["end"]]
            for span in spans.get("spans", [])
        ]
        answers["stripped"][text] = stripped.get("text", "")
    return answers


def _citations_normalise(value: Any) -> Any:
    """Compare the citation answers, whatever shape each tier returned."""
    if not isinstance(value, dict):
        return value
    chunk_ids = value.get("chunk_ids")
    spans = value.get("spans")
    stripped = value.get("stripped")
    if not isinstance(chunk_ids, dict) or not isinstance(spans, dict) or not isinstance(stripped, dict):
        return value
    normalised_spans: dict[str, list[tuple[Any, ...]]] = {}
    for text, entries in spans.items():
        normalised_spans[text] = [
            (entry[0], entry[1], entry[2]) if isinstance(entry, (list, tuple)) else tuple(entry)
            for entry in entries
        ]
    return {
        "chunk_ids": {text: list(ids) for text, ids in chunk_ids.items()},
        "spans": normalised_spans,
        "stripped": dict(stripped),
    }


#: The text the operator is said to have written.
#:
#: It arrives with whitespace around it on purpose. A section that is
#: *constructed* has its string fields stripped -- that is what Pydantic's
#: ``str_strip_whitespace=True`` means -- but a section that is *copied* does
#: not, because Pydantic's ``model_copy()`` does not re-validate. The merged
#: section is a copy, so the operator's words are stored as they were written.
OPERATOR_EDIT_CONTENT: str = "  The operator's words.  "


@lru_cache(maxsize=1)
def _operator_edit_draft() -> Any:
    """The drafted section both tiers are asked to merge an edit into.

    Built once and shared: the two tiers must be asked about the *same*
    section, down to the timestamps it carries.
    """
    from lmeditor.core.schemas import CitationLineage, Claim, SectionDraft, SectionStatus

    lineage = CitationLineage(
        lineage_id="lin-1",
        claim_id="claim-1",
        chunk_id="a" * 64,
        span_start=0,
        span_end=5,
    )
    claim = Claim(
        claim_id="claim-1",
        section_id="sec-1",
        text="A claim.",
        char_start=0,
        char_end=8,
    )
    return SectionDraft(
        section_id="sec-1",
        title="Drafted",
        content="The pipeline's words.",
        order=3,
        citations=[lineage],
        claims=[claim],
        is_verified=True,
        status=SectionStatus.VERIFIED,
        revision_count=2,
        critique="tighten this",
    )


def _operator_edit_python() -> Any:
    drafted = _operator_edit_draft()
    merged = merge_operator_text(drafted, content=OPERATOR_EDIT_CONTENT)
    return {
        "content": merged.content,
        "title": merged.title,
        "order": merged.order,
        "citations": [item.model_dump(mode="json") for item in merged.citations],
        "claims": [item.model_dump(mode="json") for item in merged.claims],
        "is_verified": merged.is_verified,
        "status": merged.status.value,
        "revision_count": merged.revision_count,
        "critique": merged.critique,
    }


def _operator_edit_params() -> Mapping[str, Any]:
    return {
        "draft": _operator_edit_draft().model_dump(mode="json"),
        "content": OPERATOR_EDIT_CONTENT,
    }


def _operator_edit_normalise(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    draft = value.get("draft", value)
    if not isinstance(draft, dict):
        return value
    return {
        "content": draft.get("content"),
        "title": draft.get("title"),
        "order": draft.get("order"),
        "citations": draft.get("citations") or [],
        "claims": draft.get("claims") or [],
        "is_verified": draft.get("is_verified"),
        "status": draft.get("status"),
        "revision_count": draft.get("revision_count"),
        "critique": draft.get("critique"),
    }


def _chunking_python() -> Any:
    from lmeditor._reference.chunker import SentenceAwareChunker

    chunker = SentenceAwareChunker()
    return {
        document_id: [
            {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "byte_start": chunk.byte_start,
                "byte_end": chunk.byte_end,
            }
            for chunk in chunker.chunk(text, document_id)
        ]
        for document_id, text in CHUNKING_TEXTS.items()
    }


def _chunking_kotlin(bridge: KotlinBridge) -> Any:
    answers: dict[str, Any] = {}
    for document_id, text in CHUNKING_TEXTS.items():
        result = bridge.call(
            "text.chunk",
            {"text": text, "document_id": document_id},
        )
        answers[document_id] = [
            {
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                "char_start": chunk["char_start"],
                "char_end": chunk["char_end"],
                "byte_start": chunk["byte_start"],
                "byte_end": chunk["byte_end"],
            }
            for chunk in result.get("chunks", [])
        ]
    return answers


def _claims_python() -> Any:
    from lmeditor._reference.claim_extractor import extract_claims

    return {
        section_id: [
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "char_start": claim.char_start,
                "char_end": claim.char_end,
                "cited_chunk_ids": list(claim.cited_chunk_ids),
                "sentence_idx": claim.sentence_idx,
            }
            for claim in extract_claims(text, section_id)
        ]
        for section_id, text in CLAIM_TEXTS.items()
    }


def _claims_kotlin(bridge: KotlinBridge) -> Any:
    answers: dict[str, Any] = {}
    for section_id, text in CLAIM_TEXTS.items():
        result = bridge.call(
            "text.claims.extract",
            {"text": text, "section_id": section_id},
        )
        answers[section_id] = [
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "char_start": claim["char_start"],
                "char_end": claim["char_end"],
                "cited_chunk_ids": list(claim["cited_chunk_ids"]),
                "sentence_idx": claim["sentence_idx"],
            }
            for claim in result.get("claims", [])
        ]
    return answers


#: The routing questions both tiers are asked. They cover every branch the
#: rules can take: everything verified, something still to revise, the retry
#: budget spent, and the operator's two answers.
def _routing_states() -> tuple[tuple[str, dict[str, Any]], ...]:
    from lmeditor.core.schemas import SectionDraft
    from lmeditor.core.types import SectionStatus

    verified = SectionDraft(
        section_id="s1",
        title="Verified",
        content="Body.",
        order=0,
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )
    unverified = SectionDraft(
        section_id="s2",
        title="Unverified",
        content="Body.",
        order=1,
        is_verified=False,
        status=SectionStatus.DRAFTING,
    )
    return (
        ("all verified", {"section_drafts": {"s1": verified}, "retry_counts": {}}),
        (
            "needs refinement",
            {
                "section_drafts": {"s2": unverified},
                "retry_counts": {"s2": 0},
                "max_retries": 3,
            },
        ),
        (
            "human gate",
            {
                "section_drafts": {"s2": unverified},
                "retry_counts": {"s2": 3},
                "max_retries": 3,
            },
        ),
        ("approved", {"hitl_approved": True}),
        ("rejected", {"hitl_approved": False}),
    )


def _routing_python() -> Any:
    from lmeditor._reference.edges import route_after_hitl, route_after_verification

    answers: dict[str, Any] = {}
    for name, state in _routing_states():
        if name in ("approved", "rejected"):
            answers[name] = route_after_hitl(state)  # type: ignore[arg-type]
        else:
            answers[name] = route_after_verification(state)  # type: ignore[arg-type]
    return answers


def _routing_kotlin(bridge: KotlinBridge) -> Any:
    answers: dict[str, Any] = {}
    for name, state in _routing_states():
        after_hitl = name in ("approved", "rejected")
        params: dict[str, Any] = {
            "route": "after_hitl" if after_hitl else "after_verification",
            "hitl_approved": bool(state.get("hitl_approved", False)),
        }
        if not after_hitl:
            params["section_drafts"] = {
                key: value.model_dump(mode="json")
                for key, value in (state.get("section_drafts") or {}).items()
            }
            params["retry_counts"] = dict(state.get("retry_counts") or {})
            if state.get("max_retries") is not None:
                params["max_retries"] = state["max_retries"]
        result = bridge.call("workflow.route", params)
        answers[name] = result.get("next_step")
    return answers


def _workflow_state_python() -> Any:
    from lmeditor._reference.state import create_initial_harness_state

    state = create_initial_harness_state(
        doc_id="doc-parity",
        topic="Parity",
        target_audience="Readers",
        raw_texts=["First raw text."],
        guidelines=["Be brief."],
        max_retries=2,
        threshold_f_beta=0.9,
    )
    return _workflow_state_view(state)


def _workflow_state_view(state: Any) -> dict[str, Any]:
    """The part of a fresh state both tiers are compared on."""
    return {
        "doc_id": state.get("doc_id"),
        "topic": state.get("topic"),
        "status": str(state.get("status")),
        "target_audience": state.get("target_audience"),
        "guided_research": state.get("guided_research"),
        "raw_texts": list(state.get("raw_texts") or []),
        "guidelines": list(state.get("guidelines") or []),
        "max_retries": state.get("max_retries"),
        "threshold_f_beta": state.get("threshold_f_beta"),
        "errors": list(state.get("errors") or []),
    }


def _workflow_state_kotlin(bridge: KotlinBridge) -> Any:
    result = bridge.call(
        "workflow.create_initial_state",
        {
            "doc_id": "doc-parity",
            "topic": "Parity",
            "target_audience": "Readers",
            "raw_texts": ["First raw text."],
            "guidelines": ["Be brief."],
            "max_retries": 2,
            "threshold_f_beta": 0.9,
        },
    )
    return _workflow_state_view(result.get("state") or {})


#: The count triples the citation arithmetic is asked about. They cover a
#: healthy document, an empty one, a document whose citations are all wrong,
#: and a document whose every claim is cited and supported.
CITATION_METRIC_CASES: tuple[tuple[int, int, int], ...] = (
    (2, 3, 5),
    (0, 0, 0),
    (0, 3, 5),
    (3, 3, 5),
    (1, 1, 1),
    (0, 0, 5),
)


def _citation_metrics_python() -> Any:
    from lmeditor._reference.citation_checker import (
        compute_citation_precision,
        compute_citation_recall,
        compute_groundedness_f_beta,
    )

    answers: dict[str, Any] = {}
    for supported_cited, total_cited, total in CITATION_METRIC_CASES:
        precision = compute_citation_precision(supported_cited, total_cited, total)
        recall = compute_citation_recall(supported_cited, total)
        answers[f"{supported_cited}/{total_cited}/{total}"] = {
            "precision": precision,
            "recall": recall,
            "f_beta": compute_groundedness_f_beta(precision, recall),
        }
    return answers


def _citation_metrics_kotlin(bridge: KotlinBridge) -> Any:
    answers: dict[str, Any] = {}
    for supported_cited, total_cited, total in CITATION_METRIC_CASES:
        precision = bridge.call(
            "text.citation_check.precision",
            {
                "supported_cited_claims": supported_cited,
                "total_cited_claims": total_cited,
                "total_claims": total,
            },
        ).get("precision")
        recall = bridge.call(
            "text.citation_check.recall",
            {"supported_claims": supported_cited, "total_claims": total},
        ).get("recall")
        f_beta = bridge.call(
            "text.citation_check.f_beta",
            {"precision": precision, "recall": recall, "beta": 0.5},
        ).get("f_beta")
        answers[f"{supported_cited}/{total_cited}/{total}"] = {
            "precision": precision,
            "recall": recall,
            "f_beta": f_beta,
        }
    return answers


#: The claims the premise rule is asked about: one that cites a chunk the
#: document has, and one that cites nothing.
def _premise_claims() -> tuple[list[Any], dict[str, Any]]:
    from lmeditor.core.schemas import Claim, SourceChunk

    chunk = SourceChunk(
        chunk_id="a" * 64,
        document_id="doc-parity",
        text="The sky is blue because of Rayleigh scattering.",
        char_start=0,
        char_end=49,
        byte_start=0,
        byte_end=49,
        token_count=8,
    )
    cited = Claim(
        claim_id="claim-1",
        section_id="sec-1",
        text="The sky is blue.",
        char_start=0,
        char_end=16,
        cited_chunk_ids=["a" * 64],
    )
    uncited = Claim(
        claim_id="claim-2",
        section_id="sec-1",
        text="Rain is wet.",
        char_start=17,
        char_end=30,
    )
    return [cited, uncited], {"a" * 64: chunk}


def _premises_python() -> Any:
    from lmeditor._reference.citation_checker import CitationChecker

    claims, chunks_by_id = _premise_claims()
    cited, premises = CitationChecker._resolve_premises(
        claims,
        chunks_by_id,
        False,
        "sec-1",
    )
    return {"cited": list(cited), "premises": list(premises)}


def _premises_kotlin(bridge: KotlinBridge) -> Any:
    claims, chunks_by_id = _premise_claims()
    result = bridge.call(
        "text.citation_check.resolve_premises",
        {
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks_by_id.values()],
            "strict_hallucination_check": False,
            "section_id": "sec-1",
        },
    )
    return {
        "cited": list(result.get("cited") or []),
        "premises": list(result.get("premises") or []),
    }


def _lineage_chunk() -> Any:
    from lmeditor.core.schemas import SourceChunk

    text = "The sky is blue because of Rayleigh scattering. Water is wet."
    return SourceChunk(
        chunk_id="a" * 64,
        document_id="doc-parity",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text),
        token_count=12,
    )


def _lineage_view(lineage: Any) -> dict[str, Any]:
    """The part of a lineage record both tiers are compared on.

    The identifier and the timestamp are left out on purpose: the Python
    original minted a random identifier and the tier mints a deterministic
    one, and neither the identifier nor the moment a record was made is part
    of the rule.
    """
    return {
        "chunk_id": lineage.chunk_id,
        "span_start": lineage.span_start,
        "span_end": lineage.span_end,
        "exact_quote": lineage.exact_quote,
        "confidence_score": lineage.confidence_score,
    }


def _lineage_view_wire(lineage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": lineage.get("chunk_id"),
        "span_start": lineage.get("span_start"),
        "span_end": lineage.get("span_end"),
        "exact_quote": lineage.get("exact_quote"),
        "confidence_score": lineage.get("confidence_score"),
    }


def _lineage_python() -> Any:
    from lmeditor._reference.lineage import CitationLineageEngine

    chunk = _lineage_chunk()
    engine = CitationLineageEngine()
    store = {chunk.chunk_id: chunk}
    span = engine.attribute_span(
        draft_text=chunk.text,
        span_start=0,
        span_end=49,
        cited_chunk_id=chunk.chunk_id,
        claim_id="claim-1",
        chunk_store=store,
    )
    draft = engine.extract_citations_from_draft(
        draft_text=f"Grounding helps [#{chunk.chunk_id}].",
        chunk_store=store,
    )
    return {
        "span": _lineage_view(span),
        "draft": [_lineage_view(item) for item in draft],
    }


def _lineage_kotlin(bridge: KotlinBridge) -> Any:
    chunk = _lineage_chunk()
    span_result = bridge.call(
        "text.lineage.attribute_span",
        {
            "draft_text": chunk.text,
            "span_start": 0,
            "span_end": 49,
            "chunk_id": chunk.chunk_id,
            "claim_id": "claim-1",
            "chunks": [chunk.model_dump(mode="json")],
        },
    )
    draft_result = bridge.call(
        "text.lineage.extract_from_draft",
        {
            "draft_text": f"Grounding helps [#{chunk.chunk_id}].",
            "chunks": [chunk.model_dump(mode="json")],
        },
    )
    spans = span_result.get("lineages") or []
    return {
        "span": _lineage_view_wire(spans[0]) if spans else None,
        "draft": [
            _lineage_view_wire(item) for item in draft_result.get("lineages") or []
        ],
    }


def _outline_corpus() -> tuple[list[Any], Any]:
    """The corpus and the memory the outline rule is asked about."""
    from lmeditor.core.schemas import ResearchMemory, SourceChunk

    texts = [
        "Retrieval augmented generation grounds a language model in sources.",
        "Chunking decides which passage a citation marker points at.",
        "An entailment model judges whether a claim follows from a passage.",
    ]
    chunks = [
        SourceChunk(
            chunk_id=f"{index:064d}",
            document_id="doc-parity",
            text=text,
            char_start=0,
            char_end=len(text),
            byte_start=0,
            byte_end=len(text),
            token_count=8,
        )
        for index, text in enumerate(texts, start=1)
    ]
    memory = ResearchMemory(
        topic="Grounded generation",
        sessions=[],
        all_referenced_chunk_ids=[chunk.chunk_id for chunk in chunks],
        collected_takeaways=["Grounding needs citations."],
    )
    return chunks, memory


def _outline_view(outline: Any) -> Any:
    """The outline's shape, without the parts that are not the rule."""
    return {
        "topic": outline.topic,
        "nodes": [_outline_node_view(node) for node in outline.nodes],
    }


def _outline_node_view(node: Any) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "title": node.title,
        "depth": node.depth,
        "order": node.order,
        "assigned_chunk_ids": list(node.assigned_chunk_ids),
        "children": [_outline_node_view(child) for child in node.children],
    }


def _outline_python() -> Any:
    from lmeditor._reference.outliner import generate_outline

    chunks, memory = _outline_corpus()
    return _outline_view(generate_outline("Grounded generation", memory, chunks))


def _outline_kotlin(bridge: KotlinBridge) -> Any:
    chunks, memory = _outline_corpus()
    result = bridge.call(
        "discovery.outline",
        {
            "topic": "Grounded generation",
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "research_memory": memory.model_dump(mode="json"),
        },
    )
    outline = result.get("outline") or {}

    def node_view(node: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "node_id": node.get("node_id"),
            "title": node.get("title"),
            "depth": node.get("depth"),
            "order": node.get("order"),
            "assigned_chunk_ids": list(node.get("assigned_chunk_ids") or []),
            "children": [node_view(child) for child in node.get("children") or []],
        }

    return {
        "topic": outline.get("topic"),
        "nodes": [node_view(node) for node in outline.get("nodes") or []],
    }


def _perspectives_python() -> Any:
    from lmeditor._reference.perspective import generate_perspectives

    return [
        {
            "role": perspective.role.value,
            "title": perspective.title,
            "focus_areas": list(perspective.focus_areas),
        }
        for perspective in generate_perspectives("Grounded generation")
    ]


def _perspectives_kotlin(bridge: KotlinBridge) -> Any:
    result = bridge.call(
        "discovery.perspectives",
        {"topic": "Grounded generation", "count": 4},
    )
    return [
        {
            "role": perspective.get("role"),
            "title": perspective.get("title"),
            "focus_areas": list(perspective.get("focus_areas") or []),
        }
        for perspective in result.get("perspectives") or []
    ]


def _word_read_python_document(payload: bytes) -> dict[str, Any]:
    """Read a package with the frozen Python reader and return its wire form.

    The reader is the *witness*: it is the original Python implementation,
    frozen, and it never calls across the seam. It is what judges both
    writers, so the two of them are compared on the same document.
    """
    from lmeditor._reference.word_reader import WordReader

    reader = WordReader()
    document = reader.read_bytes(payload)
    return {
        "document": document.to_wire(),
        "omissions": list(reader.last_omissions),
        "package_relationships": [
            relationship.to_wire()
            for relationship in reader.last_package_relationships
        ],
    }


def _word_read_python() -> Any:
    return [_word_read_python_document(fixture.read_bytes()) for fixture in WORD_FIXTURES]


def _word_read_kotlin(bridge: KotlinBridge) -> Any:
    reads = []
    for fixture in WORD_FIXTURES:
        result = bridge.call(
            "word.read",
            {
                "bytes_b64": encode_bytes(fixture.read_bytes()),
                "origin": str(fixture),
            },
        )
        reads.append(
            {
                "document": result.get("document"),
                "omissions": list(result.get("omissions") or []),
                "package_relationships": list(result.get("package_relationships") or []),
            }
        )
    return reads


def _word_write_python() -> Any:
    """The documents the frozen Python writer produces, read back.

    Two writers cannot be compared byte for byte: ``python-docx`` and the
    hand-written OOXML of the Kotlin tier lay the same document out
    differently. They are compared on what they *mean* -- each package is read
    back by the same frozen reader, and the two documents are compared.
    """
    from lmeditor._reference.word_reader import WordReader
    from lmeditor._reference.word_writer import WordWriter

    packages = []
    for fixture in WORD_FIXTURES:
        document = WordReader().read_bytes(fixture.read_bytes())
        packages.append(
            _word_written_case(document, WordWriter().write(document))
        )
    return packages


def _word_write_kotlin(bridge: KotlinBridge) -> Any:
    from lmeditor._reference.word_reader import WordReader

    packages = []
    for fixture in WORD_FIXTURES:
        # Both writers are given the same input: the document the frozen
        # reader produces from the fixture.
        source = WordReader().read_bytes(fixture.read_bytes())
        result = bridge.call(
            "word.write",
            {"document": source.to_wire(), "deterministic": True},
        )
        packages.append(
            _word_written_case(
                source,
                base64.b64decode(str(result["bytes_b64"])),
            )
        )
    return packages


def _word_written_case(model: Any, payload: bytes) -> dict[str, Any]:
    """One written package, read back and compared on what the model describes.

    The Python writer starts from ``python-docx``'s default template, so its
    package carries parts the model never mentioned: a theme, a font table,
    web settings, custom XML and a thumbnail. Those belong to the template,
    not to the document, and the Kotlin writer has no template to carry. The
    comparison is therefore about the parts the model names.

    The same reasoning applies to the package root's relationships: the
    Python one also reaches the template's thumbnail and its application
    properties. The relationship that has to be there -- the one that
    reaches the document part -- is compared.
    """
    from lmeditor._reference.word_reader import WordReader

    reader = WordReader()
    document = reader.read_bytes(payload)
    wire = document.to_wire()
    model_parts = {part.part_name for part in model.parts}
    wire["parts"] = [
        part for part in wire["parts"] if part["part_name"] in model_parts
    ]
    package_relationships = [
        relationship.to_wire() for relationship in reader.last_package_relationships
    ]
    return {
        "document": wire,
        "omissions": list(reader.last_omissions),
        "package_relationships": [
            relationship
            for relationship in package_relationships
            if relationship["target"].lstrip("/") in model_parts
            or relationship["target"].lstrip("/") == "word/document.xml"
        ],
    }


# --------------------------------------------------------------------------
# The manuscript builder: the same draft, built on both sides.
# --------------------------------------------------------------------------

#: The drafts the manuscript check builds the same document from on both
#: sides. They cover the branches the builder has: a plain section, an inline
#: citation with a cited passage, the markdown-flavoured syntax the editor
#: writes (heading, list, quote, fenced code, a pipe table), the
#: ``verified_only`` filter and the reference list.
#:
#: The draft is built once and shared, so both tiers are asked about the same
#: input, down to the timestamps it carries.
@lru_cache(maxsize=1)
def _manuscript_cases() -> tuple[tuple[str, Any, tuple[Any, ...], bool], ...]:
    """The (name, draft, bibliography, verified_only) cases of the manuscript."""
    from lmeditor.core.schemas import (
        BibliographicRecord,
        CitationLineage,
        Claim,
        DocumentDraft,
        SectionDraft,
    )
    from lmeditor.core.types import BibliographyResolverKind, SectionStatus

    lineage = CitationLineage(
        lineage_id="lin-1",
        claim_id="claim-1",
        chunk_id="chunk-1",
        span_start=0,
        span_end=20,
        exact_quote="The cited passage, verbatim.",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    claim = Claim(
        claim_id="claim-1",
        section_id="sec-1",
        text="A cited fact.",
        char_start=0,
        char_end=14,
        cited_chunk_ids=["chunk-1"],
    )
    plain = SectionDraft(
        section_id="sec-plain",
        title="Plain",
        content="A plain sentence with no citation at all.",
        order=0,
    )
    cited = SectionDraft(
        section_id="sec-cited",
        title="Cited",
        content="A cited fact [#chunk-1] and a bare one [chunk-2].",
        order=1,
        citations=[lineage],
        claims=[claim],
    )
    marked_up = SectionDraft(
        section_id="sec-markup",
        title="Marked up",
        content=(
            "## A heading\n"
            "\n"
            "A paragraph with **bold** and _italic_ and `code`.\n"
            "\n"
            "- A bullet\n"
            "- Another bullet\n"
            "\n"
            "1. A number\n"
            "\n"
            "> A quote\n"
            "\n"
            "```python\n"
            "print('hi')\n"
            "```\n"
            "\n"
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |"
        ),
        order=2,
    )
    verified = SectionDraft(
        section_id="sec-verified",
        title="Verified",
        content="A section that passed verification.",
        order=3,
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )
    unverified = SectionDraft(
        section_id="sec-unverified",
        title="Unverified",
        content="A section that did not.",
        order=4,
        status=SectionStatus.DRAFTING,
    )
    # A list written in a script that does not use ASCII digits, and a marker
    # followed by nothing but a Unicode space.  The Python witness reads
    # Python's `\d` and `\s`, which are Unicode-aware; the tier has to agree
    # with them character for character.
    exotic = SectionDraft(
        section_id="sec-exotic",
        title="Exotic",
        content=(
            "\u0663. Three\n"
            "\u0664. Four\n"
            "\n"
            "lead\n"
            "- \u00a0\n"
            "- \u001c\n"
            "\n"
            "# A heading\u2028"
        ),
        order=5,
    )
    bibliography = (
        BibliographicRecord(
            title="Reef census findings",
            authors=["Ada Lovelace", "Charles Babbage"],
            year=1843,
            container_title="The Journal",
            volume="12",
            issue="3",
            pages="100-120",
            doi="10.1234/reef.5678",
            url="https://example.org/reef",
            type="journal-article",
            resolver=BibliographyResolverKind.CROSSREF,
        ),
        BibliographicRecord(title="An Anonymous Paper"),
    )
    return (
        ("plain", DocumentDraft(document_id="doc-1", title="Plain", sections=[plain]), (), False),
        ("cited", DocumentDraft(document_id="doc-2", title="Cited", sections=[cited]), (), False),
        (
            "marked up",
            DocumentDraft(document_id="doc-3", title="Marked up", sections=[marked_up]),
            (),
            False,
        ),
        (
            "bibliography",
            DocumentDraft(document_id="doc-4", title="Bibliography", sections=[plain]),
            bibliography,
            False,
        ),
        (
            "verified only",
            DocumentDraft(
                document_id="doc-5",
                title="Verified only",
                sections=[verified, unverified],
            ),
            (),
            True,
        ),
        (
            "exotic characters",
            DocumentDraft(document_id="doc-6", title="Exotic", sections=[exotic]),
            (),
            False,
        ),
    )


def _manuscript_python() -> Any:
    """The documents the frozen Python builder produces, in their wire form.

    The witness is the *frozen* original: it is the Python implementation of
    the manuscript rules, and it never calls across the seam.  The live door
    would answer with the tier's own document, and a comparison against it
    would agree by construction.
    """
    from lmeditor._reference.word_manuscript import build_manuscript_document

    return {
        name: build_manuscript_document(draft, bibliography, verified_only=verified_only).to_wire()
        for name, draft, bibliography, verified_only in _manuscript_cases()
    }


def _manuscript_kotlin(bridge: KotlinBridge) -> Any:
    """The documents the Kotlin tier produces, in their wire form."""
    answers: dict[str, Any] = {}
    for name, draft, bibliography, verified_only in _manuscript_cases():
        result = bridge.call(
            "word.manuscript",
            {
                "draft": draft.to_wire(),
                "bibliography": [record.to_wire() for record in bibliography],
                "verified_only": verified_only,
            },
        )
        answers[name] = result.get("document")
    return answers


#: The wire form of one stored source.
#:
#: It is written out by hand so that the harness never has to ask a door what
#: a stored source looks like: the harness's Python side has to be the frozen
#: witness, and a door is by definition the thing the witness is compared
#: against.
SERVER_SOURCE_WIRE: dict[str, Any] = {
    "source_id": "src-1",
    "title": "A Paper",
    "kind": "pdf",
    "origin": "C:/docs/a-paper.pdf",
    "chunks": [],
    "record": None,
    "byte_size": 1024,
    "omissions": ["images"],
    "added_at": "2026-01-01T00:00:00Z",
}


def _server_source_summary(source: Mapping[str, Any]) -> dict[str, Any]:
    """A stored source's summary, in the shape the Python one reports."""
    return {
        "source_id": source["source_id"],
        "title": source["title"],
        "kind": source["kind"],
        "origin": source["origin"],
        "chunk_count": len(source.get("chunks") or []),
        "byte_size": source.get("byte_size"),
        "added_at": source["added_at"],
        "omissions": list(source.get("omissions") or []),
        "bibliography": source.get("record"),
    }


def _server_sources_python() -> Any:
    """The frozen library's answer to a small script of operations."""
    from lmeditor._reference.server_sources import SourceLibrary, StoredSource
    from lmeditor.core.types import SourceKind

    library = SourceLibrary()
    library.add(
        StoredSource(
            source_id="src-1",
            title="A Paper",
            kind=SourceKind.PDF,
            origin="C:/docs/a-paper.pdf",
            chunks=(),
            byte_size=1024,
            omissions=("images",),
            added_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    library.add(
        StoredSource(
            source_id="src-2",
            title="Another Paper",
            kind=SourceKind.DOCX,
            origin="C:/docs/another.docx",
            chunks=(),
            byte_size=2048,
            omissions=(),
            added_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    return {
        "ids": [stored.source_id for stored in library.list()],
        "len": len(library),
        "chunks_for": [
            chunk.chunk_id for chunk in library.chunks_for(["src-2", "src-1"])
        ],
        "summaries": [stored.to_summary() for stored in library.list()],
        "removed": library.remove("src-1"),
        "len_after_remove": len(library),
        "gone": library.get("src-1") is None,
    }


def _server_sources_kotlin(bridge: KotlinBridge) -> Any:
    """The tier's answer to the same script of operations."""
    bridge.call("server.sources.clear", {})
    second = {
        **SERVER_SOURCE_WIRE,
        "source_id": "src-2",
        "title": "Another Paper",
        "kind": "docx",
        "origin": "C:/docs/another.docx",
        "byte_size": 2048,
        "omissions": [],
        "added_at": "2026-01-02T00:00:00Z",
    }
    bridge.call("server.sources.add", {"source": SERVER_SOURCE_WIRE})
    bridge.call("server.sources.add", {"source": second})

    listed = bridge.call("server.sources.list", {}).get("sources") or []
    chunks = bridge.call(
        "server.sources.chunks_for", {"source_ids": ["src-2", "src-1"]}
    ).get("chunks") or []
    # The count is read before the removal, so that it answers the same
    # question the Python side's does.
    count = bridge.call("server.sources.len", {}).get("count")
    removed = bridge.call("server.sources.remove", {"source_id": "src-1"})
    gone = bridge.call("server.sources.get", {"source_id": "src-1"}).get("source")
    return {
        "ids": [source["source_id"] for source in listed],
        "len": count,
        "chunks_for": [chunk["chunk_id"] for chunk in chunks],
        "summaries": [_server_source_summary(source) for source in listed],
        "removed": removed.get("removed"),
        "len_after_remove": bridge.call("server.sources.len", {}).get("count"),
        "gone": gone is None,
    }


def _normalise_timestamps(value: Any) -> Any:
    """Parse the timestamps, so the two spellings of one moment compare equal."""
    if isinstance(value, dict):
        return {
            key: (
                _parse_timestamp(item)
                if key in TIMESTAMP_KEYS and isinstance(item, str)
                else _normalise_timestamps(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalise_timestamps(item) for item in value]
    return value


#: The keys whose value is an arbitrary label rather than a fact about the
#: document.
#:
#: Two writers that lay the same document out differently are expected to name
#: its relationships differently -- one of them is free to call a
#: relationship `rId1` where the other calls it `rId7`. What has to agree is
#: the *structure*: which parts exist, which part holds which relationship,
#: what type it has and what it points at. The labels are replaced so that the
#: comparison is about that structure and nothing else.
LABEL_KEYS: frozenset[str] = frozenset({"relationship_id"})


def _normalise_labels(value: Any) -> Any:
    """Replace the arbitrary labels with one placeholder."""
    if isinstance(value, dict):
        return {
            key: (
                "<label>"
                if key in LABEL_KEYS and isinstance(item, str)
                else _normalise_labels(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalise_labels(item) for item in value]
    return value


def _normalise_written(value: Any) -> Any:
    """The comparison a written package gets: timestamps and labels aside."""
    return _normalise_labels(_normalise_timestamps(value))


def _parse_timestamp(value: str) -> Any:
    """The moment a timestamp names, or the text itself when it names none."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return value


# --------------------------------------------------------------------------
# The source analysis: pure text work, and Unicode-exact by contract.
# --------------------------------------------------------------------------

#: The texts the source analysis is asked about. They cover the plain case, the
#: case with a heading, the case with a list, and -- because the tokenisation is
#: part of the contract -- text that is *not* ASCII. Python's ``\w`` is not
#: Java's ``\w``, and the corpus this runs over is scientific prose.
ANALYSIS_TEXTS: tuple[str, ...] = (
    "Reef census finds a decline in coral cover.",
    "# Findings\nThe reef census was conducted in 2019.\n\n- Cover fell by 12%.\n- Bleaching rose.",
    "Café déjàshown: naïve résumé façade. 日本語のテキスト。",
    "Mixed CASE and camelCase and snake_case identifiers.",
    "1. numbered item\n2. another numbered item",
)


def _analysis_python() -> Any:
    from lmeditor._reference import discovery_source_analysis as analysis

    answers: dict[str, Any] = {}
    for text in ANALYSIS_TEXTS:
        answers[text] = {
            "tokens": analysis.tokenize(text),
            "content_tokens": analysis.content_tokens(text),
            "salient_terms": analysis.salient_terms(text),
            "sentences": analysis.split_sentences(text),
            "findings": analysis.extract_findings(text),
            "headings": analysis.detect_headings(text),
        }
    answers["__across__"] = {
        "salient_terms": analysis.salient_terms_across(list(ANALYSIS_TEXTS)),
        "title": analysis.title_case_terms(["reef", "census", "coral", "cover"]),
    }
    answers["__clusters__"] = analysis.cluster_by_terms(
        [
            "the reef census found coral cover falling",
            "the reef census found coral cover falling",
            "a separate survey of the harbour sediment",
        ],
        2,
    )
    return answers


def _analysis_kotlin(bridge: KotlinBridge) -> Any:
    answers: dict[str, Any] = {}
    for text in ANALYSIS_TEXTS:
        tokens = bridge.call("discovery.source.tokenize", {"text": text})
        content = bridge.call("discovery.source.contentTokens", {"text": text})
        salient = bridge.call("discovery.source.salientTerms", {"text": text})
        sentences = bridge.call("discovery.source.splitSentences", {"text": text})
        findings = bridge.call("discovery.source.extractFindings", {"text": text})
        headings = bridge.call("discovery.source.detectHeadings", {"text": text})
        answers[text] = {
            "tokens": tokens.get("tokens"),
            "content_tokens": content.get("tokens"),
            "salient_terms": salient.get("terms"),
            "sentences": sentences.get("sentences"),
            "findings": findings.get("findings"),
            "headings": headings.get("headings"),
        }
    across = bridge.call(
        "discovery.source.salientTermsAcross", {"texts": list(ANALYSIS_TEXTS)}
    )
    title = bridge.call(
        "discovery.source.titleCaseTerms",
        {"terms": ["reef", "census", "coral", "cover"]},
    )
    answers["__across__"] = {
        "salient_terms": across.get("terms"),
        "title": title.get("title"),
    }
    clusters = bridge.call(
        "discovery.source.clusterByTerms",
        {
            "texts": [
                "the reef census found coral cover falling",
                "the reef census found coral cover falling",
                "a separate survey of the harbour sediment",
            ],
            "num_clusters": 2,
        },
    )
    answers["__clusters__"] = clusters.get("clusters")
    return answers


# --------------------------------------------------------------------------
# The web evidence: a URL normaliser, a tolerant result parser, a page reader.
# --------------------------------------------------------------------------

#: The hrefs the normaliser is asked about, and the result page it is asked to
#: parse. The page is the shape a search endpoint actually returns.
WEB_HREFS: tuple[str, ...] = (
    "https://reefs.example.org/census",
    "/relative/path",
    "//other.example.org/protocol-relative",
    "  https://spaced.example.org/page  ",
    "not a url at all",
    "",
)

WEB_RESULT_PAGE: bytes = b"""<html><body>
<div class="result">
  <a class="result__a" href="https://reefs.example.org/census">Reef census</a>
  <a class="result__a" href="/relative/second">Second result</a>
</div>
</body></html>"""

WEB_PAGE: bytes = (
    b"<html><head><title>Ignored</title><style>p{color:red}</style></head>"
    b"<body><h1>Reef census</h1><p>Cover fell by 12%.</p></body></html>"
)


def _web_python() -> Any:
    from lmeditor._reference import factcheck_web as web

    # The witness prefers ``trafilatura`` and falls back to stripping the tags
    # when it is not installed. The tier has no third-party dependencies, so
    # it implements the *fallback* -- and the fallback is therefore the rule
    # that is on both sides. The witness is asked for it explicitly: a module
    # entry of ``None`` makes ``import trafilatura`` raise, which is how
    # CPython lets a caller veto an optional dependency.
    saved = sys.modules.get("trafilatura")
    sys.modules["trafilatura"] = None  # type: ignore[assignment]
    try:
        text = web.extract_page_text(WEB_PAGE)
    finally:
        if saved is None:
            sys.modules.pop("trafilatura", None)
        else:
            sys.modules["trafilatura"] = saved

    return {
        "urls": {href: web.normalise_result_url(href) for href in WEB_HREFS},
        "results": [
            {"url": result.url, "title": result.title, "snippet": result.snippet}
            for result in web.parse_search_results(WEB_RESULT_PAGE)
        ],
        "text": text,
    }


def _web_kotlin(bridge: KotlinBridge) -> Any:
    urls: dict[str, Any] = {}
    for href in WEB_HREFS:
        result = bridge.call("factcheck.web.normalise_result_url", {"href": href})
        urls[href] = result.get("url")
    parsed = bridge.call(
        "factcheck.web.parse_search_results",
        {"body": encode_bytes(WEB_RESULT_PAGE)},
    )
    extracted = bridge.call(
        "factcheck.web.extract_page_text",
        {"body": encode_bytes(WEB_PAGE)},
    )
    return {
        "urls": urls,
        "results": [
            {
                "url": result.get("url"),
                "title": result.get("title"),
                "snippet": result.get("snippet"),
            }
            for result in parsed.get("results") or []
        ],
        "text": extracted.get("text"),
    }


# --------------------------------------------------------------------------
# The bibliography: the offline resolver, which reads the metadata a PDF
# carries and never makes a request.
# --------------------------------------------------------------------------

#: The metadata a PDF's own info dictionary would carry.
PDF_METADATA: dict[str, Any] = {
    "title": "Reef census findings",
    "author": "Ada Lovelace; Charles Babbage",
    "doi": "10.1234/reef.5678",
    "keywords": "reef, coral",
}


def _bibliography_python() -> Any:
    from lmeditor._reference.ingestion_resolvers import PdfHeaderBibliographyResolver
    from lmeditor.core.types import SourceKind

    resolver = PdfHeaderBibliographyResolver()
    record = resolver.resolve(
        source_id="src_parity",
        source_kind=SourceKind.PDF,
        pdf_metadata=PDF_METADATA,
    )
    if record is None:
        return None
    return {
        "title": record.title,
        "authors": list(record.authors),
        "doi": record.doi,
        "resolver": record.resolver,
    }


def _bibliography_kotlin(bridge: KotlinBridge) -> Any:
    result = bridge.call(
        "ingest.bibliography.resolve",
        {
            "resolver": "pdf-header",
            "source_id": "src_parity",
            "source_kind": "pdf",
            "pdf_metadata": PDF_METADATA,
        },
    )
    record = result.get("record")
    if not isinstance(record, dict):
        return None
    return {
        "title": record.get("title"),
        "authors": list(record.get("authors") or []),
        "doi": record.get("doi"),
        "resolver": record.get("resolver"),
    }


#: The corpus the retrieval rule is asked about. The passages are the same
#: ones the outline rule is asked about, so the two checks share a vocabulary.
RETRIEVAL_TEXTS: tuple[str, ...] = (
    "Retrieval augmented generation grounds a language model in sources.",
    "Chunking decides which passage a citation marker points at.",
    "An entailment model judges whether a claim follows from a passage.",
    "BM25 scores a document by its term frequencies and its length.",
    "Reciprocal rank fusion merges two ranked lists into one.",
)

#: The queries the retrieval rule is asked. The last one matches nothing, so
#: the sparse half has to answer with an empty list rather than with the
#: whole corpus.
RETRIEVAL_QUERIES: tuple[str, ...] = (
    "citation passage",
    "ranked lists",
    "BM25 term frequencies",
    "language model",
    "zebra",
)

#: The claims and the passages the supporting-text rule is asked about. They
#: cover a full match, a partial one, a single shared word, a run of function
#: words, no match at all, an empty text, and a text with a non-breaking
#: space in it.
SUPPORTING_TEXT_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "Retrieval augmented generation grounds a language model in sources.",
        "The retrieval pipeline grounds the model in its sources.",
    ),
    ("the model is grounded", "the model is grounded in evidence"),
    ("a claim about nothing", "a passage about something else"),
    ("single", "single word here"),
    ("it is the", "it is the one that matters"),
    ("the quick brown fox and the lazy dog", "the lazy dog sleeps"),
    ("alpha beta gamma", "alpha beta omega"),
    ("Ünicödé wörds 123", "Ünicödé wörds 123 here"),
    ("alpha\u00a0beta", "alpha\u00a0beta gamma"),
    ("", "some text"),
    ("some text", ""),
)

#: The tolerance the retrieval scores are compared with.
#:
#: Python's ``math.log`` and Java's ``Math.log`` are both allowed to be one
#: unit in the last place away from the true logarithm, and they are not
#: always the same one. Measured over the 45,150 inverse document
#: frequencies of every corpus of one to three hundred documents, 92 of them
#: -- 0.20% -- differ, and every one of those differences is exactly one
#: unit in the last place. The BM25 score is built on the logarithm, so the
#: two tiers can disagree in the last bits of a score.
#:
#: On the corpus below the two sides happen to agree bit for bit -- all 56
#: scores of this check are identical -- because every inverse document
#: frequency it uses is one of the 99.8% that agree. That is luck, not a
#: guarantee, and the check must not be brittle about it.
#:
#: The *ranking* is therefore compared exactly -- the order of the chunk ids
#: has to be the same, or the answer is a different answer -- and the
#: *scores* are compared with a tolerance that is far above the disagreement
#: and far below any difference that could change the ranking. The dense and
#: the fused scores do not involve a logarithm at all and agree exactly.
SCORE_TOLERANCE: float = 1e-12


def _retrieval_corpus() -> list[SourceChunk]:
    """The passages the retrieval rule is asked to rank."""
    return [
        SourceChunk(
            chunk_id=f"{index:064d}",
            document_id="doc-parity",
            text=text,
            char_start=0,
            char_end=len(text),
            byte_start=0,
            byte_end=len(text),
            token_count=8,
        )
        for index, text in enumerate(RETRIEVAL_TEXTS, start=1)
    ]


def _retrieval_python() -> Any:
    """Ask the frozen retrieval rule to rank the corpus."""
    from lmeditor._reference.provenance_indexer import (
        BM25Index,
        DenseVectorIndex,
        HybridIndexer,
    )

    chunks = _retrieval_corpus()
    bm25 = BM25Index()
    bm25.index(chunks)
    dense = DenseVectorIndex(enable_semantic=False)
    dense.index(chunks)
    hybrid = HybridIndexer()
    hybrid.index(chunks)
    return {
        "bm25": {query: list(bm25.search(query, top_k=5)) for query in RETRIEVAL_QUERIES},
        "dense": {query: list(dense.search(query, top_k=5)) for query in RETRIEVAL_QUERIES},
        "hybrid": {
            query: [
                [chunk.chunk_id, score] for chunk, score in hybrid.search(query, top_k=5)
            ]
            for query in RETRIEVAL_QUERIES
        },
    }


def _retrieval_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the Kotlin tier to rank the same corpus the same way."""
    chunks = _retrieval_corpus()
    answers: dict[str, Any] = {"bm25": {}, "dense": {}, "hybrid": {}}
    for mode in ("bm25", "dense", "hybrid"):
        for query in RETRIEVAL_QUERIES:
            result = bridge.call(
                "text.retrieval.rank",
                {
                    "chunks": [
                        {"chunk_id": chunk.chunk_id, "text": chunk.text}
                        for chunk in chunks
                    ],
                    "query": query,
                    "mode": mode,
                    "top_k": 5,
                },
            )
            answers[mode][query] = [
                [entry["chunk_id"], entry["score"]]
                for entry in result.get("results", [])
            ]
    return answers


def _retrieval_normalise(value: Any) -> Any:
    """Put both sides in one shape: ``mode -> query -> [[chunk_id, score]]``."""
    if not isinstance(value, dict):
        return value
    normalised: dict[str, dict[str, list[list[Any]]]] = {}
    for mode, queries in value.items():
        if not isinstance(queries, dict):
            return value
        normalised[mode] = {}
        for query, entries in queries.items():
            normalised[mode][query] = [
                [entry[0], float(entry[1])] for entry in entries
            ]
    return normalised


def _retrieval_compare(left: Any, right: Any) -> bool:
    """The ranking has to be the same; the scores may differ by an ULP.

    See :data:`SCORE_TOLERANCE` for why the scores are not compared exactly.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left == right
    if set(left) != set(right):
        return False
    for mode in left:
        queries = left[mode]
        other_queries = right[mode]
        if not isinstance(queries, dict) or not isinstance(other_queries, dict):
            return False
        if set(queries) != set(other_queries):
            return False
        for query in queries:
            entries = queries[query]
            other_entries = other_queries[query]
            if [entry[0] for entry in entries] != [entry[0] for entry in other_entries]:
                return False
            for entry, other in zip(entries, other_entries):
                if abs(float(entry[1]) - float(other[1])) > SCORE_TOLERANCE:
                    return False
    return True


def _supporting_text_python() -> Any:
    """Ask the frozen supporting-text rule about every pair."""
    from lmeditor._reference.provenance_indexer import supporting_text

    return [
        [quote, coverage]
        for quote, coverage in (
            supporting_text(claim, chunk) for claim, chunk in SUPPORTING_TEXT_PAIRS
        )
    ]


def _supporting_text_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the Kotlin tier about the same pairs."""
    answers: list[list[Any]] = []
    for claim, chunk in SUPPORTING_TEXT_PAIRS:
        result = bridge.call(
            "text.lineage.supporting_text",
            {"claim_text": claim, "chunk_text": chunk},
        )
        answers.append([result.get("quote"), float(result.get("coverage", 0.0))])
    return answers


def _supporting_text_normalise(value: Any) -> Any:
    """Put both sides in one shape: a list of ``[quote, coverage]`` pairs."""
    if not isinstance(value, list):
        return value
    normalised: list[list[Any]] = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return value
        normalised.append([entry[0], float(entry[1])])
    return normalised


#: The passages the claim-lineage rule is asked about. The third one carries
#: an astral character, so the offsets the rule reports have to be read in
#: code points on both sides.
CLAIM_LINEAGE_TEXTS: tuple[str, ...] = (
    "Coral reefs shelter a quarter of all marine species and their larvae.",
    "Mangrove forests slow storm surge along tropical coasts.",
    "Rocket \U0001F680 launches the retrieval pipeline on every claim.",
)

#: The claims the rule is asked about. Each case is the claim's id, its text,
#: the draft it was written in (``None`` for the claim itself), the offset it
#: starts at, the offset it ends at (``None`` for "as far as the claim goes"),
#: and how many candidates are considered.
#:
#: The cases cover a claim a passage supports, a claim no passage supports, a
#: claim written in a draft that holds an astral character, a claim whose span
#: runs past the end of that draft, an explicit span, and a claim that is
#: attributed to its best candidate alone.
CLAIM_LINEAGE_CASES: tuple[tuple[str, str, str | None, int, int | None, int], ...] = (
    (
        "claim-1",
        "Coral reefs shelter a quarter of all marine species",
        None,
        0,
        None,
        5,
    ),
    (
        "claim-2",
        "Mangrove forests slow storm surge along tropical coasts",
        None,
        0,
        None,
        5,
    ),
    (
        "claim-3",
        "Volcanic ash fertilises the soil of Andean valleys",
        None,
        0,
        None,
        5,
    ),
    (
        "claim-4",
        "Coral reefs shelter a quarter of all marine species",
        "\U0001F680 Coral reefs shelter a quarter of all marine species",
        2,
        None,
        5,
    ),
    (
        "claim-5",
        "Coral reefs shelter a quarter of all marine species",
        "\U0001F680 Coral reefs shelter a quarter of",
        0,
        None,
        5,
    ),
    (
        "claim-6",
        "Mangrove forests slow storm surge",
        None,
        0,
        10,
        5,
    ),
    (
        "claim-7",
        "Coral reefs shelter a quarter of all marine species",
        None,
        0,
        None,
        1,
    ),
)


def _claim_lineage_corpus() -> list[SourceChunk]:
    """The passages the claim-lineage rule is asked about."""
    return [
        SourceChunk(
            chunk_id=f"{index:064d}",
            document_id="doc-parity",
            text=text,
            char_start=0,
            char_end=len(text),
            byte_start=0,
            byte_end=len(text.encode("utf-8")),
            token_count=8,
        )
        for index, text in enumerate(CLAIM_LINEAGE_TEXTS, start=1)
    ]


def _claim_lineage_view(lineage: Any) -> dict[str, Any]:
    """The part of a lineage record both tiers are compared on.

    The identifier and the timestamp are left out on purpose: the frozen
    engine mints a random identifier and the tier mints a deterministic one,
    and neither the identifier nor the moment a record was made is part of the
    rule.
    """
    return {
        "claim_id": lineage.claim_id,
        "chunk_id": lineage.chunk_id,
        "span_start": lineage.span_start,
        "span_end": lineage.span_end,
        "exact_quote": lineage.exact_quote,
        "confidence_score": lineage.confidence_score,
    }


def _claim_lineage_view_wire(lineage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": lineage.get("claim_id"),
        "chunk_id": lineage.get("chunk_id"),
        "span_start": lineage.get("span_start"),
        "span_end": lineage.get("span_end"),
        "exact_quote": lineage.get("exact_quote"),
        "confidence_score": lineage.get("confidence_score"),
    }


def _claim_lineage_python() -> Any:
    """Ask the frozen composition to attribute every claim."""
    from lmeditor._reference.provenance_claim_lineage import ClaimLineageIndex

    index = ClaimLineageIndex(_claim_lineage_corpus())
    answers: list[list[dict[str, Any]]] = []
    for (
        claim_id,
        claim_text,
        draft_text,
        span_start,
        span_end,
        top_k,
    ) in CLAIM_LINEAGE_CASES:
        lineages = index.lineage_for_claim(
            claim_id,
            claim_text,
            draft_text=draft_text,
            span_start=span_start,
            span_end=span_end,
            top_k=top_k,
        )
        answers.append([_claim_lineage_view(lineage) for lineage in lineages])
    return answers


def _claim_lineage_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the Kotlin tier to attribute the same claims the same way."""
    chunks = _claim_lineage_corpus()
    answers: list[list[dict[str, Any]]] = []
    for (
        claim_id,
        claim_text,
        draft_text,
        span_start,
        span_end,
        top_k,
    ) in CLAIM_LINEAGE_CASES:
        params: dict[str, Any] = {
            "claim_id": claim_id,
            "claim_text": claim_text,
            "chunks": [
                {"chunk_id": chunk.chunk_id, "text": chunk.text} for chunk in chunks
            ],
            "span_start": span_start,
            "top_k": top_k,
        }
        if draft_text is not None:
            params["draft_text"] = draft_text
        if span_end is not None:
            params["span_end"] = span_end
        result = bridge.call("text.lineage.attribute_claim", params)
        answers.append(
            [_claim_lineage_view_wire(item) for item in result.get("lineages") or []]
        )
    return answers


def _claim_lineage_normalise(value: Any) -> Any:
    """Put both sides in one shape: a list of cases, each a list of records."""
    if not isinstance(value, list):
        return value
    normalised: list[list[dict[str, Any]]] = []
    for case in value:
        if not isinstance(case, (list, tuple)):
            return value
        records: list[dict[str, Any]] = []
        for record in case:
            if not isinstance(record, dict):
                return value
            records.append(
                {
                    "claim_id": record.get("claim_id"),
                    "chunk_id": record.get("chunk_id"),
                    "span_start": record.get("span_start"),
                    "span_end": record.get("span_end"),
                    "exact_quote": record.get("exact_quote"),
                    "confidence_score": float(record.get("confidence_score") or 0.0),
                }
            )
        normalised.append(records)
    return normalised


def _claim_lineage_compare(left: Any, right: Any) -> bool:
    """The records have to be the same; the confidence may differ by an ULP.

    The *order* of the records is the ranking, and the ranking is compared
    exactly: the claim id, the chunk id, the span and the quote are all
    compared exactly. The confidence is a float on both sides -- it is the
    engine's own score scaled by the coverage and rounded to three decimals
    -- and the two tiers round it with the same rule, so on this corpus they
    agree bit for bit; the tolerance keeps the check honest if a future
    rounding ever differs by a unit in the last place.
    """
    if not isinstance(left, list) or not isinstance(right, list):
        return left == right
    if len(left) != len(right):
        return False
    for records, other_records in zip(left, right):
        if len(records) != len(other_records):
            return False
        for record, other in zip(records, other_records):
            for key in ("claim_id", "chunk_id", "span_start", "span_end", "exact_quote"):
                if record.get(key) != other.get(key):
                    return False
            confidence = abs(
                float(record.get("confidence_score", 0.0))
                - float(other.get("confidence_score", 0.0))
            )
            if confidence > SCORE_TOLERANCE:
                return False
    return True


#: The caps that bound every byte that crosses the ingestion boundary. Each
#: pair is the name the frozen witness gives a cap and the key the seam gives
#: the same cap, so the comparison is by identity rather than by position.
LIMIT_NAMES: tuple[tuple[str, str], ...] = (
    ("MAX_UPLOAD_BYTES", "max_upload_bytes"),
    ("MAX_URL_RESPONSE_BYTES", "max_url_response_bytes"),
    ("MAX_RESOLVER_RESPONSE_BYTES", "max_resolver_response_bytes"),
    ("MAX_SOURCE_STORE_BYTES", "max_source_store_bytes"),
)


def _limits_python() -> Any:
    """Return the four caps of the frozen witness, keyed as the seam keys them."""
    from lmeditor._reference import ingestion_limits as limits

    return {wire: getattr(limits, name) for name, wire in LIMIT_NAMES}


def _limits_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the tier for the same four caps.

    The seam answers with the caps and the size it accepted, so the size is
    zero: the point of the check is the caps, not the refusal.
    """
    answer = bridge.call("ingest.limits", {"size": 0})
    return {wire: answer.get(wire) for _, wire in LIMIT_NAMES}


#: The briefs the two tiers are asked to merge. They cover the ordinary merge,
#: an empty addition, an empty current brief, a merge whose result overflows
#: the budget, a single line that is longer than the budget on its own, and a
#: budget of zero.
BRIEF_CASES: tuple[tuple[str, str, int], ...] = (
    ("Book: A\nSections: 2", "1. Introduction\n2. Method", 2000),
    ("", "1. Introduction", 2000),
    ("Book: A\nSections: 2", "", 2000),
    ("Book: A\nSections: 2", "  1. Introduction  ", 2000),
    ("line one\nline two\nline three", "line four", 12),
    ("only one very long line indeed", "another", 10),
    ("Book: A", "Section", 0),
)


def _brief_case_key(current: str, addition: str, max_chars: int) -> str:
    """Return the key one merge case is reported under."""
    return f"{current}|{addition}|{max_chars}"


def _brief_python() -> Any:
    """Return the merged briefs of the frozen witness, keyed by case."""
    from lmeditor._reference import library_brief as brief

    return {
        _brief_case_key(current, addition, max_chars): brief.merge_brief(
            current, addition, max_chars=max_chars
        )
        for current, addition, max_chars in BRIEF_CASES
    }


def _brief_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the tier to merge the same briefs."""
    answers: dict[str, Any] = {}
    for current, addition, max_chars in BRIEF_CASES:
        answer = bridge.call(
            "library.merge_brief",
            {"current": current, "addition": addition, "max_chars": max_chars},
        )
        answers[_brief_case_key(current, addition, max_chars)] = answer.get("brief", "")
    return answers


#: The name of the error the tier raises when the policy rule refuses a source
#: id or a file name.
#:
#: The refusal is matched by *name* and not by class: the Python class that
#: carries it lives in a door (``lmeditor.ingestion.url_policy``), and a harness
#: that imports a door would be comparing the tier with itself.
REFUSAL_ERROR_NAME = "UnsafeSourceError"


#: The source ids the policy rule is asked about. They cover an ordinary id, the
#: length boundary, a blank id, a control character, the names Windows reserves
#: for its own devices, the names that end in a space or a dot, and the names
#: that carry a separator.
SOURCE_ID_CASES: tuple[str, ...] = (
    "src-001",
    "SRC_002",
    "a" * 128,
    "a" * 129,
    "",
    "   ",
    "src\x00id",
    "src\x1fid",
    "src\x7fid",
    "src\nid",
    "src\tid",
    "NUL",
    "nul",
    "NUL.txt",
    "nul.pdf",
    "CON",
    "PRN",
    "AUX",
    "CLOCK$",
    "COM1",
    "LPT9",
    "NULL",
    "COM0",
    "CONIN",
    "src ",
    "src.",
    " src",
    "src.txt",
    "-src",
    "src/../evil",
    "src\\..\\evil",
    "src:with:colon",
    "a/b",
)


#: The upload names the policy rule is asked about, each with the source id it
#: is uploaded under. They cover an ordinary name, a name that arrives with a
#: path, the names that carry a control character, the names that name a device
#: Windows reserves, the names that end in a space or a dot, and the name that
#: is too long.
UPLOAD_NAME_CASES: tuple[tuple[str, str], ...] = (
    ("src-001", "notes.txt"),
    ("src-001", "a/b/notes.txt"),
    ("src-001", "C:\\Windows\\x.txt"),
    ("src-001", "/etc/passwd"),
    ("src-001", ".."),
    ("src-001", "."),
    ("src-001", " "),
    ("src-001", ""),
    ("src-001", "notes.txt "),
    ("src-001", "notes.txt."),
    ("src-001", "NUL"),
    ("src-001", "nul.txt"),
    ("src-001", "CON.pdf"),
    ("src-001", "notes\x00.txt"),
    ("src-001", "notes\x1f.txt"),
    ("src-001", "x" * 300),
    ("NUL", "notes.txt"),
)


#: The root the upload-path cases are resolved under. Both tiers are told the
#: same root, so the two answers are about the same directory.
UPLOAD_BASE = "uploads"


def _upload_case_key(source_id: str, filename: str) -> str:
    """Return the key one upload-path case is reported under."""
    return f"{source_id}|{filename}"


def _path_tail(path: str) -> str:
    """Return the last two components of a path, whatever separator it uses.

    Only the tail is compared: the root is a directory the two runtimes spell
    differently, and the rule under test is the one that puts the source id and
    the file name under it.
    """
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    return "/".join(parts[-2:])


def _url_policy_python() -> Any:
    """Return the answers the frozen witness gives to the policy rule."""
    from lmeditor._reference import ingestion_url_policy as policy

    ids: dict[str, dict[str, Any]] = {}
    for source_id in SOURCE_ID_CASES:
        try:
            ids[source_id] = {"accepted": True, "id": policy.validate_source_id(source_id)}
        except policy.UnsafeSourceError:
            ids[source_id] = {"accepted": False, "id": None}

    uploads: dict[str, dict[str, Any]] = {}
    for source_id, filename in UPLOAD_NAME_CASES:
        try:
            path = policy.safe_upload_path(source_id, filename, base=Path(UPLOAD_BASE))
        except policy.UnsafeSourceError:
            uploads[_upload_case_key(source_id, filename)] = {"accepted": False, "tail": None}
        else:
            uploads[_upload_case_key(source_id, filename)] = {
                "accepted": True,
                "tail": _path_tail(str(path)),
            }
    return {"ids": ids, "uploads": uploads}


def _url_policy_kotlin(bridge: KotlinBridge) -> Any:
    """Ask the tier the same questions and record the same answers.

    A refusal comes back from the seam as an error rather than as an answer, so
    it is caught here and recorded as a refusal. Every other error is re-raised:
    it says that the seam did not answer, which is not a disagreement about the
    rule.
    """
    ids: dict[str, dict[str, Any]] = {}
    for source_id in SOURCE_ID_CASES:
        try:
            answer = bridge.call(
                "ingest.url_policy.validate_source_id", {"source_id": source_id}
            )
        except Exception as exc:  # the refusal is recorded, not swallowed
            if type(exc).__name__ != REFUSAL_ERROR_NAME:
                raise
            ids[source_id] = {"accepted": False, "id": None}
        else:
            ids[source_id] = {"accepted": True, "id": answer.get("source_id")}

    uploads: dict[str, dict[str, Any]] = {}
    for source_id, filename in UPLOAD_NAME_CASES:
        try:
            answer = bridge.call(
                "ingest.url_policy.safe_upload_path",
                {"source_id": source_id, "filename": filename, "base": UPLOAD_BASE},
            )
        except Exception as exc:  # the refusal is recorded, not swallowed
            if type(exc).__name__ != REFUSAL_ERROR_NAME:
                raise
            uploads[_upload_case_key(source_id, filename)] = {"accepted": False, "tail": None}
        else:
            uploads[_upload_case_key(source_id, filename)] = {
                "accepted": True,
                "tail": _path_tail(str(answer.get("path"))),
            }
    return {"ids": ids, "uploads": uploads}


CHECKS: tuple[Check, ...] = (
    Check(
        name="chunk digest",
        method="core.schemas.source_chunk_create",
        python=_chunk_digest_python,
        kotlin_params=_chunk_digest_params,
        normalise=lambda value: value.get("chunk", {}).get("chunk_id")
        if isinstance(value, dict)
        else value,
    ),
    Check(
        name="citation grammar",
        method="core.citations.*",
        python=_citations_python,
        kotlin_params=_citations_params,
        kotlin=_citations_kotlin,
        normalise=_citations_normalise,
    ),
    Check(
        name="operator edit rule",
        method="core.sections.merge_operator_text",
        python=_operator_edit_python,
        kotlin_params=_operator_edit_params,
        normalise=_operator_edit_normalise,
    ),
    Check(
        name="chunking",
        method="text.chunk",
        python=_chunking_python,
        kotlin_params=lambda: {},
        kotlin=_chunking_kotlin,
    ),
    Check(
        name="claim extraction",
        method="text.claims.extract",
        python=_claims_python,
        kotlin_params=lambda: {},
        kotlin=_claims_kotlin,
    ),
    Check(
        name="workflow routing",
        method="workflow.route",
        python=_routing_python,
        kotlin_params=lambda: {},
        kotlin=_routing_kotlin,
    ),
    Check(
        name="workflow state",
        method="workflow.create_initial_state",
        python=_workflow_state_python,
        kotlin_params=lambda: {},
        kotlin=_workflow_state_kotlin,
    ),
    Check(
        name="citation metrics",
        method="text.citation_check.*",
        python=_citation_metrics_python,
        kotlin_params=lambda: {},
        kotlin=_citation_metrics_kotlin,
    ),
    Check(
        name="premise resolution",
        method="text.citation_check.resolve_premises",
        python=_premises_python,
        kotlin_params=lambda: {},
        kotlin=_premises_kotlin,
    ),
    Check(
        name="lineage attribution",
        method="text.lineage.*",
        python=_lineage_python,
        kotlin_params=lambda: {},
        kotlin=_lineage_kotlin,
    ),
    Check(
        name="retrieval ranking",
        method="text.retrieval.rank",
        python=_retrieval_python,
        kotlin_params=lambda: {},
        kotlin=_retrieval_kotlin,
        normalise=_retrieval_normalise,
        compare=_retrieval_compare,
    ),
    Check(
        name="supporting text",
        method="text.lineage.supporting_text",
        python=_supporting_text_python,
        kotlin_params=lambda: {},
        kotlin=_supporting_text_kotlin,
        normalise=_supporting_text_normalise,
    ),
    Check(
        name="claim lineage",
        method="text.lineage.attribute_claim",
        python=_claim_lineage_python,
        kotlin_params=lambda: {},
        kotlin=_claim_lineage_kotlin,
        normalise=_claim_lineage_normalise,
        compare=_claim_lineage_compare,
    ),
    Check(
        name="outline construction",
        method="discovery.outline",
        python=_outline_python,
        kotlin_params=lambda: {},
        kotlin=_outline_kotlin,
    ),
    Check(
        name="persona discovery",
        method="discovery.perspectives",
        python=_perspectives_python,
        kotlin_params=lambda: {},
        kotlin=_perspectives_kotlin,
    ),
    Check(
        name="word reading",
        method="word.read",
        python=_word_read_python,
        kotlin_params=lambda: {},
        kotlin=_word_read_kotlin,
        normalise=_normalise_timestamps,
    ),
    Check(
        name="word writing",
        method="word.write",
        python=_word_write_python,
        kotlin_params=lambda: {},
        kotlin=_word_write_kotlin,
        normalise=_normalise_written,
    ),
    Check(
        name="manuscript building",
        method="word.manuscript",
        python=_manuscript_python,
        kotlin_params=lambda: {},
        kotlin=_manuscript_kotlin,
    ),
    Check(
        name="the source library",
        method="server.sources.list",
        python=_server_sources_python,
        kotlin_params=lambda: {},
        kotlin=_server_sources_kotlin,
        normalise=_normalise_timestamps,
    ),
    Check(
        name="the source analysis",
        method="discovery.source.*",
        python=_analysis_python,
        kotlin_params=lambda: {},
        kotlin=_analysis_kotlin,
    ),
    Check(
        name="the web evidence",
        method="factcheck.web.*",
        python=_web_python,
        kotlin_params=lambda: {},
        kotlin=_web_kotlin,
    ),
    Check(
        name="the bibliography resolver",
        method="ingest.bibliography.resolve",
        python=_bibliography_python,
        kotlin_params=lambda: {},
        kotlin=_bibliography_kotlin,
    ),
    Check(
        name="the ingestion limits",
        method="ingest.limits",
        python=_limits_python,
        kotlin_params=lambda: {},
        kotlin=_limits_kotlin,
    ),
    Check(
        name="the bounded brief",
        method="library.merge_brief",
        python=_brief_python,
        kotlin_params=lambda: {},
        kotlin=_brief_kotlin,
    ),
    Check(
        name="the source-id policy",
        method="ingest.url_policy.*",
        python=_url_policy_python,
        kotlin_params=lambda: {},
        kotlin=_url_policy_kotlin,
    ),
)


@dataclass(frozen=True)
class Witness:
    """What holds one frozen module to its Kotlin counterpart.

    Exactly one of the two fields is set. A module a check compares names the
    checks that compare it. A module no check compares yet names the Kotlin
    file that holds its counterpart, so the gap is a place with an address
    rather than an absence.
    """

    #: The names of the checks that compare the module.
    checks: tuple[str, ...] = ()
    #: The Kotlin file, relative to ``kotlin/``, that holds the counterpart.
    counterpart: str = ""


#: Every frozen module of ``lmeditor._reference``, and what witnesses it.
#:
#: The ledger is exhaustive on purpose. :func:`_check_ledger` fails the run
#: when a module is missing from it, when a module names a check that does not
#: exist, when a module is declared as compared but the harness does not import
#: it, when the harness imports a module it does not declare, and when the
#: Kotlin file named as a counterpart is not in the tree. A rule that no check
#: compares is therefore a *declared* gap, with its counterpart named, and not
#: a silent one.
#:
#: The thirteen modules with no check all *have* a Kotlin counterpart -- the
#: seam reached every one of them -- but no check compares the two sides yet,
#: and each one names the Kotlin file that holds its rule. Two reasons
#: account for all thirteen: the rule is reachable only through a method that
#: writes to disk (the library, the upload store, the export), or the two tiers
#: read the artifact with different parsers (the loaders, the PDF metadata).
#:
#: ``ingestion_url_policy`` was the fourteenth: the tier was deliberately
#: stricter than the frozen witness -- it refused source ids the witness still
#: accepted, so a comparison would have reported that hardening as drift. The
#: witness now carries the same refusals, and the check ``the source-id
#: policy`` compares the two sides.
REFERENCE_WITNESSES: Mapping[str, Witness] = {
    "chunker": Witness(checks=("chunking",)),
    "citation_checker": Witness(checks=("citation metrics", "premise resolution")),
    "citations": Witness(checks=("citation grammar",)),
    "claim_extractor": Witness(checks=("claim extraction",)),
    "discovery_dialogue": Witness(
        counterpart="discovery/src/main/kotlin/lmeditor/discovery/Dialogue.kt"
    ),
    "discovery_source_analysis": Witness(checks=("the source analysis",)),
    "edges": Witness(checks=("workflow routing",)),
    "editor": Witness(
        counterpart="editing/src/main/kotlin/lmeditor/editing/InlineEditor.kt"
    ),
    "factcheck_service": Witness(
        counterpart="factcheck/src/main/kotlin/lmeditor/factcheck/FactCheckService.kt"
    ),
    "factcheck_web": Witness(checks=("the web evidence",)),
    "ingestion_http_safety": Witness(
        counterpart="ingest/src/main/kotlin/lmeditor/ingest/HttpSafety.kt"
    ),
    "ingestion_limits": Witness(checks=("the ingestion limits",)),
    "ingestion_loaders": Witness(
        counterpart="ingest/src/main/kotlin/lmeditor/ingest/Loaders.kt"
    ),
    "ingestion_pdf_metadata": Witness(
        counterpart="pdf/src/main/kotlin/lmeditor/pdf/PdfMetadata.kt"
    ),
    "ingestion_registry": Witness(
        counterpart="ingest/src/main/kotlin/lmeditor/ingest/LoaderRegistry.kt"
    ),
    "ingestion_resolvers": Witness(checks=("the bibliography resolver",)),
    "ingestion_service": Witness(
        counterpart="ingest/src/main/kotlin/lmeditor/ingest/IngestionService.kt"
    ),
    "ingestion_source_store": Witness(
        counterpart="ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt"
    ),
    "ingestion_url_policy": Witness(checks=("the source-id policy",)),
    "library_brief": Witness(checks=("the bounded brief",)),
    "library_export": Witness(
        counterpart="library/src/main/kotlin/lmeditor/library/BookExporter.kt"
    ),
    "library_importer": Witness(
        counterpart="library/src/main/kotlin/lmeditor/library/BookImporter.kt"
    ),
    "library_store": Witness(
        counterpart="library/src/main/kotlin/lmeditor/library/BookStore.kt"
    ),
    "library_working_set": Witness(
        counterpart="library/src/main/kotlin/lmeditor/library/WorkingSet.kt"
    ),
    "lineage": Witness(checks=("lineage attribution", "supporting text")),
    "outliner": Witness(checks=("outline construction",)),
    "perspective": Witness(checks=("persona discovery",)),
    "provenance_claim_lineage": Witness(checks=("claim lineage",)),
    "provenance_indexer": Witness(checks=("retrieval ranking", "supporting text")),
    "sections": Witness(checks=("operator edit rule",)),
    "server_sources": Witness(checks=("the source library",)),
    "state": Witness(checks=("workflow state",)),
    "word_manuscript": Witness(checks=("manuscript building",)),
    "word_reader": Witness(checks=("word reading",)),
    "word_writer": Witness(checks=("word writing",)),
}


def _reference_modules() -> list[str]:
    """Return the names of the frozen modules, in sorted order."""
    package = REPO_ROOT / "backend" / "src" / "lmeditor" / "_reference"
    return sorted(
        path.stem for path in package.glob("*.py") if path.name != "__init__.py"
    )


def _imported_reference_modules() -> set[str]:
    """Return the frozen modules this script imports.

    Read from the source rather than from ``sys.modules``: a witness that is
    imported only because another witness imports it is not a module this
    script compares, and the ledger has to say so precisely.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module == "lmeditor._reference":
            names.update(alias.name for alias in node.names)
        elif node.module.startswith("lmeditor._reference."):
            names.add(node.module.split(".", 2)[2].split(".")[0])
    return names


def _check_ledger() -> CheckResult:
    """Check that every frozen module has a witness the tree agrees with."""
    modules = _reference_modules()
    imported = _imported_reference_modules()
    check_names = {check.name for check in CHECKS}
    problems: list[str] = []

    for module in modules:
        if module not in REFERENCE_WITNESSES:
            problems.append(f"{module}: no witness is declared")
    for module in sorted(set(REFERENCE_WITNESSES) - set(modules)):
        problems.append(f"{module}: declared, but no such frozen module exists")
    for module in sorted(imported - set(REFERENCE_WITNESSES)):
        problems.append(f"{module}: is imported, but declares no witness")

    for module, witness in sorted(REFERENCE_WITNESSES.items()):
        if witness.checks and witness.counterpart:
            problems.append(f"{module}: declares both a check and a counterpart")
        elif witness.checks:
            for name in witness.checks:
                if name not in check_names:
                    problems.append(f"{module}: names the check {name!r}, which does not exist")
            if module not in imported:
                problems.append(
                    f"{module}: is declared as compared, but the harness does not import it"
                )
        elif witness.counterpart:
            if not (REPO_ROOT / "kotlin" / witness.counterpart).is_file():
                problems.append(
                    f"{module}: names the counterpart {witness.counterpart!r}, "
                    "which is not in the tree"
                )
            if module in imported:
                problems.append(
                    f"{module}: is imported, but is declared as not compared"
                )
        else:
            problems.append(f"{module}: declares neither a check nor a counterpart")

    if problems:
        return CheckResult("the frozen-module ledger", "failed", "\n".join(problems))
    return CheckResult("the frozen-module ledger", "passed")


def _run_check(bridge: KotlinBridge, check: Check) -> CheckResult:
    try:
        if check.kotlin is not None:
            kotlin_result = check.kotlin(bridge)
        else:
            kotlin_result = bridge.call(check.method, check.kotlin_params())
    except BridgeUnavailableError as exc:
        # The tier is not built (or not running). Nothing was compared, so
        # nothing disagreed: this is an absence of evidence, not evidence of
        # a difference.
        return CheckResult(check.name, "skipped", f"the Kotlin tier is unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - the type is reported, not swallowed
        if _is_missing_method(exc):
            return CheckResult(check.name, "skipped", f"{check.method} is not available yet")
        return CheckResult(check.name, "failed", f"{check.method} raised: {exc}")

    try:
        python_result = check.python()
    except Exception as exc:  # noqa: BLE001 - the type is reported, not swallowed
        return CheckResult(check.name, "failed", f"the Python side raised: {exc}")

    left = check.normalise(python_result)
    right = check.normalise(kotlin_result)
    if check.compare(left, right):
        return CheckResult(check.name, "passed")
    return CheckResult(
        check.name,
        "failed",
        "the tiers disagree:\n  python: "
        + json.dumps(left, default=str, sort_keys=True)[:2000]
        + "\n  kotlin: "
        + json.dumps(right, default=str, sort_keys=True)[:2000],
    )


def _is_missing_method(exc: Exception) -> bool:
    """Whether the failure means the Kotlin method is not implemented yet."""
    text = str(exc)
    return "not available yet" in text or "unknown method" in text.lower()


def _exit_code(results: Sequence[CheckResult]) -> int:
    """Return the exit code for a set of results.

    A skipped check makes the run fail. An absent comparison is not evidence
    of agreement, so a harness that measured nothing must not report success;
    the run is only a success when every check it claims to cover was actually
    made and both sides agreed.
    """
    return 1 if any(result.status != "passed" for result in results) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the checks without running them",
    )
    args = parser.parse_args(argv)

    if args.list:
        for check in CHECKS:
            print(f"{check.name:24s} {check.method}")
        return 0

    results: list[CheckResult] = []
    with KotlinBridge() as bridge:
        for check in CHECKS:
            result = _run_check(bridge, check)
            results.append(result)
            marker = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[result.status]
            print(f"[{marker}] {result.name}")
            if result.detail:
                print(f"       {result.detail}")

    ledger = _check_ledger()
    results.append(ledger)
    marker = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[ledger.status]
    print(f"[{marker}] {ledger.name}")
    if ledger.detail:
        print(f"       {ledger.detail}")

    failures = [result for result in results if result.status == "failed"]
    skipped = [result for result in results if result.status == "skipped"]
    agreed = len(results) - len(failures) - len(skipped)
    print()
    print(f"{agreed} agreed, {len(failures)} disagreed, {len(skipped)} skipped")
    if skipped:
        print(
            "A skipped check is not a passing check: it means the rule could not be "
            "compared, so the run counts as a failure. Build the Kotlin tier and "
            "run this again."
        )
    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
