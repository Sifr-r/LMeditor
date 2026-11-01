# Editor/layout feasibility spike

Date: 26 October 2026
Status: the M0 feasibility artifact owned by the `feasibility` slot in
`docs/replacement/m0-blueprint.md:179`, and the evidence required by that document's
acceptance item 7 (`docs/replacement/m0-blueprint.md:243-246`). It answers roadmap §8's
dependency decision (`docs/product-replacement-roadmap.md:186`) and the architecture
lead's open decision 2 on bundled font files (`docs/replacement/m0-blueprint.md:268`).

Scope: the Kotlin tier. This spike writes no code, changes no build, and does not
amend `kotlin/CONTRACTS.md` — the amendment proposed in §9 is the architecture lead's
decision (`docs/replacement/m0-blueprint.md:251-257`). No network access was used and
nothing was installed. **The desktop application was not run**; no claim below rests
on the running app.

---

## 1. Decision

**Recommendation: (b) — the layout engine is buildable with an empty third-party runtime
dependency set, but it is not buildable under `kotlin/CONTRACTS.md` §1 as written, so the
contract needs one narrow amendment.**

**A contract change is required.** The reason in one line: pagination needs glyph advance
widths, the JDK ships no font data, and §1 neither admits bundled typographic metrics nor
forbids host-font-dependent measurement — so without the amendment the engine would have
to measure text with `java.awt`, which makes the same document paginate differently on
every machine.

The amendment §1.2 in §9.1 proposes exactly two things and nothing else:

1. **A named asset category.** A tier module may ship a versioned advance-width metric
   table (plus its licence notice) as a resource of the module. It is data, not a
   dependency: it is never declared in `build.gradle.kts`, and the tier's runtime
   dependency set stays empty.
2. **A prohibition.** Tier modules must not measure text with `java.desktop`. The
   exemption in §1.1 is for the *view* (`kotlin/CONTRACTS.md:53-56`); the tier computes
   geometry and the client draws it.

What this buys: real pagination — page size, margins, orientation, breaks, sections,
headers/footers, page numbers — inside the tier, with a layout that is reproducible on
every machine and testable to a golden file. What it costs: a metric asset with a
licence obligation, one documented layout profile that is explicitly LMeditor's own and
not another renderer's, and 21–32 engineer-weeks of tier work for the full W2.01 scope
(§7, §12).

---

## 2. What exists today

### 2.1 The OOXML model already carries page setup

- `WordSection` holds page width and height in twips, an orientation string, the four
  margins, header/footer references and the id of the block the section ends at:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:233-263`. The header/footer
  reference carries a `refType` of `default`, `even` or `first`:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:217-224`.
- A document holds a **list** of sections:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:687`.
- The reader parses `w:pgSz` into width, height and orientation — deriving landscape
  from `width > height` when `w:orient` is absent:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:671-682`. It parses `w:pgMar`
  into the four margins at `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:683-693`
  and the header/footer references at
  `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:694-697`. Defaults are
  12240 × 15840 twips and 1440-twip margins.
- The writer emits `w:sectPr` with `w:pgSz` and `w:pgMar`:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:980-987`, with the header and
  footer references ahead of the page setup at
  `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:963-968`.
- **Only the first section's page setup is written.** A document with more than one
  section loses the rest, and the loss is recorded as an omission:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:952-958`.
