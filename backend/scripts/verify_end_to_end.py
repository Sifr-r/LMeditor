"""End-to-end verification of a running LMeditor backend.

The script drives the real HTTP surface of a running server -- ingest a
source, generate a document, follow the SSE stream, answer the human-in-the-loop
gate, fetch the finished manuscript and export it to Word -- and reports what
it observed.

Two modes:

``--stub-model`` (default)
    Installs a deterministic, grounded language-model double before talking to
    the server, so the whole wiring can be verified on a machine with no model
    provider configured. The double quotes the passage it is given and cites
    that passage's real chunk id.

``--live``
    Uses the model the server itself is configured with. The server must have
    ``LMEDITOR_LM_PROVIDER`` / ``LMEDITOR_LM_MODEL`` set, and the script
    refuses to run if the server reports that no model is loaded.

Usage::

    python scripts/verify_end_to_end.py --base-url http://127.0.0.1:8000
    python scripts/verify_end_to_end.py --live
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from lmeditor.core.types import EventType

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


def the_operators_token() -> str:
    """The token a server the script did not start is served with.

    A server the operator started was given its token in the environment, or
    it generated one and left it in the token file. The script looks in the
    two places the API's own client looks, so that a run against the
    operator's own server can talk to it.

    Returns:
        The token, or an empty string when there is none to be found.
    """
    named = os.environ.get("LMEDITOR_API_TOKEN", "").strip()
    if named:
        return named
    path = os.environ.get("LMEDITOR_API_TOKEN_FILE", "").strip()
    if not path:
        path = str(Path.home() / ".lmeditor" / "api-token")
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def install_stub_model() -> None:
    """Install the deterministic model double into the server process."""
    import dspy

    from lmeditor.llm.stub import StubLM

    dspy.configure(lm=StubLM())
    _info("installed the deterministic stub model")


def spawn_server(port: int, token: str, token_file: Path) -> subprocess.Popen[bytes] | None:
    """Start a backend configured with the deterministic stub model.

    The server is served with the credential the script presents, and the
    token file it keeps is the one the script owns: a token left in the
    operator's home directory would outlive the run.
    """
    env = dict(os.environ)
    env["LMEDITOR_LM_PROVIDER"] = "stub"
    env["LMEDITOR_API_TOKEN"] = token
    env["LMEDITOR_API_TOKEN_FILE"] = str(token_file)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
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
    _info(f"starting a backend on port {port} with LMEDITOR_LM_PROVIDER=stub")
    return subprocess.Popen(command, env=env, cwd=str(Path(__file__).resolve().parents[1]))


def wait_for_health(base_url: str, token: str, timeout: float = 60.0) -> bool:
    """Wait until the backend answers its health check.

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


def wait_for(client: httpx.Client, base_url: str, doc_id: str, timeout: float = 300.0) -> dict[str, Any]:
    """Poll the workflow status until the run reaches a terminal state."""
    deadline = time.time() + timeout
    status: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"{base_url}/api/v1/workflow/{doc_id}/status")
        if response.status_code == 200:
            status = response.json()
            if status.get("status") != "running":
                return status
        time.sleep(0.5)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the server's own configured model instead of the stub",
    )
    parser.add_argument(
        "--no-spawn",
        action="store_true",
        help="do not start a backend; use the one already at --base-url",
    )
    parser.add_argument("--topic", default=TOPIC)
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="how long to wait for a run to reach a terminal state",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    failures = 0
    server: subprocess.Popen[bytes] | None = None
    workspace: Path | None = None
    # The credential the script presents. A server the script starts is
    # served with the token below; a server the operator started was given a
    # token of its own, and that one is read instead.
    token = ""

    if args.live:
        print("Live mode: using the server's configured model.")
        token = the_operators_token()
    else:
        print("Stub mode: verifying the wiring without a model provider.")
        if not args.no_spawn:
            token = secrets.token_urlsafe(32)
            workspace = Path(tempfile.mkdtemp(prefix="lmeditor_end_to_end_"))
            port = int(base_url.rsplit(":", 1)[-1]) if ":" in base_url else 8000
            server = spawn_server(port, token, workspace / "api-token")
            if not wait_for_health(base_url, token):
                _fail("the backend did not become healthy in time")
                if server is not None:
                    server.terminate()
                if workspace is not None:
                    shutil.rmtree(workspace, ignore_errors=True)
                return 1
        else:
            token = the_operators_token()

    try:
        return _run_checks(args, base_url, token)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                server.kill()
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)


