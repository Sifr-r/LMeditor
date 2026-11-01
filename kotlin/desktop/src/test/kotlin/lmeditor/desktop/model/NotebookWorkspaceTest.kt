package lmeditor.desktop.model

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue
import lmeditor.core.Notebook
import lmeditor.core.NotebookAnswer
import lmeditor.core.NotebookCitation
import lmeditor.core.NotebookLibrary
import lmeditor.core.NotebookSummary

/**
 * The notebook workspace's own rules, with no server behind them.
 *
 * The workspace is a value: what it says about the folder of notebooks, the
 * open notebook and the answer it holds is true on its own, before any
 * request is made. These are the rules the value holds by itself.
 */
class NotebookWorkspaceTest {
    @Test
    fun `the workspace starts empty`() {
        val workspace = NotebookWorkspace()
        assertNull(workspace.library)
        assertTrue(workspace.notebooks.isEmpty())
        assertNull(workspace.openNotebook)
        assertEquals("", workspace.titleDraft)
        assertEquals("", workspace.descriptionDraft)
        assertEquals("", workspace.question)
        assertNull(workspace.answer)
        assertEquals(false, workspace.busy)
        assertNull(workspace.error)
        assertFalse(workspace.dirty)
        assertFalse(workspace.answered)
    }

    @Test
    fun `a transition leaves the value it came from untouched`() {
        val empty = NotebookWorkspace()
        val opened = empty.copy(
            library = library(),
            notebooks = listOf(summary()),
            openNotebook = notebook(),
            titleDraft = "A notebook",
            descriptionDraft = "What it is for.",
        )
        assertNull(empty.library)
        assertTrue(empty.notebooks.isEmpty())
        assertNull(empty.openNotebook)
        assertEquals("A notebook", opened.titleDraft)
        assertEquals(1, opened.notebooks.size)
    }

    @Test
    fun `the library carries the folder and the notebooks in it`() {
        val workspace = NotebookWorkspace(library = library(), notebooks = listOf(summary()))
        assertEquals("C:/notebooks", workspace.library?.root)
        assertEquals("notebooks", workspace.library?.name)
        assertEquals(1, workspace.library?.notebookCount)
        assertEquals("A notebook", workspace.notebooks.first().title)
        assertEquals(2, workspace.notebooks.first().sourceCount)
        assertEquals(1, workspace.notebooks.first().noteCount)
    }

    @Test
    fun `a workspace without an open notebook is never dirty`() {
        val workspace = NotebookWorkspace(titleDraft = "A title")
        assertFalse(workspace.dirty)
    }

    @Test
    fun `a workspace is dirty only when the drafts differ from the open notebook`() {
        val clean = NotebookWorkspace(
            openNotebook = notebook(),
            titleDraft = "A notebook",
            descriptionDraft = "What it is for.",
        )
        assertFalse(clean.dirty)
        val renamed = clean.copy(titleDraft = "Another title")
        assertTrue(renamed.dirty)
        val described = clean.copy(descriptionDraft = "Another description")
        assertTrue(described.dirty)
    }

    @Test
    fun `an answer that answered is told apart from one that refused`() {
        val refused = NotebookWorkspace(answer = NotebookAnswer(answer = "The sources do not answer that.", answered = false))
        assertFalse(refused.answered)
        assertEquals("The sources do not answer that.", refused.answer?.answer)

        val answered = NotebookWorkspace(
            answer = NotebookAnswer(
                answer = "The study measured the effect.",
                answered = true,
                citations = listOf(
                    NotebookCitation(chunkId = "chunk_1", sourceId = "src_1", title = "A study", quote = "The effect was measured."),
                ),
            ),
        )
        assertTrue(answered.answered)
        assertEquals(1, answered.answer?.citations?.size)
    }

    @Test
    fun `a refusal is a successful ask, not a failure`() {
        val workspace = NotebookWorkspace(
            answer = NotebookAnswer(answer = "The sources do not answer that.", answered = false),
            error = null,
        )
        assertNull(workspace.error)
        assertFalse(workspace.answered)
    }

    private fun library(): NotebookLibrary = NotebookLibrary(
        root = "C:/notebooks",
        name = "notebooks",
        notebookCount = 1,
        notebooks = listOf(summary()),
    )

    private fun summary(): NotebookSummary = NotebookSummary(
        notebookId = "nb_1",
        title = "A notebook",
        description = "What it is for.",
        sourceCount = 2,
        noteCount = 1,
    )

    private fun notebook(): Notebook = Notebook(
        notebookId = "nb_1",
        title = "A notebook",
        description = "What it is for.",
        sourceIds = listOf("src_1", "src_2"),
        noteIds = listOf("notes/one"),
    )
}
