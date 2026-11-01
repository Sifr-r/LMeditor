"""End-to-end proof that the writing harness works when a model is configured.

These tests exist because the harness used to be able to run to completion
without ever calling a language model: the drafting node caught the "no LM is
loaded" error and substituted a template paragraph built from the first 180
characters of a source chunk. The result looked like a drafted manuscript and
was verified as if it were one.

The suite therefore pins three things:

* with a model configured, a section is drafted, cites a real chunk id, and
  verifies against that chunk;
* with no model configured, the section is left **empty** and says why -- no
  substitute prose is produced;
* the terminal event carries a real scorecard, and the document can be
  fetched and exported.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import dspy
import pytest
from fastapi.testclient import TestClient

from lmeditor.core.schemas import OutlineNode, SectionDraft, Source, SourceChunk
from lmeditor.core.types import EntailmentStatus, EventType, SourceKind
from lmeditor.server.app import create_app
from lmeditor.server.sources import StoredSource
from lmeditor.workflow.nodes import NO_LM_CRITIQUE, draft_sections_node

from .stub_lm import StubLM

TOPIC = "Municipal waste collection trends"

SOURCE_TEXT = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
    "The decline was driven by a citywide composting programme introduced in 2020."
)


def _chunk() -> SourceChunk:
    return SourceChunk.create(
        document_id="doc_e2e",
        text=SOURCE_TEXT,
        char_start=0,
        char_end=len(SOURCE_TEXT),
        byte_start=0,
        byte_end=len(SOURCE_TEXT),
        metadata={"document_title": "Municipal Audit"},
    )


class DeterministicNLI:
    """An NLI evaluator that is exact rather than statistical.

    A hypothesis whose content words all appear in the premise is entailed;
    anything else is ungrounded. Because the stub drafter quotes its source
    passage verbatim, a correct draft is entailed and a fabricated one is not.
    """

    evaluator_name = "deterministic-test-evaluator"

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
        premise_words = {w for w in premise.lower().split() if len(w) > 3}
        hypothesis_words = {w for w in hypothesis.lower().split() if len(w) > 3}
        if not hypothesis_words:
            return (EntailmentStatus.UNGROUNDED, 0.0)
        overlap = len(premise_words & hypothesis_words) / len(hypothesis_words)
        if overlap >= 0.8:
            return (EntailmentStatus.ENTAILED, round(overlap, 4))
        return (EntailmentStatus.UNGROUNDED, round(overlap, 4))


@pytest.fixture(autouse=True)
def _restore_dspy() -> Any:
    """Restore the global DSPy configuration after each test."""
    previous = dspy.settings.lm
    yield
    dspy.configure(lm=previous)


@pytest.fixture
def stub_model() -> StubLM:
    model = StubLM()
    dspy.configure(lm=model)
    return model


@pytest.fixture
def deterministic_nli(monkeypatch: pytest.MonkeyPatch) -> DeterministicNLI:
    evaluator = DeterministicNLI()
    monkeypatch.setattr(
        "lmeditor.verification.citation_checker.default_nli_evaluator", evaluator
    )
    return evaluator


# ----------------------------------------------------------------------
# Drafting
# ----------------------------------------------------------------------


def _draft_state() -> dict[str, Any]:
    chunk = _chunk()
    node = OutlineNode(
        node_id="sec_1",
        title="Collection Rates",
        description="How collection rates changed",
        order=0,
        assigned_chunk_ids=[chunk.chunk_id],
    )
    return {
        "doc_id": "doc_e2e",
        "topic": TOPIC,
        "outline_nodes": [node],
        "source_chunks": {chunk.chunk_id: chunk},
        "perspectives": [],
        "section_drafts": {},
        "critiques": {},
        "errors": [],
    }


def test_drafting_with_a_model_produces_grounded_content(stub_model: StubLM) -> None:
    """A configured model yields a real draft that cites a real chunk."""
    chunk = _chunk()
    result = draft_sections_node(_draft_state())
    draft = result["section_drafts"]["sec_1"]

    assert draft.content.strip(), "a configured model must produce section content"
    assert chunk.chunk_id in draft.content, "the draft must cite the chunk it was given"
    assert chunk.chunk_id in draft.cited_chunk_ids
    assert "According to verified documentation" not in draft.content, (
        "the template fallback must never be produced when a model is available"
    )


def test_drafting_without_a_model_leaves_the_section_empty_and_says_why() -> None:
    """No model means no prose -- never a substitute paragraph."""
    dspy.configure(lm=None)

    result = draft_sections_node(_draft_state())
    draft = result["section_drafts"]["sec_1"]

    assert draft.content == "", "no model means no drafted content"
    assert "According to verified documentation" not in draft.content
    assert NO_LM_CRITIQUE in draft.critique
    assert any("no language model" in e for e in result["errors"])


def test_drafting_records_a_real_failure_instead_of_hiding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that raises leaves an empty section with the reason attached."""
    dspy.configure(lm=StubLM())

    from lmeditor.drafting import modules as drafting_modules

    class Exploding:
        def forward(self, **kwargs: Any) -> Any:
            raise RuntimeError("provider timeout")

    monkeypatch.setattr(drafting_modules, "SectionDrafter", lambda **_: Exploding())
    monkeypatch.setattr("lmeditor.workflow.nodes.SectionDrafter", lambda **_: Exploding())

    result = draft_sections_node(_draft_state())
    draft = result["section_drafts"]["sec_1"]

    assert draft.content == ""
    assert "provider timeout" in draft.critique
    assert any("provider timeout" in e for e in result["errors"])


