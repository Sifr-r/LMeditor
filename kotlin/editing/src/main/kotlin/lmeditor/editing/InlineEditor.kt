// The schema models strip whitespace when they are constructed and not when
// they are copied, the way Pydantic's constructor and `model_copy()` do. The
// generated `copy()` of a model with a private constructor is flagged by the
// compiler; the pattern is deliberate, and it is documented in
// `lmeditor.core.Schemas`.
@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.editing

import lmeditor.core.CitationHallucinationError
import lmeditor.core.Citations
import lmeditor.core.CodePoints
import lmeditor.core.Claim
import lmeditor.core.EditScope
import lmeditor.core.EditTargetNotFoundError
import lmeditor.core.EntailmentStatus
import lmeditor.core.InlineEditRejectedError
import lmeditor.core.InlineEditRequest
import lmeditor.core.InlineEditResult
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SectionDraft
import lmeditor.core.SectionStatus
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError
import lmeditor.core.VerificationScorecard
import lmeditor.text.CitationChecker
import lmeditor.text.extractClaims

/**
 * The reason returned when the model reproduced the text it was given.
 */
private const val NO_OP_REASON: String = "The revision matched the original text."

/**
 * A paragraph boundary: a blank line between two blocks of text.
 */
private val PARAGRAPH_SEPARATOR: Regex = Regex("\n[ \t]*\n")

/**
 * Words used to decide whether a chunk speaks to the target at all.
 */
private val WORD: Regex = Regex("[a-z0-9]+")

/**
 * Where the edit lands inside the section's content.
 *
 * `start` and `end` are the code-point offsets of the text to replace, so
 * the rest of the section is preserved exactly rather than being
 * re-assembled from split paragraphs.
 */
private data class Target(val start: Int, val end: Int, val text: String)

/**
 * Applies small, grounded edits to one paragraph or section of a draft.
 *
 * The editor owns no state beyond its collaborators: the retrieval index it
 * grounds the edit in, the chunks it may fall back to, the verifier it
 * checks the result with, and the rewrite seam it calls. It is therefore
 * safe to build one per request.
 *
 * The shape of the operation is a pipeline, and each stage can only say no:
 *
 * 1. **Target resolution.** The paragraph is found inside the section, and
 *    the user's selection must really be inside it. A target that does not
 *    exist raises [EditTargetNotFoundError] rather than editing whatever
 *    happens to be at that position.
 * 2. **Grounding.** Only the passages that answer the instruction are offered
 *    to the model. The corpus itself is never handed over.
 * 3. **The rewrite.** Exactly one seam call. With no seam configured the
 *    operation fails loudly; it never substitutes template prose.
 * 4. **Phantom-citation rejection.** Every citation marker in the result
 *    must name a chunk that exists. A marker that names nothing is an
 *    invented source, and the revision is refused.
 * 5. **Support check.** The claims of the revised text are verified against
 *    the chunks the revision cites. A contradicted claim is refused.
 *
 * A refused revision is *not* an error: the result carries `applied = false`,
 * the reason it was refused, and the proposal the model made, so the user
 * can see what was on offer and why it was left alone. Only the section that
 * came back is ever written to the document, and it is written back only
 * when `applied` is true.
 *
 * Every character offset this class handles is a **code-point** offset, the
 * space the Python tier uses; see `lmeditor.core.CodePoints`.
 */
