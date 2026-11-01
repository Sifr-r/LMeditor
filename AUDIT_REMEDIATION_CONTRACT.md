# AUDIT.md P1 Remediation Contract — 2026-10-03

> **Superseded in part — 2026-10-25.** The Flutter/Dart client this contract bound was **removed**; the Kotlin tier (`kotlin/`) was kept and now holds every deterministic rule **that crosses the seam**. The rules the seam does not serve are named in `ARCHITECTURE.md` §18.2.1. The clauses that govern the **client** — the parser rules in §1, the Dart field map in §6, the widget behaviour in §7, and worker D's file ownership — are **moot**: there is no client to implement them. The clauses that govern the **backend** — the canonical SSE envelope, the citation grammar, the HITL lifecycle, the ingestion trust boundary — are **still binding and unchanged**: the envelope they fixed is the envelope the Kotlin tier declares, and `backend/tests/test_wire_contract.py` now enforces that the two agree. The legacy aliases `done`/`completed`/`error` that §1 required the Dart parser to tolerate had no consumer besides that parser, and are gone with it. See `ARCHITECTURE.md` §19.

Binding interface contract for the P1 fixes (AUDIT.md findings 1–11). This file is
authoritative for cross-domain signatures. Workers must not change a contract to
suit a local convenience; report a conflict instead.

Environment facts (verified 2026-10-03):
- Python `langgraph==1.2.11` — `langgraph.types.interrupt` and `langgraph.types.Command` are available.
- `mypy` runs strict (`pyproject.toml` `[tool.mypy] strict = true`).
- Dart/Flutter are on `PATH`; `client/.dart_tool` exists. *(Removed with the client on 2026-10-25.)*

---

## 1. Canonical SSE event envelope (findings 4, 5, 7)

**The backend `WorkflowEvent` serialization is canonical. The client adapts to it.
Do not change the backend envelope shape.**

Wire frame (already produced by `routes.py`):

```
event: <event_type.value>
data: <json of {event_type, step, doc_id, data:{...}}>

```

The Dart parser must unwrap `data` and dispatch on the `event_type` field.
Dart must NOT continue to infer type from payload keys when an `event_type` field
is present.

| `event_type` | `data` payload (required keys) | Dart event class |
| --- | --- | --- |
| `node_start` | `step`, plus step-specific context | progress (existing generic handling) |
| `node_complete` | `step`, plus step-specific context | progress |
| `token_delta` | `delta: str`, `section_id: str` | `TokenDeltaEvent` |
| `scorecard_update` | `scorecard: {precision, recall, f_beta, total_claims, supported_claims, ungrounded_claims, contradicted_claims, neutral_claims}` | scorecard / metrics |
| `hitl_request` | `reason: str`, `requires_hitl: true` | new HITL-awaiting event |
| `workflow_complete` | `sections: [...]`, `scorecard: {...} \| null` | `DoneEvent` (terminal) |
| `workflow_error` | `error: str` | `ErrorEvent` (terminal) |

Terminal names are exactly `workflow_complete` and `workflow_error`. The Dart
parser must additionally tolerate the legacy aliases `done`/`completed`/`error`.

`VerificationScorecard` field names are as declared in
`lmeditor/core/schemas.py` (`f_beta`, `precision`, `recall`, `total_claims`,
`supported_claims`, `ungrounded_claims`, `contradicted_claims`, `neutral_claims`).
The client must not invent `grounding_score`.

---

## 2. Canonical citation grammar (finding 6)

New shared module: **`backend/src/lmeditor/core/citations.py`**, exporting:

```python
CITATION_PATTERN: re.Pattern[str]
def extract_chunk_ids(text: str) -> list[str]: ...        # ordered, de-duplicated
def strip_citations(text: str) -> str: ...
def iter_citation_spans(text: str) -> list[tuple[str, int, int]]: ...  # (chunk_id, start, end)
```

Accepted marker forms (all must parse to the bare chunk id):

- `[#chunk_id]` — **the form the drafter emits; this is the primary form**
- `[chunk_id]`
- `[cite: chunk_id]`
- comma-separated lists of any of the above, e.g. `[#a, #b]`

Rules:
- `#` is part of the marker prefix, never part of the chunk id.
- Markdown links `[text](url)` and images `![alt](url)` must not match.
- The character class must accept the full id alphabet actually produced by
  `provenance/chunker.py` (hex ids) **and** non-hex ids used in tests and by
  `HarnessState`, i.e. `[A-Za-z0-9_.\-]+`.

`lmeditor/verification/claim_extractor.py` and `lmeditor/drafting/modules.py`
must both consume this module. Neither may keep a private divergent regex.
Export the new module from `lmeditor/core/__init__.py`.

---

## 3. Verification gate (finding 8)

Single source of truth, declared in
`lmeditor/verification/citation_checker.py`:

```python
DEFAULT_F_BETA_THRESHOLD: float = 0.85
```

