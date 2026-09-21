"""Domain types, Enums, and Protocol interfaces for LMeditor."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from lmeditor.core.schemas import SourceChunk


class EntailmentStatus(str, Enum):
    """NLI entailment verification status."""

    ENTAILED = "entailed"
    CONTRADICTED = "contradicted"
    NEUTRAL = "neutral"
    UNGROUNDED = "ungrounded"

    # Backward-compatibility aliases
    ENTAILMENT = "entailed"
    CONTRADICTION = "contradicted"


class DocumentStatus(str, Enum):
    """Lifecycle status of a generated document draft."""

    DISCOVERY = "discovery"
    OUTLINING = "outlining"
    DRAFTING = "drafting"
    VERIFYING = "verifying"
    REVISING = "revising"
    READY_FOR_HITL = "ready_for_hitl"
    COMPLETED = "completed"
    FAILED = "failed"


class SectionStatus(str, Enum):
    """Lifecycle status of an individual manuscript section."""

    PENDING = "pending"
    DRAFTING = "drafting"
    VERIFIED = "verified"
    NEEDS_REVISION = "needs_revision"
    APPROVED = "approved"

    # Backward-compatibility and operational aliases
    DRAFTED = "drafting"
    FLAGGED = "needs_revision"


class WorkflowStep(str, Enum):
    """Operational steps of the LangGraph state machine."""

    INGEST = "ingest"
    DISCOVER = "discover"
    OUTLINE = "outline"
    DRAFT_SECTIONS = "draft_sections"
    VERIFY_CITATIONS = "verify_citations"
    CRITIQUE_REVISION = "critique_revision"
    MACRO_POLISH = "macro_polish"
    HITL_GATE = "hitl_gate"
    COMPLETE = "complete"


class EventType(str, Enum):
    """Server-Sent Event (SSE) payload classification."""

    NODE_START = "node_start"
    NODE_COMPLETE = "node_complete"
    TOKEN_DELTA = "token_delta"
    SCORECARD_UPDATE = "scorecard_update"
    HITL_REQUEST = "hitl_request"
    WORKFLOW_COMPLETE = "workflow_complete"
    WORKFLOW_ERROR = "workflow_error"


class PerspectiveRole(str, Enum):
    """Persona archetype for STORM multi-perspective discovery inquiry."""

    DOMAIN_EXPERT = "domain_expert"
    CRITICAL_SKEPTIC = "critical_skeptic"
    SYNTHESIZER = "synthesizer"
    GENERAL_READER = "general_reader"
    FACT_CHECKER = "fact_checker"
    SKEPTIC = "skeptic"
    PRACTITIONER = "practitioner"
    REGULATOR = "regulator"
    COMPARATIVE_ANALYST = "comparative_analyst"
    GENERAL_AUDIENCE = "general_audience"


class OutlineNodeType(str, Enum):
    """Structural classification of nodes in the outline DAG."""

    ROOT = "root"
    SECTION = "section"
    SUBSECTION = "subsection"
    LEAF = "leaf"


class CitationFormat(str, Enum):
    """Syntactic format for inline citations."""

    BRACKETED_ID = "bracketed_id"  # e.g., [chunk_123]
    BRACKETED_NUMBER = "bracketed_number"  # e.g., [1]


@runtime_checkable
class ChunkerProtocol(Protocol):
    """Protocol for text segmentation into traceable source chunks."""

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Any]: ...


@runtime_checkable
class DenseEmbedderProtocol(Protocol):
    """Protocol for dense vector embedding generation."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class SparseSearchProtocol(Protocol):
    """Protocol for sparse keyword/lexical search (e.g., BM25)."""

    def index(self, chunks: list[Any]) -> None: ...

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]: ...


@runtime_checkable
class HybridIndexerProtocol(Protocol):
    """Protocol for hybrid (dense vector + sparse BM25) index with RRF fusion."""

    def index(self, chunks: list[Any]) -> None: ...

    def get_chunk(self, chunk_id: str) -> Any: ...

    def search(
        self,
        query: str,
        top_k: int = 10,
        rrf_k: int = 60,
        alpha: float = 0.5,
    ) -> list[tuple[Any, float]]: ...


@runtime_checkable
class ProvenanceIndexerProtocol(Protocol):
    """Protocol representing a hybrid provenance search indexer."""

    def query(self, query: str, top_k: int = 5) -> Sequence[SourceChunk]: ...

    def get_chunk(self, chunk_id: str) -> SourceChunk | None: ...

    def has_chunk(self, chunk_id: str) -> bool: ...

    def get_all_chunk_ids(self) -> Sequence[str]: ...


@runtime_checkable
class NLIEvaluatorProtocol(Protocol):
    """Structural protocol for premise-hypothesis entailment evaluators."""

    async def evaluate(self, premise: str, hypothesis: str) -> tuple[EntailmentStatus, float]: ...


@runtime_checkable
class MetricCallable(Protocol):
    """Protocol for DSPy optimization metrics."""

    def __call__(self, gold: Any, pred: Any, trace: Any = None) -> float | bool: ...


__all__ = [
    "EntailmentStatus",
    "DocumentStatus",
    "SectionStatus",
    "WorkflowStep",
    "EventType",
    "PerspectiveRole",
    "OutlineNodeType",
    "CitationFormat",
    "ChunkerProtocol",
    "DenseEmbedderProtocol",
    "SparseSearchProtocol",
    "HybridIndexerProtocol",
    "ProvenanceIndexerProtocol",
    "NLIEvaluatorProtocol",
    "MetricCallable",
]
