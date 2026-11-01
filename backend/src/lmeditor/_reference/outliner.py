"""The frozen hierarchical topic outline generator.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.

It is frozen. The rules it carries -- how a corpus is grouped into sections,
how a passage is assigned to a leaf, how the tree is shaped -- are the tier's
rules now: the live module ``lmeditor.discovery.outliner`` is the door to
them, and this copy is the yardstick they are measured against.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from lmeditor.core.exceptions import OutlineGenerationError, ValidationError
from lmeditor.core.schemas import (
    OutlineNode,
    OutlineNodeInput,
    ResearchMemory,
    SourceChunk,
    TopicOutline,
)
from lmeditor._reference.discovery_source_analysis import (
    GENERIC_TERMS,
    cluster_by_terms,
    detect_headings,
    extract_findings,
    salient_terms,
    title_case_terms,
)

logger = logging.getLogger(__name__)

#: Maximum number of top-level sections derived from a corpus.
MAX_ROOT_SECTIONS: int = 5

#: Maximum number of leaves under a single root section.
MAX_LEAVES_PER_ROOT: int = 2

#: Cached DSPy program used for language-model outline synthesis.
_OUTLINE_PROGRAM: Any = None


class OutlineGenerator:
    """Outline generator that derives its sections from the supplied chunks.

    Args:
        use_dspy: Whether to use a language model to propose the section
            headings. The flag only takes effect when a language model is
            actually configured; without one the generator falls back to the
            deterministic, corpus-derived structure.
    """

    def __init__(self, use_dspy: bool = False) -> None:
        self.use_dspy = use_dspy

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Outline generation
    # ------------------------------------------------------------------
    def build_outline_from_sections(
        self,
        sections: Sequence[OutlineNodeInput],
        topic: str,
        available_chunks: Sequence[SourceChunk] | None = None,
    ) -> TopicOutline:
        """Build a grounded outline from sections the caller named.

        This is the operator's steer: the sections they asked for are the
        sections the manuscript is written to. What they *cannot* steer is
        what the document says — the passages assigned to each section are
        chosen from the corpus by the same keyword-relevance and balancing
        rules the synthesised outline uses, so a caller-supplied outline
        cannot smuggle in a claim the sources do not support.

        Args:
            sections: The sections the caller named, in order.
            topic: The document's subject, used for the root description.
            available_chunks: The corpus the sections are grounded in.

        Returns:
            The outline the drafter will write to.
        """
        if available_chunks is None:
            available_chunks = []

        leaves: list[dict[str, Any]] = []

        def collect(nodes: Sequence[OutlineNodeInput], depth: int) -> list[OutlineNode]:
            """Turn the caller's sections into outline nodes, assigning passages."""
            built: list[OutlineNode] = []
            for order, node in enumerate(nodes):
                node_id = f"sec_{order + 1:02d}_{len(leaves) + 1:02d}"
                if node.children:
                    children = collect(node.children, depth + 1)
                    built.append(
                        OutlineNode(
                            node_id=node_id,
                            title=node.title,
                            section_id=node_id,
                            depth=depth,
                            description=node.description,
                            assigned_chunk_ids=[],
                            children=children,
                            order=order,
                            subheadings=[child.title for child in children],
                        )
                    )
                    continue

                keywords = [
                    term.lower()
                    for term in salient_terms(f"{node.title} {node.description}", limit=4)
                ]
                leaf_spec = {
                    "id": node_id,
                    "title": node.title,
                    "description": node.description,
                    "keywords": keywords,
                    "chunk_ids": [],
                }
                leaves.append(leaf_spec)
                built.append(
                    OutlineNode(
                        node_id=node_id,
                        title=node.title,
                        section_id=node_id,
                        depth=depth,
                        description=node.description,
                        assigned_chunk_ids=[],
                        children=[],
                        order=order,
                        subheadings=[],
                    )
                )
            return built

        root_nodes = collect(sections, 0)
        if not root_nodes:
            return TopicOutline(topic=topic, nodes=())

        # With no corpus there is nothing to ground the sections in, so the
        # sections are kept and left unassigned rather than assigned a passage
        # that does not exist.
        if not available_chunks:
            return TopicOutline(topic=topic, nodes=tuple(root_nodes))

        assignments = self._assign_chunks_to_leaves(list(available_chunks), leaves)

        def with_chunks(nodes: Sequence[OutlineNode]) -> list[OutlineNode]:
            """Return the nodes with the assigned passages attached."""
            rebuilt: list[OutlineNode] = []
            for node in nodes:
                if node.children:
                    rebuilt.append(node.model_copy(update={"children": with_chunks(node.children)}))
                    continue
                rebuilt.append(
                    node.model_copy(
                        update={"assigned_chunk_ids": assignments.get(node.node_id, [])}
                    )
                )
            return rebuilt

        return TopicOutline(topic=topic, nodes=tuple(with_chunks(root_nodes)))

    def generate_outline(
        self,
        topic: str,
        research_memory: ResearchMemory | Sequence[Any] | None = None,
        available_chunks: Sequence[SourceChunk] | None = None,
        perspectives: Sequence[Any] | None = None,
    ) -> TopicOutline:
        """Generate a hierarchical topic outline DAG with leaf chunk assignments.

        The section titles come from the material that will fill them: either
        from a language model that read the passages, or from the passages
        themselves.
        """
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

        # 1. Derive the top-level sections from the source material.
        outline_specs = self._derive_section_specs(clean_topic, available_chunks)

        # 2. Extract leaf specs and assign chunks to them
        leaf_specs: list[dict[str, Any]] = []
        for root_spec in outline_specs:
            for sub_spec in root_spec["subsections"]:
                leaf_specs.append(sub_spec)

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

    # ------------------------------------------------------------------
    # Structure derivation
    # ------------------------------------------------------------------
    def _derive_section_specs(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
    ) -> list[dict[str, Any]]:
        """Derive the top-level section specifications from the chunk content.

        The language-model path is attempted first when it is enabled and a
        model is available; otherwise the structure comes from the passages
        themselves.
        """
        if self.use_dspy:
            lm_specs = self._derive_section_specs_with_lm(clean_topic, chunks)
            if lm_specs is not None:
                return lm_specs

        return self._derive_section_specs_from_chunks(clean_topic, chunks)

    def _derive_section_specs_with_lm(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
    ) -> list[dict[str, Any]] | None:
        """Ask the configured language model for section headings.

        Returns ``None`` when no language model is configured, so the caller
        can fall back to the deterministic derivation. A model that is
        configured but fails is a real failure and raises.
        """
        from lmeditor.llm import is_configured, require_lm

        if not is_configured():
            logger.info(
                "Outline generation is running without a language model; "
                "deriving the section headings from the chunk content instead."
            )
            return None

        require_lm("outline synthesis")

        passages = "\n\n".join(
            f"--- PASSAGE {idx + 1} ---\n{chunk.text}" for idx, chunk in enumerate(chunks)
        )
        try:
            program = self._ensure_outline_program()
            prediction = program(topic=clean_topic, passages=passages)
            raw = str(getattr(prediction, "outline_json", "") or "")
        except Exception as exc:
            raise OutlineGenerationError(
                f"Language model failed to propose section headings: {exc}",
                details={"topic": clean_topic, "chunk_count": len(chunks)},
            ) from exc

        sections = self._parse_lm_sections(raw, clean_topic, chunks)
        if not sections:
            raise OutlineGenerationError(
                "Language model returned no usable section headings.",
                details={"topic": clean_topic, "raw_output": raw[:500]},
            )

        return self._build_root_specs(clean_topic, chunks, sections)

    @staticmethod
    def _ensure_outline_program() -> Any:
        """Build (once) the DSPy program that proposes section headings."""
        global _OUTLINE_PROGRAM  # noqa: PLW0603 - module-level cache of the program

        if _OUTLINE_PROGRAM is None:
            import dspy

            signature = dspy.Signature(
                "topic: str, passages: str -> outline_json: str"
            ).with_instructions(
                "Propose the section headings for a manuscript about the topic, "
                "grounded in the supplied passages. Answer with a JSON array of "
                'objects, each with "title", "description" and "keywords" (a '
                "list of terms that appear in the passages the section covers). "
                "Use between 2 and 5 sections and name each one after the "
                "material it covers, never after a generic academic template."
            )
            _OUTLINE_PROGRAM = dspy.Predict(signature)
        return _OUTLINE_PROGRAM

    @staticmethod
    def _parse_lm_sections(
        raw: str,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
    ) -> list[dict[str, Any]]:
        """Parse the model's JSON answer into section specifications."""
        payload = raw.strip()
        start = payload.find("[")
        end = payload.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []

        sections: list[dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            if not title:
                continue
            description = str(entry.get("description", "")).strip()
            keywords_raw = entry.get("keywords", [])
            keywords = (
                [str(k).strip().lower() for k in keywords_raw if str(k).strip()]
                if isinstance(keywords_raw, list)
                else []
            )
            sections.append({"title": title, "description": description, "keywords": keywords})

        if not sections:
            return []

        # Assign the chunks to the section whose keywords they match best.
        assignments: list[list[SourceChunk]] = [[] for _ in sections]
        for chunk in chunks:
            lowered = chunk.text.lower()
            best_index = -1
            best_score = 0
            for index, section in enumerate(sections):
                score = sum(1 for keyword in section["keywords"] if keyword in lowered)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index >= 0:
                assignments[best_index].append(chunk)
            else:
                assignments[0].append(chunk)

        # A section with no chunk of its own is dropped rather than shipped empty.
        kept: list[dict[str, Any]] = []
        kept_assignments: list[list[SourceChunk]] = []
        for section, members in zip(sections, assignments):
            if not members:
                continue
            kept.append(section)
            kept_assignments.append(members)

        if not kept:
            kept = [{"title": clean_topic, "description": "", "keywords": []}]
            kept_assignments = [list(chunks)]

        return [
            {"title": section["title"], "description": section["description"], "chunks": members}
            for section, members in zip(kept, kept_assignments)
        ]

    def _derive_section_specs_from_chunks(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
    ) -> list[dict[str, Any]]:
        """Derive the section structure from the chunk content itself.

        Headings that the document already carries win; otherwise the
        passages are clustered by their shared vocabulary and each cluster is
        named after its dominant terms.
        """
        groups = self._group_chunks(chunks)
        sections: list[dict[str, Any]] = []
        for label, members in groups:
            sections.append(
                {
                    "title": label,
                    "description": self._describe(members),
                    "chunks": members,
                }
            )
        return self._build_root_specs(clean_topic, chunks, sections)

    @staticmethod
    def _group_chunks(chunks: Sequence[SourceChunk]) -> list[tuple[str, list[SourceChunk]]]:
        """Group the chunks into topical sections.

        Headings that the passages themselves carry are preferred, because
        they are the author's own organisation of the material. Passages
        without a heading are clustered by vocabulary.
        """
        headings_per_chunk = [detect_headings(chunk.text) for chunk in chunks]
        if any(headings for headings in headings_per_chunk):
            groups: dict[str, list[SourceChunk]] = {}
            order: list[str] = []
            loose: list[SourceChunk] = []
            for chunk, headings in zip(chunks, headings_per_chunk):
                if headings:
                    heading = headings[0]
                    if heading not in groups:
                        groups[heading] = []
                        order.append(heading)
                    groups[heading].append(chunk)
                else:
                    loose.append(chunk)

            result: list[tuple[str, list[SourceChunk]]] = [(heading, groups[heading]) for heading in order]
            if loose:
                for label, members in OutlineGenerator._cluster_groups(loose):
                    result.append((label, members))
            return result

        return OutlineGenerator._cluster_groups(list(chunks))

    @staticmethod
    def _cluster_groups(chunks: Sequence[SourceChunk]) -> list[tuple[str, list[SourceChunk]]]:
        """Cluster chunks by shared vocabulary and name each cluster."""
        if not chunks:
            return []
        if len(chunks) == 1:
            chunk = chunks[0]
            terms = salient_terms(chunk.text, limit=2)
            label = title_case_terms(terms) if terms else "Source Material"
            return [(label, [chunk])]

        num_clusters = max(1, min(MAX_ROOT_SECTIONS, (len(chunks) + 1) // 2))
        clusters = cluster_by_terms([chunk.text for chunk in chunks], num_clusters)

        groups: list[tuple[str, list[SourceChunk]]] = []
        for indices in clusters:
            members = [chunks[index] for index in indices]
            terms = salient_terms(
                " ".join(member.text for member in members),
                limit=2,
            )
            terms = [term for term in terms if term not in GENERIC_TERMS]
            label = title_case_terms(terms) if terms else "Source Material"
            groups.append((label, members))
        return groups

    @staticmethod
    def _describe(chunks: Sequence[SourceChunk]) -> str:
        """Describe a group of chunks with the findings they actually state."""
        findings: list[str] = []
        for chunk in chunks:
            findings.extend(extract_findings(chunk.text, limit=2))
        if findings:
            joined = "; ".join(findings[:3])
            return f"Source passages reporting: {joined}"
        terms = salient_terms(" ".join(chunk.text for chunk in chunks), limit=4)
        if terms:
            return f"Source passages covering {', '.join(terms)}."
        return "Source passages grouped by shared subject matter."

    def _build_root_specs(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
        sections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Turn derived sections into the root/leaf specification tree."""
        root_specs: list[dict[str, Any]] = []
        for root_idx, section in enumerate(sections[:MAX_ROOT_SECTIONS]):
            members: list[SourceChunk] = list(section.get("chunks") or [])
            title = str(section["title"]).strip() or clean_topic
            description = str(section.get("description", "")).strip() or self._describe(members)

            leaf_specs = self._derive_leaf_specs(root_idx, title, members)
            root_specs.append(
                {
                    "id": f"sec_{root_idx + 1:02d}",
                    "title": f"{root_idx + 1}. {title}",
                    "description": description,
                    "subsections": leaf_specs,
                }
            )
        return root_specs

    def _derive_leaf_specs(
        self,
        root_idx: int,
        root_title: str,
        members: Sequence[SourceChunk],
    ) -> list[dict[str, Any]]:
        """Split a section's chunks into at most two leaves."""
        if not members:
            return [
                {
                    "id": f"sec_{root_idx + 1:02d}_01",
                    "title": f"{root_idx + 1}.1 {root_title}",
                    "description": "No passage was assigned to this section.",
                    "keywords": [],
                    "chunk_ids": [],
                }
            ]

        groups: list[list[SourceChunk]] = []
        if len(members) == 1:
            groups = [list(members)]
        else:
            clusters = cluster_by_terms(
                [chunk.text for chunk in members],
                min(MAX_LEAVES_PER_ROOT, len(members)),
            )
            groups = [[members[index] for index in indices] for indices in clusters]
            groups = [group for group in groups if group]

        leaf_specs: list[dict[str, Any]] = []
        for leaf_idx, group in enumerate(groups):
            terms = salient_terms(" ".join(chunk.text for chunk in group), limit=2)
            terms = [term for term in terms if term not in GENERIC_TERMS]
            label = title_case_terms(terms) if terms else root_title
            leaf_specs.append(
                {
                    "id": f"sec_{root_idx + 1:02d}_{leaf_idx + 1:02d}",
                    "title": f"{root_idx + 1}.{leaf_idx + 1} {label}",
                    "description": self._describe(group),
                    "keywords": [term.lower() for term in terms],
                    "chunk_ids": [chunk.chunk_id for chunk in group],
                }
            )
        return leaf_specs

    def _assign_chunks_to_leaves(
        self,
        chunks: Sequence[SourceChunk],
        leaf_specs: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """Map candidate chunks to leaf sections based on keyword relevance and balanced distribution."""
        assignments: dict[str, list[str]] = {spec["id"]: [] for spec in leaf_specs}
        num_leaves = len(leaf_specs)
        if num_leaves == 0:
            return assignments

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        known_ids = set(chunk_ids)

        # 1. Honour the assignment the derivation already made.
        assigned_chunk_ids: set[str] = set()
        for spec in leaf_specs:
            for chunk_id in spec.get("chunk_ids", []):
                if chunk_id in known_ids and chunk_id not in assigned_chunk_ids:
                    assignments[spec["id"]].append(chunk_id)
                    assigned_chunk_ids.add(chunk_id)

        # 2. Any chunk that no leaf claimed is placed by keyword relevance.
        unassigned = [chunk for chunk in chunks if chunk.chunk_id not in assigned_chunk_ids]
        for chunk in unassigned:
            chunk_text_lower = chunk.text.lower()
            best_leaf_id: str | None = None
            max_score = 0
            for spec in leaf_specs:
                keywords: list[str] = spec.get("keywords", [])
                score = sum(1 for kw in keywords if kw in chunk_text_lower)
                if score > max_score:
                    max_score = score
                    best_leaf_id = spec["id"]
            if best_leaf_id is not None and max_score > 0:
                assignments[best_leaf_id].append(chunk.chunk_id)
                assigned_chunk_ids.add(chunk.chunk_id)

        # 3. Ensure all chunks are assigned (round-robin for any unassigned chunks)
        leftovers = [chunk for chunk in chunks if chunk.chunk_id not in assigned_chunk_ids]
        for idx, chunk in enumerate(leftovers):
            target_leaf_id = leaf_specs[idx % num_leaves]["id"]
            assignments[target_leaf_id].append(chunk.chunk_id)

        # 4. If any leaf has 0 chunks, ensure it receives at least one candidate chunk
        for idx, spec in enumerate(leaf_specs):
            leaf_id = spec["id"]
            if assignments[leaf_id]:
                continue
            # Prefer a chunk that no other leaf holds, so that the normal case
            # keeps every chunk in exactly one leaf.
            elsewhere = {cid for ids in assignments.values() for cid in ids}
            spare = [cid for cid in chunk_ids if cid not in elsewhere]
            pool = spare or chunk_ids
            assignments[leaf_id].append(pool[idx % len(pool)])

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
    "MAX_LEAVES_PER_ROOT",
    "MAX_ROOT_SECTIONS",
    "HierarchicalOutlineGenerator",
    "OutlineGenerator",
    "generate_outline",
]
