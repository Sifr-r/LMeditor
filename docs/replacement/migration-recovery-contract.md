# Migration, revision, conflict and recovery contract

Status: binding contract for roadmap milestone **M1 — trustworthy foundation**.
Owner: the architecture lead (see `docs/replacement/m0-blueprint.md` §4, file row
`docs/replacement/migration-recovery-contract.md`).
Scope: the rules M1 implements for *what happens when two things change the same
bytes, what survives a crash, and what a restore actually restores*.

Binding inputs, in force where they overlap with this document:

- `docs/replacement/m0-blueprint.md` §2 (requirement identity), §5 (evidence rules).
- `docs/product-replacement-roadmap.md` §3 (shared foundation), §9 (milestone table),
  §10 (release evidence).
- `kotlin/CONTRACTS.md` §6 (errors), §8 (idempotency), §10 (text and encoding),
  §11 (determinism).
- `ARCHITECTURE.md` — the repository ledger of existing storage semantics.

Where the roadmap's §2 baseline and the working tree disagree, **the tree wins**.
Every such disagreement is listed in §11.1 with a citation.

---

## 0. Conventions

**MRC-n.m** — a normative rule. Numbering is `MRC-<section>.<ordinal>` and is stable:
§10 maps requirement ids to these numbers, so a rule is never renumbered once
published; a withdrawn rule is marked `withdrawn` and keeps its number.

**`[current]`** — the rule is enforced by code that exists in the tree today, and the
citation proves it.

**`[proposed]`** — the rule is new work for M1. It does not exist today. An implementer
reading `[proposed]` rules is reading a specification, not a description.

**`[new error]`** — a typed error that does not exist in the vocabulary today. Every
such marker names the decision in §11 that must be answered before the error can be
written. No rule may invent a typed error outside `lmeditor.core`
(`kotlin/CONTRACTS.md:219-221`); where a module-local error already carries the
condition, the rule names the existing one.

**Evidence.** Every claim about current behaviour cites `path:line`
(`docs/replacement/m0-blueprint.md:190-191`). Claims about *absence* state the search
that established them, not a guess (`docs/replacement/m0-blueprint.md:195-197`).

---

## 1. Scope and vocabulary

### 1.1 What this document governs

This contract governs **durable state**: the bytes LMeditor writes to the operator's
disk and the meaning of a write that has been reported as successful. It does not
govern rendering, layout, inference, or the HTTP wire format.

In scope: the notebook manifest, the vault note file, the library book directory, the
uploaded-source store, the workspace manifest, revision snapshots, the recovery
journal, backups and persistent job records.

Out of scope, named so an implementer does not extend it by accident: the DOCX model
and its export path (`kotlin/word`), the search index rebuild policy beyond its
retention (`kotlin/vault`), the SSE envelope (`backend/src/lmeditor/server/session.py`),
and the desktop process lifecycle except where it is the thing that dies in a crash
test (§6).

### 1.2 Vocabulary

| Term | Definition | State today |
| :--- | :--- | :--- |
| **workspace** | The operator's root directory tree. It owns the documents, notes, notebooks, sources and artifacts of one installation. A workspace is the unit that is opened, backed up, restored and migrated. | **Partial.** Four independent roots exist with no common parent and no shared identity: the vault root (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:164`), the notebook root (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:336`), the library root (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:716`), and the upload root (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/UrlPolicy.kt:138`). No workspace concept exists. |
| **workspace id** | A stable, opaque identifier for one workspace, minted once and stored in the workspace manifest. It survives moving the folder and reinstalling the application. | **New.** |
| **artifact** | Any durable object the workspace owns and can restore independently: a notebook manifest, a vault note, a book, an uploaded source original, a generated artifact, a conversation record, a job record. An artifact is addressed by id and versioned by revision. | **Partial.** The vocabulary does not exist; the objects listed do (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:22`, `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:20`, `kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:485`). |
| **artifact id** | The stable, opaque identifier of an artifact. Never derived from a display title, never changed when the artifact is renamed or retitled. | **Partial — and partly violated today.** See MRC-2.1. |
| **revision** | A monotonically increasing integer, per artifact, incremented on every committed change. It is the version of the artifact's *content*, not of its name. | **New.** No revision counter exists in any store. |
| **revision precondition** | The revision the editor believes it is editing, carried on every write. The write succeeds only if the artifact's current revision still equals the precondition. This is the compare-and-swap of the write contract. | **New.** No store accepts one. |
| **conflict** | The typed outcome of a write whose revision precondition does not hold, or whose target was changed outside the application. A conflict is a *reported* state, not a silent overwrite and not a merged result. | **New.** No conflict detection exists; a vault write overwrites unconditionally (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:485-498`). |
| **recovery point** | A named, durable state from which work can be resumed or restored. This document defines seven (§6). | **New**, except the acknowledged write. |
| **acknowledgement** | The point at which the application tells the operator, the UI or the calling tier that a write has succeeded. | **Partial.** The UI is told a save succeeded (`backend/src/lmeditor/server/routes.py:1446`); nothing today guarantees the bytes were durable first. |
| **durable write** | A write whose bytes have reached stable storage: written to a temporary file, flushed, and atomically moved into place, with the containing directory flushed. A durable write survives process death and, with the directory flush, power loss. | **Partial.** Atomic replacement exists in two stores but without a flush (§3.1). |
| **acknowledged write** | A write that is durable *before* it is acknowledged. The acknowledgement is the last step, never the first. An acknowledged write is therefore guaranteed to survive a crash; a write that is not acknowledged is guaranteed to leave the prior revision intact. | **New.** |
| **recovery journal** | The durable record of in-flight multi-file operations and unsaved drafts, used to detect and resolve half-finished work on the next open (§4.3). | **New.** |

### 1.3 Rules

**MRC-1.1** `[current]` A store write is idempotent: writing the same content twice
produces the same bytes (`kotlin/CONTRACTS.md:254-255`). This is binding and is not
re-decided here.

**MRC-1.2** `[current]` All durable text is UTF-8 and is read and written through the
explicit UTF-8 charset, never the platform default
(`kotlin/CONTRACTS.md:273-275`; `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:407`).

**MRC-1.3** `[current]` All durable bytes are ordered deterministically: a manifest's
key order, a directory listing and a chunk order are stable, so two stores holding the
same logical content produce the same bytes
(`kotlin/CONTRACTS.md:286-287`; the manifest is written from an ordered map at
`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:441`).

**MRC-1.4** `[proposed]` The workspace is the **only** unit that is opened, migrated,
backed up and restored. A store may not be adopted by a workspace it does not belong
to, and a backup that contains one store's artifacts is not a workspace backup.

**MRC-1.5** `[proposed]` Every durable object in the tree is classified as exactly one
of: *artifact* (restorable, revisioned), *index* (rebuildable, not restorable), or
*journal* (scratch, evictable). A store that does not know which class an object is
cannot be given a retention rule (§7).

**MRC-1.6** `[proposed]` A revision is an integer, not a hash and not a timestamp. It
increments by one on every committed change to an artifact, including a change that
only alters metadata.

---

## 2. Identity and schema versioning

### 2.1 Id minting

**MRC-2.1** `[proposed]` An artifact id is opaque and minted at creation. It is
**never** derived from a display title, and it never changes when the artifact is
renamed or retitled.

*Why this is a rule and not a preference:* a title-derived id makes identity a function
of mutable text. Today's notebook id **is** a slug of the title
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookPath.kt:135-141`), and a
collision is resolved by appending a suffix that depends on what already exists
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:352-361`). The
result is that two notebooks with the same title have ids determined by creation order
— the test that pins this behaviour is
`kotlin/notebooks/src/test/kotlin/lmeditor/notebooks/NotebookStoreTest.kt:58`. Under
MRC-2.1 that behaviour is replaced, not preserved.

