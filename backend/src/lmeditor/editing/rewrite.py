"""The language-model seam for targeted inline revisions.

A small edit -- "make this tighter", "cite the 2023 study here" -- is a
generative act, and generative acts need a model. This module owns that one
call and nothing else: it takes an instruction, the exact text to revise and
the passages the revision may be grounded in, and returns the revised text.

Two rules hold everywhere in this module:

* **LMeditor never fabricates prose in place of a model call.** With no
  language model configured the rewrite fails with
  :class:`~lmeditor.core.exceptions.LMNotConfiguredError`; there is no
  template fallback that a reader could mistake for generated output.
* **The model is never handed the corpus.** It is handed the passages that
  were retrieved for this edit and nothing more, so a revision is grounded in
  a bounded slice of the sources rather than in everything the app ever read.

The seam is a :class:`RewriteFn` protocol, so the editor can be given a
double in a test and the real model in production without either side
knowing about the other.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, Sequence, runtime_checkable

from lmeditor.core.exceptions import (
    InlineEditRejectedError,
    LMNotConfiguredError,
    ValidationError,
)
from lmeditor.core.schemas import SourceChunk
from lmeditor.drafting.modules import SectionDrafter
from lmeditor.llm import factory as lm_factory

logger = logging.getLogger(__name__)

#: The DSPy signature of the rewrite program.
#:
#: The field names are the house vocabulary rather than names of this
#: module's own: ``draft_text`` is the passage to work on, ``assigned_chunks_text``
#: is the passages the work is grounded in, and ``grounded_draft`` is the
#: passage with its citations attached. The drafter, the citation inserter
#: and the offline language-model double all speak that vocabulary, so a
#: revision is asked for in the same terms as a draft is -- and the double
#: that ships with the app can answer it.
#:
#: The signature is declared as a string rather than as a class so that this
#: module stays a leaf: it declares no ``dspy.Signature`` subclass, and
#: therefore adds no new type to the strict-typing surface of the package.
_REWRITE_SIGNATURE = (
    "instruction: str, draft_text: str, assigned_chunks_text: str -> grounded_draft: str"
)

#: The rubric handed to the model. It says the two things the editor cannot
#: check afterwards: the revision must cover the whole passage, and it may
#: only cite the chunk ids it was given.
_REWRITE_INSTRUCTIONS = (
    "Rewrite the passage so that it satisfies the instruction. "
    "Return the complete revised passage in the grounded_draft field, not a "
    "fragment and not a comment. Ground every factual statement in the "
    "supplied passages and keep the inline citation markers [#chunk_id] of "
    "the passages that support the revised text. You may not cite a chunk "
    "id that does not appear in the supplied passages, and you may not "
    "assert anything the passages do not support."
)


@runtime_checkable
class RewriteFn(Protocol):
    """The contract a targeted revision must satisfy.

    A rewrite is given the instruction in plain language, the exact text to
    revise, and the passages the revision may be grounded in. It returns the
    revised text and nothing else.

    The editor checks what comes back -- the cited chunk ids must exist and
    the claims must be supported -- so an implementation of this protocol is
    free to be as creative as it likes, but never free to invent a source.
    """

    def __call__(self, *, instruction: str, text: str, passages: Sequence[SourceChunk]) -> str:
        """Return the revised text for ``text``."""
        ...


def _validate(instruction: str, text: str) -> None:
    """Reject a malformed rewrite request at the seam's boundary."""
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValidationError(
            "a rewrite needs a non-empty instruction.",
            field="instruction",
            value=instruction,
        )
    if not isinstance(text, str):
        raise ValidationError(
            "the text to rewrite must be a string.",
            field="text",
            value=text,
        )


def _format_passages(passages: Sequence[SourceChunk]) -> str:
    """Render the passages for the prompt, each prefixed with its chunk id.

    The format is the drafter's own, so the passages are laid out in the
    prompt exactly as they are when a section is drafted: one passage per
    block, each with the chunk id the model has to cite it by.
    """
    return SectionDrafter.format_assigned_chunks(passages)


def _build_program() -> Any:
    """Build the DSPy program that performs the rewrite."""
    import dspy

    signature = dspy.Signature(_REWRITE_SIGNATURE).with_instructions(_REWRITE_INSTRUCTIONS)
    return dspy.Predict(signature)


def rewrite_with_lm(
    lm: Any,
    *,
    instruction: str,
    text: str,
    passages: Sequence[SourceChunk],
) -> str:
    """Rewrite ``text`` with a caller-supplied language model.

    Args:
        lm: The language model to call. It is handed to DSPy for this call
            only, so a caller that owns its own model does not have to install
            it globally.
        instruction: What the revision should achieve, in plain language.
        text: The exact text to revise.
        passages: The passages the revision may be grounded in.

    Returns:
        The revised text.

    Raises:
        ValidationError: The instruction is empty or the text is not a string.
        LMNotConfiguredError: No language model was supplied.
        InlineEditRejectedError: The model call failed or returned no text.
    """
    _validate(instruction, text)
    if lm is None:
        raise LMNotConfiguredError("inline edit rewrite")

    program = _build_program()
    try:
        result = program(
            instruction=instruction,
            draft_text=text,
            assigned_chunks_text=_format_passages(passages),
            lm=lm,
        )
    except Exception as exc:
        # The model client's own failure names its provider, its endpoint
        # and its error class, which are the operator's and not the
        # caller's: the client is told the call failed, and the whole of it
        # goes to the log.
        logger.warning("The rewrite model call failed: %s", exc, exc_info=exc)
        raise InlineEditRejectedError(
            reason="the rewrite model call failed."
        ) from exc

    revised = str(getattr(result, "grounded_draft", "") or "").strip()
    if not revised:
        raise InlineEditRejectedError(reason="the rewrite model returned no text.")
    return revised


def default_rewrite(*, instruction: str, text: str, passages: Sequence[SourceChunk]) -> str:
    """Rewrite ``text`` with the configured language model.

    This is the seam the editor uses when the caller does not supply one of
    its own. The model is the one installed in DSPy, so there is exactly one
    place that answers "is a model available?".

    Args:
        instruction: What the revision should achieve, in plain language.
        text: The exact text to revise.
        passages: The passages the revision may be grounded in.

    Returns:
        The revised text.

    Raises:
        ValidationError: The instruction is empty or the text is not a string.
        LMNotConfiguredError: No language model is configured.
        InlineEditRejectedError: The model call failed or returned no text.
    """
    _validate(instruction, text)
    lm = lm_factory.require_lm("inline edit rewrite")
    return rewrite_with_lm(lm, instruction=instruction, text=text, passages=passages)


__all__ = [
    "RewriteFn",
    "default_rewrite",
    "rewrite_with_lm",
]
