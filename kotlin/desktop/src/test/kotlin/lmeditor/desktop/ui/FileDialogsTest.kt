package lmeditor.desktop.ui

import java.awt.EventQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.swing.JFileChooser
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * The rule the file dialogs follow about which thread they run on.
 *
 * The bug this pins was found by clicking the button, not by reading the
 * code: Compose Desktop delivers a click **on** the AWT event dispatch
 * thread, and `EventQueue.invokeAndWait` throws `Error` when it is called
 * from there. The export button therefore killed the event queue, and with
 * it the window.
 */
class FileDialogsTest {

    @Test
    fun `an action asked for on the event thread runs there`() {
        val done = CountDownLatch(1)
        var ranOnTheEventThread = false
        var outcome: Int? = null
        var failure: Throwable? = null

        // Compose Desktop delivers a click here, on the event dispatch thread.
        EventQueue.invokeAndWait {
            outcome = runCatching {
                FileDialogs.onEventThread {
                    ranOnTheEventThread = EventQueue.isDispatchThread()
                    42
                }
            }.getOrNull()
            failure = runCatching {
                FileDialogs.onEventThread { 42 }
            }.exceptionOrNull()
            done.countDown()
        }

        assertTrue(done.await(10, TimeUnit.SECONDS), "the action never ran")
        assertEquals(null, failure, "the action failed on the event thread: $failure")
        assertEquals(42, outcome)
        assertTrue(ranOnTheEventThread, "the action ran off the event thread")
    }

    @Test
    fun `an action asked for from another thread is handed to the event thread`() {
        val done = CountDownLatch(1)
        var ranOnTheEventThread = false
        var outcome: Int? = null

        Thread {
            outcome = FileDialogs.onEventThread {
                ranOnTheEventThread = EventQueue.isDispatchThread()
                7
            }
            done.countDown()
        }.start()

        assertTrue(done.await(10, TimeUnit.SECONDS), "the action never ran")
        assertEquals(7, outcome)
        assertTrue(ranOnTheEventThread, "the action ran off the event thread")
    }

    @Test
    fun `a failure inside the action is raised, not swallowed`() {
        val done = CountDownLatch(1)
        var failure: Throwable? = null

        Thread {
            failure = runCatching {
                FileDialogs.onEventThread { throw IllegalArgumentException("no") }
            }.exceptionOrNull()
            done.countDown()
        }.start()

        assertTrue(done.await(10, TimeUnit.SECONDS), "the action never ran")
        assertTrue(failure is IllegalArgumentException, "the failure was swallowed: $failure")
    }

    @Test
    fun `the folder chooser chooses a folder and not a file`() {
        // A vault is a folder, and AWT's own dialog cannot choose one, so the
        // rule this pins is that the chooser is Swing's in directories-only
        // mode.
        val chooser = FileDialogs.folderChooser("Open a vault folder")
        assertEquals(JFileChooser.DIRECTORIES_ONLY, chooser.fileSelectionMode)
        assertEquals("Open a vault folder", chooser.dialogTitle)
    }
}
