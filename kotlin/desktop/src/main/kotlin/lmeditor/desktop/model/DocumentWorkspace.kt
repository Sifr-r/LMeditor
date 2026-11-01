package lmeditor.desktop.model

import lmeditor.core.BookManifest

/**
 * The state of the word-processing workspace.
 *
 * The workspace is the part of the window where the operator opens a Word
 * document, reads it, edits it and saves it. It is a separate value from the
 * studio's own state because it is a separate job: a run is written from
 * sources, while a document is the operator's own file.
 *
 * The state is immutable and every transition produces a new value, exactly as
 * the studio's state does, so a screen cannot show half of a change.
 */
data class DocumentWorkspace(
    /** Every book the server holds, newest index first as the server lists them. */
    val books: List<BookManifest> = emptyList(),

    /** The book the operator has open, or null when nothing is open. */
    val openBook: BookManifest? = null,

    /** The section the operator is editing, or null when no section is open. */
    val openSectionId: String? = null,

    /** The heading of the open section. */
    val sectionTitle: String = "",

    /**
     * The operator's text for the open section.
     *
     * This is the edit buffer: it holds what the operator has typed, which may
     * not be what the server holds yet. The buffer is the truth of the screen
     * while the section is being edited; the server is the truth once the
     * section has been saved.
     */
    val sectionDraft: String = "",

    /**
     * The text the server holds for the open section.
     *
     * The dirty mark is the difference between the buffer and this, so an
     * operator who types a character and then deletes it is not left with a
     * section that claims to have unsaved changes.
     */
    val savedContent: String = "",

    /** True when the buffer holds something the server has not been told. */
    val sectionDirty: Boolean = false,

    /** True while a save is in flight. */
    val saving: Boolean = false,
) {
    /**
     * Whether the open section has unsaved changes.
     *
     * A book with no open section is never dirty: there is nothing to lose.
     */
    val hasUnsavedChanges: Boolean get() = openSectionId != null && sectionDirty
}

/**
 * The text of one section of a book, as the server read it.
 *
 * The tier's own `BookSection` is the *index* entry: it carries the counts and
 * the passage ids, and never the body text. This is the body text, so it is a
 * separate type and the two cannot be confused for one another.
 */
data class BookSectionText(
    /** The book the section belongs to. */
    val bookId: String,

    /** The section's identifier. */
    val sectionId: String,

    /** The section's heading. */
    val title: String = "",

    /** The section's position in the book. */
    val order: Int = 0,

    /** The section's body text. */
    val content: String = "",
)
