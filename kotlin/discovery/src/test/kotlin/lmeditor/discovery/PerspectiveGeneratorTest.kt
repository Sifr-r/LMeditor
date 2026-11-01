package lmeditor.discovery

import lmeditor.core.LMNotConfiguredError
import lmeditor.core.PerspectiveRole
import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The perspective generator.
 *
 * Port of the deterministic assertions of
 * `backend/tests/test_discovery.py` (the perspective section).
 */
class PerspectiveGeneratorTest {

    private val wasteContext =
        "Municipal waste collection rates fell by 30 percent between 2019 and 2023. " +
            "Kerbside recycling throughput doubled after the 2022 fleet replacement. " +
            "Landfill charges rose 12 percent, raising the cost of residual waste."

    private val quantumContext =
        "Quantum key distribution tolerates photon loss through decoy state protocols. " +
            "Post-quantum lattice schemes resist Shor's attack at 128-bit security. " +
            "Entanglement fidelity of 99.9 percent was measured on the metropolitan link."

    @Test
    fun `a valid topic yields the requested number of diverse personas`() {
        val topic = "Asynchronous Distributed Consensus in Byzantine Environments"

        val perspectives = PerspectiveGenerator().generatePerspectives(topic, numPerspectives = 4)

        assertEquals(4, perspectives.size)
        val roles = perspectives.map { it.role }.toSet()
        assertEquals(4, roles.size)
        assertTrue(PerspectiveRole.DOMAIN_EXPERT in roles)
        assertTrue(PerspectiveRole.SKEPTIC in roles)

        for (perspective in perspectives) {
            assertTrue(perspective.id.startsWith("persp_"))
            assertTrue(topic in perspective.title || topic in perspective.description)
            assertTrue(perspective.focusAreas.size >= 3)
            assertTrue(perspective.guidingQuestions.size >= 3)
        }
    }

    @Test
    fun `asking for six personas covers every archetype`() {
        val perspectives = PerspectiveGenerator()
            .generatePerspectives("Quantum Key Distribution and Post-Quantum Cryptography", numPerspectives = 6)

        assertEquals(6, perspectives.size)
        assertEquals(
            setOf(
                PerspectiveRole.DOMAIN_EXPERT,
                PerspectiveRole.SKEPTIC,
                PerspectiveRole.PRACTITIONER,
                PerspectiveRole.REGULATOR,
                PerspectiveRole.COMPARATIVE_ANALYST,
                PerspectiveRole.GENERAL_AUDIENCE,
            ),
            perspectives.map { it.role }.toSet(),
        )
    }

    @Test
    fun `a persona is grounded in the material it will interrogate`() {
        val perspectives = PerspectiveGenerator()
            .generatePerspectives("Municipal Waste", context = wasteContext, numPerspectives = 3)

        assertEquals(3, perspectives.size)
        for (perspective in perspectives) {
            val blob = (
                listOf(perspective.description) +
                    perspective.focusAreas +
                    perspective.guidingQuestions +
                    perspective.keyInquiries
                ).joinToString(" ").lowercase()

            assertTrue(
                listOf("waste", "collection", "recycling", "landfill", "kerbside", "fleet")
                    .any { it in blob },
                "perspective is not grounded in the context: $blob",
            )
        }
    }

    @Test
    fun `the same topic with different material yields different personas`() {
        val generator = PerspectiveGenerator()

        val waste = generator.generatePerspectives("Municipal Waste", context = wasteContext, numPerspectives = 2)
        val quantum = generator.generatePerspectives(
            "Post-Quantum Cryptography",
            context = quantumContext,
            numPerspectives = 2,
        )

        assertTrue(waste.map { it.description } != quantum.map { it.description })
        assertTrue(waste.map { it.guidingQuestions } != quantum.map { it.guidingQuestions })

        val quantumBlob = (
            listOf(quantum[0].description) +
                quantum[0].focusAreas +
                quantum[0].guidingQuestions
            ).joinToString(" ").lowercase()
        assertTrue(
            listOf("quantum", "photon", "lattice", "entanglement", "decoy").any { it in quantumBlob },
        )
    }

    @Test
    fun `a persona without a context still states its stance`() {
        val perspectives = PerspectiveGenerator()
            .generatePerspectives("Asynchronous Consensus", numPerspectives = 1)

        assertEquals(1, perspectives.size)
        assertTrue("Asynchronous Consensus" in perspectives[0].description)
    }

    @Test
    fun `an empty topic is refused`() {
        assertFailsWith<ValidationError> { PerspectiveGenerator().generatePerspectives("", numPerspectives = 1) }
    }

    @Test
    fun `a topic shorter than three characters is refused`() {
        assertFailsWith<ValidationError> { PerspectiveGenerator().generatePerspectives("ab", numPerspectives = 1) }
    }

    @Test
    fun `a topic longer than a thousand characters is refused`() {
        val topic = "x".repeat(1001)

        assertFailsWith<ValidationError> { PerspectiveGenerator().generatePerspectives(topic, numPerspectives = 1) }
    }

    @Test
    fun `a persona count outside one to ten is refused`() {
        val generator = PerspectiveGenerator()

        assertFailsWith<ValidationError> { generator.generatePerspectives("Valid Topic", numPerspectives = 0) }
        assertFailsWith<ValidationError> { generator.generatePerspectives("Valid Topic", numPerspectives = 11) }
    }

    @Test
    fun `a missing language model is reported rather than silently skipped`() {
        val generator = PerspectiveGenerator(useModel = true, model = null)

        val error = assertFailsWith<LMNotConfiguredError> {
            generator.generatePerspectives("Valid Topic", numPerspectives = 1)
        }
        assertEquals("perspective discovery", error.operation)
    }

    @Test
    fun `the max perspectives override replaces the requested count`() {
        val perspectives = PerspectiveGenerator().generatePerspectives(
            topic = "Valid Topic",
            numPerspectives = 6,
            maxPerspectives = 2,
        )

        assertEquals(2, perspectives.size)
    }

    @Test
    fun `generating the personas twice leaves the same personas`() {
        val generator = PerspectiveGenerator()

        val once = generator.generatePerspectives("Municipal Waste", context = wasteContext, numPerspectives = 3)
        val twice = generator.generatePerspectives("Municipal Waste", context = wasteContext, numPerspectives = 3)

        assertEquals(once, twice)
    }
}
