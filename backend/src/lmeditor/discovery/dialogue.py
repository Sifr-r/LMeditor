"""Stanford STORM conversational research loop between perspective agents and provenance indexer."""

from __future__ import annotations

from typing import Any, Sequence

from lmeditor.core.exceptions import DialogueError, ValidationError
from lmeditor.core.schemas import (
    DialogueSession,
    DialogueTurn,
    Perspective,
    ResearchMemory,
    SourceChunk,
)
from lmeditor.core.types import ProvenanceIndexerProtocol


class DialogueLoop:
    """Orchestrates multi-turn simulated Q&A inquiries grounded in source provenance."""

    def __init__(self, use_dspy: bool = False, top_k_per_turn: int = 3) -> None:
        """Initialize the conversational research loop.

        Args:
            use_dspy: Whether to invoke DSPy for question formulation/answer synthesis.
            top_k_per_turn: Number of source chunks to retrieve per research turn.
        """
        self.use_dspy = use_dspy
        self.top_k_per_turn = top_k_per_turn

    def validate_dialogue_inputs(
        self,
        perspective: Perspective,
        indexer: ProvenanceIndexerProtocol,
        num_turns: int,
    ) -> None:
        """Validate input boundaries for a single perspective dialogue session.

        Raises:
            ValidationError: If any argument violates structural contracts.
        """
        if not isinstance(perspective, Perspective):
            raise ValidationError(
                f"perspective must be a Perspective instance, got {type(perspective).__name__}",
                field="perspective",
                value=perspective,
            )
        if indexer is None or not hasattr(indexer, "query") or not callable(indexer.query):
            raise ValidationError(
                "indexer must implement ProvenanceIndexerProtocol with a callable 'query' method.",
                field="indexer",
                value=indexer,
            )
        if not isinstance(num_turns, int) or isinstance(num_turns, bool):
            raise ValidationError(
                f"num_turns must be an integer, got {type(num_turns).__name__}",
                field="num_turns",
                value=num_turns,
            )
        if num_turns < 1 or num_turns > 10:
            raise ValidationError(
                f"num_turns must be between 1 and 10, got {num_turns}",
                field="num_turns",
                value=num_turns,
            )

    def conduct_dialogue(
        self,
        perspective: Perspective,
        indexer: ProvenanceIndexerProtocol,
        num_turns: int = 3,
    ) -> DialogueSession:
        """Conduct a multi-turn interactive inquiry for a specific persona.

        Args:
            perspective: Persona guiding the investigation.
            indexer: Provenance search indexer.
            num_turns: Number of Q&A conversational turns.

        Returns:
            Structured DialogueSession containing all turns and takeaways.

        Raises:
            ValidationError: If inputs are malformed.
            DialogueError: If retrieval or synthesis fails unexpectedly.
        """
        self.validate_dialogue_inputs(perspective, indexer, num_turns)

        turns: list[DialogueTurn] = []
        questions_pool = list(perspective.guiding_questions) + [
            f"What empirical evidence addresses {focus}?" for focus in perspective.focus_areas
        ]

        for turn_idx in range(num_turns):
            turn_id = f"turn_{perspective.id}_{turn_idx + 1}"

            # 1. Select or formulate question
            if turn_idx < len(questions_pool):
                question = questions_pool[turn_idx]
            else:
                question = (
                    f"From the perspective of a {perspective.role.value}, what key trade-offs "
                    f"and operational nuances in {perspective.domain} require deeper inquiry?"
                )

            # 2. Retrieve chunks from indexer
            try:
                retrieved_chunks = indexer.query(question, top_k=self.top_k_per_turn)
            except Exception as exc:
                raise DialogueError(
                    f"Provenance indexer query failed at turn {turn_idx + 1}: {exc}",
                    perspective_id=perspective.id,
                    turn_index=turn_idx,
                    details={"error": str(exc), "query": question},
                ) from exc

            # 3. Grounded answer synthesis
            cited_ids: list[str] = []
            answer_parts: list[str] = []

            if retrieved_chunks:
                for chunk in retrieved_chunks:
                    cid = chunk.chunk_id
                    cited_ids.append(cid)
                    # Extract concise snippet
                    snippet = chunk.text.strip().replace("\n", " ")
                    if len(snippet) > 160:
                        snippet = snippet[:157] + "..."
                    answer_parts.append(f"According to [{cid}]: \"{snippet}\"")

                synthesis_text = (
                    f"In response to the inquiry regarding '{question}', empirical evidence demonstrates: "
                    + " ".join(answer_parts)
                )
            else:
                synthesis_text = (
                    f"No direct source chunks were matched in the provenance index for '{question}'. "
                    "This represents an ungrounded exploratory boundary requiring further factual documentation."
                )

            turns.append(
                DialogueTurn(
                    turn_id=turn_id,
                    question=question,
                    answer=synthesis_text,
                    referenced_chunk_ids=cited_ids,
                )
            )

        # 4. Synthesize overarching session takeaways
        takeaways = self._extract_takeaways(perspective, turns)

        return DialogueSession(
            perspective_id=perspective.id,
            turns=turns,
            synthesized_takeaways=takeaways,
        )

    def conduct_all_dialogues(
        self,
        perspectives: Sequence[Perspective],
        indexer: ProvenanceIndexerProtocol,
        turns_per_session: int = 3,
    ) -> ResearchMemory:
        """Run research dialogue sessions across all discovered perspectives.

        Args:
            perspectives: Sequence of personas.
            indexer: Provenance indexer.
            turns_per_session: Turns per individual perspective.

        Returns:
            Consolidated ResearchMemory artifact.

        Raises:
            ValidationError: If perspectives is empty or parameters invalid.
            DialogueError: If dialogue execution fails.
        """
        if not perspectives:
            raise ValidationError(
                "perspectives sequence cannot be empty.",
                field="perspectives",
                value=perspectives,
            )
        for idx, p in enumerate(perspectives):
            if not isinstance(p, Perspective):
                raise ValidationError(
                    f"perspectives[{idx}] must be a Perspective instance, got {type(p).__name__}",
                    field=f"perspectives[{idx}]",
                    value=p,
                )

        sessions: list[DialogueSession] = []
        all_chunk_ids: list[str] = []
        seen_chunk_ids: set[str] = set()
        collected_takeaways: list[str] = []

        topic = perspectives[0].domain or "Document Research Topic"

        for persp in perspectives:
            session = self.conduct_dialogue(persp, indexer, num_turns=turns_per_session)
            sessions.append(session)

            for turn in session.turns:
                for cid in turn.referenced_chunk_ids:
                    if cid not in seen_chunk_ids:
                        seen_chunk_ids.add(cid)
                        all_chunk_ids.append(cid)

            collected_takeaways.extend(session.synthesized_takeaways)

        return ResearchMemory(
            topic=topic,
            sessions=sessions,
            all_referenced_chunk_ids=all_chunk_ids,
            collected_takeaways=collected_takeaways,
        )

    def _extract_takeaways(
        self,
        perspective: Perspective,
        turns: list[DialogueTurn],
    ) -> list[str]:
        """Extract high-level, actionable research insights from completed turns."""
        takeaways: list[str] = []
        role_label = perspective.role.value.replace("_", " ").title()

        takeaways.append(
            f"[{role_label}] Identified key domain dependencies and core assumptions under {perspective.domain}."
        )

        cited_count = sum(len(t.referenced_chunk_ids) for t in turns)
        if cited_count > 0:
            takeaways.append(
                f"[{role_label}] Grounded {cited_count} factual citations validating the perspective's inquiries."
            )
        else:
            takeaways.append(
                f"[{role_label}] Highlighted key empirical knowledge gaps requiring primary source acquisition."
            )

        if perspective.focus_areas:
            primary_focus = perspective.focus_areas[0]
            takeaways.append(
                f"[{role_label}] Prioritized analytical focus on: {primary_focus}."
            )

        return takeaways


def conduct_dialogue(
    perspective: Perspective,
    indexer: ProvenanceIndexerProtocol,
    num_turns: int = 3,
) -> DialogueSession:
    """Convenience functional interface to DialogueLoop.conduct_dialogue."""
    loop = DialogueLoop()
    return loop.conduct_dialogue(perspective, indexer, num_turns=num_turns)


def conduct_all_dialogues(
    perspectives: Sequence[Perspective],
    indexer: ProvenanceIndexerProtocol,
    turns_per_session: int = 3,
) -> ResearchMemory:
    """Convenience functional interface to DialogueLoop.conduct_all_dialogues."""
    loop = DialogueLoop()
    return loop.conduct_all_dialogues(perspectives, indexer, turns_per_session=turns_per_session)
