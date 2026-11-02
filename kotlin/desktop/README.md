# The Desktop Client

The operator's window. It is the one part of this repository that is **not** a
tier module, and the only one allowed to carry a third-party dependency.

```
:desktop  ->  :core
```

The arrow runs one way and one way only. The client may use every rule the
tier holds; the tier can never come to depend on a user interface.

---

## 1. Why it is not a tier module

The tier's rule is that a module's runtime dependency set is empty, so a
reader can trace every behaviour to the JDK. A user interface cannot live
inside that rule: a user interface *is* dependencies. Compose Multiplatform
and the AndroidX transitives it pulls in are what a window is made of.

So the rule is **scoped to the tier**, and `:desktop` is named as the single
exemption. `kotlin/CONTRACTS.md` §1.1 states the exemption and what it does
not cover; `backend/tests/test_kotlin_dependencies.py` enforces it:

- every **tier** module must still declare an empty runtime dependency set;
- no tier module may depend on `:desktop`;
- `:desktop` must depend on `:core`.

The Google Maven repository is declared on this module alone, so the tier
still resolves against Maven Central only.

---

## 2. What it is

Compose Multiplatform **1.12.2**, Kotlin **2.4.21**, and the Compose
compiler plugin at **2.4.21** — the compiler plugin must match the Kotlin
version exactly.

| Package | Owns |
| :--- | :--- |
| `lmeditor.desktop` | `Main.kt` — the window and its coroutine scope |
| `lmeditor.desktop.net` | `BackendError`, `ApiCredential`, `MultipartBody`, `BackendClient`, `SseClient` |
| `lmeditor.desktop.model` | `StudioModels`, `StudioState`, `StudioController`, `RunReducer` |
| `lmeditor.desktop.render` | `ManuscriptBlocks` — the display renderer |
| `lmeditor.desktop.ui` | `Theme`, `FileDialogs`, and the fourteen remaining screens and panes |

---

## 3. Running it

Start the backend, then the client:

```powershell
# the backend, with the offline stub drafter
cd D:\LMeditor\backend
$env:PYTHONPATH="D:\LMeditor\backend\src"
$env:LMEDITOR_LM_PROVIDER="stub"
$env:LMEDITOR_LM_MODEL="offline-stub"
.\.venv\Scripts\python.exe -m uvicorn lmeditor.server.app:app --host 127.0.0.1 --port 8765

# the client, pointed at it
cd D:\LMeditor\kotlin
$env:LMEDITOR_BACKEND_URL="http://127.0.0.1:8765"
.\gradlew.bat :desktop:run --console=plain
```

`LMEDITOR_BACKEND_URL` sets the URL the window opens on. Without it the
client falls back to `http://127.0.0.1:8000`. To change it at runtime, edit
the backend URL under **Connection**, then choose **Apply**. Check, Refresh
sources, Write and Export stay disabled while the URL has unapplied changes.
Invalid addresses show an error when applied and leave the active connection
unchanged, so partial addresses can be edited freely.

### 3.1 The credential

Every route under `/api/` requires a bearer token, and the client sends it on
every request: the three REST request sites and the event stream. The token
is resolved **once**, when the window is built, in this order:

1. `LMEDITOR_API_TOKEN`, when it is set and not blank — the operator's own
   token, read the way the server reads it.
2. The token file: `LMEDITOR_API_TOKEN_FILE` when it is set, otherwise
   `<home>/.lmeditor/api-token`.

The token file is the channel between the two halves of the application: the
server writes the token it generated there, and the client reads it, because
the two are separate processes and the client is not necessarily started with
the server's environment.

A token that cannot be read is a typed failure that names the place the
client looked, not a request that quietly goes out without it. The token
never appears in a log message, an error message or a URL.

To package an installer:

```powershell
.\gradlew.bat :desktop:packageDistributionForCurrentOS
```

Targets are declared for MSI, DMG and DEB in `build.gradle.kts`.

---

## 4. The window

One application, four workspaces. A quiet rail across the top moves between
them: **Write**, **Documents**, **Vault** and **Notebook**.

### 4.1 Write

One document workspace with two optional inspectors:

- **Research** — *Sources* (ingest, selection, the passages the server
  holds), *Research* (the run's perspectives and research notes), *Outline*
  (the operator's own outline, and the outline the run produced).
- **Centre** — a bounded manuscript page with a ruler and reading controls. It streams in during the run and shows the
  finished document afterwards. Inline citations render as clickable chips;
  clicking one selects the claim and shows the passage it points at.
- **Review** — the verifier's report: the scorecard, the claims and their
  entailment status, and the supporting passage.

The header holds the document topic, Write and Export. **Connection** expands
backend settings. **Research** and **Review** toggle the inspectors; **Focus**
hides both. Citation activation reveals Review. Narrow windows show one
inspector at a time. Font and text-size controls adjust the reading view only.
The review-gate banner and bottom status strip retain run state, metrics and
visible failures.

The health pill distinguishes four states, because they mean different things
to an operator: *not checked*, *up, cannot write* (reachable but no model),
*ready (offline stub)* (the stub drafter is loaded) and *ready*.

### 4.2 Documents

The word processor. The backend reads a `.docx` into a book of editable
sections and renders it back out again; this workspace is what reaches that
work.

- **Open .docx** sends a Word file to the server, which reads it into a book
  and indexes its sections.
- The **left column** lists the books the server holds, and the sections each
  one is made of. Opening a section shows its text.
- The **centre** is the section's text, editable, on the same bounded paper
  canvas the studio uses. **Save** writes it back; the header marks the
  section *Unsaved* until it does.
- **Save as .docx** writes the whole book back out as a Word package, with the
  document's tables, images, notes and styles intact.
- **Close** closes the book. The library stays.

Switching section, book or document saves the unsaved section first. A save
that fails refuses the switch: the operator's text is never dropped to show
something else.

**Server** expands the backend address, which both workspaces share. The
address is applied, not typed-and-hoped-for: nothing changes on the server
until **Apply** is pressed.

### 4.3 Vault

The Obsidian replacement. A vault is **a folder of markdown files**, and the
folder stays a folder: it opens in Obsidian, it opens in any text editor, and
it is greppable. Nothing here is locked inside a private database.

- **Open vault…** points the application at the operator's own vault. The
  server holds that one folder; every note is read from it as it is.
- The **left column** holds the search box, the notes (grouped by the folder
  they are in, or the search results with their scores), and the vault's tags
  with the number of notes that carry each one. Clicking a tag searches for
  it.
- The **centre** is the note's markdown, editable, on the same bounded paper
  canvas as the studio. Under it are the note's **tags**, the **links** it
  holds and the **backlinks** that point at it, so the operator can walk the
  graph without leaving the note.
- **New note**, **Rename** and **Delete** act on the file itself, so a
  deletion asks before it deletes.

A wiki-link resolves as Obsidian resolves it: by file name first, then by the
full path, then by the note's title. A note written with Windows line endings
is read exactly as a note written with Unix ones.

Switching note saves the unsaved one first, exactly as the documents workspace
does: a failed save refuses the switch.

### 4.4 Notebook

The Open Notebook replacement. A **notebook** is a named collection of sources
and notes that lives in a folder on the operator's disk, and the **ask** is the
question it answers from those sources and from nowhere else.

- **Choose folder…** points the server at the folder the notebooks live in.
  The server holds that one folder; every notebook is read from it as it is.
- The **left column** holds the notebooks, each with the number of sources and
  notes it holds, and **New notebook** for one more.
- The **right column** is the open notebook: its title and description,
  editable and saved with **Save**; the **sources** it holds, each removable;
  and the **notes** it refers to, each removable. A source is added by picking
  one from the studio's own source list, and a note by naming its id — a note
  belongs to the vault, and a notebook only points at it.
- The **ask** is a question box and an **Ask** button. The answer comes back
  with the passages it leaned on, each showing the source's title and the
  passage quoted, so the operator can read the evidence rather than trust it.
  Clicking a citation opens the passage in the studio's source pane.

An answer the notebook's sources cannot support is shown as **NOT ANSWERED**,
with no citations — a refusal is a refusal, and dressing it up as an answer
would be the one thing this workspace must never do.

A source outside the notebook is never cited: the retrieval runs over the
notebook's own sources, so a passage that is not in the notebook is not a
passage the answer may lean on.

---

## 5. What it talks to

The client is a **consumer of the published HTTP surface**. It speaks the
same routes any other consumer would, over `java.net.http`, and it follows
the run's SSE stream.

It does **not** use the NDJSON bridge. The bridge exists to embed the tier
in a Python process; this is a separate program talking to a separate process
across a boundary that already has a contract.

Failures are typed and never swallowed. `BackendError` is a sealed
hierarchy — `Unreachable`, `Refused`, `Malformed`, `Uncredentialed` — and the
controller records the message in the state, where the operator can see it,
rather than turning a failure into an empty result that would look like
success.

### 5.1 One trap worth naming

**In a Compose Desktop application, the UI thread *is* the AWT event
dispatch thread.** A click is delivered *on* that thread.

`FileDialogs` opens its dialogs with `EventQueue.invokeAndWait`, because a
Swing component belongs on the event dispatch thread. Calling it from
everywhere would be a trap, so it checks first:

```kotlin
if (EventQueue.isDispatchThread()) {
    return block()
}
```

`EventQueue.invokeAndWait` throws `Error` — not `Exception` — when it is
called from the event dispatcher, and an `Error` on the AWT event queue
thread takes the whole event queue down. That is what happened before the
check was added: clicking **Export .docx** closed the window.
`FileDialogsTest` pins the rule.

---

## 6. What it borrows from the tier

This is the part that makes it a client rather than a fourth copy of the
domain. The client decodes the server's payloads with **the tier's own
decoders**:

| Borrowed | From |
| :--- | :--- |
| `Json.parseObject` / `Json.write` | `:core` — the hand-written codec |
| `sourceChunkFromWire`, `claimFromWire`, `citationLineageFromWire`, `sectionDraftFromWire`, `documentDraftFromWire`, `verificationScorecardFromWire`, `outlineNodeFromWire`, `outlineNodeInputFromWire`, `perspectiveFromWire`, `workflowEventFromWire`, `inlineCitationFromWire` | `:core` — the decoders |
| `Citations.CITATION_PATTERN`, `Citations.chunkIdsOf` | `:core` — the citation grammar |
| `EventType`, `WorkflowStep`, `EntailmentStatus`, `PerspectiveRole`, `OutlineNodeInput` | `:core` — the vocabulary |
| `LMeditorError` | `:core` — the typed errors |

If the client and the backend ever drift about what a payload means, the
client's decoder throws rather than rendering a plausible lie.

Two rules are the client's own, and both are pure functions with tests:

- **`RunReducer.reduce(state, event)`** — the whole event-to-state reduction.
  Pure, total and idempotent: replaying an event produces the same state,
  which is what makes a dropped SSE frame survivable.
- **`ManuscriptBlocks`** — body text to display blocks. It reuses the tier's
  citation pattern and owns nothing else.

---

## 7. Screenshots

`docs/studio-window.png` — a completed run against a live backend: the
manuscript with its citation chips, the grounding scorecard, and the claims.
`docs/studio-discovery.png` and `docs/studio-outline.png` show the other two
tabs.
