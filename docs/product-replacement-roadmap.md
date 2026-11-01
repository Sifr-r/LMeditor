# LMeditor roadmap: replacing Word, Obsidian and NotebookLM

Date: 26 October 2026  
Status: the backlog of record; unchecked items are not completion claims. M0 (contracts and evidence) is delivered under [`docs/replacement/`](replacement/README.md).  
Scope: one application for document authoring, personal knowledge management and source-grounded research.

Implementation progress — 26 October 2026: the first M1 slice now persists source
records, bibliographic metadata and extracted chunks across backend/bridge
restarts. The 256-entry bound applies to the memory cache; archived evidence is
retained. Notebook Ask identifies unavailable source IDs and refuses partial
corpora before inference. See [the implementation blueprint](source-persistence-blueprint.md).
F.02 and N1.01 remain in progress: original upload bytes, source version history,
changed-source detection and desktop restart validation are still outstanding.

## 1. What success means

LMeditor should let a person collect sources, understand them, maintain linked notes, write and review finished documents, and export or share their work without routinely returning to Microsoft Word, Obsidian or NotebookLM.

Replacement requires complete workflows, reliable storage and compatible files. Having three workspace screens or being able to parse a format is insufficient. Existing rich OOXML serialization does not establish rich editing, and a citation does not establish that every claim is supported.

Deliver in two explicitly named stages:

- **Daily replacement:** dependable local document editing, portable linked notes, durable source-grounded research, and the integrated workflow between them.
- **Broad functional replacement:** advanced publishing and review, visual knowledge tools, research studio outputs, multi-device use and collaboration. Do not claim broad replacement while these agreed capabilities remain absent.

Exact duplication of every third-party plugin, VBA macro, Microsoft cloud integration or product-specific AI behavior is not a credible default requirement. Maintain an explicit compatibility register for unsupported features, preservation behavior and alternatives. These exceptions must be visible before making a replacement claim.

## 2. Current baseline, based on this working tree

This assessment inspected source and existing test definitions. M0 has since executed the backend suite and compiled the Kotlin tier, and both are now measured rather than inferred: the backend suite reports **1361 passed, 0 skipped** on 26 October 2026, the Kotlin tier compiles offline, and Microsoft Word is installed on this machine, so the Office-gated conformance tests ran instead of skipping. The desktop application was **not** launched, and nothing here certifies production readiness. Existing uncommitted work is included in the baseline. Historical claims in `GAP_ANALYSIS.md` and older architecture sections must be checked against current Kotlin/Python code.

Every requirement below carries a stable identifier — `F.01`, `W1.03`, `R.12` — and the registry at [`docs/replacement/requirements.json`](replacement/requirements.json) carries that identifier's owner, milestone, status, observable gate and evidence. The M0 artifacts are indexed in [`docs/replacement/README.md`](replacement/README.md).

| Area | Existing foundation | Remaining replacement gap | Evidence |
| --- | --- | --- | --- |
| Documents | DOCX import, section text editing, explicit save and DOCX export | Structured rich editing, creation, pagination, object authoring and review UI | `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt`; `model/DocumentWorkspace.kt` in the same desktop source tree |
| Word compatibility | Rich OOXML model and reader/writer; book export preserves imported structure where supported | Prove fidelity during edits, distinguish preserved objects from editable objects, disclose omissions | `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt`; `kotlin/library/src/main/kotlin/lmeditor/library/BookExporter.kt` |
| Vault | Markdown files, note CRUD, tags, basic properties, links/backlinks and substring search | Rendered editing, deeper Markdown compatibility, indexing, conflicts, history and visual tools | `kotlin/vault/src/main/kotlin/lmeditor/vault/{VaultStore,Markdown,VaultSearch}.kt`; desktop `ui/VaultScreen.kt` |
| Notebooks | Persistent manifests, source/note membership and source-scoped question answering | Durable source archive, conversations, full evidence coverage, source reader and studio artifacts | `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt`; `backend/src/lmeditor/notebooks/answer.py` |
| Source lifecycle | Ingestion, provenance and persistent extracted-source records with a bounded memory cache | Original upload retention, source revision history and reference-aware original storage still need implementation | `backend/src/lmeditor/server/sources.py`; `kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt`; `kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt` |
| Research continuity | Workflow events, verification and human review gates | Default in-memory checkpoints and process session state need restart recovery | `backend/src/lmeditor/workflow/graph.py`; `backend/src/lmeditor/server/session.py` |
| Desktop delivery | Compose desktop with HTTP/SSE transport and declared installer targets | Installed-app startup, bundled services, upgrades, recovery and support need release evidence | `kotlin/desktop/README.md`; `kotlin/desktop/build.gradle.kts` |

