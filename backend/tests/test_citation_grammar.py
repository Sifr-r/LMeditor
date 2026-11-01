"""Regression tests for the canonical inline-citation grammar (AUDIT finding 6).

The drafter emits ``[#chunk_id]``. Before the shared grammar module existed, the
verifier's private regex excluded ``#``, so correctly cited drafts were parsed as
uncited, scored as ungrounded, and pushed into endless revision loops. These tests
pin the grammar and the drafter/verifier agreement.
"""

from __future__ import annotations

import pytest

from lmeditor.core.citations import (
    CITATION_PATTERN,
    extract_chunk_ids,
    iter_citation_spans,
    strip_citations,
)
from lmeditor.core.exceptions import PassageBoundsError
from lmeditor.core.schemas import OutlineNode, SourceChunk
from lmeditor.drafting.modules import MacroCohesionModule, SectionDrafter
from lmeditor.verification import claim_extractor as claim_extractor_module
from lmeditor.verification.claim_extractor import extract_claims

# ---------------------------------------------------------------------------
# §2 grammar: the drafter's marker form must be understood by the verifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A supported fact [#abc123].",
        "A supported fact [abc123].",
        "A supported fact [cite: abc123].",
    ],
    ids=["hash-prefix", "bare", "cite-prefix"],
)
def test_extract_claims_keeps_citation_attached(text: str) -> None:
    """A cited sentence yields exactly one claim carrying the bare chunk id."""
    claims = extract_claims(text, "s")

    assert len(claims) == 1
    assert claims[0].cited_chunk_ids == ["abc123"]
    assert claims[0].text == "A supported fact"


def test_extract_claims_parses_comma_separated_marker_list() -> None:
    """``[#a, #b]`` attaches both chunk ids to the sentence's claim."""
    claims = extract_claims("A supported fact [#a, #b].", "s")

    assert len(claims) == 1
    assert claims[0].cited_chunk_ids == ["a", "b"]


def test_markdown_links_and_images_are_not_citations() -> None:
    """``[text](url)`` and ``![alt](url)`` must never be read as citations."""
    markdown = "See [the source paper](https://example.org/paper) and ![a chart](https://example.org/c.png)."

    assert extract_chunk_ids(markdown) == []
    assert extract_claims(markdown, "s")[0].cited_chunk_ids == []


def test_citation_markers_are_stripped_from_claim_text() -> None:
    """``strip_citations`` removes every accepted marker form."""
    text = "Facts [#a] and [b] and [cite: c] and [#d, #e] are stated."

    assert extract_chunk_ids(strip_citations(text)) == []
    assert "[" not in strip_citations(text)


def test_iter_citation_spans_reports_offsets_for_each_id() -> None:
    """A comma group reports the whole marker span for each of its ids."""
    text = "First [#a, #b]. Then [c]."

    spans = iter_citation_spans(text)

    assert [chunk_id for chunk_id, _s, _e in spans] == ["a", "b", "c"]
    assert spans[0][1] == text.index("[#a, #b]")
    assert spans[0][2] == text.index("[#a, #b]") + len("[#a, #b]")
    assert spans[2][1] == text.index("[c]")


# ---------------------------------------------------------------------------
# No divergent private regex may survive in either consumer
# ---------------------------------------------------------------------------


def test_drafter_and_verifier_share_one_pattern() -> None:
    """Both consumers must reference the same compiled grammar object."""
    assert SectionDrafter.CITATION_PATTERN is CITATION_PATTERN
    assert not hasattr(claim_extractor_module, "_CITATION_REGEX")


@pytest.mark.parametrize(
    "text",
    [
        "Mixed [#abc123] and [def456] and [cite: ghi789] and [#j, #k].",
        "Markdown [link](https://example.org) and ![img](https://example.org/x.png) only.",
        "Plain prose with no markers whatsoever.",
    ],
)
def test_drafter_and_core_grammar_agree(text: str) -> None:
    """``SectionDrafter.extract_citation_ids`` matches ``extract_chunk_ids``."""
    assert SectionDrafter.extract_citation_ids(text) == extract_chunk_ids(text)


def test_drafter_accepts_general_id_alphabet_not_only_sha256_hex() -> None:
    """The drafter must not be restricted to 64-char hex ids (HarnessState/test ids)."""
    assert SectionDrafter.extract_citation_ids("Data from [chunk_1] and [cite: s2].") == [
        "chunk_1",
        "s2",
    ]


# ---------------------------------------------------------------------------
# Drafter output -> verifier round trip (the end-to-end shape of finding 6)
# ---------------------------------------------------------------------------


def test_drafter_output_round_trips_into_verified_citations() -> None:
    """A real drafter draft with ``[#hex]`` survives the pass-bounds check and
    is still cited when the verifier extracts claims from it."""
    hex_id = "ab12cd34" * 8  # 64 hex chars, matches the SourceChunk id pattern
    source = SourceChunk.create(
        document_id="doc_rt",
        text="Mortality fell by 30 percent in the trial cohort.",
        char_start=0,
        char_end=47,
        byte_start=0,
        byte_end=47,
    ).model_copy(update={"chunk_id": hex_id})

    class _FakePrediction:
        section_draft = f"Mortality fell by 30 percent in the trial cohort [#{hex_id}]."

    drafter = SectionDrafter(use_chain_of_thought=False)
    drafter.draft_program = lambda **kwargs: _FakePrediction()  # type: ignore[assignment]

    draft = drafter.forward(
        topic="Trial outcomes",
        section_node=OutlineNode(node_id="sec_rt", title="Results"),
        perspective="Analyst",
        assigned_chunks=[source],
    )

    assert draft.cited_chunk_ids == [hex_id]
    claims = extract_claims(draft.content, draft.section_id)
    assert claims
    assert all(hex_id in claim.cited_chunk_ids for claim in claims)


