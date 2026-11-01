"""The frozen trust-boundary guards for source ingestion.

This is the Python implementation of ``ingestion.url_policy``, kept here so that
the parity harness has a witness to compare the Kotlin tier against. It is
plain Python on purpose: see ``lmeditor._reference``.

The harness measures the Kotlin tier against *this* rule, so the two have to
answer the same questions the same way: a witness that is looser than the
tier is not an oracle for it. The rule was corrected once, and both sides
carry the correction -- the tier now refuses a source id or a file name that
carries a control character, that names a device Windows reserves (``NUL``,
``CON``, ``COM1``, ...), or that ends in a space or a dot, and this witness
carries the same three refusals. The correction is the tier's: the tier is
what the request path runs, and a name the tier refuses is a name that would
otherwise reach the filesystem as an untyped failure. The parity check
``the source-id policy`` compares the two sides, so the correction cannot be
undone on one side alone.

Trust-boundary guards for source ingestion: source ids, upload paths, URLs.

Implements ``AUDIT_REMEDIATION_CONTRACT.md`` section 5 and
``AUDIT_REMEDIATION_CONTRACT_P2.md`` section 7. The public names in this module
(:func:`validate_source_id`, :func:`safe_upload_path`, :func:`upload_root`,
:func:`assert_public_url`, :func:`iter_redirect_hosts`) are load-bearing:
``lmeditor.server.routes`` imports them, so their names and parameter lists
must not drift. :func:`resolve_public_addresses` is additive.

Every rejection raises :class:`UnsafeSourceError`, which callers at the HTTP
boundary translate into the domain error ``SourceLoadError``.

:func:`assert_public_url` and :func:`resolve_public_addresses` share one
resolution path on purpose: a guard that vetted addresses and a loader that
vetted a *different* set of addresses would reintroduce exactly the
validate/connect disagreement the DNS-rebinding pass exists to close.
"""

from __future__ import annotations

import os
import re
import socket
import tempfile
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx

MAX_SOURCE_ID_LENGTH: int = 128
MAX_FILENAME_LENGTH: int = 255

_FORBIDDEN_ID_CHARS: Final[frozenset[str]] = frozenset('/\\:*?"<>|')
_ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_ID_SEGMENT_SPLIT: Final[re.Pattern[str]] = re.compile(r"[\\/]")

#: The names Windows reserves for its own devices.
#:
#: A file or a directory cannot carry one of them: ``NUL`` is the null device,
#: and a name whose part before the first dot is one of these names *is* that
#: device, whatever follows the dot.
_RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)

__all__ = [
    "MAX_FILENAME_LENGTH",
    "MAX_SOURCE_ID_LENGTH",
    "UnsafeSourceError",
    "assert_public_url",
    "iter_redirect_hosts",
    "resolve_public_addresses",
    "safe_upload_path",
    "upload_root",
    "validate_source_id",
]


class UnsafeSourceError(ValueError):
    """Raised when a source id, upload filename, or URL fails a boundary check."""


def _is_control_character(character: str) -> bool:
    """Return True when the character is a control character: a C0 control or DEL.

    None of them has a meaning in a URL, a source id or a file name, and every
    one of them is a way to confuse the text that is written to a socket or
    handed to the filesystem.
    """
    code = ord(character)
    return code <= 0x1F or code == 0x7F


def _is_whitespace(character: str) -> bool:
    """Return True when the character is whitespace the way the tier reads it.

    Python's whitespace set is the tier's set plus one character: U+0085, the
    C1 "next line" control. The tier does not treat that one as whitespace, so
    the witness does not either.
    """
    return character.isspace() and character != "\u0085"


def _is_blank(text: str) -> bool:
    """Return True when the text is blank: every character it holds is whitespace.

    An empty text is blank, and so is a text of spaces.
    """
    return all(_is_whitespace(character) for character in text)


def _is_reserved_device_name(name: str) -> bool:
    """Return True when the name is one Windows reserves for a device.

    The part before the first dot decides: ``NUL``, ``nul.txt`` and ``NUL.pdf``
    all name the null device, and none of them can be created.
    """
    return name.split(".", 1)[0].upper() in _RESERVED_DEVICE_NAMES