**MRC-2.2** `[proposed]` The vault note id is **not** the note's path. Today it is:
`validateNoteId` documents that "a note id is a path"
(`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:87-91`), and `resolve` turns
it into a file (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:164`). A
rename therefore **changes the id** of the note it renames
(`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:186-213`), which breaks every
reference that was made to the old id. M1 adds a stable `uid` to the note's frontmatter
and keeps the path as the *addressing* scheme. See §11 D-7.

**MRC-2.3** `[proposed]` A caller-supplied id is accepted only after boundary
validation, and the validation already used by the library applies: a blank, unsafe or
over-long identifier is refused with `UnsafeIdentifierError`
(`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:57`,
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:189`), a notebook
identifier with `UnsafeNotebookIdError`
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookErrors.kt:26`) and a vault
note identifier with `UnsafeNoteIdError`
(`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:33`).

**MRC-2.4** `[proposed]` The workspace id is minted once when the workspace is
initialised and is never re-minted. Re-initialising an existing workspace does not
change its id, and copying a workspace folder to another machine preserves the id
unless the operator explicitly asks for a new workspace.

### 2.2 Schema versions

**MRC-2.5** `[proposed]` Every durable manifest carries a `schema_version` field
holding the semver string of the format it was written in. The field lives in the
manifest itself, so a version is readable without consulting code.

*Current state:* **no schema version exists anywhere in the tree.** A search for
`schema_version|schemaVersion` across `*.kt`, `*.py` and `*.json` returns no matches.
The manifests that exist today are unversioned JSON: the notebook manifest
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:441`), the book
manifest and its companions (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:661-666`,
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:668-686`,
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:700-709`) and the upload
store's files (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:54`).

**MRC-2.6** `[proposed]` The version is **per store format**, not one global number. A
book manifest and a notebook manifest evolve independently; each names its own
`min_supported_version` and `max_supported_version` in code.

**MRC-2.7** `[proposed]` A version bump is required when a change would make an
existing on-disk file unreadable, or would change the *meaning* of a field that is
already read. Adding an optional field with a defined default is **not** a bump.

**MRC-2.8** `[proposed]` A reader must accept every version in
`[min_supported_version, max_supported_version]` and must not depend on a version
being the newest. A writer writes `max_supported_version`.

### 2.3 A store whose on-disk schema is older than the code

**MRC-2.9** `[proposed]` When a store is opened and its on-disk `schema_version` is
**newer** than the running code's `max_supported_version`, the application refuses to
write to it and refuses to migrate it, and reports the mismatch with the typed error
named in MRC-2.10. Downgrade is never attempted.

**MRC-2.10** `[proposed]` A schema mismatch raises `ValidationError` with a `field` of
`schema_version` and a `value` carrying the offending version
(`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:207`; the Python twin is
`backend/src/lmeditor/core/exceptions.py:191`). `ValidationError` is the existing
typed error for "a value that does not satisfy a constraint" and is the correct
existing vocabulary for this case.

**MRC-2.11** `[proposed]` When a store's on-disk `schema_version` is **older** than
`min_supported_version`, the store is migrated under §8 — never read as-is and never
silently upgraded in place.

**MRC-2.12** `[proposed]` A store caught mid-migration (a migration marker present, see
MRC-8.6) is not opened as a normal store. The application reports the interrupted
migration and offers restore-from-backup (§6, MRC-6.9). It never resumes a migration
in place without the operator's consent.

---

## 3. The write contract

### 3.1 Atomic replacement

**MRC-3.1** `[proposed]` A durable write replaces the target atomically: the
new bytes are written to a temporary file **in the same directory as the target**, the
temporary file is flushed, and it is moved onto the target with `ATOMIC_MOVE`.

*Current coverage:* the library store does this —
`atomicWriteText` writes `<name>.tmp` beside the target and moves it with
`ATOMIC_MOVE`, falling back to `REPLACE_EXISTING`
(`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883-900`), and every
manifest, section and chunk file goes through it
(`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:661-666`,
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:668-686`,
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:700-709`). The notebook
manifest does this — `writeManifest` calls the same shape of `atomicWriteText`
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:437-449`,
`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:470-487`).

*Current gap:* the **vault does not**. `writeFile` calls `Files.writeString` directly
onto the live note file (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:485-498`),
so a crash during a note write truncates the note. The **upload store does not**:
`save` calls `Files.write` directly
(`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50-58`).

**MRC-3.2** `[proposed]` Every durable write in the workspace uses the MRC-3.1
mechanism, including: vault note writes, vault link rewrites (MRC-4.7), upload store
writes, workspace manifest writes, revision snapshot writes, journal writes, backup
writes and job record writes.

**MRC-3.3** `[proposed]` The temporary file is written in the target's own directory.
A temporary file in a different directory is not a valid substitute: the rename is
then not atomic across the volume boundary, and the rename can fail with
`AtomicMoveNotSupportedException` for reasons unrelated to the data.

**MRC-3.4** `[proposed]` The temporary file is flushed (`fsync` on the file) **and the
containing directory is flushed** before the move is acknowledged as durable. Without
the directory flush, an atomic rename can still be lost by a power failure.

*Why this is a separate rule:* the existing atomic writes are atomic against **process
death**, not against **power loss** — neither
`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883-900` nor
`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:470-487` flushes
the file or the directory. M1's exit condition ("Restart/crash/restore tests preserve
acknowledged work", `docs/product-replacement-roadmap.md:193`) is only satisfiable if
"durable" means the stronger sense.

**MRC-3.5** `[proposed]` A leftover temporary file is evidence of an interrupted write.
On the next open of the owning store, a temporary file beside a target is removed only
after the target has been verified as readable; if the target is absent or
unparseable, the temporary file is **kept** and reported, not silently deleted. The
report is a `NotebookError` (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookErrors.kt:13`),
`VaultError` (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:20-23`) or
`LibraryError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:372`) carrying a
`reason` of `interrupted_write`, according to the store. See §11 D-3 for the
cross-store variant.