Particularly important: notebook `note_ids` currently record membership, but the Ask path retrieves `source_ids`, not note contents. Also, `answered=true` currently requires at least one supported citation; it must not be presented as proof of complete answer support.

## 3. Shared foundation: build first

- [ ] **F.01** Durable workspace identity: stable workspace, document, note, notebook, source and artifact IDs; explicit ownership, references and schema versions. Store portable relative paths where possible.
- [ ] **F.02** Durable source archive: retain originals, extracted text, chunks, metadata, locators and source revisions. Make retrieval indexes rebuildable. Evict caches without evicting material referenced by a notebook.
- [ ] **F.03** Safe saves: atomic replacement, revision preconditions, contextual errors and recoverable multi-file operations. The notebook manifest and the whole book store already replace atomically, but neither flushes to disk, so both are atomic against process death only; extend the guarantee to vault writes, link rewrites and the upload store, and add the revision preconditions and the durability flush.
- [ ] **F.04** Recovery: autosave, draft journals, crash recovery, version restore, backup/export and tested restoration. Define retention and deletion behavior for originals, indexes and backups.
- [ ] **F.05** External changes: detect changes made by another editor or sync client; compare revisions and offer a merge or conflict copy rather than overwriting drafts.
- [ ] **F.06** Persistent jobs: store generation/ingestion progress and review checkpoints; support cancellation, safe retries and restart recovery without duplicate artifacts.
- [ ] **F.07** Unified navigation: recent work, tabs/split panes, command palette, quick open, configurable shortcuts and search across documents, notes, sources and notebooks. Keep search scope visible.
- [ ] **F.08** Local operation: ordinary editing, reading and lexical search work without an AI provider or internet. Clearly distinguish local inference, cloud inference and offline stub output.
- [ ] **F.09** Usable delivery: launch the desktop and required local services together, manage their lifecycle, offer settings and diagnostics, and package without requiring developer commands.
- [ ] **F.10** Accessibility: keyboard completion of every core workflow, screen-reader semantics, focus management, contrast/zoom, IME, Unicode and RTL coverage.

## 4. Microsoft Word replacement backlog

### W1. Structured editing and document creation — daily replacement

- [ ] **W1.01** Create blank documents and documents from templates; edit titles, sections and outline structure.
- [ ] **W1.02** Introduce a persistent block/run model with stable anchors and edit operations. A section `String` must not be the authoritative model for rich edits.
- [ ] **W1.03** Support selection formatting, paragraph styles, headings, font settings, lists/numbering, alignment, spacing, indentation and tab stops.
- [ ] **W1.04** Add undo/redo, rich clipboard operations, drag/drop, find/replace, navigation and document statistics.
- [ ] **W1.05** Allow independent human writing without requiring AI generation, ingestion or a provider configuration.
- [ ] **W1.06** Map edits to existing rich nodes so changing a word preserves surrounding formatting, links, comments, revisions and objects.

**Gate:** create a document, format selected text, reorder sections, undo/redo, save and reopen. Editing one word in an imported rich paragraph preserves unrelated styles and anchored objects in exported DOCX.

### W2. Layout, objects and publication — daily replacement

- [ ] **W2.01** Implement real page layout: page size, margins, orientation, breaks, sections, headers/footers and page numbers. Current paper-like presentation is not pagination.
- [ ] **W2.02** Add visual table creation/editing, row/column operations, merging, widths and repeating headers.
- [ ] **W2.03** Add image insertion, sizing, placement, captions and accessible descriptions; define supported wrapping behavior.
- [ ] **W2.04** Author hyperlinks, bookmarks, cross-references, footnotes/endnotes and equations.
- [ ] **W2.05** Provide automatic table of contents, bibliography and citation-style handling with editable metadata.
- [ ] **W2.06** Add print preview, printing and PDF export using the same documented layout rules.
- [ ] **W2.07** Add spellcheck, dictionaries, language settings and optional grammar suggestions.
- [ ] **W2.08** Maintain a DOCX feature matrix: read, display, edit, preserve, export and known limitations for each feature.

