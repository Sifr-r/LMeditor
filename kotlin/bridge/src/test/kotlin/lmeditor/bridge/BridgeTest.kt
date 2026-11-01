package lmeditor.bridge

import lmeditor.core.json.Json
import lmeditor.core.json.jsonArray
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.word.WordBlock
import lmeditor.word.WordBlockKind
import lmeditor.word.WordDocument
import lmeditor.word.WordWriter
import java.util.Base64
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The seam's framing rules.
 *
 * These are the rules the Python client depends on: the correlation id is
 * echoed, a failure is reported rather than thrown, and an error carries the
 * type the Python side rebuilds.
 */
class BridgeTest {

    private val bridge = Bridge()

    private fun call(method: String, params: String = "{}"): Map<String, Any?> {
        val request = """{"id": "7", "method": "$method", "params": $params}"""
        return Json.parseObject(bridge.handle(request))
    }

    // --- framing -----------------------------------------------------------

    @Test
    fun `the correlation id is echoed back`() {
        val response = call("core.citations.strip", """{"text": "a [#x] b"}""")
        assertEquals("7", response.jsonString("id"))
    }

    @Test
    fun `a successful call reports ok`() {
        val response = call("core.citations.strip", """{"text": "a [#x] b"}""")
        assertTrue(response.jsonBool("ok"))
        assertEquals("a  b", response.jsonObject("result").jsonString("text"))
    }

    @Test
    fun `an unknown method is a failed response, not a crash`() {
        val response = call("no.such.method")
        assertFalse(response.jsonBool("ok"))
        assertEquals("BridgeError", response.jsonObject("error").jsonString("type"))
    }

    @Test
    fun `a malformed request is a failed response, not a crash`() {
        val response = Json.parseObject(bridge.handle("{not json"))
        assertFalse(response.jsonBool("ok"))
        assertEquals("BridgeError", response.jsonObject("error").jsonString("type"))
    }

    @Test
    fun `a domain error carries its Kotlin type and its details`() {
        // A chunk whose character range is inverted is refused by the core.
        val response = call(
            "core.schemas.source_chunk_create",
            """{"document_id": "d", "text": "t", "char_start": 5, "char_end": 5,
               "byte_start": 0, "byte_end": 1}""",
        )
        assertFalse(response.jsonBool("ok"))
        val error = response.jsonObject("error")
        assertEquals("ValidationError", error.jsonString("type"))
        assertTrue(error.jsonString("message").contains("char_end"))
        assertEquals("char_end", error.jsonObject("details").jsonString("field"))
    }

    @Test
    fun `every response is a single line of JSON`() {
        val response = bridge.handle(
            """{"id": "1", "method": "core.citations.strip", "params": {"text": "a\nb"}}""",
        )
        assertFalse(response.contains("\n"))
    }

    // --- the methods -------------------------------------------------------

    @Test
    fun `the citation grammar is served`() {
        val response = call(
            "core.citations.extract_chunk_ids",
            """{"text": "Claim [#a] and [#b, #c]."}""",
        )
        assertEquals(
            listOf("a", "b", "c"),
            response.jsonObject("result").jsonStringList("chunk_ids"),
        )
    }

    @Test
    fun `a citation span is reported in code points`() {
        val response = call(
            "core.citations.iter_spans",
            """{"text": "Rocket 🚀 goes up [#astral] and lands."}""",
        )
        val spans = response.jsonObject("result").jsonObjectList("spans")
        assertEquals(1, spans.size)
        assertEquals(17, spans.first().jsonInt("start"))
        assertEquals(26, spans.first().jsonInt("end"))
    }

    @Test
    fun `the operator edit rule is served`() {
        val draft = """
            {"section_id": "s1", "title": "T", "content": "old", "order": 1,
             "citations": [], "claims": [], "is_verified": true,
             "status": "verified", "revision_count": 2, "critique": "c",
             "cited_chunk_ids": []}
        """.trimIndent()
        val response = call(
            "core.sections.merge_operator_text",
            """{"draft": $draft, "content": "new"}""",
        )
        val merged = response.jsonObject("result").jsonObject("draft")
        assertEquals("new", merged.jsonString("content"))
        assertFalse(merged.jsonBool("is_verified"))
        assertEquals("pending", merged.jsonString("status"))
    }

