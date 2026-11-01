"""The ingestion doors must reach the Kotlin tier, and must not fake it.

``lmeditor.ingestion.limits``, ``loaders``, ``registry``, ``pdf_metadata``,
``url_policy`` and ``http_safety`` are *doors*: they keep the Python API the
backend calls and answer with the Kotlin tier's answers. These tests prove that
is what actually happens -- that the answers come from the tier, and that a
tier that is not there fails loudly instead of quietly answering from a second,
Python copy of the rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import ValidationError
from lmeditor.core.types import SourceKind
from lmeditor.ingestion import limits, loaders, pdf_metadata, registry, url_policy
from lmeditor.tier import ingest as tier_ingest
from lmeditor.tier import pdf as tier_pdf

pytestmark = pytest.mark.usefixtures("kotlin_tier")


# ---------------------------------------------------------------------------
# The doors call the tier
# ---------------------------------------------------------------------------


def test_the_loader_door_asks_the_tier(monkeypatch: Any, tmp_path: Path) -> None:
    """A source is read by the tier, and the answer is rebuilt in Python."""
    calls: list[dict[str, Any]] = []
    original = tier_ingest.load_source

    def _spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, **kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(tier_ingest, "load_source", _spy)

    target = tmp_path / "note.txt"
    target.write_text("hello world\n", encoding="utf-8")

    text, page_count = loaders.TextSourceLoader().load(target)

    assert len(calls) == 1
    assert calls[0]["args"][0] == SourceKind.TEXT
    assert "hello world" in text
    assert page_count is None


def test_the_registry_door_asks_the_tier(monkeypatch: Any, tmp_path: Path) -> None:
    """The registry dispatches to a loader that asks the tier."""
    calls: list[dict[str, Any]] = []
    original = tier_ingest.load_source

    def _spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, **kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(tier_ingest, "load_source", _spy)

    target = tmp_path / "note.txt"
    target.write_text("hello world\n", encoding="utf-8")

    text, _page_count = registry.SourceLoaderRegistry.default().load(
        SourceKind.TEXT, target
    )

    assert len(calls) == 1
    assert "hello world" in text


def test_the_limits_door_asks_the_tier(monkeypatch: Any) -> None:
    """The size rule is the tier's; only the type check stays in Python."""
    calls: list[int] = []
    original = tier_ingest.validate_upload_size

    def _spy(size: int) -> int:
        calls.append(size)
        return original(size)

    monkeypatch.setattr(tier_ingest, "validate_upload_size", _spy)

    assert limits.validate_upload_size(10) == 10
    assert calls == [10]

    # The tier owns the sign and the cap.
    with pytest.raises(ValidationError):
        limits.validate_upload_size(-1)

    # The type check cannot cross the seam: the tier takes an ``Int``.
    with pytest.raises(ValidationError):
        limits.validate_upload_size("not a size")


def test_the_url_policy_door_asks_the_tier(monkeypatch: Any) -> None:
    """The path rules are the tier's, and the Python API keeps its shape."""
    calls: list[dict[str, Any]] = []
    original = tier_ingest.validate_source_id

    def _spy(source_id: str) -> str:
        calls.append({"source_id": source_id})
        return original(source_id)

    monkeypatch.setattr(tier_ingest, "validate_source_id", _spy)

    assert url_policy.validate_source_id("src_ok") == "src_ok"
    assert calls == [{"source_id": "src_ok"}]

    # The upload root is a ``Path`` on this side and a string on the wire.
    assert isinstance(url_policy.upload_root(), Path)
    assert isinstance(url_policy.safe_upload_path("src_ok", "a.txt"), Path)


def test_the_pdf_metadata_door_asks_the_tier(monkeypatch: Any) -> None:
    """The document metadata is the tier's, mapped onto the promised keys."""
    fixture = Path(__file__).parent / "fixtures" / "minimal.pdf"
    if not fixture.exists():  # pragma: no cover - the fixture is generated
        pytest.skip("tests/fixtures/minimal.pdf not generated")

    calls: list[dict[str, Any]] = []
    original = tier_pdf.extract_metadata

    def _spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(tier_pdf, "extract_metadata", _spy)

    metadata = pdf_metadata.extract_pdf_metadata(fixture)

    assert len(calls) == 1
    assert calls[0]["path"] == str(fixture)
    assert set(metadata) <= {
        "title",
        "author",
        "subject",
        "keywords",
        "creator",
        "producer",
        "creation_date",
        "doi",
        "arxiv_id",
    }


# ---------------------------------------------------------------------------
# A tier that is not there fails loudly
# ---------------------------------------------------------------------------


def test_an_unreachable_tier_is_not_swallowed_by_the_ingestion_doors(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """An unreachable tier is an infrastructure failure, not a bad input."""

    def _unavailable(*args: Any, **kwargs: Any) -> Any:
        raise BridgeUnavailableError("the tier is not there")

    monkeypatch.setattr(tier_ingest, "load_source", _unavailable)
    monkeypatch.setattr(tier_ingest, "validate_upload_size", _unavailable)
    monkeypatch.setattr(tier_ingest, "validate_source_id", _unavailable)
    monkeypatch.setattr(tier_pdf, "extract_metadata", _unavailable)

    target = tmp_path / "note.txt"
    target.write_text("hello world\n", encoding="utf-8")

    with pytest.raises(BridgeUnavailableError):
        loaders.TextSourceLoader().load(target)

    with pytest.raises(BridgeUnavailableError):
        registry.SourceLoaderRegistry.default().load(SourceKind.TEXT, target)

    with pytest.raises(BridgeUnavailableError):
        limits.validate_upload_size(10)

    with pytest.raises(BridgeUnavailableError):
        url_policy.validate_source_id("src_ok")

    with pytest.raises(BridgeUnavailableError):
        pdf_metadata.extract_pdf_metadata(target)
