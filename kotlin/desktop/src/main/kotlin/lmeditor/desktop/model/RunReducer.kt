package lmeditor.desktop.model

import lmeditor.core.Claim
import lmeditor.core.CitationLineage
import lmeditor.core.EventType
import lmeditor.core.OutlineNodeInput
import lmeditor.core.WorkflowEvent
import lmeditor.core.citationLineageFromWire
import lmeditor.core.claimFromWire
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.core.outlineNodeFromWire
import lmeditor.core.perspectiveFromWire
import lmeditor.core.sectionDraftFromWire
import lmeditor.core.sourceChunkFromWire
import lmeditor.core.verificationScorecardFromWire

/**
 * What one frame of the event stream means.
 *
 * The reduction is a pure function of the state and the frame, and it is
 * idempotent: applying the same frame twice leaves the same state as applying
 * it once. A run re-runs its nodes after a resume, and the window must not
 * count the same frame twice -- a claim verified twice is one claim, and a
 * chunk published twice is one chunk.
 *
 * A frame of streamed text is identified by what the reducer is given: the
 * section it belongs to and its own text. The SSE frame carries an id, but
 * the id is not in the event's data, so the reducer is not given it. The test
 * is a suffix test: a frame whose text the section already ends with is taken
 * for that frame arriving a second time, and is not counted twice.
 *
 * What that rule does not catch, and does not claim to: a replay of a whole
 * *sequence* of frames is not caught (only the frame that matches the
 * current tail is recognised), and a new frame whose text happens to be
 * identical to the section's current tail is treated as a replay. Catching
 * those needs the frame's id, which the reducer is not given.
 *
 * Keeping the reduction apart from the controller is what makes that property
 * testable: the rule is stated once, and the test states it.
 */
object RunReducer {

    /** Apply one frame to the state. */
    fun reduce(state: StudioState, event: WorkflowEvent): StudioState {
        if (state.docId != null && state.docId != event.docId) return state
        val data = event.data
        return when (event.eventType) {
            EventType.CHUNKS -> state.copy(
                chunks = state.chunks.mergeBy(
                    data.jsonObjectList("chunks").map { sourceChunkFromWire(it) },
                    key = { it.chunkId },
                ),
            )

            EventType.PERSPECTIVES -> state.copy(
                perspectives = data.jsonObjectList("perspectives").map { perspectiveFromWire(it) },
            )

            EventType.RESEARCH_NOTES -> state.copy(
                researchNotes = data.jsonStringList("notes"),
                referencedChunkIds = data.jsonStringList("referenced_chunk_ids"),
            )

            EventType.OUTLINE -> state.copy(
                outline = data.jsonObjectList("outline").map { outlineNodeFromWire(it) },
            )

            EventType.TOKEN_DELTA -> {
                val sectionId = data.jsonString("section_id")
                val delta = data.jsonString("delta")
                val applied = state.liveText[sectionId].orEmpty()
                if (applied.endsWithDelta(delta)) {
                    // The same frame again: it is the text the section already
                    // ends with. A run replays the deltas of the section it
                    // is drafting when it resumes, and the window must not
                    // count one of them twice.
                    state
                } else {
                    state.copy(liveText = state.liveText + (sectionId to applied + delta))
                }
            }

            EventType.CLAIM_VERIFIED -> {
                val claim = claimFromWire(data.jsonObject("claim"))
                state.copy(claims = state.claims.replaceWhere({ it.claimId == claim.claimId }, claim))
            }

            EventType.CITATION_CREATED -> {
                val lineage = citationLineageFromWire(data.jsonObject("citation"))
                state.copy(
                    lineages = state.lineages.replaceWhere(
                        { it.lineageId == lineage.lineageId },
                        lineage,
                    ),
                )
            }

            EventType.SCORECARD_UPDATE -> state.copy(
                scorecard = verificationScorecardFromWire(data.jsonObject("scorecard")),
            )

            EventType.STREAM_METRICS -> state.copy(
                metrics = RunMetrics(
                    tokenCount = data.jsonInt("token_count"),
                    latencyMs = data.jsonInt("latency_ms"),
                    groundingScore = data.jsonDouble("grounding_score"),
                ),
            )

            EventType.HITL_REQUEST -> state.copy(
                pendingReview = PendingReview(
                    reason = data.jsonString("reason"),
                    requiresHitl = data.jsonBool("requires_hitl"),
                    maxSectionRetries = data.jsonInt("max_section_retries"),
                ),
                status = "awaiting_human_review",
            )

            EventType.WORKFLOW_COMPLETE -> state.copy(
                status = "completed",
                pendingReview = null,
                error = null,
                sections = data.jsonObjectList("sections").map { sectionDraftFromWire(it) },
                scorecard = data.jsonObject("scorecard")
                    .takeIf { it.isNotEmpty() }
                    ?.let { verificationScorecardFromWire(it) } ?: state.scorecard,
            )

            EventType.WORKFLOW_ERROR -> state.copy(
                status = "failed",
                pendingReview = null,
                error = data.jsonString("error").ifBlank { "The run failed." },
            )

            EventType.NODE_START -> state.copy(currentStep = event.step.wire)

            EventType.NODE_COMPLETE -> state
        }
    }
}

/**
 * Whether this text already ends with [delta].
 *
 * A frame of streamed text carries no id in its data -- the wire gives a
 * `token_delta` a `section_id` and a `delta` and nothing else -- so the only
 * thing the reducer can compare is the text the section already holds. The
 * question that matters is "was this frame the last thing appended?", and
 * that is a question about the *tail* of the section: the section holds the
 * frames it has applied in order, so the last frame it applied is its tail.
 * A frame whose text the section already ends with is therefore taken for
 * that last frame arriving a second time, and is not counted twice.
 *
 * The test is a suffix test on purpose. A frame whose text occurs
 * *somewhere* earlier in the section is a new frame, not a replay: a drafted
 * section repeats itself -- a boilerplate sentence, a repeated table row --
 * and the backend cuts it into frames of up to 160 characters, so a frame
 * carries text the section already holds further up. Dropping such a frame
 * would truncate the section silently, which is the worse failure of the
 * two.
 *
 * What this rule does not catch, and does not claim to: a replay of a whole
 * *sequence* of frames (only the frame that matches the current tail is
 * recognised), and a new frame whose text happens to be identical to the
 * section's current tail (the rule cannot tell that frame from the one it
 * repeats). Catching those needs the frame's id, which the reducer is not
 * given.
 */
private fun String.endsWithDelta(delta: String): Boolean =
    delta.isNotEmpty() && endsWith(delta)

/** Merge a list of records into another, replacing the ones whose key already
 * exists.
 *
 * This is what makes a repeated frame idempotent: the second copy of a claim
 * replaces the first rather than joining it.
 */
private fun <T> List<T>.mergeBy(incoming: List<T>, key: (T) -> String): List<T> {
    val merged = LinkedHashMap<String, T>()
    for (record in this) merged[key(record)] = record
    for (record in incoming) merged[key(record)] = record
    return merged.values.toList()
}

/** Replace the first record that matches, or append the record when none does. */
private fun <T> List<T>.replaceWhere(predicate: (T) -> Boolean, replacement: T): List<T> {
    val index = indexOfFirst(predicate)
    if (index < 0) return this + replacement
    val copy = toMutableList()
    copy[index] = replacement
    return copy
}
