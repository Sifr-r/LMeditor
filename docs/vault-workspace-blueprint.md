# Vault workspace blueprint

The active client is Kotlin Compose (`kotlin/desktop`), with immutable controller
state and separately owned transport, model and rendering. Preserve those
boundaries.

## Why this exists

The application is to replace Microsoft Office, Obsidian and Open Notebook. This
wave covers the Obsidian half.

An Obsidian vault is **a folder of markdown files on the operator's disk**, and
the whole value of Obsidian is that the folder stays a folder: it opens in
Obsidian, it opens in a text editor, it syncs, it is greppable, and nothing
about it is locked inside a proprietary database. A replacement that keeps the
notes in a private format is not a replacement — it is a second app with the
same job.

So the vault here is a real folder. A note is a real `.md` file. A wiki-link
`[[Some Note]]` is a real wiki-link, and it resolves against the real file
names. A tag `#idea` is a real tag, and it can be found by grep. The operator's
existing vault must open, and Obsidian must still be able to open what this
application writes.

## The concept

| Concept | What it is |
| --- | --- |
| **Vault** | A directory the operator chose. Every note lives inside it. |
| **Note** | One markdown file. Its id is its path relative to the vault root, without the `.md`, with `/` separators. |
| **Title** | The note's first `# heading`; its file name when it has none. |
| **Tags** | The `#tag` tokens in the body, plus the `tags:` list of the frontmatter. |
| **Links** | The `[[Target]]` and `[[Target\|alias]]` wiki-links in the body. |
| **Backlinks** | The notes whose links point at this one. |

A note id is a **relative path**, never an absolute one. `folder/note` is a
note; `../secrets` is not a note, and neither is `C:/Windows`.

## Where the rules live

The deterministic rules go in the **Kotlin tier**, in a new module `:vault`.
The tier owns every rule that decides anything — the path safety, the
frontmatter, the link and tag grammar, the backlink index, the search order —
and it owns them with an empty runtime dependency set, exactly as
`kotlin/CONTRACTS.md` requires. The Python side keeps a thin door
(`lmeditor/tier/vault.py`) over the NDJSON seam and the HTTP routes; it does
not carry a second copy of any rule.

## Delegated ownership

Paths below are rooted at `kotlin/` for the tier and at `backend/` for the
Python side.

### The tier

| File | Single responsibility |
| --- | --- |
| `vault/build.gradle.kts` | The module's build file: no runtime dependency, the test framework only. |
| `vault/src/main/kotlin/lmeditor/vault/VaultPath.kt` | The path rules: what a note id may be, and how it resolves inside the vault. |
| `vault/src/main/kotlin/lmeditor/vault/Markdown.kt` | The markdown grammar: frontmatter, wiki-links, tags. |
| `vault/src/main/kotlin/lmeditor/vault/VaultNote.kt` | The note and note-summary models. |
| `vault/src/main/kotlin/lmeditor/vault/VaultStore.kt` | The on-disk store: list, read, write, create, delete, rename. |
| `vault/src/main/kotlin/lmeditor/vault/VaultSearch.kt` | The ranked search over the vault. |
| `vault/src/test/kotlin/lmeditor/vault/**` | A test for every rule above. |

### The seam

| File | Single responsibility |
| --- | --- |
| `bridge/src/main/kotlin/lmeditor/bridge/HandlersVault.kt` | The `vault.*` methods of the seam. |
| `bridge/src/main/kotlin/lmeditor/bridge/Handlers.kt` | Register the new handlers. |
| `settings.gradle.kts` | Include `:vault`. |

### The backend

| File | Single responsibility |
| --- | --- |
| `backend/src/lmeditor/tier/vault.py` | The Python door over the seam. |
| `backend/src/lmeditor/vault/__init__.py` | The API the rest of the backend calls. |
| `backend/src/lmeditor/server/vault.py` | The active vault the server holds. |
| `backend/src/lmeditor/server/routes.py` | The `/api/v1/vault` routes. |
| `backend/tests/test_vault_routes.py` | The routes' regressions. |

### The client

