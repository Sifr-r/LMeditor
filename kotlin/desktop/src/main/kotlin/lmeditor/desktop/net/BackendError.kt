package lmeditor.desktop.net

/**
 * A failure the client could not turn into a result.
 *
 * Every failure the client can meet is one of these four: the server could
 * not be reached, the server refused the request, the server answered with
 * something the client cannot read, or the client could not be given the
 * credential the API requires. A bare exception would hide which of the four
 * happened, and the operator's next action differs for each one.
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

    /**
     * The client could not be given the credential the API requires.
     *
     * Every route under `/api/` refuses a caller that presents no token, so
     * a client that cannot read one cannot talk to the server at all. The
     * failure names where the client looked: the operator's only way to fix
     * it is to put the token where the client is looking.
     */
    class Uncredentialed(
        /** Where the client looked: a file's path, or an environment variable's name. */
        val source: String,
        cause: Throwable? = null,
    ) : BackendError(
        "The API token could not be read from $source: " +
            (cause?.message ?: "no token was found there"),
        cause,
    )
}
