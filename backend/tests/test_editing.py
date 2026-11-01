"""The small-edit capability: rewrite one paragraph, and refuse what the sources cannot support.

Regenerating a whole document was the only thing the product could do; this
module is the missing half -- a user points at a paragraph, says what should
change, and the change is applied to that paragraph alone.

The tests here pin the four promises that make the feature trustworthy
rather than merely convenient:

* the edit lands where the user pointed, and nowhere else;
* the model is asked once, and is never handed the corpus;
* a revision that names a source which does not exist is refused, and the
  refusal says which source was invented;
* a revision that is supported is applied, and applying it twice is
  harmless.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from lmeditor.core.exceptions import (
    EditTargetNotFoundError,
    LMNotConfiguredError,
)
from lmeditor.core.schemas import (
    InlineEditRequest,
    SectionDraft,
    SourceChunk,
)
from lmeditor.core.types import EditScope
from lmeditor.editing import InlineEditor, apply_inline_edit, default_rewrite
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.verification.citation_checker import CitationChecker
from lmeditor.verification.nli_evaluator import HeuristicNLIEvaluator

#: The corpus the edits are grounded in. One sentence per chunk, so a claim
#: that quotes a chunk can be entailed by it.
CORPUS: list[str] = [
    "Coral reefs shelter a quarter of all marine species and their larvae.",
    "Mangrove forests slow storm surge and trap sediment along tropical coasts.",
    "Medieval abbeys kept meticulous accounts of grain harvests and rents.",
    "Monastic libraries copied manuscripts by hand in scriptoria.",
]


def _chunks() -> list[SourceChunk]:
    """Chunk the corpus into one passage per sentence."""
    chunks: list[SourceChunk] = []
    for text in CORPUS:
        chunks.append(
            SourceChunk.create(
                document_id="doc_edit",
                text=text,
                char_start=0,
                char_end=len(text),
                byte_start=0,
                byte_end=len(text.encode("utf-8")),
            )
        )
    return chunks


def _section(chunks: Sequence[SourceChunk]) -> SectionDraft:
    """Build a two-paragraph section whose paragraphs carry their citations."""
    coral, mangrove = chunks[0], chunks[1]
    content = (
        f"Coral reefs shelter a quarter of all marine species and their larvae "
        f"[#{coral.chunk_id}].\n\n"
        f"Mangrove forests slow storm surge and trap sediment along tropical coasts "
        f"[#{mangrove.chunk_id}]."
    )
    return SectionDraft(
        section_id="sec_1",
        title="Coastal ecosystems",
        content=content,
        cited_chunk_ids=[coral.chunk_id, mangrove.chunk_id],
    )


def _checker() -> CitationChecker:
    """A verifier that answers from the text itself, with no model involved."""
    return CitationChecker(evaluator=HeuristicNLIEvaluator(), beta=0.5)


def _request(**overrides: Any) -> InlineEditRequest:
    """Build an edit request for ``sec_1``."""
    payload: dict[str, Any] = {
        "doc_id": "doc_1",
        "section_id": "sec_1",
        "instruction": "Make this tighter.",
    }
    payload.update(overrides)
    return InlineEditRequest(**payload)


class _RecordingRewrite:
    """A rewrite seam that answers with a fixed text and records its calls."""

    def __init__(self, revised: str) -> None:
        self.revised = revised
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        instruction: str,
        text: str,
        passages: Sequence[SourceChunk],
    ) -> str:
        """Record the call and return the fixed revision."""
        self.calls.append(
            {"instruction": instruction, "text": text, "passages": tuple(passages)}
        )
        return self.revised


# ============================================================================
# 1. The edit lands where the user pointed
# ============================================================================


@pytest.mark.asyncio
async def test_a_paragraph_that_does_not_exist_is_refused() -> None:
    """An index past the end of the section is an error, not an edit of whatever is there."""
    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(
        rewrite=_RecordingRewrite("something else entirely"),
        chunks=chunks,
        checker=_checker(),
    )

    with pytest.raises(EditTargetNotFoundError):
        await editor.edit(_request(paragraph_index=9), section)


@pytest.mark.asyncio
async def test_a_selection_that_was_never_written_is_refused() -> None:
    """A selection that is nowhere in the section cannot be edited."""
    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(
        rewrite=_RecordingRewrite("something else entirely"),
        chunks=chunks,
        checker=_checker(),
    )

    with pytest.raises(EditTargetNotFoundError):
        await editor.edit(
            _request(selected_text="A sentence that was never written down."),
            section,
        )


@pytest.mark.asyncio
async def test_a_selection_that_sits_in_another_paragraph_is_refused() -> None:
    """The named paragraph has to be the one that holds the selection.

    Editing paragraph 0 while the selection lives in paragraph 1 would
    rewrite a paragraph the user did not point at, so the mismatch is
    refused rather than resolved by guessing which of the two the user
    meant.
    """
    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(
        rewrite=_RecordingRewrite("something else entirely"),
        chunks=chunks,
        checker=_checker(),
    )

    with pytest.raises(EditTargetNotFoundError):
        await editor.edit(
            _request(paragraph_index=0, selected_text="Mangrove forests slow storm surge"),
            section,
        )


@pytest.mark.asyncio
async def test_a_section_that_is_not_the_one_named_is_refused() -> None:
    """The request and the section must agree on which section is edited."""
    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(
        rewrite=_RecordingRewrite("something else entirely"),
        chunks=chunks,
        checker=_checker(),
    )

    with pytest.raises(EditTargetNotFoundError):
        await editor.edit(_request(section_id="sec_other"), section)


# ============================================================================
# 2. No model, no prose
# ============================================================================


@pytest.mark.asyncio
async def test_without_a_language_model_the_edit_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no model configured the operation fails instead of inventing a sentence.

    LMeditor never substitutes template prose for a model call: a user who
    asks for a tighter sentence and gets one back has been handed text the
    system made up.
    """
    monkeypatch.setattr("lmeditor.llm.factory.is_configured", lambda: False)

    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(chunks=chunks, checker=_checker())

    with pytest.raises(LMNotConfiguredError):
        await editor.edit(_request(), section)


