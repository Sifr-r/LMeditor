# LMeditor consolidated action plan

Review date: 2026-11-02. Scope: project documentation and historical review records in this workspace. This is a documentation review, not a fresh code audit or a new release-gate run.

## 1. Purpose and file ownership

This file is the single actionable entry point for the project's documentation.
Original contracts, evidence, and historical records are retained.

| Path | Single responsibility |
| --- | --- |
| `ACTION_PLAN.md` | Reconcile documented status, order remaining work, assign domain owners, and state measurable completion gates. |
| `ARCHITECTURE.md` | Maintain the existing architecture ledger and register this consolidated entry point. |

Use sections 3–5 to choose and execute the next slice. Section 8 contains every replacement requirement with its exact recorded gate, owner and status. Original evidence and detailed normative contracts remain linked; this file consolidates decisions and work rather than reproducing historical code listings.

The consolidation blueprint assigns one new file to this plan and one existing ledger update. Parallel reviewers cover (a) architecture, audits and historical task records, (b) replacement requirements and decisions, and (c) domain blueprints, operating instructions and contracts. No runtime files are added.

## 2. Current state and rules for resolving conflicts

The current product is a Python/FastAPI model and transport layer, a dependency-free deterministic Kotlin tier, and a Kotlin Compose desktop client. Documents, Vault and Notebook workspaces exist. The Flutter/Dart client has been removed. Deterministic rules served by the Kotlin seam belong in Kotlin; model inference, DSPy/LangGraph orchestration and embeddings remain in Python. Some intentional Python twins remain; see [ARCHITECTURE §18.2.1](ARCHITECTURE.md#1821-what-the-invariant-does-not-cover).

Apply these precedence rules:

1. Explicit user instructions and current binding contracts govern implementation. [Kotlin contracts](kotlin/CONTRACTS.md), [Word contract](WORD_PROCESSING_CONTRACT.md), [wire contract](kotlin/word/WIRE.md), and [migration decisions](docs/replacement/decisions.md) define domain obligations.
2. Later implementation records supersede older descriptions of current behavior. Read [ARCHITECTURE §§28–33](ARCHITECTURE.md) and [production readiness](docs/production-readiness.md) before reopening an older finding. A plan, error class, or source pointer alone does not prove a workflow complete.
3. [requirements.json](docs/replacement/requirements.json) supplies the exact replacement requirement IDs, statuses, owners and observable gates. Its statuses are preserved here; this review does not promote them. The roadmap supplies product scope and dependencies. `blocks` means downstream requirements the row blocks, not prerequisites of the row.
4. Historical measurements apply to the recorded tree and environment. A local portable pass, a full local pass and an older hosted pass are different evidence. None alone certifies production readiness.
5. If a source disagrees with current code, verify the affected behavior before implementation and update its evidence, the registry, its rendered matrix and this plan together. Preserve stable requirement and contract rule IDs.

| Conflicting or stale statement | Reconciled disposition |
| --- | --- |
| Old audit findings 1–17 and remediation task lists | Recorded closed in architecture §§6–7; retain regression gates rather than implement again. |
| September gap proposals for Flutter, SQLAlchemy, new Python Word/export architecture | Historical options; use the current Kotlin/Compose boundaries and approved roadmap. |
| CODEBASE_REPORT claims no usable model or fabricated runtime fallback | Later implementation records supersede these claims; do not reintroduce invented evidence or claim missing model support without verification. |
| Old ledger says no CI or no remote | Hosted Windows/Linux runs are recorded in §31; `.github/workflows/verify.yml` exists. Historical repository state is not current state. |
| Roadmap R.08 prose says API credential is absent | R.08 is complete; bearer authentication and client credential handling are recorded in §32. |
| Workflow matrix says 36 in-progress / zero complete | Resolved: the matrix is rendered from the registry by `backend/scripts/build_replacement_matrix.py`, which reports 35 `in-progress`, one `evidence-partial` and one `complete` (R.08). `--check` refuses drift without writing. Registry statuses are unchanged. |
| Plans describe unbounded SSE queues, missing IDs and whole-buffer resend | Those transport defects are closed in §33. State refetch and reducer identity are still open. |
| Migration contract says three storage errors do not exist and schema versions are absent everywhere | Decisions §4 and source-persistence records supersede these assertions: error vocabulary and versioned source records exist; cross-store recovery and revision behavior remain pending. |
| Word parity or preserved OOXML implies a full Word replacement | Package retention and supported round trips do not establish editable advanced features, screen-reader support or Word-identical layout. |

### Delivered baseline to preserve

- Kotlin tier, bridge and Compose client; Documents/Vault/Notebook surfaces; Codex-backed generation provider.
- Grounding/claim identity repairs, cached corpus retrieval, bounded decompression, safer outbound requests and confined vault/notebook roots, as recorded in §29.
- Persistent extracted-source records and typed storage-error vocabulary. Original source bytes, revisions and full recovery are not thereby complete.
- Reproducible release gate, locked Python environment and recorded hosted Windows/Linux portable passes.
- API bearer authentication on `/api/`, including approval routes, plus desktop REST/SSE credentials. Single-operator loopback/tunnel usage remains the documented trust model.
- Bounded shared SSE history with cursor subscribers, increasing frame IDs, exact retained replay, gap/overflow notices, cancellation cleanup and one reconnect attempt.

## 3. Execute next: ordered action queue

### Quick-fix batch — 2 November 2026

- Matrix drift reconciled: all 83 rows now match the registry, which retains 46 `not-started`, 35 `in-progress`, one `evidence-partial` and one `complete` (R.08). Two new regressions check rendered fields and totals; the focused requirement suite passes (16 tests). Registry statuses are unchanged.
- A3 C1 validation patch implemented: ingestion source IDs, upload filenames and URLs reject U+0080–U+009F through the existing contextual error. Added exhaustive C1 boundary cases and printable-Unicode preservation checks. Inspected vault, notebook and source-archive validators already reject C1.
- A3 custom Word style patch implemented: export omits a self-referential inheritance edge and reports it in writer omissions. Added custom-style regressions for reporting, deterministic repeat writes and valid inheritance preservation.
- Kotlin verification is complete: the wrapper's external cache lock under `C:\Users\rahin\.gradle` is released, and no cache redirection or permission bypass was needed. `.\gradlew.bat :ingest:test :word:test` from `kotlin` builds successfully and runs 316 tests (105 `:ingest`, 211 `:word`) with zero failures, including the new C1 boundary and custom-style regressions. The revision and the divergence that qualifies this evidence are recorded in the verification run below.

The [batch blueprint](docs/quick-fixes-blueprint.md) records file ownership and stop conditions; [architecture §34](ARCHITECTURE.md#34-action-plan-quick-fix-batch--2026-11-02) records scope and evidence. A3 and A7 remain open for their remaining gates.

### Medium-priority batch — 2 November 2026

This batch selects bounded follow-ups from A3, A5 and A7; the queue does not assign explicit severity labels. The [blueprint](docs/medium-fixes-blueprint.md) fixes ownership and acceptance gates.

- A3: error sanitization masks unfamiliar single POSIX roots as well as nested paths, preserves explicit HTTP(S) URLs (including IPv6), and retains existing file-URL/filesystem redaction. Seventeen focused adversarial cases pass; strict typing passes for the changed helper. The broader API disclosure suites now run: `test_disclosure_residuals.py` passes all 32 cases and `test_error_disclosure.py` passes, so the recorded Windows event-loop socketpair hang does not reproduce on this machine. No workaround was needed and none was added.
- A5: desktop state retains the first confirmed permanent stream-loss warning, independently of transient notices and errors. All four workspace strips render notices alongside errors. Failed/rejected start requests retain the warning; an acknowledged new run clears it. The four added regressions now run and pass — `.\gradlew.bat :desktop:test` executes 144 desktop tests with zero failures. Frame identity and authoritative snapshot reconciliation remain open.
- A7: the matrix renderer and four documentation checkers now live in maintained `backend/scripts/`, use only the Python standard library, and have 17 regression cases. The combined tooling/registry suite passes 33 tests, strict typing passes, and the live checks report no link/evidence-address, inventory, vocabulary or corpus-digest problems. Generation preserves all registry corrections and is deterministic; `--check` refuses drift without writing.

No registry statuses changed. Registry regeneration from maintained canonical source and semantic evidence-anchor validation remain future A7 work; a valid file:line address alone does not establish that its contents prove a claim. See [architecture §35](ARCHITECTURE.md#35-medium-priority-action-plan-batch--2026-11-02) for scoped evidence and remaining gates.

### Verification run — 2 November 2026

The Gradle wrapper lock that blocked both batches is released, and each batch's own gate was re-run from `D:\LMeditor`. This is a scoped re-run of those gates. It is not the release gate, an installed-deployment proof or a performance measurement, and it does not change any recorded status.

| Gate | Result |
| --- | --- |
| `.\gradlew.bat :ingest:test :word:test :desktop:test` (from `kotlin`) | BUILD SUCCESSFUL — 460 tests, zero failures (105 `:ingest`, 211 `:word`, 144 `:desktop`) |
| `backend` full suite (`pytest`) | 1,785 passed, 0 failed |
| `backend/tests/server/test_disclosure_residuals.py`, `backend/tests/server/test_error_disclosure.py` | both pass; no hang |
| `backend/tests/test_replacement_doc_tools.py`, `backend/tests/test_replacement_requirements.py` | 33 passed (17 + 16) |
| `python backend/scripts/build_replacement_matrix.py --check` | matches the registry |
| `python backend/scripts/check_replacement_links.py` | 0 problems |
| `python backend/scripts/check_inventory_cells.py` | 0 problems |
| `python backend/scripts/check_compatibility_vocabulary.py` | 0 problems |
| `python backend/scripts/verify_corpus_digests.py` | 0 problems |

The run used local `main` at `f0afc63` plus the uncommitted batch work, on Windows 11 with JDK 25.0.3, Gradle 9.8.2 and Python 3.13.9. `origin/main` is four commits ahead and overlaps three of the changed files, so this evidence describes the local tree rather than the merged one; the divergence is recorded below. §6's recorded `build/release/run-jpm08wa3` evidence is unchanged and still describes a different tree.

The order below follows the production-readiness handoff. Independent evidence and design work can proceed in parallel, but shared schemas and wire changes must be settled before dependent implementation. Owners are domains, not staffing commitments. Completion means retained evidence satisfies the gate, not that code was written.

| Order / work item | Owner and dependency | Concrete work and completion gate |
| --- | --- | --- |
| A1 — Installed deployment proof | Delivery + Python bridge; first release follow-up | Build and install the wheel outside the checkout with an explicitly supplied tier JAR and JVM. Start the API and desktop, ingest/import, edit, export, restart and read persisted data. Verify packaged contents and `_reference` exclusion; deliberately remove/misconfigure JVM, JAR and storage permissions and retain contextual errors. Save clean-machine instructions and logs. Feeds F.09/R.12; does not alone complete either broad gate. |
| A2 — Current candidate evidence | Delivery + independent QA; after A1 packaging is concrete | Run the current locked portable gate on Windows/Linux and full mode with provisioned models and Microsoft Word. Retain reports, exact source revision, environment and exclusions. Confirm desktop workflows, keyboard/focus/screen-reader behavior and declared filesystem support. Older hosted counts cannot close the current candidate. Publish whether macOS and Python 3.12 are supported or still unexercised. Feeds R.01–R.04/R.12. |
| A3 — Security boundary follow-up | Kotlin core/ingest/server + Python transport | Reject C1 controls including U+0085 at applicable identifier/path boundaries; add boundary regressions. Review vault base defaults and explicit cross-drive configuration, path redaction for POSIX roots outside the known list, user-supplied self-referential Word styles, and dependency vulnerabilities. Verify refusals/redaction with adversarial fixtures; record scoped fixes and dependency decisions. Do not reopen delivered API authentication. |
| A4 — Durable shared foundation | Kotlin core/server/stores + desktop; settle shared contracts first | Implement F.01–F.06 under the recovery rules in section 4: stable workspace/artifact identity, complete original-source retention, acknowledged durable writes, revision preconditions, conflicts, recovery, backups and jobs. In parallel, finish F.07–F.10 navigation, provider-free operation, installed lifecycle and accessibility. Use process-death, failed-write, concurrent-edit and restore tests; retain previous good data on every refused write. Current atomic replacement is not a power-loss or multi-writer guarantee. |
| A5 — Complete SSE consumer state | Desktop reducer/controller + server API; agree identity and snapshot contract first | Carry frame identity into reducer state so a whole-sequence replay cannot append twice and identical legitimate deltas are not mistaken for replay. On `gap`, fetch/reconcile authoritative document state. Preserve permanent-loss notices when later overflow notices or errors arrive; all four screens currently let errors mask notices. Test resumed, rotated-past, ahead-of-session and repeated-identical-text scenarios, cancellation and bounded reconnect. Requires a deliberate contract amendment if the public seam changes. |
| A6 — Parity and contract coverage | Kotlin owners + Python bridge | Compare the 13 declared-but-uncompared frozen witnesses and pin the section-split twin with a shared corpus or parity harness. Each added comparison must demonstrably fail under a relevant mismatch. Resolve `text.retrieval.rank` contract needs explicitly before changing its wire behavior. Track, then remove the core→tier import cycle without changing behavior. |
| A7 — Documentation/tool reproducibility | Delivery/docs + registry owner | Promote required generators and four auxiliary documentation checkers from ignored `.tools/` into maintained source, with reproducible invocation and dependency declaration. A clean checkout must regenerate the registry/matrix without reverting manual corrections and check links, evidence anchors, corpus digests and inventory vocabulary. Do not make ignored helper scripts a release prerequisite. |
| A8 — Measured performance work | Kotlin text/PDF/Word + Python provenance | First retain repeatable baselines for CMap re-inflation, quadratic chunking, chunk hashing, Word reader, seam amplification and per-query embedding payload. Then optimize measured bottlenecks behind existing contracts and parity tests. Demonstrate unchanged results and measured improvement on representative small/medium/large corpora. R.05/R.06 require their own p95 and memory gates. |
| A9 — Daily product replacement | Separate document, vault and notebook owners; after M1 interfaces | Execute M2 W1/W2, O1, N1 and integrated workflow requirements in section 8. Verify real fixtures end-to-end, including restart and missing-source behavior. Do not treat existing workspace shells or source pointers as completion. |
| A10 — Broad workflows and qualification | Domain owners + delivery; after relevant M2 gates | Execute M3 review/visual/study/media/sync work, then M4 accessibility, compatibility, performance, secrets, diagnostics and installation gates. Preserve explicit unsupported-feature warnings and publish compatibility exceptions. |

### Known technical debt to measure, not blindly optimize

Architecture §29.6 records CMap allocation around 19.81 MB/page, quadratic chunking (historical exponents 1.94 Python / 2.03 Kotlin), 5.6× seam response amplification, vector payloads around 820 KB/100 chunks and 40.6 MB/5,000 chunks, 188× chunk-ID cost and a Word reader 1.9–4.2× slower than python-docx. These are historical observations, not newly verified baselines. The 4 MB sliding-window benchmark exceeded the earlier 300-second budget. Separate local interaction latency from inference latency.

The old ruff baseline carried 479 findings; do not announce a clean lint gate without establishing one. Hash-only retrieval fallback has a documented relevance failure; provision semantic/NLI weights for full quality validation or publish an explicit degraded-quality policy.

### Resolve these documentation and evidence discrepancies during A2/A7

| Issue | Required resolution / observable check |
| --- | --- |
| Recovery contract R5 wording restores revision n, while MRC-6.10 creates a new revision | Specify restoring old content as a new revision; preserve monotonic history and test the revision API. |
| MRC-3.14 directory flush ordering precedes replacement | Review platform/filesystem semantics and specify post-replacement durability where needed before promising power-loss survival. |
| MRC-4.10 rollback assumes a missing pre-operation copy exists | Define safe halted/unresolved recovery when required bytes are unavailable; never fabricate a successful rollback. |
| W2.08 says not-started although a feature inventory exists | Reconcile the exact gate and evidence before any status promotion. The existing inventory alone is not full workflow proof. |
| Feature inventory and recovery contract disagree about vault atomicity | Inspect current store implementation and fault-injection evidence; publish the actual supported guarantee. |
| Wire README requires Python 3.13 while package metadata supports >=3.12 | Distinguish supported minimum from tested configuration. Validate 3.12 or explicitly amend support policy; do not silently infer coverage. |
| Bridge README mentions LlamaIndex, path-based `word.read`, prefixed operation names and memory-only sources | Align with actual domain.operation names, `bytes_b64` wire input and archive-backed source behavior. Use the binding wire contract, not the obsolete example. |
| Backend README uses ignored local Gradle installation | Use tracked `kotlin/gradlew.bat` / `gradlew`; verify clean-checkout instructions. |
| Desktop README promises complete replay idempotency | Document the whole-sequence token exception until A5 passes. Preserve the mixed text/image paragraph replacement limitation in Documents claims. |
| API token wording says verbatim in one source | Align with the credential contract's surrounding-whitespace trimming and exact regression behavior. |
| Source-ingestion OpenAPI summary still says file upload or URL | Correct multipart route summary to distinguish the separate URL endpoint; verify generated API documentation. |
| SSE follow-up uses attempt count as a time budget | Upstream `d062d63` replaces `HITL_WAIT_ATTEMPTS = 6000` with a 60-second monotonic deadline (`HITL_WAIT_TIMEOUT`). The local working tree still carries the count-based budget because it has not taken `origin/main`; adopt the upstream file rather than re-deriving it. The ledger's half-minute claim stays wrong either way and must be corrected to the deadline's actual wall clock. |
| Scratch SSE progress records later PRs and hosted results | Treat `.superpowers/sdd/2026-11-02-sse-backpressure/progress.md` as historical handoff, not current remote state. PR #7 (`9a8418b`) is merged into `origin/main`, so the merge this text left open has happened upstream. Compare the proposed follow-up with current source before carrying it forward. Preserve undecided checkpoint work. |
| SSE proof gaps and observability | Add deterministic coverage where feasible for cancellation/body publication, no-data event/id discard, premature stream end and cancellation handler. Avoid race-dependent flaky tests. The A5 batch gives the operator a permanent-loss notice on all four screens, but the server's three counters still have no exposure route; decide how they are inspected. |
| Local checkout is behind `origin/main` and its batch work overlaps upstream | `main` is at `f0afc63`; `origin/main` is at `9a8418b` (PR #7), four commits ahead. The uncommitted quick-fix and medium-fix batches change `ARCHITECTURE.md`, `docs/production-readiness.md` and `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/StudioController.kt`, which upstream also changed, and upstream's `d062d63` supersedes the local `HITL_WAIT_ATTEMPTS` budget and the local §33.7/§33.8 wording. Reconcile these three files — do not resolve by discarding either side — and re-run the gates afterwards, because every locally recorded result describes the local tree rather than the merged one. |

Domain acceptance must also cover edited mixed text/image paragraphs without hidden object loss, anchored block/run editing that preserves unrelated formatting, durable multi-turn notebook conversations, explicit source/note evidence selection, and per-claim supported/contradicted/insufficient results. Today notebook `note_ids` membership alone does not put note text into Ask, and answer text plus one surviving citation does not establish complete semantic support.

## 4. M1 implementation contract distilled

Apply the stable MRC rules in the [migration/recovery contract](docs/replacement/migration-recovery-contract.md), with [decisions](docs/replacement/decisions.md) as amendments:

- Workspace identity survives moves/reinstallation. Artifact identity survives rename; vault notes gain stable frontmatter `uid`, with paths retained as addresses. Each store format has its own supported schema-version range.
- Each committed change increments a monotonic artifact revision; a content hash separately detects external edits. Writes require the revision the caller read. Refuse stale writes with `RevisionConflictError` and expected/actual revision details; do not overwrite silently.
- Acknowledge only after the required flush/atomic replacement/directory durability steps for supported filesystems. Preserve the old good state on failure. Use `StorageWriteError` with `disk_full`, `permission_denied` or `io_error`; use `IncompleteOperationError` for unfinished operations. These error types already exist; finish the behavior around them.
- Journal multi-file operations and unsaved drafts; expose recovery after interruption. Put conflict copies in `<workspace>/.lmeditor/conflicts/`, exclude them from normal search/notebook membership until adoption, and show them in diagnostics.
- Do not merge automatically. Offer explicit line-oriented plain-text merge only where supported; structured artifacts retain the current version or a separate copy. Source revocation denies future retrieval, download and export while retaining historical evidence bindings marked revoked.
- Preserve original bytes referenced by notebooks. Reference-aware retention replaces modification-time eviction for referenced originals; when capacity is exhausted, refuse a new upload rather than silently deleting evidence. Extracted source persistence alone does not satisfy F.02.
- Accepted defaults: 50 revisions/artifact; 14 daily + 4 weekly + 6 monthly backups; 7-day draft retention; 24-hour operation-intent retention; 5-second autosave. Treat them as configurable defaults.
- Workspace backups include artifacts, originals and revisions, exclude rebuildable indexes and recovery journals, and allow separate draft export. Test restore by reading the restored content and relationships, not merely checking that an archive exists.
- Migration requires a verified backup, explicit dry run and operator confirmation; make retries idempotent and the final swap atomic. Refuse unsupported schemas and offer migration or backup restore. Do not silently rewrite old stores on open.
- Persistent jobs must have restart/cancel/retry semantics and idempotent transitions. A source store, a workflow session and an upload cache have different lifetimes; do not collapse them.

## 5. Milestones, parallel work and stop conditions

| Stage | Required outcome | Exit rule |
| --- | --- | --- |
| M0 — contracts and evidence | Requirement identities, feature inventory, workflow matrix, compatibility corpus, recovery contract and layout feasibility | Registry/matrix/corpus checks pass, evidence rules hold and layout decisions are resolved under the M0 blueprint's nine gates. Preserve the reconciled matrix and verify current evidence. Proposed modules remain proposals; M0 does not establish runtime replacement. |
| M1 — trustworthy foundation | F.01–F.10 | Restart/crash/restore preserves acknowledged work and notebook evidence; identity, concurrency, navigation, offline operation, accessibility and installed lifecycle gates pass. |
| M2 — daily replacement | W1/W2, O1, N1 and P.01–P.05 integrated local workflows | All assigned registry gates pass on representative real user fixtures without hidden loss. |
| M3 — broad workflows | W3, O2, N2, sharing/sync and P.06 portable bundles | Review, visual knowledge, study/media and multi-device workflows pass their gates. |
| M4 — release qualification | R requirements | Clean-machine installation, compatibility, accessibility, security and performance evidence is retained; exceptions are published. |

Before each wave, publish exact file ownership, single responsibilities, interfaces and acceptance gates. Settle core schema/error/wire edits serially; delegate independent domains in parallel. Require validation and sanitization at entry points, strict typing, contextual domain errors, deterministic serialization, bounded resources and idempotent state changes. Review dependency direction and avoid cascading monoliths. Update the architecture ledger for every new file.

Stop a slice once its named gate and relevant regressions pass and its evidence is recorded. Do not expand a packaging slice into editor redesign or a performance slice into a new storage architecture. No dates or staffing estimates are committed by these documents.

### Layout and compatibility decisions that constrain later work

LMeditor owns a documented, versioned layout profile. Word-identical pagination, complex-script shaping, kerning, multi-column layout and arbitrary embedded-font PDF output remain out of scope unless a later decision reopens them. Kotlin deterministic modules may bundle approved, licensed typographic metric tables, not font programs or `java.desktop` text measurement. Select an OFL release, named metric generator and profile version before golden-page tests. The Compose desktop dependency exemption does not apply to the tier.

Legacy `.doc` is explicitly refused today. P-3 remains an M3 conversion-policy question; do not silently parse it as DOCX. Preserved opaque OOXML does not promise that the UI can edit it. Compatibility evidence must distinguish supported, partially supported, preserved and unsupported behavior using the existing corpus/matrix vocabulary.

## 6. Validation and evidence commands

Maintained documentation tools run from the repository root with Python 3.12 or later and no third-party dependencies:

```powershell
python backend/scripts/build_replacement_matrix.py --check
python backend/scripts/check_replacement_links.py
python backend/scripts/check_inventory_cells.py
python backend/scripts/check_compatibility_vocabulary.py
python backend/scripts/verify_corpus_digests.py
```

Omit `--check` from the matrix command to regenerate it from the authoritative registry. This updates the rendered matrix only; it never regenerates or resets registry statuses. Checkers only read their inputs and return a nonzero status for errors. The link checker validates Markdown heading fragments and registry file:line addresses, not the semantic strength of evidence. Focused tests are `backend/tests/test_replacement_doc_tools.py` and `backend/tests/test_replacement_requirements.py`.

Run from `D:\LMeditor`. Documented gate tooling is Python 3.13, JDK 25, uv 0.11.16; Kotlin artifacts target Java 17. First dependency acquisition needs network access. These are documented instructions, not commands executed by this review.

```powershell
Set-Location D:\LMeditor\backend
uv sync --locked --extra dev --python 3.13
uv run --locked --extra dev python scripts/verify_release.py --portable
```

For full release evidence, provision Microsoft Word automation and semantic/NLI model weights, then run:

```powershell
uv run --locked --extra dev python scripts/verify_release.py
```

Use a dedicated environment: `uv sync` reconciles it with the lockfile. With an already provisioned environment, the root-level invocation is `python backend/scripts/verify_release.py`. `--offline` requires cached dependencies; configure `HF_HOME`, `SENTENCE_TRANSFORMERS_HOME`, `UV_CACHE_DIR` and `GRADLE_USER_HOME` as appropriate. `--timeout SECONDS` changes the default 1,800-second subprocess deadline.

The gate checks the lock, rebuilds the tier JAR, runs fresh Kotlin tests, checks strict Python typing for Linux and Windows, runs backend tests, compares parity and executes a real-HTTP document round trip using an offline model double. It is not a live-provider evaluation. Portable mode excludes three Word-conformance modules and five exact real-model tests. Allowed platform skips are exact declarations in [platform-checks.json](backend/platform-checks.json); unexpected, missing or failed evidence must fail the gate.

For focused Kotlin build and desktop checks, use the tracked wrapper from `D:\LMeditor\kotlin`:

```powershell
.\gradlew.bat build
.\gradlew.bat :bridge:tierJar
.\gradlew.bat :desktop:run --console=plain
.\gradlew.bat :desktop:packageDistributionForCurrentOS
```

Focused Python parity, HTTP and human-review probes live in `backend/scripts/verify_kotlin_parity.py`, `verify_end_to_end.py` and `verify_hitl_live.py`. Use these when the slice affects those seams; the release gate remains the combined evidence entry point.

Retain the unique `build/release/run-*` evidence directory, logs, JUnit XML, effective platform declarations and `external-checks.json`. Also record source revision, OS/tool versions and full/portable scope. Hosted CI currently covers Windows/Linux on Python 3.13 and JDK 25; macOS/Python 3.12 are not thereby exercised.

### Last documented evidence, not a fresh verification

Architecture §33 records local evidence under `build/release/run-jpm08wa3`: 1,081 Kotlin tests, strict typing of 135 Python files on both target platforms, 1,749 full backend tests, 1,725 portable backend tests, 26 parity agreements and the live HTTP round trip. Earlier hosted evidence in §31 records 1,059 Kotlin / 1,695 backend tests with the platform-declared Linux skip. These counts describe different trees and must not be merged into a new certification.

## 7. Completion bookkeeping

For each work item, record: requirement/action ID; owner; changed files and single responsibilities; observable gate; source revision and environment; retained evidence location; exclusions and remaining limitations. Update registry statuses only when their exact gates pass. Regenerate/check the workflow matrix and refresh section 8 whenever the registry changes. Read the newer ledger entries before carrying any historical task forward.

This consolidation changes documentation only. It does not close any open runtime requirement, change a binding API contract or replace the detailed normative Word/Kotlin/MRC rules. Original documents are retained for traceability.

## 8. Complete replacement requirement register

Snapshot of `docs/replacement/requirements.json` on 2026-11-02: **83 requirements — 46 not-started, 35 in-progress, 1 evidence-partial, 1 complete**. Stage totals: M1 10, M2 38, M3 23, M4 12. R.08 is the only complete requirement. Status is documentary evidence, not a fresh test result. Owners and gates below are copied exactly; evidence paths are retained as recorded and may require anchor refresh. Downstream IDs in the last column are blocked by that row.

### M1

| ID / requirement | Owner | Status | Exact completion gate | Recorded evidence | Blocks downstream |
| --- | --- | --- | --- | --- | --- |
| **F.01** Durable workspace identity | kotlin:core | in-progress | Create a workspace, a document, a note, a notebook and a source through the public API, then read back each assigned id and its schema version. | `kotlin/core/src/main/kotlin/lmeditor/core/Schemas.kt:1573`; `kotlin/core/src/main/kotlin/lmeditor/core/VaultSchemas.kt:27`; `kotlin/core/src/main/kotlin/lmeditor/core/NotebookSchemas.kt:26` | P.01, P.02, P.04, P.05 |
| **F.02** Durable source archive | kotlin:server | in-progress | Ingest a source, restart the backend, then read back the retained original, its chunks and its metadata from the durable source archive. | `kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50`; `kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:15`; `kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28`; `backend/tests/test_source_persistence.py:1` | N1.01, N1.05, N1.10, N2.03, N2.08, P.04, P.06, R.01 |
| **F.03** Safe atomic saves | kotlin:core | in-progress | Interrupt a vault note write and a notebook manifest write, then read both files back and find the previous contents intact. | `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:470`; `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883`; `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:485` | F.04, F.05, O1.07, P.06 |
| **F.04** Recovery and version restore | kotlin:library | not-started | Kill the process mid-edit, restart it, then restore the acknowledged draft and the previous version from the recovery journal. | None recorded | R.03, R.04, R.12, P.06 |
| **F.05** External change detection | kotlin:vault | not-started | Change a note on disk behind the editor, reopen it, then read a conflict copy instead of the overwritten draft. | None recorded | O1.07, O2.04, W3.05 |
| **F.06** Persistent job progress | kotlin:workflow | in-progress | Cancel a running generation job, restart the service and resume the run, then read back that no duplicate artifact was written. | `backend/src/lmeditor/workflow/graph.py:147`; `kotlin/workflow/src/main/kotlin/lmeditor/workflow/HITLRegistry.kt:57` | N2.04, N2.06, N2.07, N2.08, R.06 |
| **F.07** Unified navigation across workspaces | kotlin:desktop | not-started | Open the command palette, quick-open a document, a note and a notebook, then read back the visible search scope for each. | None recorded | — |
| **F.08** Local operation without a provider | python:llm | in-progress | Turn the provider off, edit a document and run a lexical search, then read the run log and find the stub named as the provider. | `backend/src/lmeditor/llm/stub.py:16`; `backend/src/lmeditor/llm/factory.py:48` | W1.05, N2.10 |
| **F.09** Installable local delivery | cross:delivery | in-progress | Build the desktop package, install it on a clean machine, then start the app and the local services from the installed launcher. | `kotlin/desktop/README.md:76`; `kotlin/desktop/build.gradle.kts:49` | R.12 |
| **F.10** Accessible operation of every workflow | kotlin:desktop | not-started | Complete a document edit, a note edit and a notebook answer with the keyboard alone, then read the screen-reader semantics of each step. | None recorded | — |

### M2

| ID / requirement | Owner | Status | Exact completion gate | Recorded evidence | Blocks downstream |
| --- | --- | --- | --- | --- | --- |
| **W1.01** Document creation and outline editing | kotlin:library | in-progress | Create a book, add a titled section, rename it, reorder it and reopen the book, then read back the section order. | `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:188`; `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:295` | — |
| **W1.02** Persistent block and run model | kotlin:word | in-progress | Edit a paragraph through the document operation API, then read back the anchored runs and confirm no section string became the authority. | `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:671`; `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/DocumentWorkspace.kt:37` | W1.01, W1.03, W1.04, W1.06, W2.01 |
| **W1.03** Selection formatting and paragraph styles | kotlin:word | not-started | Select a span in a paragraph, apply bold, a heading style and a list level, then read back the run and paragraph properties. | None recorded | W1.06 |
| **W1.04** Undo, clipboard, find and statistics | cross:documents | not-started | Undo and redo an edit, paste rich text, run a find and replace, then read back the document word count. | None recorded | — |
| **W1.05** Independent human writing | cross:documents | in-progress | Write a section, save it and reopen it with no provider configured, then read the run log and find no model call. | `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt:68`; `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/DocumentWorkspace.kt:37` | — |
| **W1.06** Edits preserve surrounding rich structure | kotlin:word | not-started | Change one word in an imported rich paragraph, export the DOCX, then read back that the styles, links and anchored objects around it survived. | None recorded | W2.01, W3.02, W3.03 |
| **W2.01** Real page layout and pagination | kotlin:word | not-started | Lay out a 100-page document with margins, sections and page numbers, then read back the page break positions from the layout result. | None recorded | W2.02, W2.03, W2.05, W2.06, W2.08 |
| **W2.02** Visual table editing | kotlin:word | in-progress | Add a row and a column to a table, merge two cells and repeat the header row, then read back the resulting cell placements. | `kotlin/word/src/main/kotlin/lmeditor/word/WordTableLayout.kt:82`; `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:490` | W2.08 |
| **W2.03** Image insertion and placement | kotlin:word | in-progress | Insert a PNG into a document with a caption and a description, export the DOCX, then read back the drawing and its extent. | `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:574`; `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:291` | W2.08 |
| **W2.04** Links, footnotes and cross-references | kotlin:word | in-progress | Author a hyperlink, a bookmark and a footnote in a document, then read them back from the exported DOCX package. | `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:606`; `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:1799` | W2.05, W2.08 |
| **W2.05** Table of contents and bibliography | cross:documents | in-progress | Insert a table of contents and a bibliography into a document, then read back the resolved citation metadata for each entry. | `kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:201`; `kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:505` | W2.08 |
| **W2.06** Print preview and PDF export | kotlin:pdf | not-started | Export a laid-out document to PDF from the print preview, then reopen the PDF and read back the page count and the text of each page. | None recorded | W2.08, P.06 |
| **W2.07** Spellcheck and dictionaries | python:assistant | not-started | Type a misspelled word in a paragraph, then read back the dictionary suggestions and the document language setting. | None recorded | — |
| **W2.08** DOCX feature matrix | kotlin:word | not-started | Publish the DOCX feature matrix, then read one row per feature carrying read, display, edit, preserve, export and a stated limitation. | None recorded | R.01, R.02 |
| **O1.01** Markdown source and reading modes | kotlin:vault | in-progress | Open a note in source and reading modes and switch between them, then read the markdown back unchanged. | `kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:102`; `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/VaultScreen.kt:74` | O1.02, O1.03 |
| **O1.02** Safe rendering of markdown constructs | kotlin:vault | not-started | Render a note holding a table, a code fence, a callout and a task list, then read back the rendered blocks and the escaped raw HTML. | None recorded | O1.03, O2.03 |
| **O1.03** Wiki-link aliases and references | kotlin:vault | in-progress | Follow an aliased wiki-link, a heading reference and a block reference, then read back the resolved target and the unresolved-link report. | `kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:182`; `kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:275` | O1.07, O2.01 |
| **O1.04** Attachments and relative links | kotlin:vault | not-started | Paste an image into a note and link to a file beside it, then read back the attachment path and confirm no file moved. | None recorded | — |
| **O1.05** Typed frontmatter properties | kotlin:vault | in-progress | Write a note with typed frontmatter properties, reopen it, then read back the property values with the unknown YAML left intact. | `kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:44` | O2.03 |
| **O1.06** Daily notes, templates and saved searches | kotlin:vault | not-started | Create a daily note from a template and save a search, then reopen the vault and read both back by name. | None recorded | — |
| **O1.07** Recoverable rename with backlink rewriting | kotlin:vault | in-progress | Rename a linked note, interrupt the rename, then read back the note, its rewritten backlinks and the trash entry that restores it. | `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:186`; `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:153` | O1.08, O2.01 |
| **O1.08** Incremental vault index and file watching | kotlin:vault | not-started | Change one note on disk, wait for the watcher, then read back the updated index entry and the vault load count for the session. | None recorded | O1.09, O2.01, O2.03 |
| **O1.09** Vault search with query semantics | kotlin:vault | in-progress | Run a path, tag and full-text query, then read back the ranked hits, their snippets and the filters that were applied. | `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultSearch.kt:69`; `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultSearch.kt:19` | O2.01 |
| **N1.01** Notebook sources survive a restart | kotlin:notebooks | in-progress | Ask a notebook, restart the backend and desktop, then ask again and read the explicit report of any missing or changed source. | `kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28`; `backend/src/lmeditor/notebooks/answer.py:91`; `backend/tests/test_source_persistence.py:58`; `backend/tests/test_notebook_routes.py:380` | N1.02, N1.05 |
| **N1.02** Source reader with exact locators | kotlin:text | in-progress | Open a source in the reader, jump to a page and a character span, then read back the highlighted excerpt and its offsets. | `kotlin/text/src/main/kotlin/lmeditor/text/TextOffsets.kt:19`; `kotlin/core/src/main/kotlin/lmeditor/core/Schemas.kt:75` | — |
| **N1.03** Per-question source selection | python:notebooks | in-progress | Deselect one source for a question, ask it, then read back the citations and confirm the deselected source was excluded. | `backend/src/lmeditor/notebooks/answer.py:95`; `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/NotebookScreen.kt:494` | N1.05, N1.07 |
| **N1.04** Vault notes as explicit evidence | cross:notes | not-started | Select a vault note as evidence, ask a question, then read back the note identity and the content revision each answer was bound to. | None recorded | N1.05 |
| **N1.05** Persisted multi-turn conversations | python:notebooks | not-started | Hold a three-turn conversation, restart the backend, then read back every turn with its sources, its citations and its feedback. | None recorded | N1.06, N1.07, N1.09, N2.04 |
| **N1.06** Streamed answers with cancellation | python:notebooks | in-progress | Stream an answer, cancel it mid-flight and retry, then read back the partial answer and the recorded cancellation. | `backend/src/lmeditor/server/routes.py:1050`; `backend/src/lmeditor/server/routes.py:2400` | — |
| **N1.07** Claim-level evidence evaluation | python:verification | in-progress | Ask a question whose answer carries one supported and one unsupported claim, then read back the per-claim verdict for each. | `backend/src/lmeditor/notebooks/answer.py:131`; `backend/src/lmeditor/verification/claim_extractor.py:58` | N1.08, N1.09, P.05 |
| **N1.08** Separation of relevance, identity and support | python:verification | in-progress | Evaluate an answer, then read back the retrieval rank, the exact citation identity and the semantic support score as separate fields. | `backend/src/lmeditor/verification/nli_evaluator.py:220`; `kotlin/text/src/main/kotlin/lmeditor/text/ClaimLineage.kt:40` | N1.09 |
| **N1.09** Contradiction handling and abstention | python:notebooks | in-progress | Ask a question two sources contradict and one they cannot answer, then read back the contradiction report and the refusal. | `backend/src/lmeditor/notebooks/answer.py:54`; `backend/src/lmeditor/notebooks/answer.py:167` | — |
| **N1.10** Persisted retrieval index and recall benchmark | python:provenance | not-started | Persist a retrieval index across a restart, then read back the recall and the citation-precision figures for the curated corpus. | None recorded | N1.08 |
| **P.01** Shared project workspace manifest | kotlin:core | not-started | Open one project that holds a document, a note, a notebook and a source, then read back the stable id each entry is linked by. | None recorded | P.02, P.03, P.04, P.05, P.06 |
| **P.02** Promote a cited passage into a note | cross:notes | not-started | Promote a quoted passage into a vault note, then read back the note's locator, the source id and the source revision it carries. | None recorded | P.03 |
| **P.03** Promote research into an editable document | cross:research | in-progress | Build an outline from a notebook's notes, open it as a book, edit a section, then read back the provenance each section kept. | `kotlin/discovery/src/main/kotlin/lmeditor/discovery/OutlineBuilder.kt:45`; `backend/src/lmeditor/library/importer.py:102` | P.05 |
| **P.04** Add a document to notebook research | cross:research | not-started | Add a document section to a notebook, then read back the manifest entry that says whether it is a snapshot or a live link. | None recorded | — |
| **P.05** Navigate from claim to evidence | python:verification | in-progress | Open a manuscript claim, jump to the chunk that supports it and back, then read the stale flag after the claim was edited. | `backend/src/lmeditor/verification/citation_checker.py:270`; `kotlin/text/src/main/kotlin/lmeditor/text/ClaimLineage.kt:40` | — |

### M3

| ID / requirement | Owner | Status | Exact completion gate | Recorded evidence | Blocks downstream |
| --- | --- | --- | --- | --- | --- |
| **W3.01** Anchored comments and discussions | kotlin:word | in-progress | Anchor a comment on a paragraph, write a reply, resolve it and reopen the DOCX, then read the thread back from the package. | `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:637`; `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:1145` | W3.03, W3.05 |
| **W3.02** Tracked changes authoring | kotlin:word | in-progress | Insert and delete text with tracking on, export the DOCX, then read back the revision elements with their author and date. | `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:999`; `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:355` | W3.03 |
| **W3.03** Version comparison and AI edit review | kotlin:editing | not-started | Compare two saved versions of a document and read the differing sections, then read a proposed AI edit back as an explicit change. | None recorded | — |
| **W3.04** Styles, fields, forms and mail merge | kotlin:word | not-started | Apply a reusable style set, insert a field and a content control, then read the definitions back from the exported DOCX. | None recorded | — |
| **W3.05** Coauthoring, presence and permissions | cross:documents | not-started | Two clients edit one document under different roles, then read the convergence record and the denied write in the server log. | None recorded | — |
| **W3.06** Legacy doc and macro handling | kotlin:word | not-started | Open a legacy .doc file, then read the compatibility note that names the macro and embedded-object limits of the import. | None recorded | R.01 |
| **O2.01** Graph views of the vault | kotlin:vault | not-started | Open the local and global graph view of a 10,000-note vault, then read back the rendered node count and the applied filters. | None recorded | — |
| **O2.02** Canvas with portable JSON export | kotlin:vault | not-started | Build a canvas of cards and connections, export it as JSON Canvas and import it back, then read the card set on both sides. | None recorded | — |
| **O2.03** Property-driven table and card views | kotlin:vault | not-started | Define a table view over a note property, then read back the rows in the same order after a restart. | None recorded | — |
| **O2.04** Multi-device sync with offline edits | cross:notes | not-started | Edit the same note offline on two devices, reconnect them, then read back the merged result with no silent data loss. | None recorded | — |
| **O2.05** Extension and command API | cross:delivery | not-started | Install a minimal extension through the declared API, then read back the permission it was granted and the commands it registered. | None recorded | — |
| **O2.06** Publishing selected notes | kotlin:vault | not-started | Publish a selected note with its attachments, then read back the published link and the private-link check that ran. | None recorded | — |
| **N2.01** Expanded ingestion coverage | kotlin:ingest | in-progress | Ingest a PDF, a DOCX, an HTML page, a Markdown file and a pasted text, then read back the extraction quality and the omissions for each. | `kotlin/ingest/src/main/kotlin/lmeditor/ingest/LoaderRegistry.kt:23`; `kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfText.kt:27` | N2.02, N2.03 |
| **N2.02** Audio and video ingestion | kotlin:ingest | not-started | Ingest an audio file and a video transcript, then read back the timestamped transcript and the original reference for each. | None recorded | — |
| **N2.03** Web discovery and document connectors | kotlin:ingest | in-progress | Resolve a DOI through the resolver chain, then read back the captured version so a refresh cannot alter an old answer. | `kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:505`; `kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt:315` | — |
| **N2.04** Editable summaries and briefing documents | python:drafting | in-progress | Draft a section from a notebook's sources, edit the draft and save it, then read back the draft and its evidence links. | `backend/src/lmeditor/drafting/modules.py:33`; `backend/src/lmeditor/drafting/modules.py:278` | N2.05, N2.06, N2.08 |
| **N2.05** Flashcards, quizzes and study progress | python:drafting | not-started | Generate a flashcard set and a quiz from a notebook, then read back each answer key checked against its evidence. | None recorded | — |
| **N2.06** Audio overviews | python:assistant | not-started | Generate an audio overview with its source-linked script, play it and download it, then read back the script and its references. | None recorded | — |
| **N2.07** Video overviews and slide decks | python:assistant | not-started | Generate a slide deck from a notebook, then reopen it and read back the script, the references and the generation settings. | None recorded | — |
| **N2.08** Persisted artifact jobs and versions | kotlin:notebooks | not-started | Generate a study pack, restart the backend and reopen it, then read back its provenance, its version and its export. | None recorded | N2.09, P.06 |
| **N2.09** Notebook sharing with roles | kotlin:server | not-started | Share a notebook with a reader role, then attempt a source download and read back the denial and the revocation. | None recorded | — |
| **N2.10** Provider usage, cost and privacy | python:llm | not-started | Start an operation that calls a remote provider, then read back the usage, the estimated cost and the privacy choice shown before it ran. | None recorded | — |
| **P.06** Portable project bundle export and import | python:export | not-started | Export a project bundle, import it on a clean installation, then read back the notes, documents, originals, artifacts and manifest it carried. | None recorded | R.04 |

### M4

| ID / requirement | Owner | Status | Exact completion gate | Recorded evidence | Blocks downstream |
| --- | --- | --- | --- | --- | --- |
| **R.01** Compatibility corpus | cross:evidence | in-progress | Run the corpus over edited and untouched round trips, then read back the manifest entry that names each fixture and its outcome. | `backend/tests/word_fixtures.py:209`; `backend/tests/word_shared_corpus.py:211` | R.02, R.05 |
| **R.02** Export comparison against real Word | cross:evidence | evidence-partial | Export a document, compare its semantic structure and rendered pages with the fixtures, then read back the Word validation result. | `backend/tests/word_real_word_check.py:555`; `backend/tests/test_word_real_word.py:31` | — |
| **R.03** Fault injection and recovery | cross:evidence | not-started | Fill the disk, deny a write and kill the process mid-save, then read back the acknowledged edit and the recoverable conflict. | None recorded | — |
| **R.04** Reversibility and clean-installation restore | cross:delivery | not-started | Import a project bundle on a clean installation, restore a backup, then read back the documents, notes and sources that came back. | None recorded | — |
| **R.05** Measured latency and memory budgets | cross:evidence | not-started | Benchmark the 100-page document, the 10,000-note vault and the 100-source notebook, then read back the measured p95 latency and the memory. | None recorded | R.06 |
| **R.06** Latency separation and UI responsiveness | cross:evidence | not-started | Run a long job while editing and navigating, then read back the local-interaction latency measured apart from the model latency. | None recorded | — |
| **R.07** Boundary limits and input sanitisation | kotlin:ingest | in-progress | Submit an oversized upload, a private-host URL and a path that escapes the root, then read back the refusal for each. | `kotlin/ingest/src/main/kotlin/lmeditor/ingest/Limits.kt:22`; `kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt:315`; `backend/tests/test_ingestion_security.py:9` | — |
| **R.08** Authenticated local gateway | kotlin:server | complete | Call the gateway from a foreign origin and read back the refusal of the mutation, then call it without the installation credential and read back the refusal of that request too. | `backend/src/lmeditor/server/app.py:136`; `backend/src/lmeditor/server/app.py:152`; `backend/src/lmeditor/server/app.py:443`; `backend/src/lmeditor/server/app.py:506`; `backend/src/lmeditor/server/credential.py:129`; `backend/src/lmeditor/server/credential.py:259`; `backend/tests/test_api_credential.py:124`; `backend/tests/test_api_credential.py:256`; `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/ApiCredential.kt:89` | R.12, N2.09 |
| **R.09** Secrets in protected storage | python:llm | not-started | Configure a provider key, read the diagnostics dump, then read back that no key and no document content appears in it. | None recorded | — |
| **R.10** Access checks on retrieval and export | kotlin:server | in-progress | Revoke a source, then request retrieval, an artifact export and a source download, then read back the refusal and the preserved history. | `backend/src/lmeditor/notebooks/answer.py:95`; `backend/src/lmeditor/server/sources.py:161` | — |
| **R.11** Reproducible diagnostics and repair | cross:delivery | not-started | Run the diagnostics action, read back the storage and index status, then rebuild the index and read back the rebuilt state. | None recorded | — |
| **R.12** Platform installation validation | cross:delivery | not-started | Install, launch, edit offline, restart the service, upgrade and uninstall on a supported platform, then read back the retained data. | None recorded | — |

## 9. Reviewed source inventory

Project prose was reviewed in parallel, including historical hidden SDD reports. Test fixture DOCX/PDF/Markdown/TXT files are compatibility data, not project instructions, and were excluded. Dependency caches, build outputs and ignored local tooling are not product specifications. The requirement registry, platform declarations, package metadata and CI configuration were also inspected as supporting structured inputs. No external website or live PR state was consulted.

| Source | Role in this consolidation |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture, dated implementation evidence and remaining findings |
| [AUDIT_REMEDIATION_CONTRACT_P2.md](AUDIT_REMEDIATION_CONTRACT_P2.md) | Historical findings and proposals; reconciled against later ledger |
| [AUDIT_REMEDIATION_CONTRACT.md](AUDIT_REMEDIATION_CONTRACT.md) | Historical findings and proposals; reconciled against later ledger |
| [AUDIT.md](AUDIT.md) | Historical findings and proposals; reconciled against later ledger |
| [backend/README.md](backend/README.md) | Module responsibilities and operating/build instructions |
| [CODEBASE_REPORT.md](CODEBASE_REPORT.md) | Historical findings and proposals; reconciled against later ledger |
| [docs/api-authentication-blueprint.md](docs/api-authentication-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/codex-generation-blueprint.md](docs/codex-generation-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/documents-workspace-blueprint.md](docs/documents-workspace-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/kotlin-cleanup-blueprint.md](docs/kotlin-cleanup-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/notebook-workspace-blueprint.md](docs/notebook-workspace-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/product-replacement-roadmap.md](docs/product-replacement-roadmap.md) | Product scope, milestones and dependency ownership |
| [docs/production-readiness-blueprint.md](docs/production-readiness-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/production-readiness.md](docs/production-readiness.md) | Current release follow-up order, gate and evidence limitations |
| [docs/replacement/decisions.md](docs/replacement/decisions.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/editor-layout-feasibility.md](docs/replacement/editor-layout-feasibility.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/feature-inventory.md](docs/replacement/feature-inventory.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/m0-blueprint.md](docs/replacement/m0-blueprint.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/migration-recovery-contract.md](docs/replacement/migration-recovery-contract.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/README.md](docs/replacement/README.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/replacement/supported-workflow-matrix.md](docs/replacement/supported-workflow-matrix.md) | Requirement evidence, recovery/layout contracts and recorded decisions |
| [docs/source-persistence-blueprint.md](docs/source-persistence-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/sse-backpressure-blueprint.md](docs/sse-backpressure-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/superpowers/plans/2026-09-21-source-ingestion.md](docs/superpowers/plans/2026-09-21-source-ingestion.md) | Historical implementation plan; checked against delivered records |
| [docs/superpowers/plans/2026-11-02-sse-backpressure.md](docs/superpowers/plans/2026-11-02-sse-backpressure.md) | Historical implementation plan; checked against delivered records |
| [docs/vault-workspace-blueprint.md](docs/vault-workspace-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [docs/word-processor-ui-blueprint.md](docs/word-processor-ui-blueprint.md) | Domain blueprint, delivery record and scope limitations |
| [GAP_ANALYSIS.md](GAP_ANALYSIS.md) | Historical findings and proposals; reconciled against later ledger |
| [kotlin/bridge/README.md](kotlin/bridge/README.md) | Module responsibilities and operating/build instructions |
| [kotlin/CONTRACTS.md](kotlin/CONTRACTS.md) | Binding domain/model/wire constraints |
| [kotlin/desktop/README.md](kotlin/desktop/README.md) | Module responsibilities and operating/build instructions |
| [kotlin/README.md](kotlin/README.md) | Module responsibilities and operating/build instructions |
| [kotlin/word/WIRE.md](kotlin/word/WIRE.md) | Binding domain/model/wire constraints |
| [WORD_PROCESSING_CONTRACT.md](WORD_PROCESSING_CONTRACT.md) | Binding domain/model/wire constraints |
| [requirements.json](docs/replacement/requirements.json) | Exact 83-row register, status, gates, owners, evidence and dependency edges |
| [platform-checks.json](backend/platform-checks.json) | Exact platform-specific allowed skips |
| [verify.yml](.github/workflows/verify.yml) | Current hosted candidate-gate configuration |
| [pyproject.toml](backend/pyproject.toml) | Declared Python support and dependency configuration |

### Historical SDD records (84 files)

These are local historical task artifacts, often duplicating code/diff snapshots. Later final reviews and delivered ledger entries supersede initial briefs. Unchecked boxes and historical Git instructions do not authorize new work. Source-ingestion has 73 Markdown records; SSE has 11. Historical PR and checkpoint references are unverified handoff context.

<details>
<summary>Full historical source inventory</summary>

- [.superpowers/sdd/2026-09-21-source-ingestion/final-fix-report.md](.superpowers/sdd/2026-09-21-source-ingestion/final-fix-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/final-re-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/final-re-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/final-re-review.md](.superpowers/sdd/2026-09-21-source-ingestion/final-re-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/final-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/final-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/final-review.md](.superpowers/sdd/2026-09-21-source-ingestion/final-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/progress.md](.superpowers/sdd/2026-09-21-source-ingestion/progress.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-1-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-1-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-1-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-1-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-1-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-1-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-1-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-1-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-10-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-10-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-10-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-10-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-10-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-10-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-10-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-10-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-11-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-11-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-11-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-11-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-11-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-11-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-11-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-11-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-12-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-12-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-12-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-12-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-12-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-12-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-12-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-12-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-13-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-13-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-13-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-13-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-13-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-13-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-13-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-13-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-14-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-14-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-14-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-14-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-14-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-14-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-14-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-14-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-15-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-15-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-15-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-15-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-15-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-15-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-15-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-15-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-16-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-16-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-16-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-16-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-16-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-16-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-16-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-16-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-17-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-17-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-17-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-17-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-17-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-17-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-17-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-17-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-2-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-2-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-2-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-2-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-2-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-2-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-2-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-2-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-3-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-3-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-3-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-3-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-3-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-3-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-3-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-3-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-4-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-4-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-4-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-4-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-4-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-4-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-4-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-4-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-5-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-5-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-5-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-5-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-5-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-5-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-5-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-5-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-6-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-6-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-6-fix-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-6-fix-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-6-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-6-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-7-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-7-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-7-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-7-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-7-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-7-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-7-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-7-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-8-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-8-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-8-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-8-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-8-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-8-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-8-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-8-review.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-9-brief.md](.superpowers/sdd/2026-09-21-source-ingestion/task-9-brief.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-9-report.md](.superpowers/sdd/2026-09-21-source-ingestion/task-9-report.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-9-review-pkg.md](.superpowers/sdd/2026-09-21-source-ingestion/task-9-review-pkg.md)
- [.superpowers/sdd/2026-09-21-source-ingestion/task-9-review.md](.superpowers/sdd/2026-09-21-source-ingestion/task-9-review.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/fix-round-1-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/fix-round-1-brief.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/fix-round-1-report.md](.superpowers/sdd/2026-11-02-sse-backpressure/fix-round-1-report.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/progress.md](.superpowers/sdd/2026-11-02-sse-backpressure/progress.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-1-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-1-brief.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-1-report.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-1-report.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-2-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-2-brief.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-3-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-3-brief.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-3-report.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-3-report.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-4-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-4-brief.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-4-report.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-4-report.md)
- [.superpowers/sdd/2026-11-02-sse-backpressure/task-5-brief.md](.superpowers/sdd/2026-11-02-sse-backpressure/task-5-brief.md)

</details>

