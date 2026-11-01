# M0 blueprint: contracts and evidence

Date: 26 October 2026  
Status: the contract for the M0 wave of [`docs/product-replacement-roadmap.md`](../product-replacement-roadmap.md).  
Scope: the milestone the roadmap sequences first — *contracts and evidence*.

The roadmap's M0 exit condition is: *"Each replacement requirement has an owner and an
observable gate; editor/layout feasibility is resolved."* This document is the shared
contract that M0 work is written against. It fixes the requirement identity scheme, the
registry schema, the file ownership of the wave and the acceptance rule for every
artifact.

---

## 1. Why M0 exists as a wave

The roadmap's own §10 definition of done says a helper, parser or endpoint alone does not
complete a workflow. Applied to the backlog as a whole, that means an unchecked box is
not a measurement of anything. M0 turns the backlog into something that can be measured:

1. **Every requirement gets a stable identity** that survives rewording of the roadmap.
2. **Every requirement gets an owner** — the tier module that will implement it — and an
   **observable gate** — the check that will decide whether it is done.
3. **Every requirement's current state is evidenced**, not asserted. A status of
   `complete` requires a named piece of evidence that can be read.
4. **The two structural risks are resolved on paper before code**: editor/layout
   feasibility under the tier's empty-dependency rule, and the migration/recovery
   semantics that M1 will implement.

Without this, M1 would be a wave of parallel agents inventing their own notions of
"done", and M2's acceptance gates would have nothing to check against.

---

## 2. Requirement identity (shared interface)

### 2.1 Scheme

Each checkbox in the roadmap carries a stable identifier in the form `<TRACK>.<NN>`:

| Track | Roadmap section | Milestone band |
| :--- | :--- | :--- |
| `F` | §3 Shared foundation | M1 |
| `W1` | §4 W1 Structured editing and document creation | M2 |
| `W2` | §4 W2 Layout, objects and publication | M2 |
| `W3` | §4 W3 Review and advanced workflows | M3 |
| `O1` | §5 O1 Portable everyday notes | M2 |
| `O2` | §5 O2 Visual organization and ecosystem | M3 |
| `N1` | §6 N1 Dependable source-grounded research | M2 |
| `N2` | §6 N2 Ingestion and study studio | M3 |
| `P` | §7 Make the three replacements one product | M2 |
| `R` | §10 Release evidence and operational requirements | M4 |

`NN` is a two-digit ordinal within the track, counting from `01` in document order.
The identifier is written into the roadmap as a bold marker at the head of the item:

```markdown
- [ ] **F.01** Durable workspace identity: stable workspace, document, note, notebook, source and artifact IDs; ...
```

Every item reads the same way: the marker first, then the requirement's own text
unchanged. Where an item originally opened with a bold `**Title:**` lead-in, the
inner emphasis is flattened so the line carries exactly one bold span — the
identifier. The requirement's wording is not otherwise altered.

The marker is the join key between the roadmap's prose and the registry's owner, gate and
status. A registry entry whose identifier is absent from the roadmap is an error, and so
is a roadmap item with no registry entry.

### 2.2 Counts

The roadmap carries **83** requirements: 10 in §3, 6 in W1, 8 in W2, 6 in W3, 9 in O1,
6 in O2, 10 in N1, 10 in N2, 6 in §7 and 12 in §10.

### 2.3 Non-requirement content

Not every line in the roadmap is a requirement. §1 (success), §2 (baseline), §8
(architecture blueprint), §9 (delivery sequence), §11 (references) and the per-section
**Gate:** paragraphs are prose. The **Gate:** paragraphs are the *source* for the
per-requirement `gate` field where they apply, and where they do not, the gate is written
from the requirement's own text. Gates are always observable: they name an action, an
artifact or a check, never a state of mind.

---

## 3. Registry schema (shared interface)

`docs/replacement/requirements.json` is the machine-readable registry. It is the file the
validation test reads.