@pytest.mark.asyncio
async def test_a_seam_that_reports_no_model_fails_loudly() -> None:
    """A rewrite seam that cannot reach a model fails the whole edit."""

    def _no_model(*, instruction: str, text: str, passages: Sequence[SourceChunk]) -> str:
        raise LMNotConfiguredError("inline edit rewrite")

    chunks = _chunks()
    section = _section(chunks)
    editor = InlineEditor(rewrite=_no_model, chunks=chunks, checker=_checker())

    with pytest.raises(LMNotConfiguredError):
        await editor.edit(_request(), section)


def test_the_default_rewrite_fails_without_a_model() -> None:
    """The language-model seam refuses to invent prose when there is no model.

    The seam is the one place that asks the language-model factory for a
    model, so this is where the refusal has to happen.
    """
    import dspy

    previous = dspy.settings.lm
    dspy.configure(lm=None)
    try:
        with pytest.raises(LMNotConfiguredError):
            default_rewrite(
                instruction="Make this tighter.",
                text="Coral reefs shelter a quarter of all marine species.",
                passages=(),
            )
    finally:
        dspy.configure(lm=previous)


def test_an_editor_without_room_for_passages_is_refused() -> None:
    """An editor that may show no passage at all cannot ground anything."""
    from lmeditor.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        InlineEditor(max_passages=0)


# ============================================================================
# 3. A revision that invents a source is refused
# ============================================================================


