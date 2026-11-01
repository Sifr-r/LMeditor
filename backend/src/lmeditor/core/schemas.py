"""Immutable, strictly validated Pydantic v2 data models for LMeditor core and provenance."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lmeditor.core.types import (
    BibliographyResolverKind,
    DocumentStatus,
    EditScope,
    EntailmentStatus,
    EventType,
    EvidenceSource,
    FactCheckVerdict,
    OutlineNodeType,
    PerspectiveRole,
    SectionStatus,
    SourceKind,
    WorkflowStep,
    WorkingSetRole,
)


class SourceChunk(BaseModel):
    """Immutable representation of a text segment anchored by character and byte offsets."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    chunk_id: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="Deterministic SHA-256 hex digest of the chunk",
    )
    document_id: str = Field(
        min_length=1,
        description="Identifier of the parent source document",
    )
    text: str = Field(min_length=1, description="Raw passage text content")
    char_start: int = Field(default=0, ge=0, description="Inclusive character start index in source document")
    char_end: int = Field(default=0, ge=0, description="Exclusive character end index in source document")
    byte_start: int = Field(default=0, ge=0, description="Inclusive UTF-8 byte start offset in source document")
    byte_end: int = Field(default=0, ge=0, description="Exclusive UTF-8 byte end offset in source document")
    token_count: int = Field(default=0, ge=0, description="Pre-computed token length")
    start_char: int = Field(default=0, ge=0, description="Alias for char_start")
    end_char: int = Field(default=0, ge=0, description="Alias for char_end")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary chunk metadata")

    @property
    def doc_id(self) -> str:
        """Alias for document_id."""
        return self.document_id

    @property
    def char_offset_start(self) -> int:
        """Alias for char_start."""
        return self.char_start

    @property
    def char_offset_end(self) -> int:
        """Alias for char_end."""
        return self.char_end

    @property
    def document_title(self) -> str:
        """Title of the source document from metadata."""
        return str(self.metadata.get("document_title", ""))

    @model_validator(mode="after")
    def validate_offsets(self) -> "SourceChunk":
        if self.char_end > 0 or self.char_start > 0:
            if self.char_end <= self.char_start:
                raise ValueError(
                    f"char_end ({self.char_end}) must be greater than char_start ({self.char_start})"
                )
        if self.byte_end > 0 or self.byte_start > 0:
            if self.byte_end <= self.byte_start:
                raise ValueError(
                    f"byte_end ({self.byte_end}) must be greater than byte_start ({self.byte_start})"
                )
        return self

    @classmethod
    def create(
        cls,
        document_id: str,
        text: str,
        char_start: int,
        char_end: int,
        byte_start: int,
        byte_end: int,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "SourceChunk":
        """Compute deterministic SHA-256 chunk_id and construct an immutable SourceChunk."""
        hash_payload = f"{document_id}:{char_start}:{char_end}:{text}"
        chunk_id = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
        return cls(
            chunk_id=chunk_id,
            document_id=document_id,
            text=text,
            char_start=char_start,
            char_end=char_end,
            byte_start=byte_start,
            byte_end=byte_end,
            start_char=char_start,
            end_char=char_end,
            metadata=metadata if metadata is not None else {},
        )



class BibliographicRecord(BaseModel):
    """Structured bibliographic metadata attached to a Source.

    Populated by the bibliography resolver chain in
    `lmeditor.ingestion.resolvers`. Required field is title; everything
    else is optional and populated opportunistically by resolvers.
    `resolver` defaults to `BibliographyResolverKind.NONE` for unresolved
    records; concrete resolvers (Crossref, arXiv, etc.) overwrite it on
    success.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    title: str = Field(min_length=1, description="Title of the work")
    authors: list[str] = Field(
        default_factory=list,
        description="Author names in display order",
    )
    year: Optional[int] = Field(
        default=None,
        ge=1700,
        le=2100,
        description="Four-digit publication year",
    )
    publication_date: Optional[str] = Field(
        default=None,
        description="Free-form publication date (ISO 8601 preferred)",
    )
    container_title: Optional[str] = Field(
        default=None,
        description="Journal name, book title, or publisher",
    )
    volume: Optional[str] = Field(default=None, description="Journal or book volume")
    issue: Optional[str] = Field(default=None, description="Journal issue number")
    pages: Optional[str] = Field(default=None, description="Page range, e.g. '100-120'")
    doi: Optional[str] = Field(default=None, description="Digital Object Identifier")
    url: Optional[str] = Field(default=None, description="Canonical URL")
    type: Optional[str] = Field(
        default=None,
        description="CSL type: journal-article, book, book-chapter, etc.",
    )
    resolver: "BibliographyResolverKind" = Field(
        default=BibliographyResolverKind.NONE,
        description="Which resolver produced this record (defaults to NONE)",
    )

    @model_validator(mode="after")
    def validate_resolver_set(self) -> "BibliographicRecord":
        if self.resolver is None:
            raise ValueError(
                "resolver must be set; use BibliographyResolverKind.NONE for unresolved records"
            )
        return self

    def to_wire(self) -> dict[str, Any]:
        """The record's wire form.

        This is the counterpart of ``kotlin/core/WireDecode.kt``'s
        ``bibliographicRecordFromWire``: the same keys, the same names and the
        same enum wire string, so the record can cross the NDJSON seam and be
        read back by the tier without a translation layer.

        Every key is always present.  An optional field travels as ``null``,
        which the decoder reads exactly like an absent key.
        """
        return {
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "publication_date": self.publication_date,
            "container_title": self.container_title,
            "volume": self.volume,
            "issue": self.issue,
            "pages": self.pages,
            "doi": self.doi,
            "url": self.url,
            "type": self.type,
            "resolver": self.resolver.value,
        }


class Source(BaseModel):
    """A user-supplied artifact (file or URL) plus its resolved bibliographic record."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    id: str = Field(
        min_length=1,
        pattern=r"^[a-zA-Z0-9_\-\.]+$",
        description="Engineer-assigned or server-generated stable identifier",
    )
    kind: "SourceKind" = Field(description="On-disk or on-wire kind of the artifact")
    origin: str = Field(min_length=1, description="Absolute path, URL, or paste content")
    title: str = Field(min_length=1, description="Display title for the source library")
    bibliography: Optional[BibliographicRecord] = Field(
        default=None,
        description="Resolved bibliographic metadata, if available",
    )
    page_count: Optional[int] = Field(
        default=None,
        ge=0,
        description="Number of pages (PDF/DOCX only; None for text/URL)",
    )
    byte_size: Optional[int] = Field(
        default=None,
        ge=0,
        description="Size of the artifact in bytes",
    )
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp at which the source was added",
    )
    omissions: list[str] = Field(
        default_factory=list,
        description="Content the loader could not preserve (images, comments, endnotes, ...)",
    )