A section is verified **only if all four** hold:

1. `scorecard.f_beta >= threshold`
2. `scorecard.contradicted_claims == 0`
3. `scorecard.ungrounded_claims == 0`
4. `scorecard.neutral_claims == 0`   ← the missing condition from the audit

`CitationChecker.__init__` gains a keyword parameter `f_beta_threshold: float =
DEFAULT_F_BETA_THRESHOLD` and its `audit_section` uses it.
`workflow/nodes.py::verify_citations_node` must import the same constant and
must not hardcode `0.80`.

**Out of scope (finding 12, P2):** `max_retries` / `threshold_f_beta` from
`GenerateDocumentRequest` are still not propagated into `HarnessState`. Leave the
existing behaviour intact; do not "fix" it in this pass.

---

## 4. HITL lifecycle (finding 7)

`hitl_checkpoint_node` must **suspend** the graph, not set flags and continue:

```python
from langgraph.types import interrupt

def hitl_checkpoint_node(state: HarnessState) -> dict[str, Any]:
    decision = interrupt({"reason": ..., "requires_hitl": True})
    # decision is the value supplied by Command(resume=...) on the second invoke
    return {"hitl_approved": bool(...), "requires_hitl": False, "status": ...}
```

`route_after_hitl` reads `state.get("hitl_approved", False)`; approved →
`macro_cohesion`, rejected → `END`. `END` on rejection is correct **only** because
the graph is suspended first; today the same branch is reached without suspension.

`run_workflow` in `workflow/graph.py`:

- MUST pass a real `checkpointer` (already defaults to `MemorySaver`) and use
  `config = {"configurable": {"thread_id": doc_id}}`.
- MUST use `graph.astream(..., stream_mode="updates")` (not `ainvoke`) so node
  transitions can be published to the session.
- MUST declare and use a sink protocol (in `workflow/graph.py`):

```python
@runtime_checkable
class EventSink(Protocol):
    async def publish_event(self, event: WorkflowEvent) -> None: ...
```

  `WorkflowSession` satisfies this structurally; do not import
  `lmeditor.server` from `lmeditor.workflow` (that would be a layering inversion).
- On `__interrupt__`: publish `EventType.HITL_REQUEST` with the interrupt payload
  and return a result whose `status` is `awaiting_human_review` and whose
  `requires_hitl` is `True`. It MUST NOT report completion.
- Caller resumes with `await graph.ainvoke(Command(resume=decision), config=config)`.

`server/session.py` + `server/routes.py` (worker A):

- `WorkflowSession.complete()` is called **only** when the graph actually reached
  its completed state. If `run_workflow` returns `requires_hitl=True` /
  `status == "awaiting_human_review"`, the worker must instead publish
  `hitl_request`, set `hitl_pending=True`, await the decision, resume the graph,
  and only then complete or fail.
- The worker must not unconditionally call `session.complete(...)`.

---

## 5. Ingestion trust boundary (findings 1, 2, 3)

New module: **`backend/src/lmeditor/ingestion/url_policy.py`**, exporting these
exact signatures. These are load-bearing for `server/routes.py`, so the names and
parameter lists below must not drift.

```python
class UnsafeSourceError(ValueError): ...

MAX_SOURCE_ID_LENGTH: int = 128
MAX_FILENAME_LENGTH: int = 255

def validate_source_id(source_id: str) -> str: ...
def safe_upload_path(source_id: str, filename: str, base: Path | None = None) -> Path: ...
def upload_root() -> Path: ...

def assert_public_url(url: str) -> str: ...
def iter_redirect_hosts(response: httpx.Response) -> list[str]: ...
```

Required behaviour:

- `validate_source_id` rejects: empty, `len > 128`, any of `/ \ : * ? " < > |`,
  any `.`/`..` segment, leading `-`, and NUL. Returns the validated id unchanged
  on success.
- `safe_upload_path` validates the id, reduces `filename` to its basename
  (`PurePath(filename).name`), rejects an empty/dot-only result, truncates to 255
  chars, joins under `upload_root()`, and then **proves containment** with
  `Path.resolve()` + `is_relative_to(upload_root().resolve())` before returning.
  Raise `UnsafeSourceError` on any escape.
- `persist_source_to_disk` in `server/routes_helpers.py` must use
  `safe_upload_path` and must no longer interpolate raw `source_id` /
  `file.filename` into a path.
- `assert_public_url` requires scheme `http`/`https`, resolves the host through
  `socket.getaddrinfo`, and rejects when **every** resolved address is
  loopback / private / link-local / reserved / multicast / unspecified, or when
  resolution fails. Return the URL unchanged on success.
- `UrlSourceLoader.load` must call `assert_public_url(origin)` before fetching,
  and must re-validate **each** redirect hop. The current
  `httpx.get(..., follow_redirects=True)` is not acceptable: it must be replaced
  by manual redirect following (`follow_redirects=False`, at most 5 hops) that
  validates each `Location` target via `assert_public_url`.
