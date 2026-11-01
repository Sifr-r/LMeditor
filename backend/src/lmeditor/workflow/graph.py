"""LangGraph compiled state machine for the LMeditor writing harness."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from lmeditor.core.exceptions import InvalidStateTransitionError
from lmeditor.core.schemas import OutlineNodeInput, SourceChunk, WorkflowEvent
from lmeditor.core.types import EntailmentStatus, EventType, WorkflowStep
from lmeditor.verification.citation_checker import (
    compute_citation_precision,
    compute_citation_recall,
    compute_groundedness_f_beta,
)
from lmeditor.workflow.edges import (
    MAX_SECTION_RETRIES,
    route_after_hitl,
    route_after_verification,
)
from lmeditor.workflow.nodes import (
    critique_and_refine_node,
    draft_sections_node,
    guided_research_node,
    hitl_checkpoint_node,
    ingest_sources_node,
    macro_cohesion_node,
    outline_synthesis_node,
    perspective_discovery_node,
    verify_citations_node,
)
from lmeditor.workflow.state import HarnessState, create_initial_harness_state

#: Status returned when the graph suspends at the human-in-the-loop gate.
AWAITING_HUMAN_REVIEW_STATUS = "awaiting_human_review"

#: Key LangGraph uses for an interrupt chunk in ``stream_mode="updates"``.
_INTERRUPT_KEY = "__interrupt__"

# Graph node name -> workflow step published on the SSE stream.
_NODE_STEPS: dict[str, WorkflowStep] = {
    "ingest_sources": WorkflowStep.INGEST,
    "discover_perspectives": WorkflowStep.DISCOVER,
    "guided_research": WorkflowStep.GUIDED_RESEARCH,
    "synthesize_outline": WorkflowStep.OUTLINE,
    "draft_sections": WorkflowStep.DRAFT_SECTIONS,
    "verify_citations": WorkflowStep.VERIFY_CITATIONS,
    "critique_and_refine": WorkflowStep.CRITIQUE_REVISION,
    "macro_cohesion": WorkflowStep.MACRO_POLISH,
    "hitl_checkpoint": WorkflowStep.HITL_GATE,
}

_ENTRY_NODE = "ingest_sources"

_TOKEN_PATTERN = re.compile(r"\S+\s*")

#: Compiled graphs (and therefore their checkpointers) kept alive between
#: ``run_workflow`` suspending at the human-in-the-loop gate and the caller
#: resuming it. A LangGraph interrupt can only be resumed against the exact graph
#: object that wrote the checkpoint, so this registry is what makes the resume
#: half of the lifecycle reachable from outside this module.
#:
#: Keyed by ``thread_id`` (the ``doc_id``). Entries are evicted as soon as a run
#: reaches a terminal state, so the mapping cannot grow without bound. Safe on a
#: single asyncio event loop: every mutation is a plain dict operation with no
#: ``await`` in between.
_WORKFLOW_REGISTRY: dict[str, Any] = {}


@dataclass(frozen=True)
class _StreamOutcome:
    """What one pass of the graph produced."""

    interrupted: bool = False
    interrupt_payload: Any = None
    completed_nodes: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class EventSink(Protocol):
    """Structural sink for workflow progress events.

    ``lmeditor.server.WorkflowSession`` satisfies this protocol; it is declared
    here rather than imported so that ``lmeditor.workflow`` never depends on the
    transport layer.
    """

    async def publish_event(self, event: WorkflowEvent) -> None: ...


def build_lmeditor_graph(checkpointer: Any | None = None) -> Any:
    """Build and compile the cyclic LMeditor state machine."""
    workflow = StateGraph(HarnessState)

    # Register operational nodes
    workflow.add_node("ingest_sources", ingest_sources_node)
    workflow.add_node("discover_perspectives", perspective_discovery_node)
    workflow.add_node("guided_research", guided_research_node)
    workflow.add_node("synthesize_outline", outline_synthesis_node)
    workflow.add_node("draft_sections", draft_sections_node)
    workflow.add_node("verify_citations", verify_citations_node)
    workflow.add_node("critique_and_refine", critique_and_refine_node)
    workflow.add_node("macro_cohesion", macro_cohesion_node)
    workflow.add_node("hitl_checkpoint", hitl_checkpoint_node)

    # Define linear execution edges
    workflow.set_entry_point("ingest_sources")
    workflow.add_edge("ingest_sources", "discover_perspectives")
    workflow.add_edge("discover_perspectives", "guided_research")
    workflow.add_edge("guided_research", "synthesize_outline")
    workflow.add_edge("synthesize_outline", "draft_sections")
    workflow.add_edge("draft_sections", "verify_citations")

    # Define cyclic critique and verification routing
    workflow.add_conditional_edges(
        "verify_citations",
        route_after_verification,
        {
            "all_verified": "macro_cohesion",
            "needs_refinement": "critique_and_refine",
            "hitl_required": "hitl_checkpoint",
        },
    )

    # Redraft loop: critique routes back to drafting
    workflow.add_edge("critique_and_refine", "draft_sections")

    # Human-in-the-loop decision routing
    workflow.add_conditional_edges(
        "hitl_checkpoint",
        route_after_hitl,
        {
            "proceed_to_macro": "macro_cohesion",
            "terminate": END,
        },
    )

    # Macro cohesion finishes the document
    workflow.add_edge("macro_cohesion", END)

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)


def _iter_text_deltas(text: str, max_chars: int = 160) -> Iterator[str]:
    """Split drafted text into appendable deltas without losing characters."""
    buffer = ""
    for token in _TOKEN_PATTERN.findall(text):
        while len(token) > max_chars:
            if buffer:
                yield buffer
                buffer = ""
            yield token[:max_chars]
            token = token[max_chars:]
        if len(buffer) + len(token) > max_chars:
            yield buffer
            buffer = ""
        buffer += token
    if buffer:
        yield buffer


def _successor_node(node_name: str, state: HarnessState) -> str | None:
    """Predict which node runs after ``node_name`` given the post-update state.

    Used only to emit a truthful NODE_START before the next node's NODE_COMPLETE
    arrives; the actual routing decision stays owned by ``workflow.edges``.
    """
    successors: dict[str, str] = {
        "ingest_sources": "discover_perspectives",
        "discover_perspectives": "guided_research",
        "guided_research": "synthesize_outline",
        "synthesize_outline": "draft_sections",
        "draft_sections": "verify_citations",
        "critique_and_refine": "draft_sections",
    }
    if node_name in successors:
        return successors[node_name]
    if node_name == "verify_citations":
        route = route_after_verification(state)
        if route == "all_verified":
            return "macro_cohesion"
        if route == "hitl_required":
            return "hitl_checkpoint"
        return "critique_and_refine"
    if node_name == "hitl_checkpoint":
        return "macro_cohesion" if route_after_hitl(state) == "proceed_to_macro" else None
    return None


def _scorecard_payload(section: Any) -> dict[str, Any]:
    """Rebuild a VerificationScorecard-shaped payload from an audited section."""
    claims = list(getattr(section, "claims", []) or [])

    def _count(status: EntailmentStatus) -> int:
        return sum(1 for c in claims if getattr(c, "entailment_status", None) == status)

    total = len(claims)
    supported = _count(EntailmentStatus.ENTAILED)
    contradicted = _count(EntailmentStatus.CONTRADICTED)
    neutral = _count(EntailmentStatus.NEUTRAL)
    ungrounded = _count(EntailmentStatus.UNGROUNDED)

    cited = [c for c in claims if getattr(c, "cited_chunk_ids", None)]
    supported_cited = sum(1 for c in cited if getattr(c, "entailment_status", None) == EntailmentStatus.ENTAILED)
    precision = compute_citation_precision(supported_cited, len(cited), total)
    recall = compute_citation_recall(supported, total)
    f_beta = compute_groundedness_f_beta(precision, recall, beta=0.5)

    return {
        "precision": precision,
        "recall": recall,
        "f_beta": f_beta,
        "total_claims": total,
        "supported_claims": supported,
        "ungrounded_claims": ungrounded,
        "contradicted_claims": contradicted,
        "neutral_claims": neutral,
    }


def build_document_scorecard(sections: Sequence[Any]) -> dict[str, Any]:
    """Aggregate every audited section into one document-level scorecard.

    The previous implementation published ``scorecard: None`` on the terminal
    event, so the client had nothing truthful to display and defaulted to a
    fabricated 100%. This computes the real aggregate.

    A section that was never written contributes one unsupported unit to the
    document. Without that, a document missing a section would still report
    ``f_beta = 1.0`` -- every claim that exists is entailed -- and the client
    would show a perfect grounding score for a document that is incomplete.
    The count is kept consistent: ``total_claims`` still equals the sum of the
    four status counts.
    """
    claims = [c for section in sections for c in (getattr(section, "claims", []) or [])]

    def _count(status: EntailmentStatus) -> int:
        return sum(1 for c in claims if getattr(c, "entailment_status", None) == status)

    unwritten = sum(
        1
        for section in sections
        if not str(getattr(section, "content", "") or "").strip()
    )

    total = len(claims) + unwritten
    supported = _count(EntailmentStatus.ENTAILED)
    contradicted = _count(EntailmentStatus.CONTRADICTED)
    neutral = _count(EntailmentStatus.NEUTRAL)
    ungrounded = _count(EntailmentStatus.UNGROUNDED) + unwritten

    cited = [c for c in claims if getattr(c, "cited_chunk_ids", None)]
    supported_cited = sum(1 for c in cited if getattr(c, "entailment_status", None) == EntailmentStatus.ENTAILED)
    precision = compute_citation_precision(supported_cited, len(cited), total)
    recall = compute_citation_recall(supported, total)
    f_beta = compute_groundedness_f_beta(precision, recall, beta=0.5)

    verified_sections = [s for s in sections if getattr(s, "is_verified", False)]
    unverified_ids = [
        str(getattr(s, "section_id", "")) for s in sections if not getattr(s, "is_verified", False)
    ]

    return {
        "precision": precision,
        "recall": recall,
        "f_beta": f_beta,
        "total_claims": total,
        "supported_claims": supported,
        "ungrounded_claims": ungrounded,
        "contradicted_claims": contradicted,
        "neutral_claims": neutral,
        "total_sections": len(sections),
        "verified_sections": len(verified_sections),
        "unverified_section_ids": unverified_ids,
        "all_passed": bool(sections) and not unverified_ids,
    }


def _model_payload(obj: Any) -> dict[str, Any]:
    """Serialise a pydantic model for the wire, falling back to ``dict()``."""
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:  # pragma: no cover - non-pydantic model
            payload = dump()
        if isinstance(payload, dict):
            return {str(key): value for key, value in payload.items()}
        return {"value": payload}
    if isinstance(obj, dict):
        return dict(obj)
    return {"value": str(obj)}


def _count_tokens(state: Mapping[str, Any]) -> int:
    """Approximate the token count of the manuscript assembled so far.

    A whitespace token count is an approximation, not a tokenizer count; it is
    published as a run metric so the status bar can show *that* the run is
    producing text, never as a claim about a specific model's tokenisation.
    """
    total = 0
    for draft in (state.get("section_drafts") or {}).values():
        content = getattr(draft, "content", "") or ""
        total += len(content.split())
    return total


def _chunk_payload(chunk: Any) -> dict[str, Any]:
    """Render a chunk in the canonical wire vocabulary.

    The field names are the ones `lmeditor.core.schemas.SourceChunk` declares,
    so a consumer on either side of the seam reads the chunk with the same
    model the other side wrote it with. The aliases the desktop client used
    to need -- ``id``, ``content``, ``sha256`` -- are gone: no client in this
    repository reads them any more, and a payload that carries two names for
    one value is a payload whose meaning is ambiguous.
    """
    chunk_id = str(getattr(chunk, "chunk_id", ""))
    text = str(getattr(chunk, "text", ""))
    char_start = int(getattr(chunk, "char_start", 0) or 0)
    char_end = int(getattr(chunk, "char_end", 0) or 0)
    return {
        "chunk_id": chunk_id,
        "document_id": str(getattr(chunk, "document_id", "")),
        "text": text,
        "char_start": char_start,
        "char_end": char_end,
        "byte_start": int(getattr(chunk, "byte_start", 0) or 0),
        "byte_end": int(getattr(chunk, "byte_end", 0) or 0),
        "token_count": int(getattr(chunk, "token_count", 0) or 0),
        "start_char": char_start,
        "end_char": char_end,
        "metadata": dict(getattr(chunk, "metadata", {}) or {}),
    }


def _perspective_payload(perspective: Any) -> dict[str, Any]:
    """Render a perspective in the canonical wire vocabulary.

    The field names are the ones `lmeditor.core.schemas.Perspective` declares.
    The desktop client's extras -- ``avatar_symbol``, ``is_selected`` -- and
    its ``focus_queries`` spelling are gone: the questions a persona is asked
    are ``guiding_questions``, and a consumer that has to guess which of two
    spellings is authoritative is a consumer that will guess wrong.
    """
    role_value = getattr(perspective, "role", "")
    role = getattr(role_value, "value", role_value)
    role = str(role)
    queries = (
        list(getattr(perspective, "guiding_questions", []) or [])
        or list(getattr(perspective, "key_inquiries", []) or [])
        or list(getattr(perspective, "focus_areas", []) or [])
    )
    perspective_id = str(
        getattr(perspective, "perspective_id", "") or getattr(perspective, "id", "")
    )
    title = str(getattr(perspective, "title", "") or getattr(perspective, "name", ""))
    return {
        "perspective_id": perspective_id,
        "id": perspective_id,
        "role": role,
        "title": title,
        "name": title,
        "description": str(getattr(perspective, "description", "")),
        "domain": str(getattr(perspective, "domain", "")),
        "focus_areas": [str(f) for f in (getattr(perspective, "focus_areas", []) or [])],
        "prompt_directives": [
            str(d) for d in (getattr(perspective, "prompt_directives", []) or [])
        ],
        "guiding_questions": [str(q) for q in queries],
        "key_inquiries": [
            str(q) for q in (getattr(perspective, "key_inquiries", []) or [])
        ],
    }


def _outline_payload(node: Any) -> dict[str, Any]:
    """Render one outline node in the canonical wire vocabulary.

    The field names are the ones `lmeditor.core.schemas.OutlineNode`
    declares: ``node_id``, ``description``, ``children`` and a zero-based
    ``depth``. The desktop client's spelling -- ``id``, ``section_goal``,
    ``subsections``, a one-based ``level`` -- is gone, so the tree a consumer
    receives is the tree the model declares.
    """
    depth = int(getattr(node, "depth", 0) or 0)
    node_id = str(getattr(node, "node_id", "") or "")
    return {
        "node_id": node_id,
        "title": str(getattr(node, "title", "") or ""),
        "section_id": str(getattr(node, "section_id", "") or node_id),
        "depth": depth,
        "description": str(getattr(node, "description", "") or ""),
        "assigned_chunk_ids": [
            str(c) for c in getattr(node, "assigned_chunk_ids", []) or []
        ],
        "children": [_outline_payload(c) for c in getattr(node, "children", []) or []],
        "order": int(getattr(node, "order", 0) or 0),
        "subheadings": [str(s) for s in getattr(node, "subheadings", []) or []],
    }


def _outline_tree_payload(outline: Any) -> list[dict[str, Any]]:
    """Render a whole outline as the list of its top-level nodes.

    ``generate_outline`` returns a ``TopicOutline`` — a *container* holding a
    topic and a list of top-level nodes — not an ``OutlineNode``. Rendering the
    container as though it were a node produced a payload with an empty id,
    an empty title and no children, which the consumer's parser rejects, so
    the outline panel never received an outline at all.
    """
    nodes = getattr(outline, "nodes", None)
    if nodes is None and isinstance(outline, dict):
        nodes = outline.get("nodes")
    if nodes is None:
        # A single node rather than a tree.
        return [_outline_payload(outline)]
    return [_outline_payload(node) for node in nodes]



async def _publish(
    sink: EventSink | None,
    doc_id: str,
    step: WorkflowStep,
    event_type: EventType,
    data: dict[str, Any],
) -> None:
    """Publish one event when a sink is attached, tolerating a partial sink."""
    if sink is None:
        return
    payload = {"step": step.value, **data}
    try:
        await sink.publish_event(
            WorkflowEvent(event_type=event_type, step=step, doc_id=doc_id, data=payload)
        )
    except Exception as exc:  # pragma: no cover - transport failures must not kill the graph
        import logging

        logging.getLogger(__name__).warning("Failed to publish %s event: %s", event_type.value, exc)


def _extract_interrupt_payload(chunk: Any) -> Any:
    """Pull the interrupt payload out of a ``__interrupt__`` stream chunk."""
    value = chunk[_INTERRUPT_KEY]
    if isinstance(value, (list, tuple)) and value:
        interrupt_obj = value[0]
        return getattr(interrupt_obj, "value", interrupt_obj)
    if isinstance(value, (list, tuple)):
        return None
    return getattr(value, "value", value)


def _thread_config(doc_id: str) -> dict[str, Any]:
    """Build the LangGraph config that binds a run to its checkpoint thread."""
    return {"configurable": {"thread_id": doc_id}}


def _effective_max_retries(state: Mapping[str, Any], fallback: int) -> int:
    """Read back the retry budget the run actually enforced.

    Prefers the value the checkpoint carries, because a resumed run reads it
    from the state written by the first pass rather than from a caller that is
    no longer in the frame. ``fallback`` covers a checkpoint written before the
    key existed.
    """
    value = state.get("max_retries")
    return value if isinstance(value, int) else fallback


def _finalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Attach the ``sections`` post-processing every caller relies on."""
    res = dict(state)
    section_drafts = res.get("section_drafts", {})
    if isinstance(section_drafts, dict):
        res["sections"] = sorted(section_drafts.values(), key=lambda d: getattr(d, "order", 0))
    elif isinstance(section_drafts, list):
        res["sections"] = section_drafts
    else:
        res["sections"] = []
    return res


