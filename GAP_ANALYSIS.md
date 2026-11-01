# LMeditor — Gap Analysis & Change Proposals

> **Superseded in part — 2026-10-25.** This is a dated analysis of the codebase as it stood. The Flutter/Dart desktop client it assumes was **removed**; the Kotlin tier (`kotlin/`) now holds the deterministic rules and the Compose client (`kotlin/desktop/`) provides the document pipeline UI. Proposals naming Dart components must be reassessed against that client and the HTTP contract — `EventType`, the SSE envelope and the routes. The historical findings below describe their original scope. See `ARCHITECTURE.md` §§19–20 for the removal, replacement and remaining UI gaps.

> Date: 2026-09-21 (revised)
> Scope: Codebase as it exists in `D:/LMeditor` (`backend/src/lmeditor/...`, `client/lib/...`, `ARCHITECTURE.md`)
> Purpose: Identify competing/adjacent apps in the grounded long-form writing space, map their feature surfaces, and propose the changes LMeditor would need to close the gaps.

---

## 1. What LMeditor actually is (today)

LMeditor is an **enterprise-grade grounded long-form writing harness**. Its thesis: produce a long manuscript whose every factual claim is anchored to a verifiable source chunk, with NLI-based verification and HITL gates — served over a FastAPI/LangGraph backend. *(As of 2026-10-25 the Kotlin Compose desktop client consumes the HTTP surface for the document pipeline. See `ARCHITECTURE.md` §20.)*

**In code today** (`backend/src/lmeditor/`):

| Subsystem | Status | Notes |
|---|---|---|
| `core/schemas.py`, `core/types.py` | ✅ Done | `SourceChunk`, `OutlineNode`, `SectionDraft`, `WorkflowEvent`, `EventType`, `WorkflowStep`, `EntailmentStatus`, `Perspective`, `DialogueSession`, `ResearchMemory`. |
| `provenance/chunker.py` | ✅ Done | Deterministic SHA-256 chunk IDs, char-offset provenance. |
| `provenance/indexer.py` | ✅ Done (orphan) | **`BM25Index`, `DenseVectorIndex`, `HybridIndexer` with Reciprocal Rank Fusion (RRF)** are implemented and tested (`test_provenance.py` lines 90–167), but **never called from the workflow graph** — the corpus stays unindexed at runtime. |
| `provenance/lineage.py` | ✅ Done | Per-section citation map. |
| `discovery/perspective.py` | ✅ Done (wired) | STORM-style perspectives + persona expansion. |
| `discovery/dialogue.py` | ✅ Done (orphan) | `DialogueLoop`, `conduct_all_dialogues`, `ResearchMemory` aggregation all implemented and tested, but **never called from `nodes.py`** — the conversational research loop is dead code at runtime. The graph jumps from `perspective_discovery_node` straight to `outline_synthesis_node`. |
| `discovery/outliner.py` | ⚠️ Hardcoded | `OutlineGenerator` exists but the `outline_specs` literal is a **fixed 5-section systems-engineering skeleton** (Foundations → Architecture → Critique → Operations → Synthesis) with sub-section keyword lists like `["foundation","invariant","telemetry"]`. Out of the box, LMeditor can only produce technical-systems documents. |
| `drafting/signatures.py`, `drafting/modules.py` | ✅ Done (DSPy) | `SectionDrafter`, `MacroCohesionModule`. |
| `drafting/teleprompter.py` | ⚠️ Stub | `BootstrapFewRand` imported but never exercised in the workflow. |
| `verification/claim_extractor.py` | ✅ Done (regex, **not spaCy**) | The docstring says "spaCy-based proposition extraction" in the older `ARCHITECTURE.md`, but the actual implementation (`claim_extractor.py`) is pure regex with an abbreviation-aware sentence splitter and a hand-rolled conjunction-based proposition deconstructor. **No `spacy` package is in `pyproject.toml`.** |
| `verification/nli_evaluator.py` | ✅ Done | `HeuristicNLIEvaluator` (token overlap + negation-polarity clash) is the default; `CrossEncoderNLIEvaluator` (lazy-loaded `cross-encoder/nli-deberta-v3-small`, ~512-token max input) is opt-in via env. |
| `verification/citation_checker.py` | ✅ Done | Per-claim NLI → `VerificationScorecard` with `precision`, `recall`, `F_{0.5}`. **Multi-premise claims are concatenated as a single space-joined premise string** (line 101), which can silently exceed the cross-encoder's context window. |
| `workflow/graph.py` | ✅ Done (ephemeral state) | The compiled graph uses `MemorySaver()` by default (line 74). Server restart = lost HITL state. |
| `workflow/nodes.py`, `edges.py`, `state.py` | ✅ Done | Linear LangGraph with cyclic refinement edges. |
| `server/app.py`, `routes.py`, `session.py` | ✅ Done | FastAPI + SSE gateway. **In-memory `session_manager`.** The only ingest endpoint (`POST /api/v1/documents/generate`) accepts pre-built `SourceChunk` objects — no PDF/DOCX/URL parsing anywhere in `lmeditor.*`. |
| `client/lib/...` (Flutter) | ✅ Done → **removed 2026-10-25** | Riverpod state, Canvas-based chip renderer, HITL modal, side-by-side verification pane. The files are gone; the server contract they consumed is unchanged and is enforced between the two tiers by `backend/tests/test_wire_contract.py`. |
| `pyproject.toml` deps | `pydantic`, `dspy-ai`, `langgraph`, `llama-index`, `sentence-transformers`, `fastapi`, `uvicorn`, `numpy`. **No `spacy`, no `pypdf`, no `python-docx`, no `playwright`/`trafilatura`.** |

