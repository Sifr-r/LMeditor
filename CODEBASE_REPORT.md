# LMeditor — Codebase Review Report

> **Superseded in part — 2026-10-25.** This is a dated review of the codebase as it stood. The Flutter/Dart desktop client it reports on was **removed**; the Kotlin tier (`kotlin/`) owns the deterministic rules **that cross the seam** — `ARCHITECTURE.md` §18.2.1 catalogues the rules it does not — and the Compose client (`kotlin/desktop/`) now provides the document pipeline UI. The client findings, the `client/` paths and the client LOC counts are history. Backend findings retain their historical scope and should be checked against later remediation. See `ARCHITECTURE.md` §§19–20.

**Scope:** Source code only. `ARCHITECTURE.md`, `AUDIT.md`, `GAP_ANALYSIS.md`, `AUDIT_REMEDIATION_CONTRACT*.md`, `WORD_PROCESSING_CONTRACT.md` and `docs/` were deliberately **not** read; every claim below is derived from code, tests, or a live run of the application.

**Method:** 4 parallel subsystem deep-dives (Word/OOXML, ingestion+provenance, verification/drafting/discovery, Flutter client), a full read of the core domain spine, `pytest` on the backend, `mypy` on the backend, and **live end-to-end probes against a running uvicorn server** (boot → ingest → generate → SSE → HITL approve → final manuscript).

**Codebase size:** backend 10,701 LOC src / 10,714 LOC tests (44 + 33 files) · client 6,552 LOC lib / 2,499 LOC tests (22 + 7 files).

---

## 1. What the project sets out to be

Taken from the code itself (`backend/pyproject.toml:8`):

> "An enterprise-grade, DSPy-native, local-first, STORM multi-perspective discovery and writing harness anchored strictly to verifiable source passages, exposed through a Flutter desktop client."

Decomposed, that is nine promises:

| # | Promise | Where it lives |
|---|---|---|
| P1 | DSPy-native LLM writing | `drafting/` |
| P2 | STORM multi-perspective discovery | `discovery/` |
| P3 | Source ingestion + bibliographic resolution | `ingestion/` |
| P4 | **Strict grounding** — every claim NLI-verified against real passages | `verification/` |
| P5 | Human-in-the-loop review gate | `workflow/` + `server/session.py` |
| P6 | Flutter desktop client | `client/` — **removed 2026-10-25** (§19); replaced by Kotlin Compose in `kotlin/desktop/` (§20) |
| P7 | Word (.docx) output | `word/` |
| P8 | Local-first | no cloud deps |
| P9 | Enterprise-grade quality | tests, typing, errors |

---

## 2. Headline verdict

**This is a very well-built skeleton with the product missing at the centre.**

Roughly **70% of the infrastructure is real and high quality**. Roughly **15% of the product works**. The gap is not a matter of polish — it is one missing LM configuration, one hard-coded constant, and about a dozen wiring lines, each of which is individually small but jointly fatal.

**The single most important finding: there is no LLM.** `dspy-ai` is a declared dependency (`pyproject.toml:13`), there are genuine DSPy modules and signatures, and **not one `dspy.configure(...)` or `dspy.LM(...)` call exists in the entire backend**. Verified empirically:

```
SectionDrafter.forward(...) -> ValueError: No LM is loaded. Please configure the LM
                               using `dspy.configure(lm=dspy.LM(...))`
```

> **Superseded — 2026-10-27.** This finding was true of the code as it stood,
> and it is false of the candidate. The backend now installs a language model:
> `backend/src/lmeditor/llm/factory.py:60` returns `dspy.LM(**kwargs)` and
> `:93` calls `dspy.configure(lm=lm)`, with `:199` releasing it again. The
> provider comes from `LMEDITOR_LM_PROVIDER`; with no provider configured the
> backend still starts and `/api/v1/health` reports `lm.configured: false`
> rather than pretending to be ready.

**Second most important: a failed draft is disguised as a successful one.** `workflow/nodes.py:160-175` catches that error and emits

```
"{section title}: According to verified documentation, {first 180 chars of source chunk} [#chunk_id]"
```

as if it were a drafted section. In my live run, **all ten sections of the final manuscript contained that identical sentence**, each prefixed with a different section title.