**Gate:** produce and export a mixed-content report with tables, images, notes, contents and multiple page sections. Reopen in real Word without repair warnings; compare semantic structure and rendered pages against approved fixtures.

### W3. Review and advanced workflows — broad replacement

- [ ] **W3.01** Create and reply to anchored comments; resolve and navigate discussions.
- [ ] **W3.02** Author tracked insertions/deletions/formatting changes; accept/reject changes individually and in batches.
- [ ] **W3.03** Compare document versions and review proposed AI edits as explicit changes.
- [ ] **W3.04** Support reusable styles/templates, advanced fields, forms/content controls and mail merge where included in the supported workflow set.
- [ ] **W3.05** Add coauthoring, presence, role-based permissions and conflict-safe collaboration after the local operation model is stable.
- [ ] **W3.06** Document legacy `.doc` conversion, macro handling, embedded objects and unsupported layout features. Preserve safely or warn; never imply executable macro compatibility.

**Gate:** complete a review cycle entirely in-app and exchange the result with Word with equivalent comments/revisions. Demonstrate collaborative convergence and permission enforcement before enabling shared editing.

## 5. Obsidian replacement backlog

### O1. Portable everyday notes — daily replacement

- [ ] **O1.01** Add Markdown source, reading and live-preview modes; preserve original text where no edit is requested.
- [ ] **O1.02** Render tables, code fences, math, callouts, task lists, links and local media safely.
- [ ] **O1.03** Complete wiki-link aliases, heading/block references, transclusion and link completion; test ambiguous names and unresolved links.
- [ ] **O1.04** Manage attachments, pasted images and relative links without moving files unexpectedly.
- [ ] **O1.05** Support general typed frontmatter properties, aliases, property editing and filtering without destroying unfamiliar YAML.
- [ ] **O1.06** Add daily notes, reusable templates, quick capture, bookmarks, navigation history and saved searches.
- [ ] **O1.07** Make rename/move plus backlink rewriting recoverable as one operation; add trash and restore.
- [ ] **O1.08** Watch filesystem changes and maintain a rebuildable incremental search/link index. Avoid repeatedly loading the whole vault for routine interaction.
- [ ] **O1.09** Add path/tag/property/full-text search with explicit query semantics, snippets and filters.

**Gate:** open a representative existing vault in place; leave unsupported files unchanged; edit and rename linked notes without broken references; observe external edits without losing drafts; recover after interruption during a rename.

### O2. Visual organization and ecosystem — broad replacement

- [ ] **O2.01** Add navigable local/global graph views with filters, grouping and usable large-vault limits.
- [ ] **O2.02** Add a canvas with cards, connections, attachments and portable JSON Canvas import/export.
- [ ] **O2.03** Add property-driven table/card views for structured collections, with deterministic filtering and sorting.
- [ ] **O2.04** Provide multi-device sync with offline edits, attachment synchronization, conflict resolution, version recovery and clear privacy settings.
- [ ] **O2.05** Define an extension/command API and permissions model if plugins are supported; list supported Obsidian workflows and alternatives rather than claiming arbitrary plugin compatibility.
- [ ] **O2.06** Support publishing or controlled sharing of selected notes, with attachment and private-link checks.

**Gate:** use a 10,000-note fixture to demonstrate responsive navigation/search and incremental updates; round-trip a canvas; resolve simultaneous offline changes on two devices without silent data loss.

## 6. NotebookLM replacement backlog

### N1. Dependable source-grounded research — daily replacement