**Tests** (`backend/tests/`): 6 suites, 68 KB total. **No tests for ingestion, retrieval-from-graph, or export** — those modules don't exist.

### The "core loop" of LMeditor today

1. Caller hands the server a list of pre-built `SourceChunk` objects (`routes.py:22`).
2. `ingest_sources_node` chunks any extra `raw_texts` and adds them to the dict (`nodes.py:28`).
3. `perspective_discovery_node` runs STORM perspective generation but does **not** run the implemented `DialogueLoop`.
4. `outline_synthesis_node` runs the **hardcoded 5-section systems outline**.
5. `draft_sections_node` calls DSPy's `SectionDrafter` for each leaf.
6. `verify_citations_node` runs the NLI evaluator; failures cycle back through `critique_and_refine_node`.
7. `hitl_checkpoint_node` pauses if threshold not met.
8. `macro_cohesion_node` writes intro/outro.

**The strongest claim LMeditor can make today** is: *"NLI-verified grounding against a pre-supplied corpus, in a desktop client, with HITL gates."*

**The weakest claims** are: *"no corpus ingestion, no semantic retrieval in production, no live web, no export, no editable prose, no persistence, no multi-domain outlines, no plural citation intents, and the dead-code `DialogueLoop` and orphan `HybridIndexer`."*

---

## 2. The competitive landscape

Six clusters, each with a representative feature LMeditor should consider.

### 2.1 Grounded answer engines

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Perplexity / Perplexity Pages** | Live web RAG with inline citations; "Pages" turns an answer into a shareable long-form doc; export to PDF/Markdown. | Source-fidelity UX (click chip → highlight in source), Pages-as-export, web-corpus path. |
| **You.com / Bing Copilot / ChatGPT Search** | Live web grounded answers with inline citations. | User expectations for what "cited" means — LMeditor's stronger NLI guarantee still has to feel familiar. |

### 2.2 Autonomous Deep Research agents (NEW cluster, 2025/2026)

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **OpenAI Deep Research**, **Gemini Deep Research**, **Perplexity Deep Research**, **Stanford STORM (`stanford-oval/storm`)**, **HuggingFace Open Deep Research** | Multi-step query planning, recursive 20+ page synthesis, 50+ citations, iterative web/corpus exploration. | (1) The product category the field is consolidating on. (2) LMeditor's positioning is the **auditable, NLI-grounded alternative** — these agents do not verify entailment, they cite-and-claim, which is the exact gap LMeditor fills. (3) Adoption of the deep-research **report-style output** (table of contents, in-line numbered references, "Sources" appendix) — LMeditor can ship this *with verified references*, not just asserted ones. |

### 2.3 Smart citation classification (NEW cluster)

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Scite.ai**, **Semantic Scholar**, **Consensus** | Classify each citation as **Supporting / Contrasting / Mentioning**. Aggregate "supporting vs contrasting" counts per claim. | **LMeditor currently treats any NLI `CONTRADICTED` as a failure** (`citation_checker.py:109–110`). In expository writing, citing a counter-argument (*"In contrast, Smith et al. found…") is a **positive citation intent**. This is the dialectic-grounding gap. |

### 2.4 Spatial & visual source anchoring (NEW cluster)

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Heptabase**, **LiquidText**, **MarginNote**, **Readwise Reader** | Click an inline citation chip → opens the source PDF page jumped to the **exact visual bounding box**. | LMeditor's chips today open a side-by-side text snippet. The UX step from "snippet" to "PDF page + bounding box highlight" is what users in 2026 expect. |

### 2.5 Notebook & corpus tools

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **NotebookLM (Google)** | Up to ~50 sources per notebook (PDF, MD, URL, audio, YouTube); chat grounded only to corpus; Audio Overview (podcast); cited inline answers; Studio for notes/outlines/mind maps. | The corpus-ingestion UX is the gold standard. Per-source notebook with pinned citations, chip-to-source jump. |
| **Humata / ChatPDF / AskYourPDF** | Single-PDF chat with cited snippets and page-level provenance. | Per-page pinpoint citation; hover preview; "ask across many PDFs". |
| **AnythingLLM** | Desktop local RAG over your own folder; multi-vector-DB; multi-LLM; "workspaces". | Architecture for **local-first multi-corpus UX**. Closest shape to what LMeditor should aim for, plus verification on top. |

