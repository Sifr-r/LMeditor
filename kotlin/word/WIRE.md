# The Word document model on the wire

This file is **binding**. The Kotlin tier and the Python tier both implement
against it, and neither may drift from it without this file changing first.

The Python model (`lmeditor/word/model.py`) and the Kotlin model
(`lmeditor/word/WordModel.kt`) declare the *same* fields in the same order and
with the same validation rules. This document pins the third thing: how one of
them travels between the two.

---

## 1. Conventions

| Concern | Rule |
|---|---|
| Key names | `snake_case`, identical to the Python Pydantic field names. |
| Enums | The enum's `wire` value, a plain string. Never the ordinal. |
| `bytes` | A base64 string (standard alphabet, padded). Never a list of ints. |
| `datetime` | An ISO-8601 string. See §1.1. |
| `tuple[...]` | A JSON array. |
| `Optional[X] = None` | JSON `null` or the key being absent — **both** decode to `None`. |
| Absent vs. null | A decoder MUST treat an absent key exactly like an explicit `null`. |
| Unknown keys | A decoder MUST ignore them, so the wire can grow without breaking old peers. |
| Missing required key | A decoder MUST raise the domain error, not fall back to a default. |

### 1.1 Timestamps

`WordRevision.date`, `WordComment.date`, `WordProperties.created` and
`WordProperties.modified` are `datetime`.

The encoder writes what `datetime.isoformat()` writes on the Python side. That
means a *naive* timestamp is `"2024-01-01T00:00:00"` (no offset) and an
*aware* one is `"2024-01-01T00:00:00+00:00"`.

The Kotlin decoder MUST accept both:

- a value with an offset — `OffsetDateTime.parse`;
- a value without one — `LocalDateTime.parse(...).atOffset(ZoneOffset.UTC)`.

The Kotlin encoder writes `OffsetDateTime.toString()`, which renders UTC as
`Z`. Python's `datetime.fromisoformat` accepts `Z` on 3.11+, and LMeditor
requires 3.13, so the round trip is lossless.

### 1.2 Enumerations

| Enum | Values |
|---|---|
| `WordBlockKind` | `title`, `heading`, `paragraph`, `list_item`, `table`, `quote`, `code`, `page_break` |
| `ListMarker` | `bullet`, `number` |
| `WordRevisionKind` | `insertion`, `deletion`, `move_from`, `move_to`, `format_change` |

---

## 2. The model

### 2.1 `WordDocument`

```json
{
  "blocks": [],
  "properties": {},
  "footnotes": [],
  "endnotes": [],
  "comments": [],
  "headers": [],
  "footers": [],
  "sections": [],
  "styles": [],
  "media": [],
  "parts": []
}
```

Every key is always present. The nine collection keys are arrays (possibly
empty). `properties` is an object.

### 2.2 `WordBlock`

| Key | Type |
|---|---|
| `block_id` | string |
| `kind` | `WordBlockKind` wire string |
| `text` | string |
| `level` | integer |
| `list_marker` | `ListMarker` wire string or `null` |
| `list_group` | string or `null` |
| `number_format` | string or `null` |
| `number_text` | string or `null` |
| `style` | string or `null` |
| `runs` | array of `WordRun` |
| `table` | `WordTable` or `null` |

### 2.3 `WordRun`

| Key | Type |
|---|---|
| `text` | string |
| `bold` | boolean |
| `italic` | boolean |
| `underline` | boolean |
| `superscript` | boolean |
| `subscript` | boolean |
| `strike` | boolean |
| `font` | string or `null` |
| `size` | integer or `null` |
| `color` | string or `null` |
| `highlight` | string or `null` |
| `hyperlink` | string or `null` |
| `footnote_ref` | string or `null` |
| `endnote_ref` | string or `null` |
| `revision` | `WordRevision` or `null` |
| `comment_refs` | array of string |
| `image_ref` | string or `null` |
| `equation_xml` | string or `null` |
| `part_ref` | string or `null` |
| `anchor_extent` | array of two integers, or `null` |
| `format_change_xml` | string or `null` |

### 2.4 `WordRevision`

| Key | Type |
|---|---|
| `kind` | `WordRevisionKind` wire string |
| `author` | string |
| `date` | ISO-8601 string or `null` |
| `revision_id` | string |

