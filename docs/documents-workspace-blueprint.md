# Documents workspace blueprint

The active client is Kotlin Compose (`kotlin/desktop`), with immutable controller
state and separately owned transport, model and rendering. Preserve those
boundaries.

## Why this exists

The application is to replace Microsoft Office, Obsidian and Open Notebook for
the operator. The backend already carries the whole Word half of that job: it
reads a `.docx` into a book of editable sections, keeps the operator's own text
per section, and renders the book back out as a `.docx` with the original
tables, images, notes, styles and tracked changes intact. The operator could
not reach any of it, because the desktop client only exposed the AI writing
studio and could export a document but never open one.

This wave closes that gap: the window gains a **Documents** workspace where a
`.docx` can be opened, read, edited, saved and written back to disk.

Design: a restrained editorial word processor, consistent with the studio's
existing chrome — deep green accents, warm neutral surfaces, serif manuscript
typography, a bounded paper canvas. The workspace is a real editor: the section
body is editable text, `Save` persists it, `Save as .docx` writes a real Word
package to a path the operator chooses. Nothing in the workspace may imply a
capability it does not have.

## The shared contract

These declarations are fixed. Every file below is written against them, and a
change to one is a change to all of them.

### `model/DocumentWorkspace.kt` — owned by the orchestrator, already written

```kotlin
data class DocumentWorkspace(
    val books: List<BookManifest> = emptyList(),
    val openBook: BookManifest? = null,
    val openSectionId: String? = null,
    val sectionTitle: String = "",
    val sectionDraft: String = "",
    val savedContent: String = "",
    val sectionDirty: Boolean = false,
    val saving: Boolean = false,
) {
    val hasUnsavedChanges: Boolean get() = openSectionId != null && sectionDirty
}

data class BookSectionText(
    val bookId: String,
    val sectionId: String,
    val title: String = "",
    val order: Int = 0,
    val content: String = "",
)
```

### `net/BackendClient.kt` — additions

```kotlin
suspend fun listBooks(): List<BookManifest>
suspend fun importDocx(fileName: String, content: ByteArray, title: String = ""): BookManifest
suspend fun book(bookId: String): BookManifest
suspend fun bookSection(bookId: String, sectionId: String): BookSectionText
suspend fun saveBookSection(bookId: String, sectionId: String, content: String): BookSectionSaveResult
suspend fun exportBookDocx(bookId: String): ByteArray
```

The server's routes are the contract, and they are already published:

| Call | Route |
| --- | --- |
| `listBooks` | `GET /api/v1/library/books` → `{"books": [manifest…], "count": n}` |
| `importDocx` | `POST /api/v1/library/books/import-docx` (multipart: file part named `file`, field `title`) → manifest |
| `book` | `GET /api/v1/library/books/{book_id}` → manifest |
| `bookSection` | `GET /api/v1/library/books/{book_id}/sections/{section_id}` → `{book_id, section_id, title, order, content}` |
| `saveBookSection` | `PUT /api/v1/library/books/{book_id}/sections/{section_id}` body `{"content": "…"}` → save result |
| `exportBookDocx` | `GET /api/v1/library/books/{book_id}/export.docx` → the package's bytes |

### `model/StudioState.kt` — addition

```kotlin
val documents: DocumentWorkspace = DocumentWorkspace()
```

### `model/StudioController.kt` — additions

```kotlin
fun refreshBooks()
fun openDocx(file: File, title: String = "")
fun openBook(bookId: String)
fun openSection(sectionId: String)
fun setSectionDraft(text: String)
fun saveSection()
fun exportBookDocxTo(target: File)
fun closeBook()
```

### `ui/LMeditorScreen.kt` — the window shell

```kotlin
enum class Workspace { WRITE, DOCUMENTS }

@Composable
fun LMeditorScreen(controller: StudioController, modifier: Modifier = Modifier)
```

## Delegated ownership

Paths below are rooted at `kotlin/desktop/src/main/kotlin/lmeditor/desktop/`;
test paths are rooted at `kotlin/desktop/src/test/kotlin/lmeditor/desktop/`.

### Worker A — the transport

| File | Single responsibility |
| --- | --- |
| `net/BackendClient.kt` | The six library calls above, decoded with the tier's own decoders. |
| `net/BackendClientTest.kt` | Transport regressions for the library routes. |

### Worker B — the state and the controller

