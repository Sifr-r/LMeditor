package lmeditor.ingest

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The address classification, pinned against the Python tier.
 *
 * The reference table was produced by the Python tier's own
 * `_is_blocked_address` -- the predicate the URL loader uses -- over a wide set
 * of addresses. The Kotlin policy must agree with it on every one of them.
 *
 * This is the test that catches drift between the two tiers: the two
 * implementations are separate code, and only a shared fixture keeps them
 * saying the same thing.
 */
class IpPolicyParityTest {

    @Test
    fun theAddressClassificationAgreesWithThePythonTier() {
        val reference = readReference()
        assertTrue(reference.size > 500, "the reference table must be a wide sweep")
        val disagreements = mutableListOf<String>()
        for ((address, expected) in reference) {
            val actual = if (isBlockedAddressText(address)) "blocked" else "allowed"
            if (actual != expected) {
                disagreements.add("$address: python=$expected kotlin=$actual")
            }
        }
        assertEquals(
            emptyList(),
            disagreements,
            "the Kotlin policy must refuse exactly the addresses the Python policy refuses",
        )
    }

    /** Return the reference table, read from the test resources. */
    private fun readReference(): List<Pair<String, String>> {
        val stream = javaClass.classLoader.getResourceAsStream("fixtures/ip_policy_reference.tsv")
            ?: error("the reference table is missing from the test resources")
        val text = stream.bufferedReader().use { it.readText() }
        return text.lineSequence()
            .drop(1)
            .filter { it.isNotBlank() }
            .map { line ->
                val address = line.substringBefore('\t')
                val verdict = line.substringAfter('\t')
                address to verdict
            }
            .toList()
    }
}
