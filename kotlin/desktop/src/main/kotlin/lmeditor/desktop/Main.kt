package lmeditor.desktop

import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import lmeditor.desktop.model.StudioController
import lmeditor.desktop.ui.LMeditorScreen
import lmeditor.desktop.ui.LMeditorTheme

/**
 * The entry point of the LMeditor desktop client.
 *
 * The window is opened on the Compose UI thread and the whole application
 * lives in it: the workspace rail, the studio, the documents workspace, the
 * connection to the backend, and the run it follows.
 */
fun main() = application {
    Window(
        onCloseRequest = ::exitApplication,
        title = "LMeditor",
        state = rememberWindowState(width = 1480.dp, height = 940.dp),
    ) {
        LMeditorTheme {
            val scope = rememberCoroutineScope()
            val controller = remember { StudioController(scope) }
            LMeditorScreen(controller)
        }
    }
}