### 2.6 Long-form writing tools

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Lex** | AI-native long-form editor with command palette, inline suggestions, version history, Google Docs–style live cursors, export to DOCX/PDF/Markdown. | The **rich-text editing layer** LMeditor is missing, version history, export pipeline. |
| **Sudowrite / NovelAI** | Long-form fiction AI with chapter memory, style/voice controls. | Cross-section memory / continuity (LMeditor's `MacroCohesionModule` is currently intra-manuscript only). |
| **Notion AI / Coda AI** | AI inside a doc; slash-commands, multi-block selections. | Selection-aware AI, slash-command style invocation. |

### 2.7 Reference managers & academic workflows

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Zotero** (+ plugins: PapersGPT, Aria, Beaver, MCP) | 7.5M-user reference manager; PDF library; **10,000+ CSL citation styles**; browser capture; sync; rich plugin ecosystem (Beaver supports OpenAI-compatible endpoints + Ollama). | **Bibliographic / citation-style output** (APA, MLA, Chicago, IEEE); source capture from browser; library/corpus-as-first-class-object; CSL integration; library-wide semantic search (Beaver's hybrid search). |
| **Elicit / Consensus / ResearchRabbit** | Discovery on top of papers (search 200M+ peer-reviewed works; per-finding citations; bias/effect-size flags). | Domain-vertical ingestion (peer-reviewed PDFs), structured evidence tables, bias/concern flags per finding. |

### 2.8 Verification, eval & observability

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **Patronus AI** | Production LLM eval: hallucination scoring, RAG eval, agent eval. | The "professional" verification framing — scorecards per run, A/B between prompts, regression detection. |
| **LangSmith / Langfuse / HoneyHive / Arize Phoenix** | Trace every LLM call; eval suites; regression dashboards. | Per-step traces of LMeditor's LangGraph nodes; per-stage metrics. |
| **Vellum / Braintrust** | Prompt management + regression. | Prompt registry + versioning for LMeditor's DSPy signatures. |

### 2.9 Local desktop AI harnesses (architectural peers)

| Product | What it does | What LMeditor can learn from it |
|---|---|---|
| **LM Studio / Jan.ai / Ollama + Open WebUI** | Local-first model serving, embedding, RAG. | Local-embedding + local-LLM path so verification doesn't depend on a hosted cross-encoder. |

---

## 3. Capability matrix (LMeditor vs. the field)

Legend: ✅ first-class · 🟡 partial / dev-only / opt-in · ❌ missing · ➖ N/A.

| Capability | LMeditor | Perplexity Pages | NotebookLM | Humata | Lex | Zotero+AI | Patronus | Elicit | Deep Research agents | Scite |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Live web ingestion | ❌ | ✅ | 🟡 URLs only | ❌ | ❌ | ❌ | ➖ | ✅ (papers) | ✅ | 🟡 |
| PDF / DOCX ingestion | ❌ | ❌ | ✅ | ✅ (PDF) | ❌ | ✅ | ➖ | ❌ | ✅ (PDF) | ❌ |
| Multi-document corpus UI | ❌ | 🟡 (Spaces) | ✅ | 🟡 | ❌ | ✅ | ➖ | 🟡 | ✅ | ❌ |
| Hybrid BM25 + dense retrieval (RRF) | 🟡 (orphan code) | ✅ | ✅ | ✅ | ❌ | 🟡 (Beaver) | ➖ | ✅ | ✅ | ✅ |
| STORM-style multi-perspective planning | ✅ | ❌ | 🟡 | ❌ | ❌ | ❌ | ➖ | ❌ | 🟡 | ❌ |
| Co-STORM dialogue loop | 🟡 (orphan code) | ❌ | ❌ | ❌ | ❌ | ❌ | ➖ | ❌ | 🟡 | ❌ |
| Per-claim NLI verification | ✅ | ❌ | 🟡 | 🟡 | ❌ | ❌ | ✅ | 🟡 | ❌ | ❌ |
| Dialectic citation intents (support/contrast) | ❌ | 🟡 | 🟡 | 🟡 | ❌ | 🟡 | 🟡 | ✅ | 🟡 | ✅ |
| Inline citations w/ byte offsets | ✅ | 🟡 | ✅ | ✅ | 🟡 | ✅ | ➖ | ✅ | 🟡 | ✅ |
| **Spatial PDF bounding-box highlighting** | ❌ | 🟡 | 🟡 | ✅ | ❌ | 🟡 | ➖ | ❌ | ❌ | ❌ |
| Rich-text editor (full) | 🟡 | ✅ | ✅ | ❌ | ✅ | ✅ | ➖ | ❌ | 🟡 | ❌ |
| DOCX/PDF export | ❌ | ✅ | 🟡 | ✅ | ✅ | 🟡 | ➖ | ❌ | ✅ | 🟡 |
| Bibliographic citation styles (CSL) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (10k+) | ➖ | ✅ | ❌ | ✅ |
| **"Proof of Grounding" audit dossier export** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| HITL review checkpoints | ✅ | ❌ | ❌ | ❌ | 🟡 | ❌ | ➖ | ❌ | ❌ | ❌ |
| **In-situ steering (regenerate / find-evidence / [@ picker)** | ❌ | 🟡 | 🟡 | ❌ | ❌ | ❌ | ➖ | ❌ | ❌ | ❌ |
| **Sentence-type classifier (factual vs. rhetorical)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 🟡 | ❌ | ❌ | ❌ |
| **Multi-premise NLI aggregation (token-budget aware)** | ❌ | 🟡 | 🟡 | 🟡 | ❌ | ❌ | 🟡 | ❌ | ❌ | ❌ |
| Macro-cohesion drafting | ✅ | 🟡 | 🟡 | ❌ | ❌ | ❌ | ➖ | ❌ | ✅ | ❌ |
| Streaming progress (SSE) | ✅ | ✅ | ✅ | ✅ | 🟡 | ❌ | ➖ | 🟡 | ✅ | ❌ |
| Native desktop client | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ➖ | ❌ | ❌ | ❌ |
| Pluggable LLM providers | 🟡 (DSPy, **never configured**) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Persisted corpus / resume across restart | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ➖ | ❌ | ✅ | ❌ |
| Eval / regression dashboard | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| Air-gapped local profile (zero outbound) | ❌ | ❌ | ❌ | 🟡 | ❌ | ✅ (Beaver + Ollama) | ❌ | ❌ | ❌ | ❌ |
| Source credibility / bias flag | ❌ | 🟡 | ❌ | ❌ | ❌ | ❌ | 🟡 | ✅ | 🟡 | ✅ |
| Long-output memory (multi-chapter) | ❌ | 🟡 | ✅ | ❌ | ❌ | 🟡 | ➖ | ❌ | ✅ | ❌ |

The bottom row of italic-bolded rows is where the new gaps cluster. They are not addressed by any of the categories I surveyed in v1; this revision formalizes them.

---

## 4. Where the field is and LMeditor isn't

### Three fusions the field has consolidated around that LMeditor hasn't

1. **NotebookLM + Lex** — NotebookLM does corpus + grounding; Lex does long-form writing. LMeditor's editorial layer is closer to Lex than NotebookLM, but it has neither NotebookLM's ingestion nor Lex's editor.
2. **Lex + Patronus** — writing UX with per-claim verification + audit dossier.
3. **Zotero + Elicit / Open Deep Research** — academic pipeline with structured evidence tables and 20+ page synthesis with 50+ cited sources.

### What LMeditor uniquely has (and should defend)

- **Deterministic, byte-offset chunk IDs** → provable re-anchoring on revision.
- **NLI verifier that runs as a first-class graph node** (not a post-hoc wrapper).
- **HITL gates inside the workflow**, not bolted on.
- **First-class desktop client** with Canvas-rendered citation chips.
- **STORM/Co-STORM planning** (once the dialogue loop is wired).

### The seven structural gaps the field is exploiting against LMeditor

> Each is restated with a concrete fix that names the files that exist today.

#### Gap 1 — Bibliographic entity model & metadata ingestion
**Problem.** `SourceChunk` (`core/schemas.py:19`) and the v1 `Source` proposal only store `origin`, `title`, `kind`. CSL bibliography export needs structured metadata: `authors`, `year`, `container_title` (journal/book/publisher), `volume`, `issue`, `pages`, `doi`, `url`. Without it, P1.4 (CSL) will produce blank or invalid citations.

**Fix.** Introduce a `BibliographicRecord` model in `core/schemas.py`:
```python
class BibliographicRecord(BaseModel):
    authors: list[str]
    year: int | None
    publication_date: str | None
    container_title: str | None  # journal, book, or publisher
    volume: str | None
    issue: str | None
    pages: str | None
    doi: str | None
    url: str | None
    type: Literal["journal-article", "book", "book-chapter", "conference-paper",
                   "report", "webpage", "preprint", "other"]
    source_resolver: Literal["pdf-header", "crossref", "arxiv", "pubmed",
                              "openalex", "user", "none"]
```
In P0.1 ingestion, attach a `BibliographicRecord` per `Source` via: (a) PDF header parsing (`pypdf` metadata + first-page regex), (b) CrossRef REST API for DOIs, (c) arXiv/PubMed metadata resolvers, (d) OpenAlex lookup, (e) manual author override.

#### Gap 2 — Factual vs. rhetorical sentence classification
**Problem.** `claim_extractor.py:51` treats every sentence as an atomic claim requiring a citation. In expository writing, 25–40 % of sentences are **structural or transitional** (*"In the following section, we examine the latency trade-offs…"*). Uncited transitional sentences get `UNGROUNDED`, destroy `F_{0.5}`, and trap the workflow in cyclic redraft loops or premature HITL halts.

**Fix (P1.5).** Add a `SentenceClassifier` module:
- `backend/src/lmeditor/verification/sentence_classifier.py`
- DSPy signature `ClassifySentenceType` (or a fast zero-shot classifier for low-latency).
- Categories: `FACTUAL_CLAIM` (audited), `RHETORICAL_TRANSITION` (exempt; verified only for macro cohesion), `LOGICAL_SYNTHESIS` / `AUTHOR_HYPOTHESIS` (audited for internal consistency, no citation required).
- `ClaimExtractor.extract_claims` is updated to skip `RHETORICAL_TRANSITION` sentences and tag `AUTHOR_HYPOTHESIS` claims separately.
- New metric in `VerificationScorecard`: `factual_f_beta` (computed only on `FACTUAL_CLAIM`).

#### Gap 3 — Multi-premise NLI aggregation & token-budget guards
**Problem.** `citation_checker.py:101` concatenates multiple premise chunks with a space: `premise_text = " ".join(premises)`. Concatenating 3–4 chunks easily exceeds the ~512-token context window of `cross-encoder/nli-deberta-v3-small` (`nli_evaluator.py:79`), leading to silent truncation or lexical-overlap dilution in heuristic mode.

**Fix (P1.6).** Two complementary strategies:
1. **Sub-claim decomposition** — split the claim into sub-clauses (e.g., by commas / coordinating conjunctions) and evaluate each clause against its attributed premises.
2. **Pairwise premise-claim with reducer** — evaluate `(premise_i, claim)` for each `i ∈ premises`, then aggregate with `max`, `mean`, or `top-k-mean`. New `NLIAggregator` in `verification/nli_aggregator.py`.

Add `max_premise_tokens` config (default 400) with explicit chunk truncation logging in `CitationChecker.audit_claims`.

#### Gap 4 — Document archetypes & dynamic outline templates
**Problem.** `discovery/outliner.py:101` enforces a hardcoded 5-section systems-engineering outline. LMeditor cannot currently write academic, business, or any other domain without rewriting `outliner.py`.

**Fix (P0.5).** Make the outliner template-driven:
- `backend/src/lmeditor/discovery/archetypes.py` — declarative `Archetype` registry.
- Ship archetypes:
  - `Academic_IMRaD` (Introduction, Methods, Results, Discussion)
  - `Systematic_Literature_Review` (Background, Search Strategy, Synthesis of Evidence, Risk of Bias, Gaps)
  - `Executive_Strategy_Brief` (Executive Summary, Core Findings, Strategic Alternatives, Risk Matrix, Recommendations)
  - `Technical_Architecture_Report` (the existing 5-section default)
  - `Dynamic_STORM` (zero-shot hierarchical synthesis derived from discovered perspectives + ingested corpus)
- `OutlineGenerator.__init__(archetype: str = "Technical_Architecture_Report")`; keywords and section descriptions come from the registry. Allow `Dynamic_STORM` to bypass the static skeleton entirely.
- New client dropdown `client/lib/features/outline/archetype_selector.dart`.

#### Gap 5 — "Proof of Grounding" Audit Dossier export
**Problem.** Lex and NotebookLM export standard prose. LMeditor's enterprise differentiator is formal verification, but today there is no way to export the verification evidence. This blocks regulated-industry adoption (legal discovery, FDA filings, patent applications, financial compliance).

**Fix (P1 → bundle into P0.3 export).** Add an **Audit Dossier** export alongside `.docx`/`.pdf`/`.md`:
- A ZIP (or appended-PDF) containing:
  1. The compiled manuscript with linked numeric citations.
  2. **Claim Verification Ledger** — CSV/JSON matrix: every extracted proposition, cited passage quote, char/byte offsets, SHA-256 chunk hash, NLI confidence score, verifier model version, sentence type (`FACTUAL_CLAIM` / `RHETORICAL_TRANSITION` / `AUTHOR_HYPOTHESIS`).
  3. **HITL Decision Log** — timestamped record of human reviews, manual overrides, operator critique notes.
  4. **Source Manifest** — list of `Source` objects with `BibliographicRecord` and `kind`.
  5. **Provenance Trace** — per-node LangGraph trace with timestamps and token counts.
- New module: `backend/src/lmeditor/export/audit_dossier.py`.

#### Gap 6 — Air-gapped / high-security offline profile
**Problem.** Enterprise writing harnesses for proprietary patents, legal strategy, and medical trials cannot transmit text to third-party APIs. LMeditor today has no opinion on this.

**Fix (P0.4 + P1.1).** Formalize an **Air-Gapped Local-First Profile**:
- Embeddings: local `sentence-transformers` (`all-MiniLM-L6-v2` or `bge-small-en-v1.5`).
- NLI verifier: local `cross-encoder/nli-deberta-v3-small` on CPU/ONNX (already supported in `nli_evaluator.py:79`; just need ONNX path).
- LLM drafting: Ollama / vLLM endpoint (`llama3.3:70b`, `qwen2.5:32b`, `mistral-small`).
- Retrieval & state: embedded SQLite + `sqlite-vec` + `SqliteSaver` checkpointer.
- Hard constraint: **0 outbound network requests**; assert at startup that `LMEDITOR_LM_PROVIDER` is in `{ollama, vllm, lmstudio, llama_cpp}`.

#### Gap 7 — Interactive co-editing & in-situ steering primitives
**Problem.** P2.1 in v1 was framed as "make it a real editor" — but missed the **specific HITL primitives** that differentiate LMeditor from a generic text editor.

**Fix (P2.2 — replaces the v1 P2.1).** Ship four primitives:
1. **In-situ section regeneration** — highlight a paragraph, request a redraft with specific feedback. Backend re-runs `verify_citations_node` and emits updated chips.
2. **Grounding on demand** — author writes a claim by hand, highlights it, clicks *"Find evidence in corpus"* → backend runs hybrid retrieval (`HybridIndexer.search`) and attaches verified citation chips as suggestions.
3. **Manual citation picker** — author types `[@`, autocomplete surfaces indexed chunks (`HybridIndexer.query`), manual bind.
4. **Author override gate** — author marks an ungrounded claim as *"Verified (Author Assertion)"* with an audit justification. The override is logged in the HITL decision log and exported in the audit dossier; the per-section threshold is met without lowering the safety bound.

New nodes: `regenerate_section_node`, `ground_author_claim_node`. New client components: `client/lib/features/editor/steering_bar.dart`, `citation_picker_overlay.dart`, `author_override_dialog.dart`.

---

## 5. Prioritized change proposals

Effort is engineering-days for a small focused team. Files that exist today and would be touched are listed inline.

### P0 — Without these, the product is not usable as a long-form writing tool

#### P0.1 Source ingestion (PDF, DOCX, Markdown, HTML, plain text, URL)
- **Why:** Every competitor in §2 ingests something more interesting than `raw_texts: list[str]`.
- **New modules:** `backend/src/lmeditor/ingestion/{__init__,loaders,parsers,registry}.py`.
- **Wraps:** `pypdf`, `python-docx`, `markdownify`, `trafilatura`, `httpx`.
- **Schema additions (in `core/schemas.py`):** `Source` (`id`, `kind`, `origin`, `title`, `added_at`) and `BibliographicRecord` (Gap 1).
- **Metadata resolution:** PDF header extraction + CrossRef/arXiv/PubMed/OpenAlex REST clients in `ingestion/resolvers.py`. Cache resolutions in `storage/`.
- **PDF page tracking:** `SourceChunk` extended with optional `page_number: int | None` and `bbox: tuple[float, float, float, float] | None` for Gap 2.4 spatial anchoring.
- **Graph change:** `workflow/state.py` adds `sources: list[Source]`; `nodes.py` `ingest_sources_node` dispatches to loaders, attaches `BibliographicRecord`, then chunks.
- **Server change:** `routes.py` new endpoints `POST /api/v1/sources/ingest` (multipart) and `POST /api/v1/sources/url`.
- **Client change:** `client/lib/features/sources/source_panel.dart` — drag-and-drop, URL paste, library sidebar.

#### P0.2 Wire the existing `HybridIndexer` (refactored — do **not** rebuild)
- **Why:** `provenance/indexer.py:173` already implements `BM25Index` + `DenseVectorIndex` + `HybridIndexer` with RRF, thoroughly tested in `test_provenance.py:90–167`. The real gap is that **none of this is called from `workflow/nodes.py`**. The graph stores `source_chunks: dict[str, SourceChunk]` but never indexes them.
- **Fix:**
  - In `nodes.py` `ingest_sources_node`, build `HybridIndexer.index(source_chunks.values())` and stash on `state["indexer"]`.
  - In `nodes.py` `draft_sections_node`, before calling the DSPy drafter for a leaf, run `indexer.query(leaf.thesis, top_k=8)` and inject the top-k chunks as premises. This closes the loop on the existing orphan code.
  - **Persist the index on disk** under the doc's storage directory (serialized chunk dictionary + numpy array for vectors, or `sqlite-vec`). Re-load on workflow resume.
  - **Wire `DialogueLoop`** between `perspective_discovery_node` and `outline_synthesis_node`: build the indexer, then call `conduct_all_dialogues(perspectives, indexer, turns_per_session=3)` to populate `ResearchMemory` and feed `outline_synthesis_node` a richer input than the current keyword-overlap chunk assignment.
- **No new retrieval module** — the existing one just needs to be called.

#### P0.3 Export pipeline (DOCX, PDF, Markdown, **Audit Dossier**)
- **Why:** Every long-form competitor ships export. `ARCHITECTURE.md` mentions a "Manuscript Renderer" but `routes.py` returns only the section dump. The audit dossier is LMeditor's enterprise differentiator.
- **New modules:** `backend/src/lmeditor/export/{__init__,markdown,docx,pdf,bibliography,audit_dossier}.py`.
- **Wraps:** `python-docx`, `weasyprint` or `fpdf2`, `citeproc-py` (or `pandoc-citeproc`).
- **Audit dossier:** ZIP package containing the manuscript + Claim Verification Ledger + HITL Decision Log + Source Manifest + Provenance Trace (per Gap 5).
- **Server change:** `routes.py` — `GET /api/v1/documents/{doc_id}/export?format=docx|pdf|md|dossier`.
- **Client change:** `client/lib/features/export/export_sheet.dart`.

#### P0.4 Pluggable LLM providers (with air-gapped profile)
- **Why:** `pyproject.toml` declares `dspy-ai` but nothing in the visible code wires `dspy.configure(lm=...)`. Without this, every user either re-implements it or assumes the dev's local setup.
- **New modules:** `backend/src/lmeditor/llm/{__init__,factory,config}.py`.
- **Env-driven provider dispatch:** `LMEDITOR_LM_PROVIDER ∈ {openai, anthropic, gemini, ollama, vllm, lmstudio, llama_cpp}`. Air-gapped profile asserts the provider is local-only and refuses startup if any cloud endpoint is configured.
- **App change:** `server/app.py` — call `lm_factory.bind()` on startup.
- **Tests:** per-provider factory unit tests; air-gapped startup-assertion test.

#### P0.5 Dynamic outline archetypes (Gap 4)
- **Why:** `outliner.py:101` is hardcoded to a systems engineering outline. No DOM extension points exist.
- **New module:** `backend/src/lmeditor/discovery/archetypes.py`.
- **Archetypes shipped:** `Academic_IMRaD`, `Systematic_Literature_Review`, `Executive_Strategy_Brief`, `Technical_Architecture_Report` (current default), `Dynamic_STORM`.
- **`OutlineGenerator.__init__(archetype, custom_template=None)`.**
- **Graph change:** `nodes.py` `outline_synthesis_node` reads the archetype from `state["archetype"]` (or `GenerateDocumentRequest.archetype`).
- **Client change:** `client/lib/features/outline/archetype_selector.dart`.

---

### P1 — Without these, the product is not competitive on trust/eval

#### P1.1 Persisted corpus & LangGraph resumption
- Replace in-memory `session_manager` with SQLAlchemy models in `backend/src/lmeditor/storage/`.
- Replace ephemeral `MemorySaver` (`graph.py:74`) with `SqliteSaver` so HITL state survives process restart.
- Persist `Source`, `SourceChunk`, `Session`, `Document`, `Scorecard`, `HITLDecision`. Alembic migrations.
- Add `GET /api/v1/documents`, `GET /api/v1/documents/{doc_id}` to the FastAPI router.

#### P1.2 Eval & regression dashboard
- `verification/citation_checker.py` already computes precision/recall/F₀.₅ — surface it.
- `backend/src/lmeditor/eval/{scorecards,regression,cli}.py` — persist per-run scorecards; `lmeditor eval run <doc_id>` and `lmeditor eval compare <run1> <run2>`.
- `client/lib/features/eval/scorecard_view.dart` — render run history with diff highlighting.

#### P1.3 Per-step trace export (LangSmith-compatible)
- `backend/src/lmeditor/observability/__init__.py` — env-based tracer; no-ops when `LANGSMITH_API_KEY` unset.
- Surface trace URL on each `doc_id` in the Flutter client.

#### P1.4 CSL bibliography output
- `backend/src/lmeditor/citation/__init__.py` — wrap `citeproc-py` (or shell `pandoc-citeproc`). Pairs with P0.1's `BibliographicRecord`.
- Render APA, MLA, Chicago, IEEE by default; allow user CSL file upload.
- Chip click in client → formatted citation preview.

#### P1.5 Factual vs. rhetorical sentence classifier (Gap 2)
- New: `backend/src/lmeditor/verification/sentence_classifier.py`.
- DSPy signature `ClassifySentenceType` returning one of `FACTUAL_CLAIM | RHETORICAL_TRANSITION | LOGICAL_SYNTHESIS | AUTHOR_HYPOTHESIS`.
- `ClaimExtractor.extract_claims` skips `RHETORICAL_TRANSITION`; tag `AUTHOR_HYPOTHESIS` separately.
- New metric in `VerificationScorecard`: `factual_f_beta` (over `FACTUAL_CLAIM` only).

#### P1.6 Multi-premise NLI aggregator (Gap 3)
- New: `backend/src/lmeditor/verification/nli_aggregator.py` — pairwise premise-claim evaluation with reducer (`max`, `mean`, `top-k-mean`).
- `CitationChecker.audit_claims` switches from concatenation to aggregator.
- New config: `max_premise_tokens: int = 400` with explicit truncation logging.

#### P1.7 Cross-source contradiction detection
- During `outline_synthesis_node`, run a pairwise contradiction scan across cited chunks for the same leaf section.
- Surface "Source A says X; Source B says Y" callouts in the Flutter verification pane.
- Writes to `VerificationScorecard.cross_source_contradictions: list[CrossSourceConflict]`.

---

### P2 — Without these, the product stays niche

#### P2.1 Bibliographic import from Zotero / DOI / arXiv (Gap 1 hardening)
- Zotero local-API sync, DOI resolver, arXiv API client. Pairs with P0.1 ingestion.

#### P2.2 In-situ steering primitives (Gap 7)
- New nodes: `regenerate_section_node`, `ground_author_claim_node`.
- Client components: `steering_bar.dart`, `citation_picker_overlay.dart`, `author_override_dialog.dart`.
- Big multi-month UX win.

#### P2.3 Spatial PDF anchoring (Gap 2.4)
- Extend `SourceChunk` with `page_number` and `bbox` (already in P0.1 schema).
- Client chip click opens the source PDF page at the bounding box, with highlight.

#### P2.4 Smart citation classification (dialectic intents)
- `verification/citation_intent.py` — classify citations as `SUPPORT | CONTRAST | MENTION | BACKGROUND` (Scite-like).
- `verification/nli_evaluator.py` extended with `CONTRADICTED_AS_CITED` status that does **not** count as a verification failure when the intent is `CONTRAST`.

#### P2.5 Multi-document / notebook model
- "Project = one corpus + many documents". UI reorganization on top of P1.1 persistence.

#### P2.6 Web companion
- Read-only `/view/:doc_id` web page that renders the same chip UI as the desktop client. Share-ability (Perplexity Pages' play).

#### P2.7 Browser extension
- One-click "send this page to LMeditor corpus" (the Zotero Connector play).

#### P2.8 Figure / table generation
- Perplexity Pages-style auto-figures. Heavier and lower-priority.

#### P2.9 Source credibility / bias scoring
- Consensus-style "study type / sample size / concern flags" per chunk. Useful in academic verticals.

---

## 6. Suggested sequencing (90-day plan)

The v1 sequencing was right at the macro level but missed that P0.2 is **wiring**, not building. Updated:

| Weeks | Track | Outputs |
|---|---|---|
| 1–2 | P0.1 ingestion + Gap 1 (BibRecord) | PDF/DOCX/MD/URL. `BibliographicRecord` attached per `Source`. CrossRef/arXiv/OpenAlex resolvers. Client drag-drop. |
| 2–3 | P0.4 pluggable LM + Gap 6 (air-gapped profile) | `LMEDITOR_LM_PROVIDER` env, factory, unit tests for each provider. Air-gapped startup assertion. |
| 3–4 | P0.5 archetype templates + Gap 4 | Refactor `outliner.py` to use archetype registry; ship `Academic_IMRaD`, `Systematic_Literature_Review`, `Executive_Strategy_Brief`, `Dynamic_STORM`. |
| 4–5 | P0.2 wire indexer + dialogue loop | Call `HybridIndexer` from `ingest_sources_node` and `draft_sections_node`; insert `dialogue_research_node`; persist index per doc. |
| 5–6 | P0.3 export + Gap 5 (audit dossier) | DOCX + Markdown + Audit Dossier ZIP. CSL bibliography. |
| 6–7 | P1.1 persistence + P1.3 traces | SQLAlchemy + Alembic + `SqliteSaver`. LangSmith compat. |
| 7–8 | P1.5 sentence classifier + P1.6 multi-premise NLI | Classifier + `factual_f_beta`. Aggregator + `max_premise_tokens`. |
| 8–9 | P1.2 eval dashboard + P1.7 cross-source contradiction | Scorecard store + diff CLI; Flutter scorecard view. |
| 9–12 | P2.2 steering primitives + P2.3 spatial anchoring | Regenerate-section node, find-evidence node, citation picker, author-override gate. PDF bbox highlighting. |

End-state target by week 12: LMeditor is the only **offline desktop** product that ships **NotebookLM-style ingestion + Lex-style editor + Patronus-style verification + Zotero-style citation styles + audit-dossier export**, while running entirely on local models via Ollama/LM Studio if the user wants.

---

## 7. Open questions for the team

1. **Target user.** Academic / professional research writer (Zotero + Elicit lane) or general long-form writer (Lex lane)? Different P0s get dropped or pulled in depending on the answer.
2. **Local-first vs. hosted-first.** P0.4 and P2.x both fork on this. Local-first means sqlite-vec, Ollama, embedded models, no telemetry; hosted-first means easier NLI cross-encoders, multi-device, faster iteration.
3. **Dialectic citation semantics.** Is "Smith says X but in contrast Jones says Y" a verified citation when Jones is the cited source, or an NLI contradiction? (P2.4.)
4. **HITL semantics.** Today it's "accept / edit / retry". Zotero's "save conversation as a note" pattern is missing — should it be added?
5. **Pricing surface.** None of the open questions affect code shape today, but the P1.1 persistence layer is where entitlement/quotas will live.

---

## 8. References

- Stanford STORM / Co-STORM — original multi-perspective synthesis pipeline that LMeditor's `discovery/` reimplements. https://storm.genie.stanford.edu
- Perplexity Pages — long-form export UX pattern. https://www.perplexity.ai/pages
- Google NotebookLM — corpus+citations UX gold standard. https://notebooklm.google.com
- Lex — long-form editor with version history. https://lex.page
- Zotero + AI plugins (Beaver, PapersGPT, Aria) — academic citation patterns. https://www.zotero.org
- Elicit / Consensus — discovery + bias flags. https://elicit.com / https://consensus.app
- Patronus AI — production eval framing. https://www.patronus.ai
- Humata / ChatPDF / AskYourPDF — per-page citation UX.
- LangSmith — observability baseline for LangGraph apps.
- Scite.ai — citation intent classification (Supporting / Contrasting / Mentioning).
- Heptabase / LiquidText / MarginNote / Readwise Reader — spatial PDF anchoring.
- OpenAI Deep Research / Gemini Deep Research / Perplexity Deep Research — autonomous deep-research agents.
- HuggingFace Open Deep Research — open-source deep-research agent.
- BM25Okapi / RRF — sparse+dense retrieval primitives already implemented in `provenance/indexer.py`.
