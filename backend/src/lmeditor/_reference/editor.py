"""The frozen Python implementation of the inline-edit rules.

This is the witness the drift detector measures the Kotlin tier against, so it
is **plain Python**: it never asks the Kotlin tier for an answer. It is
re-aligned to the tier's *current* semantics only when the tier's rule
deliberately changes, and every such change is recorded in the harness's
``REFERENCE_WITNESSES``. It is a copy of the implementation
``lmeditor.editing.editor`` used to carry, with its imports rewired onto the
frozen siblings in this package so that it cannot reach the seam by accident.

The rules it holds, in the order the pipeline applies them:

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
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, NamedTuple, Protocol, Sequence, runtime_checkable

from lmeditor._reference.citations import extract_chunk_ids
from lmeditor._reference.citation_checker import CitationChecker
from lmeditor._reference.claim_extractor import extract_claims
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
    SectionStatus,
    SourceChunk,
    VerificationScorecard,
)
from lmeditor.core.types import EditScope, EntailmentStatus
from lmeditor.llm import factory as lm_factory

logger = logging.getLogger(__name__)

#: The retrieval index the editor may be given.
#:
#: The live ``lmeditor.provenance.service.ProvenanceIndex`` reaches the Kotlin
#: tier through the provenance lineage door, so importing it here would make
#: this witness a door itself. The annotation is therefore spelled out rather
#: than imported: the index is duck-typed at runtime, and only the two
#: methods the editor calls -- ``rank_for_section`` and ``get`` -- are used.
ProvenanceIndex = Any


#: The DSPy signature of the rewrite program, and the rubric handed to it.
#:
#: These are copies of the ones the live rewrite seam uses. The seam is a
#: model call, and a model call is not a rule the Kotlin tier owns, so the
#: witness carries its own copy rather than reaching across the seam for it.
_REWRITE_SIGNATURE = (
    "instruction: str, draft_text: str, assigned_chunks_text: str -> grounded_draft: str"
)
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
    """

    def __call__(self, *, instruction: str, text: str, passages: Sequence[SourceChunk]) -> str:
        """Return the revised text for ``text``."""
        ...


def _rewrite_validate(instruction: str, text: str) -> None:
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
    """Render the passages for the prompt, each prefixed with its chunk id."""
    formatted_blocks: list[str] = []
    for idx, chunk in enumerate(passages, 1):
        header = f"--- PASSAGE {idx} [#CHUNK_ID: {chunk.chunk_id}] ---"
        if chunk.document_title:
            header += f" (Source: {chunk.document_title})"
        formatted_blocks.append(f"{header}\n{chunk.text.strip()}\n")
    return "\n".join(formatted_blocks)


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
    """Rewrite ``text`` with a caller-supplied language model."""
    _rewrite_validate(instruction, text)
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
        raise InlineEditRejectedError(
            reason=f"the rewrite model call failed: {exc}"
        ) from exc

    revised = str(getattr(result, "grounded_draft", "") or "").strip()
    if not revised:
        raise InlineEditRejectedError(reason="the rewrite model returned no text.")
    return revised


def default_rewrite(*, instruction: str, text: str, passages: Sequence[SourceChunk]) -> str:
    """Rewrite ``text`` with the configured language model."""
    _rewrite_validate(instruction, text)
    lm = lm_factory.require_lm("inline edit rewrite")
    return rewrite_with_lm(lm, instruction=instruction, text=text, passages=passages)

#: A paragraph boundary: a blank line between two blocks of text.
_PARAGRAPH_SEPARATOR = re.compile(r"\n[ \t]*\n")

#: Words used to decide whether a chunk speaks to the target at all.
_WORD_RE = re.compile(r"[a-z0-9]+")

#: The reason returned when the model reproduced the text it was given.
_NO_OP_REASON = "The revision matched the original text."


class _Target(NamedTuple):
    """Where the edit lands inside the section's content.

    ``start`` and ``end`` are the character offsets of the text to replace,
    so the rest of the section is preserved byte for byte rather than being
    re-assembled from split paragraphs.
    """

    start: int
    end: int
    text: str


