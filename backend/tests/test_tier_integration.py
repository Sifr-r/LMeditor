"""The Python tier and the Kotlin tier must agree, through the real seam.

These tests drive the Python adapters in ``lmeditor.tier`` -- which speak the
NDJSON protocol to the Kotlin tier over a pipe -- and compare their answers
with the Python reference implementations that still live in the tree.

They are the end-to-end proof that the seam works: the framing, the error
mapping, the wire vocabulary and the rules themselves.

The Python side of every comparison comes from ``lmeditor._reference``: the
frozen, plain-Python witnesses. The live modules under ``lmeditor.core`` and
``lmeditor.verification`` are doors to the Kotlin tier, so comparing against
them would make the two sides agree by construction and prove nothing.

The test is skipped, not passed, when the Kotlin tier has not been built.
"""

from __future__ import annotations

import pytest

from lmeditor._reference import citations as py_citations
from lmeditor._reference.chunker import SentenceAwareChunker
from lmeditor._reference.claim_extractor import extract_claims as py_extract_claims
from lmeditor._reference.sections import merge_operator_text as py_merge_operator_text
from lmeditor.core.schemas import SectionDraft, SectionStatus
from lmeditor.tier import core as tier_core
from lmeditor.tier import editing as tier_editing
from lmeditor.tier import factcheck as tier_factcheck
from lmeditor.tier import text as tier_text

pytestmark = pytest.mark.usefixtures("kotlin_tier")

SAMPLE = (
    "The model hallucinates less often with retrieval. "
    "Grounding reduces the error rate [#chunk_a]. "
    "A third sentence carries no citation."
)


def test_the_citation_grammar_agrees_across_the_seam() -> None:
    assert tier_core.extract_chunk_ids(SAMPLE) == py_citations.extract_chunk_ids(SAMPLE)
    assert tier_core.strip_citations(SAMPLE) == py_citations.strip_citations(SAMPLE)


def test_the_citation_spans_agree_across_the_seam() -> None:
    tier_spans = tier_core.iter_citation_spans(SAMPLE)
    python_spans = list(py_citations.iter_citation_spans(SAMPLE))
    assert tier_spans == python_spans


def test_a_citation_span_is_reported_in_code_points_on_both_sides() -> None:
    # The emoji is one code point and two UTF-16 units. A tier that counted
    # UTF-16 units would report the span one character to the right.
    text = "Rocket \U0001F680 goes up [#astral] and lands."
    assert tier_core.iter_citation_spans(text) == list(
        py_citations.iter_citation_spans(text)
    )


def test_the_chunk_digest_agrees_across_the_seam() -> None:
    from lmeditor.core.schemas import SourceChunk

    tier_chunk = tier_core.create_source_chunk(
        document_id="doc",
        text="Hello",
        char_start=0,
        char_end=5,
        byte_start=0,
        byte_end=5,
    )
    python_chunk = SourceChunk.create(
        document_id="doc",
        text="Hello",
        char_start=0,
        char_end=5,
        byte_start=0,
        byte_end=5,
    )
    assert tier_chunk.chunk_id == python_chunk.chunk_id
    assert tier_chunk.text == python_chunk.text


def test_the_operator_edit_rule_agrees_across_the_seam() -> None:
    drafted = SectionDraft(
        section_id="sec-1",
        title="Drafted",
        content="The pipeline's words.",
        order=3,
        is_verified=True,
        status=SectionStatus.VERIFIED,
        revision_count=2,
        critique="tighten this",
    )
    tier_merged = tier_core.merge_operator_text(drafted, content="The operator's words.")
    python_merged = py_merge_operator_text(drafted, content="The operator's words.")

    assert tier_merged.content == python_merged.content
    assert tier_merged.status == python_merged.status
    assert tier_merged.is_verified == python_merged.is_verified
    assert tier_merged.claims == python_merged.claims
    assert tier_merged.critique == python_merged.critique


