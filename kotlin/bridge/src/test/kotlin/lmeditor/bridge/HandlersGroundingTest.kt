package lmeditor.bridge

import lmeditor.core.json.Json
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.text.DEFAULT_F_BETA_THRESHOLD
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The grounding scorecard over claims that read alike.
 *
 * Two claims may read the same and rest on different passages. They are then
 * two claims with two verdicts, and the scorecard has to keep them apart: a
 * contradicted claim that inherits an entailed twin's verdict is counted as
 * supported, the score reaches the threshold, and the section is published as
 * verified without ever reaching the human gate.
 */
class HandlersGroundingTest {

    private val bridge = Bridge()

    private fun call(method: String, params: String): Map<String, Any?> {
        val request = """{"id": "1", "method": "$method", "params": $params}"""
        return Json.parseObject(bridge.handle(request))
    }

    @Test
    fun `a contradicted claim does not inherit the verdict of a claim that reads the same`() {
        val response = call(
            "text.citation_check.audit_section",
            """{"section": $SECTION, "chunks": [$BLUE_CHUNK, $GREEN_CHUNK],
                "evaluator_name": "test-evaluator", "strict_hallucination_check": true}""",
        )
        assertTrue(response.jsonBool("ok"), response.toString())
        val result = response.jsonObject("result")

        // Each claim keeps the verdict the model gave it.
        val claims = result.jsonObject("section").jsonObjectList("claims")
        assertEquals(2, claims.size, response.toString())
        assertEquals("c1", claims[0].jsonString("claim_id"))
        assertEquals("contradicted", claims[0].jsonString("entailment_status"))
        assertEquals("c2", claims[1].jsonString("claim_id"))
        assertEquals("entailed", claims[1].jsonString("entailment_status"))

        // The scorecard counts the contradiction.
        val scorecard = result.jsonObject("scorecard")
        assertEquals(2, scorecard.jsonInt("total_claims"))
        assertEquals(1, scorecard.jsonInt("supported_claims"))
        assertEquals(1, scorecard.jsonInt("contradicted_claims"))
        assertEquals(0, scorecard.jsonInt("neutral_claims"))
        assertEquals(0, scorecard.jsonInt("ungrounded_claims"))
        assertEquals(0.5, scorecard.jsonDouble("f_beta"), 1e-9)

        // And the section does not pass the gate.
        assertTrue(scorecard.jsonDouble("f_beta") < DEFAULT_F_BETA_THRESHOLD)
        val section = result.jsonObject("section")
        assertFalse(section.jsonBool("is_verified"))
        assertEquals("needs_revision", section.jsonString("status"))
    }

    @Test
    fun `two claims that read alike on the same passage take the worse verdict`() {
        // The entailment interface carries the hypothesis and the premise, and
        // here the premise is the same for both, so the two claims cannot be
        // told apart. The worse verdict is the one that keeps the section
        // from being published as verified.
        val response = call(
            "text.citation_check.audit_section",
            """{"section": $SECTION_ALIKE, "chunks": [$BLUE_CHUNK],
                "evaluator_name": "test-evaluator", "strict_hallucination_check": true}""",
        )
        assertTrue(response.jsonBool("ok"), response.toString())
        val result = response.jsonObject("result")

        val claims = result.jsonObject("section").jsonObjectList("claims")
        assertEquals(2, claims.size, response.toString())
        assertEquals("contradicted", claims[0].jsonString("entailment_status"))

        val scorecard = result.jsonObject("scorecard")
        assertEquals(2, scorecard.jsonInt("contradicted_claims"))
        assertTrue(scorecard.jsonDouble("f_beta") < DEFAULT_F_BETA_THRESHOLD)
        val section = result.jsonObject("section")
        assertFalse(section.jsonBool("is_verified"))
        assertEquals("needs_revision", section.jsonString("status"))
    }

    @Test
    fun `a claim that reads on its own keeps its verdict`() {
        // The ordinary case: one claim, one passage, one verdict.
        val response = call(
            "text.citation_check.audit_section",
            """{"section": $SECTION_ALONE, "chunks": [$BLUE_CHUNK],
                "evaluator_name": "test-evaluator", "strict_hallucination_check": true}""",
        )
        assertTrue(response.jsonBool("ok"), response.toString())
        val result = response.jsonObject("result")

        val claims = result.jsonObject("section").jsonObjectList("claims")
        assertEquals(1, claims.size, response.toString())
        assertEquals("entailed", claims[0].jsonString("entailment_status"))

        val scorecard = result.jsonObject("scorecard")
        assertEquals(1, scorecard.jsonInt("supported_claims"))
        assertEquals(0, scorecard.jsonInt("contradicted_claims"))
        assertEquals(1.0, scorecard.jsonDouble("f_beta"), 1e-9)

        val section = result.jsonObject("section")
        assertTrue(section.jsonBool("is_verified"))
        assertEquals("verified", section.jsonString("status"))
    }