```jsonc
{
  "schema_version": "1.0",
  "roadmap": "docs/product-replacement-roadmap.md",
  "stages": { "M0": "...", "M1": "...", "M2": "...", "M3": "...", "M4": "..." },
  "statuses": ["not-started", "in-progress", "evidence-partial", "complete"],
  "requirements": [
    {
      "id": "F.01",
      "track": "F",
      "title": "Durable workspace identity",
      "section": "3",
      "stage": "M1",
      "owner": "kotlin:core",
      "status": "not-started",
      "gate": "An observable acceptance check for this requirement.",
      "evidence": ["kotlin/core/src/main/kotlin/lmeditor/core/Schemas.kt:120"],
      "blocks": ["P.01"]
    }
  ]
}
```

### 3.1 Field rules

| Field | Rule |
| :--- | :--- |
| `schema_version` | Semver string. Bump on any incompatible schema change. |
| `roadmap` | Repo-relative path to the roadmap. Must exist. |
| `stages` | The five milestones, verbatim from roadmap §9. |
| `statuses` | The four-value vocabulary below. |
| `id` | `^(F\|W1\|W2\|W3\|O1\|O2\|N1\|N2\|P\|R)\.\d{2}$`. Unique. |
| `track` | The part of `id` before the dot. |
| `title` | The requirement's own subject, 2–12 words. Not a full sentence. |
| `section` | The roadmap section number as a string (`"3"`, `"4"`, `"5"`, `"6"`, `"7"`, `"10"`). |
| `stage` | One of `stages`. The milestone by which the requirement must be complete. |
| `owner` | `<tier>:<module>` — see §3.2. Never free text. |
| `status` | One of `statuses` — see §3.3. |
| `gate` | One observable check. ≥ 20 characters. Names an action and an artifact or result. |
| `evidence` | Array of `path:line` strings, or test node ids. May be empty **only** when `status` is `not-started`. |
| `blocks` | Array of requirement ids this one gates. May be empty. Every id must resolve. |

### 3.2 Owner notation

| Prefix | Meaning |
| :--- | :--- |
| `kotlin:` | A Kotlin tier module: `core`, `text`, `word`, `vault`, `notebooks`, `library`, `server`, `workflow`, `ingest`, `pdf`, `editing`, `factcheck`, `discovery`, `bridge`, `desktop`. |
| `python:` | A Python package: `core`, `word`, `library`, `ingestion`, `provenance`, `notebooks`, `workflow`, `server`, `verification`, `drafting`, `vault`, `export`, `assistant`, `feedback`, `llm`, `tier`, `bridge`. |
| `cross:` | Spans tiers. The suffix names the coordinating domain, e.g. `cross:documents`, `cross:research`, `cross:delivery`, `cross:evidence`. |

A requirement owned by the UI has `kotlin:desktop` as owner only when the UI surface *is*
the requirement. When a rule must hold, the owner is the tier module that holds the rule
and `kotlin:desktop` appears in the gate.

### 3.3 Status vocabulary

| Status | Meaning |
| :--- | :--- |
| `not-started` | No implementation exists. `evidence` is empty. |
| `in-progress` | A partial implementation exists. `evidence` names it. |
| `evidence-partial` | The behavior exists but the acceptance evidence does not yet cover it. |
| `complete` | Meets roadmap §10's definition of done: available in the UI when user-facing, persists correctly, has automated or manual evidence, handles its failure cases, documents compatibility limits, and is recorded in the architecture ledger. |

`complete` is a strong claim. It requires all six parts of §10's definition, and the
`evidence` array must name the artifact that satisfies each part — either one entry that
covers them all, or several.

### 3.4 Stage assignment

Stages follow roadmap §9's milestone table. Where a requirement is seeded early but
completed late, `stage` records the **completion** milestone and `evidence` records the
seed. `R.01` (compatibility corpus) is seeded by M0 and completes in M4.

---

## 4. File ownership of the M0 wave

