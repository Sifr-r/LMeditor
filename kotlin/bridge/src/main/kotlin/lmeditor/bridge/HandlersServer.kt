package lmeditor.bridge

import lmeditor.core.ValidationError
import lmeditor.core.toWireMap
import lmeditor.server.SourceLibrary
import lmeditor.server.SourceArchive
import lmeditor.server.storedSourceFromWire
import lmeditor.server.storedSourceToWire
import java.nio.file.Path

/**
 * The server methods of the seam.
 *
 * The source library is the server's memory of what has been ingested. It used
 * to be a Python dictionary in the server process; it is now one registry in
 * the long-lived tier, and these methods are the only way in and out of it.
 *
 * The rules these methods own, and why each one is the tier's:
 *
 * - **Persistence and cache eviction.** An explicit absolute root selects an
 *   archive-backed registry. Cache eviction never deletes archived evidence.
 *   Rootless consumers retain the legacy bounded memory registry.
 * - **The ordering.** Every stored source comes back oldest first. The tier
 *   holds the entries and the timestamps they were added at, so the tier
 *   decides what "oldest" means.
 * - **The chunk lookup.** The chunks of the named sources, in the order the
 *   ids were given, with an id the library does not hold skipped rather than
 *   refused. The tier holds the sources, so the tier knows which ids it has.
 *
 * The library itself is the tier's singleton, the same way the Python tier's
 * was a process-wide instance: one registry for the whole process. The Python
 * door proxies it; it keeps no second copy of the sources.
 *
 * Invalid arguments raise [ValidationError]. Archive failures retain their
 * contextual domain type across the bridge rather than looking like absence.
 */
internal fun serverHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    "server.sources.add" to { params ->
        // The source arrives in its wire form and is rebuilt into the model,
        // which is where the model's own invariants are enforced. The library
        // then decides what to keep: the eviction rule is the tier's.
        val stored = storedSourceFromWire(requiredObject(params, "source"))
        mapOf("source" to storedSourceToWire(sourceLibraryFor(params).add(stored)))
    },

    "server.sources.get" to { params ->
        val source = sourceLibraryFor(params).get(requiredString(params, "source_id"))
        mapOf("source" to source?.let { storedSourceToWire(it) })
    },

    "server.sources.remove" to { params ->
        mapOf("removed" to sourceLibraryFor(params).remove(requiredString(params, "source_id")))
    },

    "server.sources.list" to { params ->
        // The tier holds the timestamps, so the tier decides the order.
        mapOf("sources" to sourceLibraryFor(params).list().map { storedSourceToWire(it) })
    },

    "server.sources.chunks_for" to { params ->
        // The ids keep the order they were given, and an id the library does
        // not hold is skipped rather than refused: a client may name a source
        // it uploaded before the server restarted, and a missing source should
        // not abort a request that has other, valid material.
        val chunks = sourceLibraryFor(params).chunksFor(requiredStringList(params, "source_ids"))
        mapOf("chunks" to chunks.map { it.toWireMap() })
    },

    "server.sources.clear" to { params ->
        sourceLibraryFor(params).clear()
        mapOf("ok" to true)
    },

    "server.sources.len" to { params ->
        mapOf("count" to sourceLibraryFor(params).size)
    },
)

/**
 * The tier's source library.
 *
 * One registry for the whole process, the same way the Python tier had one
 * registry for the whole process.
 */
private val sourceLibrary: SourceLibrary = SourceLibrary()

/** Only the selected archive is retained; changing roots cannot grow a registry cache. */
private var archiveRoot: Path? = null
private var archivedLibrary: SourceLibrary? = null
private val archiveSelectionLock = Any()

/** Rootless bridge consumers retain the legacy in-memory registry. */
private fun sourceLibraryFor(params: Map<String, Any?>): SourceLibrary {
    if (!params.containsKey("root")) return sourceLibrary
    val value = requiredString(params, "root")
    val root = try {
        Path.of(value)
    } catch (exc: java.nio.file.InvalidPathException) {
        throw ValidationError("the source archive root is not a valid path", field = "root", value = value)
    }
    if (value.isBlank() || !root.isAbsolute) {
        throw ValidationError("the source archive root must be an absolute path", field = "root", value = value)
    }
    val normalized = root.normalize()
    return synchronized(archiveSelectionLock) {
        if (archiveRoot != normalized || archivedLibrary == null) {
            val replacement = SourceLibrary(archive = SourceArchive(normalized))
            archivedLibrary = replacement
            archiveRoot = normalized
        }
        archivedLibrary!!
    }
}

/**
 * The object a request must carry under a key.
 *
 * @param params the request's parameters
 * @param key the key the request must carry
 * @return the object under the key
 * @throws ValidationError if the key is absent or does not hold an object
 */
@Suppress("UNCHECKED_CAST")
private fun requiredObject(params: Map<String, Any?>, key: String): Map<String, Any?> {
    val value = params[key]
    if (value !is Map<*, *>) {
        throw ValidationError(
            "the request must carry an object under '$key'",
            field = key,
            value = value,
        )
    }
    return value as Map<String, Any?>
}

/**
 * The string a request must carry under a key.
 *
 * @param params the request's parameters
 * @param key the key the request must carry
 * @return the string under the key
 * @throws ValidationError if the key is absent or does not hold a string
 */
private fun requiredString(params: Map<String, Any?>, key: String): String {
    val value = params[key]
    if (value !is String) {
        throw ValidationError(
            "the request must carry a string under '$key'",
            field = key,
            value = value,
        )
    }
    return value
}

/**
 * The array of strings a request must carry under a key.
 *
 * @param params the request's parameters
 * @param key the key the request must carry
 * @return the strings under the key, in the order they were given
 * @throws ValidationError if the key is absent or does not hold an array of
 *   strings
 */
private fun requiredStringList(params: Map<String, Any?>, key: String): List<String> {
    val value = params[key]
    if (value !is List<*>) {
        throw ValidationError(
            "the request must carry an array under '$key'",
            field = key,
            value = value,
        )
    }
    return value.map { entry ->
        entry as? String
            ?: throw ValidationError(
                "every entry of '$key' must be a string",
                field = key,
                value = entry,
            )
    }
}
