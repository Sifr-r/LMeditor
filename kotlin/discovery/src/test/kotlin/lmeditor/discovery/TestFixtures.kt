package lmeditor.discovery

import lmeditor.core.SourceChunk

/**
 * The fixtures the discovery tests share.
 *
 * The chunk helper builds the same SHA-256 backed chunk the Python test
 * helper builds, so a chunk id minted on one side resolves on the other.
 */
object TestFixtures {
    /** A valid SHA-256 backed chunk. */
    fun chunk(
        text: String,
        docId: String = "doc_test_1",
        start: Int = 0,
    ): SourceChunk {
        val byteEnd = start + text.toByteArray(Charsets.UTF_8).size
        val charEnd = start + text.length
        return SourceChunk.create(
            documentId = docId,
            text = text,
            charStart = start,
            charEnd = charEnd,
            byteStart = start,
            byteEnd = byteEnd,
        )
    }
}