**MRC-3.6** `[current]` A store that cannot be read is an error, never an empty
result that looks like success. The notebook manifest is the worked example: a
manifest that cannot be parsed raises `NotebookError`
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:414-421`; test
`kotlin/notebooks/src/test/kotlin/lmeditor/notebooks/NotebookStoreTest.kt:236`).

### 3.2 The revision precondition

**MRC-3.7** `[proposed]` Every write to an existing artifact carries a **revision
precondition**: the revision the editor read. A write against a new artifact carries
no precondition and always succeeds.

**MRC-3.8** `[proposed]` The store compares the precondition against the artifact's
current revision **while holding the store's write lock**, and performs the replace
inside the same critical section. A compare and a replace separated by a lock release
is not a compare-and-swap.

*Current concurrency vocabulary:* `ReentrantLock` with small critical sections is the
tier's stated pattern (`kotlin/CONTRACTS.md:261-267`), and the server's source registry
already uses it (`kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:72`).

**MRC-3.9** `[proposed]` When the precondition does not hold, the write does **not**
touch the artifact, does not bump the revision and does not write a partial file. The
typed outcome is MRC-5.2.

**MRC-3.10** `[proposed]` A write that carries no precondition against an artifact
that **already exists** is refused. Blind overwrite is not reachable through the
public write API; a caller that genuinely wants to replace an artifact must read it
first and present the revision it read, or present an explicit force flag that is
recorded in the artifact's revision history.

### 3.3 Idempotence of a write

**MRC-3.11** `[current]` Writing the same content twice produces the same bytes
(`kotlin/CONTRACTS.md:254-255`). The library pins this
(`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:661-666` and the vault's
`writingTheSameContentTwiceLeavesTheSameBytes` test at
`kotlin/vault/src/test/kotlin/lmeditor/vault/VaultStoreTest.kt:45`).

**MRC-3.12** `[proposed]` A write is idempotent in the *stronger* sense required by
retries: **replaying a write that already succeeded is a no-op that reports success**.
A retry carries the same precondition and the same content; the store sees the
precondition already superseded by its own previous attempt, recognises the identical
content, and reports success without creating a second revision.

*Why:* F.06 requires "safe retries … without duplicate artifacts"
(`docs/product-replacement-roadmap.md:43`). A store that only satisfies MRC-3.11
produces one revision per retry, because each retry is a distinct commit.

**MRC-3.13** `[proposed]` Idempotence is keyed on **content**, not on the request. Two
different requests that produce the same bytes are the same write. This keeps the
determinism rule (`kotlin/CONTRACTS.md:286-290`) intact: no request id, timestamp or
random value enters a revision.

### 3.4 Acknowledgement

**MRC-3.14** `[proposed]` A write is acknowledged **only after** it is durable, in
this order: bytes written → file flushed → directory flushed → target atomically
replaced → revision incremented and persisted → *then* acknowledged.

**MRC-3.15** `[proposed]` Acknowledgement is never emitted for a write that failed at
any step. A failed write leaves the artifact at its previous revision, with its
previous bytes intact, and reports the typed error of §3.5.

**MRC-3.16** `[proposed]` An **acknowledged write** is the only thing the exit
condition protects: after a crash, every acknowledged write is present at the revision
it was acknowledged at. The converse is also a rule: a write that was **not**
acknowledged may or may not be present, but if it is present it is complete and
well-formed — never half-written. This is the sense in which "acknowledged" and
"durable" differ, and the two words are not interchangeable in this document.

*Current state:* no store distinguishes the two. The UI is told a save succeeded from
the route handler (`backend/src/lmeditor/server/routes.py:1446`) without any durability
step between the file write and the response. See MRC-3.4 for the flush that is
missing.

### 3.5 Failure taxonomy

Every failure below names the typed error it raises. Where the error does not exist
today, the row is marked and points at a decision in §11.

| Rule | Failure | How it is detected | Typed error | Operator-visible result | State |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **MRC-3.17** `[proposed]` | **Disk full** (`ENOSPC`) | The write throws `IOException` from `Files.writeString` / `Files.write`. | `StorageWriteError` **`[new error]`** with `details["reason"] = "disk_full"` (§11 D-2). Until it exists, the module-local error carries it: `VaultError` (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:20-23`), `NotebookError` (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookErrors.kt:13`) or `LibraryError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:372`), each with a `reason` key. | The write is refused; the artifact keeps its previous revision; the operator is told the disk is full and which store could not be written. | proposed |
| **MRC-3.18** `[proposed]` | **Permission denied** | The write or the directory creation throws `IOException` / `AccessDeniedException`. | `StorageWriteError` **`[new error]`** with `details["reason"] = "permission_denied"` (§11 D-2). | Same as MRC-3.17, naming the path. | proposed |
| **MRC-3.19** `[proposed]` | **Process killed mid-write** | Not detected at write time. Detected on the next open: a temporary file sits beside the target (MRC-3.5), or a journal intent has no commit record (MRC-4.4). | `IncompleteOperationError` **`[new error]`** (§11 D-3); until it exists, the store's own error with `reason = "interrupted_write"` as in MRC-3.5. | The store reports what was interrupted and offers complete-or-roll-back (§4.6). Nothing is silently repaired. | proposed |
| **MRC-3.20** `[proposed]` | **Concurrent write** — another writer changed the artifact between the read and the write | The revision comparison in MRC-3.8 fails. | `RevisionConflictError` **`[new error]`** (§11 D-1) with `details` keys `artifact_id`, `expected_revision`, `actual_revision`, `outcome`. | The operator is offered the conflict outcomes of §5. | proposed |
| **MRC-3.21** `[proposed]` | **Concurrent write** — two writers inside one process | The store's write lock serialises them; the loser sees MRC-3.8 fail. | Same as MRC-3.20. | Same as MRC-3.20. | proposed |
| **MRC-3.22** `[current]` | **Partial network failure** during a source fetch | The transfer aborts mid-stream, or the response ends early. | `SourceLoadError` **`[current]`** — `kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:255`, Python twin `backend/src/lmeditor/core/exceptions.py:253`. | The source is not archived; nothing partial is registered; the operator is told the source could not be loaded. | current |
| **MRC-3.23** `[current]` | **Partial network failure** during an upload | The transfer aborts before the store has the whole body. | `SourceUploadTooLargeError` for a size cap **`[current]`** (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:324`, `backend/src/lmeditor/core/exceptions.py:346`); a truncated-body abort is `SourceLoadError` **`[current]`**. The partial file is not left in the store. | The upload is refused; the store's byte budget is unchanged. | current |
| **MRC-3.24** `[current]` | **A malformed artifact** | The reader rejects the file. | The domain error that names it (`kotlin/CONTRACTS.md:239-240`): `WordParseError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:271`), `NotebookError`, `VaultError`, `LibraryError`. | The artifact is reported as unreadable; the previous revision, if any, is untouched. | current |

**MRC-3.25** `[proposed]` No failure in §3.5 is ever reported as success, and none is
ever swallowed. A caught exception is turned into a typed domain result that records
the failure so the caller can see it (`kotlin/CONTRACTS.md:222-225`).

**MRC-3.26** `[proposed]` A partial network failure **never** produces an
acknowledged write. The write is acknowledged only after the bytes are local and
durable (MRC-3.14); a fetch that half-succeeded is a failed fetch.

**MRC-3.27** `[proposed]` A write that fails part-way through a multi-file operation
does not report the operation as failed *and* leave the store in a state the store
cannot describe. §4 governs.

---

## 4. Multi-file operations

### 4.1 Which operations are multi-file

**MRC-4.1** `[proposed]` The following operations are **multi-file operations** and
must be journalled (§4.3):

1. **Notebook manifest plus the sources it names.** A notebook records `source_ids`
   (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:176`,
   `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:211`); the
   manifest and the source archives it points at must not disagree.
2. **Vault rename plus the backlinks it rewrites.**
   `renameNote` moves the file (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:204`)
   and *then* rewrites the links that point at it
   (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:211`). Today a crash
   between those two steps leaves a renamed note whose backlinks still name the old
   path — the operation is exactly half done and nothing records that it was.
3. **Document save plus its export.** The section body is written
   (`backend/tests/test_document_save.py:133`) and the export is produced separately
   (`backend/tests/test_document_save.py:175`); the two must agree.

**MRC-4.2** `[proposed]` Any other operation that touches more than one durable file
and can be observed half-finished is a multi-file operation, whether or not it is
listed above.

### 4.2 Transaction boundary and write order

**MRC-4.3** `[proposed]` The transaction boundary of a multi-file operation is **one
journal entry**. There is no cross-file atomicity; the journal is what makes the
sequence recoverable.

**MRC-4.4** `[proposed]` Write order is fixed:

1. Write the journal **intent** record: operation id, operation kind, the artifacts
   it will touch, their pre-operation revisions, and the paths it will write.
2. Write the **payload** files (the new note bodies, the new source archive, the new
   section text), each by the MRC-3.1 mechanism.
3. Write the **commit point** — the manifest or index that makes the new state
   visible — by the MRC-3.1 mechanism.
4. Write the journal **commit** record.
5. Close and later evict the journal entry (§7).

**MRC-4.5** `[proposed]` The **manifest is the commit point**. A reader never observes
a half-written operation, because it reads the manifest and the manifest is written
last and atomically. A reader that opens a store mid-operation sees the *previous*
consistent state, not a mixture.

*Current partial precedent:* the notebook manifest is already the last thing written
in its own store, and the store refuses a manifest that cannot be parsed
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:414-421`).
That is the right shape; it is not yet a transaction.

### 4.3 Detecting a half-finished operation

**MRC-4.6** `[proposed]` A journal entry in state `intent` with no matching `commit`
record is a **half-finished operation**. It is detected on the next open of the
owning store, before the store serves any request.

**MRC-4.7** `[proposed]` The journal entry records enough to **complete** the
operation: the target paths and their intended content hashes, and the pre-operation
copies of every file the operation overwrote or moved. The pre-operation copies are
what make **rollback** possible.

**MRC-4.8** `[proposed]` For a vault rename, the journal records the old path, the new
path, the set of notes whose links were rewritten, and the pre-operation bytes of
each of those notes. The link rewrite is then a set of ordinary atomic writes
(MRC-3.2) that can be replayed.

### 4.4 Completing or rolling back

**MRC-4.9** `[proposed]` On detecting a half-finished operation, the application
**completes** it when the operation is idempotent and replayable (MRC-3.12), and
**rolls it back** when it is not. The choice is made by a fixed rule per operation
kind, not per incident:

