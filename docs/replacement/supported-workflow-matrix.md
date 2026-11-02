# Supported workflow matrix

Date: 2 November 2026

Status: the human-readable view of [`requirements.json`](requirements.json), which is the machine-readable registry of the same requirements.
Scope: one row per requirement of [`docs/product-replacement-roadmap.md`](../product-replacement-roadmap.md).

---

## 1. How to read this matrix

Each row is one requirement of the roadmap. The **ID** is the marker the roadmap itself carries, and it is the join key between the two files: the roadmap's prose and this matrix are two renderings of the same set of identifiers, and `backend/tests/test_replacement_requirements.py` fails if they ever stop agreeing.

The **Requirement** column is the requirement's subject. The **Observable gate** is the single check that will decide whether the requirement is done — an action and an artifact or result, never a state of mind. The **Evidence** column names the files in this tree that back the status, each as `path:line`; an empty evidence cell means the claim is an absence, established by reading the tree rather than by a file.

A gate is what a *future* state will be checked against. It is not a description of what exists today — the status column says that.

---

## 2. The stage vocabulary

A requirement's stage is the milestone **by which it must be complete**, taken verbatim from the roadmap's §9 delivery sequence. It is not the milestone the work starts in: `R.01` is seeded by M0 and completes in M4, and `P.06` completes in M3 because its clean-installation import test is `R.04` in M4.

| Stage | Milestone |
| :--- | :--- |
| `M0` | M0 — contracts and evidence |
| `M1` | M1 — trustworthy foundation |
| `M2` | M2 — daily replacement |
| `M3` | M3 — broad workflows |
| `M4` | M4 — release qualification |

The stages are a sequence, not a schedule. Roadmap §9 defers every date and staffing estimate to M0's feasibility results.

---

## 3. The four status values

- **`not-started`** — no implementation of the requirement exists in the tree. Its evidence list is empty, and that emptiness is the claim: the search that established the absence is recorded in the gate, not in an evidence entry.
- **`in-progress`** — a partial implementation exists. The evidence names the files that carry it, and the gate names the part of the requirement those files do not yet cover.
- **`evidence-partial`** — the behavior exists but the acceptance evidence does not yet cover it. A skipped test is the usual cause: it is recorded as *unverified*, never as verified.
- **`complete`** — the requirement meets all six parts of roadmap §10's definition of done: available in the UI when user-facing, persists correctly, has relevant automated or manual evidence, handles its failure cases, documents its compatibility limits, and is recorded in the architecture ledger.

### What a status claims, and what it does not

A status claims **one thing**: the state of this requirement in this working tree, at the date above, measured against its gate. It does not claim that the requirement is finished, that the product replaces Word, Obsidian or NotebookLM, or that the roadmap's checkbox has been ticked.

The roadmap's own status line says that unchecked items are not completion claims, and this registry holds that line. An unchecked box is not evidence that nothing has been built — it is evidence that nothing has been claimed. The distinction is why this file exists at all: the registry says what is evidenced, and the checklist says what has been signed off. They are different claims, and neither is derived from the other.

A `complete` status in particular is a claim about six things at once, not one. If any of the six cannot be shown, the status is at best `evidence-partial`, and a skipped test is never evidence at all.

---

## 4. The owner notation

An owner is written `<tier>:<module>` and names the module that **owns the rule**, not the module that merely shows it. A rule that must hold in the user interface is owned by the tier module that holds it; the desktop client appears in the gate instead.

| Prefix | Meaning |
| :--- | :--- |
| `kotlin:` | A Kotlin tier module: `core`, `text`, `word`, `vault`, `notebooks`, `library`, `server`, `workflow`, `ingest`, `pdf`, `editing`, `factcheck`, `discovery`, `bridge`, `desktop`. |
| `python:` | A Python package: `core`, `word`, `library`, `ingestion`, `provenance`, `notebooks`, `workflow`, `server`, `verification`, `drafting`, `vault`, `export`, `assistant`, `feedback`, `llm`, `tier`, `bridge`. |
| `cross:` | Work that spans tiers. The suffix names the coordinating domain: `documents`, `notes`, `research`, `delivery`, `evidence`. |

---

## 5. The requirements

