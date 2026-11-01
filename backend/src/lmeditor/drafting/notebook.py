"""The DSPy module that writes a notebook's answer.

The writing of an answer is DSPy's job here, as the writing of a section is:
the module asks the model for prose that is anchored to the passages it was
given, and it hands back what the model wrote -- nothing more. It decides no
rule about the answer: which passages the answer may lean on, and whether the
answer is supported, are decided elsewhere, by the tier.

The passage field is named ``assigned_chunks_text`` on purpose: it is the
name the offline stub reads, so the whole ask can be proved with no model
provider at all.
"""

from __future__ import annotations

import dspy

from lmeditor.core.exceptions import DraftingError, LMNotConfiguredError, ValidationError
from lmeditor.drafting.signatures import NotebookAnswerSignature
from lmeditor.llm import is_configured


class NotebookAnswerer(dspy.Module):  # type: ignore[misc]
    """Writes the answer to one question from the passages it was given.

    The module never writes prose of its own: with no language model
    configured the call fails with
    :class:`~lmeditor.core.exceptions.LMNotConfiguredError`, and a failed
    call with a model configured fails with
    :class:`~lmeditor.core.exceptions.DraftingError`. An answer that says
    the passages do not answer the question is a real answer, and the module
    passes it on as it is.
    """

    def __init__(self, use_chain_of_thought: bool = False) -> None:
        super().__init__()
        if use_chain_of_thought:
            self.answer_program = dspy.ChainOfThought(NotebookAnswerSignature)
        else:
            self.answer_program = dspy.Predict(NotebookAnswerSignature)

    def forward(self, question: str, assigned_chunks_text: str) -> tuple[str, list[str]]:
        """Answer ``question`` from the passages in ``assigned_chunks_text``.

        Args:
            question: The operator's question.
            assigned_chunks_text: The notebook's own passages, each prefixed
                by its chunk id.

        Returns:
            The answer's text and the chunk ids the model reported citing it.
            The ids are what the model says; which of them the answer may
            actually lean on is decided by the caller, against the passages
            the notebook retrieved.

        Raises:
            ValidationError: The question or the passages are empty.
            LMNotConfiguredError: No language model is configured.
            DraftingError: The model call failed.
        """
        if not isinstance(question, str) or not question.strip():
            raise ValidationError(
                "a notebook answer needs a question.",
                field="question",
                value=question,
            )
        if not isinstance(assigned_chunks_text, str) or not assigned_chunks_text.strip():
            raise ValidationError(
                "a notebook answer needs at least one passage.",
                field="assigned_chunks_text",
                value=assigned_chunks_text,
            )

        try:
            result = self.answer_program(
                question=question,
                assigned_chunks_text=assigned_chunks_text,
            )
        except Exception as exc:
            if not is_configured():
                raise LMNotConfiguredError("notebook answering") from exc
            raise DraftingError(
                section_id="notebook_answer",
                reason=f"the notebook answer model call failed: {exc}",
            ) from exc

        answer = getattr(result, "answer", "") or ""
        cited = getattr(result, "cited_chunk_ids", []) or []
        return str(answer), [str(chunk_id) for chunk_id in cited]


__all__ = ["NotebookAnswerer"]