| Operation | Default on detection | Why |
| :--- | :--- | :--- |
| Notebook manifest + sources | **Complete** — rewrite the manifest from the journalled intent. | The manifest write is atomic; if it did not land, the previous manifest is intact and the new one is replayable. |
| Vault rename + backlink rewrite | **Complete** — finish the link rewrite from the journalled note set. | The move already happened; reverting it would have to move the file again and could fail a second time. |
| Document save + export | **Complete** the save, then re-run the export. | The export is derived from the saved section and is safe to regenerate. |

**MRC-4.10** `[proposed]` Rollback is used when the operation touched a file whose
pre-operation copy is required but is missing or unreadable. Rollback restores the
pre-operation copies atomically (MRC-3.1) and leaves the store at its pre-operation
revision.

**MRC-4.11** `[proposed]` The application **never** silently repairs a half-finished
operation. It reports it, with the typed error of MRC-3.19, and states which of
complete-or-roll-back it applied and why.

**MRC-4.12** `[proposed]` A half-finished operation is detected **before** the store
serves a read. A store with an open journal entry answers reads from the last
consistent state, not from a mixture.

---

## 5. Conflict semantics

### 5.1 The outcome taxonomy

**MRC-5.1** `[proposed]` A write against an existing artifact produces exactly one of
four outcomes. There is no fifth outcome and no silent overwrite.

| Outcome | Condition | Typed error | HTTP |
| :--- | :--- | :--- | :--- |
| **NoConflict** | The artifact's current revision equals the precondition. | — | `200` / `201` |
| **PreconditionMismatch** | The artifact's current revision is **greater than** the precondition: the artifact was changed, through this application or another, after the editor read it. | `RevisionConflictError` **`[new error]`** (§11 D-1) | `409` |
| **ExternalModification** | The artifact's bytes on disk differ from the bytes the editor read, but the store cannot attribute the change to a revision it recorded (an external editor, a sync client, a manual edit). | `RevisionConflictError` **`[new error]`** (§11 D-1) | `409` |
| **DeletedByOther** | The artifact no longer exists. | The store's not-found error, which already exists: `NoteNotFoundError` (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultStore.kt:22`), `NotebookNotFoundError` (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookErrors.kt:44`), `BookNotFoundError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:377`), `SectionNotFoundError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:385`). | `404` |

**MRC-5.2** `[proposed]` `PreconditionMismatch` and `ExternalModification` are
distinguished by *how* the change is known, and both surface the same
`RevisionConflictError` type with a different `details["outcome"]` value. The
distinction matters because it decides whether a merge is even possible: an external
modification has no recorded revision to compare against, only bytes.

**MRC-5.3** `[proposed]` A store that cannot determine the current revision of an
artifact treats the artifact as externally modified, not as unchanged. Uncertainty is
resolved towards reporting a conflict.

### 5.2 What the operator sees, and what survives

| Outcome | What the operator sees | What is preserved | Merge attempted? | Conflict copy written? |
| :--- | :--- | :--- | :--- | :--- |
| **NoConflict** | The write succeeds; the new revision is shown. | The previous revision, in the revision history (§7). | No | No |
| **PreconditionMismatch** | A conflict notice naming the artifact, the revision the editor read and the revision the artifact is at; a diff against the revision the editor read; the offered choices of §5.3. | The artifact's current content (unchanged); the editor's draft (in the draft journal, §6.2); both revisions in the revision history. | Offered, never automatic (§5.4). | Yes, when the editor chooses to keep its draft (§5.5). |
| **ExternalModification** | The same notice, with the outcome reported as an external modification, and the file's modification time and content hash. | The same as above, plus the on-disk bytes exactly as found. | Offered as an explicit action, and only for plain text (§5.4). | Yes, when the editor chooses to keep its draft. |
| **DeletedByOther** | A notice that the artifact was deleted, with the deletion recorded as a revision (a tombstone, §7.4) and the deleted content offered for restore. | The deleted content, in the revision history, for the retention window of §7.3. | No — there is nothing to merge into. | No — the conflict copy would be a duplicate of the deleted revision. The editor's draft is kept in the draft journal. |

**MRC-5.4** `[proposed]` **No merge is ever attempted automatically.** A merge is an
explicit operator action, taken from the conflict notice. Automatic merging would
make the outcome of a write depend on content the operator never saw.

**MRC-5.5** `[proposed]` A merge is offered only for artifacts whose content is plain
text with line structure — vault notes, markdown drafts. It is **not** offered for
the book manifest, a notebook manifest, a source archive or a structured document.
For those, the only offered outcomes are "keep the current version" and "keep my
version as a separate copy".

**MRC-5.6** `[proposed]` When the operator chooses to keep their draft, a **conflict
copy** is written: a new artifact in the workspace's conflict area, carrying the
editor's content, the revision it was based on, the artifact id it conflicts with,
and its own artifact id and revision. The conflict copy is durable and is itself
recoverable.

**MRC-5.7** `[proposed]` A conflict copy is **not** silently merged back and is
**not** part of any notebook's membership until the operator adopts it explicitly.

