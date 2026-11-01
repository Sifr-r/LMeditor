"""The SSRF guard, as the Python door sees it.

The rule -- which addresses a hop may reach, that every redirect hop is vetted
again, and that a connection whose peer cannot be shown to be vetted is
refused rather than served -- belongs to the Kotlin tier, and the tier's own
tests prove it there
(``kotlin/ingest/src/test/kotlin/lmeditor/ingest/UrlPolicyTest.kt`` and
``HttpSafetyTest.kt``). A Python test can no longer stub the tier's name
resolution or its transport: both happen in the JVM.

What is left for this side is the *door*: the Python entry points must forward
the URL, must refuse what the tier refuses, and must answer with the tier's
vetted pairs. The assertions below use literal addresses so that they do not
depend on a resolver at all -- a literal is vetted without a lookup, so they
say the same thing on a machine with a network and on one without.
"""

from __future__ import annotations

import pytest

from lmeditor.core.exceptions import SourceLoadError
from lmeditor.ingestion.loaders import UrlSourceLoader
from lmeditor.ingestion.url_policy import (
    UnsafeSourceError,
    assert_public_url,
    resolve_public_addresses,
)

PUBLIC_ADDRESS = "93.184.216.34"

# ============================================================================
# 1. The pin primitive
# ============================================================================


def test_the_vetted_pairs_carry_the_port_of_the_scheme() -> None:
    """A public address pins to itself and to the port the URL names."""
    assert resolve_public_addresses(f"https://{PUBLIC_ADDRESS}/paper") == [
        (PUBLIC_ADDRESS, 443)
    ]
    assert resolve_public_addresses(f"http://{PUBLIC_ADDRESS}/paper") == [(PUBLIC_ADDRESS, 80)]
    assert resolve_public_addresses(f"https://{PUBLIC_ADDRESS}:8443/paper") == [
        (PUBLIC_ADDRESS, 8443)
    ]


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("127.0.0.1", id="loopback"),
        pytest.param("10.0.0.1", id="private-10"),
        pytest.param("192.168.1.1", id="private-192"),
        pytest.param("169.254.169.254", id="cloud-metadata"),
        pytest.param("0.0.0.0", id="unspecified"),
        pytest.param("224.0.0.1", id="multicast"),
        pytest.param("::1", id="ipv6-loopback"),
        pytest.param("fd00::1", id="ipv6-unique-local"),
    ],
)
def test_an_internal_address_yields_no_pinned_pair(address: str) -> None:
    """An internal address is refused, and nothing is pinned to it."""
    with pytest.raises(UnsafeSourceError):
        resolve_public_addresses(f"http://{address}/x")


def test_a_public_address_is_allowed_unchanged() -> None:
    """A public address is returned unchanged."""
    url = f"https://{PUBLIC_ADDRESS}/paper"
    assert assert_public_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file:///etc/passwd", id="file"),
        pytest.param("gopher://127.0.0.1:11211/_stats", id="gopher"),
        pytest.param("ftp://example.org/x", id="ftp"),
        pytest.param("http:///no-host", id="no-host"),
    ],
)
def test_a_target_that_is_not_a_public_http_url_is_refused(url: str) -> None:
    """Only an http or https URL on a public address may be fetched."""
    with pytest.raises(UnsafeSourceError):
        assert_public_url(url)


# ============================================================================
# 2. The loader, which asks the same policy before it fetches
# ============================================================================


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://127.0.0.1:8080/internal", id="loopback"),
        pytest.param("http://10.0.0.1/", id="private-10"),
        pytest.param("http://192.168.1.1/", id="private-192"),
        pytest.param("http://169.254.169.254/latest/meta-data/", id="cloud-metadata"),
        pytest.param("http://[::1]/internal", id="ipv6-loopback"),
        pytest.param("http://0.0.0.0/", id="unspecified"),
        pytest.param("http://[fd00::1]/", id="ipv6-unique-local"),
        pytest.param("http://224.0.0.1/", id="multicast"),
        pytest.param("file:///etc/passwd", id="file"),
    ],
)
def test_the_url_loader_refuses_an_internal_target(url: str) -> None:
    """An internal target is refused before anything is fetched.

    The refusal reaches the caller as the domain error the loader's callers
    already catch, with the policy's reason in it.
    """
    with pytest.raises(SourceLoadError) as excinfo:
        UrlSourceLoader().load(url)
    assert "blocked URL" in str(excinfo.value)


def test_the_url_loader_refuses_a_file_path() -> None:
    """The URL loader refuses a local path: the mistake the guard exists for."""
    with pytest.raises(SourceLoadError) as excinfo:
        UrlSourceLoader().load("/etc/passwd")
    assert "is not allowed" in str(excinfo.value)