### Track F — Shared foundation (roadmap §3)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `F.01` | Durable workspace identity | `M1` | `kotlin:core` | `in-progress` | Create a workspace, a document, a note, a notebook and a source through the public API, then read back each assigned id and its schema version. | kotlin/core/src/main/kotlin/lmeditor/core/Schemas.kt:1573; kotlin/core/src/main/kotlin/lmeditor/core/VaultSchemas.kt:27; kotlin/core/src/main/kotlin/lmeditor/core/NotebookSchemas.kt:26 |
| `F.02` | Durable source archive | `M1` | `kotlin:server` | `in-progress` | Ingest a source, restart the backend, then read back the retained original, its chunks and its metadata from the durable source archive. | kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50; kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:15; kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28; backend/tests/test_source_persistence.py:1 |
| `F.03` | Safe atomic saves | `M1` | `kotlin:core` | `in-progress` | Interrupt a vault note write and a notebook manifest write, then read both files back and find the previous contents intact. | kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:470; kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883; kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:485 |
| `F.04` | Recovery and version restore | `M1` | `kotlin:library` | `not-started` | Kill the process mid-edit, restart it, then restore the acknowledged draft and the previous version from the recovery journal. | — |
| `F.05` | External change detection | `M1` | `kotlin:vault` | `not-started` | Change a note on disk behind the editor, reopen it, then read a conflict copy instead of the overwritten draft. | — |
| `F.06` | Persistent job progress | `M1` | `kotlin:workflow` | `in-progress` | Cancel a running generation job, restart the service and resume the run, then read back that no duplicate artifact was written. | backend/src/lmeditor/workflow/graph.py:147; kotlin/workflow/src/main/kotlin/lmeditor/workflow/HITLRegistry.kt:57 |
| `F.07` | Unified navigation across workspaces | `M1` | `kotlin:desktop` | `not-started` | Open the command palette, quick-open a document, a note and a notebook, then read back the visible search scope for each. | — |
| `F.08` | Local operation without a provider | `M1` | `python:llm` | `in-progress` | Turn the provider off, edit a document and run a lexical search, then read the run log and find the stub named as the provider. | backend/src/lmeditor/llm/stub.py:16; backend/src/lmeditor/llm/factory.py:48 |
| `F.09` | Installable local delivery | `M1` | `cross:delivery` | `in-progress` | Build the desktop package, install it on a clean machine, then start the app and the local services from the installed launcher. | kotlin/desktop/README.md:76; kotlin/desktop/build.gradle.kts:49 |
| `F.10` | Accessible operation of every workflow | `M1` | `kotlin:desktop` | `not-started` | Complete a document edit, a note edit and a notebook answer with the keyboard alone, then read the screen-reader semantics of each step. | — |

### Track W1 — Structured editing and document creation (§4)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `W1.01` | Document creation and outline editing | `M2` | `kotlin:library` | `in-progress` | Create a book, add a titled section, rename it, reorder it and reopen the book, then read back the section order. | kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:188; kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:295 |
| `W1.02` | Persistent block and run model | `M2` | `kotlin:word` | `in-progress` | Edit a paragraph through the document operation API, then read back the anchored runs and confirm no section string became the authority. | kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:671; kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/DocumentWorkspace.kt:37 |
| `W1.03` | Selection formatting and paragraph styles | `M2` | `kotlin:word` | `not-started` | Select a span in a paragraph, apply bold, a heading style and a list level, then read back the run and paragraph properties. | — |
| `W1.04` | Undo, clipboard, find and statistics | `M2` | `cross:documents` | `not-started` | Undo and redo an edit, paste rich text, run a find and replace, then read back the document word count. | — |
| `W1.05` | Independent human writing | `M2` | `cross:documents` | `in-progress` | Write a section, save it and reopen it with no provider configured, then read the run log and find no model call. | kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt:68; kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/DocumentWorkspace.kt:37 |
| `W1.06` | Edits preserve surrounding rich structure | `M2` | `kotlin:word` | `not-started` | Change one word in an imported rich paragraph, export the DOCX, then read back that the styles, links and anchored objects around it survived. | — |

