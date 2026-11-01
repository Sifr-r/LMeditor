"""Tests for saving the operator's own text into a document.

These tests exist to prevent a specific defect: a word processor that
discards what the operator typed. The operator's text has to reach the
server, be stored, and be reported on honestly — measured again, because the
verdict that described the draft does not describe the edited text.

Everything here runs offline. The document under test has no retrieval index,
so the save path takes its "no sources reachable" branch: the text is stored
and the section is reported as unmeasured, which is the honest answer when
there is nothing to measure against.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from lmeditor.core.schemas import CitationLineage, SectionDraft
from lmeditor.core.sections import merge_operator_text
from lmeditor.core.types import SectionStatus
from lmeditor.server.app import create_app
from lmeditor.server.session import session_manager
from lmeditor.workflow.nodes import hitl_checkpoint_node
from lmeditor.workflow.state import HarnessState

#: A lineage that says where a passage came from. The pipeline establishes
#: these; an operator's edit must not erase them.
_LINEAGE = CitationLineage(
    lineage_id="lin_1",
    claim_id="claim_1",
    chunk_id="a" * 64,
    span_start=0,
    span_end=18,
    exact_quote="The pipeline drafted this.",
)


def _draft() -> SectionDraft:
    """A section as the pipeline drafted it."""
    return SectionDraft(
        section_id="sec_1",
        title="The drafted heading",
        content="The pipeline drafted this.",
        order=0,
        citations=[_LINEAGE],
        cited_chunk_ids=["a" * 64],
        is_verified=True,
        status=SectionStatus.VERIFIED,
    )


def _client() -> httpx.AsyncClient:
    """An HTTP client bound to the application under test."""
    transport = httpx.ASGITransport(app=create_app())
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _run(coro: Any) -> Any:
    """Run one coroutine to completion."""

    async def runner() -> Any:
        async with _client() as client:
            return await coro(client)

    return asyncio.run(runner())


async def _document(doc_id: str, sections: list[dict[str, Any]]) -> None:
    """Register a document that already holds a manuscript."""
    session = await session_manager.create_session(doc_id=doc_id, topic="Saving")
    await session.complete(sections=sections)


# ---------------------------------------------------------------------------
# The domain rule
# ---------------------------------------------------------------------------


def test_an_edit_keeps_the_provenance_the_pipeline_established() -> None:
    """The operator reworded the text; they did not rewrite where it came from."""
    drafted = _draft()
    merged = merge_operator_text(drafted, content="The operator's own words.")

    assert merged.content == "The operator's own words."
    assert merged.citations == drafted.citations
    assert merged.cited_chunk_ids == drafted.cited_chunk_ids


def test_an_edit_is_handed_back_unmeasured() -> None:
    """The verdict that described the draft does not describe the edit."""
    drafted = _draft()
    merged = merge_operator_text(drafted, content="Something else entirely.")

    assert merged.is_verified is False
    assert merged.status == SectionStatus.PENDING


def test_an_edit_keeps_the_heading_and_the_position_unless_they_are_given() -> None:
    """A caller that says nothing about the heading or the order changes neither."""
    drafted = _draft()
    merged = merge_operator_text(drafted, content="New text.")

    assert merged.title == drafted.title
    assert merged.order == drafted.order

    moved = merge_operator_text(drafted, content="New text.", title="New heading", order=3)
    assert moved.title == "New heading"
    assert moved.order == 3


def test_the_original_section_is_not_mutated() -> None:
    """An edit is a new section; the drafted one is still there to compare against."""
    drafted = _draft()
    merge_operator_text(drafted, content="Replaced.")

    assert drafted.content == "The pipeline drafted this."
    assert drafted.is_verified is True


# ---------------------------------------------------------------------------
# The save route
# ---------------------------------------------------------------------------


def test_saving_a_section_stores_the_operator_text() -> None:
    """The operator's words are stored, not just returned."""
    doc_id = "doc_save_text"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await _document(doc_id, [_draft().model_dump(mode="json")])
        return await client.put(
            f"/api/v1/documents/{doc_id}/sections/sec_1",
            json={"content": "The operator's own words."},
        )

    response = _run(run)
    assert response.status_code == 200
    body = response.json()
    assert body["section_id"] == "sec_1"
    assert body["content"] == "The operator's own words."
    # Nothing was there to measure against, so nothing is claimed.
    assert body["scorecard"] is None
    assert body["is_verified"] is False


def test_the_saved_section_keeps_its_citations() -> None:
    """Saving a section must not strip the audit trail from it."""
    doc_id = "doc_save_citations"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await _document(doc_id, [_draft().model_dump(mode="json")])
        await client.put(
            f"/api/v1/documents/{doc_id}/sections/sec_1",
            json={"content": "The operator's own words."},
        )
        return await client.get(f"/api/v1/documents/{doc_id}")

    response = _run(run)
    assert response.status_code == 200
    sections = response.json()["sections"]
    assert len(sections) == 1
    assert sections[0]["content"] == "The operator's own words."
    assert sections[0]["citations"][0]["chunk_id"] == "a" * 64
    assert sections[0]["cited_chunk_ids"] == ["a" * 64]


