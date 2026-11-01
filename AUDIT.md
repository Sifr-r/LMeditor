# Codebase audit — 2026-10-02

> **Superseded in part — 2026-10-25.** The Flutter/Dart desktop client audited below was **removed**; the Kotlin tier (`kotlin/`) was kept and now holds every deterministic rule **that crosses the seam**. The rules the seam does not serve — the deliberate Python twins, the two rules that have no Kotlin counterpart, and the model-facing half — are named in `ARCHITECTURE.md` §18.2.1. Findings that concern the client are history, and every path under `client/` no longer exists. The backend findings and their remediation stand. See `ARCHITECTURE.md` §19 for the removal and what replaced the client's guard.

## Audit blueprint

This audit reviews the existing Python backend and Flutter desktop client. Application code remains unchanged.

| Scope | Review responsibility |
| --- | --- |
| `backend/src/lmeditor/server`, `ingestion` | Request validation, source ingestion, session lifecycle, transport and security boundaries. |
| `backend/src/lmeditor/core`, `provenance`, `discovery`, `drafting`, `verification`, `workflow` | Domain correctness, citation integrity, graph execution, error handling and unnecessary complexity. |
| `client/lib`, `client/test` | API compatibility, stream handling, UI state, source workflow and client validation. |
| Dependency manifests and existing tests | Available verification, dependency usage and test coverage. |

## Deliverables

| File | Single responsibility |
| --- | --- |
| `AUDIT.md` | Record verified audit findings, evidence, priorities and verification limits, plus the 2026-10-03 remediation outcome. |
| `ARCHITECTURE.md` | Maintain the existing module ledger, including modules missing from its current registry. |
| `AUDIT_REMEDIATION_CONTRACT.md` | Binding interface contract (SSE envelope, citation grammar, verification gate, HITL lifecycle, trust boundary, client field map) that the P1 remediation was implemented against. |

## Assessment

The live generation path has production blockers: the client/backend contract is disconnected, generated citations fail extraction, and failed grounding does not actually pause for human review. Source ingestion also grants API callers filesystem and network access without confinement. These findings concern implemented behavior, not proposed roadmap features.

P1 means a core-function or trust-boundary failure requiring attention before production use. P2 means a consequential reliability or correctness defect. File-access impact is bounded by the backend process's permissions; network exposure depends on how the API is deployed.

## Verified findings

### 1. P1 — Uploads can escape their storage directory

Evidence: [routes_helpers.py:27](D:/LMeditor/backend/src/lmeditor/server/routes_helpers.py:27). Both `source_id` and `file.filename` participate directly in path construction, followed by directory creation and `write_bytes`. Relative traversal escapes the upload root; an absolute filename discards it entirely. Pure path calculations reproduced both cases without writing files.

Fix: generate storage paths internally, validate identifiers and filename basenames, and prove resolved containment before writing. Add a path-traversal rejection check.

### 2. P1 — Ingestion exposes arbitrary readable server files

Evidence: [routes.py:165](D:/LMeditor/backend/src/lmeditor/server/routes.py:165), [loaders.py:36](D:/LMeditor/backend/src/lmeditor/ingestion/loaders.py:36). Multipart ingestion accepts `kind=text` and a filesystem `origin` without an upload. An offline ASGI request pointing to the project's backend README returned HTTP 202 and its contents in chunks.

Fix: require uploads for filesystem kinds and permit only internally stored paths. Keep direct local-file ingestion separate from the public HTTP boundary.

### 3. P1 — URL ingestion permits server-side requests to internal hosts

Evidence: [loaders.py:226](D:/LMeditor/backend/src/lmeditor/ingestion/loaders.py:226). The loader fetches arbitrary input with automatic redirects. A mocked request to `http://127.0.0.1:8080/internal` confirmed that ingestion fetches and returns the response content.

Fix: validate HTTP(S) schemes, resolved destination addresses, and every redirect. Reject private/internal destinations at the API boundary and enforce the same policy during fetching.

