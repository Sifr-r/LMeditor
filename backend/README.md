# LMeditor backend

A FastAPI service that turns a set of ingested sources into a **source-grounded**
manuscript: it discovers perspectives, outlines the topic, drafts each section
from the passages that concern it, verifies every claim against those passages
with an entailment model, pauses for human approval, and exports the result to
Microsoft Word.

This package is the service. The Kotlin Compose desktop client in
[`../kotlin/desktop`](../kotlin/desktop/README.md) consumes its SSE envelope and
REST routes. The deterministic half of the work lives in the Kotlin tier at
`../kotlin`; see *The Kotlin tier* below.

---

## Installing it

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

The package builds with Hatchling and needs Python 3.12 or newer. The
editable install puts `lmeditor` on the venv's path, so every command below
works from `backend/` without setting `PYTHONPATH`.

---

## Running it

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn lmeditor.server.app:app --port 8000
```

Every route under `/api/` needs a bearer token. The server settles one at
startup — the operator's own `LMEDITOR_API_TOKEN`, the token already in the
token file, or a new one — and writes it to the token file. It is at
`<home>/.lmeditor/api-token` unless `LMEDITOR_API_TOKEN_FILE` names
another. Read it back and check the server with it:

```powershell
$token = (Get-Content "$HOME\.lmeditor\api-token" -Raw).Trim()
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health -Headers @{ Authorization = "Bearer $token" } | ConvertTo-Json
```

A request without the token is refused with a **401** and the same answer
whether the token was missing or wrong:

```json
{ "detail": "This API requires a bearer token." }
```

The health payload reports whether a language model is loaded:

```json
{
  "status": "healthy",
  "service": "lmeditor-api",
  "version": "0.1.0",
  "lm": {
    "configured": true,
    "provider": "openai",
    "model": "gpt-4o-mini",
    "detail": "A language model is loaded; drafting is enabled.",
    "is_stub": false
  }
}
```

**`lm.configured` is `false` means the backend cannot write.** It will still
ingest sources and verify what it is given, but every drafting attempt fails
loudly and says why. LMeditor never substitutes template prose for a model
call — a section that could not be drafted is left empty and labelled, and the
run goes to the human-in-the-loop gate.

---

## The Kotlin tier

### Persistent notebook sources

Source records, extracted passages, offsets and bibliographic metadata are saved
in `backend/.lmeditor/sources` by default. Restarting the backend or its Kotlin
bridge restores those records automatically. Set `LMEDITOR_SOURCES_DIR` to an
absolute directory to use another location; keep that directory with the
installation's persistent data when deploying or upgrading the backend.

The 256-source limit now bounds the memory cache. It does not delete archived
records. Removing a source explicitly deletes its archived record; removing it
from a notebook only changes membership. A notebook with unavailable source IDs
refuses to answer and identifies the missing IDs before contacting the model.

This first archive stores extracted text/chunks and metadata, **not original
upload bytes**, source revision history, conversations or retrieval indexes.
Pre-existing sources held only in an already-running older process must be
ingested again after upgrading. Writes flush a sibling temporary file and
replace the record atomically. Power-loss durability and multi-process revision
conflict handling are not yet provided. Corrupt or unsupported records are
reported as errors instead of treated as an empty library.

### Tier ownership

Most of the deterministic work in this package is **not** Python any more. It
lives in a Kotlin tier at `../kotlin`, and this package reaches it over a
newline-delimited JSON pipe on the JVM process's standard input and output.

The split is by *capability*, not by convenience:

| Stays in Python | Why |
|---|---|
| The language-model calls | provider SDKs, DSPy, LangGraph |
| The entailment model | transformers, DeBERTa |
| The model-backed embeddings | sentence-transformers |
| The HTTP surface | FastAPI, and the SSE envelope the Kotlin tier declares |
| The session fan-out | asyncio and SSE, bound to the event loop |
| The Word whitespace and text-normalisation rules | the validator runs on *every* block the backend builds, so a seam call per block would be a regression, not an implementation |
| The Word format detection and the refusal reason | a magic number is compared a handful of times per file, and the refusal is the operator's own wording |
| The compiled inline-citation pattern | the drafter applies it to the model's output in-process, so it needs a regular expression it can hold |
| The sliding-window and semantic chunking | the seam serves one chunking method and it serves the sentence-aware one; answering a sliding-window request with sentence-aware chunks would be a different rule, not a faster one |
| The byte-offset and token-count helpers | the seam does not expose them as methods of their own |
| The section split of an imported document | the HTTP import route must answer a document the reader refuses with the reader's own reason and a 422 |
| The document-level grounding aggregation | no Kotlin counterpart exists; the arithmetic primitives it calls are the tier's, and the policy around them is not |

Everything else — the citation grammar, the sentence-aware chunker, the claim
extractor, the citation precision and recall, the lineage rules, the SSRF
policy, the bibliography resolvers, the book store, the OOXML reader and
writer, and the BM25, TF-hash, cosine similarity and Reciprocal Rank Fusion
retrieval rules — is Kotlin, and this package calls it across the seam rather
than repeating it.

The Python module that used to hold one of those rules is now a **door**: it
keeps its exact public API and its exact exceptions, and answers them from the
tier. The original implementation is not deleted — it is frozen under
`src/lmeditor/_reference/`, and it is what the parity harness compares the
tier against.

The deterministic rules in the table are deliberate exceptions, and none of
them is drift. The twins have one Kotlin implementation and one Python
implementation, held in step by a shared corpus or by the parity harness —
except the section split, which neither holds today: the harness declares its
counterpart and compares nothing, so the two copies are held only by their own
tests on each side. The last row has no Kotlin counterpart at all.
`../ARCHITECTURE.md` §18.2.1 is the
full catalogue of both, and it names the session broker too.

Two rules keep the two halves honest:

- **A failure never terminates the bridge.** A malformed request is a client's
  mistake, not the server's, so the error is reported and the service carries
  on.
- **The client never silently falls back to Python.** A tier that cannot be
  reached is an error that names the paths it tried, because a silent fallback
  would hide exactly the drift the tier exists to remove. In the test suite a
  missing tier is a *skip*, never a pass.

### Building it

The tier is a Gradle build, and the backend needs its jar:

```powershell
cd ..\kotlin
..\.tools\gradle-9.8.2\bin\gradle.bat :bridge:tierJar
```

That writes `kotlin/bridge/build/libs/*-tier.jar`, which is where the Python
client looks for it. Set `LMEDITOR_KOTLIN_BRIDGE` to point at a classpath
instead, or `LMEDITOR_JAVA` to choose the `java` executable.

The full wire contract is `../kotlin/bridge/README.md`; the document model's
own contract is `../kotlin/word/WIRE.md`; the module rules are
`../kotlin/CONTRACTS.md`.

---

## Configuring the language model

Everything is environment-driven; there is no config file to edit.

| Variable | Meaning |
|---|---|
| `LMEDITOR_LM_PROVIDER` | `openai`, `codex`, `anthropic`, `ollama`, `gemini`, `openai-compatible`, `stub` |
| `LMEDITOR_LM_MODEL` | Model identifier. Provider-qualified (`gpt-4o-mini`) or bare for a local server |
| `LMEDITOR_LM_API_KEY` | API key. Not needed for a local server |
| `LMEDITOR_LM_BASE_URL` | Base URL of a local / OpenAI-compatible server |
| `LMEDITOR_LM_TEMPERATURE` | Sampling temperature (default `0.2`) |
| `LMEDITOR_LM_MAX_TOKENS` | Generation cap (default `4096`) |

### A hosted provider

```powershell
$env:LMEDITOR_LM_PROVIDER = "openai"
$env:LMEDITOR_LM_MODEL     = "gpt-4o-mini"
$env:LMEDITOR_LM_API_KEY   = "sk-..."
```

### Codex using an existing CLI sign-in

Install and sign in to the Codex CLI first, then check that authentication is
available without exposing credentials:

```powershell
codex login status
$env:LMEDITOR_LM_PROVIDER = "codex"
$env:LMEDITOR_LM_MODEL = "default"
Remove-Item Env:LMEDITOR_LM_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:LMEDITOR_LM_BASE_URL -ErrorAction SilentlyContinue
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m uvicorn lmeditor.server.app:app --host 127.0.0.1 --port 8000
```

The adapter sends DSPy prompts to `codex exec` over standard input and receives
the final assistant response. The CLI owns authentication; LMeditor never reads
or copies its tokens. `default` omits the explicit model argument so Codex resolves
its default; set `LMEDITOR_LM_MODEL` to an available model identifier to select
one explicitly. The CLI runs ephemerally with a read-only sandbox in an isolated
directory beneath `.tools/codex-generation`. Reading settings in the desktop
remain independent of model generation.

The adapter ignores unrelated Codex user configuration, including configured
hooks and MCP servers. `LMEDITOR_LM_MAX_TOKENS` is a requested output budget for
Codex, rather than a hard token cap; responses also have a 1 MiB size limit.

`LMEDITOR_CODEX_EXECUTABLE` can name the Codex executable when it is not on PATH.
`LMEDITOR_CODEX_TIMEOUT_SECONDS` controls the per-call timeout (default 180
seconds). Missing executables, authentication/provider failures, timeouts and
empty output surface as generation errors instead of substituting stub text.
The backend health response identifies this provider as `codex` with
`is_stub: false`; a successful live generation call is still needed to confirm
account/model access.

### A local model (no data leaves the machine)

```powershell
$env:LMEDITOR_LM_PROVIDER = "ollama"
$env:LMEDITOR_LM_MODEL     = "llama3.1"
$env:LMEDITOR_LM_BASE_URL = "http://127.0.0.1:11434"
```

Any OpenAI-compatible server works too:

```powershell
$env:LMEDITOR_LM_PROVIDER = "openai-compatible"
$env:LMEDITOR_LM_MODEL     = "my-local-model"
$env:LMEDITOR_LM_BASE_URL = "http://127.0.0.1:8080/v1"
```

### Offline, with no provider at all

```powershell
$env:LMEDITOR_LM_PROVIDER = "stub"
```

The `stub` provider is a **deterministic drafter**, not a language model: it
quotes the passage it was given and cites that passage's real chunk id. It
exists so the whole application can be exercised on a machine with no model
configured. It identifies itself as `stub` in `/api/v1/health`
(`"is_stub": true`) and in the scorecard, so a document produced this way can
never be mistaken for one a real model wrote.

---

## The HTTP surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Readiness, including whether a model is loaded |
| `POST` | `/api/v1/sources/ingest` | Ingest an uploaded artifact (multipart) |
| `POST` | `/api/v1/sources/url` | Ingest a remote source (JSON) |
| `GET` | `/api/v1/sources` | The server's source library |
| `POST` | `/api/v1/documents/generate` | Start a generation run |
| `GET` | `/api/v1/stream/{doc_id}` | SSE stream of the run |
| `GET` | `/api/v1/workflow/{doc_id}/status` | Run status snapshot |
| `POST` | `/api/v1/workflow/{doc_id}/approve` | Answer the human-in-the-loop gate |
| `GET` | `/api/v1/documents/{doc_id}` | The authoritative manuscript and its scorecard |
| `GET` | `/api/v1/documents/{doc_id}/export.docx` | Export the manuscript as a Word document |

Every route in that table needs the bearer token described under
[Running it](#running-it).

A generation request needs source material, supplied either inline
(`source_chunks`) or by reference to sources the server already holds
(`source_ids`):

```powershell
$body = @{
  topic     = "Municipal waste collection trends"
  source_ids = @("audit_2023")
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/documents/generate -Method Post -Body $body -ContentType "application/json" -Headers @{ Authorization = "Bearer $token" }
```

---

## The origin rule

The API refuses a request that changes something on it when the request
comes from a page on another origin. CORS alone would not stop such a
request — it decides whether the browser lets the page *read* the
answer, not whether the request arrives — so the request itself is
refused with a **403**, and the change does not happen.

The API's own origin is worked out **per request**, and not from a
constant:

- the scheme and the port must be the API's own — the port the request
  was addressed to; and
- the request must have been addressed to a **loopback name** —
  `localhost`, `127.0.0.1` or `::1`.

A page served from anywhere else is a **foreign origin**: its mutating
requests are refused with a **403**, and the change does not happen.
That is what keeps a page on a name that resolves to the API — a
rebinding page — from changing anything on it, however its `Host`
header and its `Origin` agree. A browser asks what it may do before it
does it, and that question is refused too, with a 400 and no origin
header, so the request that would change something never leaves the
page.

### Allowing your own page

`LMEDITOR_ALLOWED_ORIGINS` is how the operator names the origins their
own page is served from. It holds a comma-separated list of exact
origins — a scheme, a host and a port, each named in full:

```powershell
$env:LMEDITOR_ALLOWED_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173"
```

An origin is named in full, so `http://127.0.0.1:5173` and
`http://127.0.0.1:5174` are two origins: the name covers the one that
is written down and not the other.

An API that is reached at a **non-loopback** address — a LAN address,
say — has no own origin by the rule above, so a page served from that
address **must** be named in `LMEDITOR_ALLOWED_ORIGINS`; there is no
other way for it to reach the API. The same holds for a page served on
another port of the loopback interface: that is another server's page,
and it must be named too.

The desktop client sends no `Origin` header at all, so it is not
affected by any of this.

---

## The bearer token

The origin rule answers what a **page** may do. It cannot answer what a
**process** may do, because a process need only omit the `Origin` header
and then the request is answered as if it came from the operator. So every
route under `/api/` also requires a bearer token:

```
Authorization: Bearer <token>
```

A request without one is refused with a **401**, and the refusal is the
same whether the token was missing or wrong — the same status, the same
headers and the same body — so a caller cannot learn anything from the
difference between the two.

The token is settled once, at startup, in this order:

1. `LMEDITOR_API_TOKEN`, when it is set and not blank. The operator's own
   token, used verbatim.
2. The token file, when it holds one. This is what makes the token survive
   a restart.
3. A new token, made of 32 random bytes.

Whatever it turns out to be, it is written to the token file — at
`<home>/.lmeditor/api-token` unless `LMEDITOR_API_TOKEN_FILE` names
another — because the server and the desktop client are separate processes
and the file is how one hands the credential to the other. A file that
already holds the token is not rewritten, so the file's timestamp says when
the token last changed and nothing else.

To rotate the token, set `LMEDITOR_API_TOKEN` or delete the token file and
restart the server.

The token is never logged, never in a response body and never in a URL.
The API's own documentation (`/docs`, `/redoc`, `/openapi.json`) is not
credentialed: it describes the API and acts on nothing.

---

## Verifying an installation

```powershell
.\.venv\Scripts\python.exe scripts\verify_end_to_end.py
```

The script starts a backend with the stub provider, drives the whole flow
against it — ingest, generate, follow the SSE stream, answer the gate, fetch
the manuscript, export the `.docx` — and reports what it observed. Use
`--live` to run it against a backend configured with a real model.

### The review gate, against a live backend

```powershell
.\.venv\Scripts\python.exe scripts\verify_hitl_live.py
```

This one starts a backend **without** a language model — so no section can be
drafted, which is exactly what routes a run to the human-in-the-loop gate — and
then drives the gate over the real wire. It proves the gate is published on the
stream, that it can be answered with the body the server accepts, and that the
run finishes afterwards. It also exercises the "the gateway is up but cannot
write" path, which is the one an operator meets first when a provider is
misconfigured.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe scripts\verify_kotlin_parity.py
```

`mypy src` is clean on its own; `mypy src --ignore-missing-imports` is clean too. The
third-party modules that ship no type information (`lxml`, `markdown`) are declared in
`pyproject.toml` rather than silenced with inline `# type: ignore` comments, because an inline
ignore is itself reported as unused when mypy is invoked with `--ignore-missing-imports`.

`scripts/verify_kotlin_parity.py` is the check that the two tiers still agree. It runs
the same input through the frozen Python witness and through the Kotlin tier and
compares the answers; a disagreement is a failure. It needs the tier jar built
(`../kotlin` → `:bridge:tierJar`).

Four test files are worth knowing about:

- `tests/test_workflow_e2e.py` — drives the real workflow with a deterministic
  model double and asserts that a section is drafted, cites a real chunk id,
  verifies against it, and that a section which *cannot* be drafted stays empty
  and says why.
- `tests/test_wire_contract.py` — reads the Kotlin tier's own source and checks it
  against the backend's declared contract: the event vocabulary, the SSE
  envelope and the operator's outline must be the same on both sides of the
  seam, so the two tiers cannot drift apart silently.
- `tests/test_model_provider.py` — the provider configuration, and the stub's
  contract: it identifies itself and never masquerades as a language model.
- `tests/test_perspective_grounding.py` — the discovery layer's contract with
  the corpus: the personas are derived from the passages they were given, two
  corpora with disjoint vocabulary produce disjoint personas, and a passage
  identifier never becomes a word of the corpus.

---

## Layout

| Package | Responsibility |
|---|---|
| `lmeditor/core` | Domain models, typed errors, the shared protocols |
| `lmeditor/llm` | Model configuration, the DSPy bootstrap, the offline stub |
| `lmeditor/ingestion` | Loading sources, resolving their bibliography, SSRF policy, byte caps |
| `lmeditor/provenance` | Chunking, retrieval, citation lineage |
| `lmeditor/discovery` | Perspectives, outline, dialogue |
| `lmeditor/drafting` | The drafting and cohesion modules |
| `lmeditor/verification` | Claim extraction, entailment, the citation precision and recall |
| `lmeditor/editing` | The inline editor: what is rewritten, what is refused, and why |
| `lmeditor/factcheck` | The checker: claims, verdicts, and the open-web evidence for them |
| `lmeditor/word` | Reading and writing `.docx` packages |
| `lmeditor/library` | The books on disk, their digest, and the bounded working set |
| `lmeditor/workflow` | The LangGraph state machine |
| `lmeditor/server` | HTTP routes, SSE sessions, the source library, the active vault, the notebooks folder |
| `lmeditor/notebooks` | The notebook workspace: a folder of sources and notes, and the Ask |
| `lmeditor/vault` | The vault workspace: markdown notes, links and tags on the operator's disk |
| `lmeditor/tier` | The façades that call the Kotlin tier |
| `lmeditor/bridge` | The NDJSON client for the Kotlin tier |
| `lmeditor/_reference` | The frozen Python originals the parity harness compares against |

---

## What the application will not do

**It will not fabricate prose.** If the language model is not configured, or a
drafting call fails, the affected section is left **empty**, labelled with the
reason, and recorded in the run's errors. There is no code path that produces
text the model did not write, and no path that reports a verification score
nobody measured: a score that was never computed is shown as *not measured*,
not as a perfect one.