class SourceIngestResult(BaseModel):
    """Return shape from POST /api/v1/sources/ingest."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    source: Source
    chunks: list[SourceChunk]
    omissions: list[str] = Field(
        default_factory=list,
        description="Content the loader could not preserve, surfaced to the operator",
    )


class Claim(BaseModel):
    """Atomic proposition extracted from draft text with entailment verification state."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, description="Unique identifier for the claim")
    section_id: str = Field(min_length=1, description="ID of the section containing this claim")
    text: str = Field(min_length=1, description="Atomic factual proposition text")
    char_start: int = Field(default=0, ge=0, description="Inclusive character start in section draft")
    char_end: int = Field(default=0, ge=0, description="Exclusive character end in section draft")
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Chunk IDs cited to ground this claim",
    )
    entailment_status: EntailmentStatus = Field(
        default=EntailmentStatus.NEUTRAL,
        description="Verification classification result",
    )
    entailment_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Calculated NLI entailment confidence score",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Detailed verification rationale or model audit log",
    )
    sentence_idx: int = Field(
        default=0,
        ge=0,
        description="Index of originating sentence within section",
    )
    premise_text: Optional[str] = Field(
        default=None,
        description="Source premise text evaluated against",
    )

    @property
    def rationale(self) -> Optional[str]:
        """Alias for reasoning."""
        return self.reasoning

    @model_validator(mode="after")
    def validate_offsets(self) -> "Claim":
        if self.char_end > 0 or self.char_start > 0:
            if self.char_end <= self.char_start:
                raise ValueError(
                    f"char_end ({self.char_end}) must be greater than char_start ({self.char_start})"
                )
        return self

    def to_wire(self) -> dict[str, Any]:
        """The claim's wire form.

        The counterpart of ``kotlin/core/WireDecode.kt``'s ``claimFromWire``:
        the same keys, the same names and the same enum wire string, so the
        claim can cross the NDJSON seam and be read back by the tier without a
        translation layer.

        Every key is always present.  An optional field travels as ``null``,
        which the decoder reads exactly like an absent key.
        """
        return {
            "claim_id": self.claim_id,
            "section_id": self.section_id,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "cited_chunk_ids": list(self.cited_chunk_ids),
            "entailment_status": self.entailment_status.value,
            "entailment_score": self.entailment_score,
            "reasoning": self.reasoning,
            "sentence_idx": self.sentence_idx,
            "premise_text": self.premise_text,
        }


