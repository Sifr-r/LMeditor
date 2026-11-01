package lmeditor.editing

import lmeditor.core.CodePoints
import lmeditor.core.InlineEditRejectedError
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The language-model seam of the inline editor, ported from
 * `backend/src/lmeditor/editing/rewrite.py`.
 *
 * The model call itself belongs to the Python tier; these tests pin the
 * deterministic half -- what the model is told, how the passages are laid
 * out, and what a failed call means.
 */
class RewriteTest {

    @Test
    fun thePassagesAreLaidOutWithTheChunkIdTheModelMustCiteBy() {
        val chunks = listOf(chunk("Coral reefs shelter a quarter of all marine species."))

        val rendered = RewritePrompt.formatPassages(chunks)

        assertEquals(
            "--- PASSAGE 1 [#CHUNK_ID: ${chunks[0].chunkId}] ---\n" +
                "Coral reefs shelter a quarter of all marine species.\n",
            rendered,
        )
    }

    @Test
    fun aPassageThatNamesItsSourceSaysSoInTheHeader() {
        val chunk = chunk(
            "Coral reefs shelter a quarter of all marine species.",
            metadata = mapOf("document_title" to "Global reef census"),
        )

        val rendered = RewritePrompt.formatPassages(listOf(chunk))

        assertTrue(rendered.startsWith("--- PASSAGE 1 [#CHUNK_ID: ${chunk.chunkId}] --- "))
        assertTrue(rendered.contains("(Source: Global reef census)"))
    }

    @Test
    fun theRewriteSignatureIsPinnedAgainstThePythonTier() {
        // The signature is the house vocabulary: the drafter, the citation
        // inserter and the offline double all speak it, so a change here is
        // a change every tier sees.
        assertEquals(
            "instruction: str, draft_text: str, assigned_chunks_text: str -> grounded_draft: str",
            RewritePrompt.SIGNATURE,
        )
    }

    @Test
    fun theRewriteRefusesAnEmptyInstruction() {
        val rewrite = LmRewrite(RecordingModel("a revised passage"))

        val failure = assertFailsWith<ValidationError> {
            rewrite.rewrite("   ", "the passage", emptyList())
        }
        assertEquals("instruction", failure.field)
    }

    @Test
    fun theModelIsHandedTheInstructionThePassageAndTheFormattedPassages() {
        val model = RecordingModel("a revised passage")
        val chunks = listOf(chunk("Coral reefs shelter a quarter of all marine species."))
        val rewrite = LmRewrite(model)

        rewrite.rewrite("Make this tighter.", "the passage", chunks)

        assertEquals("Make this tighter.", model.instruction)
        assertEquals("the passage", model.draftText)
        assertEquals(RewritePrompt.formatPassages(chunks), model.assignedChunksText)
    }

    @Test
    fun aFailedModelCallBecomesATypedRejection() {
        val rewrite = LmRewrite(RecordingModel(failure = IllegalStateException("the network is down")))

        val failure = assertFailsWith<InlineEditRejectedError> {
            rewrite.rewrite("Make this tighter.", "the passage", emptyList())
        }
        assertTrue(failure.reason.startsWith("the rewrite model call failed:"))
        assertTrue(failure.reason.contains("the network is down"))
    }

    @Test
    fun anEmptyAnswerIsARefusalNotProse() {
        val rewrite = LmRewrite(RecordingModel("   "))

        val failure = assertFailsWith<InlineEditRejectedError> {
            rewrite.rewrite("Make this tighter.", "the passage", emptyList())
        }
        assertEquals("the rewrite model returned no text.", failure.reason)
    }

    @Test
    fun aMissingModelIsNotSwallowed() {
        val rewrite = LmRewrite(RecordingModel(failure = LMNotConfiguredError("inline edit rewrite")))

        assertFailsWith<LMNotConfiguredError> {
            rewrite.rewrite("Make this tighter.", "the passage", emptyList())
        }
    }

    @Test
    fun theAnswerIsTrimmed() {
        val rewrite = LmRewrite(RecordingModel("\n  a revised passage  \n"))

        val revised = rewrite.rewrite("Make this tighter.", "the passage", emptyList())

        assertEquals("a revised passage", revised)
    }
}

/** A chunk with a deterministic id. */
private fun chunk(text: String, metadata: Map<String, Any?> = emptyMap()): SourceChunk =
    SourceChunk.create(
        documentId = "doc_rewrite",
        text = text,
        charStart = 0,
        charEnd = CodePoints.length(text),
        byteStart = 0,
        byteEnd = CodePoints.utf8Size(text),
        metadata = metadata,
    )

/** A model that answers with a fixed text, or fails on demand. */
private class RecordingModel(
    private val answer: String = "",
    private val failure: RuntimeException? = null,
) : RewriteModel {

    /** The instruction the model was handed. */
    var instruction: String = ""

    /** The passage the model was handed. */
    var draftText: String = ""

    /** The passages the model was handed. */
    var assignedChunksText: String = ""

    override fun rewrite(
        instruction: String,
        draftText: String,
        assignedChunksText: String,
    ): String {
        this.instruction = instruction
        this.draftText = draftText
        this.assignedChunksText = assignedChunksText
        failure?.let { throw it }
        return answer
    }
}