### 4. P1 — Live generation calls an absent backend endpoint

Evidence: [api_client.dart:686](D:/LMeditor/client/lib/core/api_client.dart:686), [routes.py:103](D:/LMeditor/backend/src/lmeditor/server/routes.py:103). Disabling demo mode and synthesizing calls `/api/v1/draft/stream`. The backend OpenAPI schema exposes `/documents/generate` followed by `/stream/{doc_id}` instead. Live synthesis therefore receives 404. Client outline, chunk, and feedback methods also target absent routes.

Fix: submit topic and source chunks to the existing generation endpoint, then connect to its returned stream URL. Align ancillary methods with actual endpoints. Add one real API-contract check.

### 5. P1 — SSE event envelopes and terminal names disagree

Evidence: [api_client.dart:363](D:/LMeditor/client/lib/core/api_client.dart:363), [routes.py:251](D:/LMeditor/backend/src/lmeditor/server/routes.py:251), [session.py:37](D:/LMeditor/backend/src/lmeditor/server/session.py:37). Dart reads flat payload fields and recognizes `done`/`completed` and `error`; backend events wrap content in `data` and use `workflow_complete`/`workflow_error`. Correcting the URL alone will not make the stream work. The worker also does not publish intermediate node/token/claim updates through the supplied session.

Fix: adopt one event contract, decode the envelope and actual terminal names, and publish the events required by the client. Check a complete generation-to-client event sequence.

### 6. P1 — Generated citation markers are rejected by verification

Evidence: [claim_extractor.py:40](D:/LMeditor/backend/src/lmeditor/verification/claim_extractor.py:40), [nodes.py:153](D:/LMeditor/backend/src/lmeditor/workflow/nodes.py:153). The drafter emits `[#chunk_id]`; extraction excludes `#`. A local check returned no citations for `A supported fact [#abc123].`, but one for the equivalent `[abc123]`. Correctly cited drafts become ungrounded and enter revision loops.

Fix: use a shared citation grammar supporting the emitted syntax. Check a drafter-output-to-verifier round trip.

### 7. P1 — Failed grounding completes without human approval

Evidence: [graph.py:63](D:/LMeditor/backend/src/lmeditor/workflow/graph.py:63), [nodes.py:293](D:/LMeditor/backend/src/lmeditor/workflow/nodes.py:293), [routes.py:96](D:/LMeditor/backend/src/lmeditor/server/routes.py:96). The HITL node sets flags, then immediately routes to END because approval defaults false. `run_workflow` never uses its session argument, and the worker unconditionally reports completion.

An injected failing graph returned `awaiting_human_review` and `requires_hitl=True`; the worker nevertheless ended with `is_completed=True`, `hitl_pending=False`, and no error. Independently invoking the HITL node and router returned `terminate`.

Fix: suspend and resume on an actual review decision; report pending review while suspended and complete only when the workflow reaches its completed state. Check rejection and approval end to end.

### 8. P1 — Neutral claims can be certified as verified

Evidence: [citation_checker.py:189](D:/LMeditor/backend/src/lmeditor/verification/citation_checker.py:189), [nodes.py:211](D:/LMeditor/backend/src/lmeditor/workflow/nodes.py:211). Verification excludes contradictions and uncited claims but does not exclude neutral, unsupported claims. A deterministic evaluator producing nine entailed claims and one neutral claim yielded `F_beta=0.9` and `VERIFIED=True`. The node separately uses 0.80 while the checker uses 0.85.

Fix: require zero neutral claims for the stated all-claims-supported guarantee and use one consistent threshold. Check the mixed entailed/neutral case.

### 9. P1 — Polishing can invalidate previously verified text

