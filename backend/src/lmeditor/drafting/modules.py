"""DSPy modules for grounded section drafting with runtime assertions."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Sequence

import dspy

from lmeditor.core.citations import CITATION_PATTERN as CANONICAL_CITATION_PATTERN
from lmeditor.core.citations import extract_chunk_ids
from lmeditor.core.exceptions import (
    DSPyAssertionError,
    DraftingError,
    LMNotConfiguredError,
    PassageBoundsError,
    ValidationError,
)
from lmeditor.core.schemas import OutlineNode, Perspective, SectionDraft, SectionStatus, SourceChunk
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)
from lmeditor.llm import is_configured
from lmeditor.verification.claim_extractor import extract_claims

logger = logging.getLogger(__name__)


class SectionDrafter(dspy.Module):
    """Declarative section drafter enforcing citation presence and passage grounding."""

    # The marker grammar is owned by ``lmeditor.core.citations``; this alias keeps
    # the historical class attribute without a second, divergent regex.
    CITATION_PATTERN = CANONICAL_CITATION_PATTERN

    def __init__(self, use_chain_of_thought: bool = True) -> None:
        super().__init__()
        if use_chain_of_thought:
            self.draft_program = dspy.ChainOfThought(SectionDraftingSignature)
        else:
            self.draft_program = dspy.Predict(SectionDraftingSignature)

    @classmethod
    def extract_citation_ids(cls, text: str) -> list[str]:
        """Extract all unique chunk IDs cited in text, in order of appearance.

        Accepts every form of the shared grammar: ``[#id]``, ``[id]``,
        ``[cite: id]`` and comma-separated lists such as ``[#a, #b]``.
        """
        return extract_chunk_ids(text)

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

    def _call_draft_program(
        self,
        section_node: OutlineNode,
        full_topic: str,
        perspective_str: str,
        formatted_passages: str,
        prior_section_summary: str,
    ) -> Any:
        """Call the drafting program and turn a failure into a typed error.

        The module never returns template prose in place of a generated
        draft: with no language model configured the call fails with
        :class:`~lmeditor.core.exceptions.LMNotConfiguredError`, and a failed
        call with a model configured fails with
        :class:`~lmeditor.core.exceptions.DraftingError`.
        """
        try:
            return self.draft_program(
                topic=full_topic,
                section_title=section_node.title,
                perspective=perspective_str,
                prior_section_summary=prior_section_summary,
                assigned_chunks_text=formatted_passages,
            )
        except Exception as exc:
            if not is_configured():
                raise LMNotConfiguredError("section drafting") from exc
            raise DraftingError(
                section_id=section_node.node_id,
                reason=f"the drafting model call failed: {exc}",
            ) from exc

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

        prediction = self._call_draft_program(
            section_node=section_node,
            full_topic=full_topic,
            perspective_str=perspective_str,
            formatted_passages=formatted_passages,
            prior_section_summary=prior_section_summary,
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

    @staticmethod
    def _claim_fingerprint(text: str) -> Counter[tuple[str, tuple[str, ...]]]:
        """Fingerprint every claim in ``text`` by its assertion and its citations.

        The assertion text is part of the key on purpose: keying on citation ids
        alone would let an LM rewrite "mortality fell by 30 percent [#c1]" into
        "this treatment cures cancer [#c1]" and pass, which is exactly the
        unsupported-rewrite the audit flagged. Text is lowercased, whitespace
        collapsed and trailing punctuation dropped so that trivial punctuation
        churn is tolerated while a changed assertion is not.
        """
        fingerprint: Counter[tuple[str, tuple[str, ...]]] = Counter()
        for claim in extract_claims(text, section_id="macro"):
            assertion = re.sub(r"\s+", " ", claim.text.strip().lower()).strip(" .,;:!?")
            fingerprint[(assertion, tuple(sorted(claim.cited_chunk_ids)))] += 1
        return fingerprint

    def _preserves_verified_content(self, original: str, polished: str) -> bool:
        """Check that polishing did not alter what the manuscript actually claims.

        Comparing the set of citation ids is not enough: an LM can keep every
        marker while rewriting the surrounding assertion into something the
        sources do not support. This guard therefore requires all three of:

        * no citation id that did not already appear in the original draft
          (no invented / phantom markers),
        * identical repeated-citation counts (no dropped or duplicated markers),
        * an unchanged claim set (no new, removed or re-grounded assertions).
        """
        original_ids = set(extract_chunk_ids(original))
        polished_ids = set(extract_chunk_ids(polished))

        invented_ids = sorted(polished_ids - original_ids)
        if invented_ids:
            logger.warning(
                "Macro cohesion rejected: polished manuscript introduced citation "
                "ids absent from the verified draft: %s",
                invented_ids,
            )
            return False

        original_counts = Counter(extract_chunk_ids(original))
        polished_counts = Counter(extract_chunk_ids(polished))
        if original_counts != polished_counts:
            logger.warning(
                "Macro cohesion rejected: repeated citation counts changed "
                "(original=%s, polished=%s)",
                dict(original_counts),
                dict(polished_counts),
            )
            return False

        original_claims = self._claim_fingerprint(original)
        polished_claims = self._claim_fingerprint(polished)
        if original_claims != polished_claims:
            logger.warning(
                "Macro cohesion rejected: claim set drifted during polishing."
            )
            return False

        return True

    def forward(self, full_draft: str, topic: str) -> str:
        """Polish the manuscript, refusing to lose anything that was verified.

        The polished text is accepted only when it preserves the verified
        prose: the claim set, the citation placements and the repeated-citation
        counts must all match the original draft exactly. Otherwise the
        original draft is returned, because a verified-but-unpolished document
        is preferable to an unverified rewrite.

        A failure of the model call is *not* swallowed: with a model
        configured it is raised as a
        :class:`~lmeditor.core.exceptions.DraftingError`, so that a silent
        no-op cannot masquerade as a successful polish. A run with no language
        model configured at all is reported as a warning and returns the
        unpolished manuscript.
        """
        try:
            result = self.cohesion_program(full_draft=full_draft, topic=topic)
        except Exception as exc:
            if not is_configured():
                logger.warning(
                    "Macro cohesion skipped: no language model is configured, so the "
                    "manuscript is returned unpolished."
                )
                return full_draft.strip()
            raise DraftingError(
                section_id="macro_cohesion",
                reason=f"the cohesion model call failed: {exc}",
            ) from exc

        polished_text = getattr(result, "cohesive_manuscript", "") or ""
        if not polished_text.strip():
            raise DraftingError(
                section_id="macro_cohesion",
                reason="the cohesion model returned an empty manuscript.",
            )

        if not self._preserves_verified_content(full_draft, polished_text):
            return full_draft

        return polished_text.strip()


class CitationInserter(dspy.Module):
    """Anchors an ungrounded draft to its source passages.

    The inserter asks the model to attach the citation markers that the
    passages support, and then checks the answer itself: every marker in the
    result has to name one of the passages it was given, so the module
    cannot introduce a phantom citation. It never returns template prose in
    place of a grounded draft -- a failed call raises
    :class:`~lmeditor.core.exceptions.DraftingError`.
    """

    def __init__(self, use_chain_of_thought: bool = False) -> None:
        super().__init__()
        if use_chain_of_thought:
            self.insert_program = dspy.ChainOfThought(CitationInsertionSignature)
        else:
            self.insert_program = dspy.Predict(CitationInsertionSignature)

    def forward(self, draft_text: str, source_passages: Sequence[SourceChunk]) -> str:
        """Insert inline citation markers into ``draft_text``.

        Args:
            draft_text: The prose that needs grounding.
            source_passages: The passages the markers may cite.

        Returns:
            The draft with validated inline citation markers.

        Raises:
            ValidationError: If the draft or the passage list is empty.
            PassageBoundsError: If the result cites a passage it was not given.
            LMNotConfiguredError: If no language model is configured.
            DraftingError: If the model call fails or returns nothing.
        """
        if not isinstance(draft_text, str) or not draft_text.strip():
            raise ValidationError(
                "draft_text must be a non-empty string.", field="draft_text", value=draft_text
            )
        if not source_passages:
            raise ValidationError(
                "source_passages cannot be empty.", field="source_passages", value=source_passages
            )

        valid_chunk_ids = {chunk.chunk_id.lower() for chunk in source_passages}
        formatted_passages = SectionDrafter.format_assigned_chunks(source_passages)

        try:
            result = self.insert_program(
                draft_text=draft_text,
                source_passages=formatted_passages,
            )
        except Exception as exc:
            if not is_configured():
                raise LMNotConfiguredError("citation insertion") from exc
            raise DraftingError(
                section_id="citation_insertion",
                reason=f"the citation insertion model call failed: {exc}",
            ) from exc

        grounded_draft = getattr(result, "grounded_draft", "") or ""
        if not grounded_draft.strip():
            raise DraftingError(
                section_id="citation_insertion",
                reason="the citation insertion model returned an empty draft.",
            )

        cited_ids = extract_chunk_ids(grounded_draft)
        hallucinated_ids = [cid for cid in cited_ids if cid.lower() not in valid_chunk_ids]
        if hallucinated_ids:
            raise PassageBoundsError(
                "Citation insertion referenced passages that were not supplied: "
                f"{hallucinated_ids}",
                section_id="citation_insertion",
                details={
                    "hallucinated_ids": hallucinated_ids,
                    "valid_ids": sorted(valid_chunk_ids),
                },
            )

        return grounded_draft.strip()


__all__ = [
    "CitationInserter",
    "MacroCohesionModule",
    "SectionDrafter",
]
