# LMeditor feature inventory: DOCX, vault and notebooks

Date: 26 October 2026
Status: evidence inventory built from the working tree at `D:\LMeditor`. It records
what the tree does **today**. It is not a completion claim for any roadmap item.

Every status in this document is one of the eight values of the shared vocabulary in
`docs/replacement/m0-blueprint.md` §6: `read`, `display`, `edit`, `preserve`, `export`,
`limited`, `unsupported`, `unverified`.

---

## 1. How to read this inventory

### 1.1 The six columns

| Column | Question the column answers | Blueprint clause |
| :--- | :--- | :--- |
| **Read** | Does the reader put this content into the model? | `read` — "The format's content is read into the model." |
| **Display** | Is this content shown to the operator, in a named screen or Compose function? | `display` — "The content is shown to the operator." |
| **Edit** | Can the operator change it, and does the change persist? | `edit` — "The operator can change it, and the change is persisted." |
| **Preserve** | Does it survive a read/write cycle unchanged in meaning? | `preserve` — "The content survives a read/write cycle unchanged in meaning." |
| **Export** | Is it written into an output format? | `export` — "The content is written into an output format." |
| **Known limitation** | The concrete restriction, with its evidence. | `limited` requires "The restriction is stated." |

A cell carrying the column's own name (`read`, `display`, …) means the full capability.
A cell carrying `limited` means the capability exists with the restriction stated in
the last column. A cell carrying `unsupported` means the capability does not exist;
the last column then names the module and the search that established it. A cell
carrying `unverified` means the status could not be established from the tree, and
the last column names the blocker.

### 1.2 The four distinctions this document exists to keep apart

- **"Read" is not "edit".** The DOCX reader accepts a construct into the model
  (`WordModel.kt`); nothing in the desktop tree lets the operator change that
  construct. A feature can be `read` and `unsupported` in the *edit* column at the
  same time, and most DOCX features below are exactly that.
- **"Preserved" is not "editable".** "Preserved" means the construct survives a
  read/write cycle. It says nothing about whether the operator can reach it.
  Blueprint §5 rule 6: "Preserved is not verified."
- **"Preserved" is not "renders identically in Word".** No row in this document claims
  that Word renders anything identically. The only Word-rendered evidence in the tree
  is the real-Word conformance harness, and this document does not assert its scope.
- **A parse is not a workflow.** Blueprint §5 rule 5. A feature that is read but has
  no path from the operator's hands to a persisted result is reported as such.

### 1.3 What "not present" means here

Per blueprint §5 rule 3, absence is reported as **"not present in \<module\>, searched
\<pattern\>"** and never as "not supported". Each such cell names the module and the
pattern so the claim can be re-checked.

### 1.4 A skipped test is not a pass

Blueprint §5 rule 2. See §5.5 for the measured suite state, which differs from the
roadmap's baseline.

---

## 2. DOCX feature matrix

**Model:** `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt` (mirrored in Python
at `backend/src/lmeditor/word/model.py`).
**Reader:** `kotlin/word/.../WordReader.kt`; **Writer:** `kotlin/word/.../WordWriter.kt`.
**Import/export route:** `backend/src/lmeditor/server/routes.py:1411` (import),
`:694` (book export), `:626` (manuscript export).

The DOCX reader and writer are a real, tested OOXML subsystem. The **desktop surface
is not**: `DocumentsScreen` opens a `.docx`, splits it into sections, and edits each
section as one plain string. The rich model is real in `kotlin/word` and the book
library; it is absent from the operator's hands.