def release_workflow(thread_id: str) -> bool:
    """Drop the compiled graph held for ``thread_id``.

    Use this for a document that was abandoned or discarded while still
    suspended, so the registry does not retain its checkpointer. Returns whether
    an entry was actually removed.

    The **retrieval index is not released here**, because it does not belong to
    the run.  It belongs to the document: the inline editor grounds a revision
    in it and the fact-checker compares a claim against it, and both are used
    after the run has ended.  A run that ended and took the document's sources
    with it left a document that could be read but neither edited nor
    fact-checked.  The index is released when the document is discarded, in
    :meth:`lmeditor.server.session.SessionManager.remove_session`.
    """
    return _WORKFLOW_REGISTRY.pop(thread_id, None) is not None


def registered_workflow_threads() -> tuple[str, ...]:
    """Return the ``thread_id``s whose graphs are currently retained."""
    return tuple(_WORKFLOW_REGISTRY)


async def _stream_graph(
    graph: Any,
    sink: EventSink | None,
    doc_id: str,
    config: dict[str, Any],
    payload: Any,
    seed: dict[str, Any] | None,
    announce_entry: bool,
) -> _StreamOutcome:
    """Drive one pass of ``graph`` and publish its transitions to ``sink``.

    ``payload`` is the value handed to ``astream``: either the initial state (a
    fresh run) or a ``Command(resume=...)`` (a resume). ``seed`` seeds the local
    routing prediction; when it is None the current checkpointed state is read
    back from the graph.
    """
    if seed is None:
        snapshot = await graph.aget_state(config)
        local_state: dict[str, Any] = dict(snapshot.values or {})
    else:
        local_state = dict(seed)

    completed: set[str] = set()
    interrupted = False
    interrupt_payload: Any = None

    if announce_entry:
        await _publish(sink, doc_id, _NODE_STEPS[_ENTRY_NODE], EventType.NODE_START, {})

    async for chunk in graph.astream(payload, config=config, stream_mode="updates"):
        if _INTERRUPT_KEY in chunk:
            interrupted = True
            interrupt_payload = _extract_interrupt_payload(chunk)
            break

        for node_name, update in chunk.items():
            step = _NODE_STEPS.get(node_name)
            if step is None:
                continue
            node_update: dict[str, Any] = dict(update) if isinstance(update, dict) else {}
            local_state.update(node_update)
            completed.add(node_name)

            if node_name == "ingest_sources":
                chunks = list((node_update.get("source_chunks") or {}).values())
                if chunks:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.CHUNKS,
                        {"chunks": [_chunk_payload(c) for c in chunks]},
                    )

            if node_name == "discover_perspectives":
                perspectives = list(node_update.get("perspectives") or [])
                if perspectives:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.PERSPECTIVES,
                        {"perspectives": [_perspective_payload(p) for p in perspectives]},
                    )

            if node_name == "guided_research":
                # The guided findings are published whether or not the stage
                # found anything: a panel that is empty is indistinguishable
                # from a stage that failed silently, so a stage that did not
                # run says why.
                notes = [str(note) for note in (node_update.get("research_notes") or [])]
                memory = node_update.get("research_memory")
                referenced = list(memory.all_referenced_chunk_ids) if memory is not None else []
                await _publish(
                    sink,
                    doc_id,
                    WorkflowStep.GUIDED_RESEARCH,
                    EventType.RESEARCH_NOTES,
                    {"notes": notes, "referenced_chunk_ids": referenced},
                )

            if node_name == "synthesize_outline":
                outline_root = node_update.get("outline_root")
                outline_nodes = list(node_update.get("outline_nodes") or [])
                if outline_root is not None:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.OUTLINE,
                        {"outline": _outline_tree_payload(outline_root)},
                    )
                elif outline_nodes:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.OUTLINE,
                        {"outline": [_outline_payload(n) for n in outline_nodes]},
                    )

            if node_name == "draft_sections":
                for sec_id, section in node_update.get("section_drafts", {}).items():
                    for delta in _iter_text_deltas(getattr(section, "content", "") or ""):
                        await _publish(
                            sink,
                            doc_id,
                            step,
                            EventType.TOKEN_DELTA,
                            {"delta": delta, "section_id": sec_id},
                        )

            if node_name == "verify_citations":
                audited = list((node_update.get("section_drafts") or {}).items())
                for sec_id, section in audited:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.SCORECARD_UPDATE,
                        {"section_id": sec_id, "scorecard": _scorecard_payload(section)},
                    )
                    for claim in getattr(section, "claims", []) or []:
                        await _publish(
                            sink,
                            doc_id,
                            step,
                            EventType.CLAIM_VERIFIED,
                            {
                                "section_id": sec_id,
                                "claim": _model_payload(claim),
                            },
                        )
                    for citation in getattr(section, "citations", []) or []:
                        await _publish(
                            sink,
                            doc_id,
                            step,
                            EventType.CITATION_CREATED,
                            {
                                "section_id": sec_id,
                                "citation": _model_payload(citation),
                            },
                        )
                if audited:
                    await _publish(
                        sink,
                        doc_id,
                        step,
                        EventType.STREAM_METRICS,
                        {
                            "token_count": _count_tokens(local_state),
                            "latency_ms": 0,
                            "grounding_score": build_document_scorecard(
                                [section for _, section in audited]
                            )["f_beta"],
                        },
                    )

            await _publish(
                sink,
                doc_id,
                step,
                EventType.NODE_COMPLETE,
                {"node": node_name, "graph_status": node_update.get("status")},
            )

            successor = _successor_node(node_name, cast(HarnessState, local_state))
            if successor is not None:
                await _publish(
                    sink, doc_id, _NODE_STEPS[successor], EventType.NODE_START, {"node": successor}
                )

    return _StreamOutcome(
        interrupted=interrupted,
        interrupt_payload=interrupt_payload,
        completed_nodes=frozenset(completed),
    )


