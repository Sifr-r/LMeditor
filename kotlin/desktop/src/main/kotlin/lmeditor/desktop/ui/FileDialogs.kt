package lmeditor.desktop.ui

import java.awt.EventQueue
import java.awt.FileDialog
import java.awt.Frame
import java.io.File
import javax.swing.JFileChooser

/**
 * The file dialogs the client needs.
 *
 * They are the JDK's own, because a file dialog is a desktop-shell
 * affordance and the client may not take a dependency to draw one. The
 * dialog is opened on the AWT event thread, where a Swing component belongs.
 */
object FileDialogs {

    /**
     * Ask the operator for a file to ingest.
     *
     * Returns null when the operator cancelled, which is not an error: an
     * operator who changes their mind has not done anything wrong.
     */
    fun chooseFileToOpen(title: String): File? = onEventThread {
        val frame = Frame()
        try {
            val dialog = FileDialog(frame, title, FileDialog.LOAD)
            dialog.isMultipleMode = false
            dialog.isVisible = true
            val name = dialog.file
            val directory = dialog.directory
            if (name == null || directory == null) {
                null
            } else {
                File(directory, name)
            }
        } finally {
            frame.dispose()
        }
    }

    /**
     * Ask the operator where to write the exported document.
     *
     * Returns null when the operator cancelled.
     */
    fun chooseSaveTarget(title: String, suggestedName: String): File? = onEventThread {
        val frame = Frame()
        try {
            val dialog = FileDialog(frame, title, FileDialog.SAVE)
            dialog.file = suggestedName
            dialog.isVisible = true
            val name = dialog.file
            val directory = dialog.directory
            if (name == null || directory == null) {
                null
            } else {
                File(directory, name)
            }
        } finally {
            frame.dispose()
        }
    }

    /**
     * Ask the operator for a folder.
     *
     * AWT's own `FileDialog` cannot choose a directory, so this one is
     * Swing's `JFileChooser` in its directories-only mode. Swing is in the
     * JDK, so the client still takes no dependency to draw a dialog.
     *
     * Returns null when the operator cancelled, which is not an error.
     */
    fun chooseFolder(title: String): File? = onEventThread {
        val chooser = folderChooser(title)
        val outcome = chooser.showOpenDialog(null)
        if (outcome != JFileChooser.APPROVE_OPTION) {
            null
        } else {
            chooser.selectedFile
        }
    }

    /**
     * The folder chooser the window draws.
     *
     * It is built apart from the showing, so the rule it follows -- that a
     * folder is chosen and a file is not -- can be checked without opening
     * a dialog on the operator's screen.
     */
    internal fun folderChooser(title: String): JFileChooser {
        val chooser = JFileChooser()
        chooser.dialogTitle = title
        chooser.fileSelectionMode = JFileChooser.DIRECTORIES_ONLY
        chooser.isAcceptAllFileFilterUsed = false
        chooser.isMultiSelectionEnabled = false
        return chooser
    }

    /**
     * Run one action on the AWT event thread and return its result.
     *
     * Compose Desktop delivers a click **on** the AWT event dispatch thread,
     * and `EventQueue.invokeAndWait` throws `Error` when it is called from
     * there. So the thread is checked first: already on it, the action runs
     * directly; otherwise it is handed over and waited for.
     *
     * The action blocks — a file dialog is modal — which is why it belongs on
     * the event thread in the first place.
     */
    internal fun <T> onEventThread(block: () -> T): T {
        if (EventQueue.isDispatchThread()) {
            return block()
        }
        var outcome: Result<T>? = null
        EventQueue.invokeAndWait {
            outcome = runCatching(block)
        }
        return outcome?.getOrThrow()
            ?: throw IllegalStateException("The file dialog produced no result.")
    }
}