*Current state, and why it matters:* the notebook manifest can already hold a note id
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:248-282`), so
an automatically-adopted conflict copy would silently enter a notebook's evidence
set. The roadmap's baseline already flags that the Ask path retrieves `source_ids`,
not note contents (`docs/product-replacement-roadmap.md:34`); this rule keeps the
conflict copy out of that path until it is adopted.

**MRC-5.8** `[proposed]` The losing draft is **never** discarded. In every conflict
outcome the operator's draft survives, either as the conflict copy or in the draft
journal (§6.2).

**MRC-5.9** `[proposed]` A conflict is **reported**, never resolved by the store
without the operator. A store that resolves a conflict by overwriting has violated
MRC-3.9.

**MRC-5.10** `[proposed]` The conflict notice is the operator's only view of a
conflict. It must state, without the operator opening a second window: the artifact
id, the expected revision, the actual revision, the outcome, whether the draft was
preserved, and where the conflict copy was written.

*Current gap:* the tier's existing 409 precedent is `ActiveSessionExistsError`
(`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:416`, mapped to `409 Conflict` in
`ARCHITECTURE.md:359`). It is the right *shape* — a typed error with a status — but it
describes a duplicate session, not a revision conflict, and reusing it would leave
the conflict's data (`expected_revision`, `actual_revision`) untyped. §11 D-1.

---

## 6. Recovery points

### 6.1 The seven recovery points

**MRC-6.1** `[proposed]` A **recovery point** is a named durable state. There are
exactly seven, and each is defined below by what survives it, what it loses, and how
it is tested.

| # | Recovery point | What exists at this point | What survives | What is lost | How it is tested |
| :--- | :--- | :--- | :--- | :--- | :--- |
| R1 | **Acknowledged write** | The artifact at its new revision, durable per MRC-3.14. | Everything the write touched. | Nothing. | Kill the process immediately after the acknowledgement; reopen; assert the bytes and the revision. |
| R2 | **Autosave** | A draft copy in the draft journal. | Operator edits made since the last acknowledged write, up to the autosave interval. | Edits made since the last autosave tick. | Edit without saving; wait one autosave interval; kill the process; reopen; assert the draft is offered. |
| R3 | **Draft journal** | The journal: drafts plus in-flight operation intents. | Every unsaved draft and every recoverable half-finished operation. | Nothing that was acknowledged. | Force a kill mid-operation; reopen; assert the operation is detected (MRC-4.6) and completed or rolled back per MRC-4.9. |
| R4 | **Crash restart** | The workspace exactly as the last durable write left it. | Every acknowledged write (R1). | Unacknowledged in-flight edits, up to the last autosave. | Kill the process with `SIGKILL` equivalent at each of a fault-injection point; reopen; assert the acknowledged set is intact and the workspace is readable. |
| R5 | **Version restore** | A chosen revision of a chosen artifact, restored into the workspace. | The chosen revision of that artifact, and every artifact not touched by the restore. | Revisions after the restored one, for that artifact only — which remain in the revision history and can be restored again. | Restore revision *n* of an artifact that has been written to *n+3*; assert the artifact is at *n* and that *n+1..n+3* are still restorable. |
| R6 | **Backup export** | A backup file holding the workspace's artifacts (§7.3). | The artifacts the backup includes, at the revision they had when the backup was taken. | Everything the backup does not include, per the backup's own manifest. | Take a backup, mutate the workspace, restore the backup into a clean directory; assert the manifest and the artifacts match. |
| R7 | **Clean-install restore** | A backup restored into a **freshly installed** application with no prior workspace. | The whole workspace as the backup described it. | Nothing — this is the top of the recovery chain. | Install to a clean location, restore the backup, run the acceptance flows against it. This is the R.04 test. |

### 6.2 The draft journal

**MRC-6.2** `[proposed]` The **draft journal** holds unsaved operator drafts. A draft
is written on the autosave interval (R2) and on every keystroke that would otherwise
be lost.

**MRC-6.3** `[proposed]` A draft is keyed by `(artifact_id, editor_session_id)` and
carries the artifact's revision at the time of the read, so that a draft can be
compared against the artifact's current revision when the operator returns.

**MRC-6.4** `[proposed]` A draft is removed when its content is acknowledged as a
write, or when the operator explicitly discards it. A draft is never removed because a
process died.

**MRC-6.5** `[proposed]` Autosave interval is a workspace setting with a **proposed**
default of **5 seconds** (§7.6). A change to this default does not change the
acknowledgement semantics of MRC-3.14.

### 6.3 Crash restart

**MRC-6.6** `[proposed]` On start, the application detects — before serving any
request — every half-finished operation (MRC-4.6) and every leftover temporary file
(MRC-3.5), and reports them.

**MRC-6.7** `[proposed]` Crash restart preserves **acknowledged work** and **notebook
evidence**. Notebook evidence means the sources a notebook names, at the revision the
notebook recorded. If a source is missing or changed, the application **reports it
explicitly** and does not silently answer from a smaller corpus.

*Current gap:* the notebook manifest names `source_ids`
(`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:176`), but
the sources themselves live in an upload store that evicts by modification time
(`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:89-104`) with no
awareness of which sources a notebook references. The roadmap's N1.01 asks for exactly
this to be reported rather than hidden
(`docs/product-replacement-roadmap.md:117`); today it cannot be reported, because
nothing records the source's revision. See §7.2 and MRC-7.8.

**MRC-6.8** `[proposed]` A restart never silently discards a job's progress. A job
that was `running` at the time of the crash is recovered as `checkpointed` (§9) or
`failed` (§9), never as `completed`.

### 6.4 Restore

**MRC-6.9** `[proposed]` A restore is an ordinary journalled multi-file operation
(§4). It is atomic per artifact and recoverable per MRC-4.9.

**MRC-6.10** `[proposed]` A restore writes a **new revision**; it does not rewrite
history. Restoring revision *n* of an artifact leaves revisions *n+1…* in the
revision history, so a restore is itself undoable.

**MRC-6.11** `[proposed]` A restore **does not** restore deletions that were
acknowledged after the restored revision, unless the operator asks for it explicitly.
Restoring is a point-in-time operation on the artifact, not a whole-workspace time
machine.

**MRC-6.12** `[proposed]` The restore of a backup validates the backup before
touching the workspace: the backup's manifest must name every artifact it carries,
and each artifact's content hash must match. A backup that fails validation is
refused with `ValidationError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:207`)
and the workspace is left untouched.

**MRC-6.13** `[proposed]` A restore report states, per artifact, what was restored and
what was skipped, and the report is itself durable.

---

## 7. Retention and deletion

### 7.1 The retention classes

**MRC-7.1** `[proposed]` Durable state is retained in one of five classes. Every
durable object belongs to exactly one.

| Class | Examples | Retained | Rebuildable | Default retention **[proposed]** |
| :--- | :--- | :--- | :--- | :--- |
| **Originals** | Uploaded source files (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50-58`), a book's `origin.json` (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:35`, written at `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:546`) | Until the operator deletes them, then for the tombstone window (§7.4). | No. | Indefinite, subject to the operator's deletion. |
| **Indexes** | The vault link/backlink index, the retrieval index, any per-notebook summary | Until the next rebuild. | Yes — from the originals. | Rebuilt on demand; no retention floor. |
| **Revisions** | The revision snapshots of an artifact (§3, §6 R5) | For a rolling window of the most recent *N* revisions per artifact, plus any revision explicitly pinned. | No. | **50 revisions per artifact**, plus pinned revisions kept until unpinned. |
| **Backups** | Whole-workspace backup files (§6 R6) | A rolling window. | No — a backup is itself the recovery artifact. | **14 daily + 4 weekly + 6 monthly**. |
| **Journals** | The recovery journal: drafts and operation intents (§4.3, §6.2) | A rolling window. | No. | **Drafts 7 days; completed operation intents 24 hours.** |

### 7.2 Eviction rules

**MRC-7.2** `[proposed]` Eviction removes the **least valuable** item in the class
when the class is over its budget, and eviction is **never** performed on an object
that another retained object references.

**MRC-7.3** `[proposed]` An **index** may always be evicted, because it is rebuildable
from the originals. Evicting an index never removes evidence.

**MRC-7.4** `[proposed]` **Originals are not evicted.** When the upload store's byte
budget is exceeded, the store refuses the new upload with
`SourceUploadTooLargeError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:324`)
rather than evicting an existing original to make room.

*Current behaviour, and the contradiction:* the upload store **does** evict, by
modification time, oldest first
(`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:89-104`;
`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:124-131` for the touch
that protects a recently-used file). Its budget is 512 MB
(`kotlin/ingest/src/main/kotlin/lmeditor/ingest/Limits.kt:25`). This is the opposite of
MRC-7.4 and it can delete the original of a source a notebook still references.
F.02 requires "Evict caches without evicting material referenced by a notebook"
(`docs/product-replacement-roadmap.md:39`) and the current store has no reference
awareness at all. This is the single sharpest conflict between the roadmap and the
tree, and it is resolved by §11 D-4.

**MRC-7.5** `[proposed]` A revision may be evicted only when it is not the current
revision, is not pinned, is outside the rolling window (§7.1), and is not the base of
a retained backup. The base revision of a retained backup is pinned for the life of
that backup.

**MRC-7.6** `[proposed]` A journal entry may be evicted only after its operation is
committed **and** the resulting revision is durable, and after the class's retention
window (§7.1) has passed. A draft may be evicted only after its window has passed and
it has not been acknowledged.

**MRC-7.7** `[proposed]` Eviction is itself journalled, so that an interrupted
eviction is detected and completed on the next open.

### 7.3 What deleting or revoking a source invalidates

**MRC-7.8** `[proposed]` Deleting or revoking a source **invalidates future access**
to that source — retrieval, download, export and any answer that would need it — and
**does not rewrite historical evidence**. An answer, a citation, a promoted note or an
exported artifact that was bound to that source keeps the binding it had, and the
binding now resolves to "revoked" rather than being deleted or silently re-pointed.

*Current state:* there is no durable source archive with revisions, so there is
nothing that could carry a binding today. The server's source registry is an
in-memory working set capped at 256 sources
(`backend/src/lmeditor/server/sources.py:159-171`;
`kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:16`,
`kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:69-72`), and the
roadmap's baseline row describes it as exactly that
(`docs/product-replacement-roadmap.md:30`). R.10
(`docs/product-replacement-roadmap.md:213`) and N1.05
(`docs/product-replacement-roadmap.md:121`) are unimplementable until this contract's
archive and revision rules land.

**MRC-7.9** `[proposed]` Revocation is recorded as a **tombstone revision** on the
source artifact: the source's content is retained under the original's retention,
and a separate revocation record marks it inaccessible. The revocation record carries
the reason and the timestamp.

**MRC-7.10** `[proposed]` A revocation is reported to the operator with
`source_removed` recorded in the error's `details` map, and the error is
`SourceLoadError`
(`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:255`) — the existing error for
"a source cannot be loaded" — with `reason = "revoked"`.

### 7.4 Deletion

**MRC-7.11** `[proposed]` Deleting an artifact writes a **tombstone revision** before
removing the artifact's current bytes, so that the deletion is itself recoverable
within the tombstone window (§7.1).

**MRC-7.12** `[proposed]` Deleting an artifact that another retained artifact
references requires an explicit confirmation naming the referrer. The application
reports the referrers; it does not delete the referrer's reference silently.