Evidence: [modules.py:123](D:/LMeditor/backend/src/lmeditor/drafting/modules.py:123), [nodes.py:285](D:/LMeditor/backend/src/lmeditor/workflow/nodes.py:285). The final LLM rewrite only preserves the set of existing citation IDs; it does not preserve assertions, placements, repeated citations, or exclude new phantom IDs. An injected LM changing a mortality claim to an unsupported cancer-cure assertion while preserving its marker and adding a phantom marker was accepted.

Fix: preserve verified prose, or re-extract and re-audit the polished manuscript before completion. Check altered claims with unchanged citation IDs.

### 10. P1 — Claim/provenance parsing can crash the verification panel

Evidence: [claim.dart:61](D:/LMeditor/client/lib/core/models/claim.dart:61), [verification_panel.dart:88](D:/LMeditor/client/lib/features/verification/verification_panel.dart:88), [citation.dart:96](D:/LMeditor/client/lib/core/models/citation.dart:96). Backend claims use `cited_chunk_ids`; Dart reads `citation_ids` and unrelated premise fields. Valid backend claims lose their references. The panel always constructs lineage for the selected claim, and its constructor rejects the resulting empty IDs. Genuinely uncited claims trigger the same failure. Backend lineage field names also differ from client parsing.

Fix: distinguish chunk references from citation IDs, map actual backend fields, and render uncited claims without constructing lineage. Check backend JSON round trips and an uncited-claim widget.

### 11. P1 — Unknown citations display fabricated verification

Evidence: [editor_view.dart:604](D:/LMeditor/client/lib/features/editor/editor_view.dart:604). A citation without a matching claim defaults to entailment at 96% confidence. This occurs before verification arrives, when an event is lost, or for a nonexistent source marker.

Fix: render pending/unverified status without fabricated confidence until a real result is available. Prefer exact citation-ID matching over the first claim sharing a chunk.

### 12. P2 — Retry and verification options are ignored

Evidence: [graph.py:82](D:/LMeditor/backend/src/lmeditor/workflow/graph.py:82), [edges.py:10](D:/LMeditor/backend/src/lmeditor/workflow/edges.py:10). The API supplies `max_retries` and `threshold_f_beta`, but neither enters workflow state or governs decisions. A run with `max_retries=1` still reached three retries.

Fix: propagate these values through state and consume them in routing and the shared verification gate.

### 13. P2 — SSE reconnects lose events or wait indefinitely

Evidence: [routes.py:244](D:/LMeditor/backend/src/lmeditor/server/routes.py:244), [session.py:44](D:/LMeditor/backend/src/lmeditor/server/session.py:44). Every subscriber consumes the same queue, including its single terminal sentinel. Concurrent streams divide events; reconnecting after completion waits on an empty queue. An in-memory reproduction drained completion and the sentinel, leaving no terminal state for the next stream.

Fix: provide subscriber-specific delivery and a bounded replay/snapshot strategy; completed sessions must close new streams deterministically. Also reject duplicate active `doc_id` values: `SessionManager.create_session` currently replaces a running session while its original worker continues.

### 14. P2 — Bibliography resolvers mishandle real response structures

Evidence: [resolvers.py:210](D:/LMeditor/backend/src/lmeditor/ingestion/resolvers.py:210), [resolvers.py:75](D:/LMeditor/backend/src/lmeditor/ingestion/resolvers.py:75). PubMed selects the first `result` entry, which can be the `uids` list; a representative payload raised `AttributeError` rather than a domain error. The arXiv resolver fetches Atom XML through an unconditional JSON decoder despite its own comment acknowledging XML. A mocked Atom response yielded no bibliography.

Fix: select and validate the article UID explicitly. Parse Atom with `xml.etree.ElementTree` in the arXiv resolver and validate metadata before constructing records. Replace synthetic JSON-only fixtures with representative transport responses.

### 15. P2 — Ingestion blocks unrelated API requests

