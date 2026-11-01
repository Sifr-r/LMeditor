package lmeditor.editing

import lmeditor.core.CodePoints
import lmeditor.core.EditScope
import lmeditor.core.EditTargetNotFoundError
import lmeditor.core.InlineEditRequest
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SectionDraft
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError
import lmeditor.text.CitationChecker
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The small-edit capability: rewrite one paragraph, and refuse what the
 * sources cannot support.
 *
 * The tests here pin the four promises that make the feature trustworthy
 * rather than merely convenient:
 *
 * * the edit lands where the user pointed, and nowhere else;
 * * the model is asked once, and is never handed the corpus;
 * * a revision that names a source which does not exist is refused, and the
 *   refusal says which source was invented;
 * * a revision that is supported is applied, and applying it twice is
 *   harmless.
 *
 * Ported from `backend/tests/test_editing.py`. The tests that need a live
 * language model are not ported: the model is a seam here, and the double
 * used in its place is the same deterministic one the Python tests use.
 */
class InlineEditorTest {

    // ------------------------------------------------------------------
    // 1. The edit lands where the user pointed
    // ------------------------------------------------------------------

    @Test
    fun aParagraphThatDoesNotExistIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("something else entirely"),
            chunks = chunks,
        )

        assertFailsWith<EditTargetNotFoundError> {
            editor.edit(request(paragraphIndex = 9), section)
        }
    }

    @Test
    fun aSelectionThatWasNeverWrittenIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("something else entirely"),
            chunks = chunks,
        )

        assertFailsWith<EditTargetNotFoundError> {
            editor.edit(
                request(selectedText = "A sentence that was never written down."),
                section,
            )
        }
    }

    @Test
    fun aSelectionThatSitsInAnotherParagraphIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("something else entirely"),
            chunks = chunks,
        )

        assertFailsWith<EditTargetNotFoundError> {
            editor.edit(
                request(paragraphIndex = 0, selectedText = "Mangrove forests slow storm surge"),
                section,
            )
        }
    }

    @Test
    fun aSectionThatIsNotTheOneNamedIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("something else entirely"),
            chunks = chunks,
        )

        assertFailsWith<EditTargetNotFoundError> {
            editor.edit(request(sectionId = "sec_other"), section)
        }
    }

    // ------------------------------------------------------------------
    // 2. No model, no prose
    // ------------------------------------------------------------------

    @Test
    fun withoutARewriteSeamTheEditFailsLoudly() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(checker = checker(), chunks = chunks)

        assertFailsWith<LMNotConfiguredError> {
            editor.edit(request(), section)
        }
    }

    @Test
    fun aSeamThatReportsNoModelFailsLoudly() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = object : RewriteFn {
                override fun rewrite(
                    instruction: String,
                    text: String,
                    passages: List<SourceChunk>,
                ): String = throw LMNotConfiguredError("inline edit rewrite")
            },
            chunks = chunks,
        )

        assertFailsWith<LMNotConfiguredError> {
            editor.edit(request(), section)
        }
    }

    @Test
    fun anEditorWithoutRoomForPassagesIsRefused() {
        assertFailsWith<ValidationError> {
            InlineEditor(checker = checker(), maxPassages = 0)
        }
    }

    // ------------------------------------------------------------------
    // 3. A revision that invents a source is refused
    // ------------------------------------------------------------------

    @Test
    fun aRevisionThatInventsASourceIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val invented = "f".repeat(64)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(
                "Coral reefs shelter a quarter of all marine species [#$invented]."
            ),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertFalse(result.applied)
        assertTrue(result.reason.contains(invented))
        // The section is the unchanged original, so the refusal cannot be
        // written back by a caller that trusts `applied`.
        assertEquals(section, result.section)
        assertEquals(section.content, result.section?.content)
    }

    @Test
    fun aRevisionThatContradictsItsSourceIsRefused() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(
                "Coral reefs shelter no marine species at all [#${coral.chunkId}]."
            ),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertFalse(result.applied)
        assertTrue(result.reason.isNotEmpty())
        assertEquals(section, result.section)
    }

    @Test
    fun theRefusalReasonNamesTheSourceThatWasInvented() {
        // The refusal reasons are user-facing text: they are pinned word for
        // word against the Python tier.
        val chunks = corpus()
        val section = section(chunks)
        val invented = "a".repeat(64)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("A sentence with a made-up source [#$invented]."),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertEquals(
            "The revision cites source chunks that do not exist: $invented. " +
                "The passage was left unchanged.",
            result.reason,
        )
    }

    // ------------------------------------------------------------------
    // 4. A supported revision is applied
    // ------------------------------------------------------------------

    @Test
    fun aSupportedRevisionIsApplied() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val mangrove = chunks[1]
        val revised = "Coral reefs shelter a quarter of all the marine species and their " +
            "larvae [#${coral.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(revised),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertTrue(result.applied)
        assertTrue(result.originalText.startsWith("Coral reefs shelter a quarter of all marine"))
        assertEquals(revised, result.revisedText)
        assertEquals(listOf(coral.chunkId), result.citedChunkIds)
        val scorecard = assertNotNull(result.scorecard)
        assertEquals(1, scorecard.supportedClaims)
        assertEquals(0, scorecard.contradictedClaims)

        val updated = result.section
        assertNotNull(updated)
        assertEquals(section.revisionCount + 1, updated.revisionCount)
        assertTrue(updated.content.contains(revised))
        // The paragraph that was not edited is still there, with its citation.
        assertTrue(updated.content.contains("[#${mangrove.chunkId}]"))
        assertEquals(listOf(coral.chunkId, mangrove.chunkId), updated.citedChunkIds)
        assertTrue(updated.isVerified)
    }

    @Test
    fun aRevisionThatMatchesTheOriginalIsANoOp() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val original = "Coral reefs shelter a quarter of all marine species and their larvae " +
            "[#${coral.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(original),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertTrue(result.applied)
        assertEquals("The revision matched the original text.", result.reason)
        assertEquals(section, result.section)
        assertEquals(section.revisionCount, result.section?.revisionCount)
    }

    @Test
    fun applyingTheSameEditTwiceLeavesTheSameState() {
        // The no-op is what makes the transition idempotent: the second
        // application does not move the revision count.
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val original = "Coral reefs shelter a quarter of all marine species and their larvae " +
            "[#${coral.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(original),
            chunks = chunks,
        )

        val first = editor.edit(request(), section)
        val second = editor.edit(request(), section)

        assertEquals(first.section, second.section)
        assertEquals(first.section?.revisionCount, second.section?.revisionCount)
    }

    @Test
    fun aProposalIsNotWrittenBackWhenPersistIsFalse() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val revised = "Coral reefs shelter a quarter of all the marine species and their " +
            "larvae [#${coral.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(revised),
            chunks = chunks,
        )

        val result = editor.edit(request(persist = false), section)

        assertEquals(section, result.section)
        assertEquals(section.content, result.section?.content)
        assertEquals(revised, result.revisedText)
        assertFalse(result.applied)
    }

    // ------------------------------------------------------------------
    // 5. The model is called once, and never with the corpus
    // ------------------------------------------------------------------

    @Test
    fun theModelIsCalledOnceAndNeverHandedTheCorpus() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val index = TestProvenanceIndex(chunks)
        val rewrite = RecordingRewrite(
            "Coral reefs shelter a quarter of all the marine species and their " +
                "larvae [#${coral.chunkId}]."
        )
        val editor = InlineEditor(
            checker = checker(),
            rewrite = rewrite,
            index = index,
            chunks = chunks,
            maxPassages = 2,
        )

        editor.edit(request(), section)

        assertEquals(1, rewrite.calls.size)
        val passages = rewrite.calls[0].passages
        assertTrue(passages.size <= 2)
        assertTrue(passages.size < chunks.size)
        val known = chunks.map { it.chunkId }.toSet()
        assertTrue(passages.all { known.contains(it.chunkId) })
    }

    @Test
    fun theConvenienceCallAppliesTheEdit() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val revised = "Coral reefs shelter a quarter of all the marine species and their " +
            "larvae [#${coral.chunkId}]."

        val result = applyInlineEdit(
            request(),
            section,
            checker = checker(),
            rewrite = RecordingRewrite(revised),
            chunks = chunks,
        )

        assertTrue(result.applied)
        assertNotNull(result.section)
        assertEquals(section.revisionCount + 1, result.section?.revisionCount)
    }

    @Test
    fun aWholeSectionCanBeRewritten() {
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val mangrove = chunks[1]
        val revised = "Coral reefs shelter a quarter of all marine species and their larvae " +
            "[#${coral.chunkId}].\n\n" +
            "Mangrove forests slow storm surge and trap sediment along tropical " +
            "coasts [#${mangrove.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(revised),
            chunks = chunks,
        )

        val result = editor.edit(request(scope = EditScope.SECTION), section)

        assertTrue(result.applied)
        assertEquals(EditScope.SECTION, result.scope)
        assertEquals(section.content.trim(), result.originalText)
        assertEquals(revised, result.section?.content)
    }

    // ------------------------------------------------------------------
    // 6. The offline double
    // ------------------------------------------------------------------

    @Test
    fun theOfflineDoubleCanRewriteAParagraph() {
        // The offline provider is the app's promise that the product can be
        // tried without a model account, so the seam has to speak the
        // vocabulary that double answers: the passages it is given, the
        // passage to work on, and the passage that comes back with its
        // citations attached.
        val chunks = corpus()
        val section = section(chunks)
        val coral = chunks[0]
        val editor = InlineEditor(
            checker = checker(),
            rewrite = LmRewrite(StubRewriteModel()),
            index = TestProvenanceIndex(chunks),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertTrue(result.applied)
        assertTrue(result.citedChunkIds.contains(coral.chunkId))
        val scorecard = assertNotNull(result.scorecard)
        assertEquals(0, scorecard.contradictedClaims)
        assertNotNull(result.section)
        assertEquals(section.revisionCount + 1, result.section?.revisionCount)
    }

    // ------------------------------------------------------------------
    // 7. Offsets are code points, not UTF-16 units
    // ------------------------------------------------------------------

    @Test
    fun anAstralCharacterInTheEditedParagraphDoesNotShiftTheRestOfTheSection() {
        val chunks = corpus()
        val coral = chunks[0]
        val mangrove = chunks[1]
        val first = "Coral reefs \uD83D\uDC1A shelter a quarter of all marine species and their " +
            "larvae [#${coral.chunkId}]."
        val second = "Mangrove forests slow storm surge and trap sediment along tropical coasts " +
            "[#${mangrove.chunkId}]."
        val section = SectionDraft(
            sectionId = "sec_1",
            title = "Coastal ecosystems",
            content = "$first\n\n$second",
            citedChunkIds = listOf(coral.chunkId, mangrove.chunkId),
        )
        val revised = "Coral reefs \uD83D\uDC1A shelter a quarter of all the marine species and " +
            "their larvae [#${coral.chunkId}]."
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite(revised),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertTrue(result.applied)
        // The splice lands on the paragraph boundary even though the
        // paragraph holds a character outside the Basic Multilingual Plane.
        assertEquals("$revised\n\n$second", result.section?.content)
    }

    @Test
    fun theTargetIsTheTrimmedParagraph() {
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("something else entirely"),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertEquals(
            "Coral reefs shelter a quarter of all marine species and their larvae " +
                "[#${chunks[0].chunkId}].",
            result.originalText,
        )
    }

    // ------------------------------------------------------------------
    // 8. The scorecard travels with the result
    // ------------------------------------------------------------------

    @Test
    fun aRefusedRevisionCarriesNoScorecard() {
        val chunks = corpus()
        val section = section(chunks)
        val invented = "b".repeat(64)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("A sentence with a made-up source [#$invented]."),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertFalse(result.applied)
        assertNull(result.scorecard)
        assertTrue(result.claims.isEmpty())
    }

    @Test
    fun aRevisionWithoutCitationsIsAppliedButLeavesTheSectionUnverified() {
        // The rule the Python tier applies: a revision that cites nothing is
        // not refused for that -- it is applied and the section is marked as
        // needing a revision, because the claims of the new text are
        // ungrounded.
        val chunks = corpus()
        val section = section(chunks)
        val editor = InlineEditor(
            checker = checker(),
            rewrite = RecordingRewrite("Coral reefs shelter a quarter of all marine species."),
            chunks = chunks,
        )

        val result = editor.edit(request(), section)

        assertTrue(result.applied)
        val scorecard = assertNotNull(result.scorecard)
        assertEquals(1, scorecard.ungroundedClaims)
        val updated = assertNotNull(result.section)
        assertFalse(updated.isVerified)
    }
}

