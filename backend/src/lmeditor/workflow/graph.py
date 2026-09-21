"""LangGraph compiled state machine for the LMeditor writing harness."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from lmeditor.workflow.edges import route_after_hitl, route_after_verification
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
from collections.abc import Sequence
from lmeditor.core.schemas import SourceChunk
from lmeditor.workflow.state import HarnessState, create_initial_harness_state


def build_lmeditor_graph(checkpointer: Any | None = None) -> Any:
    """Build and compile the cyclic LMeditor state machine."""
    workflow = StateGraph(HarnessState)

    # Register operational nodes
    workflow.add_node("ingest_sources", ingest_sources_node)
    workflow.add_node("discover_perspectives", perspective_discovery_node)
    workflow.add_node("synthesize_outline", outline_synthesis_node)
    workflow.add_node("draft_sections", draft_sections_node)
    workflow.add_node("verify_citations", verify_citations_node)
    workflow.add_node("critique_and_refine", critique_and_refine_node)
    workflow.add_node("macro_cohesion", macro_cohesion_node)
    workflow.add_node("hitl_checkpoint", hitl_checkpoint_node)

    # Define linear execution edges
    workflow.set_entry_point("ingest_sources")
    workflow.add_edge("ingest_sources", "discover_perspectives")
    workflow.add_edge("discover_perspectives", "synthesize_outline")
    workflow.add_edge("synthesize_outline", "draft_sections")
    workflow.add_edge("draft_sections", "verify_citations")

    # Define cyclic critique and verification routing
    workflow.add_conditional_edges(
        "verify_citations",
        route_after_verification,
        {
            "all_verified": "macro_cohesion",
            "needs_refinement": "critique_and_refine",
            "hitl_required": "hitl_checkpoint",
        },
    )

    # Redraft loop: critique routes back to drafting
    workflow.add_edge("critique_and_refine", "draft_sections")

    # Human-in-the-loop decision routing
    workflow.add_conditional_edges(
        "hitl_checkpoint",
        route_after_hitl,
        {
            "proceed_to_macro": "macro_cohesion",
            "terminate": END,
        },
    )

    # Macro cohesion finishes the document
    workflow.add_edge("macro_cohesion", END)

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)


async def run_workflow(
    doc_id: str,
    topic: str,
    source_chunks: Sequence[SourceChunk] | Sequence[dict[str, Any]] | None = None,
    max_retries: int = 3,
    threshold_f_beta: float = 0.85,
    session: Any | None = None,
    raw_texts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Execute the compiled LangGraph workflow asynchronously."""
    graph = build_lmeditor_graph()

    chunks: dict[str, SourceChunk] = {}
    if source_chunks:
        for c in source_chunks:
            if isinstance(c, SourceChunk):
                chunks[c.chunk_id] = c
            elif isinstance(c, dict):
                sc = SourceChunk(**c)
                chunks[sc.chunk_id] = sc

    initial_state = create_initial_harness_state(
        doc_id=doc_id,
        topic=topic,
        raw_texts=list(raw_texts or []),
    )
    initial_state["source_chunks"] = chunks
    initial_state["retry_counts"] = {}

    config = {"configurable": {"thread_id": doc_id}}

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
    except (AttributeError, NotImplementedError):
        import asyncio
        final_state = await asyncio.to_thread(graph.invoke, initial_state, config=config)

    res = dict(final_state)
    section_drafts = res.get("section_drafts", {})
    if isinstance(section_drafts, dict):
        res["sections"] = sorted(section_drafts.values(), key=lambda d: getattr(d, "order", 0))
    elif isinstance(section_drafts, list):
        res["sections"] = section_drafts
    else:
        res["sections"] = []

    return res


__all__ = [
    "build_lmeditor_graph",
    "run_workflow",
]
