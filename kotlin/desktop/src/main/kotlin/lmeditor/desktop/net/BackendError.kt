package lmeditor.desktop.net

/**
 * A failure the client could not turn into a result.
 *
 * Every failure the client can meet is one of these three: the server could
 * not be reached, the server refused the request, or the server answered with
 * something the client cannot read. A bare exception would hide which of the
 * three happened, and the operator's next action differs for each one.
 */
sealed class BackendError(
    message: String,
    cause: Throwable? = null,
) : Exception(message, cause) {

    /** The server could not be reached at all. */
    class Unreachable(
        val url: String,
        cause: Throwable,
    ) : BackendError("Could not reach the backend at $url: ${cause.message}", cause)

    /** The server answered with a status the client did not expect. */
    class Refused(
        val status: Int,
        val detail: String,
    ) : BackendError("The backend refused the request ($status): $detail")

    /** The server answered with something the client cannot read. */
    class Malformed(
        val what: String,
        cause: Throwable? = null,
    ) : BackendError(
        "The backend's $what could not be read: ${cause?.message ?: "unknown reason"}",
        cause,
    )
}
