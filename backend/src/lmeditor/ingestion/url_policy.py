"""Trust-boundary guards for source ingestion: source ids, upload paths, URLs.

Implements ``AUDIT_REMEDIATION_CONTRACT.md`` section 5 and the source-ingest
requirements of ``AUDIT.md``: every artifact that enters the system from the
outside is given a name that cannot escape the directory it is stored in, and
every URL that the system is asked to fetch is vetted before the fetch
happens.

This module is a *door*: the rules -- what a source id may look like, where an
upload may be written, and which addresses a URL may resolve to -- live in the
Kotlin tier, and the Python API answers with the tier's answers. The original
implementation is frozen in :mod:`lmeditor._reference.ingestion_url_policy`.

The parity harness does compare the two sides. The check ``the source-id
policy`` in ``backend/scripts/verify_kotlin_parity.py`` asks the frozen
witness and the tier the same questions -- a set of source ids and a set of
upload file names -- and compares the answers. The witness was brought back
into agreement with the tier: it refuses exactly what the tier refuses, the
ids and the names that carry a control character (a C0 control or DEL), name
a device Windows reserves, or end in a space or a dot.
``backend/tests/test_url_policy_witness_agreement.py`` pins that agreement
case by case, so a rule that starts refusing more, or less, than the tier
fails there rather than in production. The ledger ``REFERENCE_WITNESSES`` in
the same script names this module against its Kotlin counterpart
(``kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt``) and the
harness fails if that declaration stops matching the tree.

What stays here is what cannot cross the seam: the two helpers that read an
``httpx.Response``. They are bound to httpx, and the tier's equivalents take
the base URL and the ``Location`` headers as plain values instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from lmeditor.tier import ingest as tier_ingest

#: The longest a source id may be.
#:
#: The same number as the Kotlin tier's ``UrlPolicy.MAX_SOURCE_ID_LENGTH``.
#: It is written out rather than read across the seam because a constant that
#: could not be read without a running JVM would be useless to a module that
#: is imported at startup -- ``lmeditor.ingestion`` imports it eagerly. The
#: two numbers are the same today, and the tier's own test pins the tier's
#: number; nothing compares the two numbers across languages, so the
#: duplication is a declared gap rather than a held one.
MAX_SOURCE_ID_LENGTH: int = 128

#: The longest an upload file name may be.
#:
#: The same number as the Kotlin tier's ``UrlPolicy.MAX_FILENAME_LENGTH``; see
#: :data:`MAX_SOURCE_ID_LENGTH` for why it is written out rather than read
#: across the seam.
MAX_FILENAME_LENGTH: int = 255

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
    """Raised when a source id, upload filename, or URL fails a boundary check.

    It is a ``ValueError`` because that is what it is: a value that does not
    satisfy a constraint. The Kotlin tier raises its own ``UnsafeSourceError``
    for the same refusals, and the seam rebuilds this one from it, so a caller
    that catches this still catches the tier's answer.
    """


def upload_root() -> Path:
    """Return the directory that holds persisted uploads.

    Defaults to ``<tempdir>/lmeditor_uploads`` and is overridden by the
    ``LMEDITOR_UPLOAD_DIR`` environment variable.

    The directory is the tier's: the Kotlin tier resolves the same
    ``LMEDITOR_UPLOAD_DIR`` / ``<tempdir>/lmeditor_uploads`` pair, so both
    tiers name the same directory and a file written by one is found by the
    other.

    The *configured* value is forwarded rather than the resolved path, because
    the tier runs in its own process and that process's environment is not
    necessarily this one's. Without the forwarded value the tier would answer
    with the root *its* environment names, and the two tiers would disagree
    about where an upload goes.

    Raises:
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    return Path(tier_ingest.upload_root(base=os.environ.get("LMEDITOR_UPLOAD_DIR")))


