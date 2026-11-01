"""Unit and assertion tests for DSPy drafting modules and teleprompters."""

from __future__ import annotations

from typing import Any

import dspy
import pytest
from dspy.dsp.utils.utils import dotdict

from lmeditor.core.exceptions import (
    DraftingError,
    DSPyAssertionError,
    LMNotConfiguredError,
    PassageBoundsError,
)
from lmeditor.core.schemas import OutlineNode, Perspective, PerspectiveRole, SectionDraft, SectionStatus, SourceChunk
from lmeditor.drafting.modules import CitationInserter, MacroCohesionModule, SectionDrafter
from lmeditor.drafting.signatures import (
    CitationInsertionSignature,
    MacroCohesionSignature,
    SectionDraftingSignature,
)
from lmeditor.drafting.teleprompter import (
    OPTIMIZER_BOOTSTRAP_FEW_SHOT,
    OPTIMIZER_MIPRO_V2,
    DraftingTeleprompterCompiler,
    citation_precision_metric,
)


class _StubLM(dspy.BaseLM):
    """A language-model double that answers the drafting signatures."""

    def __init__(self, answers: dict[str, str] | None = None, fail: bool = False) -> None:
        super().__init__("stub", "chat", 0.0, 1000, True)
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[str] = []

    def forward(self, prompt=None, messages=None, **kwargs: Any) -> Any:
        messages = messages or [{"role": "user", "content": prompt}]
        content = str(messages[-1]["content"])
        self.calls.append(content)
        if self.fail:
            raise RuntimeError("model server unreachable")
        body = "\n".join(f"[[ ## {name} ## ]]\n{value}" for name, value in self.answers.items())
        message = dotdict(content=body, tool_calls=None)
        return dotdict(
            choices=[dotdict(message=message, finish_reason="stop")],
            usage=dotdict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            model="stub",
        )


@pytest.fixture()
def restore_lm() -> Any:
    """Restore whatever language model was installed before the test."""
    previous = dspy.settings.lm
    yield previous
    dspy.configure(lm=previous)


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


def test_macro_cohesion_module_citation_preservation(restore_lm: Any) -> None:
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


def test_macro_cohesion_raises_when_the_model_call_fails(restore_lm: Any) -> None:
    """A failed cohesion call must not be swallowed into a silent pass-through."""
    dspy.configure(lm=_StubLM(fail=True))

    cohesion = MacroCohesionModule()
    cid = "4" * 64
    original = f"Chapter 1: Grounded writing requires verifiable token-span citations [#{cid}]."

    with pytest.raises(DraftingError):
        cohesion.forward(full_draft=original, topic="Writing Systems")


def test_macro_cohesion_raises_when_the_model_returns_nothing(restore_lm: Any) -> None:
    """An empty manuscript is a failure, not an excuse to return the original."""
    dspy.configure(lm=_StubLM(answers={"cohesive_manuscript": ""}))

    cohesion = MacroCohesionModule()
    cid = "5" * 64
    original = f"Chapter 1: Grounded writing requires verifiable token-span citations [#{cid}]."

    with pytest.raises(DraftingError):
        cohesion.forward(full_draft=original, topic="Writing Systems")


def test_macro_cohesion_polishes_when_nothing_is_lost(restore_lm: Any) -> None:
    """A polished manuscript that keeps every claim is accepted."""
    cid = "6" * 64
    original = f"First claim [#{cid}]. Second claim [#{cid}]."
    polished = f"Second claim [#{cid}]. First claim [#{cid}]."

    dspy.configure(lm=_StubLM(answers={"cohesive_manuscript": polished}))

    cohesion = MacroCohesionModule()
    result = cohesion.forward(full_draft=original, topic="Writing Systems")
    assert result == polished.strip()


def test_section_drafter_raises_without_a_configured_model(restore_lm: Any) -> None:
    """Without a model the drafter fails loudly instead of inventing prose."""
    dspy.configure(lm=None)

    drafter = SectionDrafter(use_chain_of_thought=False)
    node = OutlineNode(node_id="sec_1", title="Intro", depth=0)
    chunk = _make_dummy_chunk("a" * 64, "Methodology details on state machines.")

    with pytest.raises(LMNotConfiguredError):
        drafter.forward(
            topic="Test Topic",
            section_node=node,
            perspective="Architect",
            assigned_chunks=[chunk],
        )


def test_section_drafter_raises_a_typed_error_when_the_model_call_fails(
    restore_lm: Any,
) -> None:
    """A failed drafting call is reported as a DraftingError."""
    dspy.configure(lm=_StubLM(fail=True))

    drafter = SectionDrafter(use_chain_of_thought=False)
    node = OutlineNode(node_id="sec_1", title="Intro", depth=0)
    chunk = _make_dummy_chunk("a" * 64, "Methodology details on state machines.")

    with pytest.raises(DraftingError):
        drafter.forward(
            topic="Test Topic",
            section_node=node,
            perspective="Architect",
            assigned_chunks=[chunk],
        )