| File | Single responsibility |
| --- | --- |
| `desktop/src/main/kotlin/lmeditor/desktop/model/VaultWorkspace.kt` | The vault's state. |
| `desktop/src/main/kotlin/lmeditor/desktop/net/BackendClient.kt` | The vault calls. |
| `desktop/src/main/kotlin/lmeditor/desktop/model/StudioController.kt` | The vault operations. |
| `desktop/src/main/kotlin/lmeditor/desktop/ui/VaultScreen.kt` | The vault workspace. |
| `desktop/src/main/kotlin/lmeditor/desktop/ui/LMeditorScreen.kt` | Add the workspace to the rail. |
| `desktop/src/main/kotlin/lmeditor/desktop/ui/FileDialogs.kt` | Choosing the vault folder. |

## The rules

### Path safety

A note id is refused unless it is a relative, forward-slash-separated path that

- is not blank, and is not `.` or `..`;
- carries no segment that is `.` or `..`;
- carries no absolute prefix, no drive letter and no UNC prefix;
- carries no control character, and no character a Windows path forbids
  (`<>:"|?*`, and a trailing dot or space in a segment);
- stays inside the vault after resolution — the resolved real path must still
  begin with the resolved real root.

Symlinked entries are skipped when listing and refused when writing, so a
vault cannot be walked out of through a link. Files and directories whose name
begins with `.` are skipped, so Obsidian's own `.obsidian` folder is never read
and never written.

### The markdown grammar

- **Frontmatter** is a `---` fenced block at the very start of the file. Its
  `title:` sets the title when the body has no `# heading`; its `tags:` list —
  inline `[a, b]` or one `- a` per line — adds tags.
- **A wiki-link** is `[[Target]]`, `[[Target|alias]]` or `[[Target#Heading]]`.
  The target is resolved against the note titles and against the note paths
  without their extension, case-insensitively. An unresolved target is still
  reported as a link — it is a link to a note that does not exist yet, which
  is what a wiki-link is for.
- **A tag** is `#` followed by a letter, then letters, digits, `_`, `-` or `/`.
  A `#` that starts a markdown heading, that follows a word character, or that
  is part of a URL fragment, is not a tag. Tags are compared case-insensitively.

### Writing

- Writing a note is idempotent: the same content twice leaves the same bytes.
- A write recomputes the note's title, tags and links, and the backlinks of
  every note that links to it.
- Renaming a note moves the file and, when asked, rewrites the `[[Old]]` links
  in the other notes to `[[New]]`.
- A note is never written through a path that leaves the vault.

### Search

Search is over titles, bodies and tags. A title hit outranks a tag hit, which
outranks a body hit. Ties are broken by the note id, so the order is the same
every time. The query is a whitespace-separated set of terms, and a note that
carries every term outranks a note that carries some of them.

## The contract

These shapes are fixed. Every file below is written against them.

### The bridge

The tier's own methods, as the NDJSON seam serves them. Every one of them is
handed the vault's root, because the tier keeps no state of its own: the server
holds the active vault, and the tier is told which folder to work in.

| Method | Parameters | Answers with |
| --- | --- | --- |
| `vault.list_notes` | `root` | `{"notes": [summary…]}` |
| `vault.read_note` | `root`, `note_id` | `{"note": note}` |
| `vault.write_note` | `root`, `note_id`, `content` | `{"note": note}` |
| `vault.create_note` | `root`, `note_id`, `title`, `content` | `{"note": note}` |
| `vault.delete_note` | `root`, `note_id` | `{"deleted": true}` |
| `vault.rename_note` | `root`, `from_id`, `to_id`, `update_links` | `{"from_id": …, "to_id": …, "updated_links": n}` |
| `vault.search` | `root`, `query`, `limit` | `{"results": [hit…]}` |
| `vault.tags` | `root` | `{"tags": [tag…]}` |

### The note

```json
{
  "note_id": "folder/note",
  "title": "Note Title",
  "path": "folder/note.md",
  "content": "# Note Title\n\nthe body",
  "tags": ["idea", "project/x"],
  "links": ["Other Note"],
  "backlinks": ["folder/second"],
  "created_at": "2026-01-01T00:00:00Z",
  "modified_at": "2026-01-02T00:00:00Z",
  "word_count": 12
}
```

### The note summary

```json
{
  "note_id": "folder/note",
  "title": "Note Title",
  "path": "folder/note.md",
  "tags": ["idea"],
  "word_count": 12,
  "modified_at": "2026-01-02T00:00:00Z"
}
```

### A search hit

```json
{"note_id": "folder/note", "title": "Note Title", "path": "folder/note.md",
 "score": 12, "matched_terms": ["idea"]}
```

### A tag

```json
{"tag": "idea", "count": 3}
```

### The HTTP surface

