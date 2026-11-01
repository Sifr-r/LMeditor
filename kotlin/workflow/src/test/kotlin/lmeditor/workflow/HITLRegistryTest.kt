package lmeditor.workflow

import lmeditor.core.InvalidStateTransitionError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The registry of runs suspended at the human-in-the-loop gate.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_hitl_gate.py::test_resume_workflow_rejects_unknown_thread_id`
 * and `::test_release_workflow_drops_an_abandoned_document`.
 */
class HITLRegistryTest {

    @Test
    fun `an unregistered thread raises a typed domain error`() {
        val registry = HITLRegistry()

        assertFalse(registry.isRegistered("doc_never_started"))

        val error = assertFailsWith<InvalidStateTransitionError> {
            registry.requireResumable("doc_never_started")
        }

        assertTrue(error.message.contains("doc_never_started"))
    }

    @Test
    fun `a registered thread is resumable`() {
        val registry = HITLRegistry()
        val state = createInitialHarnessState(docId = "doc_1", topic = "t")

        registry.register("doc_1", state)

        assertTrue(registry.isRegistered("doc_1"))
        assertEquals(state, registry.requireResumable("doc_1"))
        assertEquals(listOf("doc_1"), registry.registeredThreadIds())
    }

    @Test
    fun `registering the same thread replaces rather than appends`() {
        val registry = HITLRegistry()
        val first = createInitialHarnessState(docId = "doc_1", topic = "first")
        val second = createInitialHarnessState(docId = "doc_1", topic = "second")

        registry.register("doc_1", first)
        registry.register("doc_1", second)

        assertEquals(listOf("doc_1"), registry.registeredThreadIds())
        assertEquals(second, registry.requireResumable("doc_1"))
    }

    @Test
    fun `releasing a thread drops the abandoned run`() {
        val registry = HITLRegistry()
        registry.register("doc_abandoned", createInitialHarnessState(docId = "doc_abandoned", topic = "t"))

        assertTrue(registry.release("doc_abandoned"))

        assertFalse(registry.isRegistered("doc_abandoned"))
        assertNull(registry.state("doc_abandoned"))
        assertFailsWith<InvalidStateTransitionError> { registry.requireResumable("doc_abandoned") }
    }

    @Test
    fun `releasing a thread that was never registered reports it`() {
        assertFalse(HITLRegistry().release("doc_unknown"))
    }

    @Test
    fun `the registry keeps the threads in registration order`() {
        val registry = HITLRegistry()

        registry.register("doc_a", createInitialHarnessState(docId = "doc_a", topic = "t"))
        registry.register("doc_b", createInitialHarnessState(docId = "doc_b", topic = "t"))

        assertEquals(listOf("doc_a", "doc_b"), registry.registeredThreadIds())
    }
}
