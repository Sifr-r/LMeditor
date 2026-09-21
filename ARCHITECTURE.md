# LMeditor Architecture & System Ledger (Monorepo)

## 1. Overview
LMeditor is an enterprise-grade LLM writing harness engineered for long-form, structured expository writing anchored strictly to verifiable source passages with mathematically validated, chunk-level inline citations.

### Architectural Tiers:
1. **Client Tier (Flutter / Dart 3):** Native desktop client for Windows, macOS, and Linux with 100% sound null safety, GPU-accelerated text rendering (Canvas/Impeller), custom inline `WidgetSpan` citation chips, and real-time SSE stream consumption without Electron or browser DOM overhead.
2. **Core Agentic Tier (Python 3.13+):** Synthesizes **Stanford STORM** (multi-perspective pre-writing discovery), **DSPy** (declarative signature compilation & runtime assertions), **LangGraph** (cyclic state machine, critique loops & HITL checkpoints), and **LlamaIndex** (metadata-preserving chunk provenance).
3. **Transport Tier (FastAPI SSE):** Server-Sent Events (SSE) and REST gateway streaming token deltas, citation mappings, and NLI verification scorecards.

---

## 2. Monorepo Directory Blueprint

```
d:/LMeditor/
├── ARCHITECTURE.md                  # Foundational architectural ledger and module registry
│
├── backend/                         # Python 3.13+ Agentic Core Harness
│   ├── pyproject.toml               # Python dependencies and packaging configuration (uv/hatch)
│   ├── src/
│   │   └── lmeditor/
│   │       ├── __init__.py
│   │       ├── core/                # Foundational domain models, schemas, and error hierarchies
│   │       │   ├── __init__.py
│   │       │   ├── exceptions.py    # Strict, deterministic domain exceptions
│   │       │   ├── types.py         # Domain type definitions, Enums, and Protocol interfaces
│   │       │   └── schemas.py       # Pydantic v2 models for chunks, outlines, citations, and state
│   │       ├── provenance/          # LlamaIndex-backed chunk indexing and citation lineage
│   │       │   ├── __init__.py
│   │       │   ├── chunker.py       # Semantic and sliding-window chunking with offset tracking
│   │       │   ├── indexer.py       # Hybrid vector (dense) + BM25 (sparse) index with RRF
│   │       │   └── lineage.py       # Cryptographic chunk hashing and token-span attribution
│   │       ├── discovery/           # STORM-inspired multi-perspective pre-writing exploration
│   │       │   ├── __init__.py
│   │       │   ├── perspective.py   # Perspective agent generator (domain expert, skeptic, etc.)
│   │       │   ├── dialogue.py      # Conversational search and Q&A research loop
│   │       │   └── outliner.py      # Hierarchical section outline generator and source assigner
│   │       ├── drafting/            # DSPy-compiled section generation modules
│   │       │   ├── __init__.py
│   │       │   ├── signatures.py    # DSPy declarative signatures (Drafting, Citation Insertion)
│   │       │   ├── modules.py       # Section drafter with inline citation assertions
│   │       │   └── teleprompter.py  # MIPROv2 compilation and metric objective optimization
│   │       ├── verification/        # Formal NLI verification and hallucination auditing
│   │       │   ├── __init__.py
│   │       │   ├── claim_extractor.py # Proposition-level sentence and claim segmentation
│   │       │   ├── nli_evaluator.py   # DeBERTa-v3 / LLM judge entailment scoring engine
│   │       │   └── citation_checker.py # Precision/recall citation fidelity and ungrounded auditor
│   │       ├── workflow/            # LangGraph stateful cyclic orchestration
│   │       │   ├── __init__.py
│   │       │   ├── state.py         # HarnessState definition and reducer functions
│   │       │   ├── nodes.py         # Operational graph nodes (discovery, draft, verify, revise)
│   │       │   ├── edges.py         # Conditional routing rules (entailment thresholds, HITL gates)
│   │       │   └── graph.py         # Compiled LangGraph state machine with checkpointing
│   │       └── server/              # FastAPI streaming SSE gateway
│   │           ├── __init__.py
│   │           ├── app.py           # FastAPI application entrypoint
│   │           └── routes.py        # SSE streaming endpoints and interactive HITL review
│   └── tests/                       # Unit, assertion, and graph test suites
│       ├── test_core.py
│       ├── test_provenance.py
│       ├── test_discovery.py
│       ├── test_drafting.py
│       ├── test_verification.py
│       └── test_workflow.py
│
└── client/                          # Flutter Desktop Application (Dart 3)
    ├── pubspec.yaml                 # Flutter package definitions
    └── lib/
        ├── main.dart                # Desktop application entrypoint
        ├── core/                    # Core foundation
        │   ├── api_client.dart      # SSE stream listener and REST client
        │   ├── theme.dart           # Clean typographic hierarchy for long-form manuscripts
        │   └── models/              # Immutable Dart dataclasses
        │       ├── chunk.dart       # Source chunk model
        │       ├── citation.dart    # Inline citation and token-span lineage model
        │       ├── claim.dart       # Extracted claim with NLI entailment score
        │       └── outline.dart     # Outline node with assigned chunks
        └── features/                # Domain-grouped UI features
            ├── editor/              # Canvas-based rich-text editor
            │   ├── editor_view.dart # Main document view with streaming text
            │   └── citation_badge.dart # Interactive citation chip widget
            ├── discovery/           # STORM perspective explorer
            │   ├── perspective_card.dart # Display card for discovered personas
            │   └── outline_tree_view.dart # Hierarchical outline tree
            └── verification/        # Verification inspector
                ├── verification_panel.dart # Claim-by-claim NLI scorecard
                └── source_chunk_viewer.dart # Side-by-side ground truth passage viewer
```

