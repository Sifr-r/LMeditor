"""The workflow doors must reach the Kotlin tier, and must not fake it.

``lmeditor.workflow.state`` and ``lmeditor.workflow.edges`` are *doors*: they
keep the Python API the backend calls and answer with the Kotlin tier's
answers. These tests prove that is what actually happens -- that the answers
come from the tier, that the tier agrees with the frozen Python witness, and
that a tier that is not there fails loudly instead of quietly answering from a
second, Python copy of the rule.
"""

from __future__ import annotations

from typing import Any

import pytest

from lmeditor._reference import edges as py_edges
from lmeditor._reference import state as py_state
from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import SectionDraft, SectionStatus
from lmeditor.tier import workflow as tier_workflow
from lmeditor.workflow.edges import route_after_hitl, route_after_verification
from lmeditor.workflow.state import create_initial_harness_state

pytestmark = pytest.mark.usefixtures("kotlin_tier")


def _verified(section_id: str = "sec_1") -> SectionDraft:
    """A section that has been verified."""
    return SectionDraft(
        section_id=section_id,
        title="Section",
        content="A verified section.",
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )


def _unverified(section_id: str = "sec_1") -> SectionDraft:
    """A section that has not been verified yet."""
    return SectionDraft(
        section_id=section_id,
        title="Section",
        content="A drafted section.",
        status=SectionStatus.FLAGGED,
    )


# ---------------------------------------------------------------------------
# The doors call the tier
# ---------------------------------------------------------------------------


def test_the_state_door_asks_the_tier(monkeypatch: Any) -> None:
    """The fresh state is built by the tier, not assembled by this module."""
    calls: list[dict[str, Any]] = []
    original = tier_workflow.create_initial_state

    def _spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(tier_workflow, "create_initial_state", _spy)

    create_initial_harness_state(doc_id="doc_1", topic="Topic")

    assert len(calls) == 1
    assert calls[0]["doc_id"] == "doc_1"
    assert calls[0]["topic"] == "Topic"


def test_the_routing_doors_ask_the_tier(monkeypatch: Any) -> None:
    """Both routers reach the tier, and reach it for the right question."""
    calls: list[str] = []
    original_verify = tier_workflow.route_after_verification
    original_hitl = tier_workflow.route_after_hitl

    def _verify(state: dict[str, Any]) -> str:
        calls.append("after_verification")
        return original_verify(state)

    def _hitl(state: dict[str, Any]) -> str:
        calls.append("after_hitl")
        return original_hitl(state)

    monkeypatch.setattr(tier_workflow, "route_after_verification", _verify)
    monkeypatch.setattr(tier_workflow, "route_after_hitl", _hitl)

    route_after_verification({"section_drafts": {}, "retry_counts": {}})
    route_after_hitl({"hitl_approved": True})

    assert calls == ["after_verification", "after_hitl"]


# ---------------------------------------------------------------------------
# The answers agree with the tier, and with the frozen witness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"section_drafts": {"sec_1": _verified()}, "retry_counts": {"sec_1": 0}},
        {"section_drafts": {"sec_1": _unverified()}, "retry_counts": {"sec_1": 1}},
        {"section_drafts": {"sec_1": _unverified()}, "retry_counts": {"sec_1": 3}},
        {
            "section_drafts": {"sec_1": _unverified()},
            "retry_counts": {"sec_1": 1},
            "max_retries": 1,
        },
    ],
)
def test_the_verification_route_agrees_with_the_tier_and_the_witness(
    state: dict[str, Any],
) -> None:
    """The door, the tier and the frozen witness answer alike."""
    door = route_after_verification(state)

    assert door == tier_workflow.route_after_verification(state)
    assert door == py_edges.route_after_verification(state)


@pytest.mark.parametrize("approved", [True, False])
def test_the_gate_route_agrees_with_the_tier_and_the_witness(approved: bool) -> None:
    """The door, the tier and the frozen witness answer alike."""
    state: dict[str, Any] = {"hitl_approved": approved}
    door = route_after_hitl(state)

    assert door == tier_workflow.route_after_hitl(state)
    assert door == py_edges.route_after_hitl(state)


def test_the_fresh_state_has_the_kotlin_shape() -> None:
    """The door's fresh state is the tier's fresh state, key for key."""
    door = create_initial_harness_state(doc_id="doc_1", topic="Topic")
    tier = tier_workflow.create_initial_state(doc_id="doc_1", topic="Topic")

    assert dict(door) == dict(tier)
    assert door["status"] == "initialized"
    assert door["target_audience"] == "Technical & Academic Readers"
    assert door["max_retries"] == 3
    assert door["threshold_f_beta"] == 0.85
    assert door["sources"] == []
    assert door["section_drafts"] == {}
    assert door["errors"] == []


def test_the_fresh_state_agrees_with_the_frozen_witness() -> None:
    """Every value the caller sees is the one the original always returned."""
    door = create_initial_harness_state(doc_id="doc_1", topic="Topic")
    witness = py_state.create_initial_harness_state(doc_id="doc_1", topic="Topic")

    for key, value in witness.items():
        assert door[key] == value, key


def test_the_defaults_are_left_to_the_tier(monkeypatch: Any) -> None:
    """The door says "not asked for"; the tier supplies the default."""
    calls: list[dict[str, Any]] = []
    original = tier_workflow.create_initial_state

    def _spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(tier_workflow, "create_initial_state", _spy)

    state = create_initial_harness_state(doc_id="doc_1", topic="Topic")

    assert calls[0]["target_audience"] is None
    assert calls[0]["max_retries"] is None
    assert calls[0]["threshold_f_beta"] is None
    assert state["target_audience"] == "Technical & Academic Readers"
    assert state["max_retries"] == 3
    assert state["threshold_f_beta"] == 0.85


# ---------------------------------------------------------------------------
# An unreachable tier fails loudly
# ---------------------------------------------------------------------------


def test_an_unreachable_tier_is_not_swallowed_by_the_state_door(
    monkeypatch: Any,
) -> None:
    """There is no Python fallback: a missing tier is an error, not an answer."""

    def _unavailable(**_kwargs: Any) -> dict[str, Any]:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_workflow, "create_initial_state", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        create_initial_harness_state(doc_id="doc_1", topic="Topic")


def test_an_unreachable_tier_is_not_swallowed_by_the_routers(monkeypatch: Any) -> None:
    """The routers fail the same way the state door does."""

    def _unavailable(_state: dict[str, Any]) -> str:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_workflow, "route_after_verification", _unavailable)
    monkeypatch.setattr(tier_workflow, "route_after_hitl", _unavailable)

    with pytest.raises(BridgeUnavailableError):
        route_after_verification({"section_drafts": {}})
    with pytest.raises(BridgeUnavailableError):
        route_after_hitl({"hitl_approved": True})


# ---------------------------------------------------------------------------
# The boundary refuses bad input with a typed error
# ---------------------------------------------------------------------------


def test_a_bad_argument_is_refused_with_a_typed_error() -> None:
    """The seam would coerce it; the door refuses it and says why."""
    with pytest.raises(ValidationError) as caught:
        create_initial_harness_state(doc_id=42, topic="Topic")  # type: ignore[arg-type]

    assert caught.value.field == "doc_id"
