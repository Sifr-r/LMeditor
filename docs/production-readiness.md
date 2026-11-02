# Production readiness

The 2026-10-27 review establishes substantial local validation. It does not
establish production release readiness. This document tracks the first follow-up
slice: making that validation reproducible and refusing incomplete evidence.

## Run the candidate gate

Prerequisites: Python 3.13, JDK 25, uv 0.11.16, and network access for the first
dependency download. The Kotlin artifacts target Java 17; JDK 25 is the build
runtime used by this validation configuration. Run from the repository root:

```powershell
cd backend
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python scripts/verify_release.py
```

Full mode also requires Microsoft Word automation and provisioned semantic/NLI
model weights. Point `HF_HOME` and `SENTENCE_TRANSFORMERS_HOME` at provisioned
caches when appropriate. Use `--portable` for the hosted-CI scope described below.

`uv sync` reconciles the selected project environment with the lockfile. Use a
dedicated development environment; do not run it against an operator's active
service environment. The gate itself does not install dependencies.

An already provisioned environment can invoke
`python backend/scripts/verify_release.py` from the repository root. Add
`--offline` when the required uv and Gradle dependencies are already cached.
Set `UV_CACHE_DIR` and `GRADLE_USER_HOME` to reuse existing caches; defaults are
under the ignored repository `build/` directory. Each subprocess has a default
1,800-second deadline, adjustable with `--timeout SECONDS`.

The full gate checks the dependency lock, rebuilds the current Kotlin tier jar,
executes fresh Kotlin tests with the build cache disabled, checks strict Python
types, executes the complete backend suite, compares the parity witnesses, and
runs the document import/edit/export test against a real local HTTP server using
the offline model double. It does not contact a live language-model provider.
It refuses missing XML, zero tests, inconsistent counts, failures, errors and
skips. Module reports must exist for every declared Kotlin module.

A skip is refused unless `backend/platform-checks.json` declares it, by class
and by test name, for the platform the gate is running on. A test that asserts
a guarantee only one platform gives cannot run on the others, so it declares
an assumption and skips elsewhere; the declaration names exactly those. The
gate also refuses a declared skip that did not happen, so the declaration
cannot rot into a licence to skip anything. The effective declaration is
copied into the run evidence beside `external-checks.json`.

`--portable` runs the automated checks suitable for hosted CI. It explicitly
excludes the three Microsoft Word conformance modules and five exact tests requiring
real semantic/NLI model weights, recording their addresses in
`external-checks.json`. It still rejects every skip in the selected suite,
including missing tier integration evidence. A portable pass leaves those
external checks unproven and does not establish a full release pass.

Logs and JUnit reports are retained in a unique `build/release/run-*` directory.
The gate isolates temporary files and application data there, removes inherited
`LMEDITOR_*` overrides, and pins the bridge used by its Python checks to the jar
it just built. A failed run returns a nonzero exit code and identifies its log.

## Continuous integration

`.github/workflows/verify.yml` installs the locked Python development environment
and runs the gate in portable mode on Windows and Linux. It grants read-only repository
access, does not retain checkout credentials, and uploads logs and test reports
even when a check fails. It does not deploy or publish anything.

The fourth hosted run, `Sifr-r/LMeditor` pull request 2, run 41299656645,
**passed on both runners**: the clean locked install, the Kotlin build and
tests, the strict type check for both platforms, the portable backend suite,
the parity comparison and the live HTTP round trip. Windows and Linux agree
on **1,059 Kotlin tests** and **1,695 backend tests**; they differ by
exactly one skip, the Windows-only file-lock guarantee, which
`platform-checks.json` declares for Linux and the run observed. Both runs
retained their effective declaration beside `external-checks.json`.

This is a hosted pass of the candidate gate on two platforms. It is not a
production readiness claim, and it did not touch the release findings: the
API accepted unauthenticated callers at the time — the credential has since
been added, and the section below describes it — the live SSE subscriber
queues are still unbounded — the subscriber memory has since been bounded, and
the section below describes that too — the installed-wheel deployment is still
unproven, and the desktop accessibility evidence is still missing. macOS
and Python 3.12 have not been exercised by this matrix.

## The API credential — closed 2026-11-02

The first item on the list below is closed. Every route under `/api/` now
requires a bearer token, the desktop client sends it, and the contract is
`docs/api-authentication-blueprint.md`.