@pytest.mark.asyncio
async def test_a_revision_that_invents_a_source_is_refused() -> None:
    """A citation marker that names nothing is an invented source.

    The revision is refused and the refusal names the id that was invented,
    so the user can see what the model made up.
    """
    chunks = _chunks()
    section = _section(chunks)
    invented = "f" * 64
    rewrite = _RecordingRewrite(
        f"Coral reefs shelter a quarter of all marine species [#{invented}]."
    )
    editor = InlineEditor(rewrite=rewrite, chunks=chunks, checker=_checker())

    result = await editor.edit(_request(), section)

    assert result.applied is False
    assert invented in result.reason
    # The section is the unchanged original, so the refusal cannot be
    # written back by a caller that trusts ``applied``.
    assert result.section == section
    assert result.section.content == section.content


@pytest.mark.asyncio
async def test_a_revision_that_contradicts_its_source_is_refused() -> None:
    """A revision that asserts the opposite of what it cites is refused."""
    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]
    rewrite = _RecordingRewrite(
        f"Coral reefs shelter no marine species at all [#{coral.chunk_id}]."
    )
    editor = InlineEditor(rewrite=rewrite, chunks=chunks, checker=_checker())

    result = await editor.edit(_request(), section)

    assert result.applied is False
    assert result.reason
    assert result.section == section


# ============================================================================
# 4. A supported revision is applied
# ============================================================================


@pytest.mark.asyncio
async def test_a_supported_revision_is_applied() -> None:
    """A revision the sources support is applied to the section.

    The section keeps its other paragraph and its other citation, the
    revision count moves by one, and the scorecard that decided the matter
    is handed back.
    """
    chunks = _chunks()
    section = _section(chunks)
    coral, mangrove = chunks[0], chunks[1]
    revised = (
        "Coral reefs shelter a quarter of all the marine species and their "
        f"larvae [#{coral.chunk_id}]."
    )
    editor = InlineEditor(
        rewrite=_RecordingRewrite(revised),
        chunks=chunks,
        checker=_checker(),
    )

    result = await editor.edit(_request(), section)

    assert result.applied is True
    assert result.original_text.startswith("Coral reefs shelter a quarter of all marine")
    assert result.revised_text == revised
    assert result.cited_chunk_ids == [coral.chunk_id]
    assert result.scorecard is not None
    assert result.scorecard.supported_claims == 1
    assert result.scorecard.contradicted_claims == 0

    updated = result.section
    assert updated is not None
    assert updated.revision_count == section.revision_count + 1
    assert revised in updated.content
    # The paragraph that was not edited is still there, with its citation.
    assert f"[#{mangrove.chunk_id}]" in updated.content
    assert updated.cited_chunk_ids == [coral.chunk_id, mangrove.chunk_id]
    assert updated.is_verified is True


@pytest.mark.asyncio
async def test_a_revision_that_matches_the_original_is_a_no_op() -> None:
    """A revision that changes nothing is a success that changes nothing.

    Applying the same edit twice must not push the revision count up twice,
    so the second application is recognised as a no-op.
    """
    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]
    original = (
        f"Coral reefs shelter a quarter of all marine species and their larvae "
        f"[#{coral.chunk_id}]."
    )
    editor = InlineEditor(
        rewrite=_RecordingRewrite(original),
        chunks=chunks,
        checker=_checker(),
    )

    result = await editor.edit(_request(), section)

    assert result.applied is True
    assert result.reason == "The revision matched the original text."
    assert result.section == section
    assert result.section.revision_count == section.revision_count


@pytest.mark.asyncio
async def test_a_proposal_is_not_written_back_when_persist_is_false() -> None:
    """A proposal the caller did not ask to persist leaves the section alone."""
    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]
    revised = (
        "Coral reefs shelter a quarter of all the marine species and their "
        f"larvae [#{coral.chunk_id}]."
    )
    editor = InlineEditor(
        rewrite=_RecordingRewrite(revised),
        chunks=chunks,
        checker=_checker(),
    )

    result = await editor.edit(_request(persist=False), section)

    assert result.section == section
    assert result.section.content == section.content
    assert result.revised_text == revised


