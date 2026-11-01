# Kotlin Tier — Engineering Contract

This document is binding on every module in the Kotlin tier. It exists so that
twelve modules written in parallel converge on one house style instead of
twelve dialects.

The authority for *what* each rule means is the Python tier it replaces: the
Kotlin implementation must produce the same results, the same wire strings and
the same errors as the Python code it ports. Where this document and the
Python code appear to disagree, the Python code wins and the discrepancy is a
bug to report, not a licence to differ.

---

## 1. Dependency policy (hard rule)

**The runtime dependency set of every tier module is empty.** No third-party
library may be added to a tier module's `build.gradle.kts` other than the test
framework that the root build already declares.

Everything the tier needs comes from the JDK:

| Need | JDK API |
| :--- | :--- |
| JSON | `lmeditor.core.json.Json` (hand-written in this repo) |
| XML | `javax.xml.parsers`, `org.w3c.dom`, `org.xml.sax` |
| ZIP | `java.util.zip.ZipInputStream`, `ZipOutputStream` |
| HTTP | `java.net.http.HttpClient` (JDK 11+) |
| Hashing | `java.security.MessageDigest` |
| Dates | `java.time.Instant`, `java.time.OffsetDateTime` |
| Files | `java.nio.file.*` |
| Text | `java.text.Normalizer`, `java.util.regex.Pattern` |
| Text metrics | `lmeditor.word.TypographicMetrics` (bundled table) — **not** `java.awt.font`; see §1.2 |

If you believe a third-party library is unavoidable, stop and report it
rather than adding it. The whole point of the tier is that a reader can trace
every behaviour to the JDK.

### 1.1 The one exemption: `:desktop`

`:desktop` is **not a tier module**. It is the operator-facing client, and it
is the single, named exception to the rule above. It carries Compose
Multiplatform and the AndroidX transitives Compose pulls in.

The rule is scoped, not broken:

- The exemption is **named and singular**. `:desktop` is the only module in the
  repository permitted a non-empty runtime dependency set. A second exception
  is a policy change for this document, not a local decision.
- `:desktop` is not a tier module, and no tier module may depend on it. The
  dependency arrow points one way only: `desktop -> core`.
- `:desktop` may depend on tier modules; tier modules may not depend on
  `:desktop`, and no tier module gains a dependency because `:desktop` exists.
- The exemption is granted for the **view**, never for the **domain**. A rule
  that decides anything about a document — a citation grammar, a chunk
  boundary, a score, an outline shape — belongs in the tier, and the client
  imports it rather than reimplementing it.
- The Google Maven repository is declared on `:desktop` alone, so the tier
  still resolves against Maven Central only.

`backend/tests/test_kotlin_dependencies.py` enforces this. It asserts that
every tier module has an empty runtime dependency set, and separately asserts
that `:desktop` is the only module allowed to carry dependencies.

### 1.2 The one asset exception: bundled typographic metrics

A tier module may ship **typographic metric assets** — a versioned table of
glyph advance widths, and the licence notice that travels with it — as
resources of the module. This is the only kind of non-code file a tier module
may carry.

- The metrics are **data, not a dependency**. They are declared in the
  module's own source tree, never in `build.gradle.kts`, and the runtime
  dependency set of the tier stays empty. The tier's JDK footprint is
  unchanged: `java.base` and `java.xml`.
- The table names, in its header, the font it was derived from, the licence
  that governs it, the tool and version that produced it, and the layout
  profile version it belongs to. A table without that header is a defect, not
  a default.
- **Tier modules must not measure text with `java.desktop`.** `java.awt.font`
  resolves a font from the host, so the same document would break into
  different lines on different machines, and the tier's output would stop
  being a function of its input. The tier's advance widths come from the
  bundled table; the client draws what the tier lays out. This is §1.1's rule
  applied to layout: the exemption is granted for the *view*, never for the
  *domain*.
- `backend/tests/test_kotlin_dependencies.py` must assert both halves: that
  the tier jar carries no third-party class, **and** that the only data files
  it carries are approved metric assets with their licence header.

The metrics are derived from a font whose licence permits redistribution of the
derived data — a SIL Open Font Licence release, whose licence text and
reserved-font-name rules travel with the table. The redistribution terms of
any other metric set must be confirmed and recorded before that set is
chosen.