- `backend/src/lmeditor/server/credential.py` resolves the token, keeps it
  in the token file, and checks what a request presents. The comparison is
  constant-time, and the token is never logged, never in a response body
  and never in a URL.
- The credential middleware is the outermost one, so a request is
  **authenticated before it is authorized**: a caller that cannot prove it
  is the operator is not told whether the thing it asked for exists. A
  missing credential and a wrong one are refused identically — `401`,
  `WWW-Authenticate: Bearer realm="lmeditor"`, and
  `{"detail": "This API requires a bearer token."}` — so a caller learns
  nothing from the difference. A header's *name* is matched without regard
  to case, because HTTP says it is case-insensitive.
- The token is settled at startup: the operator's own `LMEDITOR_API_TOKEN`,
  then the token file, then a new one. Whatever it is, it is written to the
  token file, at `<home>/.lmeditor/api-token` unless
  `LMEDITOR_API_TOKEN_FILE` names another. A token that had to be
  generated and cannot be written **stops the server from starting**: a
  server that cannot hand its token to the client says so rather than
  answering 401 for ever.
- `kotlin/desktop/.../net/ApiCredential.kt` resolves the same token on the
  client and sends it on every REST request and on the event stream. A
  token that cannot be read is a typed `BackendError.Uncredentialed` naming
  the path it looked at, not a crash.

What it does not do: the token travels on a plain HTTP connection, so
anything on the path can read it. The API is still meant to be reached over
the loopback interface, or over a tunnel the operator controls. It does not
identify *which* operator presented the token — there is one operator and
one token.

## The SSE stream's backpressure and resume — closed 2026-11-02

The second item on the list below is closed. The live stream no longer keeps a
per-subscriber queue, its frames carry an identity, a reconnecting client is
served the frames it asked for and is told what it cannot have, and a dropped
stream is released. The contract is `docs/sse-backpressure-blueprint.md`, and
the ledger is ARCHITECTURE.md §33.

- **The queue was removed, not capped.** A subscriber is a position in the
  session's existing bounded history: `EventSubscription` holds an id, a
  position and a wake-up, and no frames. A subscriber that stops reading
  accumulates nothing, and the session notices when the frames it has not read
  have left the history. A hundred stalled subscribers now cost what one
  stalled subscriber costs.
- **The overflow is a disconnect with a reason.** The stream ends with an
  `overflow` control frame naming what was missed, the subscription is
  released, and the client reconnects with the id of the last frame it
  received. The reconnect is bounded to one attempt — a second overflow ends
  the stream, and the desktop client says the run stopped short rather than
  looping for ever.
- **The frames carry an identity.** Every sequence frame carries `id: <n>`,
  contiguous from 1 and strictly increasing. The two control frames carry
  none, because a control frame with an id would advance every client's
  last-event-id and silently break the resume. A client reconnects with
  `Last-Event-ID`, and a header that is not ASCII decimal digits from 1 is
  refused with a 400 rather than repaired.
- **A resume the server cannot serve completely says so.** The server serves
  what it remembers and names what it does not, in a `gap` control frame. The
  rule is the same in both directions: a request the session has rotated past
  and a request the session has not reached yet are both named.
- **The client tells its caller.** The two control frames arrive as messages of
  their own, beside the run's events, and the desktop client puts them in the
  operator's notice. A client that is told what it missed can tell a run that
  lost frames from one that did not; a client that throws the notice away
  cannot. A control frame whose payload the client cannot read is reported as
  a malformed frame, not skipped.

What it does not do: the stream is not lossless for a client that was away
longer than the session's memory. That client is told what it missed — the
notice reaches the operator — and is meant to re-fetch the document's state, and
**that re-fetch is not implemented**. The `token_delta` whole-sequence replay
is still not caught either: the backend now publishes the frame identity, but
the reducer is not given it, so the suffix test stands. macOS and Python 3.12
remain unexercised.

### Consumer notice follow-up — implemented 2026-11-02, Kotlin verification complete

The desktop retains the first confirmed permanent stream-loss warning separately
from transient notices. Documents, Vault, Notebook and Studio render notices
alongside errors. Rejected/failed starts retain that warning; an acknowledged
new run clears it. The four focused regressions run and pass:
`.\gradlew.bat :desktop:test` from `kotlin` executes 144 desktop tests with zero
failures. Frame-identity deduplication and snapshot reconciliation remain open.

