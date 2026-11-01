"""The discovery doors must answer with the Kotlin tier's answers.

The outline tree and the persona set are rules, and the rules are Kotlin's.
``lmeditor.discovery.outliner`` and ``lmeditor.discovery.perspective`` are the
doors to them: they keep the Python API and answer with the tier's answers.
These tests hold the doors to that, and they hold the failure mode too -- a
tier that cannot be reached is an infrastructure failure, and it must never
degrade into a quiet Python answer.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

import pytest

from lmeditor.bridge.errors import BridgeUnavailableError
from lmeditor.core.schemas import (
    OutlineNode,
    Perspective,
    ResearchMemory,
    SourceChunk,
    TopicOutline,
)
from lmeditor.core.types import PerspectiveRole, ProvenanceIndexerProtocol
from lmeditor.discovery.dialogue import DialogueLoop
from lmeditor.discovery.outliner import OutlineGenerator
from lmeditor.discovery.perspective import PerspectiveGenerator
from lmeditor.tier import discovery as tier_discovery

TOPIC = "Municipal Waste"
CONTEXT = "Municipal waste collection rates fell by 30 percent between 2019 and 2023."


def _chunk(text: str) -> SourceChunk:
    """A corpus chunk with a stable id."""
    return SourceChunk(
        chunk_id=hashlib.sha256(text.encode()).hexdigest(),
        document_id="doc_1",
        text=text,
    )


CHUNKS = [
    _chunk(CONTEXT),
    _chunk("## Kerbside Collection\nKerbside recycling throughput doubled after the 2022 fleet replacement."),
]


def _memory() -> ResearchMemory:
    """A research memory, as the dialogue phase hands it over."""
    return ResearchMemory(
        topic=TOPIC,
        sessions=[],
        all_referenced_chunk_ids=[chunk.chunk_id for chunk in CHUNKS],
        collected_takeaways=["Waste collection fell by 30 percent."],
    )


def test_the_outline_door_answers_with_the_tiers_outline() -> None:
    """The door's outline is the tier's outline, node for node."""
    door = OutlineGenerator().generate_outline(
        topic=TOPIC,
        research_memory=_memory(),
        available_chunks=CHUNKS,
    )
    tier = tier_discovery.build_outline(TOPIC, _memory(), CHUNKS)

    assert door.model_dump(mode="json") == tier.model_dump(mode="json")


def test_the_persona_door_answers_with_the_tiers_personas() -> None:
    """The door's personas are the tier's personas."""
    door = PerspectiveGenerator().generate_perspectives(TOPIC, CONTEXT, 3)
    tier = tier_discovery.generate_perspectives(TOPIC, CONTEXT, 3)

    assert [p.model_dump(mode="json") for p in door] == [
        p.model_dump(mode="json") for p in tier
    ]


def test_the_outline_door_asks_the_tier_and_returns_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The door has no answer of its own: the tier's answer is the answer."""
    answered = TopicOutline(
        topic=TOPIC,
        nodes=[
            OutlineNode(
                node_id="sec_01",
                title="1. Waste",
                section_id="sec_01",
                depth=0,
                description="Source passages reporting: waste.",
            )
        ],
    )

    def _answered(*args: object, **kwargs: object) -> TopicOutline:
        return answered

    monkeypatch.setattr(tier_discovery, "build_outline", _answered)

    door = OutlineGenerator().generate_outline(
        topic=TOPIC,
        research_memory=_memory(),
        available_chunks=CHUNKS,
    )

    assert door is answered


def test_the_persona_door_asks_the_tier_and_returns_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The door has no answer of its own: the tier's personas are the personas."""
    answered: list[Perspective] = tier_discovery.generate_perspectives(TOPIC, CONTEXT, 1)

    def _answered(*args: object, **kwargs: object) -> list[Perspective]:
        return answered

    monkeypatch.setattr(tier_discovery, "generate_perspectives", _answered)

    door = PerspectiveGenerator().generate_perspectives(TOPIC, CONTEXT, 1)

    assert door == answered