def _ends_with_stripped_character(name: str) -> bool:
    """Return True when the name ends in a space or a dot.

    Windows silently strips both, so the file that is written is not the file
    that was checked -- and the name that was checked is not the name that is
    later opened.
    """
    return bool(name) and name[-1] in (" ", ".")


def upload_root() -> Path:
    """Return the directory that holds persisted uploads.

    Defaults to ``<tempdir>/lmeditor_uploads`` (portable across Linux, macOS
    and Windows). ``LMEDITOR_UPLOAD_DIR`` overrides it so tests can redirect
    the root without writing into the real temp directory.
    """
    override = os.environ.get("LMEDITOR_UPLOAD_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "lmeditor_uploads"


def validate_source_id(source_id: str) -> str:
    """Return ``source_id`` unchanged, or raise if it is not a safe path segment.

    Rejects: empty (or whitespace-only) ids, ids longer than
    :data:`MAX_SOURCE_ID_LENGTH`, ids containing a control character, ids
    containing any of ``/ \\ : * ? " < > |``, ids starting with ``-``, ids
    ending in a space or a dot, ids naming a device Windows reserves (``NUL``,
    ``CON``, ``COM1``, ...), and ids containing a ``.`` or ``..`` segment.
    """
    if not isinstance(source_id, str) or _is_blank(source_id):
        raise UnsafeSourceError("source_id must be a non-empty string")
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise UnsafeSourceError(
            f"source_id exceeds the maximum length of {MAX_SOURCE_ID_LENGTH} characters"
        )
    control = next((c for c in source_id if _is_control_character(c)), None)
    if control is not None:
        raise UnsafeSourceError(
            f"source_id must not contain a control character (0x{ord(control):x})"
        )
    forbidden = sorted(_FORBIDDEN_ID_CHARS.intersection(source_id))
    if forbidden:
        raise UnsafeSourceError(f"source_id contains forbidden character(s): {forbidden}")
    if source_id.startswith("-"):
        raise UnsafeSourceError("source_id must not start with '-'")
    if _ends_with_stripped_character(source_id):
        raise UnsafeSourceError("source_id must not end in a space or a dot")
    if _is_reserved_device_name(source_id):
        raise UnsafeSourceError(f"source_id must not name a reserved device: '{source_id}'")
    segments = _ID_SEGMENT_SPLIT.split(source_id)
    if any(segment in (".", "..") for segment in segments):
        raise UnsafeSourceError("source_id must not contain a '.' or '..' segment")
    return source_id


def safe_upload_path(source_id: str, filename: str, base: Path | None = None) -> Path:
    """Return a proven-contained absolute path for an uploaded source file.

    ``source_id`` is validated, ``filename`` is reduced to its basename (so a
    client-supplied absolute path such as ``C:\\Windows\\x.txt`` or
    ``/etc/passwd`` is neutralized to ``x.txt`` / ``passwd``), truncated to
    :data:`MAX_FILENAME_LENGTH`, joined under ``base or upload_root()``, and
    finally *proven* to stay inside that root with ``Path.resolve()`` plus
    ``is_relative_to`` on the resolved root.

    A name is refused when it has no usable basename, when it carries a
    control character, when it names a device Windows reserves (``NUL``,
    ``CON``, ...), or when it ends in a space or a dot.
    """
    validated_id = validate_source_id(source_id)

    if not isinstance(filename, str):
        raise UnsafeSourceError("filename must be a string")
    control = next((c for c in filename if _is_control_character(c)), None)
    if control is not None:
        raise UnsafeSourceError(
            f"filename must not contain a control character (0x{ord(control):x})"
        )

    # Backslashes are normalized first so a Windows-style absolute path is
    # reduced to its basename on POSIX hosts too. The basename is taken as the
    # text after the last separator, which is what the tier does.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if _is_blank(name) or not name.strip(". "):
        raise UnsafeSourceError(f"filename has no usable basename: {filename!r}")
    name = name[:MAX_FILENAME_LENGTH]
    if _ends_with_stripped_character(name):
        raise UnsafeSourceError(f"filename must not end in a space or a dot: {name!r}")
    if _is_reserved_device_name(name):
        raise UnsafeSourceError(f"filename must not name a reserved device: {name!r}")

    root = Path(base) if base is not None else upload_root()
    root_resolved = root.resolve()
    candidate = (root_resolved / validated_id / name).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise UnsafeSourceError(f"resolved upload path escapes the upload root: {candidate}")
    return candidate


def _is_blocked_address(raw_address: str) -> bool:
    """Return True when an address must not be fetched by the URL loader."""
    try:
        parsed: IPv4Address | IPv6Address = ip_address(raw_address)
    except ValueError:
        # An address we cannot even parse is not one we should connect to.
        return True
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return (
        parsed.is_loopback
        or parsed.is_private
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def resolve_public_addresses(url: str) -> list[tuple[str, int]]:
    """Return the vetted ``(ip, port)`` pairs ``url`` is allowed to connect to.

    This is the pinning primitive of the SSRF guard. It validates scheme, host
    and port, resolves the host **once** through ``socket.getaddrinfo`` and
    returns only those ``(ip, port)`` pairs whose address is not loopback /
    private / link-local / reserved / multicast / unspecified (see
    :func:`_is_blocked_address`).

    Returning the vetted pairs — rather than a bare "is this URL allowed?"
    answer — is what lets a caller bind its *connection* to the addresses it
    actually inspected, instead of trusting httpx to re-resolve the name to the
    same answer a moment later (AUDIT.md "Still open": DNS rebinding).

    Raises :class:`UnsafeSourceError` when the URL is malformed, cannot be
    resolved, or resolves only to internal addresses.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeSourceError("url must be a non-empty string")

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise UnsafeSourceError(
            f"url scheme {parts.scheme!r} is not allowed (http/https only): {url!r}"
        )

    host = parts.hostname
    if not host:
        raise UnsafeSourceError(f"url has no host: {url!r}")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeSourceError(f"url has an invalid port: {url!r}") from exc

    try:
        addr_infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError) as exc:
        raise UnsafeSourceError(f"could not resolve host {host!r}: {exc}") from exc

    resolved: list[tuple[str, int]] = []
    for info in addr_infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        host_part = sockaddr[0]
        if not isinstance(host_part, str):
            continue
        # Some families report the port as a string; fall back to the port the
        # URL asked for rather than dropping an otherwise usable address.
        port_part = sockaddr[1] if len(sockaddr) > 1 else None
        resolved.append((host_part, port_part if isinstance(port_part, int) else port))

    if not resolved:
        raise UnsafeSourceError(f"could not resolve host {host!r}: no addresses returned")

    vetted = [(ip, resolved_port) for ip, resolved_port in resolved if not _is_blocked_address(ip)]
    if not vetted:
        raise UnsafeSourceError(
            f"host {host!r} resolves only to internal address(es): {[ip for ip, _ in resolved]}"
        )
    return vetted


def assert_public_url(url: str) -> str:
    """Return ``url`` unchanged, or raise if it must not be fetched (SSRF guard).

    Requires an ``http``/``https`` scheme with a host, resolves the host via
    ``socket.getaddrinfo`` and rejects the URL when resolution fails or when
    **every** resolved address is loopback / private / link-local / reserved /
    multicast / unspecified.

    A URL may legitimately resolve to a mix of public and internal addresses
    (split-horizon DNS, a CDN with an internal origin), so this guard only
    rejects an all-internal resolution. Callers that go on to open a connection
    must therefore use :func:`resolve_public_addresses` and accept only the
    vetted pairs, rather than re-resolving the name themselves.
    """
    resolve_public_addresses(url)
    return url


def _response_base_url(response: httpx.Response) -> str:
    """Return the response URL as a string, or '' when no request was attached."""
    try:
        return str(response.url)
    except RuntimeError:
        return ""


def iter_redirect_hosts(response: httpx.Response) -> list[str]:
    """Return the hostnames of every redirect target declared by ``response``.

    Reads all ``Location`` headers, resolves each against the response URL
    (so relative targets are handled) and returns the unique hostnames in
    order of appearance.
    """
    base = _response_base_url(response)
    hosts: list[str] = []
    for location in response.headers.get_list("location"):
        if not location:
            continue
        host = urlsplit(urljoin(base, location)).hostname
        if host is not None:
            lowered = host.lower()
            if lowered not in hosts:
                hosts.append(lowered)
    return hosts
