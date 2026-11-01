# AUDIT.md P2 Remediation + Performance Contract — 2026-10-04

> **Superseded in part — 2026-10-25.** The Flutter/Dart client this contract bound was **removed**; the Kotlin tier (`kotlin/`) was kept and now holds every deterministic rule **that crosses the seam**. The rules the seam does not serve are named in `ARCHITECTURE.md` §18.2.1. §8 ("Client stream + source wiring") and worker C's and D's client file ownership are **moot** — there is no client to implement them. The backend clauses — the transport and concurrency rules, the ingestion trust boundary, the resolver payload rules — are **still binding and unchanged**. The `flutter analyze` / `flutter test` baselines quoted below belonged to the client's toolchain and no longer apply. See `ARCHITECTURE.md` §19.

Binding interface contract for the P2 findings (AUDIT.md 12–17), the DNS-rebinding
window, and the measured performance work. This file is authoritative for
cross-worker signatures. A worker that needs to change a name or signature listed
here must stop and report a conflict instead of editing it.

Baseline (re-verified 2026-10-04, before this pass):
`pytest -q` → **213 passed, 1 skipped**; `mypy src/lmeditor` → 1 pre-existing error
(missing `markdown` stubs at `ingestion/loaders.py`); `flutter analyze --no-pub` →
clean; `flutter test --no-pub` → 61 passed. **These numbers must not regress.**
*(The `flutter` baselines were the client's toolchain; the client was removed on
2026-10-25 and they no longer apply. The `pytest` and `mypy` baselines stand.)*

---

## 0. Ground rules

- Strict typing is on (`[tool.mypy] strict = true`). No `Any` returns, no
  untyped defs, no bare `except:` and no silent swallow on an exposed path.
- Every state transition stays idempotent. Publishing a terminal frame twice
  remains a no-op.
- Typed, contextual domain errors only. No new bare `ValueError` escaping to the
  HTTP layer.
- Do not add new third-party dependencies. Standard library only.

## 1. Frozen public names — MUST NOT drift

These are imported across worker boundaries. Signatures are fixed:

```python
# lmeditor/ingestion/url_policy.py  (worker E)
class UnsafeSourceError(ValueError): ...
MAX_SOURCE_ID_LENGTH: int
MAX_FILENAME_LENGTH: int
def validate_source_id(source_id: str) -> str: ...
def safe_upload_path(source_id: str, filename: str, base: Path | None = None) -> Path: ...
def upload_root() -> Path: ...
def assert_public_url(url: str) -> str: ...
def iter_redirect_hosts(response: httpx.Response) -> list[str]: ...

# lmeditor/server/session.py  (worker A) — additive only
STATUS_RUNNING, STATUS_AWAITING_HUMAN_REVIEW, STATUS_COMPLETED,
STATUS_FAILED, STATUS_REJECTED
class HITLReviewNotPendingError(RuntimeError): ...
class HITLDecisionMissingError(RuntimeError): ...
class SessionManager:
    async def create_session(self, doc_id: str, topic: str) -> WorkflowSession: ...
    async def get_session(self, doc_id: str) -> WorkflowSession | None: ...
    async def remove_session(self, doc_id: str) -> None: ...
session_manager: SessionManager

# lmeditor/verification/citation_checker.py  (worker B)
DEFAULT_F_BETA_THRESHOLD: float = 0.85
class CitationChecker:
    def __init__(self, evaluator=None, beta=0.5,
                 f_beta_threshold: float = DEFAULT_F_BETA_THRESHOLD) -> None: ...
    async def audit_claims(...) -> tuple[list[Claim], VerificationScorecard]: ...
    async def audit_section(...) -> tuple[SectionDraft, VerificationScorecard]: ...

# lmeditor/workflow/edges.py  (worker B)
MAX_SECTION_RETRIES: int
def route_after_verification(state) -> Literal["all_verified","needs_refinement","hitl_required"]: ...
def route_after_hitl(state) -> Literal["proceed_to_macro","terminate"]: ...
```

