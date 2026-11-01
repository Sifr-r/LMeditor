package lmeditor.workflow

import lmeditor.core.toWireMap

/**
 * The wire form of the harness state.
 *
 * The state is the largest thing that crosses the seam, and it crosses it
 * whole: the Python tier owns the graph, and the graph's state has to survive
 * a round trip through the tier without losing a field.
 *
 * The evaluator override is the one field that does not travel: it is a live
 * object, not data. A state that carried one is a state whose evaluator is
 * chosen by whichever side rebuilt it, which is exactly the ambiguity the
 * seam exists to remove.
 */
fun HarnessState.toWireMap(): Map<String, Any?> = linkedMapOf(
    "doc_id" to docId,
    "topic" to topic,
    "target_audience" to targetAudience,
    "guidelines" to guidelines,
    "sources" to sources.map { it.toWireMap() },
    "raw_texts" to rawTexts,
    "source_chunks" to sourceChunks.mapValues { (_, chunk) -> chunk.toWireMap() },
    "perspectives" to perspectives.map { it.toWireMap() },
    "outline_root" to outlineRoot?.toWireMap(),
    "outline_nodes" to outlineNodes.map { it.toWireMap() },
    "guided_research" to guidedResearch,
    "research_memory" to researchMemory?.toWireMap(),
    "research_notes" to researchNotes,
    "requested_outline" to requestedOutline?.map { it.toWireMap() },
    "section_drafts" to sectionDrafts.mapValues { (_, draft) -> draft.toWireMap() },
    "current_section_id" to currentSectionId,
    "verification_reports" to verificationReports.mapValues { (_, report) -> report.toWireMap() },
    "retry_counts" to retryCounts,
    "critiques" to critiques,
    "full_manuscript" to fullManuscript,
    "status" to status,
    "errors" to errors,
    "requires_hitl" to requiresHitl,
    "hitl_reason" to hitlReason,
    "hitl_approved" to hitlApproved,
    "max_retries" to maxRetries,
    "threshold_f_beta" to thresholdFBeta,
)
