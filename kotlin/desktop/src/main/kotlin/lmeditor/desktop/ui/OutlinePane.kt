package lmeditor.desktop.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import lmeditor.core.OutlineNode
import lmeditor.core.OutlineNodeInput
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The outline: the run's own, and the operator's.
 *
 * The operator's outline is the steer -- the sections the next draft is
 * written to. It is a separate type from the published outline node, and that
 * separation is the point: a section id or a chunk assignment cannot be
 * posted by accident, because the type has nowhere to put one.
 */
@Composable
fun OutlinePane(
    state: StudioState,
    controller: StudioController,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(12.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Text("Your outline", style = MaterialTheme.typography.titleMedium)
        Text(
            "The sections the next draft is written to. Leave it empty to let the run decide.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 2.dp, bottom = 6.dp),
        )

        state.operatorOutline.forEachIndexed { index, node ->
            OutlineInputEditor(
                node = node,
                depth = 0,
                onChange = { updated ->
                    controller.setOperatorOutline(replaceAt(state.operatorOutline, index, updated))
                },
                onRemove = {
                    controller.setOperatorOutline(
                        state.operatorOutline.filterIndexed { position, _ -> position != index },
                    )
                },
            )
        }

        OutlinedButton(
            onClick = {
                controller.setOperatorOutline(
                    state.operatorOutline + OutlineNodeInput(title = "New section"),
                )
            },
            modifier = Modifier.padding(top = 8.dp),
        ) {
            Text("Add a section")
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))

        Text("The run's outline", style = MaterialTheme.typography.titleMedium)
        if (state.outline.isEmpty()) {
            Text(
                "The run has not published an outline yet.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        for (node in state.outline) {
            PublishedOutlineNode(node)
        }
    }
}

/**
 * One section of the operator's own outline.
 *
 * The editor is recursive because the outline is: a section may hold
 * subsections, and a subsection may hold subsections of its own.
 */
@Composable
private fun OutlineInputEditor(
    node: OutlineNodeInput,
    depth: Int,
    onChange: (OutlineNodeInput) -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth().padding(start = (depth * 14).dp, top = 6.dp)) {
        OutlinedTextField(
            value = node.title,
            onValueChange = { title -> onChange(OutlineNodeInput(title, node.description, node.children)) },
            label = { Text("Section") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = node.description,
            onValueChange = { description ->
                onChange(OutlineNodeInput(node.title, description, node.children))
            },
            label = { Text("What this section should cover") },
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
        )
        Row(
            modifier = Modifier.padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                onClick = {
                    onChange(
                        OutlineNodeInput(
                            node.title,
                            node.description,
                            node.children + OutlineNodeInput(title = "Subsection"),
                        ),
                    )
                },
            ) {
                Text("Add a subsection")
            }
            if (depth > 0) {
                OutlinedButton(onClick = onRemove) { Text("Remove") }
            }
        }
        for ((index, child) in node.children.withIndex()) {
            OutlineInputEditor(
                node = child,
                depth = depth + 1,
                onChange = { updated ->
                    onChange(
                        OutlineNodeInput(
                            node.title,
                            node.description,
                            replaceAt(node.children, index, updated),
                        ),
                    )
                },
                onRemove = {
                    onChange(
                        OutlineNodeInput(
                            node.title,
                            node.description,
                            node.children.filterIndexed { position, _ -> position != index },
                        ),
                    )
                },
            )
        }
    }
}

/** One node of the outline the run published. */
@Composable
private fun PublishedOutlineNode(
    node: OutlineNode,
    depth: Int = 0,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth().padding(start = (depth * 14).dp, top = 4.dp)) {
        Text(
            node.title,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
        )
        if (node.description.isNotBlank()) {
            Text(
                node.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (node.assignedChunkIds.isNotEmpty()) {
            Text(
                "written from ${node.assignedChunkIds.size} passage(s)",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        for (child in node.children) {
            PublishedOutlineNode(child, depth + 1)
        }
    }
}

/** Replace one section of an outline, leaving the rest where they were. */
private fun replaceAt(
    nodes: List<OutlineNodeInput>,
    index: Int,
    replacement: OutlineNodeInput,
): List<OutlineNodeInput> {
    val copy = nodes.toMutableList()
    copy[index] = replacement
    return copy
}