# ----------------------------------------------------------------------
# A section that was never written
# ----------------------------------------------------------------------


def test_an_unwritten_section_is_never_verified() -> None:
    """An empty section cannot be marked verified, however perfect its metrics.

    The metrics are defined to be perfect when there is nothing to check, so an
    empty section scored ``f_beta = 1.0`` and was marked VERIFIED. That let a
    run in which no section could be drafted report itself as a finished,
    fully grounded document.
    """
    import asyncio

    from lmeditor.core.types import SectionStatus
    from lmeditor.verification.citation_checker import CitationChecker
    from lmeditor.verification.nli_evaluator import HeuristicNLIEvaluator

    section = SectionDraft(
        section_id="sec_empty",
        title="Never drafted",
        content="",
        order=0,
        cited_chunk_ids=[],
        status=SectionStatus.NEEDS_REVISION,
        critique="No language model configured.",
    )

    checker = CitationChecker(evaluator=HeuristicNLIEvaluator(), f_beta_threshold=0.5)
    audited, scorecard = asyncio.run(checker.audit_section(section, chunks_by_id={}))

    assert audited.is_verified is False, "an unwritten section was marked verified"
    assert audited.status is SectionStatus.NEEDS_REVISION
    assert scorecard.f_beta == 0.0, "an unwritten section scored a perfect F_beta"
    assert scorecard.ungrounded_claims >= 1
    # The counts stay consistent with each other.
    assert scorecard.total_claims == (
        scorecard.supported_claims
        + scorecard.ungrounded_claims
        + scorecard.contradicted_claims
        + scorecard.neutral_claims
    )


def test_a_run_whose_sections_cannot_be_drafted_reaches_the_human_gate() -> None:
    """A run that wrote nothing must not report a finished document.

    With no language model configured, every section is empty. The run has to
    suspend at the human-in-the-loop gate, where the operator can see that
    nothing was written -- not complete with an empty manuscript.
    """
    import asyncio

    import dspy

    from lmeditor.core.types import EventType
    from lmeditor.workflow.graph import run_workflow

    dspy.configure(lm=None)

    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def publish_event(self, event: Any) -> None:
            self.events.append(event)

    sink = RecordingSink()
    result = asyncio.run(
        run_workflow(
            doc_id="doc_e2e_no_lm",
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=sink,
        )
    )

    assert result["requires_hitl"] is True, (
        "a run that drafted nothing reported that it did not need a human"
    )
    assert result["status"] == "awaiting_human_review"
    assert result["hitl_reason"], "the gate did not say why it was reached"

    published = [e.event_type for e in sink.events]
    assert EventType.HITL_REQUEST in published, "the gate was never published"
    assert EventType.WORKFLOW_COMPLETE not in published, (
        "the run published a completed document for a manuscript it never wrote"
    )
    assert any("no language model" in e for e in result["errors"])


