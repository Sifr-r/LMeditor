"""Comprehensive test suite for Stanford STORM discovery engine: perspective, dialogue, and outliner."""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

import dspy
import pytest
from dspy.dsp.utils.utils import dotdict

from lmeditor.core.exceptions import (
    DialogueError,
    OutlineGenerationError,
    PerspectiveGenerationError,
    ValidationError,
)
from lmeditor.core.schemas import (
    OutlineNode,
    Perspective,
    ResearchMemory,
    SourceChunk,
    TopicOutline,
)
from lmeditor.core.types import PerspectiveRole, ProvenanceIndexerProtocol
from lmeditor.discovery.dialogue import (
    DialogueLoop,
    conduct_all_dialogues,
    conduct_dialogue,
)
from lmeditor.discovery.outliner import (
    OutlineGenerator,
    generate_outline,
)
from lmeditor.discovery.perspective import (
    PerspectiveGenerator,
    generate_perspectives,
)


# ============================================================================
# Test Fixtures & Mocks
# ============================================================================


def make_chunk(text: str, doc_id: str = "doc_test_1", start: int = 0) -> SourceChunk:
    """Helper to construct a valid SHA-256 backed SourceChunk."""
    raw_hash = hashlib.sha256(f"{doc_id}:{start}:{text}".encode("utf-8")).hexdigest()
    return SourceChunk(
        chunk_id=raw_hash,
        document_id=doc_id,
        text=text,
        start_char=start,
        end_char=start + len(text),
        char_start=start,
        char_end=start + len(text),
        token_count=len(text.split()),
    )


class _StubLM(dspy.BaseLM):
    """A language-model double that answers the discovery signatures."""

    def __init__(self, answers: dict[str, str] | None = None, fail: bool = False) -> None:
        super().__init__("stub", "chat", 0.0, 1000, True)
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[str] = []

    def forward(self, prompt=None, messages=None, **kwargs: Any) -> Any:
        messages = messages or [{"role": "user", "content": prompt}]
        content = str(messages[-1]["content"])
        self.calls.append(content)
        if self.fail:
            raise RuntimeError("model server unreachable")
        body = "\n".join(f"[[ ## {name} ## ]]\n{value}" for name, value in self.answers.items())
        message = dotdict(content=body, tool_calls=None)
        return dotdict(
            choices=[dotdict(message=message, finish_reason="stop")],
            usage=dotdict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model="stub",
        )


@pytest.fixture()
def restore_lm() -> Any:
    """Restore whatever language model was installed before the test."""
    previous = dspy.settings.lm
    yield previous
    dspy.configure(lm=previous)


