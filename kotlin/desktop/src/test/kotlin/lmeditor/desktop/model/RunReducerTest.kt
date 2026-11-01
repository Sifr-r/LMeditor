package lmeditor.desktop.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import lmeditor.core.EventType
import lmeditor.core.WorkflowEvent
import lmeditor.core.WorkflowStep

/**
 * The rules the event reduction follows.
 *
 * The payload shapes here are the ones the backend publishes, and they are
 * pinned deliberately: they are the shared fact between the two halves of
 * the system, and a test that pins them is the test that catches the drift
 * when one side changes a name.
 */
class RunReducerTest {

    @Test
    fun `events for another document cannot mutate an active run`() {
        val state = StudioState(docId = "other_doc", status = "running")
        assertEquals(state, RunReducer.reduce(state, claimVerified("c1", "A claim.")))
    }

    @Test
    fun `terminal frames clear the review and successful completion clears the error`() {
        val waiting = StudioState(
            docId = "doc_1",
            pendingReview = PendingReview("Review needed", true, 1),
            error = "Earlier failure",
        )
        val complete = WorkflowEvent(EventType.WORKFLOW_COMPLETE, WorkflowStep.COMPLETE, "doc_1")
        val completed = RunReducer.reduce(waiting, complete)
        assertEquals(null, completed.pendingReview)
        assertEquals(null, completed.error)
        val failed = RunReducer.reduce(waiting, WorkflowEvent(
            EventType.WORKFLOW_ERROR, WorkflowStep.COMPLETE, "doc_1", data = mapOf("error" to "Failed"),
        ))
        assertEquals(null, failed.pendingReview)
        assertEquals("Failed", failed.error)
    }

    @Test
    fun `a claim verified twice leaves one claim`() {
        val frame = claimVerified("c1", "Rates fell by 30 percent.")

        val once = RunReducer.reduce(StudioState(), frame)
        val twice = RunReducer.reduce(once, frame)

        assertEquals(1, twice.claims.size)
        assertEquals("c1", twice.claims[0].claimId)
    }

    @Test
    fun `a chunk published twice leaves one chunk`() {
        val chunkId = "a".repeat(64)
        val frame = WorkflowEvent(
            eventType = EventType.CHUNKS,
            step = WorkflowStep.INGEST,
            docId = "doc_1",
            data = mapOf(
                "chunks" to listOf(
                    mapOf(
                        "chunk_id" to chunkId,
                        "document_id" to "doc_1",
                        "text" to "A passage.",
                        "char_start" to 0,
                        "char_end" to 10,
                    ),
                ),
            ),
        )

        val once = RunReducer.reduce(StudioState(), frame)
        val twice = RunReducer.reduce(once, frame)

        assertEquals(1, twice.chunks.size)
        assertEquals(chunkId, twice.chunks[0].chunkId)
    }

    @Test
    fun `the canonical outline payload decodes with the tier's decoder`() {
        val frame = WorkflowEvent(
            eventType = EventType.OUTLINE,
            step = WorkflowStep.OUTLINE,
            docId = "doc_1",
            data = mapOf(
                "outline" to listOf(
                    mapOf(
                        "node_id" to "sec_1",
                        "title" to "Introduction",
                        "section_id" to "sec_1",
                        "depth" to 0,
                        "description" to "The opening",
                        "assigned_chunk_ids" to listOf("chunk_1"),
                        "children" to listOf(
                            mapOf(
                                "node_id" to "sec_1_1",
                                "title" to "Background",
                                "section_id" to "sec_1_1",
                                "depth" to 1,
                                "description" to "How it got there",
                                "assigned_chunk_ids" to emptyList<String>(),
                                "children" to emptyList<Any?>(),
                                "order" to 0,
                                "subheadings" to emptyList<String>(),
                            ),
                        ),
                        "order" to 0,
                        "subheadings" to emptyList<String>(),
                    ),
                ),
            ),
        )

        val state = RunReducer.reduce(StudioState(), frame)

        assertEquals(1, state.outline.size)
        val node = state.outline[0]
        assertEquals("sec_1", node.nodeId)
        assertEquals("Introduction", node.title)
        assertEquals("The opening", node.description)
        assertEquals(listOf("chunk_1"), node.assignedChunkIds)
        assertEquals(1, node.children.size)
        assertEquals("sec_1_1", node.children[0].nodeId)
    }

    @Test
    fun `a review request arms the gate with its reason`() {
        val frame = WorkflowEvent(
            eventType = EventType.HITL_REQUEST,
            step = WorkflowStep.HITL_GATE,
            docId = "doc_1",
            data = mapOf(
                "reason" to "Two claims could not be grounded.",
                "requires_hitl" to true,
                "max_section_retries" to 2,
            ),
        )

        val state = RunReducer.reduce(StudioState(), frame)

        val review = assertNotNull(state.pendingReview)
        assertEquals("Two claims could not be grounded.", review.reason)
        assertTrue(review.requiresHitl)
        assertEquals(2, review.maxSectionRetries)
        assertEquals("awaiting_human_review", state.status)
    }

