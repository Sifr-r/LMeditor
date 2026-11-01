"""The frozen operator-edit rule.

An operator's edit replaces the text and keeps the provenance; it does not keep
the verdict. This is the original Python implementation, kept here so that the
parity harness has a witness to compare the Kotlin tier against. It is plain
Python on purpose: see ``lmeditor._reference``.
"""

from __future__ import annotations

from lmeditor.core.schemas import SectionDraft
from lmeditor.core.types import SectionStatus


def merge_operator_text(
    draft: SectionDraft,
    *,
    content: str,
    title: str | None = None,
    order: int | None = None,
) -> SectionDraft:
    """Return the section as the operator left it, with its provenance kept.

    The merged section keeps the pipeline's citations, drops the claims (they
    were extracted from text that no longer exists), and comes back unmeasured:
    its status is ``PENDING`` and it is not verified, because nobody has
    measured *this* text yet.
    """
    return draft.model_copy(
        update={
            "content": content,
            "title": title if title is not None else draft.title,
            "order": order if order is not None else draft.order,
            "claims": [],
            "status": SectionStatus.PENDING,
            "is_verified": False,
            "critique": None,
        }
    )


__all__ = ["merge_operator_text"]