def test_the_chunker_agrees_across_the_seam() -> None:
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    tier_chunks = tier_text.chunk(text, "doc")
    python_chunks = SentenceAwareChunker().chunk(text, "doc")

    assert [chunk.chunk_id for chunk in tier_chunks] == [
        chunk.chunk_id for chunk in python_chunks
    ]
    assert [chunk.text for chunk in tier_chunks] == [chunk.text for chunk in python_chunks]
    assert [chunk.char_start for chunk in tier_chunks] == [
        chunk.char_start for chunk in python_chunks
    ]
    assert [chunk.char_end for chunk in tier_chunks] == [
        chunk.char_end for chunk in python_chunks
    ]


def test_the_claim_extractor_agrees_across_the_seam() -> None:
    tier_claims = tier_text.extract_claims(SAMPLE, "sec-1")
    python_claims = py_extract_claims(SAMPLE, "sec-1")

    assert [claim.claim_id for claim in tier_claims] == [
        claim.claim_id for claim in python_claims
    ]
    assert [claim.text for claim in tier_claims] == [claim.text for claim in python_claims]
    assert [claim.char_start for claim in tier_claims] == [
        claim.char_start for claim in python_claims
    ]
    assert [claim.char_end for claim in tier_claims] == [
        claim.char_end for claim in python_claims
    ]
    assert [claim.cited_chunk_ids for claim in tier_claims] == [
        claim.cited_chunk_ids for claim in python_claims
    ]


def test_a_domain_error_keeps_its_python_type_across_the_seam() -> None:
    from lmeditor.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as caught:
        tier_core.create_source_chunk(
            document_id="doc",
            text="Hello",
            char_start=5,
            char_end=5,
            byte_start=0,
            byte_end=5,
        )
    assert caught.value.field == "char_end"


def test_a_revision_that_cites_a_real_source_is_accepted_across_the_seam() -> None:
    from lmeditor.core.schemas import SourceChunk

    chunk = SourceChunk.create(
        document_id="doc",
        text="The sky is blue.",
        char_start=0,
        char_end=17,
        byte_start=0,
        byte_end=17,
    )
    section = SectionDraft(section_id="sec-1", title="T", content="The sky is blue.")

    accepted, reason, cited, claims = tier_editing.validate_revision(
        revised_text=f"The sky is blue [#{chunk.chunk_id}].",
        section=section,
        chunks=[chunk],
    )

    assert accepted is True
    assert reason
    assert cited == [chunk.chunk_id]
    assert claims


def test_a_revision_that_invents_a_citation_is_refused_across_the_seam() -> None:
    from lmeditor.core.schemas import SourceChunk

    chunk = SourceChunk.create(
        document_id="doc",
        text="The sky is blue.",
        char_start=0,
        char_end=17,
        byte_start=0,
        byte_end=17,
    )
    section = SectionDraft(section_id="sec-1", title="T", content="The sky is blue.")

    accepted, reason, cited, claims = tier_editing.validate_revision(
        revised_text="The sky is blue [#bbbb].",
        section=section,
        chunks=[chunk],
    )

    assert accepted is False
    assert "bbbb" in reason
    assert cited == ["bbbb"]
    assert claims == []


def test_the_fact_check_verdict_is_aggregated_across_the_seam() -> None:
    report, scorecard = tier_factcheck.aggregate(
        doc_id="doc-1",
        text="The sky is blue.",
        claims=[
            {
                "claim_id": "c1",
                "section_id": "sec-1",
                "text": "The sky is blue.",
                "char_start": 0,
                "char_end": 17,
            }
        ],
        verdicts=[
            {
                "claim_id": "c1",
                "claim_text": "The sky is blue.",
                "status": "entailed",
                "score": 0.9,
                "evidence": [
                    {
                        "source": "corpus",
                        "chunk_id": "a" * 64,
                        "excerpt": "The sky is blue.",
                        "score": 1.0,
                    }
                ],
                "reasoning": "The passage says so.",
            }
        ],
    )

    assert report.verdict.value == "supported"
    assert report.doc_id == "doc-1"
    assert scorecard is not None
    assert scorecard.total_claims == 1
    assert scorecard.supported_claims == 1
    assert scorecard.contradicted_claims == 0