| Feature | Read | Display | Edit | Preserve | Export | Known limitation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Paragraphs and runs | `read` | `unsupported` | `unsupported` | `read` | `read` | The desktop edits a section as one string (`model/DocumentWorkspace.kt:37`, `:46`, `:83`; `ui/DocumentsScreen.kt:498-523`); the block/run model is never rendered there. Only a section whose text is unchanged keeps its imported blocks (`kotlin/library/.../BookExporter.kt:156-160`). |
| Bold | `read` | `unsupported` | `unsupported` | `read` | `read` | Formattable in the model (`WordModel.kt:96`) and on export (`WordWriter.kt:552`) but never on screen; the only bold rendering in the desktop is the studio's markdown parser (`ui/EditorPane.kt:297`), which is not a DOCX surface. |
| Italic | `read` | `unsupported` | `unsupported` | `read` | `read` | As bold: `WordModel.kt:97`, `WordWriter.kt:553`. |
| Underline | `read` | `unsupported` | `unsupported` | `read` | `read` | As bold: `WordModel.kt:98`, `WordWriter.kt:558`. Only the `single` underline style is emitted. |
| Font family | `read` | `unsupported` | `unsupported` | `limited` | `limited` | Only `w:ascii`/`w:hAnsi` are written (`WordWriter.kt:551`); east-asian and complex-script faces are not carried (`WordModel.kt:102` holds one name). |
| Font size | `read` | `unsupported` | `unsupported` | `read` | `read` | Half-points only (`WordModel.kt:104`); a run's complex-script size `w:szCs` is not written (`WordWriter.kt:556` writes `w:sz` alone). |
| Colour | `read` | `unsupported` | `unsupported` | `read` | `read` | A literal six-digit RRGGBB hex is carried (`WordModel.kt:106`); a theme-colour reference `w:themeColor` is not resolved (searched `themeColor` in `kotlin/word`, no match). |
| Highlight, strike, super- and subscript | `read` | `unsupported` | `unsupported` | `read` | `read` | none known — all five are read (`WordReader.kt:405`, `:416`, `:385`) and re-emitted (`WordWriter.kt:554`, `:557`, `:559-560`). |
| Paragraph alignment | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `w:jc`, `"jc"`, `alignment` in `WordModel.kt` and `WordReader.kt` — no field and no read; the writer emits no `w:jc`. |
| Paragraph spacing | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `"spacing"` in `WordReader.kt` — no read; the only `w:spacing` the writer emits is the styles default (`WordWriter.kt:882`) and the heading style (`:894`), not a document paragraph's. |
| Paragraph indentation | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `"ind"` in `WordReader.kt` — no read; the writer writes `w:ind` only for list items (`WordWriter.kt:239`, `:1984`). |
| Tab stops | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `"tabs"`, `tabStop`, `w:tabs` in `WordReader.kt` and `WordWriter.kt` — no match. A literal tab is normalised to whitespace (`backend/tests/test_word_contract_gaps.py:316`). |
| Headings (levels 1–9) and Title | `read` | `unsupported` | `unsupported` | `read` | `read` | The level is recovered from the style name, not from an outline level (`WordReader.kt:1061-1068`; `backend/tests/test_word_contract_gaps.py:267`, `:276`). A heading styled under a custom name is read as a paragraph and reported (`backend/tests/test_word_gaps.py:368`). |
| Named and custom styles | `read` | `unsupported` | `unsupported` | `read` | `read` | Only `styleId`, `name`, `basedOn`, `styleType`, `numId`, `numLevel` survive (`WordModel.kt:266-278`); the rest of the style's definition is not modelled. A style the writer cannot represent is recorded in the omission register rather than written (`WordWriter.kt:861`). |
| Numbered lists | `read` | `unsupported` | `unsupported` | `read` | `read` | The writer **rebuilds** numbering from the model's groups (`WordWriter.kt:1868-1869`, `:1954`), so the source's own `w:num`/`w:abstractNum` definitions are not carried verbatim; the level format and text are (`WordModel.kt:527-529`). |
| Bulleted lists | `read` | `unsupported` | `unsupported` | `read` | `read` | Picture bullets are not carried: not present in `kotlin/word`, searched `numPicBullet`, `lvlPicBulletId` — no match. |
| Multi-level numbering | `read` | `unsupported` | `unsupported` | `read` | `read` | Nesting is bounded at 8 by the model validator (`WordModel.kt:568-570`) and the writer defines 9 levels (0–8, `WordWriter.kt:2013`); a deeper list is refused rather than preserved. |
| Numbering restarts | `read` | `unsupported` | `unsupported` | `read` | `read` | A restart is expressed as the model's continuity group (`WordModel.kt:525`), and the writer emits `w:startOverride` for the group (`WordWriter.kt:1952`); the reader also honours a source `w:lvlOverride` (`WordReader.kt:451`). A restart the source expresses outside those two mechanisms is not modelled. |
| Tables — grid, rows, cells | `read` | `unsupported` | `unsupported` | `read` | `read` | Cell content is carried (`WordModel.kt:429-445`), but the desktop shows a table as the pipe-separated text the model's plain-text projection makes (`WordModel.kt:618-634`). |
| Tables — merged cells | `read` | `unsupported` | `unsupported` | `read` | `read` | A merge survives as a merge in both directions (`kotlin/word/.../WordTableLayout.kt:70`; `backend/tests/test_word_merges.py`), but the writer always emits fixed table layout (`WordWriter.kt:756`) and single borders (`:748-755`), so a source table's layout hints are replaced. |
| Tables — repeating header rows | `read` | `unsupported` | `unsupported` | `read` | `read` | The model carries a header-row count (`WordModel.kt:493-494`) and the writer emits `w:tblHeader` (`WordWriter.kt:790`); the source's own `w:tblHeader` flags are re-derived from the count, not preserved as written. |
| Tables — column widths | `read` | `unsupported` | `unsupported` | `limited` | `limited` | Widths are carried per grid column (`WordModel.kt:496`) and read (`WordReader.kt`), but when the declared count does not match the grid the writer **replaces** them with an even split and records an omission (`WordWriter.kt:775-781`). |
| Tables — nested tables | `read` | `unsupported` | `unsupported` | `read` | `read` | A nested table stays a table (`WordModel.kt:445`; `backend/tests/test_word_gaps.py:530`, `:558`). |
| Images and inline drawings | `read` | `unsupported` | `unsupported` | `read` | `read` | The media bytes are carried (`WordModel.kt:291-301`) and re-emitted (`WordWriter.kt:428`); an image the package does not carry is dropped and reported (`WordWriter.kt:426`). There is no image insertion or sizing surface. |
| Hyperlinks | `read` | `unsupported` | `unsupported` | `read` | `read` | A hyperlink target is read from the run (`WordReader.kt:1477`) and written as an **external** relationship (`WordWriter.kt:444-450`); an internal `#anchor` is not resolved to a bookmark, because bookmarks are not modelled (see below). |
| External relationships and carried parts | `read` | `unsupported` | `unsupported` | `read` | `read` | A part the model cannot represent travels with its bytes and its relationship (`WordModel.kt:377`; `WordReader.kt:52-55`), but a part not reached by any relationship cannot travel (`WordWriter.kt:1300-1306`). |
| Bookmarks | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `bookmarkStart`, `bookmarkEnd` in `WordModel.kt` and `WordReader.kt` — the reader **skips** them (`WordReader.kt:2340-2341`, `:2358-2359`) and the model has no bookmark field. |
| Footnotes | `read` | `unsupported` | `unsupported` | `read` | `read` | Footnote text and runs are carried (`WordModel.kt:179-192`) and written as a real `word/footnotes.xml` part (`WordWriter.kt:189`); the footnote body is plain text, so its own formatting is limited to what the run list holds. |
| Endnotes | `read` | `unsupported` | `unsupported` | `read` | `read` | As footnotes (`WordModel.kt:195-207`; `WordWriter.kt:190`). A run's `w:endnoteRef` is skipped inline (`WordReader.kt:2352`). |
| Fields (TOC, page numbers, cross-references) | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `fldChar`, `instrText`, `fldSimple` in `WordModel.kt` — the reader discards field instructions and field characters (`WordReader.kt:1454`, `:2339`, `:2347-2349`) and the writer emits neither. A TOC is therefore not authored, read or preserved. |
| Equations (OMML) | `read` | `unsupported` | `unsupported` | `read` | `read` | The OMML markup is preserved **verbatim** as a string (`WordModel.kt:121-122`) and re-emitted (`WordWriter.kt:462-468`); it is never parsed into an editable equation, and markup that will not parse is dropped and reported (`WordWriter.kt:465`). |
| Comments | `read` | `unsupported` | `unsupported` | `read` | `read` | A comment's text, author, initials, date and anchor are carried (`WordModel.kt:156-170`) and written as a real `word/comments.xml` part with range markers (`WordWriter.kt:189`, `:332`); there is no compose, reply or resolve surface, and the comment's own runs are carried but not editable. |
| Tracked changes | `read` | `unsupported` | `unsupported` | `read` | `read` | Insertions, deletions, moves and format changes are represented explicitly (`WordModel.kt:55-60`, `:86-91`); a deleted run stays in the model but not in the plain text (`WordModel.kt:144-152`). A run's prior formatting is kept as raw `w:rPrChange` markup (`WordModel.kt:132-136`). Accepting or rejecting a change is not available. |
| Content controls (`w:sdt`) | `limited` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | The reader walks a content control **transparently** and reads its content (`WordReader.kt:47`, `:1017-1021`), so the text inside survives; the control's own properties (tag, alias, lock, data binding) are not modelled and the writer emits no `w:sdt`, so the control does not come back. |
| Headers and footers | `read` | `unsupported` | `unsupported` | `read` | `read` | Header and footer **text** is carried as plain strings (`WordModel.kt:683-685`) and matched to its reference type rather than to part order (`WordWriter.kt:1035-1040`; `backend/tests/test_word_gaps.py:433-452`). A header is not rich: it cannot hold a page-number field, because fields are not modelled. |
| Sections and page setup | `read` | `unsupported` | `unsupported` | `limited` | `limited` | Page size, orientation, margins and the header/footer references are carried (`WordModel.kt:233-250`), but **only the first section's page setup is written**; a document with more than one section is reported (`WordWriter.kt:952-957`). No section break can be authored. |
| Page breaks | `read` | `unsupported` | `unsupported` | `read` | `read` | A page break is a first-class block kind (`WordModel.kt:15`) and is written as `w:br w:type="page"` (`WordWriter.kt:214`); it is a break, not pagination — nothing in the tree computes page numbers. |
| Text boxes | `limited` | `unsupported` | `unsupported` | `limited` | `limited` | The reader lifts a text box's content into **blocks of its own** (`WordReader.kt:48`, `:1256`), so the text survives but loses its frame, anchor and wrap. The writer emits those blocks as ordinary body paragraphs, so the text box itself does not come back. |
| Charts and embedded objects | `limited` | `unsupported` | `unsupported` | `read` | `read` | A chart or embedded object is carried as an opaque package part with its anchor (`WordModel.kt:123-131`; `WordReader.kt:52-55`), and the anchor's size is preserved in EMUs (`WordModel.kt:126-131`). Its content is never modelled, so it cannot be edited or inspected; the real-Word conformance test for the chart's size and parts exists (`backend/tests/test_word_chart_conformance.py`). |
| `numbering.xml` | `read` | `unsupported` | `unsupported` | `limited` | `read` | The source's numbering definitions are parsed into the model (`WordReader.kt:435-452`) rather than carried as an opaque part (`WordReader.kt:2324`), and the writer **regenerates** the part from the model's groups and level signatures (`WordWriter.kt:186`, `:1966-1994`); the output is a valid numbering part, not the source's. |
| `styles.xml` | `read` | `unsupported` | `unsupported` | `limited` | `read` | The source's styles are read into a five-field model (`WordModel.kt:266-278`) and the writer writes a styles part built from that model plus its own defaults (`WordWriter.kt:185`, `:878-899`); the source's full style definitions are not reproduced. |
| Theme, `fontTable.xml`, `settings.xml` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/word`, searched `theme1`, `fontTable`, `webSettings` — the reader lists all three in `IGNORED_PARTS`, i.e. they are discarded, not carried (`WordReader.kt:2319-2333`). The writer emits its own `word/settings.xml` (`WordWriter.kt:187`) but no theme or font table. |
| Document properties (`docProps`) | `read` | `unsupported` | `unsupported` | `read` | `read` | Core properties — title, author, subject, keywords, comments, created, modified, revision — are carried (`WordModel.kt:638-655`) and written (`WordWriter.kt:170`). `docProps/app.xml` is discarded (`WordReader.kt:2332`). |
| Legacy `.doc` (OLE2) | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | Deliberately refused with an actionable reason rather than mis-parsed (`kotlin/word/.../WordFormats.kt:30`, `:42`; `backend/tests/test_word_gaps.py:495-502`). No conversion path exists. |

### 2.1 DOCX matrix counts

44 features inventoried. Per column, the count of each status:

| Column | feature-level support (`read`/`display`/`edit`/`preserve`/`export`) | `limited` | `unsupported` | `unverified` |
| :--- | ---: | ---: | ---: | ---: |
| Read | 33 | 3 | 8 | 0 |
| Display | 0 | 0 | 44 | 0 |
| Edit | 0 | 0 | 44 | 0 |
| Preserve | 29 | 6 | 9 | 0 |
| Export | 31 | 4 | 9 | 0 |

Every "full support" cell above is a statement about the **model and the file**, not
about the operator's screen. Display and Edit are `unsupported` for all 44 DOCX
features, because no Compose surface in `kotlin/desktop` reads or writes the Word
model. This is the gap roadmap §1 names: "Existing rich OOXML serialization does not
establish rich editing."

---

## 3. Vault feature matrix

**Tier:** `kotlin/vault/src/main/kotlin/lmeditor/vault/{VaultStore,Markdown,VaultSearch,VaultNote,VaultPath}.kt`
**Python facade:** `backend/src/lmeditor/vault/__init__.py`
**Routes:** `backend/src/lmeditor/server/routes.py:1777-1930`
**Surface:** `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/VaultScreen.kt`

The vault is a real folder of Markdown files with a tested CRUD, link, tag and search
layer. Everything the roadmap calls "visual knowledge tools" and "deeper Markdown
compatibility" is absent from the tree.

| Feature | Read | Display | Edit | Preserve | Export | Known limitation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Markdown source mode | `read` | `read` | `read` | `read` | `read` | The only mode that exists. The editor is one plain `OutlinedTextField` holding raw Markdown (`ui/VaultScreen.kt:749-774`), and Save writes that text to the file (`kotlin/vault/.../VaultStore.kt:102-107`). |
| Markdown reading mode | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `ui/VaultScreen.kt`, searched `reading mode\|previewMode\|renderMode` — no match. The note is shown as its source text. |
| Markdown live preview | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `ui/VaultScreen.kt`, searched `live preview\|preview` — no match. The desktop's only Markdown renderer is the studio's manuscript parser (`ui/EditorPane.kt:195`), which is not a vault surface. |
| Frontmatter | `read` | `read` | `read` | `read` | `read` | Only `title` and `tags` are parsed (`kotlin/vault/.../Markdown.kt:44-93`; `VaultNote.kt:9-13`). The file's own bytes are written back unchanged (`VaultStore.kt:102-107`), so unfamiliar YAML survives **as text** but is not typed, not filterable and not editable as a property. |
| Typed frontmatter properties | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `property\|typed` in `Markdown.kt` and `VaultNote.kt` — only `title` and `tags` exist. |
| Wiki links | `read` | `read` | `limited` | `read` | `read` | Links are parsed (`Markdown.kt:13`, `:182`) and shown in the note graph (`ui/VaultScreen.kt:802-848`). Editing is limited to the whole file's text: a link is not individually addressable. Unresolved links are shown as text rather than opened (`ui/VaultScreen.kt:799-800`). |
| Link aliases | `read` | `read` | `limited` | `read` | `read` | `[[Target\|alias]]` is parsed (`Markdown.kt:13`) and the alias is preserved when a link is rewritten (`Markdown.kt:266`); there is no alias property in frontmatter and no alias list. |
| Heading references (`[[Note#Heading]]`) | `read` | `limited` | `limited` | `read` | `read` | The heading part of a link target is parsed (`Markdown.kt:13`), but the link resolves to the note, not to the heading: `ui/VaultScreen.kt:988` resolves a link to a `VaultNoteSummary`, and there is no heading-level navigation or anchor. |
| Block references (`[[Note#^id]]`) | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `blockId\|blockRef\|\^` in `Markdown.kt` — no match. |
| Transclusion (`![[...]]`) | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `embed\|transclu\|!\[\[` in `Markdown.kt` — no match. |
| Attachments and pasted images | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `attachment\|media\|image` in `Markdown.kt` and `VaultStore.kt` — no match. `VaultStore.kt:353` treats only `.md` files as notes, so other files are invisible to the vault. |
| Tags | `read` | `read` | `limited` | `read` | `read` | Tags come from the frontmatter and from the body (`Markdown.kt:194`, `:215`), are counted (`ui/VaultScreen.kt:635-670`) and can be filtered (`ui/VaultScreen.kt:635`). A tag is edited by editing the file's text. |
| Search | `read` | `read` | `unsupported` | `read` | `read` | A whole-vault substring scan over title, tags and body, ranked by weighted term count (`kotlin/vault/.../VaultSearch.kt:69-85`, `:94-114`); every query reloads the whole vault (`VaultStore.kt:286`). There is no index, no snippet and no query syntax beyond whitespace-separated terms. |
| Rename / move | `read` | `read` | `read` | `read` | `read` | The file is moved and the folder it sat in is left alone (`VaultStore.kt:153-167`, `:186`); the operation is not atomic with the link rewrite, so an interruption between them leaves stale links (roadmap F.03, O1.07). |
| Backlink rewriting | `read` | `read` | `read` | `read` | `read` | Rewriting keeps each link's alias and heading (`Markdown.kt:266`, `:275`) and the count of rewritten links is reported (`VaultStore.kt:174-186`). Not recoverable if it fails midway. |
| Trash and restore | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `backend/src/lmeditor/vault/__init__.py`, searched `trash\|restore\|recycle\|undelete` — no match. `deleteNote` deletes the file outright (`VaultStore.kt:153-167`; `backend/tests/test_vault_routes.py:236`). |
| Templates | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `template` — no match. A new note takes only a title, which becomes its first heading (`VaultStore.kt:127-140`). |
| Daily notes / quick capture | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `daily\|quickCapture` — no match. |
| Canvas | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `ui/VaultScreen.kt`, searched `canvas` — the only match is a comment about the editor frame (`ui/VaultScreen.kt:763`). No `.canvas` file is read or written. |
| Graph view | `limited` | `limited` | `unsupported` | `read` | `read` | A **local** graph only: the open note's tags, outgoing links and backlinks (`ui/VaultScreen.kt:794-848`). There is no local/global graph view, no filters, no grouping and no large-vault limit handling. |
| Table views | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `ui/VaultScreen.kt`, searched `tableView\|dataview\|propertyTable` — no match. |
| Card views | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault` and `ui/VaultScreen.kt`, searched `cardView\|board\|kanban` — no match. |
| Sync / multi-device | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `sync\|conflict\|merge` — no match. |
| External change watching | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/vault`, searched `WatchService\|watch\|FileSystemWatcher` — no match. A note is re-read from disk when it is opened; a concurrent external edit is not detected. |
| Note CRUD | `read` | `read` | `read` | `read` | `read` | Create, read, write, rename and delete are implemented and route-tested (`backend/tests/test_vault_routes.py:160-248`). Writes are atomic-by-replacement but not flushed to disk (roadmap F.03). |

### 3.1 Vault matrix counts

25 features inventoried.

| Column | feature-level support | `limited` | `unsupported` | `unverified` |
| :--- | ---: | ---: | ---: | ---: |
| Read | 10 | 1 | 14 | 0 |
| Display | 9 | 2 | 14 | 0 |
| Edit | 5 | 4 | 16 | 0 |
| Preserve | 11 | 0 | 14 | 0 |
| Export | 11 | 0 | 14 | 0 |

"Preserve" for a vault row means the file's own bytes survive a read/write cycle —
`writeNote` writes the operator's text verbatim (`VaultStore.kt:102-107`), so
unfamiliar Markdown and unfamiliar YAML are preserved as bytes but not understood.

---

## 4. Notebook feature matrix

**Tier:** `kotlin/notebooks/src/main/kotlin/lmeditor/notebooks/{NotebookStore,NotebookPath,NotebookErrors}.kt`
**Model:** `kotlin/core/src/main/kotlin/lmeditor/core/NotebookSchemas.kt`
**Python:** `backend/src/lmeditor/notebooks/{__init__.py,answer.py}`
**Routes:** `backend/src/lmeditor/server/routes.py:2015-2260`
**Surface:** `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/NotebookScreen.kt`

| Feature | Read | Display | Edit | Preserve | Export | Known limitation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Manifest | `read` | `read` | `read` | `read` | `read` | The manifest is a per-notebook directory file, written atomically (`NotebookStore.kt:77-83`, `:470-496`). It is atomic against process death only — it is never flushed to disk, so a power loss can lose the last write (roadmap F.03). |
| Membership — sources | `read` | `read` | `read` | `read` | `read` | Sources are added and removed idempotently (`NotebookStore.kt:186`, `:211`; `backend/tests/test_notebook_routes.py:209`). A notebook **points at** a source; it does not own it (`NotebookSchemas.kt:21-23`). |
| Membership — vault notes | `read` | `read` | `read` | `read` | `read` | Notes can be attached and detached (`NotebookStore.kt:248`, `:283`; `backend/tests/test_notebook_routes.py:227`), but see §5.1: the Ask path never reads them. The membership is a list of ids (`NotebookSchemas.kt:40`), with no content revision bound to them (roadmap N1.04). |
| Source ingestion | `read` | `read` | `read` | `limited` | `unsupported` | Ingestion works through the studio's source pipeline (`ui/SourcesPane.kt:80-111`). The notebook holds only ids, so the material a notebook names lives in the server's source library, not in the notebook. |
| Source retention | `limited` | `limited` | `unsupported` | `limited` | `unsupported` | The first M1 slice persists extracted source records/chunks and metadata (`kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28`). Cache eviction retains archived evidence. Original uploads and source revision history are not archived yet; F.02 remains partial. |
| Ask | `read` | `read` | `read` | `limited` | `unsupported` | The question is asked through one route (`backend/src/lmeditor/server/routes.py:2222`; `backend/src/lmeditor/notebooks/answer.py:59`). The answer is **not persisted**: `NotebookAnswer` is returned, not stored, so a past answer is gone after a restart (roadmap N1.05). |
| Answer | `read` | `read` | `unsupported` | `limited` | `unsupported` | The answer text is generated from the retrieved passages (`answer.py:96-104`) and shown under its own label (`ui/NotebookScreen.kt:770-830`). The operator cannot edit it, and there is no regeneration control. |
| Citations | `read` | `read` | `unsupported` | `limited` | `unsupported` | A citation names the chunk, its source, its title and the passage's own words (`answer.py:180-191`; `ui/NotebookScreen.kt:853-890`), and clicking one selects the chunk (`ui/NotebookScreen.kt:862`). A citation is supported when the tier can find an exact quote for the claim that carries it (`answer.py:148-177`). |
| Conversations | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/notebooks` and `backend/src/lmeditor/notebooks`, searched `conversation\|thread\|history\|turn` — no match. `NotebookSchemas.kt:26-47` has no conversation field, and there is no conversation store on disk. |
| Source reader | `limited` | `limited` | `unsupported` | `unsupported` | `unsupported` | A source's passages can be listed and selected (`ui/SourcesPane.kt:138-162`; `ui/NotebookScreen.kt:853-890`), but there is no navigation to an exact page, section, character span or timestamp, and no highlighted evidence in context (roadmap N1.02). |
| Per-question scope selection | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `backend/src/lmeditor/notebooks` and `backend/src/lmeditor/server/routes.py`, searched `scope\|selected_source_ids\|exclude` — no match. The scope is the notebook's whole source set for every question (`answer.py:86`). |
| Claim-level evaluation | `limited` | `unsupported` | `unsupported` | `limited` | `unsupported` | Claims are extracted and a claim's support is decided **per citation** (`answer.py:112-117`, `:148-177`), but the answer itself carries no per-claim verdict. `answered=true` means only that one citation survived (`answer.py:121`), so a partially supported answer reads as an answer (`ui/NotebookScreen.kt:811`; roadmap N1.07). |
| Artifacts | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/notebooks` and `backend/src/lmeditor/notebooks`, searched `artifact` — no match. `NotebookSchemas.kt:26-47` has no artifact field. |
| Sharing | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in `kotlin/notebooks` and `backend/src/lmeditor/server/routes.py`, searched `share\|permission\|role\|revoke` — no match. The notebooks folder is a local folder. |
| Studio outputs | `unsupported` | `unsupported` | `unsupported` | `unsupported` | `unsupported` | not present in the tree, searched `mind_map\|flashcard\|quiz\|study_guide\|audio_overview\|podcast\|infographic\|slide_deck` across `D:\LMeditor` — the only matches are in `docs/product-replacement-roadmap.md` and `GAP_ANALYSIS.md`, not in code. The studio surface (`ui/StudioScreen.kt`) shows sources, research and an outline; it generates no study artifact (roadmap N2.04-N2.08). |

### 4.1 Notebook matrix counts

15 features inventoried.

| Column | feature-level support | `limited` | `unsupported` | `unverified` |
| :--- | ---: | ---: | ---: | ---: |
| Read | 7 | 3 | 5 | 0 |
| Display | 7 | 2 | 6 | 0 |
| Edit | 5 | 0 | 10 | 0 |
| Preserve | 3 | 6 | 6 | 0 |
| Export | 3 | 0 | 12 | 0 |

---

## 5. Cross-cutting findings

Each finding below was checked against the current tree before it was written. Where
the tree has moved on since the roadmap was written, the current state is given and
the roadmap's wording is **not** repeated.

### 5.1 The Ask path retrieves `source_ids`, not note contents — **confirmed, still true**

`backend/src/lmeditor/notebooks/answer.py` now resolves the selected records individually:

```python
for source_id in notebook.source_ids:
    stored = source_library.get(source_id)
```

`notebook.note_ids` is never read anywhere in `answer.py`; retrieval still uses
the sources named by `notebook.source_ids` rather than note contents.
A note attached to a notebook is therefore membership only. The roadmap's §2 sentence
("the Ask path retrieves `source_ids`, not note contents") still describes the tree
exactly.

### 5.2 `answered=true` requires only at least one supported citation — **confirmed, still true**

`backend/src/lmeditor/notebooks/answer.py:121`:

```python
answered = bool(answer_text) and bool(citations)
```

A citation is added only when the tier can find an exact quote for a claim that
carries it (`answer.py:112-117`, `:175-176`). So `answered=true` means "the answer
said something and at least one citation survived", not "the answer is supported".
The desktop renders the two states with different labels (`ui/NotebookScreen.kt:786`
"NOT ANSWERED" vs `:811` "ANSWERED FROM THIS NOTEBOOK"), which makes the boolean
read as a support verdict. This is exactly the risk roadmap N1.07 names.

### 5.3 Extracted source records now persist — **first M1 implementation slice**

`kotlin/server/src/main/kotlin/lmeditor/server/SourceArchive.kt:28` stores versioned
records, chunks and metadata. Normal Python source operations pass the archive
root; the 256-entry memory cache can evict entries without deleting those records.
Rootless direct bridge calls preserve their historical memory-only behavior.

`backend/src/lmeditor/notebooks/answer.py:85` now checks every source, retains its
resolved chunk snapshot and raises `NotebookAskError` with unavailable IDs before
retrieval or inference. `backend/tests/test_source_persistence.py` exercises fresh
JVM restoration and notebook Ask after restart; `test_notebook_routes.py:379`
checks partial and total source loss. Source version history, original byte
retention and changed-source detection are still outstanding, so F.02/N1.01
remain `in-progress`.

### 5.4 A section `String` is still the authoritative document model — **confirmed, still true**

- `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/DocumentWorkspace.kt:37`
  (`sectionDraft`), `:46` (`savedContent`), `:83` (`content`) — the document workspace
  holds section text as `String`.
- `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt:498-523` —
  the section editor is a single `OutlinedTextField` over that string.
- `kotlin/desktop/src/main/kotlin/lmeditor/desktop/model/StudioController.kt:94-100` —
  saving writes the string back whole.

This is in direct contrast to the rich model in `kotlin/word`: `WordBlock` carries
runs, a style and a table payload (`WordModel.kt:513-535`), but nothing in
`kotlin/desktop` imports or renders it. The desktop's only structured renderer,
`ui/EditorPane.kt:195`, parses the **studio's markdown** through
`render/ManuscriptBlocks.kt` — a different model from the OOXML one. Roadmap W1.02
("A section `String` must not be the authoritative model for rich edits") is open.

### 5.5 The tree has moved on: the six skipped tests now run and pass

The roadmap's §2 baseline says the backend suite reports "1355 passed, 6 skipped".
**That figure no longer describes the tree.** Measured on this working tree on
26 October 2026:

```
backend\.venv\Scripts\python.exe -m pytest backend/tests -q -rs
→ 1361 passed, 2 warnings in 563.00s (0:09:23)
```

**1361 passed, 0 skipped.** The count difference is exactly the six the roadmap
recorded as skipped, and they now pass rather than skip.

The roadmap does not name the six individually, so the identification below is an
**identification by count and by gate**, not a quotation: the only Office-dependent
module in the tree whose test count is exactly six is
`backend/tests/test_word_chart_conformance.py`, and it is gated on
`word_is_available()` by a module-level `skipif`
(`backend/tests/test_word_chart_conformance.py:34-37`, reason "Microsoft Word is not
installed on this machine"). Those six are:

| # | Test | File |
| :-- | :--- | :--- |
| 1 | `test_word_opens_the_document_we_wrote_back` | `backend/tests/test_word_chart_conformance.py` |
| 2 | `test_word_still_sees_the_chart` | `backend/tests/test_word_chart_conformance.py` |
| 3 | `test_the_chart_keeps_the_size_it_was_written_at` | `backend/tests/test_word_chart_conformance.py` |
| 4 | `test_no_relationship_points_at_a_relationship_part` | `backend/tests/test_word_chart_conformance.py` |
| 5 | `test_the_chart_keeps_the_parts_it_owns` | `backend/tests/test_word_chart_conformance.py` |
| 6 | `test_the_package_holds_no_relationship_as_content` | `backend/tests/test_word_chart_conformance.py` |

Microsoft Word is present on this machine, so the module runs and all six pass. The
two sibling Office-dependent modules also now run and pass:
`backend/tests/test_word_real_word.py` (5 tests) and
`backend/tests/test_word_app_conformance.py` (8 tests) — together with
`test_word_package_root.py` (3), `test_kotlin_tier_parity.py` (1) and
`test_kotlin_dependencies.py` (24), all 41 tests across the six gated modules pass:

```
pytest backend/tests/test_word_real_word.py backend/tests/test_word_app_conformance.py \
       backend/tests/test_word_chart_conformance.py backend/tests/test_word_package_root.py \
       backend/tests/test_kotlin_tier_parity.py backend/tests/test_kotlin_dependencies.py -v -rs
→ 41 passed in 161.84s (0:02:41)
```

**Residual uncertainty, stated:** because the baseline's six were never named, the
identification above rests on the chart-conformance module being the only
Office-gated module with exactly six test functions *today*. If the baseline tree
skipped a different set of six (for example one test from each of several modules),
the mapping would differ, and this document's claim would then be wrong in the
labels while remaining right about the count. The count itself — 1361 passed, 0
skipped, six more tests executing than the roadmap recorded — is measured directly
and is not in doubt.

**Consequence for the acceptance gates:** roadmap R.02's warning ("Skipped
Office-dependent tests are unverified compatibility, not a pass") no longer applies
*on this machine* — the Office-dependent tests are no longer skipped. They are,
however, **machine-dependent**: on a machine without Word they skip again, and a
green run there is evidence that the whitebox suite passes, not that the package is
conformant. The compatibility claim this inventory relies on is therefore
machine-scoped, and the DOCX matrix above does not claim that Word renders anything
identically.

### 5.6 The DOCX preservation path has a precise boundary

The book exporter does preserve imported structure, but only up to a named line.
`kotlin/library/src/main/kotlin/lmeditor/library/BookExporter.kt:26-32` and
`:156-160`: a section whose text is **unchanged** is returned as it was imported
(keeping its table, image and tracked change); a section whose text **changed** keeps
the structure the text editor could not see and takes only its prose from the new
text. The preservation claim is therefore true for untouched sections and bounded
for edited ones. A book with no import origin is rendered from its text alone
(`BookExporter.kt:41-53`, `:62-83`).

### 5.7 The word model's own boundary

The model is flat by design: every block — heading, paragraph, list item, table —
sits in one ordered list so that reading order is the model's order
(`WordModel.kt:663-673`). A block's plain text must equal the normalised
concatenation of its live runs, and the model refuses a block that disagrees
(`WordModel.kt:603-615`). This is a strength for round-tripping and a limit for
editing: there is no anchor into a block, so an edit cannot yet be mapped onto a
single run (roadmap W1.06).

---

## 6. What this inventory does not claim

- **It does not claim Word renders anything identically.** No row asserts visual
  fidelity. "Preserved" means the construct survives a read/write cycle in the
  model's own terms. The only Word-rendered evidence in the tree is the
  real-Word conformance harness, and this document does not assert its scope
  beyond the tests it names.
- **It does not claim the desktop can edit rich documents.** It reports that
  `Display` and `Edit` are `unsupported` for all 44 DOCX features, which is a
  statement about `kotlin/desktop`, not about `kotlin/word`.
- **It does not certify the backend or the desktop application.** It is a feature
  inventory built from source and from one execution of the backend test suite.
  It says nothing about performance, concurrency, crash recovery or release
  readiness, and it is not a roadmap completion claim for any W, O, N or P item.
- **It does not report the vault and notebook surfaces as if they were one
  product.** The three matrices are separate, and the cross-cutting findings are
  the only places where they are related to each other.
- **It does not enumerate every OOXML element.** The DOCX matrix covers the
  constructs the roadmap names. Elements outside that list were not individually
  searched; where a row reports absence, the search that established it is named.
- **It does not replace the compatibility corpus.** Roadmap R.01's fixture corpus
  and its manifest are owned by another agent; this inventory records what the code
  path does, and the corpus will show what real files do to it.
- **No cell in this inventory is `unverified`.** Every status above was
  established from the source or from a named test, so nothing had to be left
  open. Where the evidence was partial, the cell says `limited` or `unsupported`
  and the last column names the search that established it. What is *not*
  established anywhere in this document is Word's rendered fidelity (§6, first
  bullet) and the machine-scope of the Office-dependent conformance tests (§5.5);
  those are stated as limits, not as statuses.
