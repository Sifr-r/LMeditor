package lmeditor.desktop.model

import lmeditor.core.VaultNote
import lmeditor.core.VaultNoteSummary
import lmeditor.core.VaultSearchHit
import lmeditor.core.VaultSummary
import lmeditor.core.VaultTag

/**
 * The state of the vault workspace.
 *
 * The vault workspace is the part of the window where the operator points at
 * the folder of markdown files they already keep and works inside it: the
 * notes are listed, one is open, its text is edited and saved back to the
 * same file. It is a separate value from the studio's own state because it is
 * a separate job -- a run is written from sources, while a vault is the
 * operator's own folder.
 *
 * The state is immutable and every transition produces a new value, exactly as
 * the studio's state and the documents workspace's state do, so a screen
 * cannot show half of a change.
 */
data class VaultWorkspace(
    /** The vault the server has open, or null when no vault is open. */
    val summary: VaultSummary? = null,

    /** Every note of the open vault, as a summary: enough to navigate by. */
    val notes: List<VaultNoteSummary> = emptyList(),

    /** The note the operator has open, or null when no note is open. */
    val openNoteId: String? = null,

    /** The open note as the server last read it, with its links and backlinks. */
    val note: VaultNote? = null,

    /**
     * The operator's text for the open note.
     *
     * This is the edit buffer: it holds what the operator has typed, which
     * may not be what the vault holds on disk yet. The buffer is the truth
     * of the screen while the note is being edited; the file is the truth
     * once the note has been saved.
     */
    val draft: String = "",

    /**
     * The text the vault holds for the open note.
     *
     * The dirty mark is the difference between the buffer and this, so an
     * operator who types a character and then deletes it is not left with a
     * note that claims to have unsaved changes.
     */
    val savedContent: String = "",

    /** True when the buffer holds something the vault has not been told. */
    val dirty: Boolean = false,

    /** The terms the operator last searched the vault for. */
    val searchQuery: String = "",

    /** The hits of that search, ranked by the tier's own rule. */
    val results: List<VaultSearchHit> = emptyList(),

    /** Every tag of the vault, with the number of notes that carry it. */
    val tags: List<VaultTag> = emptyList(),

    /** True while a save is in flight. */
    val saving: Boolean = false,
) {
    /**
     * Whether the open note has unsaved changes.
     *
     * A vault with no open note is never dirty: there is nothing to lose.
     */
    val hasUnsavedChanges: Boolean get() = openNoteId != null && dirty
}