Each file has exactly one owner. No subagent may write a file it does not own, and no
subagent may edit the roadmap or `ARCHITECTURE.md` — those are serialized orchestrator
edits.

| Path | Owner | Single responsibility |
| :--- | :--- | :--- |
| `docs/replacement/requirements.json` | registry | The machine-readable registry of all 83 requirements: owner, stage, status, gate, evidence. |
| `docs/replacement/supported-workflow-matrix.md` | registry | The human-readable cross-product matrix, generated in prose from the registry, with one row per requirement. |
| `backend/tests/test_replacement_requirements.py` | registry | The mechanical check that the roadmap and the registry agree, and that every entry is well-formed. |
| `docs/replacement/feature-inventory.md` | inventory | The DOCX, vault and notebook feature matrices: read, display, edit, preserve, export and known limitations per feature. |
| `docs/replacement/migration-recovery-contract.md` | recovery | The migration, revision, conflict and recovery contract that M1 will implement. |
| `docs/replacement/editor-layout-feasibility.md` | feasibility | The feasibility spike for real page layout under the tier's empty-dependency rule, with a recommendation and an explicit contract-change proposal. |
| `backend/tests/fixtures/compatibility/**` | corpus | The representative compatibility fixtures and their manifest. |
| `backend/tests/test_compatibility_corpus.py` | corpus | The mechanical check that the corpus matches its manifest and that each fixture behaves as the manifest claims. |
| `docs/product-replacement-roadmap.md` | orchestrator | The backlog of record. Serialized edit only. |
| `docs/replacement/README.md` | orchestrator | The index of the replacement program's artifacts. |
| `ARCHITECTURE.md` | orchestrator | The repository ledger. |

---

## 5. Evidence rules (binding on every artifact)

1. **No unverified claim.** Every statement that a behavior exists names the file and
   line, or the test that exercises it. `path:line` form is required for file evidence.
2. **A skip is not a pass.** A test that is skipped — Office-dependent tests, network
   tests, platform tests — is recorded as *unverified*, never as *verified*. The
   compatibility matrix has a distinct status for this.
3. **Absence is reported, not assumed.** If a feature cannot be located in the tree, the
   matrix says `not present` with the search that established it (module and pattern),
   not `not supported`.
4. **Distinguish "can parse" from "can edit".** A format the reader accepts is not a
   format the editor can change, and a preserved object is not an editable object. The
   feature inventory has separate columns for these and they may differ.
5. **A parser is not a workflow.** Per roadmap §10, evidence must show the behavior is
   reachable and usable, not merely that a function exists.
6. **Preserved is not verified.** For DOCX round trips, "preserved" means the part
   survives a read/write cycle; it does not mean it is editable, and it does not mean
   Word renders it identically. Both claims are separate columns.

---

## 6. Compatibility statuses (shared vocabulary)

The feature inventory and the corpus manifest use the same six values.

| Status | Meaning |
| :--- | :--- |
| `read` | The format's content is read into the model. |
| `display` | The content is shown to the operator. |
| `edit` | The operator can change it, and the change is persisted. |
| `preserve` | The content survives a read/write cycle unchanged in meaning. |
| `export` | The content is written into an output format. |
| `limited` | Supported, with a documented restriction. The restriction is stated. |
| `unsupported` | Not supported. The reason is stated. |
| `unverified` | The status could not be established from the tree. The blocker is stated. |
| `not-applicable` | The column does not apply to this feature, or the evidence at hand does not measure it. This is an honest "not measured here", never a claim of support. |

A cell is never blank, and a cell is never a value outside this list. `not-applicable`
is the value for a column that exists but that the evidence at hand does not
establish -- a corpus that shows a package can be *read* has not shown that it can
be *edited*, and saying so is different from claiming the edit is unsupported.

---

## 7. Acceptance for the M0 wave

M0 is complete when **all** of the following hold:

1. Every checkbox in the roadmap carries a `**<ID>**` marker, and the 83 markers are
   unique and correctly formatted.