def _paragraph_spans(content: str) -> list[_Target]:
    """Return every paragraph of ``content`` with its offsets.

    A paragraph is a blank-line-separated block of text. The offsets point
    at the trimmed block, so a replacement touches the paragraph and
    nothing else.
    """
    spans: list[_Target] = []
    cursor = 0
    for match in _PARAGRAPH_SEPARATOR.finditer(content):
        _append_paragraph(spans, content, cursor, match.start())
        cursor = match.end()
    _append_paragraph(spans, content, cursor, len(content))
    return spans


def _append_paragraph(spans: list[_Target], content: str, start: int, end: int) -> None:
    """Add the trimmed block between ``start`` and ``end`` to ``spans``."""
    block = content[start:end]
    trimmed = block.strip()
    if not trimmed:
        return
    offset = start + (len(block) - len(block.lstrip()))
    spans.append(_Target(offset, offset + len(trimmed), trimmed))


def _content_words(text: str) -> set[str]:
    """Return the content words of ``text``.

    Short words are dropped because they are shared by every passage in the
    corpus and would make every chunk look relevant to every target.
    """
    return {word for word in _WORD_RE.findall(text.lower()) if len(word) > 3}


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
        """
        self._validate(request, section)
        target = self._resolve_target(request, section)
        original_text = target.text

        passages = self._gather_passages(request.instruction, original_text)
        rewrite_fn = self._resolve_rewrite()
        revised_text, refusal = await self._run_rewrite(
            rewrite_fn,
            request,
            original_text,
            passages,
        )
        if refusal:
            return self._refused(request, section, original_text, revised_text, refusal)

        identical = revised_text == original_text
        if not revised_text and not identical:
            return self._refused(
                request,
                section,
                original_text,
                revised_text,
                "The rewrite returned no text, so the passage was left unchanged.",
            )

        if identical:
            # A revision that changes nothing is a success, not a failure:
            # there is nothing to apply, and the revision count must not
            # move, so applying the same edit twice is harmless.
            return InlineEditResult(
                doc_id=request.doc_id,
                section_id=request.section_id,
                scope=request.scope,
                original_text=original_text,
                revised_text=revised_text,
                applied=True,
                reason=_NO_OP_REASON,
                cited_chunk_ids=extract_chunk_ids(revised_text),
                claims=[],
                scorecard=None,
                section=section,
            )

        cited_ids = extract_chunk_ids(revised_text)

        unknown_ids = [
            chunk_id for chunk_id in cited_ids if not self._is_known_chunk(chunk_id, section)
        ]
        if unknown_ids:
            return self._refused(
                request,
                section,
                original_text,
                revised_text,
                "The revision cites source chunks that do not exist: "
                f"{', '.join(unknown_ids)}. The passage was left unchanged.",
                cited_chunk_ids=cited_ids,
            )

        chunks_by_id = self._chunks_by_id(cited_ids)
        claims = extract_claims(revised_text, section_id=section.section_id)
        try:
            evaluated, scorecard = await self._checker.audit_claims(
                claims,
                chunks_by_id,
                strict_hallucination_check=True,
                section_id=section.section_id,
            )
        except CitationHallucinationError as exc:
            return self._refused(
                request,
                section,
                original_text,
                revised_text,
                "The revision cites a source that cannot be verified: "
                f"{exc.chunk_id}. The passage was left unchanged.",
                cited_chunk_ids=cited_ids,
            )

        if scorecard.contradicted_claims > 0:
            return self._refused(
                request,
                section,
                original_text,
                revised_text,
                "The revision contradicts the sources it cites "
                f"({scorecard.contradicted_claims} contradicted claim(s)). "
                "The passage was left unchanged.",
                cited_chunk_ids=cited_ids,
            )

        updated = self._updated_section(section, target, revised_text, evaluated, scorecard)
        persisted = request.persist
        reason = (
            "The revision is supported by the sources it cites and was applied."
            if persisted
            else "The revision is supported by the sources it cites; it was "
            "returned as a proposal and the section was left unchanged."
        )
        return InlineEditResult(
            doc_id=request.doc_id,
            section_id=request.section_id,
            scope=request.scope,
            original_text=original_text,
            revised_text=revised_text,
            applied=persisted,
            reason=reason,
            cited_chunk_ids=cited_ids,
            claims=evaluated,
            scorecard=scorecard,
            section=updated if persisted else section,
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

    def _resolve_target(self, request: InlineEditRequest, section: SectionDraft) -> _Target:
        """Find the text the edit applies to.

        Raises:
            EditTargetNotFoundError: The paragraph does not exist, or the
                selected text is not inside the section.
        """
        if request.scope is EditScope.SECTION:
            if request.selected_text and request.selected_text not in section.content:
                raise EditTargetNotFoundError(
                    section.section_id,
                    request.paragraph_index,
                    message=(
                        f"Edit target not found: the selected text does not "
                        f"appear in section '{section.section_id}'."
                    ),
                )
            return _Target(0, len(section.content), section.content.strip())

        paragraphs = _paragraph_spans(section.content)
        if request.paragraph_index < 0 or request.paragraph_index >= len(paragraphs):
            raise EditTargetNotFoundError(section.section_id, request.paragraph_index)

        target = paragraphs[request.paragraph_index]
        if request.selected_text:
            if request.selected_text not in section.content:
                raise EditTargetNotFoundError(
                    section.section_id,
                    request.paragraph_index,
                    message=(
                        f"Edit target not found: the selected text does not "
                        f"appear in section '{section.section_id}'."
                    ),
                )
            if request.selected_text not in target.text:
                raise EditTargetNotFoundError(
                    section.section_id,
                    request.paragraph_index,
                    message=(
                        f"Edit target not found: the selected text is not in "
                        f"paragraph {request.paragraph_index} of section "
                        f"'{section.section_id}'."
                    ),
                )
        return target

    # ------------------------------------------------------------------
    # Grounding
    # ------------------------------------------------------------------

    def _gather_passages(self, instruction: str, target_text: str) -> list[SourceChunk]:
        """Return the passages the revision may be grounded in.

        With an index, the passages are the ones that answer the instruction
        and the target. Without one, the supplied chunks are used, but only
        the ones that actually speak to the target. Either way the corpus is
        never handed over whole.
        """
        if self._index is not None:
            query = f"{instruction} {target_text}".strip()
            ranked = self._index.rank_for_section(query, top_k=self._max_passages)
            return list(ranked[: self._max_passages])
        return self._overlapping_passages(target_text)

    def _overlapping_passages(self, target_text: str) -> list[SourceChunk]:
        """Return the supplied chunks whose text overlaps the target."""
        target_words = _content_words(target_text)
        if not target_words:
            return []

        scored: list[tuple[int, SourceChunk]] = []
        for chunk in self._chunks:
            overlap = len(_content_words(chunk.text) & target_words)
            if overlap:
                scored.append((overlap, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [chunk for _overlap, chunk in scored[: self._max_passages]]

    def _resolve_chunk(self, chunk_id: str) -> SourceChunk | None:
        """Return the chunk with ``chunk_id``, or ``None`` when there is none."""
        if self._index is not None:
            chunk: SourceChunk | None = self._index.get(chunk_id)
            if chunk is not None:
                return chunk
        for chunk in self._chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        return None

    def _is_known_chunk(self, chunk_id: str, section: SectionDraft) -> bool:
        """Whether ``chunk_id`` names a source that exists.

        A chunk the section already cites is known even when the editor was
        not given its text: the section has already made that attribution,
        and the editor has no reason to call it an invention.
        """
        if self._resolve_chunk(chunk_id) is not None:
            return True
        return chunk_id in section.cited_chunk_ids

    def _chunks_by_id(self, chunk_ids: Sequence[str]) -> dict[str, SourceChunk]:
        """Return the chunks the revision cites, keyed by chunk id."""
        resolved: dict[str, SourceChunk] = {}
        for chunk_id in chunk_ids:
            chunk = self._resolve_chunk(chunk_id)
            if chunk is not None:
                resolved[chunk_id] = chunk
        return resolved

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

    @staticmethod
    def _compose_instruction(request: InlineEditRequest) -> str:
        """Return the instruction handed to the model.

        A selection is part of the instruction: it is what "this" refers to
        in "make this tighter", so the model is told which span of the
        passage the user is talking about.
        """
        instruction = request.instruction.strip()
        if not request.selected_text:
            return instruction
        return (
            f"{instruction}\n\n"
            "The user selected this exact span inside the passage; revise "
            f"this span:\n{request.selected_text}"
        )

    async def _run_rewrite(
        self,
        rewrite_fn: RewriteFn,
        request: InlineEditRequest,
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
        instruction = self._compose_instruction(request)
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
            raise InlineEditRejectedError(
                request.section_id,
                reason=f"the rewrite call failed: {exc}",
            ) from exc

        if not isinstance(revised, str):
            raise InlineEditRejectedError(
                request.section_id,
                reason="the rewrite call returned a non-string result.",
            )
        return revised.strip(), ""

    # ------------------------------------------------------------------
    # The result
    # ------------------------------------------------------------------

    @staticmethod
    def _refused(
        request: InlineEditRequest,
        section: SectionDraft,
        original_text: str,
        revised_text: str,
        reason: str,
        cited_chunk_ids: Sequence[str] = (),
    ) -> InlineEditResult:
        """Build the result of a revision that was not accepted.

        The proposal is carried along so the user can see what was on
        offer, but the section is the unchanged original: a refused
        revision can never be written back.
        """
        return InlineEditResult(
            doc_id=request.doc_id,
            section_id=request.section_id,
            scope=request.scope,
            original_text=original_text,
            revised_text=revised_text,
            applied=False,
            reason=reason,
            cited_chunk_ids=list(cited_chunk_ids),
            claims=[],
            scorecard=None,
            section=section,
        )

    def _updated_section(
        self,
        section: SectionDraft,
        target: _Target,
        revised_text: str,
        evaluated: Sequence[Claim],
        scorecard: VerificationScorecard,
    ) -> SectionDraft:
        """Return the section with the revision written into it.

        Only the target's span of the content is replaced, so the rest of
        the section -- and every citation in it -- survives untouched. The
        cited chunk ids are recomputed from the new content, so a citation
        the revision dropped from its own paragraph goes away with it and a
        citation the other paragraphs still carry stays.
        """
        new_content = (
            section.content[: target.start] + revised_text + section.content[target.end :]
        )
        claims = self._merged_claims(section, target, revised_text, evaluated)
        is_verified = self._is_verified(new_content, claims, scorecard)
        status = SectionStatus.VERIFIED if is_verified else SectionStatus.NEEDS_REVISION
        return section.model_copy(
            update={
                "content": new_content,
                "revision_count": section.revision_count + 1,
                "claims": claims,
                "is_verified": is_verified,
                "status": status,
                "cited_chunk_ids": extract_chunk_ids(new_content),
            }
        )

    @staticmethod
    def _merged_claims(
        section: SectionDraft,
        target: _Target,
        revised_text: str,
        evaluated: Sequence[Claim],
    ) -> list[Claim]:
        """Return the claims of the section after the revision.

        The claims of the revised text are the ones this edit verified. The
        claims of the paragraphs the edit did not touch are carried over, so
        an inline edit refreshes the section's verification state instead of
        erasing it. Their offsets are moved to where those paragraphs now
        sit, because the revision changed the length of the text before
        them.
        """
        shift = len(revised_text) - len(target.text)
        merged: list[Claim] = []
        for claim in section.claims:
            if claim.char_end <= target.start:
                merged.append(claim)
            elif claim.char_start >= target.end:
                merged.append(
                    claim.model_copy(
                        update={
                            "char_start": claim.char_start + shift,
                            "char_end": claim.char_end + shift,
                        }
                    )
                )

        # The claims of the revision, moved into the section's coordinates.
        for claim in evaluated:
            merged.append(
                claim.model_copy(
                    update={
                        "char_start": claim.char_start + target.start,
                        "char_end": claim.char_end + target.start,
                    }
                )
            )
        merged.sort(key=lambda claim: (claim.char_start, claim.claim_id))
        return merged

    def _is_verified(
        self,
        content: str,
        claims: Sequence[Claim],
        scorecard: VerificationScorecard,
    ) -> bool:
        """Whether the revised section meets the verification bar.

        The rule is the one the verifier itself applies: the section must
        have content, its groundedness must reach the threshold, and no
        claim may be contradicted, ungrounded or merely neutral. The claims
        the edit did not touch are included in that judgement, so a section
        that was already shaky does not become "verified" because one
        paragraph of it was.
        """
        return (
            bool(content.strip())
            and scorecard.f_beta >= self._checker.f_beta_threshold
            and scorecard.contradicted_claims == 0
            and scorecard.ungrounded_claims == 0
            and scorecard.neutral_claims == 0
            and all(claim.entailment_status is EntailmentStatus.ENTAILED for claim in claims)
        )


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