Evidence: [routes.py:165](D:/LMeditor/backend/src/lmeditor/server/routes.py:165), [routes.py:198](D:/LMeditor/backend/src/lmeditor/server/routes.py:198). Async endpoints call synchronous disk/parser/network ingestion directly, including 30-second URL requests and bibliography requests. A slow source blocks status, SSE, health and approval processing in the same event-loop worker.

Fix: offload the existing ingestion call with `await asyncio.to_thread(...)`. No new service abstraction is needed.

### 16. P2 — Client stream errors disappear and repeated runs append duplicate output

Evidence: [app_providers.dart:261](D:/LMeditor/client/lib/state/app_providers.dart:261), [app_providers.dart:279](D:/LMeditor/client/lib/state/app_providers.dart:279). Typed error events are unhandled, and subscription errors discard their messages. Each generation starts without resetting manuscript/claims, then appends text and claim rows. Running demo synthesis twice duplicates output; toggling streaming restarts rather than resumes it.

Fix: display actionable error state, stop on terminal errors, and reset output for a new run or explicitly distinguish continuation.

### 17. P2 — Source-upload UI is disconnected from generation

Evidence: [source_panel.dart:72](D:/LMeditor/client/lib/features/sources/source_panel.dart:72), [main.dart:295](D:/LMeditor/client/lib/main.dart:295). `SourcePanel` has no caller in the active UI. Its successful upload path stores the source metadata but discards returned chunks. Users cannot reach the new ingestion workflow, and mounting the panel alone would still leave generation without uploaded evidence.

Fix: mount the existing panel and retain its chunks in the existing chunk state used by generation.

## Additional verification concern

[nli_evaluator.py:174](D:/LMeditor/backend/src/lmeditor/verification/nli_evaluator.py:174) falls back to lexical matching when model evaluation fails. An offline check labeled a 90% mortality-reduction assertion entailed against a 20% source at 0.75 confidence. Treat this as degraded/unverified evaluation rather than a substitute guarantee of semantic entailment. Model-based quality was not evaluated in this audit.

## Complexity audit

These are potential removals, not applied changes. Searches found no runtime imports/usages; removing a direct dependency does not guarantee removal of any transitive installation.

- `delete:` unused `llama-index` direct dependency; the implemented indexer uses project code. [pyproject.toml:15](D:/LMeditor/backend/pyproject.toml:15).
- `delete:` unused `markdownify` direct dependency; the Markdown loader uses `markdown`. [pyproject.toml:23](D:/LMeditor/backend/pyproject.toml:23).
- `delete:` unused `intl` direct dependency. [pubspec.yaml:39](D:/LMeditor/client/pubspec.yaml:39).
- `delete:` unused `cupertino_icons` direct dependency. [pubspec.yaml:36](D:/LMeditor/client/pubspec.yaml:36).

net: -4 manifest lines, -4 direct deps possible.

The tested retrieval/dialogue modules are currently outside the workflow path. They are integration gaps, not safely proven dead features; this audit does not recommend deleting them based solely on missing production callers.

## Verification and limits

| Check | Result |
| --- | --- |
| Existing backend suite: `.venv/Scripts/python.exe -m pytest -q` | 100 passed, 1 skipped, 2 fixture setup errors. Errors arose from denied access to the external pytest temporary directory; cache writes also warned. Locked directories were not modified or bypassed. |
| Backend strict typing: `.venv/Scripts/python.exe -m mypy src/lmeditor` | Failed: missing `markdown` typing stubs at `ingestion/loaders.py:79`; 36 source files checked. |
| Flutter `analyze --no-pub` and `test --no-pub` through RTK | Neither produced output/results; both invocations were cancelled. Client compilation and tests remain unverified. |
| Offline targeted reproductions | Backend OpenAPI inventory, citation extraction, HITL routing/worker completion, neutral verification, polishing bypass, ignored retry count, local-file API read, path calculations, mocked internal URL fetching, representative bibliography payloads, and queue/session lifecycle. |
| Scope | Authored Python/Dart modules, manifests, existing tests, architecture and platform configuration inventory. No live provider calls, model downloads, dependency installations, native release builds, load tests, or external network requests. |