Worker E may **add** exports to `url_policy` but must not alter any name above.
`lmeditor.server.routes` already imports `UnsafeSourceError`, `assert_public_url`
and `validate_source_id`; it must keep compiling against them.

## 2. Workflow option propagation (finding 12) — worker B

`HarnessState` gains two keys, both with defaults, so a checkpoint written before
this change still loads:

```python
max_retries: int        # default 3
threshold_f_beta: float # default 0.85
```

- `create_initial_harness_state` accepts `max_retries: int = 3` and
  `threshold_f_beta: float = DEFAULT_F_BETA_THRESHOLD` and seeds both.
  It must **not** import from `verification.citation_checker` (that would create a
  `state → verification` edge from a leaf module); use a literal `0.85` and note
  the coupling in a comment, or import lazily inside the function.
- `run_workflow` writes the caller's `max_retries` / `threshold_f_beta` into the
  initial state it passes to the graph. These parameters already exist on
  `run_workflow` and are already supplied by `server/routes.py` — today they are
  silently dropped.
- `route_after_verification` reads `state.get("max_retries", MAX_SECTION_RETRIES)`
  and compares against it. `MAX_SECTION_RETRIES` stays as the default constant.
- `verify_citations_node` builds `CitationChecker(f_beta_threshold=<state value>)`
  and uses that same value in its `all_passed` computation. It must stop
  referencing `DEFAULT_F_BETA_THRESHOLD` directly for the per-section decision.
- An explicit `max_retries=1` must reach the HITL gate after a single revision
  cycle. A run with `max_retries=1` that still loops three times is a failure.

## 3. Verification throughput (performance) — worker B

The measured defect: `CitationChecker.audit_claims` `await`s the NLI evaluator
once per claim in a sequential loop, and `verify_citations_node` `await`s
`audit_section` once per section in another sequential loop. Verification cost is
therefore `O(claims × sections)` serial network/CPU waits. `evaluate_batch` on
`ResilientNLIEvaluator` already exists and is unused.

Required:

- `audit_claims` issues all per-claim NLI evaluations **concurrently**, then
  assembles `evaluated_claims` and the counters in the original claim order.
  Ordering of the returned `Claim` list and of every scorecard counter must be
  bit-identical to the sequential result.
- `verify_citations_node` audits the non-VERIFIED sections **concurrently**.
- Replace the per-call `concurrent.futures.ThreadPoolExecutor` +
  `pool.submit(asyncio.run, ...).result()` block with a single event loop
  acquisition. It currently allocates a thread pool per node call and blocks the
  calling thread on `.result()`. The node may be invoked by LangGraph on a worker
  thread with no loop; handle both cases with `asyncio.get_running_loop()` +
  `run_until_complete`-style fallback, or convert the node to `async def` if
  LangGraph's `StateGraph` accepts it (verify with a test — `add_node` accepts
  coroutine functions).
- Bound the concurrency so N sections cannot open unbounded parallel model calls:
  use `asyncio.Semaphore` with a module constant
  `MAX_CONCURRENT_NLI_EVALUATIONS: int = 8`.
- `ResilientNLIEvaluator._cache` is an unbounded `dict` on a **module-level
  singleton** (`default_nli_evaluator`), shared by every document and every
  request for the process lifetime. Replace it with a bounded LRU
  (`functools.lru_cache` on a module-level helper, or an `OrderedDict` with a
  `MAX_CACHE_ENTRIES: int = 4096` bound). Add a test asserting the bound holds.

Do not change the heuristic evaluator's scoring semantics — the audit and the test
suite depend on them. This is a throughput change, not a behaviour change.

## 4. SSE fan-out and lifecycle (finding 13) — worker A

