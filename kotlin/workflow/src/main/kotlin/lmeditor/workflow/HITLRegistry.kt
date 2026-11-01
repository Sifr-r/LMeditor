package lmeditor.workflow

import lmeditor.core.InvalidStateTransitionError

/**
 * The registry of runs suspended at the human-in-the-loop gate.
 *
 * A run that is waiting for a human is retained under its `doc_id` so the
 * operator's decision can be applied to it later. The Python tier keeps the
 * compiled LangGraph in this registry; the Kotlin tier keeps the state it
 * last saw, which is the half of the decision that is deterministic.
 *
 * The registry is the source of the tier's illegal-transition error. A run
 * that never started, that already reached a terminal state, or that was
 * explicitly released cannot be resumed: resuming it would silently start a
 * fresh run in place of the operator's decision, so it raises
 * [InvalidStateTransitionError] instead.
 *
 * Every operation is idempotent. Registering the same thread twice replaces
 * the entry rather than appending a second one, so replaying a run's
 * suspension leaves the registry in the same shape.
 *
 * The registry is shared between the run that suspends and the request that
 * resumes it, so its critical sections are guarded.
 */
class HITLRegistry {
    private val entries = LinkedHashMap<String, HarnessState>()

    /**
     * Retain [state] under [threadId] as a run waiting for a decision.
     *
     * A thread that is already registered is replaced, not duplicated.
     */
    @Synchronized
    fun register(threadId: String, state: HarnessState) {
        entries[threadId] = state
    }

    /** Whether a run is retained under [threadId]. */
    @Synchronized
    fun isRegistered(threadId: String): Boolean = entries.containsKey(threadId)

    /** The thread ids currently retained, in registration order. */
    @Synchronized
    fun registeredThreadIds(): List<String> = entries.keys.toList()

    /** The state retained under [threadId], or `null` when there is none. */
    @Synchronized
    fun state(threadId: String): HarnessState? = entries[threadId]

    /**
     * Drop the run retained under [threadId].
     *
     * @return `true` when a run was dropped, `false` when there was none.
     */
    @Synchronized
    fun release(threadId: String): Boolean = entries.remove(threadId) != null

    /**
     * The state of the run retained under [threadId].
     *
     * @throws InvalidStateTransitionError when no run is retained under
     *   [threadId].
     */
    @Synchronized
    fun requireResumable(threadId: String): HarnessState = entries[threadId]
        ?: throw InvalidStateTransitionError(
            "No resumable workflow is registered for thread_id='$threadId'. " +
                "The document may never have started, may already have reached a " +
                "terminal state, or may have been released. Call run_workflow(...) first.",
        )
}
