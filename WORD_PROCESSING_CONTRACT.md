# Word Processing Rewrite — Binding Interface Contract

> **Superseded in part — 2026-10-27.** The Word subsystem was moved onto the
> Kotlin tier. `backend/src/lmeditor/word/reader.py`, `writer.py` and
> `manuscript.py` are now **doors** onto `kotlin/word`: they keep their public
> API and answer with the tier's answers. `word/text.py` and `word/formats.py`
> are deliberately **not** doors — they carry Python twins of the tier's rules,
> held in step by a shared corpus (`ARCHITECTURE.md` §18.2.1). The rules this
> contract states are still the rules; where they are *implemented* is what
> this banner supersedes. Where the contract names a Python mechanism —
> `zipfile`, `lxml`, `python-docx` — the tier uses its own: `java.util.zip`,
> `javax.xml`, and a hand-written OOXML writer.
>
> Date: 2026-10-20
> Status: binding for `backend/src/lmeditor/word/` — for the rules below; see
> the supersession banner above for where those rules are implemented.
> Scope: the Word (`.docx` / OOXML) processing element of LMeditor — **read** and **write**.
> Authority: this document outranks any implementation convenience. A component that deviates from it
> is a contract violation, not a design choice.

---

## 1. Why this rewrite exists

The word-processing element is the one place where LMeditor meets a foreign, adversarial document
format, and it is the only place where the product's central promise — *every claim is traceable to
a source passage* — must survive a round trip through a binary container.

The state of the element before this pass was:

| Surface | Where it lived | Defect |
| --- | --- | --- |
| Read | `ingestion/loaders.py::DocxSourceLoader` | `doc.paragraphs` only. Tables, list structure, headers/footers, footnotes, content controls (`w:sdt`), text boxes and tracked changes are **silently dropped**. A Word document with its evidence in a table ingested as an empty string. |
| Write | `export/docx.py` on `checkpoint/pre-track0-recovery`; **absent from `main`** | `doc.add_heading(title, level=0)` + `level=1` for every section. No list, table, run-level formatting, footnote, citation or core-property support. Inline citation markers were not converted at all. The element did not exist on the current branch. |

A reader that silently drops content and a writer that flattens structure are the two halves of the
same defect: **structure is lost at the boundary**. This pass replaces both halves with one shared
document model so that structure survives ingestion and is re-emitted on export.

### 1.1 A note on the reference implementation

The user identified Word 365 as the behavioural reference. Word for the Web is a TypeScript/React
application (Office `WOPI` + OOXML rendering); it is **not** Kotlin, and the OOXML it emits is the
standard `ECMA-376` WordprocessingML dialect. That distinction does not change the engineering
requirement: the reference behaviour is *Word's*, and Word's dialect is the specification this
element targets. Concretely, the reference document is used as a **fidelity fixture**: the reader
must recover its structure, and the writer must emit documents Word opens without repair.

---

## 2. Domain boundary

```
core ──▶ word ──▶ ingestion ──▶ verification/drafting ──▶ workflow ──▶ server
```

`lmeditor.word` is a **leaf above `core`**. It may import `lmeditor.core.*` and the standard
library. It must **not** import `lmeditor.ingestion`, `lmeditor.workflow` or `lmeditor.server`.

| Module | Owner | Single responsibility |
| --- | --- | --- |
| `word/model.py` | contract owner | The shared, format-neutral document model. Pure domain types, zero OOXML knowledge. |
| `word/text.py` | contract owner | Whitespace and text normalisation rules shared by reader and writer. |
| `word/reader.py` | worker R | `.docx` bytes → `WordDocument`. |
| `word/writer.py` | worker W | `WordDocument` → `.docx` bytes. |
| `word/manuscript.py` | worker M | `core.schemas.DocumentDraft` → `WordDocument`. |
| `core/exceptions.py` | contract owner | `WordProcessingError` / `WordParseError` / `WordRenderError`. |

---

## 3. Shared types (normative)

