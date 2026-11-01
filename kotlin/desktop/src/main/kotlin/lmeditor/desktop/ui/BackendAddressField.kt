package lmeditor.desktop.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.model.StudioState

/**
 * The address of the backend the window talks to.
 *
 * The server is the application's one dependency, and the address of it belongs
 * to the window rather than to any one workspace: the studio writes documents
 * with it and the documents workspace reads and writes books with it. So the
 * control lives here, once, and both workspaces show it.
 *
 * The address is applied, not typed-and-hoped-for: the field holds a draft, and
 * nothing changes on the server until Apply is pressed. A window that quietly
 * re-pointed itself at another server would be a window the operator could not
 * trust.
 *
 * The draft belongs to the caller, because the caller is the one that has to
 * say that an address has been typed but not applied.
 */
@Composable
fun BackendAddressField(
    state: StudioState,
    controller: StudioController,
    draft: String,
    onDraftChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val changed = draft != state.backendUrl

    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        OutlinedTextField(
            value = draft,
            onValueChange = onDraftChange,
            label = { Text("Server address") },
            singleLine = true,
            shape = RoundedCornerShape(6.dp),
            modifier = Modifier.weight(1f),
        )
        OutlinedButton(
            onClick = { applyBackendAddress(controller, draft, onDraftChange) },
            enabled = changed,
        ) {
            Text("Apply")
        }
    }
}

/**
 * Point the window at the address the operator typed.
 *
 * When the server accepts the address, the draft settles on the address the
 * server now holds, so the field does not keep claiming that there is an
 * unapplied change.
 */
fun applyBackendAddress(
    controller: StudioController,
    draft: String,
    onDraftChange: (String) -> Unit,
) {
    controller.setBackendUrl(draft)
    val activeUrl = controller.state.value.backendUrl
    if (draft.trim().trimEnd('/') == activeUrl) onDraftChange(activeUrl)
}