A note id is a path, and a path in a URL is a path in a route, so a note id
never goes in the route. It goes in the query or in the body.

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/vault/open` | Choose the vault folder. Body `{"root": "…"}`. |
| `GET` | `/api/v1/vault` | The active vault: its root and how many notes it holds. |
| `GET` | `/api/v1/vault/notes` | Every note's summary. |
| `GET` | `/api/v1/vault/note?note_id=…` | One note, with its links and its backlinks. |
| `PUT` | `/api/v1/vault/note` | Save the operator's text. Body `{"note_id", "content"}`. |
| `POST` | `/api/v1/vault/note` | Create a note. Body `{"note_id", "title", "content"}`. |
| `DELETE` | `/api/v1/vault/note?note_id=…` | Delete a note. |
| `POST` | `/api/v1/vault/note/rename` | Move a note. Body `{"from_id", "to_id", "update_links"}`. |
| `GET` | `/api/v1/vault/search?q=…&limit=…` | The ranked search. |
| `GET` | `/api/v1/vault/tags` | Every tag and how many notes carry it. |

`GET /api/v1/vault` answers **409** when no vault is open: the client has to
open one before it can ask anything. Every other route answers **409** too when
no vault is open.

### The tier's own API

```kotlin
class VaultStore(root: Path) {
    fun listNotes(): List<VaultNoteSummary>
    fun readNote(noteId: String): VaultNote
    fun writeNote(noteId: String, content: String): VaultNote
    fun createNote(noteId: String, title: String, content: String): VaultNote
    fun deleteNote(noteId: String)
    fun renameNote(fromId: String, toId: String, updateLinks: Boolean): RenameResult
    fun search(query: String, limit: Int): List<VaultSearchHit>
    fun tags(): List<VaultTag>
}
```

A refused note id, a missing note and a vault that is not there are **typed
domain errors**, not exceptions that are swallowed and not empty results that
look like success.

## The workspace

The window gains a **Vault** workspace beside **Write** and **Documents**:

- a folder chooser, so the operator points at the vault they already have;
- the note list, filterable by the search box and by tag;
- the note body, editable, with the tags and the outgoing links of the note
  shown beside it;
- the backlinks of the open note, so the operator can walk the graph without
  leaving the note;
- **Save**, which writes the file.

## Acceptance

- Pointing the app at a folder of markdown files lists those files as notes.
- A note's title, tags, wiki-links and backlinks are read from the file as it
  is, not as the app would like it to be.
- Editing a note and saving changes the file on disk, and nothing else.
- A note id that tries to leave the vault is refused with a clear message.
- A vault that contains `.obsidian/` is read without that folder.
- Search ranks a title hit above a body hit, and the order is stable.
- The tier module has an empty runtime dependency set.
- The whole build passes offline and every existing test still passes.

## Result

**Delivered.** The window opens on the studio, and the operator moves to
**Vault** on the rail above it.

- The tier owns the rules of a folder of markdown files, in a new module
  `:vault` with an **empty runtime dependency set** — the JDK only. It holds
  the path safety, the frontmatter, the link and tag grammar, the backlink
  index and the search order.
- The seam serves eight `vault.*` methods, and the Python side is a door over
  it: it keeps the exact Python API the backend calls and answers with the
  tier's answers.
- The server holds the one open vault and publishes ten routes under
  `/api/v1/vault`. Every route but `open` answers **409** when no vault is
  open, so a client is never told an empty vault when it was told nothing.
- The window reaches all of it: open a folder, list its notes, open one, edit
  its markdown, save it, search, walk the tags, the links and the backlinks.

**Two fidelity fixes were made while proving it.** A wiki-link now resolves by
**file name** first, as Obsidian resolves it, so `[[note]]` names
`folder/note.md` and not only a note whose heading happens to be "note". And a
note that already opens with a heading is no longer given a second one: the
title is only put in front of content that does not name itself.

**Verified.** 914 Kotlin tests and 1341 backend tests pass, with zero
failures and zero skips. The live round trip passes every check against a
running backend: the notes are the files, a title and its tags and its links
and its backlinks are read as the file holds them, a write reaches the file
byte for byte, a rename moves the file *and rewrites the link that pointed at
it*, a delete removes the file, and a note id that tries to leave the vault is
refused with nothing outside touched.

**Still open.** The window does not refresh the vault when none is open, so a
tab visit while no vault is chosen stays quiet rather than reporting a
refusal on every visit.
