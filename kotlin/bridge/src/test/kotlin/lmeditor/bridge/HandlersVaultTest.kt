package lmeditor.bridge

import lmeditor.core.json.Json
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The vault methods of the seam.
 *
 * A vault is a folder of markdown files on the operator's disk, so these
 * tests make one, write files into it, and ask the seam about it.
 */
class HandlersVaultTest {

    private val bridge = Bridge()

    private fun call(method: String, params: String = "{}"): Map<String, Any?> {
        val request = """{"id": "7", "method": "$method", "params": $params}"""
        return Json.parseObject(bridge.handle(request))
    }

    /** Return a fresh folder to hold a vault. */
    private fun newVault(): Path = Files.createTempDirectory("lmeditor-bridge-vault")

    /** Return the JSON fragment that names a vault. */
    private fun rootOf(vault: Path): String =
        """"root": "${vault.toString().replace("\\", "\\\\")}""""

    @Test
    fun `the notes of a vault are listed`() {
        val vault = newVault()
        Files.writeString(vault.resolve("note.md"), "# A Note\n")
        val response = call("vault.list_notes", "{${rootOf(vault)}}")
        assertTrue(response.jsonBool("ok"))
        val notes = response.jsonObject("result").jsonObjectList("notes")
        assertEquals(1, notes.size)
        assertEquals("note", notes.first().jsonString("note_id"))
        assertEquals("A Note", notes.first().jsonString("title"))
        assertEquals("note.md", notes.first().jsonString("path"))
    }

    @Test
    fun `a note of a vault is read`() {
        val vault = newVault()
        Files.writeString(vault.resolve("note.md"), "# A Note\n\nSee [[other]].\n")
        Files.writeString(vault.resolve("other.md"), "# Other\n")
        val response = call(
            "vault.read_note",
            """{${rootOf(vault)}, "note_id": "note"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val note = response.jsonObject("result").jsonObject("note")
        assertEquals("note", note.jsonString("note_id"))
        assertEquals("A Note", note.jsonString("title"))
        assertEquals(listOf("other"), note.jsonStringList("links"))
        assertTrue(note.jsonString("content").contains("# A Note"))
    }

    @Test
    fun `a note is written`() {
        val vault = newVault()
        val response = call(
            "vault.write_note",
            """{${rootOf(vault)}, "note_id": "folder/note", "content": "# A Note\n\n#idea\n"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val note = response.jsonObject("result").jsonObject("note")
        assertEquals("folder/note", note.jsonString("note_id"))
        assertEquals("A Note", note.jsonString("title"))
        assertEquals(listOf("idea"), note.jsonStringList("tags"))
        assertTrue(Files.readString(vault.resolve("folder/note.md")).contains("# A Note"))
    }

    @Test
    fun `a note is created`() {
        val vault = newVault()
        val response = call(
            "vault.create_note",
            """{${rootOf(vault)}, "note_id": "note", "title": "A Note", "content": "the body"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val note = response.jsonObject("result").jsonObject("note")
        assertEquals("A Note", note.jsonString("title"))
        assertTrue(Files.exists(vault.resolve("note.md")))
    }

    @Test
    fun `a note is deleted`() {
        val vault = newVault()
        Files.writeString(vault.resolve("note.md"), "# A Note\n")
        val response = call("vault.delete_note", """{${rootOf(vault)}, "note_id": "note"}""")
        assertTrue(response.jsonBool("ok"))
        assertTrue(response.jsonObject("result").jsonBool("deleted"))
        assertTrue(Files.notExists(vault.resolve("note.md")))
    }

    @Test
    fun `a note is renamed and the links are rewritten`() {
        val vault = newVault()
        Files.writeString(vault.resolve("old.md"), "# Old\n")
        Files.writeString(vault.resolve("other.md"), "# Other\n\nSee [[old]].\n")
        val response = call(
            "vault.rename_note",
            """{${rootOf(vault)}, "from_id": "old", "to_id": "new", "update_links": true}""",
        )
        assertTrue(response.jsonBool("ok"))
        val result = response.jsonObject("result")
        assertEquals("old", result.jsonString("from_id"))
        assertEquals("new", result.jsonString("to_id"))
        assertEquals(1, result.jsonInt("updated_links"))
        assertTrue(Files.notExists(vault.resolve("old.md")))
        assertTrue(Files.readString(vault.resolve("other.md")).contains("[[new]]"))
    }

    @Test
    fun `a vault is searched`() {
        val vault = newVault()
        Files.writeString(vault.resolve("note.md"), "# Alpha\n\nthe body\n")
        val response = call(
            "vault.search",
            """{${rootOf(vault)}, "query": "alpha", "limit": 5}""",
        )
        assertTrue(response.jsonBool("ok"))
        val results = response.jsonObject("result").jsonObjectList("results")
        assertEquals(1, results.size)
        assertEquals("note", results.first().jsonString("note_id"))
        assertEquals(listOf("alpha"), results.first().jsonStringList("matched_terms"))
    }

    @Test
    fun `the tags of a vault are listed`() {
        val vault = newVault()
        Files.writeString(vault.resolve("note.md"), "# A Note\n\n#idea\n")
        val response = call("vault.tags", "{${rootOf(vault)}}")
        assertTrue(response.jsonBool("ok"))
        val tags = response.jsonObject("result").jsonObjectList("tags")
        assertEquals(1, tags.size)
        assertEquals("idea", tags.first().jsonString("tag"))
        assertEquals(1, tags.first().jsonInt("count"))
    }

    @Test
    fun `a note that is not there is refused across the seam`() {
        val vault = newVault()
        val response = call("vault.read_note", """{${rootOf(vault)}, "note_id": "missing"}""")
        assertFalse(response.jsonBool("ok"))
        val error = response.jsonObject("error")
        assertEquals("NoteNotFoundError", error.jsonString("type"))
        assertEquals("missing", error.jsonObject("details").jsonString("note_id"))
    }

    @Test
    fun `a note id that leaves the vault is refused across the seam`() {
        val vault = newVault()
        val response = call(
            "vault.write_note",
            """{${rootOf(vault)}, "note_id": "../escape", "content": "the body"}""",
        )
        assertFalse(response.jsonBool("ok"))
        val error = response.jsonObject("error")
        assertEquals("UnsafeNoteIdError", error.jsonString("type"))
        assertEquals("../escape", error.jsonObject("details").jsonString("note_id"))
    }

    @Test
    fun `a vault that is not there is refused across the seam`() {
        val missing = newVault().resolve("not-a-vault")
        val response = call("vault.list_notes", "{${rootOf(missing)}}")
        assertFalse(response.jsonBool("ok"))
        assertEquals("VaultNotFoundError", response.jsonObject("error").jsonString("type"))
    }
}