### Track W2 — Layout, objects and publication (§4)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `W2.01` | Real page layout and pagination | `M2` | `kotlin:word` | `not-started` | Lay out a 100-page document with margins, sections and page numbers, then read back the page break positions from the layout result. | — |
| `W2.02` | Visual table editing | `M2` | `kotlin:word` | `in-progress` | Add a row and a column to a table, merge two cells and repeat the header row, then read back the resulting cell placements. | kotlin/word/src/main/kotlin/lmeditor/word/WordTableLayout.kt:82; kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:490 |
| `W2.03` | Image insertion and placement | `M2` | `kotlin:word` | `in-progress` | Insert a PNG into a document with a caption and a description, export the DOCX, then read back the drawing and its extent. | kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:574; kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:291 |
| `W2.04` | Links, footnotes and cross-references | `M2` | `kotlin:word` | `in-progress` | Author a hyperlink, a bookmark and a footnote in a document, then read them back from the exported DOCX package. | kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:606; kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:1799 |
| `W2.05` | Table of contents and bibliography | `M2` | `cross:documents` | `in-progress` | Insert a table of contents and a bibliography into a document, then read back the resolved citation metadata for each entry. | kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:201; kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:505 |
| `W2.06` | Print preview and PDF export | `M2` | `kotlin:pdf` | `not-started` | Export a laid-out document to PDF from the print preview, then reopen the PDF and read back the page count and the text of each page. | — |
| `W2.07` | Spellcheck and dictionaries | `M2` | `python:assistant` | `not-started` | Type a misspelled word in a paragraph, then read back the dictionary suggestions and the document language setting. | — |
| `W2.08` | DOCX feature matrix | `M2` | `kotlin:word` | `not-started` | Publish the DOCX feature matrix, then read one row per feature carrying read, display, edit, preserve, export and a stated limitation. | — |

### Track W3 — Review and advanced workflows (§4)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `W3.01` | Anchored comments and discussions | `M3` | `kotlin:word` | `in-progress` | Anchor a comment on a paragraph, write a reply, resolve it and reopen the DOCX, then read the thread back from the package. | kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:637; kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:1145 |
| `W3.02` | Tracked changes authoring | `M3` | `kotlin:word` | `in-progress` | Insert and delete text with tracking on, export the DOCX, then read back the revision elements with their author and date. | kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:999; kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:355 |
| `W3.03` | Version comparison and AI edit review | `M3` | `kotlin:editing` | `not-started` | Compare two saved versions of a document and read the differing sections, then read a proposed AI edit back as an explicit change. | — |
| `W3.04` | Styles, fields, forms and mail merge | `M3` | `kotlin:word` | `not-started` | Apply a reusable style set, insert a field and a content control, then read the definitions back from the exported DOCX. | — |
| `W3.05` | Coauthoring, presence and permissions | `M3` | `cross:documents` | `not-started` | Two clients edit one document under different roles, then read the convergence record and the denied write in the server log. | — |
| `W3.06` | Legacy doc and macro handling | `M3` | `kotlin:word` | `not-started` | Open a legacy .doc file, then read the compatibility note that names the macro and embedded-object limits of the import. | — |

### Track O1 — Portable everyday notes (§5)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `O1.01` | Markdown source and reading modes | `M2` | `kotlin:vault` | `in-progress` | Open a note in source and reading modes and switch between them, then read the markdown back unchanged. | kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:102; kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/VaultScreen.kt:74 |
| `O1.02` | Safe rendering of markdown constructs | `M2` | `kotlin:vault` | `not-started` | Render a note holding a table, a code fence, a callout and a task list, then read back the rendered blocks and the escaped raw HTML. | — |
| `O1.03` | Wiki-link aliases and references | `M2` | `kotlin:vault` | `in-progress` | Follow an aliased wiki-link, a heading reference and a block reference, then read back the resolved target and the unresolved-link report. | kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:182; kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:275 |
| `O1.04` | Attachments and relative links | `M2` | `kotlin:vault` | `not-started` | Paste an image into a note and link to a file beside it, then read back the attachment path and confirm no file moved. | — |
| `O1.05` | Typed frontmatter properties | `M2` | `kotlin:vault` | `in-progress` | Write a note with typed frontmatter properties, reopen it, then read back the property values with the unknown YAML left intact. | kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt:44 |
| `O1.06` | Daily notes, templates and saved searches | `M2` | `kotlin:vault` | `not-started` | Create a daily note from a template and save a search, then reopen the vault and read both back by name. | — |
| `O1.07` | Recoverable rename with backlink rewriting | `M2` | `kotlin:vault` | `in-progress` | Rename a linked note, interrupt the rename, then read back the note, its rewritten backlinks and the trash entry that restores it. | kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:186; kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:153 |
| `O1.08` | Incremental vault index and file watching | `M2` | `kotlin:vault` | `not-started` | Change one note on disk, wait for the watcher, then read back the updated index entry and the vault load count for the session. | — |
| `O1.09` | Vault search with query semantics | `M2` | `kotlin:vault` | `in-progress` | Run a path, tag and full-text query, then read back the ranked hits, their snippets and the filters that were applied. | kotlin/vault/src/main/kotlin/lmeditor/vault/VaultSearch.kt:69; kotlin/vault/src/main/kotlin/lmeditor/vault/VaultSearch.kt:19 |

