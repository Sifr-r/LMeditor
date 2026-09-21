"""Unit and assertion tests for DSPy drafting modules and teleprompters."""

from __future__ import annotations

import dspy
import pytest

from lmeditor.core.exceptions import DSPyAssertionError, PassageBoundsError
from lmeditor.core.schemas import OutlineNode, Perspective, PerspectiveRole, SectionDraft, SectionStatus, SourceChunk
from lmeditor.drafting.modules import MacroCohesionModule, SectionDrafter
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)
from lmeditor.drafting.teleprompter import (
    DraftingTeleprompterCompiler,
    citation_precision_metric,
)


def _make_dummy_chunk(chunk_id: str, text: str) -> SourceChunk:
    return SourceChunk.create(
        document_id="doc_test",
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    ).model_copy(update={"chunk_id": chunk_id})


def test_dspy_signatures_definition() -> None:
    """Verify DSPy signature input and output fields."""
    sig = SectionDraftingSignature
    assert "topic" in sig.input_fields
    assert "section_title" in sig.input_fields
    assert "assigned_chunks_text" in sig.input_fields
    assert "section_draft" in sig.output_fields

    macro_sig = MacroCohesionSignature
    assert "full_draft" in macro_sig.input_fields
    assert "cohesive_manuscript" in macro_sig.output_fields


def test_section_drafter_chunk_formatting_and_id_extraction() -> None:
    """Verify chunk formatting and regex extraction of chunk IDs."""
    c1 = _make_dummy_chunk("a" * 64, "Text of chunk A.")
    c2 = _make_dummy_chunk("b" * 64, "Text of chunk B.")

    formatted = SectionDrafter.format_assigned_chunks([c1, c2])
    assert f"[#CHUNK_ID: {c1.chunk_id}]" in formatted
    assert f"[#CHUNK_ID: {c2.chunk_id}]" in formatted

    sample_draft = f"According to findings [#{c1.chunk_id}], neural networks learn representations [cite: {c2.chunk_id}]."
    extracted = SectionDrafter.extract_citation_ids(sample_draft)
    assert extracted == [c1.chunk_id, c2.chunk_id]


def test_section_drafter_empty_passages_raises_error() -> None:
    """Verify drafter rejects empty assigned passages."""
    drafter = SectionDrafter(use_chain_of_thought=False)
    node = OutlineNode(node_id="sec_1", title="Intro", depth=0)
    persp = Perspective(
        perspective_id="p1",
        role=PerspectiveRole.DOMAIN_EXPERT,
        title="Domain Expert",
        description="Expert in systems",
        domain="Systems",
    )

    with pytest.raises(PassageBoundsError):
        drafter.forward(
            topic="Test Topic",
            section_node=node,
            perspective=persp,
            assigned_chunks=[],
        )


def test_section_drafter_mock_forward_success() -> None:
    """Verify section drafter successfully constructs SectionDraft when predictions are valid."""
    drafter = SectionDrafter(use_chain_of_thought=False)
    node = OutlineNode(node_id="sec_1", title="Methodology", depth=0)
    c1 = _make_dummy_chunk("c" * 64, "Methodology details on state machines.")

    # Mock the internal draft program to return valid output with citation
    class MockPrediction:
        section_draft = f"The system employs a cyclic state graph [#{c1.chunk_id}]."
        cited_chunk_ids = [c1.chunk_id]

    drafter.draft_program = lambda **kwargs: MockPrediction()

    draft = drafter.forward(
        topic="Architecture",
        section_node=node,
        perspective="Architect",
        assigned_chunks=[c1],
    )

    assert isinstance(draft, SectionDraft)
    assert draft.section_id == "sec_1"
    assert draft.title == "Methodology"
    assert c1.chunk_id in draft.content
    assert draft.status == SectionStatus.DRAFTED
    assert draft.cited_chunk_ids == [c1.chunk_id]


def test_section_drafter_hallucinated_citation_assertion() -> None:
    """Verify assertion fails when drafter cites a chunk not in assigned chunks."""
    drafter = SectionDrafter(use_chain_of_thought=False)
    node = OutlineNode(node_id="sec_1", title="Security Analysis", depth=0)
    c_assigned = _make_dummy_chunk("d" * 64, "Assigned security passage.")
    c_hallucinated = "e" * 64

    class MockHallucinatedPrediction:
        section_draft = f"This is an ungrounded claim citing unassigned chunk [#{c_hallucinated}]."
        cited_chunk_ids = [c_hallucinated]

    drafter.draft_program = lambda **kwargs: MockHallucinatedPrediction()

    with pytest.raises(PassageBoundsError):
        drafter.forward(
            topic="Security",
            section_node=node,
            perspective="Auditor",
            assigned_chunks=[c_assigned],
        )


def test_citation_precision_metric_calculations() -> None:
    """Verify mathematical optimization metric behavior."""
    cid_valid = "1" * 64
    cid_hallucinated = "2" * 64

    class Gold:
        assigned_chunks_text = f"Source [#{cid_valid}]"
        assigned_chunk_ids = [cid_valid]

    class PredPerfect:
        section_draft = f"This is a comprehensive expository analysis grounded in valid evidence [#{cid_valid}]. " * 15

    class PredEmpty:
        section_draft = ""

    class PredHallucinated:
        section_draft = f"Fact [#{cid_hallucinated}]."

    score_perfect = citation_precision_metric(Gold(), PredPerfect())
    assert score_perfect > 0.8

    score_empty = citation_precision_metric(Gold(), PredEmpty())
    assert score_empty == 0.0

    score_hallucinated = citation_precision_metric(Gold(), PredHallucinated())
    assert score_hallucinated < 0.5


def test_macro_cohesion_module_citation_preservation() -> None:
    """Verify macro cohesion module prevents citation dropping."""
    cid = "3" * 64
    original = f"Chapter 1: Grounded writing requires verifiable token-span citations [#{cid}]."

    cohesion = MacroCohesionModule()

    # If the model prediction drops citations, it should fall back to original
    class MockDroppingPrediction:
        cohesive_manuscript = "Chapter 1: Grounded writing is nice."

    cohesion.cohesion_program = lambda **kwargs: MockDroppingPrediction()
    result = cohesion.forward(full_draft=original, topic="Writing Systems")
    assert cid in result
    assert result == original
