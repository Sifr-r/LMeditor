"""LangGraph operational graph nodes for LMeditor."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Coroutine, Mapping, TypeVar
from collections.abc import Awaitable

from langgraph.types import interrupt

from lmeditor.core.exceptions import (
    CitationHallucinationError,
    DialogueError,
    LMeditorError,
    LMNotConfiguredError,
    ValidationError,
)
from lmeditor.core.schemas import (
    OutlineNode,
    Perspective,
    ResearchMemory,
    SectionDraft,
    SectionStatus,
    SourceChunk,
    TopicOutline,
    VerificationReport,
    VerificationScorecard,
)
from lmeditor.core.sections import merge_operator_text
from lmeditor.core.types import EntailmentStatus
from lmeditor.discovery.dialogue import DialogueLoop
from lmeditor.discovery.outliner import HierarchicalOutlineGenerator
from lmeditor.discovery.perspective import StormPerspectiveGenerator
from lmeditor.drafting.modules import MacroCohesionModule, SectionDrafter
from lmeditor.llm import StubLM, is_configured, require_lm
from lmeditor.provenance.chunker import SourceChunker
from lmeditor.verification.citation_checker import (
    DEFAULT_F_BETA_THRESHOLD,
    MAX_CONCURRENT_NLI_EVALUATIONS,
    CitationChecker,
)
from lmeditor.verification.claim_extractor import PropositionClaimExtractor
from lmeditor.verification.nli_evaluator import NLIEvaluationEngine
from lmeditor.workflow.state import HarnessState

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Critique attached to a section that could not be drafted because no
#: language model is loaded. LMeditor never substitutes template prose for a
#: model call, so the section stays empty and says why.
NO_LM_CRITIQUE = (
    "No language model is configured, so this section was not drafted. "
    "Set LMEDITOR_LM_PROVIDER and LMEDITOR_LM_MODEL (and LMEDITOR_LM_API_KEY "
    "for a hosted provider) and run again."
)

#: Note attached to a run whose guided research stage did not happen because
#: no language model is loaded. LMeditor never fabricates an inquiry it did
#: not conduct, so the document is written from the corpus alone and says so.
NO_LM_GUIDED_RESEARCH_NOTE = (
    "No language model is configured, so the corpus was not questioned and no "
    "guided research was conducted. Set LMEDITOR_LM_PROVIDER and "
    "LMEDITOR_LM_MODEL (and LMEDITOR_LM_API_KEY for a hosted provider) and "
    "run again."
)

#: Note attached to a run that has nothing to inquire into.
EMPTY_GUIDED_RESEARCH_NOTE = (
    "Guided research did not run: the run has no discovered perspectives or no "
    "source passages, so there was nothing to inquire into."
)

#: Note attached to a run that opted out of the guided research stage.
GUIDED_RESEARCH_DISABLED_NOTE = (
    "Guided research was disabled for this run, so the outline was synthesised "
    "from the corpus alone."
)

#: Note attached to a run whose retrieval index could not be built.
INDEX_UNAVAILABLE_GUIDED_RESEARCH_NOTE = (
    "Guided research did not run: the retrieval index could not be built, so "
    "the corpus was not questioned."
)

#: How many questions every perspective is asked during guided research.
GUIDED_RESEARCH_TURNS_PER_SESSION = 3

#: How many passages are retrieved to answer each of those questions.
GUIDED_RESEARCH_TOP_K = 3

#: Prefix of the sentence that carries a guided finding into a section goal.
GUIDED_FINDINGS_PREFIX = "Guided inquiry found: "

#: A chunk id as it appears inside a guided finding. Chunk ids are 64-character
#: SHA-256 digests, so the pattern cannot match ordinary prose.
_CHUNK_ID_IN_TEXT = re.compile(r"\[([0-9a-f]{64})\]")

#: Retrieval indexes built per document, keyed by ``doc_id``.
#:
#: The index is kept out of the LangGraph state (which is checkpointed and must
#: stay serialisable), but it does **not** belong to the run.  It belongs to the
#: document: the inline editor grounds a revision in it and the fact-checker
#: compares a claim against it, and both are used *after* the run has ended.
#: An index dropped at the end of the run takes the document's own sources with
#: it, and a document whose sources are gone cannot be edited or fact-checked.
#:
#: It is released when the document itself is discarded
#: (:meth:`lmeditor.server.session.SessionManager.remove_session`).
_PROVENANCE_INDEXES: dict[str, Any] = {}


def _provenance_index(doc_id: str, chunks: list[SourceChunk]) -> Any | None:
    """The hybrid retrieval index for a document, built once per corpus.

    Returns ``None`` when the index cannot be built: retrieval is an
    enhancement, and a document must still be writable when the embedder is
    unavailable.

    A cached index is reused only when it was built over the same corpus.  A
    document id can be generated again, and an index over the previous
    document's sources would answer questions about passages this document
    never saw.
    """
    if not chunks:
        return None
    # Without a document id there is nothing to cache the index under, so it
    # is built for this call and thrown away rather than shared between
    # documents that do not know about each other.
    if not doc_id:
        return _build_provenance_index(chunks)
    cached = _PROVENANCE_INDEXES.get(doc_id)
    if cached is not None and _covers(cached, chunks):
        return cached
    index = _build_provenance_index(chunks)
    if index is None:
        return None
    _PROVENANCE_INDEXES[doc_id] = index
    return index


def _covers(index: Any, chunks: list[SourceChunk]) -> bool:
    """Whether the cached index was built over exactly these chunks.

    The chunk ids are the corpus's identity: two corpora that hold the same
    chunks in the same order are the same corpus, and an index over one of
    them answers for the other.
    """
    chunk_ids = getattr(index, "chunk_ids", None)
    if chunk_ids is None:
        return False
    return list(chunk_ids) == [chunk.chunk_id for chunk in chunks]


def _build_provenance_index(chunks: list[SourceChunk]) -> Any | None:
    """Build the hybrid retrieval index, or ``None`` when it cannot be built."""
    try:
        from lmeditor.provenance.service import ProvenanceIndex

        return ProvenanceIndex.build(chunks)
    except Exception as exc:  # noqa: BLE001 - retrieval must never break a run
        logger.warning("Provenance index unavailable: %s", exc)
        return None


def release_provenance_index(doc_id: str) -> None:
    """Drop the retrieval index held for a document."""
    _PROVENANCE_INDEXES.pop(doc_id, None)


def provenance_index_for(doc_id: str) -> Any | None:
    """The retrieval index held for a document, if one was built.

    The targeted-editing and fact-check surfaces ground their work in the
    same index the document was drafted from, rather than building a second
    one: two indexes over the same corpus can disagree about which passage
    answers a question, and the document the user is editing was written
    against the first one.
    """
    return _PROVENANCE_INDEXES.get(doc_id)


def _select_chunks_for_section(
    doc_id: str,
    node: OutlineNode,
    chunks_dict: dict[str, SourceChunk],
    top_k: int = 3,
) -> list[SourceChunk]:
    """Choose the passages a section should be written from.

    The passages the outline assigned are used when they exist. Otherwise the
    corpus is ranked against the section's intent with the hybrid retrieval
    index, so the passages a section receives are the ones that actually
    concern it -- rather than the first three the server happened to hold.
    """
    assigned = [chunks_dict[cid] for cid in node.assigned_chunk_ids if cid in chunks_dict]
    if assigned:
        return assigned
    if not chunks_dict:
        return []

    index = _provenance_index(doc_id, list(chunks_dict.values()))
    if index is not None:
        query = f"{node.title} {node.description}".strip()
        try:
            ranked = index.rank_for_section(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 - retrieval is best effort
            logger.warning("Retrieval failed for section '%s': %s", node.node_id, exc)
            ranked = []
        if ranked:
            return list(ranked)

    logger.warning(
        "No retrieval index for document '%s'; falling back to the first %d chunks.",
        doc_id,
        top_k,
    )
    return list(chunks_dict.values())[:top_k]


def ingest_sources_node(state: HarnessState) -> dict[str, Any]:
    """Ingest and chunk source artifacts (text or full Source objects)."""
    raw_texts = state.get("raw_texts", [])
    sources = state.get("sources", [])
    doc_id = state.get("doc_id", "doc_default")
    chunker = SourceChunker(chunk_size=120, overlap_size=25)

    chunks_dict: dict[str, SourceChunk] = dict(state.get("source_chunks", {}))

    # Layer 1: chunk raw_texts (legacy behavior preserved)
    for idx, text in enumerate(raw_texts):
        if not text.strip():
            continue
        sub_doc_id = f"{doc_id}_src_{idx}"
        produced_chunks = chunker.chunk_text(
            text=text,
            document_id=sub_doc_id,
            document_title=f"Source Document {idx + 1}",
        )
        for chunk in produced_chunks:
            chunks_dict[chunk.chunk_id] = chunk

    # Layer 2: re-chunk any Source whose chunks weren't already produced
    from lmeditor.ingestion.service import IngestionService

    svc = IngestionService.default()
    for source in sources:
        # Only re-ingest if no chunks for this source already exist
        already = any(c.document_id == source.id for c in chunks_dict.values())
        if already:
            continue
        result = svc.ingest(
            source_id=source.id,
            kind=source.kind,
            origin=source.origin,
            title=source.title,
        )
        for chunk in result.chunks:
            chunks_dict[chunk.chunk_id] = chunk

    return {
        "source_chunks": chunks_dict,
        "sources": sources,
        "status": "sources_indexed",
    }


def perspective_discovery_node(state: HarnessState) -> dict[str, Any]:
    """Discover multi-perspective expert and skeptic personas using STORM pattern.

    The personas are grounded in the ingested corpus: the chunk text is handed
    to the generator so each perspective's focus areas and questions come from
    the material being written about, not from a fixed archetype table.
    """
    topic = state.get("topic", "Grounded Expository Writing")
    chunks = list(state.get("source_chunks", {}).values())
    context = _corpus_context(chunks)
    generator = StormPerspectiveGenerator()
    perspectives = generator.generate_perspectives(
        topic=topic,
        max_perspectives=4,
        context=context,
    )

    return {
        "perspectives": perspectives,
        "status": "perspectives_discovered",
    }


def _corpus_context(chunks: list[SourceChunk], max_chars: int = 12000) -> str:
    """Render the ingested corpus as the context a generator can ground on.

    The text is truncated at a character budget so a large corpus cannot blow
    up a prompt, and the passages are separated by a blank line so that the
    boundaries between them stay visible.

    The passages are prose, and only prose. A chunk id is a 64-character
    SHA-256 digest; prefixing it here made the identifier a token of the
    corpus, and because the discovery layer ranks the longest candidate word
    first, the personas came out reading the material "through
    0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74"
    rather than through the subject matter -- and that description is handed
    to the drafter as the persona it must adopt. The identity of a passage
    travels with the chunk, not inside the text a generator analyses.
    """
    if not chunks:
        return ""
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        block = chunk.text.strip()
        if not block:
            continue
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def guided_research_node(state: HarnessState) -> dict[str, Any]:
    """Question the corpus with the discovered perspectives before outlining.

    This is the stage that turns "write from these sources" into "write from
    what these sources actually say about the questions that matter": every
    discovered perspective asks its questions of the corpus, the answers are
    assembled from the retrieved passages alone, and each finding keeps the
    chunks it came from. The outline is then synthesised from those findings
    rather than from the raw corpus alone.

    The stage never kills a run. Without perspectives, without passages,
    without a language model, or when the inquiry itself fails, the document
    is still written from the corpus and the state records why.
    """
    notes: list[str] = []
    result: dict[str, Any] = {"research_memory": None, "research_notes": notes}

    if not state.get("guided_research", True):
        notes.append(GUIDED_RESEARCH_DISABLED_NOTE)
        return result

    perspectives = list(state.get("perspectives", []))
    chunks = list(state.get("source_chunks", {}).values())
    if not perspectives or not chunks:
        notes.append(EMPTY_GUIDED_RESEARCH_NOTE)
        return result

    if not is_configured():
        notes.append(NO_LM_GUIDED_RESEARCH_NOTE)
        return result

    index = _provenance_index(state.get("doc_id", ""), chunks)
    if index is None:
        notes.append(INDEX_UNAVAILABLE_GUIDED_RESEARCH_NOTE)
        result["errors"] = list(state.get("errors", [])) + [
            "guided research did not run: the retrieval index could not be "
            "built, so the corpus was not questioned"
        ]
        return result

    try:
        memory = _conduct_guided_research(perspectives, index)
    except (DialogueError, ValidationError) as exc:
        message = f"guided research failed: {exc}"
        notes.append(f"Guided research did not complete: {exc}")
        result["errors"] = list(state.get("errors", [])) + [message]
        return result

    notes.extend(memory.collected_takeaways)
    result["research_memory"] = memory
    return result


def _conduct_guided_research(perspectives: list[Perspective], index: Any) -> ResearchMemory:
    """Run the guided inquiry and return what it found.

    The inquiry is the one :class:`~lmeditor.discovery.dialogue.DialogueLoop`
    conducts: the questions come from the perspectives, the answers from the
    retrieved passages, and every finding carries the chunks it came from.
    """
    loop = _guided_dialogue_loop()
    return loop.conduct_all_dialogues(
        perspectives,
        index,
        turns_per_session=GUIDED_RESEARCH_TURNS_PER_SESSION,
    )


def _guided_dialogue_loop() -> DialogueLoop:
    """The dialogue loop the guided stage conducts its inquiry with.

    The loop asks the language model to frame the retrieved evidence when the
    model can answer that request. The shipped offline drafter answers the
    drafting signatures, not the dialogue's synthesis signature, so asking it
    to frame the evidence would fail the whole inquiry; with that model the
    inquiry runs in its passage-grounded mode, where every answer is
    assembled from the retrieved passages.
    """
    try:
        lm = require_lm("guided research")
    except LMNotConfiguredError:
        lm = None
    use_dspy = lm is not None and not isinstance(lm, StubLM)
    return DialogueLoop(use_dspy=use_dspy, top_k_per_turn=GUIDED_RESEARCH_TOP_K)


def outline_synthesis_node(state: HarnessState) -> dict[str, Any]:
    """Build the outline the manuscript is written to.

    When the caller supplied an outline, that outline is the one: the operator
    named the sections, and the draft is written to them. The passages assigned
    to each section are still chosen from the corpus by the outliner's own
    rules, so the operator steers *what the document covers* and never *what
    the document claims*.

    Otherwise the outline is synthesised. The guided findings, when the guided
    research stage produced them, are what the outline is written from: the
    research memory is handed to the synthesizer and the findings that name a
    passage are carried into the goal of the section that holds that passage.
    A finding is a quotation from a retrieved passage, so carrying it into a
    section goal cannot make the outline less grounded than the corpus it was
    read from; without findings the outline is exactly the outline the corpus
    alone produces.
    """
    topic = state.get("topic", "Grounded Expository Writing")
    perspectives = state.get("perspectives", [])
    chunks_dict = state.get("source_chunks", {})
    available_chunks = list(chunks_dict.values())
    research_memory = state.get("research_memory")

    outliner = HierarchicalOutlineGenerator()
    requested = state.get("requested_outline") or []
    if requested:
        outline_root = outliner.build_outline_from_sections(
            requested,
            topic=topic,
            available_chunks=available_chunks,
        )
    else:
        outline_root = outliner.generate_outline(
            topic=topic,
            research_memory=research_memory,
            perspectives=perspectives,
            available_chunks=available_chunks,
        )

    findings = _guided_findings_by_chunk(research_memory, available_chunks)
    if findings:
        outline_root = _attach_guided_findings(outline_root, findings)
    leaf_nodes = outline_root.get_leaves()

    return {
        "outline_root": outline_root,
        "outline_nodes": leaf_nodes,
        "status": "outline_synthesized",
    }


def _guided_findings_by_chunk(
    research_memory: ResearchMemory | None,
    chunks: list[SourceChunk],
) -> dict[str, list[str]]:
    """The guided findings, grouped by the passage each one is anchored to.

    A finding that names a passage is grounded in that passage, so it belongs
    to the section the passage was assigned to. Findings that name no passage
    (a question that stayed open, for instance) are not attributed to a
    section: there is nothing to ground them in.
    """
    if research_memory is None:
        return {}
    known_ids = {chunk.chunk_id for chunk in chunks}
    findings: dict[str, list[str]] = {}
    for finding in research_memory.collected_takeaways:
        for chunk_id in _CHUNK_ID_IN_TEXT.findall(finding):
            if chunk_id not in known_ids:
                continue
            anchored = findings.setdefault(chunk_id, [])
            if finding not in anchored:
                anchored.append(finding)
    return findings


def _attach_guided_findings(
    outline_root: TopicOutline,
    findings: dict[str, list[str]],
) -> TopicOutline:
    """Return the outline with the guided findings carried into its goals.

    The tree is rebuilt leaf by leaf, and a section's goal gains the findings
    that name one of the passages assigned to it. Nothing else about the
    outline changes.
    """
    rebuilt = [_attach_findings_to_node(node, findings) for node in outline_root.nodes]
    return outline_root.model_copy(update={"nodes": rebuilt})


def _attach_findings_to_node(node: OutlineNode, findings: dict[str, list[str]]) -> OutlineNode:
    """Return ``node`` with the guided findings carried into its goal."""
    if node.children:
        rebuilt = [_attach_findings_to_node(child, findings) for child in node.children]
        return node.model_copy(update={"children": rebuilt})

    anchored: list[str] = []
    for chunk_id in node.assigned_chunk_ids:
        for finding in findings.get(chunk_id, []):
            if finding not in anchored:
                anchored.append(finding)
    if not anchored:
        return node
    description = f"{node.description} {GUIDED_FINDINGS_PREFIX}{' '.join(anchored)}".strip()
    return node.model_copy(update={"description": description})


def _is_missing_lm_error(exc: BaseException) -> bool:
    """Whether an exception means "no language model is loaded".

    DSPy signals this with its own ``LMNotConfiguredError`` in recent
    versions and with a plain ``ValueError`` before that, so both shapes are
    recognised.
    """
    if isinstance(exc, LMNotConfiguredError):
        return True
    try:
        import dspy

        dspy_exc = getattr(dspy, "LMNotConfiguredError", None)
        if dspy_exc is not None and isinstance(exc, dspy_exc):
            return True
    except Exception:  # noqa: BLE001 - dspy may be unavailable
        pass
    message = str(exc).lower()
    return "no lm is loaded" in message or "no language model is configured" in message


def draft_sections_node(state: HarnessState) -> dict[str, Any]:
    """Draft sections anchored to assigned passages using DSPy modules.

    A section that cannot be drafted is left **empty and explicitly marked**,
    never filled with substitute prose. The previous behaviour emitted a
    template paragraph built from the first 180 characters of a source chunk
    and a ``[#chunk_id]`` marker, which was indistinguishable in the output
    from a real draft while containing none of the reasoning a real draft
    would have. An empty, labelled section routes to the human-in-the-loop
    gate, where the operator can see exactly what failed.
    """
    topic = state.get("topic", "")
    leaf_nodes = state.get("outline_nodes", [])
    chunks_dict = state.get("source_chunks", {})
    perspectives = state.get("perspectives", [])
    existing_drafts = dict(state.get("section_drafts", {}))
    critiques = state.get("critiques", {})
    errors = list(state.get("errors", []))

    drafter = SectionDrafter(use_chain_of_thought=False)
    updated_drafts: dict[str, SectionDraft] = {}

    prior_summary = ""

    for idx, node in enumerate(leaf_nodes):
        # Determine if this section needs drafting or redrafting
        current_draft = existing_drafts.get(node.node_id)
        if current_draft is not None and current_draft.status == SectionStatus.VERIFIED:
            continue  # Already verified

        assigned_chunks = _select_chunks_for_section(
            state.get("doc_id", ""), node, chunks_dict
        )
        if not assigned_chunks:
            # Nothing to ground this section in: record it rather than draft
            # prose that cites nothing.
            draft = SectionDraft(
                section_id=node.node_id,
                title=node.title,
                content="",
                order=node.order,
                cited_chunk_ids=[],
                status=SectionStatus.NEEDS_REVISION,
                critique=(
                    "No source passage was available for this section, so it "
                    "could not be drafted."
                ),
            )
            errors.append(f"section '{node.node_id}' has no source passage to draft from")
            updated_drafts[node.node_id] = draft
            continue

        persp = perspectives[idx % len(perspectives)] if perspectives else "Objective Expository Analyst"
        feedback = critiques.get(node.node_id)

        try:
            draft = drafter.forward(
                topic=topic,
                section_node=node,
                perspective=persp,
                assigned_chunks=assigned_chunks,
                prior_section_summary=prior_summary,
                revision_feedback=feedback,
            )
        except Exception as exc:
            # The draft failed for a real reason. Record it honestly and leave
            # the section empty; a failure is not a section.
            missing_lm = _is_missing_lm_error(exc)
            logger.warning(
                "Drafting failed for section '%s': %s", node.node_id, exc
            )
            draft = SectionDraft(
                section_id=node.node_id,
                title=node.title,
                content="",
                order=node.order,
                cited_chunk_ids=[],
                status=SectionStatus.NEEDS_REVISION,
                critique=NO_LM_CRITIQUE if missing_lm else f"Drafting failed: {exc}",
            )
            errors.append(
                f"section '{node.node_id}' was not drafted: "
                + ("no language model configured" if missing_lm else str(exc))
            )

        updated_drafts[node.node_id] = draft
        prior_summary = f"{node.title}: {draft.content[:100]}..."

    return {
        "section_drafts": updated_drafts,
        "errors": errors,
        "status": "sections_drafted",
    }


_shared_audit_lock = threading.Lock()
_shared_audit_loop: asyncio.AbstractEventLoop | None = None


def _audit_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide loop used when the caller already runs one.

    LangGraph dispatches a synchronous node onto a worker thread, so the normal
    path never needs this: :func:`_run_audit` owns a fresh loop with
    ``asyncio.run`` instead. This covers the other case -- a caller invoking the
    node from inside a running loop, where ``asyncio.run`` is illegal. The loop
    and its single daemon thread are created once and reused; the previous
    implementation allocated a fresh ``ThreadPoolExecutor`` *and* a fresh event
    loop on every single node call and then blocked the calling thread on
    ``.result()``. The loop is never closed, because its whole purpose is to
    outlive the call that needed it.
    """
    global _shared_audit_loop

    loop = _shared_audit_loop
    if loop is None:
        with _shared_audit_lock:
            loop = _shared_audit_loop
            if loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever,
                    name="lmeditor-verification-loop",
                    daemon=True,
                ).start()
                _shared_audit_loop = loop
    return loop


