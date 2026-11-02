package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.StudioController

/**
 * The studio: the whole application in one window.
 *
 * A document-first workspace with optional research and review panels.
 * Narrow windows show one inspector at a time so the manuscript retains
 * a comfortable reading measure. Citation selection reveals its evidence.
 */
@Composable
fun StudioScreen(controller: StudioController, modifier: Modifier = Modifier) {
    val state by controller.state.collectAsState()
    var leftTab by remember { mutableIntStateOf(0) }
    val editorStateHolder = rememberSaveableStateHolder()

    // The window opens on the backend it was told to reach, so the operator
    // sees what the server already holds rather than an empty pane.
    LaunchedEffect(Unit) {
        controller.checkHealth()
        controller.refreshSources()
    }

    val onExport = {
        val suggested = "${state.docId ?: "document"}.docx"
        val target = FileDialogs.chooseSaveTarget("Export the manuscript", suggested)
        if (target != null) controller.exportDocxTo(target)
    }

    BoxWithConstraints(modifier = modifier.fillMaxSize()) {
        val wideWorkspace = maxWidth >= 1400.dp
        val compactWorkspace = maxWidth < 720.dp
        var showResearch by remember { mutableStateOf(maxWidth >= 1200.dp) }
        var showReview by remember { mutableStateOf(false) }
        var focusMode by remember { mutableStateOf(false) }

        LaunchedEffect(state.selectedClaimId, state.selectedChunkId) {
            if (state.selectedClaimId != null || state.selectedChunkId != null) {
                focusMode = false
                showReview = true
                if (!wideWorkspace) showResearch = false
            }
        }

        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(modifier = Modifier.fillMaxSize()) {
                ConnectionBar(state, controller, onExport)
                ReviewGateBanner(state, controller)
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 2.dp)
                        .horizontalScroll(rememberScrollState()),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        "DOCUMENT",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp),
                    )
                    TextButton(onClick = {
                        val wasVisible = showResearch && !focusMode && (!showReview || wideWorkspace)
                        focusMode = false
                        showResearch = !wasVisible
                        if (showResearch && !wideWorkspace) showReview = false
                    }) {
                        Text(if (showResearch && !focusMode && (!showReview || wideWorkspace)) "Hide research" else "Research")
                    }
                    TextButton(onClick = {
                        val wasVisible = showReview && !focusMode
                        focusMode = false
                        showReview = !wasVisible
                        if (showReview && !wideWorkspace) showResearch = false
                    }) {
                        Text(if (showReview && !focusMode) "Hide review" else "Review")
                    }
                    TextButton(onClick = { focusMode = !focusMode }) {
                        Text(if (focusMode) "Leave focus" else "Focus")
                    }
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))

                Row(modifier = Modifier.weight(1f).fillMaxWidth()) {
                    val researchVisible = showResearch && !focusMode && (!showReview || wideWorkspace)
                    val reviewVisible = showReview && !focusMode
                    if (researchVisible) {
                        Column(
                            modifier = Modifier
                                .then(if (compactWorkspace) Modifier.weight(1f) else Modifier.width(312.dp))
                                .fillMaxHeight()
                                .background(MaterialTheme.colorScheme.surface),
                        ) {
                            Text(
                                "Research desk",
                                style = MaterialTheme.typography.titleMedium,
                                modifier = Modifier.padding(start = 16.dp, top = 18.dp, bottom = 10.dp),
                            )
                            PrimaryTabRow(selectedTabIndex = leftTab) {
                                Tab(selected = leftTab == 0, onClick = { leftTab = 0 }, text = { Text("Sources", maxLines = 1) })
                                Tab(selected = leftTab == 1, onClick = { leftTab = 1 }, text = { Text("Research", maxLines = 1) })
                                Tab(selected = leftTab == 2, onClick = { leftTab = 2 }, text = { Text("Outline", maxLines = 1) })
                            }
                            when (leftTab) {
                                0 -> SourcesPane(state, controller)
                                1 -> DiscoveryPane(state)
                                else -> OutlinePane(state, controller)
                            }
                        }
                        VerticalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
                    }

                    if (!compactWorkspace || (!researchVisible && !reviewVisible)) {
                        Column(Modifier.weight(1f).fillMaxHeight()) {
                            editorStateHolder.SaveableStateProvider("manuscript") {
                                EditorPane(state, controller, onCitationSelected = {
                                    focusMode = false
                                    showReview = true
                                    if (!wideWorkspace) showResearch = false
                                })
                            }
                        }
                    }

                    if (reviewVisible) {
                        VerticalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
                        Column(
                            modifier = Modifier
                                .then(if (compactWorkspace) Modifier.weight(1f) else Modifier.width(348.dp))
                                .fillMaxHeight()
                                .background(MaterialTheme.colorScheme.surface),
                        ) {
                            Text(
                                "Document review",
                                style = MaterialTheme.typography.titleMedium,
                                modifier = Modifier.padding(start = 16.dp, top = 18.dp, bottom = 8.dp),
                            )
                            // The filter the operator chose is kept while the
                            // report is hidden and shown again.
                            editorStateHolder.SaveableStateProvider("review") {
                                VerificationPane(state, controller)
                            }
                        }
                    }
                }

                HorizontalDivider()
                StatusStrip(state, controller, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

/**
 * The strip along the bottom.
 *
 * It carries what the operator needs to know without looking anywhere else:
 * where the run is, what it measured, and what went wrong.
 */
@Composable
private fun StatusStrip(
    state: lmeditor.desktop.model.StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.background(MaterialTheme.colorScheme.surface).padding(horizontal = 18.dp, vertical = 7.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                state.status.ifBlank { "Idle" },
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
                // The state of the run is announced when it changes, so a
                // reader who cannot see the strip still hears where the run is.
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                maxLines = 1,
            )
            Text(
                state.currentStep.ifBlank { "${state.sections.size} sections  ·  ${state.sources.size} sources" },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            state.metrics?.let { metrics ->
                Text(
                    "${metrics.tokenCount} tokens · grounding ${"%.2f".format(metrics.groundingScore)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
        }
        val error = state.error
        if (error != null) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.weight(1f),
                )
                androidx.compose.material3.TextButton(onClick = controller::clearError) {
                    Text("Dismiss")
                }
            }
        }
        state.statusNotices.forEach { notice ->
            Text(
                notice,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