- [ ] **N1.01** Restore all notebook sources after backend/desktop restarts. Report missing or changed sources explicitly instead of silently searching a smaller corpus.
- [ ] **N1.02** Add a source reader with exact page, section, character-span or timestamp navigation and highlighted evidence.
- [ ] **N1.03** Offer per-question source selection and clear corpus scope; support source summaries and source comparisons.
- [ ] **N1.04** Include linked vault notes as evidence only when explicitly selected; bind each answer to the note's identity and content revision.
- [ ] **N1.05** Persist multi-turn conversations, selected sources, prompts/settings, answers, citations and feedback. Keep old answers bound to their original source snapshots.
- [ ] **N1.06** Add streamed answers, cancellation, retries, suggested follow-ups and source-management progress/errors.
- [ ] **N1.07** Evaluate material claims individually: supported, contradicted or insufficient evidence. Show partial support rather than marking the whole answer verified because one citation survives.
- [ ] **N1.08** Distinguish retrieval relevance, exact citation identity and semantic support; record the evaluator/model used and degraded modes. Automated scores are fallible evidence, not mathematical proof of truth.
- [ ] **N1.09** Add contradiction handling, cross-source synthesis and reliable abstention. Source instructions must be treated as data rather than authority over the assistant.
- [ ] **N1.10** Persist retrieval indexes and benchmark retrieval recall and citation precision on a curated answerable/unanswerable corpus.

**Gate:** continue a multi-turn investigation after restart, cite the exact originals, exclude deselected sources, expose missing evidence and refuse unsupported questions. Evaluate answers at claim level, including distractors and conflicting sources.

### N2. Ingestion and study studio — broad replacement

- [ ] **N2.01** Expand ingestion coverage: scanned PDFs/OCR, slides, EPUB, HTML/web pages and pasted text, with explicit extraction quality and omissions.
- [ ] **N2.02** Add audio transcription and video/YouTube transcript ingestion where access permits, retaining timestamps and original references.
- [ ] **N2.03** Add opt-in web discovery and external document connectors; capture versions rather than letting refreshed sources silently alter old answers.
- [ ] **N2.04** Generate editable summaries, briefing documents, FAQs, study guides and comparison/data tables, with links to evidence.
- [ ] **N2.05** Add mind maps, flashcards, quizzes and study progress; check answer keys against evidence.
- [ ] **N2.06** Add audio overviews with source-linked scripts, playback, downloads and regeneration controls.
- [ ] **N2.07** Add video overviews, slide decks and infographics as later studio capabilities; preserve scripts, references and generation settings.
- [ ] **N2.08** Persist artifact jobs, provenance, versions and exports. Generated artifacts must be editable or replaceable, not transient chat decorations.
- [ ] **N2.09** Add notebook sharing with reader/editor roles, source access controls, revocation and export permissions.
- [ ] **N2.10** Display provider usage, estimated cost, quotas and privacy choices before expensive or remote operations.

**Gate:** generate a study pack and audio overview from a notebook, reopen them after restart, inspect their evidence and export them. Shared users cannot access sources outside their permissions.

## 7. Make the three replacements one product

- [ ] **P.01** Define a shared project/workspace manifest linking documents, vault notes, notebooks and sources through stable IDs rather than fragile display titles.
- [ ] **P.02** Promote a quoted source passage or answer into a vault note with its locator and source revision.
- [ ] **P.03** Promote selected notes/research into an outline and then an editable document, retaining provenance across transformations.
- [ ] **P.04** Add a document or selected section to notebook research without duplicating originals unnecessarily; make snapshot versus live-link behavior explicit.
- [ ] **P.05** Navigate from manuscript claim to evidence and back; indicate when an edit makes a citation stale or breaks support.
- [ ] **P.06** Export a portable project bundle containing notes, documents, source originals, artifacts and a manifest; test import on a clean installation.

**End-to-end gate:** import sources, investigate them, save a linked note, build an outline, write and review a report, and export DOCX/PDF. Restart during the process, recover safely, and trace final claims back to the original source versions.

## 8. Architecture and directory blueprint

Extend existing domains. Kotlin owns deterministic rules; Python owns inference, embeddings and orchestration; FastAPI/bridge remain adapters; Compose owns interaction and presentation. Do not duplicate deterministic rules in the UI or Python facades.

The paths below are proposed responsibilities, not files created by this document. Class names and contracts must be settled in each implementation blueprint before parallel dispatch.