async def _publish_hitl_request(
    sink: EventSink | None,
    doc_id: str,
    interrupt_payload: Any,
    max_retries: int,
) -> None:
    """Publish the HITL_REQUEST event for a graph that just suspended."""
    payload: dict[str, Any] = interrupt_payload if isinstance(interrupt_payload, dict) else {}
    reason = str(payload.get("reason") or "Human review is required before continuing.")
    await _publish(
        sink,
        doc_id,
        WorkflowStep.HITL_GATE,
        EventType.HITL_REQUEST,
        {
            "reason": reason,
            "requires_hitl": True,
            "interrupt": payload,
            # The budget the run actually enforced, not the module default: the
            # caller may have asked for fewer retries than MAX_SECTION_RETRIES.
            "max_section_retries": max_retries,
        },
    )


async def run_workflow(
    doc_id: str,
    topic: str,
    source_chunks: Sequence[SourceChunk] | Sequence[dict[str, Any]] | None = None,
    max_retries: int = 3,
    threshold_f_beta: float = 0.85,
    session: Any | None = None,
    raw_texts: Sequence[str] | None = None,
    requested_outline: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Execute the compiled LangGraph workflow asynchronously.

    Streams node transitions so the caller can surface progress, and suspends at
    the human-in-the-loop gate instead of terminating. When the graph
    interrupts, ``requires_hitl`` is True and ``status`` is
    ``"awaiting_human_review"``.

    The compiled graph and its checkpointer are retained under ``doc_id`` (the
    ``thread_id``) for exactly as long as the run can still be resumed; call
    :func:`resume_workflow` with the same id and a decision to continue, or
    :func:`release_workflow` to drop an abandoned document.
    """
    graph = build_lmeditor_graph()
    _WORKFLOW_REGISTRY[doc_id] = graph
    sink: EventSink | None = session

    chunks: dict[str, SourceChunk] = {}
    if source_chunks:
        for c in source_chunks:
            if isinstance(c, SourceChunk):
                chunks[c.chunk_id] = c
            elif isinstance(c, dict):
                sc = SourceChunk(**c)
                chunks[sc.chunk_id] = sc

    initial_state = create_initial_harness_state(
        doc_id=doc_id,
        topic=topic,
        raw_texts=list(raw_texts or []),
        # These two were accepted and forwarded by the API but never reached the
        # state, so routing compared retry counts against MAX_SECTION_RETRIES and
        # the gate used the hardcoded threshold (AUDIT finding 12). Seeding them
        # here is what makes ``max_retries=1`` actually mean one revision cycle.
        max_retries=max_retries,
        threshold_f_beta=threshold_f_beta,
    )
    initial_state["source_chunks"] = chunks
    initial_state["retry_counts"] = {}
    if requested_outline:
        # The operator's steer: the sections they named are the sections the
        # manuscript is written to. The passages are still assigned from the
        # corpus, so the steer decides what the document covers and never
        # what it claims.
        initial_state["requested_outline"] = [
            node if isinstance(node, OutlineNodeInput) else OutlineNodeInput.model_validate(node)
            for node in requested_outline
        ]

    config = _thread_config(doc_id)

    outcome = await _stream_graph(
        graph=graph,
        sink=sink,
        doc_id=doc_id,
        config=config,
        payload=initial_state,
        seed=dict(initial_state),
        announce_entry=True,
    )

    snapshot = await graph.aget_state(config)
    res = _finalize_state(dict(snapshot.values or {}))

    if outcome.interrupted:
        await _publish_hitl_request(
            sink, doc_id, outcome.interrupt_payload, _effective_max_retries(res, max_retries)
        )
        res["requires_hitl"] = True
        res["hitl_approved"] = False
        res["hitl_reason"] = str(
            (outcome.interrupt_payload or {}).get("reason")
            if isinstance(outcome.interrupt_payload, dict)
            else "Human review is required before continuing."
        )
        res["status"] = AWAITING_HUMAN_REVIEW_STATUS
    else:
        # Terminal: nothing can be resumed, so do not retain the checkpointer.
        release_workflow(doc_id)

    return res


async def resume_workflow(
    thread_id: str,
    decision: Any,
    session: EventSink | None = None,
) -> dict[str, Any]:
    """Resume a workflow suspended at the human-in-the-loop gate.

    Args:
        thread_id: The ``doc_id`` of the suspended run; identifies the retained
            compiled graph and therefore its checkpoint.
        decision: The operator decision, forwarded verbatim as
            ``Command(resume=decision)``.
        session: Optional sink receiving the same event types as ``run_workflow``.

    Returns:
        The final state in the same shape ``run_workflow`` returns, including
        ``sections``.

    Raises:
        InvalidStateTransitionError: If no suspended run is retained for
            ``thread_id`` -- the document never started, already reached a
            terminal state, or was explicitly released.
    """
    graph = _WORKFLOW_REGISTRY.get(thread_id)
    if graph is None:
        raise InvalidStateTransitionError(
            f"No resumable workflow is registered for thread_id='{thread_id}'. "
            "The document may never have started, may already have reached a "
            "terminal state, or may have been released. Call run_workflow(...) first."
        )

    sink: EventSink | None = session
    config = _thread_config(thread_id)

    outcome = await _stream_graph(
        graph=graph,
        sink=sink,
        doc_id=thread_id,
        config=config,
        payload=Command(resume=decision),
        seed=None,
        announce_entry=False,
    )

    snapshot = await graph.aget_state(config)
    res = _finalize_state(dict(snapshot.values or {}))

    if outcome.interrupted:
        # The run suspended again: still resumable, so keep the registry entry.
        await _publish_hitl_request(
            sink,
            thread_id,
            outcome.interrupt_payload,
            _effective_max_retries(res, MAX_SECTION_RETRIES),
        )
        res["requires_hitl"] = True
        res["hitl_approved"] = False
        res["status"] = AWAITING_HUMAN_REVIEW_STATUS
        return res

    # Terminal in both branches: approved -> macro_cohesion -> END, or
    # rejected -> END straight from the gate.
    approved = "macro_cohesion" in outcome.completed_nodes
    res["requires_hitl"] = False

    if approved:
        sections = list(res.get("sections", []) or [])
        await _publish(
            sink,
            thread_id,
            WorkflowStep.COMPLETE,
            EventType.WORKFLOW_COMPLETE,
            {
                # The document-level aggregate of every audited section. It was
                # published as ``None`` here, so the client had nothing
                # truthful to display on the terminal frame and defaulted to a
                # fabricated 100%.
                "scorecard": build_document_scorecard(sections),
                "sections": [s.model_dump() for s in sections],
            },
        )
    release_workflow(thread_id)

    return res


__all__ = [
    "AWAITING_HUMAN_REVIEW_STATUS",
    "EventSink",
    "build_lmeditor_graph",
    "registered_workflow_threads",
    "release_workflow",
    "resume_workflow",
    "run_workflow",
]
