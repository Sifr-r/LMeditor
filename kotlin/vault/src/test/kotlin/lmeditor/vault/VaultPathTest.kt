package lmeditor.vault

import org.junit.jupiter.api.Assumptions.assumeTrue
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The path rules of a vault: what a note id may be, and how it resolves.
 */
class VaultPathTest {

    /** A note id that walks out of the vault is refused. */
    @Test
    fun aNoteIdWithAParentSegmentIsRefused() {
        for (id in listOf("..", ".", "../evil", "folder/../../evil", "folder/..", "a/../b")) {
            assertFailsWith<UnsafeNoteIdError>("'$id' must be refused") {
                VaultPath.validateNoteId(id)
            }
        }
    }

    /** An absolute note id is refused. */
    @Test
    fun anAbsoluteNoteIdIsRefused() {
        for (id in listOf("/etc/passwd", "//server/share", "C:/Windows", "c:\\Windows", "\\\\server\\share")) {
            assertFailsWith<UnsafeNoteIdError>("'$id' must be refused") {
                VaultPath.validateNoteId(id)
            }
        }
    }

    /** A note id with a character Windows forbids is refused. */
    @Test
    fun aNoteIdWithAForbiddenCharacterIsRefused() {
        val forbidden = listOf(
            "note:x",
            "a:b",
            "note?x",
            "note*x",
            "note<x",
            "note>x",
            "note\"x",
            "note|x",
            "name.",
            "name ",
        )
        for (id in forbidden) {
            assertFailsWith<UnsafeNoteIdError>("'$id' must be refused") {
                VaultPath.validateNoteId(id)
            }
        }
    }

    /** A note id with a control character in it is refused. */
    @Test
    fun aNoteIdWithAControlCharacterIsRefused() {
        val withBell = "with" + 7.toChar() + "bel"
        assertFailsWith<UnsafeNoteIdError> {
            VaultPath.validateNoteId(withBell)
        }
    }

    /** A hidden note id is refused: a vault never writes into a hidden file. */
    @Test
    fun aHiddenNoteIdIsRefused() {
        for (id in listOf(".hidden", "folder/.hidden", ".obsidian/config")) {
            assertFailsWith<UnsafeNoteIdError>("'$id' must be refused") {
                VaultPath.validateNoteId(id)
            }
        }
    }

    /** A note id that is blank is refused. */
    @Test
    fun aBlankNoteIdIsRefused() {
        for (id in listOf("", "   ")) {
            assertFailsWith<UnsafeNoteIdError>("'$id' must be refused") {
                VaultPath.validateNoteId(id)
            }
        }
    }

    /** A safe note id is accepted, unchanged. */
    @Test
    fun aSafeNoteIdIsAccepted() {
        for (id in listOf("note", "folder/note", "folder/sub/note", "note-2024", "a.b.c", "Note With Spaces")) {
            assertEquals(id, VaultPath.validateNoteId(id))
        }
    }

    /** A note id that leaves the vault through a link is refused. */
    @Test
    fun aNoteIdThatLeavesTheVaultThroughALinkIsRefused() {
        val vault = newVault()
        val outside = newVault()
        val link = vault.resolve("escape")
        assumeTrue(
            createLink(outside, link),
            "this machine does not allow a link to be created",
        )
        val store = VaultStore(vault)
        assertFailsWith<UnsafeNoteIdError>("a link must not be a way out of the vault") {
            store.writeNote("escape/note", "text")
        }
        assertTrue(Files.notExists(outside.resolve("note.md")))
    }

    /** A note id resolves to a file inside the vault. */
    @Test
    fun aNoteIdResolvesInsideTheVault() {
        val vault = newVault()
        val store = VaultStore(vault)
        store.writeNote("folder/note", "text")
        val file = VaultPath.resolve(vault, "folder/note")
        assertTrue(file.startsWith(VaultPath.realRoot(vault)))
        assertTrue(Files.isRegularFile(file))
    }

    /** A vault folder that is not there is refused. */
    @Test
    fun aVaultThatIsNotThereIsRefused() {
        val missing = newVault().resolve("not-a-vault")
        assertFailsWith<VaultNotFoundError> {
            VaultPath.realRoot(missing)
        }
    }
}

/** Return a fresh temporary folder to hold a vault. */
internal fun newVault(): Path = Files.createTempDirectory("lmeditor-vault")

/**
 * Create a link at [link] that points at [target], and report whether it worked.
 *
 * A symbolic link is the first thing to try. When the machine does not allow
 * one to be created -- Windows wants the administrator's privilege for that
 * -- a junction is made instead: it needs none, and the vault has to refuse
 * it just the same.
 */
internal fun createLink(target: Path, link: Path): Boolean = try {
    Files.createSymbolicLink(link, target)
    true
} catch (exc: IOException) {
    createJunction(target, link)
} catch (exc: UnsupportedOperationException) {
    createJunction(target, link)
}

/** Create a junction at [link] that points at [target], and report whether it worked. */
private fun createJunction(target: Path, link: Path): Boolean {
    if (!System.getProperty("os.name").lowercase().startsWith("windows")) {
        return false
    }
    return try {
        val process = ProcessBuilder("cmd", "/c", "mklink", "/J", link.toString(), target.toString())
            .redirectErrorStream(true)
            .start()
        process.inputStream.readBytes()
        process.waitFor() == 0 && Files.exists(link)
    } catch (exc: IOException) {
        false
    } catch (exc: InterruptedException) {
        Thread.currentThread().interrupt()
        false
    }
}