class MockProvenanceIndexer(ProvenanceIndexerProtocol):

    def __init__(self, chunks: Sequence[SourceChunk] | None = None, fail_queries: bool = False) -> None:
        self.chunks = list(chunks) if chunks else []
        self._chunk_map = {c.chunk_id: c for c in self.chunks}
        self.fail_queries = fail_queries
        self.recorded_queries: list[str] = []

    def query(self, query: str, top_k: int = 5) -> Sequence[SourceChunk]:
        self.recorded_queries.append(query)
        if self.fail_queries:
            raise RuntimeError("Database connection timed out during vector search")
        if not self.chunks:
            return []
        # Return top_k matching or first top_k
        query_words = set(query.lower().split())
        scored: list[tuple[int, SourceChunk]] = []
        for c in self.chunks:
            overlap = sum(1 for w in query_words if w in c.text.lower())
            scored.append((overlap, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    def get_chunk(self, chunk_id: str) -> SourceChunk | None:
        return self._chunk_map.get(chunk_id)

    def has_chunk(self, chunk_id: str) -> bool:
        return chunk_id in self._chunk_map

    def get_all_chunk_ids(self) -> Sequence[str]:
        return list(self._chunk_map.keys())


# ============================================================================
# 1. Perspective Generator Tests
# ============================================================================


def test_perspective_generator_valid_topic() -> None:
    """Test generating diverse perspectives for a valid research topic."""
    generator = PerspectiveGenerator()
    topic = "Asynchronous Distributed Consensus in Byzantine Environments"
    perspectives = generator.generate_perspectives(topic, num_perspectives=4)

    assert len(perspectives) == 4
    roles = [p.role for p in perspectives]
    # Ensure diverse roles
    assert len(set(roles)) == 4
    assert PerspectiveRole.DOMAIN_EXPERT in roles
    assert PerspectiveRole.SKEPTIC in roles

    for p in perspectives:
        assert p.id.startswith("persp_")
        assert topic in p.title or topic in p.description
        assert len(p.focus_areas) >= 3
        assert len(p.guiding_questions) >= 3


def test_perspective_generator_bounds_and_roles() -> None:
    """Test that requesting varying numbers of perspectives maintains role distribution."""
    topic = "Quantum Key Distribution and Post-Quantum Cryptography"
    perspectives = generate_perspectives(topic, num_perspectives=6)
    assert len(perspectives) == 6

    expected_roles = {
        PerspectiveRole.DOMAIN_EXPERT,
        PerspectiveRole.SKEPTIC,
        PerspectiveRole.PRACTITIONER,
        PerspectiveRole.REGULATOR,
        PerspectiveRole.COMPARATIVE_ANALYST,
        PerspectiveRole.GENERAL_AUDIENCE,
    }
    actual_roles = {p.role for p in perspectives}
    assert expected_roles == actual_roles


WASTE_CONTEXT = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
    "Kerbside recycling throughput doubled after the 2022 fleet replacement. "
    "Landfill charges rose 12 percent, raising the cost of residual waste."
)

QUANTUM_CONTEXT = (
    "Quantum key distribution tolerates photon loss through decoy state protocols. "
    "Post-quantum lattice schemes resist Shor's attack at 128-bit security. "
    "Entanglement fidelity of 99.9 percent was measured on the metropolitan link."
)


def test_perspectives_are_grounded_in_the_supplied_context() -> None:
    """A persona's substance comes from the material it will interrogate."""
    generator = PerspectiveGenerator()
    perspectives = generator.generate_perspectives(
        "Municipal Waste", context=WASTE_CONTEXT, num_perspectives=3
    )

    assert len(perspectives) == 3
    for perspective in perspectives:
        blob = " ".join(
            [
                perspective.description,
                *perspective.focus_areas,
                *perspective.guiding_questions,
                *perspective.key_inquiries,
            ]
        ).lower()
        assert any(
            term in blob
            for term in ("waste", "collection", "recycling", "landfill", "kerbside", "fleet")
        ), f"perspective is not grounded in the context: {blob!r}"


def test_perspectives_differ_between_contexts() -> None:
    """The same topic with different material yields different personas."""
    generator = PerspectiveGenerator()
    waste = generator.generate_perspectives(
        "Municipal Waste", context=WASTE_CONTEXT, num_perspectives=2
    )
    quantum = generator.generate_perspectives(
        "Post-Quantum Cryptography", context=QUANTUM_CONTEXT, num_perspectives=2
    )

    assert [p.description for p in waste] != [p.description for p in quantum]
    assert [p.guiding_questions for p in waste] != [p.guiding_questions for p in quantum]

    quantum_blob = " ".join(
        [
            quantum[0].description,
            *quantum[0].focus_areas,
            *quantum[0].guiding_questions,
        ]
    ).lower()
    assert any(
        term in quantum_blob for term in ("quantum", "photon", "lattice", "entanglement", "decoy")
    )


def test_outline_uses_the_language_model_when_use_dspy_is_enabled(restore_lm: Any) -> None:
    """``use_dspy`` really controls the language-model path."""
    chunks = [
        make_chunk("Municipal waste collection rates fell by 30 percent between 2019 and 2023.", start=0),
        make_chunk("Kerbside recycling throughput doubled after the fleet replacement.", start=200),
    ]
    dspy.configure(
        lm=_StubLM(
            answers={
                "outline_json": (
                    '[{"title": "Municipal Waste Collection", '
                    '"description": "Collection throughput and charges.", '
                    '"keywords": ["collection", "waste", "kerbside"]}]'
                )
            }
        )
    )

    generator = OutlineGenerator(use_dspy=True)
    memory = conduct_all_dialogues(
        generate_perspectives("Municipal Waste", num_perspectives=1),
        MockProvenanceIndexer(chunks=chunks),
        turns_per_session=1,
    )
    outline = generator.generate_outline(
        topic="Municipal Waste", research_memory=memory, available_chunks=chunks
    )

    titles = " ".join(node.title for node in outline.nodes)
    assert "Municipal Waste Collection" in titles


def test_outline_without_a_language_model_ignores_use_dspy(restore_lm: Any) -> None:
    """With no model configured the generator falls back cleanly."""
    dspy.configure(lm=None)

    chunks = [
        make_chunk("Municipal waste collection rates fell by 30 percent between 2019 and 2023.", start=0),
        make_chunk("Kerbside recycling throughput doubled after the fleet replacement.", start=200),
    ]

    generator = OutlineGenerator(use_dspy=True)
    memory = conduct_all_dialogues(
        generate_perspectives("Municipal Waste", num_perspectives=1),
        MockProvenanceIndexer(chunks=chunks),
        turns_per_session=1,
    )
    outline = generator.generate_outline(
        topic="Municipal Waste", research_memory=memory, available_chunks=chunks
    )

    titles = " ".join(node.title for node in outline.nodes).lower()
    assert any(term in titles for term in ("waste", "collection", "recycling", "kerbside"))


def test_dialogue_uses_the_language_model_when_use_dspy_is_enabled(restore_lm: Any) -> None:
    """``use_dspy`` really controls the language-model path."""
    chunk = make_chunk(
        "Landfill charges rose 12 percent over the same period.",
        start=0,
    )
    dspy.configure(
        lm=_StubLM(
            answers={"synthesis": "The retrieved passage reports a rise in landfill charges."}
        )
    )

    perspective = Perspective(
        perspective_id="persp_expert_3",
        id="persp_expert_3",
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Domain Expert",
        name="Domain Expert",
        description="Reads the reported figures.",
        domain="Municipal Waste",
        guiding_questions=["What did landfill charges do?"],
        focus_areas=["Cost of residual waste"],
    )

    loop = DialogueLoop(use_dspy=True, top_k_per_turn=1)
    session = loop.conduct_dialogue(perspective, MockProvenanceIndexer(chunks=[chunk]), num_turns=1)

    answer = session.turns[0].answer
    assert "The retrieved passage reports a rise in landfill charges." in answer
    # The grounded quotation is still added by the module itself.
    assert f"[{chunk.chunk_id}]" in answer
    assert "12 percent" in answer


def test_perspective_generation_failure_is_not_swallowed() -> None:
    """A language model that fails must not degrade into a generic persona list."""
    import dspy

    class BrokenLM(dspy.BaseLM):
        def __init__(self) -> None:
            super().__init__("broken", "chat", 0.0, 1000, True)

        def forward(self, prompt=None, messages=None, **kwargs):
            raise RuntimeError("model server unreachable")

    previous = dspy.settings.lm
    dspy.configure(lm=BrokenLM())
    try:
        generator = PerspectiveGenerator(use_dspy=True)
        with pytest.raises(PerspectiveGenerationError):
            generator.generate_perspectives("Municipal Waste", context=WASTE_CONTEXT, num_perspectives=2)
    finally:
        dspy.configure(lm=previous)


def test_perspective_generator_validation_failures() -> None:
    generator = PerspectiveGenerator()

    # Blank topic
    with pytest.raises(ValidationError) as exc:
        generator.generate_perspectives("   ")
    assert "topic" in str(exc.value).lower()

    # Topic too short
    with pytest.raises(ValidationError):
        generator.generate_perspectives("AB")

    # Non-string topic
    with pytest.raises(ValidationError):
        generator.generate_perspectives(12345)  # type: ignore

    # Invalid num_perspectives
    with pytest.raises(ValidationError):
        generator.generate_perspectives("Valid Research Topic", num_perspectives=0)

    with pytest.raises(ValidationError):
        generator.generate_perspectives("Valid Research Topic", num_perspectives=11)


# ============================================================================
# 2. Conversational Dialogue Loop Tests
# ============================================================================


def test_dialogue_single_perspective_session() -> None:
    """Test a multi-turn dialogue session with grounded chunks and citation extraction."""
    c1 = make_chunk("Raft and Paxos guarantee safety under asynchronous network partitions.", start=0)
    c2 = make_chunk("Byzantine fault tolerance requires 3f+1 nodes to withstand malicious attacks.", start=200)
    indexer = MockProvenanceIndexer(chunks=[c1, c2])

    perspective = Perspective(
        perspective_id="persp_expert_1",
        id="persp_expert_1",
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Consensus Specialist",
        name="Consensus Specialist",
        description="Expert in formal distributed protocols.",
        domain="Distributed Consensus",
        guiding_questions=[
            "What invariants govern safety in asynchronous networks?",
            "How does Byzantine resilience scale with node cluster size?",
        ],
        focus_areas=["Safety Invariants", "Byzantine Resilience"],
    )

    loop = DialogueLoop(top_k_per_turn=2)
    session = loop.conduct_dialogue(perspective, indexer, num_turns=2)

    assert session.perspective_id == "persp_expert_1"
    assert len(session.turns) == 2
    assert len(session.synthesized_takeaways) >= 2

    # Verify turn content and citation binding
    turn1 = session.turns[0]
    assert "invariants" in turn1.question.lower()
    assert len(turn1.referenced_chunk_ids) > 0
    for cid in turn1.referenced_chunk_ids:
        assert f"[{cid}]" in turn1.answer


def test_dialogue_indexer_failure_handling() -> None:
    """Test that indexer exceptions are caught and wrapped in deterministic DialogueError."""
    indexer = MockProvenanceIndexer(fail_queries=True)
    perspective = generate_perspectives("Neural Architecture Search", num_perspectives=1)[0]

    loop = DialogueLoop()
    with pytest.raises(DialogueError) as exc_info:
        loop.conduct_dialogue(perspective, indexer, num_turns=2)

    err = exc_info.value
    assert err.perspective_id == perspective.id
    assert err.turn_index == 0
    assert "timed out" in str(err)


def test_dialogue_conduct_all_and_research_memory() -> None:
    """Test running research across multiple perspectives and consolidating ResearchMemory."""
    c1 = make_chunk("Transformers rely on multi-head scaled dot-product attention.", start=0)
    c2 = make_chunk("Linear attention achieves sub-quadratic memory complexity.", start=300)
    indexer = MockProvenanceIndexer(chunks=[c1, c2])

    perspectives = generate_perspectives("Efficient Transformer Architectures", num_perspectives=2)
    memory = conduct_all_dialogues(perspectives, indexer, turns_per_session=2)

    assert isinstance(memory, ResearchMemory)
    assert len(memory.sessions) == 2
    assert len(memory.all_referenced_chunk_ids) > 0
    # Verify discovery deduplication
    assert len(memory.all_referenced_chunk_ids) == len(set(memory.all_referenced_chunk_ids))
    assert len(memory.collected_takeaways) >= 4


def test_dialogue_answers_quote_the_retrieved_passage() -> None:
    """An answer quotes the passage that bears on the question and names it."""
    chunk = make_chunk(
        "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
        "Kerbside recycling throughput doubled after the fleet replacement.",
        start=0,
    )
    indexer = MockProvenanceIndexer(chunks=[chunk])

    perspective = Perspective(
        perspective_id="persp_critic_1",
        id="persp_critic_1",
        role=PerspectiveRole.SKEPTIC,
        title="Empirical Critic",
        name="Empirical Critic",
        description="Interrogates the reported figures.",
        domain="Municipal Waste",
        guiding_questions=["What happened to waste collection rates?"],
        focus_areas=["Reported collection rates"],
    )

    loop = DialogueLoop(top_k_per_turn=1)
    session = loop.conduct_dialogue(perspective, indexer, num_turns=1)

    turn = session.turns[0]
    assert turn.referenced_chunk_ids == [chunk.chunk_id]
    assert f"[{chunk.chunk_id}]" in turn.answer
    # The answer quotes the passage rather than describing it in the abstract.
    assert "30 percent" in turn.answer
    assert "Municipal waste collection rates fell" in turn.answer


def test_dialogue_takeaways_are_derived_from_the_turns() -> None:
    """The takeaways name the evidence that carried the session."""
    chunk = make_chunk(
        "Landfill charges rose 12 percent over the same period.",
        start=0,
    )
    indexer = MockProvenanceIndexer(chunks=[chunk])

    perspective = Perspective(
        perspective_id="persp_expert_2",
        id="persp_expert_2",
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Domain Expert",
        name="Domain Expert",
        description="Reads the reported figures.",
        domain="Municipal Waste",
        guiding_questions=["What did landfill charges do?"],
        focus_areas=["Cost of residual waste"],
    )

    loop = DialogueLoop(top_k_per_turn=1)
    session = loop.conduct_dialogue(perspective, indexer, num_turns=2)

    takeaways = " ".join(session.synthesized_takeaways)
    assert len(session.synthesized_takeaways) >= 2
    # The takeaway names the passage that carried the session ...
    assert chunk.chunk_id in takeaways
    # ... and repeats what that passage actually said.
    assert "12 percent" in takeaways


def test_dialogue_boundary_validations() -> None:
    loop = DialogueLoop()
    indexer = MockProvenanceIndexer()

    # Invalid perspective
    with pytest.raises(ValidationError):
        loop.conduct_dialogue("Not a perspective", indexer)  # type: ignore

    # Invalid indexer
    p = generate_perspectives("Test Topic", num_perspectives=1)[0]
    with pytest.raises(ValidationError):
        loop.conduct_dialogue(p, None)  # type: ignore

    # Invalid turn count
    with pytest.raises(ValidationError):
        loop.conduct_dialogue(p, indexer, num_turns=-1)

    # Empty perspectives in conduct_all
    with pytest.raises(ValidationError):
        loop.conduct_all_dialogues([], indexer)


# ============================================================================
# 3. Outline DAG Generator Tests
# ============================================================================


def test_outline_generator_hierarchy_and_leaf_assignment() -> None:
    """Test hierarchical topic outline DAG generation and strict leaf chunk assignment."""
    chunks = [
        make_chunk("Formal definitions and problem background for verifiable writing harnesses.", start=0),
        make_chunk("Component architecture and pipeline execution flow.", start=200),
        make_chunk("Failure modes and bottleneck latency under high concurrent load.", start=400),
        make_chunk("Production deployment telemetry and benchmark evaluation results.", start=600),
        make_chunk("Synthesis of future research frontiers and open problems.", start=800),
    ]
    indexer = MockProvenanceIndexer(chunks=chunks)
    perspectives = generate_perspectives("Verifiable LLM Writing Systems", num_perspectives=2)
    memory = conduct_all_dialogues(perspectives, indexer, turns_per_session=2)

    generator = OutlineGenerator()
    outline = generator.generate_outline(
        topic="Verifiable LLM Writing Systems",
        research_memory=memory,
        available_chunks=chunks,
    )

    assert isinstance(outline, TopicOutline)
    assert outline.topic == "Verifiable LLM Writing Systems"
    # The shape of the outline follows the material: it is derived from the
    # chunks, so the number of sections is not a fixed constant any more.
    assert len(outline.nodes) >= 1

    # Invariant: Root nodes MUST have children and MUST NOT have assigned chunks
    for root in outline.nodes:
        assert len(root.children) > 0
        assert len(root.assigned_chunk_ids) == 0

    # Invariant: Leaves MUST have assigned chunks
    leaves = outline.get_leaves()
    assert len(leaves) >= 1
    valid_cids = {c.chunk_id for c in chunks}
    assigned: list[str] = []

    for leaf in leaves:
        assert leaf.is_leaf is True
        assert len(leaf.assigned_chunk_ids) > 0
        for cid in leaf.assigned_chunk_ids:
            # Every assigned chunk must strictly exist in available_chunks
            assert cid in valid_cids
            assigned.append(cid)

    # Every chunk is placed exactly once.
    assert sorted(assigned) == sorted(valid_cids)


def test_outline_is_derived_from_the_corpus_and_not_a_fixed_skeleton() -> None:
    """Two different corpora must not produce the same outline."""
    waste_chunks = [
        make_chunk("Municipal waste collection rates fell by 30 percent between 2019 and 2023.", start=0),
        make_chunk("Kerbside recycling throughput doubled after the 2022 fleet replacement.", start=200),
        make_chunk("Landfill charges rose 12 percent, raising the cost of residual waste.", start=400),
    ]
    quantum_chunks = [
        make_chunk("Quantum key distribution tolerates photon loss through decoy state protocols.", start=0),
        make_chunk("Post-quantum lattice schemes resist Shor's attack at 128-bit security.", start=200),
        make_chunk("Entanglement fidelity of 99.9 percent was measured on the metropolitan link.", start=400),
    ]

    generator = OutlineGenerator()

    waste_memory = conduct_all_dialogues(
        generate_perspectives("Municipal Waste", num_perspectives=1),
        MockProvenanceIndexer(chunks=waste_chunks),
        turns_per_session=1,
    )
    quantum_memory = conduct_all_dialogues(
        generate_perspectives("Post-Quantum Cryptography", num_perspectives=1),
        MockProvenanceIndexer(chunks=quantum_chunks),
        turns_per_session=1,
    )

    waste_outline = generator.generate_outline(
        topic="Municipal Waste", research_memory=waste_memory, available_chunks=waste_chunks
    )
    quantum_outline = generator.generate_outline(
        topic="Post-Quantum Cryptography",
        research_memory=quantum_memory,
        available_chunks=quantum_chunks,
    )

    waste_titles = " ".join(node.title for node in waste_outline.nodes)
    quantum_titles = " ".join(node.title for node in quantum_outline.nodes)

    assert waste_titles != quantum_titles

    # The headings name the material they cover.
    assert any(
        term in waste_titles.lower()
        for term in ("waste", "collection", "recycling", "landfill", "kerbside", "fleet")
    )
    assert any(
        term in quantum_titles.lower()
        for term in ("quantum", "key", "lattice", "entanglement", "photon", "decoy")
    )


def test_outline_uses_headings_the_source_already_carries() -> None:
    """A document with its own headings is organised by those headings."""
    chunks = [
        make_chunk("## Kerbside Collection\nCollection frequency rose to weekly in 2023.", start=0),
        make_chunk("## Landfill Charges\nCharges rose 12 percent over the same period.", start=200),
    ]

    generator = OutlineGenerator()
    memory = conduct_all_dialogues(
        generate_perspectives("Municipal Waste", num_perspectives=1),
        MockProvenanceIndexer(chunks=chunks),
        turns_per_session=1,
    )
    outline = generator.generate_outline(
        topic="Municipal Waste", research_memory=memory, available_chunks=chunks
    )

    titles = " ".join(node.title for node in outline.nodes)
    assert "Kerbside Collection" in titles
    assert "Landfill Charges" in titles


def test_outline_dag_acyclicity_and_duplicate_rejection() -> None:
    """Test that TopicOutline.validate_dag catches cycles and duplicate node IDs."""
    leaf1 = OutlineNode(node_id="leaf_1", title="Leaf 1", description="desc 1", assigned_chunk_ids=["c1"])
    leaf2 = OutlineNode(node_id="leaf_2", title="Leaf 2", description="desc 2", assigned_chunk_ids=["c2"])
    root = OutlineNode(node_id="root_1", title="Root 1", children=[leaf1, leaf2])

    valid_outline = TopicOutline(topic="Test Topic", nodes=[root])
    # Should validate without error
    valid_outline.validate_dag()

    # Test duplicate node IDs
    dup_node = OutlineNode(node_id="leaf_1", title="Duplicate Leaf", description="desc")
    invalid_outline = TopicOutline(topic="Test Topic", nodes=[root, dup_node])
    with pytest.raises(OutlineGenerationError) as exc_dup:
        invalid_outline.validate_dag()
    assert "Duplicate node_id" in str(exc_dup.value)


def test_outline_out_of_bounds_chunk_assignment_rejected() -> None:
    """Test that assigning a chunk outside available_chunks raises OutlineGenerationError."""
    available_chunk = make_chunk("Legitimate available chunk", start=0)
    bogus_chunk_id = "0" * 64

    perspectives = generate_perspectives("Test Topic", num_perspectives=1)
    memory = conduct_all_dialogues(perspectives, MockProvenanceIndexer(chunks=[available_chunk]))

    generator = OutlineGenerator()

    # Subclass or mock generator to attempt injecting bogus chunk
    class BrokenOutlineGenerator(OutlineGenerator):
        def _assign_chunks_to_leaves(self, chunks: Sequence[SourceChunk], leaf_specs: list[dict[str, Any]]) -> dict[str, list[str]]:
            res = super()._assign_chunks_to_leaves(chunks, leaf_specs)
            # Inject hallucinated/out-of-bounds chunk into first leaf
            first_leaf = leaf_specs[0]["id"]
            res[first_leaf].append(bogus_chunk_id)
            return res

    broken_gen = BrokenOutlineGenerator()
    with pytest.raises(OutlineGenerationError) as exc_info:
        broken_gen.generate_outline("Test Topic", memory, [available_chunk])

    assert "not in available_chunks" in str(exc_info.value)
    assert exc_info.value.node_id is not None


def test_outline_validation_failures() -> None:
    """Test input boundary validation on OutlineGenerator."""
    generator = OutlineGenerator()
    perspectives = generate_perspectives("Test Topic", num_perspectives=1)
    memory = conduct_all_dialogues(perspectives, MockProvenanceIndexer())
    chunks = [make_chunk("Chunk 1")]

    # Empty topic
    with pytest.raises(ValidationError):
        generator.generate_outline("", memory, chunks)

    # Empty available_chunks
    with pytest.raises(ValidationError):
        generator.generate_outline("Valid Topic", memory, [])

    # Invalid research_memory
    with pytest.raises(ValidationError):
        generator.generate_outline("Valid Topic", "Not a memory", chunks)  # type: ignore
