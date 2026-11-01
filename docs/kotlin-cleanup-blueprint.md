# Kotlin reliability and Flutter retirement blueprint

## Acceptance

The supported client is `kotlin/desktop`; the already deleted `client/`
Flutter tree stays deleted. Current setup documentation points to Compose.
Transport failures appear in studio state, stream cancellation releases HTTP
resources, and switching backends cannot retain or overwrite another backend's
document. Domain modules retain their dependency policy.

## File responsibilities

| Path | Responsibility / planned change |
| --- | --- |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/BackendClient.kt` | HTTP request execution; classify network failures and validate backend URLs. |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/SseClient.kt` | Cancellable SSE transport, frame assembly, and response resource ownership. |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/net/HttpTransport.kt` | Shared backend URL validation, path encoding and cancellable HTTP future ownership. |
| `kotlin/desktop/src/test/kotlin/lmeditor/desktop/net/BackendClientTest.kt` | Regression proof for HTTP failures and invalid connection input. |
| `kotlin/desktop/src/test/kotlin/lmeditor/desktop/net/SseClientTest.kt` | Regression proof for framing, failures and cancellation. |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/StudioController.kt` | Connection/request lifecycle and visible task/stream errors. |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/RunReducer.kt` | Terminal review cleanup and document event isolation. |
| `kotlin/desktop/src/test/kotlin/lmeditor/desktop/model/StudioControllerTest.kt` | Regression proof for connection isolation and error reporting. |
| `kotlin/desktop/src/test/kotlin/lmeditor/desktop/model/RunReducerTest.kt` | Terminal transition and cross-document regression proof. |
| `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/ConnectionBar.kt` | Editable backend URL draft with explicit validated Apply action. |
| `kotlin/desktop/README.md` | Operator instructions for applying backend connection changes. |
| `kotlin/README.md`, `backend/README.md`, `GAP_ANALYSIS.md`, `CODEBASE_REPORT.md` | Current setup/ownership guidance and accurate archive banners. |
| `ARCHITECTURE.md` | Current architecture summary and final file responsibility ledger. |
| `docs/kotlin-cleanup-blueprint.md` | This scoped, verifiable implementation plan. |

Transport, controller/reducer, and documentation changes are delegated to
separate agents. No new runtime dependencies or product features are planned.
Historical Flutter plans and frozen Python parity references remain evidence.
Verification: Kotlin tests/build, Python dependency/wire contract checks, and
scan for remaining active Flutter paths. All cache writes stay in the project.

## Result

Implemented and reviewed. Offline Gradle `build` succeeds across all 13 modules;
test reports total 807 tests with zero failures/errors. The targeted Python
dependency, wire and stream contract suites pass all 44 tests. No Flutter
project artifacts remain on disk; historical migration evidence is retained.
The URL field received an explicit Apply action after review caught that
validation on every keystroke would prevent entering a replacement address.
