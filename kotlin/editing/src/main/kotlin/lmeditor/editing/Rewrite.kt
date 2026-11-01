package lmeditor.editing

import lmeditor.core.InlineEditRejectedError
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError

/**
 * The contract a targeted revision must satisfy.
 *
 * A rewrite is given the instruction in plain language, the exact text to
 * revise, and the passages the revision may be grounded in. It returns the
 * revised text and nothing else.
 *
 * The editor checks what comes back -- the cited chunk ids must exist and
 * the claims must be supported -- so an implementation of this interface is
 * free to be as creative as it likes, but never free to invent a source.
 */
interface RewriteFn {

    /**
     * Return the revised text for [text].
     *
     * @param instruction what the revision should achieve, in plain language.
     * @param text the exact text to revise.
     * @param passages the passages the revision may be grounded in.
     * @return the revised text.
     */
    fun rewrite(instruction: String, text: String, passages: List<SourceChunk>): String
}

/**
 * The language-model call a revision is made of.
 *
 * The generation of a revision is a generative act and needs a model, so it
 * stays in the Python tier; this interface is where that call crosses the
 * NDJSON seam. The implementation returns the model's raw answer, and every
 * deterministic rule around it -- what the model is told, how the passages
 * are laid out, what a failed call means -- stays in Kotlin and is the same
 * whether the answer came from a real model or from a double.
 */
interface RewriteModel {

    /**
     * Perform the rewrite and return the model's raw answer.
     *
     * @param instruction what the revision should achieve, in plain language.
     * @param draftText the exact text to revise.
     * @param assignedChunksText the passages the revision is grounded in,
     *   one passage per block, each prefixed with the chunk id it must be
     *   cited by.
     * @return the revised passage.
     */
    fun rewrite(instruction: String, draftText: String, assignedChunksText: String): String
}

/**
 * The vocabulary a revision is asked in.
 *
 * The field names are the house vocabulary rather than names of this
 * module's own: `draft_text` is the passage to work on,
 * `assigned_chunks_text` is the passages the work is grounded in, and
 * `grounded_draft` is the passage with its citations attached. The drafter,
 * the citation inserter and the offline language-model double all speak that
 * vocabulary, so a revision is asked for in the same terms as a draft is.
 *
 * The rubric says the two things the editor cannot check afterwards: the
 * revision must cover the whole passage, and it may only cite the chunk ids
 * it was given.
 */
object RewritePrompt {

    /** The signature of the rewrite program. */
    const val SIGNATURE: String =
        "instruction: str, draft_text: str, assigned_chunks_text: str -> grounded_draft: str"

    /** The rubric handed to the model. */
    const val INSTRUCTIONS: String =
        "Rewrite the passage so that it satisfies the instruction. " +
            "Return the complete revised passage in the grounded_draft field, not a " +
            "fragment and not a comment. Ground every factual statement in the " +
            "supplied passages and keep the inline citation markers [#chunk_id] of " +
            "the passages that support the revised text. You may not cite a chunk " +
            "id that does not appear in the supplied passages, and you may not " +
            "assert anything the passages do not support."

    /**
     * Render the passages for the prompt, each prefixed with its chunk id.
     *
     * The format is the drafter's own, so the passages are laid out in the
     * prompt exactly as they are when a section is drafted: one passage per
     * block, each with the chunk id the model has to cite it by.
     */
    fun formatPassages(passages: List<SourceChunk>): String {
        val blocks = passages.mapIndexed { index, chunk ->
            var header = "--- PASSAGE ${index + 1} [#CHUNK_ID: ${chunk.chunkId}] ---"
            if (chunk.documentTitle.isNotEmpty()) {
                header += " (Source: ${chunk.documentTitle})"
            }
            "$header\n${chunk.text.trim()}\n"
        }
        return blocks.joinToString("\n")
    }
}

/**
 * The rewrite performed with a caller-supplied language model.
 *
 * This is the offline implementation of [RewriteFn]: it owns no model of its
 * own, so the caller may hand it a model that talks to a real provider, a
 * local server, or the deterministic double that ships with the app.
 *
 * Two rules hold everywhere in this class:
 *
 * * **LMeditor never fabricates prose in place of a model call.** A call that
 *   comes back empty is refused with
 *   [InlineEditRejectedError]; there is no template fallback a reader could
 *   mistake for generated output.
 * * **The model is never handed the corpus.** It is handed the passages that
 *   were retrieved for this edit and nothing more.
 */
class LmRewrite(private val model: RewriteModel) : RewriteFn {

    override fun rewrite(instruction: String, text: String, passages: List<SourceChunk>): String {
        validate(instruction)
        val revised = try {
            model.rewrite(instruction, text, RewritePrompt.formatPassages(passages))
        } catch (e: LMNotConfiguredError) {
            // The model is not there. That is not a failed revision; it is a
            // refusal to run at all, and the caller has to hear about it.
            throw e
        } catch (e: InlineEditRejectedError) {
            throw e
        } catch (e: RuntimeException) {
            // The model is an injected dependency and may fail in any way.
            // The failure is not swallowed: it is carried back as a typed
            // domain error that names what went wrong.
            throw InlineEditRejectedError(reason = "the rewrite model call failed: $e")
        }

        val trimmed = revised.trim()
        if (trimmed.isEmpty()) {
            throw InlineEditRejectedError(reason = "the rewrite model returned no text.")
        }
        return trimmed
    }

    /** Reject a malformed rewrite request at the seam's boundary. */
    private fun validate(instruction: String) {
        if (instruction.isBlank()) {
            throw ValidationError(
                "a rewrite needs a non-empty instruction.",
                field = "instruction",
                value = instruction,
            )
        }
    }
}