The evidence for this clause, and the spike that produced it, are in
[`docs/replacement/editor-layout-feasibility.md`](../docs/replacement/editor-layout-feasibility.md).
Without it, the tier has no permitted source of advance widths, and the
enforcement test above would not notice the gap: it inspects only `.class`
entries.

---

## 2. Module boundaries

The dependency direction is strictly downward. A module may depend on the
modules listed in its own `build.gradle.kts` and on nothing else.

```
core   <- text  <- ingest
  |        ^         ^
  |        |         +-- pdf, word
  |        |
  |        +-- library (also <- word), editing, factcheck
  |
  +-- word, workflow, discovery, vault, notebooks
  +-- server (also <- workflow)

bridge depends on all thirteen modules above it.
```

- `:core` depends on nothing.
- No module may depend on `:bridge`.
- No module may depend on a module that is not declared in its build file.
- **No circular dependencies.** If you need a type from a higher module,
  the type belongs in `:core`.
- A rule two modules need belongs in `:core`, because `:core` is the only
  module both of them may depend on. The decompression bound is the example:
  `:pdf` and `:word` both read untrusted compressed data, so
  `lmeditor.core.DecompressionLimits` holds the numbers once rather than
  twice.

`:desktop` sits **above** the tier and is not part of it:

```
desktop -> core
```

It is a client, not a tier module: the arrow runs downward, the tier never
sees it, and §1.1 records why it is exempt from the empty-dependency rule.

---

## 3. Package and file layout

- One Gradle module per Python package. `:text` ↔ `lmeditor.text`.
- Package root mirrors the Python package: `lmeditor.core`, `lmeditor.text`,
  `lmeditor.word`, `lmeditor.pdf`, `lmeditor.ingest`, `lmeditor.library`,
  `lmeditor.workflow`, `lmeditor.editing`, `lmeditor.factcheck`,
  `lmeditor.discovery`, `lmeditor.server`, `lmeditor.vault`,
  `lmeditor.notebooks`, `lmeditor.bridge`. The desktop client's package is
  `lmeditor.desktop`, and it is not a tier package.
- Source root: `kotlin/<module>/src/main/kotlin/lmeditor/<module>/`.
- Test root: `kotlin/<module>/src/test/kotlin/lmeditor/<module>/`.
- One top-level declaration per file, unless the file is a cohesive group
  (an object that owns a family of related functions is fine).
- File names are PascalCase and match the declaration they contain:
  `Chunker.kt`, `WordReader.kt`, `UrlPolicy.kt`.
- A Python module maps to a Kotlin file of the same name in PascalCase:
  `provenance/chunker.py` → `text/Chunker.kt`.
- A module that is a thin wrapper around a single rule may name the Kotlin
  file after the *rule* rather than after the module, when the module's own
  name would not say what the file holds. `provenance/service.py` →
  `text/SupportingText.kt`: the service is a wrapper around the provenance
  index, and the one rule it still owns is the supporting passage a claim and
  a source share. The module still owns the rule; only the file's name
  differs, and the file's name matches the declaration inside it.

---

## 4. Naming

The Python tier is snake_case; the Kotlin tier is camelCase. The mapping is
mechanical and applies to every identifier:

| Python | Kotlin |
| :--- | :--- |
| `chunk_id` | `chunkId` |
| `char_start` | `charStart` |
| `is_verified` | `isVerified` |
| `max_evidence` | `maxEvidence` |
| `CLASS_NAME` | `ClassName` |
| `module_level_function` | `moduleLevelFunction` |

- Private members are prefixed with `_`? **No.** Kotlin uses `private` or an
  internal visibility modifier. Do not use underscore prefixes.
- Constants are `const val` in an `object` or a `companion object`, in
  `SCREAMING_SNAKE_CASE` to match the Python source they port.
- Boolean properties read as assertions: `isVerified`, `hasChunk`,
  `allPassed`.

---

## 5. Types

### 5.1 Nullability