    private companion object {
        /** The chunk that supports the claim. */
        const val BLUE_CHUNK_ID: String = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" +
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        /** The chunk that contradicts it. */
        const val GREEN_CHUNK_ID: String = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" +
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        /** The chunk that supports the claim. */
        val BLUE_CHUNK: String = """
            {"chunk_id": "$BLUE_CHUNK_ID", "document_id": "d1",
             "text": "The sky is blue.",
             "char_start": 0, "char_end": 16, "byte_start": 0, "byte_end": 16,
             "token_count": 4, "start_char": 0, "end_char": 16, "metadata": {}}
        """.trimIndent()

        /** The chunk that contradicts it. */
        val GREEN_CHUNK: String = """
            {"chunk_id": "$GREEN_CHUNK_ID", "document_id": "d1",
             "text": "The sky is green.",
             "char_start": 0, "char_end": 17, "byte_start": 0, "byte_end": 17,
             "token_count": 4, "start_char": 0, "end_char": 17, "metadata": {}}
        """.trimIndent()

        /**
         * A section holding two claims that read alike and rest on different
         * passages: the first rests on the passage that contradicts it, the
         * second on the passage that supports it.
         */
        val SECTION: String = """
            {"section_id": "s1", "title": "T", "content": "The sky is blue.",
             "order": 1, "citations": [], "is_verified": false, "status": "pending",
             "revision_count": 0, "critique": null, "cited_chunk_ids": [],
             "claims": [
               {"claim_id": "c1", "section_id": "s1", "text": "The sky is blue.",
                "char_start": 0, "char_end": 16, "cited_chunk_ids": ["$GREEN_CHUNK_ID"],
                "entailment_status": "contradicted", "entailment_score": 0.9,
                "premise_text": "The sky is green."},
               {"claim_id": "c2", "section_id": "s1", "text": "The sky is blue.",
                "char_start": 0, "char_end": 16, "cited_chunk_ids": ["$BLUE_CHUNK_ID"],
                "entailment_status": "entailed", "entailment_score": 0.95,
                "premise_text": "The sky is blue."}
             ]}
        """.trimIndent()

        /**
         * The same two claims, both resting on the same passage: they read
         * alike and are judged against the same text, so nothing tells them
         * apart.
         */
        val SECTION_ALIKE: String = """
            {"section_id": "s1", "title": "T", "content": "The sky is blue.",
             "order": 1, "citations": [], "is_verified": false, "status": "pending",
             "revision_count": 0, "critique": null, "cited_chunk_ids": [],
             "claims": [
               {"claim_id": "c1", "section_id": "s1", "text": "The sky is blue.",
                "char_start": 0, "char_end": 16, "cited_chunk_ids": ["$BLUE_CHUNK_ID"],
                "entailment_status": "contradicted", "entailment_score": 0.9,
                "premise_text": "The sky is blue."},
               {"claim_id": "c2", "section_id": "s1", "text": "The sky is blue.",
                "char_start": 0, "char_end": 16, "cited_chunk_ids": ["$BLUE_CHUNK_ID"],
                "entailment_status": "entailed", "entailment_score": 0.95,
                "premise_text": "The sky is blue."}
             ]}
        """.trimIndent()

        /** A section holding a single claim, on the passage that supports it. */
        val SECTION_ALONE: String = """
            {"section_id": "s1", "title": "T", "content": "The sky is blue.",
             "order": 1, "citations": [], "is_verified": false, "status": "pending",
             "revision_count": 0, "critique": null, "cited_chunk_ids": [],
             "claims": [
               {"claim_id": "c1", "section_id": "s1", "text": "The sky is blue.",
                "char_start": 0, "char_end": 16, "cited_chunk_ids": ["$BLUE_CHUNK_ID"],
                "entailment_status": "entailed", "entailment_score": 0.95,
                "premise_text": "The sky is blue."}
             ]}
        """.trimIndent()
    }
}
