package lmeditor.ingest

import lmeditor.core.ValidationError

/**
 * The largest body the URL loader will read from a fetched URL.
 *
 * The cap is enforced *while* the body is read, so an oversized document is
 * abandoned mid-flight instead of being buffered whole.
 */
const val MAX_URL_RESPONSE_BYTES: Int = 10 * 1024 * 1024

/**
 * The largest body a bibliography resolver will read.
 *
 * The upstream services answer with a single JSON record (or a short Atom feed);
 * two megabytes is already orders of magnitude more than any of them needs.
 */
const val MAX_RESOLVER_RESPONSE_BYTES: Int = 2 * 1024 * 1024

/** The largest uploaded file the server will accept, in bytes. */
const val MAX_UPLOAD_BYTES: Int = 50 * 1024 * 1024

/** The total number of bytes the persisted-upload store may hold at once. */
const val MAX_SOURCE_STORE_BYTES: Long = 512L * 1024 * 1024

/**
 * Raised when a payload exceeds the cap that applies to it.
 *
 * Deliberately *not* a [lmeditor.core.SourceLoadError]: the callers translate it
 * into the domain error that fits their layer (a load failure for a source, a
 * resolution failure for a resolver), and the translation is where the
 * surrounding context is added.
 */
class PayloadTooLargeError(
    /** The cap that was exceeded. */
    val limit: Int,
    /** The number of bytes seen when the cap was passed, when it is known. */
    val observed: Int? = null,
) : RuntimeException(
    if (observed == null) {
        "payload exceeds the maximum of $limit bytes"
    } else {
        "payload of $observed bytes exceeds the maximum of $limit bytes"
    },
)

/**
 * Return [size] when it is an acceptable upload size, else raise.
 *
 * This is the helper the HTTP boundary calls *before* it buffers an upload: the
 * server reads the whole file into memory to persist it, so the only moment a
 * cheap refusal is possible is before that read.
 *
 * The Python original takes an `Any` and rejects a non-integer with
 * "upload size must be an integer number of bytes". Kotlin's type system makes
 * that branch unreachable, so the Kotlin entry point takes an [Int] and keeps
 * only the two rules a number can still break: the sign and the cap.
 *
 * @throws ValidationError the size is negative, or it exceeds [MAX_UPLOAD_BYTES].
 */
fun validateUploadSize(size: Int): Int {
    if (size < 0) {
        throw ValidationError(
            "upload size must not be negative",
            field = "size",
            value = size,
        )
    }
    if (size > MAX_UPLOAD_BYTES) {
        throw ValidationError(
            "upload of $size bytes exceeds the maximum of $MAX_UPLOAD_BYTES bytes",
            field = "size",
            value = size,
        )
    }
    return size
}
