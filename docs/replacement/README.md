# The replacement program

This folder holds the artifacts of LMeditor's replacement program: the work that turns
one application into a replacement for Microsoft Word, Obsidian and NotebookLM.

The program is ordered by the roadmap. The roadmap is the backlog of record; this folder
is where the contracts, the evidence and the measurements that make the backlog
checkable live.

## The roadmap

Implementation has started with [source persistence](../source-persistence-blueprint.md):
extracted source records/chunks survive restart and cache eviction, and notebook
Ask reports unavailable source IDs before inference. The registry records F.02 and
N1.01 as `in-progress`; original uploads, revision history and full M1 recovery
remain outstanding.

[`docs/product-replacement-roadmap.md`](../product-replacement-roadmap.md) carries **83**
requirements. Each one has a stable identifier — `F.01`, `W1.03`, `R.12` — written into
the item itself, so an identifier survives rewording of the prose and a reference to a
requirement means the same thing in every document here.

The roadmap groups them by target and by stage:

| Track | Roadmap section | Milestone |
| :--- | :--- | :--- |
| `F` | §3 Shared foundation | M1 |
| `W1`, `W2` | §4 Word, daily replacement | M2 |
| `O1` | §5 Obsidian, daily replacement | M2 |
| `N1` | §6 NotebookLM, daily replacement | M2 |
| `P` | §7 Making them one product | M2 |
| `W3`, `O2`, `N2` | §4–§6, broad replacement | M3 |
| `R` | §10 Release evidence | M4 |

## What is here

| File | Single responsibility |
| :--- | :--- |
| [`m0-blueprint.md`](m0-blueprint.md) | The M0 wave contract: requirement identity, the registry schema, file ownership, evidence rules and the acceptance gate. |
| [`decisions.md`](decisions.md) | The decision register: what the architecture lead ruled on the questions the artifacts raised, and what M1 still owes. |
| [`requirements.json`](requirements.json) | The machine-readable registry. One entry per requirement, with its owner, milestone, status, observable gate and evidence. |
| [`supported-workflow-matrix.md`](supported-workflow-matrix.md) | The human-readable cross-product matrix, one row per requirement, consistent with the registry. |
| [`feature-inventory.md`](feature-inventory.md) | The DOCX, vault and notebook feature matrices: read, display, edit, preserve, export and the known limitation per feature. |
| [`migration-recovery-contract.md`](migration-recovery-contract.md) | The migration, revision, conflict and recovery contract that M1 implements. |
| [`editor-layout-feasibility.md`](editor-layout-feasibility.md) | The feasibility spike for real page layout under the tier's empty-dependency rule, with a decision and its cost. |

The M0 deliverables also include the representative fixture corpus, which lives with the
tests that own it: `backend/tests/fixtures/compatibility/`.

## The decision M0 took

The roadmap deferred one question to M0: whether a real pagination engine can live inside
the Kotlin tier's rule that no tier module carries a third-party runtime dependency.

The answer was **yes, but the rule needed one narrow amendment**. Pagination needs glyph
advance widths; the JDK ships no font data; and without a clause the engine would have had
to measure text with `java.awt`, which resolves a font from the host — so the same document
would break into different lines on different machines, and the tier's output would stop
being a function of its input.

[`kotlin/CONTRACTS.md` §1.2](../../kotlin/CONTRACTS.md) therefore admits exactly one asset
category — a versioned table of glyph advance widths, with the licence notice that travels
with it — and prohibits tier modules from measuring text with `java.desktop`. The metrics
are *data, not a dependency*, so the tier's runtime dependency set stays empty.

The amendment is **enforced, not conventional**: the existing class check reads only
`.class` entries, so two assertions were added to close that gap. The reasoning, the
effort and what the decision puts permanently out of scope are in
[`editor-layout-feasibility.md`](editor-layout-feasibility.md).

## How the artifacts fit together

```
docs/product-replacement-roadmap.md      the backlog of record, 83 requirements
            │
            │  each item carries a stable **ID** marker
            ▼
docs/replacement/requirements.json       the registry: owner, milestone, status, gate, evidence
            │                                        │
            │                                        ├── supported-workflow-matrix.md
            │                                        ├── feature-inventory.md
            │                                        └── migration-recovery-contract.md
            ▼
backend/tests/test_replacement_requirements.py
            the check that the roadmap and the registry agree
```

The registry is the join key. Without it the roadmap is prose that no test can check and
the matrix is a table that nothing keeps honest.

## How to read a status

The registry uses four values, and they are claims of different strength:

| Status | Claim |
| :--- | :--- |
| `not-started` | No implementation exists. |
| `in-progress` | A partial implementation exists, and the entry names it. |
| `evidence-partial` | The behavior exists; the acceptance evidence does not yet cover it. |
| `complete` | Every part of the roadmap's §10 definition of done is satisfied, and the entry names the artifact that satisfies each one. |

A status is a snapshot of the working tree on a date, not a certification of the product.
`complete` is the strongest claim in the program, and it is the one the validation test
holds to the most.

## The evidence rules

Every artifact here obeys the same rules, stated in
[`m0-blueprint.md` §5](m0-blueprint.md):

1. No unverified claim — a statement that a behavior exists names the file and line, or
   the test that exercises it.
2. A skip is not a pass — an Office-dependent, network-dependent or platform-dependent
   test that did not run is *unverified*, never *verified*.
3. Absence is reported — a feature that cannot be found is "not present in *module*,
   searched *pattern*", not "not supported".
4. "Can parse" is not "can edit", and "preserved" is not "editable" and not "renders
   identically in Word".

## Verifying the program

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_replacement_requirements.py tests/test_compatibility_corpus.py -q
```

The first test fails if the roadmap and the registry drift apart, or if a requirement
loses its owner or its gate. The second fails if the fixture corpus and its manifest
drift apart.
