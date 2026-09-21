"""LangGraph typed state definition and pure reducer operators for LMeditor."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from lmeditor.core.schemas import (
    OutlineNode,
    Perspective,
    SectionDraft,
    SourceChunk,
    VerificationReport,
)


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Pure dictionary reducer merging updates without side effects."""
    res = dict(a)
    res.update(b)
    return res


def append_lists(a: list[Any], b: list[Any]) -> list[Any]:
    """Pure list reducer concatenating elements."""
    return list(a) + list(b)


class HarnessState(TypedDict, total=False):
    """Central LangGraph state passed through the cyclic writing harness."""

    # Document metadata
    doc_id: str
    topic: str
    target_audience: str
    guidelines: list[str]

    # Layer 1: Ingested & Indexed Source Chunks
    raw_texts: list[str]
    source_chunks: dict[str, SourceChunk]  # chunk_id -> SourceChunk

    # Layer 2: STORM Discovery & Outlining
    perspectives: list[Perspective]
    outline_root: OutlineNode | None
    outline_nodes: list[OutlineNode]  # Flat list of leaf sections

    # Layer 3: DSPy Drafting
    section_drafts: Annotated[dict[str, SectionDraft], merge_dicts]  # section_id -> SectionDraft
    current_section_id: str | None

    # Layer 4: Verification & Citation Fidelity
    verification_reports: Annotated[dict[str, VerificationReport], merge_dicts]  # section_id -> report
    retry_counts: Annotated[dict[str, int], merge_dicts]  # section_id -> int
    critiques: Annotated[dict[str, str], merge_dicts]  # section_id -> critique string

    # Layer 5: Macro Cohesion & Polish
    full_manuscript: str
    status: str
    errors: Annotated[list[str], append_lists]

    # Human-in-the-Loop Review Gates
    requires_hitl: bool
    hitl_reason: str | None
    hitl_approved: bool


def create_initial_harness_state(
    doc_id: str,
    topic: str,
    target_audience: str = "Technical & Academic Readers",
    raw_texts: list[str] | None = None,
    guidelines: list[str] | None = None,
) -> HarnessState:
    """Initialize a clean, deterministic HarnessState."""
    return HarnessState(
        doc_id=doc_id,
        topic=topic,
        target_audience=target_audience,
        guidelines=guidelines or [],
        raw_texts=raw_texts or [],
        source_chunks={},
        perspectives=[],
        outline_root=None,
        outline_nodes=[],
        section_drafts={},
        current_section_id=None,
        verification_reports={},
        retry_counts={},
        critiques={},
        full_manuscript="",
        status="initialized",
        errors=[],
        requires_hitl=False,
        hitl_reason=None,
        hitl_approved=False,
    )


__all__ = [
    "HarnessState",
    "append_lists",
    "create_initial_harness_state",
    "merge_dicts",
]
