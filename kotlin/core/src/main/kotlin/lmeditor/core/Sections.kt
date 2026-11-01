// The schema models strip whitespace when they are constructed and not when
// they are copied, the way Pydantic's constructor and `model_copy()` do. The
// generated `copy()` of a model with a private constructor is flagged by the
// compiler; the pattern is deliberate, and it is documented in
// `lmeditor.core.Schemas`.
@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.core

/**
 * The domain rule for an operator's own edit to a section.
 *
 * The rule is: **an operator's edit replaces the text and keeps the
 * provenance.** A citation the pipeline verified still says where its passage
 * came from after the sentence around it has been reworded, so the audit trail
 * survives the edit.
 *
 * What does not survive the edit is the *verdict*. The text has changed, so
 * the verdict that described the previous text no longer describes this one.
 * The merged section is therefore handed back unmeasured, with a status that
 * says so, for the server to measure again.
 *
 * The rule lives in one place so that the review gate and the save route
 * cannot drift apart.
 */
object Sections {

    /**
     * Return the section as the operator left it, with its provenance kept.
     *
     * @param draft the section as the pipeline drafted it
     * @param content the text the operator wrote
     * @param title a new heading, or `null` to keep the drafted one
     * @param order a new position in the document, or `null` to keep the drafted one
     */
    fun mergeOperatorText(
        draft: SectionDraft,
        content: String,
        title: String? = null,
        order: Int? = null,
    ): SectionDraft = draft.copy(
        content = content,
        title = title ?: draft.title,
        order = order ?: draft.order,
        // A claim is a proposition extracted from *the text*. The operator's
        // words are not the words the claims were extracted from, so the
        // measurement re-extracts them.
        claims = emptyList(),
        status = SectionStatus.PENDING,
        isVerified = false,
        critique = null,
    )
}
