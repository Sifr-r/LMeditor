"""The frozen URL-policy witness and the Kotlin tier must agree on the rule.

The tier was hardened to refuse a source id or a file name that carries a
control character, that names a device Windows reserves (``NUL``, ``CON``,
``COM1``, ...) or that ends in a space or a dot. The frozen witness in
``lmeditor._reference.ingestion_url_policy`` used to *accept* those, so it was
no longer an oracle for the rule: the tier refused what the witness accepted,
and a witness that answers differently is not a witness.

What this file proves is that the two sides now agree. The harness that
compares them over the whole corpus is ``backend/scripts/verify_kotlin_parity.py``
-- the check ``the source-id policy`` -- and it is the tool that keeps them
that way. The tests here are the narrow proof: they name the cases the
hardening was about, and they are the ones that fail if either side is loosened
again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from lmeditor._reference import ingestion_url_policy as witness
from lmeditor.bridge import KotlinBridge
from lmeditor.ingestion.url_policy import UnsafeSourceError

#: A source id carrying a control character.
CONTROL_CHARACTER_IDS: tuple[str, ...] = (
    "src\x00id",
    "src\x1fid",
    "src\x7fid",
    "src\nid",
    "src\tid",
)

#: A source id naming a device Windows reserves.
RESERVED_DEVICE_IDS: tuple[str, ...] = (
    "NUL",
    "nul",
    "NUL.txt",
    "nul.pdf",
    "CON",
    "PRN",
    "AUX",
    "CLOCK$",
    "COM1",
    "LPT9",
)

#: A source id that ends in a space or a dot.
STRIPPED_CHARACTER_IDS: tuple[str, ...] = (
    "src ",
    "src.",
    "NUL ",
    "NUL.",
)

#: The file names carrying a control character, naming a reserved device, or
#: ending in a space or a dot.
HARDENED_NAMES: tuple[str, ...] = (
    "notes\x00.txt",
    "notes\x1f.txt",
    "NUL",
    "nul.txt",
    "CON.pdf",
    "notes.txt ",
    "notes.txt.",
)

#: The ids the two sides are asked about, so that a rule that refuses too much
#: is caught as well as a rule that refuses too little.
AGREEMENT_IDS: tuple[str, ...] = (
    "src-001",
    "a" * 128,
    "a" * 129,
    "",
    "   ",
    *CONTROL_CHARACTER_IDS,
    *RESERVED_DEVICE_IDS,
    *STRIPPED_CHARACTER_IDS,
    "NULL",
    "COM0",
    "CONIN",
    " src",
    "src.txt",
    "-src",
    "src/../evil",
    "src:with:colon",
)

#: The file names the two sides are asked about.
AGREEMENT_NAMES: tuple[str, ...] = (
    "notes.txt",
    "a/b/notes.txt",
    "C:\\Windows\\x.txt",
    "/etc/passwd",
    "..",
    ".",
    " ",
    "",
    *HARDENED_NAMES,
    "x" * 300,
)


@pytest.fixture(scope="module")
def bridge(kotlin_tier: bool) -> Iterator[KotlinBridge]:
    """A bridge to the Kotlin tier, for the tests that compare the two sides."""
    client = KotlinBridge()
    try:
        yield client
    finally:
        client.close()


def _refuses(call: Callable[[], Any]) -> bool:
    """Whether the rule refused the input."""
    try:
        call()
    except witness.UnsafeSourceError:
        return True
    return False


def _tier_refuses(bridge: KotlinBridge, method: str, params: dict[str, Any]) -> bool:
    """Whether the tier refused the input.

    The tier refuses by raising, so a refusal is the error the seam rebuilds
    from the tier's own error type. Any other error is raised: it says the
    seam did not answer, which is not a refusal and not a disagreement about
    the rule.
    """
    try:
        bridge.call(method, params)
    except UnsafeSourceError:
        return True
    return False


# ============================================================================
# The witness refuses what the tier refuses
# ============================================================================


@pytest.mark.parametrize("source_id", CONTROL_CHARACTER_IDS)
def test_the_witness_refuses_a_control_character_in_a_source_id(source_id: str) -> None:
    """A control character in a source id is refused."""
    assert _refuses(lambda: witness.validate_source_id(source_id))


@pytest.mark.parametrize("source_id", RESERVED_DEVICE_IDS)
def test_the_witness_refuses_a_reserved_device_name(source_id: str) -> None:
    """A source id naming a device Windows reserves is refused."""
    assert _refuses(lambda: witness.validate_source_id(source_id))


@pytest.mark.parametrize("source_id", STRIPPED_CHARACTER_IDS)
def test_the_witness_refuses_a_source_id_that_ends_in_a_space_or_a_dot(
    source_id: str,
) -> None:
    """A source id that ends in a space or a dot is refused."""
    assert _refuses(lambda: witness.validate_source_id(source_id))


@pytest.mark.parametrize("filename", HARDENED_NAMES)
def test_the_witness_refuses_a_hardened_file_name(filename: str, tmp_path: Path) -> None:
    """A file name that carries a control character, names a reserved device, or
    ends in a space or a dot is refused."""
    assert _refuses(lambda: witness.safe_upload_path("src-001", filename, base=tmp_path))


# ============================================================================
# The tier refuses the same
# ============================================================================


@pytest.mark.parametrize("source_id", CONTROL_CHARACTER_IDS)
def test_the_tier_refuses_a_control_character_in_a_source_id(
    bridge: KotlinBridge, source_id: str
) -> None:
    """The tier refuses a control character in a source id."""
    assert _tier_refuses(
        bridge, "ingest.url_policy.validate_source_id", {"source_id": source_id}
    )


@pytest.mark.parametrize("source_id", RESERVED_DEVICE_IDS)
def test_the_tier_refuses_a_reserved_device_name(
    bridge: KotlinBridge, source_id: str
) -> None:
    """The tier refuses a source id naming a device Windows reserves."""
    assert _tier_refuses(
        bridge, "ingest.url_policy.validate_source_id", {"source_id": source_id}
    )


@pytest.mark.parametrize("source_id", STRIPPED_CHARACTER_IDS)
def test_the_tier_refuses_a_source_id_that_ends_in_a_space_or_a_dot(
    bridge: KotlinBridge, source_id: str
) -> None:
    """The tier refuses a source id that ends in a space or a dot."""
    assert _tier_refuses(
        bridge, "ingest.url_policy.validate_source_id", {"source_id": source_id}
    )


@pytest.mark.parametrize("filename", HARDENED_NAMES)
def test_the_tier_refuses_a_hardened_file_name(
    bridge: KotlinBridge, filename: str
) -> None:
    """The tier refuses a file name that carries a control character, names a
    reserved device, or ends in a space or a dot."""
    assert _tier_refuses(
        bridge,
        "ingest.url_policy.safe_upload_path",
        {"source_id": "src-001", "filename": filename},
    )


# ============================================================================
# The two sides agree
# ============================================================================


@pytest.mark.parametrize("source_id", AGREEMENT_IDS)
def test_the_witness_and_the_tier_agree_about_a_source_id(
    bridge: KotlinBridge, source_id: str
) -> None:
    """The two sides reach the same decision about every source id."""
    witness_refused = _refuses(lambda: witness.validate_source_id(source_id))
    tier_refused = _tier_refuses(
        bridge, "ingest.url_policy.validate_source_id", {"source_id": source_id}
    )
    assert witness_refused == tier_refused


@pytest.mark.parametrize("filename", AGREEMENT_NAMES)
def test_the_witness_and_the_tier_agree_about_a_file_name(
    bridge: KotlinBridge, filename: str, tmp_path: Path
) -> None:
    """The two sides reach the same decision about every file name."""
    witness_refused = _refuses(
        lambda: witness.safe_upload_path("src-001", filename, base=tmp_path)
    )
    tier_refused = _tier_refuses(
        bridge,
        "ingest.url_policy.safe_upload_path",
        {"source_id": "src-001", "filename": filename},
    )
    assert witness_refused == tier_refused