/** The corpus the edits are grounded in. One sentence per chunk. */
private val CORPUS: List<String> = listOf(
    "Coral reefs shelter a quarter of all marine species and their larvae.",
    "Mangrove forests slow storm surge and trap sediment along tropical coasts.",
    "Medieval abbeys kept meticulous accounts of grain harvests and rents.",
    "Monastic libraries copied manuscripts by hand in scriptoria.",
)

/** Chunk the corpus into one passage per sentence. */
private fun corpus(): List<SourceChunk> = CORPUS.map { text ->
    SourceChunk.create(
        documentId = "doc_edit",
        text = text,
        charStart = 0,
        charEnd = CodePoints.length(text),
        byteStart = 0,
        byteEnd = CodePoints.utf8Size(text),
    )
}

/** Build a two-paragraph section whose paragraphs carry their citations. */
private fun section(chunks: List<SourceChunk>): SectionDraft {
    val coral = chunks[0]
    val mangrove = chunks[1]
    val content = "Coral reefs shelter a quarter of all marine species and their larvae " +
        "[#${coral.chunkId}].\n\n" +
        "Mangrove forests slow storm surge and trap sediment along tropical coasts " +
        "[#${mangrove.chunkId}]."
    return SectionDraft(
        sectionId = "sec_1",
        title = "Coastal ecosystems",
        content = content,
        citedChunkIds = listOf(coral.chunkId, mangrove.chunkId),
    )
}