class CitationLineage(BaseModel):
    """Fine-grained token-span attribution binding a claim to an exact source passage."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    lineage_id: str = Field(min_length=1, description="Unique identifier for this lineage link")
    claim_id: str = Field(min_length=1, description="Associated claim identifier")
    chunk_id: str = Field(min_length=1, description="Referenced source chunk SHA-256 identifier")
    span_start: int = Field(ge=0, description="Inclusive character start of citation in text")
    span_end: int = Field(description="Exclusive character end of citation in text")
    exact_quote: Optional[str] = Field(
        default=None,
        description="Verbatim source quote snippet grounded in source chunk",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of the citation alignment",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of lineage generation",
    )

    @model_validator(mode="after")
    def validate_span(self) -> "CitationLineage":
        if self.span_end <= self.span_start:
            raise ValueError(
                f"span_end ({self.span_end}) must be greater than span_start ({self.span_start})"
            )
        return self

    def to_wire(self) -> dict[str, Any]:
        """The lineage's wire form.

        The counterpart of ``kotlin/core/WireDecode.kt``'s
        ``citationLineageFromWire``: the same keys, the same names and the same
        defaults, so the lineage can cross the NDJSON seam and be read back by
        the tier without a translation layer.

        Every key is always present.  The timestamp travels as an ISO-8601
        string, the way ``kotlin/word/WIRE.md`` §1.1 spells a timestamp.
        """
        return {
            "lineage_id": self.lineage_id,
            "claim_id": self.claim_id,
            "chunk_id": self.chunk_id,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "exact_quote": self.exact_quote,
            "confidence_score": self.confidence_score,
            "created_at": self.created_at.isoformat(),
        }


class InlineCitation(BaseModel):
    """An inline citation token mapping to an indexed SourceChunk."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    citation_id: str = Field(description="Unique citation token instance ID")
    chunk_id: str = Field(description="Target source chunk ID")
    source_doc: str = Field(default="", description="Target source document identifier")
    span_start: int = Field(ge=0, description="Character offset start in draft text")
    span_end: int = Field(ge=0, description="Character offset end in draft text")
    prefix_text: str = Field(default="", description="Preceding context or sentence excerpt")