    @Test
    fun `a chunk is created with the deterministic digest`() {
        val response = call(
            "core.schemas.source_chunk_create",
            """{"document_id": "doc", "text": "Hello", "char_start": 0, "char_end": 5,
               "byte_start": 0, "byte_end": 5}""",
        )
        val chunk = response.jsonObject("result").jsonObject("chunk")
        assertEquals(
            "ed5f5a656f33e7e095be7064d3af8b49b87dc337b0939433be9eedc66165cfa2",
            chunk.jsonString("chunk_id"),
        )
    }

    @Test
    fun `text is chunked into traceable passages`() {
        val response = call(
            "text.chunk",
            """{"text": "First sentence. Second sentence.", "document_id": "d"}""",
        )
        val chunks = response.jsonObject("result").jsonObjectList("chunks")
        assertTrue(chunks.isNotEmpty())
        assertEquals("d", chunks.first().jsonString("document_id"))
        assertEquals(64, chunks.first().jsonString("chunk_id").length)
    }

    // --- editing -----------------------------------------------------------

    @Test
    fun `a revision that cites a source the document has is accepted`() {
        val response = call(
            "editing.validate_revision",
            """{"revised_text": "The claim rests on [#$CHUNK_ID].", "section": $DRAFT, "chunks": [$CHUNK]}""",
        )
        val result = response.jsonObject("result")
        assertTrue(result.jsonBool("accepted"))
        assertEquals(listOf(CHUNK_ID), result.jsonStringList("cited_chunk_ids"))
        assertTrue(result.jsonObjectList("claims").isNotEmpty())
    }

    @Test
    fun `a revision that invents a citation is refused, not raised`() {
        val response = call(
            "editing.validate_revision",
            """{"revised_text": "The claim rests on [#bbbb].", "section": $DRAFT, "chunks": [$CHUNK]}""",
        )
        assertTrue(response.jsonBool("ok"))
        val result = response.jsonObject("result")
        assertFalse(result.jsonBool("accepted"))
        assertTrue(result.jsonString("reason").contains("bbbb"))
    }

    // --- factcheck ---------------------------------------------------------

    @Test
    fun `the verdicts on a passage are aggregated into a report`() {
        val response = call(
            "factcheck.aggregate",
            """{"doc_id": "d1", "text": "The sky is blue.",
                "claims": [{"claim_id": "c1", "section_id": "s1", "text": "The sky is blue."}],
                "verdicts": [{"claim_id": "c1", "claim_text": "The sky is blue.",
                              "status": "entailed", "score": 0.9,
                              "evidence": [{"source": "corpus", "chunk_id": "aaaa",
                                            "excerpt": "The sky is blue.", "score": 1.0}],
                              "reasoning": "The passage says so."}]}""",
        )
        val result = response.jsonObject("result")
        val report = result.jsonObject("report")
        assertEquals("d1", report.jsonString("doc_id"))
        assertEquals("supported", report.jsonString("verdict"))
        val scorecard = result.jsonObject("scorecard")
        assertEquals(1, scorecard.jsonInt("total_claims"))
        assertEquals(1, scorecard.jsonInt("supported_claims"))
    }

    @Test
    fun `a passage with no claim is inconclusive, not a verdict`() {
        val response = call(
            "factcheck.aggregate",
            """{"doc_id": "d1", "text": "", "claims": [], "verdicts": []}""",
        )
        val report = response.jsonObject("result").jsonObject("report")
        assertEquals("inconclusive", report.jsonString("verdict"))
    }

    // --- ingest -----------------------------------------------------------

