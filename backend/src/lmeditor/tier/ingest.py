"""The ingestion rules, as the Kotlin tier implements them.

Loading a source, the bibliography its resolvers can find, the policy that
keeps a fetched URL on a public address, the bounded reads that keep one
response from filling the disk, and the store the uploads are persisted in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lmeditor.bridge.client import decode_bytes, encode_bytes
from lmeditor.core.exceptions import BibliographyResolutionError
from lmeditor.core.schemas import BibliographicRecord, SourceChunk, SourceIngestResult
from lmeditor.core.types import BibliographyResolverKind, SourceKind
from lmeditor.tier.bridge import tier_bridge


def load_source(
    kind: SourceKind,
    origin: str | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    """Read a source artifact into text, its metadata and what it lost."""
    params: dict[str, Any] = {"kind": kind.value}
    if origin is not None:
        params["origin"] = origin
    if data is not None:
        params["bytes_b64"] = encode_bytes(data)
    return tier_bridge().call("ingest.load", params)


def ingest_source(
    kind: SourceKind,
    origin: str | None = None,
    data: bytes | None = None,
    source_id: str | None = None,
    title: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    pmid: str | None = None,
    pdf_metadata: Mapping[str, Any] | None = None,
    byte_size: int | None = None,
) -> SourceIngestResult:
    """Ingest a source artifact and the chunks it was cut into."""
    params: dict[str, Any] = {"kind": kind.value}
    if origin is not None:
        params["origin"] = origin
    if data is not None:
        params["bytes_b64"] = encode_bytes(data)
    if source_id is not None:
        params["id"] = source_id
    if title is not None:
        params["title"] = title
    if doi is not None:
        params["doi"] = doi
    if arxiv_id is not None:
        params["arxiv_id"] = arxiv_id
    if pmid is not None:
        params["pmid"] = pmid
    if pdf_metadata is not None:
        params["pdf_metadata"] = {
            str(key): value for key, value in pdf_metadata.items() if isinstance(value, str)
        }
    if byte_size is not None:
        params["byte_size"] = byte_size
    result = tier_bridge().call("ingest.ingest", params)
    return SourceIngestResult.model_validate(result)


def resolve_bibliography(
    resolver: BibliographyResolverKind | str,
    *,
    source_id: str,
    source_kind: SourceKind | str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    pmid: str | None = None,
    pdf_metadata: Mapping[str, Any] | None = None,
    record: Mapping[str, Any] | None = None,
    source_ids: Sequence[str] | None = None,
) -> BibliographicRecord | None:
    """Resolve the bibliographic record of one source with one strategy.

    The strategy is named by its wire kind, so the caller asks for exactly the
    resolver it holds. ``None`` means "this strategy cannot resolve the
    source"; a failure is raised as the domain error the callers already
    catch.

    Raises:
        BibliographyResolutionError: the strategy could not be carried out.
    """
    params: dict[str, Any] = {
        "resolver": _resolver_wire(resolver),
        "source_id": source_id,
        "source_kind": _source_kind_wire(source_kind),
    }
    if doi is not None:
        params["doi"] = doi
    if arxiv_id is not None:
        params["arxiv_id"] = arxiv_id
    if pmid is not None:
        params["pmid"] = pmid
    if pdf_metadata is not None:
        params["pdf_metadata"] = {
            str(key): value for key, value in pdf_metadata.items() if isinstance(value, str)
        }
    if record is not None:
        params["record"] = dict(record)
    if source_ids is not None:
        params["source_ids"] = list(source_ids)
    result = tier_bridge().call("ingest.bibliography.resolve", params)
    error = result.get("error")
    if isinstance(error, Mapping):
        raise BibliographyResolutionError(
            resolver=str(error.get("resolver", "")),
            origin=str(error.get("origin", "")),
            reason=str(error.get("reason", "")),
        )
    wire = result.get("record")
    if not isinstance(wire, Mapping):
        return None
    return BibliographicRecord.model_validate(wire)


def resolve_bibliography_chain(
    resolvers: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    source_kind: SourceKind | str,
    doi: str | None = None,
    arxiv_id: str | None = None,
    pmid: str | None = None,
    pdf_metadata: Mapping[str, Any] | None = None,
) -> BibliographicRecord | None:
    """Resolve the bibliographic record of one source with a whole chain.

    The chain is run by the tier, so the *order* the strategies are tried in
    -- identifier-bearing resolvers before the PDF-header one -- is the
    tier's rule and not the caller's.
    """
    params: dict[str, Any] = {
        "resolvers": [dict(spec) for spec in resolvers],
        "source_id": source_id,
        "source_kind": _source_kind_wire(source_kind),
    }
    if doi is not None:
        params["doi"] = doi
    if arxiv_id is not None:
        params["arxiv_id"] = arxiv_id
    if pmid is not None:
        params["pmid"] = pmid
    if pdf_metadata is not None:
        params["pdf_metadata"] = {
            str(key): value for key, value in pdf_metadata.items() if isinstance(value, str)
        }
    result = tier_bridge().call("ingest.bibliography.resolve_chain", params)
    wire = result.get("record")
    if not isinstance(wire, Mapping):
        return None
    return BibliographicRecord.model_validate(wire)


def source_store_save(
    root: str,
    source_id: str,
    filename: str,
    content: bytes,
    max_bytes: int,
) -> str:
    """Persist an upload under the store rooted at ``root`` and return its path."""
    result = tier_bridge().call(
        "ingest.source_store.save",
        {
            "root": root,
            "source_id": source_id,
            "filename": filename,
            "bytes_b64": encode_bytes(content),
            "max_bytes": max_bytes,
        },
    )
    return str(result.get("path", ""))


def source_store_list(root: str) -> tuple[list[str], int]:
    """Every file the store holds, oldest first, and the bytes they take."""
    result = tier_bridge().call("ingest.source_store.list", {"root": root})
    files = [str(item) for item in result.get("files") or []]
    return files, int(result.get("total_bytes", 0))


def source_store_evict(root: str, max_bytes: int) -> list[str]:
    """Delete the oldest files until the store fits its budget."""
    result = tier_bridge().call(
        "ingest.source_store.evict",
        {"root": root, "max_bytes": max_bytes},
    )
    return [str(item) for item in result.get("removed") or []]


def source_store_touch(root: str, source_id: str, filename: str) -> str | None:
    """Mark a stored file as recently used, or answer ``None`` when it is gone."""
    result = tier_bridge().call(
        "ingest.source_store.touch",
        {"root": root, "source_id": source_id, "filename": filename},
    )
    path = result.get("path")
    return None if path is None else str(path)


def source_store_remove(root: str, source_id: str) -> bool:
    """Remove every file stored for a source; answer whether anything went."""
    result = tier_bridge().call(
        "ingest.source_store.remove",
        {"root": root, "source_id": source_id},
    )
    return bool(result.get("removed", False))


def _resolver_wire(resolver: BibliographyResolverKind | str) -> str:
    """The wire name of a resolver strategy."""
    if isinstance(resolver, BibliographyResolverKind):
        return resolver.value
    return str(resolver)


def _source_kind_wire(source_kind: SourceKind | str) -> str:
    """The wire name of a source kind."""
    if isinstance(source_kind, SourceKind):
        return source_kind.value
    return str(source_kind)


def assert_public_url(url: str) -> None:
    """Refuse a URL that resolves to a private address.

    Raises the tier's SSRF error when the address is not public.
    """
    tier_bridge().call("ingest.url_policy.assert_public", {"url": url})


def fetch_limited(url: str, max_bytes: int) -> bytes:
    """Fetch a URL under a byte cap, re-validating every redirect hop.

    The body comes back as the bytes themselves, which is what the Python
    tier's own fetch returns, so a caller can be moved onto the tier without
    its callers noticing.
    """
    result = tier_bridge().call(
        "ingest.fetch_limited",
        {"url": url, "max_bytes": max_bytes},
    )
    return decode_bytes(str(result.get("body_b64", "")))


def resolve_public_addresses(url: str) -> list[tuple[str, int]]:
    """The public addresses a URL resolves to, and the port each may use.

    The resolver is the JDK's, so the answer is the same wherever the tier
    runs.
    """
    result = tier_bridge().call(
        "ingest.url_policy.resolve_public_addresses",
        {"url": url},
    )
    return [
        (str(item.get("ip", "")), int(item.get("port", 0)))
        for item in result.get("addresses") or []
    ]


def validate_source_id(source_id: str) -> str:
    """Return ``source_id`` unchanged, or refuse it as an unsafe path segment."""
    result = tier_bridge().call(
        "ingest.url_policy.validate_source_id",
        {"source_id": source_id},
    )
    return str(result.get("source_id", source_id))


def safe_upload_path(
    source_id: str,
    filename: str,
    base: str | None = None,
) -> str:
    """A proven-contained absolute path for an uploaded source file."""
    params: dict[str, Any] = {"source_id": source_id, "filename": filename}
    if base is not None:
        params["base"] = base
    result = tier_bridge().call("ingest.url_policy.safe_upload_path", params)
    return str(result.get("path", ""))


def upload_root(base: str | None = None) -> str:
    """The directory that holds persisted uploads.

    ``base`` is the *configured* root the caller resolved for itself. It is
    sent with the request because the tier runs in its own process, and that
    process's environment is not necessarily the caller's: without the
    forwarded value the tier would answer with the root *its* environment
    names, and the two tiers would disagree about where an upload goes.
    """
    params: dict[str, Any] = {}
    if base is not None:
        params["base"] = base
    result = tier_bridge().call("ingest.url_policy.upload_root", params)
    return str(result.get("path", ""))


def validate_upload_size(size: int) -> int:
    """Return ``size`` when it is an acceptable upload size, else raise.

    The caps on every byte that crosses the ingestion boundary are the tier's,
    and so is the rule that refuses an oversized upload *before* it is
    buffered: the server reads the whole file into memory to persist it, so
    the only moment a cheap refusal is possible is before that read.
    """
    result = tier_bridge().call("ingest.limits", {"size": size})
    return int(result.get("size", size))


def limits() -> dict[str, int]:
    """The caps on every byte that crosses the ingestion boundary."""
    result = tier_bridge().call("ingest.limits", {"size": 0})
    return {
        "max_upload_bytes": int(result.get("max_upload_bytes", 0)),
        "max_url_response_bytes": int(result.get("max_url_response_bytes", 0)),
        "max_resolver_response_bytes": int(result.get("max_resolver_response_bytes", 0)),
        "max_source_store_bytes": int(result.get("max_source_store_bytes", 0)),
    }


__all__ = [
    "load_source",
    "ingest_source",
    "assert_public_url",
    "fetch_limited",
    "resolve_bibliography",
    "resolve_bibliography_chain",
    "source_store_save",
    "source_store_list",
    "source_store_evict",
    "source_store_touch",
    "source_store_remove",
    "validate_upload_size",
    "limits",
    "resolve_public_addresses",
    "validate_source_id",
    "safe_upload_path",
    "upload_root",
    "SourceChunk",
]
