"""LangGraph conditional routing logic and decision gates for LMeditor.

Where a run goes after the verification step, and where it goes after the
human gate, are rules, and the rules are the Kotlin tier's: they are
implemented once, in ``lmeditor.workflow`` on the Kotlin side, and served
across the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.edges``, frozen, so the parity
harness can still ask both sides the same question.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import LMeditorError, ValidationError, WorkflowError
from lmeditor.tier import workflow as tier_workflow
from lmeditor.workflow.state import HarnessState

#: The number of revision cycles a section is bought before the run is bounced
#: to the human gate.
#:
#: The same number as the Kotlin tier's ``Routing.MAX_SECTION_RETRIES``. It is
#: written out rather than read across the seam because a constant that could
#: not be read without a running JVM would be useless to a module that is
#: imported at startup. The two copies are pinned separately -- the Kotlin side
#: by its ``RoutingTest``, and this side by
#: ``tests/test_workflow_options.py`` -- and nothing compares them across the
#: seam: the parity harness's ``workflow routing`` check compares the routes
#: the two tiers take, not this number. So a change to one copy alone would
#: not be caught.
MAX_SECTION_RETRIES = 3

#: The routes the verification step may hand the graph.
_VERIFICATION_ROUTES = ("all_verified", "needs_refinement", "hitl_required")

#: The routes the human gate may hand the graph.
_HITL_ROUTES = ("proceed_to_macro", "terminate")


def _require_state(state: Any) -> None:
    """Refuse anything that is not the state the router reads."""
    if not isinstance(state, dict):
        raise ValidationError(
            f"Expected a state mapping, got {type(state).__name__}",
            field="state",
        )


def route_after_verification(
    state: HarnessState,
) -> Literal["all_verified", "needs_refinement", "hitl_required"]:
    """Evaluate whether sections passed verification or need redrafting / human intervention.

    The decision is the Kotlin tier's: the sections, their retry counts, the
    retry budget and the operator's decision cross the seam, and the tier
    answers with the route.

    ``max_retries`` is the caller-supplied budget seeded by ``run_workflow``
    (AUDIT finding 12). The constant is only the fallback for a state that
    predates the key -- ``state`` is a ``total=False`` TypedDict, so a
    checkpoint written before the option existed simply has no such key. The
    fallback itself is the tier's now, and is the same number as
    :data:`MAX_SECTION_RETRIES`.

    Raises:
        ValidationError: ``state`` was not a state mapping, or the tier
            answered with a route the graph does not know.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    _require_state(state)
    try:
        route = tier_workflow.route_after_verification(cast(dict[str, Any], state))
    except BridgeUnavailableError:
        # The tier is not there. That is an infrastructure failure, not a bad
        # input, and it must not be dressed up as one: the caller needs to
        # know that the rule could not be answered at all.
        raise
    except LMeditorError:
        # The tier's own refusal is already the Python counterpart of the
        # Kotlin error, carrying its field and its value.
        raise
    except Exception as exc:  # pragma: no cover - the tier's own failures
        raise WorkflowError(
            f"the Kotlin tier could not route the verification step: {exc}"
        ) from exc

    if route not in _VERIFICATION_ROUTES:
        raise ValidationError(
            f"the Kotlin tier answered with an unknown verification route '{route}'",
            field="verification_route",
            value=route,
        )
    return cast(Literal["all_verified", "needs_refinement", "hitl_required"], route)


def route_after_hitl(
    state: HarnessState,
) -> Literal["proceed_to_macro", "terminate"]:
    """Route based on the human operator's approval.

    The operator's decision is the only thing the router reads, so it is the
    only thing that crosses the seam.

    Raises:
        ValidationError: ``state`` was not a state mapping, or the tier
            answered with a route the graph does not know.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    _require_state(state)
    try:
        route = tier_workflow.route_after_hitl(cast(dict[str, Any], state))
    except BridgeUnavailableError:
        raise
    except LMeditorError:
        raise
    except Exception as exc:  # pragma: no cover - the tier's own failures
        raise WorkflowError(
            f"the Kotlin tier could not route the human gate: {exc}"
        ) from exc

    if route not in _HITL_ROUTES:
        raise ValidationError(
            f"the Kotlin tier answered with an unknown human-gate route '{route}'",
            field="hitl_route",
            value=route,
        )
    return cast(Literal["proceed_to_macro", "terminate"], route)


__all__ = [
    "MAX_SECTION_RETRIES",
    "route_after_hitl",
    "route_after_verification",
]
