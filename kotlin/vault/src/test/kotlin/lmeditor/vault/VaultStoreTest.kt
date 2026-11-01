package lmeditor.vault

import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The store of a vault: the folder of markdown files the operator keeps.
 */
class VaultStoreTest {

    /** A hidden file or folder is skipped when listing. */
    @Test
    fun aHiddenFileOrFolderIsSkippedWhenListing() {
        val vault = newVault()
        val store = VaultStore(vault)
        store.writeNote("note", "# Note\n")
        store.writeNote("folder/other", "# Other\n")
        Files.createDirectories(vault.resolve(".obsidian"))
        Files.writeString(vault.resolve(".obsidian/config"), "hidden")
        Files.writeString(vault.resolve(".hidden.md"), "hidden")
        assertEquals(listOf("folder/other", "note"), store.listNotes().map { it.noteId })
    }

    /** The title of a note is its first heading. */
    @Test
    fun theTitleOfANoteIsItsFirstHeading() {
        val store = VaultStore(newVault())
        val note = store.writeNote("note", "# The Heading\n\nthe body\n")
        assertEquals("The Heading", note.title)
    }

    /** The title of a note without a heading is its file name. */
    @Test
    fun theTitleOfANoteWithoutAHeadingIsItsFileName() {
        val store = VaultStore(newVault())
        val note = store.writeNote("folder/note", "just text\n")
        assertEquals("note", note.title)
    }

    /** Writing the same content twice leaves the same bytes. */
    @Test
    fun writingTheSameContentTwiceLeavesTheSameBytes() {
        val vault = newVault()
        val store = VaultStore(vault)
        val content = "# Note\n\nthe body with a tag #idea\n"
        store.writeNote("note", content)
        val first = Files.readAllBytes(vault.resolve("note.md"))
        store.writeNote("note", content)
        val second = Files.readAllBytes(vault.resolve("note.md"))
        assertTrue(first.contentEquals(second))
    }

    /** A write recomputes the title, the tags, the links and the backlinks. */
    @Test
    fun aWriteRecomputesTheTitleTagsLinksAndBacklinks() {
        val store = VaultStore(newVault())
        store.writeNote("target", "# Target\n")
        val note = store.writeNote("note", "# Note\n\nSee [[target]] and #idea\n")
        assertEquals("Note", note.title)
        assertEquals(listOf("idea"), note.tags)
        assertEquals(listOf("target"), note.links)
        assertEquals(listOf("note"), store.readNote("target").backlinks)
    }

    /** A note written with Windows line endings is read as it is. */
    @Test
    fun aNoteWrittenWithWindowsLineEndingsIsReadAsItIs() {
        val vault = newVault()
        val store = VaultStore(vault)
        val content = "---\r\ntags: [project]\r\n---\r\n# The Note\r\n\r\nsee [[Other]] #idea\r\n"
        Files.writeString(vault.resolve("note.md"), content)
        val note = store.readNote("note")
        assertEquals("The Note", note.title)
        assertEquals(listOf("idea", "project"), note.tags)
        assertEquals(listOf("Other"), note.links)
    }

    /** A link names a file as readily as it names a path: this is Obsidian's own rule. */
    @Test
    fun aLinkNamesTheFileAsWellAsThePath() {
        val store = VaultStore(newVault())
        store.writeNote("folder/note", "# A heading that is not the file name\n")
        store.writeNote("other", "# Other\n\nSee [[note]].\n")
        assertEquals(listOf("other"), store.readNote("folder/note").backlinks)
    }

    /** A link that names a path wins over a link that names only a file. */
    @Test
    fun aLinkThatNamesAPathWinsOverOneThatNamesAFile() {
        val store = VaultStore(newVault())
        store.writeNote("note", "# At the root\n")
        store.writeNote("folder/note", "# In a folder\n")
        store.writeNote("other", "# Other\n\nSee [[note]].\n")
        assertEquals(listOf("other"), store.readNote("note").backlinks)
        assertEquals(emptyList<String>(), store.readNote("folder/note").backlinks)
    }

