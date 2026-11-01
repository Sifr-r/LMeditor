package lmeditor.discovery

import lmeditor.core.DialogueError
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.Perspective
import lmeditor.core.PerspectiveRole
import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The conversational research loop.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_discovery.py` (the dialogue section).
 */
class DialogueTest {

    private fun expert() = Perspective(
        perspectiveId = "persp_expert_1",
        id = "persp_expert_1",
        role = PerspectiveRole.DOMAIN_EXPERT,
        title = "Consensus Specialist",
        name = "Consensus Specialist",
        description = "Expert in formal distributed protocols.",
        domain = "Distributed Consensus",
        guidingQuestions = listOf(
            "What invariants govern safety in asynchronous networks?",
            "How does Byzantine resilience scale with node cluster size?",
        ),
        focusAreas = listOf("Safety Invariants", "Byzantine Resilience"),
    )

    @Test
    fun `a session holds the requested number of grounded turns`() {
        val first = TestFixtures.chunk(
            "Raft and Paxos guarantee safety under asynchronous network partitions.",
            start = 0,
        )
        val second = TestFixtures.chunk(
            "Byzantine fault tolerance requires 3f+1 nodes to withstand malicious attacks.",
            start = 200,
        )
        val indexer = MockProvenanceIndexer(listOf(first, second))

        val session = DialogueLoop(topKPerTurn = 2).conductDialogue(expert(), indexer, numTurns = 2)

        assertEquals("persp_expert_1", session.perspectiveId)
        assertEquals(2, session.turns.size)
        assertTrue(session.synthesizedTakeaways.size >= 2)

        val turn = session.turns[0]
        assertTrue("invariants" in turn.question.lowercase())
        assertTrue(turn.referencedChunkIds.isNotEmpty())
        for (chunkId in turn.referencedChunkIds) {
            assertTrue("[$chunkId]" in turn.answer)
        }
    }

    @Test
    fun `a failing indexer is reported as a typed dialogue error`() {
        val indexer = MockProvenanceIndexer(failQueries = true)
        val perspective = PerspectiveGenerator()
            .generatePerspectives(topic = "Neural Architecture Search", numPerspectives = 1)[0]

        val error = assertFailsWith<DialogueError> {
            DialogueLoop().conductDialogue(perspective, indexer, numTurns = 2)
        }

        assertEquals(perspective.id, error.details["perspective_id"])
        assertEquals(0, error.details["turn_index"] as Int)
        assertTrue(error.message.contains("timed out"))
    }

    @Test
    fun `research across several personas consolidates into one memory`() {
        val first = TestFixtures.chunk(
            "Transformers rely on multi-head scaled dot-product attention.",
            start = 0,
        )
        val second = TestFixtures.chunk(
            "Linear attention achieves sub-quadratic memory complexity.",
            start = 300,
        )
        val indexer = MockProvenanceIndexer(listOf(first, second))
        val perspectives = PerspectiveGenerator()
            .generatePerspectives("Efficient Transformer Architectures", numPerspectives = 2)

        val memory = DialogueLoop().conductAllDialogues(perspectives, indexer, turnsPerSession = 2)

        assertEquals(2, memory.sessions.size)
        assertTrue(memory.allReferencedChunkIds.isNotEmpty())
        // Discovery de-duplication: a passage is named once.
        assertEquals(memory.allReferencedChunkIds.toSet().size, memory.allReferencedChunkIds.size)
        assertTrue(memory.collectedTakeaways.size >= 4)
    }

    @Test
    fun `an answer quotes the passage that bears on the question`() {
        val chunk = TestFixtures.chunk(
            "Municipal waste collection rates fell by 30 percent between 2019 and 2023. " +
                "Kerbside recycling throughput doubled after the fleet replacement.",
            start = 0,
        )
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val perspective = Perspective(
            perspectiveId = "persp_critic_1",
            id = "persp_critic_1",
            role = PerspectiveRole.SKEPTIC,
            title = "Empirical Critic",
            name = "Empirical Critic",
            description = "Interrogates the reported figures.",
            domain = "Municipal Waste",
            guidingQuestions = listOf("What happened to waste collection rates?"),
            focusAreas = listOf("Reported collection rates"),
        )

        val session = DialogueLoop(topKPerTurn = 1).conductDialogue(perspective, indexer, numTurns = 1)

        val turn = session.turns[0]
        assertEquals(listOf(chunk.chunkId), turn.referencedChunkIds)
        assertTrue("[${chunk.chunkId}]" in turn.answer)
        // The answer quotes the passage rather than describing it in the abstract.
        assertTrue("30 percent" in turn.answer)
        assertTrue("Municipal waste collection rates fell" in turn.answer)
    }

    @Test
    fun `the takeaways name the evidence that carried the session`() {
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val perspective = Perspective(
            perspectiveId = "persp_expert_2",
            id = "persp_expert_2",
            role = PerspectiveRole.DOMAIN_EXPERT,
            title = "Domain Expert",
            name = "Domain Expert",
            description = "Reads the reported figures.",
            domain = "Municipal Waste",
            guidingQuestions = listOf("What did landfill charges do?"),
            focusAreas = listOf("Cost of residual waste"),
        )

        val session = DialogueLoop(topKPerTurn = 1).conductDialogue(perspective, indexer, numTurns = 2)

        val takeaways = session.synthesizedTakeaways.joinToString(" ")
        assertTrue(session.synthesizedTakeaways.size >= 2)
        // The takeaway names the passage that carried the session ...
        assertTrue(chunk.chunkId in takeaways)
        // ... and repeats what that passage actually said.
        assertTrue("12 percent" in takeaways)
    }

    @Test
    fun `a session with no retrieved passage says so`() {
        val indexer = MockProvenanceIndexer()
        val perspective = Perspective(
            perspectiveId = "persp_empty_1",
            id = "persp_empty_1",
            role = PerspectiveRole.GENERAL_READER,
            title = "Reader",
            name = "Reader",
            description = "Reads.",
            domain = "Municipal Waste",
            guidingQuestions = listOf("What happened to waste collection rates?"),
            focusAreas = listOf("Reported collection rates"),
        )

        val session = DialogueLoop().conductDialogue(perspective, indexer, numTurns = 1)

        assertTrue(session.turns[0].referencedChunkIds.isEmpty())
        assertTrue("ungrounded" in session.turns[0].answer.lowercase())
        assertTrue(session.synthesizedTakeaways.any { "empirically unsupported" in it })
    }

    @Test
    fun `a turn count outside one to ten is refused`() {
        val indexer = MockProvenanceIndexer()
        val loop = DialogueLoop()

        assertFailsWith<ValidationError> { loop.conductDialogue(expert(), indexer, numTurns = 0) }
        assertFailsWith<ValidationError> { loop.conductDialogue(expert(), indexer, numTurns = 11) }
    }

    @Test
    fun `an empty set of personas is refused`() {
        val indexer = MockProvenanceIndexer()

        assertFailsWith<ValidationError> { DialogueLoop().conductAllDialogues(emptyList(), indexer) }
    }

    @Test
    fun `a missing language model is reported rather than silently skipped`() {
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val loop = DialogueLoop(useModel = true, model = null)

        val error = assertFailsWith<LMNotConfiguredError> {
            loop.conductDialogue(expert(), indexer, numTurns = 1)
        }
        assertEquals("dialogue answer synthesis", error.operation)
    }

    @Test
    fun `the model frames the evidence but never the citations`() {
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val loop = DialogueLoop(useModel = true, model = FramingModel())

        val session = loop.conductDialogue(expert(), indexer, numTurns = 1)

        val answer = session.turns[0].answer
        assertTrue("The passages report" in answer)
        assertTrue("[${chunk.chunkId}]" in answer)
    }

    @Test
    fun `conducting the same dialogue twice leaves the same session`() {
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val loop = DialogueLoop()

        val once = loop.conductDialogue(expert(), indexer, numTurns = 2)
        val twice = loop.conductDialogue(expert(), indexer, numTurns = 2)

        assertEquals(once, twice)
    }

    @Test
    fun `the seam-facing pieces answer what the loop answers`() {
        // The seam cannot retrieve: the index is the Python tier's. So the
        // plan, the turn and the takeaways are asked for one at a time, and
        // they have to add up to the session the loop would have held.
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val loop = DialogueLoop()
        val perspective = expert()

        val questions = loop.planQuestions(perspective, 2)
        assertEquals(2, questions.size)
        assertTrue("invariants" in questions[0].lowercase())

        val turns = questions.mapIndexed { index, question ->
            loop.synthesizeTurn(perspective, index, question, indexer.query(question, topK = 3))
        }
        val takeaways = loop.extractTakeaways(perspective, turns)

        val session = loop.conductDialogue(perspective, indexer, numTurns = 2)
        assertEquals(session.turns, turns)
        assertEquals(session.synthesizedTakeaways, takeaways)
    }

    @Test
    fun `a framing written elsewhere frames the evidence`() {
        // The language model is the Python tier's: it writes the framing
        // sentence and the tier quotes the passages underneath it.
        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val loop = DialogueLoop()

        val turn = loop.synthesizeTurn(
            perspective = expert(),
            turnIndex = 0,
            question = "What did landfill charges do?",
            retrievedChunks = listOf(chunk),
            framing = "The retrieved passage reports a rise in landfill charges.",
        )

        assertTrue("The retrieved passage reports a rise in landfill charges." in turn.answer)
        assertTrue("[${chunk.chunkId}]" in turn.answer)
        assertEquals("turn_persp_expert_1_1", turn.turnId)
    }

    @Test
    fun `a tie between two passages is broken by the greater chunk id`() {
        // Python's `max` over `(count, chunk id)` keeps the *greatest* chunk
        // id when two passages were cited equally often. The other order
        // would name a different passage as the one that carried the session.
        val first = TestFixtures.chunk(
            "Landfill charges rose 12 percent over the same period.",
            start = 0,
        )
        val second = TestFixtures.chunk(
            "Kerbside recycling throughput doubled after the fleet replacement.",
            start = 200,
        )
        val indexer = MockProvenanceIndexer(listOf(first, second))
        val loop = DialogueLoop(topKPerTurn = 2)

        val session = loop.conductDialogue(expert(), indexer, numTurns = 1)

        val greater = listOf(first.chunkId, second.chunkId).max()
        val takeaway = session.synthesizedTakeaways.first { "carried this session" in it }
        assertTrue("[$greater]" in takeaway)
    }

    @Test
    fun `the memory consolidates the sessions it is given`() {        val chunk = TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 0)
        val indexer = MockProvenanceIndexer(listOf(chunk))
        val loop = DialogueLoop()
        val perspective = expert()

        val session = loop.conductDialogue(perspective, indexer, numTurns = 1)
        val memory = loop.buildMemory(listOf(perspective), listOf(session))

        assertEquals("Distributed Consensus", memory.topic)
        assertEquals(listOf(session), memory.sessions)
        assertEquals(listOf(chunk.chunkId), memory.allReferencedChunkIds)
        assertEquals(session.synthesizedTakeaways, memory.collectedTakeaways)
    }
}

/** A model that writes only the framing sentence. */
private class FramingModel : DialogueModel {
    override fun frameAnswer(question: String, passages: String, role: String): String =
        "The passages report the cost of residual waste."
}