Measured defect: `WorkflowSession` has exactly one `asyncio.Queue`. Every
subscriber `get()`s from it, so N concurrent streams divide the event stream
between them and each sees roughly 1/N of the frames, including the single `None`
sentinel. A stream opened after the sentinel was consumed blocks forever on an
empty queue. `SessionManager.create_session` also silently replaces a live
session for the same `doc_id` while its original worker keeps running.

Required on `WorkflowSession`:

- Per-subscriber delivery. Add a `subscribe()` returning a subscription handle and
  an `unsubscribe()`; `publish_event` fans out to every live subscriber.
- Bounded replay: retain the last `STREAM_REPLAY_BUFFER: int = 512` events so a
  late or reconnecting subscriber sees recent history instead of an empty queue.
- Terminal handling: `complete` / `fail` / `terminate_rejected` deliver the
  terminal event then a `None` sentinel to **every** subscriber, and mark the
  session closed. A `subscribe()` on a closed session must return a handle that
  is **immediately** terminated (it yields the terminal event and the sentinel at
  once) — never one that waits on a queue nobody will write to again.
- Keep the `is_completed`/`status` invariants of the P1 pass: a failure or a
  rejection is terminal but `is_completed` stays `False`.
- `test_stream_contract.py` currently references `event_queue` (2 occurrences).
  Migrate those tests to the new subscription API; do not keep a dead
  single-consumer queue alive as a second, divergent delivery path.
- `SessionManager.create_session` must **reject** a duplicate `doc_id` that is
  still live (not terminal) with a typed error, and let the route map it to
  `409 Conflict`. It may still replace an already-terminal session.

## 5. Event-loop blocking (finding 15) — worker A

Measured defect: `ingest_source` and `ingest_url_source` are `async def` but call
the **synchronous** `_ingest_source`, which performs disk writes, PDF/DOCX
parsing and — for a URL — a network fetch with a multi-second timeout, all
directly on the event-loop thread. A slow source therefore stalls status, SSE,
health and HITL approval handling for every concurrent client.

Required:

- Offload the ingestion call with `asyncio.to_thread(...)`. The audit already
  prescribes exactly this and explicitly says no new service abstraction is
  needed.
- Offload the blocking writes in `routes_helpers.persist_source_to_disk`
  (`mkdir` + `write_bytes`) with `asyncio.to_thread` as well.
- Do **not** introduce a process-wide executor, a semaphore, or a queue. Scope is
  the two ingest endpoints plus the helper.
- Verify with a test that the loop stays responsive: while a slow synchronous
  ingest is in flight, a trivial coroutine on the same loop must still run.
  A test that merely asserts a 200 response does not prove non-blocking.

## 6. Bibliography payload handling (finding 14) — worker E

- `PubMedBibliographyResolver._parse`: esummary returns
  `{"result": {"uids": ["12345"], "12345": {...}}}`. It must take the requested
  article id explicitly: read `result["uids"]`, pick the entry matching the
  requested pmid when present, else the first uid, then look up that key. It must
  never `next(iter(result))` (which can bind to the `uids` list and raise
  `AttributeError`). If the entry is missing or is not a mapping, return `None` —
  never raise. A `result` lacking a title returns `None`.
- `ArxivBibliographyResolver`: arXiv's `export.arxiv.org` API returns **Atom XML**,
  but `_HttpHibliResolver._fetch` unconditionally JSON-decodes, which always
  fails. Parse the Atom document with `xml.etree.ElementTree`. Map
  `http://www.w3.org/2005/Atom`: `entry/title`, `entry/author/name`,
  `entry/published`, `entry/id`. Handle the Atom `totalResults` zero-entry case
  by returning `None`. Namespace handling must not crash on a missing namespace.
- Validate before constructing: no `BibliographicRecord` is built from an entry
  without a non-empty title.
- `_HttpHibliResolver._fetch` should surface an Atom/XML body to the arXiv
  resolver. The cleanest shape that does not break CrossRef/PubMed/OpenAlex:
  give the base class a `_content_type_is_xml` class flag (or an overridable
  `_parse` entry point) so only arXiv takes the XML path. Do not make every
  resolver XML-aware.
