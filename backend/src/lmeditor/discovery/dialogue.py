"""Conversational research loop between perspective agents and a provenance indexer.

The loop answers a question with the passages it retrieved and nothing else.
Every answer quotes the sentence of a retrieved chunk that actually bears on
the question and names the chunk it came from, so a reader can walk from the
answer back to the source. The takeaways of a session are derived from the
turns that were actually held: which passages carried the weight, what they
said, and which inquiries found nothing at all.

When a language model is configured and ``use_dspy`` is enabled, the model
writes the synthesis sentence that frames the retrieved evidence. The
grounded quotations themselves are always added by the tier, so a model
can add framing but can never introduce an uncited claim.

This module is the **door**: the loop's rules -- the plan of the questions,
the grounded answer, the takeaways and the way the sessions are consolidated
into a research memory -- are the Kotlin tier's, they are implemented once in
``lmeditor.discovery``, and the tier serves them across the NDJSON seam. The
implementation this module used to carry now lives in
``lmeditor._reference.discovery_dialogue``, frozen.

The parity harness does not compare those two sides yet. It declares
``discovery_dialogue`` as a *gap*: it names the Kotlin file that holds the
counterpart, and no check asks both sides the same question about this rule.

Two things stay on this side of the seam, because they cannot be rules:

* the **retrieval**, because the vector index and its ranking are the Python
  tier's; and
* the **language model**, because a model is model-bound work: it reads the
  passages and writes the framing sentence.

There is deliberately no fallback to a Python implementation here: a silent
fallback would hide the exact drift the Kotlin tier exists to remove -- two
implementations that disagree, with only one of them in the request path.
"""

from __future__ import annotations

import logging
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

# These names were bound in this module's namespace and callers may still
# reach them through it, so they stay bound. The rules behind them are the
# tier's: `content_tokens` and `split_sentences` are the lexical analysis the
# tier answers, and `GENERIC_TERMS` is the table the tier ranks against.
from lmeditor.discovery.source_analysis import (
    GENERIC_TERMS,
    content_tokens,
    salient_terms,
    split_sentences,
)
from lmeditor.tier import discovery as tier_discovery

logger = logging.getLogger(__name__)

#: Cached DSPy program used to frame the retrieved evidence.
_SYNTHESIS_PROGRAM: Any = None


class DialogueLoop:
    """Orchestrates multi-turn Q&A inquiries grounded in source provenance.

    Args:
        use_dspy: Whether to use a language model to frame the retrieved
            evidence. The flag only takes effect when a language model is
            configured; without one the answers are assembled from the
            retrieved passages alone.
        top_k_per_turn: Number of source chunks to retrieve per research turn.
    """

    def __init__(self, use_dspy: bool = False, top_k_per_turn: int = 3) -> None:
        """Initialize the conversational research loop.

        Args:
            use_dspy: Whether to invoke DSPy for answer synthesis.
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

        turns = self._plan_turns(perspective, indexer, num_turns)
        memory = tier_discovery.dialogue_memory([(perspective, turns)])
        return memory.sessions[0]

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
                    field="perspectives",
                    value=p,
                )

        planned = [
            (persp, self._plan_turns(persp, indexer, turns_per_session))
            for persp in perspectives
        ]
        return tier_discovery.dialogue_memory(planned)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def _plan_turns(
        self,
        perspective: Perspective,
        indexer: ProvenanceIndexerProtocol,
        num_turns: int,
    ) -> list[dict[str, Any]]:
        """The turns a session will hold, and the passages each of them saw.

        The plan of the questions is the tier's, and the retrieval is this
        tier's: the index and its ranking live here. Each turn therefore
        carries the question that was asked, the passages the index returned
        for it and, when a language model wrote one, the framing sentence it
        produced.
        """
        turns: list[dict[str, Any]] = []

        for question in tier_discovery.dialogue_questions(perspective, num_turns):
            turn_index = len(turns)

            try:
                retrieved_chunks = indexer.query(question, top_k=self.top_k_per_turn)
            except Exception as exc:
                raise DialogueError(
                    f"Provenance indexer query failed at turn {turn_index + 1}: {exc}",
                    perspective_id=perspective.id,
                    turn_index=turn_index,
                    details={"error": str(exc), "query": question},
                ) from exc

            framing = ""
            if self.use_dspy and retrieved_chunks:
                framing = self._synthesize_with_lm(perspective, question, retrieved_chunks)

            turns.append(
                {
                    "question": question,
                    "chunks": [chunk.model_dump(mode="json") for chunk in retrieved_chunks],
                    "framing": framing,
                }
            )

        return turns

    # ------------------------------------------------------------------
    # Answer synthesis
    # ------------------------------------------------------------------
    def _synthesize_with_lm(
        self,
        perspective: Perspective,
        question: str,
        retrieved_chunks: Sequence[SourceChunk],
    ) -> str:
        """Frame the retrieved evidence with the configured language model.

        The model only writes the framing sentence. The grounded quotations
        are added by the tier, so the model cannot introduce an uncited claim.

        An empty answer means "no framing": the tier then uses its own
        deterministic framing, which is what this module did when no language
        model was configured.
        """
        from lmeditor.llm import is_configured, require_lm

        if not is_configured():
            logger.info(
                "Dialogue synthesis is running without a language model; "
                "answering from the retrieved passages alone."
            )
            return ""

        require_lm("dialogue answer synthesis")

        passages = "\n\n".join(
            f"--- PASSAGE {index + 1} [{chunk.chunk_id}] ---\n{chunk.text}"
            for index, chunk in enumerate(retrieved_chunks)
        )
        try:
            program = self._ensure_synthesis_program()
            prediction = program(
                question=question,
                passages=passages,
                role=perspective.role.value,
            )
            framing = str(getattr(prediction, "synthesis", "") or "").strip()
        except Exception as exc:
            raise DialogueError(
                f"Language model failed to synthesise the answer: {exc}",
                perspective_id=perspective.id,
                details={"error": str(exc), "query": question},
            ) from exc

        if not framing:
            raise DialogueError(
                "Language model returned an empty synthesis for the retrieved evidence.",
                perspective_id=perspective.id,
                details={"query": question},
            )
        return framing

    @staticmethod
    def _ensure_synthesis_program() -> Any:
        """Build (once) the DSPy program that frames retrieved evidence."""
        global _SYNTHESIS_PROGRAM  # noqa: PLW0603 - module-level cache of the program

        if _SYNTHESIS_PROGRAM is None:
            import dspy

            signature = dspy.Signature(
                "question: str, passages: str, role: str -> synthesis: str"
            ).with_instructions(
                "Answer the question in one or two sentences, strictly from the "
                "supplied passages. Name the concepts the passages cover. Do not "
                "introduce any fact that is not in the passages."
            )
            _SYNTHESIS_PROGRAM = dspy.Predict(signature)
        return _SYNTHESIS_PROGRAM


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
