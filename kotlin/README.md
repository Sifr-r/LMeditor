# The Kotlin tier

The Kotlin tier owns every deterministic rule of the LMeditor backend **that
crosses the seam**, and the Python tier calls it rather than repeating it. The
Python tier keeps what genuinely needs a machine-learning library (DSPy,
LangGraph, sentence-transformers, transformers) and calls into this tier over a
newline-delimited JSON pipe.

The rules the seam does not serve are named rather than implied: a small set of
deliberate Python twins is held to the tier by a shared corpus or by the
parity harness — except the section split, which neither holds today — two
rules have no Kotlin counterpart at all, and the
model-facing half is Python by design. `../ARCHITECTURE.md` §18.2.1 is the
catalogue of them.

See `CONTRACTS.md` for the engineering rules every module here follows, and
`bridge/README.md` for the wire contract between the two tiers.

The operator-facing Compose client lives in `:desktop`, above the tier. See
[`desktop/README.md`](desktop/README.md) for backend setup, running the window
with `gradlew.bat :desktop:run`, and packaging an installer. It replaces the
removed Flutter client and reuses the tier's domain models.

## Building

```bash
cd kotlin
./gradlew build          # compile every module and run every test
./gradlew :core:test     # one module
```

The wrapper fetches its own Gradle distribution, so nothing has to be
installed first. A JDK 17 or newer is required; the build targets Java 17
bytecode.

On Windows:

```bat
cd kotlin
gradlew.bat build
```

## The dependency policy

**The runtime dependency set of every tier module is empty.** JSON is hand-written
in `:core`, XML comes from `javax.xml`, ZIP from `java.util.zip`, HTTP from
`java.net.http`, hashing from `java.security`, dates from `java.time`.

Tier modules declare only the test framework, which never ships with the
application: every tier module's own `build.gradle.kts` declares nothing but
`project(...)` dependencies, and the test framework is added once for every
subproject by the root build. The rule exists so that every behaviour of the
tier can be traced
to this repository or to the JDK -- see `CONTRACTS.md` §1. The `:desktop`
client is the sole exemption: it carries Compose runtime dependencies and
depends on `:core`; no tier module may depend on it (§1.1).

## The modules

| Module | Owns |
| :--- | :--- |
| `:core` | Errors, enums, schemas, the citation grammar, the operator-edit rule, the runtime interfaces, the shared decompression bound, the native JSON codec. Depends on nothing. |
| `:text` | Chunking, lineage, claim extraction, the citation audit, BM25 and hybrid retrieval. |
| `:word` | The OOXML document model, the reader, the byte-deterministic writer, the manuscript builder. |
| `:pdf` | PDF text and metadata extraction, written against the JDK's inflater. |
| `:ingest` | Source loaders, the SSRF policy, the bounded upload store, the bibliography resolvers. |
| `:library` | The on-disk book store, the brief, the working set. |
| `:workflow` | The workflow state and the conditional routing rules. |
| `:editing` | The acceptance rule for a grounded inline revision. |
| `:factcheck` | The aggregation of claims, evidence and scores into a verdict. |
| `:discovery` | Perspectives, outline construction, the research dialogue. |
| `:server` | The session broker, the source library, the book registry. |
| `:vault` | The rules of a vault: the safe note ids, the markdown grammar, the frontmatter, the backlink index, the search order and the store. |
| `:notebooks` | The rules of a notebook: the safe notebook ids, the manifest, the counts, the ordering, the idempotence of a write and the membership of a source or a note. |
| `:bridge` | The NDJSON stdio seam the Python tier calls into. |
| `:desktop` | The operator window: source ingestion, streaming manuscript, review, verification and export, and the vault browsing and notebook threads. Depends on `:core`; outside the tier. |

The dependency direction is strictly downward; `:core` depends on nothing at
all. The graph is declared in each module's `build.gradle.kts` and enforced by
Gradle.

## What stays in Python, and why

DSPy, LangGraph, sentence-transformers embeddings and the transformer-based NLI
evaluator stay in Python because they need their model libraries. Their
deterministic rules -- routing, scoring and assertion checks -- live here, with
the one exception of the document-level grounding aggregation, which has no
Kotlin counterpart at all (`../ARCHITECTURE.md` §18.2.1).
Retrieval itself is in `:text`: BM25 Okapi, TF-hash embeddings, cosine similarity
and Reciprocal Rank Fusion use the JDK. LlamaIndex is not required by this
implementation; see `CONTRACTS.md` §14.

The Python face of this tier is `backend/src/lmeditor/tier/`, and the client
that speaks the pipe is `backend/src/lmeditor/bridge/`.