> **Superseded — 2026-10-27.** This finding was true of the code as it stood,
> and it is false of the candidate. `backend/src/lmeditor/workflow/nodes.py:605-613`
> now catches the drafting failure, records it honestly and leaves the section
> **empty** — "a failure is not a section" — with the section's status set to
> `NEEDS_REVISION` and the reason appended to the run's errors. The template
> sentence quoted above appears nowhere in the backend. The finding is kept
> because it is the evidence that the two headline gaps above it were closed.

---

## 3. What actually works (verified, not asserted)

These are real, exercised, and in several cases excellent:

**Ingestion — genuinely production-shaped.** Real libraries throughout: `pypdf` for PDF, `trafilatura` for HTML, `markdown` for Markdown, a hand-written lxml OOXML reader for DOCX (`ingestion/loaders.py`). Chunking is a sliding window with **real char and byte offsets** and boundary assertions (`provenance/chunker.py:86-113`).

**Bibliographic resolution — real network clients, zero fabrication.** `httpx` clients against Crossref, arXiv, PubMed and OpenAlex with genuine parsers for each response format (`ingestion/resolvers.py:574,636,716,816`). The chain is PdfHeader → CrossRef → ArXiv → PubMed → OpenAlex, first non-`None` wins, failures fall through. **When resolution fails it returns `None` — it never synthesises a plausible-looking record.** This matters enormously for a tool whose entire premise is verifiability, and it is correct.

**SSRF protection — the strongest thing in the repo.** `ingestion/url_policy.py` + `loaders.py:388-495`: scheme allowlist, single `getaddrinfo`, loopback/private/link-local/reserved blocking, IPv4-mapped IPv6 unwrapping, **per-hop DNS pinning** with the peer compared against the vetted set, **fails closed** when the peer is unobservable, body read only after peer approval, manual redirect handling capped at 5 hops with each target re-validated. 16 dedicated tests in `tests/test_dns_pinning.py` prove rebinding to loopback, 10.x, `169.254.169.254`, `::1`, swapped public addresses, mixed public/private sets, unobservable peers, and redirect-to-private are all refused. My live probe: `POST /api/v1/sources/url` against `http://127.0.0.1:8099` → **400, refused**.

**The Word OOXML subsystem — the most sophisticated code in the repo.** Not a `python-docx` wrapper: a real OOXML reader that resolves `basedOn` style chains, inherits `numPr` from styles, handles `numFmt`/`lvlOverride`, computes `gridSpan`/`vMerge` merge regions including covered columns, follows hyperlink relationships and `#anchor`s, recurses into `w:sdt` content controls and text boxes, and surfaces dropped content in an explicit `last_omissions` register rather than silently discarding it. XXE/entity expansion is blocked at the parser (`resolve_entities=False, no_network=True, huge_tree=False`, `word/reader.py:584`). The writer emits genuine constructs — multi-level numbering with `startOverride`, raw `w:tbl` with `gridSpan`/`vMerge`/`tblHeader`, hyperlink rels with nested runs, and a hand-built `footnotes.xml`.

**SSE streaming and the HITL gate — production quality.** `server/session.py` implements fan-out with a replay buffer, typed terminal semantics, and a genuine suspend/resume: the workflow stops at `hitl_gate`, the decision is recorded against the *pending* review, and the run resumes on the same graph thread. A decision for an already-answered review returns 409, not a silent no-op. **There is no client-side bypass.** My live run confirmed the full sequence: `node_start → … → hitl_request → approve (200) → … → workflow_complete`.

**Verification mathematics.** `verification/citation_checker.py` computes real precision/recall/F-beta against real chunk text fetched from the provenance layer. A section is `VERIFIED` only when `f_beta >= 0.85` **and** zero contradicted/ungrounded/neutral claims. Hallucinated chunk IDs raise a typed error — though the workflow disables that check in production (see §5).

**Discipline.** 689 backend tests pass (1 skipped) in 87s. `mypy` is clean apart from two trivial `unused-ignore` warnings. Test-to-source LOC ratio is 1:1. Errors are typed throughout — `LMeditorApiException`, `SourceLoadError`, `BibliographyResolutionError`, `NLIModelError` — with no silent swallows in the transport layer. The client's SSE parser handles heartbeats, parse errors and transport errors distinctly, and cancellation genuinely cancels the subscription.

---

## 4. The real NLI verification is good — and it is not what ships

`verification/nli_evaluator.py` has a real primary path: `cross-encoder/nli-deberta-v3-small` via `sentence-transformers`. The model is **present in your local HuggingFace cache**, so it loads offline. Measured on a real pair:

```
entailed paraphrase   -> ENTAILED      0.9988
direct contradiction  -> CONTRADICTED  0.9994
```

That is a working entailment engine. Two problems remain:

**(a) The fallback silently inverts meaning.** When the model is unavailable, `HeuristicNLIEvaluator` takes over — and it is pure token overlap. I measured it directly:

```
premise: "Municipal waste collection rates FELL by 30 percent between 2019 and 2023."
claim:   "Municipal waste collection rates ROSE by 30 percent between 2019 and 2023."
          -> ENTAILED 0.90     <-- a direct contradiction
```

A negation ("fell" vs "rose") is not in its word list, so token overlap sails through. `nli_evaluator.py:209-214` catches every primary exception with **no log, no flag, no scorecard marker** — the scores change meaning and nothing says so.

**(b) The cross-encoder itself has a weak neutral/contradiction boundary.** An unrelated premise/hypothesis pair came back `CONTRADICTED 0.9991` rather than `NEUTRAL`. Off-topic claims are being counted as contradictions, which inflates the "caught an error" signal while deflating precision. There is no calibration set anywhere in the repo — the thresholds (`0.65`, `0.6`, `0.5`, `0.85`) are hardcoded constants.

---

## 5. Gap analysis, in priority order

### P0 — Blocking. The product cannot do its job.

**5.1 No LLM is ever configured.**
`grep -r "dspy.configure\|dspy.LM\|dspy.settings"` across `backend/src` → **zero hits**. `pyproject.toml:13` declares `dspy-ai`; `drafting/modules.py` has genuine `dspy.ChainOfThought` modules and `drafting/signatures.py` has well-formed signatures. But no provider, no key, no `dspy.configure` in the `server/app.py` lifespan. Every drafting call raises. **Fix:** an `llm/` config module reading a provider env var, configured at app startup. This is one small file.

**5.2 The fallback turns an outage into plausible prose.**
`workflow/nodes.py:160-175`. Because the fallback emits a real-looking section with a real-looking `[#chunk_id]` tag, an LM outage is indistinguishable in the output from a successful draft. In my live run the final manuscript was ten copies of the same 180-character source excerpt. **Fix:** delete the fallback, or gate it behind an explicit "degraded mode" flag that is surfaced in the scorecard and the UI.

**5.3 The outline is a hard-coded constant.**
`discovery/outliner.py:102-198` is a literal 5-root / 10-leaf academic skeleton. It is **identical for every topic and every corpus**. On a run about municipal waste collection, my output contained:

> `4.1 Production Engineering & Telemetry` · `2.2 Provenance Binding & Dataflow Integrity` · `3.2 Cost, Latency & Ergonomic Trade-offs` · `5.2 Concluding Epistemic Synthesis`

`self.use_dspy` is assigned at `outliner.py:21` and **never read**; the docstring promising "utilize DSPy for dynamic section heading generation" is false.

**5.4 Perspectives are template personas.**
`discovery/perspective.py` interpolates the topic string into a fixed 6-entry `ROLE_ARCHETYPES` table (`:28-119`). The DSPy branch is opt-in, never enabled, and swallows exceptions. `perspective_discovery_node` (`workflow/nodes.py:90-91`) passes **no context**, so perspectives never see a source. The "STORM multi-perspective discovery engine" is a list of job titles.

**5.5 Client and server speak different event vocabularies.**
This is the finding that breaks the headline feature. The backend's `EventType` (`core/types.py:66-75`) has exactly **seven** values:

```
node_start · node_complete · token_delta · scorecard_update
hitl_request · workflow_complete · workflow_error
```

The client's SSE parser (`client/lib/core/api_client.dart`) handles `outline` (`:593`), `perspectives` (`:608`), `chunks` (`:623`), `citation`/`citation_created` (`:638`), `claim`/`claim_verified` (`:651`), `metrics`/`stream_metrics` (`:664`), and `done`/`completed` (`:675`).

**The backend emits none of them.** I grepped `backend/src` for `claim_verified|citation_created|event_type="outline"|…` → **no matches**. Consequence, live:

- Claim list empty → **every citation badge renders "unverified"** with no confidence (`editor_view.dart:601-602`)
- Verification panel permanently empty
- Chunk inspector, outline tree and perspective panels all empty
- `claimsProvider` never populated (`app_providers.dart:378-383`)

