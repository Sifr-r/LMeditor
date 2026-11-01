# Production readiness: reproducible validation

Date: 2026-10-28. Scope: verify the existing candidate and establish a repeatable
release gate. Passing this gate does not establish that the application is safe
to release; authentication, accessibility, platform and performance gaps remain.

## Acceptance

- The Python lock describes the current project and development dependencies.
- A checked-in command rebuilds the Kotlin bridge, reruns Kotlin tests without
  cached test results, runs strict Python typing, the complete backend suite,
  parity and the live HTTP document round trip.
- Missing prerequisites, failed subprocesses, missing test reports, zero tests
  and skipped integration tests produce failure, never a green release gate.
- An explicit portable CI mode excludes only named real-Word and real-model
  checks, retains an exclusions manifest, and cannot claim full release proof.
- CI runs the same command from a clean checkout and retains verification reports.
- Local results and unproven conditions are recorded separately.

## File responsibilities and ownership

| Path | Single responsibility | Owner |
| --- | --- | --- |
| `backend/uv.lock` | Reproducible resolution of current Python project metadata | Dependency lane |
| `backend/scripts/verify_release.py` | Coordinate existing gates and reject incomplete evidence | Gate lane |
| `backend/tests/test_release_gate.py` | Prove gate failures cannot silently become success | Gate lane |
| `.github/workflows/verify.yml` | Provision isolated runners and invoke the release gate | Coordinator |
| `docs/production-readiness.md` | Operator instructions, evidence and remaining release blockers | Coordinator |
| `docs/production-readiness-blueprint.md` | Scope, acceptance and file ownership for this slice | Coordinator |
| `ARCHITECTURE.md` | Record validation ownership and updated readiness evidence | Coordinator |

The HTTP server, desktop protocol and deterministic domain rules are outside this
slice. Existing source changes are preserved. No git mutation or publishing is
part of this work. Every implementation lane must validate boundary inputs, use
contextual failures and strict typing, and make repeated runs safe. Generated
reports belong under the ignored `build/` directory, never in operator data.
