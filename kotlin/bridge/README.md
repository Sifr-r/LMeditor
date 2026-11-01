# The NDJSON Seam

The Kotlin tier and the Python tier run as two processes and talk over a
single pipe: newline-delimited JSON on the JVM process's standard input and
standard output.

This is the only coupling between the two tiers. It is deliberately narrow,
because a narrow seam is the one that stays honest as both sides change.

```
Python (FastAPI, DSPy, LangGraph, LlamaIndex, transformers)
   │  writes one JSON request per line
   ▼
   java -cp lmeditor-bridge.jar lmeditor.bridge.BridgeMain
   │  writes one JSON response per line
   ▼
Kotlin (core, text, word, pdf, ingest, library, workflow, editing, factcheck, discovery)
```

---

## 1. Why not HTTP

An HTTP boundary would add a port, a deployment unit and a failure mode
(network, timeouts, retries) to a relationship that is always
process-to-process. The seam is a pipe: the OS already handles its lifetime,
its backpressure and its errors, and there is nothing to configure.

---

## 2. Framing

**One JSON document per line, in both directions.** A document never contains
a raw newline. The encoding is UTF-8.

A request:

```json
{"id": "17", "method": "word.read", "params": {"path": "C:/tmp/a.docx"}}
```

A successful response:

```json
{"id": "17", "ok": true, "result": {"document": { "...": "..." }}}
```

A failed response:

```json
{"id": "17", "ok": false, "error": {"type": "WordParseError", "message": "Failed to parse Word document 'a.docx': not a zip", "details": {"origin": "a.docx", "reason": "not a zip"}}}
```

Rules:

- `id` is echoed back verbatim. It is the caller's correlation handle; the
  seam does not interpret it.