### 2.5 `WordTable`

| Key | Type |
|---|---|
| `rows` | array of `WordTableRow` |
| `header_rows` | integer |
| `column_widths` | array of integer |

### 2.6 `WordTableRow`

| Key | Type |
|---|---|
| `cells` | array of `WordTableCell` |

### 2.7 `WordTableCell`

| Key | Type |
|---|---|
| `text` | string |
| `row_span` | integer |
| `col_span` | integer |
| `is_header` | boolean |
| `is_merge_continuation` | boolean |
| `width` | integer or `null` |
| `runs` | array of `WordRun` |
| `tables` | array of `WordTable` |

### 2.8 `WordFootnote` / `WordEndnote`

| Key | Type |
|---|---|
| `footnote_id` / `endnote_id` | string |
| `text` | string |
| `runs` | array of `WordRun` |

### 2.9 `WordComment`

| Key | Type |
|---|---|
| `comment_id` | string |
| `author` | string |
| `initials` | string |
| `date` | ISO-8601 string or `null` |
| `text` | string |
| `runs` | array of `WordRun` |
| `anchor_block_id` | string or `null` |
| `anchor_text` | string |

### 2.10 `WordHeaderFooterRef`

| Key | Type |
|---|---|
| `ref_type` | string |
| `part_name` | string |
| `text` | string |

### 2.11 `WordSection`

| Key | Type |
|---|---|
| `page_width` | integer |
| `page_height` | integer |
| `orientation` | string |
| `margin_top` | integer |
| `margin_right` | integer |
| `margin_bottom` | integer |
| `margin_left` | integer |
| `header_refs` | array of `WordHeaderFooterRef` |
| `footer_refs` | array of `WordHeaderFooterRef` |
| `end_block_id` | string or `null` |

### 2.12 `WordStyle`

| Key | Type |
|---|---|
| `style_id` | string |
| `name` | string |
| `based_on` | string |
| `style_type` | string |
| `num_id` | string or `null` |
| `num_level` | integer |

### 2.13 `WordMedia`

| Key | Type |
|---|---|
| `media_id` | string |
| `part_name` | string |
| `content_type` | string |
| `data` | base64 string |
| `relationship_id` | string |

### 2.14 `WordPart`

| Key | Type |
|---|---|
| `part_name` | string |
| `content_type` | string |
| `data` | base64 string |
| `relationship_id` | string |
| `relationship_type` | string |
| `relationships` | array of `WordPartRelationship` |
| `package_relationship` | `WordPartRelationship` or `null` |

### 2.15 `WordPartRelationship`

| Key | Type |
|---|---|
| `relationship_id` | string |
| `relationship_type` | string |
| `target` | string |
| `is_external` | boolean |

### 2.16 `WordProperties`

| Key | Type |
|---|---|
| `title` | string |
| `author` | string |
| `subject` | string |
| `keywords` | string |
| `comments` | string |
| `created` | ISO-8601 string or `null` |
| `modified` | ISO-8601 string or `null` |
| `revision` | integer |

---

## 3. The seam methods

Three methods. All live in the `word.` namespace.

### 3.1 `word.read`

Reads a `.docx` package and answers with the document model.

**Request**

| Key | Type | Meaning |
|---|---|---|
| `bytes_b64` | string | The raw bytes of the package, base64. Always present. |
| `base_uri` | string or absent | The URI the document is served from. |
| `origin` | string or absent | The origin label used in error messages. `"<bytes>"` for a byte source, the path for a file source. Defaults to `"<bytes>"`. |

The Python door decides which of those it sends: it reads a path into bytes
itself and then reports the path as `origin`, so the tier never touches the
filesystem on Python's behalf and the error messages stay identical.

**Response**

```json
{
  "document": { ...the WordDocument wire form... },
  "omissions": ["..."],
  "package_relationships": [ ...the WordPartRelationship wire form... ]
}
```

`omissions` is the reader's omission register, in order. `package_relationships`
is the package root's relationships, sorted by `relationship_id`.

**Errors**

`WordParseError` with `details = {"origin": <origin>, "reason": <reason>}`.

### 3.2 `word.write`

Renders the document model back into a `.docx` package.

**Request**

| Key | Type | Meaning |
|---|---|---|
| `document` | object | The WordDocument wire form. |
| `deterministic` | boolean or absent | Whether the output is byte-deterministic. Defaults to `true`. |