**5.6 The UI reports "100.0% grounded" on a never-verified manuscript.**
`Manuscript.empty().groundingScore = 1.0` (`client/lib/core/models/manuscript.dart:25`), reset every run (`app_providers.dart:490`), and rendered as a percentage (`main.dart:464`). The backend emits no `stream_metrics` event, and the client **discards** the `scorecard_update` it does receive (`app_providers.dart:398-404` — the comment calls it "otherwise telemetry"). Meanwhile `verification_panel.dart:67-69` hardcodes an entailment rate of `'100.0'`.

So the app displays perfect grounding and perfect entailment over a manuscript where every claim was measured as `ungrounded 0.0`. **This directly contradicts the product's own stated promise** and the client's own "no fabricated verification" rule. **Fix:** default to `null`/`—` and populate from the scorecard.

**5.7 "Completed" is not gated on quality.**
My live run: `status: completed`, `is_completed: True`, `workflow_complete` with 10 sections — while **all ten** sections were `status: needs_revision`, `is_verified: False`, one claim each at `ungrounded 0.0`, and the scorecard was **`null`**. A run in which nothing was verified reports success.

### P1 — The product is unreachable.

**5.8 The Word write path has no way out.** `WordWriter` and `build_manuscript_document` have **zero references outside `word/` and `backend/tests/`**. No export endpoint, no workflow node, no CLI, no client call. **A user cannot obtain a .docx from this application.** The reader is reachable (via `DocxSourceLoader`); the writer is not.

**5.9 The omission register is discarded in production.** `ingestion/loaders.py:296-299` calls `WordReader().read(path)` then `document.plain_text()` — `last_omissions` is never read. Every "image dropped", "comment dropped", "endnote dropped" warning dies inside the app. The careful design is inert.

**5.10 Provenance retrieval is unwired.** `HybridIndexer`, `BM25Index`, `DenseVectorIndex` and `CitationLineageEngine` are real implementations with **zero call sites** outside their own module. Live chunk→section binding is keyword-substring counting against the hard-coded outline (`outliner.py:276-299`) plus a "first 3 chunks" fallback (`nodes.py:144-146`). For a product promising strict anchoring, retrieval quality is keyword-level.

**5.11 No semantic embeddings despite the dependency.** `DenseVectorIndex` defaults `embedder=None` → always a SHA-256 TF-hash 128-d fallback (`provenance/indexer.py:112-146`). `sentence-transformers` is used **only** by the NLI evaluator — and `all-MiniLM-L6-v2` is sitting in your HF cache unused. Storage is an in-memory dict; there is no vector store.

**5.12 The editor is read-only.** No editor package exists. Rendering is `SelectableText`/`Text.rich` over a hand-rolled markdown subset (`#`, `##`, `###`, `**bold**` only). The only mutations are Clear and Copy Markdown. The server accepts `modified_sections` on the HITL payload; the client **never sends it** (`api_client.dart:1154`). So "the human edits the draft" — half of the HITL premise — is unimplemented in the UI.

**5.13 There is no persistent source library.** `server/session.py` holds zero source or chunk state. The client must POST its ingested chunks back to `/documents/generate` as `source_chunks`. Uploaded files are written to disk and **never deleted**.

### P2 — Quality and safety.

**5.14 No size limits anywhere.** Upload body buffered without a cap (`server/routes_helpers.py:44`); URL response read unbounded (`ingestion/loaders.py:445`); the Word reader reads the whole file into memory (`word/reader.py:455`) with **no zip-bomb, member-count or compression-ratio defence**. A small malicious `.docx` can exhaust memory.

**5.15 Phantom citations are silently downgraded.** `CitationHallucinationError` exists and is correct (`citation_checker.py:134-140`), but the workflow calls the checker with `strict_hallucination_check=False` (`workflow/nodes.py:266`), so an invented chunk ID becomes `UNGROUNDED` rather than an error.

**5.16 SSRF protection is post-connect.** The TCP/TLS handshake and request line reach the internal host before the peer mismatch is noticed; only the body is closed. This is candidly documented at `loaders.py:364-375` and `test_dns_pinning.py:343-344` — a conscious trade-off, not an oversight, but worth knowing.

**5.17 Resolver calls use `follow_redirects=True` with no re-validation** (`resolvers.py:434`) — the URL loader's careful re-validation does not apply to Crossref/arXiv/PubMed/OpenAlex.

**5.18 Threshold drift and calibration.** The NLI fallback accepts a contradiction; the cross-encoder's neutral/contradiction boundary is untested; no calibration set exists.