**MRC-7.13** `[proposed]` A tombstone is a revision, so it is counted against the
revision retention window like any other and is evicted on the same schedule.

### 7.5 Retention defaults

**MRC-7.14** `[proposed]` The retention defaults in §7.1 — 50 revisions per artifact,
14 daily / 4 weekly / 6 monthly backups, 7-day drafts, 24-hour operation intents,
5-second autosave — are **proposals for the architecture lead to confirm** (§11 D-5).
They are not binding until confirmed. Every other rule in this document is binding.

**MRC-7.15** `[proposed]` The one retention number that is **current** and not a
proposal is the upload store's 512 MB budget
(`kotlin/ingest/src/main/kotlin/lmeditor/ingest/Limits.kt:25`) and the server
registry's 256-source cap (`kotlin/server/src/main/kotlin/lmeditor/server/SourceLibrary.kt:16`).
MRC-7.4 changes what happens when the upload budget is exceeded, not the budget.

---

## 8. Migration

### 8.1 The migration rule

**MRC-8.1** `[proposed]` A schema migration is **forward-only**. There is no
downgrade path in the application, and none is required: downgrade is served by
restoring the backup taken before the migration (MRC-8.4).

**MRC-8.2** `[proposed]` A migration is **reversible where possible**, by the
combination of a pre-migration backup and the migration's own idempotence: running
the migration twice yields the same result as running it once. "Reversible" here
means *restorable to the prior state from the backup*, not *computable in reverse*.

**MRC-8.3** `[proposed]` Every migration ships with a **dry run**: the same code path
that reports what it would change, how many artifacts it would touch, and how many
bytes it would rewrite, without writing anything. The dry run is the operator's
decision input.

**MRC-8.4** `[proposed]` A migration takes a **backup first**. The backup is
verified (MRC-6.12) before the migration begins. If the backup cannot be taken or
verified, the migration does not start.

**MRC-8.5** `[proposed]` A migration that **fails leaves the original intact**. The
migration writes to a new location and swaps it in atomically; it never mutates the
pre-migration bytes in place. A failure at any step leaves the store readable at its
old schema version.

**MRC-8.6** `[proposed]` A migration is marked by a **migration marker** written
before it starts and cleared after it finishes. A marker that is present on the next
open means the migration was interrupted: the store is not opened as normal
(MRC-2.12) and the operator is offered restore-from-backup.

**MRC-8.7** `[proposed]` A migration is **dry-runnable, backup-first, atomic-at-swap
and idempotent**, and every one of those four properties has a test that fails when
the property is broken.

### 8.2 Which stores need a migration

**MRC-8.8** `[proposed]` The following stores need a migration, and each is listed
with its **current on-disk shape** — the shape an existing installation has today.

| Store | Current on-disk shape | Migration needed | Why |
| :--- | :--- | :--- | :--- |
| **Notebook manifest** | `<notebooks_root>/<notebook_id>/manifest.json` — a JSON object written from `notebook.toWireMap()` (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:22`, `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:441`). Unversioned. | Add `schema_version` (MRC-2.5); add the notebook's own stable id distinct from its title-derived directory name (MRC-2.1). | MRC-2.5; F.01. |
| **Book directory** | `<root>/<book_id>/manifest.json`, `<root>/<book_id>/sections/<section_id>.json`, `<root>/<book_id>/chunks.jsonl`, `<root>/<book_id>/origin.json` (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:149-152`, `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:20`, `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:23`, `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:26`, `kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:35`). Unversioned. | Add `schema_version` to `manifest.json`; add the revision field and the revision store (MRC-3.7, §7.1). | MRC-2.5; F.03, F.04. |
| **Upload / source archive** | `<upload_root>/<source_id>/` — flat files written with `Files.write` (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:40`, `kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50-58`). Unversioned, unmanifested, and evicted by mtime (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:89-104`). | Introduce the source archive: a `meta.json` carrying `schema_version`, the source's revisions and the extraction metadata; move originals under a reserved subdirectory. | F.02; MRC-7.4; R.10. |
| **Vault** | Plain markdown files under the vault root; there is **no vault manifest at all**. Notes carry YAML frontmatter parsed by `kotlin/vault/src/main/kotlin/lmeditor/vault/Markdown.kt`. | Add the note `uid` frontmatter field (MRC-2.2) and the vault index, which is rebuildable and therefore needs no migration (MRC-7.3). | O1.07; F.05. |
| **Workspace manifest** | Does not exist. | Created fresh; no migration. | F.01. |
| **Workflow checkpoints** | In-memory only — `MemorySaver()` is the default checkpointer (`backend/src/lmeditor/workflow/graph.py:147`, imported at `backend/src/lmeditor/workflow/graph.py:10`), and the compiled-graph registry is process memory keyed by `thread_id` (`backend/src/lmeditor/workflow/graph.py:63-69`), evicted at `backend/src/lmeditor/workflow/graph.py:492-507`. | Nothing to migrate — there is nothing on disk. The persistent store in §9 is new work. | F.06; the roadmap's baseline row agrees (`docs/product-replacement-roadmap.md:31`). |
| **Sessions and source registry** | In-memory only (`backend/src/lmeditor/server/session.py:409-413`, `backend/src/lmeditor/server/sources.py:159-171`). | Nothing to migrate. | F.06; N1.01. |

### 8.3 Restoring on a clean installation

**MRC-8.9** `[proposed]` Before any schema upgrade ships, a **restore on a clean
installation** is tested: a backup taken by the pre-upgrade version is restored into
a freshly installed application with no prior workspace, and the restored workspace
passes the acceptance flows. This is the R.04 gate
(`docs/product-replacement-roadmap.md:207`) and it is recovery point R7 (§6.1).

**MRC-8.10** `[proposed]` The clean-install restore test is part of the release
evidence, not a unit test. Its result is recorded as evidence for R.04 with the
application version, the backup version and the outcome.

---

## 9. Persistent jobs

### 9.1 The state machine

**MRC-9.1** `[proposed]` A generation or ingestion job is in exactly one of six
states, and the transitions are:

```
          ┌──────────┐
          │  queued  │
          └────┬─────┘
               │ start
          ┌────▼─────┐
     ┌────┤ running  ├────┐
     │    └────┬─────┘    │
     │         │          │ checkpoint
     │         │          │
     │    ┌────▼──────────▼──┐
     │    │   checkpointed   │
     │    └────┬──────────┬──┘
     │         │ resume   │
     │         └────► running
     │
     │   ┌─────────────┬───────────────┐
     └──►│  cancelled  │    failed     │
   cancel│  (terminal) │  (retryable)  │
         └─────────────┴───────┬───────┘
                                  │ retry
                                  └──► queued
     running ── all steps done ──► completed (terminal)
```

| State | Meaning | Durable representation | Transitions out |
| :--- | :--- | :--- | :--- |
| **queued** | Accepted and scheduled; no step has executed. | A job record on disk. | `running` (start), `cancelled` (operator). |
| **running** | A step is executing. | A job record updated at each checkpoint. | `checkpointed`, `cancelled`, `failed`, `completed`. |
| **checkpointed** | Progress is durably recorded; the job can be resumed from here. | A job record plus a checkpoint payload. | `running` (resume), `cancelled`, `failed`. |
| **cancelled** | The operator cancelled it. Terminal. | A terminal job record. | — |
| **failed** | It stopped on an error. Terminal until retried. | A terminal job record carrying the typed error. | `queued` (retry). |
| **completed** | It finished successfully. Terminal. | A terminal job record carrying the produced artifact ids. | — |

**MRC-9.2** `[proposed]` The job record is **durable before the job starts
executing**. A job that is not durable cannot be cancelled, cannot be reported on and
cannot be recovered.

**MRC-9.3** `[proposed]` The state machine has **no illegal transitions**. An
attempted illegal transition raises `InvalidStateTransitionError`
(`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:200`; Python twin
`backend/src/lmeditor/core/exceptions.py:187`). This is the existing typed error for
the condition and it is reused, not replaced.

**MRC-9.4** `[proposed]` Cancellation is durable and takes effect at a **checkpoint
boundary**. After a cancellation is acknowledged, the job produces no new artifacts
and no new durable state.

