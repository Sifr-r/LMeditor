# Codex generation integration

Use the installed, signed-in Codex CLI via its supported `exec` interface.
Keep credentials under CLI ownership. Feed DSPy prompts over stdin and return the
final assistant text through a bounded, read-only, ephemeral subprocess. Use an
isolated workspace beneath the project, with no repository instructions or tools
needed for manuscript generation. Do not read or copy authentication tokens.

| File | Responsibility |
| --- | --- |
| `backend/src/lmeditor/llm/codex.py` | DSPy BaseLM adapter, safe CLI invocation, timeout and contextual failures. |
| `backend/src/lmeditor/llm/config.py` | Explicit Codex provider configuration. |
| `backend/src/lmeditor/llm/factory.py` | Bootstrap the Codex adapter and report provider health. |
| `backend/tests/test_codex_provider.py` | Offline adapter/configuration regression proof. |
| `backend/README.md` | Codex startup and authentication instructions. |
| `ARCHITECTURE.md` | Final responsibility ledger and observed live evidence. |

Acceptance: offline provider tests pass; a real Codex call succeeds; backend
reports Codex rather than stub; a source-grounded workflow completes and its
authoritative manuscript can be fetched and exported. Keep source fixture and
generated evidence inside the project. Report any real provider/gate failure.

## Result

Implemented with no new dependencies. All 37 focused tests and strict targeted
adapter mypy pass. Real DSPy/Codex smoke and the existing full HTTP/SSE generation
test pass. `doc_9aadcac584dd` completed with three verified sections, 3/3 supported
final claims and F-beta 1.0. Its 5,219-byte Word export and replayable evidence are
saved under `.tools/codex-generation-test/`. The Codex-backed server is running
on the desktop client's default address, `http://127.0.0.1:8000`.
