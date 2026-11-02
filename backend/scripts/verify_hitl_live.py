"""Verify the human-in-the-loop gate over the real wire.

The review gate is the one flow where a human is the only thing that can move
a run forward, so it is worth proving over the wire: the gate must be
published, it must be answerable with the body the server accepts, and the run
must finish afterwards.

The backend is started **without a language model**, so no section can be
drafted -- which is exactly what routes a run to the human-in-the-loop gate.
That also exercises the "the gateway is up but cannot write" path, which is
the one an operator meets first when a provider is misconfigured.

This script replaces the Flutter client's live HITL harness. It drives the same
run over the same HTTP surface and asserts the same things the harness did --
the gate arrives, the decision is accepted, the run finishes -- without a
client in the picture.

Usage::

    .\\.venv\\Scripts\\python.exe scripts\\verify_hitl_live.py
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent

SOURCE_TEXT = (
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023. "
    "The decline was driven by a citywide composting programme introduced in 2020. "
    "Household recycling participation rose to 61 percent of the population."
)
TOPIC = "Municipal waste collection trends"

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"

#: The session states, as the server names them.
STATUS_RUNNING = "running"
STATUS_AWAITING_HUMAN_REVIEW = "awaiting_human_review"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

#: The terminal states a run may reach once the gate has been answered.
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_REJECTED})


def _ok(message: str) -> None:
    print(f"{GREEN}PASS{RESET}  {message}")


def _fail(message: str) -> None:
    print(f"{RED}FAIL{RESET}  {message}")


def _info(message: str) -> None:
    print(f"{DIM}      {message}{RESET}")


def auth_headers(token: str) -> dict[str, str]:
    """The header that carries the API's credential.

    Every route under ``/api/`` refuses a request that does not carry it, so
    the credential travels with every request the script makes.
    """
    return {"Authorization": f"Bearer {token}"}


def free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(base_url: str, token: str, timeout: float = 90.0) -> bool:
    """Wait for the backend to answer its health endpoint.

    The health check is under ``/api/`` and is credentialed like every other
    route, so the probe presents the credential: a probe without one is
    answered with a refusal, and the server would never look healthy.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(
                f"{base_url}/api/v1/health",
                timeout=5.0,
                headers=auth_headers(token),
            )
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def spawn_server(port: int, token: str, token_file: Path) -> subprocess.Popen[bytes]:
    """Start a backend with **no** language model configured.

    Without a model no section can be drafted, so the run must route to the
    human-in-the-loop gate. The provider variables are removed rather than
    set, so an inherited environment cannot accidentally supply one.

    The server is served with the credential the script presents, and the
    token file it keeps is the one inside the folder the script owns: a token
    left in the operator's home directory would outlive the run.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_DIR / "src")
    env["LMEDITOR_API_TOKEN"] = token
    env["LMEDITOR_API_TOKEN_FILE"] = str(token_file)
    for name in ("LMEDITOR_LM_PROVIDER", "LMEDITOR_LM_MODEL", "LMEDITOR_LM_API_KEY"):
        env.pop(name, None)

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "lmeditor.server.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    _info(f"starting a backend on port {port} with no language model configured")
    return subprocess.Popen(command, env=env, cwd=str(BACKEND_DIR))


class StreamCollector:
    """Collects the SSE frames a run publishes."""

    def __init__(self, base_url: str, doc_id: str, token: str) -> None:
        self.base_url = base_url
        self.doc_id = doc_id
        self.token = token
        self.events: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            with httpx.stream(
                "GET",
                f"{self.base_url}/api/v1/stream/{self.doc_id}",
                timeout=300.0,
                headers=auth_headers(self.token),
            ) as response:
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        self.events.append(json.loads(line.split(":", 1)[1].strip()))
                    except json.JSONDecodeError:
                        continue
                    if self.events[-1].get("event_type") in (
                        "workflow_complete",
                        "workflow_error",
                    ):
                        return
        except httpx.HTTPError:
            return

    def types(self) -> list[str]:
        return [event.get("event_type", "") for event in self.events]

    def terminal(self) -> dict[str, Any] | None:
        for event in self.events:
            if event.get("event_type") in ("workflow_complete", "workflow_error"):
                return event
        return None


def wait_for_status(
    client: httpx.Client,
    base_url: str,
    doc_id: str,
    wanted: frozenset[str],
    timeout: float = 120.0,
) -> str:
    """Poll the workflow status until the run reaches one of the wanted states."""
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        try:
            response = client.get(f"{base_url}/api/v1/workflow/{doc_id}/status")
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            status = response.json().get("status", "")
            if status in wanted:
                return status
        time.sleep(0.5)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    upload_dir = tempfile.mkdtemp(prefix="lmeditor_live_hitl_uploads_")
    os.environ["LMEDITOR_UPLOAD_DIR"] = upload_dir
    # The credential the script presents, and the server it is served with.
    # The token file lives in the folder the script owns, so the server
    # writes nothing into the operator's home directory.
    token = secrets.token_urlsafe(32)

    server = spawn_server(port, token, Path(upload_dir) / "api-token")
    failures = 0

    try:
        print(f"backend: {base_url} (no language model, so the gate is reached)")
        if not wait_for_health(base_url, token):
            _fail("the backend did not become healthy")
            return 1
        _ok("the backend is healthy")

        client = httpx.Client(timeout=120.0, headers=auth_headers(token))

        print("\n1. The backend reports that it cannot write")
        health = client.get(f"{base_url}/api/v1/health").json()
        lm = health.get("lm") or {}
        if lm.get("configured"):
            _fail("the backend reports a configured model; the gate would not be reached")
            failures += 1
        else:
            _ok("no model is configured, so no section can be drafted")
        if lm.get("is_stub"):
            _fail("the backend is running the stub; the gate would not be reached")
            failures += 1

        print("\n2. Ingest a source")
        ingest = client.post(
            f"{base_url}/api/v1/sources/ingest",
            data={"source_id": "hitl_src", "title": "Municipal Audit"},
            files={"file": ("audit.txt", SOURCE_TEXT.encode("utf-8"), "text/plain")},
        )
        if ingest.status_code >= 400:
            _fail(f"ingest returned {ingest.status_code}: {ingest.text[:200]}")
            return 1
        _ok(f"ingested {len(ingest.json().get('chunks') or [])} chunk(s)")

        print("\n3. Start a run")
        generate = client.post(
            f"{base_url}/api/v1/documents/generate",
            json={"topic": TOPIC, "source_ids": ["hitl_src"], "max_retries": 1},
        )
        if generate.status_code >= 400:
            _fail(f"generate returned {generate.status_code}: {generate.text[:300]}")
            return 1
        doc_id = generate.json()["doc_id"]
        _ok(f"document {doc_id} started")

        collector = StreamCollector(base_url, doc_id, token)
        collector.start()

        print("\n4. The run reaches the gate")
        status = wait_for_status(
            client,
            base_url,
            doc_id,
            frozenset({STATUS_AWAITING_HUMAN_REVIEW, *TERMINAL_STATUSES}),
            timeout=args.timeout,
        )
        if status != STATUS_AWAITING_HUMAN_REVIEW:
            _fail(f"the run reached {status!r} instead of the review gate")
            failures += 1
        else:
            _ok("the run is awaiting human review")

        print("\n5. The gate is published on the stream")
        deadline = time.time() + args.timeout
        while "hitl_request" not in collector.types() and time.time() < deadline:
            time.sleep(0.25)
        if "hitl_request" in collector.types():
            _ok("the stream published 'hitl_request'")
        else:
            _fail(f"the stream never published 'hitl_request' (saw {collector.types()})")
            failures += 1

        print("\n6. Answer the gate")
        # The path names the document; the body carries the decision alone.
        approve = client.post(
            f"{base_url}/api/v1/workflow/{doc_id}/approve",
            json={"approved": True, "feedback": "verified by script"},
        )
        if approve.status_code >= 400:
            _fail(f"approve returned {approve.status_code}: {approve.text[:300]}")
            failures += 1
        else:
            _ok(f"the gate was accepted ({approve.text[:120]})")

        print("\n7. The run finishes")
        final = wait_for_status(
            client, base_url, doc_id, TERMINAL_STATUSES, timeout=args.timeout
        )
        if final in TERMINAL_STATUSES:
            _ok(f"the run reached the terminal state {final!r}")
        else:
            _fail(f"the run never reached a terminal state (last saw {final!r})")
            failures += 1

        print("\n8. The stream published a terminal frame")
        terminal = collector.terminal()
        if terminal is None:
            _fail("the stream never published a terminal frame")
            failures += 1
        else:
            _ok(f"the stream published {terminal.get('event_type')!r}")

        print()
        if failures:
            _fail(f"{failures} check(s) failed")
            return 1
        _ok("every check passed")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            server.kill()
        shutil.rmtree(upload_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