### Track O2 — Visual organization and ecosystem (§5)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `O2.01` | Graph views of the vault | `M3` | `kotlin:vault` | `not-started` | Open the local and global graph view of a 10,000-note vault, then read back the rendered node count and the applied filters. | — |
| `O2.02` | Canvas with portable JSON export | `M3` | `kotlin:vault` | `not-started` | Build a canvas of cards and connections, export it as JSON Canvas and import it back, then read the card set on both sides. | — |
| `O2.03` | Property-driven table and card views | `M3` | `kotlin:vault` | `not-started` | Define a table view over a note property, then read back the rows in the same order after a restart. | — |
| `O2.04` | Multi-device sync with offline edits | `M3` | `cross:notes` | `not-started` | Edit the same note offline on two devices, reconnect them, then read back the merged result with no silent data loss. | — |
| `O2.05` | Extension and command API | `M3` | `cross:delivery` | `not-started` | Install a minimal extension through the declared API, then read back the permission it was granted and the commands it registered. | — |
| `O2.06` | Publishing selected notes | `M3` | `kotlin:vault` | `not-started` | Publish a selected note with its attachments, then read back the published link and the private-link check that ran. | — |

### Track N1 — Dependable source-grounded research (§6)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `N1.01` | Notebook sources survive a restart | `M2` | `kotlin:notebooks` | `in-progress` | Ask a notebook, restart the backend and desktop, then ask again and read the explicit report of any missing or changed source. | kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28; backend/src/lmeditor/notebooks/answer.py:91; backend/tests/test_source_persistence.py:58; backend/tests/test_notebook_routes.py:380 |
| `N1.02` | Source reader with exact locators | `M2` | `kotlin:text` | `in-progress` | Open a source in the reader, jump to a page and a character span, then read back the highlighted excerpt and its offsets. | kotlin/text/src/main/kotlin/lmeditor/text/TextOffsets.kt:19; kotlin/core/src/main/kotlin/lmeditor/core/Schemas.kt:75 |
| `N1.03` | Per-question source selection | `M2` | `python:notebooks` | `in-progress` | Deselect one source for a question, ask it, then read back the citations and confirm the deselected source was excluded. | backend/src/lmeditor/notebooks/answer.py:95; kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/NotebookScreen.kt:494 |
| `N1.04` | Vault notes as explicit evidence | `M2` | `cross:notes` | `not-started` | Select a vault note as evidence, ask a question, then read back the note identity and the content revision each answer was bound to. | — |
| `N1.05` | Persisted multi-turn conversations | `M2` | `python:notebooks` | `not-started` | Hold a three-turn conversation, restart the backend, then read back every turn with its sources, its citations and its feedback. | — |
| `N1.06` | Streamed answers with cancellation | `M2` | `python:notebooks` | `in-progress` | Stream an answer, cancel it mid-flight and retry, then read back the partial answer and the recorded cancellation. | backend/src/lmeditor/server/routes.py:1050; backend/src/lmeditor/server/routes.py:2400 |
| `N1.07` | Claim-level evidence evaluation | `M2` | `python:verification` | `in-progress` | Ask a question whose answer carries one supported and one unsupported claim, then read back the per-claim verdict for each. | backend/src/lmeditor/notebooks/answer.py:131; backend/src/lmeditor/verification/claim_extractor.py:58 |
| `N1.08` | Separation of relevance, identity and support | `M2` | `python:verification` | `in-progress` | Evaluate an answer, then read back the retrieval rank, the exact citation identity and the semantic support score as separate fields. | backend/src/lmeditor/verification/nli_evaluator.py:220; kotlin/text/src/main/kotlin/lmeditor/text/ClaimLineage.kt:40 |
| `N1.09` | Contradiction handling and abstention | `M2` | `python:notebooks` | `in-progress` | Ask a question two sources contradict and one they cannot answer, then read back the contradiction report and the refusal. | backend/src/lmeditor/notebooks/answer.py:54; backend/src/lmeditor/notebooks/answer.py:167 |
| `N1.10` | Persisted retrieval index and recall benchmark | `M2` | `python:provenance` | `not-started` | Persist a retrieval index across a restart, then read back the recall and the citation-precision figures for the curated corpus. | — |

