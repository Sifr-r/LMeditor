package lmeditor.bridge

import lmeditor.core.ValidationError
import lmeditor.core.json.jsonString
import lmeditor.core.toWireMap
import lmeditor.notebooks.NotebookStore
import java.nio.file.Path

/**
 * The notebook methods of the seam.
 *
 * A notebook is a named collection of sources and notes on the operator's
 * disk. The rules that make such a folder a notebook -- the safety of a
 * notebook id, how an id is derived from a title, the manifest and the store
 * that reads and writes it -- are the tier's, and these methods are the only
 * way in and out of them.
 *
 * Every method is handed the notebooks' root, because the tier keeps no state
 * of its own: the server holds the open notebooks folder, and the tier is
 * told which folder to work in. A request that names no root is refused,
 * because there is no notebooks folder to work in and an empty answer would
 * look like an empty set of notebooks.
 *
 * The failures these methods raise are the notebook's own, and they cross the
 * seam as domain errors: a notebook that is not there is a
 * [lmeditor.notebooks.NotebookNotFoundError], a notebook id that is not a
 * safe path segment is an [lmeditor.notebooks.UnsafeNotebookIdError], and a
 * manifest that cannot be read is a [lmeditor.notebooks.NotebookError].
 */
internal fun notebookHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    "notebooks.list" to { params ->
        val notebooks = notebookStoreFor(params).list()
        mapOf("notebooks" to notebooks.map { it.toWireMap() })
    },

    "notebooks.read" to { params ->
        val notebook = notebookStoreFor(params).read(params.jsonString("notebook_id"))
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.create" to { params ->
        val notebook = notebookStoreFor(params).create(
            notebookId = params.jsonString("notebook_id").ifBlank { null },
            title = params.jsonString("title"),
            description = params.jsonString("description"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.set_metadata" to { params ->
        val notebook = notebookStoreFor(params).setMetadata(
            notebookId = params.jsonString("notebook_id"),
            title = params.jsonString("title"),
            description = params.jsonString("description"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.add_source" to { params ->
        val notebook = notebookStoreFor(params).addSource(
            notebookId = params.jsonString("notebook_id"),
            sourceId = params.jsonString("source_id"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.remove_source" to { params ->
        val notebook = notebookStoreFor(params).removeSource(
            notebookId = params.jsonString("notebook_id"),
            sourceId = params.jsonString("source_id"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.add_note" to { params ->
        val notebook = notebookStoreFor(params).addNote(
            notebookId = params.jsonString("notebook_id"),
            noteId = params.jsonString("note_id"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.remove_note" to { params ->
        val notebook = notebookStoreFor(params).removeNote(
            notebookId = params.jsonString("notebook_id"),
            noteId = params.jsonString("note_id"),
        )
        mapOf("notebook" to notebook.toWireMap())
    },

    "notebooks.delete" to { params ->
        notebookStoreFor(params).delete(params.jsonString("notebook_id"))
        mapOf("deleted" to true)
    },
)

/**
 * The store of the notebooks a request names.
 *
 * The root is a parameter of the store, not a setting of the process: the
 * operator may open a notebooks folder in any directory, and the seam must
 * work in *that* folder. A request that names no root is refused, because a
 * notebooks folder that was never named is not an empty one -- it is no
 * notebooks folder at all.
 */
private fun notebookStoreFor(params: Map<String, Any?>): NotebookStore {
    val root = params.jsonString("root")
    if (root.isBlank()) {
        throw ValidationError("the request must name the notebooks' root", field = "root")
    }
    return NotebookStore(Path.of(root))
}