| File | Single responsibility |
| --- | --- |
| `model/StudioState.kt` | Hold the documents workspace in the studio state. |
| `model/StudioController.kt` | The document operations above, and their failures. |
| `model/StudioControllerTest.kt` | Controller regressions for the document operations. |
| `model/DocumentWorkspaceTest.kt` | The workspace value's own rules. |

### Worker C — the window

| File | Single responsibility |
| --- | --- |
| `ui/LMeditorScreen.kt` | The workspace rail and the switch between workspaces. |
| `ui/DocumentsScreen.kt` | The documents workspace: library, section list, editor, actions. |
| `Main.kt` | Open the shell rather than the studio directly. |
| `ui/FileDialogs.kt` | Only if a new dialog is genuinely needed. |

### Orchestrator — the ledger

| File | Single responsibility |
| --- | --- |
| `docs/documents-workspace-blueprint.md` | This scope, ownership and acceptance record. |
| `ARCHITECTURE.md` | The new file responsibility ledger and the verification record. |

## Rules the implementation must hold to

- **The tier's decoders are used, not new ones.** `BookManifest` and
  `BookSection` come from `lmeditor.core`, with `bookManifestFromWire` and
  `bookSectionFromWire`. A payload the server produced is read by the rules
  that wrote it.
- **No new dependency.** `:desktop` already carries Compose; nothing else may
  be added, and no tier module may gain one.
- **Failures are visible.** A refusal is recorded in the state where the
  operator sees it, and is never turned into an empty result that would look
  like success.
- **The buffer is not the server.** An unsaved edit is the operator's own text;
  `sectionDirty` says so, and `Save` is the only thing that changes the
  server's copy. The dirty mark is the difference between `sectionDraft` and
  `savedContent`, so an edit that is typed and then undone is not dirty.
- **Switching section or book does not lose the operator's text.** Opening
  another section, or another book, saves the dirty section first. If that save
  fails, the switch does not happen and the failure is reported: a word
  processor that drops an unsaved paragraph to show another one is worse than
  one that refuses to move.
- **Every path segment is encoded.** Book and section identifiers go through
  `pathSegment`, exactly as document identifiers do.
- **No swallow.** `catch (e: Exception) { }` is forbidden, and the typed errors
  from `lmeditor.core` and `BackendError` are used as they are.

## Acceptance

- The window opens on the studio, and the operator can switch to **Documents**.
- **Open .docx** uploads a real Word file and lists the book's sections.
- Choosing a section shows its text and makes it editable.
- **Save** persists the operator's text and the dirty mark clears.
- **Save as .docx** writes a real Word package to a chosen path.
- The server's books are listed, and an already-imported book can be reopened.
- Failures (an unreadable file, a refused request, an unwritable path) appear
  in the status strip; they do not crash the window.
- `:desktop:build` succeeds offline; every existing test still passes.
- A live round trip against a running backend proves a `.docx` in, an edit, and a
  `.docx` out.

## Result

**Delivered.** The window opens on the studio, and the operator moves to
**Documents** on the rail above it.

- The transport carries the six library calls, decoded with the tier's own
  decoders (`bookManifestFromWire`, `bookSectionFromWire`, and the new
  `bookSectionSaveResultFromWire`). Every book and section identifier goes
  through the one-path-segment encoder, and a regression proves they cannot
  alter the request path.
- The controller carries the eight document operations, and the workspace
  carries the books, the open book, the open section, the buffer, the server's
  copy of it and the dirty mark.
- The workspace renders the server's books, the sections of each, and the open
  section's text on the studio's own paper canvas, with **Open .docx**, **Save**,
  **Save as .docx** and **Close**.
- The backend address is one control now, shared by both workspaces, because it
  belongs to the window and not to a workspace.
- Switching section, book or document saves the dirty section first. A save
  that fails refuses the switch: the operator's text is never dropped to show
  something else.

**A defect was found and fixed while proving it.** Writing the live round trip
down showed that an edited section lost its table and its image: the exporter
re-rendered the section from its text alone, and a table renders as
pipe-separated text while an image renders as nothing. The exporter now rebuilds
an edited section instead of discarding it — the heading keeps its style, the
blocks that render to no text are kept, an unchanged table is restored, a
changed one is rebuilt as a table, and the prose is substituted in place.

**Verified.** 839 Kotlin tests and 1314 backend tests pass, with zero failures
and zero skips. The live round trip passes every check against a running
backend, editing the section that holds the table and the image and finding
both of them still in the exported package.

**Still open.** An image that sits inside a paragraph that also carries text is
lost with that paragraph; a section-level text editor cannot see it.