*Current partial precedent:* the server already refuses to replace a live session and
reports it as `409 Conflict` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:416`,
`ARCHITECTURE.md:359`), and the workflow registry is dropped on a terminal state
(`backend/src/lmeditor/workflow/graph.py:492-507`; test
`backend/tests/test_hitl_gate.py:435`). What is missing is that neither survives a
restart.

### 9.2 Retries and duplicate artifacts

**MRC-9.5** `[proposed]` A retried job **resumes from the last checkpointed state**.
It never re-executes a step that is already recorded as complete.

**MRC-9.6** `[proposed]` A retried job produces **no duplicate artifact**. Artifact
identity is derived from `(job_id, artifact_key)` — never from a timestamp, a random
value or a content hash of a run — so a re-run **replaces** the artifact rather than
adding a second one.

*Why this exact rule:* F.06 requires "safe retries and restart recovery without
duplicate artifacts" (`docs/product-replacement-roadmap.md:43`); the determinism rule
forbids timestamps and random values in a computed identifier
(`kotlin/CONTRACTS.md:288-290`); and MRC-3.12 requires a replayed write to be a
no-op. Together they mean the artifact id of a job's output is a pure function of the
job and the artifact's role in it.

**MRC-9.7** `[proposed]` A failed job records the **typed error** that stopped it and
never reports success. The existing vocabulary covers the domain failures:
`DraftingError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:306`),
`LMNotConfiguredError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:294`),
`SourceLoadError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:255`),
`WorkflowDeadlockError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:179`),
`ExportError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:315`).

*Current state:* the tier already refuses to fabricate — a drafting failure is
recorded as a real failure rather than hidden
(`backend/tests/test_workflow_e2e.py:155`) — and a run whose sections cannot be
drafted still reaches the human gate (`backend/tests/test_workflow_e2e.py:223`).

### 9.3 Where a job checkpoint lives

**MRC-9.8** `[proposed]` A job's checkpoint lives in the **workspace**, on disk,
inside the recovery journal's sibling directory — not in process memory.

*Current state, which the roadmap's baseline row already records correctly:* the
default checkpointer is `MemorySaver()`
(`backend/src/lmeditor/workflow/graph.py:147`), the compiled graph and its
checkpointer are retained in a process-memory registry keyed by `thread_id`
(`backend/src/lmeditor/workflow/graph.py:63-69`), and the registry is released on a
terminal state (`backend/src/lmeditor/workflow/graph.py:492-507`). A restart therefore
loses every suspended run. The roadmap states this as
`docs/product-replacement-roadmap.md:31`, and the tree confirms it.

**MRC-9.9** `[proposed]` The **human-review gate is a job checkpoint**. A run
suspended at the gate survives a restart and is resumable. The gate's typed errors
already exist — `HITLReviewNotPendingError`
(`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:397`) and
`HITLDecisionMissingError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:403`;
Python twins at `backend/src/lmeditor/server/session.py:28` and
`backend/src/lmeditor/server/session.py:32`) — and the resume path is already tested
(`backend/tests/test_hitl_gate.py:320`,
`backend/tests/test_hitl_gate.py:425`). What is missing is durability.

**MRC-9.10** `[proposed]` A job that was `running` when the process died is recovered
as `checkpointed` (if it had a checkpoint) or `failed` (if it did not). It is never
recovered as `completed`, and it never re-runs a step whose artifact already exists
(MRC-9.6).

---

## 10. Requirement coverage

Every requirement this document governs, mapped to the rules that implement it.
"Partial" means the rule exists but the implementation does not.

| Requirement | Roadmap line | Rules | Coverage |
| :--- | :--- | :--- | :--- |
| **F.01** Durable workspace identity | `:38` | MRC-1.4, MRC-2.1, MRC-2.2, MRC-2.4, MRC-2.5, MRC-2.6, MRC-8.8 | Full |
| **F.02** Durable source archive | `:39` | MRC-7.1, MRC-7.4, MRC-7.8, MRC-8.8 | Full |
| **F.03** Safe saves | `:40` | MRC-3.1, MRC-3.2, MRC-3.3, MRC-3.4, MRC-3.7, MRC-3.8, MRC-3.9, MRC-3.10, MRC-4.1, MRC-4.2, MRC-4.3, MRC-4.4, MRC-4.5, MRC-4.6, MRC-4.9, MRC-4.12 | Full |
| **F.04** Recovery | `:41` | MRC-6.1 – MRC-6.13, MRC-7.1, MRC-7.5, MRC-7.6 | Full |
| **F.05** External changes | `:42` | MRC-5.1, MRC-5.2, MRC-5.3, MRC-5.6, MRC-5.9, MRC-5.10 | Full |
| **F.06** Persistent jobs | `:43` | MRC-9.1 – MRC-9.10, MRC-3.12 | Full |
| **O1.07** Recoverable rename/move + backlinks; trash and restore | `:96` | MRC-2.2, MRC-4.1, MRC-4.4, MRC-4.7, MRC-4.8, MRC-4.9, MRC-4.10, MRC-7.11, MRC-7.12 | Full |
| **O1.08** Watch filesystem changes; rebuildable index | `:97` | MRC-5.1, MRC-5.2, MRC-7.3 | **Partial** — the index's rebuildability and retention are governed here; the watcher itself is not a recovery concern and is out of scope (§1.1). |
| **N1.01** Restore notebook sources; report missing or changed | `:117` | MRC-6.7, MRC-7.4, MRC-7.8, MRC-8.8 | Full |
| **N1.05** Persist conversations; bind old answers to original source snapshots | `:121` | MRC-7.8, MRC-7.9, MRC-5.6, MRC-6.10 | **Partial** — the binding rule is governed here; the conversation store's own schema is N1's work. |
| **N1.10** Persist retrieval indexes; rebuild them | `:126` | MRC-7.1, MRC-7.3, MRC-8.8 | Full |
| **N2.08** Persist artifact jobs, provenance, versions and exports | `:139` | MRC-9.5, MRC-9.6, MRC-9.8, MRC-3.11, MRC-3.12 | Full |
| **P.06** Export a portable project bundle; test import on a clean installation | `:151` | MRC-6.1 (R6, R7), MRC-6.12, MRC-8.9, MRC-8.10 | Full |
| **R.03** Fault injection; preserve acknowledged edits; recoverable conflicts | `:206` | MRC-3.17 – MRC-3.27, MRC-4.6, MRC-4.9, MRC-6.1, MRC-6.6, MRC-6.8, MRC-9.4 | Full |
| **R.04** Migration reversibility; restore on a clean installation | `:207` | MRC-8.1 – MRC-8.10, MRC-6.12 | Full |
| **R.10** Deleting or revoking a source invalidates access without rewriting history | `:213` | MRC-7.8, MRC-7.9, MRC-7.10, MRC-5.7 | Full |
| **R.11** Reproducible diagnostics; storage/index status; safe repair | `:214` | MRC-3.5, MRC-4.6, MRC-4.11, MRC-7.7, MRC-6.13 | **Partial** — the repair and status surfaces are governed here; the diagnostics UI is R.11's own work. |
| **R.12** Validate upgrade and uninstall with data retention | `:215` | MRC-7.1, MRC-7.14, MRC-8.4, MRC-8.9, MRC-6.1 (R7) | Full |

**Requirements covered: 18.** Full coverage on 15, partial on 3 (O1.08, N1.05, R.11),
where the partial scope is stated in the row.

---

## 11. Open decisions for the architecture lead

Recorded so they are not silently decided by an implementer
(`docs/replacement/m0-blueprint.md:263`). Numbering follows the blueprint's §8 style.

**All twelve have been ruled on.** The rulings, the reasons and what M1 still owes are
in [`docs/replacement/decisions.md`](decisions.md); the recommendations below are the
ones the rulings adopted. D-12 is already enacted in the roadmap.

| # | Question | Raised by | Needed by | Recommendation | Consequence if not decided |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **D-1** | What typed error does a failed revision precondition raise? The vocabulary has no conflict error, and `ActiveSessionExistsError` (`kotlin/core/src/main/kotlin/lmeditor/core/Errors.kt:416`) describes a different condition. | §3.20, §5.1 | Before F.03 | Add **`RevisionConflictError`** to `lmeditor.core`, subclassing `LMeditorError`, with `details` keys `artifact_id`, `expected_revision`, `actual_revision`, `outcome` and a Python twin with the same keys (`kotlin/CONTRACTS.md:228-229`). | Each implementer invents a per-module error; the two tiers drift; §5's outcome taxonomy cannot be asserted by a test. |
| **D-2** | What typed error does a storage failure (disk full, permission denied) raise? The vocabulary has no I/O-failure error, and `ValidationError` describes a bad *value*, not a failed *write*. | §3.17, §3.18 | Before F.03, R.03 | Add **`StorageWriteError`** to `lmeditor.core` with `details["reason"]` in `{disk_full, permission_denied, io_error}` and `details["path"]`, plus the Python twin. | R.03's fault injection has no typed outcome to assert; the disk-full case will be reported as a generic 500. |
| **D-3** | Is "interrupted operation" a cross-store error or a per-store detail? | §3.19, MRC-3.5 | Before F.03 | Add **`IncompleteOperationError`** to `lmeditor.core` carrying `operation_id`, `operation_kind` and `affected_paths`; the per-store errors keep `reason = "interrupted_write"` for the local case. | A half-finished rename is indistinguishable from a rename that was never asked for; §4's detection has no single type to catch. |
| **D-4** | The upload store evicts originals by modification time (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:89-104`), which can delete the original of a source a notebook still references. F.02 forbids that (`docs/product-replacement-roadmap.md:39`). Which wins? | §7.4 | Before F.02 | **The notebook reference wins.** Replace time-based eviction with reference-aware eviction: the store refuses a new upload with `SourceUploadTooLargeError` when the budget is exhausted and the operator has not freed space. Time-based eviction survives only for objects with no notebook reference. | The upload store can silently destroy the evidence a notebook's answer was grounded in, and N1.01's "report missing sources" can never be honoured because the deletion is invisible. |
| **D-5** | Are the retention defaults in §7.1 accepted? | §7.14 | Before F.04 | Accept as written: **50 revisions/artifact, 14 daily + 4 weekly + 6 monthly backups, 7-day drafts, 24-hour operation intents, 5-second autosave.** All are cheap to change later; none is a correctness risk. | Implementers will pick their own numbers, and the "rolling window" of MRC-7.5 and MRC-7.6 has no measurable definition. |
| **D-6** | Is a revision an integer, a content hash, or both? | MRC-1.6, MRC-2.x | Before F.03 | **Both, with different jobs.** The *precondition* is a monotonic integer per artifact (so an operator can say "I edited what I read"); a *content hash* is stored alongside it to detect external modification (MRC-5.1). | If the precondition is a hash, every keystroke changes it and the precondition becomes unusable; if there is no hash, external modification is undetectable and F.05 is unimplementable. |
| **D-7** | Does the vault note id become path-independent? A note id **is** its path today (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:87-91`), so a rename changes the id. | MRC-2.2 | Before O1.07 | **Yes.** Add a stable `uid` to the note's frontmatter; keep the path as the addressing scheme; rewrite references on rename. | O1.07's "rename/move plus backlink rewriting recoverable as one operation" has no stable reference to repair — the operation can only be undone by moving the file back, which is exactly the fragile path. |
| **D-8** | Is the schema version per store or one global number? | MRC-2.6 | Before F.01 | **Per store format.** Each store declares its own `min_supported_version` / `max_supported_version`; the notebook manifest and the book manifest evolve independently. | One global number forces every store to be migrated whenever any one of them changes, which is what §8.5's atomic-swap requirement is meant to avoid. |
| **D-9** | How is a migration run — in-process on open, or as an explicit operator action? | MRC-2.12, MRC-8.x | Before R.04 | **Explicit action**, with a dry run (MRC-8.3) that the operator must confirm. The application **refuses to open** a store whose schema is below `min_supported_version`; it offers to migrate or to restore from backup. | A migration that runs silently on open can surprise the operator with a rewrite of their workspace at the moment they least expect it. |
| **D-10** | Does the workspace backup include the recovery journal? | MRC-6.1 (R6) | Before F.04 | **No.** The backup carries artifacts, originals and revisions; the journal is workspace-local scratch and its content is either already durable (as an artifact) or still a draft. Drafts are exported separately, on demand, if the operator asks. | A backup that carries journals restores stale drafts and in-flight intents that belonged to a different session, and forces the restore path to resolve them (MRC-4.9) for no benefit. |
| **D-11** | Where do conflict copies live, and are they indexed? | MRC-5.6, MRC-5.7 | Before F.05 | **`<workspace>/.lmeditor/conflicts/`**, excluded from normal search and from notebook membership until explicitly adopted, and listed in the operator's diagnostics. | A conflict copy that enters search or a notebook silently becomes evidence, which contradicts MRC-5.7 and the roadmap's caution about notebook membership (`docs/product-replacement-roadmap.md:34`). |
| **D-12** | The roadmap's §2 baseline does not mention that the **library** store already writes atomically (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883-900`), and describes the atomic-write guarantee as a notebook property (`docs/product-replacement-roadmap.md:40`). | §3.1 | Before M1 planning | **Correct the roadmap's F.03 text** to say the library and notebook stores already use atomic replacement and that the *missing* pieces are the vault writes, the upload store writes, the flush for durability, and the revision preconditions. | M1 is scoped against a baseline that understates what exists and overstates what is atomic, so the work is estimated twice. |