`word/model.py` exposes its public names through its own `__all__`, and that list is the
authority. Reader, writer and manuscript bridge import **only** from this surface; they must not
re-declare these types. The listing below shows the core of that surface.

```python
class WordBlockKind(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    QUOTE = "quote"
    CODE = "code"
    PAGE_BREAK = "page_break"

class ListMarker(str, Enum):
    BULLET = "bullet"
    NUMBER = "number"

class WordRun(BaseModel):        # frozen, extra="forbid"
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    hyperlink: str | None = None      # absolute target URL
    footnote_ref: str | None = None   # id of a WordFootnote
    part_ref: str | None = None       # package part a non-image element anchors
    anchor_extent: tuple[int, int] | None = None   # EMU (width, height)

class WordPartRelationship(BaseModel):   # frozen, extra="forbid"
    relationship_id: str            # id inside the owning part
    relationship_type: str          # full relationship type URI
    target: str                     # package part name, or a URL when external
    is_external: bool = False

class WordPart(BaseModel):          # frozen, extra="forbid"
    part_name: str                  # package part name
    content_type: str = ""
    data: bytes = b""
    relationship_id: str = ""       # relationship of the DOCUMENT part
    relationship_type: str = ""     # that relationship's type
    relationships: tuple[WordPartRelationship, ...] = ()   # the part's own
    package_relationship: WordPartRelationship | None = None  # of the PACKAGE ROOT

class WordTableCell(BaseModel):  # frozen, extra="forbid"
    text: str
    row_span: int = 1                 # >= 1
    col_span: int = 1                 # >= 1
    is_header: bool = False

class WordTableRow(BaseModel):    # frozen, extra="forbid"
    cells: tuple[WordTableCell, ...]

class WordTable(BaseModel):       # frozen, extra="forbid"
    rows: tuple[WordTableRow, ...]
    header_rows: int = 0             # number of leading rows marked as header

class WordBlock(BaseModel):       # frozen, extra="forbid"
    block_id: str
    kind: WordBlockKind
    text: str = ""
    level: int = 0                   # HEADING: 1..9; LIST_ITEM: 0..8; else 0
    list_marker: ListMarker | None = None
    list_group: str | None = None    # continuity group for ordered numbering
    style: str | None = None
    runs: tuple[WordRun, ...] = ()
    table: WordTable | None = None

class WordFootnote(BaseModel):     # frozen, extra="forbid"
    footnote_id: str
    text: str = ""
    runs: tuple[WordRun, ...] = ()

class WordProperties(BaseModel):  # frozen, extra="forbid"
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    comments: str = ""
    created: datetime | None = None
    modified: datetime | None = None
    revision: int = 1

class WordDocument(BaseModel):    # frozen, extra="forbid"
    blocks: tuple[WordBlock, ...]
    properties: WordProperties = WordProperties()
    footnotes: tuple[WordFootnote, ...] = ()
    headers: tuple[str, ...] = ()
    footers: tuple[str, ...] = ()
```

### 3.1 Invariants (enforced by `WordBlock` validators; I7 is enforced on the `WordDocument`)

| # | Invariant |
| --- | --- |
| I1 | `kind == TABLE` ⟺ `table is not None`. |
| I2 | `kind == TABLE` ⟹ `text == ""` and `runs == ()`. |
| I3 | `kind == LIST_ITEM` ⟹ `list_marker is not None`. |
| I4 | `kind == HEADING` ⟹ `1 <= level <= 9`. |
| I5 | `kind in {TITLE, PARAGRAPH, QUOTE, CODE}` ⟹ `level == 0` and `list_marker is None`. |
| I6 | `kind == PAGE_BREAK` ⟹ `text == ""` and `table is None`. |
| I7 | `block_id` is unique within a `WordDocument` — enforced by the `WordDocument` validator, see D1. |
| I8 | If `runs` is non-empty, `text` **equals** the normalised concatenation of the run texts. A block therefore cannot claim one text while carrying runs that say another. |
| I9 | A table cell that continues a vertical merge carries `is_merge_continuation=True` and `row_span=1`. The cell that *starts* a merge carries `row_span=N` (the number of rows it occupies, counting its own) and keeps the text. A continuation cell may not itself span rows. |
| I10 | `kind == LIST_ITEM` ⟹ `level <= 8`. The nesting depth of a list item is bounded by the nine levels Word's numbering can express (`w:ilvl` 0-8), so a deeper item is refused at construction rather than handed to a renderer that cannot express it. |

