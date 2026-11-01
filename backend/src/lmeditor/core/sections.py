"""The domain rule for an operator's own edit to a section.

The pipeline's job is to establish *where every claim came from*. The
operator's job is to say what the document should say. Those are different
acts, and the rule that joins them lives in the Kotlin tier, in
``lmeditor.core.sections``, so that the review gate and the save route apply
exactly the same one.

The rule is: **an operator's edit replaces the text and keeps the
provenance.** A citation the pipeline verified still says where its passage
came from after the sentence around it has been reworded, so the audit trail
survives the edit — discarding it would lose the very thing this application
exists to maintain.

What does not survive the edit is the *verdict*. The text has changed, so the
verdict that described the previous text no longer describes this one. The
merged section is therefore handed back unmeasured, with a status that says so,
for the server to measure again and report what it found.
"""

from __future__ import annotations

from lmeditor.core.schemas import SectionDraft


def merge_operator_text(
    draft: SectionDraft,
    *,
    content: str,
    title: str | None = None,
    order: int | None = None,
) -> SectionDraft:
    """Return the section as the operator left it, with its provenance kept.

    The rule itself lives in the Kotlin tier; this call is the door to it.

    Args:
        draft: The section as the pipeline drafted it.
        content: The text the operator wrote.
        title: A new heading, or ``None`` to keep the drafted one.
        order: A new position in the document, or ``None`` to keep the drafted one.

    Returns:
        The merged section. Its **citations** — the lineages saying which
        passage each chunk id came from — are the pipeline's, unchanged: the
        operator reworded the text, not the record of where the passages came
        from. Its **claims** are cleared, because a claim is a proposition
        extracted from the text, and the text the operator wrote is not the
        text the claims were extracted from. The measurement re-extracts them
        from the operator's words, so the verdict describes what is now on the
        page.

        Its status is ``PENDING`` and it is not verified, because nobody has
        measured *this* text yet, and saying otherwise would be a verdict
        nobody earned.
    """
    from lmeditor.tier import core as tier_core

    return tier_core.merge_operator_text(draft, content=content, title=title, order=order)
