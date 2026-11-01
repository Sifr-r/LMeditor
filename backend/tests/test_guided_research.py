"""The guided research stage: the corpus is questioned before it is written up.

The guided machinery already existed in :mod:`lmeditor.discovery.dialogue` and
was never called by the writing pipeline: the document was drafted from the
raw corpus without ever being questioned by the perspectives that discovery
had just produced. These tests pin the stage that puts it back, and the
properties that keep it honest:

* the stage never kills a run -- without perspectives, without passages,
  without a model, or when the inquiry itself fails, the document is still
  written from the corpus;
* it invents nothing: a stage that did not run says why and leaves no
  research memory behind;
* what it found reaches the outline and the event stream, with the chunks
  the findings came from.
"""

from __future__ import annotations

import asyncio
from typing import Any

import dspy
import pytest

from lmeditor.core.exceptions import DialogueError
from lmeditor.core.schemas import (
    DialogueSession,
    Perspective,
    ResearchMemory,
    SourceChunk,
)
from lmeditor.core.types import EventType, PerspectiveRole, WorkflowStep
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.workflow.graph import build_lmeditor_graph, run_workflow
from lmeditor.workflow.nodes import (
    GUIDED_RESEARCH_DISABLED_NOTE,
    NO_LM_GUIDED_RESEARCH_NOTE,
    guided_research_node,
    outline_synthesis_node,
    release_provenance_index,
)

from .stub_lm import StubLM

TOPIC = "Municipal waste collection trends"

SOURCE_TEXT = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
    "The decline was driven by a citywide composting programme introduced in 2020."
)

SECOND_SOURCE_TEXT = (
    "The composting programme was rolled out to every district by the end of 2021, "
    "and the council reported a further 12 percent reduction in landfill charges."
)


#: The document ids the tests in this file use. The guided stage caches its
#: retrieval index per document, so every test has to leave that cache as it
#: found it: an index built from one test's passages must not answer another
#: test's questions, and the order the tests run in must not matter.
_DOCUMENT_IDS: set[str] = set()


@pytest.fixture(autouse=True)
def _restore_dspy() -> Any:
    """Restore the global DSPy configuration after each test."""
    previous = dspy.settings.lm
    yield
    dspy.configure(lm=previous)


@pytest.fixture(autouse=True)
def _release_retrieval_index() -> Any:
    """Drop the retrieval index the guided stage caches for a document."""
    yield
    for doc_id in _DOCUMENT_IDS:
        release_provenance_index(doc_id)


@pytest.fixture
def stub_model() -> StubLM:
    """The repository's offline language model, loaded the way the app loads it."""
    model = StubLM()
    dspy.configure(lm=model)
    return model


def _chunk(text: str = SOURCE_TEXT) -> SourceChunk:
    return SourceChunk.create(
        document_id="doc_guided",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text),
        metadata={"document_title": "Municipal Audit"},
    )


def _perspective() -> Perspective:
    return Perspective(
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Municipal finance analyst",
        description="Reads the collection figures and what drove them.",
        domain="Municipal waste",
        guiding_questions=[
            "What happened to municipal waste collection rates between 2019 and 2023?"
        ],
        focus_areas=["Collection rates"],
    )


def _state(
    *,
    with_perspectives: bool = True,
    with_chunks: bool = True,
    guided_research: bool = True,
    doc_id: str = "doc_guided",
) -> dict[str, Any]:
    """The state the guided stage runs against."""
    chunks = [_chunk(), _chunk(SECOND_SOURCE_TEXT)] if with_chunks else []
    _DOCUMENT_IDS.add(doc_id)
    return {
        "doc_id": doc_id,
        "topic": TOPIC,
        "guided_research": guided_research,
        "perspectives": [_perspective()] if with_perspectives else [],
        "source_chunks": {chunk.chunk_id: chunk for chunk in chunks},
        "errors": [],
    }


# ----------------------------------------------------------------------
# The stage does not kill the run
# ----------------------------------------------------------------------


def test_without_perspectives_there_is_nothing_to_inquire_into() -> None:
    """A run that discovered no perspectives says so instead of failing."""
    state = _state(with_perspectives=False)

    result = guided_research_node(state)

    assert result["research_memory"] is None
    assert result["research_notes"], "the stage did not say that it had nothing to do"
    assert "nothing to inquire into" in result["research_notes"][0]
    assert "errors" not in result, "an empty run is not a failed run"