def test_an_unreachable_tier_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tier that is not there is an infrastructure failure, not a domain error."""

    def _unavailable(*args: object, **kwargs: object) -> None:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_discovery, "build_outline", _unavailable)
    monkeypatch.setattr(tier_discovery, "generate_perspectives", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        OutlineGenerator().generate_outline(
            topic=TOPIC,
            research_memory=_memory(),
            available_chunks=CHUNKS,
        )

    with pytest.raises(BridgeUnavailableError):
        PerspectiveGenerator().generate_perspectives(TOPIC, CONTEXT, 2)


# ---------------------------------------------------------------------------
# The lexical analysis and the dialogue loop are doors too
#
# Their witnesses are frozen next door, in ``lmeditor._reference``. The witness
# is plain Python and never calls across the seam, so comparing the door with
# the witness compares the tier with an independent implementation of the same
# rule.
# ---------------------------------------------------------------------------

#: The corpus the doors and the witnesses are compared over. It is scientific
#: prose, and it carries the characters the two languages disagree about:
#: accented letters, a combining mark, a non-ASCII digit, a non-breaking
#: space, a Windows line ending and an astral character.
CORPUS: tuple[str, ...] = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
    "Kerbside recycling throughput doubled after the 2022 fleet replacement.",
    "Le café thermodynamique mesure déjà 30 % -- déjà-vu, αβγ and 中文.",
    "## Kerbside Collection\r\nCharges rose.\r\n\r\nChapter 2 Landfill",
    "The protocol yields already 30 %; the report shows a measurable reduction.",
    "ÉTUDE SUR LES DÉCHETS",
    "١. Kerbside Collection",
)


def test_the_source_analysis_door_answers_what_the_frozen_witness_answers() -> None:
    """Every lexical primitive answers as the frozen witness answers."""
    from lmeditor._reference import discovery_source_analysis as witness
    from lmeditor.discovery import source_analysis as door

    for text in CORPUS:
        assert door.tokenize(text) == witness.tokenize(text)
        assert door.content_tokens(text) == witness.content_tokens(text)
        assert door.salient_terms(text, limit=4) == witness.salient_terms(text, limit=4)
        assert door.split_sentences(text) == witness.split_sentences(text)
        assert door.extract_findings(text, limit=3) == witness.extract_findings(text, limit=3)
        assert door.detect_headings(text) == witness.detect_headings(text)

    assert door.salient_terms_across(CORPUS, limit=6) == witness.salient_terms_across(
        CORPUS, limit=6
    )
    assert door.cluster_by_terms(CORPUS, 2) == witness.cluster_by_terms(CORPUS, 2)
    assert door.title_case_terms(["waste", "recycling"]) == witness.title_case_terms(
        ["waste", "recycling"]
    )


def test_the_source_analysis_door_honours_the_negative_limit() -> None:
    """Python's slice counts a negative limit from the end of the list."""
    from lmeditor._reference import discovery_source_analysis as witness
    from lmeditor.discovery import source_analysis as door

    assert door.salient_terms("alpha beta gamma delta", limit=-1) == witness.salient_terms(
        "alpha beta gamma delta", limit=-1
    )


class _StubIndexer(ProvenanceIndexerProtocol):
    """An indexer that answers with the passages it was given."""

    def __init__(self, chunks: Sequence[SourceChunk]) -> None:
        self._chunks = list(chunks)

    def query(self, query: str, top_k: int = 5) -> Sequence[SourceChunk]:
        return self._chunks[:top_k]

    def get_chunk(self, chunk_id: str) -> SourceChunk | None:
        return next((chunk for chunk in self._chunks if chunk.chunk_id == chunk_id), None)

    def has_chunk(self, chunk_id: str) -> bool:
        return self.get_chunk(chunk_id) is not None

    def get_all_chunk_ids(self) -> Sequence[str]:
        return [chunk.chunk_id for chunk in self._chunks]


def _perspective() -> Perspective:
    """The persona the dialogue is compared over."""
    return Perspective(
        perspective_id="persp_expert_1",
        id="persp_expert_1",
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Consensus Specialist",
        name="Consensus Specialist",
        description="Expert in formal distributed protocols.",
        domain="Municipal Waste",
        guiding_questions=["What happened to waste collection rates?"],
        focus_areas=["Reported collection rates"],
    )


def test_the_dialogue_door_answers_what_the_frozen_witness_answers() -> None:
    """The session the door holds is the session the witness holds."""
    from lmeditor._reference import discovery_dialogue as witness
    from lmeditor.discovery import dialogue as door

    indexer = _StubIndexer(CHUNKS)
    perspective = _perspective()

    held = door.DialogueLoop(top_k_per_turn=2).conduct_dialogue(perspective, indexer, num_turns=2)
    frozen = witness.DialogueLoop(top_k_per_turn=2).conduct_dialogue(
        perspective, indexer, num_turns=2
    )

    assert held.model_dump(mode="json") == frozen.model_dump(mode="json")


def test_the_dialogue_door_consolidates_the_sessions_what_the_witness_does() -> None:
    """The research memory is the witness's research memory."""
    from lmeditor._reference import discovery_dialogue as witness
    from lmeditor.discovery import dialogue as door

    indexer = _StubIndexer(CHUNKS)
    perspectives = [_perspective()]

    held = door.DialogueLoop().conduct_all_dialogues(perspectives, indexer, turns_per_session=2)
    frozen = witness.DialogueLoop().conduct_all_dialogues(
        perspectives, indexer, turns_per_session=2
    )

    assert held.model_dump(mode="json") == frozen.model_dump(mode="json")
