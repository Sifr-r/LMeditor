"""The PDF rules, as the Kotlin tier implements them.

The extraction is native -- the JDK's inflater and a hand-written PDF parser
-- so the tier keeps its empty runtime dependency set.
"""

from __future__ import annotations

from typing import Any

from lmeditor.bridge.client import encode_bytes
from lmeditor.tier.bridge import tier_bridge


def extract_text(path: str | None = None, data: bytes | None = None) -> tuple[str, int]:
    """The text of a PDF and its page count."""
    params: dict[str, Any] = {}
    if path is not None:
        params["path"] = path
    if data is not None:
        params["bytes_b64"] = encode_bytes(data)
    result = tier_bridge().call("pdf.text", params)
    return str(result.get("text", "")), int(result.get("pages", 0))


def extract_metadata(path: str | None = None, data: bytes | None = None) -> dict[str, Any]:
    """The bibliographic metadata a PDF carries about itself."""
    params: dict[str, Any] = {}
    if path is not None:
        params["path"] = path
    if data is not None:
        params["bytes_b64"] = encode_bytes(data)
    return tier_bridge().call("pdf.metadata", params)


__all__ = ["extract_text", "extract_metadata"]