def test_without_a_language_model_the_stage_invents_nothing() -> None:
    """With no model loaded there is no inquiry, and no fabricated findings."""
    dspy.configure(lm=None)
    state = _state()

    result = guided_research_node(state)

    assert result["research_memory"] is None, "the stage invented research it did not do"
    assert result["research_notes"], "the stage did not say why it did not run"
    assert NO_LM_GUIDED_RESEARCH_NOTE in result["research_notes"]
    assert "no language model" in result["research_notes"][0].lower()


def test_a_failed_dialogue_is_recorded_and_the_run_continues(
    monkeypatch: pytest.MonkeyPatch,
    stub_model: StubLM,
) -> None:
    """A failing inquiry is recorded; it never takes the document down with it."""

    class FailingDialogueLoop:
        """A dialogue loop whose inquiry always fails."""

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def conduct_all_dialogues(self, *args: Any, **kwargs: Any) -> ResearchMemory:
            raise DialogueError("the retrieval backend is unavailable")

    monkeypatch.setattr("lmeditor.workflow.nodes.DialogueLoop", FailingDialogueLoop)
    state = _state()

    result = guided_research_node(state)

    assert result["research_memory"] is None
    assert result["errors"], "the failure was not recorded"
    assert any(
        "the retrieval backend is unavailable" in error for error in result["errors"]
    ), "the failure that was recorded is not the one the inquiry raised"
    assert result["research_notes"], "the failure was not explained in the notes"


def test_the_stage_can_be_switched_off(stub_model: StubLM) -> None:
    """A run that opts out of the guided stage conducts no inquiry."""
    state = _state(guided_research=False)

    result = guided_research_node(state)

    assert result["research_memory"] is None
    assert result["research_notes"] == [GUIDED_RESEARCH_DISABLED_NOTE]
    assert "errors" not in result


# ----------------------------------------------------------------------
# The stage finds something
# ----------------------------------------------------------------------


def test_the_stage_questions_the_corpus_and_records_what_it_found(
    stub_model: StubLM,
) -> None:
    """With a model loaded the corpus is questioned and the findings are kept."""
    state = _state()

    result = guided_research_node(state)

    memory = result["research_memory"]
    assert isinstance(memory, ResearchMemory)
    assert result["research_notes"], "the stage found something but recorded nothing"
    assert memory.collected_takeaways, "the inquiry kept no findings"

    # The findings are grounded: every passage they name is a passage of the
    # corpus the document is written from.
    corpus_ids = set(state["source_chunks"])
    assert set(memory.all_referenced_chunk_ids) <= corpus_ids


def test_the_findings_are_the_same_for_the_same_state_and_model(
    stub_model: StubLM,
) -> None:
    """The stage is deterministic in what it stores."""
    state = _state()

    first = guided_research_node(state)
    second = guided_research_node(state)

    assert first["research_notes"] == second["research_notes"]
    assert first["research_memory"] == second["research_memory"]


# ----------------------------------------------------------------------
# The findings reach the outline
# ----------------------------------------------------------------------


def _memory_for(chunk: SourceChunk) -> ResearchMemory:
    """A research memory whose finding is anchored to ``chunk``."""
    finding = (
        "[Municipal finance analyst] The passage that carried this session was "
        f'[{chunk.chunk_id}], which states: "Collection rates fell by 30 percent."'
    )
    return ResearchMemory(
        topic=TOPIC,
        sessions=[
            DialogueSession(
                perspective_id="p_1",
                turns=[],
                synthesized_takeaways=[finding],
            )
        ],
        all_referenced_chunk_ids=[chunk.chunk_id],
        collected_takeaways=[finding],
    )


def test_the_outline_carries_the_guided_findings() -> None:
    """The section that holds the passage also states what the inquiry found."""
    chunk = _chunk()
    state: dict[str, Any] = {
        "doc_id": "doc_guided",
        "topic": TOPIC,
        "perspectives": [_perspective()],
        "source_chunks": {chunk.chunk_id: chunk},
        "research_memory": _memory_for(chunk),
    }

    result = outline_synthesis_node(state)

    leaves = result["outline_nodes"]
    assert leaves, "the outline has no sections"
    grounded = [leaf for leaf in leaves if chunk.chunk_id in leaf.assigned_chunk_ids]
    assert grounded, "the passage the inquiry read is in no section"
    assert "Guided inquiry found:" in grounded[0].description
    assert "Collection rates fell by 30 percent." in grounded[0].description