- Python `Optional[X]` / `X | None` → Kotlin `X?`.
- Python `str` (non-optional) → Kotlin `String` (non-null).
- A Python `list[str]` → Kotlin `List<String>`; use `emptyList()` not
  `mutableListOf()` unless mutation is required.
- A Python `dict[str, Any]` → Kotlin `Map<String, Any?>`.

### 5.2 Data models

- Python Pydantic `BaseModel` → Kotlin `data class` with `val` properties.
- **Invariant checks go in an `init` block**, and they must throw a typed
  `LMeditorError` — never `require`, never `IllegalArgumentException`.
  Use `ValidationError(message, field = "...", value = ...)`.
- **Every Pydantic constraint has a counterpart.** A `min_length=1` is a
  non-empty check; a `ge`/`le` is a range check. A model that one tier accepts
  and the other refuses is a model that reaches the audit trail in one form
  and not in the other. Check the *trimmed* value, the way Pydantic does.
- Computed properties that exist in Python as `@property` → Kotlin `val` with
  a getter (`val docId: String get() = documentId`).
- A Python `@model_validator(mode="after")` → an `init` block.
- A Python `model_copy(update={...})` → Kotlin `copy(...)`.

#### 5.2.1 The two ways of making a model

Pydantic's constructor and Pydantic's `model_copy()` are **not** the same
thing: the constructor validates and strips whitespace, and `model_copy()` does
neither. A model declared with `str_strip_whitespace=True` therefore strips
when it is built and keeps its values exactly when it is copied.

Kotlin's `data class` has one constructor, and the generated `copy()` calls
it. To reproduce both behaviours, a schema model whose Python counterpart
declares `str_strip_whitespace=True` has:

- a **private** primary constructor, reached only through the companion's
  `operator fun invoke` — which trims the string fields, then validates;
- a **public** `copy()`, annotated `@kotlin.ExposedCopyVisibility`, which
  reaches the private constructor directly and therefore neither trims nor
  strips.

The Python whitespace set is **not** Kotlin's: Python strips U+00A0, U+2007,
U+202F and U+0085, and `String.trim()` leaves all four alone. Use
`String.stripModelWhitespace()` from `lmeditor.core`, never `trim()`.

The compiler flags every `copy()` of such a model with
`DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING`, because it will become an error in
Kotlin language version 2.5. Suppress it at the **file** level, with a
comment saying why the pattern is deliberate.

A model whose Python counterpart does *not* strip keeps an ordinary public
constructor.

### 5.3 Enumerations

- A Python `str, Enum` → a Kotlin `enum class` with the wire string in a
  `val wire: String` property and a `fromWire(value: String)` companion
  function that throws `ValidationError` for an unknown value.
- A Python enum *alias* (a second name for the same value, e.g.
  `EntailmentStatus.ENTAILMENT`) → a `val` in the companion object, not a
  second enum entry.

### 5.4 Interfaces

- A Python `Protocol` → a Kotlin `interface` in `lmeditor.core` (see
  `core/Protocols.kt`).

---

## 6. Errors

- Every failure raises a typed error from `lmeditor.core`. There are no bare
  `throw RuntimeException(...)`, no `IllegalStateException`, and no
  `error(...)` calls outside the `lmeditor.core` package.
- **Never swallow an exception.** Do not write `catch (e: Exception) { }`.
  If a failure is genuinely recoverable, catch the *specific* type and turn
  it into a domain result (a refusal, a note, a report field) — and record
  it in the result so the caller can see it.
- The exception hierarchy, the `details` map and the message text are
  specified in `lmeditor.core`. Reuse them; do not invent a parallel one.
- When the Python code raises a specific error, raise the same one, with the
  same `details` keys.

---

## 7. Boundary validation

Every public entry point validates its input:

- Reject a null/blank identifier with `ValidationError`.
- Reject an out-of-range number with `ValidationError`.
- Reject a malformed artifact with the domain error that names it
  (`WordParseError`, `SourceLoadError`, `SpanAlignmentError`, ...).

The `lmeditor.core` data classes already enforce the schema invariants. Do
not re-implement them at the call site.

---

## 8. Idempotency

State transitions must be idempotent: applying the same transition twice
leaves the same state as applying it once. This matters because the workflow
re-runs nodes after a resume, and the book library re-indexes a section after
an edit.

