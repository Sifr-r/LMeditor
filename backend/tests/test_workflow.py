"""Unit and cyclic integration tests for LangGraph state machine workflow."""

from __future__ import annotations

import pytest

from lmeditor.core.schemas import (
    BibliographicRecord,
    OutlineNode,
    SectionDraft,
    SectionStatus,
    Source,
    SourceChunk,
    VerificationReport,
)
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.workflow.edges import route_after_hitl, route_after_verification
from lmeditor.workflow.graph import build_lmeditor_graph
from lmeditor.workflow.nodes import (
    critique_and_refine_node,
    draft_sections_node,
    hitl_checkpoint_node,
    ingest_sources_node,
    macro_cohesion_node,
    outline_synthesis_node,
    perspective_discovery_node,
    verify_citations_node,
)
from lmeditor.workflow.state import (
    HarnessState,
    append_lists,
    create_initial_harness_state,
    merge_dicts,
)
from tests.http_support import async_client


def test_harness_state_initialization_and_reducers() -> None:
    """Verify state initialization and pure functional reducer operators."""
    state = create_initial_harness_state(
        doc_id="doc_100",
        topic="Neural Architecture Search",
        raw_texts=["Passage text for document 100."],
    )
    assert state["doc_id"] == "doc_100"
    assert state["status"] == "initialized"
    assert len(state["raw_texts"]) == 1

    # Test merge_dicts
    d1 = {"sec_1": 1, "sec_2": 2}
    d2 = {"sec_2": 3, "sec_3": 4}
    merged = merge_dicts(d1, d2)
    assert merged == {"sec_1": 1, "sec_2": 3, "sec_3": 4}

    # Test append_lists
    l1 = ["err_1"]
    l2 = ["err_2", "err_3"]
    appended = append_lists(l1, l2)
    assert appended == ["err_1", "err_2", "err_3"]


def test_workflow_node_sequence() -> None:
    """Verify individual workflow nodes transform state deterministically."""
    sample_text = (
        "Transformer models rely on self-attention mechanisms to compute representations. "
        "Each attention head projects queries, keys, and values into independent subspaces. "
        "Feed-forward networks apply non-linear transformations after attention blocks."
    )

    state = create_initial_harness_state(
        doc_id="doc_test_wf",
        topic="Transformer Fundamentals",
        raw_texts=[sample_text],
    )

    # 1. Ingest
    ingest_res = ingest_sources_node(state)
    state.update(ingest_res)
    assert len(state["source_chunks"]) > 0
    assert state["status"] == "sources_indexed"

    # 2. Discover perspectives
    persp_res = perspective_discovery_node(state)
    state.update(persp_res)
    assert len(state["perspectives"]) >= 3
    assert state["status"] == "perspectives_discovered"

    # 3. Outline
    outline_res = outline_synthesis_node(state)
    state.update(outline_res)
    assert state["outline_root"] is not None
    assert len(state["outline_nodes"]) > 0
    assert state["status"] == "outline_synthesized"

    # 4. Draft sections
    draft_res = draft_sections_node(state)
    state.update(draft_res)
    assert len(state["section_drafts"]) > 0
    assert state["status"] == "sections_drafted"

    # 5. Verify citations
    verify_res = verify_citations_node(state)
    state.update(verify_res)
    assert len(state["verification_reports"]) > 0
    assert state["status"] == "citations_verified"

    # 6. Macro cohesion
    macro_res = macro_cohesion_node(state)
    state.update(macro_res)
    assert len(state["full_manuscript"]) > 0
    assert "# Transformer Fundamentals" in state["full_manuscript"]
    assert state["status"] == "completed"


def test_conditional_edges_routing() -> None:
    """Verify routing decision logic based on verification thresholds and retry limits."""
    # Scenario A: All sections verified
    sec1 = SectionDraft(
        section_id="sec_1",
        title="Intro",
        content="Grounded content.",
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )
    state_a: HarnessState = {
        "section_drafts": {"sec_1": sec1},
        "retry_counts": {"sec_1": 0},
    }
    assert route_after_verification(state_a) == "all_verified"

    # Scenario B: Unverified with retries < 3
    sec2 = SectionDraft(
        section_id="sec_2",
        title="Analysis",
        content="Unverified content.",
        is_verified=False,
        status=SectionStatus.FLAGGED,
    )
    state_b: HarnessState = {
        "section_drafts": {"sec_2": sec2},
        "retry_counts": {"sec_2": 1},
    }
    assert route_after_verification(state_b) == "needs_refinement"

    # Scenario C: Unverified with retries >= 3
    state_c: HarnessState = {
        "section_drafts": {"sec_2": sec2},
        "retry_counts": {"sec_2": 3},
    }
    assert route_after_verification(state_c) == "hitl_required"

    # Scenario D: HITL routing
    state_hitl_ok: HarnessState = {"hitl_approved": True}
    assert route_after_hitl(state_hitl_ok) == "proceed_to_macro"

    state_hitl_rejected: HarnessState = {"hitl_approved": False}
    assert route_after_hitl(state_hitl_rejected) == "terminate"


def test_compile_lmeditor_graph() -> None:
    """Verify that the full LangGraph state graph compiles successfully without cycles/errors."""
    compiled_app = build_lmeditor_graph()
    assert compiled_app is not None


# ---------------------------------------------------------------------------
# Task 13: Source-object support in HarnessState and ingest_sources_node
# ---------------------------------------------------------------------------


def test_initial_state_carries_empty_sources() -> None:
    """Default initial state exposes an empty ``sources`` list (Layer 0)."""
    state = create_initial_harness_state(doc_id="d1", topic="t", raw_texts=[])
    assert state.get("sources", []) == []


def test_initial_state_accepts_supplied_sources() -> None:
    """``create_initial_harness_state`` accepts and round-trips a list of Source."""
    src = Source(
        id="src_seed",
        kind=SourceKind.TEXT,
        origin="/tmp/x.txt",
        title="x.txt",
        bibliography=BibliographicRecord(
            title="Seed", resolver=BibliographyResolverKind.NONE
        ),
    )
    state = create_initial_harness_state(
        doc_id="d2", topic="t", raw_texts=[], sources=[src]
    )
    assert len(state["sources"]) == 1
    assert state["sources"][0].id == "src_seed"


# ---------------------------------------------------------------------------
# Task 14: POST /api/v1/sources/ingest route
# ---------------------------------------------------------------------------


def test_ingest_endpoint_accepts_text_file() -> None:
    """The ingest endpoint accepts a multipart text upload and returns 202 + chunks."""
    import asyncio
    import tempfile
    from pathlib import Path

    from lmeditor.server.app import create_app
    from lmeditor.server.sources import source_library

    app = create_app()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("hello from the API\n")
        path = Path(f.name)

    files = {"file": ("note.txt", path.read_bytes(), "text/plain")}
    data = {"source_id": "src_api", "title": "note.txt"}

    async def run() -> None:
        async with async_client(app) as client:
            response = await client.post(
                "/api/v1/sources/ingest",
                files=files,
                data=data,
            )
        assert response.status_code == 202
        body = response.json()
        assert body["source"]["id"] == "src_api"
        assert body["source"]["kind"] == "text"
        assert isinstance(body["chunks"], list)
        assert len(body["chunks"]) >= 1

    try:
        asyncio.run(run())
    finally:
        # The library is the server's memory of what it ingested, and the tier
        # keeps one registry for the whole process: this test leaves it as it
        # found it, and it does so whether the assertions passed or not.
        path.unlink(missing_ok=True)
        source_library.clear()
