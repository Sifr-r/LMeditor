"""Tests for the caller-supplied outline: the operator's steer.

A caller-supplied outline decides *what the document covers*; the corpus
decides *what the document may claim*. These tests hold both halves of that
line.
"""

from __future__ import annotations

import hashlib

import pytest

from lmeditor.core.schemas import OutlineNodeInput, SourceChunk
from lmeditor.discovery.outliner import OutlineGenerator


def _chunk(text: str) -> SourceChunk:
    """A corpus chunk with a stable id."""
    return SourceChunk(
        chunk_id=hashlib.sha256(text.encode()).hexdigest(),
        document_id="doc_1",
        text=text,
    )


CHUNKS = [
    _chunk("Retrieval quality dominates answer quality in open-domain question answering."),
    _chunk("Dense vector indexes accelerate nearest neighbour search for large corpora."),
    _chunk("Citation provenance makes every claim traceable to its source passage."),
]


def _sections() -> list[OutlineNodeInput]:
    """The sections an operator would name."""
    return [
        OutlineNodeInput(
            title="Why retrieval quality decides answer quality",
            description="Evidence that retrieval dominates.",
        ),
        OutlineNodeInput(
            title="Indexing at scale",
            description="Dense indexes and speed.",
            children=[
                OutlineNodeInput(title="Vector search", description="Nearest neighbour search."),
            ],
        ),
        OutlineNodeInput(title="Provenance and trust", description="Tracing claims to sources."),
    ]


def test_the_callers_sections_become_the_outline() -> None:
    """The operator's titles are the outline's titles, in their order."""
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=CHUNKS
    )

    assert [node.title for node in outline.nodes] == [
        "Why retrieval quality decides answer quality",
        "Indexing at scale",
        "Provenance and trust",
    ]


def test_the_callers_nesting_is_preserved() -> None:
    """A nested section stays nested under the section it was nested in."""
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=CHUNKS
    )

    nested = outline.nodes[1]
    assert nested.title == "Indexing at scale"
    assert [child.title for child in nested.children] == ["Vector search"]
    assert nested.children[0].depth == 1


def test_every_section_is_grounded_in_the_corpus() -> None:
    """A supplied outline decides what is covered, not what is claimed.

    Every section the operator named must still be given passages to write
    from. A section with no passage could only be filled with invention.
    """
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=CHUNKS
    )

    leaves = outline.get_leaves()
    assert leaves, "the outline must have leaves to write"
    for leaf in leaves:
        assert leaf.assigned_chunk_ids, f"'{leaf.title}' has no passage to write from"


def test_a_passage_is_assigned_to_at_most_one_section() -> None:
    """A passage grounds one section, so it cannot be counted twice."""
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=CHUNKS
    )

    assigned = [
        chunk_id for leaf in outline.get_leaves() for chunk_id in leaf.assigned_chunk_ids
    ]
    assert len(assigned) == len(set(assigned))


def test_every_assignment_names_a_real_chunk() -> None:
    """No section may be assigned a passage that is not in the corpus."""
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=CHUNKS
    )

    known = {chunk.chunk_id for chunk in CHUNKS}
    for leaf in outline.get_leaves():
        for chunk_id in leaf.assigned_chunk_ids:
            assert chunk_id in known


def test_an_outline_with_no_sections_yields_no_outline() -> None:
    """An empty outline is not an outline; the caller gets nothing, not a stub."""
    outline = OutlineGenerator().build_outline_from_sections(
        [], topic="Retrieval", available_chunks=CHUNKS
    )

    assert list(outline.nodes) == []


def test_an_outline_supplied_without_a_corpus_is_still_the_callers_sections() -> None:
    """With no corpus there is nothing to ground, but the shape is still the caller's."""
    outline = OutlineGenerator().build_outline_from_sections(
        _sections(), topic="Retrieval", available_chunks=[]
    )

    assert [node.title for node in outline.nodes] == [
        "Why retrieval quality decides answer quality",
        "Indexing at scale",
        "Provenance and trust",
    ]
    for leaf in outline.get_leaves():
        assert leaf.assigned_chunk_ids == []


def test_the_request_model_rejects_a_section_with_no_title() -> None:
    """A section must say what it is."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OutlineNodeInput(title="   ")


def test_the_request_model_rejects_an_unknown_field() -> None:
    """A section carries a title, a description and children -- nothing else."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OutlineNodeInput(title="A section", chunk_ids=["a-chunk-id"])
