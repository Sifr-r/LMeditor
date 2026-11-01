"""The wire contract: the two tiers, and the HTTP surface they publish.

The Flutter desktop client was removed on 2026-10-25. What it used to guard is
now guarded between the two tiers that remain. The Kotlin tier owns the wire
vocabulary, the Python tier publishes it, and any consumer of the HTTP surface
-- whatever it turns out to be -- is only ever as correct as the agreement
between those two.

These tests read the Kotlin tier's own source and check it against the Python
tier's declared contract, so the two cannot drift apart silently again.

What is checked here is a *contract*, not a consumer: the vocabulary the
server publishes, the shape of the envelope it publishes it in, the outline the
operator may steer a run with, and the routes that carry them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lmeditor.core.schemas import OutlineNodeInput, WorkflowEvent
from lmeditor.core.types import EventType
from lmeditor.server.app import create_app

#: The root of the repository, one level above the backend package.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The Kotlin tier's core module: the enums and the schema data classes.
KOTLIN_CORE = REPO_ROOT / "kotlin" / "core" / "src" / "main" / "kotlin" / "lmeditor" / "core"

#: The file that declares the Kotlin tier's wire vocabulary.
ENUM_SOURCE = KOTLIN_CORE / "Enums.kt"

#: The file that declares the Kotlin tier's schema data classes.
SCHEMA_SOURCE = KOTLIN_CORE / "Schemas.kt"

#: The events a consumer of the HTTP surface needs in order to show anything.
#:
#: Each one names the thing it carries, not the panel that used to draw it.
CONTENT_EVENTS: frozenset[str] = frozenset(
    {
        "chunks",
        "perspectives",
        "research_notes",
        "outline",
        "claim_verified",
        "citation_created",
        "stream_metrics",
        "scorecard_update",
    }
)

#: The Kotlin property names, and the Python field names they correspond to.
#: Kotlin names its properties in camelCase; the wire calls them snake_case.
_KOTLIN_TO_PYTHON_FIELD: dict[str, str] = {
    "eventType": "event_type",
    "docId": "doc_id",
}


def _require(path: Path) -> str:
    """Read a Kotlin source file, failing the test when it is not there.

    The Kotlin tier is not optional. A missing file is a broken repository, not
    an absent comparison, so this fails rather than skips.
    """
    if not path.exists():  # pragma: no cover - a broken checkout
        pytest.fail(f"the Kotlin tier's source is missing at {path}")
    return path.read_text(encoding="utf-8")


def _kotlin_enum_wire_values(source: str, enum_name: str) -> set[str]:
    """The wire strings of a Kotlin enum that carries one.

    The declaration looks like::

        enum class EventType(override val wire: String) : WireEnum {
            NODE_START("node_start"),
            ...
        }
    """
    match = re.search(
        rf"enum class {re.escape(enum_name)}\([^)]*\)[^{{]*\{{(.*?)\n\}}",
        source,
        re.DOTALL,
    )
    assert match is not None, f"could not find the Kotlin enum {enum_name}"
    return set(re.findall(r'\(\s*"([a-z_]+)"\s*\)', match.group(1)))


def _kotlin_data_class_properties(source: str, class_name: str) -> set[str]:
    """The property names of a Kotlin data class.

    The declaration looks like::

        data class OutlineNodeInput private constructor(
            val title: String,
            ...
        ) {
    """
    match = re.search(
        rf"data class {re.escape(class_name)}\b[^{{]*?\((.*?)\n\s*\)\s*\{{",
        source,
        re.DOTALL,
    )
    assert match is not None, f"could not find the Kotlin data class {class_name}"
    return set(re.findall(r"\bval\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1)))


def _python_field_names(fields: object) -> set[str]:
    """The field names of a Pydantic model."""
    return set(fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The event vocabulary
# ---------------------------------------------------------------------------


def test_the_two_tiers_declare_the_same_event_vocabulary() -> None:
    """The Kotlin tier and the Python tier must publish the same event names.

    The Kotlin tier owns the vocabulary and the Python tier publishes it. A
    name that exists on one side only is an event a consumer cannot recognise,
    or a frame nobody ever publishes -- and both look like a working system
    until something is missing.
    """
    kotlin_events = _kotlin_enum_wire_values(_require(ENUM_SOURCE), "EventType")
    python_events = {event.value for event in EventType}

    assert kotlin_events == python_events, (
        "the two tiers declare different event vocabularies: "
        f"kotlin-only={sorted(kotlin_events - python_events)} "
        f"python-only={sorted(python_events - kotlin_events)}"
    )


def test_the_content_events_a_consumer_needs_are_published() -> None:
    """Every event that carries content must be in the declared vocabulary."""
    published = {event.value for event in EventType}
    missing = sorted(CONTENT_EVENTS - published)
    assert not missing, (
        f"the vocabulary declares no {missing} event, so whatever consumes the "
        "stream receives nothing for it"
    )


def test_the_event_vocabulary_is_not_empty() -> None:
    """An empty vocabulary means the parse found nothing, not that there is none."""
    kotlin_events = _kotlin_enum_wire_values(_require(ENUM_SOURCE), "EventType")
    assert len(kotlin_events) >= len(CONTENT_EVENTS), (
        "the Kotlin tier's event vocabulary parsed as "
        f"{sorted(kotlin_events)}, which cannot be the whole vocabulary"
    )


# ---------------------------------------------------------------------------
# The event envelope
# ---------------------------------------------------------------------------


def test_the_two_tiers_declare_the_same_event_envelope() -> None:
    """The SSE frame must have the same fields on both sides of the seam."""
    kotlin_fields = _kotlin_data_class_properties(
        _require(SCHEMA_SOURCE), "WorkflowEvent"
    )
    python_fields = _python_field_names(WorkflowEvent.model_fields)

    mapped = {_KOTLIN_TO_PYTHON_FIELD.get(name, name) for name in kotlin_fields}
    assert mapped == python_fields, (
        "the two tiers declare different event envelopes: "
        f"kotlin-only={sorted(mapped - python_fields)} "
        f"python-only={sorted(python_fields - mapped)}"
    )


def test_the_envelope_forbids_fields_it_does_not_understand() -> None:
    """An extra field must be refused, not ignored.

    A frame that carries a field nobody reads is a frame whose meaning is
    ambiguous, and an ambiguous frame is how two halves of a system drift.
    """
    assert WorkflowEvent.model_config.get("extra") == "forbid", (
        "the event envelope accepts unknown fields; a frame that means two "
        "things is worse than a frame that is refused"
    )


# ---------------------------------------------------------------------------
# The operator's steer
# ---------------------------------------------------------------------------


def test_the_two_tiers_declare_the_same_outline_input() -> None:
    """The outline a caller sends must have the same fields on both sides.

    The model forbids extra fields, so a field one side knows and the other
    does not is a 422 at run time rather than a compile error.
    """
    kotlin_fields = _kotlin_data_class_properties(
        _require(SCHEMA_SOURCE), "OutlineNodeInput"
    )
    python_fields = _python_field_names(OutlineNodeInput.model_fields)

    assert kotlin_fields == python_fields, (
        "the two tiers declare different outline inputs: "
        f"kotlin-only={sorted(kotlin_fields - python_fields)} "
        f"python-only={sorted(python_fields - kotlin_fields)}"
    )


def test_the_outline_forbids_fields_it_does_not_understand() -> None:
    """A section id or a chunk assignment must not be postable by accident."""
    assert OutlineNodeInput.model_config.get("extra") == "forbid", (
        "the outline input accepts unknown fields; a caller could smuggle a "
        "chunk assignment into a document the corpus is supposed to decide"
    )


def test_the_generate_request_accepts_the_operator_outline() -> None:
    """The generate request must carry an outline at all."""
    from lmeditor.server.routes import GenerateDocumentRequest

    assert "outline" in GenerateDocumentRequest.model_fields, (
        "the generate request does not accept an outline, so the operator's "
        "steer could never reach the run"
    )


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


def _backend_paths() -> set[str]:
    """Every path the backend serves, read from its OpenAPI schema.

    ``app.routes`` is not used: recent FastAPI versions keep the routes of an
    included router behind a wrapper object rather than copying them onto the
    application, so the schema is the reliable source.
    """
    schema = create_app().openapi()
    return set(schema.get("paths", {}))


def test_the_document_and_export_routes_exist() -> None:
    """A generated manuscript must be retrievable and exportable."""
    paths = _backend_paths()
    assert "/api/v1/documents/{doc_id}" in paths
    assert "/api/v1/documents/{doc_id}/export.docx" in paths
    assert "/api/v1/sources" in paths


def test_the_stream_and_gate_routes_exist() -> None:
    """A run must be followable, and its gate answerable."""
    paths = _backend_paths()
    assert "/api/v1/stream/{doc_id}" in paths
    assert "/api/v1/workflow/{doc_id}/approve" in paths


def test_every_route_is_under_the_versioned_prefix() -> None:
    """A route outside the versioned prefix is a route nobody can migrate."""
    outside = sorted(
        path for path in _backend_paths() if not path.startswith("/api/v1")
    )
    assert not outside, f"routes outside the versioned prefix: {outside}"
