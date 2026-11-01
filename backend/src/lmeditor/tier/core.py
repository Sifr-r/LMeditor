"""The core rules, as the Kotlin tier implements them.

Each function here is a thin call across the NDJSON seam and a validation of
the answer into the Python tier's own models. The rule itself lives in
``lmeditor.core`` on the Kotlin side; this module is the door, not the room.
"""

from __future__ import annotations

from typing import Any

from lmeditor.core.schemas import SectionDraft, SourceChunk
from lmeditor.tier.bridge import tier_bridge


def extract_chunk_ids(text: str) -> list[str]:
    """The ordered, de-duplicated chunk ids cited in ``text``."""
    result = tier_bridge().call("core.citations.extract_chunk_ids", {"text": text})
    return [str(item) for item in result.get("chunk_ids", [])]


def iter_citation_spans(text: str) -> list[tuple[str, int, int]]:
    """The ``(chunk_id, start, end)`` triple for every citation marker in ``text``."""
    result = tier_bridge().call("core.citations.iter_spans", {"text": text})
    spans = result.get("spans") or []
    return [(str(span["chunk_id"]), int(span["start"]), int(span["end"])) for span in spans]


def strip_citations(text: str) -> str:
    """``text`` with every citation marker removed."""
    result = tier_bridge().call("core.citations.strip", {"text": text})
    return str(result.get("text", ""))


def merge_operator_text(
    draft: SectionDraft,
    *,
    content: str,
    title: str | None = None,
    order: int | None = None,
) -> SectionDraft:
    """The section as the operator left it, with its provenance kept.

    The rule lives in ``lmeditor.core.sections`` on the Kotlin side; this call
    keeps the review gate and the save route applying exactly the same one.
    """
    params: dict[str, Any] = {
        "draft": draft.model_dump(mode="json"),
        "content": content,
    }
    if title is not None:
        params["title"] = title
    if order is not None:
        params["order"] = order
    result = tier_bridge().call("core.sections.merge_operator_text", params)
    return SectionDraft.model_validate(result["draft"])


def create_source_chunk(
    document_id: str,
    text: str,
    char_start: int,
    char_end: int,
    byte_start: int,
    byte_end: int,
    metadata: dict[str, Any] | None = None,
) -> SourceChunk:
    """A chunk with the deterministic SHA-256 id the Kotlin tier mints.

    The digest is computed on the Kotlin side so that both tiers agree on it
    byte for byte; this call only names the payload.
    """
    params: dict[str, Any] = {
        "document_id": document_id,
        "text": text,
        "char_start": char_start,
        "char_end": char_end,
        "byte_start": byte_start,
        "byte_end": byte_end,
    }
    if metadata:
        params["metadata"] = metadata
    result = tier_bridge().call("core.schemas.source_chunk_create", params)
    return SourceChunk.model_validate(result["chunk"])


__all__ = [
    "extract_chunk_ids",
    "iter_citation_spans",
    "strip_citations",
    "merge_operator_text",
    "create_source_chunk",
]