class Perspective(BaseModel):
    """STORM-style perspective persona guiding inquiry, discovery, and critique."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    perspective_id: str = Field(default="", description="Unique identifier for the perspective")
    id: str = Field(default="", description="Alias for perspective_id")
    role: PerspectiveRole = Field(description="Standardized persona role archetype")
    title: str = Field(default="", description="Human-readable title for this perspective")
    name: str = Field(default="", description="Alias for title")
    description: str = Field(default="", description="Professional background and stance")
    domain: str = Field(default="", description="Field of expertise or point of view")
    focus_areas: list[str] = Field(
        default_factory=list,
        description="Key domain topics and themes to investigate",
    )
    prompt_directives: list[str] = Field(
        default_factory=list,
        description="Explicit instructions injected into agent prompts",
    )
    guiding_questions: list[str] = Field(
        default_factory=list,
        description="Initial investigative prompts",
    )
    key_inquiries: list[str] = Field(
        default_factory=list,
        description="Core analytical questions",
    )

    @model_validator(mode="after")
    def populate_aliases(self) -> "Perspective":
        # Ensure either perspective_id or id is populated
        pid = self.perspective_id or self.id or "perspective_default"
        title = self.title or self.name or "Perspective Title"
        object.__setattr__(self, "perspective_id", pid)
        object.__setattr__(self, "id", pid)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "name", title)
        return self


class DialogueTurn(BaseModel):
    """Single turn in a conversational research loop."""

    model_config = ConfigDict(frozen=True)

    turn_id: str = Field(..., description="Unique turn identifier")
    question: str = Field(..., min_length=1, description="Question asked by perspective agent")
    answer: str = Field(..., min_length=1, description="Answer provided by research retrieval agent")
    referenced_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Chunk IDs cited in this answer",
    )


class DialogueSession(BaseModel):
    """Full conversational research record for a perspective."""

    model_config = ConfigDict(frozen=True)

    perspective_id: str = Field(..., description="ID of the interviewing perspective")
    turns: list[DialogueTurn] = Field(default_factory=list, description="Sequence of research turns")
    synthesized_takeaways: list[str] = Field(
        default_factory=list,
        description="High-level insights extracted from this session",
    )


class ResearchMemory(BaseModel):
    """Aggregated knowledge repository from multi-perspective research."""

    model_config = ConfigDict(frozen=True)

    topic: str = Field(..., min_length=1, description="Main document research topic")
    sessions: list[DialogueSession] = Field(default_factory=list, description="Sessions across all perspectives")
    all_referenced_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Deduplicated chunk IDs referenced across all dialogues",
    )
    collected_takeaways: list[str] = Field(
        default_factory=list,
        description="Consolidated list of thematic takeaways",
    )


class OutlineNode(BaseModel):
    """Hierarchical section node in the document outline tree."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(min_length=1, description="Unique identifier for this outline node")
    title: str = Field(min_length=1, description="Section heading or outline title")
    section_id: str = Field(default="", description="Identifier linking to section draft")
    depth: int = Field(default=0, ge=0, description="Nesting level in the outline tree (0 = top-level)")
    description: str = Field(default="", description="Detailed narrative intent for this section")
    assigned_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Source chunks assigned to substantiate this section",
    )
    children: list[OutlineNode] = Field(
        default_factory=list,
        description="Sub-sections or nested child nodes",
    )
    order: int = Field(default=0, ge=0, description="Sequential ordering among siblings")
    subheadings: list[str] = Field(
        default_factory=list,
        description="Subheading titles",
    )

    @model_validator(mode="after")
    def populate_section_id(self) -> "OutlineNode":
        if not self.section_id:
            object.__setattr__(self, "section_id", self.node_id)
        return self

    @property
    def is_leaf(self) -> bool:
        """A node is a leaf if it has no children."""
        return len(self.children) == 0

    @property
    def children_ids(self) -> list[str]:
        """IDs of immediate child nodes."""
        return [c.node_id for c in self.children]

    def get_leaves(self) -> list[OutlineNode]:
        """Recursively get all leaf nodes in this subtree."""
        if self.is_leaf:
            return [self]
        leaves: list[OutlineNode] = []
        for child in self.children:
            leaves.extend(child.get_leaves())
        return leaves


class OutlineNodeInput(BaseModel):
    """One section of a caller-supplied outline.

    A caller-supplied outline is the operator's steer: the sections they name
    are the sections the manuscript is written to. It carries no chunk
    assignments and no ids, because the server assigns the passages — the
    operator says *what* the document should cover, and the corpus decides
    *what may be said* about it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, description="Section heading the draft is written to")
    description: str = Field(default="", description="What this section should cover")
    children: list["OutlineNodeInput"] = Field(
        default_factory=list,
        description="Nested sections under this one",
    )


OutlineNodeInput.model_rebuild()


class SectionDraft(BaseModel):
    """Complete draft content for an outline section with associated claims and citations."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(min_length=1, description="Identifier of the outline section")
    title: str = Field(min_length=1, description="Section heading")
    content: str = Field(default="", description="Raw section text content including inline citations")
    order: int = Field(default=0, ge=0, description="Linear sequence order within the manuscript")
    citations: list[CitationLineage] = Field(
        default_factory=list,
        description="Inline citation lineages mapped to this section",
    )
    claims: list[Claim] = Field(
        default_factory=list,
        description="Extracted propositions and verification states",
    )
    is_verified: bool = Field(
        default=False,
        description="True if all claims meet the entailment threshold",
    )
    status: SectionStatus = Field(
        default=SectionStatus.PENDING,
        description="Verification lifecycle status",
    )
    revision_count: int = Field(
        default=0,
        ge=0,
        description="Number of revision cycles applied",
    )
    critique: Optional[str] = Field(
        default=None,
        description="Targeted feedback for redrafting",
    )
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Explicit list of chunk IDs cited in content",
    )

    def to_wire(self) -> dict[str, Any]:
        """The section's wire form.

        The counterpart of ``kotlin/core/WireDecode.kt``'s ``sectionDraftFromWire``:
        the same keys, the same names, the same defaults and the same enum wire
        string, so the section can cross the NDJSON seam and be read back by the
        tier without a translation layer.

        Every key is always present.  An optional field travels as ``null``,
        which the decoder reads exactly like an absent key.
        """
        return {
            "section_id": self.section_id,
            "title": self.title,
            "content": self.content,
            "order": self.order,
            "citations": [citation.to_wire() for citation in self.citations],
            "claims": [claim.to_wire() for claim in self.claims],
            "is_verified": self.is_verified,
            "status": self.status.value,
            "revision_count": self.revision_count,
            "critique": self.critique,
            "cited_chunk_ids": list(self.cited_chunk_ids),
        }


