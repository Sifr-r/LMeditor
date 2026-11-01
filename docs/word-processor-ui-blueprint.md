# Word processor UI blueprint

The active client is Kotlin Compose (`kotlin/desktop`), with immutable controller
state and separately owned transport and rendering. Preserve those boundaries.

Design: a restrained editorial word processor. Deep green accents, warm neutral
chrome, serif manuscript typography, a centered paper sheet and compact document
controls. Research and verification are optional side panels, leaving writing
space central. Controls must perform real actions; do not imply unsupported rich
text editing or persisted formatting.

## Delegated ownership

All UI paths below are in `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/`.

| File | Single responsibility |
| --- | --- |
| `StudioScreen.kt` | Adaptive document workspace, sidebar visibility and status. |
| `ConnectionBar.kt` | Document header, topic/run/export actions and expandable connection settings. |
| `EditorPane.kt` | Paper canvas, manuscript rendering and functional reading controls. |
| `DocumentRuler.kt` (new, if needed) | Decorative page ruler aligned to the paper canvas. |
| `Theme.kt` | Shared restrained chrome and manuscript typography/color tokens. |
| `docs/word-processor-ui-blueprint.md` | Scope, ownership and acceptance record. |
| `ARCHITECTURE.md` | Final architecture and new-file responsibility ledger. |

## Acceptance

- Manuscript has visible page boundaries, comfortable margins and an empty page.
- Header keeps backend settings secondary while retaining URL Apply safeguards.
- Research/review panels can be hidden; citation selection can reveal review.
- Reading controls operate on presentation only and are labeled accordingly.
- Existing generation, citation selection, review, export and errors remain wired.
- Compile and run desktop tests; inspect the live desktop layout if available.
- Add no dependencies; preserve unrelated working tree changes.

## Result

Implemented with all five UI files and the new `DocumentRuler.kt`. Offline
`:desktop:build` succeeds; all 45 existing desktop tests pass. Reviewed pending
connection visibility, repeated citation activation and compact-editor state
preservation. Visually inspected the live dark-theme window and exercised Focus
and reading-size controls. Backend unavailable during this pass; generation and
export were not exercised live.
