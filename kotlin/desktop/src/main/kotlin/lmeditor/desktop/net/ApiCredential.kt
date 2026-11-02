package lmeditor.desktop.net

import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.InvalidPathException
import java.nio.file.Path
import java.nio.file.Paths

/**
 * The operator's home directory, where operator-level state already lives.
 *
 * The token file belongs beside the vault and the notebooks, not beside the
 * application's own working directory: it is the operator's, and it has to
 * be where a second half of the application can find it.
 */
private val operatorHome: Path = Paths.get(System.getProperty("user.home", "."))

/**
 * The bearer token the API requires, and where the client looked for it.
 *
 * The token is opaque: the client carries it and never reads it. What the
 * client does care about is where it came from, because the operator's only
 * way to fix a client that cannot authenticate is to put the token where the
 * client is looking.
 *
 * A credential that could not be read is carried as a failure rather than
 * raised at the moment it was resolved. The window is built long before the
 * operator presses anything, and a window that refuses to open cannot say
 * which file it wanted.
 *
 * The class is deliberately not a data class: a generated `toString` would
 * print the token, and a token that reaches a log has leaked.
 */
sealed class ApiCredential {

    /** The value of the `Authorization` header this credential travels in. */
    abstract fun authorization(): String

    /**
     * A credential that was read.
     *
     * The token is held and never described: the credential's own
     * `toString` must not be able to give it away.
     */
    class Read internal constructor(private val token: String) : ApiCredential() {
        override fun authorization(): String = "Bearer $token"
    }

    /**
     * A credential that could not be read.
     *
     * The failure is kept rather than thrown so that building a client
     * cannot fail: the request that needs the credential is where the
     * failure belongs, and it is where the operator will see it.
     */
    class Unreadable internal constructor(
        /** The failure the operator has to be given, and where it looked. */
        val failure: BackendError.Uncredentialed,
    ) : ApiCredential() {
        override fun authorization(): String = throw failure
    }

    companion object {

        /** The environment variable that carries the operator's own token. */
        const val TOKEN_ENVIRONMENT: String = "LMEDITOR_API_TOKEN"

        /** The environment variable that names the file the token is kept in. */
        const val TOKEN_FILE_ENVIRONMENT: String = "LMEDITOR_API_TOKEN_FILE"

        /**
         * Resolve the credential, the way the server resolves it.
         *
         * The operator's own token wins, and it is read the way the server
         * reads it: the server strips the value it is given, so the client
         * strips it too. A token the client tidied up differently would be a
         * token the server does not recognise.
         *
         * The token file comes next. It is where the server leaves the token
         * it generated, and it is the channel between the two halves of the
         * application: the server and the client are separate processes, and
         * the client is not necessarily started with the server's
         * environment.
         *
         * The token is read once, here, and travels on every request
         * afterwards.
         */
        fun resolve(
            environment: (String) -> String? = System::getenv,
            home: Path = operatorHome,
        ): ApiCredential {
            val named = environment(TOKEN_ENVIRONMENT)?.trim()
            if (!named.isNullOrEmpty()) {
                return if (isToken(named)) {
                    Read(named)
                } else {
                    Unreadable(malformed(TOKEN_ENVIRONMENT))
                }
            }
            return try {
                read(tokenFile(environment, home))
            } catch (cause: BackendError.Uncredentialed) {
                Unreadable(cause)
            }
        }

        /**
         * The file the token is kept in.
         *
         * `LMEDITOR_API_TOKEN_FILE` names it; otherwise it is the operator's
         * own home directory, where operator-level state already lives. The
         * client and the server must name the same file, so a named file
         * replaces the default rather than being tried after it: a client
         * that fell back to the default would offer the server a token the
         * server never issued.
         */
        fun tokenFile(
            environment: (String) -> String? = System::getenv,
            home: Path = operatorHome,
        ): Path {
            val named = environment(TOKEN_FILE_ENVIRONMENT)?.trim()
            if (named.isNullOrEmpty()) {
                return home.resolve(".lmeditor").resolve("api-token")
            }
            return expandHome(named, home)
        }

        /**
         * A named path that begins with `~` is the operator's home directory.
         *
         * The server expands it the same way, and the two halves have to name
         * the same file: a client that read `~/token` literally would look
         * in a place the server never wrote to.
         */
        private fun expandHome(named: String, home: Path): Path = when {
            named == "~" -> home
            named.startsWith("~/") -> home.resolve(named.substring(2))
            else -> try {
                Paths.get(named)
            } catch (cause: InvalidPathException) {
                throw BackendError.Uncredentialed(named, cause)
            }
        }

        /** Read the token out of the file, or remember why it could not be read. */
        private fun read(file: Path): ApiCredential {
            val text = try {
                Files.readString(file, StandardCharsets.UTF_8)
            } catch (cause: IOException) {
                return Unreadable(BackendError.Uncredentialed(file.toString(), cause))
            }
            val token = text.trim()
            if (token.isEmpty()) {
                return Unreadable(BackendError.Uncredentialed(file.toString()))
            }
            if (!isToken(token)) {
                return Unreadable(malformed(file.toString()))
            }
            return Read(token)
        }

        /**
         * Whether a value is a token at all.
         *
         * A token travels in a header, and a control character would split
         * that header in two. Such a value is refused rather than sent.
         */
        private fun isToken(value: String): Boolean = value.none { it.isISOControl() }

        /** The failure for a value that is not a token. */
        private fun malformed(source: String): BackendError.Uncredentialed =
            BackendError.Uncredentialed(
                source,
                IllegalArgumentException("the token holds a control character"),
            )
    }
}