class VerificationReport(BaseModel):
    """Formal audit scorecard summarizing claim verification outcomes for a section or document."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    report_id: str = Field(min_length=1, description="Unique identifier for the report")
    section_id: str = Field(min_length=1, description="Section or document identifier audited")
    total_claims: int = Field(ge=0, description="Total number of evaluated claims")
    entailed_claims: int = Field(ge=0, description="Count of entailed claims")
    contradicted_claims: int = Field(ge=0, description="Count of contradicted claims")
    ungrounded_claims: int = Field(ge=0, description="Count of ungrounded or neutral claims")
    precision: float = Field(
        ge=0.0,
        le=1.0,
        description="Citation precision ratio (entailed / total_claims)",
    )
    claims: list[Claim] = Field(default_factory=list, description="All individual audited claims")
    status: EntailmentStatus = Field(description="Aggregate entailment status")
    all_passed: bool = Field(description="True if zero contradictions or ungrounded claims exist")
    summary: str = Field(description="Human-readable synthesis of verification findings")
    evaluator: str = Field(
        default="",
        description="Which NLI evaluator produced these verdicts (e.g. 'cross-encoder/nli-deberta-v3-small')",
    )

    @property
    def f_beta(self) -> float:
        """Alias for precision/groundedness metric score."""
        return self.precision

    @property
    def ungrounded_count(self) -> int:
        """Alias for ungrounded_claims count."""
        return self.ungrounded_claims

    @property
    def contradiction_count(self) -> int:
        """Alias for contradicted_claims count."""
        return self.contradicted_claims


class VerificationScorecard(BaseModel):
    """Mathematical citation audit metrics and claim breakdowns."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    precision: float = Field(ge=0.0, le=1.0, description="Citation Precision (P_cite)")
    recall: float = Field(ge=0.0, le=1.0, description="Citation Recall (R_cite)")
    f_beta: float = Field(ge=0.0, le=1.0, description="Groundedness harmonic mean with beta weight")
    beta: float = Field(default=0.5, gt=0.0, description="Beta parameter weighting precision vs recall")
    total_claims: int = Field(ge=0, description="Total factual claims evaluated")
    supported_claims: int = Field(ge=0, description="Claims with ENTAILED status")
    ungrounded_claims: int = Field(ge=0, description="Claims lacking citations or premise support")
    contradicted_claims: int = Field(ge=0, description="Claims with CONTRADICTED status")
    neutral_claims: int = Field(ge=0, description="Claims with NEUTRAL status")
    details: list[Claim] = Field(default_factory=list, description="Claim-by-claim evaluation records")
    evaluator: str = Field(
        default="",
        description="Which NLI evaluator produced these verdicts; 'heuristic' means the cross-encoder was unavailable",
    )


class HITLReviewPayload(BaseModel):
    """Human-in-the-loop review decision payload.

    ``doc_id`` names the document the decision is about and is a **path**
    parameter of the route that takes this body, so it is optional here and the
    path is authoritative: a body that names a different document cannot
    redirect a decision to another run.  The route fills it in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(
        default="",
        description=(
            "Document the decision is about; the path is authoritative and the "
            "route fills this in"
        ),
    )
    approved: bool = Field(description="Whether manuscript was approved by human operator")
    feedback: Optional[str] = Field(default=None, description="Human critique or guidance for redrafting")
    modified_sections: Optional[list[SectionDraft]] = Field(
        default=None, description="Optional manual edits submitted by human"
    )


class SectionSaveRequest(BaseModel):
    """The operator's own text for one section of a document.

    This is what a word processor's "save" carries: the text the operator left
    on the page. It is deliberately narrower than ``SectionDraft`` — the
    operator supplies the *content*, and the server keeps the provenance the
    pipeline established for that section. A citation the pipeline verified
    still says where its passage came from after the operator has reworded the
    sentence around it, so discarding it would lose the audit trail the whole
    application exists to maintain.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="The section's markdown, as the operator left it")
    title: Optional[str] = Field(
        default=None,
        min_length=1,
        description="A new heading for the section; the current one is kept when absent",
    )
    order: Optional[int] = Field(
        default=None,
        ge=0,
        description="A new position in the document; the current one is kept when absent",
    )