def test_the_document_scorecard_counts_an_unwritten_section_as_unsupported() -> None:
    """A document missing a section is not 100% grounded."""
    from lmeditor.core.schemas import SectionDraft
    from lmeditor.workflow.graph import build_document_scorecard

    written = SectionDraft(
        section_id="sec_1",
        title="Written",
        content="Waste fell by 30 percent [#chunk_a].",
        order=0,
        cited_chunk_ids=["chunk_a"],
        is_verified=True,
    )
    missing = SectionDraft(
        section_id="sec_2",
        title="Never drafted",
        content="",
        order=1,
        cited_chunk_ids=[],
        is_verified=False,
    )

    card = build_document_scorecard([written, missing])

    assert card["f_beta"] < 1.0, "a document with a missing section scored perfectly"
    assert card["ungrounded_claims"] >= 1
    assert card["total_claims"] == (
        card["supported_claims"]
        + card["ungrounded_claims"]
        + card["contradicted_claims"]
        + card["neutral_claims"]
    )
    assert card["all_passed"] is False
    assert "sec_2" in card["unverified_section_ids"]


# ----------------------------------------------------------------------
# The whole pipeline
# ----------------------------------------------------------------------


def test_full_workflow_verifies_a_grounded_section(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """The graph drafts, verifies and completes with a real scorecard."""
    import asyncio

    from lmeditor.workflow.graph import build_document_scorecard, run_workflow

    chunk = _chunk()
    result = asyncio.run(
        run_workflow(
            doc_id="doc_e2e_graph",
            topic=TOPIC,
            source_chunks=[chunk],
            max_retries=1,
            threshold_f_beta=0.5,
        )
    )

    drafts = result.get("section_drafts") or {}
    assert drafts, "the workflow must produce sections"
    for draft in drafts.values():
        assert draft.content.strip(), "every drafted section must have content"
        assert "According to verified documentation" not in draft.content

    card = build_document_scorecard(list(drafts.values()))
    assert card["total_claims"] > 0, "the audit must have examined real claims"
    assert card["supported_claims"] > 0, "a quoted passage must verify as entailed"
    assert card["f_beta"] > 0.0


def test_the_stream_publishes_the_content_events_the_client_parses(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """The events a consumer of the stream needs must actually be published."""
    import asyncio

    from lmeditor.workflow.graph import run_workflow

    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def publish_event(self, event: Any) -> None:
            self.events.append(event)

    sink = RecordingSink()
    asyncio.run(
        run_workflow(
            doc_id="doc_e2e_events",
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=sink,
        )
    )

    published = {event.event_type for event in sink.events}
    for expected in (
        EventType.CHUNKS,
        EventType.PERSPECTIVES,
        EventType.OUTLINE,
        EventType.CLAIM_VERIFIED,
        EventType.STREAM_METRICS,
    ):
        assert expected in published, f"{expected.value} was never published"

    chunk_event = next(e for e in sink.events if e.event_type is EventType.CHUNKS)
    assert chunk_event.data["chunks"], "the chunks event must carry the chunks"
    assert chunk_event.data["chunks"][0]["chunk_id"] == _chunk().chunk_id

    outline_event = next(e for e in sink.events if e.event_type is EventType.OUTLINE)
    assert outline_event.data["outline"], "the outline event must carry the outline"
    assert "depth" in outline_event.data["outline"][0]
    assert "description" in outline_event.data["outline"][0]

    metrics_event = next(e for e in sink.events if e.event_type is EventType.STREAM_METRICS)
    assert "grounding_score" in metrics_event.data
    assert metrics_event.data["grounding_score"] is not None


def test_the_published_outline_is_a_tree_the_client_can_parse(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """Every published outline node must have an id and a title.

    The outline was published by rendering the outline *container* as though it
    were a node, which produced a single node with an empty id and an empty
    title. The consumer's parser rejects both, so the outline panel never
    received an outline at all -- and the event was published, so nothing
    looked wrong from the outside.
    """
    import asyncio

    from lmeditor.workflow.graph import run_workflow

    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def publish_event(self, event: Any) -> None:
            self.events.append(event)

    sink = RecordingSink()
    asyncio.run(
        run_workflow(
            doc_id="doc_e2e_outline",
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=sink,
        )
    )

    outline_event = next(e for e in sink.events if e.event_type is EventType.OUTLINE)
    outline = outline_event.data["outline"]
    assert outline, "the outline event carried no outline"

    def _check(node: dict[str, Any], depth: int) -> None:
        # These are exactly the two fields the canonical OutlineNode refuses to
        # accept when they are empty.
        assert node["node_id"], "an outline node has no id"
        assert node["title"], "an outline node has no title"
        assert node["depth"] == depth, "the canonical depth is zero-based"
        for child in node.get("children", []):
            _check(child, depth + 1)

    for node in outline:
        _check(node, 0)

    # The tree is not a single empty shell: it has at least one leaf, and the
    # leaves name the material they cover.
    def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
        children = node.get("children", [])
        return [c for child in children for c in _leaves(child)] if children else [node]

    all_leaves = [leaf for node in outline for leaf in _leaves(node)]
    assert all_leaves, "the outline has no leaves"
    titles = " ".join(leaf["title"] for leaf in all_leaves).lower()
    assert any(
        term in titles
        for term in ("waste", "collection", "compost", "municipal", "decline")
    ), f"the leaf titles do not name the material: {titles!r}"


# ----------------------------------------------------------------------
# The HTTP surface
# ----------------------------------------------------------------------


def test_health_reports_whether_a_model_is_loaded() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert "lm" in payload
    assert "configured" in payload["lm"]


def test_document_can_be_fetched_and_exported_to_word(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """The manuscript must be retrievable and exportable as a .docx."""
    import asyncio

    from lmeditor.server.session import session_manager
    from lmeditor.workflow.graph import run_workflow

    doc_id = "doc_e2e_export"

    async def _seed() -> dict[str, Any]:
        session = await session_manager.create_session(doc_id=doc_id, topic=TOPIC)
        result = await run_workflow(
            doc_id=doc_id,
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=session,
        )
        from lmeditor.server.routes import _apply_scorecard, _sections_dump

        _apply_scorecard(session, result)
        await session.complete(sections=_sections_dump(result))
        return result

    try:
        asyncio.run(_seed())

        with TestClient(create_app()) as client:
            document = client.get(f"/api/v1/documents/{doc_id}")
            assert document.status_code == 200
            payload = document.json()
            assert payload["sections"], "the document must expose its sections"
            assert payload["scorecard"], "the document must expose a real scorecard"
            assert payload["scorecard"]["total_claims"] > 0

            export = client.get(f"/api/v1/documents/{doc_id}/export.docx")
            assert export.status_code == 200
            assert export.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument"
            )
            with zipfile.ZipFile(io.BytesIO(export.content)) as package:
                names = set(package.namelist())
                assert "word/document.xml" in names
                assert "[Content_Types].xml" in names
    finally:
        # The registry is process-wide: the session this test opened is taken
        # out of it again, whether the body succeeded or not.
        asyncio.run(session_manager.remove_session(doc_id))


def test_an_ingested_source_is_remembered_by_the_server() -> None:
    """The server keeps what it ingested, so a document can name it by id."""
    from lmeditor.server.sources import source_library

    source_library.clear()
    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/v1/sources/ingest",
                data={"source_id": "src_lib_1", "title": "Municipal Audit"},
                files={"file": ("audit.txt", SOURCE_TEXT.encode("utf-8"), "text/plain")},
            )
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["chunks"], "the ingest must produce chunks"

            listing = client.get("/api/v1/sources")
            assert listing.status_code == 200
            sources = listing.json()["sources"]
            assert [s["source_id"] for s in sources] == ["src_lib_1"]
            assert sources[0]["chunk_count"] == len(body["chunks"])

            stored = source_library.get("src_lib_1")
            assert stored is not None
            assert stored.chunks, "the library keeps the chunks of the source"
    finally:
        # The tier keeps one library for the whole process, so the source this
        # test ingested is cleared whether the assertions passed or not.
        source_library.clear()


def test_a_legacy_doc_upload_is_refused_with_a_useful_message() -> None:
    """A binary ``.doc`` is refused clearly, not as a corrupt ``.docx``."""
    # The OLE2 compound-file magic number every legacy Word document starts
    # with.
    legacy = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/sources/ingest",
            data={"source_id": "src_legacy", "title": "old.doc"},
            files={"file": ("old.doc", legacy, "application/msword")},
        )

    assert response.status_code == 415
    assert "docx" in response.json()["detail"].lower()


def test_a_grounded_claim_must_verify_against_the_chunk_it_cites() -> None:
    """A verbatim quote of a chunk must be judged entailed, not neutral.

    The entailment model is trained on pairs of statements. Judging a claim
    that restates *one* sentence against a chunk that holds *three* is a
    different task, and the model answered it wrongly: a verbatim quote came
    back NEUTRAL at 0.998, so perfectly grounded sections were reported as
    ungrounded and no document could ever pass the gate.
    """
    from lmeditor.verification.citation_checker import CitationChecker
    from lmeditor.verification.nli_evaluator import CrossEncoderNLIEvaluator

    chunk = _chunk()
    claim_text = "Municipal waste collection rates fell by 30 percent between 2019 and 2023"

    focused = CitationChecker._focus_premise(chunk.text, claim_text)
    assert focused != chunk.text, (
        "the premise must be narrowed to the sentence the claim speaks to"
    )
    assert "composting" not in focused, (
        "the premise must not carry sentences the claim says nothing about"
    )

    try:
        evaluator = CrossEncoderNLIEvaluator()
        evaluator._load_model()
    except Exception as exc:  # noqa: BLE001 - the weights may not be present
        pytest.skip(f"the cross-encoder weights are not available: {exc}")

    import asyncio

    status, score = asyncio.run(evaluator.evaluate(focused, claim_text))
    assert status is EntailmentStatus.ENTAILED, (
        f"a verbatim quote must verify, got {status.value} at {score}"
    )
    assert score >= 0.5


def test_the_checker_hands_the_focused_premise_to_the_evaluator() -> None:
    """The premise the evaluator receives must be the focused one."""
    from lmeditor.core.schemas import SectionDraft
    from lmeditor.verification.citation_checker import CitationChecker

    seen: list[tuple[str, str]] = []

    class RecordingEvaluator:
        evaluator_name = "recording"

        async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]:
            seen.append((premise, hypothesis))
            return (EntailmentStatus.ENTAILED, 0.99)

    chunk = _chunk()
    section = SectionDraft(
        section_id="sec_1",
        title="Rates",
        content=(
            "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
            f"[#{chunk.chunk_id}]"
        ),
        order=0,
        cited_chunk_ids=[chunk.chunk_id],
    )

    checker = CitationChecker(evaluator=RecordingEvaluator(), f_beta_threshold=0.5)
    import asyncio

    asyncio.run(
        checker.audit_section(
            section=section,
            chunks_by_id={chunk.chunk_id: chunk},
            strict_hallucination_check=True,
        )
    )

    assert seen, "the evaluator must be consulted"
    premise, hypothesis = seen[0]
    assert "composting" not in premise, (
        f"the premise was not focused: {premise!r}"
    )
    assert "rates fell by 30 percent" in premise
    assert "rates fell by 30 percent" in hypothesis


