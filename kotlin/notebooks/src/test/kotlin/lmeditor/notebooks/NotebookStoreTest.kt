package lmeditor.notebooks

import lmeditor.core.json.Json
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The store of the notebooks: a folder of notebooks, one directory each.
 */
class NotebookStoreTest {

    /** The manifest is written where the contract says, and read back unchanged. */
    @Test
    fun theManifestIsWrittenWhereTheContractSays() {
        val root = newNotebooksRoot()
        val store = NotebookStore(root)
        val created = store.create("my-notebook", "The Title", "the description")

        val manifest = root.resolve("my-notebook").resolve(MANIFEST_FILENAME)
        assertTrue(Files.isRegularFile(manifest))

        val parsed = Json.parseObject(Files.readString(manifest, StandardCharsets.UTF_8))
        assertEquals("my-notebook", parsed["notebook_id"])
        assertEquals("The Title", parsed["title"])
        assertEquals("the description", parsed["description"])

        assertEquals(created, store.read("my-notebook"))
    }

    /** A notebook that is not there is refused, not answered with an empty notebook. */
    @Test
    fun aNotebookThatIsNotThereIsRefused() {
        val store = NotebookStore(newNotebooksRoot())
        assertFailsWith<NotebookNotFoundError> { store.read("my-notebook") }
        assertFailsWith<NotebookNotFoundError> { store.setMetadata("my-notebook", "T", "D") }
        assertFailsWith<NotebookNotFoundError> { store.addSource("my-notebook", "source-1") }
        assertFailsWith<NotebookNotFoundError> { store.delete("my-notebook") }
    }

    /** Creating a notebook twice changes nothing: the title is never overwritten. */
    @Test
    fun creatingANotebookTwiceChangesNothing() {
        val store = NotebookStore(newNotebooksRoot())
        val created = store.create("my-notebook", "The Title", "the description")
        val again = store.create("my-notebook", "Another Title", "another description")
        assertEquals("The Title", again.title)
        assertEquals("the description", again.description)
        assertEquals(created.createdAt, again.createdAt)
        assertEquals(created, again)
    }

    /** A derived id is stable: the same title always yields the same notebook. */
    @Test
    fun aDerivedIdIsStableForTheSameTitle() {
        val store = NotebookStore(newNotebooksRoot())
        val created = store.create(null, "My Notebook", "the description")
        assertEquals("my-notebook", created.notebookId)
        val again = store.create(null, "My Notebook", "another description")
        assertEquals("my-notebook", again.notebookId)
        assertEquals(created, again)
        assertEquals(1, store.list().size)
    }