2. `docs/replacement/requirements.json` contains exactly those 83 ids, and every entry
   has a well-formed `owner`, `stage`, `status` and a `gate` of at least 20 characters.
3. `backend/tests/test_replacement_requirements.py` passes, and it *fails* when the
   roadmap and the registry disagree (the test must be shown to have teeth).
4. `docs/replacement/supported-workflow-matrix.md` has one row per requirement and is
   consistent with the registry.
5. `docs/replacement/feature-inventory.md` covers DOCX, vault and notebook features with
   the six compatibility columns and a stated limitation or `none` for every entry.
6. `docs/replacement/migration-recovery-contract.md` defines the preconditions, the
   conflict outcomes, the recovery points and the failure handling for the M1 work, and
   says which roadmap requirements it implements.
7. `docs/replacement/editor-layout-feasibility.md` reaches a decision: either the
   layout engine can be built under `kotlin/CONTRACTS.md` §1 without a contract change,
   or it cannot, in which case the document proposes the specific contract change, its
   cost and its maintenance burden, and marks the affected requirements as blocked.
8. `backend/tests/fixtures/compatibility/` holds the representative fixtures with a
   manifest, and `backend/tests/test_compatibility_corpus.py` passes.
9. `ARCHITECTURE.md` records every new file and its single responsibility.

### 7.1 What M0 does not decide

- Whether `kotlin/CONTRACTS.md` §1 is amended. M0 *proposes*; the architecture lead
  decides with the evidence in hand.
- Any implementation. M0 writes no production code and changes no runtime behavior.
- Any stage date or staffing estimate. Roadmap §9 defers those to M0's feasibility
  results, and M0's feasibility result is scoped, not a schedule.

---

## 8. Decisions of the architecture lead

These are recorded so they are not silently decided by an implementer. The full
register — including the twelve decisions the recovery contract raised — is
[`decisions.md`](decisions.md); the rulings that bear on this blueprint are below.

| # | Question | Raised by | Decision |
| :--- | :--- | :--- | :--- |
| 1 | Does the tier's empty-dependency rule survive a real pagination engine? | §8 dependency decision | **No, not as written — and the rule is amended rather than abandoned.** A narrow clause, `kotlin/CONTRACTS.md` §1.2, admits one asset category (bundled typographic metric tables, with the licence notice that travels with them) and forbids tier modules from measuring text with `java.desktop`, so the tier's layout stays a function of its input on every machine. See [`editor-layout-feasibility.md`](editor-layout-feasibility.md) §1 and §8.1 for the spike and the reasoning. |
| 2 | Are bundled font files an asset dependency, and therefore a contract change? | feasibility | **Yes, and that is now stated in the contract.** Only *metrics* are admitted — a versioned table of glyph advance widths — never a font program. A font program is a separate decision, needed only for embedded PDF text, and is not admitted. The metrics must come from a font whose licence permits redistribution of the derived data, so a SIL Open Font Licence release; the terms of any other metric set must be confirmed before that set is chosen. |
| 3 | Is a durable source archive a Kotlin tier module or a Python tier module? | §3 F.02, §8 `kotlin/server/SourceArchive.kt` | Open. It is decided by F.02's own design, and `migration-recovery-contract.md` D-4 settles the sharper half of it: a notebook reference must beat the upload store's modification-time eviction, or F.02 is violated and N1.01's "report missing sources" cannot be honoured. |
| 4 | Does the requirement registry become the source the desktop reads at runtime? | registry | Open. M0 keeps the registry a development artifact. If a later milestone needs it at runtime, that is a new contract with a new schema version, not a quiet change to this one. |
| 5 | Which `.doc` conversion path is supported, if any? | W3.06 | Open, and explicitly out of M0's scope. The roadmap's W3.06 asks for legacy `.doc` behaviour to be *documented* — preserved safely or warned about — and `kotlin/word/WordFormats.kt` already refuses the format rather than mis-parsing it. |