def _run_audit(awaitable: Coroutine[Any, Any, _T]) -> _T:
    """Drive ``awaitable`` to completion from synchronous graph-node context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread: the common case. ``asyncio.run`` borrows a
        # loop for the duration of the call and tears it down afterwards, so
        # there is nothing per-call to allocate and no foreign thread to join.
        return asyncio.run(awaitable)

    # A loop is already running here, so hand the work to the shared loop and
    # block only this (already-synchronous) caller's thread.
    return asyncio.run_coroutine_threadsafe(awaitable, _audit_loop()).result()


async def audit_sections(
    checker: CitationChecker,
    section_drafts: Mapping[str, SectionDraft],
    chunks_dict: Mapping[str, SourceChunk],
) -> dict[str, tuple[SectionDraft, VerificationScorecard]]:
    """Audit the sections that have not been verified yet.

    This is the public entry point to :func:`_audit_sections`, so that a
    caller outside the graph — the server's save route, which measures the
    text an operator has just written — measures a section exactly the way the
    pipeline measures a drafted one. A second implementation of the audit
    would be a second opinion about what "verified" means, and the two would
    eventually disagree.
    """
    return await _audit_sections(checker, section_drafts, chunks_dict)


async def _audit_sections(
    checker: CitationChecker,
    section_drafts: Mapping[str, SectionDraft],
    chunks_dict: Mapping[str, SourceChunk],
) -> dict[str, tuple[SectionDraft, VerificationScorecard]]:
    """Audit every non-VERIFIED section concurrently, preserving section order.

    ``asyncio.gather`` yields results in the order the awaitables were supplied,
    so the returned mapping is keyed identically to a serial loop no matter which
    section finishes first. The semaphore is what stops a long manuscript from
    turning into an unbounded fan-out of NLI calls.
    """
    pending: list[tuple[str, SectionDraft]] = [
        (sec_id, draft)
        for sec_id, draft in section_drafts.items()
        if draft.status != SectionStatus.VERIFIED
    ]
    if not pending:
        return {}

    # A per-call semaphore, deliberately distinct from the one
    # ``CitationChecker`` holds around individual NLI calls: a section holding
    # one permit here and then trying to take one there would deadlock as soon
    # as both pools were saturated.
    gate = asyncio.Semaphore(MAX_CONCURRENT_NLI_EVALUATIONS)

    async def _audit(sec_id: str, draft: SectionDraft) -> tuple[str, tuple[SectionDraft, VerificationScorecard]]:
        async with gate:
            try:
                updated_draft, scorecard = await checker.audit_section(
                    section=draft,
                    chunks_by_id=chunks_dict,
                    # Strict mode is the production behaviour: a citation that
                    # names a chunk the index does not contain is a
                    # hallucinated reference, and it must be visible rather
                    # than quietly downgraded to "ungrounded".
                    strict_hallucination_check=True,
                )
            except CitationHallucinationError as exc:
                # One section citing a phantom chunk must not abort the whole
                # audit. The section fails, the reason is recorded, and the
                # run continues to the human-in-the-loop gate.
                logger.warning("Hallucinated citation in section '%s': %s", sec_id, exc)
                updated_draft = draft.model_copy(
                    update={
                        "is_verified": False,
                        "status": SectionStatus.NEEDS_REVISION,
                        "critique": str(exc),
                    }
                )
                scorecard = VerificationScorecard(
                    precision=0.0,
                    recall=0.0,
                    f_beta=0.0,
                    beta=checker.beta,
                    total_claims=max(len(draft.claims), 1),
                    supported_claims=0,
                    ungrounded_claims=max(len(draft.claims), 1),
                    contradicted_claims=0,
                    neutral_claims=0,
                    details=[],
                    evaluator=_evaluator_name(checker),
                )
        return sec_id, (updated_draft, scorecard)

    return dict(await asyncio.gather(*(_audit(sec_id, draft) for sec_id, draft in pending)))


def _evaluator_name(checker: Any) -> str:
    """Identify the NLI evaluator that produced a set of verdicts.

    The checker may be a test double without an ``evaluator`` attribute, so
    the lookup is defensive.
    """
    evaluator = getattr(checker, "evaluator", None)
    if evaluator is None:
        return ""
    return str(getattr(evaluator, "evaluator_name", "") or "")


def verify_citations_node(state: HarnessState) -> dict[str, Any]:
    """Verify claim entailment and compute mathematical citation metrics.

    Stays a synchronous node on purpose: LangGraph runs sync nodes on a worker
    thread, so the coroutine below runs on a loop of its own and never blocks
    the server's event loop. The threshold is the caller's
    ``threshold_f_beta`` (AUDIT finding 12), not the module constant -- the same
    value is handed to the checker and used for this node's own ``all_passed``
    gate so the two can never disagree.
    """
    section_drafts = state.get("section_drafts", {})
    chunks_dict = state.get("source_chunks", {})
    f_beta_threshold = state.get("threshold_f_beta", DEFAULT_F_BETA_THRESHOLD)
    # The evaluator can be overridden by the caller. Production leaves it unset
    # and gets the default cross-encoder; the test suite supplies a
    # deterministic one so verification does not require model weights.
    evaluator_override = state.get("nli_evaluator")
    checker = (
        CitationChecker(evaluator=evaluator_override, f_beta_threshold=f_beta_threshold)
        if evaluator_override is not None
        else CitationChecker(f_beta_threshold=f_beta_threshold)
    )

    audit_results = _run_audit(_audit_sections(checker, section_drafts, chunks_dict))

    new_reports: dict[str, VerificationReport] = {}
    updated_drafts: dict[str, SectionDraft] = {}

    for sec_id, (updated_draft, scorecard) in audit_results.items():
        all_passed = (
            scorecard.f_beta >= f_beta_threshold
            and scorecard.contradicted_claims == 0
            and scorecard.ungrounded_claims == 0
            and scorecard.neutral_claims == 0
        )
        report = VerificationReport(
            report_id=f"rep_{sec_id}",
            section_id=sec_id,
            total_claims=scorecard.total_claims,
            entailed_claims=scorecard.supported_claims,
            contradicted_claims=scorecard.contradicted_claims,
            ungrounded_claims=scorecard.ungrounded_claims,
            precision=scorecard.precision,
            claims=scorecard.details,
            status=EntailmentStatus.ENTAILED if all_passed else EntailmentStatus.UNGROUNDED,
            all_passed=all_passed,
            summary=(
                f"Audit finished: F_beta={scorecard.f_beta:.3f}, "
                f"ungrounded={scorecard.ungrounded_claims}, "
                f"neutral={scorecard.neutral_claims}"
            ),
            evaluator=str(getattr(scorecard, "evaluator", "") or "")
            or _evaluator_name(checker),
        )
        new_reports[sec_id] = report
        # The checker rebuilds the section, so its explicit chunk references
        # are re-attached here: the citations are what the manuscript is
        # grounded in and the client renders them from.
        incoming = section_drafts.get(sec_id)
        cited = updated_draft.cited_chunk_ids or (
            incoming.cited_chunk_ids if incoming is not None else []
        )
        lineage = _lineage_for_section(
            state.get("doc_id", ""), updated_draft, chunks_dict
        )
        updated_drafts[sec_id] = updated_draft.model_copy(
            update={
                "is_verified": all_passed,
                "cited_chunk_ids": cited,
                "citations": lineage or updated_draft.citations,
            }
        )

    return {
        "verification_reports": new_reports,
        "section_drafts": updated_drafts,
        "status": "citations_verified",
    }


def _lineage_for_section(
    doc_id: str,
    section: SectionDraft,
    chunks_dict: dict[str, SourceChunk],
) -> list[Any]:
    """Map a section's claims to the source passages that support them.

    The lineage is what turns a claim into a *citation*: it records which
    chunk supports it, the exact quoted span and how confident the mapping
    is. Without it the client can only show a chunk id.
    """
    claims = list(getattr(section, "claims", []) or [])
    if not claims:
        return []
    index = _provenance_index(doc_id, list(chunks_dict.values()))
    if index is None:
        return []
    records: list[Any] = []
    for claim in claims:
        try:
            records.extend(
                index.lineage_for_claim(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    draft_text=section.content,
                    span_start=claim.char_start,
                    span_end=claim.char_end,
                )
            )
        except Exception as exc:  # noqa: BLE001 - lineage is best effort
            logger.warning(
                "Lineage failed for claim '%s': %s", claim.claim_id, exc
            )
    return records


def critique_and_refine_node(state: HarnessState) -> dict[str, Any]:
    """Generate precise critique feedback for ungrounded or contradictory claims."""
    section_drafts = state.get("section_drafts", {})
    reports = state.get("verification_reports", {})
    retry_counts = dict(state.get("retry_counts", {}))
    critiques: dict[str, str] = {}

    for sec_id, draft in section_drafts.items():
        if draft.status == SectionStatus.VERIFIED:
            continue

        retries = retry_counts.get(sec_id, 0) + 1
        retry_counts[sec_id] = retries

        report = reports.get(sec_id)
        if report:
            critique_msg = (
                f"Section '{draft.title}' failed grounding check with F_beta={report.f_beta:.2f}. "
                f"Ungrounded claims: {report.ungrounded_count}, Contradictions: {report.contradiction_count}. "
                "Ensure every factual assertion quotes or directly paraphrases the assigned passages with [#chunk_id]."
            )
        else:
            critique_msg = "Please verify all factual assertions against assigned passages with explicit [#chunk_id] tags."

        critiques[sec_id] = critique_msg

    return {
        "retry_counts": retry_counts,
        "critiques": critiques,
        "status": "critiqued_for_refinement",
    }


def macro_cohesion_node(state: HarnessState) -> dict[str, Any]:
    """Stitch verified sections into a unified document and polish transitions.

    The polish is optional: a failure inside the cohesion module must not
    lose the manuscript that was already drafted and verified. The unpolished
    manuscript is kept, and the reason is recorded.
    """
    topic = state.get("topic", "Grounded Expository Synthesis")
    section_drafts = state.get("section_drafts", {})
    errors = list(state.get("errors", []))
    sorted_drafts = sorted(section_drafts.values(), key=lambda d: d.order)

    assembled_sections: list[str] = []
    for draft in sorted_drafts:
        assembled_sections.append(f"## {draft.title}\n\n{draft.content}\n")

    raw_manuscript = f"# {topic}\n\n" + "\n".join(assembled_sections)

    cohesion_module = MacroCohesionModule()
    try:
        polished_manuscript = cohesion_module.forward(
            full_draft=raw_manuscript, topic=topic
        )
    except LMeditorError as exc:
        # The cohesion module refuses to return prose it cannot vouch for.
        # The manuscript itself is already drafted and verified, so it is kept
        # as it is rather than lost to a cosmetic failure.
        logger.warning("Macro cohesion failed, keeping the assembled manuscript: %s", exc)
        errors.append(f"macro cohesion failed, the manuscript was not polished: {exc}")
        polished_manuscript = raw_manuscript

    return {
        "full_manuscript": polished_manuscript,
        "errors": errors,
        "status": "completed",
    }


HITL_REASON = "One or more sections exceeded the retry threshold or contained ungrounded claims."


def _hitl_approved_from_decision(decision: Any) -> bool:
    """Normalise the value supplied by ``Command(resume=...)`` into a verdict.

    Accepts a bare bool, a HITL review payload, or a mapping carrying an
    ``approved`` key. Anything else is treated as a rejection, which is the
    conservative outcome.
    """
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, dict):
        return bool(decision.get("approved", False))
    approved = getattr(decision, "approved", None)
    if approved is None:
        return False
    return bool(approved)


def _operator_sections_from_decision(decision: Any) -> list[SectionDraft]:
    """The sections the operator submitted with their review decision.

    A decision reaches the gate as a payload object, as a mapping, or as a bare
    verdict, and the operator's edits travel with it in the first two cases
    only. A section that does not validate is dropped rather than guessed at:
    the operator's text is their own, and it is not the server's job to
    reconstruct it.
    """
    raw: Any = None
    if isinstance(decision, dict):
        raw = decision.get("modified_sections")
    elif decision is not None and not isinstance(decision, bool):
        raw = getattr(decision, "modified_sections", None)
    if not raw:
        return []

    sections: list[SectionDraft] = []
    for item in raw:
        if isinstance(item, SectionDraft):
            sections.append(item)
            continue
        if isinstance(item, dict):
            try:
                sections.append(SectionDraft.model_validate(item))
            except Exception:
                logger.warning("Ignoring an operator section that does not validate: %r", item)
    return sections


def hitl_checkpoint_node(state: HarnessState) -> dict[str, Any]:
    """Human-in-the-Loop review gate: suspend the graph until a human decides.

    ``interrupt`` suspends execution and hands the payload to the caller, who
    resumes with ``Command(resume=decision)``. On the second invoke the decision
    is returned and ``route_after_hitl`` reads the resulting ``hitl_approved``
    flag.

    The operator's own text is applied here, not discarded. A review gate that
    throws away what the reviewer wrote is a gate that makes editing pointless:
    the operator would approve a document, go back and fix a paragraph, and
    find their paragraph replaced by the draft again. The edited sections are
    merged into the state — the text is the operator's, the provenance is the
    pipeline's — and measured again, so the document reports honestly on the
    text as it now stands.
    """
    decision = interrupt({"reason": HITL_REASON, "requires_hitl": True})
    approved = _hitl_approved_from_decision(decision)

    updates: dict[str, Any] = {
        "hitl_approved": approved,
        "requires_hitl": False,
        "hitl_reason": HITL_REASON,
        "status": "human_review_approved" if approved else "human_review_rejected",
    }

    edited = _operator_sections_from_decision(decision)
    if edited:
        updates.update(_apply_operator_sections(state, edited))
    return updates


def _apply_operator_sections(
    state: HarnessState,
    edited: list[SectionDraft],
) -> dict[str, Any]:
    """Merge the operator's text into the drafted sections and measure them.

    Only the text and the heading are taken from the operator's submission.
    The position is not: a section's order is the pipeline's arrangement, and
    a submission that omitted it would silently move the section to the front
    of the document.
    """
    drafts: dict[str, SectionDraft] = dict(state.get("section_drafts", {}))
    merged: dict[str, SectionDraft] = {}

    for operator_section in edited:
        drafted = drafts.get(operator_section.section_id)
        if drafted is None:
            logger.warning(
                "Ignoring an edit to section '%s': the document has no such section.",
                operator_section.section_id,
            )
            continue
        merged[operator_section.section_id] = merge_operator_text(
            drafted,
            content=operator_section.content,
            title=operator_section.title,
        )

    if not merged:
        return {}

    # The edited text is new, so the verdict that described the old text no
    # longer describes this one. It is measured again, exactly the way the
    # pipeline measures a drafted section.
    chunks = state.get("source_chunks", {})
    if not chunks:
        # There is nothing to measure against. "Nothing supports this" and
        # "there was nothing to check against" are different statements, and
        # only the first of them is a verdict about the text.
        logger.info(
            "The run holds no sources to measure the operator's edited sections against."
        )
        drafts.update(merged)
        return {"section_drafts": drafts}

    checker = CitationChecker(
        f_beta_threshold=state.get("threshold_f_beta", DEFAULT_F_BETA_THRESHOLD)
    )
    try:
        audit = _run_audit(_audit_sections(checker, merged, chunks))
    except Exception:
        logger.exception(
            "Could not measure the operator's edited sections; keeping them unmeasured."
        )
        audit = {}

    for sec_id, (updated, _scorecard) in audit.items():
        merged[sec_id] = updated

    drafts.update(merged)
    return {"section_drafts": drafts}


__all__ = [
    "HITL_REASON",
    "MAX_CONCURRENT_NLI_EVALUATIONS",
    "critique_and_refine_node",
    "draft_sections_node",
    "hitl_checkpoint_node",
    "ingest_sources_node",
    "macro_cohesion_node",
    "outline_synthesis_node",
    "perspective_discovery_node",
    "verify_citations_node",
]