---

## 3. Module Responsibilities (Single Responsibility Principle)

| Module / File | Responsibility |
| :--- | :--- |
| **Backend Core** | |
| `backend/pyproject.toml` | Modern Python packaging configuration with Hatchling, strict mypy mode, and dependency definitions. |
| `lmeditor/__init__.py` | Top-level package namespace exporting package version. |
| `lmeditor/core/__init__.py` | Core domain facade exporting all exceptions, types, protocols, and schemas. |
| `lmeditor/core/exceptions.py` | Strict, non-silent domain exceptions (`CitationHallucinationError`, `UngroundedClaimError`, `ChunkNotFoundError`, `SpanAlignmentError`, `WorkflowDeadlockError`). |
| `lmeditor/core/types.py` | Domain type definitions, Enums (`EntailmentStatus`, `DocumentStatus`, `PerspectiveRole`), and runtime Protocols (`ChunkerProtocol`, `HybridIndexerProtocol`, etc.). |
| `lmeditor/core/schemas.py` | Immutable Pydantic v2 data models for `SourceChunk`, `Claim`, `CitationLineage`, `OutlineNode`, `SectionDraft`, and `VerificationReport`. |
| `lmeditor/provenance/chunker.py` | Segments source texts, attaches byte/char offsets, assigns SHA-256 identifiers, and maintains sentence boundaries. |
| `lmeditor/provenance/indexer.py` | Builds and queries hybrid sparse (BM25) and dense (vector) indices preserving node metadata. |
| `lmeditor/provenance/lineage.py` | Binds generated token spans back to source chunk IDs with exact character offsets. |
| `lmeditor/discovery/perspective.py` | Synthesizes diverse subject-matter perspectives to avoid premature convergence. |
| `lmeditor/discovery/dialogue.py` | Simulates multi-turn interactive inquiry between perspective agents and retrieval indices. |
| `lmeditor/discovery/outliner.py` | Generates a hierarchical outline tree and statically maps candidate chunks to each section. |
| `lmeditor/drafting/signatures.py` | Defines DSPy input/output typing and field descriptions for section drafting and citation integration. |
| `lmeditor/drafting/modules.py` | Implements the section drafting program equipped with runtime `dspy.Assert` validation constraints. |
| `lmeditor/drafting/teleprompter.py` | Automates prompt optimization via MIPROv2, optimizing for citation precision and macro flow. |
| `lmeditor/verification/claim_extractor.py` | Deconstructs draft sentences into atomic, verifiable factual propositions. |
| `lmeditor/verification/nli_evaluator.py` | Executes premise-hypothesis Natural Language Inference (NLI) scoring against candidate source passages. |
| `lmeditor/verification/citation_checker.py` | Computes mathematical citation precision/recall and flags ungrounded drift. |
| `lmeditor/workflow/state.py` | Defines the central state schema (`HarnessState`) passed along the cyclic state machine. |
| `lmeditor/workflow/nodes.py` | Encapsulates single-step transitions (Retrieve, Outline, Draft, Verify, Revise, Polish, Review). |
| `lmeditor/workflow/edges.py` | Evaluates conditional logic to route between drafting, critique, human sign-off, or completion. |
| `lmeditor/workflow/graph.py` | Assembles the state graph, registers checkpointers, and exposes the run/resume APIs. |
| `lmeditor/server/app.py` | Configures FastAPI application lifecycle, middleware, and dependency injection. |
| `lmeditor/server/routes.py` | Serves REST/SSE endpoints for document generation, real-time draft streaming, and HITL decision inputs. |
| `lmeditor/server/session.py` | Asynchronous in-memory session store and SSE event broker managing client subscriptions, event queuing, and HITL signaling. |
| **Flutter Client** | |
| `client/pubspec.yaml` | Flutter desktop configuration, asset definitions, and package dependencies. |
| `client/lib/main.dart` | Desktop application entrypoint configuring window frame, responsive 3-pane layout (Outline / Editor / Verification), and live stream state bindings. |
| `client/lib/core/api_client.dart` | Streams real-time Server-Sent Events from FastAPI and dispatches updates to client state. |
| `client/lib/core/theme.dart` | Typography and color specifications tailored for long-form expository reading with verified citation highlighting. |
| `client/lib/core/models/chunk.dart` | Sound Dart 3 data model for source chunks with deterministic SHA-256 validation and offset tracking. |
| `client/lib/core/models/citation.dart` | Sound Dart 3 data model for citation token spans and premise attribution. |
| `client/lib/core/models/claim.dart` | Sound Dart 3 data model for propositional claims with NLI entailment scores and verification metrics. |
| `client/lib/core/models/outline.dart` | Sound Dart 3 data model for recursive hierarchical outline trees and assigned chunk mappings. |
| `client/lib/features/editor/editor_view.dart` | Renders the live manuscript using Flutter's native Canvas text engine with interactive citation chips. |
| `client/lib/features/editor/citation_badge.dart` | Clickable inline badge displaying chunk ID, entailment status, and triggering the verification inspector. |
| `client/lib/features/discovery/perspective_card.dart` | Visual card rendering discovered STORM personas with focus queries and inquiry objectives. |
| `client/lib/features/discovery/outline_tree_view.dart` | Visualizes the STORM-generated outline tree, allowing the user to reorder or edit sections. |
| `client/lib/features/verification/verification_panel.dart` | Renders the sentence-by-sentence NLI entailment heatmap, showing premises and source passages. |
| `client/lib/features/verification/source_chunk_viewer.dart` | Side-by-side ground truth passage inspector displaying original source context for cited tokens. |
| **Verification & Test Suites** | |
| `backend/tests/test_core.py` | Unit validation tests for Pydantic v2 schemas, boundary constraints, and domain exception hierarchies. |
| `backend/tests/test_provenance.py` | Unit tests for semantic/sliding-window chunking, SHA-256 determinism, and hybrid dense/sparse indexing. |
| `backend/tests/test_discovery.py` | Validation tests for STORM multi-perspective persona discovery and hierarchical outline generation. |
| `backend/tests/test_drafting.py` | Unit tests for DSPy signatures, SectionDrafter assertion enforcement, and MIPROv2 objective metric. |
| `backend/tests/test_verification.py` | Mathematical tests for claim extraction, P_cite, R_cite, F_beta calculations, and DeBERTa NLI scoring. |
| `backend/tests/test_workflow.py` | Cyclic state graph execution tests, node transitions, redraft loops, and HITL gate verifications. |
| `client/test/models_test.dart` | Comprehensive Dart 3 unit tests for JSON round-trips, null safety, boundary validations, and model equality. |
| `client/test/widget_test.dart` | Widget test verifying responsive 3-pane studio rendering, navigation tree, and verification inspector. |

---

## 4. Production Guardrails & Standards
1. **Input Sanitization & Boundary Validation:** All entry points into Python modules accept validated Pydantic schemas. Dart models enforce 100% sound null safety.
2. **Deterministic Error Handling:** Domain-specific errors halt execution or trigger explicit graph recovery paths; silent failures are prohibited.
3. **Strict Typing:** All Python code must satisfy `mypy --strict`. All Dart code must satisfy Flutter analyze with strict type checks enabled.
4. **Idempotence:** State mutations in the workflow engine are handled via pure reducer functions and versioned checkpoints.