| Proposed path / existing owner | Single responsibility |
| --- | --- |
| `kotlin/core/.../WorkspaceSchemas.kt` | Versioned shared IDs, references and workspace manifest contracts |
| `kotlin/core/.../RevisionSchemas.kt` | Revision preconditions and typed conflict results |
| `kotlin/library/.../WorkspaceStore.kt` | Durable workspace manifest and reference resolution |
| `kotlin/library/.../RevisionStore.kt` | Revision snapshots, recovery journal and restore rules |
| `kotlin/word/.../DocumentEdit.kt` | Validated structured document operations and stable anchor updates |
| `kotlin/word/.../DocumentLayout.kt` | Deterministic pagination/layout contracts, separate from OOXML conversion |
| `kotlin/vault/.../VaultIndex.kt` | Rebuildable incremental note/property/link index |
| `kotlin/vault/.../VaultChanges.kt` | External-change reconciliation and recoverable note moves |
| `kotlin/server/.../SourceArchive.kt` | Durable source originals, revisions, metadata and chunk manifests |
| `kotlin/notebooks/.../ConversationStore.kt` | Persistent conversation turns and evidence snapshots |
| `kotlin/notebooks/.../ArtifactStore.kt` | Persistent artifact metadata, provenance and lifecycle |
| `backend/src/lmeditor/notebooks/answer.py` | Orchestrate source-scoped answer generation using shared evidence rules |
| `backend/src/lmeditor/notebooks/artifacts.py` | Orchestrate research/study artifact generation through typed jobs |
| `backend/src/lmeditor/ingestion/` | Extraction/transcription adapters feeding existing deterministic ingestion rules |
| `backend/src/lmeditor/server/` | Thin authenticated HTTP/job adapters with domain error mapping |
| `kotlin/bridge/.../Handlers*.kt` | Thin typed bridge dispatch to domain operations |
| `kotlin/desktop/.../model/{documents,vault,notebooks}/` | Separate workspace state/actions; keep the central controller as composition/root routing |
| `kotlin/desktop/.../ui/{documents,vault,notebooks}/` | Feature-specific interaction surfaces and accessibility semantics |
| `backend/tests/`; `kotlin/*/src/test/` | Domain, transport, compatibility and recovery acceptance evidence |

Here `...` expands to each existing `src/main/kotlin/lmeditor/<module>/` source root. Do not create literal ellipsis directories. Keep new responsibilities in focused files rather than extending a single controller, route collection or screen indefinitely.

**Dependency decision:** `kotlin/CONTRACTS.md` currently forbids third-party runtime dependencies in tier modules, with `:desktop` the named exemption. Before committing to rich layout, OCR, sync, media or persistent indexing technology, record a feasibility spike, maintenance cost and any proposed contract change. Do not silently add dependencies or promise a full publishing engine under an untested constraint.

## 9. Delivery sequence and parallel ownership

| Milestone | Deliverable | Dependencies / parallel work | Exit condition |
| --- | --- | --- | --- |
| M0 — contracts and evidence | Supported-workflow matrix, feature inventory, migration/recovery contract and representative fixtures | Architecture lead plus document, knowledge and research reviewers | Each replacement requirement has an owner and an observable gate; editor/layout feasibility is resolved |
| M1 — trustworthy foundation | Archive, revisions, conflicts, recovery, persistent jobs and installed startup | Storage and desktop lifecycle work can run together after shared contracts | Restart/crash/restore tests preserve acknowledged work and notebook evidence |
| M2 — daily replacement | W1/W2, O1, N1 and integrated local workflows | Document, vault and notebook agents implement separate files against M1 interfaces | Daily workflows pass on representative real user fixtures with no hidden data loss |
| M3 — broad workflows | W3, O2, N2, sharing and sync | Domain streams proceed independently; permissions and sync share a reviewed protocol | Review, visual knowledge, study/media and multi-device workflows pass |
| M4 — release qualification | Installer/update/migration, compatibility, accessibility and performance evidence | Independent verification after domain integration | Clean-machine trial and integrated acceptance suite pass; compatibility exceptions are published |

