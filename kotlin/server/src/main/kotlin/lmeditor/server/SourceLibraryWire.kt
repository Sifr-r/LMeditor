package lmeditor.server

import lmeditor.core.SourceKind
import lmeditor.core.ValidationError
import lmeditor.core.bibliographicRecordFromWire
import lmeditor.core.sourceChunkFromWire
import lmeditor.core.toWireMap
import java.time.DateTimeException
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneOffset

/**
 * The wire form of the server's source library.
 *
 * The chunk and the bibliographic record are not spelled out here: `:core`
 * already owns the wire form of both, and a second copy of either would be a
 * second vocabulary. This file adds only the envelope that holds them, which
 * is why it lives beside the type it converts rather than in `:core` -- `:core`
 * cannot see `lmeditor.server` without a circular dependency.
 *
 * The rules the converters follow, the same ones `kotlin/word/WIRE.md` pins:
 *
 * - A key is `snake_case` and is named after the Python dataclass field.
 * - An enum travels as its `wire` string, never as its ordinal.
 * - A timestamp travels as an ISO-8601 string.
 * - A field the model declares optional travels as `null` when it has no value,
 *   and a decoder reads an absent key exactly like an explicit `null`.
 * - A decoder never guesses: a required key that is not there is an error, not
 *   a default. A default would let a typo become a silent difference between
 *   the two tiers, which is exactly the drift this file exists to prevent.
 *
 * The converters are public because the seam in the `:bridge` module uses them,
 * and `internal` would stop at the module boundary.
 */

/**
 * The wire form of a [StoredSource].
 *
 * Every field crosses. A stored source that lost one on the way would come
 * back from the tier as a different source, and the Python tier would never
 * know.
 *
 * @param stored the stored source
 * @return the wire form of the source
 */
fun storedSourceToWire(stored: StoredSource): Map<String, Any?> = linkedMapOf(
    "source_id" to stored.sourceId,
    "title" to stored.title,
    "kind" to stored.kind.wire,
    "origin" to stored.origin,
    "chunks" to stored.chunks.map { it.toWireMap() },
    "record" to stored.record?.toWireMap(),
    "byte_size" to stored.byteSize,
    "omissions" to stored.omissions,
    // An instant names one moment, and `Instant.toString()` spells it the way
    // the Python tier reads it back: `datetime.fromisoformat` takes it.
    "added_at" to stored.addedAt.toString(),
)

/**
 * Read a [StoredSource] from the wire.
 *
 * @param map the wire form of the source
 * @return the stored source
 * @throws ValidationError if a required key is missing or a value has the
 *   wrong type
 */
fun storedSourceFromWire(map: Map<String, Any?>): StoredSource = StoredSource(
    sourceId = map.requiredString("source_id"),
    title = map.requiredString("title"),
    kind = SourceKind.fromWire(map.requiredString("kind")),
    origin = map.requiredString("origin"),
    chunks = map.requiredObjectList("chunks").map { sourceChunkFromWire(it) },
    record = map.optionalObject("record")?.let { bibliographicRecordFromWire(it) },
    byteSize = map.optionalInt("byte_size"),
    omissions = map.optionalStringList("omissions"),
    addedAt = instantFromWire(map.requiredString("added_at"), "added_at"),
)

// ---------------------------------------------------------------------------
// The reading half
// ---------------------------------------------------------------------------

/**
 * The value of a key the payload must carry.
 *
 * An absent key and an explicit `null` are the same thing here: neither is a
 * value the model can hold, so neither may be turned into one.
 */
private fun Map<String, Any?>.requiredValue(key: String): Any? =
    this[key] ?: throw ValidationError(
        "the wire payload does not carry the required key '$key'",
        field = key,
    )

/** Read a required string. */
private fun Map<String, Any?>.requiredString(key: String): String {
    val value = requiredValue(key)
    return value as? String
        ?: throw ValidationError(
            "the wire payload key '$key' must be a string",
            field = key,
            value = value,
        )
}

/** Read an optional object: an absent key and an explicit `null` both read as `null`. */
@Suppress("UNCHECKED_CAST")
private fun Map<String, Any?>.optionalObject(key: String): Map<String, Any?>? {
    val value = this[key] ?: return null
    return value as? Map<String, Any?>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an object",
            field = key,
            value = value,
        )
}

/** Read an optional integer: an absent key and an explicit `null` both read as `null`. */
private fun Map<String, Any?>.optionalInt(key: String): Int? {
    val value = this[key] ?: return null
    return (value as? Number)?.toInt()
        ?: throw ValidationError(
            "the wire payload key '$key' must be an integer",
            field = key,
            value = value,
        )
}

/** Read a required array of objects. */
@Suppress("UNCHECKED_CAST")
private fun Map<String, Any?>.requiredObjectList(key: String): List<Map<String, Any?>> {
    val value = requiredValue(key)
    val entries = value as? List<*>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an array",
            field = key,
            value = value,
        )
    return entries.map { entry ->
        entry as? Map<String, Any?>
            ?: throw ValidationError(
                "every entry of the wire payload key '$key' must be an object",
                field = key,
                value = entry,
            )
    }
}

/**
 * Read an optional array of strings.
 *
 * An absent key and an explicit `null` both read as an empty list: a stored
 * source that names no omissions has none.
 */
private fun Map<String, Any?>.optionalStringList(key: String): List<String> {
    val value = this[key] ?: return emptyList()
    val entries = value as? List<*>
        ?: throw ValidationError(
            "the wire payload key '$key' must be an array",
            field = key,
            value = value,
        )
    return entries.map { entry ->
        entry as? String
            ?: throw ValidationError(
                "every entry of the wire payload key '$key' must be a string",
                field = key,
                value = entry,
            )
    }
}

/**
 * Read a timestamp.
 *
 * Both spellings of an ISO-8601 timestamp are accepted: the one with an offset
 * and the naive one. A naive timestamp is read as UTC, because a timestamp
 * without an offset has no other meaning.
 */
private fun instantFromWire(value: String, key: String): Instant =
    try {
        OffsetDateTime.parse(value).toInstant()
    } catch (_: DateTimeException) {
        try {
            LocalDateTime.parse(value).toInstant(ZoneOffset.UTC)
        } catch (_: DateTimeException) {
            throw ValidationError(
                "the wire payload key '$key' is not an ISO-8601 timestamp",
                field = key,
                value = value,
            )
        }
    }