> **Correction (2026-10-03).** The Flutter row above was wrong: the toolchain was unavailable through the RTK wrapper used, not broken. Re-run directly on `PATH`, `flutter analyze --no-pub` reports `No issues found!` and `flutter test --no-pub` passes 61 tests. The client was therefore verifiable during this audit and is now verified. The backend rows stand as originally recorded for 2026-10-02.

Existing backend tests do not exercise the canonical generated citation syntax through verification or the complete graph-to-session review lifecycle. Client tests cover models, upload mocks and initial layout, but omit generation/controller/SSE integration. Passing unit tests therefore do not establish a functioning live writing flow.

*As originally scoped (2026-10-02), application source files were not changed. `AUDIT.md` records this audit; `ARCHITECTURE.md` documents missing modules and corrects the ledger's production-status assumptions. The P1 findings were subsequently remediated on 2026-10-03 — see the remediation pass below.*

---

## Remediation pass — 2026-10-03

The eleven P1 findings (1–11) were remediated under a binding interface contract,
[AUDIT_REMEDIATION_CONTRACT.md](D:/LMeditor/AUDIT_REMEDIATION_CONTRACT.md). Findings 12–17 and
the dependency removals were deliberately left open and remain as recorded above.

### Outcome by finding

| # | P1 finding | Status | How it is now enforced |
| --- | --- | --- | --- |
| 1 | Uploads escape storage directory | Fixed | `ingestion/url_policy.py::safe_upload_path` sanitises the id and basename, then proves containment with `resolve()` + `is_relative_to()` before any write. |
| 2 | Arbitrary server file read via ingestion | Fixed | Filesystem origins are rejected at the API boundary unless produced by `persist_source_to_disk`; `POST /sources/url` requires an absolute `http(s)` URL. |
| 3 | SSRF to internal hosts | Fixed | `assert_public_url` resolves the host and rejects loopback/private/link-local/reserved/multicast; the loader follows redirects manually (max 5 hops), re-validating every hop. |
| 4 | Client/backend endpoint mismatch | Fixed | Client performs `POST /documents/generate`, then opens the server-supplied `stream_url`. All four client paths now exist on the backend. |
| 5 | SSE envelope and terminal names | Fixed | The `WorkflowEvent` envelope is canonical; the client dispatches on `event_type` and unwraps `data`. Terminals are `workflow_complete` / `workflow_error`. |
| 6 | Generated citations rejected by the verifier | Fixed | One grammar in `core/citations.py` accepts `[#id]`, `[id]`, `[cite: id]` and comma lists; drafting and verification both consume it. |
| 7 | Failed grounding completed without human approval | Fixed | `hitl_checkpoint_node` calls `interrupt()`; `resume_workflow` resumes on the retained graph. Approval completes, rejection terminates without `is_completed`. |
| 8 | Neutral claims certified verified | Fixed | Verification additionally requires `neutral_claims == 0`; one `DEFAULT_F_BETA_THRESHOLD` is shared by the checker and the workflow node. |
| 9 | Polishing invalidated verified text | Fixed | Polishing is rejected unless the claim set, citation placements and repeated-citation counts are unchanged; otherwise the verified draft is kept. |
| 10 | Unparsable claims/provenance crashed the panel | Fixed | `cited_chunk_ids` and lineage fields are mapped; an uncited claim renders with `lineage == null` instead of throwing. |
| 11 | Unknown citations displayed fabricated verification | Fixed | Exact `citationId` match first, then `citedChunkIds`; no match renders a distinct unverified state with no confidence figure. |

### Defects found and fixed during remediation

These were not in the original audit and were corrected in the same pass.

