package lmeditor.notebooks

import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/**
 * The id rule of a notebook: what an id may be, and how one is derived.
 */
class NotebookPathTest {

    /** A plain id is accepted. */
    @Test
    fun aPlainIdIsAccepted() {
        assertEquals("my-notebook", NotebookPath.validateNotebookId("my-notebook"))
    }

    /** The surrounding whitespace is stripped, so the id that is stored is the id that is written. */
    @Test
    fun theSurroundingWhitespaceIsStripped() {
        assertEquals("my-notebook", NotebookPath.validateNotebookId("  my-notebook  "))
    }

    /** An id of 128 characters is the longest there is. */
    @Test
    fun anIdOf128CharactersIsAccepted() {
        val id = "a".repeat(128)
        assertEquals(id, NotebookPath.validateNotebookId(id))
    }

    /** An id of 129 characters is refused. */
    @Test
    fun anIdOf129CharactersIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> {
            NotebookPath.validateNotebookId("a".repeat(129))
        }
    }

    /** A blank id is refused. */
    @Test
    fun aBlankIdIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("") }
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("   ") }
    }

    /** The relative path segments are refused: an id is never the root or its parent. */
    @Test
    fun theRelativePathSegmentsAreRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId(".") }
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("..") }
    }

    /** A path separator is refused, whichever way it is written. */
    @Test
    fun aPathSeparatorIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("a/b") }
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("a\\b") }
    }

    /** A control character is refused. */
    @Test
    fun aControlCharacterIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("a\u0000b") }
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("a\u001Fb") }
    }

    /** Every character a Windows path forbids is refused. */
    @Test
    fun theCharactersAWindowsPathForbidsAreRefused() {
        for (character in listOf("<", ">", ":", "\"", "|", "?", "*")) {
            assertFailsWith<UnsafeNotebookIdError> {
                NotebookPath.validateNotebookId("a${character}b")
            }
        }
    }

    /** A drive letter is refused. */
    @Test
    fun aDriveLetterIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("C:") }
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("C:\\notebooks") }
    }

    /** A reserved device name is refused, and an extension does not save it. */
    @Test
    fun aReservedDeviceNameIsRefused() {
        for (name in listOf("CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "nul.json")) {
            assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId(name) }
        }
    }

    /** A trailing dot is refused: Windows would strip it and the two names would be one folder. */
    @Test
    fun aTrailingDotIsRefused() {
        assertFailsWith<UnsafeNotebookIdError> { NotebookPath.validateNotebookId("notebook.") }
    }

    /** A title yields a lower-case slug. */
    @Test
    fun aTitleYieldsASlug() {
        assertEquals("my-notebook", NotebookPath.deriveNotebookId("My Notebook"))
    }

    /** A run of characters that is not a letter or a digit is one dash. */
    @Test
    fun aRunOfOtherCharactersIsOneDash() {
        assertEquals("my-notebook", NotebookPath.deriveNotebookId("My  ---  Notebook"))
    }

    /** The leading and the trailing dashes are removed. */
    @Test
    fun theLeadingAndTrailingDashesAreRemoved() {
        assertEquals("my-notebook", NotebookPath.deriveNotebookId("  My Notebook  "))
        assertEquals("my-notebook", NotebookPath.deriveNotebookId("--My Notebook--"))
    }

    /** A title that yields nothing yields the fallback id. */
    @Test
    fun aTitleThatYieldsNothingYieldsTheFallbackId() {
        assertEquals("notebook", NotebookPath.deriveNotebookId(""))
        assertEquals("notebook", NotebookPath.deriveNotebookId("!!!"))
    }

    /** A derived id is always a safe id. */
    @Test
    fun aDerivedIdIsAlwaysASafeId() {
        for (title in listOf("My Notebook", "  ", "!!!", "Ünïcödé", "a".repeat(400))) {
            val derived = NotebookPath.deriveNotebookId(title)
            assertEquals(derived, NotebookPath.validateNotebookId(derived))
        }
    }
}

/** Return a fresh temporary folder to hold the notebooks. */
internal fun newNotebooksRoot(): Path = Files.createTempDirectory("lmeditor-notebooks")
