"""Citation lineage, as the Kotlin tier implements it.

Attribution is a rule -- whether a span of a draft lies inside the draft,
whether the quoted passage is really in the source chunk, and how well the two
align -- and the rule is Kotlin's: it is implemented once, in
``lmeditor.text.LineageEngine``, and the tier serves it across the NDJSON
seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.lineage``, frozen, so the parity
harness can still ask both sides the same question.

What stays in Python, and why:

* **The chunk lookup.** The seam carries data, not a live index, so the
  engine still resolves the cited chunk itself -- from the ``chunk_store`` it
  is handed, or through the ``indexer`` when one is supplied -- and hands the
  resolved chunk to the tier. Every *rule* about that chunk (the boundary
  check, the grounding check, the confidence score) is the tier's.
* **The marker grammar.** ``CITATION_MARKER_PATTERN`` is the canonical
  grammar from :mod:`lmeditor.core.citations`; a compiled pattern is an
  object rather than a rule.
"""

from __future__ import annotations

from collections.abc import Mapping

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.citations import CITATION_PATTERN as CANONICAL_CITATION_PATTERN
from lmeditor.core.exceptions import (
    ChunkNotFoundError,
    LMeditorError,
    ValidationError,
)
from lmeditor.core.schemas import CitationLineage, SourceChunk
from lmeditor.core.types import HybridIndexerProtocol
from lmeditor.tier import text as tier_text


def _require_text(text: str, field: str = "draft_text") -> None:
    """Refuse anything that is not text."""
    if not isinstance(text, str):
        raise ValidationError(
            f"Expected str input for {field}, got {type(text).__name__}",
            field=field,
            value=text,
        )


def _require_span(value: int, field: str) -> None:
    """Refuse a span bound that is not an integer.

    The *value* of the bound is the tier's business: a span that does not lie
    inside the draft is a ``SpanAlignmentError``, which the tier raises.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"Expected int input for {field}, got {type(value).__name__}",
            field=field,
            value=value,
        )


def _require_id(value: str, field: str) -> None:
    """Refuse a chunk or claim id that is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"Expected a non-empty str input for {field}, got {value!r}",
            field=field,
            value=value,
        )


def _require_chunk_store(chunk_store: Mapping[str, SourceChunk] | None) -> None:
    """Refuse a chunk store that is not a mapping of chunks."""
    if chunk_store is None:
        return
    if not isinstance(chunk_store, Mapping):
        raise ValidationError(
            f"Expected a mapping of chunk id to SourceChunk, got {type(chunk_store).__name__}",
            field="chunk_store",
            value=chunk_store,
        )
    for key, chunk in chunk_store.items():
        if not isinstance(key, str):
            raise ValidationError(
                f"Expected a str chunk id in the chunk store, got {type(key).__name__}",
                field="chunk_store",
                value=key,
            )
        if not isinstance(chunk, SourceChunk):
            raise ValidationError(
                f"Expected a SourceChunk in the chunk store, got {type(chunk).__name__}",
                field="chunk_store",
                value=chunk,
            )


def _require_indexer(indexer: HybridIndexerProtocol | None) -> None:
    """Refuse an indexer that cannot look a chunk up."""
    if indexer is None:
        return
    if not hasattr(indexer, "get_chunk"):
        raise ValidationError(
            f"Expected an indexer with a get_chunk method, got {type(indexer).__name__}",
            field="indexer",
            value=indexer,
        )