- `NLILabel.fromString` threw `ArgumentError` on `"entailed"`, which is the backend's actual spelling — every real claim hit this path.
- `tests/test_ingestion_loaders.py` asserted `follow_redirects is True`, so the suite actively pinned the SSRF behaviour finding 3 removed. The assertion encoded the vulnerability and was corrected.
- `WorkflowSession.fail()` set `is_completed = True`, contradicting the invariant documented at `server/session.py:9-11` and making `GET /workflow/{doc_id}/status` report a failed run as completed. Now `False`, matching `terminate_rejected`.
- `run_workflow` compiled a fresh `MemorySaver` per call, so an interrupt checkpoint was discarded on return and `Command(resume=...)` was unreachable. A `thread_id`-keyed graph registry and a public `resume_workflow` now make resume possible.

### Verification after remediation

| Check | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | **213 passed, 1 skipped** (baseline 2026-10-02: 100 passed + 2 fixture errors). |
| `.venv/Scripts/python.exe -m mypy src/lmeditor` | **1 error, 38 files** — the same pre-existing missing `markdown` stubs. No new typing errors. |
| `flutter analyze --no-pub` | `No issues found!` |
| `flutter test --no-pub` | **61 tests passed** |
| New regression tests | 4 new backend files (**111 tests**: ingestion security 60, citation grammar 19, HITL gate 15, stream contract 17) and 2 new client files (**26 tests**). Baseline backend was 103 collected; 103 + 111 = 214 = 213 passed + 1 skipped. |

The HITL approve path is verified against the **real** `run_workflow` and `resume_workflow`, not stubs: an approving resume re-enters the graph at `hitl_gate` and `macro_polish` with no pre-gate node re-run, and publishes exactly one terminal frame. The failure and rejection paths were mutation-checked by temporarily restoring the old behaviour and confirming the tests fail.

### Still open after this pass

- **Not exercised end to end**: no live provider call, no native release build, and no multi-subscriber SSE soak test were run. The client/server handshake is verified by tests against recorded payloads and offline ASGI transport, not against a running server.

---

## Remediation pass — 2026-10-04 (P2 findings 12–17 + performance)

Contract for this pass: **`AUDIT_REMEDIATION_CONTRACT_P2.md`**. Work was executed as
four parallel workers on disjoint file ownership, with the orchestrator verifying the
merged result.

### Findings closed

| # | Finding | Resolution |
| --- | --- | --- |
| 12 | `max_retries` / `threshold_f_beta` accepted by the API then discarded | Both now seed `HarnessState` (defaulted, so pre-existing checkpoints still load). `route_after_verification` reads `max_retries`; `verify_citations_node` builds `CitationChecker` from the state's `threshold_f_beta` and no longer re-derives the default. `max_retries=1` provably reaches the gate after one cycle. |
| 13 | One shared SSE queue split frames between subscribers and could block forever | Per-subscriber delivery with a 512-event bounded replay buffer. Terminal frames fan out to every subscriber and close the session; a subscriber joining a closed session is terminated immediately instead of waiting on a dead queue. Duplicate `doc_id` on a live session is rejected with a typed `ActiveSessionExistsError` → `409`. |
| 14 | Resolvers crashed on real provider payloads | PubMed reads `result["uids"]` and selects the requested PMID explicitly (it previously bound the `uids` **list** and raised `AttributeError`). arXiv is parsed as Atom XML via `ElementTree`; it previously JSON-decoded an XML body and could never succeed. Only arXiv takes the XML path. |
| 15 | Blocking ingestion on the event loop | `_ingest_source` and the disk writes in `persist_source_to_disk` are offloaded with `asyncio.to_thread`. A slow source no longer stalls status, SSE, health or HITL approval for other clients. |
| 16 | Client stream errors vanished; runs duplicated output | Terminal `workflow_error` and subscription errors surface a typed, rendered error. Each new generation resets manuscript text, claims and derived state, so a second run cannot inherit the first's output. |
| 17 | `SourcePanel` unreachable | Mounted in the composed UI with a widget test proving reachability. The URL-ingestion stub is now wired to the live `POST /api/v1/sources/url` endpoint. |

