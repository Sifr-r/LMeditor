"""Regression tests for the ingestion trust boundary (AUDIT.md findings 1-3).

Covered defects:

* Finding 1 - ``persist_source_to_disk`` allowed ``source_id`` / ``file.filename``
  to escape the upload directory.
* Finding 2 (loader side) - local-file loaders leaked ``OSError`` subclasses
  instead of the domain error ``SourceLoadError``.
* Finding 3 - the URL loader fetched internal/private hosts (SSRF), because
  nothing validated the URL, not even the original one.

The rules themselves live in the Kotlin tier now, and the tier's own tests
prove them there
(``kotlin/ingest/src/test/kotlin/lmeditor/ingest/UrlPolicyTest.kt`` and
``HttpSafetyTest.kt``). A Python test can no longer stub the tier's name
resolution or its transport: both happen in the JVM.

What this file therefore covers is the *door*: the upload path stays inside
its root, the loaders report a failure as the domain error their callers
catch, and the SSRF guard refuses the internal targets it is asked about.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import (
    HtmlSourceLoader,
    MarkdownSourceLoader,
    TextSourceLoader,
)
from lmeditor.ingestion.url_policy import (
    UnsafeSourceError,
    assert_public_url,
    iter_redirect_hosts,
    safe_upload_path,
    validate_source_id,
)
from lmeditor.server.routes_helpers import persist_source_to_disk

# ============================================================================
# Fakes / helpers
# ============================================================================


class _StubUploadFile:
    """Minimal stand-in for ``fastapi.UploadFile`` (only what the helper uses)."""

    def __init__(self, filename: str | None, content: bytes = b"payload") -> None:
        self.filename = filename
        self._content = content
        self._position = 0

    async def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes, as ``fastapi.UploadFile.read`` does.

        The read position advances, so repeated reads walk the content exactly
        as a real upload would.
        """
        if size is None or size < 0:
            chunk = self._content[self._position :]
            self._position = len(self._content)
            return chunk
        chunk = self._content[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


# ============================================================================
# Finding 1 - upload path containment
# ============================================================================


@pytest.mark.parametrize(
    "source_id",
    [
        "../../evil",
        "..",
        ".",
        "src/../../evil",
        "src\\evil",
        "..\\..\\evil",
        "",
        "   ",
        "-leading-dash",
        "has:colon",
        "has*star",
        "nul\x00byte",
        "z" * 129,
    ],
)
def test_validate_source_id_rejects_unsafe_ids(source_id: str) -> None:
    """Traversal, separators, leading dash, NUL and over-long ids are rejected."""
    with pytest.raises(UnsafeSourceError):
        validate_source_id(source_id)


@pytest.mark.parametrize("source_id", ["src_url_api", "a.b.c", "paper-2024", "x" * 128])
def test_validate_source_id_accepts_safe_ids_unchanged(source_id: str) -> None:
    """Safe ids are returned unchanged (the id becomes the on-disk directory)."""
    assert validate_source_id(source_id) == source_id


def test_safe_upload_path_rejects_traversal_source_id(tmp_path: Path) -> None:
    """``source_id='../../evil'`` must not produce a path above ``base``."""
    with pytest.raises(UnsafeSourceError):
        safe_upload_path("../../evil", "notes.txt", base=tmp_path)
    assert not (tmp_path.parent / "evil").exists()


@pytest.mark.parametrize(
    "filename",
    [r"C:\Windows\x.txt", r"C:/Windows/evil.txt", "/etc/passwd", "\\\\server\\share\\f.txt"],
)
def test_safe_upload_path_neutralizes_absolute_filename(
    filename: str, tmp_path: Path
) -> None:
    """An absolute client filename is reduced to a basename inside the root."""
    result = safe_upload_path("src_ok", filename, base=tmp_path)

    assert result == (tmp_path.resolve() / "src_ok" / Path(filename.replace("\\", "/")).name)
    assert result.is_relative_to(tmp_path.resolve())
    assert result.parent == (tmp_path.resolve() / "src_ok")


@pytest.mark.parametrize("filename", ["..", ".", "", "   ", "...", "\x00name.txt"])
def test_safe_upload_path_rejects_dot_only_filenames(filename: str, tmp_path: Path) -> None:
    """A filename that has no usable basename is rejected."""
    with pytest.raises(UnsafeSourceError):
        safe_upload_path("src_ok", filename, base=tmp_path)


def test_safe_upload_path_keeps_traversal_filename_inside_root(tmp_path: Path) -> None:
    """A ``../`` filename segment is stripped; the result stays under the root."""
    result = safe_upload_path("src_ok", "../../../etc/shadow.txt", base=tmp_path)

    assert result == (tmp_path.resolve() / "src_ok" / "shadow.txt")
    assert result.is_relative_to(tmp_path.resolve())


def test_safe_upload_path_truncates_long_filename(tmp_path: Path) -> None:
    """Over-long filenames are truncated to the 255-char cap."""
    result = safe_upload_path("src_ok", "a" * 400 + ".txt", base=tmp_path)

    assert len(result.name) == 255
    assert result.is_relative_to(tmp_path.resolve())


def test_persist_source_to_disk_rejects_traversal_source_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a traversing ``source_id`` writes nothing outside the root."""
    root = tmp_path / "uploads"
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(root))

    with pytest.raises(UnsafeSourceError):
        asyncio.run(persist_source_to_disk(_StubUploadFile("notes.txt"), "../../evil"))

    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "evil" / "notes.txt").exists()
    assert not root.exists() or not any(root.rglob("notes.txt"))


def test_persist_source_to_disk_rejects_absolute_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: an absolute filename is neutralized, never honoured."""
    root = tmp_path / "uploads"
    monkeypatch.setenv("LMEDITOR_UPLOAD_DIR", str(root))

    saved = asyncio.run(
        persist_source_to_disk(_StubUploadFile(r"C:\Windows\x.txt"), "src_ok")
    )

    assert saved.path == (root.resolve() / "src_ok" / "x.txt")
    assert saved.path.read_bytes() == b"payload"
    assert not Path(r"C:\Windows\x.txt").exists()


# ============================================================================
# Finding 2 (loader side) - domain error instead of raw OS errors
# ============================================================================


@pytest.mark.parametrize(
    "loader_factory",
    [TextSourceLoader, MarkdownSourceLoader, HtmlSourceLoader],
)
def test_loaders_report_directories_as_source_load_error(
    loader_factory: Any, tmp_path: Path
) -> None:
    """A directory origin raises ``SourceLoadError``, not ``IsADirectoryError``."""
    directory = tmp_path / "a_directory"
    directory.mkdir()

    with pytest.raises(SourceLoadError):
        loader_factory().load(directory)


def test_the_loader_reports_a_missing_file_as_source_load_error(tmp_path: Path) -> None:
    """A file that is not there raises ``SourceLoadError``, not ``FileNotFoundError``.

    The read happens in the tier, so the failure reaches the caller as the
    domain error the loaders' callers already catch.
    """
    with pytest.raises(SourceLoadError, match="Failed to load text source"):
        TextSourceLoader().load(tmp_path / "not_there.txt")


# ============================================================================
# Finding 3 - SSRF guard
# ============================================================================


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/internal",
        "http://localhost/internal",
        "https://localhost:9000/internal",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/internal",
        "http://0.0.0.0/",
        "http://[fd00::1]/",
        "http://224.0.0.1/",
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/_stats",
        "ftp://example.org/x",
        "http:///no-host",
    ],
)
def test_assert_public_url_rejects_internal_targets(url: str) -> None:
    """Loopback / private / link-local / reserved / non-http targets are rejected."""
    with pytest.raises(UnsafeSourceError):
        assert_public_url(url)


def test_assert_public_url_allows_a_public_address() -> None:
    """A public address is allowed, unchanged.

    A literal address is vetted without a lookup, so the assertion does not
    depend on a resolver being available.
    """
    assert assert_public_url("https://93.184.216.34/paper") == "https://93.184.216.34/paper"


def test_iter_redirect_hosts_reports_target_hosts() -> None:
    """The hosts a response declares as redirect targets are listed."""
    response = httpx.Response(
        302,
        headers=[
            ("location", "/one"),
            ("location", "https://other.example/two"),
            ("location", "/one"),
        ],
        request=httpx.Request("GET", "https://public.example/start"),
    )
    assert iter_redirect_hosts(response) == ["public.example", "other.example"]
