"""DSPy declarative signatures for section drafting and citation integration."""

from __future__ import annotations

import dspy


class SectionDraftingSignature(dspy.Signature):
    """Draft a rigorous expository section anchored strictly to assigned source passages.

    Every factual proposition must carry an immediate inline citation tag referencing a valid chunk ID:
    Format: [#<chunk_id>] or [cite: <chunk_id>].
    No factual claims may be asserted outside the provided source passages.
    """

    topic: str = dspy.InputField(desc="Overarching document title and research topic")
    section_title: str = dspy.InputField(desc="Current section title and structural objective")
    perspective: str = dspy.InputField(desc="Specific domain perspective or epistemic angle")
    prior_section_summary: str = dspy.InputField(
        desc="Brief summary of preceding sections to preserve narrative continuity"
    )
    assigned_chunks_text: str = dspy.InputField(
        desc="Verbatim source passages prefixed by their hexadecimal chunk IDs"
    )

    section_draft: str = dspy.OutputField(
        desc="Expository text containing inline citation tags: [#<chunk_id>]"
    )
    cited_chunk_ids: list[str] = dspy.OutputField(
        desc="List of unique chunk IDs cited in the generated draft"
    )


class CitationInsertionSignature(dspy.Signature):
    """Anchor an ungrounded or draft sentence to supporting source passages.

    Given draft prose and candidate passages, insert exact inline citation tags [#<chunk_id>]
    immediately after each factual claim that is entailed by the passages.
    """

    draft_text: str = dspy.InputField(desc="Draft text requiring citation grounding")
    source_passages: str = dspy.InputField(desc="Available source chunks with chunk IDs")

    grounded_draft: str = dspy.OutputField(
        desc="Draft text with validated inline citation tags inserted"
    )


class MacroCohesionSignature(dspy.Signature):
    """Harmonize transitions and expository flow across drafted document sections.

    Polish stylistic transitions between sections while strictly preserving all
    existing inline citation tags [#<chunk_id>]. Do NOT delete or invent citation tags.
    """

    full_draft: str = dspy.InputField(desc="Combined draft sections with inline citations")
    topic: str = dspy.InputField(desc="Overall document topic")

    cohesive_manuscript: str = dspy.OutputField(
        desc="Polished expository document with all citation tags preserved"
    )


__all__ = [
    "CitationInsertionSignature",
    "MacroCohesionSignature",
    "SectionDraftingSignature",
]
