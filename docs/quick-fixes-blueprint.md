# Action-plan quick fixes

Scope: the small A3 boundary/style checks and the stale workflow matrix identified in `ACTION_PLAN.md`. No shared schema or wire changes; no requirement status promotion.

| Owner | Paths | Single responsibility and acceptance gate |
| --- | --- | --- |
| Boundary agent | `kotlin/core`, `kotlin/ingest`, `kotlin/server` boundary validators and their existing tests | Reject C1 controls at identifier/path entry points, including U+0085; preserve valid Unicode; focused regressions pass. Inspect other store validators and report gaps outside ownership. |
| Word agent | `kotlin/word` style writer and existing tests | Inspect user-supplied self-referential styles, make the narrow necessary correction, and prove the exported styles cannot acquire an invalid self-reference. Preserve unrelated round-trip behavior. |
| Registry agent | `docs/replacement/supported-workflow-matrix.md`, `backend/tests/test_replacement_requirements.py` | Reconcile the matrix with the registry, retaining R.08 complete and every other status; add focused drift proof if absent. Do not promote ignored generators in this slice. |
| Lead | `ACTION_PLAN.md`, `ARCHITECTURE.md`, this file | Record verified outcomes, exact validation, remaining scope and file responsibilities. Review merged changes and run relevant combined checks. |

Agents must enforce boundary validation, contextual deterministic errors, strict existing types and idempotent state changes. Reuse existing files and abstractions. Do not introduce modules or unrelated cleanup. Git usage is limited to log, status and diff. All writes stay in this workspace.

Stop when the three focused gates pass and the ledger records the evidence. Installed deployment, broad SSE recovery, dependency audit and the remaining A3/A7 work retain their existing gates.