class SectionSaveResult(BaseModel):
    """What the server stored for one section, and what it measured on it.

    The status and the scorecard are the honest account of the saved text.
    A section the operator rewrote is not thereby verified: it is approved by
    the person who wrote it, and the grounding measured on it is reported
    separately so the two are never confused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(description="The section that was saved")
    title: str = Field(description="The section's heading, as it now stands")
    content: str = Field(description="The text that was stored")
    status: SectionStatus = Field(description="The section's lifecycle status after the save")
    is_verified: bool = Field(description="Whether the saved text was measured and passed")
    scorecard: Optional[VerificationScorecard] = Field(
        default=None,
        description="The grounding measured on the saved text, when it could be measured",
    )


class WorkflowEvent(BaseModel):
    """SSE broadcast payload for frontend synchronization."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    event_type: EventType = Field(description="SSE event type classification")
    step: WorkflowStep = Field(description="Current operational state node")
    doc_id: str = Field(description="Document identifier")
    data: dict[str, Any] = Field(default_factory=dict, description="Arbitrary event data payload")


class TopicOutline(BaseModel):
    """Hierarchical outline of topics and sections forming a Directed Acyclic Graph."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1, description="Manuscript topic or prompt")
    nodes: list[OutlineNode] = Field(default_factory=list, description="Top-level outline nodes")

    def get_leaves(self) -> list[OutlineNode]:
        """Collect all leaf nodes across the entire outline hierarchy."""
        leaves: list[OutlineNode] = []
        for node in self.nodes:
            leaves.extend(node.get_leaves())
        return leaves

    def get_all_assigned_chunk_ids(self) -> list[str]:
        """Collect all distinct chunk IDs assigned across all outline nodes."""
        seen: set[str] = set()
        result: list[str] = []
        for leaf in self.get_leaves():
            for cid in leaf.assigned_chunk_ids:
                if cid not in seen:
                    seen.add(cid)
                    result.append(cid)
        return result

    def find_node(self, node_id: str) -> OutlineNode | None:
        """Find a node by ID anywhere in the hierarchy."""
        def _search(nodes: list[OutlineNode]) -> OutlineNode | None:
            for n in nodes:
                if n.node_id == node_id:
                    return n
                found = _search(n.children)
                if found is not None:
                    return found
            return None
        return _search(self.nodes)

    def validate_dag(self) -> None:
        """Validate DAG acyclicity, node uniqueness, and integrity."""
        from lmeditor.core.exceptions import OutlineGenerationError

        seen_ids: set[str] = set()
        visiting: set[str] = set()

        def _traverse(node: OutlineNode) -> None:
            if node.node_id in visiting:
                raise OutlineGenerationError(
                    f"Cycle detected in outline DAG at node '{node.node_id}'.",
                    node_id=node.node_id,
                    cycle_detected=True,
                )
            if node.node_id in seen_ids:
                raise OutlineGenerationError(
                    f"Duplicate node_id '{node.node_id}' detected in outline DAG.",
                    node_id=node.node_id,
                )
            visiting.add(node.node_id)
            for child in node.children:
                _traverse(child)
            visiting.remove(node.node_id)
            seen_ids.add(node.node_id)

        for root in self.nodes:
            _traverse(root)


class DocumentDraft(BaseModel):
    """Complete document draft with structured sections and provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    document_id: str = Field(min_length=1, description="Document identifier")
    title: str = Field(min_length=1, description="Document title")
    sections: list[SectionDraft] = Field(default_factory=list, description="Manuscript sections")
    status: DocumentStatus = Field(default=DocumentStatus.DRAFTING, description="Document lifecycle status")

    def to_wire(self) -> dict[str, Any]:
        """The draft's wire form.

        The counterpart of ``kotlin/core/WireDecode.kt``'s ``documentDraftFromWire``:
        the same keys, the same names and the same enum wire string, so the
        draft can cross the NDJSON seam and be read back by the tier without a
        translation layer.

        Every key is always present.
        """
        return {
            "document_id": self.document_id,
            "title": self.title,
            "sections": [section.to_wire() for section in self.sections],
            "status": self.status.value,
        }