    @Test
    fun `the terminal frame carries the sections and the scorecard`() {
        val frame = WorkflowEvent(
            eventType = EventType.WORKFLOW_COMPLETE,
            step = WorkflowStep.COMPLETE,
            docId = "doc_1",
            data = mapOf(
                "sections" to listOf(
                    mapOf(
                        "section_id" to "sec_1",
                        "title" to "Introduction",
                        "content" to "Rates fell [#chunk_1].",
                        "order" to 0,
                        "claims" to listOf(
                            mapOf(
                                "claim_id" to "c1",
                                "section_id" to "sec_1",
                                "text" to "Rates fell.",
                                "char_start" to 0,
                                "char_end" to 11,
                                "cited_chunk_ids" to listOf("chunk_1"),
                                "entailment_status" to "entailed",
                                "entailment_score" to 0.9,
                            ),
                        ),
                        "citations" to emptyList<Any?>(),
                        "is_verified" to true,
                        "status" to "verified",
                        "revision_count" to 0,
                        "cited_chunk_ids" to listOf("chunk_1"),
                    ),
                ),
                "scorecard" to mapOf(
                    "precision" to 1.0,
                    "recall" to 1.0,
                    "f_beta" to 1.0,
                    "total_claims" to 1,
                    "supported_claims" to 1,
                    "ungrounded_claims" to 0,
                    "contradicted_claims" to 0,
                    "neutral_claims" to 0,
                ),
            ),
        )

        val state = RunReducer.reduce(StudioState(), frame)

        assertEquals("completed", state.status)
        assertEquals(1, state.sections.size)
        assertEquals("Introduction", state.sections[0].title)
        assertEquals(1, state.sections[0].claims.size)
        val card = assertNotNull(state.scorecard)
        assertEquals(1.0, card.fBeta)
        assertEquals(1, card.totalClaims)
    }

    @Test
    fun `a failed run says so`() {
        val frame = WorkflowEvent(
            eventType = EventType.WORKFLOW_ERROR,
            step = WorkflowStep.COMPLETE,
            docId = "doc_1",
            data = mapOf("error" to "The model refused."),
        )

        val state = RunReducer.reduce(StudioState(), frame)

        assertEquals("failed", state.status)
        assertEquals("The model refused.", state.error)
    }

    @Test
    fun `streamed text accumulates per section`() {
        fun delta(sectionId: String, text: String) = WorkflowEvent(
            eventType = EventType.TOKEN_DELTA,
            step = WorkflowStep.DRAFT_SECTIONS,
            docId = "doc_1",
            data = mapOf("section_id" to sectionId, "delta" to text),
        )

        val state = RunReducer.reduce(
            RunReducer.reduce(StudioState(), delta("sec_1", "One ")),
            delta("sec_1", "two."),
        )

        assertEquals("One two.", state.liveText["sec_1"])
    }

    @Test
    fun `a token delta applied twice leaves the section text alone`() {
        val frame = tokenDelta("sec_1", "Rates fell by 30 percent. ")

        val once = RunReducer.reduce(StudioState(), frame)
        val twice = RunReducer.reduce(once, frame)

        assertEquals("Rates fell by 30 percent. ", once.liveText["sec_1"])
        assertEquals(once.liveText["sec_1"], twice.liveText["sec_1"])
    }

    @Test
    fun `a replayed frame is caught only when it is the section's current tail`() {
        val first = tokenDelta("sec_1", "Rates fell ")
        val second = tokenDelta("sec_1", "by 30 percent. ")

        val streamed = RunReducer.reduce(RunReducer.reduce(StudioState(), first), second)
        val tail = RunReducer.reduce(streamed, second)
        val whole = RunReducer.reduce(RunReducer.reduce(streamed, first), second)

        assertEquals("Rates fell by 30 percent. ", streamed.liveText["sec_1"])
        // The frame the section already ends with is a replay, and it is not
        // counted twice.
        assertEquals(streamed.liveText["sec_1"], tail.liveText["sec_1"])
        // A replay of the whole sequence is not caught: the first frame of
        // the replay is not what the section ends with, so it is appended.
        // Nothing is lost -- the section keeps every character it was given
        // -- but it is counted twice. The reducer cannot tell that frame from
        // a new frame that carries the same text, because the state and the
        // frame it is given are the same in both cases; catching it needs an
        // id on the frame, which the wire does not carry yet.
        assertEquals(
            "Rates fell by 30 percent. Rates fell by 30 percent. ",
            whole.liveText["sec_1"],
        )
    }

    @Test
    fun `two different token deltas still accumulate`() {
        val state = RunReducer.reduce(
            RunReducer.reduce(StudioState(), tokenDelta("sec_1", "One ")),
            tokenDelta("sec_1", "two."),
        )

        assertEquals("One two.", state.liveText["sec_1"])
    }