def test_citation_inserter_grounds_a_draft(restore_lm: Any) -> None:
    """The citation inserter returns the grounded draft it was asked for."""
    cid = "7" * 64
    chunk = _make_dummy_chunk(cid, "Municipal waste collection rates fell by 30 percent.")

    dspy.configure(
        lm=_StubLM(
            answers={
                "grounded_draft": (
                    f"Municipal waste collection rates fell by 30 percent [#{cid}]."
                )
            }
        )
    )

    inserter = CitationInserter()
    grounded = inserter.forward(
        draft_text="Municipal waste collection rates fell by 30 percent.",
        source_passages=[chunk],
    )

    assert f"[#{cid}]" in grounded
    assert "Municipal waste collection rates fell by 30 percent" in grounded


def test_citation_inserter_rejects_a_phantom_citation(restore_lm: Any) -> None:
    """A marker that names a passage the inserter was not given is refused."""
    cid = "8" * 64
    phantom = "9" * 64
    chunk = _make_dummy_chunk(cid, "Municipal waste collection rates fell by 30 percent.")

    dspy.configure(
        lm=_StubLM(
            answers={
                "grounded_draft": f"Municipal waste collection rates fell [#{phantom}]."
            }
        )
    )

    inserter = CitationInserter()
    with pytest.raises(PassageBoundsError):
        inserter.forward(
            draft_text="Municipal waste collection rates fell.",
            source_passages=[chunk],
        )


def test_citation_inserter_raises_without_a_configured_model(restore_lm: Any) -> None:
    """Without a model the inserter fails loudly."""
    dspy.configure(lm=None)

    chunk = _make_dummy_chunk("a" * 64, "Municipal waste collection rates fell by 30 percent.")
    inserter = CitationInserter()
    with pytest.raises(LMNotConfiguredError):
        inserter.forward(
            draft_text="Municipal waste collection rates fell by 30 percent.",
            source_passages=[chunk],
        )


def test_teleprompter_selects_the_optimizer_the_installed_dspy_provides() -> None:
    """The compiler uses MIPROv2 when the installed DSPy has it."""
    compiler = DraftingTeleprompterCompiler()

    available = DraftingTeleprompterCompiler.available_optimizers()
    assert OPTIMIZER_BOOTSTRAP_FEW_SHOT in available

    if OPTIMIZER_MIPRO_V2 in available:
        assert compiler.select_optimizer() == OPTIMIZER_MIPRO_V2
    else:
        assert compiler.select_optimizer() == OPTIMIZER_BOOTSTRAP_FEW_SHOT


def test_teleprompter_rejects_an_optimizer_that_is_not_installed() -> None:
    """An explicitly requested optimizer must exist."""
    compiler = DraftingTeleprompterCompiler(optimizer="does_not_exist")
    with pytest.raises(ValueError):
        compiler.select_optimizer()


def test_teleprompter_bootstraps_demonstrations(restore_lm: Any) -> None:
    """The BootstrapFewShot path compiles a drafting module."""
    cid = "b" * 64
    chunk = _make_dummy_chunk(cid, "Methodology details on state machines.")

    dspy.configure(
        lm=_StubLM(
            answers={
                "section_draft": f"The system employs a cyclic state graph [#{cid}].",
                "cited_chunk_ids": cid,
            }
        )
    )

    example = dspy.Example(
        topic="Architecture",
        section_title="Methodology",
        perspective="Architect",
        prior_section_summary="",
        assigned_chunks_text=SectionDrafter.format_assigned_chunks([chunk]),
        section_draft=f"The system employs a cyclic state graph [#{cid}].",
        cited_chunk_ids=[cid],
    ).with_inputs("topic", "section_title", "perspective", "prior_section_summary", "assigned_chunks_text")

    compiler = DraftingTeleprompterCompiler(optimizer=OPTIMIZER_BOOTSTRAP_FEW_SHOT)
    compiled = compiler.compile(SectionDrafter(use_chain_of_thought=False), [example])

    assert isinstance(compiled, SectionDrafter)


def test_citation_precision_metric_penalises_unverifiable_examples() -> None:
    """An example that names no passage cannot score like a verifiable one."""

    class Gold:
        assigned_chunks_text = "Passages were supplied."
        assigned_chunk_ids: list[str] = []

    class Pred:
        section_draft = "A grounded statement [#{'a' * 64}]."

    unverifiable = citation_precision_metric(Gold(), Pred())
    assert unverifiable <= 0.25


def test_citation_precision_metric_rewards_grounded_coverage() -> None:
    """A draft that uses the assigned passage beats one that ignores it."""
    cid_a = "c" * 64
    cid_b = "d" * 64

    class Gold:
        assigned_chunks_text = f"Source [#{cid_a}] [#{cid_b}]"
        assigned_chunk_ids = [cid_a, cid_b]

    class PredBoth:
        section_draft = f"First claim [#{cid_a}]. Second claim [#{cid_b}]."

    class PredOne:
        section_draft = f"First claim [#{cid_a}]."

    assert citation_precision_metric(Gold(), PredBoth()) > citation_precision_metric(
        Gold(), PredOne()
    )