**Response**

```json
{ "bytes_b64": "...", "omissions": ["..."] }
```

| Key | Type | Meaning |
|---|---|---|
| `bytes_b64` | string | The `.docx` package, base64. |
| `omissions` | list of strings | The write's omission register: one human-readable description per loss the render did not carry. Empty when nothing was lost. The register belongs to the write, not to the writer: it is reset before every render. |

The register is the writer's, not the reader's: `word.read` reports what the
*reader* lost, `word.write` reports what the *writer* could not carry. The
wording is the reference writer's wording, so the same loss is named the same
way on both sides of the seam.

**Errors**

`WordRenderError` with `details = {"reason": <reason>}`.

### 3.3 The binary key name

A seam method's **top-level** binary payload is `bytes_b64`, in both
directions. That includes the `word.manuscript` method of §3.4. Do not
introduce a second name for the same thing.

This is about the *method's* payload, not about a model field. A field of the
document model keeps the name the Python model gives it — §2.13 and §2.14 call
the binary field of a media part and of a package part `data`. So:

- the request body of `word.read` — `bytes_b64`;
- the response body of `word.write` — `bytes_b64`;
- the response body of `word.manuscript` — `bytes_b64`;
- the `data` field of a `WordMedia` or a `WordPart` inside the document —
  `data`, holding base64.

Both `data` fields are base64 strings. A `bytes` field is never rendered as
text: Pydantic's `model_dump(mode="json")` renders `bytes` as a UTF-8 string,
so the encoder must encode those two fields itself.

### 3.4 `word.manuscript`

Builds a manuscript document from a draft and its bibliography.

The manuscript rules are the Kotlin tier's (`kotlin/word/WordManuscript.kt`);
this method is where they are reached. The draft and the bibliography are core
schemas, so they cross the seam in the core wire form that
`kotlin/core/WireDecode.kt` reads (`documentDraftFromWire`,
`bibliographicRecordFromWire`).

**Request**

| Key | Type | Meaning |
|---|---|---|
| `draft` | object | The `DocumentDraft` wire form. |
| `bibliography` | array of objects | The `BibliographicRecord` wire forms, in the order they should appear. May be absent or empty. |
| `verified_only` | boolean or absent | Whether to drop every section that did not pass verification. Defaults to `false`. |

**Response**

```json
{
  "bytes_b64": "...",
  "document": { ...the WordDocument wire form... }
}
```

| Key | Type | Meaning |
|---|---|---|
| `bytes_b64` | string | The rendered `.docx` package, base64. The package is rendered deterministically. |
| `document` | object | The WordDocument wire form of §2.1 — the manuscript the package holds. |

The answer carries **both** halves because the two callers want different
ones: the Python door rebuilds the document model with `WordDocument.from_wire`
and hands it back to its own callers, and the parity harness compares the two
tiers' documents directly. The document travels in exactly the wire form §2
pins — the same form `word.read` answers with and `word.write` is given — so
there is one vocabulary, not two.

The bytes and the document are two views of the same build: the package is
rendered from the document, never from a second, separately built one.

**Errors**

The tier's own domain errors: a draft or a bibliography the core decoders
refuse is a `ValidationError`, and a package that cannot be rendered is a
`WordRenderError`. A failure never terminates the bridge.

---

## 4. What must not drift

The Python door and the Kotlin tier are two implementations of one contract.
When they disagree, the rule that decides is *the Python model's validation*:
the Python model is the public API of the package, so a document the Kotlin
tier accepts but the Python model rejects is a bug in the Kotlin tier.

In particular:

- A `WordBlock` of kind `heading` has `level` in `1..9`.
- A `WordBlock` of kind `title`, `paragraph`, `quote` or `code` has `level == 0`.
- A `WordBlock` of kind `list_item` has `level <= 8`.
- A `WordTableCell` with `row_span > 1` or `col_span > 1` must not be
  repeated in the grid: the continuation cells carry
  `is_merge_continuation == true`.
- `WordDocument` has at least one block and unique `block_id`s.

Both sides enforce these before answering. The Python side enforces them with
Pydantic (so the door raises `ValidationError`), the Kotlin side with its own
model invariants (so the tier raises `WordRenderError`).
