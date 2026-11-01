package lmeditor.desktop.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The review gate.
 *
 * A run that cannot ground itself stops here and waits for a person. The
 * banner appears only while the run is actually waiting, and it carries the
 * reason the run gave -- a gate the operator cannot see the reason for is a
 * gate the operator cannot judge.
 */
@Composable
fun ReviewGateBanner(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    val review = state.pendingReview ?: return
    var feedback by remember { mutableStateOf("") }

    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.tertiary.copy(alpha = 0.14f),
        ),
        modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("The run needs a decision", style = MaterialTheme.typography.titleMedium)
            Text(
                review.reason.ifBlank { "Human review is required before continuing." },
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 2.dp),
            )
            OutlinedTextField(
                value = feedback,
                onValueChange = { feedback = it },
                label = { Text("What should the run know?") },
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            )
            Row(
                modifier = Modifier.padding(top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = { controller.answerReview(approved = true, feedback = feedback) },
                    enabled = !state.busy,
                ) {
                    Text("Approve")
                }
                OutlinedButton(
                    onClick = { controller.answerReview(approved = false, feedback = feedback) },
                    enabled = !state.busy,
                ) {
                    Text("Reject")
                }
            }
        }
    }
}