For every implementation wave, publish the exact file ownership and shared typed interfaces first. Then dispatch independent domain subagents in parallel. Require boundary validation, contextual domain errors, strict typing, idempotent retry semantics and focused tests. Integrate and review each artifact for dependency direction and single responsibility; update `ARCHITECTURE.md` with every new file. Serialize schema/shared-file edits and contract migrations to avoid conflicting implementations.

Dates and staffing estimates require the M0 feasibility results; this is a dependency-ordered backlog, not a delivery-time commitment.

## 10. Release evidence and operational requirements

- [ ] **R.01** Maintain a compatibility corpus of real DOCX files and vaults, including corrupt inputs, unsupported features, Unicode, large assets and difficult layouts. Test edited as well as untouched round trips.
- [ ] **R.02** Compare exported semantic structure and rendered pages, and validate with actual Word where available. Skipped Office-dependent tests are unverified compatibility, not a pass.
- [ ] **R.03** Fault-inject disk-full, permission errors, process termination, cancellation, duplicate requests, interrupted migration and partial network failure. Preserve acknowledged edits; leave conflicts recoverable.
- [ ] **R.04** Test import/export/migration reversibility and restore backups on a clean installation before schema upgrades ship.
- [ ] **R.05** Benchmark a 100-page mixed-content document, a 10,000-note vault and a notebook with 100 substantial sources. Set measured latency/memory budgets in M0; suggested initial targets are p95 local indexed search under 500 ms and no sustained UI blocking during background work, on recorded reference hardware.
- [ ] **R.06** Separate local-interaction latency from model generation latency; test long-running jobs without freezing editing/navigation.
- [ ] **R.07** Validate limits and sanitize boundaries: filesystem confinement, symlinks, ZIP/XML abuse, extraction bounds, unsafe rendered content and URL/redirect SSRF.
- [ ] **R.08** Protect the local gateway with an installation/session credential and controlled origins; the origin half is implemented — the wildcard CORS the configuration used to permit was replaced by a named allowlist, with `allow_credentials=False`, an explicit method list, and a 403 refusal for a mutating request from a foreign origin — but the installation credential is not. Remote use needs authenticated identities, authorization and encrypted transport.
- [ ] **R.09** Keep secrets in OS-protected storage; make remote-provider data transmission explicit. Redact sensitive document content from diagnostics by default.
- [ ] **R.10** Build access checks into retrieval, artifact export and source downloads; deleting or revoking a source must invalidate future access without rewriting historical evidence silently.
- [ ] **R.11** Offer reproducible diagnostics, clear storage/index status and safe repair/rebuild actions.
- [ ] **R.12** Validate installation, first launch, offline editing, service restart, upgrade and uninstall with data retention on each supported platform.

**Definition of done for a backlog item:** the behavior is available in the UI when user-facing, persists correctly, has relevant automated/manual evidence, handles its failure cases, documents compatibility limits, and is recorded in the architecture ledger. A helper, parser or endpoint alone does not complete the workflow.

## 11. Reference baselines

Official product documentation checked on 26 October 2026. These establish representative capabilities, not an exhaustive feature-equivalence certification. Roadmap priorities and implementation choices above are recommendations based on this repository.

- Word review: [Track changes in Word](https://support.microsoft.com/en-us/word/training/track-changes-in-word).
- Word layout: [Page numbers in headers and footers](https://support.microsoft.com/en-us/word/add-page-numbers-to-a-header-or-footer-in-word).
- Word accessibility: [Accessibility Assistant](https://support.microsoft.com/en-us/accessibility/word/make-your-word-documents-accessible-to-everyone-with-accessibility-assistant).
- Obsidian visualization: [Graph view](https://help.obsidian.md/Plugins/Graph+view) and [Canvas](https://help.obsidian.md/Plugins/Canvas), including the open JSON Canvas format.
- NotebookLM research/studio baseline: [Create a notebook](https://support.google.com/notebooklm/answer/16206563?hl=en); the current help page redirects to Google's Gemini Notebook documentation and lists research/study/media outputs.
- NotebookLM audiovisual sources: [Audio and YouTube source support](https://blog.google/innovation-and-ai/products/notebooklm-audio-video-sources/).

This roadmap complements the existing workspace blueprints. It supersedes their sufficiency claims where those equate a basic document/vault/notebook surface with full replacement; it does not erase their implementation history.