### 11.1 Where the tree contradicts the roadmap's §2 baseline

Per the task's evidence rule, the tree wins. Recorded so the contradiction is not
resolved silently.

| Roadmap baseline claim | The tree | Effect |
| :--- | :--- | :--- |
| "Notebook manifests already use atomic writes" (`docs/product-replacement-roadmap.md:40`) | **Correct, and incomplete.** The notebook manifest is atomic (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:437-449`, `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:470-487`) — but so is the whole **library** store (`kotlin/library/src/main/kotlin/lmeditor/library/BookStore.kt:883-900`), which the roadmap does not mention. Neither flushes the file or the directory, so both are atomic against process death only. | The baseline understates existing coverage and overstates its strength. See D-12 and MRC-3.4. |
| "The registry is a memory working set capped at 256 sources" (`docs/product-replacement-roadmap.md:30`) | **Correct**, and it omits a second, *persistent* store: the upload store holds originals on disk under a 512 MB budget (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/Limits.kt:25`, `kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:50-58`) and **evicts them by modification time** (`kotlin/ingest/src/main/kotlin/lmeditor/ingest/SourceStore.kt:89-104`). | The baseline describes the cache and not the archive. The archive already evicts evidence. See D-4 and MRC-7.4. |
| "Default in-memory checkpoints and process session state need restart recovery" (`docs/product-replacement-roadmap.md:31`) | **Correct and confirmed**: `MemorySaver()` (`backend/src/lmeditor/workflow/graph.py:147`), process-memory session map (`backend/src/lmeditor/server/session.py:409-413`). | No contradiction. §9 governs the fix. |
| F.01 requires "stable … IDs" (`docs/product-replacement-roadmap.md:38`) | The notebook id **is** a title slug with an order-dependent collision suffix (`kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookPath.kt:135-141`, `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/NotebookStore.kt:352-361`); the vault note id **is** its path (`kotlin/vault/src/main/kotlin/lmeditor/vault/VaultPath.kt:87-91`). | The tree does not yet meet F.01. MRC-2.1 and MRC-2.2 specify the target; D-7 decides the vault half. |
| The blueprint's registry carries a `schema_version` field for the requirements registry (`docs/replacement/m0-blueprint.md:93`, `docs/replacement/m0-blueprint.md:118`) | No store in the tree carries any schema version: a search for `schema_version|schemaVersion` across `*.kt`, `*.py` and `*.json` returns no matches. | The M0 registry anticipates the versioning that MRC-2.5 introduces. The gap is the stores, not the registry. |

---

*End of contract. Rules are binding; §7.1's retention numbers and §11's decisions are
the only parts awaiting confirmation.*