### Performance work (beyond the finding list)

- **Verification was fully serial.** `audit_claims` awaited one NLI call per claim, and `verify_citations_node` awaited one `audit_section` per section — wall time was the sum of every call, and the existing `evaluate_batch` was unused. Both loops are now concurrent, bounded by `asyncio.Semaphore(MAX_CONCURRENT_NLI_EVALUATIONS = 8)`. Claim ordering and all scorecard counters are bit-identical to the sequential result, which is what makes the change safe. Measured **7.84x** on a synthetic 4-section / 4-claim case.
- **Per-node-call thread pool removed.** `verify_citations_node` allocated a `ThreadPoolExecutor` per invocation and blocked on `.result()`. It is now a coroutine node; LangGraph was verified to accept coroutine `add_node` callables by test, not by assumption.
- **Unbounded cache on a process-wide singleton closed.** `ResilientNLIEvaluator._cache` is now a bounded LRU (`MAX_CACHE_ENTRIES = 4096`) on `default_nli_evaluator`, which is shared by every document and request for the process lifetime.
- **`threshold_f_beta` is provably inert.** Enumerating every claim-status composition proves that when conditions 2–4 of the four-part gate hold (no contradicted, no ungrounded, no neutral), every claim is entailed, which forces precision = recall = 1.0 and therefore F_beta = 1.0. Condition 1 can never exclude a section for any threshold ≤ 1.0, and the API caps the knob at 1.0. The option is now genuinely plumbed end to end, but it does not change any outcome. Making it bite would mean changing the gate's semantics, which is a product decision, not a performance fix — recorded rather than silently made.

### Dependencies removed

`llama-index` and `markdownify` from `backend/pyproject.toml`; `intl` and
`cupertino_icons` from `client/pubspec.yaml`, re-resolved with
`flutter pub get --offline` (confirmed: "no longer being depended on"). The
`markdownify` mention in the `ingestion/loaders.py` comment was rewritten so it no
longer explains the absence of a dependency that is no longer declared.

### Verification after remediation

| Check | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | **388 passed, 1 skipped** (0 failures) |
| `.venv/Scripts/python.exe -m pytest -q` ×3 consecutive | **309 / 309 / 309** — the newly concurrent paths are not flaky |
| `.venv/Scripts/python.exe -m mypy src/lmeditor` | **`Success: no issues found in 38 source files`** — the pre-existing `markdown` stubs error is now resolved too |
| `flutter analyze --no-pub` | `No issues found!` |
| `flutter test --no-pub` | **98 tests passed** (61 → 79 → 98) |
| New backend tests | 5 new files — `test_sse_fanout.py`, `test_workflow_options.py`, `test_verification_throughput.py`, `test_resolver_payloads.py`, `test_dns_pinning.py` |
| New client tests | `generation_controller_test.dart`, `url_ingestion_test.dart`, `hitl_review_test.dart`, plus widget coverage |

The 60 SSRF tests survived the transport-seam change **intact** — 22 test functions,
same names, same order, zero skips or xfails, nothing deleted or converted to a comment.
The only edits were moving the patch target from `httpx.get` to `httpx.Client` and
deriving the scripted peer from the pin instead of hardcoding it, so the pin stays the
only thing deciding the outcome.

Mutation checks (all reverted): restoring the fail-open branch → 2 failures; removing
the peer assertion → 8; `stream=True` → `stream=False` → 1; removing the client-side
review dispatch while keeping the state transition → 9; reverting `hitl_request` to
telemetry-only → 12; removing all four identifier-validation call sites → 43. In that
last case the 32 that still passed were exactly the byte-identity and pre-existing
payload cases, which confirms those assert *unchanged* behaviour rather than the new
validation, and that none of the 43 failures was vacuous. Disabling the DOI encoding →
7 failures, all of them the encoding cases, while the byte-identity cases still passed —
so the two properties are provably independent.