- On rejection, raise `lmeditor.core.exceptions.SourceLoadError` (domain error),
  never a bare `ValueError`, so the API maps it to a client error.

`server/routes.py` ingest endpoints must additionally enforce:

- `POST /sources/ingest`: a filesystem `origin` is **rejected** unless it was
  produced by `persist_source_to_disk` in this process. Direct local-file
  ingestion stays off the public boundary.
- `POST /sources/url`: reject `kind` other than `url` and reject any `origin`
  that is not an absolute `http(s)` URL. `kind=text` with a filesystem path must
  no longer return file contents.

---

## 6. Backend → Dart field mapping (findings 10, 11)

Backend `Claim` (`core/schemas.py`) fields are
`claim_id, section_id, text, char_start, char_end, cited_chunk_ids,
entailment_status, entailment_score, reasoning, sentence_idx, premise_text`.
Backend `CitationLineage` fields are
`lineage_id, claim_id, chunk_id, span_start, span_end, exact_quote,
confidence_score, created_at`.

Dart `Claim` must map:

| backend | Dart |
| --- | --- |
| `claim_id` | `claimId` |
| `text` | `proposition` **and** `sentence` (backend has no separate `sentence`; both must be non-empty or the constructor throws) |
| `cited_chunk_ids` | new field `citedChunkIds` |
| `citation_ids` / `citation_id` | `citationIds` (client-internal only) |
| `entailment_status` | `label` |
| `entailment_score` | `entailmentScore` |
| `reasoning` | `explanation` |
| `premise_text` | new field `premiseText` (a passage, **not** a chunk id) |
| `char_start` / `char_end` | `spanStart` / `spanEnd` |
| `section_id` | `sectionId` |
| `sentence_idx` | new optional field |

**Chunk references and citation ids are different things and must not be
conflated.** `premiseChunkId` must not be populated from `premise_text`.

Dart `CitationLineage.fromJson` must also accept `span_start`/`span_end` and
`exact_quote` (currently only `start_offset`/`end_offset`/`exact_span` are read),
and `lineage_id`. `claim_id` must be retained (e.g. in `metadata`) so the panel
can link a lineage back to its claim.

`NLILabel.fromString` must:
- map `"entailed"` → `NLILabel.entailment` (the backend's actual spelling; today
  it throws `ArgumentError`);
- return `NLILabel.unverified` for `null`/absent — never `entailment`, never a throw
  on a legitimate backend section `status` value;
- only throw for a genuinely unrecognised non-empty label.

Add `NLILabel.unverified` (display name "Not yet verified", neutral colouring,
a distinct icon) and render it **without** a fabricated confidence percentage.

`Claim.isVerified` threshold is `0.70` and must become `0.85` to match
`DEFAULT_F_BETA_THRESHOLD` (§3).

---

## 7. No fabricated verification (finding 11)

`editor_view.dart` currently defaults an unmatched citation to
`entailment @ 0.96`. Required behaviour:

- Match a claim by exact `citationId` **first**; only then fall back to `chunkId`
  membership via `citedChunkIds`. Do not take the first claim that merely shares
  a chunk.
- When no claim matches, render `NLILabel.unverified` with no numeric confidence.
- `verification_panel.dart` must construct `CitationLineage` **only** when the
  selected claim actually has a non-empty citation id and a non-empty chunk id.
  A genuinely uncited claim renders with `lineage == null` and must not throw
  (`CitationLineage`'s constructor rejects empty ids).
- `CitationBadge` must accept the unverified state and omit the confidence
  figure for it.

---

## 8. Ownership map (no two workers touch the same file)

| Worker | Owns exclusively |
| --- | --- |
| B — ingestion trust boundary | `ingestion/url_policy.py` (new), `ingestion/loaders.py`, `server/routes_helpers.py`, `backend/tests/test_ingestion_security.py` (new) |
| C — workflow/verification | `core/citations.py` (new), `core/__init__.py`, `verification/claim_extractor.py`, `verification/citation_checker.py`, `drafting/modules.py`, `workflow/nodes.py`, `workflow/edges.py`, `workflow/graph.py`, `backend/tests/test_citation_grammar.py` (new), `backend/tests/test_hitl_gate.py` (new) |
| A — server transport | `server/routes.py`, `server/session.py`, `backend/tests/test_stream_contract.py` (new) |
| D — client | all of `client/lib/**`, `client/test/stream_contract_test.dart` (new), `client/test/claim_mapping_test.dart` (new) |

`server/routes.py` is owned solely by worker A. Worker A must therefore **not**
re-implement worker B's guards; it calls `validate_source_id`,
`safe_upload_path` and `assert_public_url` from §5.

Dispatch order: B, C, D run in parallel. **A runs only after B completes**, because
A depends on the §5 functions existing.