    @Test
    fun `a source is loaded into text, its metadata and what it lost`() {
        val file = java.nio.file.Files.createTempFile("lmeditor-bridge-", ".txt")
        java.nio.file.Files.writeString(file, "The first line.\nThe second line.\n")

        val response = call(
            "ingest.load",
            """{"kind": "text", "origin": "${file.toString().replace("\\", "\\\\")}"}""",
        )
        val result = response.jsonObject("result")
        assertTrue(result.jsonString("text").contains("The first line."))
    }

    @Test
    fun `a url that resolves to a private address is refused`() {
        val response = call(
            "ingest.url_policy.assert_public",
            """{"url": "http://127.0.0.1/secret"}""",
        )
        assertFalse(response.jsonBool("ok"))
        assertEquals("UnsafeSourceError", response.jsonObject("error").jsonString("type"))
    }

    // --- library ----------------------------------------------------------

    @Test
    fun `a book is imported, indexed and exported again`() {
        val document = WordDocument(
            blocks = listOf(
                WordBlock(
                    blockId = "b1",
                    kind = WordBlockKind.HEADING,
                    text = "A chapter",
                    level = 1,
                ),
                WordBlock(
                    blockId = "b2",
                    kind = WordBlockKind.PARAGRAPH,
                    text = "The body of the chapter.",
                ),
            ),
        )
        val bytes = WordWriter(deterministic = true).write(document)
        val encoded = Base64.getEncoder().encodeToString(bytes)
        val bookId = "bridge-book-${System.nanoTime()}"

        val imported = call(
            "library.import_book",
            """{"book_id": "$bookId", "title": "A book", "bytes_b64": "$encoded"}""",
        )
        assertTrue(imported.jsonBool("ok"))
        val manifest = imported.jsonObject("result").jsonObject("manifest")
        assertEquals(bookId, manifest.jsonString("book_id"))

        val fetched = call("library.get_manifest", """{"book_id": "$bookId"}""")
        assertEquals(bookId, fetched.jsonObject("result").jsonObject("manifest").jsonString("book_id"))

        val brief = call("library.build_brief", """{"book_id": "$bookId"}""")
        assertTrue(brief.jsonBool("ok"))
        assertTrue(brief.jsonObject("result").jsonString("brief").isNotEmpty())

        val exported = call("library.export_book", """{"book_id": "$bookId"}""")
        assertTrue(exported.jsonBool("ok"))
        assertTrue(exported.jsonObject("result").jsonString("bytes_b64").isNotEmpty())
    }

    // --- citation checking ------------------------------------------------

    @Test
    fun `the citation precision is served`() {
        val response = call(
            "text.citation_check.precision",
            """{"supported_cited_claims": 2, "total_cited_claims": 3, "total_claims": 5}""",
        )
        assertEquals(0.6667, response.jsonObject("result").jsonDouble("precision"), 1e-9)
    }

    @Test
    fun `the citation recall is served`() {
        val response = call(
            "text.citation_check.recall",
            """{"supported_claims": 2, "total_claims": 5}""",
        )
        assertEquals(0.4, response.jsonObject("result").jsonDouble("recall"), 1e-9)
    }

    @Test
    fun `the groundedness score is served`() {
        val response = call(
            "text.citation_check.f_beta",
            """{"precision": 0.5, "recall": 1.0, "beta": 2.0}""",
        )
        assertEquals(0.8333, response.jsonObject("result").jsonDouble("f_beta"), 1e-9)
    }

    @Test
    fun `the premises of a claim are resolved by the tier`() {
        val claim = """
            {"claim_id": "c1", "section_id": "s1", "text": "The sky is blue.",
             "char_start": 0, "char_end": 17, "cited_chunk_ids": ["$CHUNK_ID"]}
        """.trimIndent()
        val response = call(
            "text.citation_check.resolve_premises",
            """{"claims": [$claim], "chunks": [$CHUNK]}""",
        )
        val result = response.jsonObject("result")
        assertEquals(listOf(true), result.jsonArray("cited").map { it == true })
        assertEquals(listOf("The sky is blue."), result.jsonStringList("premises"))
    }

