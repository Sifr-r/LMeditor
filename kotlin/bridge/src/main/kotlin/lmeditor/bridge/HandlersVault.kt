package lmeditor.bridge

import lmeditor.core.ValidationError
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonString
import lmeditor.core.toWireMap
import lmeditor.vault.VaultStore
import java.nio.file.Path

/**
 * The vault methods of the seam.
 *
 * A vault is a folder of markdown files on the operator's disk. The rules
 * that make such a folder a vault -- the safety of a note id, the markdown
 * grammar, the store that reads and writes the files -- are the tier's, and
 * these methods are the only way in and out of them.
 *
 * Every method is handed the vault's root, because the tier keeps no state of
 * its own: the server holds the active vault, and the tier is told which
 * folder to work in. A request that names no root is refused, because there
 * is no vault to work in and an empty answer would look like an empty vault.
 *
 * The failures these methods raise are the vault's own, and they cross the
 * seam as domain errors: a note that is not there is a [lmeditor.vault.NoteNotFoundError],
 * a note id that tries to leave the vault is an [lmeditor.vault.UnsafeNoteIdError],
 * and a vault folder that is not there is a [lmeditor.vault.VaultNotFoundError].
 */
internal fun vaultHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    "vault.list_notes" to { params ->
        val notes = vaultStoreFor(params).listNotes()
        mapOf("notes" to notes.map { it.toWireMap() })
    },

    "vault.read_note" to { params ->
        val note = vaultStoreFor(params).readNote(params.jsonString("note_id"))
        mapOf("note" to note.toWireMap())
    },

    "vault.write_note" to { params ->
        val note = vaultStoreFor(params).writeNote(
            noteId = params.jsonString("note_id"),
            content = params.jsonString("content"),
        )
        mapOf("note" to note.toWireMap())
    },

    "vault.create_note" to { params ->
        val note = vaultStoreFor(params).createNote(
            noteId = params.jsonString("note_id"),
            title = params.jsonString("title"),
            content = params.jsonString("content"),
        )
        mapOf("note" to note.toWireMap())
    },

    "vault.delete_note" to { params ->
        vaultStoreFor(params).deleteNote(params.jsonString("note_id"))
        mapOf("deleted" to true)
    },

    "vault.rename_note" to { params ->
        val result = vaultStoreFor(params).renameNote(
            fromId = params.jsonString("from_id"),
            toId = params.jsonString("to_id"),
            updateLinks = params.jsonBool("update_links", false),
        )
        mapOf(
            "from_id" to result.fromId,
            "to_id" to result.toId,
            "updated_links" to result.updatedLinks,
        )
    },

    "vault.search" to { params ->
        val hits = vaultStoreFor(params).search(
            query = params.jsonString("query"),
            limit = params.jsonInt("limit", DEFAULT_SEARCH_LIMIT),
        )
        mapOf("results" to hits.map { it.toWireMap() })
    },

    "vault.tags" to { params ->
        val tags = vaultStoreFor(params).tags()
        mapOf("tags" to tags.map { it.toWireMap() })
    },
)

/**
 * The store of the vault a request names.
 *
 * The root is a parameter of the store, not a setting of the process: the
 * operator may open a vault in any folder, and the seam must work in *that*
 * vault. A request that names no root is refused, because a vault that was
 * never named is not an empty vault -- it is no vault at all.
 */
private fun vaultStoreFor(params: Map<String, Any?>): VaultStore {
    val root = params.jsonString("root")
    if (root.isBlank()) {
        throw ValidationError("the request must name the vault's root", field = "root")
    }
    return VaultStore(Path.of(root))
}

/** The number of hits a search returns when the request names no limit. */
private const val DEFAULT_SEARCH_LIMIT: Int = 50