## Remaining release work, in order

1. Exercise the installed Python wheel with its separately supplied tier jar and
   JVM outside the source checkout. Confirm artifact contents, startup, error
   handling and persistence across restart. The source gate alone cannot prove
   deployment packaging.
2. Obtain clean Windows/Linux CI results and verify supported desktop workflows,
   keyboard and screen-reader behavior, real Word interoperability, and explicit
   filesystem support. These remain unproven until exercised.
3. Close the remaining security and durability items in ARCHITECTURE.md §29.6,
   including C1 control validation, vault defaults, dependency vulnerability
   review, persistence durability and concurrent writes. A lockfile establishes
   resolution consistency; it does not establish that dependencies are secure.

The empty-model-cache experiment also exposed a retrieval quality limitation:
the hash fallback ranked an unrelated mangrove passage ahead of the expected
monastic-library passage for a monasteries query. The semantic-quality assertion
is retained in full mode. A release must provision and verify its models, or
explicitly define the quality users can expect in the degraded mode.

The matrix renderer and four auxiliary documentation checkers now live in
maintained `backend/scripts/`, with standard-library-only invocation documented
in ACTION_PLAN.md §6. The tooling/registry suite passes 33 focused tests and
strict typing; live checks report zero link/fragment/evidence-address,
inventory-cell, vocabulary and corpus-digest problems. The registry remains
authoritative. The old hardcoded registry builder is not promoted or invoked;
reproducible registry regeneration and semantic evidence-anchor checking remain
open A7 work. No fresh release gate is claimed by these focused checks.

The A3 follow-up also masks unfamiliar single POSIX roots in client-facing
errors while preserving explicit HTTP(S) URLs. Seventeen adversarial cases and
strict typing pass. The broader API disclosure suites run: the residual and
error-disclosure suites pass and the full backend suite passes 1,785 tests with
no failure, so the recorded Windows event-loop socketpair hang does not
reproduce on this machine. The earlier C1 and custom-style patches now have
executed Kotlin proof — `:ingest:test` and `:word:test` pass 316 tests with zero
failures; vault defaults, dependency vulnerability review and durability work
remain open. See ARCHITECTURE.md §34 and §35.

The locally recorded results describe local `main` at `f0afc63` plus the
uncommitted batch work. `origin/main` is four commits ahead and overlaps three
of the changed files, so these figures are not a result for the merged tree and
must be re-run after that divergence is reconciled.

## Evidence from this follow-up

The dependency lock was regenerated with uv's resolver. The offline lock check
passes and its project metadata matches the current 14 direct dependencies plus
the development extra. The release gate has fault-injection coverage for missing,
malformed, empty, skipped and failed evidence, subprocess failure and timeout,
and inherited configuration overrides.
The timeout regression starts a bounded parent/child process and verifies that
the child cannot keep running after the check times out.

The exact full-run outcome is recorded in ARCHITECTURE.md §30. Historical test
counts in earlier ledger entries remain records of those earlier runs.

Final portable local verification passed: 1,059 Kotlin tests, 1,689 selected
backend tests, strict typing over 134 source files, 26 parity comparisons, and
the live HTTP document round trip. There were no executed failures or skips.
The five model deselections and three excluded Word modules are recorded in
`build/release/run-d8zgldlw/external-checks.json`; logs are beside it.

The credential slice was verified the same way, on the tree it landed on:
**1,075 Kotlin tests**, **1,716 selected backend tests**, strict typing over
**135 source files** for both platforms, **26** parity comparisons and the
live HTTP document round trip, with no executed failures or skips. The
evidence is under `build/release/run-iuv5yp7p`; the full local suite
outside the portable scope is **1,740 passed, 0 failed**.

The SSE slice was verified the same way: **1,081 Kotlin tests**, **1,725
selected backend tests**, strict typing over **135 source files** for both
platforms, **26** parity comparisons and the live HTTP document round trip,
with no executed failures or skips. The evidence is under
`build/release/run-jpm08wa3`, with the full suite's own log kept beside it as
`pytest-full-suite.log`; the full local suite outside the portable scope is
**1,749 passed, 0 failed**.
