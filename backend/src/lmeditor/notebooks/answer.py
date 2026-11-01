"""The Ask: the question a notebook answers from its own sources.

The ask is the promise that makes a notebook worth keeping: the operator asks
a question of the material they gathered, and the answer comes from that
material and from nowhere else. A passage that is not in the notebook is not a
passage the answer may lean on, however relevant it looks.

The six rules the ask follows, in order:

1. The question is answered **only** from the notebook's sources.
2. The passages are ranked with the retrieval the backend already has, and
   the top of that ranking is what the answer is written from.
3. The answer is written by a DSPy module whose passage field is named
   ``assigned_chunks_text``, so the whole thing is provable with no provider.
4. Every citation names a passage the answer actually used, and quotes it. A
   chunk id that is not among the notebook's retrieved passages is dropped,
   never reported.
5. A citation is *supported* when the answer's own claims that carry it are
   supported by that passage, under the lineage rule the tier already owns.
   An unsupported citation is not reported as supported.
6. ``answered`` is true when the answer is not empty and at least one
   supported citation survives. When the sources cannot answer the question,
   the answer says so and ``answered`` is false.

The rules that decide anything about a passage -- the citation grammar, the
claim segmentation, the ranking and the lineage -- are the Kotlin tier's, and
this module asks for them rather than keeping a copy.
"""

from __future__ import annotations

from collections.abc import Sequence

from lmeditor.core.citations import extract_chunk_ids
from lmeditor.core.exceptions import NotebookAskError
from lmeditor.core.schemas import Claim, SourceChunk
from lmeditor.drafting.modules import SectionDrafter
from lmeditor.drafting.notebook import NotebookAnswerer
from lmeditor.notebooks import Notebook, NotebookAnswer, NotebookCitation
from lmeditor.provenance.service import ProvenanceIndex, build_provenance_index
from lmeditor.server.sources import source_library
from lmeditor.verification.claim_extractor import extract_claims

#: How many passages the answer is written from. It is the top of the
#: retrieval the backend already has.
TOP_K: int = 5

#: What the answer says when the notebook holds no source at all.
NO_SOURCES_ANSWER: str = (
    "This notebook holds no sources, so there is nothing in it to answer the question."
)

#: What the answer says when the notebook's own passages do not answer it.
UNANSWERED_ANSWER: str = (
    "The sources this notebook holds do not answer this question."
)


def ask_notebook(root: str, notebook: Notebook, question: str) -> NotebookAnswer:
    """Answer a question from the notebook's own sources.

    Args:
        root: The notebooks folder the notebook is in.
        notebook: The notebook the question is asked of.
        question: The operator's question.

    Returns:
        The answer, with the passages it leaned on. An answer that says the
        sources cannot answer the question is a successful ask: it is returned
        with ``answered`` false and no citations.

    Raises:
        NotebookAskError: The question is empty or a referenced source is missing.
    """
    text = (question or "").strip()
    if not text:
        raise NotebookAskError(
            "The question is empty.",
            reason="the question is empty",
            notebook_id=notebook.notebook_id,
            root=root,
        )

    # Resolve every source before retrieving or generating. A missing source
    # changes the notebook's evidence scope, so it cannot be silently skipped.
    # Use the records returned here rather than re-reading their chunks later:
    # each ask keeps the source snapshot it actually checked.
    chunks: list[SourceChunk] = []
    missing_source_ids: list[str] = []
    for source_id in notebook.source_ids:
        stored = source_library.get(source_id)
        if stored is None:
            missing_source_ids.append(source_id)
        else:
            chunks.extend(stored.chunks)
    if missing_source_ids:
        reason = "the notebook's sources are unavailable: " + ", ".join(missing_source_ids)
        raise NotebookAskError(
            "Cannot answer until the missing sources are restored or removed from the notebook. "
            "Missing source IDs: " + ", ".join(missing_source_ids) + ".",
            reason=reason,
            notebook_id=notebook.notebook_id,
            root=root,
            details={"missing_source_ids": missing_source_ids},
        )
    if not chunks:
        return NotebookAnswer(answer=NO_SOURCES_ANSWER, answered=False, citations=[])

    # Rule 2: the passages are ranked with the retrieval the backend has.
    index = build_provenance_index(chunks)
    passages = list(index.query(text, top_k=TOP_K))
    if not passages:
        return NotebookAnswer(answer=UNANSWERED_ANSWER, answered=False, citations=[])

    # Rule 3: the answer is written from the top of that ranking.
    answerer = NotebookAnswerer()
    answer_text, _reported = answerer.forward(
        question=text,
        assigned_chunks_text=SectionDrafter.format_assigned_chunks(passages),
    )
    answer_text = answer_text.strip()
    if not answer_text:
        return NotebookAnswer(answer=UNANSWERED_ANSWER, answered=False, citations=[])

    # Rule 4: a citation names a passage the answer used, and quotes it. A
    # chunk id that is not among the retrieved passages is dropped.
    used = _used_passages(answer_text, passages)

    # Rule 5: a citation is supported when the claims that carry it are
    # supported by that passage.
    claims = extract_claims(answer_text)
    citations = [
        _citation(chunk)
        for chunk in used
        if _is_supported(chunk, claims, index, answer_text)
    ]

    # Rule 6: the answer is answered when it says something and leans on a
    # passage the tier could support.
    answered = bool(answer_text) and bool(citations)
    return NotebookAnswer(
        answer=answer_text,
        answered=answered,
        citations=citations,
    )


def _used_passages(answer_text: str, passages: Sequence[SourceChunk]) -> list[SourceChunk]:
    """The retrieved passages the answer actually cited, in the order it did.

    The citation ids are read with the tier's own grammar, and a passage the
    retrieval did not return is dropped: a citation that points at a passage
    the answer did not use is a lie.
    """
    by_id = {chunk.chunk_id.lower(): chunk for chunk in passages}
    used: list[SourceChunk] = []
    seen: set[str] = set()
    for cited in extract_chunk_ids(answer_text):
        chunk = by_id.get(cited.lower())
        if chunk is None or chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        used.append(chunk)
    return used


def _is_supported(
    chunk: SourceChunk,
    claims: Sequence[Claim],
    index: ProvenanceIndex,
    answer_text: str,
) -> bool:
    """Whether the answer's own claims that carry this passage are supported by it.

    The rule -- what supports what, and how confidently -- is the tier's, and
    it is asked through the index. A claim the tier cannot support leaves the
    citation unsupported.
    """
    for claim in claims:
        if not claim.text.strip():
            continue
        if chunk.chunk_id.lower() not in {
            cited.lower() for cited in claim.cited_chunk_ids
        }:
            continue
        lineages = index.lineage_for_claim(
            claim.claim_id,
            claim.text,
            draft_text=answer_text,
        )
        for lineage in lineages:
            if lineage.chunk_id.lower() != chunk.chunk_id.lower():
                continue
            if lineage.exact_quote and lineage.confidence_score > 0:
                return True
    return False


def _citation(chunk: SourceChunk) -> NotebookCitation:
    """The citation a supported passage earns.

    The quote is the passage's own words, so the operator can read the
    evidence rather than trust it.
    """
    return NotebookCitation(
        chunk_id=chunk.chunk_id,
        source_id=chunk.document_id,
        title=chunk.document_title,
        quote=chunk.text,
    )


__all__ = [
    "NO_SOURCES_ANSWER",
    "TOP_K",
    "UNANSWERED_ANSWER",
    "ask_notebook",
]
