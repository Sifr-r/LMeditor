# The replacement program's decision register

Date: 26 October 2026  
Status: the architecture lead's rulings on the questions the M0 artifacts raised.

A decision that is written down and not enacted is a decision nobody can implement
against. This register records what was decided, why, and what remains to be built. Each
ruling names the artifact that raised it, so the reasoning can be checked.

A decision here does not change runtime behaviour by itself. Most name the work that M1
must do; a few are already enacted and say so.

---

## 1. The layout contract (from `editor-layout-feasibility.md`)

| # | Question | Ruling | State |
| :--- | :--- | :--- | :--- |
| **L-1** | Does the tier's empty-dependency rule survive a real pagination engine? | **No, not as written — and the rule is amended rather than abandoned.** A layout engine needs no third-party library, so the tier's dependency set stays empty; but the rule needed one clause to say so. `kotlin/CONTRACTS.md` §1.2 admits bundled typographic metric assets and forbids tier modules from measuring text with `java.desktop`. | **Enacted.** |
| **L-2** | Are bundled font files an asset dependency, and therefore a contract change? | **Yes, and only metrics are admitted.** A versioned table of glyph advance widths travels with its licence notice. A font *program* is not admitted: it is needed only for embedded PDF text, and it is a separate decision with its own licence obligations. The metrics must come from a font whose licence permits redistribution of the derived data, so a SIL Open Font Licence release. | **Enacted** (the clause); the metric set is M1's choice. |
| **L-3** | Is the layout LMeditor's own or Word's? | **LMeditor's own**, and it is called a *layout profile*. Word-identical pagination, complex-script shaping, kerning, multi-column text and PDF with arbitrary embedded fonts are **permanently out of scope** unless a later decision reopens them. W2.06 and the rendered-pages half of R.02 are satisfied by "the same documented layout rules" meaning the LMeditor layout profile. | Decided; the profile is M1's work. |
| **L-4** | How is the decision enforced? | **Mechanically, not by convention.** The existing class check reads only `.class` entries, so a shipped data file would have passed it unnoticed. Two assertions were added: the tier jar carries no unapproved data file, and an approved metric asset declares its font, licence, tool and layout profile. | **Enacted.** `backend/tests/test_kotlin_dependencies.py` went from 18 tests to 20. |

---

## 2. The write, conflict and recovery contract (from `migration-recovery-contract.md`)

These are the rulings the contract's §11 raised, each with the contract's own
recommendation. D-1, D-2 and D-3 block every M1 write path, so they are settled first.

