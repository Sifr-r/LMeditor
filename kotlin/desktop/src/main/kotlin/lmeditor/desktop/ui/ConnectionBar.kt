package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.HealthSummary
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState
import lmeditor.desktop.model.isRunInFlight

/** Document actions, with connection configuration kept in a secondary drawer. */
@Composable
fun ConnectionBar(
    state: StudioState,
    controller: StudioController,
    onExport: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var backendDraft by remember(state.backendUrl) { mutableStateOf(state.backendUrl) }
    var connectionVisible by remember { mutableStateOf(false) }
    val hasUnappliedBackend = backendDraft != state.backendUrl
    val canRequest = !state.busy && !hasUnappliedBackend
    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        BoxWithConstraints {
            val compact = maxWidth < 760.dp
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Surface(
                        color = MaterialTheme.colorScheme.primary,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                        shape = RoundedCornerShape(6.dp),
                    ) {
                        Box(Modifier.size(34.dp), contentAlignment = Alignment.Center) {
                            Text("L", style = MaterialTheme.typography.titleLarge)
                        }
                    }
                    Column(Modifier.weight(1f)) {
                        Text("LMeditor", style = MaterialTheme.typography.titleMedium)
                        Text(
                            state.topic.ifBlank { "Untitled manuscript" },
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    if (!compact) HealthPill(state.health)
                    TextButton(onClick = { connectionVisible = !connectionVisible }) {
                        Text(if (connectionVisible) "Hide connection" else "Connection")
                    }
                }

                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                if (hasUnappliedBackend && !connectionVisible) {
                    Text(
                        "Server address has unapplied changes. Open Connection to apply them.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.padding(horizontal = 20.dp, vertical = 6.dp),
                    )
                }
                Column(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        OutlinedTextField(
                            value = state.topic,
                            onValueChange = controller::setTopic,
                            label = { Text("Document topic") },
                            placeholder = { Text("What would you like to write?") },
                            singleLine = true,
                            shape = RoundedCornerShape(6.dp),
                            modifier = Modifier.weight(1f),
                        )
                        if (!compact) DocumentActions(state, controller, canRequest, onExport)
                    }
                    if (compact) {
                        HealthPill(state.health)
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            DocumentActions(state, controller, canRequest, onExport)
                        }
                    }
                }

                if (connectionVisible) {
                    Column(
                        modifier = Modifier.fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
                            .padding(horizontal = 20.dp, vertical = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text("BACKEND CONNECTION", style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            BackendAddressField(
                                state = state,
                                controller = controller,
                                draft = backendDraft,
                                onDraftChange = { backendDraft = it },
                                modifier = Modifier.weight(1f),
                            )
                            if (!compact) ConnectionActions(controller, canRequest)
                        }
                        if (compact) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                ConnectionActions(controller, canRequest)
                            }
                        }
                        if (hasUnappliedBackend) {
                            Text("Apply the server address to resume document actions.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
}

@Composable
private fun DocumentActions(
    state: StudioState,
    controller: StudioController,
    canRequest: Boolean,
    onExport: () -> Unit,
) {
    Button(
        onClick = controller::startRun,
        enabled = canRequest && state.topic.isNotBlank(),
        shape = RoundedCornerShape(6.dp),
    ) { Text(if (state.busy) "Working…" else "Write", fontWeight = FontWeight.SemiBold) }
    OutlinedButton(
        onClick = controller::stopRun,
        enabled = controller.runInFlight() || isRunInFlight(state.status),
        shape = RoundedCornerShape(6.dp),
    ) { Text("Stop") }
    OutlinedButton(
        onClick = onExport,
        enabled = canRequest && state.docId != null,
        shape = RoundedCornerShape(6.dp),
    ) { Text("Export .docx") }
}

@Composable
private fun ConnectionActions(controller: StudioController, canRequest: Boolean) {
    TextButton(onClick = controller::checkHealth, enabled = canRequest) { Text("Check") }
    TextButton(onClick = controller::refreshSources, enabled = canRequest) { Text("Refresh sources") }
}

/**
 * What the backend says about itself.
 *
 * Distinguishes unchecked, reachable without a model, offline stub and ready.
 */
@Composable
private fun HealthPill(health: HealthSummary?, modifier: Modifier = Modifier) {
    val palette = verdictPalette()
    val (label, color) = when {
        health == null -> "not checked" to MaterialTheme.colorScheme.onSurfaceVariant
        !health.lmConfigured -> "up, cannot write" to MaterialTheme.colorScheme.error
        health.lmStub -> "ready (offline stub)" to palette.ungrounded
        else -> "ready" to MaterialTheme.colorScheme.secondary
    }
    Box(
        modifier = modifier
            .background(color.copy(alpha = 0.16f), RoundedCornerShape(10.dp))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = color)
    }
}

/** A small coloured square, used to mark a claim's verdict. */
@Composable
fun VerdictSwatch(color: Color, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .width(10.dp)
            .background(color, RoundedCornerShape(3.dp)),
    )
}
