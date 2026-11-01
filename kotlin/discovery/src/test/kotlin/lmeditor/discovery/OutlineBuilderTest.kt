package lmeditor.discovery

import lmeditor.core.LMNotConfiguredError
import lmeditor.core.OutlineGenerationError
import lmeditor.core.OutlineNode
import lmeditor.core.ResearchMemory
import lmeditor.core.SourceChunk
import lmeditor.core.TopicOutline
import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The outline builder.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_discovery.py` (the outline section).
 */
class OutlineBuilderTest {

    private fun researchMemory(
        topic: String,
        chunks: List<SourceChunk>,
        perspectives: Int = 2,
    ): ResearchMemory {
        val personas = PerspectiveGenerator().generatePerspectives(topic, numPerspectives = perspectives)
        return DialogueLoop()
            .conductAllDialogues(personas, MockProvenanceIndexer(chunks), turnsPerSession = 2)
    }

    @Test
    fun `the outline has a root per section and a passage per leaf`() {
        val chunks = listOf(
            TestFixtures.chunk(
                "Formal definitions and problem background for verifiable writing harnesses.",
                start = 0,
            ),
            TestFixtures.chunk("Component architecture and pipeline execution flow.", start = 200),
            TestFixtures.chunk(
                "Failure modes and bottleneck latency under high concurrent load.",
                start = 400,
            ),
            TestFixtures.chunk(
                "Production deployment telemetry and benchmark evaluation results.",
                start = 600,
            ),
            TestFixtures.chunk(
                "Synthesis of future research frontiers and open problems.",
                start = 800,
            ),
        )
        val topic = "Verifiable LLM Writing Systems"
        val memory = researchMemory(topic, chunks)

        val outline = OutlineBuilder().generateOutline(topic, memory, chunks)

        assertEquals(topic, outline.topic)
        assertTrue(outline.nodes.isNotEmpty())

        // Root nodes must have children and must not hold passages.
        for (root in outline.nodes) {
            assertTrue(root.children.isNotEmpty())
            assertTrue(root.assignedChunkIds.isEmpty())
        }

        // Leaves must hold passages, and every passage must exist.
        val leaves = outline.getLeaves()
        assertTrue(leaves.isNotEmpty())
        val validChunkIds = chunks.map { it.chunkId }.toSet()
        val assigned = mutableListOf<String>()

        for (leaf in leaves) {
            assertTrue(leaf.isLeaf)
            assertTrue(leaf.assignedChunkIds.isNotEmpty())
            for (chunkId in leaf.assignedChunkIds) {
                assertTrue(chunkId in validChunkIds)
                assigned.add(chunkId)
            }
        }

        // Every passage is placed exactly once.
        assertEquals(validChunkIds.sorted(), assigned.sorted())
    }

    @Test
    fun `two corpora do not produce the same outline`() {
        val wasteChunks = listOf(
            TestFixtures.chunk(
                "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
                start = 0,
            ),
            TestFixtures.chunk(
                "Kerbside recycling throughput doubled after the 2022 fleet replacement.",
                start = 200,
            ),
            TestFixtures.chunk(
                "Landfill charges rose 12 percent, raising the cost of residual waste.",
                start = 400,
            ),
        )
        val quantumChunks = listOf(
            TestFixtures.chunk(
                "Quantum key distribution tolerates photon loss through decoy state protocols.",
                start = 0,
            ),
            TestFixtures.chunk(
                "Post-quantum lattice schemes resist Shor's attack at 128-bit security.",
                start = 200,
            ),
            TestFixtures.chunk(
                "Entanglement fidelity of 99.9 percent was measured on the metropolitan link.",
                start = 400,
            ),
        )

        val generator = OutlineBuilder()
        val wasteOutline = generator.generateOutline(
            topic = "Municipal Waste",
            researchMemory = researchMemory("Municipal Waste", wasteChunks, perspectives = 1),
            availableChunks = wasteChunks,
        )
        val quantumOutline = generator.generateOutline(
            topic = "Post-Quantum Cryptography",
            researchMemory = researchMemory("Post-Quantum Cryptography", quantumChunks, perspectives = 1),
            availableChunks = quantumChunks,
        )

        val wasteTitles = wasteOutline.nodes.joinToString(" ") { it.title }
        val quantumTitles = quantumOutline.nodes.joinToString(" ") { it.title }

        assertTrue(wasteTitles != quantumTitles)

        // The headings name the material they cover.
        assertTrue(
            listOf("waste", "collection", "recycling", "landfill", "kerbside", "fleet")
                .any { it in wasteTitles.lowercase() },
        )
        assertTrue(
            listOf("quantum", "key", "lattice", "entanglement", "photon", "decoy")
                .any { it in quantumTitles.lowercase() },
        )
    }

    @Test
    fun `a document with its own headings is organised by those headings`() {
        val chunks = listOf(
            TestFixtures.chunk(
                "## Kerbside Collection\nCollection frequency rose to weekly in 2023.",
                start = 0,
            ),
            TestFixtures.chunk(
                "## Landfill Charges\nCharges rose 12 percent over the same period.",
                start = 200,
            ),
        )

        val outline = OutlineBuilder().generateOutline(
            topic = "Municipal Waste",
            researchMemory = researchMemory("Municipal Waste", chunks, perspectives = 1),
            availableChunks = chunks,
        )

        val titles = outline.nodes.joinToString(" ") { it.title }
        assertTrue("Kerbside Collection" in titles)
        assertTrue("Landfill Charges" in titles)
    }

    @Test
    fun `a duplicate node id is refused by the shared DAG check`() {
        // The cycle and duplicate detection lives in `lmeditor.core`; the
        // builder must not ship an outline that fails it.
        val leaf = OutlineNode(nodeId = "leaf_1", title = "Leaf 1", assignedChunkIds = listOf("c1"))
        val root = OutlineNode(nodeId = "root_1", title = "Root 1", children = listOf(leaf))
        val duplicate = OutlineNode(nodeId = "leaf_1", title = "Duplicate Leaf")

        val invalid = TopicOutline(topic = "Test Topic", nodes = listOf(root, duplicate))

        val error = assertFailsWith<OutlineGenerationError> { invalid.validateDag() }
        assertTrue(error.message.contains("Duplicate node_id"))
    }

    @Test
    fun `a passage outside the corpus is refused`() {
        val availableChunk = TestFixtures.chunk("Legitimate available chunk", start = 0)
        val bogusChunkId = "0".repeat(64)
        val memory = researchMemory("Test Topic", listOf(availableChunk), perspectives = 1)

        val broken = BrokenOutlineBuilder(bogusChunkId)

        val error = assertFailsWith<OutlineGenerationError> {
            broken.generateOutline("Test Topic", memory, listOf(availableChunk))
        }

        assertTrue(error.message.contains("not in available_chunks"))
        assertTrue(error.nodeId != null)
    }

    @Test
    fun `an empty topic is refused`() {
        val chunk = TestFixtures.chunk("Chunk 1")

        assertFailsWith<ValidationError> {
            OutlineBuilder().generateOutline("", researchMemory("Test Topic", listOf(chunk)), listOf(chunk))
        }
    }

    @Test
    fun `an empty corpus is refused`() {
        val chunk = TestFixtures.chunk("Chunk 1")

        assertFailsWith<ValidationError> {
            OutlineBuilder().generateOutline("Valid Topic", researchMemory("Test Topic", listOf(chunk)), emptyList())
        }
    }

    @Test
    fun `a missing research memory is refused`() {
        val chunk = TestFixtures.chunk("Chunk 1")

        assertFailsWith<ValidationError> {
            OutlineBuilder().generateOutline("Valid Topic", null, listOf(chunk))
        }
    }

    @Test
    fun `a missing language model is reported rather than silently skipped`() {
        val chunk = TestFixtures.chunk("Kerbside recycling throughput doubled.")
        val memory = researchMemory("Test Topic", listOf(chunk), perspectives = 1)

        val builder = OutlineBuilder(useModel = true, model = null)

        val error = assertFailsWith<LMNotConfiguredError> {
            builder.generateOutline("Test Topic", memory, listOf(chunk))
        }
        assertEquals("outline synthesis", error.operation)
    }

    @Test
    fun `the language model path really controls the section headings`() {
        val chunks = listOf(
            TestFixtures.chunk(
                "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
                start = 0,
            ),
            TestFixtures.chunk("Kerbside recycling throughput doubled after the fleet replacement.", start = 200),
        )
        val memory = researchMemory("Municipal Waste", chunks, perspectives = 1)
        val builder = OutlineBuilder(useModel = true, model = StubOutlineModel())

        val outline = builder.generateOutline("Municipal Waste", memory, chunks)

        val titles = outline.nodes.joinToString(" ") { it.title }
        assertTrue("Municipal Waste Collection" in titles)
    }

    @Test
    fun `building the outline twice leaves the same outline`() {
        val chunks = listOf(
            TestFixtures.chunk("Kerbside recycling throughput doubled in 2023.", start = 0),
            TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 200),
        )
        val memory = researchMemory("Municipal Waste", chunks, perspectives = 1)
        val builder = OutlineBuilder()

        val once = builder.generateOutline("Municipal Waste", memory, chunks)
        val twice = builder.generateOutline("Municipal Waste", memory, chunks)

        assertEquals(once, twice)
    }

    @Test
    fun `the caller may name the sections and the corpus still decides the passages`() {
        val chunks = listOf(
            TestFixtures.chunk("Kerbside recycling throughput doubled in 2023.", start = 0),
            TestFixtures.chunk("Landfill charges rose 12 percent over the same period.", start = 200),
        )
        val sections = listOf(
            lmeditor.core.OutlineNodeInput(title = "Collection", description = "How bins are collected."),
            lmeditor.core.OutlineNodeInput(title = "Disposal", description = "Where the waste ends up."),
        )

        val outline = OutlineBuilder().buildOutlineFromSections(sections, "Municipal Waste", chunks)

        assertEquals("Municipal Waste", outline.topic)
        assertEquals(2, outline.nodes.size)
        for (node in outline.nodes) {
            assertTrue(node.children.isEmpty())
            assertTrue(node.assignedChunkIds.isNotEmpty())
        }
    }
}

/** A builder that hands one leaf a passage the corpus does not carry. */
private class BrokenOutlineBuilder(private val bogusChunkId: String) : OutlineBuilder() {
    override fun assignChunksToLeaves(
        chunks: List<SourceChunk>,
        leafSpecs: List<OutlineBuilder.LeafSpec>,
    ): Map<String, List<String>> {
        val assigned = super.assignChunksToLeaves(chunks, leafSpecs)
            .mapValues { (_, ids) -> ids.toMutableList() }
            .toMutableMap()
        assigned.getValue(leafSpecs[0].id).add(bogusChunkId)
        return assigned.mapValues { (_, ids) -> ids.toList() }
    }
}

/** A model that proposes the section headings the Python stub answers with. */
private class StubOutlineModel : OutlineModel {
    override fun proposeSections(topic: String, passages: String): String =
        "[{\"title\": \"Municipal Waste Collection\", " +
            "\"description\": \"Collection throughput and charges.\", " +
            "\"keywords\": [\"collection\", \"waste\", \"kerbside\"]}]"
}
