package lmeditor.workflow

import lmeditor.core.SectionStatus
import lmeditor.core.ValidationError
import lmeditor.core.WireEnum
import lmeditor.core.WorkflowDeadlockError

/**
 * The conditional routing rules of the writing harness.
 *
 * Port of `backend/src/lmeditor/workflow/edges.py`, plus the deterministic
 * half of the two decisions the Python tier leaves implicit:
 *
 *  * the *retry bound*. The Python router bounces an exhausted section to the
 *    human gate; [Routing.requireRetryBudget] is the same bound expressed as
 *    the typed error the tier reserves for it, so a caller that must not
 *    bounce learns that the budget is spent instead of retrying forever.
 *  * the *gate transition*. [Routing.applyHitlDecision] records the
 *    operator's decision the way the Python checkpoint node records it, and is
 *    idempotent: applying the same decision twice leaves the same state.
 *
 * Every threshold and bound here is numerically identical to the Python
 * original. `RoutingTest` asserts that against the constants below.
 */

/**
 * Where the run goes after the verification step.
 *
 * The wire strings are the keys the Python conditional edge maps to node
 * names, so they cross the NDJSON seam unchanged.
 */
enum class VerificationRoute(override val wire: String) : WireEnum {
    ALL_VERIFIED("all_verified"),
    NEEDS_REFINEMENT("needs_refinement"),
    HITL_REQUIRED("hitl_required");

    companion object {
        /** Parse a wire value, or fail with a typed validation error. */
        fun fromWire(value: String): VerificationRoute =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown verification route '$value'",
                    field = "verification_route",
                    value = value,
                )
    }
}

/** Where the run goes after the human-in-the-loop gate. */
enum class HitlRoute(override val wire: String) : WireEnum {
    PROCEED_TO_MACRO("proceed_to_macro"),
    TERMINATE("terminate");

    companion object {
        /** Parse a wire value, or fail with a typed validation error. */
        fun fromWire(value: String): HitlRoute =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown hitl route '$value'",
                    field = "hitl_route",
                    value = value,
                )
    }
}

/** The reason a run is parked at the human-in-the-loop gate. */
const val HITL_REASON: String =
    "One or more sections exceeded the retry threshold or contained ungrounded claims."

/** The status a run carries once the operator approved it. */
const val STATUS_HUMAN_REVIEW_APPROVED: String = "human_review_approved"

/** The status a run carries once the operator rejected it. */
const val STATUS_HUMAN_REVIEW_REJECTED: String = "human_review_rejected"

/** The conditional routing rules, the retry bound and the gate transition. */
object Routing {
    /**
     * The number of revision cycles a section is bought.
     *
     * Identical to `workflow.edges.MAX_SECTION_RETRIES`.
     */
    const val MAX_SECTION_RETRIES: Int = 3

    /**
     * The groundedness threshold a scorecard must reach to count as verified.
     *
     * Identical to `verification.citation_checker.DEFAULT_F_BETA_THRESHOLD`.
     * The verification stack is not a dependency of this module, so the value
     * is written out; `RoutingTest` asserts it against the state default.
     */
    const val DEFAULT_F_BETA_THRESHOLD: Double = 0.85

    /**
     * Decide whether the sections passed verification or need redrafting or
     * human intervention.
     *
     * [HarnessState.maxRetries] is the caller-supplied budget. The constant
     * is only the fallback for a state that predates the key: the state is
     * read through `.get(key, <default>)` semantics, so a checkpoint written
     * before the option existed still loads and still routes on the default.
     *
     * The decision order is the Python original's, unchanged:
     *  1. no sections at all is "everything is verified";
     *  2. every section verified (either flagged verified or carrying the
     *     verified status) is "everything is verified";
     *  3. a section whose retry count has reached the budget is "a human is
     *     needed";
     *  4. anything else is "refine it again".
     */
    fun routeAfterVerification(state: HarnessState): VerificationRoute {
        val sectionDrafts = state.sectionDrafts
        val retryCounts = state.retryCounts
        val retryBudget = state.maxRetries ?: MAX_SECTION_RETRIES

        if (sectionDrafts.isEmpty()) return VerificationRoute.ALL_VERIFIED

        var allVerified = true
        var hasExhaustedRetries = false

        for ((sectionId, draft) in sectionDrafts) {
            if (!draft.isVerified && draft.status != SectionStatus.VERIFIED) {
                allVerified = false
                val retries = retryCounts[sectionId] ?: 0
                if (retries >= retryBudget) hasExhaustedRetries = true
            }
        }

        if (allVerified) return VerificationRoute.ALL_VERIFIED
        if (hasExhaustedRetries) return VerificationRoute.HITL_REQUIRED
        return VerificationRoute.NEEDS_REFINEMENT
    }

    /**
     * Route on the human operator's approval.
     *
     * A run that is not approved terminates; anything else proceeds to the
     * macro-cohesion step.
     */
    fun routeAfterHitl(state: HarnessState): HitlRoute =
        if (state.hitlApproved) HitlRoute.PROCEED_TO_MACRO else HitlRoute.TERMINATE

    /**
     * The sections that cannot be refined again because their retry budget is
     * spent, in state order.
     *
     * A section counts as spent when it is not verified and its recorded
     * retry count has reached the budget. The comparison is `>=`, the same
     * comparison the router uses.
     */
    fun exhaustedSections(state: HarnessState): List<String> {
        val retryBudget = state.maxRetries ?: MAX_SECTION_RETRIES
        return state.sectionDrafts.entries
            .filter { (sectionId, draft) ->
                !draft.isVerified && draft.status != SectionStatus.VERIFIED &&
                    (state.retryCounts[sectionId] ?: 0) >= retryBudget
            }
            .map { (sectionId, _) -> sectionId }
    }

    /**
     * The guard on the refinement loop.
     *
     * A section that has spent its retry budget cannot be sent back to the
     * drafter: the run would circle forever. The Python tier bounces such a
     * section to the human gate; this guard is the same bound expressed as
     * the typed error the tier reserves for it, so a caller that has no human
     * to bounce to learns the budget is spent.
     *
     * @param state the state to inspect.
     * @param currentNode the node the run is at, recorded on the error.
     * @throws WorkflowDeadlockError when at least one unverified section has
     *   reached the retry budget.
     */
    fun requireRetryBudget(state: HarnessState, currentNode: String) {
        val exhausted = exhaustedSections(state)
        if (exhausted.isEmpty()) return

        val retryBudget = state.maxRetries ?: MAX_SECTION_RETRIES
        val retryCount = exhausted.maxOf { state.retryCounts[it] ?: 0 }
        throw WorkflowDeadlockError(
            currentNode = currentNode,
            stateSummary = "Section(s) ${exhausted.joinToString(", ")} exhausted the retry budget.",
            docId = state.docId,
            retryCount = retryCount,
            maxRetries = retryBudget,
        )
    }

    /**
     * Record the operator's decision at the human-in-the-loop gate.
     *
     * The decision clears the gate, records the reason the run was parked
     * there and moves the run's status, exactly as the Python checkpoint node
     * does. The transition is idempotent: applying the same decision to the
     * result again leaves the same state.
     */
    fun applyHitlDecision(state: HarnessState, approved: Boolean): HarnessState = state.copy(
        hitlApproved = approved,
        requiresHitl = false,
        hitlReason = HITL_REASON,
        status = if (approved) STATUS_HUMAN_REVIEW_APPROVED else STATUS_HUMAN_REVIEW_REJECTED,
    )
}
