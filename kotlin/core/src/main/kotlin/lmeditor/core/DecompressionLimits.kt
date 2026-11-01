package lmeditor.core

/**
 * The bounds a reader holds untrusted compressed data to.
 *
 * A rule is written once. A `.docx` package part and a PDF content stream
 * are the same threat -- a small file that makes the reader allocate a large
 * one -- so they are held to the same numbers, and those numbers live here
 * rather than in each reader. Two copies of a bound is exactly the drift this
 * pattern exists to prevent: someone who tightened the DOCX bound would
 * otherwise have silently left the PDF bound looser.
 *
 * The bounds live in `:core` because two modules need them and `:core` is the
 * only module both of them may depend on.
 */
object DecompressionLimits {

    /**
     * The largest number of bytes one compressed stream may expand to.
     *
     * Sixty-four megabytes is far more than any document the tier reads
     * needs, and small enough that a file that stores a bomb cannot make the
     * reader allocate one.
     */
    const val MAX_EXPANDED_BYTES: Long = 64L * 1024 * 1024

    /**
     * The largest ratio between the size a stream expands to and the size it
     * was stored at.
     *
     * A member that expands two hundred times its own size is not a document;
     * it is a bomb wearing a document's clothes.
     */
    const val MAX_COMPRESSION_RATIO: Long = 200
}
