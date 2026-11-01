"""Targeted inline edits to one paragraph or section of a manuscript.

The whole document can already be regenerated; what it could not do was
*changed*. This module is that missing capability, and it is deliberately
narrow: it rewrites one paragraph (or one whole section) of a section draft,
grounded in the document's own sources, and refuses the revision when the
sources do not support it.

This module is a **door** to the Kotlin tier. The rules are the Kotlin tier's
and live in exactly one place; what stays here is the part that cannot be
moved:

* the **rewrite** -- the language-model call that produces the new text. The
  model is the Python tier's, so the call is made here.
* the **entailment judgement** -- the model that says whether a claim follows
  from the passage it cites. That model is the Python tier's too.

Everything else is a rule, and the rules are asked of the Kotlin tier:

1. **Target resolution.** The paragraph is found inside the section, and the
   user's selection must really be inside it. A target that does not exist
   raises :class:`~lmeditor.core.exceptions.EditTargetNotFoundError` rather
   than editing whatever happens to be at that position.
2. **Grounding.** Only the passages that answer the instruction are offered
   to the model. The corpus itself is never handed over.
3. **The rewrite.** Exactly one model call. With no model configured the
   operation fails loudly; it never substitutes template prose.
4. **Phantom-citation rejection.** Every citation marker in the result must
   name a chunk that exists. A marker that names nothing is an invented
   source, and the revision is refused.
5. **Support check.** The claims of the revised text are verified against
   the chunks the revision cites. A contradicted claim is refused.

A refused revision is *not* an error: the result carries ``applied=False``,
the reason it was refused, and the proposal the model made, so the user can
see what was on offer and why it was left alone. Only the section that
came back is ever written to the document, and it is written back only when
``applied`` is true.

There is no fallback to a Python implementation of any of those rules: a
silent fallback would hide exactly the drift the Kotlin tier exists to
remove.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from lmeditor.core.exceptions import (
    CitationHallucinationError,
    EditTargetNotFoundError,
    InlineEditRejectedError,
    LMNotConfiguredError,
    ValidationError,
)
from lmeditor.core.schemas import (
    Claim,
    InlineEditRequest,
    InlineEditResult,
    SectionDraft,
    SourceChunk,
)
from lmeditor.editing.rewrite import RewriteFn, default_rewrite, rewrite_with_lm
from lmeditor.llm import factory as lm_factory
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.tier import editing as tier_editing
from lmeditor.verification.citation_checker import CitationChecker

logger = logging.getLogger(__name__)


class InlineEditor:
    """Applies small, grounded edits to one paragraph or section of a draft.

    The editor owns no state beyond its collaborators: the retrieval index
    it grounds the edit in, the chunks it may fall back to, the verifier it
    checks the result with, and the rewrite seam it calls. It is therefore
    safe to build one per request.
    """

    def __init__(
        self,
        *,
        rewrite: RewriteFn | None = None,
        lm: Any | None = None,
        index: ProvenanceIndex | None = None,
        chunks: Sequence[SourceChunk] = (),
        checker: CitationChecker | None = None,
        max_passages: int = 6,
    ) -> None:
        """Build an editor.

        Args:
            rewrite: The seam that performs the rewrite. When it is given,
                the caller owns the language model and the editor does not
                require one to be configured globally.
            lm: The language model to use when no ``rewrite`` seam is given.
            index: The retrieval index the edit is grounded in. When it is
                given, the passages are the ones that answer the
                instruction.
            chunks: The chunks the editor may fall back to when there is no
                index. Only the chunks that speak to the target are used.
            checker: The verifier that checks the revised text.
            max_passages: The largest number of passages that may be handed
                to the model.

        Raises:
            ValidationError: ``max_passages`` is not positive.
        """
        if max_passages <= 0:
            raise ValidationError(
                f"max_passages must be positive, got {max_passages}",
                field="max_passages",
                value=max_passages,
            )

        self._rewrite = rewrite
        self._lm = lm
        self._index = index
        self._chunks: tuple[SourceChunk, ...] = tuple(chunks)
        self._checker = checker if checker is not None else CitationChecker()
        self._max_passages = max_passages

    @property
    def max_passages(self) -> int:
        """The largest number of passages that may be handed to the model."""
        return self._max_passages

    async def edit(self, request: InlineEditRequest, section: SectionDraft) -> InlineEditResult:
        """Rewrite one paragraph or section of ``section`` and check the result.

        Args:
            request: What to change and how.
            section: The section the change applies to.

        Returns:
            The outcome of the edit. When the revision was refused the result
            carries ``applied=False``, the reason it was refused and the
            proposal that was refused; the section it carries is the
            unchanged original, so a caller that writes back on
            ``applied=True`` can never persist a rejected revision.

        Raises:
            ValidationError: The request is empty or malformed.
            EditTargetNotFoundError: The section, the paragraph or the
                selected text does not exist.
            LMNotConfiguredError: No language model is configured.
            InlineEditRejectedError: The rewrite call failed.
            BridgeUnavailableError: The Kotlin tier cannot be reached.
        """
        self._validate(request, section)

        # The plan is the Kotlin tier's answer to "which span of the section
        # does this edit touch, what does the model get to see, and what is it
        # told". The rewrite itself is the Python tier's model call.
        instruction, original_text, passages = tier_editing.plan_edit(
            request,
            section,
            self._chunks,
            max_passages=self._max_passages,
        )
        ranked = self._ranked_passages(request.instruction, original_text)
        if self._index is not None:
            # The retrieval index belongs to the Python tier, so the passages
            # it ranks are the ones the model is shown.
            passages = ranked

        rewrite_fn = self._resolve_rewrite()
        revised_text, refusal = await self._run_rewrite(
            rewrite_fn,
            request,
            instruction,
            original_text,
            passages,
        )

        evaluated: list[Claim] = []
        if not refusal:
            evaluated = await self._judge(revised_text, section)

        # Whether the revision may stand is the Kotlin tier's answer, and so is
        # the section that comes back when it does.
        return tier_editing.apply_edit(
            request,
            section,
            self._chunks,
            revised_text,
            evaluated,
            index_chunks=self._index_chunks(),
            ranked_passages=ranked,
            use_index=self._index is not None,
            max_passages=self._max_passages,
            refusal_reason=refusal,
            beta=self._checker.beta,
            threshold_f_beta=self._checker.f_beta_threshold,
            evaluator_name=self._evaluator_name(),
        )

    # ------------------------------------------------------------------
    # Boundaries
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(request: InlineEditRequest, section: SectionDraft) -> None:
        """Check the request before any work is done."""
        if not isinstance(request, InlineEditRequest):
            raise ValidationError(
                "an inline edit needs an InlineEditRequest.",
                field="request",
                value=request,
            )
        if not request.instruction.strip():
            raise ValidationError(
                "an inline edit needs a non-empty instruction.",
                field="instruction",
                value=request.instruction,
            )
        if request.section_id != section.section_id:
            raise EditTargetNotFoundError(
                request.section_id,
                request.paragraph_index,
                message=(
                    f"Edit target not found: the request names section "
                    f"'{request.section_id}' but the section being edited is "
                    f"'{section.section_id}'."
                ),
            )

    # ------------------------------------------------------------------
    # Grounding
    # ------------------------------------------------------------------

    def _ranked_passages(self, instruction: str, target_text: str) -> list[SourceChunk]:
        """Return the passages the index ranks as answering the instruction.

        The retrieval index belongs to the Python tier -- it owns the vector
        index and the ranking -- so the ranking is asked of it here. The
        passages it returns are the ones the model is shown.
        """
        if self._index is None:
            return []
        query = f"{instruction} {target_text}".strip()
        ranked = self._index.rank_for_section(query, top_k=self._max_passages)
        return list(ranked[: self._max_passages])

    def _index_chunks(self) -> list[SourceChunk]:
        """Return the chunks the retrieval index holds.

        A chunk the revision cites may be one the index holds and the editor
        was not given, so the index's chunks travel with the edit.
        """
        if self._index is None:
            return []
        chunks: list[SourceChunk] = []
        for chunk_id in self._index.chunk_ids:
            chunk = self._index.get(chunk_id)
            if chunk is not None:
                chunks.append(chunk)
        return chunks

    def _corpus(self) -> dict[str, SourceChunk]:
        """Return every chunk the document can account for, keyed by chunk id.

        The index is consulted first, because a chunk it holds is the one the
        document's own attribution points at.
        """
        corpus: dict[str, SourceChunk] = {}
        for chunk in self._index_chunks():
            corpus.setdefault(chunk.chunk_id, chunk)
        for chunk in self._chunks:
            corpus.setdefault(chunk.chunk_id, chunk)
        return corpus

    # ------------------------------------------------------------------
    # The rewrite
    # ------------------------------------------------------------------

    def _resolve_rewrite(self) -> RewriteFn:
        """Return the seam that performs the rewrite.

        Raises:
            LMNotConfiguredError: No seam and no language model were given
                and no model is configured.
        """
        if self._rewrite is not None:
            return self._rewrite
        if self._lm is not None:
            return self._rewrite_with_injected_lm
        if not lm_factory.is_configured():
            raise LMNotConfiguredError("inline edit rewrite")
        return default_rewrite

    def _rewrite_with_injected_lm(
        self,
        *,
        instruction: str,
        text: str,
        passages: Sequence[SourceChunk],
    ) -> str:
        """Rewrite with the language model the editor was given."""
        return rewrite_with_lm(
            self._lm,
            instruction=instruction,
            text=text,
            passages=passages,
        )

    async def _run_rewrite(
        self,
        rewrite_fn: RewriteFn,
        request: InlineEditRequest,
        instruction: str,
        target_text: str,
        passages: Sequence[SourceChunk],
    ) -> tuple[str, str]:
        """Call the rewrite seam exactly once and return the revised text.

        The call runs in a worker thread because the model call is blocking
        and the editor is awaited from the request handler.

        Returns:
            The revised text and the reason the revision was refused, which
            is empty when the seam produced a text at all. A seam that
            refused the revision itself -- because the model call failed or
            came back empty -- is not an error: the passage stays as it is
            and the reason travels back to the caller.

        Raises:
            LMNotConfiguredError: The seam reported that no model is
                available.
            InlineEditRejectedError: The seam failed in a way it did not
                report itself.
        """
        try:
            revised = await asyncio.to_thread(
                rewrite_fn,
                instruction=instruction,
                text=target_text,
                passages=tuple(passages),
            )
        except LMNotConfiguredError:
            raise
        except InlineEditRejectedError as exc:
            logger.warning(
                "Inline edit refused for section '%s': %s",
                request.section_id,
                exc.reason,
            )
            return "", exc.reason
        except Exception as exc:
            # The language model's own failure names its provider, its
            # endpoint and its error class, which are the operator's and not
            # the caller's: the client is told the rewrite could not be
            # made, and the whole of it goes to the log.
            logger.warning(
                "The rewrite call failed for section '%s': %s",
                request.section_id,
                exc,
                exc_info=exc,
            )
            raise InlineEditRejectedError(
                request.section_id,
                reason="the rewrite call failed.",
            ) from exc

        if not isinstance(revised, str):
            raise InlineEditRejectedError(
                request.section_id,
                reason="the rewrite call returned a non-string result.",
            )
        return revised.strip(), ""

    # ------------------------------------------------------------------
    # Judging
    # ------------------------------------------------------------------

    async def _judge(self, revised_text: str, section: SectionDraft) -> list[Claim]:
        """Let the model judge the revision's claims against the sources.

        The judgement is a model call, and the model is the Python tier's, so
        it happens here. The rules around it -- which premise a claim is
        judged against, whether a cited chunk exists, what the scorecard
        says -- are the Kotlin tier's, and the checker asks for them.

        Returns:
            The claims of the revision with their verdicts on them. A claim
            that cites a source that cannot be verified yields no claims at
            all: the refusal is the Kotlin tier's to phrase, and it phrases
            it when the revision is put to it.
        """
        corpus = self._corpus()
        accepted, _reason, _cited, claims = tier_editing.validate_revision(
            revised_text,
            section,
            list(corpus.values()),
        )
        if not accepted:
            # The revision names a source the document does not have. The
            # refusal is the Kotlin tier's to phrase, and it phrases it when
            # the revision is put to it.
            return []
        try:
            evaluated, _scorecard = await self._checker.audit_claims(
                claims,
                corpus,
                strict_hallucination_check=True,
                section_id=section.section_id,
            )
        except CitationHallucinationError:
            return []
        return list(evaluated)

    def _evaluator_name(self) -> str:
        """Identifier of the NLI evaluator that produced the verdicts."""
        name = getattr(self._checker.evaluator, "evaluator_name", "")
        return str(name) if isinstance(name, str) else ""


async def apply_inline_edit(
    request: InlineEditRequest,
    section: SectionDraft,
    *,
    rewrite: RewriteFn | None = None,
    lm: Any | None = None,
    index: ProvenanceIndex | None = None,
    chunks: Sequence[SourceChunk] = (),
    checker: CitationChecker | None = None,
) -> InlineEditResult:
    """Apply a targeted edit to ``section`` and return the outcome.

    This is the one-call entry point: it builds an :class:`InlineEditor`
    from the collaborators it is given and runs the edit. It is the
    function a route calls.

    Args:
        request: What to change and how.
        section: The section the change applies to.
        rewrite: The seam that performs the rewrite.
        lm: The language model to use when no ``rewrite`` seam is given.
        index: The retrieval index the edit is grounded in.
        chunks: The chunks the editor may fall back to.
        checker: The verifier that checks the revised text.

    Returns:
        The outcome of the edit.

    Raises:
        ValidationError: The request is empty or malformed.
        EditTargetNotFoundError: The section, the paragraph or the selected
            text does not exist.
        LMNotConfiguredError: No language model is configured.
        InlineEditRejectedError: The rewrite call failed.
        BridgeUnavailableError: The Kotlin tier cannot be reached.
    """
    editor = InlineEditor(
        rewrite=rewrite,
        lm=lm,
        index=index,
        chunks=chunks,
        checker=checker,
    )
    return await editor.edit(request, section)


__all__ = [
    "InlineEditor",
    "apply_inline_edit",
]