def test_the_export_carries_the_operator_text() -> None:
    """The whole point: what the operator wrote is what the file contains."""
    doc_id = "doc_save_export"

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await _document(doc_id, [_draft().model_dump(mode="json")])
        await client.put(
            f"/api/v1/documents/{doc_id}/sections/sec_1",
            json={"content": "A sentence the operator wrote themselves."},
        )
        return await client.get(f"/api/v1/documents/{doc_id}/export.docx")

    response = _run(run)
    assert response.status_code == 200
    package = zipfile.ZipFile(io.BytesIO(response.content))
    document_xml = package.read("word/document.xml").decode("utf-8")
    assert "A sentence the operator wrote themselves." in document_xml
    assert "The pipeline drafted this." not in document_xml


def test_saving_an_unknown_section_is_a_404() -> None:
    """A section the document does not hold cannot be saved."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await _document("doc_save_missing_section", [_draft().model_dump(mode="json")])
        return await client.put(
            "/api/v1/documents/doc_save_missing_section/sections/sec_nope",
            json={"content": "Text."},
        )

    response = _run(run)
    assert response.status_code == 404


def test_saving_into_an_unknown_document_is_a_404() -> None:
    """A document the server does not hold cannot be saved into."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        return await client.put(
            "/api/v1/documents/doc_save_nope/sections/sec_1",
            json={"content": "Text."},
        )

    response = _run(run)
    assert response.status_code == 404


def test_saving_into_a_document_with_no_manuscript_is_a_409() -> None:
    """There is nothing to save into until the document has a manuscript."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        session = await session_manager.create_session(
            doc_id="doc_save_empty", topic="Saving"
        )
        await session.complete(sections=[])
        return await client.put(
            "/api/v1/documents/doc_save_empty/sections/sec_1",
            json={"content": "Text."},
        )

    response = _run(run)
    assert response.status_code == 409


def test_a_save_that_names_an_unknown_field_is_refused() -> None:
    """The server's model forbids fields it does not understand."""

    async def run(client: httpx.AsyncClient) -> httpx.Response:
        await _document("doc_save_extra", [_draft().model_dump(mode="json")])
        return await client.put(
            "/api/v1/documents/doc_save_extra/sections/sec_1",
            json={"content": "Text.", "citations": []},
        )

    response = _run(run)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# The review gate
# ---------------------------------------------------------------------------


def _gate_graph() -> Any:
    """A graph that suspends at the review gate and records what came out."""

    def _record(state: HarnessState) -> dict[str, Any]:
        return {"status": "completed"}

    workflow = StateGraph(HarnessState)
    workflow.set_entry_point("hitl_checkpoint")
    workflow.add_node("hitl_checkpoint", hitl_checkpoint_node)
    workflow.add_node("macro_completion", _record)
    workflow.add_conditional_edges(
        "hitl_checkpoint",
        lambda state: "proceed_to_macro" if state.get("hitl_approved") else "terminate",
        {"proceed_to_macro": "macro_completion", "terminate": END},
    )
    workflow.add_edge("macro_completion", END)
    return workflow.compile(checkpointer=MemorySaver())


def test_the_review_gate_applies_the_operator_edited_sections() -> None:
    """Approving a document must not throw away what the reviewer wrote.

    A gate that discards the reviewer's edits makes editing pointless: the
    operator would fix a paragraph, approve, and find the draft back.
    """
    from langgraph.types import Command

    graph = _gate_graph()
    config = {"configurable": {"thread_id": "gate_save"}}

    async def run() -> dict[str, Any]:
        await graph.ainvoke(
            {"section_drafts": {"sec_1": _draft()}, "source_chunks": {}},
            config=config,
        )
        # The gate is suspended; it is resumed with the operator's decision.
        await graph.ainvoke(
            Command(
                resume={
                    "approved": True,
                    "modified_sections": [
                        {
                            "section_id": "sec_1",
                            "title": "The operator's heading",
                            "content": "The operator's own words.",
                            "order": 0,
                        }
                    ],
                }
            ),
            config=config,
        )
        state = await graph.aget_state(config)
        return dict(state.values)

    final = asyncio.run(run())
    drafts = final.get("section_drafts", {})
    assert "sec_1" in drafts, "the gate dropped the edited section"
    assert drafts["sec_1"].content == "The operator's own words."
    assert drafts["sec_1"].title == "The operator's heading"
    # The provenance the pipeline established is still attached to it.
    assert drafts["sec_1"].citations == [_LINEAGE]
    # But the claims are gone: they were extracted from the text that is no
    # longer there, and the measurement extracts them from the operator's
    # words instead.
    assert drafts["sec_1"].claims == []
    assert drafts["sec_1"].is_verified is False


def test_the_review_gate_ignores_an_edit_to_a_section_that_does_not_exist() -> None:
    """An edit to a section the document never had cannot inject content."""
    from langgraph.types import Command

    graph = _gate_graph()
    config = {"configurable": {"thread_id": "gate_save_unknown"}}

    async def run() -> dict[str, Any]:
        await graph.ainvoke(
            {"section_drafts": {"sec_1": _draft()}, "source_chunks": {}},
            config=config,
        )
        await graph.ainvoke(
            Command(
                resume={
                    "approved": True,
                    "modified_sections": [
                        {
                            "section_id": "sec_that_does_not_exist",
                            "title": "Injected",
                            "content": "Content the document never had.",
                            "order": 0,
                        }
                    ],
                }
            ),
            config=config,
        )
        state = await graph.aget_state(config)
        return dict(state.values)

    final = asyncio.run(run())
    drafts = final.get("section_drafts", {})
    assert "sec_that_does_not_exist" not in drafts
    assert drafts["sec_1"].content == "The pipeline drafted this."