def test_without_findings_the_outline_is_exactly_what_the_corpus_produced() -> None:
    """No findings means no change to the outline."""
    chunk = _chunk()
    state: dict[str, Any] = {
        "doc_id": "doc_guided",
        "topic": TOPIC,
        "perspectives": [_perspective()],
        "source_chunks": {chunk.chunk_id: chunk},
    }

    result = outline_synthesis_node(state)

    assert result["outline_nodes"], "the outline has no sections"
    for leaf in result["outline_nodes"]:
        assert "Guided inquiry found:" not in leaf.description


# ----------------------------------------------------------------------
# The graph asks the corpus before it writes the outline
# ----------------------------------------------------------------------


def test_the_graph_asks_the_corpus_before_it_writes_the_outline() -> None:
    """The guided stage sits between discovery and the outline."""
    drawn = build_lmeditor_graph().get_graph()

    assert "guided_research" in drawn.nodes
    edges = {(edge.source, edge.target) for edge in drawn.edges}
    assert ("discover_perspectives", "guided_research") in edges
    assert ("guided_research", "synthesize_outline") in edges


# ----------------------------------------------------------------------
# The findings reach the client
# ----------------------------------------------------------------------


class _RecordingSink:
    """A sink that keeps every event the run publishes."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish_event(self, event: Any) -> None:
        self.events.append(event)


def _research_notes_events(sink: _RecordingSink) -> list[Any]:
    return [event for event in sink.events if event.event_type == EventType.RESEARCH_NOTES]


def test_the_findings_are_published_as_a_research_notes_event(
    stub_model: StubLM,
) -> None:
    """The client learns what the guided stage found, and where it came from."""
    sink = _RecordingSink()
    doc_id = "doc_guided_events"
    _DOCUMENT_IDS.add(doc_id)

    asyncio.run(
        run_workflow(
            doc_id=doc_id,
            topic=TOPIC,
            source_chunks=[_chunk(), _chunk(SECOND_SOURCE_TEXT)],
            max_retries=1,
            threshold_f_beta=0.5,
            session=sink,
        )
    )

    published = _research_notes_events(sink)
    assert published, "the guided findings were never published"

    event = published[0]
    assert event.step == WorkflowStep.GUIDED_RESEARCH
    assert event.data["notes"], "the event carries no findings"
    assert event.data["referenced_chunk_ids"], "the event does not say where they came from"

    # The client parses the event by these two keys.
    assert {"notes", "referenced_chunk_ids"} <= set(event.data)

    corpus_ids = {_chunk().chunk_id, _chunk(SECOND_SOURCE_TEXT).chunk_id}
    assert set(event.data["referenced_chunk_ids"]) <= corpus_ids


def test_a_stage_that_did_not_run_still_publishes_why() -> None:
    """An empty panel is indistinguishable from a stage that failed silently."""
    dspy.configure(lm=None)
    sink = _RecordingSink()
    doc_id = "doc_guided_events_no_lm"
    _DOCUMENT_IDS.add(doc_id)

    asyncio.run(
        run_workflow(
            doc_id=doc_id,
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=sink,
        )
    )

    published = _research_notes_events(sink)
    assert published, "the stage did not run and said nothing"
    assert published[0].data["notes"], "the event carries no reason"
    assert "no language model" in published[0].data["notes"][0].lower()
    assert published[0].data["referenced_chunk_ids"] == []


# ----------------------------------------------------------------------
# The provenance index can be questioned
# ----------------------------------------------------------------------


def test_the_provenance_index_answers_the_protocol() -> None:
    """The index the document is drafted from is the index that is questioned."""
    chunk = _chunk()
    index = ProvenanceIndex.build([chunk])

    assert list(index.query("collection rates", top_k=3)) == list(
        index.rank_for_section("collection rates", top_k=3)
    )
    assert index.get_chunk(chunk.chunk_id) == index.get(chunk.chunk_id)
    assert index.get_chunk("0" * 64) is None