    /** A derived id that is taken by another notebook is suffixed until it is free. */
    @Test
    fun aTakenDerivedIdGetsASuffix() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "Another Title", "the description")
        val derived = store.create(null, "My Notebook", "the description")
        assertEquals("my-notebook-2", derived.notebookId)

        store.create("my-notebook-2", "Yet Another Title", "the description")
        val third = store.create(null, "My Notebook", "the description")
        assertEquals("my-notebook-3", third.notebookId)
    }

    /** Every unsafe id is refused, and the refusal says which id was refused. */
    @Test
    fun anUnsafeIdIsRefused() {
        val store = NotebookStore(newNotebooksRoot())
        val unsafe = listOf(
            "",
            "   ",
            ".",
            "..",
            "a/b",
            "a\\b",
            "a\u0000b",
            "a\u001Fb",
            "a<b",
            "a>b",
            "a:b",
            "a\"b",
            "a|b",
            "a?b",
            "a*b",
            "C:",
            "CON",
            "nul.json",
            "a".repeat(129),
        )
        for (id in unsafe) {
            val failure = assertFailsWith<UnsafeNotebookIdError> { store.read(id) }
            assertEquals(id, failure.notebookId)
            assertTrue(failure.details.containsKey("reason"))
        }
    }

    /** A blank id on create is not an unsafe id: it asks for an id derived from the title. */
    @Test
    fun aBlankIdOnCreateIsDerivedFromTheTitle() {
        val store = NotebookStore(newNotebooksRoot())
        val created = store.create("   ", "My Notebook", "the description")
        assertEquals("my-notebook", created.notebookId)
    }

    /** Adding a source twice changes nothing. */
    @Test
    fun addingASourceTwiceChangesNothing() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        val first = store.addSource("my-notebook", "source-1")
        val second = store.addSource("my-notebook", "source-1")
        assertEquals(listOf("source-1"), first.sourceIds)
        assertEquals(first, second)
        assertEquals(listOf("source-1"), store.read("my-notebook").sourceIds)
    }

    /** Removing a source the notebook does not hold is refused. */
    @Test
    fun removingASourceTheNotebookDoesNotHoldIsRefused() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        assertFailsWith<NotebookError> { store.removeSource("my-notebook", "source-1") }

        store.addSource("my-notebook", "source-1")
        val removed = store.removeSource("my-notebook", "source-1")
        assertEquals(emptyList(), removed.sourceIds)
    }

    /** Adding a note twice changes nothing. */
    @Test
    fun addingANoteTwiceChangesNothing() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        val first = store.addNote("my-notebook", "folder/note.md")
        val second = store.addNote("my-notebook", "folder/note.md")
        assertEquals(listOf("folder/note.md"), first.noteIds)
        assertEquals(first, second)
        assertEquals(listOf("folder/note.md"), store.read("my-notebook").noteIds)
    }

    /** Removing a note the notebook does not hold is refused. */
    @Test
    fun removingANoteTheNotebookDoesNotHoldIsRefused() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        assertFailsWith<NotebookError> { store.removeNote("my-notebook", "folder/note.md") }

        store.addNote("my-notebook", "folder/note.md")
        val removed = store.removeNote("my-notebook", "folder/note.md")
        assertEquals(emptyList(), removed.noteIds)
    }

    /** The metadata of a notebook is changed. */
    @Test
    fun theMetadataOfANotebookIsChanged() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        val changed = store.setMetadata("my-notebook", "Another Title", "another description")
        assertEquals("Another Title", changed.title)
        assertEquals("another description", changed.description)
        assertEquals("Another Title", store.read("my-notebook").title)
    }

    /** Deleting a notebook removes its own directory and touches nothing else. */
    @Test
    fun deletingANotebookRemovesItsDirectoryAndTouchesNothingElse() {
        val root = newNotebooksRoot()
        val store = NotebookStore(root)
        store.create("kept", "Kept", "the description")
        store.create("gone", "Gone", "the description")
        store.addSource("gone", "source-1")
        store.addNote("gone", "folder/note.md")

        // The sources and the notes are the operator's and live outside the notebook.
        Files.createDirectories(root.resolve("sources"))
        Files.writeString(root.resolve("sources/source-1.txt"), "the source", StandardCharsets.UTF_8)
        Files.createDirectories(root.resolve("folder"))
        Files.writeString(root.resolve("folder/note.md"), "the note", StandardCharsets.UTF_8)

        store.delete("gone")

        assertTrue(Files.notExists(root.resolve("gone")))
        assertTrue(Files.isRegularFile(root.resolve("kept").resolve(MANIFEST_FILENAME)))
        assertTrue(Files.isRegularFile(root.resolve("sources/source-1.txt")))
        assertTrue(Files.isRegularFile(root.resolve("folder/note.md")))
        assertEquals(listOf("kept"), store.list().map { it.notebookId })
    }

    /** The notebooks are ordered by id, and a directory without a manifest is not a notebook. */
    @Test
    fun theNotebooksAreOrderedByIdAndADirectoryWithoutAManifestIsSkipped() {
        val root = newNotebooksRoot()
        val store = NotebookStore(root)
        store.create("second", "Second", "the description")
        store.create("first", "First", "the description")
        Files.createDirectories(root.resolve("not-a-notebook"))
        Files.writeString(root.resolve("not-a-notebook/other.txt"), "not a notebook")

        assertEquals(listOf("first", "second"), store.list().map { it.notebookId })
    }

    /** The summary of a notebook counts what it holds. */
    @Test
    fun theSummaryOfANotebookCountsWhatItHolds() {
        val store = NotebookStore(newNotebooksRoot())
        store.create("my-notebook", "The Title", "the description")
        store.addSource("my-notebook", "source-1")
        store.addNote("my-notebook", "folder/note.md")

        val summary = store.list().single()
        assertEquals("my-notebook", summary.notebookId)
        assertEquals("The Title", summary.title)
        assertEquals("the description", summary.description)
        assertEquals(1, summary.sourceCount)
        assertEquals(1, summary.noteCount)
    }

    /** A manifest that cannot be parsed is an error, not a notebook that is quietly missing. */
    @Test
    fun aManifestThatCannotBeParsedIsAnError() {
        val root = newNotebooksRoot()
        Files.createDirectories(root.resolve("broken"))
        Files.writeString(
            root.resolve("broken").resolve(MANIFEST_FILENAME),
            "{ this is not json",
            StandardCharsets.UTF_8,
        )
        val store = NotebookStore(root)
        assertFailsWith<NotebookError> { store.list() }
        assertFailsWith<NotebookError> { store.read("broken") }
    }

    /** The created_at is set once, and the modified_at moves on a write. */
    @Test
    fun theCreatedAtIsSetOnceAndTheModifiedAtMovesOnAWrite() {
        val store = NotebookStore(newNotebooksRoot())
        val created = store.create("my-notebook", "The Title", "the description")
        assertEquals(created.createdAt, created.modifiedAt)

        Thread.sleep(30)
        val changed = store.setMetadata("my-notebook", "Another Title", "another description")
        assertEquals(created.createdAt, changed.createdAt)
        assertTrue(changed.modifiedAt > created.modifiedAt)

        val read = store.read("my-notebook")
        assertEquals(created.createdAt, read.createdAt)
        assertEquals(changed.modifiedAt, read.modifiedAt)
    }

    /** A notebooks folder that is not there holds no notebooks. */
    @Test
    fun aNotebooksFolderThatIsNotThereHoldsNoNotebooks() {
        val store = NotebookStore(newNotebooksRoot().resolve("missing"))
        assertEquals(emptyList(), store.list())
    }
}
