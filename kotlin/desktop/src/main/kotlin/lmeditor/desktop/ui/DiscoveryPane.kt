package lmeditor.desktop.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import lmeditor.core.Perspective
import lmeditor.desktop.model.StudioState

/**
 * What the discovery stage found.
 *
 * The personas are the run's own reading of the corpus, and the guided
 * findings are what the research stage recorded. Both are shown as they
 * arrive, because a stage that found nothing is indistinguishable from a
 * stage that never ran.
 */
@Composable
fun DiscoveryPane(state: StudioState, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(12.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Text("Perspectives", style = MaterialTheme.typography.titleMedium)
        if (state.perspectives.isEmpty()) {
            Text(
                "No perspectives yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        for (perspective in state.perspectives) {
            PerspectiveCard(perspective)
        }

        if (state.researchNotes.isNotEmpty()) {
            Text(
                "Guided findings",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = 14.dp),
            )
            for (note in state.researchNotes) {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    ),
                    modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                ) {
                    Text(note, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(10.dp))
                }
            }
            if (state.referencedChunkIds.isNotEmpty()) {
                Text(
                    "Leaning on ${state.referencedChunkIds.size} passage(s).",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}

/** One persona, with the questions it is being asked. */
@Composable
private fun PerspectiveCard(perspective: Perspective, modifier: Modifier = Modifier) {
    Card(modifier = modifier.fillMaxWidth().padding(top = 6.dp)) {
        Column(modifier = Modifier.padding(10.dp)) {
            Text(perspective.resolvedTitle, style = MaterialTheme.typography.titleMedium)
            Text(
                perspective.role.wire.replace('_', ' '),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
            )
            if (perspective.description.isNotBlank()) {
                Text(
                    perspective.description,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
            val questions = perspective.guidingQuestions.ifEmpty { perspective.keyInquiries }
            if (questions.isNotEmpty()) {
                Column(
                    modifier = Modifier.padding(top = 6.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    for (question in questions) {
                        Text(
                            "· $question",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

/** A one-line summary of the run, shown above the status strip. */
@Composable
fun RunSummary(state: StudioState, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            state.status.ifBlank { "idle" },
            style = MaterialTheme.typography.labelMedium,
        )
        if (state.currentStep.isNotBlank()) {
            Text(
                state.currentStep,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        state.metrics?.let { metrics ->
            Text(
                "${metrics.tokenCount} token(s) · grounding ${"%.2f".format(metrics.groundingScore)}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
