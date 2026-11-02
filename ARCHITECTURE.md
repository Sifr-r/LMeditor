# LMeditor Architecture & System Ledger (Monorepo)

> **Product replacement roadmap — 2026-10-26:**
> [`docs/product-replacement-roadmap.md`](docs/product-replacement-roadmap.md)
> records the current Word/Obsidian/NotebookLM foundations, remaining workflow
> gaps, proposed domain ownership, dependency-ordered milestones and acceptance
> gates. Its single responsibility is the cross-product replacement backlog and
> release criteria. Proposed source paths are a blueprint, not implemented
> modules. This documentation pass changes no application behavior and does not
> certify the existing app or tests as production-ready.
>
> **The replacement program's M0 — 2026-10-26:**
> [`docs/replacement/README.md`](docs/replacement/README.md) indexes the
> milestone's artifacts: the requirement registry
> ([`requirements.json`](docs/replacement/requirements.json)), the supported
> workflow matrix, the feature inventory, the migration and recovery contract
> and the editor/layout feasibility spike. The 83 roadmap requirements each
> carry a stable identifier, and a registry entry gives that identifier an
> owner, a milestone, a status and an observable gate. §27 records the pass,
> including the one amendment to `kotlin/CONTRACTS.md` that the layout
> feasibility spike required.

## 1. Overview
LMeditor is an enterprise-grade LLM writing harness engineered for long-form, structured expository writing anchored strictly to verifiable source passages with mathematically validated, chunk-level inline citations.

### Architectural Tiers:
1. **Deterministic Tier (Kotlin / JDK 17):** Every deterministic rule **the tier serves** — the citation grammar, the chunker, the claim extractor, the lineage engine, the OOXML reader and writer, the SSRF policy, the book store, the workflow's routing rules — is implemented **once**, here, and the Python tier calls it over a newline-delimited JSON pipe rather than repeating it. A named set of rules is deliberately outside that statement, and **§18.2.1 is the catalogue**: some have a drift-pinned Python twin, two have no Kotlin counterpart at all, and the model-facing half is Python by design. Its runtime dependency set is *empty*: JSON is hand-written, XML comes from `javax.xml`, ZIP from `java.util.zip`, HTTP from `java.net.http`, hashing from `java.security`, dates from `java.time`. See §18.
2. **Core Agentic Tier (Python 3.12+):** Synthesizes **Stanford STORM** (multi-perspective pre-writing discovery), **DSPy** (declarative signature compilation & runtime assertions), and **LangGraph** (cyclic state machine, critique loops & HITL checkpoints). Python owns model inference and embeddings; Kotlin owns retrieval arithmetic and deterministic provenance. See §18.11–§18.13 and `kotlin/CONTRACTS.md` §14.
3. **Transport Tier (FastAPI SSE):** Server-Sent Events (SSE) and REST gateway streaming token deltas, citation mappings, and NLI verification scorecards. The transport publishes the envelope declared in `lmeditor.core.types.EventType` and the routes in the OpenAPI schema.
4. **Desktop Client (Kotlin Compose):** `kotlin/desktop` consumes the HTTP/SSE surface and reuses `:core` decoders and citation rules. It is the single UI dependency exemption above the deterministic tier. See §20.

> **Status — 2026-10-25.** The Flutter/Dart desktop client was removed and replaced by `kotlin/desktop` (§20). Sections 1–4 below describe the architecture as it stands now. Sections 5–19 are dated passes and read as history: any path under `client/` named in them no longer exists; §19 records the removal, and §20 records the Compose replacement.

---

## 2. Monorepo Directory Blueprint

```
d:/LMeditor/
├── ARCHITECTURE.md                  # Foundational architectural ledger and module registry
│
├── backend/                         # Python 3.12+ Agentic Core Harness
│   ├── pyproject.toml               # Python dependencies and packaging configuration (uv/hatch)
│   ├── src/
│   │   └── lmeditor/
│   │       ├── __init__.py
│   │       ├── core/                # Foundational domain models, schemas, and error hierarchies
│   │       │   ├── __init__.py
│   │       │   ├── exceptions.py    # Strict, deterministic domain exceptions
│   │       │   ├── types.py         # Domain type definitions, Enums, and Protocol interfaces
│   │       │   ├── schemas.py       # Pydantic v2 models for chunks, outlines, citations, and state
│   │       │   ├── citations.py     # The citation grammar, shared by every surface
│   │       │   └── sections.py      # The rule for an operator's own edit to a section
│   │       ├── llm/                 # Language-model configuration and the offline stub
│   │       │   ├── config.py        # Provider configuration and the environment contract
│   │       │   ├── factory.py       # Builds the configured model, or says it is not configured
│   │       │   ├── codex.py         # DSPy generation through the locally authenticated Codex CLI
│   │       │   └── stub.py          # The offline stub, which quotes the passage it was given
│   │       ├── ingestion/           # Source ingestion: files, URLs, and the policy around them
│   │       │   ├── loaders.py       # File readers (text, markdown, PDF, DOCX)
│   │       │   ├── registry.py      # The loader registry
│   │       │   ├── resolvers.py     # URL and path resolution
│   │       │   ├── url_policy.py    # SSRF policy: public addresses only
│   │       │   ├── http_safety.py   # Redirect, size and content-type limits
│   │       │   ├── limits.py        # The size and count limits of an ingest
│   │       │   ├── service.py       # The ingestion service
│   │       │   ├── source_store.py  # The on-disk source library
│   │       │   └── pdf_metadata.py  # PDF metadata and page count
│   │       ├── word/                # Word (.docx / OOXML) processing: reader, writer, shared model
│   │       │   ├── __init__.py     # Lazy facade over the word-processing public surface
│   │       │   ├── model.py         # Format-neutral document model shared by reader and writer
│   │       │   ├── text.py          # Whitespace and text-normalisation rules (contract §4)
│   │       │   ├── formats.py       # Legacy .doc detection, and the reason it is refused
│   │       │   ├── reader.py        # .docx package -> WordDocument (a door onto the tier's OOXML reader)
│   │       │   ├── writer.py        # WordDocument -> .docx package (a door onto the tier's OOXML writer)
│   │       │   └── manuscript.py    # DocumentDraft + bibliography -> WordDocument
│   │       ├── provenance/          # Chunk indexing and citation lineage (the rules are Kotlin's)
│   │       │   ├── __init__.py
│   │       │   ├── chunker.py       # Semantic and sliding-window chunking with offset tracking
│   │       │   ├── indexer.py       # Hybrid vector (dense) + BM25 (sparse) index with RRF
│   │       │   ├── embeddings.py    # The embedding model, and its offline fallback
│   │       │   ├── lineage.py       # Cryptographic chunk hashing and token-span attribution
│   │       │   └── service.py       # The retrieval index over a document's sources
│   │       ├── discovery/           # STORM-inspired multi-perspective pre-writing exploration
│   │       │   ├── __init__.py
│   │       │   ├── perspective.py   # Perspective agent generator (domain expert, skeptic, etc.)
│   │       │   ├── dialogue.py      # Conversational search and Q&A research loop
│   │       │   ├── source_analysis.py # What the corpus says the document should cover
│   │       │   └── outliner.py      # Hierarchical section outline generator and source assigner
│   │       ├── drafting/            # DSPy-compiled section generation modules
│   │       │   ├── __init__.py
│   │       │   ├── signatures.py    # DSPy declarative signatures (Drafting, Citation Insertion)
│   │       │   ├── modules.py       # Section drafter with inline citation assertions
│   │       │   ├── notebook.py      # The DSPy module that writes a notebook's answer
│   │       │   └── teleprompter.py  # MIPROv2 compilation and metric objective optimization
│   │       ├── verification/        # Formal NLI verification and hallucination auditing
│   │       │   ├── __init__.py
│   │       │   ├── claim_extractor.py # Proposition-level sentence and claim segmentation
│   │       │   ├── nli_evaluator.py   # DeBERTa-v3 / LLM judge entailment scoring engine
│   │       │   └── citation_checker.py # Precision/recall citation fidelity and ungrounded auditor
│   │       ├── editing/             # Targeted inline editing, grounded in the document's sources
│   │       │   ├── __init__.py
│   │       │   ├── editor.py        # The inline editor: what is rewritten, and what is refused
│   │       │   └── rewrite.py       # The rewrite contract (the protocol, and the stub)
│   │       ├── factcheck/           # Fact-checking a passage against sources and the open web
│   │       │   ├── __init__.py
│   │       │   ├── service.py       # The checker: claims, verdicts, and the evidence for them
│   │       │   └── web.py           # The web evidence provider, and its SSRF policy
│   │       ├── library/             # The book library: long documents, edited one section at a time
│   │       │   ├── __init__.py
│   │       │   ├── store.py         # The on-disk store: sections, chunks, briefs, and origins
│   │       │   ├── importer.py      # A Word document -> a book of editable sections
│   │       │   ├── export.py        # A book -> a WordDocument, keeping what is not text
│   │       │   ├── brief.py         # The bounded digest of what a book contains
│   │       │   └── working_set.py   # The bounded slice of a book that reaches the model
│   │       ├── workflow/            # LangGraph stateful cyclic orchestration
│   │       │   ├── __init__.py
│   │       │   ├── state.py         # HarnessState definition and reducer functions
│   │       │   ├── nodes.py         # Operational graph nodes (discovery, draft, verify, revise)
│   │       │   ├── edges.py         # Conditional routing rules (entailment thresholds, HITL gates)
│   │       │   └── graph.py         # Compiled LangGraph state machine with checkpointing
│   │       └── server/              # FastAPI streaming SSE gateway
│   │           ├── __init__.py
│   │           ├── app.py           # FastAPI application entrypoint
│   │           ├── routes.py        # SSE streaming endpoints and interactive HITL review
│   │           ├── routes_helpers.py # The route helpers shared by the endpoints
│   │           ├── session.py       # The workflow session: events, review, and the terminal state
│   │           ├── library.py       # The process-wide book library and its retrieval indexes
│   │           ├── sources.py       # The process-wide source library
│   │           ├── notebooks.py     # The notebooks folder the server holds
│   │           └── vault.py         # The active vault the server holds
│   │       ├── notebooks/           # The notebook workspace: a folder of sources and notes
│   │       │   ├── __init__.py     # The API the rest of the backend calls
│   │       │   └── answer.py       # The ask: a question answered from the notebook's own sources
│   │       ├── vault/               # The vault workspace: markdown notes on the operator's disk
│   │       │   └── __init__.py     # The API the rest of the backend calls
│   │       ├── tier/                # The doors to the Kotlin tier, one per domain module
│   │       ├── bridge/              # The NDJSON client for the Kotlin tier
│   │       └── _reference/          # The frozen Python originals the parity harness compares against
│   ├── scripts/                     # The live verifiers
│   │   ├── verify_end_to_end.py     # The whole flow, over the real HTTP surface
│   │   ├── verify_hitl_live.py      # The review gate, over the real wire
│   │   └── verify_kotlin_parity.py  # The two tiers, compared answer for answer
│   └── tests/                       # Unit, assertion, and graph test suites
│       ├── test_wire_contract.py    # The two tiers must declare one wire vocabulary
│       ├── test_core.py
│       ├── test_provenance.py
│       ├── test_discovery.py
│       ├── test_drafting.py
│       ├── test_verification.py
│       └── test_workflow.py
│
└── kotlin/                          # The deterministic tier and Compose desktop client
    ├── build.gradle.kts              # The root build: the version catalog, the tier jar
    ├── settings.gradle.kts           # The module map; the dependency direction is downward
    ├── gradle.properties             # The JVM target and the build's own settings
    ├── gradlew / gradlew.bat         # The wrapper; it fetches its own Gradle distribution
    ├── CONTRACTS.md                  # The engineering rules every module here follows
    ├── README.md                     # What the tier owns, and what stays in Python
    ├── core/                         # Errors, enums, schemas, the citation grammar, the JSON codec
    ├── text/                         # Chunking, lineage, claim extraction, the citation audit
    ├── word/                         # The OOXML model, the reader, the byte-deterministic writer
    ├── pdf/                          # PDF text and metadata extraction
    ├── ingest/                       # Source loaders, the SSRF policy, the bounded upload store
    ├── library/                      # The on-disk book store, the brief, the working set
    ├── workflow/                     # The workflow state and the conditional routing rules
    ├── editing/                      # The acceptance rule for a grounded inline revision
    ├── factcheck/                    # The aggregation of claims, evidence and scores
    ├── discovery/                    # Perspectives, outline construction, the research dialogue
    ├── server/                       # The session broker, the source library, the book registry
    ├── vault/                        # The rules of a vault: paths, markdown grammar and store
    ├── notebooks/                    # The rules of a notebook: ids, manifest and store
    ├── desktop/                      # Compose HTTP/SSE client; depends downward on core
    └── bridge/                       # The NDJSON stdio seam the Python tier calls into
        ├── README.md                 # The wire contract between the two tiers
        └── src/{main,test}/kotlin/lmeditor/bridge/  # The request router and its handlers

    # Every module above has the same internal layout:
    #   <module>/build.gradle.kts                   # its dependencies; the direction is downward
    #   <module>/src/main/kotlin/lmeditor/<module>/ # the implementation
    #   <module>/src/test/kotlin/lmeditor/<module>/ # the module's own tests
```

---

## 3. Module Responsibilities (Single Responsibility Principle)