### 3.2 Invariants on `WordDocument`

| # | Invariant |
| --- | --- |
| D1 | `block_id` values are unique across `blocks`. |
| D2 | Every `run.footnote_ref` resolves to a `WordFootnote.footnote_id`. |
| D3 | `plain_text()` is **deterministic**: the same document always yields the same string. |
| D4 | A cell with `row_span > 1` is the origin of a vertical merge; the rows it covers carry continuation cells. A continuation cell carries **no text** of its own and spans a single row, because Word ignores content stored in a continuation cell. Both rules are **enforced by the model**, not merely documented: a continuation cell that carries text, or that spans more than one row, cannot be constructed at all. |

---

## 4. Text normalisation rules (`word/text.py`)

| # | Rule |
| --- | --- |
| T1 | A run's text is taken **verbatim** from `w:t`. Runs are never stripped individually — Word encodes meaningful leading/trailing spaces with `xml:space="preserve"`. Stripping per run destroys inter-word spacing. |
| T2 | Normalisation happens at **block** level, after all runs of the block are concatenated. |
| T3 | Normalisation replaces non-breaking space (`U+00A0`), zero-width space (`U+200B`), soft hyphen (`U+00AD`) and the Word-specific figure/ideographic spaces with a plain space. |
| T4 | Normalisation collapses runs of whitespace (space, tab, `w:tab`, `w:br` line breaks) into a single space and strips the ends. |
| T5 | A block whose normalised text is empty is **dropped** from `plain_text()`. A table is never dropped for having empty *cells*: its rows are always rendered, so a table with one blank row still contributes its other rows. |
| T6 | `plain_text()` joins surviving blocks with a single blank line (`"\n\n"`). |
| T7 | A `TABLE` block renders **exactly one line per row** — blank rows included — with cells joined by `" | "` and rows joined by `"\n"`. A three-row table therefore renders three lines, never two. |
| T8 | A `PAGE_BREAK` block contributes no characters. |
| T9 | A `LIST_ITEM` renders as its text **without** a bullet or number prefix: the marker is structure, not content, and prefixes pollute the chunker. |

---

## 5. Reader contract (`word/reader.py`)

```python
class WordReader:
    def read(self, source: str | Path | bytes, *, base_uri: str | None = None) -> WordDocument: ...
    def read_bytes(self, data: bytes, *, base_uri: str | None = None) -> WordDocument: ...
```

**Implementation boundary.** The reader parses the OOXML package directly (`zipfile` + `lxml`).
It must **not** use `python-docx`'s `Document.paragraphs` / `Document.tables` accessors, because
those walk only the direct children of `w:body` and silently discard content controls, text boxes
and table-cell paragraphs — the exact defect this pass exists to remove.

**Required behaviour.**