class InlineEditRequest(BaseModel):
    """A targeted edit to one paragraph or section of a manuscript.

    This is the small-edit contract: the caller names a section, points at
    the text to change, and says what the change should achieve. The
    revision is grounded in the document's own sources, and the edit is
    rejected rather than applied when the revision cannot be supported.

    ``doc_id`` names the document being edited and is a **path** parameter of
    the route that takes this body, so it is optional here and the path is
    authoritative: a body that names a different document cannot redirect the
    edit.  The route fills it in before the request reaches the domain.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(
        default="",
        description=(
            "Identifier of the manuscript being edited; the path is "
            "authoritative and the route fills this in"
        ),
    )
    section_id: str = Field(min_length=1, description="Identifier of the section being edited")
    instruction: str = Field(
        min_length=1,
        description="What the edit should achieve, stated in plain language",
    )
    selected_text: str = Field(
        default="",
        description="The exact text the user selected, when the edit targets a selection",
    )
    scope: EditScope = Field(
        default=EditScope.PARAGRAPH,
        description="Whether the instruction applies to one paragraph or the whole section",
    )
    paragraph_index: int = Field(
        default=0,
        ge=0,
        description="Zero-based index of the paragraph within the section",
    )
    persist: bool = Field(
        default=True,
        description="Whether the accepted revision is written back to the document",
    )


class InlineEditResult(BaseModel):
    """The outcome of a targeted edit."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    scope: EditScope = Field(default=EditScope.PARAGRAPH)
    original_text: str = Field(default="", description="The text before the edit")
    revised_text: str = Field(default="", description="The proposed text after the edit")
    applied: bool = Field(
        default=False,
        description="True when the revision was accepted and persisted",
    )
    reason: str = Field(default="", description="Why the revision was accepted or rejected")
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
        description="Source chunks the revision relies on",
    )
    claims: list[Claim] = Field(default_factory=list, description="Claims in the revised text")
    scorecard: Optional[VerificationScorecard] = Field(
        default=None,
        description="Verification scorecard for the revised text",
    )
    section: Optional[SectionDraft] = Field(
        default=None,
        description="The section after the edit, when the edit was persisted",
    )


class Evidence(BaseModel):
    """A single piece of evidence used to judge a claim."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source: EvidenceSource = Field(description="Whether the evidence came from the corpus or the web")
    chunk_id: str = Field(default="", description="Corpus chunk identifier, when the source is the corpus")
    url: str = Field(default="", description="Web address, when the source is the web")
    title: str = Field(default="", description="Human-readable title of the evidence")
    excerpt: str = Field(default="", description="The passage that supports or refutes the claim")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Relevance of the evidence to the claim")


class ClaimVerdict(BaseModel):
    """The verdict on one claim together with the evidence behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(default="")
    status: EntailmentStatus = Field(description="Entailment status of the claim against its evidence")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence in the verdict")
    evidence: list[Evidence] = Field(default_factory=list, description="Evidence considered for this claim")
    reasoning: str = Field(default="", description="Why this verdict was reached")


class FactCheckRequest(BaseModel):
    """A request to fact-check a passage against the corpus, the web, or both.

    ``doc_id`` names the document whose sources provide the evidence and is a
    **path** parameter of the route that takes this body, so it is optional
    here and the path is authoritative: a body that names a different document
    cannot redirect the check.  The route fills it in.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(
        default="",
        description=(
            "Document whose sources provide the evidence; the path is "
            "authoritative and the route fills this in"
        ),
    )
    text: str = Field(min_length=1, description="The passage to fact-check")
    section_id: str = Field(default="", description="Section the passage belongs to, when known")
    include_web: bool = Field(
        default=False,
        description="Whether the open web should also be consulted",
    )
    max_evidence: int = Field(
        default=5,
        ge=1,
        le=25,
        description="Maximum number of evidence items retrieved per claim",
    )


class FactCheckReport(BaseModel):
    """The result of fact-checking a passage."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(min_length=1)
    text: str = Field(default="", description="The passage that was checked")
    verdict: FactCheckVerdict = Field(
        default=FactCheckVerdict.INCONCLUSIVE,
        description="Aggregate verdict over every claim in the passage",
    )
    claims: list[ClaimVerdict] = Field(default_factory=list, description="Per-claim verdicts")
    scorecard: Optional[VerificationScorecard] = Field(
        default=None,
        description="Verification scorecard for the passage",
    )
    web_searched: bool = Field(
        default=False,
        description="Whether the web was consulted for this check",
    )
    summary: str = Field(default="", description="Human-readable synthesis of the findings")


