"""The frozen conversational research loop.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.

It is frozen. The plan of the questions, the grounded answer, the takeaways
and the way the sessions are consolidated into a research memory are the
tier's rules now: the live module ``lmeditor.discovery.dialogue`` is the door
to them, and this copy is the yardstick they are measured against.

The lexical analysis this loop reads is frozen next door, in
``lmeditor._reference.discovery_source_analysis``: it is the same frozen copy
the live door answers with, so the witness never reaches the tier.
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
from lmeditor._reference.discovery_source_analysis import (
    GENERIC_TERMS,
    content_tokens,
    salient_terms,
    split_sentences,
)

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
            synthesis_text, cited_ids = self._synthesize_answer(perspective, question, retrieved_chunks)

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
                    field="perspectives",
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

    # ------------------------------------------------------------------
    # Answer synthesis
    # ------------------------------------------------------------------
    def _synthesize_answer(
        self,
        perspective: Perspective,
        question: str,
        retrieved_chunks: Sequence[SourceChunk],
    ) -> tuple[str, list[str]]:
        """Build an answer from the retrieved passages.

        The answer quotes the passage sentences that bear on the question and
        names the chunk each quotation came from.
        """
        if not retrieved_chunks:
            return (
                f"No direct source chunks were matched in the provenance index for '{question}'. "
                "This represents an ungrounded exploratory boundary requiring further factual documentation.",
                [],
            )

        cited_ids: list[str] = []
        answer_parts: list[str] = []
        evidence_terms: list[str] = []
        quoted: list[tuple[str, str]] = []

        for chunk in retrieved_chunks:
            cid = chunk.chunk_id
            if cid not in cited_ids:
                cited_ids.append(cid)
            quote = self._select_quote(chunk, question)
            quoted.append((cid, quote))
            answer_parts.append(f"According to [{cid}]: \"{quote}\"")
            evidence_terms.extend(content_tokens(chunk.text))

        grounding = self._describe_evidence(evidence_terms)

        if self.use_dspy:
            framing = self._synthesize_with_lm(perspective, question, retrieved_chunks)
        else:
            framing = (
                f"In response to the inquiry regarding '{question}', the retrieved evidence "
                f"{grounding}:"
            )

        return f"{framing} " + " ".join(answer_parts), cited_ids

    @staticmethod
    def _select_quote(chunk: SourceChunk, question: str) -> str:
        """Pick the sentence of ``chunk`` that bears most on ``question``."""
        sentences = split_sentences(chunk.text) or [chunk.text.strip()]
        question_tokens = set(content_tokens(question))
        best_sentence = sentences[0]
        best_score = -1
        for sentence in sentences:
            sentence_tokens = set(content_tokens(sentence))
            if not sentence_tokens:
                continue
            score = len(question_tokens & sentence_tokens)
            if score > best_score:
                best_score = score
                best_sentence = sentence

        quote = " ".join(best_sentence.split())
        if len(quote) > 240:
            quote = quote[:237] + "..."
        return quote

    @staticmethod
    def _describe_evidence(tokens: Sequence[str]) -> str:
        """Describe what the retrieved passages collectively cover."""
        from collections import Counter

        counts = Counter(token for token in tokens if token not in GENERIC_TERMS)
        if not counts:
            return "reports the following statements"
        top = [term for term, _ in counts.most_common(3)]
        return f"centres on {', '.join(top)} and reports the following statements"

    def _synthesize_with_lm(
        self,
        perspective: Perspective,
        question: str,
        retrieved_chunks: Sequence[SourceChunk],
    ) -> str:
        """Frame the retrieved evidence with the configured language model.

        The model only writes the framing sentence. The grounded quotations
        are appended by the caller, so the model cannot introduce an
        uncited claim.
        """
        from lmeditor.llm import is_configured, require_lm

        if not is_configured():
            logger.info(
                "Dialogue synthesis is running without a language model; "
                "answering from the retrieved passages alone."
            )
            return (
                f"In response to the inquiry regarding '{question}', the retrieved evidence "
                f"{self._describe_evidence([token for chunk in retrieved_chunks for token in content_tokens(chunk.text)])}:"
            )

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

    # ------------------------------------------------------------------
    # Takeaway synthesis
    # ------------------------------------------------------------------
    def _extract_takeaways(
        self,
        perspective: Perspective,
        turns: list[DialogueTurn],
    ) -> list[str]:
        """Derive the session takeaways from the turns that were actually held.

        A takeaway names the evidence that carried the session: the passage
        that was cited most, what it said, what the retrieved passages had in
        common, and which inquiries came back empty.
        """
        takeaways: list[str] = []
        role_label = perspective.role.value.replace("_", " ").title()

        evidence: list[tuple[str, str]] = []
        for turn in turns:
            for cid in turn.referenced_chunk_ids:
                quote = self._quote_for_cid(turn.answer, cid)
                if quote:
                    evidence.append((cid, quote))

        if not evidence:
            takeaways.append(
                f"[{role_label}] Found no source passage for {perspective.domain}; "
                "the inquiries in this session remain empirically unsupported."
            )
            return takeaways

        counts: dict[str, int] = {}
        for cid, _ in evidence:
            counts[cid] = counts.get(cid, 0) + 1
        dominant_cid = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
        dominant_quote = next(quote for cid, quote in evidence if cid == dominant_cid)

        takeaways.append(
            f"[{role_label}] The passage that carried this session was [{dominant_cid}], "
            f"which states: \"{dominant_quote}\""
        )

        terms = salient_terms(" ".join(quote for _, quote in evidence), limit=4)
        if terms:
            takeaways.append(
                f"[{role_label}] Across the retrieved evidence the recurring substance is "
                f"{', '.join(terms)}, which bounds what {perspective.domain} can be claimed to show."
            )

        grounded_turns = sum(1 for turn in turns if turn.referenced_chunk_ids)
        if grounded_turns < len(turns):
            takeaways.append(
                f"[{role_label}] {len(turns) - grounded_turns} of {len(turns)} inquiries returned no "
                "matching passage and remain open."
            )
        else:
            takeaways.append(
                f"[{role_label}] Every one of the {len(turns)} inquiries was answered from a retrieved passage."
            )

        if perspective.focus_areas:
            primary_focus = perspective.focus_areas[0]
            takeaways.append(
                f"[{role_label}] Prioritised analytical focus on: {primary_focus}."
            )

        return takeaways

    @staticmethod
    def _quote_for_cid(answer: str, chunk_id: str) -> str:
        """The quotation the answer attributes to ``chunk_id``."""
        marker = f"[{chunk_id}]"
        index = answer.find(marker)
        if index == -1:
            return ""
        start = answer.find('"', index)
        if start == -1:
            return ""
        end = answer.find('"', start + 1)
        if end == -1:
            return ""
        return answer[start + 1 : end].strip()


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