class InlineEditor(
    /** The verifier that checks the revised text. */
    private val checker: CitationChecker,
    /** The seam that performs the rewrite. */
    private val rewrite: RewriteFn? = null,
    /** The retrieval index the edit is grounded in. */
    private val index: ProvenanceIndexer? = null,
    /** The chunks the editor may fall back to when there is no index. */
    private val chunks: List<SourceChunk> = emptyList(),
    /** The largest number of passages that may be handed to the model. */
    val maxPassages: Int = 6,
) {

    init {
        if (maxPassages <= 0) {
            throw ValidationError(
                "max_passages must be positive, got $maxPassages",
                field = "max_passages",
                value = maxPassages,
            )
        }
    }

    /**
     * Rewrite one paragraph or section of [section] and check the result.
     *
     * @param request what to change and how.
     * @param section the section the change applies to.
     * @return the outcome of the edit. When the revision was refused the
     *   result carries `applied = false`, the reason it was refused and the
     *   proposal that was refused; the section it carries is the unchanged
     *   original, so a caller that writes back on `applied = true` can never
     *   persist a rejected revision.
     * @throws ValidationError the request is empty or malformed.
     * @throws EditTargetNotFoundError the section, the paragraph or the
     *   selected text does not exist.
     * @throws LMNotConfiguredError no rewrite seam was given.
     * @throws InlineEditRejectedError the rewrite call failed.
     */
    fun edit(request: InlineEditRequest, section: SectionDraft): InlineEditResult {
        validate(request, section)
        val target = resolveTarget(request, section)
        val originalText = target.text

        val passages = gatherPassages(request.instruction, originalText)
        val rewriteFn = resolveRewrite()
        val (revisedText, refusal) = runRewrite(rewriteFn, request, originalText, passages)
        if (refusal.isNotEmpty()) {
            return refused(request, section, originalText, revisedText, refusal)
        }

        val identical = revisedText == originalText
        if (revisedText.isEmpty() && !identical) {
            return refused(
                request,
                section,
                originalText,
                revisedText,
                "The rewrite returned no text, so the passage was left unchanged.",
            )
        }

        if (identical) {
            // A revision that changes nothing is a success, not a failure:
            // there is nothing to apply, and the revision count must not
            // move, so applying the same edit twice is harmless.
            return InlineEditResult(
                docId = request.docId,
                sectionId = request.sectionId,
                scope = request.scope,
                originalText = originalText,
                revisedText = revisedText,
                applied = true,
                reason = NO_OP_REASON,
                citedChunkIds = Citations.extractChunkIds(revisedText),
                claims = emptyList(),
                scorecard = null,
                section = section,
            )
        }

        val citedIds = Citations.extractChunkIds(revisedText)

        val unknownIds = unknownCitedChunks(citedIds) { isKnownChunk(it, section) }
        if (unknownIds.isNotEmpty()) {
            return refused(
                request,
                section,
                originalText,
                revisedText,
                "The revision cites source chunks that do not exist: " +
                    "${unknownIds.joinToString(", ")}. The passage was left unchanged.",
                citedIds,
            )
        }

        val chunksById = resolveChunksById(citedIds)
        val claims = extractClaims(revisedText, section.sectionId)
        val (evaluated, scorecard) = try {
            checker.auditClaims(
                claims = claims,
                chunksById = chunksById,
                strictHallucinationCheck = true,
                sectionId = section.sectionId,
            )
        } catch (e: CitationHallucinationError) {
            return refused(
                request,
                section,
                originalText,
                revisedText,
                "The revision cites a source that cannot be verified: " +
                    "${e.chunkId}. The passage was left unchanged.",
                citedIds,
            )
        }

        if (scorecard.contradictedClaims > 0) {
            return refused(
                request,
                section,
                originalText,
                revisedText,
                "The revision contradicts the sources it cites " +
                    "(${scorecard.contradictedClaims} contradicted claim(s)). " +
                    "The passage was left unchanged.",
                citedIds,
            )
        }

        val updated = updatedSection(section, target, revisedText, evaluated, scorecard)
        val persisted = request.persist
        val reason = if (persisted) {
            "The revision is supported by the sources it cites and was applied."
        } else {
            "The revision is supported by the sources it cites; it was " +
                "returned as a proposal and the section was left unchanged."
        }
        return InlineEditResult(
            docId = request.docId,
            sectionId = request.sectionId,
            scope = request.scope,
            originalText = originalText,
            revisedText = revisedText,
            applied = persisted,
            reason = reason,
            citedChunkIds = citedIds,
            claims = evaluated,
            scorecard = scorecard,
            section = if (persisted) updated else section,
        )
    }

    // ------------------------------------------------------------------
    // Boundaries
    // ------------------------------------------------------------------

    /** Check the request before any work is done. */
    private fun validate(request: InlineEditRequest, section: SectionDraft) {
        if (request.instruction.isBlank()) {
            throw ValidationError(
                "an inline edit needs a non-empty instruction.",
                field = "instruction",
                value = request.instruction,
            )
        }
        if (request.sectionId != section.sectionId) {
            throw EditTargetNotFoundError(
                request.sectionId,
                request.paragraphIndex,
                message = (
                    "Edit target not found: the request names section " +
                        "'${request.sectionId}' but the section being edited is " +
                        "'${section.sectionId}'."
                    ),
            )
        }
    }

    /**
     * Find the text the edit applies to.
     *
     * @throws EditTargetNotFoundError the paragraph does not exist, or the
     *   selected text is not inside the section.
     */
    private fun resolveTarget(request: InlineEditRequest, section: SectionDraft): Target {
        if (request.scope == EditScope.SECTION) {
            if (request.selectedText.isNotEmpty() && !section.content.contains(request.selectedText)) {
                throw EditTargetNotFoundError(
                    section.sectionId,
                    request.paragraphIndex,
                    message = (
                        "Edit target not found: the selected text does not " +
                            "appear in section '${section.sectionId}'."
                        ),
                )
            }
            return Target(0, CodePoints.length(section.content), section.content.trim())
        }

        val paragraphs = paragraphSpans(section.content)
        if (request.paragraphIndex < 0 || request.paragraphIndex >= paragraphs.size) {
            throw EditTargetNotFoundError(section.sectionId, request.paragraphIndex)
        }

        val target = paragraphs[request.paragraphIndex]
        if (request.selectedText.isNotEmpty()) {
            if (!section.content.contains(request.selectedText)) {
                throw EditTargetNotFoundError(
                    section.sectionId,
                    request.paragraphIndex,
                    message = (
                        "Edit target not found: the selected text does not " +
                            "appear in section '${section.sectionId}'."
                        ),
                )
            }
            if (!target.text.contains(request.selectedText)) {
                throw EditTargetNotFoundError(
                    section.sectionId,
                    request.paragraphIndex,
                    message = (
                        "Edit target not found: the selected text is not in " +
                            "paragraph ${request.paragraphIndex} of section " +
                            "'${section.sectionId}'."
                        ),
                )
            }
        }
        return target
    }

    // ------------------------------------------------------------------
    // Grounding
    // ------------------------------------------------------------------

    /**
     * Return the passages the revision may be grounded in.
     *
     * With an index, the passages are the ones that answer the instruction
     * and the target. Without one, the supplied chunks are used, but only
     * the ones that actually speak to the target. Either way the corpus is
     * never handed over whole.
     */
    private fun gatherPassages(instruction: String, targetText: String): List<SourceChunk> {
        val retrieval = index
        if (retrieval != null) {
            val query = "$instruction $targetText".trim()
            return retrieval.query(query, maxPassages).take(maxPassages)
        }
        return overlappingPassages(targetText)
    }

    /** Return the supplied chunks whose text overlaps the target. */
    private fun overlappingPassages(targetText: String): List<SourceChunk> {
        val targetWords = contentWords(targetText)
        if (targetWords.isEmpty()) {
            return emptyList()
        }

        val scored = chunks.mapNotNull { chunk ->
            val overlap = contentWords(chunk.text).intersect(targetWords).size
            if (overlap > 0) overlap to chunk else null
        }
        return scored
            .sortedWith(
                compareByDescending<Pair<Int, SourceChunk>> { it.first }.thenBy { it.second.chunkId },
            )
            .take(maxPassages)
            .map { it.second }
    }

    /** Return the chunk with [chunkId], or `null` when there is none. */
    private fun resolveChunk(chunkId: String): SourceChunk? {
        val fromIndex = index?.getChunk(chunkId)
        if (fromIndex != null) {
            return fromIndex
        }
        return chunks.firstOrNull { it.chunkId == chunkId }
    }

    /**
     * Whether [chunkId] names a source that exists.
     *
     * A chunk the section already cites is known even when the editor was
     * not given its text: the section has already made that attribution,
     * and the editor has no reason to call it an invention.
     */
    private fun isKnownChunk(chunkId: String, section: SectionDraft): Boolean =
        resolveChunk(chunkId) != null || section.citedChunkIds.contains(chunkId)

    /** Return the chunks the revision cites, keyed by chunk id. */
    private fun resolveChunksById(chunkIds: List<String>): Map<String, SourceChunk> {
        val resolved = LinkedHashMap<String, SourceChunk>()
        for (chunkId in chunkIds) {
            val chunk = resolveChunk(chunkId) ?: continue
            resolved[chunkId] = chunk
        }
        return resolved
    }

    // ------------------------------------------------------------------
    // The rewrite
    // ------------------------------------------------------------------

    /**
     * Return the seam that performs the rewrite.
     *
     * @throws LMNotConfiguredError no seam was given. LMeditor never
     *   fabricates prose in place of a model call.
     */
    private fun resolveRewrite(): RewriteFn =
        rewrite ?: throw LMNotConfiguredError("inline edit rewrite")

    /**
     * Return the instruction handed to the model.
     *
     * A selection is part of the instruction: it is what "this" refers to
     * in "make this tighter", so the model is told which span of the
     * passage the user is talking about.
     */
    private fun composeInstruction(request: InlineEditRequest): String {
        val instruction = request.instruction.trim()
        if (request.selectedText.isEmpty()) {
            return instruction
        }
        return (
            "$instruction\n\n" +
                "The user selected this exact span inside the passage; revise " +
                "this span:\n${request.selectedText}"
            )
    }

    /**
     * Call the rewrite seam exactly once and return the revised text.
     *
     * @return the revised text and the reason the revision was refused, which
     *   is empty when the seam produced a text at all. A seam that refused
     *   the revision itself -- because the model call failed -- is not an
     *   error: the passage stays as it is and the reason travels back to the
     *   caller.
     * @throws LMNotConfiguredError the seam reported that no model is
     *   available.
     * @throws InlineEditRejectedError the seam failed in a way it did not
     *   report itself.
     */
    private fun runRewrite(
        rewriteFn: RewriteFn,
        request: InlineEditRequest,
        targetText: String,
        passages: List<SourceChunk>,
    ): Pair<String, String> {
        val instruction = composeInstruction(request)
        val revised = try {
            rewriteFn.rewrite(instruction, targetText, passages)
        } catch (e: LMNotConfiguredError) {
            throw e
        } catch (e: InlineEditRejectedError) {
            return "" to e.reason
        } catch (e: RuntimeException) {
            // The seam is an injected dependency and may fail in any way.
            // The failure is not swallowed: it is carried back as a typed
            // domain error that names what went wrong.
            throw InlineEditRejectedError(
                request.sectionId,
                reason = "the rewrite call failed: $e",
            )
        }
        return revised.trim() to ""
    }

    // ------------------------------------------------------------------
    // The result
    // ------------------------------------------------------------------

    /**
     * Build the result of a revision that was not accepted.
     *
     * The proposal is carried along so the user can see what was on offer,
     * but the section is the unchanged original: a refused revision can
     * never be written back.
     */
    private fun refused(
        request: InlineEditRequest,
        section: SectionDraft,
        originalText: String,
        revisedText: String,
        reason: String,
        citedChunkIds: List<String> = emptyList(),
    ): InlineEditResult = InlineEditResult(
        docId = request.docId,
        sectionId = request.sectionId,
        scope = request.scope,
        originalText = originalText,
        revisedText = revisedText,
        applied = false,
        reason = reason,
        citedChunkIds = citedChunkIds,
        claims = emptyList(),
        scorecard = null,
        section = section,
    )

    /**
     * Return the section with the revision written into it.
     *
     * Only the target's span of the content is replaced, so the rest of the
     * section -- and every citation in it -- survives untouched. The cited
     * chunk ids are recomputed from the new content, so a citation the
     * revision dropped from its own paragraph goes away with it and a
     * citation the other paragraphs still carry stays.
     */
    private fun updatedSection(
        section: SectionDraft,
        target: Target,
        revisedText: String,
        evaluated: List<Claim>,
        scorecard: VerificationScorecard,
    ): SectionDraft {
        val newContent = CodePoints.slice(section.content, 0, target.start) +
            revisedText +
            CodePoints.slice(section.content, target.end, CodePoints.length(section.content))
        val claims = mergedClaims(section, target, revisedText, evaluated)
        val isVerified = meetsVerificationBar(newContent, claims, scorecard)
        val status = if (isVerified) SectionStatus.VERIFIED else SectionStatus.NEEDS_REVISION
        return section.copy(
            content = newContent,
            revisionCount = section.revisionCount + 1,
            claims = claims,
            isVerified = isVerified,
            status = status,
            citedChunkIds = Citations.extractChunkIds(newContent),
        )
    }

    /**
     * Return the claims of the section after the revision.
     *
     * The claims of the revised text are the ones this edit verified. The
     * claims of the paragraphs the edit did not touch are carried over, so
     * an inline edit refreshes the section's verification state instead of
     * erasing it. Their offsets are moved to where those paragraphs now
     * sit, because the revision changed the length of the text before them.
     */
    private fun mergedClaims(
        section: SectionDraft,
        target: Target,
        revisedText: String,
        evaluated: List<Claim>,
    ): List<Claim> {
        val shift = CodePoints.length(revisedText) - CodePoints.length(target.text)
        val merged = mutableListOf<Claim>()
        for (claim in section.claims) {
            if (claim.charEnd <= target.start) {
                merged.add(claim)
            } else if (claim.charStart >= target.end) {
                merged.add(
                    claim.copy(
                        charStart = claim.charStart + shift,
                        charEnd = claim.charEnd + shift,
                    ),
                )
            }
        }

        // The claims of the revision, moved into the section's coordinates.
        for (claim in evaluated) {
            merged.add(
                claim.copy(
                    charStart = claim.charStart + target.start,
                    charEnd = claim.charEnd + target.start,
                ),
            )
        }
        return merged.sortedWith(compareBy<Claim> { it.charStart }.thenBy { it.claimId })
    }

    /**
     * Whether the revised section meets the verification bar.
     *
     * The rule is the one the verifier itself applies: the section must
     * have content, its groundedness must reach the threshold, and no claim
     * may be contradicted, ungrounded or merely neutral. The claims the
     * edit did not touch are included in that judgement, so a section that
     * was already shaky does not become "verified" because one paragraph of
     * it was.
     */
    private fun meetsVerificationBar(
        content: String,
        claims: List<Claim>,
        scorecard: VerificationScorecard,
    ): Boolean =
        content.isNotBlank() &&
            scorecard.fBeta >= checker.fBetaThreshold &&
            scorecard.contradictedClaims == 0 &&
            scorecard.ungroundedClaims == 0 &&
            scorecard.neutralClaims == 0 &&
            claims.all { it.entailmentStatus == EntailmentStatus.ENTAILED }

    /**
     * Return every paragraph of [content] with its code-point offsets.
     *
     * A paragraph is a blank-line-separated block of text. The offsets point
     * at the trimmed block, so a replacement touches the paragraph and
     * nothing else.
     */
    private fun paragraphSpans(content: String): List<Target> {
        val spans = mutableListOf<Target>()
        var cursor = 0
        for (match in PARAGRAPH_SEPARATOR.findAll(content)) {
            appendParagraph(spans, content, cursor, match.range.first)
            cursor = match.range.last + 1
        }
        appendParagraph(spans, content, cursor, content.length)
        return spans
    }

    /** Add the trimmed block between [start] and [end] to [spans]. */
    private fun appendParagraph(spans: MutableList<Target>, content: String, start: Int, end: Int) {
        val block = content.substring(start, end)
        val trimmed = block.trim()
        if (trimmed.isEmpty()) {
            return
        }
        val offset = start + (block.length - block.trimStart().length)
        spans.add(
            Target(
                start = CodePoints.offsetOf(content, offset),
                end = CodePoints.offsetOf(content, offset + trimmed.length),
                text = trimmed,
            ),
        )
    }
}