class BookSection(BaseModel):
    """One section of a book, stored and edited in isolation."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(min_length=1)
    title: str = Field(default="")
    order: int = Field(default=0, ge=0, description="Position of the section in the book")
    word_count: int = Field(default=0, ge=0)
    char_count: int = Field(default=0, ge=0)
    chunk_ids: list[str] = Field(default_factory=list, description="Corpus chunks indexed for this section")


class BookManifest(BaseModel):
    """The index of a book: enough to navigate it without loading it.

    The manifest holds metadata and a section index, never the body text of
    the book. That is what lets a book of any size be opened, navigated and
    edited one section at a time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    book_id: str = Field(min_length=1)
    title: str = Field(default="")
    section_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    created_at: datetime
    modified_at: datetime
    sections: list[BookSection] = Field(default_factory=list, description="The section index")
    brief: str = Field(
        default="",
        description="Bounded running digest of what the book contains so far",
    )


class WorkingSetSection(BaseModel):
    """A section as it appears inside a bounded working set."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    section_id: str = Field(min_length=1)
    title: str = Field(default="")
    order: int = Field(default=0, ge=0)
    role: WorkingSetRole = Field(default=WorkingSetRole.TARGET)
    content: str = Field(default="", description="The section text, present only for loaded sections")


class WorkingSet(BaseModel):
    """The bounded context used to edit one section of a book.

    A working set is the whole of the book that the language model ever
    sees: the target section, its immediate neighbours, the passages that
    retrieval considers relevant, and the running brief. Everything else
    stays on disk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    book_id: str = Field(min_length=1)
    target_section_id: str = Field(min_length=1)
    sections: list[WorkingSetSection] = Field(default_factory=list)
    passages: list[SourceChunk] = Field(default_factory=list)
    brief: str = Field(default="")
    context_chars: int = Field(default=0, ge=0, description="Total size of the working set in characters")


class BookSectionEditRequest(BaseModel):
    """A request to edit one section of a book in place.

    ``book_id`` and ``section_id`` name the book and the section and are
    **path** parameters of the route that takes this body, so they are
    optional here and the path is authoritative: a body that names a
    different book cannot redirect the edit.  The route fills them in.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    book_id: str = Field(
        default="",
        description=(
            "Book the section belongs to; the path is authoritative and the "
            "route fills this in"
        ),
    )
    section_id: str = Field(
        default="",
        description=(
            "Section being edited; the path is authoritative and the route "
            "fills this in"
        ),
    )
    instruction: str = Field(min_length=1, description="What the edit should achieve")
    selected_text: str = Field(default="", description="The exact text the user selected")
    scope: EditScope = Field(default=EditScope.PARAGRAPH)
    paragraph_index: int = Field(default=0, ge=0)
    persist: bool = Field(default=True, description="Whether the accepted revision is written back")


class BookSectionSaveRequest(BaseModel):
    """The operator's own text for one section of a book.

    A book is edited one section at a time, and the operator's own hand has to
    be able to do that editing. The instruction-based edit asks the machine to
    rewrite; this carries what the operator wrote themselves.

    The whitespace is deliberately not stripped: the text is the operator's,
    and a word processor stores what was typed.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="The section's markdown, as the operator left it")


class BookSectionSaveResult(BaseModel):
    """What the server stored for one section of a book.

    ``chunk_count`` is how many passages the section is now indexed as. The
    section's text changed, so its passages changed with it, and the operator
    is told the new number rather than left to guess whether the index caught
    up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str = Field(description="The section that was saved")
    title: str = Field(default="", description="The section's heading")
    content: str = Field(default="", description="The text that was stored")
    word_count: int = Field(default=0, ge=0, description="Words in the stored text")
    char_count: int = Field(default=0, ge=0, description="Characters in the stored text")
    chunk_count: int = Field(default=0, ge=0, description="Passages the section is now indexed as")


__all__ = [
    "SourceChunk",
    "Claim",
    "CitationLineage",
    "InlineCitation",
    "Perspective",
    "DialogueTurn",
    "DialogueSession",
    "ResearchMemory",
    "OutlineNode",
    "OutlineNodeInput",
    "TopicOutline",
    "SectionDraft",
    "DocumentDraft",
    "SectionStatus",
    "VerificationReport",
    "VerificationScorecard",
    "HITLReviewPayload",
    "WorkflowEvent",
    "InlineEditRequest",
    "InlineEditResult",
    "Evidence",
    "ClaimVerdict",
    "FactCheckRequest",
    "FactCheckReport",
    "BookSection",
    "BookManifest",
    "WorkingSetSection",
    "WorkingSet",
    "BookSectionEditRequest",
    "BookSectionSaveRequest",
    "BookSectionSaveResult",
]