**5.19 Claim extraction offsets are wrong on repetition.** `claim_extractor.py:210` uses `text.find(sentence)`, which returns the **first** occurrence — a repeated sentence gets offsets pointing at the wrong place.

**5.20 Dead code with live promises.** `use_dspy` flags assigned and never read (`outliner.py:21`, `dialogue.py:21`, `perspective.py:121`); `CitationInsertionSignature` imported and used by nothing; `LLMJudgeNLIEvaluator` exported and wired to nothing; `TeleprompterCompiler` never invoked; `DraftingTeleprompterCompiler`'s docstring promises "MIPROv2 and BootstrapFewShot" but implements only `BootstrapFewShot`; `PerspectiveGenerationError` is defined and never raised. `PdfHeaderBibliographyResolver` is dead because no producer ever populates `pdf_metadata`.

### P3 — Smaller items.

- `SourceChunk.token_count` — "Pre-computed token length" (`schemas.py:46`) — always 0. `Source.byte_size` — always `None`.
- `Source.title` — "Display title for the source library" — no library exists.
- Comments, endnotes, sections, images, custom styles: absent from the Word model; tracked changes flattened on read and never re-emitted; run font/size/colour/strike/highlight/subscript lost; numbering format hardcoded to `•`/`%1.` so roman lists become arabic; header↔footer mapping is positional, so even-page headers round-trip swapped.
- Legacy `.doc` is misrouted to the OOXML reader (`routes.py:540-541`) → always a parse failure.
- `generateOutlineOnly()` returns a **hard-coded fake outline** after a 600ms sleep (`app_providers.dart:235-286`).
- Demo mode **defaults ON** (`app_providers.dart:20`) — the app boots labelled "Mock SSE Demo".
- No drag-and-drop ingestion (only `file_picker`), no upload progress, no SSE reconnect/resume, base URL hardcoded to `http://127.0.0.1:8000` with no settings UI, `/health` and `/workflow/{id}/status` never called.

---

## 6. Promise vs. code

| Claim in code | Reality |
|---|---|
| "STORM multi-perspective discovery engine" | 6 template personas interpolated with the topic; never see a source |
| "Stanford STORM-style hierarchical outline DAG **synthesizer**" | a frozen constant |
| "DSPy declarative drafting package" | `ValueError: No LM is loaded` on every call |
| "Enterprise-grade … anchored strictly to verifiable source passages" | sections are source excerpts verified against themselves; the UI shows 100% grounded regardless |
| `use_dspy` on outliner / dialogue / perspective | dead flags; docstrings contradict behaviour |
| "MIPROv2 and BootstrapFewShot" | BootstrapFewShot only, unwired |
| "Dense vector index" + `sentence-transformers` dependency | TF-hash fallback |
| `SourceChunker` / source-aware chunking | 120-character sliding window |
| `Source.title` — "for the source library" | no library exists |
| `token_count` — "pre-computed" | always 0 |
| `PDF_HEADER` resolver kind | no producer; always `None` |

---

## 7. What the tests actually cover

**689 backend tests pass, 1 skipped, 87s.** The client has 7 test files (~2,499 LOC) that are genuinely behavioural — `stream_contract_test.dart` drives a `MockClient.streaming` and asserts the exact method+path sequence and the request body; `hitl_review_test.dart` taps Approve in a mounted app and asserts the POST; `url_ingestion_test.dart` asserts rejection before the network.

**The gap is between the tested units and the assembled product.** Almost every test exercises a module directly. The tests that would catch the five P0 defects do not exist, because each P0 defect lives in a *seam*:

- No test asserts an LM is configured (P0.1)
- No test asserts the emitted event vocabulary matches the client's parser (P0.5)
- No test asserts the grounding score reflects actual verification (P0.6)
- No test asserts "completed" implies sections are verified (P0.7)
- No test asserts the Word writer is reachable from the app (P1.8)

`tests/test_word_real_word.py` also gates a real-Microsoft-Word COM conformance check behind a skip — it will not have run in CI.

Client tests assert the **mapping algorithm** in isolation (`claim_mapping_test.dart` builds `EditorView` from hand-made `Claim`s). That proves the badge renderer works. It proves nothing about the live path, because no live path exists.

---

## 8. Scorecard