| # | Requirement |
| --- | --- |
| R1 | Body children are walked **in document order**: `w:p`, `w:tbl`, `w:sdt` (recurse into `w:sdtContent`), `w:bookmarkStart`, `w:sectPr`. |
| R2 | A paragraph's style is resolved against `word/styles.xml`. A style whose `w:name` is `heading 1`…`heading 9` (case-insensitive) — or whose `w:styleId` is `Heading1`…`Heading9` — yields `kind=HEADING` with the matching `level`. The `Title` style yields `kind=TITLE`. |
| R3 | A paragraph carrying `w:numPr` with an ordered `w:numFmt` yields `kind=LIST_ITEM`, `list_marker=NUMBER`; a bullet format yields `BULLET`. The `w:ilvl` becomes `level`; the resolved `w:numId` becomes `list_group`. |
| R4 | Table cells are read recursively; each cell's text is the concatenation of its paragraphs' normalised text **joined by a single space**. `w:gridSpan` sets `col_span` (default 1). A vertical merge is represented the way Word represents it: the cell carrying `w:vMerge w:val="restart"` **keeps the text** and receives `row_span` = the number of rows the merge occupies, counting its own; the cells below it, carrying `w:vMerge` without `restart`, receive `row_span=1`, `is_merge_continuation=True` and no text. A row whose `w:trPr/w:tblHeader` is present is a header row: mark its cells `is_header=True` and count it in `WordTable.header_rows`. |
| R5 | Hyperlinks (`w:hyperlink`) resolve their target through `word/_rels/document.xml.rels` and produce runs with `hyperlink` set to the resolved target. An absolute relationship target is used verbatim. A **relative** target is resolved against the caller's optional `base_uri` when one is supplied (`WordReader.read(..., base_uri=...)`); with no base, a relative target is preserved verbatim and recorded in `last_omissions`, because a relative target cannot be made absolute without knowing where the document lives. Internal anchors (`w:anchor`, no `r:id`) resolve to `"#anchor"`. |
| R6 | Footnotes (`word/footnotes.xml`) are read into `WordDocument.footnotes`; a `w:footnoteReference` in a run sets `run.footnote_ref`. |
| R7 | `w:ins` (inserted) content is **included**; `w:del` (deleted) content is **excluded**. `w:delText` is never emitted. |
| R8 | Text boxes (`w:txbxContent`) and content controls (`w:sdtContent`) contribute their paragraphs in place, in document order. |
| R9 | Field codes (`w:fldSimple`, `w:instrText`) contribute **no** text. A `w:br` of type `page` yields a `PAGE_BREAK` block; other breaks are whitespace. |
| R10 | Headers and footers (`word/header*.xml`, `word/footer*.xml`) are read into `WordDocument.headers` / `footers` as plain strings, in part-name order. |
| R11 | Core properties (`docProps/core.xml`) populate `WordDocument.properties`; `dcterms:created` / `dcterms:modified` become timezone-aware UTC datetimes. |
| R12 | The reader is **deterministic**: identical bytes produce an identical `WordDocument`. |
| R13 | A malformed package raises `WordParseError` — never `KeyError`, `zipfile.BadZipFile` or `lxml` errors escaping to the caller. |
| R14 | A **relationship part is never content**. A package part name whose parent directory is `_rels` is the relationship list of a sibling part, not a part; it is never carried into `WordDocument.parts`, and the reader reports no omission for it. Handing one to Word as content is what makes Word report the package as corrupt. |
| R15 | Every carried part carries **its own relationships** in `WordPart.relationships`: the relationships of `<dir>/_rels/<name>.rels`, each with the id it is known by inside the owning part, its type, and its target. An **internal** target is resolved **relative to the owning part's own directory** (`colors1.xml` beside `word/charts/chart1.xml` is `word/charts/colors1.xml`); an **external** target keeps its URL and sets `is_external=True`. The list is sorted by relationship id, so the same package always reads to the same model. |
| R16 | The **package root** relationships (`_rels/.rels`) are read as well. A part they reach carries that relationship in `WordPart.package_relationship`: the custom document properties of a Word document are reached from the package root and not from the document. `WordPart.relationship_id` / `relationship_type` continue to describe the relationship **of the document part**, so the two namespaces never collide. |
| R17 | The **size an anchored element is drawn at** is recorded on the run that carries the anchor, as `WordRun.anchor_extent` in EMUs. A DrawingML `wp:extent` is already in EMUs; a VML `v:shape/@style` is in points and is converted at 1 pt = 12700 EMU. The size is recorded for a picture and for a carried part alike, because Word scales both by rewriting the extent of the drawing that holds them. A drawing with no extent yields `None`. |

**Omission register.** Content that has no representation in the model is dropped **loudly**, never
silently: images (`w:drawing`, `w:pict`), embedded objects and comments are skipped, and each
omission increments a counter that the reader exposes through
`WordReader.last_omissions: list[str]`. A reader that cannot say what it dropped is the defect
this pass is removing.

