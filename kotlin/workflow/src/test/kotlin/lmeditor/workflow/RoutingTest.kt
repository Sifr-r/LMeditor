package lmeditor.workflow

import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import lmeditor.core.ValidationError
import lmeditor.core.WorkflowDeadlockError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The conditional routing rules and the retry bound.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_workflow.py::test_conditional_edges_routing` and
 * `backend/tests/test_workflow_options.py`.
 *
 * The thresholds and bounds asserted here are the shared facts between the
 * two tiers: they decide whether a document is shipped or bounced to a
 * human, so a drift in any of them changes what the product does.
 */
class RoutingTest {

    private fun draft(
        sectionId: String,
        verified: Boolean,
        status: SectionStatus = SectionStatus.PENDING,
    ) = SectionDraft(
        sectionId = sectionId,
        title = sectionId,
        content = "Content of $sectionId.",
        isVerified = verified,
        status = status,
    )

    // --- The shared numeric facts -----------------------------------------

    @Test
    fun `the shared retry budget is three revision cycles`() {
        assertEquals(3, Routing.MAX_SECTION_RETRIES)
    }

    @Test
    fun `the shared groundedness threshold is zero point eight five`() {
        assertEquals(0.85, Routing.DEFAULT_F_BETA_THRESHOLD)
    }

    @Test
    fun `the state default and the routing constant are the same number`() {
        // The state module writes its defaults out rather than importing
        // them, so the two copies must be checked against each other.
        val state = createInitialHarnessState(docId = "d", topic = "t")

        assertEquals(Routing.MAX_SECTION_RETRIES, state.maxRetries)
        assertEquals(Routing.DEFAULT_F_BETA_THRESHOLD, state.thresholdFBeta)
    }

    // --- Routing after verification ----------------------------------------

    @Test
    fun `a state with no sections is all verified`() {
        val state = HarnessState(docId = "d", topic = "t")

        assertEquals(VerificationRoute.ALL_VERIFIED, Routing.routeAfterVerification(state))
    }

    @Test
    fun `a verified section routes to all verified`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf("sec_1" to draft("sec_1", verified = true)),
            retryCounts = mapOf("sec_1" to 0),
        )

        assertEquals(VerificationRoute.ALL_VERIFIED, Routing.routeAfterVerification(state))
    }

    @Test
    fun `an unverified section below the budget routes to needs refinement`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 1),
        )

        assertEquals(VerificationRoute.NEEDS_REFINEMENT, Routing.routeAfterVerification(state))
    }

    @Test
    fun `an unverified section at the budget routes to the human gate`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 3),
        )

        assertEquals(VerificationRoute.HITL_REQUIRED, Routing.routeAfterVerification(state))
    }

    @Test
    fun `the verified status alone is enough to pass a section`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_3" to draft("sec_3", verified = false, status = SectionStatus.VERIFIED),
            ),
            retryCounts = mapOf("sec_3" to 3),
        )

        assertEquals(VerificationRoute.ALL_VERIFIED, Routing.routeAfterVerification(state))
    }

    @Test
    fun `routing is pure and repeatable`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 1),
        )

        assertEquals(
            Routing.routeAfterVerification(state),
            Routing.routeAfterVerification(state),
        )
    }

    // --- Backward compatibility with checkpoints written before the keys ----

    @Test
    fun `a checkpoint without the option keys still routes on the defaults`() {
        val unverified = draft("sec_legacy", verified = false, status = SectionStatus.NEEDS_REVISION)

        val belowDefault = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf("sec_legacy" to unverified),
            retryCounts = mapOf("sec_legacy" to Routing.MAX_SECTION_RETRIES - 1),
        )
        val atDefault = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf("sec_legacy" to unverified),
            retryCounts = mapOf("sec_legacy" to Routing.MAX_SECTION_RETRIES),
        )

        assertEquals(null, belowDefault.maxRetries)
        assertEquals(null, belowDefault.thresholdFBeta)
        assertEquals(VerificationRoute.NEEDS_REFINEMENT, Routing.routeAfterVerification(belowDefault))
        assertEquals(VerificationRoute.HITL_REQUIRED, Routing.routeAfterVerification(atDefault))
    }

    @Test
    fun `the option and its absence disagree on the same run`() {
        // Both states describe the identical run -- one unverified section
        // with one recorded retry. With the key present the budget is 1 and
        // the run is spent; without it the router falls back to
        // MAX_SECTION_RETRIES.
        val unverified = draft("sec_legacy", verified = false, status = SectionStatus.NEEDS_REVISION)

        val withOption = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf("sec_legacy" to unverified),
            retryCounts = mapOf("sec_legacy" to 1),
            maxRetries = 1,
        )
        val withoutOption = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf("sec_legacy" to unverified),
            retryCounts = mapOf("sec_legacy" to 1),
        )

        assertEquals(VerificationRoute.HITL_REQUIRED, Routing.routeAfterVerification(withOption))
        assertEquals(VerificationRoute.NEEDS_REFINEMENT, Routing.routeAfterVerification(withoutOption))
    }

    // --- Routing after the human gate ---------------------------------------

    @Test
    fun `an approved run proceeds to the macro step`() {
        val state = HarnessState(docId = "d", topic = "t", hitlApproved = true)

        assertEquals(HitlRoute.PROCEED_TO_MACRO, Routing.routeAfterHitl(state))
    }

    @Test
    fun `a rejected run terminates`() {
        val state = HarnessState(docId = "d", topic = "t", hitlApproved = false)

        assertEquals(HitlRoute.TERMINATE, Routing.routeAfterHitl(state))
    }

    @Test
    fun `a run that never reached the gate terminates`() {
        assertEquals(HitlRoute.TERMINATE, Routing.routeAfterHitl(HarnessState()))
    }

    @Test
    fun `recording the decision is idempotent`() {
        val state = createInitialHarnessState(docId = "d", topic = "t").copy(requiresHitl = true)

        val once = Routing.applyHitlDecision(state, approved = true)
        val twice = Routing.applyHitlDecision(once, approved = true)

        assertEquals(once, twice)
        assertTrue(once.hitlApproved)
        assertEquals(false, once.requiresHitl)
        assertEquals(STATUS_HUMAN_REVIEW_APPROVED, once.status)
        assertEquals(HITL_REASON, once.hitlReason)
    }

    @Test
    fun `a rejected decision is recorded as a rejection`() {
        val state = createInitialHarnessState(docId = "d", topic = "t").copy(requiresHitl = true)

        val decided = Routing.applyHitlDecision(state, approved = false)

        assertEquals(false, decided.hitlApproved)
        assertEquals(STATUS_HUMAN_REVIEW_REJECTED, decided.status)
        assertEquals(HitlRoute.TERMINATE, Routing.routeAfterHitl(decided))
    }

    // --- The retry bound ----------------------------------------------------

    @Test
    fun `a section below the budget does not deadlock the run`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 2),
        )

        Routing.requireRetryBudget(state, currentNode = "critique_revision")
    }

    @Test
    fun `a spent retry budget raises the deadlock error`() {
        val state = HarnessState(
            docId = "doc_deadlock",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 3),
        )

        val error = assertFailsWith<WorkflowDeadlockError> {
            Routing.requireRetryBudget(state, currentNode = "critique_revision")
        }

        assertEquals("critique_revision", error.currentNode)
        assertEquals("doc_deadlock", error.docId)
        assertEquals(3, error.retryCount)
        assertEquals(3, error.maxRetries)
        assertTrue(error.message.contains("sec_2"))
    }

    @Test
    fun `the exhausted sections are the unverified ones at the budget`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_ok" to draft("sec_ok", verified = true),
                "sec_spent" to draft("sec_spent", verified = false),
                "sec_fresh" to draft("sec_fresh", verified = false),
            ),
            retryCounts = mapOf("sec_ok" to 3, "sec_spent" to 3, "sec_fresh" to 1),
        )

        assertEquals(listOf("sec_spent"), Routing.exhaustedSections(state))
    }

    @Test
    fun `a custom budget moves the boundary`() {
        val state = HarnessState(
            docId = "d",
            topic = "t",
            sectionDrafts = mapOf(
                "sec_2" to draft("sec_2", verified = false, status = SectionStatus.NEEDS_REVISION),
            ),
            retryCounts = mapOf("sec_2" to 1),
            maxRetries = 1,
        )

        assertEquals(VerificationRoute.HITL_REQUIRED, Routing.routeAfterVerification(state))
        assertEquals(listOf("sec_2"), Routing.exhaustedSections(state))
    }

    // --- The wire vocabulary ------------------------------------------------

    @Test
    fun `the route wire strings are the ones the graph maps`() {
        assertEquals("all_verified", VerificationRoute.ALL_VERIFIED.wire)
        assertEquals("needs_refinement", VerificationRoute.NEEDS_REFINEMENT.wire)
        assertEquals("hitl_required", VerificationRoute.HITL_REQUIRED.wire)
        assertEquals("proceed_to_macro", HitlRoute.PROCEED_TO_MACRO.wire)
        assertEquals("terminate", HitlRoute.TERMINATE.wire)
    }

    @Test
    fun `an unknown route wire value is refused with a typed error`() {
        assertFailsWith<ValidationError> { VerificationRoute.fromWire("nonsense") }
        assertFailsWith<ValidationError> { HitlRoute.fromWire("nonsense") }
    }
}
