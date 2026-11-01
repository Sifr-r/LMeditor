"""The topic outline, as the Kotlin tier implements it.

The outline is *derived*, never assumed: the sections of a manuscript are
named after the material that will actually fill them. How that derivation
works -- how a corpus is grouped into sections, how a passage is assigned to
a leaf, what shape the tree takes -- is a rule, and the rule is Kotlin's: it
is implemented once, in ``lmeditor.discovery``, and the tier serves it across
the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.outliner``, frozen, so the parity
harness can still ask both sides the same question.

The language model stays Python's. A model is model-bound work: it reads the
passages and proposes the section headings. Everything the model does not
decide -- the shape of the tree, the assignment of passages, the boundary
rules -- is the tier's.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.bridge.errors import KotlinDomainError
from lmeditor.core.exceptions import OutlineGenerationError, ValidationError
from lmeditor.core.schemas import (
    OutlineNode,
    OutlineNodeInput,
    Perspective,
    ResearchMemory,
    SourceChunk,
    TopicOutline,
)
from lmeditor.discovery.source_analysis import (
    GENERIC_TERMS,
    cluster_by_terms,
    extract_findings,
    salient_terms,
    title_case_terms,
)
from lmeditor.tier import discovery as tier_discovery

logger = logging.getLogger(__name__)

#: Maximum number of top-level sections derived from a corpus.
#:
#: The tier holds the same limit (``OutlineBuilder.MAX_ROOT_SECTIONS``) and is
#: the one that applies it. This constant is kept because callers and tests
#: import it; reading it from the tier would mean a call across the seam at
#: import time, which no module may do.
MAX_ROOT_SECTIONS: int = 5

#: Maximum number of leaves under a single root section.
#:
#: The tier holds the same limit (``OutlineBuilder.MAX_LEAVES_PER_ROOT``).
MAX_LEAVES_PER_ROOT: int = 2

#: Cached DSPy program used for language-model outline synthesis.
_OUTLINE_PROGRAM: Any = None


class OutlineGenerator:
    """Outline generator that derives its sections from the supplied chunks.

    Args:
        use_dspy: Whether to use a language model to propose the section
            headings. The flag only takes effect when a language model is
            actually configured; without one the generator asks the tier for
            the deterministic, corpus-derived structure.
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

        Args:
            topic: The manuscript's subject.
            research_memory: The research memory the outline is written from.
            available_chunks: The corpus the outline is grounded in.

        Returns:
            The sanitized topic.

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

        **The rule is the tier's.** This is the door to it: the tree is built
        and the passages are assigned in Kotlin, and this method asks for the
        result.

        Args:
            sections: The sections the caller named, in order.
            topic: The document's subject, used for the root description.
            available_chunks: The corpus the sections are grounded in.

        Returns:
            The outline the drafter will write to.
        """
        return tier_discovery.build_outline_from_sections(
            sections,
            topic,
            available_chunks,
        )

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
        themselves. Which one is decided here; how the tree is built is the
        tier's rule.

        Args:
            topic: The manuscript's subject.
            research_memory: The research memory the outline is written from.
                A sequence of personas is a sequence of personas: the tier
                builds the memory from them, so the rule that says how a
                memory is made lives in one place.
            available_chunks: The corpus the outline is grounded in.
            perspectives: The personas the research produced, when the caller
                has no memory to hand over.

        Returns:
            The outline the drafter will write to.

        Raises:
            ValidationError: If the inputs are out of bounds.
            OutlineGenerationError: If the outline cannot be built.
            BridgeUnavailableError: If the Kotlin tier cannot be reached.
        """
        chunks = list(available_chunks or [])
        memory, memory_to_check, named = self._resolve_inputs(
            topic, research_memory, perspectives
        )
        clean_topic = self.validate_inputs(topic, memory_to_check, chunks)
        if named is not None:
            self._require_perspectives(named)

        if self.use_dspy:
            # The model reads the passages and names the sections. The rest of
            # the outline -- the tree and the assignment of passages -- is
            # still built here, because the seam cannot carry the model's
            # answer into the tier.
            outline_specs = self._derive_section_specs_with_lm(clean_topic, chunks)
            if outline_specs is not None:
                return self._outline_from_specs(clean_topic, chunks, outline_specs)

        return self._outline_from_tier(clean_topic, memory, chunks, named)

    # ------------------------------------------------------------------
    # The seam
    # ------------------------------------------------------------------
    def _resolve_inputs(
        self,
        topic: str,
        research_memory: ResearchMemory | Sequence[Any] | None,
        perspectives: Sequence[Any] | None,
    ) -> tuple[ResearchMemory | None, Any, list[Any] | None]:
        """Return the memory to send, the memory to check, and the personas.

        A caller may hand over a research memory or a list of personas. When
        the caller hands over personas, the tier builds the memory from them,
        so the rule that says how a memory is made lives in one place; the
        door then checks only the *shape* of what the tier will receive.
        """
        memory: ResearchMemory | None = None
        named: list[Any] | None = None
        if isinstance(research_memory, ResearchMemory):
            memory = research_memory
        else:
            source = research_memory if research_memory is not None else perspectives
            if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
                named = list(source)

        if named is not None and not named:
            # There is nothing to build a memory from, so the memory is empty.
            memory = _empty_memory(topic)
            named = None

        if memory is not None:
            return memory, memory, None
        if named is not None:
            # The tier builds the memory from the personas, so the door checks
            # only the shape of what it was handed.
            return None, _empty_memory(topic), named
        # Neither a memory nor personas: the boundary check refuses the call,
        # exactly as it always did.
        return None, research_memory, None

    @staticmethod
    def _require_perspectives(named: list[Any]) -> None:
        """Every persona must be one the tier can carry across the seam."""
        for index, item in enumerate(named):
            if not isinstance(item, Perspective):
                raise ValidationError(
                    f"perspectives[{index}] must be a Perspective instance, "
                    f"got {type(item).__name__}",
                    field=f"perspectives[{index}]",
                    value=item,
                )

    def _outline_from_tier(
        self,
        clean_topic: str,
        memory: ResearchMemory | None,
        chunks: list[SourceChunk],
        named: list[Perspective] | None,
    ) -> TopicOutline:
        """The tier's answer: the outline it builds from the corpus."""
        try:
            outline = tier_discovery.build_outline(clean_topic, memory, chunks, named)
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except KotlinDomainError as exc:
            if exc.error_type == "OutlineGenerationError":
                raise OutlineGenerationError(
                    exc.message,
                    node_id=exc.details.get("node_id"),
                    details=dict(exc.details),
                ) from exc
            raise

        valid_chunk_ids = {chunk.chunk_id for chunk in chunks}
        self._assert_grounded(outline, valid_chunk_ids)
        # The caller's own assignment hook still runs, and its answer is held
        # to the same boundary the tier's is held to: a passage the corpus
        # does not carry may never be assigned to a leaf.
        self._assert_grounded_assignment(
            self._assign_chunks_to_leaves(chunks, _leaf_specs(outline)),
            valid_chunk_ids,
        )
        return outline

    @staticmethod
    def _assert_grounded(outline: TopicOutline, valid_chunk_ids: set[str]) -> None:
        """Refuse an outline that assigns a passage the corpus does not carry."""
        for leaf in outline.get_leaves():
            for chunk_id in leaf.assigned_chunk_ids:
                if chunk_id not in valid_chunk_ids:
                    raise OutlineGenerationError(
                        f"Assigned chunk '{chunk_id}' is not in available_chunks.",
                        node_id=leaf.node_id,
                        details={
                            "assigned_chunk_id": chunk_id,
                            "valid_chunk_ids": sorted(valid_chunk_ids),
                        },
                    )

    @staticmethod
    def _assert_grounded_assignment(
        assignments: dict[str, list[str]],
        valid_chunk_ids: set[str],
    ) -> None:
        """Refuse an assignment that names a passage the corpus does not carry."""
        for leaf_id, chunk_ids in assignments.items():
            for chunk_id in chunk_ids:
                if chunk_id not in valid_chunk_ids:
                    raise OutlineGenerationError(
                        f"Assigned chunk '{chunk_id}' is not in available_chunks.",
                        node_id=leaf_id,
                        details={
                            "assigned_chunk_id": chunk_id,
                            "valid_chunk_ids": sorted(valid_chunk_ids),
                        },
                    )

    # ------------------------------------------------------------------
    # Language-model path
    # ------------------------------------------------------------------
    def _derive_section_specs_with_lm(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
    ) -> list[dict[str, Any]] | None:
        """Ask the configured language model for section headings.

        Returns ``None`` when no language model is configured, so the caller
        can ask the tier for the deterministic derivation. A model that is
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

    def _outline_from_specs(
        self,
        clean_topic: str,
        chunks: Sequence[SourceChunk],
        outline_specs: list[dict[str, Any]],
    ) -> TopicOutline:
        """Build the tree from the section specifications the model proposed.

        The model named the sections; the passages are assigned to them here.
        This path still owns the rules the seam cannot carry: the model's
        answer has to reach the tier as prose for the tier to own it, and the
        seam has no method for that yet.
        """
        leaf_specs: list[dict[str, Any]] = []
        for root_spec in outline_specs:
            for sub_spec in root_spec["subsections"]:
                leaf_specs.append(sub_spec)

        leaf_chunk_assignments = self._assign_chunks_to_leaves(chunks, leaf_specs)

        valid_chunk_map: dict[str, SourceChunk] = {c.chunk_id: c for c in chunks}

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

        # Verify DAG acyclicity and structural constraints
        outline.validate_dag()

        return outline

    # ------------------------------------------------------------------
    # Structure the model path still owns
    # ------------------------------------------------------------------
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
        """Turn the model's sections into the root/leaf specification tree."""
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
        """Map candidate chunks to leaf sections based on keyword relevance and balanced distribution.

        The tier owns this rule for the deterministic path. It is still here
        because the language-model path cannot hand the model's answer to the
        tier, and because it is the hook a caller has always been able to
        override.
        """
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


def _empty_memory(topic: str) -> ResearchMemory:
    """A research memory with nothing in it.

    The tier builds the memory from the personas when the caller hands over
    personas. When there are none -- or when the door only needs a memory of
    the right shape to look at -- the memory is empty: the tier reads the
    fact that a memory is there, never what it carries.
    """
    return ResearchMemory(
        topic=topic.strip() or "research",
        sessions=[],
        all_referenced_chunk_ids=[],
        collected_takeaways=[],
    )


def _leaf_specs(outline: TopicOutline) -> list[dict[str, Any]]:
    """The leaves of an outline, as the leaf specifications the assignment rule reads."""
    return [
        {
            "id": leaf.node_id,
            "title": leaf.title,
            "description": leaf.description,
            "keywords": [],
            "chunk_ids": list(leaf.assigned_chunk_ids),
        }
        for leaf in outline.get_leaves()
    ]


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
