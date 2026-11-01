"""The workflow rules, as the Kotlin tier implements them.

The state and the routing rules are Kotlin's; the graph that drives them is
LangGraph's, and stays in Python. These calls are how the graph asks the
routing rules where to go next.
"""

from __future__ import annotations

from typing import Any

from lmeditor.core.schemas import (
    OutlineNode,
    OutlineNodeInput,
    Perspective,
    ResearchMemory,
    SectionDraft,
    Source,
    SourceChunk,
    VerificationReport,
)
from lmeditor.tier.bridge import tier_bridge


def create_initial_state(
    doc_id: str,
    topic: str,
    target_audience: str | None = None,
    raw_texts: list[str] | None = None,
    sources: list[Source] | None = None,
    guidelines: list[str] | None = None,
    max_retries: int | None = None,
    threshold_f_beta: float | None = None,
) -> dict[str, Any]:
    """The state a run starts from.

    A parameter left as ``None`` is not sent, so the Kotlin tier's own default
    applies. The retry budget, the groundedness threshold and the default
    audience are owned by the tier; the Python side holds no second copy of
    any of the three.
    """
    params: dict[str, Any] = {"doc_id": doc_id, "topic": topic}
    if target_audience is not None:
        params["target_audience"] = target_audience
    if raw_texts is not None:
        params["raw_texts"] = raw_texts
    if sources is not None:
        params["sources"] = [source.model_dump(mode="json") for source in sources]
    if guidelines is not None:
        params["guidelines"] = guidelines
    if max_retries is not None:
        params["max_retries"] = max_retries
    if threshold_f_beta is not None:
        params["threshold_f_beta"] = threshold_f_beta

    result = tier_bridge().call("workflow.create_initial_state", params)
    state = result.get("state")
    if not isinstance(state, dict):
        raise ValueError("the Kotlin tier returned no workflow state")
    return _rehydrate_state(state)


def route_after_verification(state: dict[str, Any]) -> str:
    """The next step after the verification step.

    The routing question is what crosses the seam: the sections, the retry
    counts, the retry budget and the operator's decision. The authoritative
    state stays in the Python tier that owns the graph, because the graph is
    what reads the rest of it.
    """
    result = tier_bridge().call(
        "workflow.route",
        {
            "route": "after_verification",
            "section_drafts": _dump_section_drafts(state),
            "retry_counts": dict(state.get("retry_counts") or {}),
            "max_retries": state.get("max_retries"),
            "hitl_approved": bool(state.get("hitl_approved", False)),
        },
    )
    return str(result.get("next_step", ""))


def route_after_hitl(state: dict[str, Any]) -> str:
    """The next step after the human-in-the-loop gate.

    Only the operator's decision is read here, so only the operator's
    decision crosses the seam.
    """
    result = tier_bridge().call(
        "workflow.route",
        {
            "route": "after_hitl",
            "hitl_approved": bool(state.get("hitl_approved", False)),
        },
    )
    return str(result.get("next_step", ""))


def _dump_section_drafts(state: dict[str, Any]) -> dict[str, Any]:
    """The section drafts, in their wire form."""
    drafts = state.get("section_drafts") or {}
    return {
        str(key): value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        for key, value in drafts.items()
    }


def _rehydrate_state(state: dict[str, Any]) -> dict[str, Any]:
    """The wire state, as the Python tier's own models.

    The state is the largest thing that crosses the seam, so it crosses it
    whole: every model in it is rebuilt into the Python class the graph and
    its nodes expect, rather than left as a plain dict.
    """
    hydrated: dict[str, Any] = dict(state)

    hydrated["sources"] = [
        Source.model_validate(item) for item in state.get("sources") or []
    ]
    hydrated["source_chunks"] = {
        str(key): SourceChunk.model_validate(value)
        for key, value in (state.get("source_chunks") or {}).items()
    }
    hydrated["perspectives"] = [
        Perspective.model_validate(item) for item in state.get("perspectives") or []
    ]
    root = state.get("outline_root")
    hydrated["outline_root"] = OutlineNode.model_validate(root) if root else None
    hydrated["outline_nodes"] = [
        OutlineNode.model_validate(item) for item in state.get("outline_nodes") or []
    ]
    memory = state.get("research_memory")
    hydrated["research_memory"] = (
        ResearchMemory.model_validate(memory) if memory else None
    )
    requested = state.get("requested_outline")
    hydrated["requested_outline"] = (
        [OutlineNodeInput.model_validate(item) for item in requested]
        if requested
        else None
    )
    hydrated["section_drafts"] = {
        str(key): SectionDraft.model_validate(value)
        for key, value in (state.get("section_drafts") or {}).items()
    }
    hydrated["verification_reports"] = {
        str(key): VerificationReport.model_validate(value)
        for key, value in (state.get("verification_reports") or {}).items()
    }
    return hydrated


__all__ = [
    "create_initial_state",
    "route_after_verification",
    "route_after_hitl",
]