| # | Question | Ruling | State |
| :--- | :--- | :--- | :--- |
| **D-1** | What typed error does a failed revision precondition raise? | **Add `RevisionConflictError`** to `lmeditor.core`, subclassing `LMeditorError`, with `details` keys `artifact_id`, `expected_revision`, `actual_revision` and `outcome`, and a Python twin with the same keys. `ActiveSessionExistsError` describes a different condition and is not reused. | Decided; M1 adds the error. |
| **D-2** | What typed error does a storage failure raise? | **Add `StorageWriteError`** to `lmeditor.core`, with `details["reason"]` in `{disk_full, permission_denied, io_error}` and `details["path"]`, plus the Python twin. `ValidationError` describes a bad *value*, not a failed *write*, and is not reused. | Decided; M1 adds the error. |
| **D-3** | Is "interrupted operation" a cross-store error or a per-store detail? | **Both, at two levels.** Add `IncompleteOperationError` to `lmeditor.core`, carrying `operation_id`, `operation_kind` and `affected_paths`; the per-store errors keep `reason = "interrupted_write"` for the local case. | Decided; M1 adds the error. |
| **D-4** | May the upload store evict an original a notebook still references? | **No. The notebook reference wins.** Replace modification-time eviction with reference-aware eviction: the store refuses a new upload with `SourceUploadTooLargeError` when the budget is exhausted and the operator has not freed space. Time-based eviction survives only for objects with no notebook reference. F.02 forbids evicting referenced material, and N1.01's "report missing sources" cannot be honoured while the deletion is invisible. | Decided; M1 changes the store. |
| **D-5** | Are the retention defaults accepted? | **Yes, as written:** 50 revisions per artifact, 14 daily + 4 weekly + 6 monthly backups, 7-day draft retention, 24-hour operation-intent retention, 5-second autosave. All are cheap to change later and none is a correctness risk. | **Accepted.** |
| **D-6** | Is a revision an integer, a content hash, or both? | **Both, with different jobs.** The *precondition* is a monotonic integer per artifact, so an operator can say "I edited what I read"; a *content hash* is stored alongside it, to detect external modification. A hash cannot be the precondition, because every keystroke would change it. | Decided; M1 stores both. |
| **D-7** | Does the vault note id become path-independent? | **Yes.** Add a stable `uid` to the note's frontmatter; keep the path as the addressing scheme; rewrite references on rename. Today a note id *is* its path, so a rename changes the id and O1.07's "rename plus backlink rewriting as one operation" has no stable reference to repair. | Decided; M1 adds the `uid`. |
| **D-8** | Is the schema version per store or one global number? | **Per store format.** Each store declares its own `min_supported_version` and `max_supported_version`; the notebook manifest and the book manifest evolve independently. One global number would force every store to migrate whenever any one of them changes. | Decided; M1 carries the versions. |
| **D-9** | How is a migration run — in-process on open, or as an explicit action? | **An explicit action**, with a dry run the operator must confirm. The application **refuses to open** a store whose schema is below `min_supported_version`; it offers to migrate or to restore from backup. A silent migration on open would surprise the operator with a rewrite of their workspace. | Decided; M1 builds the migration path. |
| **D-10** | Does the workspace backup include the recovery journal? | **No.** The backup carries artifacts, originals and revisions. The journal is workspace-local scratch: its content is either already durable as an artifact or still a draft, and drafts are exported separately on demand. A backup carrying journals would restore stale drafts and in-flight intents that belonged to a different session. | Decided; M1 excludes it. |
| **D-11** | Where do conflict copies live, and are they indexed? | **In `<workspace>/.lmeditor/conflicts/`**, excluded from normal search and from notebook membership until explicitly adopted, and listed in the operator's diagnostics. A conflict copy that enters search or a notebook silently becomes evidence. | Decided; M1 writes them there. |
| **D-12** | The roadmap's §2 baseline does not mention that the library store already writes atomically, and describes the atomic-write guarantee as a notebook property. | **The roadmap was corrected.** F.03 now says that the notebook manifest and the whole book store already replace atomically, that neither flushes to disk and so both are atomic against process death only, and that the missing pieces are the vault writes, the link rewrites, the upload-store writes, the durability flush and the revision preconditions. | **Enacted.** `docs/product-replacement-roadmap.md`. |

---

## 3. The program-level decisions (from `m0-blueprint.md` §8)

| # | Question | Ruling | State |
| :--- | :--- | :--- | :--- |
| **P-1** | Is a durable source archive a Kotlin tier module or a Python tier module? | **Kotlin owns the archive in the existing `:server` domain.** `SourceArchive` owns versioned extracted-source records; Python only selects the root and forwards calls. Original-byte retention and source revisions remain F.02 follow-up work. | First M1 slice enacted; see `docs/source-persistence-blueprint.md`. |
| **P-2** | Does the requirement registry become the source the desktop reads at runtime? | **No.** M0 keeps the registry a development artifact. If a later milestone needs it at runtime, that is a new contract with a new schema version, not a quiet change to this one. | Decided. |
| **P-3** | Which `.doc` conversion path is supported, if any? | **Open, and out of M0's scope.** W3.06 asks for legacy `.doc` behaviour to be *documented* — preserved safely or warned about — and `kotlin/word/WordFormats.kt` already refuses the format rather than mis-parsing it. The compatibility corpus records a legacy OLE2 fixture as `unsupported` for exactly this reason. | Open for M3. |

---

## 4. What is still owed

- **D-1, D-2 and D-3 vocabulary now exists.** `kotlin/core/.../StorageErrors.kt`
  and `backend/src/lmeditor/core/exceptions.py` define the three errors, with
  bridge rehydration and regression coverage in `backend/tests/test_storage_errors.py`.
  `SourceArchive` uses `StorageWriteError`; revision preconditions and interrupted
  operation recovery are still pending. Adding their error types does not complete
  those behaviors. The original decision rows above retain their historical ruling.
- **D-4 is a behaviour change to a production store.** It replaces
  modification-time eviction in `kotlin/ingest` with reference-aware eviction, and it
  needs a way to know which sources a notebook still references. That is F.02's own
  work.
- **L-2's metric set is not chosen.** The clause admits a metric table; choosing one
  means picking an OFL release, generating the table with a named tool, and recording
  the layout profile version. The table is what makes the golden layout tests
  possible.
- **The retention numbers in D-5 are defaults, not laws.** They are recorded so that
  two implementations do not pick different ones.
