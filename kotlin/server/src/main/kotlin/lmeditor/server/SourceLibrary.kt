package lmeditor.server

import lmeditor.core.BibliographicRecord
import lmeditor.core.SourceChunk
import lmeditor.core.SourceKind
import lmeditor.core.ValidationError
import java.time.Instant
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * How many source records the memory working set keeps. Archived records are
 * retained independently of this cache bound.
 */
const val MAX_SOURCES: Int = 256

/**
 * One ingested source and everything derived from it.
 *
 * Immutable: a stored source is replaced, never edited in place.
 */
data class StoredSource(
    /** The source's identifier. */
    val sourceId: String,
    /** The source's display title. */
    val title: String,
    /** The kind of artifact it was read from. */
    val kind: SourceKind,
    /** Where it came from: a path or a URL. */
    val origin: String,
    /** The passages it was cut into. */
    val chunks: List<SourceChunk>,
    /** Its resolved bibliographic metadata, when there is any. */
    val record: BibliographicRecord? = null,
    /** How many bytes it holds. */
    val byteSize: Int? = null,
    /** The content the loader could not preserve. */
    val omissions: List<String> = emptyList(),
    /** When it was added. */
    val addedAt: Instant = Instant.now(),
) {
    /** A compact description of the source for the client. */
    fun toSummary(): Map<String, Any?> = linkedMapOf(
        "source_id" to sourceId,
        "title" to title,
        "kind" to kind.wire,
        "origin" to origin,
        "chunk_count" to chunks.size,
        "byte_size" to byteSize,
        "added_at" to addedAt.toString(),
        "omissions" to omissions,
        "bibliography" to record,
    )
}

/**
 * A thread-safe registry with a bounded memory working set. With an archive,
 * records remain discoverable after eviction or process restart. Without an
 * archive, this retains the original bounded session-only behavior.
 */
class SourceLibrary(
    private val maxSources: Int = MAX_SOURCES,
    private val archive: SourceArchive? = null,
) {

    private val sources = LinkedHashMap<String, StoredSource>()
    private val lock = ReentrantLock()

    init {
        if (maxSources < 1) throw ValidationError("source cache size must be positive", field = "max_sources", value = maxSources)
    }

    /** Commit to the archive first, then evict only from the memory working set. */
    fun add(stored: StoredSource): StoredSource = lock.withLock {
        archive?.add(stored)
        cache(stored)
        return stored
    }

    private fun cache(stored: StoredSource) {
        sources[stored.sourceId] = stored
        while (sources.size > maxSources) {
            val oldest = sources.values.minByOrNull { it.addedAt }
                ?: break
            sources.remove(oldest.sourceId)
        }
    }

    /** The stored source with this id, if the library holds it. */
    fun get(sourceId: String): StoredSource? = lock.withLock {
        if (archive == null) return@withLock sources[sourceId]
        // Disk is authoritative: do not hide a deleted or corrupt record with a
        // cache hit, including when another process has replaced the record.
        val stored = archive.get(sourceId)
        if (stored == null) sources.remove(sourceId) else cache(stored)
        stored
    }

    /** Drop a source. Returns whether it was present. */
    fun remove(sourceId: String): Boolean = lock.withLock {
        if (archive == null) return@withLock sources.remove(sourceId) != null
        val removed = archive.remove(sourceId)
        sources.remove(sourceId)
        removed
    }

    /** Every stored source, oldest first. */
    fun list(): List<StoredSource> = lock.withLock {
        archive?.list() ?: sources.values.sortedBy { it.addedAt }
    }

    /**
     * The chunks of the named sources, in the order the ids were given.
     *
     * Unknown ids are skipped rather than raising: a client may reference a
     * source it uploaded before the server restarted, and a missing source
     * should not abort a generation request that has other, valid material.
     */
    fun chunksFor(sourceIds: List<String>): List<SourceChunk> = lock.withLock {
        val chunks = mutableListOf<SourceChunk>()
        for (id in sourceIds) {
            get(id)?.chunks?.let { chunks.addAll(it) }
        }
        chunks
    }

    /** Forget every source. Intended for tests. */
    fun clear() = lock.withLock {
        archive?.clear()
        sources.clear()
    }

    /** How many sources the library holds. */
    val size: Int
        get() = lock.withLock { archive?.list()?.size ?: sources.size }
}
