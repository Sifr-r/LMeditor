# Medium-priority action-plan batch

Date: 2 November 2026. The action queue has no explicit severity labels; this
batch selects bounded follow-ups from A3, A5 and A7 after the quick fixes.

| Owner | Paths | Responsibility and acceptance gate |
| --- | --- | --- |
| Redaction agent | `backend/src/lmeditor/server/routes_helpers.py`; existing server sanitization tests | Mask absolute POSIX paths beyond known roots without corrupting public URLs or useful error text. Adversarial regression tests fail before and pass after the fix; nearest sanitizer suite passes. |
| SSE notice agent | Desktop `StudioState.kt`, `StudioController.kt`, existing controller/model tests, and `DocumentsScreen.kt`, `NotebookScreen.kt`, `StudioScreen.kt`, `VaultScreen.kt` | Retain permanent stream-loss notices across later overflow/errors and show them alongside errors in all four screens. Clear them when a new run begins. Preserve bounded reconnect and transport behavior. No public wire changes or snapshot reconciliation. |
| Documentation tooling agent | New `backend/scripts/build_replacement_matrix.py`, `check_replacement_links.py`, `check_inventory_cells.py`, `check_compatibility_vocabulary.py`, `verify_corpus_digests.py`; new `backend/tests/test_replacement_doc_tools.py`; matrix output if needed | Promote the matrix renderer and four auxiliary checks from ignored helpers into maintained standard-library tools. Use the existing registry as authoritative; preserve manual status/gate corrections. Provide repeatable/check-only invocations and regressions demonstrating drift detection and deterministic generation. Do not execute the stale hardcoded registry builder. |
| Lead | This file, `ACTION_PLAN.md`, `ARCHITECTURE.md`, `docs/production-readiness.md` | Review combined changes, retain evidence, document exact file responsibilities and remaining gates. |

All agents must validate boundaries, retain strict existing types, use contextual
deterministic errors and keep mutations idempotent. No unrelated cleanup or
new runtime dependencies. Git is limited to log/status/diff; writes stay in the
project. Do not retry or bypass the previously denied external Gradle cache.
Coordinate any newly required files or contract changes with the lead first.

Stop after the focused checks and documentation are complete. Kotlin execution
remains pending if the environment blocker persists. A3/A5/A7 are not broadly
closed by these scoped changes; installed deployment, full SSE recovery,
registry regeneration and performance qualification retain their own gates.