/** A verifier that answers from the text itself, with no model involved. */
private fun checker(): CitationChecker = CitationChecker(evaluator = HeuristicNliEvaluator(), beta = 0.5)

/** Build an edit request for `sec_1`. */
private fun request(
    sectionId: String = "sec_1",
    instruction: String = "Make this tighter.",
    selectedText: String = "",
    scope: EditScope = EditScope.PARAGRAPH,
    paragraphIndex: Int = 0,
    persist: Boolean = true,
): InlineEditRequest = InlineEditRequest(
    sectionId = sectionId,
    instruction = instruction,
    docId = "doc_1",
    selectedText = selectedText,
    scope = scope,
    paragraphIndex = paragraphIndex,
    persist = persist,
)

/** One call to the rewrite seam. */
private class RewriteCall(
    val instruction: String,
    val text: String,
    val passages: List<SourceChunk>,
)

/** A rewrite seam that answers with a fixed text and records its calls. */
private class RecordingRewrite(private val revised: String) : RewriteFn {

    /** The calls the seam received, in order. */
    val calls: MutableList<RewriteCall> = mutableListOf()

    override fun rewrite(instruction: String, text: String, passages: List<SourceChunk>): String {
        calls.add(RewriteCall(instruction, text, passages))
        return revised
    }
}

