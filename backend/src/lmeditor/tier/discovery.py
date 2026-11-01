"""The discovery rules, as the Kotlin tier implements them.

The perspectives and the outline tree are constructed in Kotlin; the model
that fills them in is Python's, and its answers travel across the seam as
plain data.

The lexical analysis of the corpus and the dialogue loop are here too: the
vocabulary of a passage, the findings its sentences state, the headings it
carries, the way its passages cluster and the turns a persona holds are all
rules, and the rules are Kotlin's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lmeditor.core.schemas import (
    OutlineNode,
    OutlineNodeInput,
    Perspective,
    ResearchMemory,
    SourceChunk,
    TopicOutline,
)
from lmeditor.core.types import PerspectiveRole
from lmeditor.tier.bridge import tier_bridge


def build_outline(
    topic: str,
    research_memory: ResearchMemory | Sequence[Any] | None = None,
    available_chunks: Sequence[SourceChunk] = (),
    perspectives: Sequence[Perspective] | None = None,
) -> TopicOutline:
    """The outline tree for a topic, with the corpus's passages assigned.

    A research memory handed over as a sequence of perspectives is a sequence
    of perspectives: the tier builds the memory from them, so the rule that
    says how a memory is made lives in one place.
    """
    params: dict[str, Any] = {
        "topic": topic,
        "chunks": [chunk.model_dump(mode="json") for chunk in available_chunks],
    }
    memory: ResearchMemory | None = None
    if isinstance(research_memory, ResearchMemory):
        memory = research_memory
    elif isinstance(research_memory, Sequence) and not isinstance(
        research_memory, (str, bytes)
    ):
        # A sequence of perspectives was passed where a memory was expected.
        # The tier builds the memory from them, so the rule that says how a
        # memory is made lives in one place.
        perspectives = list(research_memory)
    if memory is not None:
        params["research_memory"] = memory.model_dump(mode="json")
    if perspectives is not None:
        params["perspectives"] = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in perspectives
        ]
    result = tier_bridge().call("discovery.outline", params)
    return TopicOutline.model_validate(result["outline"])


def build_outline_from_sections(
    sections: Sequence[OutlineNodeInput],
    topic: str,
    available_chunks: Sequence[SourceChunk] | None = None,
) -> TopicOutline:
    """The outline tree for the sections the caller named.

    The operator's steer decides what the document covers. The passages are
    still assigned from the corpus by the tier's own rules, so the steer never
    decides what the document claims.
    """
    params: dict[str, Any] = {
        "topic": topic,
        "sections": [section.model_dump(mode="json") for section in sections],
    }
    if available_chunks:
        params["chunks"] = [chunk.model_dump(mode="json") for chunk in available_chunks]
    result = tier_bridge().call("discovery.outlineSections", params)
    return TopicOutline.model_validate(result["outline"])


def generate_perspectives(
    topic: str,
    context: str | None = None,
    num_perspectives: int = 4,
) -> list[Perspective]:
    """The personas that will interrogate the topic."""
    params: dict[str, Any] = {"topic": topic, "count": num_perspectives}
    if context is not None:
        params["context"] = context
    result = tier_bridge().call("discovery.perspectives", params)
    return [Perspective.model_validate(item) for item in result.get("perspectives", [])]


# ---------------------------------------------------------------------------
# The lexical analysis of the corpus
#
# These are the primitives the discovery layer reads a corpus with. They are
# deterministic, so they live in the tier, and the Python modules that use
# them are doors.
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """The lower-cased word tokens of ``text``."""
    result = tier_bridge().call("discovery.source.tokenize", {"text": text})
    return [str(item) for item in result.get("tokens", [])]


def content_tokens(text: str) -> list[str]:
    """The tokens of ``text`` that carry content, in order of appearance."""
    result = tier_bridge().call("discovery.source.contentTokens", {"text": text})
    return [str(item) for item in result.get("tokens", [])]


def salient_terms(text: str, limit: int = 12) -> list[str]:
    """The most distinctive vocabulary of ``text``."""
    result = tier_bridge().call(
        "discovery.source.salientTerms", {"text": text, "limit": limit}
    )
    return [str(item) for item in result.get("terms", [])]


def salient_terms_across(texts: Sequence[str], limit: int = 12) -> list[str]:
    """The salient terms of several passages at once."""
    result = tier_bridge().call(
        "discovery.source.salientTermsAcross",
        {"texts": list(texts), "limit": limit},
    )
    return [str(item) for item in result.get("terms", [])]


def split_sentences(text: str) -> list[str]:
    """``text`` split into sentences on terminal punctuation."""
    result = tier_bridge().call("discovery.source.splitSentences", {"text": text})
    return [str(item) for item in result.get("sentences", [])]


def extract_findings(text: str, limit: int = 6) -> list[str]:
    """The sentences of ``text`` that state a finding."""
    result = tier_bridge().call(
        "discovery.source.extractFindings", {"text": text, "limit": limit}
    )
    return [str(item) for item in result.get("findings", [])]


def detect_headings(text: str) -> list[str]:
    """The headings the document itself carries."""
    result = tier_bridge().call("discovery.source.detectHeadings", {"text": text})
    return [str(item) for item in result.get("headings", [])]


def title_case_terms(terms: Sequence[str]) -> str:
    """The terms joined into a readable section heading fragment."""
    result = tier_bridge().call(
        "discovery.source.titleCaseTerms", {"terms": list(terms)}
    )
    return str(result.get("title", ""))


def cluster_by_terms(texts: Sequence[str], num_clusters: int) -> list[list[int]]:
    """The indices of ``texts`` grouped into topical clusters."""
    result = tier_bridge().call(
        "discovery.source.clusterByTerms",
        {"texts": list(texts), "num_clusters": num_clusters},
    )
    return [[int(index) for index in cluster] for cluster in result.get("clusters", [])]


# ---------------------------------------------------------------------------
# The dialogue loop
#
# The retrieval is Python's -- the vector index lives there -- so the passages
# a turn saw travel across the seam as the passages themselves. The plan of
# the questions, the grounded answer and the takeaways are the tier's.
# ---------------------------------------------------------------------------


def dialogue_questions(perspective: Perspective, num_turns: int = 3) -> list[str]:
    """The questions a persona's session asks, in order."""
    result = tier_bridge().call(
        "discovery.dialogue.questions",
        {
            "perspective": perspective.model_dump(mode="json"),
            "num_turns": num_turns,
        },
    )
    return [str(item) for item in result.get("questions", [])]


def dialogue_memory(
    sessions: Sequence[tuple[Perspective, Sequence[Mapping[str, Any]]]],
) -> ResearchMemory:
    """The research memory for a run of dialogue sessions.

    Each entry pairs a persona with the turns it held. A turn names the
    question that was asked, the passages the index returned for it and, when
    a language model wrote one, the framing sentence it produced.
    """
    params: dict[str, Any] = {
        "perspectives": [
            {
                "perspective": perspective.model_dump(mode="json"),
                "turns": [dict(turn) for turn in turns],
            }
            for perspective, turns in sessions
        ]
    }
    result = tier_bridge().call("discovery.dialogue.memory", params)
    return ResearchMemory.model_validate(result["memory"])


__all__ = [
    "build_outline",
    "build_outline_from_sections",
    "cluster_by_terms",
    "content_tokens",
    "detect_headings",
    "dialogue_memory",
    "dialogue_questions",
    "extract_findings",
    "generate_perspectives",
    "salient_terms",
    "salient_terms_across",
    "split_sentences",
    "title_case_terms",
    "tokenize",
    "OutlineNode",
    "PerspectiveRole",
]
