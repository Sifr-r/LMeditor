package lmeditor.desktop.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The documents workspace's own rules, with no server behind them.
 *
 * The workspace is a value: what it says about the open book, the open
 * section and the operator's text is true on its own, before any request is
 * made. These are the rules the value holds by itself.
 */
class DocumentWorkspaceTest {
    @Test
    fun `the workspace starts empty`() {
        val workspace = DocumentWorkspace()
        assertTrue(workspace.books.isEmpty())
        assertNull(workspace.openBook)
        assertNull(workspace.openSectionId)
        assertEquals("", workspace.sectionTitle)
        assertEquals("", workspace.sectionDraft)
        assertEquals("", workspace.savedContent)
        assertEquals(false, workspace.sectionDirty)
        assertEquals(false, workspace.saving)
    }

    @Test
    fun `a workspace without an open section has no unsaved changes`() {
        val workspace = DocumentWorkspace(sectionDirty = true)
        assertFalse(workspace.hasUnsavedChanges)
    }

    @Test
    fun `a workspace is unsaved only when an open section is dirty`() {
        val clean = DocumentWorkspace(openSectionId = "sec_1")
        assertFalse(clean.hasUnsavedChanges)
        val dirty = DocumentWorkspace(openSectionId = "sec_1", sectionDirty = true)
        assertTrue(dirty.hasUnsavedChanges)
    }
}