/**
 * The offline language-model double, in the vocabulary the rewrite seam
 * speaks: it lifts the passage it was given and cites that passage's real
 * chunk id.
 */
private class StubRewriteModel : RewriteModel {

    override fun rewrite(instruction: String, draftText: String, assignedChunksText: String): String {
        val chunkId = CHUNK_HEADER.find(assignedChunksText)?.groupValues?.get(1).orEmpty()
        if (chunkId.isEmpty()) {
            return draftText
        }
        return "$draftText [$chunkId]"
    }

    private companion object {
        val CHUNK_HEADER: Regex = Regex("\\[#CHUNK_ID:\\s*([^\\]]+)]")
    }
}

/**
 * A retrieval index that ranks the chunks by the content words they share
 * with the query.
 */
private class TestProvenanceIndex(private val chunks: List<SourceChunk>) : ProvenanceIndexer {

    override fun query(query: String, topK: Int): List<SourceChunk> {
        val words = CONTENT_WORD.findAll(query.lowercase()).map { it.value }.filter { it.length > 3 }.toSet()
        return chunks
            .map { chunk ->
                val overlap = CONTENT_WORD.findAll(chunk.text.lowercase())
                    .map { it.value }
                    .filter { it.length > 3 }
                    .toSet()
                    .intersect(words)
                    .size
                overlap to chunk
            }
            .filter { it.first > 0 }
            .sortedWith(
                compareByDescending<Pair<Int, SourceChunk>> { it.first }.thenBy { it.second.chunkId },
            )
            .take(topK)
            .map { it.second }
    }

    override fun getChunk(chunkId: String): SourceChunk? = chunks.firstOrNull { it.chunkId == chunkId }

    override fun hasChunk(chunkId: String): Boolean = getChunk(chunkId) != null

    override fun getAllChunkIds(): List<String> = chunks.map { it.chunkId }

    private companion object {
        val CONTENT_WORD: Regex = Regex("[a-z0-9]+")
    }
}
