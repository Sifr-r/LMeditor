"""DSPy modules for grounded section drafting with runtime assertions."""

from __future__ import annotations

import re
from typing import Any, Sequence

import dspy

from lmeditor.core.exceptions import DSPyAssertionError, PassageBoundsError
from lmeditor.core.schemas import OutlineNode, Perspective, SectionDraft, SectionStatus, SourceChunk
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)


class SectionDrafter(dspy.Module):
    """Declarative section drafter enforcing citation presence and passage grounding."""

    CITATION_PATTERN = re.compile(r"\[(?:#|cite:\s*)?([a-f0-9]{64})\]", re.IGNORECASE)

    def __init__(self, use_chain_of_thought: bool = True) -> None:
        super().__init__()
        if use_chain_of_thought:
            self.draft_program = dspy.ChainOfThought(SectionDraftingSignature)
        else:
            self.draft_program = dspy.Predict(SectionDraftingSignature)

    @classmethod
    def extract_citation_ids(cls, text: str) -> list[str]:
        """Extract all unique hexadecimal chunk IDs cited in text."""
        return list(dict.fromkeys(cid.lower() for cid in cls.CITATION_PATTERN.findall(text)))

    @classmethod
    def format_assigned_chunks(cls, chunks: Sequence[SourceChunk]) -> str:
        """Format source chunks with explicit chunk ID headers for prompt ingestion."""
        formatted_blocks: list[str] = []
        for idx, chunk in enumerate(chunks, 1):
            header = f"--- PASSAGE {idx} [#CHUNK_ID: {chunk.chunk_id}] ---"
            if chunk.document_title:
                header += f" (Source: {chunk.document_title})"
            formatted_blocks.append(f"{header}\n{chunk.text.strip()}\n")
        return "\n".join(formatted_blocks)

    def forward(
        self,
        topic: str,
        section_node: OutlineNode,
        perspective: Perspective | str,
        assigned_chunks: Sequence[SourceChunk],
        prior_section_summary: str = "",
        revision_feedback: str | None = None,
    ) -> SectionDraft:
        """Draft a section anchored to assigned source passages with runtime assertions."""
        if not assigned_chunks:
            raise PassageBoundsError(
                f"Cannot draft section '{section_node.title}' without assigned source chunks.",
                section_id=section_node.node_id,
            )

        perspective_str = (
            perspective.description
            if isinstance(perspective, Perspective)
            else str(perspective)
        )

        valid_chunk_ids = {c.chunk_id.lower() for c in assigned_chunks}
        formatted_passages = self.format_assigned_chunks(assigned_chunks)

        full_topic = topic
        if revision_feedback:
            full_topic = f"{topic} [REVISION FEEDBACK: {revision_feedback}]"

        prediction = self.draft_program(
            topic=full_topic,
            section_title=section_node.title,
            perspective=perspective_str,
            prior_section_summary=prior_section_summary,
            assigned_chunks_text=formatted_passages,
        )

        raw_content = getattr(prediction, "section_draft", "") or ""
        cited_ids = self.extract_citation_ids(raw_content)

        # DSPy Programmatic Runtime Assertions
        if not cited_ids:
            raise DSPyAssertionError(
                f"Citation presence assertion failed for section '{section_node.title}': "
                f"Section must contain at least one inline citation [#chunk_id]."
            )

        # Check for hallucinated chunk IDs outside the assigned passage set
        hallucinated_ids = [cid for cid in cited_ids if cid not in valid_chunk_ids]
        if hallucinated_ids:
            raise PassageBoundsError(
                f"Passage bounds assertion failed for section '{section_node.title}': "
                f"Detected citations referencing unassigned or hallucinated chunks: {hallucinated_ids}",
                section_id=section_node.node_id,
                details={"hallucinated_ids": hallucinated_ids, "valid_ids": list(valid_chunk_ids)},
            )

        return SectionDraft(
            section_id=section_node.node_id,
            title=section_node.title,
            content=raw_content.strip(),
            order=section_node.order,
            cited_chunk_ids=cited_ids,
            status=SectionStatus.DRAFTED,
        )


class MacroCohesionModule(dspy.Module):
    """Harmonizes expository narrative flow while strictly safeguarding inline citations."""

    def __init__(self) -> None:
        super().__init__()
        self.cohesion_program = dspy.Predict(MacroCohesionSignature)

    def forward(self, full_draft: str, topic: str) -> str:
        """Polish full manuscript and verify zero citation degradation."""
        original_citations = set(SectionDrafter.extract_citation_ids(full_draft))

        try:
            result = self.cohesion_program(full_draft=full_draft, topic=topic)
            polished_text = getattr(result, "cohesive_manuscript", "") or full_draft
        except Exception:
            # Graceful fallback to original draft if LM is unconfigured or call fails
            return full_draft.strip()

        polished_citations = set(SectionDrafter.extract_citation_ids(polished_text))

        # Guarantee no citations were lost during macro polishing
        missing_citations = original_citations - polished_citations
        if missing_citations:
            # If the macro polisher dropped citations, preserve original draft to avoid regression
            return full_draft

        return polished_text.strip()


__all__ = [
    "MacroCohesionModule",
    "SectionDrafter",
]
