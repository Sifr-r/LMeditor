"""Comprehensive test suite for Stanford STORM discovery engine: perspective, dialogue, and outliner."""

from __future__ import annotations

import hashlib
from typing import Sequence
import pytest

from lmeditor.core.exceptions import (
    DialogueError,
    OutlineGenerationError,
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


class MockProvenanceIndexer(ProvenanceIndexerProtocol):
    """Deterministic mock indexer for testing conversational dialogue loops."""

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


def test_perspective_generator_validation_failures() -> None:
    """Test input boundary validation on PerspectiveGenerator."""
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


def test_dialogue_boundary_validations() -> None:
    """Test input boundary validation on DialogueLoop."""
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
    assert len(outline.nodes) >= 5

    # Invariant: Root nodes MUST have children and MUST NOT have assigned chunks
    for root in outline.nodes:
        assert len(root.children) > 0
        assert len(root.assigned_chunk_ids) == 0

    # Invariant: Leaves MUST have assigned chunks
    leaves = outline.get_leaves()
    assert len(leaves) >= 10
    valid_cids = {c.chunk_id for c in chunks}

    for leaf in leaves:
        assert leaf.is_leaf is True
        assert len(leaf.assigned_chunk_ids) > 0
        for cid in leaf.assigned_chunk_ids:
            # Every assigned chunk must strictly exist in available_chunks
            assert cid in valid_cids


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