| Promise | Status | Evidence |
|---|---|---|
| P1 DSPy-native LLM writing | **ABSENT** | zero `dspy.configure`; `ValueError: No LM is loaded` |
| P2 STORM multi-perspective discovery | **STUB** | fixed archetype table, no source access |
| P3 Ingestion + bibliography | **FULL** | real loaders, real resolvers, zero fabrication |
| P4 Strict grounding | **PARTIAL** | real NLI + real metrics; wrong fallback; wrong client events |
| P5 HITL review gate | **FULL (server)** / PARTIAL (UI) | enforced, 409 on misuse; editor read-only |
| P6 Flutter desktop client | **PARTIAL** | excellent transport; event vocabulary mismatch; fake percentages |
| P7 Word .docx output | **LIBRARY-ONLY** | writer unreachable from the app |
| P8 Local-first | **FULL** | no cloud dependency |
| P9 Enterprise-grade quality | **PARTIAL** | clean typing, 1:1 tests — but no integration/e2e assertions |

**Roughly:** infrastructure ~70% built and good; product surface ~15% working.

---

## 9. Recommended order of work

1. **Configure an LM** (`llm/config.py` + `dspy.configure` in the `server/app.py` lifespan) — one file, unlocks P1.
2. **Delete or gate the fallback draft** (`nodes.py:160-175`) — an outage must not look like success.
3. **Make the outline source-grounded** — make `use_dspy` real, or replace the constant with a structure derived from the ingested chunks.
4. **Give perspectives source context** — pass the chunk set into `perspective_discovery_node`.
5. **Align the event vocabulary** — publish `claim_verified`, `citation_created`, `outline`, `perspectives`, `chunks` from the backend (the schemas already exist), or delete the client's handlers. This one change makes the entire verification UI live.
6. **Stop displaying fabricated percentages** — default `groundingScore`/entailment rate to `null`.
7. **Gate "completed" on verification** — if sections are unverified, the run should not report success.
8. **Expose the Word writer** — an export endpoint plus a client action.
9. **Turn on strict hallucination checking** in production.
10. **Fix the NLI heuristic fallback** and surface fallback usage in the scorecard.
11. **Add e2e assertions** for 1–7 above — they are all seam defects, and seam defects are exactly what unit tests miss.
12. **Add size caps** to uploads, URL responses and the Word reader (zip-bomb defence).

Items 1–7 are, conservatively, a few hundred lines. They convert an impressive skeleton into a working product.

---

## 10. Evidence appendix

| Check | Result |
|---|---|
| `pytest` (backend) | **689 passed, 1 skipped**, 87s |
| `mypy src` | 2 errors in 44 files — both trivial `unused-ignore` |
| Backend LOC src / tests | 10,701 / 10,714 (1:1) |
| Client LOC lib / tests | 6,552 / 2,499 |
| Server boot | `uvicorn lmeditor.server.app:app` → `{"status":"healthy","version":"0.1.0"}` |
| `GET /api/v1/health` | 200 healthy |
| `POST /api/v1/sources/ingest` (multipart) | 200, chunk created with real offsets |
| `POST /api/v1/sources/url` → `http://127.0.0.1:8099` | **400 refused** (SSRF blocked) |
| `POST /api/v1/documents/generate` | 202, SSE streamed 83 frames |
| SSE event types observed | `node_start · node_complete · token_delta · scorecard_update · hitl_request · workflow_complete` — **no claim/outline/perspective/chunk events** |
| `POST /api/v1/workflow/{id}/approve` | 200 `decision_recorded` → run resumed → `completed` |
| Final manuscript | 10 sections, **all** `needs_revision`, `is_verified: False`, claim `ungrounded 0.0`, scorecard `null` |
| Final manuscript content | 10× identical `"{title}: According to verified documentation, {chunk[:180]} [#chunk_id]"` |
| `SectionDrafter.forward` (no LM) | `ValueError: No LM is loaded` |
| NLI cross-encoder (real model, offline) | ENTAILED 0.9988 / CONTRADICTED 0.9994 |
| NLI heuristic fallback | **contradiction → ENTAILED 0.90** |
| `dspy.configure` / `dspy.LM` in `backend/src` | **0 occurrences** |
| `claim_verified` / `citation_created` / `outline` / `perspectives` in `backend/src` | **0 occurrences** |
| Client tests | **not executed** — no Flutter/Dart toolchain on this machine |

**Unverified / out of scope:** the Flutter client's own test suite could not be run (no `flutter` or `dart` on PATH); the client analysis is from source reading and a contract diff against `server/routes.py`. The Word module's Microsoft-Word COM conformance harness (`tests/test_word_real_word.py`) is skip-gated and was not exercised.
