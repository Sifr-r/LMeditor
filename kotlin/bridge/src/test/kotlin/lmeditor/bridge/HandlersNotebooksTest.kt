package lmeditor.bridge

import lmeditor.core.json.Json
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * The notebook methods of the seam.
 *
 * A notebook is a folder of notebooks on the operator's disk, so these tests
 * make one, write notebooks into it, and ask the seam about them.
 */
class HandlersNotebooksTest {

    private val bridge = Bridge()

    private fun call(method: String, params: String = "{}"): Map<String, Any?> {
        val request = """{"id": "7", "method": "$method", "params": $params}"""
        return Json.parseObject(bridge.handle(request))
    }

    /** Return a fresh folder to hold the notebooks. */
    private fun newNotebooks(): Path = Files.createTempDirectory("lmeditor-bridge-notebooks")

    /** Return the JSON fragment that names the notebooks folder. */
    private fun rootOf(root: Path): String =
        """"root": "${root.toString().replace("\\", "\\\\")}""""

    @Test
    fun `the notebooks are listed`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val response = call("notebooks.list", "{${rootOf(root)}}")
        assertTrue(response.jsonBool("ok"))
        val notebooks = response.jsonObject("result").jsonObjectList("notebooks")
        assertEquals(1, notebooks.size)
        assertEquals("my-notebook", notebooks.first().jsonString("notebook_id"))
        assertEquals("My Notebook", notebooks.first().jsonString("title"))
        assertEquals("about", notebooks.first().jsonString("description"))
        assertEquals(0, notebooks.first().jsonInt("source_count"))
        assertEquals(0, notebooks.first().jsonInt("note_count"))
    }

    @Test
    fun `a notebook is read`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val response = call(
            "notebooks.read",
            """{${rootOf(root)}, "notebook_id": "my-notebook"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val notebook = response.jsonObject("result").jsonObject("notebook")
        assertEquals("my-notebook", notebook.jsonString("notebook_id"))
        assertEquals("My Notebook", notebook.jsonString("title"))
        assertEquals("about", notebook.jsonString("description"))
        assertEquals(emptyList(), notebook.jsonStringList("source_ids"))
        assertEquals(emptyList(), notebook.jsonStringList("note_ids"))
    }

    @Test
    fun `a notebook is created with the id it is given`() {
        val root = newNotebooks()
        val response = call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val notebook = response.jsonObject("result").jsonObject("notebook")
        assertEquals("my-notebook", notebook.jsonString("notebook_id"))
        assertTrue(Files.isRegularFile(root.resolve("my-notebook").resolve("manifest.json")))
    }

    @Test
    fun `a notebook is created with an id derived from its title`() {
        val root = newNotebooks()
        val response = call(
            "notebooks.create",
            """{${rootOf(root)}, "title": "My Notebook", "description": "about"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val notebook = response.jsonObject("result").jsonObject("notebook")
        assertEquals("my-notebook", notebook.jsonString("notebook_id"))
        assertTrue(Files.isRegularFile(root.resolve("my-notebook").resolve("manifest.json")))
    }

    @Test
    fun `the metadata of a notebook is changed`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val response = call(
            "notebooks.set_metadata",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "Another Title", "description": "other"}""",
        )
        assertTrue(response.jsonBool("ok"))
        val notebook = response.jsonObject("result").jsonObject("notebook")
        assertEquals("Another Title", notebook.jsonString("title"))
        assertEquals("other", notebook.jsonString("description"))
    }

    @Test
    fun `a source is added and removed`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val added = call(
            "notebooks.add_source",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "source_id": "source-1"}""",
        )
        assertTrue(added.jsonBool("ok"))
        assertEquals(
            listOf("source-1"),
            added.jsonObject("result").jsonObject("notebook").jsonStringList("source_ids"),
        )

        val removed = call(
            "notebooks.remove_source",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "source_id": "source-1"}""",
        )
        assertTrue(removed.jsonBool("ok"))
        assertEquals(
            emptyList(),
            removed.jsonObject("result").jsonObject("notebook").jsonStringList("source_ids"),
        )
    }

    @Test
    fun `a note is added and removed`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val added = call(
            "notebooks.add_note",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "note_id": "folder/note.md"}""",
        )
        assertTrue(added.jsonBool("ok"))
        assertEquals(
            listOf("folder/note.md"),
            added.jsonObject("result").jsonObject("notebook").jsonStringList("note_ids"),
        )

        val removed = call(
            "notebooks.remove_note",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "note_id": "folder/note.md"}""",
        )
        assertTrue(removed.jsonBool("ok"))
        assertEquals(
            emptyList(),
            removed.jsonObject("result").jsonObject("notebook").jsonStringList("note_ids"),
        )
    }

    @Test
    fun `a notebook is deleted`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val response = call(
            "notebooks.delete",
            """{${rootOf(root)}, "notebook_id": "my-notebook"}""",
        )
        assertTrue(response.jsonBool("ok"))
        assertTrue(response.jsonObject("result").jsonBool("deleted"))
        assertTrue(Files.notExists(root.resolve("my-notebook")))
    }

    @Test
    fun `a request that names no root is refused`() {
        val response = call("notebooks.list")
        assertFalse(response.jsonBool("ok"))
        assertEquals("ValidationError", response.jsonObject("error").jsonString("type"))
    }

    @Test
    fun `a notebook that is not there is refused across the seam`() {
        val root = newNotebooks()
        val response = call(
            "notebooks.read",
            """{${rootOf(root)}, "notebook_id": "missing"}""",
        )
        assertFalse(response.jsonBool("ok"))
        val error = response.jsonObject("error")
        assertEquals("NotebookNotFoundError", error.jsonString("type"))
        assertEquals("missing", error.jsonObject("details").jsonString("notebook_id"))
    }

    @Test
    fun `an unsafe notebook id is refused across the seam`() {
        val root = newNotebooks()
        val response = call(
            "notebooks.read",
            """{${rootOf(root)}, "notebook_id": "../escape"}""",
        )
        assertFalse(response.jsonBool("ok"))
        val error = response.jsonObject("error")
        assertEquals("UnsafeNotebookIdError", error.jsonString("type"))
        assertEquals("../escape", error.jsonObject("details").jsonString("notebook_id"))
    }

    @Test
    fun `removing a source the notebook does not hold is refused across the seam`() {
        val root = newNotebooks()
        call(
            "notebooks.create",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "title": "My Notebook", "description": "about"}""",
        )
        val response = call(
            "notebooks.remove_source",
            """{${rootOf(root)}, "notebook_id": "my-notebook", "source_id": "source-1"}""",
        )
        assertFalse(response.jsonBool("ok"))
        assertEquals("NotebookError", response.jsonObject("error").jsonString("type"))
    }

    @Test
    fun `a manifest that cannot be parsed is refused across the seam`() {
        val root = newNotebooks()
        Files.createDirectories(root.resolve("broken"))
        Files.writeString(
            root.resolve("broken").resolve("manifest.json"),
            "{ this is not json",
            StandardCharsets.UTF_8,
        )
        val response = call("notebooks.list", "{${rootOf(root)}}")
        assertFalse(response.jsonBool("ok"))
        assertEquals("NotebookError", response.jsonObject("error").jsonString("type"))
    }
}