def _run_checks(args: argparse.Namespace, base_url: str, token: str) -> int:
    failures = 0
    client = httpx.Client(timeout=120.0, headers=auth_headers(token))

    print("\n1. Health")
    try:
        health = client.get(f"{base_url}/api/v1/health")
    except httpx.HTTPError as exc:
        _fail(f"could not reach {base_url}: {exc}")
        return 1
    if health.status_code != 200:
        _fail(f"health returned {health.status_code}")
        return 1
    payload = health.json()
    _ok(f"backend is healthy (version {payload.get('version')})")
    lm = payload.get("lm") or {}
    _info(f"model configured: {lm.get('configured')} ({lm.get('model') or 'none'})")
    if args.live and not lm.get("configured"):
        _fail("live mode was requested but the server has no model configured")
        return 1

    print("\n2. Ingest a source")
    ingest = client.post(
        f"{base_url}/api/v1/sources/ingest",
        data={"source_id": "verify_src", "title": "Municipal Audit"},
        files={"file": ("audit.txt", SOURCE_TEXT.encode("utf-8"), "text/plain")},
    )
    if ingest.status_code >= 400:
        _fail(f"ingest returned {ingest.status_code}: {ingest.text[:200]}")
        return 1
    body = ingest.json()
    chunks = body.get("chunks") or []
    _ok(f"ingested {len(chunks)} chunk(s)")
    omissions = body.get("omissions") or []
    _info(f"omissions reported: {omissions or 'none'}")

    print("\n3. Generate a document")
    generate = client.post(
        f"{base_url}/api/v1/documents/generate",
        json={
            "topic": args.topic,
            "source_ids": ["verify_src"],
            "max_retries": 1,
            "threshold_f_beta": 0.5,
        },
    )
    if generate.status_code >= 400:
        _fail(f"generate returned {generate.status_code}: {generate.text[:300]}")
        return 1
    doc_id = generate.json()["doc_id"]
    _ok(f"document {doc_id} started")

    collector = StreamCollector(base_url, doc_id, token)
    collector.start()

    status = wait_for(client, base_url, doc_id, timeout=args.timeout)
    _info(f"status: {status.get('status')}")

    if status.get("status") == "awaiting_human_review":
        print("\n4. Answer the human-in-the-loop gate")
        approve = client.post(
            f"{base_url}/api/v1/workflow/{doc_id}/approve",
            json={"doc_id": doc_id, "approved": True, "feedback": "verified by script"},
        )
        if approve.status_code >= 400:
            _fail(f"approve returned {approve.status_code}: {approve.text[:200]}")
            failures += 1
        else:
            _ok("gate approved")
            status = wait_for(client, base_url, doc_id, timeout=args.timeout)
            _info(f"status: {status.get('status')}")

    print("\n5. The published event stream")
    terminal = collector.terminal()
    seen = collector.types()
    _info(f"events: {seen}")
    required = [
        "chunks",
        "perspectives",
        "outline",
        "claim_verified",
        "stream_metrics",
    ]
    for event_type in required:
        if event_type in seen:
            _ok(f"published '{event_type}'")
        else:
            _fail(f"the stream never published '{event_type}'")
            failures += 1

    # The frame the stream published must be one the vocabulary declares. A name
    # that is not declared is a frame a consumer cannot recognise, and the
    # frame it replaced is a frame nothing receives.
    declared = {event.value for event in EventType}
    undeclared = sorted({name for name in seen if name} - declared)
    if undeclared:
        _fail(f"the stream published events the vocabulary does not declare: {undeclared}")
        failures += 1
    else:
        _ok(f"every published event is one the vocabulary declares ({len(declared)} names)")

    print("\n5b. The published perspectives")
    perspective_event = next(
        (event for event in collector.events if event.get("event_type") == "perspectives"),
        None,
    )
    if perspective_event is None:
        _fail("the stream never published a perspectives frame")
        failures += 1
    else:
        personas = (perspective_event.get("data") or {}).get("perspectives") or []
        if not personas:
            _fail("the perspectives frame carried no personas")
            failures += 1
        else:
            blob = json.dumps(personas).lower()
            # The personas must speak about the corpus, not about the topic
            # alone and not about the identifiers of the passages.
            corpus_terms = [
                "composting",
                "participation",
                "recycling",
                "population",
                "household",
            ]
            grounded = [term for term in corpus_terms if term in blob]
            if grounded:
                _ok(f"the personas are grounded in the corpus ({', '.join(grounded)})")
            else:
                _fail("the personas never mention the vocabulary of the corpus")
                failures += 1
            if re.search(r"\b[0-9a-f]{64}\b", blob):
                _fail("a chunk digest leaked into the personas")
                failures += 1
            else:
                _ok("no passage identifier leaked into the personas")

    if terminal is not None and terminal.get("event_type") == "workflow_complete":
        _ok("the run published a terminal workflow_complete frame")
    else:
        _fail("the run never published workflow_complete")
        failures += 1

    print("\n6. The finished manuscript")
    document = client.get(f"{base_url}/api/v1/documents/{doc_id}")
    if document.status_code != 200:
        _fail(f"document returned {document.status_code}")
        failures += 1
    else:
        doc = document.json()
        sections = doc.get("sections") or []
        card = doc.get("scorecard") or {}
        _ok(f"the document exposes {len(sections)} section(s)")
        if card:
            _ok(
                f"scorecard: f_beta={card.get('f_beta')} "
                f"supported={card.get('supported_claims')}/{card.get('total_claims')} "
                f"verified={doc.get('verified')}"
            )
        else:
            _fail("the document carries no scorecard")
            failures += 1
        for section in sections[:3]:
            content = (section.get("content") or "").strip()
            _info(f"section {section.get('title')!r}: {len(content)} chars, "
                  f"verified={section.get('is_verified')}")

    print("\n7. Export to Word")
    export = client.get(f"{base_url}/api/v1/documents/{doc_id}/export.docx")
    if export.status_code != 200:
        _fail(f"export returned {export.status_code}: {export.text[:200]}")
        failures += 1
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(export.content)) as package:
                names = set(package.namelist())
                if "word/document.xml" in names:
                    _ok(f"exported a valid .docx ({len(export.content)} bytes)")
                else:
                    _fail("the exported package has no word/document.xml")
                    failures += 1
        except zipfile.BadZipFile:
            _fail("the export is not a valid zip package")
            failures += 1

    print()
    if failures:
        _fail(f"{failures} check(s) failed")
        return 1
    _ok("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