def validate_source_id(source_id: str) -> str:
    """Return ``source_id`` unchanged, or raise if it is not a safe path segment.

    A source id becomes a directory name under the upload root, so it may not
    contain a path separator, a NUL, a drive letter or any of the characters
    Windows refuses in a file name, and it may not be one of the reserved
    device names. It is also bounded in length.

    The rule is the Kotlin tier's; this function answers with the tier's
    answer.

    Raises:
        UnsafeSourceError: the id is not a safe path segment.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    return tier_ingest.validate_source_id(source_id)


def safe_upload_path(source_id: str, filename: str, base: Path | None = None) -> Path:
    """Return a proven-contained absolute path for an uploaded source file.

    The name is reduced to its basename (so a client-supplied absolute path
    such as ``C:\\Windows\\x.txt`` is neutralized to ``x.txt``), truncated to
    :data:`MAX_FILENAME_LENGTH`, joined under ``base or upload_root()``, and
    finally *proven* to stay inside that root.

    The rule is the Kotlin tier's; this function answers with the tier's
    answer.

    Raises:
        UnsafeSourceError: the id or the name is not usable, or the resolved
            path escapes the upload root.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    return Path(
        tier_ingest.safe_upload_path(
            source_id,
            filename,
            base=str(base) if base is not None else None,
        )
    )


def resolve_public_addresses(url: str) -> list[tuple[str, int]]:
    """Return the vetted ``(ip, port)`` pairs ``url`` is allowed to connect to.

    This is the pinning primitive of the SSRF guard. It validates scheme, host
    and port, resolves the host **once** and returns only those ``(ip, port)``
    pairs whose address is not loopback / private / link-local / reserved /
    multicast / unspecified.

    Returning the vetted pairs -- rather than a bare "is this URL allowed?"
    answer -- is what lets a caller bind its *connection* to the addresses it
    actually inspected, instead of trusting the transport to re-resolve the
    name to the same answer a moment later (AUDIT.md "Still open": DNS
    rebinding).

    The rule is the Kotlin tier's: it resolves the name and vets every address
    it gets back. This function answers with the tier's answer.

    Raises:
        UnsafeSourceError: the URL is malformed, cannot be resolved, or
            resolves only to internal addresses.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    return tier_ingest.resolve_public_addresses(url)


def assert_public_url(url: str) -> str:
    """Return ``url`` unchanged, or raise if it must not be fetched (SSRF guard).

    Requires an ``http``/``https`` scheme with a host, resolves the host and
    rejects the URL when resolution fails or when **every** resolved address
    is loopback / private / link-local / reserved / multicast / unspecified.

    A URL may legitimately resolve to a mix of public and internal addresses
    (split-horizon DNS, a CDN with an internal origin), so this guard only
    rejects an all-internal resolution. Callers that go on to open a connection
    must therefore use :func:`resolve_public_addresses` and accept only the
    vetted pairs, rather than re-resolving the name themselves.

    The rule is the Kotlin tier's; this function answers with the tier's
    answer.

    Raises:
        UnsafeSourceError: the URL must not be fetched.
        BridgeUnavailableError: The Kotlin tier could not be reached. That is
            an infrastructure failure, not a bad input, and it is never
            swallowed.
    """
    tier_ingest.assert_public_url(url)
    return url


def _response_base_url(response: httpx.Response) -> str:
    """Return the response URL as a string, or '' when no request was attached.

    This helper is bound to httpx: it reads an ``httpx.Response``, which
    cannot cross the seam. The tier's equivalent takes the base URL as a
    plain string instead.
    """
    try:
        return str(response.url)
    except RuntimeError:
        return ""


def iter_redirect_hosts(response: httpx.Response) -> list[str]:
    """Return the hostnames of every redirect target declared by ``response``.

    Reads all ``Location`` headers, resolves each against the response URL
    (so relative targets are handled) and returns the unique hostnames in
    order of appearance.

    This helper is bound to httpx: it reads an ``httpx.Response``, which
    cannot cross the seam. The tier's equivalent takes the base URL and the
    ``Location`` headers as plain values instead.
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
