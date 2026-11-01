"""The frozen routing rules of the writing harness.

This is the original Python implementation of ``workflow.edges``, kept here so
that the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

Nothing in this module may be rewritten to follow a Kotlin change. The harness
measures the Kotlin tier against *this* rule; a witness that was rewritten to
agree would prove nothing.

The state type comes from the frozen copy beside it, not from the live
``lmeditor.workflow.state``: the live module is now a door to the Kotlin tier,
and a witness that leaned on the door it is supposed to witness would prove
nothing.
"""

from __future__ import annotations

from typing import Literal

from lmeditor._reference.state import HarnessState
from lmeditor.core.schemas import SectionStatus

MAX_SECTION_RETRIES = 3


def route_after_verification(
    state: HarnessState,
) -> Literal["all_verified", "needs_refinement", "hitl_required"]:
    """Evaluate whether sections passed verification or need redrafting / human intervention.

    ``max_retries`` is the caller-supplied budget seeded by ``run_workflow``
    (AUDIT finding 12). The constant is only the fallback for a state that
    predates the key -- ``state`` is a ``total=False`` TypedDict, so a
    checkpoint written before the option existed simply has no such key.
    """
    section_drafts = state.get("section_drafts", {})
    retry_counts = state.get("retry_counts", {})
    retry_budget = state.get("max_retries", MAX_SECTION_RETRIES)

    if not section_drafts:
        return "all_verified"

    all_verified = True
    has_exhausted_retries = False

    for sec_id, draft in section_drafts.items():
        if not draft.is_verified and draft.status != SectionStatus.VERIFIED:
            all_verified = False
            retries = retry_counts.get(sec_id, 0)
            if retries >= retry_budget:
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