| Module / File | Responsibility |
| :--- | :--- |
| **Backend Core** | |
| `backend/pyproject.toml` | Modern Python packaging configuration with Hatchling, strict mypy mode, and dependency definitions. |
| `lmeditor/__init__.py` | Top-level package namespace exporting package version. |
| `lmeditor/core/__init__.py` | Core domain facade exporting all exceptions, types, protocols, and schemas. |
| `lmeditor/core/exceptions.py` | Strict, non-silent domain exceptions (`CitationHallucinationError`, `UngroundedClaimError`, `ChunkNotFoundError`, `SpanAlignmentError`, `WorkflowDeadlockError`, `WordProcessingError`/`WordParseError`/`WordRenderError`). |
| `lmeditor/core/types.py` | Domain type definitions, Enums (`EntailmentStatus`, `DocumentStatus`, `PerspectiveRole`), and runtime Protocols (`ChunkerProtocol`, `HybridIndexerProtocol`, etc.). |
| `lmeditor/core/schemas.py` | Immutable Pydantic v2 data models for `SourceChunk`, `Claim`, `CitationLineage`, `OutlineNode`, `SectionDraft`, and `VerificationReport`. |
| `lmeditor/word/__init__.py` | Lazy facade over the word-processing public surface; the shared model is importable without the OOXML machinery of the reader and writer. |
| `lmeditor/word/model.py` | Format-neutral Word document model (`WordDocument`, `WordBlock`, `WordRun`, `WordTable`, `WordFootnote`, `WordProperties`) with the block/document invariants enforced by validators, so a malformed model cannot be constructed. |
| `lmeditor/word/text.py` | Whitespace and text-normalisation rules shared by the reader and the writer: exotic Word spaces, whitespace collapsing, control-character stripping. |
| `lmeditor/word/reader.py` | Parses a `.docx` OPC package (`zipfile` + `lxml`, never `python-docx`'s body accessors) into a `WordDocument`, recovering tables, list structure, footnotes, hyperlinks, tracked changes, content controls and text boxes, and reporting every omission instead of dropping it silently. |
| `lmeditor/word/writer.py` | Renders a `WordDocument` into a valid `.docx` package: real heading styles, multi-level numbering with per-group restarts, tables with repeating header rows, a `footnotes.xml` part, and external hyperlink relationships. Byte-deterministic by default. |
| `lmeditor/word/manuscript.py` | Builds a `WordDocument` from a `DocumentDraft` and its bibliography, consuming the inline citation markers into superscript markers with the cited passage attached as a footnote. |
| `lmeditor/provenance/chunker.py` | Segments source texts, attaches byte/char offsets, assigns SHA-256 identifiers, and maintains sentence boundaries. `SourceChunker` is a compatibility alias of `SentenceAwareChunker`, so every caller gets sentence-aligned chunks. |
| `lmeditor/provenance/indexer.py` | The door for hybrid sparse (BM25) and dense (vector) retrieval: the scoring, the term-frequency hash fallback and the rank fusion are the Kotlin tier's, and the module keeps the Python API and the embedder. |
| `lmeditor/provenance/embeddings.py` | The dense half's embedder: a lazily-loaded, offline-only `sentence-transformers` model, with a deterministic term-frequency hash as the declared fallback. |
| `lmeditor/provenance/service.py` | The door for the retrieval view of a document's sources: `build_provenance_index(chunks)` returns a `ProvenanceIndex` whose `rank_for_section` and `lineage_for_claim` are answered by the Kotlin tier. |
| `lmeditor/provenance/lineage.py` | Binds generated token spans back to source chunk IDs with exact character offsets. |
| `lmeditor/llm/config.py` | Parses `LMEDITOR_LM_*` environment variables into a typed `LMConfig`; names the provider (`openai`, `anthropic`, `ollama`, `gemini`, `openai-compatible`, `stub`). |
| `lmeditor/llm/factory.py` | The single place that loads, reports and releases the language model: `configure_lm`, `is_configured`, `require_lm`, `status`, `reset`, `build_lm`. |
| `lmeditor/llm/stub.py` | `StubLM` — a deterministic drafter that quotes the passage it is given and cites that passage's real chunk id. It is an explicit, self-identifying provider (`stub`), never a silent substitute for a model. |
| `lmeditor/llm/codex.py` | The DSPy adapter that asks the locally authenticated Codex CLI for text. The CLI owns its credentials; each call is an ephemeral read-only session in a dedicated project-local directory, and a failure surfaces as a generation error. |
| `lmeditor/ingestion/limits.py` | The hard byte caps on everything that crosses the ingestion boundary: URL bodies, resolver responses, uploads, and the total persisted-upload budget. |
| `lmeditor/ingestion/http_safety.py` | Streaming reads that abort mid-stream at a byte cap, and the redirect-following policy that re-validates every hop against the private-address rule. |
| `lmeditor/ingestion/source_store.py` | The bounded, self-cleaning store for persisted uploads: one directory per source, a total-bytes budget, and LRU-by-mtime eviction. |
| `lmeditor/ingestion/pdf_metadata.py` | Extracts bibliographic metadata (title, author, subject, keywords, DOI/arXiv id) from a PDF's `/Info` dictionary or first page. |
| `lmeditor/ingestion/loaders.py` | The door for the per-kind source loaders: how a source of each kind is read — what it loses, how many pages it has, how large it is — is the tier's rule, and this module keeps the Python loader API. |
| `lmeditor/ingestion/registry.py` | The door for the dispatch from a source's kind to the loader that reads it. What stays on the Python side is a type matter rather than a rule: a URL source is named by a URL string and never by a file path. |
| `lmeditor/ingestion/resolvers.py` | The door for the bibliography resolver chain — PdfHeader, Crossref, arXiv, PubMed and OpenAlex in order — and the tier's parsers behind it. A chain that resolves nothing returns nothing: it never synthesises a plausible-looking record. |
| `lmeditor/ingestion/service.py` | The door for the high-level ingestion pipeline — load, resolve the bibliography, chunk, return — and the tier's rule behind it. The constructor, the factory and the `ingest` signature stay Python's. |
| `lmeditor/discovery/perspective.py` | Synthesizes diverse subject-matter perspectives to avoid premature convergence. |
| `lmeditor/discovery/dialogue.py` | Simulates multi-turn interactive inquiry between perspective agents and retrieval indices. |
| `lmeditor/discovery/outliner.py` | Generates a hierarchical outline tree and statically maps candidate chunks to each section. |
| `lmeditor/drafting/signatures.py` | Defines DSPy input/output typing and field descriptions for section drafting and citation integration. |
| `lmeditor/drafting/modules.py` | Implements the section drafting program equipped with runtime `dspy.Assert` validation constraints. |
| `lmeditor/drafting/teleprompter.py` | Automates prompt optimization via MIPROv2, optimizing for citation precision and macro flow. |
| `lmeditor/drafting/notebook.py` | The DSPy module that writes a notebook's answer, and nothing else: which passages the answer may lean on, and whether it is supported, are decided elsewhere, by the tier. |
| `lmeditor/verification/claim_extractor.py` | Deconstructs draft sentences into atomic, verifiable factual propositions. |
| `lmeditor/verification/nli_evaluator.py` | Executes premise-hypothesis Natural Language Inference (NLI) scoring against candidate source passages. |
| `lmeditor/verification/citation_checker.py` | Computes mathematical citation precision/recall and flags ungrounded drift. |
| `lmeditor/workflow/state.py` | Defines the central state schema (`HarnessState`) passed along the cyclic state machine. |
| `lmeditor/workflow/nodes.py` | Encapsulates single-step transitions (Retrieve, Outline, Draft, Verify, Revise, Polish, Review). |
| `lmeditor/workflow/edges.py` | Evaluates conditional logic to route between drafting, critique, human sign-off, or completion. |
| `lmeditor/workflow/graph.py` | Assembles the state graph, registers checkpointers, and exposes the run/resume APIs. |
| `lmeditor/server/app.py` | Configures FastAPI application lifecycle, middleware, and dependency injection. |
| `lmeditor/server/routes.py` | Serves REST/SSE endpoints for document generation, real-time draft streaming, and HITL decision inputs. |
| `lmeditor/server/routes_helpers.py` | The upload boundary: bounded reads, the persisted-upload write, and the size cap the route turns into a 413. |
| `lmeditor/server/sources.py` | The server's memory of ingested sources: a bounded, thread-safe registry of sources, their chunks and their bibliographic records. |
| `lmeditor/server/session.py` | Asynchronous in-memory session store and SSE event broker managing client subscriptions, event queuing, and HITL signaling. |
| `lmeditor/core/sections.py` | The rule for an operator's own edit to a section: the text is replaced and the provenance is kept, while the verdict that described the old text does not survive. One rule, so the save route and the review gate cannot drift apart. |
| `lmeditor/core/citations.py` | The door to the canonical inline-citation grammar: every function here calls across the NDJSON seam and returns the tier's answer. The compiled pattern itself stays in Python, because the drafter applies it to the model's output in-process, and the parity harness keeps the two in step. |
| `lmeditor/word/formats.py` | Recognises the legacy `.doc` format and states plainly that it is not read, rather than mis-parsing it as a `.docx`. |
| `lmeditor/discovery/source_analysis.py` | What the corpus says the document should cover — the question the guided stage asks the sources. |
| `lmeditor/ingestion/url_policy.py` | The rule that a fetched URL must resolve to a public address: the first half of the SSRF policy. |
| `lmeditor/editing/editor.py` | The inline editor: what is rewritten, what is refused, and why. A revision citing a source that does not exist is refused, and the refusal is a result rather than an exception. |
| `lmeditor/editing/rewrite.py` | The rewrite contract — the protocol the editor calls, and the offline implementation of it. |
| `lmeditor/factcheck/service.py` | The checker: a passage becomes claims, claims become verdicts, and a verdict carries the evidence for it. No evidence means no verdict. |
| `lmeditor/factcheck/web.py` | The open-web evidence provider, and the policy that keeps it from reaching a private address. |
| `lmeditor/library/store.py` | The on-disk store for long documents: one file per section, the passages of the book, the brief, and the origin record that keeps what is not text. |
| `lmeditor/library/importer.py` | A Word document becomes a book of editable sections, each with the blocks it came from and the passages it was indexed as. |
| `lmeditor/library/export.py` | A book becomes a Word document again: an unedited section keeps its blocks, an edited one is re-rendered, and the document's notes, media and parts survive. |
| `lmeditor/library/brief.py` | The bounded digest of what a book contains, so the model can be told about the book without being given the book. |
| `lmeditor/library/working_set.py` | The bounded slice of a book that reaches the model: a section and its neighbours, measured against the whole book. |
| `lmeditor/server/library.py` | The process-wide book library, and the retrieval indexes built through the manifest, so a passage the editor deleted is never retrieved. |
| `lmeditor/server/notebooks.py` | The notebooks folder the server holds: the folder the operator chose, or the default one under the user's home. The value is process-wide and guarded by a lock, so a request that opens a folder and a request that reads the open one cannot tear it between them. |
| `lmeditor/server/vault.py` | The one active vault the server holds, because the tier keeps no state of its own: every call carries the root of the vault the operator is working in. The root is checked when it is taken, and a root that is not absolute, not there or not a directory is refused with the reason. |
| `lmeditor/notebooks/__init__.py` | The Python API over the tier's notebook rules: the ids that are safe to write, the manifest, the counts, the ordering, the idempotence of a write and the membership of a source or a note are the tier's answers, handed back as models. |
| `lmeditor/notebooks/answer.py` | The ask: a question answered from the notebook's own sources and nowhere else, cited by chunk id. A source the notebook does not hold is refused, and the missing ids are named before the model is asked. |
| `lmeditor/vault/__init__.py` | The Python API over the tier's vault rules: the path safety, the frontmatter, the link and tag grammar, the backlink index and the search order are the tier's answers, handed back as models. |
| **Kotlin Tier** | |
| `kotlin/settings.gradle.kts` | The module map. The dependency direction is strictly downward and `:core` depends on nothing at all; Gradle enforces the graph. |
| `kotlin/build.gradle.kts` | The root build: the version catalog, the Java 17 target, and the `:bridge:tierJar` task the Python tier loads. |
| `kotlin/CONTRACTS.md` | The engineering rules every module of the tier follows: no runtime dependency, typed contextual errors, idempotent transitions. |
| `kotlin/bridge/README.md` | The wire contract between the two tiers: the NDJSON frames, the operations, and the error shapes. |
| `kotlin/core` | The typed error hierarchy, the domain enums and their wire strings, the schema data classes, the inline-citation grammar, the operator-edit rule, and the native JSON codec. Depends on nothing. |
| `kotlin/text` | Sentence-aware chunking, token-span lineage, claim segmentation, the citation precision/recall audit, and the retrieval rule — BM25 Okapi, the dense cosine index and their rank fusion. |
| `kotlin/word` | The OOXML document model, the `.docx` reader, the byte-deterministic writer, and the manuscript builder. The markdown syntax is parsed here, once, for every consumer. |
| `kotlin/pdf` | PDF text and metadata extraction, written against the JDK's `Inflater` rather than `pypdf`. |
| `kotlin/ingest` | Source loaders, the SSRF policy, the bounded HTTP reads, the upload store, the bibliography resolver chain and the ingestion service. |
| `kotlin/library` | The on-disk book store, the bounded digest of a book, and the bounded working set. |
| `kotlin/workflow` | The workflow state, its reducers, and the conditional routing rules. |
| `kotlin/editing` | The acceptance rule for a grounded inline revision. |
| `kotlin/factcheck` | The aggregation of claims, evidence and scores into a verdict. |
| `kotlin/discovery` | Perspective generation, outline construction, and the research dialogue. |
| `kotlin/server` | The session broker, the source library and the book registry. |
| `kotlin/vault` | The rules of a vault: the safe note ids, the markdown grammar, the frontmatter, the backlink index, the search order and the store that reads and writes the files. Depends on `:core`. |
| `kotlin/notebooks` | The rules of a notebook: the safe notebook ids, the manifest, the counts, the ordering, the idempotence of a write and the membership of a source or a note. Depends on `:core`. |
| `kotlin/bridge` | The NDJSON stdio seam the Python tier calls into. |
| `kotlin/desktop` | The operator window: the documents, vault and notebook workspaces, the streaming manuscript, the review gate, the verification pane and the export. Depends on `:core` alone, and no tier module may depend on it. |
| **Verification & Test Suites** | |
| `backend/tests/test_core.py` | Unit validation tests for Pydantic v2 schemas, boundary constraints, and domain exception hierarchies. |
| `backend/tests/test_provenance.py` | Unit tests for semantic/sliding-window chunking, SHA-256 determinism, and hybrid dense/sparse indexing. |
| `backend/tests/test_discovery.py` | Validation tests for STORM multi-perspective persona discovery and hierarchical outline generation. |
| `backend/tests/test_drafting.py` | Unit tests for DSPy signatures, SectionDrafter assertion enforcement, and MIPROv2 objective metric. |
| `backend/tests/test_verification.py` | Mathematical tests for claim extraction, P_cite, R_cite, F_beta calculations, and DeBERTa NLI scoring. |
| `backend/tests/test_workflow.py` | Cyclic state graph execution tests, node transitions, redraft loops, and HITL gate verifications. |
| `backend/tests/test_wire_contract.py` | The two tiers must declare one wire vocabulary: the same event names, the same SSE envelope, the same operator outline. Reads the Kotlin tier's own source and compares it with the backend's declared contract. |
| `backend/tests/test_request_path_precedence.py` | The path names the thing; the body does not name it again. A body that contradicts the path cannot redirect a request. |
| `backend/scripts/verify_end_to_end.py` | The whole flow over the real HTTP surface: ingest, generate, follow the SSE stream, answer the gate, fetch the manuscript, export the `.docx`. |
| `backend/scripts/verify_hitl_live.py` | The review gate over the real wire, against a backend started without a language model. |
| `backend/scripts/verify_kotlin_parity.py` | The two tiers, asked the same question and compared answer for answer. |

---

## 4. Production Guardrails & Standards
1. **Input Sanitization & Boundary Validation:** All entry points into Python modules accept validated Pydantic schemas. All entry points into the Kotlin tier validate their arguments and throw a typed `ValidationError` naming the field.
2. **Deterministic Error Handling:** Domain-specific errors halt execution or trigger explicit graph recovery paths; silent failures are prohibited. Across the NDJSON seam a failure is reported and the service carries on — never a silent fallback to a second implementation.
3. **Strict Typing:** All Python code must satisfy `mypy --strict`. All Kotlin code must compile with the strictest type checks the toolchain offers, and the tier's runtime dependency set stays empty.
4. **Idempotence:** State mutations in the workflow engine are handled via pure reducer functions and versioned checkpoints. Every state transition on the seam is idempotent: the same request twice changes the state once.
5. **One Implementation Per Rule:** A deterministic rule that crosses the seam is implemented once, in Kotlin. The Python modules that used to hold one are *doors*: they keep their public API and answer from the tier. A second implementation is a second thing that can drift — which is why the deliberate Python twins are pinned by a shared corpus or by the parity harness rather than left to agree by luck. §18.2.1 is the catalogue of the rules the seam does not cover.

## 5. Audit ledger synchronization — 2026-10-02

The tiers and guardrails above describe intended architecture, not verified production guarantees. The backend manifest currently supports Python 3.12+. Retrieval is implemented locally rather than through LlamaIndex, and the workflow does not call the hybrid indexer or dialogue research loop. The verification engine includes a lexical heuristic; SSE intermediate events and session-based human review are not connected to graph execution. Source ingestion now exists. See [AUDIT.md](D:/LMeditor/AUDIT.md) for verified defects and check results.

The following existing modules extend the original directory blueprint:

| File | Single responsibility |
| --- | --- |
| `backend/src/lmeditor/ingestion/__init__.py` | Export the ingestion package's public types and services. |
| `backend/src/lmeditor/ingestion/loaders.py` | Extract source text from supported file formats and URLs. |
| `backend/src/lmeditor/ingestion/registry.py` | Dispatch source kinds to their loaders. |
| `backend/src/lmeditor/ingestion/resolvers.py` | Resolve bibliography metadata through ordered strategies. |
| `backend/src/lmeditor/ingestion/service.py` | Coordinate loading, bibliography resolution and provenance chunk creation. |
| `backend/src/lmeditor/server/routes_helpers.py` | Persist uploaded source files for ingestion. |
| *Flutter client modules* | A Flutter/Dart desktop client was added by this pass and removed in §19. Its paths are not listed: the files are gone. |
| `backend/tests/test_ingestion_loaders.py` | Check supported source text extraction behavior. |
| `backend/tests/test_ingestion_registry.py` | Check loader registration and source-kind dispatch. |
| `backend/tests/test_ingestion_resolvers.py` | Check bibliography resolver strategies and fallback ordering. |
| `backend/tests/test_ingestion_service.py` | Check source ingestion coordination and returned provenance. |
| `backend/tests/test_routes_url.py` | Check JSON URL-ingestion route dispatch with mocked fetching. |
| `backend/tests/fixtures/minimal.pdf` | Supply a small PDF for ingestion tests. |
| `backend/tests/fixtures/minimal.docx` | Supply a small DOCX for ingestion tests. |
| `AUDIT.md` | Record the audit blueprint, verified findings and verification limits. |

This audit adds documentation only. It does not apply the proposed code fixes or certify production readiness.

---

## 6. Remediation pass — 2026-10-03 (P1 findings 1–11)

The P1 findings recorded in [AUDIT.md](D:/LMeditor/AUDIT.md) were remediated under a binding
interface contract, [AUDIT_REMEDIATION_CONTRACT.md](D:/LMeditor/AUDIT_REMEDIATION_CONTRACT.md).
The P2 findings (12–17) and the unused-dependency removals were deliberately left open.

### Modules added by this pass

| File | Single responsibility |
| --- | --- |
| `backend/src/lmeditor/core/citations.py` | Single source of truth for the inline-citation grammar (`[#chunk_id]`, `[chunk_id]`, `[cite: id]`, comma lists). Consumed by both the drafting and verification layers so they cannot diverge. |
| `backend/src/lmeditor/ingestion/url_policy.py` | Trust-boundary policy: source-id validation, containment-proven upload paths, and the public-destination (SSRF) check applied per redirect hop. |
| `backend/tests/test_ingestion_security.py` | Regressions for path escape, arbitrary file read and internal-destination fetching. |
| `backend/tests/test_citation_grammar.py` | Drafter-output-to-verifier round trip across every accepted marker form. |
| `backend/tests/test_hitl_gate.py` | Suspend/resume behaviour of the human-review gate, including an approving resume that re-enters the graph. |
| `backend/tests/test_stream_contract.py` | SSE envelope, ingestion trust boundary, and approve/reject worker outcomes. |
| *Flutter client tests* | The client's own parsing tests for the SSE envelope and the claim mapping. They were added by this pass and removed in §19; `backend/tests/test_wire_contract.py` now guards the same boundary between the two tiers. |

### Contract boundaries established

| Contract | Owner | Consumer |
| --- | --- | --- |
| `WorkflowEvent` envelope (`event_type`/`step`/`doc_id`/`data`) with terminal `workflow_complete` / `workflow_error` | `core/schemas.py`, emitted by `workflow/graph.py` | `server/routes.py` serialises; the wire ends there. The Kotlin tier declares the same envelope (`kotlin/core/.../Schemas.kt`), and `tests/test_wire_contract.py` keeps the two in step. |
| `CITATION_PATTERN` and its helpers | `core/citations.py` | `verification/claim_extractor.py`, `drafting/modules.py` |
| `DEFAULT_F_BETA_THRESHOLD` and the four-part verified rule (including `neutral_claims == 0`) | `verification/citation_checker.py` | `workflow/nodes.py`, and the scorecard on the wire |
| `EventSink` protocol (structural; no import of the transport layer) | `workflow/graph.py` | `server/session.py::WorkflowSession` |
| `resume_workflow` / `release_workflow` over a `thread_id`-keyed compiled-graph registry | `workflow/graph.py` | `server/routes.py` approve path |
| `validate_source_id` / `safe_upload_path` / `assert_public_url` | `ingestion/url_policy.py` | `server/routes_helpers.py`, `ingestion/loaders.py`, `server/routes.py` |

### Dependency direction

`core` → `ingestion` → `verification`/`drafting` → `workflow` → `server`. The workflow layer
reaches the transport layer only through the structural `EventSink` protocol, so `lmeditor.workflow`
never imports `lmeditor.server`. Ingestion policy is a leaf module that both the loaders and the
HTTP boundary call, so the boundary check and the loader check cannot diverge.

### Known limits after this pass

- `max_retries` / `threshold_f_beta` from the API request are still not propagated into `HarnessState` (P2, finding 12).
- SSE subscribers still share one queue and a single terminal sentinel; reconnect and replay remain unaddressed (P2, finding 13).
- The SSRF guard validates resolved addresses but does not pin the IP for the subsequent connection, so a DNS-rebinding window remains.
- The lexical NLI fallback in `verification/nli_evaluator.py` remains a degraded path, not a substitute guarantee of semantic entailment.

---

## 7. Remediation pass — 2026-10-04 (P2 findings 12–17 + performance)

Binding contract: **`AUDIT_REMEDIATION_CONTRACT_P2.md`**. Executed as four parallel
workers on disjoint file ownership; merged state verified by the orchestrator.

### Modules added by this pass

| Module | Responsibility |
| --- | --- |
| `backend/tests/test_sse_fanout.py` | Multi-subscriber SSE delivery, bounded replay, closed-session termination, duplicate-`doc_id` rejection, and event-loop responsiveness under slow ingestion. |
| `backend/tests/test_workflow_options.py` | `max_retries` / `threshold_f_beta` actually reaching routing and the verification gate, plus backward compatibility for state lacking the new keys. |
| `backend/tests/test_verification_throughput.py` | Concurrency equivalence with the sequential result, the NLI semaphore bound observed via an in-flight counter, and the bounded cache. |
| `backend/tests/test_resolver_payloads.py` | Resolvers against representative transport responses: the real esummary envelope with `uids` first, and a real arXiv Atom document. |
| `backend/tests/test_dns_pinning.py` | Rebinding simulation and the residual-protection property actually enforced. |
| *Flutter client review-gate modules* | The client's typed failure and pending-review state, the review banner, and their tests. They were added by this pass and removed in §19; the gate itself is unchanged and is now checked by `backend/scripts/verify_hitl_live.py` over the real wire. |

### Public contract boundaries established

| Contract | Owner | Consumer |
| --- | --- | --- |
| `EventSubscription` + `WorkflowSession.subscribe()` / `unsubscribe()`, bounded by `STREAM_REPLAY_BUFFER = 512` | `server/session.py` | `server/routes.py::stream_workflow_events`; each subscriber now receives the full stream rather than a fraction of a shared queue |
| `ActiveSessionExistsError` → `409 Conflict` | `server/session.py` | `server/routes.py`; a live `doc_id` can no longer be silently replaced while its worker keeps running |
| `resolve_public_addresses(url) -> list[tuple[str, int]]` | `ingestion/url_policy.py` | `ingestion/loaders.py`; re-run per redirect hop so validation and connection agree |
| `max_retries` / `threshold_f_beta` as defaulted `HarnessState` keys | `workflow/state.py` | `workflow/edges.py::route_after_verification`, `workflow/nodes.py::verify_citations_node` |
| `MAX_CONCURRENT_NLI_EVALUATIONS = 8` semaphore | `workflow/nodes.py`, `verification/citation_checker.py` | bounds parallel NLI calls so N sections cannot open unbounded model inferences |
| `MAX_CACHE_ENTRIES = 4096` bounded LRU | `verification/nli_evaluator.py` | replaces the unbounded dict on the process-wide `default_nli_evaluator` singleton |
| Streamed request + fail-closed peer verification | `ingestion/loaders.py::_request` / `_assert_vetted_peer` | `ingestion/url_policy.py::resolve_public_addresses` supplies the pinned set; the body is read only after the live peer is approved |
| Per-identifier validation (`_validated_doi` / `_validated_arxiv_id` / `_validated_pmid`) | `ingestion/resolvers.py` | each `_url` implementation validates before interpolating, then percent-encodes via `urllib.parse.quote`; a rejection raises `BibliographyResolutionError`, which is **not** a `ValueError` and therefore still falls through `BibliographyResolverChain` |

### Dependency direction

Unchanged and deliberately so: `core` → `ingestion` → `verification`/`drafting` →
`workflow` → `server`. Two constraints were enforced during this pass:

- `workflow/state.py` seeds `threshold_f_beta` from a **literal default**, not an import
  of `verification/citation_checker`, because a `state` → `verification` import would
  turn a leaf module into a dependency of the verification layer.
- The resolver XML path is reached through an overridable seam on `_HttpHibliResolver`,
  so only `ArxivBibliographyResolver` parses Atom. CrossRef, PubMed and OpenAlex remain
  JSON-only and no shared base gained XML handling.

### Known limits after this pass

- **DNS rebinding is closed for the body, not for the connection.** A rebinding hop
  raises `SourceLoadError` with zero body bytes read, but the TCP/TLS handshake and the
  request line/headers still reach the internal host before the mismatch is noticed.
  Blocking *contact* requires transport-level pinning or an egress proxy, which is not
  reachable from this call site. The peer check is deliberately **fail-closed**: any
  transport that cannot report a peer address is refused, which could reject a working
  future transport.
- **The bibliography resolvers still use `follow_redirects=True` with no
  `assert_public_url` guard**, unlike the source loader. The four destinations are
  compile-time literals and the identifier can no longer alter the authority, so a
  redirect can only be issued by the trusted provider itself; this is defence in depth
  rather than an open hole. Hardening it would require `resolve_public_addresses`
  pinning plus per-redirect re-validation on a shared file, and httpx has no supported
  way to bind connections to pre-vetted addresses without a custom transport.
- `threshold_f_beta` is plumbed end to end but **provably cannot change an outcome**: when
  the other three conditions of the four-part gate hold, every claim is entailed, forcing
  precision = recall = 1.0 and therefore F_beta = 1.0. Enumerating every claim-status
  composition confirms condition 1 never excludes a section for any threshold ≤ 1.0.
  Making it bite is a change to gate semantics, not a performance fix.
- `ResilientNLIEvaluator.evaluate_batch` is still not called: an unbounded gather would let
  a section open one NLI call per claim with no cap, and `MAX_CONCURRENT_NLI_EVALUATIONS`
  exists to bound exactly that. The current gather is bounded at the claim level.
- The lexical NLI fallback in `verification/nli_evaluator.py` remains a degraded path, not a
  substitute guarantee of semantic entailment.
- **`provenance/lineage.py` conflates "chunk missing" with "chunk lookup failed".** The
  `except Exception: chunk = None` branch around `indexer.get_chunk(...)` converts *any*
  indexer fault — a timeout, a corrupted store, a backend outage — into
  `ChunkNotFoundError`, which the strict hallucination check then surfaces as
  `CitationHallucinationError`. A system fault is therefore reported as a model
  hallucination. Left unchanged deliberately: the failure mode is **fail-closed** (a
  genuinely ungrounded claim can never pass), which is the safe direction, and the
  distinction is not currently covered by any test, so changing the most
  verification-critical path at the end of this pass would be under-verified. The
  correct fix is a distinct typed retrieval error, and it should land with tests that
  separate the two cases.
- No live provider call, no native release build, and no multi-subscriber soak test were run;
  the client/server handshake is verified against recorded payloads and offline ASGI transport.

---

## 8. Word processing rewrite — 2026-10-20

Binding contract: **`WORD_PROCESSING_CONTRACT.md`**. Executed as three parallel workers on
disjoint file ownership (reader / writer / manuscript bridge) against one shared type
definition authored by the orchestrator; merged state verified by the orchestrator.

### The defect this pass removes

The word-processing element is where LMeditor meets a foreign, adversarial document format,
and it is the only place where the product's central promise — *every claim is traceable to a
source passage* — must survive a round trip through a binary container. Both halves of that
boundary dropped structure:

- **Read**: `ingestion/loaders.py::DocxSourceLoader` used `python-docx`'s `Document.paragraphs`,
  which walks only the direct children of `w:body`. Tables, list structure, headers/footers,
  footnotes, content controls (`w:sdt`), text boxes and tracked changes were dropped silently.
  A document whose evidence lives in a table ingested as an empty string.
- **Write**: `export/docx.py` existed only on `checkpoint/pre-track0-recovery` and was
  **absent from `main`**. It flattened a manuscript to `add_heading(title, level=0)` plus
  `level=1` per section: no list, table, run formatting, footnote, citation or core-property
  support.

The rewrite replaces both halves with one shared document model, so structure that survives
ingestion is structure the writer can re-emit.

### Modules added by this pass

| Module | Responsibility |
| --- | --- |
| `backend/src/lmeditor/word/__init__.py` | Lazy facade over the word-processing public surface; the shared model imports without the OOXML machinery of the reader and writer. |
| `backend/src/lmeditor/word/model.py` | Format-neutral document model (`WordDocument`, `WordBlock`, `WordRun`, `WordTable`, `WordTableRow`, `WordTableCell`, `WordFootnote`, `WordProperties`, `WordBlockKind`, `ListMarker`) with the §3.1/§3.2 invariants enforced by validators. |
| `backend/src/lmeditor/word/text.py` | Whitespace and text-normalisation rules shared by both directions (§4): exotic Word spaces, whitespace collapsing, control-character stripping. |
| `backend/src/lmeditor/word/reader.py` | `.docx` package → `WordDocument` via `zipfile` + `lxml`, never `python-docx`'s body accessors. Recovers tables, list structure, footnotes, hyperlinks, tracked changes, content controls and text boxes; reports every omission through `last_omissions` instead of dropping it silently. |
| `backend/src/lmeditor/word/writer.py` | `WordDocument` → `.docx` package via `python-docx` plus raw OOXML: real `Heading N` styles, multi-level `numbering.xml` with per-group restarts, tables with repeating header rows, a hand-built `word/footnotes.xml` part, external hyperlink relationships. Byte-deterministic by default. |
| `backend/src/lmeditor/word/manuscript.py` | `DocumentDraft` + bibliography → `WordDocument`, consuming the inline citation markers into superscript markers with the cited passage attached as a footnote, plus a `References` section. |
| `backend/tests/word_fixtures.py` | A Word-365-shaped raw OPC fixture (Word's own part layout) exercising content controls, tracked changes, text boxes, field codes, page breaks, header/footer parts, footnotes and a table with a repeating header row. |
| `backend/tests/test_word_model.py` | Whitebox tests for the model invariants, including the failing case for each of §3.1 I1-I10 and §3.2 D1-D4. |
| `backend/tests/test_word_text.py` | Whitebox tests for the §4 normalisation rules, including idempotence. |
| `backend/tests/test_word_reader.py` | Whitebox tests for the reader against the Word-365-shaped fixture, one assertion per recovered structure. |
| `backend/tests/test_word_writer.py` | Whitebox tests for the writer at the OOXML level: parts, styles, numbering, tables, run formatting, hyperlink relationships, the footnotes part, core properties, determinism, and the typed error path. |
| `backend/tests/test_word_roundtrip.py` | Round-trip properties: model → write → read, Word-365 fixture → read → write → read, and the **whole product path** — `DocumentDraft` → bridge → package → text — the only test that starts where the product actually starts. |
| `backend/tests/test_word_manuscript.py` | Whitebox tests for the manuscript bridge, asserting that citation markers are consumed, numbered once per chunk, and carried into footnotes. |
| `backend/tests/test_word_ingestion.py` | The ingestion regression: table content must survive DOCX ingestion. |
| `backend/tests/test_word_contract_gaps.py` | The clauses an independent audit found uncovered or asserted weakly: grid spans, vertical merges (two- and three-row), multi-paragraph cells, internal anchors, relative hyperlink targets, `w:basedOn` chains, style-inherited numbering, soft breaks, field codes, multi-part headers/footers, exact UTC timestamps, malformed parts, the one-line-per-row rule, the `Title` style, `deterministic=False`, numbering restarts, and the writer's model validation. |
| `backend/tests/test_word_merges.py` | The vertical merge path in both directions — the second audit found it had no test at all. Covers the reader's merge model (origin carries the span and the text, continuations carry neither), the writer's emission (`w:vMerge w:val="restart"` for the origin, a continuing `w:vMerge` for a continuation, the grid counted in grid columns), and the round trip in the narrow and the wide case. |
| `backend/tests/word_real_word_check.ps1` | Opens a `.docx` in **Microsoft Word itself** over COM (invisible, read-only) and reports the structure Word actually sees: styles, list levels, pages, table count, cell widths, vertical merges, footnotes, hyperlinks and core properties. |
| `backend/tests/word_real_word_check.py` | The driver: builds the document under test, builds two **negative controls** (a truncated package and a package whose `word/document.xml` is not XML), runs Word over all three and turns the answers into assertions. Runnable on its own for a human-readable report. |
| `backend/tests/test_word_real_word.py` | The real-Word check as a test. It is **skipped when Word is not installed**, so the suite stays green on a machine without Office. |

### Public contract boundaries established

| Contract | Owner | Consumer |
| --- | --- | --- |
| `WordDocument` / `WordBlock` / `WordRun` / `WordTable` / `WordFootnote` with validators enforcing §3.1 I1-I10 and §3.2 D1-D4 | `word/model.py` | `word/reader.py`, `word/writer.py`, `word/manuscript.py` — the shared type surface; no module re-declares these types |
| `normalize_whitespace` / `join_texts` / `strip_control_characters` | `word/text.py` | reader and writer; the two directions must normalise identically or the round trip drifts |
| `WordReader.read(source) -> WordDocument`, `WordReader.read_bytes(data) -> WordDocument`, `WordReader.last_omissions` | `word/reader.py` | `ingestion/loaders.py::DocxSourceLoader`; a caller needing structure uses the reader directly, a caller needing text uses the loader |
| `WordWriter(deterministic=True).write(document) -> bytes` | `word/writer.py` | any caller holding a `WordDocument`; `deterministic=True` pins every zip member timestamp to the DOS epoch, so the output is byte-stable. A core-property timestamp the model does not carry is **omitted** rather than stamped with an epoch, so the package never claims a creation date the document does not have |
| `build_manuscript_document(document, bibliography, *, verified_only=False) -> WordDocument` | `word/manuscript.py` | the export path; consumes the citation grammar from `core/citations.py` rather than re-implementing it, so the drafting side and the export side cannot drift |
| `WordProcessingError` → `WordParseError(origin, reason)` / `WordRenderError(reason)` | `core/exceptions.py` | every malformed-package condition in the reader and every unrenderable-model condition in the writer; nothing else escapes to the caller |

### Dependency direction

`core` → `word` → `ingestion` → `verification`/`drafting` → `workflow` → `server`.

`lmeditor.word` sits **above** `core` and **below** `ingestion`: it may import `lmeditor.core.*`
and the standard library, and it must not import `ingestion`, `workflow` or `server`. Two
constraints were enforced during this pass:

- `lmeditor/core/citations.py` is the single source of truth for the inline-citation
  grammar, and **every** consumer reads it: the drafting side (`drafting/modules.py`), the
  verification side (`verification/claim_extractor.py`), the export side
  (`word/manuscript.py`) and the provenance side (`provenance/lineage.py`). No layer
  defines a second regex, so no two of them can disagree about which chunks a section
  cites. The provenance layer was the last holdout; the sixth pass below records it.
- Only `word/reader.py` (lxml) and `word/writer.py` (python-docx) touch OOXML machinery. No
  other module in `src/` imports `lxml` or `docx`, so the format knowledge stays inside one
  domain.
- `python-docx` is now a **writer-only** dependency. The ingestion path reads a `.docx` with the
  raw reader and no longer needs python-docx at all — the old loader used `Document.paragraphs`,
  which is exactly the accessor that dropped tables, lists, headers and footnotes.
- The two halves use deliberately different mechanisms: the **reader** is raw `zipfile` + `lxml`,
  because it must reach parts python-docx does not expose (numbering definitions, headers and
  footers, content controls, tracked changes, text boxes). The **writer** is python-docx, because
  it gets package plumbing (content types, relationships, part wiring) for free. The writer does
  reach into python-docx *internals* (`docx.oxml`, `docx.opc.part.Part`,
  `docx.opc.oxml.serialize_part_xml`), which python-docx does not guarantee across releases, so
  the dependency is bounded to `>=1.0.0,<2.0.0` rather than left open.

### Verification

The contract's §8 acceptance gates, and the evidence for each:

| Gate | Requirement | Evidence |
| --- | --- | --- |
| 1 | No regression against the pre-pass baseline | `pytest -q` → **604 passed, 1 skipped** (the one skip is pre-existing; the only warning is torch's `torch.jit.script` deprecation, unrelated) |
| 2 | `mypy --strict src/lmeditor/word` clean | `mypy --strict` over the word package plus `core/exceptions.py`, `core/__init__.py`, `core/citations.py`, `ingestion/loaders.py`, `provenance/lineage.py` → **no issues found in 11 source files** |
| 3 | The round-trip property holds, and a vertical merge survives as a merge | `tests/test_word_roundtrip.py` (13 tests, including the whole product path) plus `tests/test_word_merges.py`; the merge is checked in the narrow case (one column) and the wide case (an origin spanning two grid columns, where the merge is a region and the grid width must not grow). The round trip is also checked against **real Word** in the seventh pass, which is the only place a well-formed package can still be a wrong document |
| 4 | Every §3.1/§3.2 invariant has a failing-case test; every §4/§5/§6/§7 clause has a test that would fail if it were broken | §3.1/§3.2 audited clause by clause against the validators in `word/model.py` — the one drift found (a list item's nesting depth was bounded in the model but declared nowhere) is now clause I10 with its own failing case. §4/§5/§6/§7 audited by **mutation**: 89 clauses were each broken in an isolated copy of the tree and the suite re-run, so "covered" means *a test actually failed*, not "a test mentions the clause". Every clause is now covered; the eleven gaps and weak assertions the sweep found are listed in the fifth pass below, and the two the sweep could not have found — because they are Word behaviours rather than package properties — are listed in the seventh pass |
| 5 | `DocxSourceLoader` returns text that includes table content | `tests/test_word_ingestion.py::test_table_content_survives_ingestion` — the regression that motivated this pass |
| 6 | The writer's output opens as a valid package and declares the expected styles | `tests/test_word_writer.py` re-opens the written bytes with `docx.Document(BytesIO(payload))`; the style declarations are asserted against `word/document.xml` |
| 7 | No component outside `lmeditor/word/` imports `zipfile`/`lxml` for OOXML purposes | Verified mechanically over `src/lmeditor/**`: the only `zipfile`, `lxml` and `docx` imports in the tree are in `word/reader.py` and `word/writer.py`. Everything else mentions "DOCX" only as an enum value, a file suffix or a docstring |

- The pre-pass working tree measured 388 passed, 1 skipped; the committed `HEAD` alone measures 102
  passed, 1 skipped, so the 388 figure describes the **uncommitted pre-pass tree**, not a commit, and
  is not reproducible from the repository.
- Every §3.1/§3.2 invariant has a dedicated failing-case test, and every §4/§5/§6/§7 clause
  named in the contract is covered by at least one test — a claim that was **false** at the
  first pass and was corrected by the second and third (see below).
- The round-trip property holds in both directions: `WordReader().read(WordWriter().write(doc))`
  yields the same `plain_text()` as the original, and a Word-365-shaped document survives
  read → write → read with its headings, list markers and table intact, **including a vertical
  merge, which stays a merge** — in the narrow case (one column) and in the wide case (an origin
  spanning two grid columns, where the merge is a region and the grid width must not grow).
- The final measurement of this working tree is **689 passed, 1 skipped** across the whole
  backend suite (`pytest -q`), of which **297 passed** are the word domain
  (`pytest tests -q -k word`). The single warning is torch's `torch.jit.script` deprecation,
  which is pre-existing and unrelated.
- **Opened in real Word — now a repo-checkable gate.** Microsoft Word 16.0 is installed
  on the author's machine and can be driven over COM, so the element is checked against the
  **reference implementation** rather than against a model of it (see the seventh pass). The
  earlier one-off check against **Word for the Web** — which required an authenticated
  session and a file in the author's OneDrive — is superseded by it: the current build is
  opened by Word itself, on a machine, with no cloud artifact and no manual step.

### Second pass — corrections after an independent audit

An independent audit of the first pass (run against the contract, not against the
implementation's own claims) found five contract violations and five assertions that could not
fail. All are fixed:

| Finding | Fix |
| --- | --- |
| **W10** — the writer never validated the model it was handed, so a document built with `model_construct` (bypassing pydantic validation) was rendered with the illegal parts silently dropped | the writer now re-validates the model against §3.1/§3.2 before rendering and raises `WordRenderError`; four dedicated tests cover the illegal shapes |
| **R4** — a vertical merge was modelled backwards: the *continuation* cell carried a hard-coded `row_span=2` and the origin carried `1` | the origin now carries the true span (counting its own row) and keeps its text; continuation cells carry `is_merge_continuation=True` and no text. A new model field, `WordTableCell.is_merge_continuation`, and invariants **I9**/**D4** make the semantics explicit. |
| **R4 cross-component** — a merge read from a real document was written back with no `w:vMerge` at all | the writer emits `w:vMerge w:val="restart"` for an origin and a continuing `w:vMerge` for a continuation, keeping the synthesis fallback for hand-authored models |
| **R5** — a relative hyperlink target was passed through verbatim | the reader gains an optional `base_uri`; a relative target is resolved against it, and without a base it is preserved **and recorded in the omission register** rather than silently mangled |
| **T5/T7** — a table dropped its blank rows, so a three-row table could render two lines | a table renders exactly one line per row, blank rows included |
| Five assertions that could not fail (an `isinstance` check, a self-comparison, an un-pinned timestamp, a substring-only formatting check, a mislabelled error-path test) | all five now assert the behaviour they claim; a new module, `tests/test_word_contract_gaps.py`, adds the 20+ clauses the audit found uncovered |

The ledger's earlier claim that every invariant had a failing-case test was **overstated** (the
D3 determinism test was a tautology) and is now true. The earlier claim that vertical merges
"lose no text" was **understated** (the merge itself was lost) and is now fixed.


### Third pass — corrections after a second independent audit

A second independent audit, run against the contract again rather than against the
implementation's own claims, found the first pass's merge work **incomplete on the multi-column
axis** and the merge path **untested**. All findings are fixed:

| Finding | Fix |
| --- | --- |
| **R4, multi-column** — a merge whose origin spans two grid columns (Word's 2×2 merged block) registered the origin at its *starting* column only, so the second continuation cell was left as an ordinary cell and **its text survived as content**. Read → write → read of such a table added a spurious grid column and changed `plain_text()` | the reader now models a merge as a grid **region** covering every column the origin spans; the merged region's cells become continuations and the text stays in the origin |
| **W4, grid arithmetic** — the writer counted *cells* rather than grid columns, so a wide merge produced an extra `w:gridCol`; and a model whose continuation cell was **not first in its row** made the writer invent an extra `<w:vMerge/>` cell *and* a third grid column | the grid is now built from grid columns: a cell occupies `col_span` columns from its placed column, and a continuation is matched to the merge it continues and placed on **that merge's column**, so no cell and no column is invented whichever order the model lists them in |
| **D4, unenforced** — the rule "a continuation cell carries no text" was documented but not enforced, so `WordTableCell(text="LEAKED", is_merge_continuation=True)` could still be constructed and the writer dropped the text silently | the model now refuses such a cell at construction time, and the writer refuses it again before rendering, raising `WordRenderError` instead of dropping content |
| **The merge path had no test at all** — mutating the writer to stop synthesising continuations, or to emit a bare `w:vMerge` instead of `w:vMerge w:val="restart"`, left every test green | a new module, `tests/test_word_merges.py` (13 tests), covers the reader's merge model, the writer's merge emission, and the round trip in both the narrow and the wide case; `tests/test_word_model.py` gained the I9 and D4 failing-case tests |
| **A false creation date** — the deterministic writer stamped `dcterms:created`/`dcterms:modified` with the DOS epoch whenever the model carried no timestamp, so Word's file properties claimed the document was created in 1980 | a timestamp the model does not carry is now **omitted** from `docProps/core.xml`; each of the two is decided on its own, so a document with a creation date but no modification date says exactly that |

The merge audit also re-checked the properties the first pass claimed. The round trip now holds
for the **wide** case as well as the narrow one: a 2×2 merged block read from OOXML comes back
with the same grid width, the same spans, the same flags and the same `plain_text()`.


### Fourth pass — contract audit, and a real defect it found

The contract's §8 gate 4 was audited clause by clause rather than by assertion-counting. The
model invariants (§3.1/§3.2) were checked directly against the validators in `word/model.py`;
§4/§5/§6/§7 were checked by **mutation** — each clause broken in an isolated copy of the tree,
the suite re-run, and "covered" recorded only when a test actually failed.

Two findings came out of it that reading the code had not:

1. **A list item's nesting depth was bounded in the model but declared nowhere in the contract.**
   `WordBlock` refuses `level > 8` for a `LIST_ITEM` — the nine levels Word's numbering can
   express (`w:ilvl` 0-8) — but no clause said so, and no test covered it. The rule is now
   clause **I10**, with a failing case and an accepted case in `tests/test_word_model.py`. The
   contract is the binding document, so a rule the model enforces has to be written down; the
   alternative would have been to drop the rule, which would have let a depth Word cannot
   express reach the writer.
2. **The reader leaked a raw `zlib.error` on a corrupt compressed member.** The contract (§5
   R13) requires *every* malformed-package condition to arrive as a `WordParseError`. CPython's
   `zipfile` calls `zlib.decompress` without wrapping the failure, so a damaged deflate stream
   escaped the reader as `zlib.error: Error -3 while decompressing data` — an exception the
   caller has no way to handle. `_parse_part` now refuses the part with a `WordParseError`.
   The catch is deliberately broad: everything inside `archive.read()` belongs to the standard
   library, so no LMeditor code runs within it and nothing of ours can be masked.

The audit also added the tests the element was missing:

- `tests/test_word_reader.py` — a **corruption property test**: one byte of a valid package is
  corrupted at a time and the reader must either read a document or refuse it with
  `WordParseError`. The three targeted malformed cases only proved three ways of being
  malformed; this proves the *discipline*. It is the test that found the `zlib.error` leak.
- `tests/test_word_roundtrip.py` — the **whole product path**: `DocumentDraft` → bridge →
  package → text. Every other round-trip test started from the shared model or from a raw Word
  fixture; this is the only one that starts where the product actually starts. Its assertion
  that the cited passage survives is falsifiable — dropping the passage in the bridge makes it
  fail.
- `tests/test_word_model.py` — the I10 failing case, and I7's failing case now cites I7 as well
  as D1 (the two clauses state the same rule, and I7 is enforced on the `WordDocument`).

And one dependency change: `python-docx` is bounded to `>=1.0.0,<2.0.0`. The writer reaches into
python-docx *internals* (`docx.oxml`, `docx.opc.part.Part`, `docx.opc.oxml.serialize_part_xml`),
which python-docx does not guarantee across releases, and an unbounded range would let a major
release break the writer silently.

### Fifth pass — the clause-by-clause mutation sweep

Acceptance gate 4 demands that every §4/§5/§6/§7 clause has a test that *would fail if the
clause were broken*. That claim had been made twice and been false twice, so this pass proved
it by mutation rather than by reading: each of the 38 clauses — plus the omission-register
paragraph that follows R13 — was broken in an isolated copy of the tree and the suite re-run,
across **89 mutation runs** in two independent sweeps (52 for §4/§5, 37 for §6/§7). Several
clauses were broken in more than one way: a heading's id branch and its name branch separately,
a field's instruction and its cached result separately, a model's re-validation and the
individual check each on their own. "Covered" was recorded only when a test
failed **for that clause's own reason** — the failure output was read, not just the FAILED
lines. Where a mutation produced no failure at all, the whole suite was re-run to prove it was a
survivor rather than a gap in the word tests.

Eleven gaps and weak assertions survived. All are now closed, and each new test was
**re-mutation-tested** to prove it fails when its clause is broken:

| Gap | What was unasserted | Fix |
| --- | --- | --- |
| W3 | A nested list item's `w:ilvl` was never read back — forcing every item to level 0 changed nothing | `test_a_nested_list_item_writes_its_level_as_ilvl` reads the emitted `w:ilvl` value |
| W6 | The hyperlink relationship's **type** was unasserted — pointing it at an image relationship changed nothing | `test_the_hyperlink_relationship_has_the_hyperlink_type` |
| W1 | The package was re-opened by python-docx but its XML parts were never parsed; python-docx ignores the parts it does not need | `test_every_emitted_xml_part_is_well_formed` parses every XML part and checks the OPC parts are present |
| W10 | The writer's own re-check of the merge rule was never exercised | `test_a_merge_continuation_carrying_text_is_refused_by_the_writer` builds the model around its own validation and requires a `WordRenderError` |
| M2 | The `section_id` tiebreak in the ordering was never exercised — two sections with the same `order` were untested | `test_sections_with_the_same_order_are_broken_by_id` |
| M6 | The bridge's purity was only tested with **one** section and **one** record, so an in-place sort was a no-op and went unnoticed | `test_the_inputs_are_not_mutated` now uses two sections and an out-of-order bibliography |
| R13 | The omission register's **counter** never rendered, because every fixture had exactly one image | `test_the_omission_register_counts_repeated_omissions`, with a two-image fixture |
| R4 | A table **inside a cell** was never exercised, so the recursive-read branch could be deleted unnoticed | `test_a_table_inside_a_cell_is_read`, with a nested-table fixture |
| R2 | A heading style's id and name both matched in every fixture, so neither branch of "id *or* name" was exercised alone | `test_a_heading_is_recognised_by_its_style_id_or_its_name`, with a fixture where one heading matches only by id and the other only by name |
| R9 | The fixture's `w:fldSimple` was empty, so "contributes no text" was indistinguishable from "had no text" | the fixture's field now carries a cached result, and `test_a_simple_field_contributes_no_text` requires it to be dropped |
| T8 | A page break rendering a form feed would be invisible to `plain_text()`, which filters whitespace | `test_plain_text_omits_a_page_break` now also requires the block to render to the empty string |

One mutation was an **equivalent** one and is recorded as such rather than as a gap: removing
the writer's *inner* merge check changes nothing, because the writer's model re-validation
already refuses that model. The contract's requirement — a `WordRenderError`, never a silent
drop — is what the new test pins, and that is the observable the clause is actually about.

### Sixth pass — closing the integration seam with the provenance layer

The element was rewritten in isolation, and the seam to the layers around it was left open:
`core/citations.py` was introduced as the single source of truth for the inline-citation
grammar, but **one** consumer still carried a second, private regex.
`provenance/lineage.py::CitationLineageEngine` matched `\[(?:#|cite:\s*)?([a-f0-9]{64})\]` —
exactly one id per bracket — while the drafter, the verifier and the manuscript bridge all
read the canonical grammar.

The divergence was not cosmetic. A marker **group**, `[#chunk_a, #chunk_b]`, is accepted by
the canonical grammar, so the verifier checks such a group chunk by chunk and the manuscript
bridge writes one footnote per chunk in it; the lineage engine dropped the whole marker,
because its regex could not match a bracket holding two ids. A section could therefore
carry **no lineage at all** while its footnotes were written — the provenance of the draft
and the document the user receives disagreed about the same citations.

| File | Change |
| --- | --- |
| `backend/src/lmeditor/provenance/lineage.py` | reads its markers with `iter_citation_spans`; the historical class attribute becomes an alias of the canonical pattern, so no public name changed |
| `backend/tests/test_provenance.py` | four tests: the marker group, the agreement with the canonical grammar, the chunk-store guard, and the upper-case chunk id |

The engine now reads its markers with `iter_citation_spans`, the same helper the manuscript
bridge uses. A chunk id is resolved as written first and then lower-cased, which preserves
the old case-insensitive behaviour for the lower-case hex digests the chunker produces.

The four new tests were **mutation-tested**: restoring the old regex, dropping the
lower-case fallback, dropping the chunk-store guard, and collapsing a marker group to its
first id each make a test fail.

The old regex was also the source of a wrong claim in this ledger. The migration was first
written as "a citation to any non-hex chunk id was silently dropped", which is unreachable:
`SourceChunk.chunk_id` is constrained to `^[a-f0-9]{64}$`, so a chunk store can only ever
hold 64-character hex digests. The defect is the **marker form**, not the id alphabet, and
the ledger and the code comments now say so.

### Seventh pass — the check against real Word, and the two defects it found

The element had been verified against the contract and against a synthetic Word-365-shaped
package, but never against **Word**. Microsoft Word 16.0 is installed on the author's machine
and can be driven over COM, so the element is now checked against the reference
implementation itself. This is a different kind of evidence, and it found two defects that no
package-level test could see.

**The harness.** `backend/tests/word_real_word_check.ps1` opens a package in Word (invisible,
read-only) and reports the structure Word actually sees: the styles it applied, the list
levels, the page each paragraph landed on, the table count, each table's rows and cell
widths, the vertical merges, the footnotes, the hyperlinks and the core properties.
`backend/tests/word_real_word_check.py` drives it, builds the document under test and two
**negative controls**, and turns the answers into assertions;
`backend/tests/test_word_real_word.py` runs it as a test and **skips when Word is not
installed**, so the suite stays green on a machine without Office.

The negative controls matter as much as the good file. A truncated package and a package
whose `word/document.xml` is not XML are the two packages Word must refuse; it refuses both
("The file appears to be corrupted." and "Word experienced an error trying to open the
file."), so a pass on the good file means something rather than being the harness's default
answer. A check that only ever sees good files cannot tell a good file from a bad one.

#### Defect 1 — two adjacent tables became one table (W11)

The writer emitted two `<w:tbl>` elements side by side. OOXML has no table boundary, and Word
reads two adjacent tables as **one** — so the second table's grid was destroyed and its
merges went with it. Word reported `TABLE_COUNT=1` for a document that holds two tables, and
the three-column grid of the second table became two columns.

Nothing in the whitebox suite could see this. The package was well-formed; the reader parses
the XML and still saw two tables; the round trip was exact. The mutation sweep **proved** the
round trip stayed exact with the separator removed, because the round trip never involves
Word. Only Word merges them. The writer now separates two adjacent tables with an empty
paragraph — the separator Word itself writes when it splits a table — and the reader drops
it again (an empty paragraph carries no content, T5), so the round trip is still exact.

#### Defect 2 — a table whose row does not fill the grid was rendered into something else (W12)

A table's grid is as wide as its widest row, and **every** row must fill it. A row that falls
short is malformed, and Word does not refuse it: it silently re-lays the table out, so the
document the reader opens is not the document the model describes. The writer now refuses such
a table with a `WordRenderError` rather than emitting one that Word will change. The check
runs on the same layout the renderer emits, so the two cannot disagree about what the table
looks like.

#### Evidence from the real-Word run

| What Word reported | What it proves |
| --- | --- |
| `OPENED=yes` — no repair prompt | W1, on the current build rather than on a remembered one |
| `TABLE_COUNT=2` | W11: the two tables stay two tables |
| `TABLE1\|vertical_merge=yes` | the vertical merge is a merge **to Word**, not just in the XML |
| `'Wide merge across two columns' (w=312.0pt)` beside `'C' (w=156.0pt)` | the wide merge is exactly twice its neighbour's width: the `gridSpan` survived |
| `style=Title`, `style=Heading 1`, `style=Heading 2` | W2, as Word resolves the styles |
| `level=2` on the nested list item | W3: the nesting depth survived, not just the text |
| `FOOTNOTE1=The footnote body that Word must show.` | W7: the footnote body is genuinely in the file Word loaded |
| `address=https://example.org/lmeditor` | W6: the hyperlink is live in Word |
| `PROP_TITLE=LMeditor Word Feature Check` | W8: the core properties are read back by Word |
| `page=2` on the paragraph after the break | the page break is a page break |
| two corrupt controls refused | the check can tell a good package from a bad one |

Both new clauses were **mutation-tested**: removing the separator and disabling the grid
check each make a test fail.

### Known limits after these passes

- **The real-Word check needs Word on the machine.** The harness is reproducible and runs in
  the suite, but it is skipped where Microsoft Word is not installed, so a green run on such a
  machine is **not** evidence that the package is conformant — only that the whitebox suite
  is. The check is local and leaves no cloud artifact; the earlier Word-for-the-Web route,
  which needed an authenticated session and a file in the author's OneDrive, is superseded by
  it.

- **The canonical citation grammar is broader than the chunk-id model.** `core/citations.py`
  accepts any `[A-Za-z0-9_.\-]+` id, while `SourceChunk.chunk_id` accepts only a 64-character
  lower-case hex digest. A citation to an id of any other shape is therefore accepted by the
  drafter, the verifier and the export path, and is then dropped by the lineage engine,
  because no chunk of that shape can be in the store. The grammar is deliberately broader
  (test fixtures and `HarnessState` use non-hex ids), so this is a known looseness rather
  than a defect — but it is the reason the lineage engine can legitimately record fewer
  citations than the verifier checks.
- **The shared reference document was empty.** The Word 365 link resolves to `Document 3.docx`,
  which opens as "Page 1 of 1, 0 words". There was therefore no content in it to whitebox the
  reader against, and the fidelity suite runs against the synthetic Word-365-shaped package in
  `backend/tests/word_fixtures.py` instead. A populated real document is still an open check.
- **A relative hyperlink target cannot be made absolute without a base.** The reader resolves
  one against the caller's optional `base_uri`; with no base, the target is preserved verbatim
  and reported in the omission register. A `.docx` carries no location of its own, so this
  cannot be resolved from the package alone.
- **Images are carried, not dropped.** *(This bullet was stale: it described the state before
  the media pass.)* The media part travels in `WordDocument.media` with its bytes intact, the
  run that embeds it carries the media id, and the writer puts the part back into the package
  and re-anchors it in the text. Only an image the reader genuinely cannot reach — a broken
  relationship, a part that is not in the package — is reported in the omission register, with
  its relationship id and part name.
- **Comments and endnotes are carried, not dropped.** *(Also stale.)* `word/comments.xml` and
  `word/endnotes.xml` are parsed into the model, the runs they cover carry their ids, and the
  writer emits a real comments part and a real endnotes part on the way out. A comment's text,
  author and initials survive a read → write → read round trip.
- **Tracked changes are re-emitted, not flattened.** *(Also stale.)* The writer groups the runs
  of one revision into a `w:ins` or a `w:del` container with the revision's id, author and
  date, and a deleted run's text is written as `w:delText` rather than `w:t` — which is what
  Word expects.
- **Several Word constructs still have no representation and are dropped rather than
  carried.** The reader records each one in the omission register, but the content itself is
  gone: **embedded objects** (`w:object`, and the `word/embeddings/` parts they carry),
  **charts and diagrams**, the **glossary** part (building blocks), **alternative format
  chunks** (`w:altChunk`), **subdocument** references (`w:subDoc`), a **formatting change**
  (`w:rPrChange`), and any **package part the reader does not model**. The prose around them
  survives. A content control (`w:sdt`) is read when it has content; only an *empty* one is
  reported.
- **Equations and symbols were dropped; that is being closed.** *(See section 11.)* An
  `m:oMath` equation and a `w:sym` symbol are the two constructs that used to lose their
  content entirely.
- **A vertical merge is modelled the way Word models it, not the way it is drawn.** The origin
  cell carries the span and the text; the cells below it are flagged `is_merge_continuation` and
  carry no text, because Word ignores content stored in a continuation cell. A merge that spans
  several **grid columns** is modelled as a region covering all of them, so its continuations are
  as many as the columns it covers. Re-issuing a document whose merged cell was edited in place
  will therefore need the text re-attached.
- **The export HTTP surface now exists.** *(This bullet was stale: it described the state before
  the export route was added.)* `GET /api/v1/documents/{doc_id}/export.docx` renders the
  document through `build_manuscript_document(...)` → `WordWriter().write(...)` and returns the
  `.docx` package, so the word-processing element is reachable by the client and not only by
  the library.
- **`lxml` and `python-docx` have no stubs in the backend environment.** `lxml` needs one
  `# type: ignore[import-untyped]` on its import; `python-docx` 1.2.0 ships `py.typed` and must
  carry no ignore at all, because `warn_unused_ignores` is enabled.

---

## 9. Remediation pass — 2026-10-21 (the code-audit remediation)

This pass is the remediation of the audit recorded in `CODEBASE_REPORT.md`. Every finding in that
report is a defect in the *code*, not in a document, and every fix below was verified by running
the code.

### The defect this pass removes

The application's central promise is that a manuscript is **source-grounded**: every sentence is
attributable to a passage the writer actually read. The code did not honour that promise:

- A failed draft was replaced by a **template string** (`"{title}: According to verified
  documentation, {chunk[:180]} [#id]"`). An outage of the language model therefore produced a
  document that *looked* written and was cited as though it were, and the citation pointed at a
  chunk the model had never read.
- The **entailment premise was the whole cited chunk**, not the sentence the claim speaks to. A
  verbatim quotation judged against a three-sentence chunk returned `NEUTRAL` (0.998); the same
  quotation judged against its own sentence returned `ENTAILED` (0.9945). The verifier was
  measuring the wrong thing, so it reported ungrounded claims as unverified.
- A **citation marker was attached to the following sentence**, leaving the supported claim
  uncited and crediting the next sentence instead.
- **Retrieval was a keyword count** over a hard-coded skeleton of section titles, falling back to
  "the first three chunks" — the passages a section was written from were the first ones the
  server happened to hold.
- The **outline was a constant skeleton**, the **perspectives were a constant list**, and the
  **document scorecard was a fabricated aggregate**.

### The invariant that replaces it

**LMeditor never fabricates prose.** A section that could not be drafted is *empty*, labelled
with the reason, and recorded in the run's errors. A section that cannot be verified says so.
There is no code path in the application that produces text a model did not write.

**And a document that was never written is never reported as a finished document.** This is
the second half of the invariant, and it was broken in a subtler way than the first:

- The citation metrics are defined to be *perfect* when there is nothing to check — zero claims
  gives precision 1.0, recall 1.0 and F_beta 1.0. That is the right convention for a section
  that **was** written but contains no checkable proposition. It was the wrong convention for a
  section whose drafting **failed**, because that section is empty precisely because there is
  nothing there.
- An unwritten section was therefore marked `VERIFIED`, the run routed straight to macro
  cohesion, and the application published a *completed*, fully grounded document whose sections
  were empty. With no language model configured at all, every run did this: the document looked
  finished, the scorecard read `f_beta = 1.0`, and the operator was never asked.
- The rule now is: **a section is verified when it was written and every claim in it is
  entailed.** An empty section is never verified, its scorecard counts one unsupported unit, and
  the run reaches the human-in-the-loop gate, where the operator sees that nothing was written.
  The document-level aggregate applies the same rule, so a document missing a section no longer
  reports `f_beta = 1.0`.

### Modules added by this pass

| Path | Responsibility |
| :--- | :--- |
| `lmeditor/llm/config.py` | `LMConfig.from_env`, `parse_provider`, the `LMProvider` enum. |
| `lmeditor/llm/factory.py` | `configure_lm`, `is_configured`, `require_lm`, `status`, `reset`, `build_lm`. |
| `lmeditor/llm/stub.py` | `StubLM`, the deterministic offline drafter. |
| `lmeditor/server/sources.py` | `SourceLibrary`, the server's memory of ingested sources. |
| `backend/scripts/verify_end_to_end.py` | Drives a live server through ingest → generate → SSE → document → export and reports what it observed. |
| `backend/scripts/verify_hitl_live.py` | The same, against a server started **without** a language model, so the review gate is reached: proves the gate is published, that it can be answered, and that the run then finishes. |
| `backend/tests/test_workflow_e2e.py` | The end-to-end proof, with a deterministic model double. |
| `backend/tests/test_model_provider.py` | The provider configuration and the stub's contract. |
| `backend/tests/test_wire_contract.py` | Reads the Kotlin tier's own source and checks it against the backend's event vocabulary, the SSE envelope and the operator outline. |
| `backend/tests/stub_lm.py` | Re-exports `lmeditor.llm.stub` for tests. |

### Contract boundaries established

- **`EventType` is the single event vocabulary.** It was extended to thirteen values
  (`CHUNKS`, `PERSPECTIVES`, `OUTLINE`, `CLAIM_VERIFIED`, `CITATION_CREATED`, `STREAM_METRICS`
  among them) so that the backend's published events and the Kotlin tier's declaration agree by
  construction. `tests/test_wire_contract.py` enforces this by reading the Kotlin tier's own
  source, so the two halves of the tier cannot drift apart silently.
- **The language model is a configured dependency, not an ambient assumption.** `/api/v1/health`
  reports whether one is loaded, which provider it is, and whether it is the stub. The scorecard
  names the evaluator that produced it. A run without a model is *legible*: it says so at every
  point where prose would otherwise appear.
- **`VerificationReport.evaluator` and `VerificationScorecard.evaluator` name the engine.** A
  score is meaningless without knowing what produced it.
- **`Source.omissions` and `SourceIngestResult.omissions` carry the loader's omissions register**
  out to the client, so content the loader could not preserve is visible to the operator rather
  than silently dropped.

### Dependency direction

- `lmeditor.core` ← `lmeditor.llm` ← `lmeditor.workflow` ← `lmeditor.server`. Nothing below
  `core` imports anything above it.
- `lmeditor.provenance.service` is the seam between `lmeditor.provenance` and
  `lmeditor.workflow`: the workflow knows *how to rank and attribute*, not how BM25 and dense
  retrieval are fused.
- The server owns the HTTP boundary and the source library; the ingestion package owns the byte
  caps and the persisted-upload store. The two no longer duplicate either.

### Verification

- `pytest -q` → **880 passed, 0 skipped**. The last skip (a PDF fixture with no extractable
  text) was removed by rebuilding the fixture with a real content stream, so the ingestion path
  is now exercised rather than skipped.
- `mypy src` **and** `mypy src --ignore-missing-imports` → **Success: no issues found in 57 source
  files**. Both spellings are clean because the untyped third-party modules (`lxml`, `markdown`)
  are declared in `pyproject.toml` rather than silenced with inline ignores, which are themselves
  reported as unused when mypy is invoked with `--ignore-missing-imports`.
- `scripts/verify_end_to_end.py` → **every check passed** against a live server: three chunks
  ingested, the full event vocabulary streamed, `scorecard: f_beta=1.0 supported=3/3
  verified=True`, all three sections verified, and a valid 37 KB `.docx` exported.
- The terminal frame was inspected directly: `workflow_complete` carries the real aggregate
  (`f_beta`, `total_sections`, `verified_sections`, `all_passed`), not `None`.
- The real cross-encoder was measured directly: `ENTAILED` at 0.9945 / 0.9893 / 0.9783 on
  focused premises, against `NEUTRAL` at 0.5625 / 0.6838 / 0.9983 on whole-chunk premises.
- The Flutter client was verified with the toolchain at `C:\src\flutter` (Flutter 3.47.1,
  Dart 3.13.1): `flutter pub get` → resolved; `flutter analyze` → **No issues found**;
  `flutter test` → **118 passed** (plus 5 skipped — the live tests, which skip
  themselves unless `LMEDITOR_LIVE_BACKEND` names a server, and pass when it does);
  `flutter build windows --release` → **built**
  (`build/windows/x64/runner/Release/client.exe` plus the Flutter runtime and `data/app.so`).
- **The client was then run against a live backend** (`scripts/verify_client_live.py`), which
  found a defect that every fake-driven test had missed: the backend published the outline
  *container* as though it were an outline *node*, so the event carried one node with an empty
  id and an empty title. The client's parser refuses both, so the outline panel never received
  an outline — and the event was published, so nothing looked wrong from the outside. The
  outline now publishes the real hierarchical tree, with corpus-derived titles and the chunk
  ids assigned to each leaf.
- The two halves were also checked against each other **at runtime**, not only on paper: every
  path the client calls (`/api/v1/health`, `/api/v1/sources/ingest`, `/api/v1/sources/url`,
  `/api/v1/documents/generate`, `/api/v1/stream/{doc_id}`,
  `/api/v1/workflow/{doc_id}/approve`) exists on the backend; a generate request with no corpus
  is rejected with 422; an approve request without `doc_id` is rejected with 422 *naming the
  missing field*, while the same request with `doc_id` is accepted; and the client's
  `modified_sections` payload validates against the backend's `SectionDraft` (`extra="forbid"`).
- Three client flows are now proven live rather than against a fake:
  - **the review gate** — the client receives the gate with a reason and a document id, answers
    it with a body the backend accepts, and then sees the run finish;
  - **a dropped connection** — the client drops its subscription mid-run, re-attaches with
    `resumeStream(docId)`, and the server's replay carries the run to its terminal frame without
    the document being generated a second time;
  - **the ordinary run** — ingest, generate, the full event vocabulary, the real scorecard and a
    non-empty manuscript.

### Tenth pass — reading the real payloads (finding 5.4)

The audit's finding 5.4 was that the perspectives were template personas that never saw a source:
`perspective_discovery_node` passed no context, so the personas were a job-title list with the
topic interpolated. The context is now passed, and this pass **read the actual payload** to
confirm it — the way the outline defect was found, by looking at what the server really sends
rather than at what the contract says it should send.

Reading the payload found three defects that no contract test could have caught, because the
contract said nothing about vocabulary:

- **The chunk digests were being read as vocabulary.** `_corpus_context` prefixed every passage
  with its id, so the corpus context began `[#0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74] Municipal
  composting diverts…`. The id is a 64-character token, the salience ranking prefers long
  candidates, and the personas came out reading the material *"through composting,
  0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74, 708e529c6b71a0738…"* — and
  the focus areas asked what the source says *"about 0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74"*.
  This is not only a display defect: `SectionDrafter` passes `perspective.description` to the model
  as the persona it must adopt, so the digest was in the drafting prompt too. The context is now
  prose only; the identity of a passage travels with the chunk, not inside the text a generator
  analyses.
- **The finding-verb lexicon had only some inflections.** It carried `reduced` but not `reduce`
  or `reduces`, `increased` but not `increase`, `showed`/`shows`/`show` but `reported`/`reports`
  and then nothing for `suggest`. A corpus of ordinary present-tense sentences therefore produced
  **no findings at all**, so the personas were anchored on vocabulary but never on a claim — the
  "point of departure is the reported finding" branch was inert for most sources. The lexicon is
  now a set of verb *families*, matched on word boundaries, so a finding is detected in the tense
  the source is written in.
- **An identifier could still outrank a word.** `content_tokens` now drops tokens too long to be
  words, which is what the function's own comment had always claimed it did. This is defence in
  depth: an identifier in a source document, or a prefix reintroduced by a future caller, can no
  longer become the most distinctive word of a corpus.

The invariant these fixes serve: **a persona is grounded in the corpus it was given.** The
regression tests assert it directly — two corpora with disjoint vocabulary produce two disjoint
sets of personas, neither borrowing a word from the other, and no persona carries an identifier.

| Path | Responsibility |
| :--- | :--- |
| `backend/tests/test_perspective_grounding.py` | Proves the personas are derived from the corpus: vocabulary grounding, finding grounding, corpus-to-corpus disjointness, the node's wiring, and the absence of identifiers. |
| `backend/scripts/verify_end_to_end.py` | Now also asserts, against a live server, that the published personas mention the corpus's vocabulary and carry no passage identifier. |
| *The live client test* | The live client test asserted the same two properties on the payload the perspective panel actually renders. It was removed in §19; `backend/scripts/verify_end_to_end.py` now asserts them against the live server. |

### Eleventh pass — the fidelity number the user actually sees

The tenth pass's technique was applied once more, to the payload types that had not yet been
read: `chunks`, `outline`, `claim_verified`, `citation_created`, `scorecard_update`,
`stream_metrics` and `workflow_complete`. The claim and citation payloads turned out to carry a
defect of the same family as the two above — a number that looked like a measurement but was not
one.

A single claim was published with **three** citations. One was real:

```
"exact_quote": "The decline was driven by a citywide composting programme introduced in",
"confidence_score": 0.857
```

The other two pointed at passages that do not contain the claim at all — the household-recycling
passage and the collection-rates passage — and both reported `"confidence_score": 0.5`.

**Why 0.5.** `CitationLineageEngine.attribute_span` scores a span that has no matching wording
by the ratio of shared tokens, and then applied `max(0.5, …)` to that ratio. A passage sharing
one word in twelve scored 0.083 and was published as 0.5. The method's own docstring promises
"the token-overlap ratio when they do not appear at all"; the floor replaced the ratio with a
constant. The client renders that number in the source-chunk viewer as
`Fidelity: 50.0%`, so a passage that supports nothing was shown to the reader as supporting the
claim halfway — a fabricated measurement, in the same family as a fabricated draft.

The floor is gone: the confidence is now the measured overlap, so an unrelated passage reports
`0.077` and the reader sees `Fidelity: 7.7%`. The candidate set itself is unchanged on purpose
— `lineage_for_claim` is contracted to report the passages the retrieval ranked, and the
confidence is what says whether one of them actually supports the claim. What changed is that
the confidence now means what it says.

The Word export was never affected: `_section_quotes` skips a lineage without an `exact_quote`,
so the unrelated candidates never became footnotes. The defect reached the user through the
chunk viewer, not through the manuscript.

| Path | Responsibility |
| :--- | :--- |
| `backend/src/lmeditor/provenance/lineage.py` | The lineage confidence is the measured token overlap, never a floor above it. |
| `backend/tests/test_provenance.py` | A span with no shared wording against an unrelated passage reports no confidence. |
| `backend/tests/test_provenance_service.py` | Every ranked candidate is still reported, and none of them claims a confidence it has not earned. |

### Known limits after this pass

- **The Flutter client was written without a compiler and had to be corrected afterwards.** The
  client changes in this pass were produced before a Flutter toolchain could be located, and
  three compile errors survived into the tree: `DragTarget<Object?>` (whose type parameter is
  bounded by `Object`, so a nullable argument does not conform) and two missing imports in
  `truthful_state_test.dart` (`CitationLineage`, `generationFailureProvider`). They are fixed,
  and the client now analyzes, tests and builds — but the lesson is recorded here because the
  toolchain was **present on the machine** (`C:\src\flutter`, Dart 3.13.1) and simply absent
  from `PATH`. "No toolchain available" is a claim to verify before it is believed.

- **The client's tests could not see a defect that only the wire can show.** Every client test
  drives the client through a fake, so the fakes supplied a well-formed outline and the panel
  looked healthy. The live integration test then found that the backend published the outline
  *container* as though it were a node — one node, with an empty id and an empty title. The
  client's parser refuses both, so the frame became a `PARSE_ERROR` event, which the client
  treats as **terminal**: the run was reported as failed at the outline step, every time, for
  every document. A fake-driven test suite had passed throughout, because the fake did not
  reproduce the backend's mistake. The lesson is that a contract test is not the same as a
  round trip, and only the round trip finds a payload that both halves disagree about.

- **No real language model was available.** The `stub` provider makes the whole application
  runnable and verifiable offline, and it identifies itself as `stub` in `/api/v1/health`
  (`is_stub: true`), in the scorecard and in the run log. A hosted or local provider must be
  configured through `LMEDITOR_LM_*` for real prose.
- **The real-Word conformance check needs Word installed.** It runs in the suite and is skipped
  where Microsoft Word is absent, so a green run on such a machine is evidence that the
  whitebox suite passes, not that the package is conformant.

- **Three stale upload directories remain under `%TEMP%\lmeditor_uploads`** (`probe_src_1`,
  `probe_src_2`, `probe_src_3`, 65 bytes each). They are residue from an earlier probing turn,
  made before the bounded upload store cleaned up after itself; the application now removes each
  upload with its directory once the source has been ingested. The runtime's recoverable-delete
  launcher refuses any target outside the workspace, and the guardrail was not bypassed, so they
  are left in place. They are inert: 195 bytes of scratch data in the system temp directory.

- **Three defects were found by reading payloads, and none of them by a test.** The empty outline,
  the chunk digests read as vocabulary, and the fixed 0.5 fidelity all passed every contract test
  that existed, because the contracts said nothing about the *content* of those fields. The
  technique that found them is worth keeping: stand up the real thing, run it, and read what
  actually crosses the wire. Each of these three was a field that looked like a measurement and
  was not one — an empty node, an identifier standing in for a word, and a constant standing in
  for a ratio. The invariant that catches all three is the same: **a field that presents itself
  as a measurement must be the measurement.**



---

## 10. Remediation pass — 2026-10-23 (targeted editing, fact checking, and book-scale working sets)

### The defect this pass removes

The application could **write** a document and could **verify** one, but it could not
**work on** one. Three of the five things the product is for were missing outright:

| Objective | State before this pass |
| --- | --- |
| Small inline edits | **Absent.** The editor's only write path was a free-text `TextField` whose contents were carried along to the human-in-the-loop approval payload. Nothing could rewrite a passage. |
| Paragraph fact checking | **Absent as a surface.** Verification existed only as a node inside the generation graph, scoring the sections the graph had just written. A user with a paragraph in front of them had no way to ask "is this true?" against the document's own sources or against the web. |
| Editing a book bit by bit | **Absent.** The whole corpus was held in the graph state and passed to the model. There was no on-disk book, no section index, and no bounded working set, so "edit this 500-page book" was not a thing the application could do. |

Each of those was a *product* gap, not a bug, and each was closed by a new domain module
rather than by widening an existing one.

### Modules added by this pass

- `lmeditor/editing/` — `editor.py`, `rewrite.py`. The targeted-edit domain.
- `lmeditor/factcheck/` — `service.py`, `web.py`. The fact-check domain and the web evidence source.
- `lmeditor/library/` — `store.py`, `working_set.py`, `brief.py`, `importer.py`. The book library.
- `lmeditor/server/library.py` — the process-wide book store and the per-book retrieval index cache.

The three domains were built against a shared contract written first, so they could be
implemented in parallel without any of them inventing a shape another would have to match.

### Public contract boundaries established

The shared contracts live in `core/` and are owned by the orchestrator, not by the domains:

- `core/types.py`: `EditScope`, `EvidenceSource`, `FactCheckVerdict`, `WorkingSetRole`.
- `core/schemas.py`: `InlineEditRequest`, `InlineEditResult`, `Evidence`, `ClaimVerdict`,
  `FactCheckRequest`, `FactCheckReport`, `BookSection`, `BookManifest`, `WorkingSetSection`,
  `WorkingSet`, `BookSectionEditRequest`.
- `core/exceptions.py`: `EditingError`, `InlineEditRejectedError`, `EditTargetNotFoundError`,
  `FactCheckError`, `WebEvidenceError`, `LibraryError`, `BookNotFoundError`, `SectionNotFoundError`.

The domain-facing APIs the HTTP layer is written against:

```python
# lmeditor.editing
class InlineEditor:
    def __init__(self, *, rewrite: RewriteFn | None = None, lm: Any | None = None,
                 index: ProvenanceIndex | None = None, chunks: Sequence[SourceChunk] = (),
                 checker: CitationChecker | None = None, max_passages: int = 6) -> None: ...
    async def edit(self, request: InlineEditRequest, section: SectionDraft) -> InlineEditResult: ...
async def apply_inline_edit(request, section, *, rewrite=None, lm=None, index=None,
                             chunks=(), checker=None) -> InlineEditResult: ...

# lmeditor.factcheck
class FactChecker:
    def __init__(self, *, index=None, chunks=(), web: WebSearchProvider | None = None,
                 checker=None, evaluator=None, max_evidence: int = 5) -> None: ...
    async def check(self, request: FactCheckRequest) -> FactCheckReport: ...

# lmeditor.library
class BookStore:
    def __init__(self, root: Path | str) -> None: ...
    def create(self, book_id: str, title: str = "") -> BookManifest: ...
    def open(self, book_id: str) -> BookManifest: ...
    def add_section(self, book_id, section_id, content, *, title="", order=None) -> BookSection: ...
    def read_section(self, book_id: str, section_id: str) -> str: ...
    def write_section(self, book_id: str, section_id: str, content: str) -> BookSection: ...
    def put_chunks(self, book_id: str, chunks: Sequence[SourceChunk]) -> None: ...
    def chunks(self, book_id: str) -> list[SourceChunk]: ...
    def brief(self, book_id: str) -> str: ...
    def update_brief(self, book_id: str, brief: str) -> None: ...

class WorkingSetBuilder:
    def __init__(self, store: BookStore, *, index: ProvenanceIndex | None = None) -> None: ...
    def build(self, book_id: str, section_id: str, *, neighbours=1, max_passages=8,
              max_brief_chars=2000, max_section_chars=4000) -> WorkingSet: ...
```

### Dependency direction

```
core  ←  llm  ←  provenance / verification / ingestion / word
                     ↑
          editing   factcheck   library
                     ↑
                   server
```

The three new domains sit **above** the existing ones and **below** the server. They never
import `lmeditor.server`, and nothing below them imports them. `library` may use `word`
(it splits a `.docx` into sections at its headings); `factcheck` may use `ingestion`
(it reuses the SSRF-safe fetch path and the private-IP policy for web evidence).

### The three invariants this pass establishes

**1. A refused edit leaves the document unchanged.** The revision is grounded in the
document's own retrieval index, and the editor refuses it when it cites a source that does
not exist or when the revised claims cannot be supported. A refusal is a *result*
(`applied=False` plus a reason), not an exception, because "your edit was refused" is an
answer the operator has to see. The result carries the **unchanged** section, so a caller
that writes back only on `applied=True` can never persist a rejected revision.

**2. No evidence, no verdict.** A passage whose claims have no supporting evidence is
reported as `unsupported` — never as `supported`, never as a hedge that reads like a pass.
This holds at every layer: the domain verdict, the HTTP payload, and the client's rendering.
The route-level test asserts it directly: a passage checked against a document the server
does not hold comes back `200` with `verdict="unsupported"`, `status="ungrounded"` and an
empty evidence list, because a pasted passage can still be checked against the web — but it
is told plainly that nothing was found.

**3. The working set is bounded, and the bound is measured.** A book is stored on disk as
one file per section plus a manifest that holds the section index and never the body. The
working set for editing one section reads **at most `2 * neighbours + 1` section bodies**;
every other section of the book stays untouched on disk. Measured on a 200-section book
with `neighbours=1`: **3 body reads**, `context_chars=3762` against `book_chars=118490` —
**3.2 %** of the book. `open`, `list_books` and `section_count` read no body at all. The
"important info" the objective asks to keep cached is kept in two places: the retrieval
index over the book's chunks, and the running **brief**, a bounded digest of the book's
structure that is trimmed from the front so it keeps the most recent information and stays
small no matter how long the book is.

### The HTTP surface

| Route | Purpose |
| --- | --- |
| `POST /api/v1/documents/{doc_id}/edits` | Rewrite one paragraph or one section, grounded in the document's sources. |
| `POST /api/v1/documents/{doc_id}/factcheck` | Check a passage against the corpus, the web, or both. |
| `POST /api/v1/library/books` | Create a book from explicit sections. |
| `POST /api/v1/library/books/import-docx` | Import a `.docx` as a book, one section per heading. |
| `GET /api/v1/library/books` | List the books the library holds. |
| `GET /api/v1/library/books/{book_id}` | The manifest: metadata and section index, never the body. |
| `GET /api/v1/library/books/{book_id}/sections/{section_id}` | One section's text. |
| `GET /api/v1/library/books/{book_id}/working-set/{section_id}` | The bounded context for editing one section. |
| `POST /api/v1/library/books/{book_id}/sections/{section_id}/edits` | Edit one section of a book in place. |

Each domain error maps to the status code that answers the caller's actual question rather
than a generic 500: `EditTargetNotFoundError` → 404, `InlineEditRejectedError` → 422,
`LMNotConfiguredError` → 503 ("configure the model", not "the server is broken"),
`FactCheckError` → 400, `WebEvidenceError` → 502, `BookNotFoundError` and
`SectionNotFoundError` → 404, `LibraryError` → 400.

### The client

The Flutter client gained the two surfaces that a person actually touches:

- **`core/models/editing.dart`** — `InlineEditOutcome`, `FactCheckOutcome`, `ClaimVerdict`,
  `EvidenceItem`. A refused edit and an unsupported claim are *models with state*, not
  exceptions: the backend's honest "no" has to survive all the way to the screen.
- **`core/models/library.dart`** — `BookManifest`, `BookSectionSummary`, `BookWorkingSet`,
  `WorkingSetSection`, `BookSectionContent`.
- **`features/editor/editor_view.dart`** — a rewrite row on every section: a scope toggle
  (paragraph / whole section), an instruction box, and a **Rewrite** button. The outcome is
  rendered verbatim, so a refusal is shown as a refusal with the backend's reason.
- **`features/verification/verification_panel.dart`** — a fact-check card seeded with the
  selected claim's own text, a "Also search the web" toggle, and a verdict panel that keeps
  *unsupported* and *contradicted* apart.
- **`features/library/book_panel.dart`** — the **Books** tab: the library's books, the
  section index of the open book, and — for the selected section — its working set with the
  number that matters: *"3.2 % of the book"*. The panel shows the size of the context the
  model was given against the size of the book, because the bounded working set is the
  feature, and a feature whose bound is invisible is a feature nobody can trust.
- **`state/app_providers.dart`** — the client now **names its own document** (`doc_id` is
  generated before the run starts and sent with the generate request). This is what makes
  the document addressable for editing and fact-checking while the run is still in flight;
  a run whose id the client only learns afterwards cannot be edited.

The client names the document because the backend addresses every document by id, and the
two new surfaces are called against one.

### Verification

- `pytest tests/test_editing.py` — 18 tests. The targeted-edit domain.
- `pytest tests/test_factcheck.py` — 19 tests. The fact-check domain and the web evidence
  source. No test touches the network: the provider is a fake, or the real provider with
  `fetch_limited`/`assert_public_url` monkeypatched.
- `pytest tests/test_library.py` — 20 tests. The book library, including the bounded-context
  property measured against a 200-section book.
- `pytest tests/test_targeting_routes.py` — 7 tests. The HTTP surface, including the honest
  `unsupported` verdict for a passage with no evidence.
- `pytest -q` — **946 passed**, 2 warnings, 0 failed. The suite was **882 passed** before
  this pass; the 64 new tests are the four suites above plus the route surface, and nothing
  regressed.
- `mypy --strict src/lmeditor/editing src/lmeditor/factcheck src/lmeditor/library
  src/lmeditor/server/library.py` — **Success: no issues found in 12 source files**.
- `flutter analyze` — no issues. `flutter test` — 21 new client tests for the editing,
  fact-check and library surfaces.

### Known limits after this pass

- **The book library is server-side and local.** It lives under `%TEMP%\lmeditor_books`
  (overridable with `LMEDITOR_LIBRARY_DIR`), so books do not sync between machines and a
  temp-directory cleanup loses them. A book is a working document, not an archive.
- **A book is imported, not authored.** A `.docx` becomes a book through
  `POST /api/v1/library/books/import-docx`; the library does not yet write a book back out
  as a `.docx` the way the document path does.
- **A book section is edited as text.** The sections carry no rich formatting, so the
  round trip through the library is plainer than the Word reader/writer path.
- **Web evidence depends on one public search endpoint.** `DuckDuckGoSearchProvider` parses
  an HTML result page, which is a moving target; the provider is a protocol, so a keyed
  provider can replace it without touching the domain. A markup change degrades to zero
  results rather than an error, which is deliberate but silent.
- **The inline edit is session-scoped.** The revision is written back into the workflow
  session's section list, so it survives the session but is not written to the source
  `.docx` until the document is exported.
- **The word processor's remaining fidelity gaps are untouched by this pass** — images,
  equations, endnotes, comments, tracked changes and content controls are still not
  round-tripped, and the client editor is still a plain text field rather than a rich-text
  surface. Those are the next pass, not this one.
---

## 11. Remediation pass — 2026-10-23 (word-processor fidelity, an editing surface, and the guided stage)

### The defects this pass removes

Section 10 closed the gaps that made the application unable to *work on* a document. This pass
closes the ones that made it unable to *be a word processor*, plus the one that made the
"guided prompting" requirement hollow.

| Defect | Consequence |
| --- | --- |
| The manuscript bridge emitted every section paragraph as a plain `PARAGRAPH` | A document written with headings, lists, quotes and character formatting left the application **flat**. The export route worked; the export was not faithful. |
| The exported footnote carried the quoted passage but **not the chunk id** | A reader of the `.docx` could see what was quoted but not *which stored passage* it was. The product's central promise — every claim traceable to its source — did not survive the export. |
| The editor had no formatting controls and no find-and-replace | The writing surface was a textarea with a raw-markdown toggle. |
| Equations (`m:oMath`) and symbols (`w:sym`) were dropped into the omission register | For a scientific manuscript the formula's text was not even in the document. |
| `discovery/dialogue.py` — the guided inquiry engine — was an **orphan** | The "guided prompting" requirement was met by machinery nothing called: the pipeline went straight from personas to an outline over the raw corpus, never questioning it. |

### What changed

**The manuscript bridge reads the editor's syntax** (`word/manuscript.py`). `_split_blocks`
replaces `_split_paragraphs`: `#`…`######` → `HEADING` at that level, `-`/`*`/`+` → a bullet
`LIST_ITEM`, `1.` → a numbered `LIST_ITEM` (consecutive items share one `list_group`),
`>` → `QUOTE`, a fenced block → `CODE` with its content preserved verbatim. `_parse_inline`
turns a paragraph into runs carrying `bold`, `italic`, `underline`, `strike`, `font` and
`highlight`.

**The citation markers stay opaque.** The inline parser is handed the citation spans as
opaque ranges it steps over, so a marker is never split, never emphasised, and
`**with [#abc123] inside**` still yields the superscript and the footnote with the bold span
intact around it. An unmatched marker stays literal text: the user's words never disappear.

**The footnote names its chunk.** `_footnote_text(chunk_id, quote)` is `"<quote> [<chunk id>]"`,
so the exported file is self-describing — a claim can be walked back to the exact passage from
the document alone. One footnote per chunk, still in first-appearance order; a chunk with no
quote still gets no footnote.

**The editor has a toolbar and a find-and-replace** (`client/lib/features/editor/`). The pure
helpers live in `client/lib/core/models/editor_formatting.dart` (no Flutter import, so they
are unit-testable): inline formats, block formats, match finding and replacement. The toolbar
acts on the field's selection, writes back to the controller and calls the existing `onChanged`,
so a formatting command reaches the approval payload exactly like a typed edit. Find-and-replace
runs over the document's sections and reports a verbatim count.

**A formatting command refuses to cross a citation marker.** A selection whose boundary falls
*strictly inside* a `[#chunk]` or `{{cite:…}}` marker is refused with a message; a selection
covering a whole marker is allowed and leaves it intact. Guessing the marker's true extent
could corrupt a citation, and a refused keystroke is recoverable while a lost citation is not.

**Equations and symbols survive the round trip** (`word/model.py`, `word/reader.py`,
`word/writer.py`). `WordRun` gains one optional field, `equation_xml`, holding the raw OMML
verbatim; the run's `text` holds the equation's flattened text, so the formula is searchable
and quotable as well as renderable. The writer re-inserts the OMML with `parse_xml` and falls
back to the flattened text if the markup will not parse. The symbol table (`_SYMBOL_CHARS`,
**124 entries** across `Symbol` and `Wingdings`) was derived from the installed fonts' own
glyph outlines and the published Adobe Symbol encoding — so `F0B7` is `•` and `F0A7` is `♣`,
which is not what a guess would have produced. An unmapped code is reported with its font and
code rather than turned into a wrong character.

**The guided stage is in the pipeline** (`workflow/nodes.py`, `workflow/graph.py`,
`workflow/state.py`). `guided_research_node` sits between `discover_perspectives` and
`synthesize_outline` and runs the dialogue loop over the discovered perspectives, grounded in
the document's own retrieval index. `ProvenanceIndex` gained `query()` and `get_chunk()` so it
satisfies `ProvenanceIndexerProtocol` — the same index the document was drafted from, so the
guided findings and the outline cannot disagree about which passage answers a question.

### The three invariants this pass establishes

**1. The guided stage never kills the run.** No perspectives, no chunks, the stage disabled, no
language model, an index that will not build, a `DialogueError` — each produces a *note*
saying what happened and lets the pipeline continue. The stage that did not run says so rather
than reporting nothing, because an empty panel is indistinguishable from a silent failure. The
findings are published as `EventType.RESEARCH_NOTES` **whether or not** the stage found
anything.

**2. A finding is attributed only to a section that holds its passage.** `_guided_findings_by_chunk`
attaches a guided finding to the section whose passage it quotes; a finding that names no
passage is attributed to no section. With no findings, the outline is exactly what the corpus
produced — pinned by a test, because a change that made the outline *worse* would still look
like a change.

**3. A citation marker is never split.** By the inline formatter, by the find-and-replace, by
the citation machinery itself. The marker is opaque to every one of them.

### The wire vocabulary

Two event names were added:

- `EventType.RESEARCH_NOTES = "research_notes"` — the guided findings, with `notes` and
  `referenced_chunk_ids`.
- `WorkflowStep.GUIDED_RESEARCH = "guided_research"` — the stage's step.

A contract test enforced that the backend's vocabulary and the client's agreed in **both**
directions — `tests/test_client_contract.py`, which read the Dart source. That test was
replaced in §19 by `tests/test_wire_contract.py`, which enforces the same agreement between
the two tiers that remain, and which fails rather than skips when the Kotlin source is absent.

The client parsed `research_notes` into a `ResearchNotesEvent` and showed the findings in a
**GUIDED RESEARCH** panel in the discovery tab.

### Verification

- `pytest -q` — **998 passed**, 2 warnings, 0 failed. The suite was **882 passed** before
  section 10 and **946 passed** after it; the 52 new tests since are the guided stage (12),
  the equations and symbols (18), the manuscript bridge (22) and the word-package additions.
- `mypy --strict src/lmeditor/workflow src/lmeditor/provenance/service.py src/lmeditor/word`
  — **Success: no issues found in 13 source files**.
- `flutter analyze` — no issues. `flutter test` — **176 passed, 5 skipped**, 0 failed (the
  baseline was 140 passed, 5 skipped).
- `backend/scripts/verify_client_live.py` — **PASS**: the real Dart client against a real
  backend, over the real SSE wire, with the guided stage running.
- Independently checked by hand, not only by test: a package with an `m:oMath` equation and a
  `w:sym` bullet reads with an **empty omission register**, and the written package still
  contains `m:oMath` and the bullet character; a document with `##`, bullets, a numbered list,
  a quote and `**bold**` produces the matching `WordBlockKind`s and formatted runs; and the
  footnote reads `"<quote> [<chunk id>]"`.

### Known limits after this pass

- **The offline stub cannot frame the guided dialogue.** The stub answers the *drafting*
  signatures, not the dialogue's synthesis signature, so with the shipped stub the guided
  inquiry takes its failure path and says why. A real model (`use_dspy=True`) frames it. The
  stage degrades honestly; it does not pretend.
- **Toggling italic over an already-bold run nests** (`***x***`) rather than converting.
- **The code-block marker is a per-line prefix**, and the backend sibling decides where a
  fence closes.
- **`{{cite:…}}` is still not converted to a citation run.** It survives verbatim, but it is
  not yet a first-class citation; the mapping needs a decision on which id is the citation's
  and which is the chunk's.
- **Embedded objects, charts, the glossary part, `w:altChunk`, `w:subDoc` and `w:rPrChange`
  are still dropped** — see section 8's corrected list. Each is reported in the omission
  register.
- **The editor formats markdown, not Word.** The round trip is markdown → Word formatting,
  which is lossy in the way markdown always is: a table in the editor's content is not a Word
  table.
---

## 12. Remediation pass — 2026-10-23 (the operator can get the file out)

### The defect this pass removes

The word-processing element could *produce* a `.docx`, but nothing in the client could reach it.
The backend route `GET /api/v1/documents/{doc_id}/export.docx` existed and worked; the Flutter
client had **no export call at all** — `export` did not appear once in the client's source. And
a book, however carefully it had been edited, could not be handed back at all: the library
stored sections on disk and there was no route that turned them into a document.

So the loop was open in three places: a manuscript could not be saved, a book could not be
exported, and a `file_picker` dependency sat in `pubspec.yaml` unused — the save dialog the
design had always intended, never wired.

### What changed

**A book can be rendered back out** (`lmeditor/library/export.py`).
`build_book_document(store, book_id)` reads the book's sections **one at a time**, in book
order, and assembles them through the *same manuscript bridge* a document uses — so a book
carries real headings, real lists, real character formatting and real footnotes. It is not a
text dump. Reading only the sections means exporting a five-hundred-section book does not load
the book.

**A route that hands the book over** — `GET /api/v1/library/books/{book_id}/export.docx`,
answering with the finished package and a `Content-Disposition` filename. A book that can be
edited but never exported is work trapped in a directory.

**The client can export, and asks the operator where to put the file**
(`client/lib/core/export_service.dart`). `ExportService.saveDocx` calls
`FilePicker.platform.saveFile` and writes the bytes to the path the operator chose. Guessing a
folder is how an application ends up scattering copies of a manuscript through a disk the user
never agreed to, so the save dialog is the only mechanism used.

**A cancelled dialog is not an error.** The operator chose not to save; the result says
`cancelled`, and the caller is told that rather than being handed a failure it did not cause.

**A failed export never produces a zero-byte file.** The client's download path raises
`LMeditorApiException` on a non-2xx status, exactly as the JSON paths do, so a failed export
says *why* instead of handing back an empty `.docx` that looks like a successful one.

**Two buttons.** The editor's banner carries **Export .docx** for the current manuscript; the
Books panel carries it for the open book. The editor does not know where files go — the
implementation owns the dialog.

### The invariant

**The file that leaves the application carries the provenance with it.** The exported footnote
reads `"<quoted passage> [<chunk id>]"`, so a reader of the `.docx` can walk a claim back to
the exact stored passage it came from. This is the product's central promise, and it now
survives the export rather than stopping at the application boundary.

### Verification

- `pytest -q` — **1000 passed**, 0 failed.
- `mypy --strict` over every new and changed backend module — no issues.
- `flutter analyze` — no issues. `flutter test` — **180 passed, 5 skipped**, 0 failed.
- Checked by hand, not only by test: a live run generated a document and exported it — the
  package carries `word/document.xml`, `word/footnotes.xml`, `word/numbering.xml`,
  `word/styles.xml` and the rest, and **the chunk id is in the footnote**; a live book was
  created and exported, and the package contains the book's title, both chapter headings, the
  `##` sub-heading, the bold run and the bullet.

### Known limits after this pass

- **The export is a snapshot.** It renders the manuscript as it is at the moment of the
  export; it does not write the `.docx` back over the source file the operator opened.
- **The book export carries no footnotes unless the book's sections cite chunks.** A book
  imported from a `.docx` has its text but not the claim-to-chunk lineages, so its citations
  render as plain text rather than as footnotes with chunk ids.
- **The save dialog is a desktop feature.** On a platform where `file_picker` cannot present
  one, the export fails with a reason rather than writing somewhere unasked.

---

## 13. Wave 3 — the package is whole, the outline is steerable, and the document can be saved

Three defects were open at the end of section 12, and a fourth was found while this pass was
being built. All four are closed here.

### 13.1 The Word package is no longer lossy

The reader used to *report* the parts it could not model — charts, embedded objects, the
glossary document, `w:altChunk`, `w:subDoc` — and then drop them. A document that went in came
out with its charts stripped and its embedded objects gone, which is the one thing a word
processor must never do.

`WordPart` (`part_name`, `content_type`, `data`, `relationship_id`, `relationship_type`) now
carries such a part's **bytes**, and `WordDocument.parts` holds them. `WordRun.part_ref` anchors
a part to the body element that referenced it, so the writer re-emits the right construct:

| Construct | Carried | Anchored as |
| --- | --- |
| chart / diagram part | bytes + content type + relationship | `run.part_ref` → `w:drawing` (`c:chart` / `dgm:relIds`) |
| `w:object` embedding | bytes + relationship | `run.part_ref` → `w:object` (`v:shape` + `o:OLEObject`) |
| `w:altChunk`, `w:subDoc` | bytes + relationship | `run.part_ref` → the **block-level** element |
| glossary part, any other part | bytes + content type + relationship | package-level relationship |
| `w:rPrChange` | the previous `w:rPr`, verbatim | `run.format_change_xml` + a `FORMAT_CHANGE` revision |

The omission register is now **silent for a carried part**: it still reports a missing part, a
missing relationship, an external target and an unwritable part, because those are real losses.

`w:rPrChange` is no longer dropped either. The run's single revision slot belongs to text
revisions, so a tracked *formatting* revision needed a carrier of its own; it has one now, and a
run that carries it is no longer wrapped in `w:ins`/`w:del`, because a formatting change is not
a text change.

Four things are still not carried, and each says so: an external target (there are no bytes in
the package to carry), a relationship to a part the package does not hold, an embedded object's
preview image (reachable only through the media model), and a part whose name the output package
already holds — writing it would put the same name in the zip twice, which Word calls corrupt.

### 13.2 Markdown tables are Word tables

A table typed in the editor used to reach the `.docx` as loose prose, even though the writer
already knew how to emit a table. The manuscript bridge now recognises a GitHub-flavoured pipe
table — a line carrying an unescaped `|` immediately followed by a delimiter row whose every cell
matches `^:?-{1,}:?$` — and renders it as a real `w:tbl`:

- cells split on unescaped pipes; outer pipes are optional; `\|` stays inside its cell;
- the grid is the **widest** row; shorter rows are padded, a wider row widens the table;
- the header row carries `is_header` and `header_rows == 1`, so Word repeats it across pages;
- cells go through the same inline machinery as paragraphs, so `**bold**` and the citation
  markers work inside a cell;
- **a line with pipes but no delimiter row stays prose.** Content is never lost to a guess.

### 13.3 The client renders what the editor writes

The editor's toolbar wrote `*italic*`, `__underline__`, `~~strike~~`, `` `code` `` and
`==highlight==`, but the client renderer understood only `**bold**` — so five of the six formats
the operator could apply rendered as literal punctuation. The renderer now understands the whole
shared syntax, in both directions with the backend bridge:

- block `#`…`######` heading, `-`/`*`/`+` bullet, `1.` numbered, `>` quote, fenced code;
- inline `**bold**`, `*italic*`/`_italic_`, `__underline__`, `~~strike~~`, `` `code` ``,
  `==highlight==`;
- a marker opens only on a run of exactly its own length (`***x***` stays literal), needs a
  non-whitespace neighbour, and `_` may not open inside a word (`snake_case` survives);
- an unmatched marker stays literal;
- a citation marker (`[#chunk]`, `[#a, #b]`, `{{cite:citId:chunkId}}`) is opaque and never split.

The editor now shows a **live preview** beside the source while it is in edit mode, so what the
operator writes is what they see.

### 13.4 The operator writes the outline

The pipeline used to decide the document's structure on its own. The caller can now supply the
outline:

- `OutlineNodeInput` (`title`, `description`, `children`) on `GenerateDocumentRequest.outline`;
- `OutlineGenerator.build_outline_from_sections()` uses the caller's sections as the skeleton and
  assigns the passages to every leaf **by the outliner's own rules**;
- the client models it as `SteerableSection` / `SteerableOutline` / `OutlineSteer`, with a
  dedicated type rather than the published `OutlineNode`. This is deliberate: the server's
  outline model forbids extra fields, so a section id or a chunk assignment cannot be posted by
  accident. **The operator says what the document covers; the corpus decides what may be said
  about it.**
- the client's **Your outline** view renames, describes, inserts, reorders and removes sections;
  a steer the operator has edited is never overwritten by a blueprint the server publishes
  afterwards.

The steer travels with the generate request. It is omitted entirely when the operator has named
no sections: an empty outline would ask the server to write a document with no sections, which is
not what "let the server choose" means.

**Where it lives in the UI.** The left pane is 310 pixels wide. Measured in a widget test, the
five labelled tabs it would have needed — STORM, Outline, Your Outline, Sources, Books — measure
about 630 pixels between them, so three of them sat outside the viewport, behind a scroll the
operator has no reason to know about. Two of those three were pre-existing (`Sources` and
`Books`). The steer therefore **shares the outline tab** with the server's plan, behind a compact
two-way switch, rather than taking a tab of its own. The outline tab now measures within the
pane, and the operator's list of sections is where they already look for the outline.

### 13.5 The document can be saved

**This was a real defect, and the most important thing in this pass.** The operator could type
into a section in the editor, but nothing stored it. The typed text lived in the editor's
`TextEditingController` and nowhere else; the exported `.docx` is rendered by the backend from
the server's own section list, so **the operator's typing was silently discarded on export**. A
word processor that drops what you wrote is not a word processor.

There was a second, quieter version of the same defect: the review gate accepted the operator's
edited sections on the approval payload and then ignored them, so approving a document threw away
the paragraphs the operator had just fixed.

Both are closed:

- `PUT /api/v1/documents/{doc_id}/sections/{section_id}` stores the operator's text for one
  section. The section's **provenance is kept** — the citations the pipeline established still
  say where their passages came from, because the operator reworded the text and not the record
  of where it came from. The section's **claims are cleared**, because a claim is a proposition
  extracted from the text, and the text the operator wrote is not the text the claims were
  extracted from; the measurement re-extracts them from the operator's words, so the verdict
  describes what is now on the page rather than what was on it before.
- The saved text is **measured again** before the response is sent, so the status and the
  scorecard describe the text as it now stands rather than the draft that preceded it. The
  measurement uses **the document's own threshold**, which the session remembers, so the bar the
  document was written to is the bar the edit is judged by. A measurement that cannot be made
  leaves the text stored and the section unmeasured: losing the operator's work because a
  verifier was unavailable would be the worse failure, and a verdict nobody measured would be
  the dishonest one.
- The review gate now **applies** the operator's edited sections instead of ignoring them
  (`core.sections.merge_operator_text`), and measures them the same way the pipeline measures a
  drafted section. An edit to a section the document never had cannot inject content.
- The client's **Save** button stores every edited section, and the **export saves first**: a
  file handed back without the operator's edits would be a lie about the state of the document.
  A failed save refuses the export and says why.

The rule itself lives in one place, `core.sections.merge_operator_text`, so the gate and the save
route cannot drift apart.

### 13.6 Verification

- `pytest -q` — **1060 passed**, 2 warnings, 0 failed. The suite was 1000 passed before this pass.
- `mypy --strict` — clean on the backend packages this pass touched, including
  `src/lmeditor/server/routes.py`, which the earlier strict runs had never covered. That run
  found a real bug: the book section edit route passed a `BookSectionEditRequest` where an
  `InlineEditRequest` was expected, and the editor reads `request.doc_id` — the book request has
  `book_id`, so the route would have raised `AttributeError` at run time. It now builds the
  document request explicitly.
- `flutter analyze` — no issues. `flutter test` — **269 passed, 7 skipped**, 0 failed (the
  baseline was 180 passed, 5 skipped; waves 4 and 5 added the rest).
- `backend/scripts/verify_client_live.py` — **PASS**: the real Dart client against a real
  backend, over the real SSE wire. It proves the operator's outline reaches the server and is
  honoured, and that the operator's saved text is what the server holds.

### Known limits after this pass

- **The editor edits markdown, with a live preview beside it.** That is a deliberate production
  design — the source of truth stays a text format that can be diffed, cited and re-rendered —
  but it is not a WYSIWYG canvas: the operator does not see character formatting applied in
  place while typing.
- **A saved section keeps the draft's claims until it is re-measured.** The measurement is done
  on save, but a section whose text was changed *outside* the save path (a hand-edited file in
  the library) carries the claims of the text that was there before.
- **Legacy `.doc` is still not read.** It is a different file format with no package to carry;
  the reader refuses it with a reason rather than guessing.
- **Toggling italic over an already-bold run nests** (`***x***`) rather than converting.
- **`{{cite:…}}` is still not converted to a citation run.** It survives verbatim.
- **The export is a snapshot.** It renders the manuscript as it is at the moment of the export;
  it does not write the `.docx` back over the source file the operator opened.
- **The save dialog is a desktop feature.** On a platform where `file_picker` cannot present
  one, the export fails with a reason rather than writing somewhere unasked.

---

## 15. Wave 6 — the editing surface shows the formatting while it is applied

The editor's field was a plain `TextField` showing the raw markdown in monospace. The operator
typed `**bold**` and saw `**bold**` — asterisks and all — in a flat colour. The *preview* beside
it rendered the formatting, but the surface being typed into showed nothing but source. A word
processor shows the formatting as it is applied.

**The field now renders the formatting in place, and the text stays exactly the markdown it
was.** The markers remain in the text and remain editable; they are drawn dimmed, so the
sentence reads as prose while the source stays the source.

### 15.1 One parser, two readings

`renderSpans` (the preview) consumes the markers: `**bold**` comes back as one run of `bold`.
That is right for a preview and wrong for an editable field, which must draw every character
the operator typed.

`renderSourceSpans` returns the spans **over the original text**: the texts of the spans
concatenate back to the input, character for character, and a marker run carries an `isMarker`
flag so the widget layer can dim it.

It is the *same* parser, with an `emitMarkers` flag. That is the point: a second implementation
of the marker rules would be a second opinion about what `**` means, and the two would drift.
A test asserts that the two readings agree on what is formatted.

### 15.2 The styled field

`RichMarkdownController` is a `TextEditingController` whose `buildTextSpan` returns the styled
tree:

| Run | Drawn as |
| --- | --- |
| a marker | dimmed (`StudioTheme.textMuted`) — punctuation, not words |
| bold / italic / underline / strike | the implied style on the field's base style |
| `==highlight==` | an amber background |
| `` `code` `` | a neutral tint (the field is monospace throughout, so code is told apart by tint, not by face) |
| a citation marker | one opaque span in `accentCyan` on a cyan tint, so the cited parts of the sentence are visible at a glance |

The tree covers the whole text exactly, so the caret, the selection and the find-and-replace are
unaffected. The controller never rewrites the text — it only draws it — so the toolbar, the
preview, the save and the export all still see the markdown the operator typed. The IME
composing region is re-underlined, because an override of `buildTextSpan` has to repeat that.

### 15.3 Verification

The final state of the repository:

- `pytest -q` — **1080 passed**, 2 warnings, 0 failed.
- `mypy --strict src/lmeditor/core src/lmeditor/server src/lmeditor/workflow src/lmeditor/library
  src/lmeditor/word src/lmeditor/editing src/lmeditor/factcheck src/lmeditor/provenance/service.py`
  — **Success: no issues found in 38 source files**.
- `flutter analyze` — no issues. `flutter test` — **292 passed, 8 skipped**, 0 failed (the
  baseline was 180 passed, 5 skipped). The 15 new tests cover the source spans, the marker
  rules, the styled tree, and the rendered field.
- `backend/scripts/verify_client_live.py` — **PASS**: the real Dart client against a real
  backend, over the real SSE wire, covering the run, the operator's outline, the operator's
  saved text, a document with a retrieval index, the review gate's refusal, and a dropped
  connection.

### Known limits after this pass

- **The field is a styled source, not a document canvas.** The operator sees the formatting
  applied, but the editing surface is still a single text field per section: there is no
  page-layout view, no column, and no direct manipulation of a table. The markdown stays the
  source of truth, which is what makes the export, the diff and the citations exact.
- Everything listed under sections 13 and 14 still stands.

---

## 14. Wave 4 and 5 — the library keeps the document, and the operator's hand reaches it

### 14.1 The library keeps what is not text

Importing a `.docx` into the library stored each section as **text only** (`block.render_text()`).
A writer who opened their own document, edited one paragraph, and exported it got a text-only
document back: the images, the tables, the footnotes, the tracked changes and the charts were
gone. The word model could already carry them; the library was throwing them away.

The store now keeps two things beyond the editable text:

- **Per section** (`sections/<id>.json`): the section's original `WordBlock`s and the text those
  blocks rendered to. One file per section, so one read yields both and the bounded-read property
  survives.
- **Per book** (`origin.json`): the document-level records — `properties`, `footnotes`,
  `endnotes`, `comments`, `headers`, `footers`, `sections`, `styles`, `media`, `parts`. Media and
  part bytes travel base64, because JSON cannot hold bytes; a PNG round-trips byte-identical.

**The edited/unedited rule is one comparison:** a section is unedited iff its current text still
equals the text its original blocks rendered to. An unedited section is exported **verbatim**,
which is what keeps its table, its image and its tracked change alive. An edited section is
re-rendered from its new text through the manuscript bridge, so markdown in the editor becomes
real Word structure again.

**Note-id collisions are resolved deterministically.** The bridge numbers its footnotes `f1`,
`f2`, …; an imported document's notes carry whatever ids the file said. A rebuilt section's new
note keeps the bridge's id when it is free and takes the first free `f1-2`, `f1-3`, … otherwise,
and every run that referenced it — including runs inside table cells — is remapped. An id
collision that is not resolved is a corrupt package, so it is handled rather than assumed away.

**Import stays idempotent.** Re-importing replaces the origins of the sections it names and
merges the book origin: notes, media, parts and styles the new document lacks are kept, so the
sections it did not name keep working.

A book with no origin exports through the unchanged bridge path. Nothing about a book created
from a list of sections changed.

### 14.2 The operator can type into a book section

The book panel offered only an *instruction* box: the operator asked the machine to rewrite, and
the machine did. **There was no way to type into a book section by hand**, which is the first
thing requirement 5 asks for — editing a book bit by bit.

- `PUT /api/v1/library/books/{book_id}/sections/{section_id}` stores the operator's text.
- The client's read-only box became the **editable field itself**, so there is one copy of the
  text rather than two that can disagree. The machine-rewrite row is untouched: the operator may
  use either.
- A **failed save keeps the typed text**. The controller is seeded only when a section is opened,
  so a refused save cannot destroy a paragraph the operator wrote.
- The section is re-chunked, the manifest is told which passages belong to it now, and the book's
  index is dropped so the next retrieval is built from the text that is actually there.

### 14.3 The retrieval index stopped serving deleted text

`put_chunks` **adds** to a book's corpus: a chunk id is a hash of the chunk's text, so two
sections carrying the same text share one passage, and indexing one section must not drop
another's. That is right for indexing and wrong for *editing*: when a section's text changed, the
passages built from the old text stayed in the corpus, the rebuilt index included them, and the
index went on retrieving a paragraph the editor had deleted. An index that serves a deleted
paragraph is not a cache, it is a lie.

The index is now built **through the manifest**, which says which passages belong to which
section (`server.library.book_passages`), and the edit routes record the section's new passages.
The corpus file still keeps the inert passages — that is the price of the shared-passage rule —
but nothing points at them, so they cannot be retrieved, and they are never handed to a rewrite
as the book's material.

### 14.4 A property was called as a method

`ProvenanceIndex.chunk_ids` is a **property**; the server called it as `index.chunk_ids()`. Every
document that had a retrieval index therefore raised `TypeError: 'list' object is not callable`
inside `_document_chunks`, which took the **targeted-edit** and the **fact-check** routes down
with it — the two surfaces that ground their work in that index. It is fixed, and the live
integration test now exercises both against a document that has one.

This is what the strict type-check run over `server/routes.py` — a file the earlier runs had
never covered — was for.

### 14.5 Verification

- `pytest -q` — **1080 passed**, 2 warnings, 0 failed.
- `mypy --strict src/lmeditor/core src/lmeditor/server src/lmeditor/workflow src/lmeditor/library
  src/lmeditor/word src/lmeditor/editing src/lmeditor/factcheck src/lmeditor/provenance/service.py`
  — **Success: no issues found in 38 source files**. The earlier runs covered 26 files; the
  server and the core were never in the set, and that is where this pass's two type errors were.
- `flutter analyze` — no issues. `flutter test` — **277 passed, 7 skipped**, 0 failed.
- `backend/scripts/verify_client_live.py` — **PASS**, including the two new live proofs: the
  operator's saved text is what the server holds, and a document with a retrieval index can be
  edited and fact-checked.

### Known limits after this pass

- **The library's corpus file grows with every edit.** The passages built from a deleted
  paragraph stay on disk, unreferenced and unreachable. Pruning them would break the
  shared-passage rule (two sections with the same text share one passage), so they are left.
- **A re-import of a different document swaps the page setup and headers** while keeping the
  media the untouched sections need. The merge is a judgement call, and it is documented rather
  than hidden.
- **The origin path does not synthesise citation lineages**, so the notes a rebuilt section
  creates are empty. The collision machinery is implemented and tested regardless.
- **The editor edits markdown, with a live preview beside it** (see section 13).
- **Legacy `.doc` is still not read**, and the reader refuses it with a reason rather than
  guessing.
- **The export is a snapshot**, and **the save dialog is a desktop feature** (see section 13).


---

## 16. Wave 7 and 8 — Microsoft Word is the oracle, and the package it refused

Microsoft Word **is installed on this machine**. That turns "is this a real Word document?" from
an opinion into a question with an answer, and this pass asked it.

### 16.1 The harness

`backend/tests/word_real_word_check.ps1` drives Word over COM and reports what it saw: the
paragraph styles, the list markers, the tables and their cells, the footnotes, the hyperlinks,
the comments, the revisions, the sections, the inline shapes and their sizes.
`backend/tests/word_real_word_check.py` wraps it.

One bug in the harness was itself worth finding: **the answer was being decoded with the
machine's locale encoding**. PowerShell writes UTF-8 to a redirected pipe, and the locale on a
Windows machine is usually cp1252, which turns the bullet marker into three characters and an
em dash into three more. A harness that mis-reads what Word said mis-reads exactly the
characters a document is most likely to be checked on. It now decodes UTF-8.

### 16.2 The application export, checked by Word

`backend/tests/test_word_app_conformance.py` takes the document the **application** exports — the
manuscript bridge's output, with the markdown the editor writes, a table, and a citation whose
footnote names the passage it came from and the chunk it was taken from — and asks Word about it:

| What the check asserts | What Word answered |
| --- | --- |
| Word opens the document | `OPENED=yes`, no repair prompt |
| the markdown table is a table | `TABLE_COUNT=1`, `rows=3`, `cols=2`, the right cells |
| the lists are lists | `marker=U+2022` and `marker=1.` |
| the provenance is a footnote | `FOOTNOTE_COUNT=1`, carrying the passage and the chunk id |
| the section title is a heading | style `Heading 1` |
| the harness does not mangle characters | an em dash, an accent and guillemets survive |

### 16.3 The defect Word found

A **real Word document with a chart** was authored by Word itself
(`backend/tests/word_make_chart.ps1`, committed as `backend/tests/fixtures/word_chart_fixture.docx`)
and put through our reader and our writer. Word was asked about the result:

```
OPENED=no
ERROR=The file appears to be corrupted.
```

The cause was found by elimination, with Word as the oracle: our reader carried the chart's
**relationship part** `word/charts/_rels/chart1.xml.rels` into the model as if it were content,
and our writer then created a relationship pointing at that `.rels` file. A `.rels` file is the
relationship list of a sibling part, never content. Dropping that one relationship made the
package open; nothing else about the package had to change.

A second defect was measured in the same run: Word wrote the chart at `wp:extent cx="5486400"
cy="3200400"` (432 x 252 points) and we wrote `1905000 x 1905000` (a 150 x 150 point default),
because the reader never recorded the size of the drawing that anchors the part.

### 16.4 The model, extended

`backend/src/lmeditor/word/model.py` gained three things, and they are the whole fix:

| Type | What it says |
| --- | --- |
| `WordPartRelationship` | a relationship a carried part holds: its id inside the owning part, its type, its target (a package part name, or a URL when external) |
| `WordPart.relationships` | the relationships the part holds — the chart's colour style, its quick style, the workbook behind it |
| `WordPart.package_relationship` | the relationship **of the package root** that reaches the part — the custom document properties are reached from there and not from the document |
| `WordRun.anchor_extent` | the size the anchored element is drawn at, in EMUs |

### 16.5 The rules the reader and the writer now follow

- **A relationship part is never content.** Any part whose parent directory is `_rels` is
  refused, wherever it lives.
- **A part the document did not reach gets no relationship from the document.** It is reached
  from the relationship list of the part that owns it. The invented relationship type
  (`.../relationships/carriedPart`) is gone: it is not a relationship type in OOXML, and it was
  how the writer used to force a part into the package.
- **A part the package root reaches is related from the package root**, with its original id and
  type, so the document's own relationship list stays exactly the one the body needs.
- **A part nothing reaches cannot travel.** An OPC package holds the parts its relationship
  graph reaches; a part with no path into it is absent from the zip. The writer does not invent
  a path — it names the part in the omission register, so the loss is loud.
- **The size an element is drawn at travels with it**, so a chart comes back at the size it was
  written at.

### 16.6 The library keeps the relationships too

`library/store.py` writes a book's origin record to disk. The relationships of a part travel
with it, so a chart stored in a book and exported again still reaches its own parts. An
`origin.json` written before this change still loads: the field defaults to empty.

### 16.7 Verification

The final state of the repository:

- `pytest -q` — **1141 passed**, 2 warnings, 0 failed. That count includes the **real Microsoft
  Word** checks: the application export, the model-level document, and the round trip of a
  genuine Word chart document.
- `mypy --strict src/lmeditor/core src/lmeditor/server src/lmeditor/workflow src/lmeditor/library
  src/lmeditor/word src/lmeditor/editing src/lmeditor/factcheck src/lmeditor/provenance/service.py`
  — **Success: no issues found in 38 source files**.
- `flutter analyze` — no issues. `flutter test` — **292 passed, 8 skipped**, 0 failed.
- `backend/scripts/verify_client_live.py` — **PASS**: the real Dart client against a real
  backend, over the real SSE wire.

Word's own verdict on the round trip of its own document, before and after:

| | Before | After |
| --- | --- | --- |
| opens | `OPENED=no` — "the file appears to be corrupted" | `OPENED=yes` |
| the chart | not reached | `INLINE_SHAPE1\|type=12\|width=432\|height=252` |
| the chart's own parts | carried as content | byte for byte identical |

A round trip of that document now loses **nothing**: every part of the source is present in the
output, the chart and the three parts it owns are preserved byte for byte, and neither the reader
nor the writer reports an omission.

### Known limits after this pass

- **The library's corpus file grows with every edit.** The passages built from a deleted
  paragraph stay on disk, unreferenced and unreachable. Pruning them would break the
  shared-passage rule (two sections with the same text share one passage), so they are left.
- **A re-import of a different document swaps the page setup and headers** while keeping the
  media the untouched sections need. The merge is a judgement call, and it is documented rather
  than hidden.
- **The origin path does not synthesise citation lineages**, so the notes a rebuilt section
  creates are empty. The collision machinery is implemented and tested regardless.
- **The editor edits markdown, with a live preview beside it** (see section 13).
- **Legacy `.doc` is still not read**, and the reader refuses it with a reason rather than
  guessing.
- **The export is a snapshot**, and **the save dialog is a desktop feature** (see section 13).
- **A drawing's geometry is carried as its extent, not as its markup.** The size survives; the
  crop, the rotation and the alternative-content fallback of a drawing do not. The fallback is
  the only one of those Word itself writes routinely, and it is not a part of the document the
  operator reads.


---

## 17. Wave 9 — the document must still answer after the run has ended

### 17.1 What was broken

The retrieval index a document is drafted against was released the moment the run reached a
terminal state:

```
workflow/graph.py   release_workflow(doc_id)      ->  every terminal state
workflow/nodes.py   release_provenance_index()   ->  _PROVENANCE_INDEXES.pop(doc_id)
```

The index is what the two editing surfaces read. `InlineEditor` grounds a revision in the
passages the instruction asks about; `FactChecker` compares a claim against the corpus the
document was written from. Both are used *after* the run has ended — that is what a finished
document is for. So for every completed document:

- a fact-check found no corpus, reported `verdict=unsupported` with every claim `ungrounded`
  and **zero** evidence items, even when the passage checked was *verbatim one of the
  document's own source chunks*;
- a targeted edit was refused with "the revision cites a source that cannot be verified",
  because the chunks it cited could not be resolved.

The domain layer was never at fault. Given an index, `FactChecker` returns SUPPORTED with the
right chunk top-ranked at relevance 1.0, and `InlineEditor` applies a verified revision. The
fault was a lifetime: the index was given the run's lifetime when the document needs it.

### 17.2 The fix — two resources, two lifetimes

| Resource | Belongs to | Released when |
| --- | --- | --- |
| the compiled graph and its checkpointer | the **run** | the run reaches a terminal state |
| the retrieval index | the **document** | the document is discarded (`SessionManager.remove_session`) |

`release_workflow` now drops only the checkpointer. The index survives the run, and the
embedding model it uses is a process-wide cache, so keeping an index alive costs the vectors
and the chunks — not the ninety-megabyte model.

A **stale corpus** is the hazard that fix opens, and it is closed explicitly: a cached index
is reused only when it was built over exactly the chunks it is asked about
(`_provenance_index` → `_covers`). A document id can be generated again, and an index over the
previous document's sources would answer questions about passages this document never saw.

### 17.3 The path names the thing; the body does not name it again

`InlineEditRequest`, `FactCheckRequest`, `BookSectionEditRequest` and `HITLReviewPayload` each
required a document, book or section id in the body although the id is already a **path**
parameter of the route that takes the body. Two sources of truth for one identifier disagree
the first time a client is built wrong or an id is copied from another request. The path is
the address of the resource, so the path is what counts: those fields are now optional, the
route fills them from the path, and a body that names a different document cannot redirect the
request. The Flutter client no longer sends the duplicate either.

### 17.4 The tests that would have caught it

The route-level suite was green because its only edit test asserted a 404 for a document that
does not exist, and its only fact-check test asserted "unsupported" for a document that does
not exist. Both agreed with a broken product. `backend/tests/test_completed_document_surfaces.py`
now runs a document **to completion through the real workflow** and then exercises the two
surfaces over HTTP:

- the document keeps its retrieval index after the run;
- a claim that is *verbatim one of the document's own source passages* comes back with corpus
  evidence — the right chunk id, the passage as the excerpt — and no claim left `ungrounded`;
- a targeted edit applies, grounded, citing the sources it used;
- and the **A/B**: the same claim, the same document, the same route, with the index present
  and then dropped. With the corpus the verdict rests on that passage; without it every claim
  is `ungrounded` and carries no evidence. The index is the only variable.

`backend/tests/test_request_path_precedence.py` pins the second half: the body models do not
demand the path's identifier, the path wins over a body that contradicts it, and the client
does not send the duplicate.

### 17.5 Verification

- `pytest -q` — **1155 passed**, 0 failed.
- `mypy --strict` — **no issues found in 38 source files**.
- `flutter analyze` — no issues. `flutter test` — **292 passed, 8 skipped**, 0 failed.
- `backend/scripts/verify_client_live.py` — **PASS**.

### Known limits after this pass

- **A document's retrieval index is held until the document is discarded.** There is no
  "delete document" route, so a long-lived server accumulates one index per document it has
  generated. The sessions themselves accumulate the same way; the index is the smaller of the
  two costs, and it is the price of a document that can still be edited and fact-checked.
- **A drawing's geometry is carried as its extent, not as its markup.** The size survives; the
  crop, the rotation and the alternative-content fallback of a drawing do not (section 16).
- **Legacy `.doc` is still not read**, and the reader refuses it with a reason rather than
  guessing.

---

## 18. The Kotlin tier (2026-10-24)

### 18.1 What this pass is for

The backend's behaviour was spread across a hundred and forty-three Python
modules, and the rules that decide whether a document is *true* -- how a chunk
is cut, which citation is grounded, what an operator's edit does to a verdict,
whether a `.docx` round-trips -- were scattered through them. Nothing said
those rules were the product. They looked like implementation.

They are the product. Everything else in the system is machinery around them.

This pass moves those rules into a **Kotlin tier**: a second implementation of
the deterministic half of the backend, with the Python tier reduced to what
genuinely needs a machine-learning library. The tier is a new tier, not a
rewrite of the old one -- the Python modules keep their public surface, and
the Kotlin tier takes over their bodies.

### 18.2 The invariant this pass establishes

> **Every deterministic rule of the backend that crosses the seam is
> implemented once, in Kotlin, and the Python tier calls it rather than
> repeating it.**

The corollary is equally binding:

> **The Kotlin tier's runtime dependency set is empty.**

Not "small". Empty. JSON is hand-written, XML comes from `javax.xml`, ZIP from
`java.util.zip`, HTTP from `java.net.http`, hashing from `java.security`,
dates from `java.time`. A reader can trace every behaviour of the tier to
either this repository or the JDK. A rule that needed a third-party library is
a rule that belongs in the Python tier instead.

The reason for the second invariant is not purity. It is that the tier's
value depends on it being *checkable*: a chunk id minted in Kotlin must
resolve in Python and the other way round, a `.docx` written by one must open
in the other, and a citation must parse identically on both sides. Every one
of those is a claim about bytes, and every one of them is cheap to verify when
the implementation is small and has no transitive dependencies.

The first invariant is scoped on purpose. "Every deterministic rule of the
backend" is not true of the candidate as it stands — the candidate's own
modules say so in their docstrings — and a reader who trusts an absolute
would read a deliberate Python twin as drift rather than as design.
**§18.2.1 is the catalogue of what the invariant does not cover**, and it sits
here, under the claim, so that the exception and the claim are read together.

#### 18.2.1 What the invariant does not cover

Three groups of deterministic rules sit outside the seam. Every one of them is
a decision rather than an oversight, and every one of them is listed here.

**Drift-pinned twins — the rule has one Kotlin implementation and one Python
implementation, and what holds the two together is named in the last
column:**

| Rule | Python | Kotlin | What holds them together |
| :--- | :--- | :--- | :--- |
| The Word whitespace and text-normalisation rules (contract §4, T1–T9) | `backend/src/lmeditor/word/text.py` | `kotlin/word/src/main/kotlin/lmeditor/word/WordText.kt` | The shared corpus `kotlin/word/src/test/resources/fixtures/word_whitespace_corpus.tsv`, read by both sides. |
| The Word format detection and the operator's refusal reason | `backend/src/lmeditor/word/formats.py` | `kotlin/word/src/main/kotlin/lmeditor/word/WordFormats.kt` | The same shared corpus. |
| The compiled inline-citation grammar | `backend/src/lmeditor/core/citations.py` (`CITATION_PATTERN`) | `kotlin/core/src/main/kotlin/lmeditor/core/Citations.kt` | `backend/scripts/verify_kotlin_parity.py`. The pattern stays in Python because the drafter applies it to the model's output in-process. |
| The section split of an imported document | `backend/src/lmeditor/library/importer.py` (`sections_with_origin`) | `kotlin/library/src/main/kotlin/lmeditor/library/BookImporter.kt` (`sectionsWithOrigin`) | **No comparison.** `library_importer` is declared in the harness's `REFERENCE_WITNESSES` with a Kotlin counterpart and no check, so the file's existence is witnessed and the rule itself is held only by each side's own tests. The Python copy serves the HTTP import route, where a document the reader refuses must still be answered with the reader's own reason and a 422. |
| The sliding-window and semantic chunking algorithms | `backend/src/lmeditor/provenance/chunker.py` | `kotlin/text/src/main/kotlin/lmeditor/text/Chunker.kt` | The seam serves one chunking method, and it serves the sentence-aware one; answering a sliding-window request with sentence-aware chunks would be a different rule, not a faster one. |
| The byte-offset and token-count helpers | `backend/src/lmeditor/provenance/chunker.py` | `kotlin/text/src/main/kotlin/lmeditor/text/Chunker.kt` | The seam does not expose them as methods of their own. |

**Rules with no Kotlin counterpart at all:**

| Rule | Where it lives | Why |
| :--- | :--- | :--- |
| The document-level grounding aggregation — that an unwritten section contributes one unsupported unit, so an incomplete document cannot report `f_beta = 1.0`, and that `total_claims` is the sum of the four status counts | `backend/src/lmeditor/workflow/graph.py` (`build_document_scorecard`) | The three arithmetic primitives it calls are the tier's; the aggregation policy around them is not. No `buildDocumentScorecard` exists anywhere in `kotlin/`. |
| The session and human-in-the-loop broker — the replay buffer, the subscriber fan-out, and the latch semantics of `open_hitl_review` and `record_hitl_decision` | `backend/src/lmeditor/server/session.py` | Its fan-out is asyncio and its transport is SSE, both bound to the FastAPI event loop. `kotlin/server/src/main/kotlin/lmeditor/server/WorkflowSession.kt` implements the same review-scoped latch, but the two copies are not doors over one another. |

**Python by design — the model-facing half:**

| Rule | Where it lives | Why |
| :--- | :--- | :--- |
| The drafting modules, the entailment model and the dense embedder | `backend/src/lmeditor/drafting/`, `backend/src/lmeditor/verification/nli_evaluator.py`, `backend/src/lmeditor/provenance/embeddings.py` | A model is not a rule: these need DSPy, transformers and sentence-transformers, and a rule that needs a third-party library belongs in the Python tier. |

§18.10, §18.12.10 and §18.13.7 record the same catalogue from the passes that
produced it, with the reasoning each decision had at the time.

### 18.3 The module map

The Kotlin tier is a Gradle multi-module build at `kotlin/`. One Gradle module
per Python package:

| Module | Package | Owns | Python it replaces |
| :--- | :--- | :--- | :--- |
| `:core` | `lmeditor.core` | The typed error hierarchy, the domain enums and their wire strings, the schema data classes, the inline-citation grammar, the operator-edit rule, the runtime interfaces, the shared decompression bound, and the native JSON codec. | `core/exceptions.py`, `core/types.py`, `core/schemas.py`, `core/citations.py`, `core/sections.py` |
| `:text` | `lmeditor.text` | Sentence-aware chunking, token-span lineage, claim segmentation, the citation precision/recall audit, and the provenance retrieval rule — BM25 Okapi, the dense cosine index and their rank fusion. | `provenance/chunker.py`, `provenance/lineage.py`, `provenance/indexer.py`, `verification/claim_extractor.py`, `verification/citation_checker.py` |
| `:word` | `lmeditor.word` | The OOXML document model, the `.docx` reader, the byte-deterministic writer, and the manuscript builder. | `word/model.py`, `word/text.py`, `word/formats.py`, `word/reader.py`, `word/writer.py`, `word/manuscript.py` |
| `:pdf` | `lmeditor.pdf` | PDF text and metadata extraction, written against the JDK's `Inflater` rather than `pypdf`. | `ingestion/pdf_metadata.py`, and the `pypdf` call inside `ingestion/loaders.py` |
| `:ingest` | `lmeditor.ingest` | Source loaders, the SSRF policy, the bounded HTTP reads, the upload store, the bibliography resolver chain and the ingestion service. | `ingestion/*.py` |
| `:library` | `lmeditor.library` | The on-disk book store, the bounded digest of a book, and the bounded working set. | `library/store.py`, `library/brief.py`, `library/working_set.py`, `library/importer.py`, `library/export.py` |
| `:workflow` | `lmeditor.workflow` | The workflow state, its reducers, and the conditional routing rules. | `workflow/state.py`, `workflow/edges.py` |
| `:editing` | `lmeditor.editing` | The acceptance rule for a grounded inline revision. | `editing/editor.py`, `editing/rewrite.py` |
| `:factcheck` | `lmeditor.factcheck` | The aggregation of claims, evidence and scores into a verdict. | `factcheck/service.py`, `factcheck/web.py` |
| `:discovery` | `lmeditor.discovery` | Perspective generation, outline construction, and the research dialogue. | `discovery/perspective.py`, `discovery/outliner.py`, `discovery/dialogue.py` |
| `:server` | `lmeditor.server` | The session broker, the source library and the book registry. | `server/session.py`, `server/sources.py`, `server/library.py` |
| `:vault` | `lmeditor.vault` | The rules of a vault: the safe note ids, the markdown grammar, the frontmatter, the backlink index, the search order and the store. | new — `vault/__init__.py` is the door over them |
| `:notebooks` | `lmeditor.notebooks` | The rules of a notebook: the safe notebook ids, the manifest, the counts, the ordering, the idempotence of a write and the membership of a source or a note. | new — `notebooks/__init__.py` is the door over them |
| `:bridge` | `lmeditor.bridge` | The NDJSON stdio seam the Python tier calls into. | new |

### 18.4 Dependency direction

```
                        :core
        ┌────────┬────────┼────────┬──────────┐
      :text    :word     :pdf   :workflow  :discovery
        │        │        │
        ├────────┴────────┴───► :ingest
        ├───────────────► :library
        ├───────────────► :editing
        ├───────────────► :factcheck
        └─────────────────► :server ──► :bridge
```

The direction is strictly downward and the rule is mechanical: **a module may
depend on the modules named in its own `build.gradle.kts` and on nothing
else.** `:core` depends on nothing at all. No module depends on `:bridge`.
No module depends on a module above it, and there are no cycles -- if a type
is needed by two domains, it belongs in `:core`.

The build files are the enforcement, not a convention: Gradle fails the build
on a dependency that was not declared.

### 18.5 The seam

The two tiers are two processes talking over one pipe: newline-delimited JSON
on the JVM process's standard input and standard output. The full contract is
`kotlin/bridge/README.md`; the Python client is `backend/src/lmeditor/bridge/`.

The seam carries **deterministic work only**. Where a rule needs a model, a
network or a vector index, the model side stays in Python and hands its
*result* to Kotlin, which does the arithmetic and the rule-checking:

- Python runs the NLI model; Kotlin computes the citation precision, the
  recall and the ungrounded audit from the scored claims.
- Python generates a revision; Kotlin decides whether the revision is
  acceptable -- that is, whether every citation it carries names a chunk that
  exists.
- Python retrieves evidence; Kotlin aggregates evidence into a verdict.

The wire vocabulary is the Python tier's own: snake_case keys, the same enum
strings the Python tier already publishes, and base64 for binary payloads.
A Kotlin result can therefore be validated by the Python tier's existing
Pydantic model and gets the same guarantees it gets today.

The seam serves **114 methods** across thirteen domains: `core` (5), `text` (15),
`word` (3), `pdf` (2), `ingest` (16), `library` (21), `workflow` (2),
`editing` (3), `factcheck` (9), `discovery` (14), `server` (7), `vault` (8)
and `notebooks` (9). The full
table, with the parameters and the result shape of each, is
`kotlin/bridge/README.md`.

Two rules make the seam honest. A **failure never terminates the bridge** --
the error is reported and the service continues, because a malformed request
is a client's mistake and not the server's. And the Python client **never
silently falls back** to the Python implementation: a missing bridge is an
error that names the paths it tried, because a silent fallback would hide
exactly the drift this tier exists to remove.

### 18.6 What stays in Python, and why

| Python module | Why it stays |
| :--- | :--- |
| `drafting/signatures.py`, `drafting/modules.py`, `drafting/teleprompter.py` | DSPy |
| `workflow/graph.py`, and the model calls in `workflow/nodes.py` | LangGraph, DSPy |
| `provenance/embeddings.py` | sentence-transformers |
| `verification/nli_evaluator.py` (the model half) | transformers, DeBERTa |
| `llm/` | provider SDKs |
| `server/app.py`, `server/routes.py` | FastAPI, and the wire contract the Kotlin tier declares |

The deterministic half of each of those *is* ported, with one exception: the
document-level grounding aggregation in `workflow/graph.py` has no Kotlin
counterpart at all (§18.2.1). `workflow/edges.py` and
`workflow/state.py` are Kotlin; only the graph that drives them is not.
`verification/citation_checker.py` is Kotlin; only the entailment model that
feeds it is not. What remains in Python is the machinery, not the rules.

### 18.7 The contracts that keep the two tiers from drifting

`kotlin/CONTRACTS.md` is binding on every Kotlin module: the dependency
policy, the naming mapping, the error rules, the nullability rules, the
determinism rules and the test conventions. It exists because twelve modules
written in parallel otherwise converge on twelve dialects.

The rules that actually prevent drift are narrower:

- **A shared fact is pinned in both tiers.** A chunk digest, a wire string, an
  exact output is asserted with the *same literal value* on both sides, so a
  change to one is a failing test in the other.
- **The chunk id is a contract.** `sha256("<document_id>:<char_start>:<char_end>:<text>")`,
  computed with `lmeditor.core.Hashing.sha256Hex`. Python computes the same
  digest with `hashlib`. A chunk id minted on one side resolves on the other.
- **Enums carry their wire string.** `EntailmentStatus.ENTAILED.wire` is
  `"entailed"` because that is what the Python tier publishes. The Python
  tier's historical aliases (`ENTAILMENT`, `DRAFTED`, `FLAGGED`) are
  reproduced as companion constants rather than as second enum entries.
- **Errors are typed on both sides.** The Kotlin error's simple name, message
  and `details` map cross the seam, and the Python client rebuilds the
  matching Python exception -- so `WordParseError` is still a `WordParseError`
  to the code that catches it.
- **The Python side of every comparison is a frozen witness.**
  `backend/src/lmeditor/_reference/` holds the original Python implementations
  of the rules that have been swapped onto the tier: the citation grammar, the
  operator-edit rule, the chunker and the claim extractor. They are plain
  Python and they never call across the seam. They *are* re-aligned to the
  tier's semantics when the tier's rule **deliberately** changes, and every
  such change is recorded in the harness's `REFERENCE_WITNESSES`; what they
  are not is free to drift, because the comparison is between two
  implementations. The parity harness compares the tier against
  *these*, never against the live modules -- because the live modules are now
  doors to the tier, and a door compared with the tier it opens agrees by
  construction.
  `backend/tests/test_parity_reference.py` enforces that mechanically: it
  parses the import graph of the `lmeditor` package and fails if anything in
  `_reference` can reach `lmeditor.tier`, if the harness draws its Python
  side from a door, or if the detector that finds the doors has stopped
  finding them.
- **The dependency policy is mechanical, not a promise.** The tier takes no
  third-party runtime dependency: JSON is hand-written, XML comes from
  `javax.xml`, ZIP from `java.util.zip`, HTTP from `java.net.http`, hashing
  from `java.security` and dates from `java.time`. That is written down in
  `kotlin/build.gradle.kts`, but a written-down policy is a convention.
  `backend/tests/test_kotlin_dependencies.py` enforces it from two sides: it
  reads every Gradle module's declared dependencies and requires them to be
  references to other modules of this build (the test framework is allowed,
  and only on the test classpath), and it reads the **shipped** tier jar and
  requires every class in it to belong to LMeditor or to the Kotlin language
  runtime. The second half is the one that cannot be fooled, because a
  dependency can arrive transitively or through a plugin and would never
  appear in the build files.

### 18.8 Verification

The tier is verified from three sides, because a second implementation of the
same rules is worth nothing unless the two are *shown* to agree.

**The Kotlin tier's own suite.** `gradle test` over the whole tier: **606
tests, 0 failures** — 60 in `:core`, 46 in `:text`, 139 in `:word`, 62 in
`:discovery`, 37 in `:workflow`, 28 in `:pdf`, 24 in `:server`, 31 in
`:editing`, 38 in `:factcheck`, 65 in `:ingest`, 49 in `:library` and 27 in
`:bridge`.

**The parity harness.** `backend/scripts/verify_kotlin_parity.py` asks both
tiers the same questions and compares the answers byte for byte:

| Rule | How it is compared | Result |
| :--- | :--- | :--- |
| The chunk digest | The same SHA-256 literal on both sides | agrees |
| The citation grammar | 18 texts — every marker form, markdown links, images, groups, duplicates, an astral-plane character before and after the marker | agrees |
| The operator-edit rule | The whole merged draft, field by field, including the text the operator wrote with whitespace around it | agrees |
| The chunker | 7 documents — short, long, paragraphed, astral, one character, no final stop, repeated | agrees |
| The claim extractor | 4 texts, every field of every claim, one of them padded with whitespace | agrees |
| The routing rules | 5 questions — everything verified, something still to revise, the retry budget spent, and the operator's two answers | agrees |
| The fresh state of a run | Every field a caller reads, with the tier supplying the retry budget and the threshold | agrees |
| The citation arithmetic | 6 count triples, including an empty document and a document whose citations are all wrong | agrees |
| The premise rule | Which claims are cited and the passage each is judged against | agrees |
| The lineage rule | A span's chunk, its bounds, its quote and its confidence, and a draft's markers bound to their chunks | agrees |
| The outline rule | The whole tree — node ids, titles, depths, order and the passages assigned to each leaf | agrees |
| The persona rule | Every persona's role, title and focus areas | agrees |

**12 agreed, 0 disagreed, 0 skipped.** A rule that could not be compared is
reported as *skipped*, never as passed: an absent comparison is not evidence.

The Python side of every comparison comes from `lmeditor._reference`, the
frozen witnesses — never from the live modules, which are doors to the tier
and would agree with it by construction. `backend/tests/test_parity_reference.py`
enforces that, and also pins the frozen witnesses to the answers the original
Python gave.

**The Python suite.** `pytest -q`: **1238 passed**, 0 failed — the
pre-existing suite plus the bridge-client tests, the seam integration tests,
the parity-reference tests and one test file per swapped domain.

**The seam itself.** `backend/tests/test_tier_integration.py` drives the
Python adapters in `lmeditor.tier` against the live Kotlin tier over the
pipe, and compares their answers with the frozen witnesses. A domain error
raised in Kotlin arrives in Python as the matching Python exception, with its
field — `UnsafeSourceError` from the SSRF guard included.

**Python types.** `mypy --strict src/lmeditor/tier src/lmeditor/bridge
src/lmeditor/_reference`: **no issues found in 54 source files** (the tier's 15
doors, the bridge's 3 modules and the frozen witnesses' 36 files — 35 modules
and the package's `__init__.py`). The parity
harness is strict too.

### 18.9 The behavioural differences this port found

A port is a disagreement machine: every place the two implementations differ
is a place one of them was wrong. These were found, and they are recorded
here because they are decisions, not accidents.

- **Character offsets are code points, not UTF-16 units.** The Python tier
  indexes strings by code point; Kotlin indexes them by UTF-16 code unit.
  The two disagree as soon as the text holds a character outside the Basic
  Multilingual Plane. A citation span after an emoji would have been shifted
  one character to the right. The rule now lives in
  `lmeditor.core.CodePoints`, and the parity harness carries an astral-plane
  text on both sides of a marker to keep it honest.
- **A double keeps its decimal form on the wire.** `1.0` and `1` are the same
  JSON number but not the same Python type, and a field typed as a float
  should arrive as one.
- **The seam's standard output is UTF-8 whatever the console's encoding is.**
  The first end-to-end run mangled every non-ASCII character in a document,
  because `System.out` is platform-encoded by default.
- **ZIP member timestamps are pinned.** The `.docx` writer pins them to
  `2000-01-01T00:00:00Z`, because Java's `ZipEntry.setTime` interprets the
  value in the local zone and clamps anything before the 1980 epoch.
- **A multi-section document writes only the last section's `sectPr`.** The
  Python reference path writes one per section. Single-section documents --
  the common case -- are identical.
- **An encrypted PDF is refused, not decrypted.** The native extractor has no
  RC4 or AES handler, so it reports the document as unreadable instead of
  returning an empty page that looks like an empty document.
- **A missing language model is a configuration failure.** Where the Python
  tier fell back to a deterministic derivation, the Kotlin tier raises
  `LMNotConfiguredError`. A silent fallback hides a misconfiguration; the
  deterministic path is still reachable, deliberately.
- **The Python models strip whitespace; the Kotlin models did not.** The Python
  models are declared with `str_strip_whitespace=True`, so a string field
  never keeps the whitespace around it, and the constraints are checked on the
  *trimmed* value. The Kotlin models kept it. A section that arrived padded
  was one character out in the Kotlin tier and every offset measured against
  it with it. The Kotlin models now strip on construction, using Python's
  whitespace set -- which is not Kotlin's: Python strips the no-break space,
  the narrow no-break space, the figure space and the next-line control, and
  `String.trim()` leaves all four alone. The rule lives in
  `lmeditor.core.stripModelWhitespace`.
- **Pydantic's two ways of making a model are not one.** The Python models
  *strip* when they are constructed and *do not* when they are copied
  (`model_copy()` does not re-validate). Kotlin's `data class` has one
  constructor, and the generated `copy()` calls it -- so a `copy()` that
  stripped would have quietly differed from `model_copy()` on exactly the
  values the operator hands the editor. The Kotlin models therefore have a
  **private** primary constructor reached only through the companion's
  `operator fun invoke` -- which trims, then validates -- and a **public**
  `copy()` that reaches the private constructor directly, and therefore
  neither trims nor strips.
- **The Kotlin models were missing constraints the Python models enforce.**
  A negative offset, an empty id, a score outside `[0, 1]`: Pydantic refuses
  all of them, and the Kotlin models accepted them. A model that one tier
  accepts and the other refuses is a model that reaches the audit trail in one
  form and not in the other. Every `min_length`, `ge` and `le` in the Python
  schemas now has its counterpart in `lmeditor.core.Schemas`, checked on the
  trimmed value.
- **The parity harness was comparing the tier with itself.** Two of the five
  rules it checked -- the citation grammar and the operator-edit rule -- drew
  their Python side from the live modules, which had already been swapped
  onto the tier. Those checks could not fail: they asked the tier a question
  and compared its answer with itself. They now draw their Python side from
  the frozen witnesses, and a test enforces it.

### 18.10 Known limits after this pass

- **The two shared rules already run in Kotlin.** The inline-citation grammar
  and the operator-edit rule are implemented once, in `:core`, and the Python
  tier calls them across the seam. `lmeditor/core/citations.py` and
  `lmeditor/core/sections.py` are now doors rather than implementations. The
  compiled `CITATION_PATTERN` stays in Python because the drafter needs a
  regular expression it can apply to the model's output in-process; the parity
  harness keeps it and the Kotlin tier's pattern from drifting apart.
- **Most of the domain modules are now swapped onto the tier.** The claim
  extractor, the citation checker, the chunker, the lineage engine, the
  workflow's state and routing rules, the discovery rules, the editing rules
  and the fact-check aggregation keep their Python APIs and answer with the
  tier's answers; their implementations are frozen in `lmeditor._reference`.
  What is left beside them: the ingestion rules, the book library, the OOXML
  reader and writer, and the server's session broker. Those are the domains
  whose Python APIs are *stateful objects* -- a `BookStore`, an
  `IngestionService` -- rather than functions, so their swap needs the seam to
  carry more than one call per rule, and the library's importer and exporter
  need the OOXML document model to cross it. The pattern is proven; what
  remains is the seam's breadth, not the concept.
- **Some rules stayed in Python because the seam does not serve them yet.**
  The sliding-window and semantic chunking algorithms, the byte-offset and
  token-count helpers, the model's own outline synthesis and the archetype
  table are all implemented in Kotlin and are not reachable across the seam.
  Each one is a single, clearly-named method away from being moved.
- **The Python suite needs the Kotlin tier for the swapped rules.** A test that
  exercises a swapped rule talks to the JVM. `backend/tests/conftest.py` turns
  the bridge's "the tier is not there" error into a *skip* rather than a
  failure -- an absent comparison is not a failing comparison, and it is
  certainly not a passing one. A test that never reaches the tier still runs.
- **The tier jar is a build artefact, and a stale one fails confusingly.** The
  Python side starts `kotlin/bridge/build/libs/bridge-0.1.0-tier.jar`, so a
  jar that was not rebuilt after a Kotlin change serves the *old* rules and
  the failure looks like a logic bug. `tierJar` now depends on the tasks that
  build the modules it packages, so it cannot be built without them.
- **The Kotlin models will need a migration in Kotlin 2.5.** The schema models
  have a private primary constructor and a public `copy()`, which is what
  reproduces Pydantic's two ways of making a model. The Kotlin compiler flags
  every such `copy()` with `DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING`, because
  it will become an error in language version 2.5. The files that copy a model
  suppress the diagnostic with a comment saying why. The migration is to
  replace the generated `copy()` with an explicit one -- but the distinction
  is the point, and dropping it would change what the operator's edit stores.
- **There is no desktop client.** The Flutter/Dart client was removed on
  2026-10-25 (§19); the product is the backend and the Kotlin tier, and the
  HTTP surface is the whole of the operator-facing interface until a front
  end is built. A Compose Multiplatform client was considered and left for a
  later pass: it would trade a large dependency set for a working editor the
  tier's dependency policy currently forbids.
- **PDF support is narrower than `pypdf`'s.** The native extractor covers the
  cases real documents use -- Flate streams, standard encodings, `ToUnicode`
  CMaps -- and *detects and reports* what it does not support rather than
  returning an empty document that looks like an empty page. Encrypted PDFs
  are refused rather than decrypted, and object streams are reported rather
  than read.
- **Equations survive as markup, not as a model.** The OOXML reader preserves
  an equation's OMML markup on the run and the writer re-emits it verbatim,
  but the equation is not modelled the way a paragraph is.
- **The full OOXML document model does not cross the seam.** `word.read` and
  `word.write` would carry a structure of several dozen types; the tier
  consumes documents internally instead, and `word.manuscript` is the method
  the Python side uses.

### 18.11 The second pass: swapping the domain modules onto the tier

The first pass built the Kotlin tier and proved it agreed with the Python
originals. It did not remove the originals: both implementations still stood
beside each other, and two implementations of one rule are worth nothing
unless one of them goes.

This pass removes them, one domain at a time. A swapped module keeps its
Python API exactly -- the names, the signatures, the defaults, the return
types and the exceptions -- and answers with the tier's answers. Its
implementation is not deleted but *frozen* into `lmeditor._reference`, where
the parity harness can still compare the two sides. A frozen witness is the
only thing the harness may compare against: a door compared with the tier it
opens agrees by construction, and a drift detector that cannot fail is not a
drift detector.

#### 18.11.1 The contract drifts this pass found

The tier facades had been written against a seam that had since moved, and
three of them were wrong in ways that did not announce themselves:

- `tier.workflow.route` sent `{"state": ...}` while the handler read
  `section_drafts`, `retry_counts`, `max_retries` and `hitl_approved`. Because
  a missing field reads as an empty one, the route answered `all_verified` for
  *every* state -- a wrong answer, not an error.
- `tier.text.build_lineage` sent a shape the handler did not read, so every
  call fell into the wrong branch of the method.
- `tier.discovery.build_outline` sent an `operator_outline` the handler
  ignored, and never sent the research memory the outline is built from.

A seam that silently answers is worse than a seam that refuses. All three are
fixed, and the routing rules are now asked of the tier in the shape the
handler reads. The parity harness asks both tiers the same five routing
questions -- everything verified, something still to revise, the retry budget
spent, and the operator's two answers -- and compares the answers.

#### 18.11.2 Two rules that had to be split rather than moved

The citation checker's premise selection is a *rule* (a chunk is narrowed to
the sentence that speaks to the claim) and the entailment judgement is a
*model call*. The premise rule now lives only in Kotlin and is served by
`text.citation_check.resolve_premises`; Python asks for the premises, runs the
model, and hands the scored claims to `text.citation_check.audit`, which owns
the precision, the recall and the groundedness score. The same shape applies
to the fact checker: Python retrieves the evidence and judges it, Kotlin
aggregates it.

The wire contract grew where the rules needed it. `text.lineage.build` became
`text.lineage.attribute_span` and `text.lineage.extract_from_draft`, because
choosing the mode from the text would have answered the wrong question for a
draft whose text is empty. The citation arithmetic is served one function at a
time (`precision`, `recall`, `f_beta`), so that each Python function maps onto
exactly one call and neither side holds a second copy of a formula.

#### 18.11.3 A packaging bug that would have shipped stale code

The `tierJar` task packages the tier's modules from their jars, but it did not
depend on the tasks that build them. Gradle therefore resolved the classpath
at execution time and took whatever jar happened to be on disk: the tier jar
could carry a stale module, and the seam would fail in a way that looked like
a logic bug rather than a packaging bug. The dependency is now declared. It is
the kind of failure the tier exists to make impossible everywhere else, found
in the tier's own build.

#### 18.11.4 What was swapped

| Python module | Frozen witness | What the tier now owns |
| :--- | :--- | :--- |
| `lmeditor.core.citations` | `_reference/citations.py` | the inline-citation grammar |
| `lmeditor.core.sections` | `_reference/sections.py` | the operator-edit rule |
| `lmeditor.verification.claim_extractor` | `_reference/claim_extractor.py` | claim segmentation |
| `lmeditor.verification.citation_checker` | `_reference/citation_checker.py` | premise selection, the audit arithmetic, the section verdict |
| `lmeditor.provenance.chunker` | `_reference/chunker.py` | the sentence-aware chunking window |
| `lmeditor.provenance.lineage` | `_reference/lineage.py` | span attribution and the confidence score |
| `lmeditor.workflow.state` | `_reference/state.py` | the shape of a fresh run |
| `lmeditor.workflow.edges` | `_reference/edges.py` | the routing rules |
| `lmeditor.discovery.outliner` | `_reference/outliner.py` | the outline tree and the passage assignment |
| `lmeditor.discovery.perspective` | `_reference/perspective.py` | the persona set |
| `lmeditor.editing.editor` | `_reference/editor.py` | the edit plan and the acceptance rule |
| `lmeditor.factcheck.service` | `_reference/factcheck_service.py` | the aggregation, the scorecard and the summary |

Every one of them kept its Python API: the names, the signatures, the
defaults, the return types and the exceptions. The parity harness asks the
tier and the frozen witness the same questions about the rules they share and
compares the answers, so a door and the rule behind it are shown to agree
rather than assumed to.

What the swaps did **not** do is swallow the parts of those modules that are
model-bound or network-bound. The entailment model, the language model, the
indexer and the web search all stay in Python, and the tier is asked for the
rules around them rather than for their answers.

#### 18.11.5 What is left, and why it is different

Four Python domains still carry their own implementations: the ingestion
rules (2,846 lines), the book library (2,529), the server's session broker
(2,836) and the OOXML reader and writer (8,592). Their Kotlin twins already
exist and are tested, so nothing needs to be *written* — what they need is a
wider seam.

The difference is the shape of their APIs. Every module swapped in this pass
exposes **functions**: a rule takes data and answers with data, so one seam
method serves it. The four that remain expose **objects**: a `BookStore`, an
`IngestionService`, a session broker, a document model. Their rules are
sequences of stateful steps over a shared on-disk store, so a faithful door
needs the seam to carry more than one call per rule — read the manifest, write
the section, list the books, index the corpus — and to carry the library's
importer and exporter, which read and write the OOXML document model whose
nested collections were deliberately kept out of the wire.

That is a matter of seam breadth, not of concept. The pattern is proven by
twelve modules, and the work that remains is: give each store a method per
operation, let the shared store root carry the state, and — for the library's
importer and exporter — decide whether the OOXML model crosses the seam or
whether the tier keeps reading and writing it internally and the Python side
asks for the result.

---

### 18.12 The third pass: the objects cross the seam

§18.11 left four domains behind and named the reason: they expose **objects**,
not functions. A `BookStore`, an `IngestionService`, a source library and a
document model are stateful, so one seam method cannot serve them the way one
method served the citation grammar. The third pass answered that question, and
the answer changed the shape of the seam.

#### 18.12.1 The decision

**The OOXML document model crosses the seam.**

The alternative — keep the document model inside the tier, and let the Python
side ask for the bytes — was rejected, and the reason is worth recording.
Without the model on the wire, the reader and the writer stay in Kotlin but
the *library* cannot use them: importing a `.docx` means splitting it into
sections, and splitting it means reading the document model. If the model does
not cross, either the split stays in Python (and the model has to cross
anyway) or the tier grows a splitter it cannot express over a value seam.

The model crosses, and one decision retired **7,600 lines** of Python. The
cost is a contract that must be pinned, because a document model has seventeen
types and a field added to one of them is a field that does not arrive.

#### 18.12.2 The contract that pins it

`kotlin/word/WIRE.md` is the binding wire contract for the document model:

- §1 the conventions — snake_case keys, `wire` strings for the enums, and the
  shape of a nested type;
- §2 the wire form of all **seventeen** model types, field by field;
- §3 the `word.read` and `word.write` requests and responses, and §3.3 "The
  binary key name";
- §4 the rules a change to the model has to follow.

Two conventions in it are worth naming here, because both are traps:

- **A top-level binary payload is `bytes_b64`; a binary field inside a model
  keeps its own name and holds base64.** `WordMedia.data` and `WordPart.data`
  are base64 strings under `data`, not under `bytes_b64`. The rule is
  "the payload is bytes; the field is data", and it exists because the live
  `word.manuscript` method already used `bytes_b64` for its answer.
- **`model_dump(mode="json")` renders `bytes` as a UTF-8 string, not as
  base64.** The Python encoder therefore builds the `data` fields explicitly;
  a `model_dump` cannot be trusted to carry a binary field across.

#### 18.12.3 The store root travels on the wire

The book store and the upload store both have a root, and both are now
parameters of every call rather than settings of the process. The rule is
mechanical: a request that names no root gets the configured one, and a request
that names one gets exactly that one. The same rule is why
`LMEDITOR_LIBRARY_DIR` and `LMEDITOR_UPLOAD_DIR` are read by the process that
owns them and forwarded as *values* — the tier resolves the value, but it does
not read the environment of a process it does not own.

#### 18.12.4 Where a wire form lives

A model's wire form lives **beside the model**, not in `:core`, whenever
`:core` cannot import it without a cycle. The source library's wire form is
the case in point: `:server` owns the model, `:core` cannot import `:server`,
and `:server` cannot be imported by `:bridge` before `:server` — so
`SourceLibraryWire.kt` lives in `:server` and the handler in `:bridge`
consumes it. The rule is the dependency policy of §18.4 applied to a new kind
of artefact.

#### 18.12.5 What the third pass swapped

| Wave | Domain | Python modules retired | New seam methods |
| :--- | :--- | :--- | :--- |
| 3 | The ingestion rules | `ingestion/{limits,loaders,registry,pdf_metadata,url_policy,http_safety}.py` | 9 `ingest.*` |
| 3b | The book library | `library/{store,brief,importer,export,working_set}.py` | 21 `library.*` |
| 4 | The OOXML document | `word/{reader,writer}.py` | `word.read`, `word.write` |
| 5a | The server's source library | `server/sources.py` | 7 `server.sources.*` |
| 5c | The bibliography and the source store | `ingestion/{resolvers,service,source_store}.py` | 7 `ingest.*` |
| 6 | The web evidence | `factcheck/web.py` | 5 `factcheck.web.*` |
| 7 | The source analysis and the dialogue | `discovery/{source_analysis,dialogue}.py` | 11 `discovery.source.*`, `discovery.dialogue.*` |
| 8 | The manuscript builder | `word/manuscript.py` | `word.manuscript` (extended) |

Wave 8 also removed a **1,165-line vendored copy** of the manuscript builder
that the `library.export` witness carried: the witness now imports the frozen
`word_manuscript` sibling instead, so there is one frozen original rather
than two copies of it.

`word/model.py`, `word/text.py` and `word/formats.py` stayed in Python, for
reasons that are not about capability: `formats.py` holds constants that are
consumed at import time by other modules, and `text.py` is used by a Pydantic
validator that has to raise `ValidationError` — a Python exception — not
`BridgeUnavailableError`. The model's own `from_wire`/`to_wire` live on the
Python side because the witnesses import them.

#### 18.12.6 The environment crosses the process boundary with the value

The upload root is read from `LMEDITOR_UPLOAD_DIR`, and the tier is a
different process. A tier that read its *own* environment would answer with
the root *its* environment names, and the two tiers would disagree about
where an upload goes — and a `monkeypatch.setenv` in a Python test would be
invisible to it.

The rule is therefore split the way the ownership is split: **the process
that owns the configuration resolves the *value*, and the tier resolves the
value into a path.** `upload_root(base=…)` takes the configured directory as
an optional parameter, and the store's `root` — the resolved directory — is
sent with every store call. One rule, one answer, and a test that sets the
variable in Python gets the directory it asked for.

#### 18.12.7 What the third pass also found

The three later waves found the same class of defect the writer had, and
each was found by a comparison rather than by reading:

- **The Kotlin's `\w` is not Python's `\w`.** The source analysis tokenises
  with a regular expression, and Python's word character is *alphanumeric or
  underscore* while Java's `(?U)\w` also admits combining marks and reads a
  surrogate pair as two non-word characters. `déjàshown` was a finding in
  Python and not in Kotlin. The tokenisation is now decided in code from
  Python's own definition, and a test with non-ASCII input pins it.
- **The tie-break in the dominant passage was the other way round.** Python's
  `max` over `(count, chunk_id)` keeps the *greatest* chunk id; the Kotlin
  kept the smallest, so a session's takeaway named a different passage.
- **A dotted base class is a name to a type checker.** The DSPy signature is
  built at run time from a module fetched on demand, and mypy could not
  follow the dotted name in the base class list. The lookup is now by
  `getattr`, which is the same at run time and is a name the checker can
  follow.
- **`SourceStore.remove` had a traversal bug in the Python original.** It
  joined the source id to the root without validating it, so
  `remove("../../evil")` deleted files outside the root. The Kotlin tier
  validates the id and refuses. The tier is right, and the door now refuses
  too — a behaviour change in a public method, in the direction of safety.
- **`Source.added_at` was empty on the wire.** The Python model needs a
  datetime, so `ingest.ingest` could not be validated at all. The tier now
  stamps the source with the instant it was added.
- **The web evidence lost `trafilatura`, and the reason is the dependency
  rule.** The original page reader preferred `trafilatura` and fell back to
  stripping the tags; the tier has no third-party dependencies, so it
  implements the *fallback*, and the fallback is what the parity harness
  compares. The consequence is real and is recorded here rather than hidden:
  the premise text drawn from a thin web snippet may now carry boilerplate
  that `trafilatura` would have stripped. The same rule that made the tier
  dependency-free made this extraction simpler, and the two cannot both be
  had without a dependency.
- **The manuscript builder's regular expressions were not Unicode-exact.**
  Python's `re` is Unicode-aware and Java's is not: `\d` in Python is the
  decimal category and in Java it is `[0-9]`, `\S` in Python excludes the
  non-breaking space and in Java it does not, and Python's `.` matches
  U+2028 while Java's does not. A list written in a script that does not use
  ASCII digits was therefore prose on the Kotlin side and a list on the
  Python side. The patterns are now written to Python's own semantics, and a
  differential probe over eighteen adversarial inputs reports no divergence.
- **A dotted base class is a name to a type checker.** The DSPy signature is
  built at run time from a module fetched on demand, and mypy could not
  follow the dotted name in the base class list. The lookup is now by
  `getattr`, which is the same at run time and is a name the checker can
  follow.

#### 18.12.8 The defects these passes found

The parity harness earned its place here. Each of the following was found by
a comparison, not by reading:

1. **`xmlns:o` was declared but never used.** The VML `o:title` attribute was
   undeclared, which made `word/document.xml` unparseable. A package Word
   cannot open is not a package.
2. **The writer dropped every carried part's own `.rels` part** and renumbered
   the part relationship ids, so a document that had been read and written
   again lost its relationships.
3. **`+` binds tighter than `if` in Kotlin.** A branch inside a string
   concatenation was swallowed, and the `/>` that closes an external
   relationship was dropped. The XML was malformed.
4. **The ZIP epoch was pinned to 2000-01-01** on the Kotlin side and to
   1980-01-01 on the Python side.
5. **`xml:space="preserve"` was emitted unconditionally.** python-docx emits it
   only when the text has surrounding whitespace.
6. **A table re-declared the namespace of the document part.** The reference
   writer builds its tables with lxml, which drops a redundant declaration
   when the element is moved into the body; the Kotlin writer kept it, and the
   two writers disagreed on the bytes of the document part for no gain.
7. **The seam answered a missing section with the wrong error type.** A
   `ValidationError` reached the route as a 500 where the Python contract said
   404; the tier already had `SectionNotFoundError` and the handler simply
   did not use it.
8. **`tierJar` could ship a stale module jar.** The packaging task did not
   depend on the runtime classpath, so a module jar that had not been rebuilt
   was packed into the tier.

#### 18.12.9 Verification

The parity harness grew from twelve checks to **nineteen** in this pass, and to
**twenty-two** across the whole of §18 *as that section stood* — the harness
holds **25** named checks today, which §18.13.6 records — adding "word
reading", "word writing", "manuscript building", "the source library", "the
source analysis", "the web evidence" and "the bibliography resolver". Every
check compares the frozen Python witness against the Kotlin tier on the same
input, and a disagreement is a failure, not a note.

The new checks earned their place immediately: "the web evidence" found, on
its first run, that the tier's page reader and the witness's *fallback* page
reader disagreed — and the reason turned out to be that the witness still
preferred `trafilatura`, which the tier cannot use. The check now asks the
witness for the fallback explicitly, and the difference between the two
extraction paths is recorded rather than hidden.

Two rules keep the comparison honest. The Python side of a comparison is
always a **frozen witness** — plain Python, self-contained, and forbidden by
`backend/tests/test_parity_reference.py` from importing `lmeditor.tier` or
`lmeditor.bridge`, transitively. And the comparison normalises the things
that are *labels* rather than facts: relationship ids are placeholders, and
timestamps and labels are normalised, so a check compares structure and not
spelling.

#### 18.12.10 What is left, and why it is different

Three things stayed in Python, and none of them is a limitation of the tier:

- **`server/session.py`** — the session broker. Its fan-out is asyncio and its
  transport is SSE, both bound to the FastAPI event loop; a synchronous NDJSON
  seam cannot drive them. The Kotlin twin of the state machine is complete and
  tested; what cannot cross is the *transport*, not the *rules*.
- **`library.sections`** — the section split of an imported document. The rule
  has two homes: the tier's `sectionsWithOrigin`, which `library.import_book`
  uses, and Python's `sections_with_origin`, which the HTTP import route uses
  so that a document the reader refuses can still be answered with the
  reader's own reason and a 422. The Python copy cannot simply become a door,
  because a section's origin carries the document's *blocks* in the Python
  model shape — the origin is not a free-form map that wants a type, it is a
  copy of the document. No comparison holds the two rules together today; each
  is held only by its own side's tests, and the harness declares
  `library_importer` with a counterpart and no check, so the gap has an
  address rather than being silent. The rest of the import and export path is
  on the tier.
- **The Pydantic model layer** — `core/schemas.py`, `core/types.py`,
  `core/exceptions.py` and `word/model.py`. These are not rules: they are the
  *shape* of the Python API, and the Python side validates what crosses the
  seam against them. The tier has its own data classes for the same shapes,
  and the two are kept honest by the wire contract and by the parity harness.
  The frozen witnesses import the Python models, so the models cannot be
  retired without retiring the witnesses.

The rule that produced all three is the same one the whole tier rests on:
**a Python implementation is retired only when what remains behind the door is
genuinely Python.** A Pydantic validator that must raise `ValidationError` is
genuinely Python. An SSE fan-out on an event loop is genuinely Python.
Everything else was a rule in disguise.

---

### 18.13 The fourth pass: the rules that were never in a library

#### 18.13.1 The decision

**The provenance retrieval rule moves onto the tier.**

`provenance/indexer.py` held 337 lines of hand-written arithmetic: BM25
Okapi, a deterministic SHA-256 term-frequency hash embedder, cosine
similarity and Reciprocal Rank Fusion. `provenance/service.py` held the
supporting-text rule — the longest run of words a claim and a passage share,
and the share of the claim that run covers.

Neither of them needed a library. The contract said the indexer stayed in
Python because of LlamaIndex, and the contract was **wrong**: the module
imported no ML library at all, and the only part of it that was ever
model-bound was the *embedder* it could delegate to — `sentence-transformers`,
which lives in `provenance/embeddings.py` and is still Python.

That is the whole lesson of this pass, and it is worth stating plainly
because the cost of getting it wrong is a port that is never started: **a
contract that names a library the code does not use is worse than one that
names none, because it makes the port look impossible.** The rule that
decided the matter was not "is this module portable?" but "what does this
module actually import?"

#### 18.13.2 What the fourth pass swapped

| Wave | Domain | Python modules retired | New seam methods |
| :--- | :--- | :--- | :--- |
| 9 | The retrieval and the supporting text | `provenance/{indexer,service}.py` | `text.retrieval.rank`, `text.lineage.supporting_text` |
| 10 | The Word text and format rules | — pinned, not moved (see §18.13.5) | — |
| 11 | The claim-lineage composition | `provenance/service.py` (the composition) | `text.lineage.attribute_claim` |

The tier gained `text/Indexer.kt` (the retrieval rule), `text/SupportingText.kt`
(the supporting passage), and the character predicates that both need:
`CodePoints.isPythonWordChar` and `CodePoints.isPythonWhitespace`, moved out
of `discovery` so that the rule has one home.

#### 18.13.3 The measurement: two logarithms

The BM25 inverse document frequency is `math.log(...)`, and Python's
`math.log` and Java's `Math.log` are **not** guaranteed to agree. The
question was measured rather than assumed: every IDF for every corpus of
1…300 documents — 45,150 values — was computed on both sides and compared
as IEEE-754 bit patterns.

- **45,058 (99.80%) are bit-identical.**
- **92 (0.20%) differ, and every one of them by exactly 1 ULP.**

So the parity check compares the **ranking exactly** — the ordered chunk ids —
and the **scores within `1e-12`**. The tolerance is there so the check is not
brittle, not to cover a difference: on the parity corpus all 56 score entries
came out bit-identical, because every IDF it uses happens to be one of the
99.80% that agree, and the dense and fused scores involve no logarithm at all.

The rule that came out of it is the one the whole tier rests on: **Python's
`re` and arithmetic semantics are the yardstick.** The Kotlin arithmetic was
not "fixed" to force a bit-exact match; a difference that exists was recorded
as a difference, with the tolerance stated.

#### 18.13.4 The seam carries data, and what that costs

This pass found the seam carrying **data**, not a live index: every search
sent the corpus — the chunk ids and their text — and the tier re-tokenised it
per query. That was a deliberate property of the design, and it had a price:
for a large corpus the transfer, not the scoring, was the dominant cost.

The same property made the *composition* of the claim-lineage rule expensive.
Ranking, supporting text and attribution were three questions asked in
Python, and a claim cost `1 + 2 × top_k` seam round trips — **eleven** at the
default `top_k = 5`. Wave 11 moved the composition itself to the tier as one
method, `text.lineage.attribute_claim`, so a claim costs **one** round trip
and the rule that decides *which* passage supports *which* claim has one
home. The saving is not a micro-optimisation: it is the difference between
eleven crossings of a process boundary per claim and one.

**Corrected — 2026-10-27.** The tier-side index handle this pass said would
need a larger decision now exists. The tier builds the index **once per
corpus** and keeps it in a resident cache keyed by a content-derived digest
(`CORPUS_DESCRIPTOR_CHUNK_ID` in `kotlin/text/.../Indexer.kt`), and the door
names the corpus by that digest — so the corpus travels **once, when it
changes**, and a later query is answered from the index the tier already
built. A corpus that changed is a different digest, so the tier never
answers a query about the old corpus with the new one. The transfer cost
this pass recorded was the cost of the design *then*; the rest of the section
is left as the record it was.

#### 18.13.5 The Word rules had two answers, and one of them was wrong

`word/text.py` and `word/formats.py` are the Python twins of
`lmeditor.word.WordText` and `lmeditor.word.WordFormats`. They are **not**
doors, and they cannot be: `normalize_whitespace` is called inside a Pydantic
model validator on **every** `WordBlock` construction, so a seam round trip
per block would be a serious regression. The rule stays in Python; what was
missing was a mechanism that stops the two copies drifting apart.

They had already drifted.

Java classifies **U+0085** (NEXT LINE) as a control character, so
`Character.isWhitespace` says it is not whitespace. Python's `re` `\s` and
`str.strip()` both say it **is**. The house's canonical
`CodePoints.isPythonWhitespace` already had it right — and
`lmeditor.word.WordText.isWhitespace` kept a second copy that omitted it.

The consequence was live and reachable. The tier read a `.docx`, normalised
its text with the Java set, and kept the U+0085; the Python model then
compared that text with the *normalised* concatenation of the block's runs,
using Python's set, which removes it — and **rejected the document the tier
had just parsed**:

```
python normalize_whitespace: 'abc def'
REJECTED: ValidationError
```

The fix was not a patch but a deletion: `WordText.isWhitespace` and
`WordManuscript.isSpace` now **delegate** to `CodePoints.isPythonWhitespace`,
and the two hand-rolled copies are gone. What the reader does now is worth
stating, because it is the real invariant: the reader hands the raw U+0085
through **in the runs** — runs carry the document's text verbatim — and
**normalises it in the block text**. The tier no longer *produces* a block
text that the model must reject.

The sweep found three predicates in the tier and one of them was a different
rule, not a copy: `PdfParser.isWhitespace` is the **PDF specification's**
whitespace (NUL, tab, LF, FF, CR, space), and it stays, because PDF text
operators are not Python text. Likewise `WordManuscript.isAlnum` is Python's
`isalnum`, which is a genuinely different rule from Python's `\w` — the
latter includes the underscore — so it stays too.

**What replaced the drift is a corpus, not a convention.** One file,
`kotlin/word/src/test/resources/fixtures/word_whitespace_corpus.tsv`, holds
107 records — 47 normalisations, 11 joins, 41 strippings and 8 format
detections — and **both** sides read it: the Kotlin test as a classpath
resource, the Python test by path. The expected values are generated by
`backend/scripts/generate_word_shared_corpus.py`, which calls the Python
implementations; they are never written by hand. Two copies of the expected
values could drift, which is the failure being closed, so there is one copy.

The format is tab-separated ASCII with escapes, because neither side may grow
a JSON parser to read it. As a one-off check, the two tiers were compared
over **201,488 records** — every BMP code point alone and wrapped, every
whitespace and replaced-space pair, and 16³ triples — with **zero**
disagreements. To repeat it: generate the corpus with
`backend/scripts/generate_word_shared_corpus.py` over a wider input set,
stand it in for the shared file, run `:word:test`, and put the committed
corpus back. The committed 107 records are the permanent guarantee; the
sweep is what says they are not cherry-picked.

#### 18.13.6 Verification

| Check | Before this pass | After this pass |
| :--- | :--- | :--- |
| Kotlin tests | 716 | **762**, 0 failures |
| Python tests | 1234 | **1280**, 0 failed |
| Parity checks | 19 agreed | **22 agreed**, 0 disagreed, 0 skipped |
| `mypy --strict` | clean, 121 files | clean, **123 source files** |
| Seam methods | 94 | **97** |
| Seam calls per claim | 11 (`1 + 2 × top_k`) | **1** |

**Current — 2026-10-27.** The table above is this pass's own measurement and
is kept as the record of it. The candidate's totals have since grown: the
Kotlin tier holds **1,059** tests across the fifteen modules
`kotlin/settings.gradle.kts` declares — **941** of them in the fourteen tier
modules and the remaining **118** in `:desktop` — the Python suite is **1691
passed, 0 failed, 0 skipped**, `mypy --strict src` is clean over **133**
source files, and the parity harness holds **25** named checks, which make
**26** results with the frozen-module ledger check appended.

The three new parity checks — "retrieval ranking", "supporting text" and
"claim lineage" — compare a **frozen witness** against the tier, never a
door: a door compared with the tier it opens agrees by construction and
proves nothing.

Two of the checks compare a float. The retrieval one compares the ranking
exactly and the scores within `1e-12`, for the reason given in §18.13.3. The
claim-lineage one compares the claim id, the chunk id, the span and the exact
quote exactly, and the confidence within `1e-12` — a guard, not a necessity:
all 19 records across its 7 cases were verified **bit-for-bit identical** on
every compared field, because both tiers scale with the same
round-half-to-even on the exact binary value. The lineage id and the
timestamp are excluded: the frozen engine mints a random one, the tier a
deterministic one.

Two traps in the new rule are pinned by tests rather than by convention: the
span is measured in **code points** and not UTF-16 units (a draft of 34 code
points and 35 UTF-16 units, so a `String.length` would put the span one past
the end of the draft and raise instead of answering), and the confidence is
rounded with the house's `roundTo` — `BigDecimal` with `HALF_EVEN`, which is
exactly Python's `round` — and never with Java's half-up rounding.

#### 18.13.7 What is left, and why it is different

- **The embedder.** `sentence-transformers` and any injected
  `DenseEmbedderProtocol` stay in Python. A model is not a rule. When an
  embedder resolves, Python normalises the vectors and hands them to the
  tier; when it does not, no vectors travel and the tier uses its own
  deterministic TF-hash fallback. Which of the two is in use is never a
  secret: `embedder_name` reports it.
- **The confidence scaling** moved to the tier with the rest of the
  composition, because it is arithmetic on the tier's own record. It uses
  the house's `roundTo` — `BigDecimal` with `HALF_EVEN` — which is exactly
  Python's `round`; Java's own rounding is half-up and would disagree. The
  helper now lives in one place, `text/Rounding.kt`, and the duplicate that
  had been sitting in `CitationChecker.kt` is gone.
- **The tier's chunk store** is a map of chunk id to passage text, not of
  chunk id to `SourceChunk`. The seam carries `{chunk_id, text}`, and a
  `SourceChunk` would have demanded a 64-hex chunk id and a non-empty
  document id that the seam's parameter table never promises. The engine
  only ever read the passage, so the store was widened to what it reads.
- **One dead parameter was left in place.** `ProvenanceIndex.__init__` still
  accepts a `lineage_engine`, which the claim rule no longer uses now that
  the composition is the tier's. Nothing in the repository passes it, but a
  door's constructor is part of its public API, and removing an argument is
  a breaking change that no caller has asked for. It is recorded here rather
  than quietly changed.
- **The Word text and format rules** stay in Python, because the Pydantic
  validator is a hot path and a seam call per block would be a regression.
  They are held to the tier by the shared corpus, which is a different kind
  of guarantee from the seam and the right one for this shape.
- **The section split** of an imported document has two homes — the tier's
  `sectionsWithOrigin` and Python's `sections_with_origin` — and **no
  comparison holds them together**: the parity harness declares the
  counterpart and no check. See §18.12.10.

---

## 19. The Dart/Flutter client removed — 2026-10-25

### 19.1 What was decided

The **Kotlin tier was kept** and the **Flutter/Dart desktop client was
removed**. The repository now holds the deterministic tier, the Python
agentic tier, and the FastAPI transport tier, and no front end.

The reason is the one the tier policy already argues. A deterministic rule
belongs to exactly one implementation, and the client was a third copy of
several of them: `client/lib/core/models/manuscript_renderer.dart` was 32 KB
of Dart re-implementing the exporter's markdown syntax — the rules that
`backend/src/lmeditor/word/model.py` and the tier's
`kotlin/word/WordManuscript.kt` already hold. Two copies of a rule can
disagree; three are three chances to. Removing the client removes a whole
class of drift rather than creating one.

The consequence is accepted deliberately: **there is no operator-facing UI**
until one is built. §18.10 has said for some time that the objective is the
backend, and that a Compose Multiplatform client would trade a working
desktop editor for a large dependency set the tier's policy forbids. That
trade was not taken here either. The HTTP surface is the interface until then.

### 19.2 What was removed

| Removed | Was | Now |
|---|---|---|
| `client/` (83 tracked files) | The Flutter desktop application | Nothing. The tier and the backend are the product. |
| `backend/tests/test_client_contract.py` | Scraped the Dart source to guard the SSE vocabulary | `backend/tests/test_wire_contract.py` |
| `backend/scripts/verify_client_live.py` | Ran the Dart suite against a live server | `backend/scripts/verify_end_to_end.py`, plus a vocabulary check |
| `backend/scripts/verify_client_hitl_live.py` | Ran the Dart review-gate suite against a live server | `backend/scripts/verify_hitl_live.py` |
| The client half of `backend/tests/test_request_path_precedence.py` | Proved the Dart client did not send the path's id in the body | The server half, strengthened: a body that contradicts the path cannot redirect a request |

### 19.3 The guard that replaced the client guard

The old contract test asked a good question — *does every event the backend
publishes reach a consumer that understands it?* — and asked it by scraping
the Dart source for a `switch` statement. With no consumer in the
repository, that question has a better answer than a source scrape, and it
has two of them:

- **`backend/tests/test_wire_contract.py`** reads the Kotlin tier's own
  source and checks the two tiers against each other: the same event
  vocabulary, the same SSE envelope, the same operator outline. A name that
  exists on one side only is an event a consumer cannot recognise, or a frame
  nobody ever publishes — and both look like a working system until
  something is missing. The test *fails* when the Kotlin source is absent,
  because an absent comparison is not a passing comparison.
- **`backend/scripts/verify_end_to_end.py`** now checks the live wire: every
  event the stream actually published must be one the declared vocabulary
  names. A typo'd event name is now a failure rather than a frame that
  silently reaches nobody.

This is a stronger guard than the one it replaces. The old test could only
prove the backend and the client agreed *at rest*; the new pair proves the
two tiers agree *at rest* **and** that the running server publishes nothing
outside the contract.

### 19.4 The review gate, kept

The gate was the one flow where a human is the only thing that can move a
run forward, and the Flutter harness was proving it end to end. That proof
is kept, over the same wire, without a client:
`backend/scripts/verify_hitl_live.py` starts a backend **without** a language
model — so no section can be drafted, which is what routes a run to the gate
— and then checks that the gate is published, that it can be answered with
the body the server accepts, and that the run finishes afterwards. It also
exercises the "the gateway is up but cannot write" path, which is the one an
operator meets first when a provider is misconfigured.

### 19.5 What did not change

- **No rule was lost.** The client's renderer was a port of a rule that
  already lived in Python and Kotlin; the client's models were projections
  of the schemas that the two tiers own.
- **The Kotlin tier is untouched.** No module, no file, no contract moved.
  The one change is a comment: `kotlin/core/.../Enums.kt` no longer says
  the vocabulary exists for the Flutter client, and says instead what it
  does exist for.
- **The frozen witness is still frozen.** `backend/src/lmeditor/_reference/`
  keeps its prose, including the phrase "in Flutter studio", because that
  tree is a historical witness of a past implementation and is not rewritten
  to follow a later change. It is not drift; it is a record. The one
  exception is deliberate and recorded: a change to the tier's *rule* is
  mirrored into the witness when the tier's rule deliberately changes, and
  every such change is named in the harness's `REFERENCE_WITNESSES`.
- **The endpoint contract is unchanged.** Every route, every payload and
  every event name is exactly what it was. A consumer written against the
  old client still works.

### 19.6 The debt this leaves

The operator-facing interface is now an HTTP contract with no implementation
in the repository. That is a real gap, not a tidy state, and it is recorded
here so the next pass starts from it rather than rediscovering it:

1. **No UI at all.** The outline, the verification inspector, the library
   panel, the source panel and the streaming editor exist only as routes and
   payloads.
2. **The wire is now the contract.** `EventType` and the SSE envelope are
   the only description of what a consumer should expect. They are enforced
   between the tiers, but nothing is enforcing them *for* a consumer, because
   there is no consumer.
3. **The natural replacement is Kotlin.** A Compose Multiplatform client
   would reuse the tier's schemas and its wire types rather than
   reimplementing them, and would need a dependency set the tier does not
   currently take. That decision — and the dependency policy change it
   implies — is a separate pass and is not pre-empted here.

> **Answered by §20.** Item 1 above is closed for the document pipeline: a
> Kotlin desktop client now exists, and it lives in a module above the tier
> rather than inside it. Item 3's question — where the client goes and what
> it may depend on — was answered in §20.2 and §20.4. Item 2 is partly
> closed: the client is a consumer, so the wire is now enforced for a
> consumer as well as between the tiers. The book library and inline editing
> remain open, and are recorded in §20.12.

---

## 20. The Kotlin desktop client — 2026-10-25

### 20.1 What this pass is for

§19 ended with a debt: the operator-facing interface existed only as routes
and payloads, with nothing in the repository to call them. This pass builds
that interface, for the document pipeline — sources in, a verified, exportable
document out — and it builds it in Kotlin, reusing the tier rather than
copying it.

The scope is the pipeline, deliberately:

| In scope | Deferred |
| :--- | :--- |
| Ingesting sources (file and URL) | The book library |
| Discovery: perspectives, research notes | Inline edit and fact-check |
| The operator's outline | |
| Streaming draft and the live manuscript | |
| Verification: claims, citations, scorecard | |
| The review gate | |
| Export to `.docx` | |

Books and inline editing are the two parts of the system with no natural
place in a single-window studio. Building them would have doubled this pass
to no end, so they are recorded as debt in §20.12 rather than quietly
dropped.

### 20.2 Where the client sits

The client is a **new Gradle module above the tier**, not a thirteenth tier
module:

```
:desktop  ->  :core
```

`:desktop` depends on `:core` and on nothing else in the repository. The
tier still depends on nothing at all. The arrow runs one way and one way
only, so the client may use every rule the tier holds, and the tier can
never come to depend on a user interface.

The client is a **consumer of the published HTTP surface**, not a second
frontend inside the tier. It talks to the Python backend over
`java.net.http` — the same routes any other consumer would use — and not
through the NDJSON bridge: the bridge exists to embed the tier in a Python
process, and this is a separate program talking to a separate process over a
network boundary that already has a contract.

### 20.3 The module

`kotlin/desktop` — Compose Multiplatform **1.12.2**, Kotlin **2.4.21**, and
the Compose compiler plugin at **2.4.21**, because the compiler plugin must
match the Kotlin version exactly. 30 main source files (~8,600 lines) and 11
test files (~2,800 lines) — 41 files in all under `kotlin/desktop/src`.

| Package | Owns |
| :--- | :--- |
| `lmeditor.desktop` | `Main.kt` — the window and its scope |
| `lmeditor.desktop.net` | `BackendError`, `MultipartBody`, `BackendClient`, `SseClient` |
| `lmeditor.desktop.model` | `StudioModels`, `StudioState`, `StudioController`, `RunReducer` |
| `lmeditor.desktop.render` | `ManuscriptBlocks` — the display renderer |
| `lmeditor.desktop.ui` | `Theme`, `FileDialogs`, and the fourteen remaining screens and panes |

`kotlin/desktop/README.md` is the module's own documentation: how to run it,
what the window does, and the full list of what it borrows from the tier.

### 20.4 The dependency policy, scoped — not broken

The tier's rule was that a module's runtime dependency set is empty, so that
a reader can trace every behaviour to the JDK. A user interface cannot live
inside that rule: a user interface *is* dependencies. So the rule is
**scoped to the tier** and `:desktop` is named as the single exemption.

The scoping is enforced, not just documented:

- `backend/tests/test_kotlin_dependencies.py` reads every tier module's build
  file and fails on any non-project runtime dependency — as before, with the
  tier's own modules as the set.
- A second test, `test_the_client_is_the_only_module_that_may_carry_dependencies`,
  asserts that no tier module depends on `:desktop` and that `:desktop`
  depends on `:core`. It fails if the client is renamed, so the exemption
  cannot drift away from the module it names.
- The Google Maven repository — Compose's AndroidX transitives live there,
  not on Maven Central — is declared **on `:desktop` alone**. The tier still
  resolves against Maven Central only.

`kotlin/CONTRACTS.md` §1 now reads "every **tier** module", with §1.1
naming the exemption and saying what it does and does not cover.

### 20.5 The backend payloads had to become canonical first

The backend's SSE payloads carried a second, client-shaped vocabulary that
only the deleted Dart client understood: `_chunk_payload` sent `id`,
`content` and `sha256`; `_perspective_payload` sent `avatar_symbol` and
`is_selected`; `_outline_payload` sent `id`, `level`, `section_goal`,
`subsections` and `is_completed`.

That vocabulary was a third spelling of a shape the tier already had. The
tier's own decoder, `outlineNodeFromWire`, reads `node_id`, `depth`,
`description` and `children` — and `jsonString("node_id")` on a payload that
only has `id` is a throw, not a default. A client written against the tier's
decoders could not have read the old payloads.

So the aliases were **dropped, not translated**. The payloads are now
canonical, and the client decodes them with the tier's own decoders. The
alternative — keeping the aliases and adding a second decoder — would have
kept exactly the drift class this repository has spent two passes removing.

The dead `_ROLE_AVATARS` constant went with them, and
`backend/tests/test_workflow_e2e.py` was updated to the canonical shape.

### 20.6 The client reuses the tier rather than reimplementing it

This is the part that matters, because it is the difference between a client
and a fourth copy of the domain.

| The client uses | From |
| :--- | :--- |
| `Json.parseObject` / `write` | `:core` — the tier's hand-written codec |
| `sourceChunkFromWire`, `claimFromWire`, `citationLineageFromWire`, `sectionDraftFromWire`, `documentDraftFromWire`, `verificationScorecardFromWire`, `outlineNodeFromWire`, `outlineNodeInputFromWire`, `perspectiveFromWire`, `workflowEventFromWire`, `inlineCitationFromWire` | `:core` — the tier's decoders |
| `Citations.CITATION_PATTERN` | `:core` — the inline-citation grammar |
| `EventType`, `WorkflowStep`, `EntailmentStatus`, `PerspectiveRole`, `OutlineNodeInput` | `:core` — the vocabulary |
| `LMeditorError` | `:core` — the tier's typed errors |

The client therefore **cannot disagree** with the backend about what a
payload means: if the two ever drifted, the client's decoder would throw
rather than render a plausible lie.

The tier gained exactly one public API in this pass:
`Citations.chunkIdsOf(body)`, which extracts the chunk identifiers a body
cites. The client needed it to turn a rendered citation chip back into the
chunk it points at, and it was needed *by two callers*, so it belongs in the
tier rather than in the client.

One rule was deliberately **not** moved into the client: the display
renderer. Rendering a body into headings, paragraphs, emphasis and citation
chips is a view concern, and no second tier needs it. But it does not
reimplement the citation grammar — it reuses `Citations.CITATION_PATTERN`,
so the client and the tier agree on where a citation begins and ends.

### 20.7 The shape of the window

One window, 1480×940, three panes:

- **Left (340dp)** — three tabs over the material and the run's thinking:
  *Material* (sources, ingest, selection), *Discovery* (perspectives and
  research notes), *Outline* (the operator's outline, editable before the
  run).
- **Centre** — the manuscript. The widest pane, because it is the thing
  being read. It streams in during the run and shows the finished document
  after it, with inline citations rendered as clickable chips that select
  the claim they belong to.
- **Right (380dp)** — the verifier's report: claims, their entailment
  status, the scorecard, and the passage a claim is checked against.

Above them, a connection bar (the backend URL, the health pill, the topic
field, Write, Export) and the review-gate banner when a run is waiting for
an answer. Below them, a status strip carrying the run's state, its metrics
and any error.

The health pill distinguishes four states, because they mean different
things to an operator: *not checked*, *up, cannot write* (the server is
reachable but has no model), *ready (offline stub)* (the stub drafter is
loaded and can write) and *ready*.

### 20.8 The two rules the client owns

The client is not allowed to invent domain rules, but it does own two pieces
of pure logic, and both are pure functions with tests:

- **`RunReducer.reduce(state, event)`** — the whole event-to-state reduction.
  Pure, total and **idempotent**: replaying an event produces the same
  state, which is what makes a dropped SSE frame survivable. The controller
  is a thin door onto it; the window never reduces anything itself.
- **`ManuscriptBlocks`** — body text to display blocks. It reuses the tier's
  citation pattern and owns nothing else.

### 20.9 The client, the backend, and the seam

The client speaks **36** HTTP routes plus one SSE stream — 37 endpoints in
all — in seven groups:

- **health** — `GET /api/v1/health`
- **sources** — `GET /api/v1/sources`, `POST /api/v1/sources/ingest`,
  `POST /api/v1/sources/url`
- **documents** — `POST /api/v1/documents/generate`,
  `GET /api/v1/documents/{doc_id}`,
  `GET /api/v1/documents/{doc_id}/export.docx`
- **workflow** — `GET /api/v1/workflow/{doc_id}/status`,
  `POST /api/v1/workflow/{doc_id}/approve`
- **library** — `GET /api/v1/library/books`,
  `POST /api/v1/library/books/import-docx`,
  `GET /api/v1/library/books/{book_id}`,
  `GET` and `PUT`
  `/api/v1/library/books/{book_id}/sections/{section_id}`,
  `GET /api/v1/library/books/{book_id}/export.docx`
- **vault** — `POST /api/v1/vault/open`, `GET /api/v1/vault`,
  `GET /api/v1/vault/notes`, `GET`, `PUT`, `POST` and `DELETE`
  `/api/v1/vault/note`, `POST /api/v1/vault/note/rename`,
  `GET /api/v1/vault/search`, `GET /api/v1/vault/tags`
- **notebooks** — `GET` and `POST /api/v1/notebooks`,
  `POST /api/v1/notebooks/open`, `GET`, `PUT` and `DELETE`
  `/api/v1/notebooks/{notebook_id}`, `POST` and `DELETE`
  `/api/v1/notebooks/{notebook_id}/sources`, `POST` and `DELETE`
  `/api/v1/notebooks/{notebook_id}/notes`,
  `POST /api/v1/notebooks/{notebook_id}/ask`

and one SSE stream, `GET /api/v1/stream/{doc_id}`.

Failures are typed, never swallowed. `BackendError` is a sealed hierarchy —
`Unreachable`, `Refused`, `Malformed` — and the controller records the
message in the state, where the operator sees it, rather than turning a
failure into an empty result that would look like success.

### 20.10 The bug the live run found

The live run found a crash that no unit test would have found, and it is
worth recording because the shape of the mistake is a trap worth naming.

**Clicking "Export .docx" killed the window.** The trace was:

```
java.lang.Error: Cannot call invokeAndWait from the event dispatcher thread
    at java.awt.EventQueue.invokeAndWait(EventQueue.java:1294)
    at lmeditor.desktop.ui.FileDialogs.onEventThread(FileDialogs.kt:67)
```

`FileDialogs` opened its dialogs with `EventQueue.invokeAndWait`, because a
Swing component belongs on the AWT event dispatch thread. But **Compose
Desktop delivers a click on that same thread**, so the call was always made
from the thread it was trying to reach. `invokeAndWait` throws `Error` — not
`Exception` — when it is called from the event dispatcher, and an `Error` on
the AWT event queue thread takes the whole event queue down. The window
vanished.

The fix is one line of intent: check the thread first.

```kotlin
if (EventQueue.isDispatchThread()) {
    return block()
}
```

`FileDialogsTest` pins it. The test calls the action from inside
`EventQueue.invokeAndWait` — that is, from the event thread — and asserts it
does not fail; and it calls it from another thread and asserts the action
still lands on the event thread. The first test was confirmed to **fail**
against the old code and pass against the new one, which is the only way to
know a regression test is not merely decorative.

The general lesson, recorded so it is not relearned: in a Compose Desktop
application, the UI thread *is* the AWT event dispatch thread. Any helper
that says "run this on the EDT" must ask which thread it is already on.

### 20.11 Verification

- **Kotlin:** `gradlew build` over all 13 modules — **BUILD SUCCESSFUL**; 762
  tier tests and 27 client tests, 0 failures. The client tests cover the
  renderer, the multipart body builder, the event reduction and the file
  dialog's thread rule.
- **Backend:** the full suite is green — **1285 passed** — including the new
  `backend/tests/test_wire_contract.py` (11 tests) and the new
  dependency-boundary test.
- **Live, end to end:** the backend was started with the offline stub drafter
  (`LMEDITOR_LM_PROVIDER=stub`) on `127.0.0.1:8765` and the client was run
  against it with `LMEDITOR_BACKEND_URL`. The window opens, reads the
  backend's health and shows *ready (offline stub)* — the client is talking
  to the server, not to a mock.

  A source was then ingested, selected, given a topic and written. The
  manuscript streamed in with three numbered sections, each carrying a
  clickable citation chip; the grounding pane reported precision 1.00,
  recall 1.00 and three entailed claims; the status strip read *completed ·
  macro_polish · 37 token(s) · grounding 1.00*. Clicking a citation chip
  resolved it to the source passage it points at. The *Discovery* tab
  rendered the run's perspectives, and the *Outline* tab rendered the tree
  the run produced — both decoded with the tier's own decoders.

  **Export** was exercised too, because it is the one path that leaves the
  application: the save dialog opened, a target was chosen, and the client
  wrote a 5,260-byte `.docx` — a valid OOXML package whose `word/document.xml`
  carries the topic, the three section headings and their citation markers.
  The status strip confirmed it: *Exported 5260 bytes to …*.

  `kotlin/desktop/docs/studio-window.png` is that window;
  `studio-discovery.png` and `studio-outline.png` are the other two tabs.

### 20.12 Known limits after this pass

1. **The book library and inline editing are not in the window.** They are
   deferred, not absent from the backend. A studio that grows them will
   need a different navigation shape than three tabs.
2. **The client is one operator, one window.** There is no multi-user
   story, no session persistence, and the state dies with the process.
3. **The run is driven from the backend, not the client.** The client starts
   a run and watches it; it does not own the workflow. That is deliberate —
   the workflow lives in the tier — but it means the window cannot resume a
   run it did not start, except by re-reading the document.
4. **The verification pane is read-only.** It shows claims and their status;
   it does not yet let the operator reject a claim and re-run.
5. **The end-to-end proof is manual.** The client has unit tests, and the
   whole pipeline — ingest, write, verify, export — has been driven through
   the real window against a live backend. But it was driven by hand: there
   is no automated test that opens the window and asserts what it shows. The
   crash in §20.10 is exactly the kind of defect only a click finds, and
   the same kind will escape again.

## 21. Kotlin desktop reliability and Flutter retirement cleanup — 2026-10-25

The supported client is `kotlin/desktop`. The Flutter `client/` tree was
already deleted when this pass began; no Dart sources, pubspec files or
Flutter project metadata remain on disk. Current README and report annotations
now name the Compose replacement. Historical audit and plan bodies retain their
original paths as migration evidence.

### 21.1 Connection and stream lifecycle

`HttpTransport` validates backend URLs before they become active connection
state, encodes document identifiers as single path segments, and awaits JDK
HTTP futures with coroutine cancellation. Network failures use the existing
`BackendError.Unreachable`; plain-text refusals preserve their HTTP status.
`SseClient` joins multiline data fields and closes response bodies on refusal,
normal completion and cancellation, including cancellation during a blocked
read. The deterministic tier gains no dependencies.

`StudioController` captures each connection's clients and owns its request jobs.
Switching connections cancels old work, clears server-owned document state, and
prevents obsolete completions from publishing into the new connection. Stream
failures and premature EOF become visible errors; file export runs on IO and
reports filesystem errors. Network work stays outside retryable state transforms.
`RunReducer` ignores other-document events and clears review gates on terminal
frames, including rejection/failure; successful completion clears stale errors.

`ConnectionBar` holds a local editable backend URL draft. Apply validates and
commits the address. Check, Refresh sources, Write and Export are disabled while
the draft differs from the active address, so editing cannot silently send those
actions to an unintended backend.

### 21.2 File responsibility ledger for this pass

Paths below `net`, `model` and `ui` are rooted at
`kotlin/desktop/src/main/kotlin/lmeditor/desktop/`; test paths are rooted at
`kotlin/desktop/src/test/kotlin/lmeditor/desktop/`.

| File | Single responsibility |
| --- | --- |
| `net/HttpTransport.kt` (new) | Backend URL validation, safe path segments and cancellable HTTP future/resource ownership. |
| `net/BackendClient.kt` | REST request execution and contextual response/failure decoding. |
| `net/SseClient.kt` | SSE frame assembly and stream body lifecycle. |
| `model/StudioController.kt` | Connection-scoped request/stream orchestration and observable task results. |
| `model/RunReducer.kt` | Pure event-to-state transitions with document isolation and terminal review cleanup. |
| `ui/ConnectionBar.kt` | Backend draft editing, explicit Apply and connection action availability. |
| `net/BackendClientTest.kt` (new test) | HTTP validation, failure classification, encoded identifiers and cancellation regressions; in-memory HTTP fixtures. |
| `net/SseClientTest.kt` (new test) | Multiline framing, malformed/refused stream resource cleanup and blocked-reader cancellation proof. |
| `model/StudioControllerTest.kt` (new test) | Connection isolation, visible stream/EOF/export failures and successful terminal completion proof. |
| `model/RunReducerTest.kt` | Event reduction regression proof, including cross-document and terminal transitions. |
| `docs/kotlin-cleanup-blueprint.md` (new, repository root) | Acceptance conditions, delegated file ownership and verification record for this pass. |
| `kotlin/README.md` | Tier/module build guidance and the desktop dependency exemption. |
| `kotlin/desktop/README.md` | Desktop operation, including backend address Apply. |
| `backend/README.md` | Backend operation and Python/Kotlin ownership guidance. |
| `GAP_ANALYSIS.md`, `CODEBASE_REPORT.md` | Historical findings with accurate present-day replacement annotations. |

### 21.3 Verification and limits

- Offline Gradle `build` succeeds across all 13 modules. JUnit XML reports total
  **807 tests, zero failures and zero errors** (762 tier tests, 45 desktop tests).
- Backend dependency policy, wire vocabulary and server stream contract suites:
  **44 passed**.
- Test fixtures exercise in-memory JDK HTTP futures and controlled streams;
  no new runtime or test dependency was added.
- The Compose URL Apply change compiles with the client; this pass did not
  repeat the manual live-window walkthrough recorded in §20.
- Build/cache/temp outputs stayed inside `.tools/` and module build directories.
  Existing unrelated working-tree changes were preserved.

## 22. Word processor presentation (2026-10-25)

The Compose desktop client now centers the manuscript on a bounded paper canvas
with a visual ruler, generous margins and local reading controls. Evergreen
accents, Georgia manuscript type and Candara chrome resolve locally without
network fonts. Light and dark system themes remain supported.

Research and verification are collapsible inspectors. Focus hides both; compact
windows show one inspector at a time. Citation activation explicitly reveals
review, including repeated activation of the same citation. Reading preferences
and scroll position survive compact inspector navigation through a saveable state
holder. Reading font and size affect presentation only; the manuscript/export
contract is unchanged.

The document header retains topic, generation and export actions. Connection
settings expand separately; the URL Apply safeguards remain in force, with a
visible pending-change explanation even when settings are collapsed.

### File responsibility ledger

UI paths are rooted at `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/`.

| File | Single responsibility |
| --- | --- |
| `StudioScreen.kt` | Responsive workspace, inspector visibility, saved editor presentation and visible run status. |
| `ConnectionBar.kt` | Document actions and explicitly applied connection configuration. |
| `EditorPane.kt` | Paper presentation, local reading settings and citation-aware manuscript rendering. |
| `DocumentRuler.kt` (new) | Scaled visual page ruler whose margin bands align with the document insets. |
| `Theme.kt` | Shared desktop color palettes and manuscript/chrome typography. |
| `kotlin/desktop/README.md` (repository root) | Operator guidance for document controls and optional inspectors. |
| `docs/word-processor-ui-blueprint.md` (new, repository root) | Planned ownership, design scope and verifiable acceptance conditions. |

### Verification

Offline `:desktop:build` passes with **45 desktop tests, zero failures/errors**.
The running desktop window was visually inspected in dark mode, including the
paper/ruler, research sidebar, Focus and reading-size controls. The local backend
was unavailable, so this pass did not repeat live generation/export verification.
No dependencies or backend/domain rules were changed.

## 23. Codex generation provider (2026-10-25)

The Python model tier supports `LMEDITOR_LM_PROVIDER=codex` (alias `codex-cli`).
`CodexLM` adapts DSPy text conversations to the installed CLI's supported
non-interactive `codex exec` interface. Authentication stays with the signed-in
CLI; the adapter never reads or copies credentials. `default` omits an explicit
model argument; an available model can be selected with `LMEDITOR_LM_MODEL`.

Each request uses stdin, ephemeral read-only execution, disabled shell/browsing,
and project-local temporary output/state beneath `.tools/codex-generation`.
Unrelated user CLI configuration is ignored. Inputs and final responses have
1 MiB limits; the default per-call timeout is 180 seconds (configurable from
1 to 600). Failures are contextual domain errors. The token budget is a prompt
hint because Codex exec does not expose a hard token cap. Async cancellation
does not immediately terminate the worker; the same subprocess timeout applies.

| File | Single responsibility |
| --- | --- |
| `backend/src/lmeditor/llm/codex.py` (new) | Bounded DSPy-to-Codex text adapter and contextual invocation errors. |
| `backend/src/lmeditor/llm/config.py` | Provider selection, model defaults and refusal of unused HTTP credentials for Codex. |
| `backend/src/lmeditor/llm/factory.py` | Codex adapter bootstrap and honest provider health reporting. |
| `backend/tests/test_codex_provider.py` (new) | Offline authentication boundary, CLI arguments, formatting, limits and failure regression proof. |
| `backend/README.md` | Authenticated Codex startup and configuration guidance. |
| `docs/codex-generation-blueprint.md` (new) | Scope, delegated ownership and acceptance conditions for this integration. |

### Observed verification

- 29 new adapter tests plus 8 existing provider tests: **37 passed**.
- Strict targeted mypy for the adapter: **passed**.
- A live DSPy Predict request through the actual CLI returned the expected
  sentinel; the CLI default resolved to `gpt-6.1-sol` in the smoke call.
- Live `scripts/verify_end_to_end.py --live --no-spawn --timeout 600` passed
  every check against `http://127.0.0.1:8000` with provider `codex`, `is_stub=false`.
- Run `doc_9aadcac584dd` ingested 3 passages and completed research, drafting,
  revision and verification with 3 verified sections, 3/3 supported final claims,
  F-beta 1.0, no ungrounded/contradicted final claims and `workflow_complete`.
- Export was a valid 5,219-byte Word package. Saved manuscript JSON/Markdown,
  event replay, verification JSON and `.docx` live in the ignored local evidence
  folder `.tools/codex-generation-test/`.
- Desktop Connection Check now displays `ready`; Refresh sources loads the
  live server's test source. The server remains running at the default address.
- This is a small synthetic-source integration test, not a broad quality or
  factuality evaluation. No dependency or deterministic tier rule was changed.

## 24. The Documents workspace -- the Word half of the replacement (2026-10-25)

The application is to replace Microsoft Office, Obsidian and Open Notebook. This
section records the first of those three: the window can now open a Word
document, read it, edit it, save it, and write it back to disk.

The backend already carried the whole Word half. It reads a `.docx` into a
*book* of editable sections, keeps the operator's own text per section, and
renders the book back out as a `.docx` with the document's tables, images,
notes, styles and tracked changes intact. None of it was reachable: the desktop
client could export a document it had written, but it could not open one, and
the studio was the only workspace in the window.

The window is now one application with two rooms in it. A quiet **workspace
rail** sits above them, in the studio's own idiom, and the operator moves
between **Write** -- the AI-grounded manuscript studio that was already there --
and **Documents**, the word processor. The Documents workspace opens a `.docx`
through a file dialog, lists the books the server holds and the sections each
one is made of, edits the open section on the same bounded paper canvas the
studio uses, saves it, and writes the whole book back out as a `.docx`.

The server's address belongs to the window rather than to any one workspace,
so it was lifted out of the studio's connection drawer into one shared control
that both workspaces show. The draft of an address stays with the workspace
that shows it, because the studio's "an address has been typed but not applied"
notice depends on knowing it.

### The export defect this work uncovered

Writing the live round trip down exposed a real loss of data. When a section's
text was edited, the exporter threw that section's original blocks away and
re-rendered the section from the new text alone. A table rendered as
pipe-separated text, so the re-render turned it back into a paragraph; an image
rendered as nothing at all, so it was never represented in the text and was
removed with it; and the document's `Title`-styled heading came back as
`Heading1`. Measured on a fixture that carries a table and an image:
tables 1 -> 0, drawings 1 -> 0, paragraphs 13 -> 9.

The exporter now rebuilds an edited section instead of discarding it. The
heading is the origin's own heading block, so its style survives; a block that
renders to no text -- an image, a page break -- is kept where it was, because
the text editor never saw it and so cannot have removed it; a table whose text
the operator did not change is restored with its borders, widths and cells; a
table whose text the operator did change is rebuilt as a table rather than as
prose; and the prose is substituted in place, paragraph for paragraph. The
parity harness does not cover book export, so the frozen Python witness was
left alone and the live Python door inherits the fix through the seam.

### File responsibility ledger

UI paths are rooted at `kotlin/desktop/src/main/kotlin/lmeditor/desktop/`;
test paths at `kotlin/desktop/src/test/kotlin/lmeditor/desktop/`.

| File | Single responsibility |
| --- | --- |
| `ui/LMeditorScreen.kt` (new) | The window: the workspace rail and the switch between the workspaces. |
| `ui/DocumentsScreen.kt` (new) | The documents workspace: library column, section editor, actions and status strip. |
| `ui/BackendAddressField.kt` (new) | The backend address, shared by every workspace, applied rather than typed-and-hoped-for. |
| `ui/ConnectionBar.kt` | Document actions and explicitly applied connection configuration. |
| `model/DocumentWorkspace.kt` (new) | The documents workspace's state: the books, the open book, the open section, the buffer and the dirty mark. |
| `model/StudioState.kt` | The studio's state, holding the documents workspace. |
| `model/StudioController.kt` | The document operations: open, read, edit, save, export and close. |
| `net/BackendClient.kt` | The six library calls, decoded with the tier's own decoders. |
| `net/HttpTransport.kt` | The transport helpers, including the one-path-segment encoder. |
| `core/WireDecode.kt` | The wire decoders, including the saved-section result. |
| `kotlin/library/BookExporter.kt` | The book exporter: an edited section is rebuilt, not discarded. |
| `backend/scripts/verify_documents_roundtrip.py` (new) | The live proof that a `.docx` survives a round trip through the backend. |
| `backend/tests/fixtures/roundtrip_fixture.docx` (new) | A document with a heading, a table and an image, for that proof. |
| `docs/documents-workspace-blueprint.md` (new) | The scope, the delegated ownership and the acceptance conditions. |
| `docs/vault-workspace-blueprint.md` (new) | The planned Obsidian half: a vault of markdown files with wiki-links, tags, backlinks and search. |

### Observed verification

- Offline `kotlin/` build: **839 tests, zero failures, zero skipped** across
  the thirteen tier modules and the desktop client. The desktop client alone:
  69 tests, up from 45.
- Backend suite: **1314 passed**.
- Live round trip against a running backend, through the real HTTP surface:
  every check passed. The script now edits the section that *holds* the table
  and the image, and the export keeps every cell of that table and its
  drawing, with the paragraph count unchanged at 13 against the original's 13.
- The exporter's own tests in `kotlin/library` cover the table, the image,
  the page break, the heading style, the changed table and the unedited
  section: 56 tests in the module, zero failures.
- The pre-existing test `document identifiers cannot alter request paths` had
  never run: JUnit 5 skips a test method that does not return `void`, and
  the assertion returned the caught exception. It runs now, and the new
  book-and-section path test runs with it.
- An image that sits *inside* a paragraph that also has text is still lost with
  that paragraph. It is a documented limit of a section-level text editor, not
  a silent one.
## 25. The Vault workspace -- the Obsidian half of the replacement (2026-10-25)

The application is to replace Microsoft Office, Obsidian and Open Notebook.
This section records the second of those three.

An Obsidian vault is **a folder of markdown files on the operator's disk**, and
the whole value of Obsidian is that the folder stays a folder: it opens in
Obsidian, it opens in any text editor, it is greppable, and nothing about it is
locked inside a proprietary database. A replacement that kept the notes in a
private format would not be a replacement, it would be a second application
with the same job. So the vault here is a real folder: a note is a real
`.md` file, a wiki-link is a real wiki-link, and a tag is a real tag that
grep finds.

The window gains a **Vault** workspace beside **Write** and **Documents**. It
points the server at the operator's own folder, lists the notes in it, opens
one, edits its markdown, saves it back to the same file, searches, and shows
the note's tags, the links it holds and the backlinks that point at it.

### The rules the tier owns

The rules of a folder of markdown files live in a new tier module `:vault`,
with an **empty runtime dependency set**: the JDK only, as every tier module
has.

- **Path safety.** A note id is a relative path inside the vault, with `/`
  separators and no `.md`. It is refused when it is blank, when it carries
  a `.` or `..` segment, when it carries an absolute prefix, a drive
  letter or a UNC prefix, when it carries a character a Windows path forbids,
  or when the *resolved* path leaves the *resolved* root. Symlinked entries are
  skipped when listing and refused when writing, and hidden entries are
  skipped, so Obsidian's own `.obsidian` folder is never read.
- **The markdown grammar.** The frontmatter's `title:` and `tags:`; the
  wiki-links `[[Target]]`, `[[Target|alias]]` and `[[Target#Heading]]`;
  and the tags, where a `#` that starts a heading, that follows a word
  character or that is part of a URL fragment is not a tag.
- **Resolution as Obsidian resolves it.** A link names a **file name** first,
  then a full path, then a note's title, so `[[note]]` finds
  `folder/note.md`. A note that already opens with a heading is not given a
  second one: the title is only put in front of content that does not name
  itself.
- **The search.** A title hit outranks a tag hit, which outranks a body hit;
  a note carrying every term outranks one carrying some; and the order is the
  same every time.

### The seam and the server

The seam serves eight `vault.*` methods, each handed the vault's root,
because the tier keeps no state: the server holds the one open vault and
hands it over on every call. The Python side is a door over the seam, and it
publishes ten routes under `/api/v1/vault`. A note id is a path, and a path
in a URL is a path in a route, so **a note id never goes in the route**: it
goes in the query or the body.

Every route but `open` answers **409** when no vault is open. A client is
never told an empty vault when it was told nothing.

### File responsibility ledger

UI paths are rooted at `kotlin/desktop/src/main/kotlin/lmeditor/desktop/`;
test paths at `kotlin/desktop/src/test/kotlin/lmeditor/desktop/`.

| File | Single responsibility |
| --- | --- |
| `kotlin/vault/VaultPath.kt` (new) | The path rules: what a note id may be, and how it resolves inside the vault. |
| `kotlin/vault/Markdown.kt` (new) | The markdown grammar: frontmatter, headings, wiki-links and tags. |
| `kotlin/vault/VaultNote.kt` (new) | The tier's own parsed-note values. |
| `kotlin/vault/VaultStore.kt` (new) | The on-disk store: list, read, write, create, delete, rename. |
| `kotlin/vault/VaultSearch.kt` (new) | The ranked search over the vault. |
| `kotlin/bridge/HandlersVault.kt` (new) | The `vault.*` methods of the seam. |
| `backend/src/lmeditor/tier/vault.py` (new) | The Python door over the seam. |
| `backend/src/lmeditor/vault/__init__.py` (new) | The API the rest of the backend calls. |
| `backend/src/lmeditor/server/vault.py` (new) | The one active vault the server holds. |
| `backend/src/lmeditor/server/routes.py` | The `/api/v1/vault` routes. |
| `desktop/model/VaultWorkspace.kt` (new) | The vault's state: the notes, the open note, the buffer and the dirty mark. |
| `desktop/ui/VaultScreen.kt` (new) | The vault workspace: search, notes, tags, the editor and the graph. |
| `desktop/ui/FileDialogs.kt` | Choosing a folder, which an AWT dialog cannot do. |
| `core/VaultSchemas.kt` (new) | The vault's wire schemas, shared by the tier and the client. |
| `backend/scripts/verify_vault_roundtrip.py` (new) | The live proof that a vault survives a round trip through the backend. |
| `docs/vault-workspace-blueprint.md` (new) | The scope, the delegated ownership and the acceptance conditions. |
| `docs/notebook-workspace-blueprint.md` (new) | The planned Open Notebook half: notebooks of sources, and Ask with citations. |

### Observed verification

- Offline `kotlin/` build: **914 tests, zero failures, zero skipped** across
  the thirteen tier modules and the desktop client. The new `:vault`
  module holds 47 of them; the desktop client is at 86, up from 69. The
  candidate's total **as of this pass** is **993** tests across the fifteen
  modules `kotlin/settings.gradle.kts` declares.
- Backend suite: **1341 passed**.
- The parity harness: **22 agreed, 0 disagreed, 0 skipped**. The vault's rules
  are not among them: they are new rules, not a port of a Python one, and
  they have no Python ancestor to be compared with.
- `backend/tests/test_kotlin_dependencies.py` discovers the tier's modules
  from the Gradle build, so `:vault` was covered without being named:
  **17 passed**, and the module's runtime dependency set is empty.
- Live round trip against a running backend, through the real HTTP surface:
  **every check passed**. The notes are the files; a title, its tags, its
  links and its backlinks are read as the file holds them; a write reaches
  the file byte for byte; a rename moves the file **and rewrites the link that
  pointed at it**; a delete removes the file; and a note id that tries to
  leave the vault is refused, with nothing outside touched.
- The desktop window was launched with the third workspace on its rail and
  opened without error.

## 26. The Notebook workspace -- the Open Notebook half of the replacement (2026-10-25)

The third of the three applications. The Word half made the app a word
processor; the Obsidian half made it a folder of markdown the operator keeps
and can open in anything. This half makes it an **Open Notebook**: the
local-first notebook of gathered material, written notes beside it, and
questions asked of that material.

A **notebook** is a named collection of sources and notes that lives in a
folder on the operator's disk. It is the unit Open Notebook is built around:
the material the operator gathered (its **sources**), the notes they wrote
beside it (its **notes**), and the questions they asked of it (the **ask**).

The notebook **points at** its sources and its notes; it does not own them.
Removing a source from a notebook leaves the source where it is, because the
source is the operator's and the notebook only borrows it. The same holds for
a note, which is a vault note the notebook names and nothing more.

### The rules the tier owns

The on-disk rules of a notebook live in a new tier module `:notebooks`, with
an **empty runtime dependency set**: the JDK only, as every tier module has.

- **The id rule.** A notebook id is the name of the notebook's own directory
  and it travels in a URL path segment, so it is a **single safe path
  segment**. The rule is the reconciliation of the two rules the tier already
  owns -- the library's `validateIdentifier` and the vault's
  `VaultPath.validateNoteId` -- and the two places they disagree are settled
  for the stricter one: the vault's refusal of every control character wins
  over the library's refusal of NUL alone, and the vault's refusal of a
  trailing dot wins over the library's allowance of one, because Windows
  strips trailing dots from a directory name, so `notes` and `notes.` would
  be one directory under two names and the second id would read the first
  one's manifest. A leading dot is allowed here: a notebooks root is not a
  vault and holds no application folder of its own.
- **Derivation.** A request that names no id gets one derived from the title:
  lower-cased, every run of characters that is not a letter or a digit
  becomes one `-`, the ends trimmed. The derivation is deterministic, so the
  same title always yields the same notebook; a derived id already held by a
  notebook of another title is suffixed `-2`, `-3`, and so on.
- **The manifest.** A notebook is `<root>/<notebook_id>/manifest.json`, and
  the manifest is the JSON of the notebook itself, written atomically the way
  the library writes a book's. `created_at` is set once; `modified_at` moves
  on every write.
- **Idempotence.** Creating a notebook that already exists returns it
  **unchanged** -- the call never overwrites a title the operator wrote.
  Adding a source a notebook already holds changes nothing. A directory with
  no manifest is not a notebook and is skipped; a manifest that cannot be
  parsed is an error, not a silently omitted notebook.

### The ask

The ask is the promise that makes a notebook worth keeping: the operator asks
a question of the material they gathered, and the answer comes from that
material and from nowhere else.

1. The question is answered **only** from the notebook's sources. A passage
   that is not in the notebook is not a passage the answer may lean on,
   however relevant it looks.
2. The passages are ranked with the retrieval the backend already has --
   `chunks_for` the notebook's source ids, `build_provenance_index`, `query`
   at `top_k = 5` -- and the top of that ranking is what the answer is
   written from.
3. The answer is written by a DSPy module whose passage field is named
   `assigned_chunks_text`: the same field name the offline stub reads, so
   the whole thing is provable with no model provider. The stub quotes the
   passage it was given and cites it; with no passage there is nothing to
   ground an answer in, so it answers empty rather than inventing.
4. Every citation names a passage the answer actually used, and quotes it.
   The cited ids are read with the tier's own citation grammar, and a chunk
   id that is not among the retrieved passages is **dropped**, never
   reported: a citation that points at a passage the answer did not use is a
   lie.
5. A citation is **supported** when the answer's own claims that carry it
   are supported by that passage, under the lineage rule the tier already
   owns. A claim the tier cannot support leaves the citation unsupported,
   and an unsupported citation is not reported as supported.
6. `answered` is true when the answer is not empty and at least one
   supported citation survives. When the sources cannot answer the question,
   the answer says so plainly and `answered` is false. **An answer that says
   so is a successful ask; an answer that invents one is not.**

### The seam and the server

The seam serves nine `notebooks.*` methods, each handed the notebooks root,
because the tier keeps no state: the server holds the one folder and hands it
over on every call. The Python side is a door over the seam, and it publishes
eleven routes under `/api/v1/notebooks`.

A **notebook id** is a single safe path segment, so it travels in the route.
A **source id** and a **note id** do not: a note id is a relative path with
`/` separators, and a `%2F` in a route breaks FastAPI's routing, so both
travel in the query or the body. This is the rule the vault already follows,
and it is why the two `DELETE` routes carry their id in the query string.

The notebooks root always exists: it defaults to a folder under the
operator's home, `LMEDITOR_NOTEBOOKS_DIR` overrides it, and
`POST /api/v1/notebooks/open` points it at a folder of the operator's
choosing. There is no `409`: the server is never asked to list notebooks in a
folder it does not have.

### The workspace

The window gains a **Notebook** workspace beside **Write**, **Documents** and
**Vault**. It lists the notebooks, opens one, renames it, holds sources in it
and notes in it, and asks it a question. The answer comes back with the
passages it leaned on, each quoting the passage so the operator can read the
evidence rather than trust it, and each clickable so the passage opens in the
studio's source pane.

An answer the notebook's sources cannot support is shown as **NOT ANSWERED**,
with no citations. A refusal is a refusal, and dressing it up as an answer
would be the one thing this workspace must never do.

### File responsibility ledger

UI paths are rooted at `kotlin/desktop/src/main/kotlin/lmeditor/desktop/`;
test paths at `kotlin/desktop/src/test/kotlin/lmeditor/desktop/`.

| File | Single responsibility |
| --- | --- |
| `kotlin/notebooks/NotebookErrors.kt` (new) | The notebook domain's errors: the base, the missing notebook, the unsafe id. |
| `kotlin/notebooks/NotebookPath.kt` (new) | The id rule: what a notebook id may be, and how one is derived from a title. |
| `kotlin/notebooks/NotebookStore.kt` (new) | The on-disk store: list, read, create, set metadata, add and remove a source and a note, delete. |
| `kotlin/bridge/HandlersNotebooks.kt` (new) | The `notebooks.*` methods of the seam. |
| `backend/src/lmeditor/tier/notebooks.py` (new) | The Python door over the seam. |
| `backend/src/lmeditor/notebooks/__init__.py` (new) | The models and the notebook API the rest of the backend calls. |
| `backend/src/lmeditor/notebooks/answer.py` (new) | The ask: retrieval, the draft, the citations and the grounding check. |
| `backend/src/lmeditor/server/notebooks.py` (new) | The one notebooks folder the server holds. |
| `backend/src/lmeditor/drafting/notebook.py` (new) | The DSPy module that writes the answer. |
| `backend/src/lmeditor/drafting/signatures.py` | The answer's signature: the question, the passages, the answer and the cited ids. |
| `backend/src/lmeditor/llm/stub.py` | The offline double, taught to answer from a passage it was given. |
| `backend/src/lmeditor/core/exceptions.py` | The notebook domain's errors, on the Python side. |
| `backend/src/lmeditor/server/routes.py` | The `/api/v1/notebooks` routes. |
| `desktop/model/NotebookWorkspace.kt` (new) | The notebook's state: the notebooks, the open one, the question, the answer. |
| `desktop/ui/NotebookScreen.kt` (new) | The notebook workspace: the notebooks, their sources and notes, and the ask. |
| `desktop/model/StudioState.kt` | The notebook workspace's state, beside the documents' and the vault's. |
| `desktop/model/StudioController.kt` | The notebook operations. |
| `desktop/net/BackendClient.kt` | The notebook methods of the published HTTP surface. |
| `desktop/ui/LMeditorScreen.kt` | The rail's fourth tab. |
| `core/NotebookSchemas.kt` (new) | The notebook's wire schemas, shared by the tier and the client. |
| `backend/scripts/verify_notebook_roundtrip.py` (new) | The live proof that a notebook survives a round trip and that the ask is answered from the notebook's own sources. |
| `docs/notebook-workspace-blueprint.md` | The scope, the fixed contract and the acceptance conditions. |

### Observed verification

- Offline `kotlin/` build: **977 tests, zero failures, zero errors, zero
  skipped** across the fourteen tier modules and the desktop client, up from
  914. The new `:notebooks` module holds 35 of them; the desktop client is
  at 101, up from 86. The candidate's total **as of this pass** is **993**
  tests across the fifteen modules `kotlin/settings.gradle.kts` declares.
- Backend suite: **1361 passed**, up from 1341.
- The parity harness: **22 agreed, 0 disagreed, 0 skipped**. The notebook's
  rules are not among them: they are new rules, not a port of a Python one,
  and they have no Python ancestor to be compared with.
- `backend/tests/test_kotlin_dependencies.py` discovers the tier's modules
  from the Gradle build, so `:notebooks` was covered without being named:
  **18 passed**, and the module's runtime dependency set is empty.
- Live round trip against a running backend, through the real HTTP surface:
  **every check passed**. The notebook is a directory with a manifest on the
  operator's disk; a title, its sources and its notes follow it; a source the
  notebook does not hold cannot be removed; **the ask is answered from the
  notebook's own sources, with the passage it leaned on quoted**; a source
  **outside** the notebook is never cited; a notebook with no sources says it
  cannot answer; an unsafe notebook id is refused; and the sources outlive
  the notebook that pointed at them.
- The desktop window was launched with the fourth workspace on its rail, and
  the workspace opened without error.

---

## 27. The replacement program's first milestone — M0 (2026-10-26)

### What this pass does

The roadmap named **83** requirements and gave no way to check any of them. An
unchecked box is not a measurement, and roadmap §10's definition of done --
"the behavior is available in the UI when user-facing, persists correctly, has
relevant automated/manual evidence, handles its failure cases, documents
compatibility limits, and is recorded in the architecture ledger" -- cannot be
applied to a list of prose.

This pass is the roadmap's first milestone, M0, and it makes the backlog
checkable:

1. every requirement carries a **stable identity** that survives rewording of
   the roadmap;
2. every requirement has an **owner** -- the tier module that would implement
   the rule -- and an **observable gate** -- the check that decides whether it
   is done;
3. every requirement's status is **evidenced**, not asserted;
4. the one question the roadmap explicitly deferred -- whether a real
   pagination engine can live inside the tier's empty-dependency rule -- is
   **resolved**, with a decision and its cost;
5. the **migration, revision, conflict and recovery contract** that M1 will
   implement is written down.

No runtime behaviour changed. The pass adds documents, one registry, two test
modules, one fixture corpus and one clause to a contract.

### The requirement identity rule

A requirement id is `<TRACK>.<NN>`, where the track names the roadmap section
and the ordinal counts from `01` in document order. The id is written into the
requirement itself, so the roadmap's prose and the registry's facts are joined
by a key rather than by a string comparison.

| Track | Roadmap section | Milestone |
| :--- | :--- | :--- |
| `F` | §3 Shared foundation | M1 |
| `W1`, `W2` | §4 Word, daily replacement | M2 |
| `O1` | §5 Obsidian, daily replacement | M2 |
| `N1` | §6 NotebookLM, daily replacement | M2 |
| `P` | §7 Making them one product | M2 |
| `W3`, `O2`, `N2` | §4–§6, broad replacement | M3 |
| `R` | §10 Release evidence | M4 |

The rule is that the roadmap holds the **requirement**, the registry holds the
**facts about it**, and a test holds them together. A registry entry whose id
is absent from the roadmap is an error; a roadmap requirement with no registry
entry is an error.

### Public contract boundaries established

- **`docs/replacement/requirements.json`** is the registry. Its schema is
  versioned (`schema_version`), and its entry shape is fixed: `id`, `track`,
  `title`, `section`, `stage`, `owner`, `status`, `gate`, `evidence`, `blocks`.
  `owner` is `<tier>:<module>` or `cross:<domain>` -- never free text. `status`
  is one of `not-started`, `in-progress`, `evidence-partial`, `complete`.
- **A status is a claim of a known strength.** `complete` requires all six
  parts of roadmap §10's definition of done; anything short of that is
  `evidence-partial`. `not-started` carries no evidence at all. No requirement
  in the registry claims `complete`.
- **A gate is one observable check**, naming an action and an artifact or
  result. "Works properly" is not a gate.
- **Evidence is a `path:line` reference or a named test.** An entry whose
  status is above `not-started` must carry at least one, and every file
  reference must resolve to a file that exists with the line in range.
- **The compatibility vocabulary is shared** across the inventory and the
  corpus manifest: `read`, `display`, `edit`, `preserve`, `export`, `limited`,
  `unsupported`, `unverified`. "Can parse" is not "can edit"; "preserved" is
  not "editable" and not "renders identically in Word".

### The one contract amendment: `kotlin/CONTRACTS.md` §1.2

The feasibility spike answered the roadmap's §8 dependency question, and the
answer required a contract change, so the contract was amended rather than the
question left open.

**A layout engine is buildable in the tier with an empty third-party runtime
dependency set**, because it needs no library. It is **not** buildable under
`kotlin/CONTRACTS.md` §1 as written, because pagination needs glyph advance
widths, the JDK ships no font data, and §1 neither admits bundled typographic
metrics nor forbids host-font-dependent measurement. Left alone, the engine
would have had to measure text with `java.awt`, which resolves a font from the
host -- so the same document would break into different lines on different
machines, and the tier's output would stop being a function of its input.

§1.2 therefore does exactly two things:

- it admits **one asset category** -- a versioned table of glyph advance
  widths, with the licence notice that travels with it -- as a resource of a
  tier module. The metrics are *data, not a dependency*: they are never
  declared in `build.gradle.kts`, so the tier's runtime dependency set stays
  empty;
- it **prohibits** tier modules from measuring text with `java.desktop`. This
  is §1.1's rule applied to layout: the exemption is granted for the *view*,
  never for the *domain*. The tier computes the geometry; the client draws it.

A font *program* is not admitted. It is needed only for embedded PDF text, and
it is a separate decision with its own licence obligations.

The amendment is **enforced, not conventional**. The class check in
`backend/tests/test_kotlin_dependencies.py` reads only `.class` entries, so a
shipped data file would have passed it unnoticed. Two assertions now close
that gap: one refuses any data file in the tier jar that is not an approved
metric asset, and one requires an approved asset to name its font, licence,
tool and layout profile in its header.

### Dependency direction

Unchanged, and nothing new was added to the dependency graph. The replacement
program's artifacts are documents, a JSON registry, a fixture corpus and two
test modules. The contract amendment **narrows** what a tier module may carry
rather than widening it.

### Files added by this pass

| Path | Responsibility |
| :--- | :--- |
| `docs/replacement/README.md` | The index of the replacement program's artifacts, and how a status is to be read. |
| `docs/replacement/m0-blueprint.md` | The M0 wave contract: requirement identity, the registry schema, file ownership, the evidence rules and the acceptance gate. |
| `docs/replacement/decisions.md` | The decision register: the architecture lead's rulings on the questions the artifacts raised, and what M1 still owes. |
| `docs/replacement/requirements.json` | The machine-readable registry: one entry per requirement, with owner, milestone, status, gate and evidence. |
| `docs/replacement/supported-workflow-matrix.md` | The human-readable cross-product matrix, one row per requirement, consistent with the registry. |
| `docs/replacement/feature-inventory.md` | The DOCX, vault and notebook feature matrices: read, display, edit, preserve, export and the known limitation per feature. |
| `docs/replacement/migration-recovery-contract.md` | The migration, revision, conflict and recovery contract that M1 implements: 115 normative rules. |
| `docs/replacement/editor-layout-feasibility.md` | The feasibility spike for real page layout under the tier's empty-dependency rule, with the decision and its cost. |
| `backend/tests/fixtures/compatibility/` | The representative compatibility corpus and its generator, which is deterministic and writes its own manifest. |
| `backend/tests/test_replacement_requirements.py` | The check that the roadmap and the registry agree, and that every entry is well-formed. |
| `backend/tests/test_compatibility_corpus.py` | The check that the corpus and its manifest agree, and that each fixture behaves as the manifest claims. |

### Files changed by this pass

| Path | Change |
| :--- | :--- |
| `docs/product-replacement-roadmap.md` | Every requirement gained its stable id. The §2 baseline now carries the measured suite result and the second, persistent source store the first pass missed. The F.03 text now states what is atomic today and what is missing. |
| `kotlin/CONTRACTS.md` | §1 gains the text-metrics row of the API table and a new §1.2, the one asset exception. |
| `backend/tests/test_kotlin_dependencies.py` | Two assertions: the tier jar carries no unapproved data file, and an approved metric asset declares its font, licence, tool and layout profile. |

### Observed verification

- **Backend suite: 1409 passed, 0 skipped.** The candidate's total **as of
  this pass** is **1422 passed, 0 failed, 0 skipped**. The pass added 48 tests to a
  suite that measured 1361 before it. The very first measurement of this
  pass reported 1355 passed and 6 skipped; that was an artifact of running
  pytest concurrently with a Gradle build, which made the tier jar
  temporarily unavailable to the bridge. The clean run has no skips. The six
  Office-gated conformance tests in `test_word_chart_conformance.py` run
  rather than skip, because Microsoft Word is installed on this machine -- so
  the Word-verified evidence in §16 and §8 is live on this host and would skip
  on a host without Word.
- **The registry check has teeth.** Eleven mutations of the registry -- a
  dropped requirement, a phantom requirement, an unknown milestone, a free-text
  owner, an empty gate, a status outside the vocabulary, a `complete` status
  with no evidence, evidence pointing at a missing file, evidence pointing past
  the end of a file, a dangling `blocks` reference, and a track that does not
  match its identifier -- were each caught by the suite. The unmutated
  registry passes.
- **All 72 registry evidence citations resolve** to a file that exists with
  the line in range.
- **The metric-asset guard has teeth.** A scratch copy of the tier jar
  carrying one unapproved data file was refused by the new assertion, and the
  real jar was never touched.
- **The feature inventory's counts are exact**: 44 DOCX features, 25 vault
  features and 15 notebook features, counted from the rows themselves.
- **The compatibility corpus is reproducible.** Its generator was run twice
  and the digest set was byte-identical both times; all 20 fixture digests
  recompute correctly, including the vault tree, whose digest covers every
  file's path and its own digest in path order.
- **The corpus check has teeth.** A flipped digest, a dropped manifest entry
  and a claim that the truncated zip is readable were each caught by the
  suite, the last one by the reader actually refusing the package.
- **Every status cell uses the shared vocabulary.** All 100 manifest status
  cells and all 420 inventory status cells are values of
  `m0-blueprint.md` §6 -- none is blank and none is outside the list.
- **The recovery contract's citations are machine-checked**: every `path:line`
  reference in it resolves to an existing file with the line in range.
- **New tests: 48 passing** -- 14 in `test_replacement_requirements.py`, 32 in
  `test_compatibility_corpus.py`, and 2 in the dependency suite, which went
  from 18 to 20.

### The four findings that matter most

1. **Display and Edit are `unsupported` for all 44 DOCX features.**
   `kotlin/word` is a real OOXML subsystem and `kotlin/desktop` never touches
   it: the documents screen edits a section as one string, and the only
   structured renderer parses the studio's *markdown*, not the OOXML model.
   Roadmap §1's "existing rich OOXML serialization does not establish rich
   editing" is now a measured statement rather than a warning.
2. **The book exporter preserves only untouched sections.** A section whose
   text has not changed keeps the blocks it was imported with; a section whose
   text has changed keeps the structure the text editor could not see and
   takes only its prose from the new text. The roadmap's "preserves imported
   structure where supported" is true, and bounded to a line.
3. **The upload store can destroy evidence a notebook was grounded in.** It
   evicts originals by modification time under a byte budget, and eviction does
   not consult notebook membership -- while F.02 requires that material a
   notebook references is not evicted. This is recorded as decision **D-4** in
   the recovery contract, with the recommendation that the notebook reference
   wins.
4. **A vault note with a UTF-8 byte order mark is only partly read.** The
   body arrives, but the mark is not stripped, so the note's frontmatter is
   not recognised, its frontmatter tags are lost and its title falls back to
   its heading. The corpus records this as `limited` rather than as a
   support, and O1.05's "typed frontmatter properties" is affected by it.

### Known limits after this pass

- **M0 is contracts and evidence, not implementation.** No requirement's
  behaviour changed, and none is claimed `complete`.
- **M1 is blocked on three named errors.** The rulings D-1, D-2 and D-3 in
  `docs/replacement/decisions.md` -- `RevisionConflictError`,
  `StorageWriteError` and `IncompleteOperationError` -- are the vocabulary
  every durable write is named in. Nothing that persists a write can be
  implemented or tested until they exist.
- **The registry is a development artifact.** Nothing reads it at runtime yet,
  and if a later milestone needs it at runtime that is a new contract with a
  new schema version, not a quiet change to this one.
- **The recovery contract's normative rules are proposals.** 107 of its 115
  rules are marked `[proposed]`; eight are marked `[current]`. Its retention
  numbers and its twelve open decisions are awaiting the architecture lead's
  confirmation.
- **The layout decision constrains the layout profile.** Under §1.2 the
  layout is LMeditor's own and reproducible on every machine; Word-identical
  pagination, complex-script shaping, kerning, multi-column text and PDF with
  arbitrary embedded fonts are permanently out of scope unless a different
  decision is taken.
- **The feasibility spike verified the JDK's capabilities but not every
  licence.** The redistribution terms of any metric set other than a SIL Open
  Font Licence release were not verified, because no network was used.
- **The desktop application was not launched** in this pass, and the
  compatibility evidence here is about the package and the model, not about
  what an operator sees on screen.

## 28. Persistent notebook source records — first M1 slice (2026-10-26)

Normal backend source calls now select a disk-backed Kotlin source archive.
Extracted passages, offsets, omissions, source metadata and bibliography survive
a fresh JVM/backend process. The memory bound of 256 entries no longer deletes
archived evidence. Rootless direct bridge consumers retain the historical bounded
memory registry for compatibility.

`LMEDITOR_SOURCES_DIR` selects an absolute root; the default is the project's
`backend/.lmeditor/sources`, excluded from Git. Archive records use SHA-256 filenames
and schema version `1.0.0`. Writes flush a unique sibling temporary file before
atomic replacement. Readers reject symlink records, invalid identities, corrupt
records and unknown schemas; writers refuse to overwrite those corrupt/newer
records. Explicit removal deletes the archived record. Removing notebook membership
leaves the archived source available.

Notebook Ask now resolves every source before retrieval, uses the resolved chunk
snapshot and refuses the question if any source is missing. Missing source IDs are
included in the visible error and its contextual domain details. A source that
cannot be read is an error rather than an empty result.

### File responsibility ledger

| File | Single responsibility |
| --- | --- |
| `docs/source-persistence-blueprint.md` | Scope, interface and parallel file ownership for this implementation slice |
| `kotlin/core/src/main/kotlin/lmeditor/core/StorageErrors.kt` | Shared M1 storage, revision-conflict and incomplete-operation error vocabulary |
| `kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt` | Versioned extracted-source records and validated atomic archive I/O |
| `kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt` | Source registry with optional archive backing and bounded memory cache |
| `kotlin/bridge/src/main/kotlin/lmeditor/bridge/HandlersServer.kt` | Source bridge dispatch and absolute archive-root selection |
| `backend/src/lmeditor/tier/server.py` | Root configuration and thin source bridge calls |
| `backend/src/lmeditor/server/sources.py` | Python stored-source facade and wire conversion |
| `backend/src/lmeditor/core/exceptions.py`, `core/__init__.py` | Python storage error twins and exports |
| `backend/src/lmeditor/bridge/client.py` | Rehydrate storage errors into Python domain types |
| `backend/src/lmeditor/notebooks/answer.py` | Source-scoped model orchestration with explicit missing-evidence refusal |
| `kotlin/server/src/test/kotlin/lmeditor/server/SourceArchiveTest.kt` | Reconstruction, eviction, corruption, safe IDs, symlinks and failed-write regression proof |
| `backend/tests/test_source_persistence.py` | Real fresh-JVM, facade and notebook-route restart acceptance tests |
| `backend/tests/test_storage_errors.py` | Cross-tier error identity and contextual payload tests |
| `backend/tests/conftest.py` | Isolate archive data per test; required live-tier gates fail rather than skip |
| `backend/tests/test_tier_server_sources.py` | Durable-retention expectations alongside preserved legacy witness behavior |
| `backend/tests/test_notebook_routes.py` | Notebook route behavior including partial/all missing-source refusal |

### Acceptance and limits

The targeted `:server:test`, `:bridge:test` and `:bridge:tierJar` tasks passed:
91 Kotlin tests, including all 16 new archive regressions, with no test skips.
The focused Python gate passed 96 tests with no skips, exercising a fresh bridge
process, normal facade restart, notebook Ask after restart, missing-source failures,
source retention, bridge behavior and the requirement registry. The commands were:

```text
gradle :server:test :bridge:test :bridge:tierJar --offline --no-daemon
python -m pytest tests/test_storage_errors.py tests/test_source_persistence.py
  tests/test_tier_server_sources.py tests/test_notebook_routes.py
  tests/test_bridge_client.py tests/test_replacement_requirements.py
  tests/test_ingestion_source_store.py tests/test_completed_document_surfaces.py -q
```

The Python filenames above form one command; line breaks are for readability.
Local JVM loopback initialization needed execution outside the restricted network
sandbox; the initial sandbox failure was an environment failure, not a passing gate.

F.02 and N1.01 remain **in progress**. This archive does not retain original
upload bytes, source revision history or persistent retrieval indexes. The desktop
was not launched, and existing older memory-only records require reingestion.
Directory fsync/power-loss durability, revision preconditions and operation journals
remain outstanding; their shared error vocabulary is now available. Existing
upload-store retention behavior is unchanged. Archive-backed reads currently
decode records from disk to expose external changes/corruption; large-corpus
indexing and performance remain separate work. No new runtime dependency was added.

---

## 29. Ledger synchronisation — the candidate as it stands (2026-10-27)

This section records the shape of the candidate tree, for the reader who has to
know what exists rather than what was decided. Everything below was measured
against the tree, not inferred from a plan.

### 29.1 What the candidate adds

**The Kotlin tier** (`kotlin/`) is fifteen Gradle modules — fourteen tier
modules and one module that is not a tier module:

| Module | Single responsibility |
| :--- | :--- |
| `:core` | The typed error hierarchy, the domain enums and their wire strings, the schema data classes, the inline-citation grammar, the operator-edit rule, the shared decompression bound and the native JSON codec. Depends on nothing. |
| `:text` | Sentence-aware chunking, token-span lineage, claim segmentation, the citation precision/recall audit and the retrieval rule — BM25 Okapi, the dense cosine index and their rank fusion. |
| `:word` | The OOXML document model, the `.docx` reader, the byte-deterministic writer and the manuscript builder. |
| `:pdf` | PDF text and metadata extraction, written against the JDK's `Inflater`. |
| `:ingest` | Source loaders, the SSRF policy, the bounded HTTP reads, the upload store, the bibliography resolver chain and the ingestion service. |
| `:library` | The on-disk book store, the bounded digest of a book and the bounded working set. |
| `:workflow` | The workflow state, its reducers and the conditional routing rules. |
| `:editing` | The acceptance rule for a grounded inline revision. |
| `:factcheck` | The aggregation of claims, evidence and scores into a verdict. |
| `:discovery` | Perspective generation, outline construction and the research dialogue. |
| `:server` | The session broker, the source library, the book registry and the versioned source archive. |
| `:vault` | The rules of a vault: the safe note ids, the markdown grammar, the frontmatter, the backlink index, the search order and the store. |
| `:notebooks` | The rules of a notebook: the safe notebook ids, the manifest, the counts, the ordering, the idempotence of a write and the membership of a source or a note. |
| `:bridge` | The NDJSON stdio seam the Python tier calls into. |
| `:desktop` | The operator window. **Not a tier module**: it depends on `:core` alone, and no tier module may depend on it. |

**The Compose desktop client** (`kotlin/desktop/`) is 41 Kotlin files under
`kotlin/desktop/src` — 30 of them main sources and 11 tests. `model/` holds the immutable
workspaces and the reducers, `net/` the HTTP and SSE clients, `ui/` the
screens and panes, `render/` the manuscript blocks. It is the only module in
the repository with a third-party runtime dependency set, which is exactly what
makes it the one documented exemption (`kotlin/CONTRACTS.md` §1.1, §18.4).

**The restructured Python backend** (`backend/src/lmeditor/`) has eighteen
packages. The tier's doors live in `lmeditor/tier/` (15 modules: one per
domain, plus the package façade and the process-wide bridge), the pipe client
in `lmeditor/bridge/`, and the frozen witnesses in
`lmeditor/_reference/`. The two workspaces this candidate adds are
`lmeditor/vault/` and `lmeditor/notebooks/`, with their server faces in
`lmeditor/server/vault.py` and `lmeditor/server/notebooks.py`, the Codex
adapter in `lmeditor/llm/codex.py`, the notebook's writer in
`lmeditor/drafting/notebook.py`, and the notebook's Ask in
`lmeditor/notebooks/answer.py`.

**The `_reference/` parity-oracle tree**
(`backend/src/lmeditor/_reference/`) is 35 frozen plain-Python modules — 36
files, including the package's `__init__.py`. They are the pre-tier
implementations the parity harness compares the tier against, answer for
answer, for the 22 rules the harness checks today; the other 13 are declared
in the harness's `REFERENCE_WITNESSES` with a Kotlin counterpart and no
check. They are the yardstick, and their lifetime is the tier's. They are
plain Python and they never call across the NDJSON seam — a witness that
asked the tier what the answer is would agree by construction and prove
nothing. They *are* re-aligned to the tier's semantics when the tier's rule
**deliberately** changes, and every such change is recorded in
`REFERENCE_WITNESSES`: the repair round of 2026-10-27 re-aligned the Word
merge semantics and the source-id policy that way.
`backend/tests/test_parity_reference.py` enforces the plain-Python rule
mechanically. §19.2 recorded the same principle for the Flutter removal: *it
is not drift; it is a record.*

### 29.2 The public contract boundaries

Which layer owns which rule is **§18.2.1**. The summary is:

- **The Kotlin tier owns** every deterministic rule that crosses the NDJSON
  seam. The Python module that used to hold one keeps its public API and
  answers from the tier: it is a *door*.
- **The drift-pinned twins** are the deliberate exceptions to "one
  implementation": the Word whitespace and text-normalisation rules, the Word
  format detection and refusal reason, the compiled inline-citation pattern,
  the section split of an imported document, the sliding-window and semantic
  chunking algorithms, and the byte-offset and token-count helpers. Each has
  one Kotlin implementation and one Python implementation, held together by
  the shared Word corpus or by `backend/scripts/verify_kotlin_parity.py` —
  **except the section split, which neither holds today** (§18.2.1).
- **Two rules have no Kotlin counterpart**: the document-level grounding
  aggregation in `backend/src/lmeditor/workflow/graph.py`, and the session and
  human-in-the-loop broker in `backend/src/lmeditor/server/session.py`.
- **The model-facing half is Python by design**: DSPy, transformers and
  sentence-transformers.

The one thing a reader should take from this section is that the tier's
ownership is scoped to the rules the seam serves, and that the exceptions are
named above rather than left to be discovered.

### 29.3 The dependency direction

The direction is strictly downward and is enforced by Gradle, not by
convention:

| Module | Depends on |
| :--- | :--- |
| `:core` | nothing |
| `:text`, `:pdf`, `:word`, `:workflow`, `:discovery`, `:vault`, `:notebooks` | `:core` |
| `:factcheck`, `:editing` | `:core`, `:text` |
| `:server` | `:core`, `:workflow` |
| `:library` | `:core`, `:text`, `:word` |
| `:ingest` | `:core`, `:text`, `:word`, `:pdf` |
| `:bridge` | every other tier module |
| `:desktop` | `:core` |

Across the process boundary the direction is the same and runs the other way:
the Python tier depends on the Kotlin tier, only over the NDJSON seam, and the
Kotlin tier never depends on Python. The model libraries are Python's, and the
tier is what they are orchestrated around.

### 29.4 The trust boundary of the local backend API

- **The backend is a local service.** The documented invocation
  (`backend/README.md`) starts uvicorn without a `--host`, and uvicorn's
  default host is `127.0.0.1`; the desktop client connects to
  `http://127.0.0.1:<port>`. The service is therefore bound to the loopback
  interface by default.
- **Its CORS policy is a named allowlist, and a foreign origin cannot change
  anything on it.** `backend/src/lmeditor/server/app.py` installs
  `CORSMiddleware(allow_origins=allowed_origins(), allow_credentials=False,
  allow_methods=list(_CORS_METHODS), allow_headers=["*"])`. The rules are:
  - `allowed_origins()` holds **only** the origins the operator names in
    `LMEDITOR_ALLOWED_ORIGINS`, and `DEFAULT_ALLOWED_ORIGINS` is **empty** —
    a constant cannot name the port the operator bound the server to;
  - `allow_credentials` is **false** and `allow_methods` is an explicit list;
  - a mutating request from a **foreign** origin is refused with **403** and
    the side effect does not happen — CORS decides what a browser may *read*,
    so the refusal is enforced in the server, not in the browser;
  - the API's own origin is worked out **per request**: the scheme and the
    port must be the API's own, and the request must have been addressed to a
    **loopback name** (`localhost`, `127.0.0.1`, `::1`). A page that reaches
    the API by naming another host — a rebound name — is therefore **not**
    the API's own origin, and its mutations are refused;
  - an API reached at a **non-loopback** address has no own origin at all, so
    a page served from that address must be named in
    `LMEDITOR_ALLOWED_ORIGINS`. **The operator-visible consequence:** serving
    the API on anything but the loopback interface puts every browser page
    behind the allowlist, and an allowlist left empty then refuses every
    mutation a page could make.
- **The ingestion trust boundary itself is enforced in the tier.**
  `assert_public_url` (`backend/src/lmeditor/ingestion/url_policy.py:183`)
  gates every fetch, and `safe_upload_path`
  (`backend/src/lmeditor/ingestion/url_policy.py:129`) proves resolved
  containment before an upload is written. Those are the AUDIT findings 1–3,
  and they stand.

### 29.5 The measured state of the candidate

Measured on 2026-10-27, for the reader who needs a number rather than a
promise:

| Measure | Value |
| :--- | :--- |
| Kotlin tests | **1,059**, 0 failures, 0 skipped, across the fifteen modules `kotlin/settings.gradle.kts` declares — **941** in the fourteen tier modules, **118** in `:desktop` |
| Python tests | **1691 passed, 0 failed, 0 skipped** |
| `mypy --strict src` | clean over **133** source files |
| Parity harness | **25** named checks — **26** results with the frozen-module ledger check — 0 disagreed, 0 skipped |
| Seam methods | **114**, across thirteen domains |
| Tier runtime dependencies | **empty** — every tier module declares only `project(...)`; `:desktop` alone carries Compose |

The Kotlin, mypy, harness and Python figures were measured by running the
suite and the tools in this tree; earlier dated sections quote smaller
totals. Those numbers are the record of the pass that measured them and are
left as they are; this table is the candidate's state.

### 29.6 The exhaustive review of the candidate — 2026-10-27

An **exhaustive** pr-team review ran against this candidate. The mode was
exhaustive — each lane read the tree rather than a diff — and the
**baseline commit** was `ddd6f64c0690a658b976e7de22aeb4d1482baace` on
`main`. **The repository is local-only: there is no GitHub PR and no git
remote**, so the review target was the working tree *against that baseline*
— 131 tracked files changed and 480 untracked files added, as measured on
2026-10-27. Thirty-four repair lanes changed the code; an independent
documentation-truth sweep then found 37 stale or overstated statements,
**four** of them in §29 itself. This subsection is the record of both waves.

**The repairs applied.** One line each, with the area and the behaviour that
changed:

- **The grounding scorecard** — the seam keyed a verdict by the claim's
  *text* alone, so a contradicted claim could inherit an entailed verdict,
  `f_beta` could reach 1.0 and a section could publish as `VERIFIED` without
  reaching the HITL gate. A claim is now identified by its text *and* the
  passage it was judged against.
- **The Word table round trip** — a cell's text was silently deleted on
  read→write→read: the reader's grid cursor advanced by the cell's own
  `gridSpan`, so the cell after a merged origin was built empty.
- **Retrieval** — the corpus was re-shipped and re-tokenised on every query.
  The tier now builds the index **once per corpus**, holds it in a resident
  cache keyed by a content-derived digest
  (`CORPUS_DESCRIPTOR_CHUNK_ID` in `kotlin/text/.../Indexer.kt`), and the
  door names the corpus by that digest, so the corpus travels once, when it
  changes.
- **The local trust boundary** — a wildcard, credentialed CORS on an
  unauthenticated local API became a named allowlist with
  `allow_credentials=False`, an explicit method list, and a **403** refusal
  for a mutating request from a foreign origin (§29.4).
- **The vault and notebook roots** — the process-global, unconfined roots are
  now held to a configured base, and a network share is refused outright.
- **The decompression bound** — the PDF inflate path had no cap at all. The
  bound is now one shared object,
  `kotlin/core/src/main/kotlin/lmeditor/core/DecompressionLimits.kt`:
  `MAX_EXPANDED_BYTES` = 64 MiB and `MAX_COMPRESSION_RATIO` = 200, used by
  the PDF filters and by the `.docx` reader alike. It lives in `:core`
  because `:ingest` depends on `:word` and `:pdf`, so `:ingest` could not
  hold it without a cycle; the values are unchanged and the constant now
  appears once in the Kotlin tree.
- **Outbound HTTP safety** — a CRLF in a path or an authority could split a
  request and inject a header. The tier now refuses control characters,
  Windows reserved device names, and a trailing space or dot.
- **Malformed PDFs** — a raw `ArrayIndexOutOfBoundsException` became a typed
  `SourceLoadError`.
- **Word styles** — the default `Normal` style declared `w:basedOn` pointing
  at itself in every Kotlin-written `.docx`. It no longer does.
- **The desktop verdicts** — "the worst verdict wins" was inverted, because
  `ENTAILED` is ordinal 0. Severity is now ranked by the verdict's name.
- **The desktop `token_delta`** — the reducer accumulated non-idempotently
  against its own contract. It is now idempotent under a suffix rule.
- **Checkout stability** — a new root `.gitattributes` pins the fixtures to
  the bytes they are written with: `*.pdf binary`, `*.docx`, `*.doc`, `*.png`
  and `*.ico` binary, `*.tsv text eol=lf`,
  `kotlin/ingest/src/test/resources/fixtures/ip_policy_reference.tsv text
  eol=crlf`, and `backend/tests/fixtures/compatibility/** -text` **last** —
  so nothing under the digest-pinned compatibility corpus can be
  EOL-converted whatever the rules above it say, and the rule that made every
  `.txt` text cannot corrupt the corpus's renamed `.txt` package.
  `kotlin/.gradle/` is now ignored.
- **The `_reference/` tree in the wheel** — the frozen witnesses are excluded
  from both the wheel and the sdist, and the bridge client's fallback
  classpath names `:vault` and `:notebooks`.
- **The stated invariant** — "every deterministic rule of the backend is
  implemented once, in Kotlin" was false and was restated in nine documents.
  It is now scoped to the rules the seam serves, and §18.2.1 is the
  catalogue of the exceptions.
- **The documentation** — the sweep's 37 statements, the four in §29 among
  them.
- **The replacement registry and its matrix** — `docs/replacement/requirements.json`
  is generated, and `docs/replacement/supported-workflow-matrix.md` is rendered
  from it: *one registry, two renderings of it*. The generator had drifted from
  the tree it describes. Two of its anchors no longer resolved at all — the
  retrieval call they named was replaced during the repair round — so the
  generator could not run. `R.08` and `N1.01` had been corrected in the
  registry by hand and not in the generator, so a regeneration would have
  reverted both. Fifteen requirements' evidence did not match the tree, and
  **fourteen** of those pointers named no rule at all — six blank lines, five
  comments, a package declaration and two docstrings. The generator now resolves
  every anchor, the registry and the matrix are regenerated from it, and the
  matrix is byte-identical to what its own generator renders. The matrix's
  counts were wrong by one — it claimed **47** requirements were `not-started`
  while its own table listed **46** — and both are now the counts the table
  holds.

**The Word twin topology.** `backend/src/lmeditor/word/writer.py` is the
**live door**: it delegates the rendering to the Kotlin tier and keeps a
Python **pre-check grid**, and the grid it measures is the grid the tier
produces. `backend/src/lmeditor/_reference/word_reader.py` and
`_reference/word_writer.py` are the **frozen oracle** the parity harness
measures the tier against. The door's grid measurement now matches the
oracle's and the tier's rule.

**The ragged merged region.** The writer is the reader's layout read
backwards. A merged region covers its origin's columns **less** the columns
the continuation cells inside it cover — the reader read those cells back as
continuations of a region that was that much narrower than its origin — and
those cells are written as ordinary cells inside the region, so the row tiles
the grid instead of falling short of it.

**The findings that were verified and deliberately not repaired.** They are
recorded here so the next engineer knows they exist:

- The PDF `/ToUnicode` CMap is re-inflated per page — about 19.81 MB of
  allocation per page.
- Chunking is quadratic in **both** tiers (exponent 1.94 in Python, 2.03 in
  Kotlin). The shape is the same on both sides, so this is not a regression.
- The seam's response is **5.6×** the request bytes.
- The semantic model's vectors still travel per query — 820 KB at 100 chunks
  and 40.6 MB at 5 000.
- The SSE frames carry no `id:`, the whole replay buffer is resent, the
  per-subscriber queue is unbounded, and there is no cancellation path.
- `compute_chunk_id` is **188×** slower.
- The Kotlin Word reader is **1.9–4.2×** slower than `python-docx`.
- `text.retrieval.rank` needs a contract extension, and a contract change
  needs authorisation.
- The `lmeditor.core` → `lmeditor.tier` import cycle survives, through
  function-local imports.
- **Bearer-token authentication on the local API** and the unauthenticated
  `POST /workflow/{doc_id}/approve` were both open here. They are closed in
  §32; every route under `/api/` now requires a bearer token and the
  desktop client presents one.
- The default vault base is the operator's home directory; a vault on another
  drive needs `LMEDITOR_VAULT_BASE`.
- `fastapi` and `starlette` are unpinned in `backend/pyproject.toml`.
- The tier refuses C0 and DEL but **not** the C1 controls (0x80–0x9F), so a
  source id containing U+0085 still passes.
- **13** frozen modules remain declared-but-uncompared in the parity witness
  ledger: each names its Kotlin counterpart, so the gap has an address, but no
  check compares the two sides. `ingestion_url_policy` was the fourteenth and
  was re-aligned in this round.
- The section-split twin is held by neither a shared corpus nor the harness.
- The `token_delta` whole-sequence replay is not caught: a complete fix needs
  a frame identity published by the backend and carried in `StudioState`.
- A streamed frame identical to the section's current tail is treated as a
  replay.
- A user-supplied Word style can still declare `w:basedOn` pointing at
  itself.
- The POSIX path pattern still covers only a known list of roots — the
  repair lanes did **not** widen it — so a path under a root outside that
  list is not masked. The patterns are
  `backend/src/lmeditor/server/routes_helpers.py:87` and
  `backend/src/lmeditor/server/routes_helpers.py:98`.

**What the review could not observe.** These are the coverage gaps, not the
findings:

- No browser-driven test of the HTTP surface.
- No live UNC or SMB execution.
- No Microsoft Word was available to confirm the self-referential `basedOn`
  rejection against the real application.
- No screen-reader, keyboard or focus trace for the Compose UI. The desktop
  accessibility items are **unproven**, not proven.
- **No CI configuration exists anywhere in the candidate.**
- `ruff` is not a usable gate: the baseline already carries 479 findings over
  `backend/src`.
- The 4 MB sliding-window benchmark was not run — it exceeds the 300 s
  limit.
- `sentence-transformers` is absent, so the dense payload was traced rather
  than measured.
- The platform-independence repairs were proved by relocating `HOME`, **not**
  by running on a Linux or macOS runner.

## 30. Production-readiness follow-up — 2026-10-28

The §29.6 verdict describes the reviewed local candidate. It is not a production
release verdict. An independent follow-up confirmed that the API still accepts
unauthenticated callers without an `Origin` header, workflow approval is not
authenticated, and live SSE subscriber queues remain unbounded. Authentication
must span the server and desktop REST/export/SSE clients; changing origin rules
alone would not close that boundary.

The first follow-up slice makes validation reproducible. Its blueprint is
`docs/production-readiness-blueprint.md`; its operating instructions and ordered
remaining release work are in `docs/production-readiness.md`.

The authentication finding above is closed in §32. It is left here as the
record it was: a dated review of the candidate as it stood on 2026-10-28.

### 30.1 Ownership and single responsibilities

| File | Single responsibility |
| --- | --- |
| `backend/uv.lock` (updated) | Resolve the current Python project's dependencies reproducibly |
| `backend/scripts/verify_release.py` | Run existing candidate checks and reject incomplete evidence |
| `backend/tests/test_release_gate.py` | Inject failures to prove the verification gate cannot silently pass incomplete work |
| `.github/workflows/verify.yml` | Provision Windows/Linux CI environments and retain candidate verification evidence |
| `docs/production-readiness-blueprint.md` | Define acceptance, scope and file ownership for this slice |
| `docs/production-readiness.md` | Describe execution, evidence limits and remaining release work |

The old lock still declared removed `llama-index` dependencies and omitted seven
current direct dependencies. It was regenerated with uv 0.11.16, not edited by
hand: 153 packages resolved, project metadata agrees with the 14 current direct
dependencies and the development extra, and `uv lock --check --offline` passes.
FastAPI 0.141.1 and Starlette 1.6.0 are resolved in the lock. The existing Python
environment was not synchronized or replaced by this repair; a fresh locked
installation remains a separate CI obligation.

### 30.2 Candidate gate

The checked-in gate verifies lock consistency, runs `cleanTest test
:bridge:tierJar --no-build-cache`, requires nonempty passing JUnit evidence from
every declared Kotlin module, checks strict Python typing including itself,
executes the backend suite, reruns parity, and drives the live HTTP document
round trip. Missing reports, inconsistent counts, failures, errors and skips
are failures. The Python checks use the tier jar built by that run, and cannot
inherit an operator's bridge or requirements-registry override. A timeout
terminates the check's process tree; cleanup failures are reported explicitly.

Full mode includes all tests. Explicit `--portable` mode excludes the three
Microsoft Word conformance modules and five exact real semantic/NLI model checks.
It records those exact exclusions in `external-checks.json`, forces model hub
access offline, and retains the zero-skip policy for all selected tests. A
portable pass does not claim the excluded capabilities or production readiness.

Each invocation writes independent logs, JUnit reports, temporary files and
application data under ignored `build/release/run-*`. Configured dependency and
model caches are preserved; default caches live under ignored `build/`.
The POSIX Gradle wrapper is invoked through `sh`, so this Windows-authored tree
does not require a pre-established executable mode to enter the Linux build.

### 30.3 Evidence and limits

A full local run, before the final portable-mode and timeout-cleanup additions,
passed **1,059 Kotlin tests across 15 modules**, **1,708 backend tests**, strict
mypy over **134 source files**, **26 parity comparisons with no skips**, and the
live HTTP document round trip. Evidence is retained locally at
`build/release/run-r7gx0s4j/`. The 1,708 count includes the first 17 gate tests;
it is not a replacement claim about an unchanged historical suite.

The final gate's focused fault-injection suite passed **22 tests**, including a
real parent/descendant timeout cleanup probe; strict typing passed for the script.
An initial sandbox run could not connect to the Gradle daemon and correctly
failed; a subsequent permitted run supplied the successful evidence above.

An empty-cache, offline portable experiment at `build/release/run-kieneju1/`
reported **3 failed, 1,689 passed, 2 deselected**. Two tests instantiated the real
NLI model without a skip guard; the third required semantic ranking of a passage
with no query-token overlap. The logs confirmed missing model weights and hash
fallback. Those three exact model-dependent assertions join the original two
real-model checks in the external manifest; their assertions are unchanged and
remain mandatory in full mode. Generic retrieval and fallback tests stay in the
portable gate. The hash fallback's inability to reproduce that semantic ranking
is an observed quality limitation, not evidence of a regression from this slice.

The final portable gate passed on the settled implementation at
`build/release/run-d8zgldlw/`: **1,059 Kotlin tests**, **1,689 selected backend
tests with no failures or skips**, strict mypy over **134 source files**, **26
agreed parity checks with no skips**, and the live HTTP document round trip.
Five exact model tests were deselected and the three Word modules were excluded;
`external-checks.json` preserves the scope. The 22 gate regression tests are
included in that 1,689 count. This is a portable local pass, not hosted CI proof
or a production release verdict.

The workflow is prepared locally, with read-only repository permissions and
locked installation. **No hosted CI run, clean installation, Linux execution,
deployment, or publishing is claimed.** The old §29.6 statement that there is no
CI configuration is superseded by the new workflow; its other unresolved release
findings remain open. The generators and four auxiliary documentation checkers
under ignored `.tools/` still need promotion to maintained source before all old
handoff checks can be reproduced from a clean checkout.
- The rebinding case was not demonstrated against a live browser.

**Superseded by §31.** The "no hosted CI run" statement above describes the
state on 2026-10-28 and is no longer true: the workflow ran on hosted Windows
and Linux runners on 2026-11-01 and found four real defects. The claim about
deployment and publishing is unchanged.

## 31. The hosted CI runs -- 2026-11-01

Section 30.3 said that no hosted CI run was claimed. That is superseded.
The workflow ran on hosted Windows and Linux runners for the first time,
against the branch that carries the tier migration. It found four defects
that the local Windows runs could not, one per run, and every one of them
was real.

### 31.1 What the hosted runs established

The first run is `Sifr-r/LMeditor` pull request 1, run 41295655441. On both
runners the locked installation succeeded on a clean checkout, the Kotlin
build produced the same **1,059 tests across 15 modules**, and strict
Python typing passed on the Windows runner. The gate's own machinery
worked: the process tree was bounded, the evidence was retained as an
artifact, and the failure was reported rather than swallowed. Both runners
failed, each for its own reason.

The local runs had been on CPython 3.13.9 and on Windows only. The hosted
runners were CPython 3.13.16 on Windows and 3.13.17 on Linux. That
difference is what the next sections are about.

The second run is pull request 2, run 41297804342. The Windows runner
passed the whole gate. The Linux runner got further -- the Kotlin evidence
check accepted the declared skip -- and then failed its type check, which
is the third defect below. The third run reached the backend suite on
Linux, where one fixture test failed, which is the fourth defect below.

Each defect was found by a different runner because each defect was only
reachable from the platform that had never run the gate before.

### 31.2 The witness and the standard library

The Windows runner failed the parity check *the web evidence*: the Python
witness found one result in the page and the Kotlin tier found two. The
harness reported 25 agreed and 1 disagreed, and the stderr carried
`Could not parse the search response: can only join an iterable`.

CPython gave `html.parser.HTMLParser` an internal `_pending` buffer in
3.13.16 and 3.14.6, and joins that buffer in `close()`. The frozen witness
`_DuckDuckGoResultParser` keeps its own state under that same name: the
result that is waiting for its snippet. A subclass that shadows the name
replaces the stdlib's buffer with its own state, and `close()` then joins
the subclass's state, which is not iterable. The parse died with
`TypeError: can only join an iterable` -- after the first result had
already been recorded, so the page yielded one result and lost the second.

The witness caught that exception and returned what it had, which is why
the failure was a disagreement about a count and not a crash.

Reproduced under CPython 3.12.13, 3.13.9, 3.13.13 and 3.14.5 the witness
finds two results; under 3.14.7 it finds one and raises. The local
environment is 3.13.9, so every local run agreed with Kotlin and the
divergence was invisible until a runner used a newer interpreter.

The repair renames the witness's attribute to `_pending_result`. The rule
the witness states does not change: it produces the two results it has
always produced, on every supported interpreter. Only the name of a
private attribute moved, so the rule survives a change in the standard
library it never depended on. The witness is not rewritten to follow
Kotlin -- it is restored to the behaviour it always had.

`test_the_frozen_web_parser_keeps_every_result_it_finds` pins that count
directly, so a future change in the standard library is caught by a unit
test rather than by the parity harness, where it costs a Kotlin build to
see.

### 31.3 The gate and a platform guarantee

The Linux runner failed earlier, in the Kotlin evidence check. The gate
refuses every skipped test, and one Kotlin test skips on Linux.

`SourceArchiveTest`'s *Windows mandatory file lock produces typed write
failure without losing old record* asserts a guarantee only Windows gives:
the tier refuses a write over a locked record only where the lock is
mandatory. The test states that assumption and skips where the guarantee
does not hold. The gate's zero-skip rule rejected the skip, so the gate
could never pass on the Linux runner the workflow declares. The rule was
total where it had to be scoped.

Across all 15 Kotlin modules on the hosted Linux runner there was exactly
one skip; on the Windows runner there were none.

The repair scopes the rule rather than dropping it.
`backend/platform-checks.json` is a checked-in declaration of the tests
that may skip, per platform, named exactly by class and test name. The gate
still refuses every skip it has not been told about, and it now also
refuses a declaration that did not happen -- so a platform cannot declare
a skip and then not have it, and the declaration cannot rot unnoticed. The
effective declaration is copied into the run evidence beside
`external-checks.json`, so the exclusions are visible in the artifact
rather than implicit in the source.

Both behaviours are covered by fault injection in `test_release_gate.py`:
an undeclared skip is rejected, a declared skip that happened is accepted,
and a declared skip that did not happen is rejected.

### 31.4 The type check and the host platform

The Linux runner then failed its strict Python type check, on the gate's own
source: `Module has no attribute "CREATE_NEW_PROCESS_GROUP"`.

The gate starts every check in its own process group so that a timeout can
terminate the check's whole tree. On Windows that is
`subprocess.CREATE_NEW_PROCESS_GROUP`; on POSIX the flag does not exist and
the group is created with `start_new_session` instead. The code was correct
at runtime on both platforms -- the conditional expression never evaluates
the attribute on POSIX -- but `mypy` checks the attribute rather than the
branch, and mypy's default platform is the host it runs on. The gate was
written on Windows, so the check passed there and could not pass on Linux.

A gate that runs on both platforms has to type-check for both. The flag is
now read through `getattr`, which is the Windows value there and zero
everywhere else, and the gate runs its own type check twice: once with
`--platform linux` and once with `--platform win32`. A branch that only one
of the two can reach is now checked rather than assumed, and the defect
that this section describes could not have passed the gate on either runner.

This defect was in the gate before the hosted runs. It was never visible
because the gate had never run on a platform that was not the one it was
written on.

### 31.5 The fixture and the compressor

The Linux runner then failed one backend test:
`test_the_committed_fixture_is_the_ragged_merge_region_package`. The
committed fixture and the builder's output are the same document, and they
are not the same bytes.

The difference is inside the DEFLATE stream, not inside the document: the
first differing byte was at offset 2714 of 3242, and it was `\x00` against
`\x03` -- compressor output, not XML. A DEFLATE stream is the output of
the compressor that produced it, and the stock zlib and the zlib-ng a
Linux runner links against do not produce the same stream for the same
input. The test asserted on the compressed bytes, so it could only pass
where the two compressors agree.

The fixture itself was never wrong. Its members, their order, their
metadata and their contents are the same on every platform: the names are
literals, the timestamp is `_FIXED_TIMESTAMP`, the compression method and
the external attributes are constants, and each payload is a literal string
encoded as UTF-8. Only the compressed form varies.

The comparison is now over the members -- their names, their order, their
metadata and their contents -- and not over the bytes. The drift the test
guards against is still caught: a change to the builder's body, to a part's
name, to the member order or to the timestamp all fail it. What it no
longer asserts is that two compressors agree.

The parity harness already refused to compare two written packages byte for
byte, for this reason and one more -- two different writers lay the same
document out differently -- and reads each package back to compare the
documents. This test was the one place that still compared compressed
bytes.

### 31.6 Evidence

The repaired gate was run against the artifacts the first hosted run
retained. Both are accepted: the Linux evidence with its one declared and
observed skip, and the Windows evidence with none, **1,059 tests** each.

A full local portable gate run then passed end to end: **1,059 Kotlin tests**
across all 15 modules, **1,695 backend tests** in portable mode with no
failures and no skips, strict mypy over **134 source files** for both
platforms, **26** parity comparisons agreed with none disagreed, and the live
HTTP document round trip.

The fourth hosted run, `Sifr-r/LMeditor` pull request 2, run 41299656645,
**passed on both runners**. Every step succeeded on Windows and on Linux:
the clean locked install, the Kotlin build and tests, the strict type check
for both platforms, the portable backend suite, the parity comparison and
the live HTTP round trip.

| | Windows | Linux |
| --- | --- | --- |
| Kotlin tests | 1,059 | 1,059 |
| Kotlin failures / errors | 0 / 0 | 0 / 0 |
| Kotlin skips | 0 | 1 (declared) |
| Backend tests | 1,695 passed | 1,695 passed |
| Python deselected | 5 | 5 |

The two runs differ by exactly one skip, and that skip is the Windows-only
file-lock guarantee, declared in `platform-checks.json` for Linux and
observed in the run. Both runs retained their effective declaration beside
`external-checks.json`, so the exclusions are visible in the artifact rather
than implicit in the source.

The merge commit was then gated on `main` itself -- run 41305519803 -- and
passed there as well, with the same evidence on both runners. The gate is
therefore known to pass on the branch that carries the work, not only on a
pull request against it.

This is a hosted pass of the candidate gate on two platforms. It is not a
release verdict, and it is not a production readiness claim; §31.7 says what
is still open.

### 31.7 What this does not change

The hosted runs are now a fact, but they are not a release verdict. The
§29.6 and §30 findings are untouched by them: at the time of those runs the
API still accepted unauthenticated callers, workflow approval was still
unauthenticated, the live SSE subscriber queues are still unbounded, the
installed-wheel deployment is still unproven, and the desktop accessibility
evidence is still missing. The gate was made able to run on the platforms
it declares; it was not made to certify a product that is not ready.

§32 closes the first of those.

---

## 32. The API credential — 2026-11-02

The first item of the §30 follow-up is closed. Every route under `/api/`
requires a bearer token, and the desktop client presents one. The
authoritative contract is `docs/api-authentication-blueprint.md`; it was
written before either half of the implementation, so that the two could not
drift from each other.

### 32.1 What the finding was

The origin rules refuse a *mutating* request from a page this API does not
serve, and they are a good rule — the origin has to be the API's own,
worked out per request and anchored on the loopback interface. But they
are not authentication. A caller that is not a browser omits `Origin`, and
the request is then answered as if it came from the operator. The desktop
client does exactly that, because it is not a browser.

So the boundary was only as strong as the assumption that nothing else on
the machine speaks HTTP to the loopback interface — an assumption that
stops holding the moment the API is bound to a LAN address, which is
exactly the configuration the origin rules were written to allow.

### 32.2 What was built

**The server.** `backend/src/lmeditor/server/credential.py` is a new
module with one responsibility: it resolves the token, keeps it in the
token file, and checks what a request presents.

| | |
|---|---|
| Where the token comes from | `LMEDITOR_API_TOKEN`, then the token file, then a newly generated one |
| Where the token file is | `<home>/.lmeditor/api-token`, unless `LMEDITOR_API_TOKEN_FILE` names another |
| What a refusal is | `401`, `WWW-Authenticate: Bearer realm="lmeditor"`, `{"detail": "This API requires a bearer token."}` |
| What a refusal says | nothing else — it is identical for a missing credential and a wrong one |
| How the token is compared | constant-time, over digests, so neither the comparison nor the length leaks |
| Where the token is allowed to appear | the `Authorization` header, and nowhere else |

The credential middleware is the **outermost** one, so a request is
*authenticated before it is authorized*: a caller that cannot prove it is
the operator is not told whether the thing it asked for exists, and a
mutating request from a foreign origin is refused by the credential before
the origin rules decide anything about it. Both answers are needed — the
origin rules answer what a *page* may do and cannot answer what a
*process* may do.

The token is settled at startup, so that a server which cannot keep its
token says so by refusing to start rather than by answering 401 for ever.
A token that had to be generated and cannot be written to the token file
**stops the server from starting**: a generated token that cannot be
handed to the client is a server the client cannot talk to.

**The desktop client.** `kotlin/desktop/.../net/ApiCredential.kt` is the
client's half. It resolves the same token, in the same order, and sends it
on every request — the three REST request sites and the SSE stream. A
token that cannot be read is a typed `BackendError.Uncredentialed` naming
the path it looked at, not a crash. The credential is not a data class,
deliberately: a generated `toString` would print the token.

### 32.3 Two decisions the two halves had to agree on

The contract was written first, but two details were not in it, and the
client and the server disagreed about them until they were settled. Both
are now in the blueprint.

- **The operator's token is trimmed.** The blueprint said "used verbatim";
  the server trimmed it, because HTTP trims the whitespace around a header
  value anyway and a token pasted out of a file would otherwise arrive
  with a newline on the end of it. The client now trims it the same way.
- **A named token file replaces the default one** rather than being tried
  after it. The two halves have to name the same file, and a client that
  fell back to the default would offer the server a token the server never
  issued.

The client also refuses a token holding a control character, because a
control character would split the header the token travels in. The server
does not refuse one — it only ever compares the token and never sends it —
so the halves differ here, and they differ on the safe side.

### 32.4 A defect the migration found

`backend/tests/server/test_origin_host_header.py` builds its ASGI scope by
hand, and the scope it builds spells the header `Authorization` with a
capital A. Starlette 1.6 compares raw scope header names exactly as they
arrive and does not lowercase them, so `request.headers.get("authorization")`
did not find a header that was really there.

HTTP header names are case-insensitive, so that was a defect in the
server, not in the test — a caller that wrote `Authorization` would have
been refused for having spelled it properly. The lookup now walks the
scope's headers and matches the name without regard to case, and
`test_a_header_name_is_case_insensitive` in
`backend/tests/test_api_credential.py` pins it. The regression test was
verified to fail without the fix and to pass with it.

The same defect was found in the **origin** rules, where it fails the
other way. `refuse_a_foreign_origin` read the `Origin` header the same
case-sensitive way, so a scope that kept the client's capitalisation would
have left the request looking like one from *no* origin — and the refusal
would not have happened. A header the server cannot find is a header the
server does not act on, and for this one the server does not act on it by
refusing. `backend/tests/server/test_header_casing.py` pins both halves:
a foreign origin spelled `Origin` is still refused, and the API's own
origin spelled `Origin` is still allowed.

This is a hardening of a control that already existed, not a change to
it: on every server that complies with the ASGI specification — which
lowercases the header names it puts in the scope — the behaviour is
identical before and after. It matters for the servers that do not, and
for the tests that write a scope by hand.

### 32.4.1 The live server is a process, not a thread

The test that proves the credential over a real socket runs the server as
a **subprocess**, and the reason is worth recording because it is not
obvious.

The application's lifespan configures the language model on the way up and
releases it on the way down, and DSPy binds its configuration to whichever
thread configures it first: a second `dspy.configure` from another thread
raises `RuntimeError`. A server run in a *thread* of the test process
therefore takes that binding away from every test that runs after it — and
the suite's own `stub_model` fixtures, which call `dspy.configure` from the
main thread, all fail. The symptom is a scatter of setup and teardown
errors across files that have nothing to do with each other.

A subprocess has its own DSPy and its own binding, so the real-HTTP test
cannot reach the rest of the suite. The scripts that drive a live server
already spawned one for this reason; the test now does the same.

### 32.5 What it proves, and what it does not

`backend/tests/test_api_credential.py` is the proof. It walks the routes
the application is actually made of — 43 of them, taken from the
application rather than from a list written out by hand — and shows that
every one of them is refused without the credential. It shows that a
missing credential and a wrong one are refused *identically*, across
eight different spellings of a wrong credential. It shows that the
credential is served when it is right, over a real socket and not only
in-process. It shows the token file is created, is reused across a
restart, is replaced when the operator names a new token, is not rewritten
when it already holds the token, and is private. It shows that a
generated token that cannot be written stops the server, and that a named
token that cannot be written is a warning and not a failure. It shows
that the token appears in no log and in no response body.

What it does **not** prove: the token travels on a plain HTTP connection,
so anything on the path can read it. The API is still meant to be reached
over the loopback interface, or over a tunnel the operator controls. It
does not identify *which* operator presented the token — there is one
operator and one token. And it does not establish production readiness:
the live SSE subscriber queues are still unbounded, the installed-wheel
deployment is still unproven, and the desktop accessibility evidence is
still missing.

### 32.6 The ledger

`docs/replacement/requirements.json` requirement **R.08** ("Authenticated
local gateway") moves from `in-progress` to `complete`. Its gate — refuse
a mutation from a foreign origin, and refuse a call that carries no
installation credential — is now satisfied on both halves, and its
evidence names the lines that satisfy it.

The §29.6 finding list is corrected: *Bearer-token authentication on the
local API is unimplemented* and *`POST /workflow/{doc_id}/approve` is
still unauthenticated* are no longer true, and have been removed from it.
The rest of that list is untouched — the unbounded SSE queues, the
declared-but-uncompared parity witnesses, the C1 control gap, the vault
default, the unpinned dependencies and the rest all stand.

### 32.7 The verification

The local portable gate passes on this tree, and its evidence is kept under
`build/release/run-*`.

| | |
|---|---|
| Kotlin tests | **1,075** across 15 modules, 0 failures, 0 errors, 0 skipped (desktop 134) |
| Strict Python typing | **135 source files**, clean for `--platform linux` and `--platform win32` |
| Backend suite (full) | **1,740 passed**, 0 failed — the 1,719 of before plus the 21 this slice adds |
| Backend suite (portable) | 1,716 passed, 5 deselected, 0 failed |
| Kotlin parity | 26 agreed, 0 disagreed, 0 skipped |
| Live HTTP round trip | every check passed |

The 21 new backend tests are 19 in `backend/tests/test_api_credential.py`
and 2 in `backend/tests/server/test_header_casing.py`. The 16 new Kotlin
tests are 12 in `ApiCredentialTest` and 4 across the client, the stream
and the controller.

Three of the regression tests were verified to **fail without** their fix
and to pass with it, because a test that cannot fail proves nothing:
`test_a_header_name_is_case_insensitive`,
`test_a_foreign_origin_is_refused_however_its_header_is_spelled`, and
`test_a_preflight_is_recognised_whatever_its_header_is_spelled`.

The gate now also names the API's token file among the directories it
isolates, so a run that resolved a credential without naming the file
cannot leave a token in the operator's home directory.

The hosted CI agrees. On PR #4 the gate passed on both platforms:

| Job | Run | Result |
|---|---|---|
| `Verify on ubuntu-latest` | 41393531249 | pass, 6m24s |
| `Verify on windows-latest` | 41393531249 | pass, 7m16s |
| `Verify on ubuntu-latest` | 41393561242 | pass, 5m47s |
| `Verify on windows-latest` | 41393561242 | pass, 5m54s |

Those runs are the first to exercise the credential end to end on Linux,
where the desktop client is not part of the tier but the gateway, the
release scripts and the live HTTP round trip are.