def test_a_citation_marker_follows_the_sentence_it_supports() -> None:
    """The marker after a sentence belongs to that sentence."""
    from lmeditor.verification.claim_extractor import extract_claims

    chunk = _chunk()
    content = (
        "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
        f"[#{chunk.chunk_id}] "
        "The decline was driven by a citywide composting programme introduced in 2020. "
        f"[#{chunk.chunk_id}]"
    )

    claims = extract_claims(content)
    assert claims, "the text must yield claims"
    for claim in claims:
        assert claim.cited_chunk_ids == [chunk.chunk_id], (
            f"the claim {claim.text!r} lost its citation"
        )


def test_a_generation_request_needs_material() -> None:
    """A request naming no source material is rejected, not silently run."""
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/documents/generate", json={"topic": TOPIC})
    assert response.status_code == 422


def test_a_generation_request_can_reference_a_source_by_id(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """``source_ids`` resolves to the chunks the server already holds."""
    import asyncio

    from lmeditor.server.session import session_manager
    from lmeditor.server.sources import source_library

    source_library.clear()
    generated: list[str] = []
    try:
        chunk = _chunk()
        source_library.add(
            StoredSource.from_source(
                Source(
                    id="src_ref_1",
                    kind=SourceKind.TEXT,
                    origin="memory://src_ref_1",
                    title="Municipal Audit",
                ),
                [chunk],
            )
        )

        with TestClient(create_app()) as client:
            response = client.post(
                "/api/v1/documents/generate",
                json={"topic": TOPIC, "source_ids": ["src_ref_1"]},
            )
            assert response.status_code == 202, response.text
            assert response.json()["doc_id"]
            generated.append(response.json()["doc_id"])
    finally:
        # The tier keeps one library for the whole process, so the source this
        # test stored is cleared whether the assertions passed or not.
        source_library.clear()
        # The session registry is process-wide as well: the session the run
        # opened is taken out of it again, whether the body succeeded or not.
        for doc_id in generated:
            asyncio.run(session_manager.remove_session(doc_id))