---

## 6. Writer contract (`word/writer.py`)

```python
class WordWriter:
    def __init__(self, *, deterministic: bool = True) -> None: ...
    def write(self, document: WordDocument) -> bytes: ...
```

**Implementation boundary.** The writer uses `python-docx` as the package builder and injects raw
OOXML where `python-docx` has no API (multi-level `numbering.xml`, `footnotes.xml`, hyperlink
relationships, `w:coreProperties`).

| # | Requirement |
| --- | --- |
| W1 | Output is a valid OPC package that **Word opens without a repair prompt**. |
| W2 | `kind=HEADING` emits the real `Heading {level}` style, so Word's navigation pane and TOC work. `kind=TITLE` emits the `Title` style. |
| W3 | `kind=LIST_ITEM` emits a real list paragraph with numbering that respects `level` (nesting) and `list_marker`. Consecutive `NUMBER` items that share a `list_group` continue one sequence; a new `list_group` restarts at 1. |
| W4 | `kind=TABLE` emits a `w:tbl` with a grid (the column count comes from the widest row, honouring `col_span` → `w:gridSpan`), one `w:tr` per row, and `w:tblHeader` on header rows. A cell with `row_span > 1` emits `w:vMerge w:val="restart"`; a cell with `is_merge_continuation=True` emits `w:vMerge` (continue) and no text. A merge read from a document must survive the round trip as a merge. |
| W5 | Runs emit their formatting: `bold`, `italic`, `underline`, `superscript`. |
| W6 | A run with `hyperlink` emits a `w:hyperlink` with a relationship of type `.../hyperlink` and `TargetMode="External"`. |
| W7 | A run with `footnote_ref` emits a `w:footnoteReference`; `WordDocument.footnotes` become `word/footnotes.xml`. |
| W8 | `WordDocument.properties` populate `docProps/core.xml`. |
| W9 | With `deterministic=True` (the default) the writer produces **byte-identical** output for the same input: all timestamps are pinned to a fixed epoch and the zip is written with fixed member order and timestamps. `deterministic=False` uses the current time. |
| W10 | The writer **re-validates** the model it was handed against §3.1/§3.2 before rendering anything, and raises `WordRenderError` when the model violates them. A model that bypassed construction-time validation (for example via `model_construct`) must never be rendered silently: an illegal `text` on a `TABLE` block, or a `text` that disagrees with its runs, is a rendering error, not something to drop. The writer never emits a malformed package to "make it fit". |
| W11 | Two tables that are **adjacent in the model** are separated in the output by an empty paragraph. OOXML has no table boundary: Word reads two adjacent `w:tbl` elements as **one** table, so emitting them side by side silently destroys the second table's grid and every merge in it — a defect no package-level test can see, because the package is well-formed and a raw XML reader still sees two tables. The separator is the empty paragraph Word itself writes when it splits a table; the reader drops it again (an empty paragraph carries no content, T5), so the round trip stays exact. |
| W12 | Every row of a table must fill the table's grid. The grid is as wide as the widest row, so a row that covers fewer columns is a **malformed** table: Word does not refuse it, it silently re-lays the table out, and the document the reader opens is no longer the document the model describes. The writer raises `WordRenderError` for such a table rather than emitting one that Word will change. The check runs on the same layout the renderer emits, so the two cannot disagree. |
| W13 | **Only a part the document reached gets a relationship from the document.** A carried part with a relationship id and a type is related from `word/document.xml` under that id and that type, so a body reference that pointed at `rId7` still resolves. A carried part the document did **not** reach gets **no** relationship from the document: it is reached from the relationship list of the part that owns it. There is no invented relationship type: `.../relationships/carriedPart` is not one, and it was how the writer used to force a part into the package. |
| W14 | **A carried part's own relationships are re-created on the owning part.** Each relationship of `WordPart.relationships` is added to that part's own relationship list, with its original id and its original type, so the package builder writes the owner's `.rels` file with the targets relative to the owner's own directory. An internal target must name a part that is in the package; a target that is not is omitted and **named**. An external target is written as an external relationship. |
| W15 | **A part the package root reaches is related from the package root.** `WordPart.package_relationship` is re-created on `_rels/.rels` with its original id and type, so the document's own relationship list stays exactly the one the body needs. Where the package builder already used that id, its own relationship is moved aside rather than lost. |
| W16 | **A part nothing reaches does not travel, and is named.** An OPC package holds the parts its relationship graph reaches; a part that neither the document, nor the package root, nor any other part reaches has no path into the package and cannot be in it. The writer does not invent a path for it — it records the part in `WordWriter.last_omissions`, so the loss is loud rather than silent. |
| W17 | **A relationship part is never written as content.** A carried part whose parent directory is `_rels` is refused and named. |
| W18 | **The anchored element is drawn at the size the model records.** `WordRun.anchor_extent` (EMU) drives the `wp:extent` of a chart, a diagram and a picture, and the shape style of an embedded object. A picture prefers the recorded extent over the intrinsic pixel size of its image, because Word scales a picture by rewriting the extent of the drawing that holds it. A run with no recorded extent keeps the default size. |

