package lmeditor.desktop.model

import lmeditor.core.Notebook
import lmeditor.core.NotebookAnswer
import lmeditor.core.NotebookLibrary
import lmeditor.core.NotebookSummary

/**
 * The state of the notebook workspace.
 *
 * The notebook workspace is the part of the window where the operator gathers
 * the material they will later ask questions of: a named collection of
 * sources and notes, and the answer the machine gave about it. It is a
 * separate value from the studio's own state because it is a separate job --
 * a run is written from sources, while a notebook is the operator's own
 * collection and the questions they asked of it.
 *
 * The state is immutable and every transition produces a new value, exactly
 * as the studio's state, the documents workspace's state and the vault
 * workspace's state do, so a screen cannot show half of a change.
 */
data class NotebookWorkspace(
    /**
     * The folder the notebooks live in, as the server answered.
     *
     * The library carries the folder's path, its own name and how many
     * notebooks it holds, so the operator always knows where their work is.
     */
    val library: NotebookLibrary? = null,

    /** Every notebook of the library, as a summary: enough to navigate by. */
    val notebooks: List<NotebookSummary> = emptyList(),

    /** The notebook the operator has open, or null when none is open. */
    val openNotebook: Notebook? = null,

    /**
     * The operator's title for the open notebook.
     *
     * This is the edit buffer: it holds what the operator has typed, which
     * may not be what the server holds yet. The buffer is the truth of the
     * screen while the notebook is being renamed; the server is the truth
     * once the notebook has been saved.
     */
    val titleDraft: String = "",

    /** The operator's description for the open notebook. */
    val descriptionDraft: String = "",

    /** The question the operator is asking of the open notebook. */
    val question: String = "",

    /**
     * The last answer the server gave, whether it answered or refused.
     *
     * A refusal is an answer too: the server said the sources do not answer
     * the question, and the client shows that as it was told rather than
     * hiding it or dressing it up as a reply.
     */
    val answer: NotebookAnswer? = null,

    /** True while a request is in flight. */
    val busy: Boolean = false,

    /** The last failure worth showing the operator. */
    val error: String? = null,
) {
    /**
     * Whether the open notebook holds changes the server has not been told.
     *
     * Only the title and the description are editable, so those are what the
     * dirty mark is the difference of. A workspace with no open notebook is
     * never dirty: there is nothing to lose.
     */
    val dirty: Boolean
        get() {
            val notebook = openNotebook ?: return false
            return titleDraft != notebook.title || descriptionDraft != notebook.description
        }

    /**
     * Whether the last answer answered the question.
     *
     * An answer that says the sources do not answer the question is a
     * successful ask that answered false, and the operator has to be able to
     * tell the two apart.
     */
    val answered: Boolean get() = answer?.answered == true
}