- The header/footer distances are hard-coded to 720 twips:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordWriter.kt:985`.
- **Field instructions are discarded.** The reader drops `instrText`, `delInstrText`,
  `fldChar` and `lastRenderedPageBreak`:
  `kotlin/word/src/main/kotlin/lmeditor/word/WordReader.kt:1454`. A `PAGE` field is
  therefore not modelled at all, even though the test fixtures contain one
  (`kotlin/word/src/test/kotlin/lmeditor/word/WordFixtures.kt:421`).

So the *vocabulary* of page setup exists in the model and survives a round trip; the
*geometry* of it does not exist anywhere.

### 2.2 The only layout in the tree is table-grid placement

`kotlin/word/src/main/kotlin/lmeditor/word/WordTableLayout.kt:70` (`layoutTable`) places
cells on a grid by column span and row span, resolving vertical merges
(`kotlin/word/src/main/kotlin/lmeditor/word/WordTableLayout.kt:87-159`). It computes no
width, no height and no page. A table's declared column widths are carried by the model
(`kotlin/word/src/main/kotlin/lmeditor/word/WordModel.kt:493-496`) and nothing consumes
them for layout.

### 2.3 The desktop presentation is paper-like, not pagination

- The "paper" is one continuous scrolling sheet: the column has a minimum height of
  920 dp, not a page count
  (`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/EditorPane.kt:132`), and the same
  pattern appears on the documents screen
  (`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt:453`).
- The margins are fixed dp constants chosen by viewport width, not read from the
  document's section: `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/EditorPane.kt:119`
  and `kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentsScreen.kt:436`.
- The ruler is a `Canvas` that draws 68 fixed subdivisions of the widget's own width
  (`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentRuler.kt:26-27`); its
  comment says it scales with the paper "rather than claiming a physical display
  measurement" (`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/DocumentRuler.kt:24-25`).
  It never touches `WordSection`.
- The block renderer parses markdown into display blocks
  (`kotlin/desktop/src/main/kotlin/lmeditor/desktop/render/ManuscriptBlocks.kt:61` and
  `:81`); it measures nothing.

**No advance width is computed anywhere in the tree.** A search of the whole Kotlin tier
for `measureText`, `advanceWidth`, `getStringBounds`, `TextMeasurer`, `fontMetrics` and
`java.awt` returns no match in any tier module; the only `java.awt`/`javax.swing` imports
in the repository are in the exempt client
(`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/FileDialogs.kt:3-7`).

### 2.4 PDF: the tier reads PDFs and does not write them

- `kotlin/pdf` is "PDF text and metadata extraction":
  `ARCHITECTURE.md:144` and `ARCHITECTURE.md:234`. `PdfDocument` parses and holds
  pages, metadata and issues (`kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfDocument.kt:17-30`),
  `PdfText` extracts page text
  (`kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfText.kt:21` and `:64`) and `PdfMetadata`
  extracts the bibliographic dictionary
  (`kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfMetadata.kt:66-72`). There is no writer:
  no page tree is built and no `startxref` is emitted.
- The Python side has no PDF writer either. `pypdf` is a **reader** dependency
  (`backend/pyproject.toml:20`, used at
  `backend/src/lmeditor/ingestion/ingestion_loaders.py:313` and
  `backend/src/lmeditor/ingestion/ingestion_pdf_metadata.py:115`); `python-docx` and
  `lxml` handle DOCX (`backend/pyproject.toml:21-22`). A search of `backend/` for
  `reportlab`, `fpdf`, `weasyprint`, `docx2pdf` and `pdfkit` finds nothing.

**PDF writing is new work on both tiers**, and the tier is the right place for it.

---

## 3. The `java.desktop` question, answered

Facts verified on this machine (Temurin 25.0.3+9 LTS, `java -version`):

| Question | Answer | How verified |
| :--- | :--- | :--- |
| Is `java.desktop` reachable without a dependency? | **Yes.** `java --describe-module java.se` prints `requires java.desktop transitive`, so `java.desktop` is in `java.se` and therefore in the default root module set for a classpath application. | `java --describe-module java.se` |
| Does the build run in classpath mode? | **Yes.** There is no `module-info.java` anywhere under `kotlin/`, so the unnamed module is used and `java.desktop` is resolved by default; no `--add-modules` is needed. | search of `kotlin/` |
| Can a tier module compile against `java.awt.font`? | **Yes.** `options.release.set(17)` (`kotlin/build.gradle.kts:39`) and `jvmTarget JVM_17` (`kotlin/build.gradle.kts:45`) expose the release-17 API of `java.desktop` — `java.awt`, `java.awt.font` and `java.awt.geom` are all exported (`java --describe-module java.desktop`). | `javap --module java.desktop java.awt.font.LineBreakMeasurer` |
| Does the JDK ship font files? | **No.** `C:\Program Files\Eclipse Adoptium\jdk-25.0.3.9-hotspot\lib\fonts` does not exist. | directory check |
| Does the host have fonts? | **Yes, and they are the host's.** `C:\Windows\Fonts` holds 549 files. | directory count |

**Conclusion.** `java.desktop` is *available* to a tier module with no Gradle dependency
and no contract change — but it must be **forbidden** in tier modules.

The reason is measurement, not availability. `java.awt.font.Font` resolves a font from the
host: `Font.createFont(int, File)` and `Font.createFont(int, InputStream)` (both verified
present by `javap`) need a physical font file, and the JDK has none to give. Any
`TextLayout`-based measurement therefore depends on whichever fonts the machine has
installed, and the resulting line breaks and page counts differ between machines for the
same document. That is exactly the failure mode `kotlin/CONTRACTS.md:284-293` (§11
determinism) and `kotlin/CONTRACTS.md:308` (§12, "tests must be deterministic") exist to
prevent, and it would be invisible: the tier would pass on the author's machine.

The client is unaffected. `:desktop` is the named exemption
(`kotlin/CONTRACTS.md:38-58`) and already imports `java.awt`/`javax.swing`
(`kotlin/desktop/src/main/kotlin/lmeditor/desktop/ui/FileDialogs.kt:3-7`); Compose and its
transitives draw text with real fonts. The rule that follows from the exemption is the
one already written at `kotlin/CONTRACTS.md:53-56`: the exemption is granted for the
view, never for the domain. The tier decides *where* every line goes; the client decides
*how* it looks.

Two further points on `java.desktop`, stated as risk rather than fact: whether font
loading works on a host with no fonts installed and no display was **not verified here**
(it is not needed under the recommendation, but it must be checked before anyone
reintroduces `java.desktop` into the tier); and the JDK's text shaping is versioned with
the JDK, so a `TextLayout`-based layout would change between JDK 17 and JDK 25 with no
source change — the contract pins the *bytecode* target (`kotlin/gradle.properties:9-11`,
`kotlin/build.gradle.kts:34-45`) but not the runtime JDK.

---

## 4. Text measurement

### 4.1 Option A — measure with `java.awt.font` (rejected)

APIs available, all in `java.desktop`, all verified by `javap`:
`java.awt.Font` (`getStringBounds(String, FontRenderContext)`,
`createFont(int, File)`, `deriveFont(float)`), `java.awt.font.FontRenderContext`
(`FontRenderContext(AffineTransform, boolean, boolean)`), `java.awt.font.TextLayout`
(`TextLayout(String, Font, FontRenderContext)`, `getAdvance()`, `getVisibleAdvance()`),
`java.awt.font.LineBreakMeasurer` (`LineBreakMeasurer(AttributedCharacterIterator,
FontRenderContext)`, `nextOffset(float)`, `nextLayout(float)`), `java.awt.font.GlyphVector`,
`java.awt.font.LineMetrics`, `java.awt.font.TextAttribute`, `java.awt.geom.AffineTransform`.

Rejected for one reason: the inputs are the host's fonts. There is no third-party
dependency to add, but there is also no way to make the output a function of the
repository alone.

### 4.2 Option B — `java.awt.font` plus a font bundled in the tier (viable, second choice)

Deterministic only if the same font file *and* the same JDK build are used. The JDK
version is not pinned by the contract, so the layout can shift under a JDK upgrade with no
source change, and the tier would carry a font *program* with its full licence
obligation. This is a reasonable plan for the **client** (which already draws with real
fonts) and an unnecessary risk for the **tier**.

### 4.3 Option C — bundled advance-width metrics plus the JDK's own break iterator (recommended)

The tier's measurement is split into two halves, and neither needs a font file:

- **Advance widths** come from a versioned table committed to the repository — per glyph
  and per face (regular/bold/italic), in font units scaled to points. It is data, not a
  program: no shaping, no rasterizer, no font loader.
- **Break opportunities** come from `java.text.BreakIterator.getLineInstance()` —
  **module `java.base`**, verified by `javap --module java.base java.text.BreakIterator`
  — which supplies the Unicode line-break opportunity set without any font and without
  `java.desktop`.
- **The arithmetic** — greedy first-fit line breaking, then page filling — is the tier's
  own code, over integers and fixed-point widths, so the same input yields the same
  output.

This keeps the tier inside `java.base` plus `java.xml` plus the metrics file: the same
JDK footprint the contract's own API table describes (`kotlin/CONTRACTS.md:23-32`).

### 4.4 Which option is deterministic, and what that means for the contract

Option C is the only option whose output is a function of *(document, metric table
version, JDK break data)* and of nothing else. Options A and B are functions of the
host's installed fonts and of the JDK build.

- **§11 determininism** (`kotlin/CONTRACTS.md:284-293`) is written against computed
  identifiers, but the property it protects — an observable output is a function of the
  input — is the same property a page break has. A page break that moves when a font is
  installed is an observable output that is not a function of the input.
- **§12** (`kotlin/CONTRACTS.md:308`) requires deterministic tests. A golden test of the
  form "this document paginates to seven pages" can only pass on every machine if the
  advance widths live in the repository. A test that compares against the host's fonts is
  a test that fails on someone else's laptop.
- **The parity suite** (§10) needs the same property: a fixture whose page count and
  line breaks are fixed by the repository, not by the machine.

One residual is honest to name: the JDK's break data is versioned with the JDK, so a JDK
upgrade can alter break opportunities even with fixed metrics. The mitigation is cheap
and is part of the recommendation: the layout profile records the JDK baseline, and the
golden tests fail loudly when the break set changes, turning a JDK upgrade into a
recorded layout-profile change rather than a silent one.

### 4.5 What the metric model cannot do

The metric model carries advance widths, nothing else. It therefore cannot carry
kerning, OpenType features, ligatures, complex-script shaping (Arabic, Devanagari,
CJK), micro-typography, or justification behaviour that depends on glyph shapes. This is
a **layout-fidelity limit**, stated about the LMeditor layout model and the OOXML
format, not about any other renderer's output: the OOXML format specifies the page box
(`w:pgSz`, `w:pgMar`, in twips) and the section structure, and it does not specify the
intra-paragraph break positions — the positions are a layout engine's own product.

---

## 5. Page geometry

Almost none of this needs a JDK API; it is integer arithmetic on twips (1/1440 inch)
scaled to points (1/72 inch) and to millimetres.

| Requirement | What is needed | Available today |
| :--- | :--- | :--- |
| Page size | The page box in twips | Modelled: `WordModel.kt:235-238`; read: `WordReader.kt:674-679`; written: `WordWriter.kt:982` |
| Orientation | A swap of the page box's axes | Already derived by the reader when `w:orient` is absent (`WordReader.kt:681`); written as `w:orient="landscape"` (`WordWriter.kt:959`) |
| Margins | The four inset values | Modelled `WordModel.kt:239-246`; read `WordReader.kt:683-693`; written `WordWriter.kt:983-985` |
| Text area | Page box minus margins, minus the header/footer bands | Not computed anywhere; the header/footer distances are hard-coded to 720 twips (`WordWriter.kt:985`) |
| Section breaks | A section per range of blocks, each with its own page box | The model holds a list (`WordModel.kt:687`) and an end block id (`WordModel.kt:250`), but the writer writes only the first section (`WordWriter.kt:952-958`) |
| Headers/footers | A band above/below the text area, chosen by `default`/`even`/`first` | Refs modelled (`WordModel.kt:217-224`) and written (`WordWriter.kt:963-968`); the *placement* is new work |
| Page numbers | A `PAGE` field resolved at layout time | Not modelled: the reader discards `instrText`/`fldChar` (`WordReader.kt:1454`) |

**What "the same documented layout rules" (W2.06) would have to mean.** W2.06
(`docs/product-replacement-roadmap.md:69`) requires the preview, the print path and the
PDF export to use the same rules. It does not say "the same rules as another renderer",
and the roadmap supplies no such definition. Under this recommendation the rules are the
**LMeditor layout profile**: one versioned document specifying the metric set, the break
opportunity set, the line-breaking strategy, the widow/orphan and keep-with-next rules,
the table and footnote rules, and the JDK baseline. Preview, print and PDF export all
consume that one profile, so "the same documented layout rules" is satisfiable and
testable. It is *not* satisfiable as "the same page breaks as some other renderer",
because the OOXML format does not specify intra-paragraph break positions at all.

---

## 6. Line breaking and pagination arithmetic

The components, and what each costs. Estimates are engineer-weeks for one engineer
already familiar with this tier.

| # | Component | Estimate | Assumptions behind the estimate |
| :--- | :--- | :--- | :--- |
| 1 | Metric set: advance-width table for one Latin family (regular/bold/italic), the extraction script, the licence file | 0.5–1.0 | One family; the table is generated **once, offline**, by a development-time script and committed; the script is not a runtime dependency and never ships. Licensing the source font is assumed to be one reviewable decision. |
| 2 | `TypographicMetrics` reader in the tier | 0.5–1.0 | The table is a small resource parsed once per document; no binary format work. |
| 3 | Break-opportunity layer over `java.text.BreakIterator` | 0.5–1.0 | `BreakIterator` (module `java.base`) is used as-is; no re-implementation of UAX #14. If the JDK-version dependency is removed by implementing the break rules in the tier, add **1.5–2.5**. |
| 4 | Greedy first-fit line breaker over the metric table | 1.0–1.5 | Latin, LTR, no complex shaping; break opportunities come from #3; width arithmetic is fixed-point integer. |
| 5 | Total-fit (Knuth-Plass-style) breaking | 1.5–2.5 *additional* | Deferred. Greedy is the documented default of the profile; total-fit is an opt-in second strategy. |
| 6 | Paragraph and style model feeding the breaker: spacing before/after, indents, alignment, tab stops | 1.5–2.5 | **Shared with W1.03** (`docs/product-replacement-roadmap.md:55`); if W1.03 lands first, the layout-only share is roughly half. |
| 7 | Page box, sections, section breaks, orientation, multi-section writing | 1.5–2.5 | The model already carries the data (`WordModel.kt:233-263`); the work is the writer change at `WordWriter.kt:946-988` and the reader's multi-section path. |
| 8 | Page filler (greedy), widow/orphan control, keep-with-next/previous | 2.0–3.0 | A single text column per section; no frames; widows/orphans are a paragraph-line-count rule, not a typographic one. |
| 9 | Tables in the flow: row splitting, repeating header rows (`WordModel.kt:493-494`), a row taller than the text area | 2.0–3.0 | The grid placement already exists (`WordTableLayout.kt:70`); the flow rules are new. |
| 10 | Inline and anchored objects; an image larger than the text area | 2.0–3.0 | Shares work with W2.03 (`docs/product-replacement-roadmap.md:66`); the layout half only. |
| 11 | Footnotes/endnotes: a footnote area that grows upward and reflows the page | 2.0–3.0 | Footnote and endnote references are modelled (`WordModel.kt:111-114`) and the footnote itself (`WordModel.kt:179-192`); the *area* is new. |
| 12 | Header/footer bands and the `PAGE`/`NUMPAGES` fields | 1.5–2.5 | The reader must stop discarding field instructions (`WordReader.kt:1454`) and the model must carry a field kind. |
| 13 | Two-pass resolution for TOC and page-number cross-references | 1.5–2.0 | A page number is a property of the page, so a TOC needs a second pass after pagination. |
| 14 | PDF writer: page tree, content streams, fonts | 3.0–4.0 | The tier already has a hand-written PDF object model to mirror (`kotlin/pdf/src/main/kotlin/lmeditor/pdf/PdfObject.kt`, `PdfXref.kt`); no library is needed. See §8 for the font constraint. |
| 15 | Layout golden tests and layout corpus fixtures | 1.5–2.0 | The corpus work in `docs/replacement/m0-blueprint.md:180` supplies the documents; the layout expectations are new. |

**Totals.**

- **Minimal tier profile** (components 1–4, 7, 8, 12, 15 — no footnotes, no tables in
  the flow, no anchored objects, no PDF writer): **9–12 engineer-weeks**.
- **Full W2.01 scope** (components 1–15): **21–32 engineer-weeks.**
- **Client page rendering** (`:desktop`, outside the tier rule): **2–3 engineer-weeks** —
  a page canvas that draws the tier's boxes, maps selection to the laid-out lines and
  shows the preview.

**Assumptions common to all of the estimates:** one Latin text family with metrics only;
LTR only; one text column per section; no vertical text, no multi-column sections and no
Word-style frames; the tier computes geometry and the client draws; the metric table is
generated offline and the generator is not shipped; a single engineer, not a team, and no
parallelism across components 1–4 (which are strictly sequential).

---

## 7. Output: PDF export (W2.06)

- The tier's `:pdf` module is a reader (§2.4). A writer is new work, and it needs no
  library: a PDF is a byte format, and the tier already has an object/xref model to
  mirror.
- Because the layout is LMeditor's own, the PDF **draws what the tier laid out** — one
  text-showing operator per line at a computed position — instead of re-flowing. This is
  what makes preview and export agree, which is the substance of W2.06.
- **The font is the constraint.** A PDF may reference the standard 14 fonts by name, with
  no embedding and therefore no font-program licence obligation — but then the document's
  fonts are limited to that set, and the metrics must be the standard-14 metrics.
  Otherwise the writer must embed a font program, which is a shipped asset with its full
  licence. This is the second, independent reason the metric-set choice in §6 #1 is a
  decision rather than an implementation detail: **the metric table must be the metric set
  the PDF writer can use**, or the exported PDF silently disagrees with the preview.

---

## 8. The contract decision

### 8.1 The recommendation, and the exact proposed change

The amendment below is proposed for the architecture lead to place in
`kotlin/CONTRACTS.md`. It is **not** applied here
(`docs/replacement/m0-blueprint.md:251-257`). It is written in that document's own voice.

> ### 1.2 The one asset exception: bundled typographic metrics
>
> A tier module may ship **typographic metric assets** — a versioned table of glyph
> advance widths, and the licence notice that travels with it — as resources of the
> module. This is the only kind of non-code file a tier module may carry.
>
> - The metrics are **data, not a dependency**. They are declared in the module's own
>   source tree, never in `build.gradle.kts`, and the runtime dependency set of the tier
>   stays empty. The tier's JDK footprint is unchanged: `java.base` and `java.xml`.
> - The table names, in its header, the font it was derived from, the licence that
>   governs it, the tool and version that produced it, and the layout profile version it
>   belongs to. A table without that header is a defect, not a default.
> - **Tier modules must not measure text with `java.desktop`.** `java.awt.font` resolves a
>   font from the host, so the same document would break into different lines on
>   different machines, and the tier's output would stop being a function of its input.
>   The tier's advance widths come from the bundled table; the client draws what the
>   tier lays out. This is §1.1's rule applied to layout: the exemption is granted for
>   the view, never for the domain.
> - `backend/tests/test_kotlin_dependencies.py` must assert both halves: that the tier
>   jar carries no third-party class, **and** that the only non-class entries it carries
>   are approved metric assets with their licence header.
>
> The API table in §1 gains one row:
>
> | Text metrics | `lmeditor.word.TypographicMetrics` (bundled table) — **not** `java.awt.font` |

**Why the amendment is needed and not optional.** Two reasons, both concrete:

1. §1's API table is the baseline of what "JDK only" means for this tier
   (`kotlin/CONTRACTS.md:23-32`). Pagination has no entry in it, and the source of the
   advance widths is neither a JDK API nor a dependency — it is a shipped data file with a
   licence obligation. The roadmap forbids "silently add[ing] dependencies"
   (`docs/product-replacement-roadmap.md:186`); a metric table that arrived without a
   contract clause would have been added silently.
2. **The mechanical check would not catch it.** `test_the_shipped_tier_jar_carries_no_third_party_class`
   inspects only `.class` entries (`backend/tests/test_kotlin_dependencies.py:236` and
   `:246-253`), so a metric asset would pass the enforcement test unnoticed. Without the
   §1.2 clause and the matching assertion, the tier would grow a shipped data file with
   no contract and no test.

### 8.2 What a reviewer should check

- The metric table's header names the source font, the licence, the extraction tool and
  version, and the layout profile version.
- No tier module imports `java.awt`, `java.awt.font`, `javax.swing` or
  `java.awt.geom` — a grep-able rule, and the reason the client keeps its exemption.
- The tier jar's only non-class entries are the approved metric assets.
- The layout golden tests pass on a machine with **no fonts installed** — the property
  that distinguishes this design from the `java.awt` one.
- The redistribution terms of whatever metric set is bundled actually permit it, and the
  notice travels with every distribution of the tier jar.

### 8.3 Maintenance cost

- The metric table is regenerated only when the font or the extractor changes; each
  regeneration must bump the layout profile version, because page breaks are observable
  output.
- A JDK upgrade can change `java.text.BreakIterator`'s line-break opportunities. The
  golden tests are written to fail loudly on such a change, turning it into a recorded
  layout-profile revision instead of a silent repagination.
- The tier jar grows by one small data file and one licence file. The licence file must
  be carried by every distribution of the jar — an operational obligation, not a
  one-off.

### 8.4 Licence and redistribution obligation

- If the metric set is derived from an SIL Open Font Licence family (for example a
  Liberation or Noto release), the OFL governs the redistributed derived data: the
  licence text and the reserved-font-name rules must travel with it. The metrics should
  be treated as covered by the font's licence.
- If the metric set is the Adobe Core 14 AFM metrics, the redistribution terms of those
  AFM files must be confirmed and recorded. **This was not verified here** — no network
  access was used — and it is a reviewer task before the metric set is chosen.
- If a font *program* is shipped later (only needed for embedded PDF text, §7), that
  font's own licence applies in full, and shipping it is a separate decision from
  shipping metrics.

### 8.5 Why not (a) and why not (c)

- **Not (a).** "No contract change" is not available: without a clause admitting bundled
  metrics and forbidding host-font measurement, the tier has no permitted source of
  advance widths, and the enforcement test would not notice the gap.
- **Not (c) alone.** The "LMeditor layout profile" is a *scope* decision, and this
  document adopts it (§5, §10.4) — but it does not by itself resolve the dependency
  question, because the profile still needs metrics from somewhere. The profile is the
  consequence of the recommendation; the amendment is the decision.

---

## 9. Consequences

### 9.1 Unblocked

- **W2.01** — real page layout: page size, margins, orientation, breaks, sections,
  headers/footers and page numbers (`docs/product-replacement-roadmap.md:64`). This is
  the requirement the spike exists for.
- **W2.02** — table layout prerequisite removed: widths, repeating header rows and rows
  that do not fit (`docs/product-replacement-roadmap.md:65`). The *editing* half of
  W2.02 remains its own work.
- **W2.03** — the placement and wrapping model now has a home
  (`docs/product-replacement-roadmap.md:66`). Image editing remains separate.
- **W2.04** — footnotes/endnotes need page space and page-number cross-references need
  pagination (`docs/product-replacement-roadmap.md:67`).
- **W2.05** — a table of contents with page numbers needs a two-pass layout
  (`docs/product-replacement-roadmap.md:68`).
- **W2.08** — the DOCX feature matrix can state real layout limits instead of "unknown"
  (`docs/product-replacement-roadmap.md:71`).
- **W1.02** — the persistent block/run model is the layout engine's input
  (`docs/product-replacement-roadmap.md:54`).
- **W1.03** — font settings, spacing, indentation and tab stops acquire a measurable
  meaning through the metric table (`docs/product-replacement-roadmap.md:55`).
- **R.01** — "difficult layouts" in the compatibility corpus become testable fixtures
  (`docs/product-replacement-roadmap.md:204`).
- **R.05** — the 100-page mixed-content benchmark becomes measurable
  (`docs/product-replacement-roadmap.md:208`).

### 9.2 Unblocked under a stated interpretation

- **W2.06** — print preview, printing and PDF export "using the same documented layout
  rules" (`docs/product-replacement-roadmap.md:69`) is satisfiable **if** those rules are
  the LMeditor layout profile of §5. If the lead means another renderer's rules, W2.06
  stays blocked and no engineering inside this tier closes it.
- **R.02** — comparing exported semantic structure and rendered pages, and validating
  with actual Word where available (`docs/product-replacement-roadmap.md:205`): the
  semantic half and the manual Word validation are unaffected; the "rendered pages"
  half is comparable only against the LMeditor profile.
- **§7's end-to-end gate** — "export DOCX/PDF"
  (`docs/product-replacement-roadmap.md:154`) depends on W2.06 and inherits its
  interpretation.

### 9.3 Blocked

- **Without** the §1.2 amendment, **W2.01, W2.02, W2.03, W2.04, W2.05 and W2.06** are
  blocked, together with the W1.02 and W1.03 layout prerequisites: there is no permitted
  source of advance widths.
- **With** the amendment, nothing in W1 or W2 is blocked outright by the dependency
  question. What remains is ordinary implementation work.

### 9.4 Permanently out of scope under the LMeditor layout profile

These are scope statements, and they must be written into the feature inventory and the
layout profile rather than left implicit:

- **Word-identical pagination.** The OOXML format specifies the page box and the section
  structure, not the intra-paragraph break positions; the positions are an engine's own
  product. No tier can promise a page-for-page match to another renderer.
- **Typographic fidelity the metric model does not carry**: kerning, OpenType features,
  ligatures, complex-script shaping (Arabic, Devanagari, CJK) and micro-typography
  (§4.5). Every document whose text is outside the bundled metric set is laid out with
  fallback widths, and that must be stated, not hidden.
- **PDF output with an arbitrary embedded font** — only the standard 14 referenced by
  name, or a shipped OFL family (§7).
- **Multi-column section layout, vertical text, Word-style frames and text boxes, and
  text wrap around arbitrary shapes.**
- **The `.doc` conversion and "unsupported layout features" questions of W3.06**
  (`docs/product-replacement-roadmap.md:82`) stay exactly as the roadmap wrote them; the
  profile lets the inventory answer them concretely rather than changing their scope.

### 9.5 The open decision this spike closes, and the one it does not

- **Closed:** roadmap §8's dependency decision for layout
  (`docs/product-replacement-roadmap.md:186`) and the blueprint's open decision 2
  (`docs/replacement/m0-blueprint.md:268`) — bundled font files are treated as an asset
  dependency and therefore need a named contract clause, proposed in §8.1.
- **Not closed, and explicitly out of this spike's scope:** the roadmap's parallel
  dependency questions for OCR, sync, media and persistent indexing technology
  (`docs/product-replacement-roadmap.md:186`), and the blueprint's open decisions 3, 4
  and 5 (`docs/replacement/m0-blueprint.md:269-271`).

---

## 10. Assumptions and verification gaps

**Assumptions**

1. One Latin text family with metrics only is enough for the first layout profile.
2. LTR only; no complex-script shaping; no vertical text.
3. The tier computes geometry; the client draws it. The client and the tier use the same
   metric set, so the client's rendering agrees with the tier's breaks.
4. Estimates in §6 are for one engineer already familiar with the tier.
5. The metric set's redistribution terms are resolvable by review (one decision, one
   licence notice).
6. W1.03's style work lands early enough to be shared with §6 #6 rather than duplicated.

**Verification gaps — stated as gaps, not as claims**

- The exact redistribution terms of the Adobe Core 14 AFM metrics were **not verified**
  (no network access in this spike).
- The headless behaviour of `java.awt` font loading on a host with no fonts is **not
  verified**; it is not needed under the recommendation but must be checked before anyone
  reintroduces `java.desktop` into the tier.
- Whether the JDK's `BreakIterator` line-break data differs between JDK 17 and JDK 25 is
  **not verified**; the golden tests will show it, and the layout profile must record the
  baseline either way.
- The desktop application was **not run** for this baseline, and no claim in this
  document depends on it.

---

## 11. Effort summary

| Scope | Estimate | Notes |
| :--- | :--- | :--- |
| Tier, minimal layout profile | 9–12 engineer-weeks | Metrics, break opportunities, greedy breaking, page box and sections, page filler with widow/orphan and keep-with-next, header/footer bands and `PAGE` fields, golden tests |
| Tier, full W2.01 scope | 21–32 engineer-weeks | Adds total-fit breaking (optional, +1.5–2.5), tables in the flow, inline and anchored objects, footnotes, two-pass field/TOC resolution, and the PDF writer |
| Client (`:desktop`, exempt module) | 2–3 engineer-weeks | Page canvas, selection mapping, preview |
| Contract work | < 1 day | The §8.1 amendment and the matching assertion in `backend/tests/test_kotlin_dependencies.py` |

Every estimate above is conditional on the §8.1 amendment being adopted. Without it the
tier work is not permitted to begin.