---

## 7. Manuscript bridge contract (`word/manuscript.py`)

```python
def build_manuscript_document(
    document: DocumentDraft,
    bibliography: Sequence[BibliographicRecord] = (),
    *,
    verified_only: bool = False,
) -> WordDocument: ...
```

| # | Requirement |
| --- | --- |
| M1 | The document title becomes a `TITLE` block. Each section becomes a `HEADING` block at `level=1` followed by one `PARAGRAPH` block per paragraph of `SectionDraft.content`. |
| M2 | Sections are ordered by `(order, section_id)`. With `verified_only=True`, only sections that passed verification are included — verified means `status in {VERIFIED, APPROVED}` or `is_verified` is true. |
| M3 | The inline citation markers accepted by `core/citations.py` (`[#chunk_id]`, `[chunk_id]`, `[cite: id]`, comma lists) are **consumed** from the body text and replaced by a superscript numeric run. The marker text must not survive into the paragraph text. |
| M4 | Each consumed marker is numbered in first-appearance order, and the citation's `exact_quote` (when the section carries `CitationLineage` for that chunk) becomes a `WordFootnote` attached to the superscript run. |
| M5 | When `bibliography` is non-empty and `include_references` is requested, a `HEADING` block `References` is appended, followed by one `PARAGRAPH` per record in the given order. |
| M6 | The bridge never mutates its inputs. |

---

## 8. Acceptance gates

1. `backend/.venv/Scripts/python.exe -m pytest -q` — no regression against the pre-pass baseline.
2. `backend/.venv/Scripts/python.exe -m mypy --strict src/lmeditor/word` — clean.
3. A reader/writer **round-trip** property: `WordReader().read(WordWriter().write(doc))` yields a
   document whose `plain_text()` equals the original's `plain_text()`. A **vertical merge must
   survive the round trip as a merge** (the origin keeps its span and its text, and the
   continuation cells stay continuations).
4. Every §3.1/§3.2 invariant has a dedicated failing-case test, and every §4/§5/§6/§7 clause has
   at least one test that would fail if the clause were broken.
5. The ingestion path (`DocxSourceLoader`) returns text that includes **table** content — the
   regression that motivated this pass.
6. The writer's output opens as a valid package: `python-docx` re-reads it, and the `word/document.xml`
   part declares the expected styles.
7. No component outside `lmeditor/word/` imports `zipfile`/`lxml` for OOXML purposes.
8. **Microsoft Word opens the package.** Word is installed on the development machine, so a
   package-level assertion is not the last word: the harness in
   `backend/tests/word_real_word_check.py` asks Word what it saw, and the answer is what the
   document is. The application export, the model-level document and the round trip of a genuine
   Word-authored chart document are all checked this way. A package that Word has to repair is
   a package whose content the operator cannot trust, and this gate is the one that says so.
