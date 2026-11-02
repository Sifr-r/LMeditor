"""A completed document must still answer an edit and a fact-check.

These are the two surfaces a person uses *after* a document is written.  A
document that has run to completion is exactly the document that gets edited
and fact-checked, so the machinery those surfaces need must outlive the run
that produced it.

The retrieval index is that machinery.  The inline editor grounds a revision
in the passages the instruction asks about, and the fact-checker compares a
claim against the corpus the document was written from.  Both read the
document's own index; neither is handed a corpus of its own, because two
indexes over the same corpus can disagree about which passage answers a
question.

The check that matters is not "the index exists".  It is that a claim which is
*verbatim one of the document's own source passages* is reported as supported,
with that passage as its evidence -- over HTTP, through the route the client
calls, for a document that has finished running.
"""

from __future__ import annotations

import asyncio
from typing import Any

import dspy
import pytest

from lmeditor.core.schemas import SourceChunk
from lmeditor.core.types import EntailmentStatus
from lmeditor.server.app import create_app
from lmeditor.server.session import session_manager
from lmeditor.workflow.graph import run_workflow
from lmeditor.workflow.nodes import provenance_index_for, release_provenance_index
from tests.http_support import sync_client

from .stub_lm import StubLM

TOPIC = "Municipal waste collection trends"

SOURCE_TEXT = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
    "The decline was driven by a citywide composting programme introduced in 2020."
)


def _chunk() -> SourceChunk:
    """One source chunk, the way a request would carry it."""
    return SourceChunk.create(
        document_id="doc_completed",
        text=SOURCE_TEXT,
        char_start=0,
        char_end=len(SOURCE_TEXT),
        byte_start=0,
        byte_end=len(SOURCE_TEXT),
        metadata={"document_title": "Municipal Audit"},
    )


class DeterministicNLI:
    """An NLI evaluator that is exact rather than statistical."""

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


def _run_a_document_to_completion(doc_id: str) -> None:
    """Run the real workflow to its terminal state, the way the server does.

    The document is not a hand-built model: it is written by the pipeline from
    a real source chunk, verified, and finished.  Everything the run leaves
    behind -- the session, the sections, the scorecard -- is the state the
    editing and fact-check surfaces are supposed to work against.
    """
    from lmeditor.server.routes import _apply_scorecard, _sections_dump

    async def _seed() -> None:
        session = await session_manager.create_session(doc_id=doc_id, topic=TOPIC)
        result = await run_workflow(
            doc_id=doc_id,
            topic=TOPIC,
            source_chunks=[_chunk()],
            max_retries=1,
            threshold_f_beta=0.5,
            session=session,
        )
        _apply_scorecard(session, result)
        await session.complete(sections=_sections_dump(result))

    asyncio.run(_seed())


def test_the_document_keeps_its_retrieval_index_after_the_run(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """The index outlives the run that built it.

    The run ends.  The document does not: it is still on the operator's desk,
    waiting to be edited and fact-checked against the sources it was written
    from.
    """
    doc_id = "doc_completed_index"
    _run_a_document_to_completion(doc_id)

    index = provenance_index_for(doc_id)
    assert index is not None, (
        "the document lost its retrieval index when the run ended, so nothing "
        "can be grounded against the sources it was written from"
    )
    assert index.chunk_count >= 1


def test_a_verbatim_source_passage_fact_checks_as_supported(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """A claim that is one of the document's own passages is supported.

    This is the honest answer and it is only available when the document still
    holds its corpus.  With the corpus gone the checker has nothing to check
    against and reports "unsupported" -- which is a different claim entirely,
    and a false one.

    The verdict is allowed to be partial, because the evaluator that judges the
    passage may hold that one of its sentences is neither supported nor
    refuted.  What must not happen is the passage being *ungrounded*: that is
    the signature of a checker with no corpus behind it.
    """
    doc_id = "doc_completed_factcheck"
    _run_a_document_to_completion(doc_id)

    with sync_client(create_app()) as client:
        response = client.post(
            f"/api/v1/documents/{doc_id}/factcheck",
            json={
                "doc_id": doc_id,
                "text": SOURCE_TEXT,
                "include_web": False,
            },
        )

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["verdict"] in {"supported", "partially_supported"}, (
        f"a passage the document was written from is not supported: {report}"
    )
    assert report["claims"], "the passage's claim must be reported, not dropped"
    for claim in report["claims"]:
        assert claim["status"] != "ungrounded", (
            f"the claim is ungrounded, which is what a checker with no corpus "
            f"behind it says: {claim}"
        )
        assert claim["evidence"], (
            f"the claim carries no evidence, so 'supported' would be an "
            f"assertion: {claim}"
        )
        for evidence in claim["evidence"]:
            assert evidence["source"] == "corpus", evidence
            assert evidence["chunk_id"] == _chunk().chunk_id, evidence
            assert evidence["excerpt"] == SOURCE_TEXT, evidence


def test_the_index_is_what_makes_the_verdict_supported(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """The index is the only variable between the two answers.

    The same claim, the same document, the same route: with the corpus the
    passage came from the verdict rests on that passage; without it there is
    nothing to check against and the honest answer becomes "unsupported", with
    every claim ungrounded and carrying no evidence at all.  That is the whole
    defect in one test.
    """
    doc_id = "doc_completed_ab"
    _run_a_document_to_completion(doc_id)

    def _check() -> dict[str, Any]:
        with sync_client(create_app()) as client:
            response = client.post(
                f"/api/v1/documents/{doc_id}/factcheck",
                json={
                    "doc_id": doc_id,
                    "text": SOURCE_TEXT,
                    "include_web": False,
                },
            )
        assert response.status_code == 200, response.text
        return response.json()

    with_corpus = _check()
    assert with_corpus["verdict"] != "unsupported", with_corpus
    assert any(claim["evidence"] for claim in with_corpus["claims"]), (
        "the passage was checked against nothing"
    )

    release_provenance_index(doc_id)
    without_corpus = _check()
    assert without_corpus["verdict"] == "unsupported", (
        "the corpus is what makes the verdict supported, so dropping it must "
        "change the answer"
    )
    assert without_corpus["claims"], "the claim is still reported when ungrounded"
    for claim in without_corpus["claims"]:
        assert claim["status"] == "ungrounded", claim
        assert claim["evidence"] == [], claim


def test_a_grounded_edit_applies_to_a_completed_document(
    stub_model: StubLM, deterministic_nli: DeterministicNLI
) -> None:
    """A small edit to a finished document is grounded in its own sources.

    The editor refuses a revision that cites a source it cannot verify, and
    it cannot verify one without the corpus.  A finished document that cannot
    be edited is a finished document the operator can only throw away.
    """
    doc_id = "doc_completed_edit"
    _run_a_document_to_completion(doc_id)

    with sync_client(create_app()) as client:
        document = client.get(f"/api/v1/documents/{doc_id}")
        assert document.status_code == 200, document.text
        sections = document.json()["sections"]
        assert sections, "the document must expose the section to edit"
        section_id = sections[0]["section_id"]

        response = client.post(
            f"/api/v1/documents/{doc_id}/edits",
            json={
                "doc_id": doc_id,
                "section_id": section_id,
                "instruction": "tighten this",
                "scope": "paragraph",
                "paragraph_index": 0,
            },
        )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["applied"] is True, (
        f"a grounded edit was refused on a completed document: {result}"
    )
    assert "supported by the sources it cites" in result.get("reason", ""), result
    assert result["section"]["content"], "the revised text is missing"
    assert result["cited_chunk_ids"], (
        "the revision cites nothing, so it was not grounded in the sources"
    )