### Track N2 — Ingestion and study studio (§6)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `N2.01` | Expanded ingestion coverage | `M3` | `kotlin:ingest` | `in-progress` | Ingest a PDF, a DOCX, an HTML page, a Markdown file and a pasted text, then read back the extraction quality and the omissions for each. | kotlin/ingest/src/main/kotlin/lmeditor/ingest/LoaderRegistry.kt:23; kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfText.kt:27 |
| `N2.02` | Audio and video ingestion | `M3` | `kotlin:ingest` | `not-started` | Ingest an audio file and a video transcript, then read back the timestamped transcript and the original reference for each. | — |
| `N2.03` | Web discovery and document connectors | `M3` | `kotlin:ingest` | `in-progress` | Resolve a DOI through the resolver chain, then read back the captured version so a refresh cannot alter an old answer. | kotlin/ingest/src/main/kotlin/lmeditor/ingest/BibliographyResolvers.kt:505; kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt:315 |
| `N2.04` | Editable summaries and briefing documents | `M3` | `python:drafting` | `in-progress` | Draft a section from a notebook's sources, edit the draft and save it, then read back the draft and its evidence links. | backend/src/lmeditor/drafting/modules.py:33; backend/src/lmeditor/drafting/modules.py:278 |
| `N2.05` | Flashcards, quizzes and study progress | `M3` | `python:drafting` | `not-started` | Generate a flashcard set and a quiz from a notebook, then read back each answer key checked against its evidence. | — |
| `N2.06` | Audio overviews | `M3` | `python:assistant` | `not-started` | Generate an audio overview with its source-linked script, play it and download it, then read back the script and its references. | — |
| `N2.07` | Video overviews and slide decks | `M3` | `python:assistant` | `not-started` | Generate a slide deck from a notebook, then reopen it and read back the script, the references and the generation settings. | — |
| `N2.08` | Persisted artifact jobs and versions | `M3` | `kotlin:notebooks` | `not-started` | Generate a study pack, restart the backend and reopen it, then read back its provenance, its version and its export. | — |
| `N2.09` | Notebook sharing with roles | `M3` | `kotlin:server` | `not-started` | Share a notebook with a reader role, then attempt a source download and read back the denial and the revocation. | — |
| `N2.10` | Provider usage, cost and privacy | `M3` | `python:llm` | `not-started` | Start an operation that calls a remote provider, then read back the usage, the estimated cost and the privacy choice shown before it ran. | — |

### Track P — Make the three replacements one product (§7)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `P.01` | Shared project workspace manifest | `M2` | `kotlin:core` | `not-started` | Open one project that holds a document, a note, a notebook and a source, then read back the stable id each entry is linked by. | — |
| `P.02` | Promote a cited passage into a note | `M2` | `cross:notes` | `not-started` | Promote a quoted passage into a vault note, then read back the note's locator, the source id and the source revision it carries. | — |
| `P.03` | Promote research into an editable document | `M2` | `cross:research` | `in-progress` | Build an outline from a notebook's notes, open it as a book, edit a section, then read back the provenance each section kept. | kotlin/discovery/src/main/kotlin/lmeditor/discovery/OutlineBuilder.kt:45; backend/src/lmeditor/library/importer.py:102 |
| `P.04` | Add a document to notebook research | `M2` | `cross:research` | `not-started` | Add a document section to a notebook, then read back the manifest entry that says whether it is a snapshot or a live link. | — |
| `P.05` | Navigate from claim to evidence | `M2` | `python:verification` | `in-progress` | Open a manuscript claim, jump to the chunk that supports it and back, then read the stale flag after the claim was edited. | backend/src/lmeditor/verification/citation_checker.py:270; kotlin/text/src/main/kotlin/lmeditor/text/ClaimLineage.kt:40 |
| `P.06` | Portable project bundle export and import | `M3` | `python:export` | `not-started` | Export a project bundle, import it on a clean installation, then read back the notes, documents, originals, artifacts and manifest it carried. | — |

### Track R — Release evidence and operational requirements (§10)

| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `R.01` | Compatibility corpus | `M4` | `cross:evidence` | `in-progress` | Run the corpus over edited and untouched round trips, then read back the manifest entry that names each fixture and its outcome. | backend/tests/word_fixtures.py:209; backend/tests/word_shared_corpus.py:211 |
| `R.02` | Export comparison against real Word | `M4` | `cross:evidence` | `evidence-partial` | Export a document, compare its semantic structure and rendered pages with the fixtures, then read back the Word validation result. | backend/tests/word_real_word_check.py:555; backend/tests/test_word_real_word.py:31 |
| `R.03` | Fault injection and recovery | `M4` | `cross:evidence` | `not-started` | Fill the disk, deny a write and kill the process mid-save, then read back the acknowledged edit and the recoverable conflict. | — |
| `R.04` | Reversibility and clean-installation restore | `M4` | `cross:delivery` | `not-started` | Import a project bundle on a clean installation, restore a backup, then read back the documents, notes and sources that came back. | — |
| `R.05` | Measured latency and memory budgets | `M4` | `cross:evidence` | `not-started` | Benchmark the 100-page document, the 10,000-note vault and the 100-source notebook, then read back the measured p95 latency and the memory. | — |
| `R.06` | Latency separation and UI responsiveness | `M4` | `cross:evidence` | `not-started` | Run a long job while editing and navigating, then read back the local-interaction latency measured apart from the model latency. | — |
| `R.07` | Boundary limits and input sanitisation | `M4` | `kotlin:ingest` | `in-progress` | Submit an oversized upload, a private-host URL and a path that escapes the root, then read back the refusal for each. | kotlin/ingest/src/main/kotlin/lmeditor/ingest/Limits.kt:22; kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt:315; backend/tests/test_ingestion_security.py:9 |
| `R.08` | Authenticated local gateway | `M4` | `kotlin:server` | `complete` | Call the gateway from a foreign origin and read back the refusal of the mutation, then call it without the installation credential and read back the refusal of that request too. | backend/src/lmeditor/server/app.py:136; backend/src/lmeditor/server/app.py:152; backend/src/lmeditor/server/app.py:443; backend/src/lmeditor/server/app.py:506; backend/src/lmeditor/server/credential.py:129; backend/src/lmeditor/server/credential.py:259; backend/tests/test_api_credential.py:124; backend/tests/test_api_credential.py:256; kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/ApiCredential.kt:89 |
| `R.09` | Secrets in protected storage | `M4` | `python:llm` | `not-started` | Configure a provider key, read the diagnostics dump, then read back that no key and no document content appears in it. | — |
| `R.10` | Access checks on retrieval and export | `M4` | `kotlin:server` | `in-progress` | Revoke a source, then request retrieval, an artifact export and a source download, then read back the refusal and the preserved history. | backend/src/lmeditor/notebooks/answer.py:95; backend/src/lmeditor/server/sources.py:161 |
| `R.11` | Reproducible diagnostics and repair | `M4` | `cross:delivery` | `not-started` | Run the diagnostics action, read back the storage and index status, then rebuild the index and read back the rebuilt state. | — |
| `R.12` | Platform installation validation | `M4` | `cross:delivery` | `not-started` | Install, launch, edit offline, restart the service, upgrade and uninstall on a supported platform, then read back the retained data. | — |

---

## 6. Coverage

This matrix describes **83 requirements**, the same count the roadmap carries and the same count the registry holds.

### By track

| Track | Requirements |
| :--- | ---: |
| F | 10 |
| W1 | 6 |
| W2 | 8 |
| W3 | 6 |
| O1 | 9 |
| O2 | 6 |
| N1 | 10 |
| N2 | 10 |
| P | 6 |
| R | 12 |
| **Total** | **83** |

### By status

| Status | Requirements |
| :--- | ---: |
| `not-started` | 46 |
| `in-progress` | 35 |
| `evidence-partial` | 1 |
| `complete` | 1 |
| **Total** | **83** |

### Requirements not started

**46 of 83 requirements are `not-started`** — no implementation of the requirement exists in this tree, and their evidence lists are empty. The remaining 37 carry at least one evidence entry, and every one of those entries names a file and a line that exists in this working tree.

The `blocks` column of the registry is not reproduced here. It records the requirements each one gates, and every id in it resolves to a row above.

### What this is, and what it is not

This is a **registry snapshot reconciled on 2 November 2026**, not a new product certification. Its statuses and evidence are taken from the registry; this reconciliation does not establish that every workflow runs end to end or that the product replaces the three applications the roadmap is about.

A status of `in-progress` means a partial implementation was found, not that the requirement is nearly done. The registry records `R.08` as `complete`, with its recorded evidence; every other requirement retains its recorded status. The matrix reflects the registry and remains as current as the evidence behind it.
