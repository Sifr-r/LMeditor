"""Comprehensive test suite for LMeditor core exceptions, types, protocols, and schemas."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

import lmeditor.core as core
from lmeditor.core.exceptions import (
    BibliographyResolutionError,
    ChunkNotFoundError,
    CitationHallucinationError,
    LMeditorError,
    ProvenanceError,
    SourceLoadError,
    SpanAlignmentError,
    UngroundedClaimError,
    WorkflowDeadlockError,
)
from lmeditor.core.schemas import (
    CitationLineage,
    Claim,
    OutlineNode,
    Perspective,
    SectionDraft,
    SourceChunk,
    VerificationReport,
)
from lmeditor.core.types import (
    BibliographyResolverKind,
    ChunkerProtocol,
    DenseEmbedderProtocol,
    DocumentStatus,
    EntailmentStatus,
    HybridIndexerProtocol,
    PerspectiveRole,
    SourceKind,
    SparseSearchProtocol,
)


# ============================================================================
# 1. Core Module Export Tests
# ============================================================================


def test_core_module_all_exports() -> None:
    """Verify all expected symbols are exported in __all__ from lmeditor.core."""
    expected = {
        "LMeditorError",
        "ProvenanceError",
        "ChunkNotFoundError",
        "SpanAlignmentError",
        "CitationHallucinationError",
        "UngroundedClaimError",
        "WorkflowDeadlockError",
        "EntailmentStatus",
        "DocumentStatus",
        "PerspectiveRole",
        "ChunkerProtocol",
        "DenseEmbedderProtocol",
        "SparseSearchProtocol",
        "HybridIndexerProtocol",
        "SourceChunk",
        "Claim",
        "CitationLineage",
        "Perspective",
        "OutlineNode",
        "SectionDraft",
        "VerificationReport",
    }
    assert expected.issubset(set(core.__all__))
    for name in expected:
        assert hasattr(core, name)


# ============================================================================
# 2. Domain Exception Hierarchy Tests
# ============================================================================


def test_exception_hierarchy() -> None:
    """Validate strict inheritance of domain exceptions."""
    assert issubclass(ProvenanceError, LMeditorError)
    assert issubclass(ChunkNotFoundError, ProvenanceError)
    assert issubclass(SpanAlignmentError, ProvenanceError)
    assert issubclass(CitationHallucinationError, LMeditorError)
    assert issubclass(UngroundedClaimError, LMeditorError)
    assert issubclass(WorkflowDeadlockError, LMeditorError)


def test_chunk_not_found_error() -> None:
    """Test ChunkNotFoundError attributes and string representation."""
    err = ChunkNotFoundError(chunk_id="chunk_123")
    assert err.chunk_id == "chunk_123"
    assert "chunk_123" in str(err)
    assert "ChunkNotFoundError" in str(err)

    custom_err = ChunkNotFoundError(chunk_id="c1", message="Custom missing chunk")
    assert custom_err.message == "Custom missing chunk"
    assert "Custom missing chunk" in str(custom_err)


def test_span_alignment_error() -> None:
    """Test SpanAlignmentError with and without doc_length."""
    err = SpanAlignmentError(span_start=50, span_end=100, doc_length=80)
    assert err.span_start == 50
    assert err.span_end == 100
    assert err.doc_length == 80
    assert "[50, 100)" in str(err)
    assert "doc_length=80" in str(err)

    err_no_len = SpanAlignmentError(span_start=10, span_end=5)
    assert err_no_len.doc_length is None
    assert "[10, 5)" in str(err_no_len)


def test_citation_hallucination_error() -> None:
    """Test CitationHallucinationError formatting with contextual properties."""
    err = CitationHallucinationError(
        chunk_id="chunk_abc",
        quote="Long ungrounded claim that does not exist in the source document",
        reason="No lexical or semantic overlap",
    )
    assert err.chunk_id == "chunk_abc"
    assert err.quote is not None
    assert err.reason == "No lexical or semantic overlap"
    assert "chunk_id='chunk_abc'" in str(err)
    assert "CitationHallucinationError" in str(err)


def test_ungrounded_claim_error() -> None:
    """Test UngroundedClaimError score and threshold formatting."""
    err = UngroundedClaimError(claim_id="claim_001", entailment_score=0.452, threshold=0.75)
    assert err.claim_id == "claim_001"
    assert err.entailment_score == 0.452
    assert err.threshold == 0.75
    assert "0.4520" in str(err)
    assert "0.7500" in str(err)


def test_workflow_deadlock_error() -> None:
    """Test WorkflowDeadlockError node tracking and summary."""
    err = WorkflowDeadlockError(current_node="drafting_node", state_summary="Max revisions reached")
    assert err.current_node == "drafting_node"
    assert err.state_summary == "Max revisions reached"
    assert "drafting_node" in str(err)
    assert "Max revisions reached" in str(err)


# ============================================================================
# 3. Types and Protocols Tests
# ============================================================================


def test_enum_values() -> None:
    """Verify string Enum contents for domain types."""
    assert EntailmentStatus.ENTAILED.value == "entailed"
    assert EntailmentStatus.CONTRADICTED.value == "contradicted"
    assert EntailmentStatus.NEUTRAL.value == "neutral"
    assert EntailmentStatus.UNGROUNDED.value == "ungrounded"

    assert DocumentStatus.DISCOVERY.value == "discovery"
    assert DocumentStatus.COMPLETED.value == "completed"
    assert DocumentStatus.READY_FOR_HITL.value == "ready_for_hitl"

    assert PerspectiveRole.DOMAIN_EXPERT.value == "domain_expert"
    assert PerspectiveRole.CRITICAL_SKEPTIC.value == "critical_skeptic"
    assert PerspectiveRole.FACT_CHECKER.value == "fact_checker"


def test_runtime_protocols() -> None:
    """Verify Protocols with @runtime_checkable."""
    class DummyChunker:
        def chunk(
            self,
            text: str,
            document_id: str,
            metadata: dict[str, Any] | None = None,
        ) -> list[Any]:
            return []

    class DummyEmbedder:
        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2]

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2] for _ in texts]

    class DummySparseSearch:
        def index(self, chunks: list[Any]) -> None:
            pass

        def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
            return [("id1", 1.0)]

    class DummyHybridIndexer:
        def index(self, chunks: list[Any]) -> None:
            pass

        def get_chunk(self, chunk_id: str) -> Any:
            return None

        def search(
            self,
            query: str,
            top_k: int = 10,
            rrf_k: int = 60,
            alpha: float = 0.5,
        ) -> list[tuple[Any, float]]:
            return []

    assert isinstance(DummyChunker(), ChunkerProtocol)
    assert isinstance(DummyEmbedder(), DenseEmbedderProtocol)
    assert isinstance(DummySparseSearch(), SparseSearchProtocol)
    assert isinstance(DummyHybridIndexer(), HybridIndexerProtocol)


# ============================================================================
# 4. Pydantic v2 Schema Tests
# ============================================================================


def test_source_chunk_creation_and_deterministic_hash() -> None:
    """Verify SourceChunk.create generates deterministic SHA-256 hash."""
    doc_id = "doc_test_1"
    text = "Machine learning harnesses require grounded factual claims."
    char_start = 0
    char_end = len(text)
    byte_start = 0
    byte_end = len(text.encode("utf-8"))

    chunk = SourceChunk.create(
        document_id=doc_id,
        text=text,
        char_start=char_start,
        char_end=char_end,
        byte_start=byte_start,
        byte_end=byte_end,
        metadata={"source": "manual"},
    )

    expected_payload = f"{doc_id}:{char_start}:{char_end}:{text}"
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    assert chunk.chunk_id == expected_hash
    assert chunk.document_id == doc_id
    assert chunk.text == text
    assert chunk.char_start == char_start
    assert chunk.char_end == char_end
    assert chunk.metadata == {"source": "manual"}


def test_source_chunk_immutability_and_extra_fields() -> None:
    """Verify SourceChunk is frozen and forbids extra attributes."""
    chunk = SourceChunk.create(
        document_id="doc1",
        text="Valid passage content.",
        char_start=0,
        char_end=22,
        byte_start=0,
        byte_end=22,
    )
    with pytest.raises(ValidationError):
        chunk.text = "Attempt mutation"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        SourceChunk(  # type: ignore[call-arg]
            chunk_id=chunk.chunk_id,
            document_id="doc1",
            text="Valid passage content.",
            char_start=0,
            char_end=22,
            byte_start=0,
            byte_end=22,
            unauthorized_field="illegal",
        )


def test_source_chunk_boundary_validation() -> None:
    """Validate char_end > char_start and byte_end > byte_start invariants."""
    with pytest.raises(ValidationError) as exc_info:
        SourceChunk.create(
            document_id="doc1",
            text="Sample",
            char_start=10,
            char_end=5,  # Invalid: char_end <= char_start
            byte_start=0,
            byte_end=6,
        )
    assert "char_end" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        SourceChunk.create(
            document_id="doc1",
            text="Sample",
            char_start=0,
            char_end=6,
            byte_start=10,
            byte_end=5,  # Invalid: byte_end <= byte_start
        )
    assert "byte_end" in str(exc_info.value)


def test_claim_validation() -> None:
    """Test Claim schema validation and boundary enforcement."""
    claim = Claim(
        claim_id="claim_1",
        section_id="sec_intro",
        text="All propositions are mathematically verified.",
        char_start=0,
        char_end=46,
        cited_chunk_ids=["chunk_a", "chunk_b"],
        entailment_status=EntailmentStatus.ENTAILED,
        entailment_score=0.98,
        reasoning="Direct semantic entailment verified by NLI model.",
    )
    assert claim.entailment_score == 0.98
    assert claim.entailment_status == EntailmentStatus.ENTAILED

    # Invalid offset
    with pytest.raises(ValidationError):
        Claim(
            claim_id="c2",
            section_id="sec1",
            text="Invalid offset",
            char_start=20,
            char_end=15,
        )

    # Invalid score > 1.0
    with pytest.raises(ValidationError):
        Claim(
            claim_id="c3",
            section_id="sec1",
            text="Invalid score",
            char_start=0,
            char_end=10,
            entailment_score=1.5,
        )


def test_citation_lineage_validation() -> None:
    """Test CitationLineage schema constraints and UTC timestamp default."""
    before = datetime.now(timezone.utc)
    lineage = CitationLineage(
        lineage_id="lin_1",
        claim_id="claim_1",
        chunk_id="chunk_a",
        span_start=10,
        span_end=25,
        exact_quote="mathematically verified",
        confidence_score=0.95,
    )
    after = datetime.now(timezone.utc)

    assert lineage.span_start == 10
    assert lineage.span_end == 25
    assert before <= lineage.created_at <= after

    # Span validation
    with pytest.raises(ValidationError):
        CitationLineage(
            lineage_id="lin_2",
            claim_id="claim_1",
            chunk_id="chunk_a",
            span_start=30,
            span_end=30,  # Invalid: span_end <= span_start
        )


def test_perspective_schema() -> None:
    """Test Perspective schema initialization."""
    persp = Perspective(
        perspective_id="persp_skeptic",
        role=PerspectiveRole.CRITICAL_SKEPTIC,
        title="Adversarial Peer Reviewer",
        focus_areas=["methodological rigor", "source authenticity"],
        prompt_directives=["Scrutinize statistical claims", "Highlight unverified assertions"],
    )
    assert persp.role == PerspectiveRole.CRITICAL_SKEPTIC
    assert len(persp.focus_areas) == 2
    assert len(persp.prompt_directives) == 2


def test_outline_node_recursive_hierarchy() -> None:
    """Test OutlineNode hierarchical nesting and constraints."""
    child_node = OutlineNode(
        node_id="sec_1_1",
        title="Theoretical Foundations",
        section_id="s_1_1",
        depth=1,
        description="Detailed review of NLI scoring architectures",
        assigned_chunk_ids=["chunk_1"],
        order=0,
    )
    root_node = OutlineNode(
        node_id="sec_1",
        title="Introduction & Background",
        section_id="s_1",
        depth=0,
        description="High level overview",
        assigned_chunk_ids=["chunk_0"],
        children=[child_node],
        order=0,
    )
    assert root_node.depth == 0
    assert len(root_node.children) == 1
    assert root_node.children[0].node_id == "sec_1_1"


def test_section_draft_schema() -> None:
    """Test SectionDraft schema linking claims and citations."""
    claim = Claim(
        claim_id="clm_1",
        section_id="sec_intro",
        text="The system guarantees chunk traceability.",
        char_start=0,
        char_end=42,
    )
    citation = CitationLineage(
        lineage_id="lin_1",
        claim_id="clm_1",
        chunk_id="chk_1",
        span_start=21,
        span_end=39,
    )
    draft = SectionDraft(
        section_id="sec_intro",
        title="Introduction",
        content="The system guarantees chunk traceability.",
        order=0,
        citations=[citation],
        claims=[claim],
        is_verified=True,
    )
    assert draft.is_verified is True
    assert len(draft.claims) == 1
    assert len(draft.citations) == 1


def test_verification_report_schema() -> None:
    """Test VerificationReport metrics scorecard."""
    report = VerificationReport(
        report_id="rep_01",
        section_id="sec_intro",
        total_claims=10,
        entailed_claims=9,
        contradicted_claims=0,
        ungrounded_claims=1,
        precision=0.9,
        status=EntailmentStatus.NEUTRAL,
        all_passed=False,
        summary="9 out of 10 claims entailed, 1 claim neutral.",
    )
    assert report.precision == 0.9
    assert report.all_passed is False
    assert report.entailed_claims == 9


def test_verification_scorecard_and_events() -> None:
    """Test VerificationScorecard, HITLReviewPayload, and WorkflowEvent schemas."""
    from lmeditor.core.schemas import HITLReviewPayload, VerificationScorecard, WorkflowEvent
    from lmeditor.core.types import EventType, WorkflowStep

    scorecard = VerificationScorecard(
        precision=0.95,
        recall=0.90,
        f_beta=0.94,
        total_claims=20,
        supported_claims=19,
        ungrounded_claims=1,
        contradicted_claims=0,
        neutral_claims=0,
    )
    assert scorecard.precision == 0.95
    assert scorecard.supported_claims == 19

    hitl = HITLReviewPayload(
        doc_id="doc_123",
        approved=True,
        feedback="Looks good to proceed.",
    )
    assert hitl.doc_id == "doc_123"
    assert hitl.approved is True

    event = WorkflowEvent(
        event_type=EventType.TOKEN_DELTA,
        step=WorkflowStep.DRAFT_SECTIONS,
        doc_id="doc_123",
        data={"delta": "Generated text chunk"},
    )
    assert event.event_type == EventType.TOKEN_DELTA
    assert event.step == WorkflowStep.DRAFT_SECTIONS


# ============================================================================
# 5. Source Ingestion Enum Tests (Task 1 — SourceKind & BibliographyResolverKind)
# ============================================================================


def test_source_kind_includes_pdf_docx_md_html_text_url() -> None:
    """Verify SourceKind exposes all six ingestion modalities with stable wire values."""
    assert SourceKind.PDF.value == "pdf"
    assert SourceKind.DOCX.value == "docx"
    assert SourceKind.MARKDOWN.value == "markdown"
    assert SourceKind.HTML.value == "html"
    assert SourceKind.TEXT.value == "text"
    assert SourceKind.URL.value == "url"


def test_bibliography_resolver_kind_ordering() -> None:
    """Verify BibliographyResolverKind declaration order defines resolver priority."""
    order = [k.value for k in BibliographyResolverKind]
    assert order[:5] == [
        "pdf-header",
        "crossref",
        "arxiv",
        "pubmed",
        "openalex",
    ]
    assert BibliographyResolverKind.USER.value == "user"
    assert BibliographyResolverKind.NONE.value == "none"


# ============================================================================
# 6. Source Ingestion Exception Tests (Task 2 — SourceLoadError & BibliographyResolutionError)
# ============================================================================


def test_source_load_error_carries_kind_and_origin() -> None:
    """Verify SourceLoadError exposes kind/origin/reason and is raisable."""
    err = SourceLoadError(kind="pdf", origin="paper.pdf", reason="not a real PDF")
    assert err.kind == "pdf"
    assert err.origin == "paper.pdf"
    assert "not a real PDF" in str(err)
    with pytest.raises(SourceLoadError):
        raise err


def test_bibliography_resolution_error_carries_resolver_and_origin() -> None:
    """Verify BibliographyResolutionError exposes resolver/origin/reason."""
    err = BibliographyResolutionError(
        resolver="crossref",
        origin="10.1234/missing",
        reason="HTTP 404",
    )
    assert err.resolver == "crossref"
    assert err.origin == "10.1234/missing"
    assert "HTTP 404" in str(err)


# ============================================================================
# 7. BibliographicRecord Schema Tests (Task 3)
# ============================================================================


def test_bibliographic_record_minimal() -> None:
    """BibliographicRecord with only a title defaults to empty authors, no year, and resolver=NONE."""
    from lmeditor.core.schemas import BibliographicRecord

    rec = BibliographicRecord(title="A Foundational Paper")
    assert rec.title == "A Foundational Paper"
    assert rec.authors == []
    assert rec.year is None
    assert rec.resolver.value == "none"


def test_bibliographic_record_full() -> None:
    """BibliographicRecord populates all metadata fields and resolver='crossref'."""
    from lmeditor.core.schemas import BibliographicRecord

    rec = BibliographicRecord(
        title="A Foundational Paper",
        authors=["Doe, J.", "Roe, R."],
        year=2024,
        publication_date="2024-06-01",
        container_title="Journal of Tests",
        volume="12",
        issue="3",
        pages="100-120",
        doi="10.1234/journal.2024.001",
        url="https://example.org/paper",
        type="journal-article",
        resolver="crossref",
    )
    assert rec.title == "A Foundational Paper"
    assert rec.authors == ["Doe, J.", "Roe, R."]
    assert rec.year == 2024
    assert rec.publication_date == "2024-06-01"
    assert rec.container_title == "Journal of Tests"
    assert rec.volume == "12"
    assert rec.issue == "3"
    assert rec.pages == "100-120"
    assert rec.doi == "10.1234/journal.2024.001"
    assert rec.url == "https://example.org/paper"
    assert rec.type == "journal-article"
    assert rec.resolver.value == "crossref"


def test_bibliographic_record_year_bounds() -> None:
    """Year field rejects values outside [1700, 2100] with a year-related error."""
    from lmeditor.core.schemas import BibliographicRecord

    with pytest.raises(ValidationError, match="year"):
        BibliographicRecord(title="Bad Year", year=1000)
    with pytest.raises(ValidationError, match="year"):
        BibliographicRecord(title="Future Year", year=9999)
    # Boundary values are accepted
    edge_low = BibliographicRecord(title="Old", year=1700)
    edge_high = BibliographicRecord(title="New", year=2100)
    assert edge_low.year == 1700
    assert edge_high.year == 2100


def test_bibliographic_record_immutability_and_extra_forbid() -> None:
    """BibliographicRecord is frozen and rejects unknown fields."""
    from lmeditor.core.schemas import BibliographicRecord

    rec = BibliographicRecord(title="Locked", resolver=BibliographyResolverKind.USER)
    with pytest.raises(ValidationError):
        rec.title = "Mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        BibliographicRecord(  # type: ignore[call-arg]
            title="Locked",
            resolver=BibliographyResolverKind.USER,
            bogus_field="nope",
        )


# ============================================================================
# 8. Source and SourceIngestResult Schema Tests (Task 4)
# ============================================================================


def test_source_minimal() -> None:
    """Source with required fields defaults bibliography and page_count to None."""
    from lmeditor.core.schemas import Source

    src = Source(
        id="src_01",
        kind=SourceKind.PDF,
        origin="/tmp/paper.pdf",
        title="paper.pdf",
    )
    assert src.id == "src_01"
    assert src.kind == SourceKind.PDF
    assert src.bibliography is None
    assert src.page_count is None


def test_source_with_bibliography() -> None:
    """Source attaches an optional BibliographicRecord and page_count."""
    from lmeditor.core.schemas import BibliographicRecord, Source

    bib = BibliographicRecord(
        title="A Foundational Paper",
        resolver=BibliographyResolverKind.NONE,
    )
    src = Source(
        id="src_02",
        kind=SourceKind.PDF,
        origin="/tmp/paper.pdf",
        title="paper.pdf",
        bibliography=bib,
        page_count=12,
    )
    assert src.bibliography.title == "A Foundational Paper"
    assert src.page_count == 12


def test_source_id_pattern() -> None:
    """Source.id rejects whitespace and empty strings via the pattern + min_length guard."""
    from lmeditor.core.schemas import Source

    with pytest.raises(ValueError):
        Source(id="bad id with spaces", kind=SourceKind.TEXT, origin="x", title="x")
    with pytest.raises(ValueError):
        Source(id="", kind=SourceKind.TEXT, origin="x", title="x")


def test_source_ingest_result_chunks_have_parent_source_id() -> None:
    """SourceIngestResult bundles a Source with chunks whose document_id matches the source id."""
    from lmeditor.core.schemas import BibliographicRecord, Source, SourceIngestResult

    bib = BibliographicRecord(title="x", resolver=BibliographyResolverKind.NONE)
    src = Source(id="src_03", kind=SourceKind.TEXT, origin="x", title="x", bibliography=bib)
    chunks = [
        SourceChunk.create(
            document_id="src_03",
            text="hello world",
            char_start=0,
            char_end=11,
            byte_start=0,
            byte_end=11,
        )
    ]
    result = SourceIngestResult(source=src, chunks=chunks)
    assert all(c.document_id == src.id for c in result.chunks)