class CitationLineageEngine:
    """Engine for attributing draft text spans to ground truth source chunks.

    Every method here is the tier's rule; this class exists so that the
    callers keep the API they had.
    """

    # The marker grammar is owned by ``lmeditor.core.citations``; this alias
    # keeps the historical class attribute without a second, divergent regex.
    # The canonical grammar accepts a marker group (``[#a, #b]``) and a bare
    # ``[id]``, and its first group is the whole bracketed body rather than a
    # single chunk id: use ``iter_citation_spans`` to read the chunk ids out
    # of a draft.
    CITATION_MARKER_PATTERN = CANONICAL_CITATION_PATTERN

    def _resolve_chunk(
        self,
        cited_chunk_id: str,
        chunk_store: Mapping[str, SourceChunk] | None,
        indexer: HybridIndexerProtocol | None,
    ) -> SourceChunk | None:
        """Find the cited chunk in the store, or through the index.

        The lookup stays here because the seam carries data, not a live index:
        the index lives in the Python tier, and the tier is told which chunk
        the draft span is about.
        """
        if chunk_store is not None and cited_chunk_id in chunk_store:
            return chunk_store[cited_chunk_id]
        if indexer is None:
            return None
        try:
            found: SourceChunk | None = indexer.get_chunk(cited_chunk_id)
        except ChunkNotFoundError:
            raise
        except LMeditorError:
            raise
        except Exception:
            # An index that cannot answer is an index that has no such chunk;
            # the caller is told the chunk was not found, as it always was.
            return None
        return found

    def attribute_span(
        self,
        draft_text: str,
        span_start: int,
        span_end: int,
        cited_chunk_id: str,
        claim_id: str,
        chunk_store: dict[str, SourceChunk] | None = None,
        indexer: HybridIndexerProtocol | None = None,
        exact_quote: str | None = None,
    ) -> CitationLineage:
        """Attribute a span in draft text to an indexed SourceChunk.

        The rules are the tier's: the boundary check, the grounding check and
        the confidence score all live in ``lmeditor.text.LineageEngine``. What
        happens here is the lookup of the cited chunk -- from the
        ``chunk_store``, or through the ``indexer`` when one is supplied --
        and the hand-off of that chunk to the tier.

        Args:
            draft_text: The draft the span is a span of.
            span_start: Inclusive start of the span, in code points.
            span_end: Exclusive end of the span, in code points.
            cited_chunk_id: The id of the chunk the span is attributed to.
            claim_id: The id of the claim the span belongs to.
            chunk_store: The chunks the citation may name, by chunk id.
            indexer: The index to ask when the store does not hold the chunk.
            exact_quote: The quoted passage, when the citation carries one.

        Returns:
            The lineage record for the attributed span.

        Raises:
            ValidationError: An argument was not of the type this method
                accepts.
            SpanAlignmentError: The span does not lie inside the draft.
            ChunkNotFoundError: The cited chunk could not be resolved.
            CitationHallucinationError: The quoted passage is not in the
                source chunk.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is
                never swallowed.
        """
        _require_text(draft_text)
        _require_span(span_start, "span_start")
        _require_span(span_end, "span_end")
        _require_id(cited_chunk_id, "cited_chunk_id")
        _require_id(claim_id, "claim_id")
        _require_chunk_store(chunk_store)
        _require_indexer(indexer)
        if exact_quote is not None:
            _require_text(exact_quote, "exact_quote")

        chunk = self._resolve_chunk(cited_chunk_id, chunk_store, indexer)
        chunks = [chunk] if chunk is not None else []
        try:
            return tier_text.attribute_span(
                draft_text=draft_text,
                span_start=span_start,
                span_end=span_end,
                cited_chunk_id=cited_chunk_id,
                claim_id=claim_id,
                chunks=chunks,
                exact_quote=exact_quote,
            )
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except ChunkNotFoundError:
            # The tier could not find the chunk either: the store did not
            # hold it and the index did not have it. The error names the id
            # the caller asked about, exactly as it always did.
            raise ChunkNotFoundError(cited_chunk_id) from None
        except LMeditorError:
            raise

    def extract_citations_from_draft(
        self,
        draft_text: str,
        chunk_store: dict[str, SourceChunk],
    ) -> list[CitationLineage]:
        """Extract inline citation tags from draft text and construct CitationLineage records.

        The markers are found with the canonical grammar from
        :mod:`lmeditor.core.citations` -- the same grammar the drafting side
        writes, the verifier reads and the manuscript bridge consumes -- so the
        lineage records and the footnotes of an exported document can never
        disagree about which chunks a section cites. That grammar is the
        tier's, and so is the binding of a marker to the chunk it names.

        Args:
            draft_text: The draft to read the markers out of.
            chunk_store: The chunks the markers may name, by chunk id.

        Returns:
            One lineage record per cited chunk, in order of appearance.

        Raises:
            ValidationError: An argument was not of the type this method
                accepts.
            BridgeUnavailableError: The Kotlin tier could not be reached. That
                is an infrastructure failure, not a bad input, and it is
                never swallowed.
        """
        _require_text(draft_text)
        _require_chunk_store(chunk_store)
        try:
            return tier_text.extract_citations_from_draft(
                draft_text=draft_text,
                chunks=list(chunk_store.values()) if chunk_store else [],
            )
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except LMeditorError:
            raise


__all__ = ["CitationLineageEngine"]