# ============================================================================
# 5. The model is called once, and never with the corpus
# ============================================================================


@pytest.mark.asyncio
async def test_the_model_is_called_once_and_never_handed_the_corpus() -> None:
    """One edit is one model call, and the model sees a bounded slice.

    The whole corpus in the prompt would defeat the point of retrieving:
    the model would be free to ground the revision in a passage the edit
    has nothing to do with.
    """
    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]
    index = ProvenanceIndex.build(chunks)
    rewrite = _RecordingRewrite(
        f"Coral reefs shelter a quarter of all the marine species and their "
        f"larvae [#{coral.chunk_id}]."
    )
    editor = InlineEditor(
        rewrite=rewrite,
        index=index,
        chunks=chunks,
        checker=_checker(),
        max_passages=2,
    )

    await editor.edit(_request(), section)

    assert len(rewrite.calls) == 1
    passages = rewrite.calls[0]["passages"]
    assert len(passages) <= 2
    assert len(passages) < len(chunks)
    assert all(passage.chunk_id in {chunk.chunk_id for chunk in chunks} for passage in passages)


@pytest.mark.asyncio
async def test_the_convenience_call_applies_the_edit() -> None:
    """The one-call entry point behaves like the editor it builds."""
    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]
    revised = (
        "Coral reefs shelter a quarter of all the marine species and their "
        f"larvae [#{coral.chunk_id}]."
    )

    result = await apply_inline_edit(
        _request(),
        section,
        rewrite=_RecordingRewrite(revised),
        chunks=chunks,
        checker=_checker(),
    )

    assert result.applied is True
    assert result.section is not None
    assert result.section.revision_count == section.revision_count + 1


@pytest.mark.asyncio
async def test_a_whole_section_can_be_rewritten() -> None:
    """The section scope rewrites the section, not one of its paragraphs."""
    chunks = _chunks()
    section = _section(chunks)
    coral, mangrove = chunks[0], chunks[1]
    revised = (
        f"Coral reefs shelter a quarter of all marine species and their larvae "
        f"[#{coral.chunk_id}].\n\n"
        f"Mangrove forests slow storm surge and trap sediment along tropical "
        f"coasts [#{mangrove.chunk_id}]."
    )
    editor = InlineEditor(
        rewrite=_RecordingRewrite(revised),
        chunks=chunks,
        checker=_checker(),
    )

    result = await editor.edit(_request(scope=EditScope.SECTION), section)

    assert result.applied is True
    assert result.scope is EditScope.SECTION
    assert result.original_text == section.content.strip()
    assert result.section is not None
    assert result.section.content == revised


def test_the_default_rewrite_is_exported() -> None:
    """The language-model seam is part of the package's public surface."""
    assert callable(default_rewrite)


@pytest.mark.asyncio
async def test_the_offline_double_can_rewrite_a_paragraph() -> None:
    """The double that ships with the app can answer the rewrite seam.

    The offline provider is the app's promise that the product can be tried
    without a model account, so the seam has to speak the vocabulary that
    double answers: the passages it is given, the passage to work on, and
    the passage that comes back with its citations attached.
    """
    import dspy

    from lmeditor.llm.stub import StubLM

    chunks = _chunks()
    section = _section(chunks)
    coral = chunks[0]

    previous = dspy.settings.lm
    dspy.configure(lm=StubLM())
    try:
        editor = InlineEditor(
            index=ProvenanceIndex.build(chunks),
            chunks=chunks,
            checker=_checker(),
        )
        result = await editor.edit(_request(), section)
    finally:
        dspy.configure(lm=previous)

    assert result.applied is True
    assert coral.chunk_id in result.cited_chunk_ids
    assert result.scorecard is not None
    assert result.scorecard.contradicted_claims == 0
    assert result.section is not None
    assert result.section.revision_count == section.revision_count + 1