def test_drafter_still_rejects_hallucinated_chunk_id_with_hash_marker() -> None:
    """Passage bounds still fire for a ``[#id]`` marker outside the assigned set."""
    hex_id = "ab12cd34" * 8
    assigned = SourceChunk.create(
        document_id="doc_hb",
        text="Assigned passage.",
        char_start=0,
        char_end=17,
        byte_start=0,
        byte_end=17,
    ).model_copy(update={"chunk_id": hex_id})

    class _PhantomPrediction:
        section_draft = "An unsupported assertion citing a phantom chunk [#ffffffffffffffff]."

    drafter = SectionDrafter(use_chain_of_thought=False)
    drafter.draft_program = lambda **kwargs: _PhantomPrediction()  # type: ignore[assignment]

    with pytest.raises(PassageBoundsError):
        drafter.forward(
            topic="Security",
            section_node=OutlineNode(node_id="sec_hb", title="Security"),
            perspective="Auditor",
            assigned_chunks=[assigned],
        )


# ---------------------------------------------------------------------------
# Finding 9: the macro polisher must not smuggle in an unsupported rewrite
# ---------------------------------------------------------------------------


def test_macro_cohesion_rejects_rewritten_assertion_with_kept_marker() -> None:
    """Preserving the marker is not enough: the assertion itself must survive.

    This is the exact attack from the audit -- an LM turned a mortality claim
    into a cancer-cure claim while keeping its citation marker.
    """
    original = "Chapter 1: Mortality fell by 30 percent in the trial cohort [#abc123]."

    class _RewritingPrediction:
        cohesive_manuscript = "Chapter 1: This intervention cures cancer in every patient [#abc123]."

    cohesion = MacroCohesionModule()
    cohesion.cohesion_program = lambda **kwargs: _RewritingPrediction()  # type: ignore[assignment]

    result = cohesion.forward(full_draft=original, topic="Trial outcomes")

    assert result == original
    assert "cures cancer" not in result


def test_macro_cohesion_rejects_phantom_citation_id() -> None:
    """A citation id absent from the verified draft is rejected."""
    original = "Chapter 1: Mortality fell by 30 percent in the trial cohort [#abc123]."

    class _PhantomPrediction:
        cohesive_manuscript = (
            "Chapter 1: Mortality fell by 30 percent in the trial cohort [#abc123, #deadbeef]."
        )

    cohesion = MacroCohesionModule()
    cohesion.cohesion_program = lambda **kwargs: _PhantomPrediction()  # type: ignore[assignment]

    result = cohesion.forward(full_draft=original, topic="Trial outcomes")

    assert result == original
    assert "deadbeef" not in result


def test_macro_cohesion_rejects_dropped_repeated_citation() -> None:
    """Repeated-citation counts must be preserved, not just the id set."""
    original = (
        "Chapter 1: Mortality fell by 30 percent [#abc123]. "
        "The same effect held in both cohorts [#abc123]."
    )

    class _DroppingPrediction:
        cohesive_manuscript = "Chapter 1: Mortality fell by 30 percent [#abc123]."

    cohesion = MacroCohesionModule()
    cohesion.cohesion_program = lambda **kwargs: _DroppingPrediction()  # type: ignore[assignment]

    result = cohesion.forward(full_draft=original, topic="Trial outcomes")

    assert result == original


def test_macro_cohesion_accepts_rewrite_that_preserves_the_claim_set() -> None:
    """A polish that leaves every assertion and marker intact is accepted.

    Rewriting ``[#abc123]`` into the equivalent ``[cite: abc123]`` is safe, so
    the polisher is not a no-op.
    """
    original = "Chapter 1: Mortality fell by 30 percent in the trial cohort [#abc123]."

    class _FaithfulPrediction:
        cohesive_manuscript = "Chapter 1: Mortality fell by 30 percent in the trial cohort [cite: abc123]."

    cohesion = MacroCohesionModule()
    cohesion.cohesion_program = lambda **kwargs: _FaithfulPrediction()  # type: ignore[assignment]

    result = cohesion.forward(full_draft=original, topic="Trial outcomes")

    assert result != original
    assert extract_chunk_ids(result) == ["abc123"]
    assert "Mortality fell by 30 percent in the trial cohort" in result


def test_macro_cohesion_rejects_polish_that_repartitions_claims() -> None:
    """Reformatting a heading onto its own line changes the extracted claim set.

    This documents the deliberate trade-off required by the audit: the polisher
    may not change the claim partition at all, and a verified-but-unpolished
    document is preferred over an unverified rewrite.
    """
    original = "Chapter 1: Mortality fell by 30 percent in the trial cohort [#abc123]."

    class _ReformattingPrediction:
        cohesive_manuscript = (
            "Chapter 1:\n\nMortality fell by 30 percent in the trial cohort [#abc123].\n"
        )

    cohesion = MacroCohesionModule()
    cohesion.cohesion_program = lambda **kwargs: _ReformattingPrediction()  # type: ignore[assignment]

    result = cohesion.forward(full_draft=original, topic="Trial outcomes")

    assert result == original