- A store write is idempotent: writing the same content twice produces the
  same bytes.
- A registry insert replaces rather than appends.
- A cache lookup never mutates the stored value.

---

## 9. Concurrency

- The Python tier uses `threading.Lock` for the shared registries. Use
  `java.util.concurrent.locks.ReentrantLock` (or `synchronized` for the
  simple cases) and keep the critical sections small.
- Prefer immutable data classes over mutable holders.
- Do not block inside a lock.

---

## 10. Text and encoding

- All I/O is UTF-8. Never rely on the platform default charset.
- Read files with `Files.readString(path, StandardCharsets.UTF_8)` or a
  `BufferedReader` over `Files.newBufferedReader(path, UTF_8)`.
- When a byte length matters (byte offsets in a chunk), use
  `text.toByteArray(Charsets.UTF_8).size` — never `text.length`, which counts
  UTF-16 code units.
- Normalise line endings to `\n` when a rule says "text", and keep `\r\n`
  when a rule says "raw bytes".

---

## 11. Determinism

- No `HashMap`/`HashSet` iteration in anything whose order is observable.
  Use `LinkedHashMap`/`LinkedHashSet` or `sortedBy`.
- No timestamps in a computed identifier, unless the identifier is defined to
  contain one.
- No random values in a computed identifier.
- A chunk id is `sha256("<document_id>:<char_start>:<char_end>:<text>")`,
  computed with `lmeditor.core.Hashing.sha256Hex`. The Python tier computes
  the same digest; the two must agree byte for byte.

---

## 12. Tests

- Tests live in `src/test/kotlin` and use `kotlin.test` (`@Test`,
  `assertEquals`, `assertTrue`, `assertFailsWith`).
- Every ported rule gets a test that states the rule, not the implementation.
- A test that pins a **shared** fact (a wire string, a digest value, an
  exact output) must say so in its name, because those are the tests that
  catch drift between the Python and Kotlin tiers.
- Port the Python test cases where they exist: if
  `backend/tests/test_provenance.py` asserts something about the chunker,
  the Kotlin chunker test must assert the same thing.
- Tests must be deterministic and must not touch the network or the clock.

---

## 13. Comments and documentation

- Every public declaration carries a KDoc comment that says what it is *for*,
  in the same voice as the Python docstrings it replaces.
- Comment the rules, not the mechanics. A comment that restates the code is
  noise; a comment that states the invariant is documentation.
- Do not write "TODO", "FIXME" or "not implemented" in a delivered module.

---

## 14. What must NOT be ported to Kotlin

These parts of the Python tier genuinely need their libraries and stay in
Python:

| Python module | Why it stays |
| :--- | :--- |
| `drafting/signatures.py`, `drafting/modules.py`, `drafting/teleprompter.py` | DSPy |
| `workflow/graph.py` | LangGraph |
| `workflow/nodes.py` (the LM calls) | DSPy + LangGraph |
| `provenance/embeddings.py` | sentence-transformers |
| `verification/nli_evaluator.py` (the model half) | transformers / DeBERTa |
| `llm/` | provider SDKs |

The deterministic half of each of those — the routing rules, the scoring
maths, the assertion checks, the fallback behaviour — **is** ported, and lives
in Kotlin. The Python side keeps a thin adapter that calls into the Kotlin
tier over the NDJSON seam described in `bridge/README.md`.

Do not attempt to reimplement DSPy, LangGraph or a transformer in Kotlin.
That work would be larger than the entire rest of the tier and would add the
dependencies the tier exists to avoid.

**LlamaIndex is not on that list, because the tier never used it.** An
earlier revision of this contract recorded `provenance/indexer.py` as
LlamaIndex-bound. It was not: the module imported no ML library at all, and
the retrieval rule it held — BM25 Okapi, a SHA-256 TF-hash embedder, cosine
similarity and Reciprocal Rank Fusion — was hand-written arithmetic. It is in
Kotlin now, in `text/Indexer.kt`. Only the *embedder* it could delegate to,
`provenance/embeddings.py`, was ever model-bound, and that module is still
Python. A contract that names a library the code does not use is worse than
one that names none: it makes a port look impossible.