- `ok` is exactly `true` or `false`. There is no third state.
- A failure carries `error.type` (the Kotlin exception's simple name),
  `error.message` (the full message, `details` included), and
  `error.details` (the exception's `details` map, verbatim).
- **A failure never terminates the bridge.** The bridge reports the error and
  keeps serving. Only a malformed *request* (unparseable line, unknown
  method, missing `id`) is a protocol error, and even that is reported as a
  failed response rather than a crash.
- Standard error carries diagnostics only — never protocol data. If a
  diagnostic is written to standard output it corrupts the stream.

---

## 3. Binary payloads

A `.docx` or a PDF is a ZIP of binary parts, so it cannot travel as JSON text.
Binary payloads are **base64** (RFC 4648, standard alphabet, with padding),
carried in a `bytes_b64` field.

Encoding is always done by the sender, so the receiver never has to guess.

---

## 4. Method naming

`lmeditor.<domain>.<operation>`, where `<domain>` is the Gradle module that
owns the behaviour:

| Domain | Module |
| :--- | :--- |
| `core` | `:core` |
| `text` | `:text` |
| `word` | `:word` |
| `pdf` | `:pdf` |
| `ingest` | `:ingest` |
| `library` | `:library` |
| `workflow` | `:workflow` |
| `editing` | `:editing` |
| `factcheck` | `:factcheck` |
| `discovery` | `:discovery` |
| `server` | `:server` |
| `vault` | `:vault` |
| `notebooks` | `:notebooks` |

An unknown method is a protocol error, reported as a failed response with
type `BridgeError`.

---

## 5. The method surface

The seam carries **deterministic** work. Work that needs a model or a
network stays in Python and its *result* is handed to Kotlin, which does the
arithmetic and the rule-checking.

The seam serves **114** methods. The list below is the whole surface; a method
that is not in it does not exist.

### 5.1 `core` — the shared grammar (5)

| Method | Params | Result |
| :--- | :--- | :--- |
| `core.citations.extract_chunk_ids` | `text` | `chunk_ids: [..]` |
| `core.citations.iter_spans` | `text` | `spans: [{chunk_id, start, end}]` |
| `core.citations.strip` | `text` | `text` |
| `core.sections.merge_operator_text` | `draft, content, title?, order?` | `draft` |
| `core.schemas.source_chunk_create` | `document_id, text, char_start, char_end, byte_start, byte_end, metadata?` | `chunk` |

### 5.2 `text` — chunking, lineage, claims, auditing, retrieval (15)

| Method | Params | Result |
| :--- | :--- | :--- |
| `text.chunk` | `text, document_id, metadata?, window_size?, overlap_size?` | `chunks: [..]` |
| `text.claims.extract` | `text, section_id` | `claims: [..]` |
| `text.claims.segment_sentences` | `text` | `sentences: [..]` |
| `text.citations.extract` | `text` | `citations: [..]` |
| `text.lineage.attribute_span` | `draft_text, span_start, span_end, chunk_id, claim_id, chunks, exact_quote?, created_at?` | `lineages: [..]` |
| `text.lineage.extract_from_draft` | `draft_text, chunks, created_at?` | `lineages: [..]` |
| `text.lineage.supporting_text` | `claim_text, chunk_text` | `quote, coverage` |
| `text.lineage.attribute_claim` | `claim_id, claim_text, draft_text?, span_start?, span_end?, top_k?, chunks, k1?, b?, rrf_k?, alpha?, embedding_dim?, dense_vectors?, query_vector?` | `lineages: [..]` |
| `text.citation_check.resolve_premises` | `claims, chunks, strict_hallucination_check?, section_id?` | `cited: [..], premises: [..]` |
| `text.citation_check.precision` | `supported_cited_claims, total_cited_claims, total_claims` | `precision` |
| `text.citation_check.recall` | `supported_claims, total_claims` | `recall` |
| `text.citation_check.f_beta` | `precision, recall, beta?` | `f_beta` |
| `text.citation_check.audit` | `claims, chunks, beta?, evaluator_name?, strict_hallucination_check?, section_id?` | `claims: [..], scorecard, hallucinated: [..]` |
| `text.citation_check.audit_section` | `section, chunks, beta?, threshold_f_beta?, evaluator_name?, strict_hallucination_check?` | `section, scorecard` |
| `text.retrieval.rank` | `chunks, query, mode?, top_k?, rrf_k?, alpha?, k1?, b?, embedding_dim?, dense_vectors?, query_vector?` | `results: [..]` |

`text.claims.extract` returns claims with **no** entailment verdict; the
Python side runs the NLI model and sends the scored claims back to
`text.citation_check.audit`, which owns the precision/recall arithmetic and
the ungrounded audit.

`text.retrieval.rank` is the retrieval rule: BM25 Okapi, a dense cosine
index and their Reciprocal Rank Fusion, in one method. `mode` is `bm25`,
`dense` or `hybrid` (the default). `chunks` carries `{chunk_id, text}` pairs
— the tier builds its index once per corpus and answers later queries from
it, and the corpus is named by a content-derived digest, so it travels once,
when it changes.
`dense_vectors` and `query_vector` are either both present or both absent:
when present, the sender has already normalised them and the tier uses them
as-is; when absent, the tier computes its own deterministic SHA-256 TF-hash
vectors. The embedder itself — `sentence-transformers`, or any injected one
— stays in Python, because it is a model.

`text.lineage.supporting_text` is the passage a claim and a source share,
and the share of the claim it covers. The claim's own coverage scaling
stays in Python, where the claim's record lives.

`text.lineage.attribute_claim` is the whole claim-lineage rule in one
question: rank the corpus for the claim, find the supporting passage, align
the draft's span to it, and scale the confidence by the claim's coverage. It
returns **one record per candidate** — a passage that supports nothing gets a
record with no quote and a confidence of `0.0`. Asking it once costs one
round trip; asking its parts separately cost `1 + 2 × top_k`.

The span is measured in **code points**, not UTF-16 units: `span_start` and
`span_end` index the draft the way Python's `len()` counts. A draft carrying
an astral character has more UTF-16 units than code points, and the span
must be clamped to the code-point length.

### 5.3 `word` — OOXML (3)

| Method | Params | Result |
| :--- | :--- | :--- |
| `word.manuscript` | `draft, bibliography, verified_only?` | `bytes_b64, document` |
| `word.read` | `bytes_b64, origin?, base_uri?` | `document, omissions: [..], package_relationships: [..]` |
| `word.write` | `document, deterministic?` | `bytes_b64, omissions: [..]` |

The document model **does** cross the seam, in the wire form
`kotlin/word/WIRE.md` pins: it is modelled once, in the Kotlin tier, and the
Python tier validates what arrives with its own Pydantic model, so a key that
is wrong on one side shows up as a validation error on the other. The wire
form of all seventeen model types is WIRE.md §2; §3 pins the three requests and
the three responses.

`word.manuscript` answers with **both** the bytes of the package and the
document it built, because the builder's answer *is* a document: the caller
that wants to render it uses the bytes, and the caller that wants to inspect
the structure uses the model, and neither should have to ask twice.

`word.write` answers with the writer's **omission register**: one
human-readable line per construct the writer could not represent. It is
always present, and it is empty when nothing was lost. The register is reset
before every render, so it describes the last write and not the last write
that happened to lose something.

A *top-level* binary payload is `bytes_b64`. A binary field *inside* a model
keeps its own name and holds base64: `WordMedia.data` and `WordPart.data` are
base64 strings under `data`.

### 5.4 `pdf` (2)

| Method | Params | Result |
| :--- | :--- | :--- |
| `pdf.text` | `path?` or `bytes_b64?` | `text, pages, page_texts: [..], limitations: [..]` |
| `pdf.metadata` | `path?` or `bytes_b64?` | `title, author, subject, keywords, creator, producer, creation_date, doi, arxiv_id, page_count, metadata` |

### 5.5 `ingest` (16)

| Method | Params | Result |
| :--- | :--- | :--- |
| `ingest.load` | `kind, origin?` or `bytes_b64?` | `text, page_count, byte_size, metadata, omissions: [..]` |
| `ingest.ingest` | `kind, id, title, origin?` or `bytes_b64?, doi?, arxiv_id?, pmid?, pdf_metadata?, byte_size?` | `source, chunks: [..], omissions: [..]` |
| `ingest.bibliography.resolve` | `resolver, source_id, source_kind, doi?, arxiv_id?, pmid?, pdf_metadata?, record?, source_ids?` | `record, error` |
| `ingest.bibliography.resolve_chain` | `resolvers: [{kind, record?, source_ids?}], source_id, source_kind, doi?, arxiv_id?, pmid?, pdf_metadata?` | `record` |
| `ingest.url_policy.assert_public` | `url` | `ok` |
| `ingest.url_policy.resolve_public_addresses` | `url` | `addresses: [{ip, port}]` |
| `ingest.url_policy.validate_source_id` | `source_id` | `source_id` |
| `ingest.url_policy.safe_upload_path` | `source_id, filename, base?` | `path` |
| `ingest.url_policy.upload_root` | `base?` | `path` |
| `ingest.source_store.save` | `root, source_id, filename, bytes_b64, max_bytes` | `path` |
| `ingest.source_store.list` | `root` | `files: [..], total_bytes` |
| `ingest.source_store.evict` | `root, max_bytes` | `removed: [..]` |
| `ingest.source_store.touch` | `root, source_id, filename` | `path` |
| `ingest.source_store.remove` | `root, source_id` | `removed` |
| `ingest.fetch_limited` | `url, max_bytes?` | `body_b64` |
| `ingest.limits` | `size` | `size, max_upload_bytes, max_url_response_bytes, max_resolver_response_bytes, max_source_store_bytes` |

**`base` and `root` carry the configuration, not the answer.** The tier runs in
its own process, so its environment is not the caller's. A caller that has
resolved `LMEDITOR_UPLOAD_DIR` for itself sends the *value* and the tier
resolves it; the store's `root` is the resolved directory and is sent with
every store call. Without this the two processes would disagree about where an
upload goes.

The bibliography chain is one seam method rather than one per resolver,
because the chain's rule — *try each resolver in turn, take the first that
answers, and report the failure of the last* — is the rule that matters.

### 5.6 `library` (21)

Every method takes a `root` — the directory the books live in — because the
root is a parameter of the store, not a setting of the process. A request that
names no root gets the configured one (`LMEDITOR_LIBRARY_DIR`, else
`<tmpdir>/lmeditor_books`).

| Method | Params | Result |
| :--- | :--- | :--- |
| `library.import_book` | `book_id, title, path?` or `bytes_b64?` | `manifest` |
| `library.import_sections` | `book_id, title, sections: [{section_id, title, content}]` | `manifest` |
| `library.export_book` | `book_id` | `bytes_b64` |
| `library.create` | `book_id, title` | `manifest` |
| `library.list_books` | — | `books: [..]` |
| `library.delete` | `book_id` | `ok` |
| `library.get_manifest` | `book_id` | `manifest` |
| `library.get_section` | `book_id, section_id` | `section_id, title, order, content, word_count, char_count, chunk_ids` |
| `library.read_section_record` | `book_id, section_id` | `record: {section_id, title, order, content, origin}` |
| `library.save_section` | `book_id, section_id, content` | `result` |
| `library.add_section` | `book_id, section_id, content, title?, order?, origin?` | `result` |
| `library.set_section_chunks` | `book_id, section_id, chunk_ids` | `result` |
| `library.put_chunks` | `book_id, chunks` | `ok` |
| `library.chunks` | `book_id` | `chunks: [..]` |
| `library.read_book_origin` | `book_id` | `origin` |
| `library.set_book_origin` | `book_id, origin` | `ok` |
| `library.update_brief` | `book_id, brief` | `ok` |
| `library.build_brief` | `book_id, max_chars?` | `brief` |
| `library.merge_brief` | `current, addition, max_chars?` | `brief` |
| `library.build_working_set` | `book_id, section_id, ranked_passages?, neighbours?, max_passages?, max_brief_chars?, max_section_chars?` | `working_set` |
| `library.read_count` | — | `read_count` |

`library.add_section` is the tier's upsert: it creates the section when it is
not there yet and replaces it when it is. The Python side used to write the
index entry itself, which meant two writers for one file format.

`library.export_book` answers with the **bytes** of a `.docx` package. The
bytes are the canonical form of the package, so the round trip through the
reader is faithful.

### 5.7 `workflow` (2)

| Method | Params | Result |
| :--- | :--- | :--- |
| `workflow.create_initial_state` | `doc_id, topic, target_audience?, raw_texts?, sources?, guidelines?, max_retries?, threshold_f_beta?` | `state` |
| `workflow.route` | `route?, section_drafts?, retry_counts?, max_retries?, hitl_approved?` | `next_step, reason, exhausted_sections?` |

### 5.8 `editing` (3)

| Method | Params | Result |
| :--- | :--- | :--- |
| `editing.validate_revision` | `revised_text, section, chunks` | `accepted, reason, cited_chunk_ids, claims` |
| `editing.plan_edit` | `request, section, chunks, ranked_passages?, max_passages?` | `instruction, original_text, passages` |
| `editing.apply_edit` | `request, section, revised_text, refusal_reason?, evaluated_claims?, evaluator_name?, beta?, threshold_f_beta?, chunks, index_chunks?, ranked_passages?, use_index?, max_passages?` | `result` |

The *generation* of a revision is a model call and stays in Python; the
*acceptance* rule — is every citation grounded in a chunk that exists — is
deterministic and lives in Kotlin. The claims come back with no verdict on
them: the entailment model is the Python side's business, and its answer goes
to `text.citation_check.audit`.

### 5.9 `factcheck` (9)

| Method | Params | Result |
| :--- | :--- | :--- |
| `factcheck.relevance` | `claim_text, evidence_text` | `relevance` |
| `factcheck.evidence_limit` | `max_evidence, requested_max_evidence` | `limit` |
| `factcheck.validate_max_evidence` | `max_evidence` | `valid` |
| `factcheck.aggregate` | `doc_id, text, claims, verdicts, web_searched?, evidence_found?, notes?, beta?, evaluator_name?` | `report, scorecard` |
| `factcheck.web.search` | `query, limit?, timeout?, max_bytes?` | `results: [{url, title, snippet}]` |
| `factcheck.web.fetch_page_text` | `url, timeout?, max_bytes?` | `text` |
| `factcheck.web.parse_search_results` | `body_b64` | `results: [{url, title, snippet}]` |
| `factcheck.web.normalise_result_url` | `href` | `url` |
| `factcheck.web.extract_page_text` | `body_b64` | `text` |

The retrieval and the judgement of each claim happen in Python, because the
corpus index and the entailment model are the Python side's business. What
the report *says* is a rule, and the rule is Kotlin's: the verdict, the
scorecard and the summary are all derived from the verdicts that cross the
seam, so the scorecard and the per-claim verdicts cannot disagree.

The **web evidence** is the opposite case: it is entirely deterministic — a
URL is built from a literal host, a result page is parsed with a tolerant
scanner, and a page is reduced to its text — so all of it is Kotlin's. The
transport is the tier's `ingest.fetch_limited` and `ingest.url_policy.*`, so
the SSRF policy is one implementation and not two.

### 5.10 `discovery` (14)

| Method | Params | Result |
| :--- | :--- | :--- |
| `discovery.outline` | `topic, chunks, research_memory?, perspectives?` | `outline` |
| `discovery.outlineSections` | `topic, sections, chunks` | `outline` |
| `discovery.perspectives` | `topic, count?, context?` | `perspectives` |
| `discovery.source.tokenize` | `text` | `tokens: [..]` |
| `discovery.source.contentTokens` | `text` | `tokens: [..]` |
| `discovery.source.salientTerms` | `text, limit?` | `terms: [..]` |
| `discovery.source.salientTermsAcross` | `texts: [..], limit?` | `terms: [..]` |
| `discovery.source.splitSentences` | `text` | `sentences: [..]` |
| `discovery.source.extractFindings` | `text, limit?` | `findings: [..]` |
| `discovery.source.detectHeadings` | `text` | `headings: [..]` |
| `discovery.source.titleCaseTerms` | `terms: [..]` | `title` |
| `discovery.source.clusterByTerms` | `texts: [..], num_clusters` | `clusters: [[..]]` |
| `discovery.dialogue.questions` | `perspective, num_turns?` | `questions: [..]` |
| `discovery.dialogue.memory` | `perspectives: [{perspective, turns: [{question, chunks: [..], framing}]}]` | `memory` |

The source analysis is pure text work — tokenise, find the salient terms,
split the sentences, cluster — and it is Unicode-exact: Python's `\w` is not
Java's `\w`, and the corpus this runs over is scientific prose, so the
tokenisation is part of the contract and is pinned by a test that uses
non-ASCII input.

The dialogue is split the other way. The **plan** — which questions to ask,
which passage answers which, and what the session concluded — is a rule and is
Kotlin's. The **model** is Python's: it reads the passages and writes one
framing sentence, and the quotations that carry the evidence are the tier's.
A model can therefore add framing but never an uncited claim.

### 5.11 `server` (7)

| Method | Params | Result |
| :--- | :--- | :--- |
| `server.sources.add` | `source` | `source` |
| `server.sources.get` | `source_id` | `source` |
| `server.sources.remove` | `source_id` | `removed` |
| `server.sources.list` | — | `sources: [..]` |
| `server.sources.chunks_for` | `source_ids` | `chunks: [..]` |
| `server.sources.clear` | — | `ok` |
| `server.sources.len` | — | `count` |

The source library is the server's memory of what has been ingested. It was a
Python dictionary in the server process; it is now one registry in the
long-lived tier. The eviction rule, the ordering and the chunk lookup are the
tier's, because the tier holds the working set they are rules about.

### 5.12 `vault` — the rules of a vault (8)

Every method takes a `root` — the folder the notes live in — because the tier
keeps no state of its own: the server holds the active vault, and the tier is
told which folder to work in. A request that names no root is refused with
`ValidationError`.

| Method | Params | Result |
| :--- | :--- | :--- |
| `vault.list_notes` | `root` | `notes: [..]` |
| `vault.read_note` | `root, note_id` | `note` |
| `vault.write_note` | `root, note_id, content` | `note` |
| `vault.create_note` | `root, note_id, title, content` | `note` |
| `vault.delete_note` | `root, note_id` | `deleted` |
| `vault.rename_note` | `root, from_id, to_id, update_links` | `from_id, to_id, updated_links` |
| `vault.search` | `root, query, limit` | `results: [..]` |
| `vault.tags` | `root` | `tags: [..]` |

A `note_id` is the note's path inside the vault without the `.md`, with `/`
separators. A note id that is not a safe path inside the vault is refused with
`UnsafeNoteIdError`, a note that is not there with `NoteNotFoundError`, and a
folder that is not there with `VaultNotFoundError`. A refusal is never an
empty result that looks like a success.

A vault is a folder of markdown files and nothing else: there is no index and
no database, so the operator's own edits — made in Obsidian, or in a text
editor — are seen the next time the vault is read. Hidden files and folders
are never read, and a note is never written through a link that points out of
the vault.

### 5.13 `notebooks` — the rules of a notebook (9)

Every method takes a `root` — the folder the notebooks live in — because the
tier keeps no state of its own: the server holds the open notebooks folder,
and the tier is told which folder to work in. A request that names no root is
refused with `ValidationError`, because a notebooks folder that was never
named is not an empty one.

| Method | Params | Result |
| :--- | :--- | :--- |
| `notebooks.list` | `root` | `notebooks: [..]` |
| `notebooks.read` | `root, notebook_id` | `notebook` |
| `notebooks.create` | `root, notebook_id?, title, description` | `notebook` |
| `notebooks.set_metadata` | `root, notebook_id, title, description` | `notebook` |
| `notebooks.add_source` | `root, notebook_id, source_id` | `notebook` |
| `notebooks.remove_source` | `root, notebook_id, source_id` | `notebook` |
| `notebooks.add_note` | `root, notebook_id, note_id` | `notebook` |
| `notebooks.remove_note` | `root, notebook_id, note_id` | `notebook` |
| `notebooks.delete` | `root, notebook_id` | `deleted` |

A `notebook_id` is a single safe path segment, not a path: one that is not
safe is refused with `UnsafeNotebookIdError`, a notebook that is not there
with `NotebookNotFoundError`, and a manifest that cannot be read or written
with `NotebookError`. A refusal is never an empty result that looks like a
success.

A notebook lives at `<root>/<notebook_id>/manifest.json`, and the manifest is
the core `Notebook` model written as JSON — the same shape the seam carries —
so the file on disk and the answer over the seam cannot disagree. Creating a
notebook that is already there returns the notebook that is there and never
overwrites what the operator wrote; creating one with no id derives the id
from the title, so the same title always yields the same notebook.

The store owns the *membership* rules and nothing else: a source is added by
id and removed by id, a note is added by the id of a note that lives in the
vault, and adding a note the notebook already holds changes nothing. The
sources and the notes themselves are the operator's, and this store does not
read, write or delete them.


---

## 6. Schema on the wire

The `core` schema classes travel as JSON objects with **snake_case** keys,
the same keys the Python Pydantic models use, so the Python side can validate
a Kotlin result with its own model and get the same guarantees it gets today.

| Kotlin | Wire |
| :--- | :--- |
| `SourceChunk.chunkId` | `chunk_id` |
| `SourceChunk.documentId` | `document_id` |
| `SourceChunk.charStart` | `char_start` |
| `SourceChunk.byteEnd` | `byte_end` |
| `Claim.entailmentStatus` | `entailment_status` |
| `Claim.citedChunkIds` | `cited_chunk_ids` |
| `SectionDraft.isVerified` | `is_verified` |
| `CitationLineage.spanStart` | `span_start` |

Enums travel as their `wire` string (`"entailed"`, `"needs_revision"`), never
as their Kotlin name.

---

## 7. Where the bridge lives

- Kotlin side: `kotlin/bridge/` (module `:bridge`), entry point
  `lmeditor.bridge.BridgeMain`.
- Python side: `backend/src/lmeditor/bridge/` — the client that spawns the
  JVM process and performs the calls.
- The jar the client runs is located, in order, from:
  1. the `LMEDITOR_KOTLIN_BRIDGE` environment variable;
  2. `kotlin/bridge/build/libs/` in this repository (the development default);
  3. `kotlin/bridge/build/install/` for an installed distribution.

If none of them yields a jar, the client raises a typed error naming the
paths it tried. It never silently falls back to the Python implementation —
a silent fallback would hide exactly the drift this tier exists to remove.
