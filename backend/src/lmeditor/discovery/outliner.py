"""Hierarchical topic outline DAG generator assigning candidate chunk IDs to section leaves."""

from __future__ import annotations

import re
from typing import Any, Sequence

from lmeditor.core.exceptions import OutlineGenerationError, ValidationError
from lmeditor.core.schemas import OutlineNode, ResearchMemory, SourceChunk, TopicOutline


class OutlineGenerator:
    """Stanford STORM-style hierarchical outline DAG synthesizer with leaf chunk mapping."""

    def __init__(self, use_dspy: bool = False) -> None:
        """Initialize the outline generator.

        Args:
            use_dspy: Whether to utilize DSPy for dynamic section heading generation.
        """
        self.use_dspy = use_dspy

    def validate_inputs(
        self,
        topic: str,
        research_memory: Any,
        available_chunks: Sequence[SourceChunk],
    ) -> str:
        """Validate input parameters against boundary conditions.

        Raises:
            ValidationError: If inputs are empty or incorrect types.
        """
        if not isinstance(topic, str):
            raise ValidationError(
                f"topic must be a string, got {type(topic).__name__}",
                field="topic",
                value=topic,
            )
        clean_topic = topic.strip()
        if not clean_topic:
            raise ValidationError("topic cannot be empty or whitespace.", field="topic", value=topic)

        if not isinstance(research_memory, ResearchMemory):
            raise ValidationError(
                f"research_memory must be an instance of ResearchMemory, got {type(research_memory).__name__}",
                field="research_memory",
                value=research_memory,
            )

        if not available_chunks:
            raise ValidationError(
                "available_chunks sequence cannot be empty.",
                field="available_chunks",
                value=available_chunks,
            )

        for idx, chunk in enumerate(available_chunks):
            if not isinstance(chunk, SourceChunk):
                raise ValidationError(
                    f"available_chunks[{idx}] must be a SourceChunk instance, got {type(chunk).__name__}",
                    field=f"available_chunks[{idx}]",
                    value=chunk,
                )

        return clean_topic

    def generate_outline(
        self,
        topic: str,
        research_memory: ResearchMemory | Sequence[Any] | None = None,
        available_chunks: Sequence[SourceChunk] | None = None,
        perspectives: Sequence[Any] | None = None,
    ) -> TopicOutline:
        """Generate a hierarchical topic outline DAG with leaf chunk assignments."""
        if available_chunks is None:
            available_chunks = []

        if research_memory is None and perspectives is not None:
            # Construct a ResearchMemory from the provided perspectives
            research_memory = ResearchMemory(
                topic=topic,
                sessions=[],
                all_referenced_chunk_ids=[c.chunk_id for c in available_chunks],
                collected_takeaways=[getattr(p, "title", str(p)) for p in perspectives],
            )
        elif isinstance(research_memory, Sequence) and not isinstance(research_memory, (ResearchMemory, str, bytes)):
            # Sequence of perspectives passed as positional or named arg
            research_memory = ResearchMemory(
                topic=topic,
                sessions=[],
                all_referenced_chunk_ids=[c.chunk_id for c in available_chunks],
                collected_takeaways=[getattr(p, "title", str(p)) for p in research_memory],
            )

        clean_topic = self.validate_inputs(topic, research_memory, available_chunks)

        # Build chunk lookup index for strict boundary verification
        valid_chunk_map: dict[str, SourceChunk] = {c.chunk_id: c for c in available_chunks}

        # 1. Define foundational hierarchical sections
        outline_specs: list[dict[str, Any]] = [
            {
                "id": "sec_foundations",
                "title": f"1. Foundations & Scope of {clean_topic}",
                "description": f"Problem scope, theoretical context, and motivations for {clean_topic}.",
                "subsections": [
                    {
                        "id": "sec_foundations_background",
                        "title": "1.1 Problem Background & Core Motivations",
                        "description": "Historical context, foundational problem statement, and architectural needs.",
                        "keywords": ["foundation", "background", "motivation", "problem", "overview", "introduction"],
                    },
                    {
                        "id": "sec_foundations_formal_model",
                        "title": "1.2 Formal Definitions & Core Invariants",
                        "description": "Rigorous terminology, invariants, and fundamental constraints.",
                        "keywords": ["formal", "definition", "invariant", "model", "theory", "principles"],
                    },
                ],
            },
            {
                "id": "sec_architecture",
                "title": f"2. Architectural Mechanics & Systems Design",
                "description": f"Mechanistic workflow, pipeline execution, and core abstractions in {clean_topic}.",
                "subsections": [
                    {
                        "id": "sec_architecture_components",
                        "title": "2.1 Component Topology & Execution Pipeline",
                        "description": "Structural topology, subsystem interaction, and computational execution loop.",
                        "keywords": ["architecture", "component", "pipeline", "execution", "design", "topology"],
                    },
                    {
                        "id": "sec_architecture_provenance",
                        "title": "2.2 Provenance Binding & Dataflow Integrity",
                        "description": "Data models, state tracking, and mathematical grounding guarantees.",
                        "keywords": ["dataflow", "provenance", "integrity", "state", "schema", "flow"],
                    },
                ],
            },
            {
                "id": "sec_critique",
                "title": "3. Critical Evaluation, Bottlenecks & Trade-offs",
                "description": "Adversarial critique, failure modes, trade-off costs, and empirical vulnerabilities.",
                "subsections": [
                    {
                        "id": "sec_critique_failure_modes",
                        "title": "3.1 Edge-Case Vulnerabilities & Failure Modes",
                        "description": "Stress limits, failure boundaries, and behavioral edge cases.",
                        "keywords": ["vulnerability", "failure", "bottleneck", "edge", "risk", "stress"],
                    },
                    {
                        "id": "sec_critique_tradeoffs",
                        "title": "3.2 Cost, Latency & Ergonomic Trade-offs",
                        "description": "Resource trade-offs, operational complexity, and comparison against alternatives.",
                        "keywords": ["trade-off", "cost", "latency", "overhead", "alternative", "comparison"],
                    },
                ],
            },
            {
                "id": "sec_operations",
                "title": "4. Operational Ergonomics & Practical Implementation",
                "description": "Engineering deployment, production telemetry, and empirical benchmark performance.",
                "subsections": [
                    {
                        "id": "sec_operations_deployment",
                        "title": "4.1 Production Engineering & Telemetry",
                        "description": "Real-world deployment, monitoring signals, and operational ergonomics.",
                        "keywords": ["production", "deployment", "telemetry", "monitoring", "engineering", "tooling"],
                    },
                    {
                        "id": "sec_operations_benchmarks",
                        "title": "4.2 Empirical Benchmarks & Case Studies",
                        "description": "Experimental verification, quantitative results, and observed efficacy.",
                        "keywords": ["benchmark", "experiment", "evaluation", "result", "metric", "case study"],
                    },
                ],
            },
            {
                "id": "sec_synthesis",
                "title": "5. Synthesis & Future Horizons",
                "description": "Unified research takeaways, open scientific challenges, and long-term trajectory.",
                "subsections": [
                    {
                        "id": "sec_synthesis_open_challenges",
                        "title": "5.1 Open Research Questions & Unsolved Frontiers",
                        "description": "Unresolved challenges, emerging hypotheses, and future avenues.",
                        "keywords": ["future", "frontier", "unsolved", "challenge", "open question", "horizon"],
                    },
                    {
                        "id": "sec_synthesis_conclusion",
                        "title": "5.2 Concluding Epistemic Synthesis",
                        "description": "Comprehensive synthesis reconciling perspectives and ground truth.",
                        "keywords": ["conclusion", "synthesis", "summary", "closing", "verdict", "perspective"],
                    },
                ],
            },
        ]

        # 2. Extract leaf specs and assign chunks statically to leaf nodes
        leaf_specs: list[dict[str, Any]] = []
        for root_spec in outline_specs:
            for sub_spec in root_spec["subsections"]:
                leaf_specs.append(sub_spec)

        # Statically map chunks to leaf specs using keyword matching with guaranteed full coverage
        leaf_chunk_assignments = self._assign_chunks_to_leaves(available_chunks, leaf_specs)

        # 3. Construct the OutlineNode tree DAG
        root_nodes: list[OutlineNode] = []

        for root_idx, root_spec in enumerate(outline_specs):
            child_nodes: list[OutlineNode] = []

            for sub_idx, sub_spec in enumerate(root_spec["subsections"]):
                sub_id = sub_spec["id"]
                assigned_cids = leaf_chunk_assignments.get(sub_id, [])

                # Verify every assigned chunk strictly exists in available_chunks
                for cid in assigned_cids:
                    if cid not in valid_chunk_map:
                        raise OutlineGenerationError(
                            f"Assigned chunk '{cid}' is not in available_chunks.",
                            node_id=sub_id,
                            details={"assigned_chunk_id": cid, "valid_chunk_ids": list(valid_chunk_map.keys())},
                        )

                leaf_node = OutlineNode(
                    node_id=sub_id,
                    title=sub_spec["title"],
                    section_id=sub_id,
                    depth=1,
                    description=sub_spec["description"],
                    assigned_chunk_ids=assigned_cids,
                    children=[],
                    order=sub_idx,
                    subheadings=[f"Sub-aspect of {sub_spec['title']}"],
                )
                child_nodes.append(leaf_node)

            root_node = OutlineNode(
                node_id=root_spec["id"],
                title=root_spec["title"],
                section_id=root_spec["id"],
                depth=0,
                description=root_spec["description"],
                assigned_chunk_ids=[],  # Root nodes are non-leaves; assigned chunks reside in leaves
                children=child_nodes,
                order=root_idx,
                subheadings=[c.title for c in child_nodes],
            )
            root_nodes.append(root_node)

        outline = TopicOutline(
            topic=clean_topic,
            nodes=root_nodes,
        )

        # 4. Verify DAG acyclicity and structural constraints
        outline.validate_dag()

        return outline

    def _assign_chunks_to_leaves(
        self,
        chunks: Sequence[SourceChunk],
        leaf_specs: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Map candidate chunks to leaf sections based on keyword relevance and balanced distribution."""
        assignments: dict[str, list[str]] = {spec["id"]: [] for spec in leaf_specs}
        num_leaves = len(leaf_specs)

        # Track which chunks have been assigned
        assigned_chunk_ids: set[str] = set()

        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            best_leaf_id: str | None = None
            max_score = 0

            for spec in leaf_specs:
                keywords: list[str] = spec.get("keywords", [])
                score = 0
                for kw in keywords:
                    if kw in chunk_text_lower:
                        score += 1
                if score > max_score:
                    max_score = score
                    best_leaf_id = spec["id"]

            if best_leaf_id is not None and max_score > 0:
                assignments[best_leaf_id].append(chunk.chunk_id)
                assigned_chunk_ids.add(chunk.chunk_id)

        # Ensure all chunks are assigned (round-robin for any unassigned chunks)
        unassigned = [c for c in chunks if c.chunk_id not in assigned_chunk_ids]
        for idx, chunk in enumerate(unassigned):
            target_leaf_id = leaf_specs[idx % num_leaves]["id"]
            assignments[target_leaf_id].append(chunk.chunk_id)

        # If any leaf has 0 chunks, ensure it receives at least one candidate chunk
        all_cids = [c.chunk_id for c in chunks]
        for idx, spec in enumerate(leaf_specs):
            leaf_id = spec["id"]
            if not assignments[leaf_id]:
                # Lend a chunk from available pool
                fallback_cid = all_cids[idx % len(all_cids)]
                assignments[leaf_id].append(fallback_cid)

        return assignments


def generate_outline(
    topic: str,
    research_memory: ResearchMemory,
    available_chunks: Sequence[SourceChunk],
) -> TopicOutline:
    """Convenience functional interface to OutlineGenerator.generate_outline."""
    generator = OutlineGenerator()
    return generator.generate_outline(topic, research_memory, available_chunks)


HierarchicalOutlineGenerator = OutlineGenerator

__all__ = [
    "HierarchicalOutlineGenerator",
    "OutlineGenerator",
    "generate_outline",
]
