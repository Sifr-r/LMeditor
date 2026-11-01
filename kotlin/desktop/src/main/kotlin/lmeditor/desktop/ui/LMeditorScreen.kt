package lmeditor.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.StudioController

/**
 * The workspaces the window holds.
 *
 * The window is one application, so it is one window: the studio, the
 * documents workspace, the vault workspace and the notebook workspace are four
 * rooms in it, not four programs. The rail above them is how the operator
 * moves between them.
 */
enum class Workspace {
    /** The writing studio: sources in, a manuscript out. */
    WRITE,

    /** The documents workspace: a Word file in, a Word file out. */
    DOCUMENTS,

    /** The vault workspace: a folder of markdown files in, the same folder out. */
    VAULT,

    /** The notebook workspace: a folder of notebooks in, an answer out. */
    NOTEBOOK,
}

/**
 * The window.
 *
 * The rail sits above the studio rather than inside it, so the studio keeps
 * its own header and its full height, and the Documents workspace inherits
 * exactly the same chrome the studio already wears.
 */
@Composable
fun LMeditorScreen(controller: StudioController, modifier: Modifier = Modifier) {
    var workspace by rememberSaveable { mutableStateOf(Workspace.WRITE) }
    val state by controller.state.collectAsState()

    Surface(modifier = modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(modifier = Modifier.fillMaxSize()) {
            WorkspaceRail(workspace = workspace, onSelect = { workspace = it })
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
            when (workspace) {
                Workspace.WRITE -> StudioScreen(controller, modifier = Modifier.weight(1f))
                Workspace.DOCUMENTS ->
                    DocumentsScreen(state, controller, modifier = Modifier.weight(1f))
                Workspace.VAULT ->
                    VaultScreen(state, controller, modifier = Modifier.weight(1f))
                Workspace.NOTEBOOK ->
                    NotebookScreen(
                        state = state,
                        controller = controller,
                        onOpenPassage = { chunkId ->
                            controller.selectChunk(chunkId)
                            workspace = Workspace.WRITE
                        },
                        modifier = Modifier.weight(1f),
                    )
            }
        }
    }
}

/**
 * The rail across the top of the window.
 *
 * It is a quiet row of tabs in the studio's own idiom -- the same small-caps
 * label and the same text buttons the studio's mode row uses -- rather than a
 * browser's tab bar.
 */
@Composable
private fun WorkspaceRail(
    workspace: Workspace,
    onSelect: (Workspace) -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(modifier = modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surface) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                "WORKSPACE",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 12.dp),
            )
            for (entry in Workspace.entries) {
                WorkspaceTab(
                    workspace = entry,
                    selected = entry == workspace,
                    onClick = { onSelect(entry) },
                )
            }
        }
    }
}

/** One tab of the rail, marked when it is the workspace on show. */
@Composable
private fun WorkspaceTab(
    workspace: Workspace,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val label = when (workspace) {
        Workspace.WRITE -> "Write"
        Workspace.DOCUMENTS -> "Documents"
        Workspace.VAULT -> "Vault"
        Workspace.NOTEBOOK -> "Notebook"
    }
    TextButton(
        onClick = onClick,
        colors = ButtonDefaults.textButtonColors(
            contentColor = if (selected) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurfaceVariant,
        ),
        // The tab says what it is and whether it is the one on show, so the
        // rail is not read as a row of equal words.
        modifier = modifier.selectable(selected = selected, onClick = onClick, role = Role.Tab),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                label,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
            Box(
                Modifier.width(30.dp).height(2.dp).background(
                    if (selected) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.surface,
                ),
            )
        }
    }
}