/**
 * Apply a targeted edit to [section] and return the outcome.
 *
 * This is the one-call entry point: it builds an [InlineEditor] from the
 * collaborators it is given and runs the edit. It is the function a route
 * calls.
 *
 * @throws ValidationError the request is empty or malformed.
 * @throws EditTargetNotFoundError the section, the paragraph or the selected
 *   text does not exist.
 * @throws LMNotConfiguredError no rewrite seam was given.
 * @throws InlineEditRejectedError the rewrite call failed.
 */
fun applyInlineEdit(
    request: InlineEditRequest,
    section: SectionDraft,
    checker: CitationChecker,
    rewrite: RewriteFn? = null,
    index: ProvenanceIndexer? = null,
    chunks: List<SourceChunk> = emptyList(),
    maxPassages: Int = 6,
): InlineEditResult = InlineEditor(
    checker = checker,
    rewrite = rewrite,
    index = index,
    chunks = chunks,
    maxPassages = maxPassages,
).edit(request, section)

/**
 * Return the content words of [text].
 *
 * Short words are dropped because they are shared by every passage in the
 * corpus and would make every chunk look relevant to every target.
 */
private fun contentWords(text: String): Set<String> =
    WORD.findAll(text.lowercase())
        .map { it.value }
        .filter { it.length > 3 }
        .toSet()
