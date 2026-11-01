"""Tests for the NDJSON seam's client half.

The client is exercised against a fake bridge process that speaks the same
wire protocol, so these tests check the framing, the correlation, the error
mapping and the failure handling without needing a built Kotlin tier.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from lmeditor.bridge import (
    BridgeProtocolError,
    BridgeUnavailableError,
    KotlinBridge,
    KotlinDomainError,
    decode_bytes,
    encode_bytes,
)
from lmeditor.core.exceptions import (
    ValidationError,
    WordParseError,
)

FAKE_BRIDGE = '''
import json
import sys

# The pipe is UTF-8 on both ends, whatever the console encoding is.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params") or {}
    if method == "text.chunk":
        response = {"ok": True, "result": {"chunks": [{"chunk_id": "abc"}]}}
    elif method == "boom":
        response = {
            "ok": False,
            "error": {
                "type": "WordParseError",
                "message": "Failed to parse Word document 'a.docx': not a zip",
                "details": {"origin": "a.docx", "reason": "not a zip"},
            },
        }
    elif method == "invalid":
        response = {
            "ok": False,
            "error": {
                "type": "ValidationError",
                "message": "text must not be empty",
                "details": {"field": "text", "value": ""},
            },
        }
    elif method == "unknown":
        response = {
            "ok": False,
            "error": {"type": "BridgeError", "message": "unknown method", "details": {}},
        }
    elif method == "garbage":
        sys.stdout.write("this is not json\\n")
        sys.stdout.flush()
        continue
    else:
        response = {"ok": True, "result": {"echo": method, "params": params}}
    response["id"] = request.get("id")
    sys.stdout.write(json.dumps(response) + "\\n")
    sys.stdout.flush()
'''


@pytest.fixture()
def fake_bridge(tmp_path: Path) -> Path:
    script = tmp_path / "fake_bridge.py"
    script.write_text(FAKE_BRIDGE, encoding="utf-8")
    return script


@pytest.fixture()
def bridge(fake_bridge: Path) -> Iterator[KotlinBridge]:
    client = KotlinBridge(command=[sys.executable, str(fake_bridge)])
    try:
        yield client
    finally:
        client.close()


def test_a_call_returns_the_result_object(bridge: KotlinBridge) -> None:
    result = bridge.call("text.chunk", {"text": "Hello", "document_id": "d"})
    assert result == {"chunks": [{"chunk_id": "abc"}]}


def test_a_call_without_parameters_still_works(bridge: KotlinBridge) -> None:
    result = bridge.call("anything")
    assert result == {"echo": "anything", "params": {}}


def test_the_request_id_is_echoed_and_the_call_still_matches(
    bridge: KotlinBridge,
) -> None:
    first = bridge.call("anything")
    second = bridge.call("anything")
    assert first == second


def test_parameters_travel_unchanged(bridge: KotlinBridge) -> None:
    params = {"text": "Héllo", "nested": {"a": [1, 2, 3]}, "flag": True}
    result = bridge.call("anything", params)
    assert result["params"] == params


def test_a_typed_domain_error_becomes_its_python_counterpart(
    bridge: KotlinBridge,
) -> None:
    with pytest.raises(WordParseError) as caught:
        bridge.call("boom")
    assert caught.value.origin == "a.docx"
    assert caught.value.reason == "not a zip"


def test_a_validation_error_keeps_its_field(bridge: KotlinBridge) -> None:
    with pytest.raises(ValidationError) as caught:
        bridge.call("invalid")
    assert caught.value.field == "text"


def test_an_unmapped_error_type_becomes_a_domain_error(bridge: KotlinBridge) -> None:
    with pytest.raises(KotlinDomainError) as caught:
        bridge.call("unknown")
    assert caught.value.error_type == "BridgeError"


def test_a_malformed_response_is_reported_as_a_protocol_error(
    bridge: KotlinBridge,
) -> None:
    with pytest.raises(BridgeProtocolError):
        bridge.call("garbage")


def test_a_dead_bridge_is_reported_as_unavailable(tmp_path: Path) -> None:
    script = tmp_path / "dead.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    client = KotlinBridge(command=[sys.executable, str(script)])
    try:
        with pytest.raises(BridgeUnavailableError):
            client.call("anything")
    finally:
        client.close()


def test_a_missing_java_executable_is_reported_as_unavailable() -> None:
    client = KotlinBridge(classpath="anything", java_executable="definitely-not-java")
    try:
        with pytest.raises(BridgeUnavailableError):
            client.call("anything")
    finally:
        client.close()


def test_binary_payloads_round_trip() -> None:
    payload = b"\x00\x01\x02 the quick brown fox \xff"
    assert decode_bytes(encode_bytes(payload)) == payload


def test_the_bridge_reports_whether_it_is_running(bridge: KotlinBridge) -> None:
    assert not bridge.is_running
    bridge.call("anything")
    assert bridge.is_running