- Replace the synthetic fixtures with representative **transport** responses
  (a real esummary JSON envelope, a real Atom document) in the new test file.

## 7. DNS-rebinding window (SSRF) — worker E

Measured defect: `assert_public_url` resolves the host and checks the addresses,
but `UrlSourceLoader` then hands the *hostname* to httpx, which resolves again.
An attacker controlling DNS can answer public during validation and private
during the connect (TOCTOU rebinding).

Required:

- Pin the validated address. Add a public helper to `url_policy`, e.g.
  `resolve_public_addresses(url: str) -> list[tuple[str, int]]` returning the
  vetted `(ip, port)` pairs, and have the loader connect to one of those pinned
  addresses rather than re-resolving the name.
- Because httpx does not expose a clean "connect to this IP but present this
  Host/SNI" switch for every transport, prefer whichever is genuinely correct:
  a custom `httpx.Transport` that resolves once and reuses the vetted address, or
  — if pinning the socket is not reliably achievable — re-validate the address
  the connection actually landed on. State plainly in the code comment and in
  your report which of the two you implemented and what remains unmitigated.
- The already-fixed behaviours must not regress: manual redirect following, at
  most 5 hops, `assert_public_url` re-validated on every hop, `SourceLoadError`
  (never a bare `ValueError`) on rejection.
- The 60 existing `test_ingestion_security.py` tests must still pass unchanged.

## 8. Client stream + source wiring (findings 16, 17) — worker C

- Error events from the stream are unhandled and subscription errors discard
  their message. Add a typed error state the UI can render, stop the run on a
  terminal `workflow_error`, and surface the message instead of dropping it.
- Each generation currently appends to the previous run's manuscript and claims.
  Reset per-run output when a **new** run starts; keep an explicit, separate
  action for continuation rather than overloading the toggle.
- Mount `SourcePanel` in the active UI and make sure its chunks reach the chunk
  state that `POST /api/v1/documents/generate` sends. It already publishes into
  `chunksProvider`; the missing half is the mount.
- `flutter analyze --no-pub` must stay clean and `flutter test --no-pub` must
  pass. Add tests for the new client behaviour.

## 9. Dependency removals (worker D / orchestrator)

Delete the four direct declarations the audit proved unused:
`llama-index` and `markdownify` from `backend/pyproject.toml`; `intl` and
`cupertino_icons` from `client/pubspec.yaml`. Re-verify the suite afterwards —
`markdownify` in particular is mentioned in a code comment in
`ingestion/loaders.py`, and that comment must be corrected, not left to mislead.

## 10. Ownership map (no two workers touch the same file)

| Worker | Owns exclusively |
| --- | --- |
| A — transport & concurrency | `server/session.py`, `server/routes.py`, `server/routes_helpers.py`, `backend/tests/test_stream_contract.py`, `backend/tests/test_sse_fanout.py` (new) |
| B — workflow & verification | `workflow/state.py`, `workflow/edges.py`, `workflow/nodes.py`, `workflow/graph.py`, `verification/citation_checker.py`, `verification/nli_evaluator.py`, `backend/tests/test_workflow_options.py` (new), `backend/tests/test_verification_throughput.py` (new) |
| C — client | all of `client/lib/**`, `client/test/**` |
| E — ingestion & resolvers | `ingestion/url_policy.py`, `ingestion/loaders.py`, `ingestion/resolvers.py`, `backend/tests/test_resolver_payloads.py` (new), `backend/tests/test_dns_pinning.py` (new) |
| D — manifests | `backend/pyproject.toml`, `client/pubspec.yaml` |

Dispatch order: A, B, C, E run in parallel — they own disjoint files. D runs
independently and is verified at the end by A/B/C's suites. The orchestrator owns
`AUDIT.md`, `ARCHITECTURE.md` and this contract, and runs the final full
verification.
