package lmeditor.editing

import lmeditor.core.Citations
import lmeditor.core.Claim
import lmeditor.core.SectionDraft
import lmeditor.core.SourceChunk
import lmeditor.text.extractClaims

/**
 * The verdict on a proposed revision.
 *
 * @property accepted whether the revision may stand.
 * @property reason why it may, or why it may not.
 * @property citedChunkIds the source chunks the revision cites.
 * @property claims the claims the revision makes, with no verdict on them.
 */
data class RevisionValidation(
    val accepted: Boolean,
    val reason: String,
    val citedChunkIds: List<String>,
    val claims: List<Claim>,
)

/**
 * Whether a proposed revision may stand, given the sources the document has.
 *
 * The *generation* of a revision is a model call and stays in the Python tier.
 * The *acceptance* of one is a rule, and the rule is this: a revision may not
 * cite a source the document does not have. A revision that invents a
 * citation is refused, and the refusal is a result rather than an exception,
 * so the caller can show the operator what happened and why.
 *
 * The claims the revision makes are returned with no verdict on them: the
 * model that judges a claim lives in the Python tier, and its answer is
 * handed back to Kotlin for the audit.
 *
 * @param revisedText the text the model proposed.
 * @param section the section the revision belongs to.
 * @param chunks the source chunks the document has.
 * @return the verdict on the revision.
 */
fun validateRevision(
    revisedText: String,
    section: SectionDraft,
    chunks: List<SourceChunk>,
): RevisionValidation {
    val citedIds = Citations.extractChunkIds(revisedText)
    val knownIds = HashSet(chunks.map { it.chunkId })
    knownIds.addAll(section.citedChunkIds)

    val unknownIds = unknownCitedChunks(citedIds) { it in knownIds }
    if (unknownIds.isNotEmpty()) {
        return RevisionValidation(
            accepted = false,
            reason = "The revision cites source chunks that do not exist: " +
                "${unknownIds.joinToString(", ")}. The passage was left unchanged.",
            citedChunkIds = citedIds,
            claims = emptyList(),
        )
    }

    return RevisionValidation(
        accepted = true,
        reason = "The revision cites only sources the document has.",
        citedChunkIds = citedIds,
        claims = extractClaims(revisedText, section.sectionId),
    )
}

/**
 * Return the cited chunk ids the document cannot account for.
 *
 * This is the rule the editor and the validator both use, so a revision
 * refused by one is refused by the other.
 */
internal fun unknownCitedChunks(
    citedIds: List<String>,
    isKnown: (String) -> Boolean,
): List<String> = citedIds.filter { !isKnown(it) }
