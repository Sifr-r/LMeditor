"""LangGraph conditional routing logic and decision gates for LMeditor."""

from __future__ import annotations

from typing import Literal

from lmeditor.core.schemas import SectionStatus
from lmeditor.workflow.state import HarnessState

MAX_SECTION_RETRIES = 3


def route_after_verification(
    state: HarnessState,
) -> Literal["all_verified", "needs_refinement", "hitl_required"]:
    """Evaluate whether sections passed verification or need redrafting / human intervention."""
    section_drafts = state.get("section_drafts", {})
    retry_counts = state.get("retry_counts", {})

    if not section_drafts:
        return "all_verified"

    all_verified = True
    has_exhausted_retries = False

    for sec_id, draft in section_drafts.items():
        if not draft.is_verified and draft.status != SectionStatus.VERIFIED:
            all_verified = False
            retries = retry_counts.get(sec_id, 0)
            if retries >= MAX_SECTION_RETRIES:
                has_exhausted_retries = True

    if all_verified:
        return "all_verified"

    if has_exhausted_retries:
        return "hitl_required"

    return "needs_refinement"


def route_after_hitl(
    state: HarnessState,
) -> Literal["proceed_to_macro", "terminate"]:
    """Route based on human operator approval in Flutter studio."""
    if state.get("hitl_approved", False):
        return "proceed_to_macro"
    return "terminate"


__all__ = [
    "MAX_SECTION_RETRIES",
    "route_after_hitl",
    "route_after_verification",
]
