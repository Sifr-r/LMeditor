# Source persistence implementation slice

Date: 26 October 2026. Scope: F.02 / N1.01, partial delivery.

## Acceptance

Ingested source records, metadata and extracted chunks survive a fresh Kotlin
bridge process. Cache pressure never removes archived records. Existing source
HTTP endpoints need no caller changes. A notebook with missing source references
reports those IDs instead of answering against an undisclosed reduced corpus.
Writes are atomic and flushed before acknowledgement; corrupt records and I/O
failures become contextual errors. No new runtime dependency is permitted.

This slice does not yet archive original upload bytes, version sources, persist
conversations or certify the full F.02 requirement.

## Files and responsibilities

| Owner | Files | Responsibility |
| --- | --- | --- |
| Storage agent | `kotlin/server/.../SourceArchive.kt` | Versioned disk records, hashed safe filenames, atomic writes, validation and explicit errors |
| Storage agent | `kotlin/server/.../SourceLibrary.kt` | Optional archive-backed registry with bounded memory cache and archive-aware lookup/list/removal |
| Storage agent | `kotlin/server/src/test/.../SourceArchiveTest.kt` | Reconstruction, cache eviction, corruption, deletion, safe IDs and write-failure regression proof |
| Integration agent | `kotlin/bridge/.../HandlersServer.kt` | Select archive-backed registry by explicit request root; preserve rootless memory semantics for legacy bridge consumers |
| Integration agent | `backend/src/lmeditor/tier/server.py` | Pass the selected archive root on source calls; resolve `LMEDITOR_SOURCES_DIR`, defaulting inside the project at `backend/.lmeditor/sources` |
| Integration agent | `backend/src/lmeditor/server/sources.py` | Correct facade documentation for archive/cache behavior |
| Integration agent | `backend/tests/conftest.py`, `test_tier_server_sources.py`, `test_source_persistence.py` | Isolated test archive roots, intentional retention semantics and fresh-process persistence coverage |
| Notebook agent | `backend/src/lmeditor/notebooks/answer.py`, `backend/tests/test_notebook_routes.py` | Refuse incomplete source scope with explicit missing IDs before model inference |
| Lead | `kotlin/core/.../StorageErrors.kt`, Python core exports/exceptions and bridge error mapping, `backend/tests/test_storage_errors.py` | Shared M1 storage/conflict/interruption vocabulary and cross-tier error identity |
| Lead | `.gitignore`, `backend/README.md`, `ARCHITECTURE.md`, roadmap/requirement status | Runtime-data exclusion, operator guidance and measured delivery record |

`...` means the existing `src/main/kotlin/lmeditor/<module>/` package root.

## Shared interfaces

- `SourceArchive(root: java.nio.file.Path)` owns disk I/O, uses existing
  `storedSourceToWire` / `storedSourceFromWire`, and provides add/get/list/remove/clear.
- `SourceLibrary(maxSources: Int = MAX_SOURCES, archive: SourceArchive? = null)`
  preserves existing behavior without an archive. With an archive, all entries
  remain discoverable after memory eviction; disk commits precede cache changes.
- Source bridge calls accept optional `root: String`; the root must be absolute.
  Rootless calls retain the existing memory registry. Python normal calls always
  supply a root, resolved per call so tests and workspace configuration are isolated.
- The disk envelope contains schema version `1.0.0` and the complete stored-source wire
  record. Hash source IDs to filenames; reject symlink records and corrupt envelopes.
- Do not cache archive-backed registries by every test root indefinitely. Reuse
  only the currently selected root (or reconstruct per request) with safe locking.
- Missing notebook source IDs raise `NotebookAskError` carrying
  `missing_source_ids`; existing HTTP error mapping must expose the failure.

## Contract limits for this slice

The D-1/D-2/D-3 shared error types are introduced before the new store is built;
their availability does not mean revision checking or recovery journals exist.
P-1 is resolved in favor of the existing Kotlin server domain owning the archive,
while Python passes configuration and inference requests only. Records use a
per-store semver version as required by MRC-2.5. Unsupported versions are refused;
there are no previous archive versions to migrate.

The file is flushed before atomic replacement. This establishes restart/process
crash recovery for committed records. Portable directory fsync on Windows is not
provided by this JDK implementation, so this slice does not claim MRC-3.4 power-loss
durability or completion of M1's broader write/recovery contract. Explicit deletion
and clear remove archive records; removing membership alone does not delete them.

All agents validate boundaries, use typed contextual errors and idempotent
mutations, preserve unrelated working-tree changes, and restrict writes to this
workspace. Git use is limited to status/log/diff. RTK is unavailable; direct
commands are the fallback. Integration waits on storage interfaces, then the lead
runs focused Kotlin and Python regression checks before marking progress.

## Verified result

The offline Kotlin server/bridge build and packaged tier completed successfully:
91 tests passed with no test skips, including 16 archive regressions. The focused
Python regression gate passed 96 tests with no skips, including actual fresh-JVM
restoration, notebook Ask after restart, missing-source refusal and the requirement
registry. Strict mypy validation passed for the five changed Python source modules.
Test-helper import failure found on the first Python run was corrected
before the final green gate. Local JVM loopback access required running the checks
outside the network-restricted sandbox; all data and build outputs stayed inside
the project. No desktop launch or full-suite production certification is claimed.
