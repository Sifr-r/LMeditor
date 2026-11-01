package lmeditor.desktop.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.SourceSummary
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The material the next run is written from.
 *
 * A source is ingested once and then named by id, so the same corpus can be
 * written from again and again without being shipped again. The pane is
 * therefore two things: a way to add material, and the list of what is
 * already there.
 */
@Composable
fun SourcesPane(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    var sourceId by remember { mutableStateOf("source_1") }
    var title by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }

    Column(modifier = modifier.fillMaxSize().padding(12.dp)) {
        Text("Material", style = MaterialTheme.typography.titleMedium)
        Text(
            "Ingest a file, or name a URL for the server to fetch.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 8.dp),
        )

        OutlinedTextField(
            value = sourceId,
            onValueChange = { sourceId = it },
            label = { Text("Source id") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = title,
            onValueChange = { title = it },
            label = { Text("Title") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
        )

        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Button(
                onClick = {
                    val file = FileDialogs.chooseFileToOpen("Choose a source file") ?: return@Button
                    controller.ingestFile(
                        sourceId = sourceId,
                        title = title.ifBlank { file.name },
                        fileName = file.name,
                        content = file.readBytes(),
                    )
                },
                enabled = !state.busy,
            ) {
                Text("Add a file")
            }
        }

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
        )
        OutlinedButton(
            onClick = {
                controller.ingestUrl(sourceId, title.ifBlank { url }, url)
            },
            enabled = !state.busy && url.isNotBlank(),
            modifier = Modifier.padding(top = 6.dp),
        ) {
            Text("Add a URL")
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

        Text(
            "On the server (${state.sources.size})",
            style = MaterialTheme.typography.titleMedium,
        )
        if (state.sources.isEmpty()) {
            Text(
                "No sources yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        LazyColumn(modifier = Modifier.weight(1f).padding(top = 4.dp)) {
            items(state.sources, key = { it.sourceId }) { source ->
                SourceRow(
                    source = source,
                    selected = source.sourceId == state.selectedSourceId,
                    onSelect = { controller.selectSource(source.sourceId) },
                )
            }
        }

        if (state.chunks.isNotEmpty()) {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text(
                "Passages (${state.chunks.size})",
                style = MaterialTheme.typography.titleMedium,
            )
            Column(
                modifier = Modifier
                    .weight(1f)
                    .verticalScroll(rememberScrollState())
                    .padding(top = 4.dp),
            ) {
                for (chunk in state.chunks) {
                    Text(
                        chunk.text,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Serif,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { controller.selectChunk(chunk.chunkId) }
                            .padding(vertical = 4.dp),
                    )
                }
            }
        }
    }
}

/** One source in the server's library, with the button that selects it. */
@Composable
private fun SourceRow(
    source: SourceSummary,
    selected: Boolean,
    onSelect: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            // The row is the one control: the radio button beside it marks the
            // state, so a reader is not offered the same row twice.
            .selectable(selected = selected, onClick = onSelect, role = Role.RadioButton)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(
            selected = selected,
            onClick = null,
            modifier = Modifier.clearAndSetSemantics { },
        )
        Column(modifier = Modifier.weight(1f)) {
            Text(source.title.ifBlank { source.sourceId }, style = MaterialTheme.typography.bodyMedium)
            Text(
                "${source.kind} · ${source.chunkCount} passage(s)",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