    /** Renaming moves the file and rewrites the links that pointed at it. */
    @Test
    fun renamingMovesTheFileAndRewritesTheLinks() {
        val vault = newVault()
        val store = VaultStore(vault)
        store.writeNote("old", "# Old\n")
        store.writeNote("other", "# Other\n\nSee [[old]] and [[old|alias]].\n")
        val result = store.renameNote("old", "new", updateLinks = true)
        assertEquals("old", result.fromId)
        assertEquals("new", result.toId)
        assertEquals(2, result.updatedLinks)
        assertTrue(Files.notExists(vault.resolve("old.md")))
        assertTrue(Files.isRegularFile(vault.resolve("new.md")))
        val other = store.readNote("other")
        assertEquals(listOf("new"), other.links)
        assertTrue(other.content.contains("[[new]]"))
        assertTrue(other.content.contains("[[new|alias]]"))
    }

    /** Renaming without the links leaves the links alone. */
    @Test
    fun renamingWithoutTheLinksLeavesTheLinksAlone() {
        val store = VaultStore(newVault())
        store.writeNote("old", "# Old\n")
        store.writeNote("other", "# Other\n\nSee [[old]].\n")
        val result = store.renameNote("old", "new", updateLinks = false)
        assertEquals(0, result.updatedLinks)
        assertTrue(store.readNote("other").content.contains("[[old]]"))
    }

    /** A note that is not there is refused. */
    @Test
    fun aNoteThatIsNotThereIsRefused() {
        val store = VaultStore(newVault())
        assertFailsWith<NoteNotFoundError> { store.readNote("missing") }
        assertFailsWith<NoteNotFoundError> { store.deleteNote("missing") }
    }

    /** A refused note id raises the typed error and does not touch the disk. */
    @Test
    fun aRefusedNoteIdDoesNotTouchTheDisk() {
        val vault = newVault()
        val store = VaultStore(vault)
        assertFailsWith<UnsafeNoteIdError> { store.writeNote("../escape", "text") }
        assertFailsWith<UnsafeNoteIdError> { store.writeNote("/absolute", "text") }
        assertFailsWith<UnsafeNoteIdError> { store.deleteNote("../escape") }
        assertEquals(0, Files.newDirectoryStream(vault).use { it.count() })
    }

    /** A vault folder that is not there is refused. */
    @Test
    fun aVaultThatIsNotThereIsRefused() {
        val missing = newVault().resolve("not-a-vault")
        val store = VaultStore(missing)
        assertFailsWith<VaultNotFoundError> { store.listNotes() }
        assertFailsWith<VaultNotFoundError> { store.readNote("note") }
    }

    /** A note is created, and creating it again changes nothing. */
    @Test
    fun creatingANoteTwiceChangesNothing() {
        val store = VaultStore(newVault())
        val created = store.createNote("note", "The Title", "the body")
        assertEquals("The Title", created.title)
        val again = store.createNote("note", "Another Title", "other body")
        assertEquals("The Title", again.title)
        assertTrue(again.content.contains("the body"))
    }

    /** A note is deleted, and it is gone. */
    @Test
    fun aNoteIsDeleted() {
        val vault = newVault()
        val store = VaultStore(vault)
        store.writeNote("note", "# Note\n")
        store.deleteNote("note")
        assertTrue(Files.notExists(vault.resolve("note.md")))
        assertEquals(emptyList(), store.listNotes())
    }

    /** The tags of a vault are counted. */
    @Test
    fun theTagsOfAVaultAreCounted() {
        val store = VaultStore(newVault())
        store.writeNote("one", "# One\n\n#idea\n")
        store.writeNote("two", "# Two\n\n#idea and #plan\n")
        val tags = store.tags()
        assertEquals(listOf("idea", "plan"), tags.map { it.tag })
        assertEquals(listOf(2, 1), tags.map { it.count })
    }

    /** The backlinks of a note name the notes that link to it. */
    @Test
    fun theBacklinksOfANoteNameTheNotesThatLinkToIt() {
        val store = VaultStore(newVault())
        store.writeNote("target", "# Target\n")
        store.writeNote("first", "# First\n\nSee [[target]].\n")
        store.writeNote("second", "# Second\n\nSee [[Target]].\n")
        store.writeNote("third", "# Third\n\nSee [[nowhere]].\n")
        assertEquals(listOf("first", "second"), store.readNote("target").backlinks)
    }
}
