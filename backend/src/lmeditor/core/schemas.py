"""Immutable, strictly validated Pydantic v2 data models for LMeditor core and provenance."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lmeditor.core.types import (
    DocumentStatus,
    EntailmentStatus,
    EventType,
    OutlineNodeType,
    PerspectiveRole,
    SectionStatus,
    WorkflowStep,
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


class HITLReviewPayload(BaseModel):
    """Human-in-the-loop review decision payload."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    doc_id: str = Field(description="Document identifier")
    approved: bool = Field(description="Whether manuscript was approved by human operator")
    feedback: Optional[str] = Field(default=None, description="Human critique or guidance for redrafting")
    modified_sections: Optional[list[SectionDraft]] = Field(
        default=None, description="Optional manual edits submitted by human"
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
    "TopicOutline",
    "SectionDraft",
    "DocumentDraft",
    "SectionStatus",
    "VerificationReport",
    "VerificationScorecard",
    "HITLReviewPayload",
    "WorkflowEvent",
]