    // --- workflow ----------------------------------------------------------

    @Test
    fun `the fresh state of a run is built by the tier`() {
        val response = call(
            "workflow.create_initial_state",
            """{"doc_id": "d1", "topic": "Rockets", "max_retries": 2, "threshold_f_beta": 0.9}""",
        )
        val state = response.jsonObject("result").jsonObject("state")
        assertEquals("d1", state.jsonString("doc_id"))
        assertEquals("Rockets", state.jsonString("topic"))
        assertEquals(2, state.jsonInt("max_retries"))
        assertEquals(0.9, state.jsonDouble("threshold_f_beta"), 1e-9)
    }

    @Test
    fun `a run whose sections are all verified is routed to the end`() {
        val draft = """
            {"section_id": "s1", "title": "T", "content": "old", "order": 1,
             "citations": [], "claims": [], "is_verified": true,
             "status": "verified", "revision_count": 0, "critique": null,
             "cited_chunk_ids": []}
        """.trimIndent()
        val response = call(
            "workflow.route",
            """{"route": "after_verification", "section_drafts": {"s1": $draft},
                "retry_counts": {}, "hitl_approved": false}""",
        )
        assertEquals("all_verified", response.jsonObject("result").jsonString("next_step"))
    }

    @Test
    fun `a run whose section still needs work is routed back to the draft`() {
        val draft = """
            {"section_id": "s1", "title": "T", "content": "old", "order": 1,
             "citations": [], "claims": [], "is_verified": false,
             "status": "drafting", "revision_count": 0, "critique": null,
             "cited_chunk_ids": []}
        """.trimIndent()
        val response = call(
            "workflow.route",
            """{"route": "after_verification", "section_drafts": {"s1": $draft},
                "retry_counts": {"s1": 0}, "max_retries": 3, "hitl_approved": false}""",
        )
        assertEquals("needs_refinement", response.jsonObject("result").jsonString("next_step"))
    }

    // --- lineage -----------------------------------------------------------

    @Test
    fun `a span is attributed to the chunk it came from`() {
        val response = call(
            "text.lineage.attribute_span",
            """{"draft_text": "The sky is blue.", "span_start": 0, "span_end": 16,
                "chunk_id": "$CHUNK_ID", "claim_id": "c1", "chunks": [$CHUNK]}""",
        )
        val lineages = response.jsonObject("result").jsonObjectList("lineages")
        assertEquals(1, lineages.size, response.toString())
        assertEquals(CHUNK_ID, lineages.first().jsonString("chunk_id"))
        assertEquals(0, lineages.first().jsonInt("span_start"))
        assertEquals(16, lineages.first().jsonInt("span_end"))
    }

    @Test
    fun `the citations of a draft are bound to their chunks`() {
        val response = call(
            "text.lineage.extract_from_draft",
            """{"draft_text": "The sky is blue [#$CHUNK_ID].", "chunks": [$CHUNK]}""",
        )
        val lineages = response.jsonObject("result").jsonObjectList("lineages")
        assertEquals(1, lineages.size)
        assertEquals(CHUNK_ID, lineages.first().jsonString("chunk_id"))
    }

    private companion object {
        /** The one source chunk the document has. Its id is a chunk digest. */
        const val CHUNK_ID: String = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" +
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        /** A section the editor may work on. */
        val DRAFT: String = """
            {"section_id": "s1", "title": "T", "content": "old", "order": 1,
             "citations": [], "claims": [], "is_verified": false,
             "status": "pending", "revision_count": 0, "critique": null,
             "cited_chunk_ids": []}
        """.trimIndent()

        /** The one source chunk the document has. */
        val CHUNK: String = """
            {"chunk_id": "$CHUNK_ID", "document_id": "d1", "text": "The sky is blue.",
             "char_start": 0, "char_end": 17, "byte_start": 0, "byte_end": 17,
             "token_count": 4, "start_char": 0, "end_char": 17, "metadata": {}}
        """.trimIndent()
    }
}