### DNS rebinding — closed for the response body, honestly scoped

`UrlSourceLoader` validated a host's addresses and then handed the **hostname** to
httpx, which resolved again. The fix: the request is now issued as a **stream**
(`httpx.Client(..., follow_redirects=False).send(request, stream=True)`), the peer
address is compared against the pinned set **while the socket is still live**, and the
body is read only after that comparison approves. An unobservable peer now **fails
closed** instead of passing silently.

That last point was the actual bug. The earlier attempt looked for the peer *after*
`httpx.get()` had already read the body and released the connection, so the lookup
always raised and the check silently passed on every request. Proven empirically
against a real loopback server rather than mocks: `httpx.get` → peer `None` (the old
hole); `stream=True` → peer observable.

**Now guaranteed:** a rebinding hop raises `SourceLoadError` with zero body bytes read,
extracted, or returned.

**Still not claimed:** the TCP/TLS handshake and the request line/headers reach the
internal host before the mismatch is noticed. Blocking *contact* needs transport-level
pinning or an egress proxy, unreachable from this call site. The limit is documented in
the class docstring rather than glossed over.

### Human review is no longer a dead end

`submitReviewDecision` was fully implemented in the API client, but no UI called it — so
after the P1 fix that correctly suspends a run at the gate, a user who hit the gate had
no way to answer it. The client now arms an explicit awaiting-review state from
`hitl_request`, renders a `ReviewGateBanner` with Approve and Reject, and dispatches the
decision. A `409` is read as a settled state ("Review unavailable", showing the
backend's own `detail`) instead of an Approve button that could only fail again; a
transport failure stays retryable. The gate leaves the awaiting state **only** on server
acknowledgement — no optimistic flip that could strand a suspended run.

### Bibliography identifiers were interpolated unvalidated

`doi`, `arxiv_id` and `pmid` reached `ingestion/resolvers.py` as bare `str | None` from
two public entry points (`server/routes.py` JSON body and multipart form) and were
f-string interpolated straight into outbound URLs. Scoped honestly: the literal prefixes
close the URL authority, so this was **not** a host-injection SSRF — but query-parameter
injection (`pmid=1&retmode=xml`), path injection (a DOI containing `/../`, `?` or `#`)
and request-line injection (CR/LF) were all possible. All three identifier types are now
validated against per-type character allow-lists before interpolation and rejected with
`BibliographyResolutionError`.

The change deliberately preserved chain fall-through: `BibliographyResolutionError`
extends `LMeditorError`, not `ValueError`, so it is not swallowed by the resolver's own
parse-error handler and still reaches `BibliographyResolverChain`, which advances to the
next resolver. Two tests drive the real production resolver ordering to prove it.

A regression was then caught and corrected: the first charset rejected legitimate legacy
DOIs — notably the Wiley SICI form
`10.1002/(SICI)1097-0258(19980815/30)17:15/16<1661::AID-SIM968>3.0.CO;2-2` — which had
worked before the hardening. Rejection was the wrong mechanism: those characters are
none of the injection vectors, they are simply characters that must be **percent-encoded**
in a URL. The DOI charset is now derived as printable US-ASCII minus an explicit
five-character reject-list (space, `?`, `#`, `%`, `\`) — deriving by subtraction rather
than enumeration keeps the allow-list and its documentation from drifting apart — and the
identifier is encoded with `urllib.parse.quote(..., safe="/")`, leaving ordinary DOIs
byte-identical to the pre-hardening f-string. The SICI DOI now resolves to
`https://api.crossref.org/works/10.1002/%28SICI%291097-0258%2819980815/30%2917%3A15/16%3C1661%3A%3AAID-SIM968%3E3.0.CO%3B2-2`
with every `/` preserved.

### Still open after this pass

- **Not exercised end to end**: no live provider call and no native release build. The
  client/server handshake is verified against recorded payloads and offline ASGI
  transport, not a running server.

