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
production readiness claim, and the release findings below are untouched by
it: the API still accepts unauthenticated callers, the live SSE subscriber
queues are still unbounded, the installed-wheel deployment is still
unproven, and the desktop accessibility evidence is still missing. macOS
and Python 3.12 have not been exercised by this matrix.

## Remaining release work, in order

1. Authenticate the local API end to end, including workflow approval, desktop
   REST requests, exports and SSE connections. Origin filtering is useful but a
   caller can omit `Origin`; it does not identify the operator. Verify missing,
   invalid and valid credentials over actual HTTP, including streaming.
2. Bound live SSE subscriber memory and specify an overflow/disconnect policy.
   Add frame identity, reconnect/replay proof and cancellation before claiming
   streaming resilience.
3. Exercise the installed Python wheel with its separately supplied tier jar and
   JVM outside the source checkout. Confirm artifact contents, startup, error
   handling and persistence across restart. The source gate alone cannot prove
   deployment packaging.
4. Obtain clean Windows/Linux CI results and verify supported desktop workflows,
   keyboard and screen-reader behavior, real Word interoperability, and explicit
   filesystem support. These remain unproven until exercised.
5. Close the remaining security and durability items in ARCHITECTURE.md §29.6,
   including C1 control validation, vault defaults, dependency vulnerability
   review, persistence durability and concurrent writes. A lockfile establishes
   resolution consistency; it does not establish that dependencies are secure.

The empty-model-cache experiment also exposed a retrieval quality limitation:
the hash fallback ranked an unrelated mangrove passage ahead of the expected
monastic-library passage for a monasteries query. The semantic-quality assertion
is retained in full mode. A release must provision and verify its models, or
explicitly define the quality users can expect in the degraded mode.

The documentation generator and four auxiliary documentation checkers still
live in the ignored `.tools/` directory. The backend suite checks the tracked
requirements registry, but reproducing every check in the old handoff requires
promoting those tools into the maintained source tree in a subsequent slice.

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
