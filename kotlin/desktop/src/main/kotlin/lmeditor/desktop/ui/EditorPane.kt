package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.LinkInteractionListener
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.awt.HeadlessException
import java.awt.Toolkit
import java.awt.datatransfer.StringSelection
import lmeditor.core.Claim
import lmeditor.core.EntailmentStatus
import lmeditor.core.SectionDraft
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState
import lmeditor.desktop.render.BlockKind
import lmeditor.desktop.render.Emphasis
import lmeditor.desktop.render.InlineSpan
import lmeditor.desktop.render.ManuscriptBlock
import lmeditor.desktop.render.ManuscriptBlocks

/**
 * The manuscript.
 *
 * The editor shows the document the backend produced, not the concatenation
 * of the frames that arrived while it was being written: the text on screen
 * is the text that will be exported.
 *
 * A citation marker is drawn as a chip, and clicking it opens the passage it
 * names. The chip is coloured by the verdict the verifier reached on the
 * claim that cites it, so a reader can see at a glance which parts of a
 * document rest on evidence and which do not.
 */
@Composable
fun EditorPane(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
    onCitationSelected: () -> Unit = {},
) {
    val verdicts = remember(state.claims) { chunkVerdicts(state) }
    val palette = verdictPalette()
    val scrollState = rememberScrollState()
    var useSerif by rememberSaveable { mutableStateOf(true) }
    var readingScale by rememberSaveable { mutableStateOf(100) }
    val manuscriptTypography = MaterialTheme.typography.copy(
        bodyLarge = MaterialTheme.typography.bodyLarge.copy(
            fontFamily = if (useSerif) MaterialTheme.typography.bodyLarge.fontFamily
                else MaterialTheme.typography.bodyMedium.fontFamily,
            fontSize = (16f * readingScale / 100).sp,
            lineHeight = (27f * readingScale / 100).sp,
        ),
    )

    // A draft that is still being written follows itself down the page, so the
    // operator reads the newest text instead of the text the run began with.
    val liveLength = state.liveText.values.sumOf { it.length }
    LaunchedEffect(liveLength) {
        if (liveLength > 0) {
            // The new text has to be laid out before the page knows its height.
            withFrameNanos { }
            scrollState.animateScrollTo(scrollState.maxValue)
        }
    }

    Column(modifier = modifier.fillMaxSize().background(MaterialTheme.colorScheme.surfaceVariant)) {
        Row(
            modifier = Modifier.fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("Reading view", style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { useSerif = !useSerif }) {
                Text(if (useSerif) "Use sans serif" else "Use serif")
            }
            TextButton(onClick = { copyManuscript(state, controller) }) {
                Text("Copy")
            }
            TextButton(onClick = { readingScale = (readingScale - 10).coerceIn(80, 150) },
                modifier = Modifier.semantics { contentDescription = "Decrease reading text size" },
                enabled = readingScale > 80) { Text("A−") }
            Text("$readingScale%", style = MaterialTheme.typography.labelMedium)
            TextButton(onClick = { readingScale = (readingScale + 10).coerceIn(80, 150) },
                modifier = Modifier.semantics { contentDescription = "Increase reading text size" },
                enabled = readingScale < 150) { Text("A+") }
            TextButton(onClick = { readingScale = 100; useSerif = true },
                colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.onSurfaceVariant)) {
                Text("Reset view")
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
        BoxWithConstraints(modifier = Modifier.weight(1f).fillMaxWidth()) {
            val outerMargin = if (maxWidth < 500.dp) 12.dp else 28.dp
            val paperMargin = if (maxWidth < 500.dp) 24.dp else 56.dp
            Column(
                modifier = Modifier.fillMaxSize().verticalScroll(scrollState)
                    .padding(horizontal = outerMargin, vertical = 16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                DocumentRuler(Modifier.widthIn(max = 800.dp).fillMaxWidth(), paperMargin)
                Spacer(Modifier.height(12.dp))
                Column(
                    modifier = Modifier.widthIn(max = 800.dp).fillMaxWidth()
                        .shadow(3.dp, RoundedCornerShape(1.dp))
                        .background(MaterialTheme.colorScheme.surface)
                        .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.22f))
                        .heightIn(min = 920.dp)
                        .padding(horizontal = paperMargin, vertical = 48.dp),
                ) {
                    Text("MANUSCRIPT", style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        letterSpacing = 2.sp)
                    Spacer(Modifier.height(18.dp))
                    Text(state.topic.ifBlank { "Untitled document" },
                        style = MaterialTheme.typography.headlineMedium)
                    Spacer(Modifier.height(30.dp))
                    MaterialTheme(typography = manuscriptTypography) {
                        if (state.sections.isEmpty() && state.liveText.isEmpty()) {
                            Text("Your next chapter starts here.",
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text("Add your sources, enter a topic, and choose Write to begin.",
                                modifier = Modifier.padding(top = 10.dp),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        for (section in state.sections) {
                            SectionView(
                                section = section,
                                verdicts = verdicts,
                                palette = palette,
                                claims = state.claims,
                                controller = controller,
                                onCitationSelected = onCitationSelected,
                            )
                            Spacer(Modifier.height(28.dp))
                        }
                        for ((sectionId, text) in state.liveText) {
                            if (state.sections.none { it.sectionId == sectionId }) {
                                Text(sectionId, style = MaterialTheme.typography.titleMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Text(text, style = MaterialTheme.typography.bodyLarge,
                                    modifier = Modifier.padding(top = 8.dp, bottom = 28.dp))
                            }
                        }
                    }
                }
                Spacer(Modifier.height(18.dp))
                Text("Document preview · Reading settings apply to this view",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

/** One finished section, with its blocks. */
@Composable
private fun SectionView(
    section: SectionDraft,
    verdicts: Map<String, EntailmentStatus>,
    palette: VerdictPalette,
    claims: List<Claim>,
    controller: StudioController,
    modifier: Modifier = Modifier,
    onCitationSelected: () -> Unit = {},
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Text(section.title, style = MaterialTheme.typography.titleLarge)
        val critique = section.critique
        if (!critique.isNullOrBlank()) {
            Text(
                critique,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.tertiary,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        for (block in ManuscriptBlocks.parse(section.content)) {
            BlockView(
                block = block,
                verdicts = verdicts,
                palette = palette,
                claims = claims,
                controller = controller,
                onCitationSelected = onCitationSelected,
            )
        }
    }
}

/** One block of a section. */
@Composable
private fun BlockView(
    block: ManuscriptBlock,
    verdicts: Map<String, EntailmentStatus>,
    palette: VerdictPalette,
    claims: List<Claim>,
    controller: StudioController,
    modifier: Modifier = Modifier,
    onCitationSelected: () -> Unit = {},
) {
    val openCitation: (String) -> Unit = { chunkId ->
        controller.selectChunk(chunkId)
        claims.firstOrNull { chunkId in it.citedChunkIds }?.let { controller.selectClaim(it.claimId) }
        onCitationSelected()
    }
    val annotated = remember(block, verdicts, palette, claims) {
        block.annotated(verdicts, palette, openCitation)
    }
    // The citations of the block are named in words as well as drawn in
    // colour, so what a chip means is read and not only seen.
    val description = remember(block, verdicts, claims) {
        block.citationDescription(verdicts, claims)
    }
    val spoken = if (description == null) {
        Modifier
    } else {
        Modifier.semantics { contentDescription = description }
    }

    when (block.kind) {
        BlockKind.HEADING -> Text(
            text = annotated,
            style = MaterialTheme.typography.titleMedium,
            modifier = modifier.padding(top = 8.dp).then(spoken),
        )

        BlockKind.LIST_ITEM -> Row(
            modifier = modifier.padding(top = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text("·", style = MaterialTheme.typography.bodyLarge)
            Text(
                text = annotated,
                style = MaterialTheme.typography.bodyLarge,
                modifier = spoken.weight(1f),
            )
        }

        BlockKind.QUOTE -> Row(
            modifier = modifier.padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "│",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.outline,
            )
            Text(
                text = annotated,
                style = MaterialTheme.typography.bodyLarge,
                modifier = spoken.weight(1f),
            )
        }

        BlockKind.CODE -> Text(
            text = annotated,
            style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
            modifier = modifier.padding(top = 4.dp).then(spoken),
        )

        BlockKind.PARAGRAPH -> Text(
            text = annotated,
            style = MaterialTheme.typography.bodyLarge,
            modifier = modifier.padding(top = 6.dp).then(spoken),
        )
    }
}

/**
 * How strongly a verdict tints the chip behind its marker.
 *
 * The chip is read as text against that tint, so the tint is part of what a
 * verdict has to be legible against.
 */
internal const val CHIP_TINT_ALPHA = 0.14f

/**
 * How severe a verdict is, worst last.
 *
 * The order is written out in terms of the enum's names and is never read
 * from the declaration: the declaration order is the wire contract, and
 * reading it as a severity ranking is what made a passage that is also
 * contradicted render as entailed.
 */
internal fun severityOf(status: EntailmentStatus): Int = when (status) {
    EntailmentStatus.ENTAILED -> 0
    EntailmentStatus.NEUTRAL -> 1
    EntailmentStatus.UNGROUNDED -> 2
    EntailmentStatus.CONTRADICTED -> 3
}

/**
 * The worst verdict among those given, or null when there is none.
 *
 * A passage that is cited by an entailed claim and by a contradicted claim is
 * a contradicted passage: the worst verdict wins, whatever order the verdicts
 * arrived in.
 */
internal fun worstVerdict(statuses: Iterable<EntailmentStatus>): EntailmentStatus? =
    statuses.maxByOrNull { severityOf(it) }

/**
 * The mark that carries a verdict without colour.
 *
 * A verdict is a tint on the page; a reader who cannot tell the tints apart
 * still reads the mark: clean, underlined, marked and struck through, struck
 * through, or written in italics while it is still unverified.
 */
internal data class VerdictMark(
    val decoration: TextDecoration?,
    val italic: Boolean = false,
)

/**
 * The mark one verdict is drawn with.
 */
internal fun verdictMark(status: EntailmentStatus?): VerdictMark = when (status) {
    EntailmentStatus.ENTAILED -> VerdictMark(null)
    EntailmentStatus.NEUTRAL -> VerdictMark(TextDecoration.Underline)
    EntailmentStatus.UNGROUNDED ->
        VerdictMark(TextDecoration.combine(listOf(TextDecoration.Underline, TextDecoration.LineThrough)))
    EntailmentStatus.CONTRADICTED -> VerdictMark(TextDecoration.LineThrough)
    null -> VerdictMark(null, italic = true)
}

/**
 * Build the styled text of one block.
 *
 * A citation is drawn as a chip: a small, tinted, bold run that cannot be
 * mistaken for prose. The chip is a real link, so it can be reached with the
 * keyboard and opened without a mouse.
 */
private fun ManuscriptBlock.annotated(
    verdicts: Map<String, EntailmentStatus>,
    palette: VerdictPalette,
    openCitation: (String) -> Unit,
): AnnotatedString =
    buildAnnotatedString {
        for (span in spans) {
            when (span) {
                is InlineSpan.Text -> when (span.style) {
                    Emphasis.PLAIN -> append(span.text)
                    Emphasis.BOLD -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(span.text) }
                    Emphasis.ITALIC -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { append(span.text) }
                    Emphasis.CODE -> withStyle(
                        SpanStyle(
                            fontFamily = FontFamily.Monospace,
                            background = Color(0x22000000),
                        ),
                    ) { append(span.text) }
                }

                is InlineSpan.Citation -> {
                    val worst = worstVerdict(span.chunkIds.mapNotNull { chunkId -> verdicts[chunkId] })
                    val color = palette.of(worst)
                    val mark = verdictMark(worst)
                    val chunkId = span.chunkIds.firstOrNull()
                    val chip = SpanStyle(
                        color = color,
                        fontWeight = FontWeight.SemiBold,
                        background = color.copy(alpha = CHIP_TINT_ALPHA),
                        textDecoration = mark.decoration,
                        fontStyle = if (mark.italic) FontStyle.Italic else null,
                    )
                    if (chunkId == null) {
                        withStyle(chip) { append(span.text) }
                    } else {
                        withStyle(chip) {
                            withLink(
                                LinkAnnotation.Clickable(
                                    tag = chunkId,
                                    styles = TextLinkStyles(
                                        style = chip,
                                        focusedStyle = SpanStyle(
                                            textDecoration = TextDecoration.combine(
                                                listOfNotNull(mark.decoration, TextDecoration.Underline),
                                            ),
                                        ),
                                    ),
                                    linkInteractionListener = LinkInteractionListener {
                                        openCitation(chunkId)
                                    },
                                ),
                            ) {
                                append(span.text)
                            }
                        }
                    }
                }
            }
        }
    }

/**
 * What a block's citations mean, in words.
 *
 * The verdict a citation carries is a tint and a mark on the page; this is the
 * same fact written out, so that it is read as well as seen. Null when the
 * block cites nothing.
 */
private fun ManuscriptBlock.citationDescription(
    verdicts: Map<String, EntailmentStatus>,
    claims: List<Claim>,
): String? {
    val citations = spans.filterIsInstance<InlineSpan.Citation>()
    if (citations.isEmpty()) return null
    val said = citations.map { span ->
        val chunkId = span.chunkIds.firstOrNull().orEmpty()
        val claim = claims.firstOrNull { it.citedChunkIds.any { cited -> cited == chunkId } }
        buildString {
            append(chunkId)
            append(", ")
            append(verdicts[chunkId]?.wire ?: "not verified")
            if (claim != null) {
                append(", claim: ")
                append(claim.text)
            }
        }
    }
    val prose = spans.filterIsInstance<InlineSpan.Text>().joinToString("") { it.text }
    return (prose + " Citations: " + said.joinToString(separator = "; ")).trim()
}

/**
 * The verdict on each passage, taken from the claims that cite it.
 *
 * When several claims cite the same passage, the worst verdict wins: one
 * unsupported claim resting on a passage is enough to make that passage
 * something the reader must check.
 */
internal fun chunkVerdicts(state: StudioState): Map<String, EntailmentStatus> {
    val verdicts = LinkedHashMap<String, EntailmentStatus>()
    for (claim in state.claims) {
        for (chunkId in claim.citedChunkIds) {
            val current = verdicts[chunkId]
            if (current == null || severityOf(claim.entailmentStatus) > severityOf(current)) {
                verdicts[chunkId] = claim.entailmentStatus
            }
        }
    }
    return verdicts
}

/**
 * Put the manuscript on the clipboard, as the text that will be exported.
 *
 * A machine with no clipboard is told so rather than left to wonder why
 * nothing was copied.
 */
private fun copyManuscript(state: StudioState, controller: StudioController) {
    val text = state.sections.joinToString("\n\n") { it.content }
    if (text.isBlank()) {
        controller.notice("There is no manuscript to copy yet.")
        return
    }
    try {
        Toolkit.getDefaultToolkit().systemClipboard.setContents(StringSelection(text), null)
        controller.notice("The manuscript was copied to the clipboard.")
    } catch (cause: HeadlessException) {
        controller.notice("This machine has no clipboard to copy to.")
    } catch (cause: IllegalStateException) {
        controller.notice("The clipboard is not available right now.")
    }
}
