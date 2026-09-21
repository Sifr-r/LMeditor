"""LangGraph operational graph nodes for LMeditor."""

from __future__ import annotations

import logging
from typing import Any

from lmeditor.core.schemas import (
    OutlineNode,
    SectionDraft,
    SectionStatus,
    SourceChunk,
    VerificationReport,
)
from lmeditor.core.types import EntailmentStatus
from lmeditor.discovery.outliner import HierarchicalOutlineGenerator
from lmeditor.discovery.perspective import StormPerspectiveGenerator
from lmeditor.drafting.modules import MacroCohesionModule, SectionDrafter
from lmeditor.provenance.chunker import SourceChunker
from lmeditor.verification.citation_checker import CitationChecker
from lmeditor.verification.claim_extractor import PropositionClaimExtractor
from lmeditor.verification.nli_evaluator import NLIEvaluationEngine
from lmeditor.workflow.state import HarnessState

logger = logging.getLogger(__name__)


def ingest_sources_node(state: HarnessState) -> dict[str, Any]:
    """Ingest and chunk raw source texts, generating cryptographic chunk IDs."""
    raw_texts = state.get("raw_texts", [])
    doc_id = state.get("doc_id", "doc_default")
    chunker = SourceChunker(chunk_size=120, overlap_size=25)

    chunks_dict: dict[str, SourceChunk] = dict(state.get("source_chunks", {}))

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

    return {
        "source_chunks": chunks_dict,
        "status": "sources_indexed",
    }


def perspective_discovery_node(state: HarnessState) -> dict[str, Any]:
    """Discover multi-perspective expert and skeptic personas using STORM pattern."""
    topic = state.get("topic", "Grounded Expository Writing")
    generator = StormPerspectiveGenerator()
    perspectives = generator.generate_perspectives(topic=topic, max_perspectives=4)

    return {
        "perspectives": perspectives,
        "status": "perspectives_discovered",
    }


def outline_synthesis_node(state: HarnessState) -> dict[str, Any]:
    """Synthesize a hierarchical outline DAG and assign source chunks to leaf nodes."""
    topic = state.get("topic", "Grounded Expository Writing")
    perspectives = state.get("perspectives", [])
    chunks_dict = state.get("source_chunks", {})
    available_chunks = list(chunks_dict.values())

    outliner = HierarchicalOutlineGenerator()
    outline_root = outliner.generate_outline(
        topic=topic,
        perspectives=perspectives,
        available_chunks=available_chunks,
    )
    leaf_nodes = outline_root.get_leaves()

    return {
        "outline_root": outline_root,
        "outline_nodes": leaf_nodes,
        "status": "outline_synthesized",
    }


def draft_sections_node(state: HarnessState) -> dict[str, Any]:
    """Draft sections anchored to assigned passages using DSPy modules."""
    topic = state.get("topic", "")
    leaf_nodes = state.get("outline_nodes", [])
    chunks_dict = state.get("source_chunks", {})
    perspectives = state.get("perspectives", [])
    existing_drafts = dict(state.get("section_drafts", {}))
    critiques = state.get("critiques", {})

    drafter = SectionDrafter(use_chain_of_thought=False)
    updated_drafts: dict[str, SectionDraft] = {}

    prior_summary = ""

    for idx, node in enumerate(leaf_nodes):
        # Determine if this section needs drafting or redrafting
        current_draft = existing_drafts.get(node.node_id)
        if current_draft is not None and current_draft.status == SectionStatus.VERIFIED:
            continue  # Already verified

        assigned_chunks = [
            chunks_dict[cid] for cid in node.assigned_chunk_ids if cid in chunks_dict
        ]
        if not assigned_chunks:
            # Fallback: assign first available chunks if none matched
            assigned_chunks = list(chunks_dict.values())[:3]

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
            # Create a fallback draft with explicit chunk citations to avoid total failure
            first_chunk = assigned_chunks[0]
            fallback_text = (
                f"{node.title}: According to verified documentation, {first_chunk.text.strip()[:180]} "
                f"[#{first_chunk.chunk_id}]."
            )
            draft = SectionDraft(
                section_id=node.node_id,
                title=node.title,
                content=fallback_text,
                order=node.order,
                cited_chunk_ids=[first_chunk.chunk_id],
                status=SectionStatus.DRAFTED,
                critique=str(exc),
            )

        updated_drafts[node.node_id] = draft
        prior_summary = f"{node.title}: {draft.content[:100]}..."

    return {
        "section_drafts": updated_drafts,
        "status": "sections_drafted",
    }


def verify_citations_node(state: HarnessState) -> dict[str, Any]:
    """Verify claim entailment and compute mathematical citation metrics."""
    import asyncio
    import concurrent.futures

    section_drafts = state.get("section_drafts", {})
    chunks_dict = state.get("source_chunks", {})
    checker = CitationChecker()

    async def _audit_all() -> dict[str, tuple[SectionDraft, Any]]:
        results: dict[str, tuple[SectionDraft, Any]] = {}
        for sec_id, draft in section_drafts.items():
            if draft.status == SectionStatus.VERIFIED:
                continue
            updated_draft, scorecard = await checker.audit_section(
                section=draft,
                chunks_by_id=chunks_dict,
                strict_hallucination_check=False,
            )
            results[sec_id] = (updated_draft, scorecard)
        return results

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            audit_results = pool.submit(asyncio.run, _audit_all()).result()
    else:
        audit_results = asyncio.run(_audit_all())

    new_reports: dict[str, VerificationReport] = {}
    updated_drafts: dict[str, SectionDraft] = {}

    for sec_id, (updated_draft, scorecard) in audit_results.items():
        all_passed = (
            scorecard.f_beta >= 0.80
            and scorecard.contradicted_claims == 0
            and scorecard.ungrounded_claims == 0
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
            summary=f"Audit finished: F_beta={scorecard.f_beta:.3f}, ungrounded={scorecard.ungrounded_claims}",
        )
        new_reports[sec_id] = report
        updated_drafts[sec_id] = updated_draft.model_copy(update={"is_verified": all_passed})

    return {
        "verification_reports": new_reports,
        "section_drafts": updated_drafts,
        "status": "citations_verified",
    }


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
    """Stitch verified sections into a unified document and polish transitions."""
    topic = state.get("topic", "Grounded Expository Synthesis")
    section_drafts = state.get("section_drafts", {})
    sorted_drafts = sorted(section_drafts.values(), key=lambda d: d.order)

    assembled_sections: list[str] = []
    for draft in sorted_drafts:
        assembled_sections.append(f"## {draft.title}\n\n{draft.content}\n")

    raw_manuscript = f"# {topic}\n\n" + "\n".join(assembled_sections)

    cohesion_module = MacroCohesionModule()
    polished_manuscript = cohesion_module.forward(full_draft=raw_manuscript, topic=topic)

    return {
        "full_manuscript": polished_manuscript,
        "status": "completed",
    }


def hitl_checkpoint_node(state: HarnessState) -> dict[str, Any]:
    """Human-in-the-Loop review gate for ungrounded sections or exhausted retries."""
    return {
        "requires_hitl": True,
        "hitl_reason": "One or more sections exceeded retry threshold or contained ungrounded claims.",
        "status": "awaiting_human_review",
    }


__all__ = [
    "critique_and_refine_node",
    "draft_sections_node",
    "hitl_checkpoint_node",
    "ingest_sources_node",
    "macro_cohesion_node",
    "outline_synthesis_node",
    "perspective_discovery_node",
    "verify_citations_node",
]