    @Test
    fun `a new frame whose text occurred earlier in the section is appended, not dropped`() {
        // A drafted section that repeats itself. The backend splits drafted
        // text into the frames it publishes as `token_delta` events, so the
        // last frame here carries text the section already holds further up:
        // it is a new frame, not a replay, and dropping it truncates the
        // section without a word of complaint.
        val text = repeatedSection()
        val frames = textDeltas(text)

        assertEquals(3, frames.size)
        assertEquals(listOf(159, 160, 47), frames.map { it.length })
        assertTrue(
            (frames[0] + frames[1]).contains(frames[2]),
            "the last frame must occur earlier in the section",
        )
        assertEquals(text, frames.joinToString(""))

        val state = frames.fold(StudioState()) { acc, frame ->
            RunReducer.reduce(acc, tokenDelta("sec_1", frame))
        }

        assertEquals(text, state.liveText["sec_1"])
    }

    @Test
    fun `a repeated table row does not truncate the section`() {
        // The same loss in the shape a drafted table takes: the last frame
        // carries a row the section already holds.
        val row = "| North | 120 | -30% |\n| South | 95 | -30% |\n| East | 140 | -30% |\n"
        val text = "| Region | Revenue | Change |\n" + row + row
        val frames = textDeltas(text)

        assertEquals(2, frames.size)
        assertTrue(
            frames[0].contains(frames[1]),
            "the last frame must occur earlier in the section",
        )

        val state = frames.fold(StudioState()) { acc, frame ->
            RunReducer.reduce(acc, tokenDelta("sec_1", frame))
        }

        assertEquals(text, state.liveText["sec_1"])
    }

    @Test
    fun `a frame that repeats the section's current tail is taken for a replay`() {
        // The limitation the reducer states about itself, pinned: a frame
        // whose text is identical to the section's current tail cannot be
        // told from the frame it repeats. Catching that needs an id on the
        // frame, which the wire does not carry yet.
        val tail = "percent in every measured quarter of the year. "

        val once = RunReducer.reduce(StudioState(), tokenDelta("sec_1", tail))
        val twice = RunReducer.reduce(once, tokenDelta("sec_1", tail))

        assertEquals(tail, once.liveText["sec_1"])
        assertEquals(tail, twice.liveText["sec_1"])
    }

    /** The text of a section that repeats a sentence, as the backend drafts it. */
    private fun repeatedSection(): String {
        val repeated = "The margin fell by thirty percent in every measured quarter of the year. "
        val between = "Analysts had warned that the same pattern would carry into the next year. "
        return repeated + repeated + between + repeated + repeated
    }

    /**
     * The backend's chunking rule, ported.
     *
     * `backend/src/lmeditor/workflow/graph.py` `_iter_text_deltas` splits
     * drafted text into the frames it publishes as `token_delta` events: it
     * walks the text token by token -- a run of non-space characters plus the
     * whitespace that follows it -- and flushes the buffer whenever the next
     * token would push it past [maxChars]. A token longer than the limit is
     * cut into pieces of the limit.
     */
    private fun textDeltas(text: String, maxChars: Int = 160): List<String> {
        val frames = mutableListOf<String>()
        var buffer = ""
        for (match in TOKEN_PATTERN.findAll(text)) {
            var token = match.value
            while (token.length > maxChars) {
                if (buffer.isNotEmpty()) {
                    frames += buffer
                    buffer = ""
                }
                frames += token.take(maxChars)
                token = token.drop(maxChars)
            }
            if (buffer.length + token.length > maxChars) {
                frames += buffer
                buffer = ""
            }
            buffer += token
        }
        if (buffer.isNotEmpty()) frames += buffer
        return frames
    }

    /** A frame carrying one piece of streamed text. */
    private fun tokenDelta(sectionId: String, text: String): WorkflowEvent = WorkflowEvent(
        eventType = EventType.TOKEN_DELTA,
        step = WorkflowStep.DRAFT_SECTIONS,
        docId = "doc_1",
        data = mapOf("section_id" to sectionId, "delta" to text),
    )

    /** A frame carrying one verified claim. */
    private fun claimVerified(claimId: String, text: String): WorkflowEvent = WorkflowEvent(
        eventType = EventType.CLAIM_VERIFIED,
        step = WorkflowStep.VERIFY_CITATIONS,
        docId = "doc_1",
        data = mapOf(
            "section_id" to "sec_1",
            "claim" to mapOf(
                "claim_id" to claimId,
                "section_id" to "sec_1",
                "text" to text,
                "char_start" to 0,
                "char_end" to text.length,
                "cited_chunk_ids" to listOf("chunk_1"),
                "entailment_status" to "entailed",
                "entailment_score" to 0.9,
            ),
        ),
    )

    private companion object {
        /**
         * The backend's token pattern: a run of non-space characters and the
         * whitespace that follows it.
         */
        val TOKEN_PATTERN = Regex("\\S+\\s*")
    }
}
