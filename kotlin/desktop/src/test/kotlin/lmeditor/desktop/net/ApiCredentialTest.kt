package lmeditor.desktop.net

import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** The credential's resolution, walked against a controlled environment. */
class ApiCredentialTest {

    @Test
    fun `the environment variable supplies the token`() {
        val credential = ApiCredential.resolve(
            environment = environment(ApiCredential.TOKEN_ENVIRONMENT to "from-the-environment"),
        )
        assertEquals("Bearer from-the-environment", credential.authorization())
    }

    @Test
    fun `the token file supplies the token`() {
        withTokenFile("from-the-file") { file ->
            val credential = ApiCredential.resolve(
                environment = environment(ApiCredential.TOKEN_FILE_ENVIRONMENT to file.toString()),
            )
            assertEquals("Bearer from-the-file", credential.authorization())
        }
    }

    @Test
    fun `the environment variable wins over the token file`() {
        withTokenFile("from-the-file") { file ->
            val credential = ApiCredential.resolve(
                environment = environment(
                    ApiCredential.TOKEN_ENVIRONMENT to "from-the-environment",
                    ApiCredential.TOKEN_FILE_ENVIRONMENT to file.toString(),
                ),
            )
            assertEquals("Bearer from-the-environment", credential.authorization())
        }
    }

    @Test
    fun `a blank environment variable falls through to the token file`() {
        withTokenFile("from-the-file") { file ->
            val credential = ApiCredential.resolve(
                environment = environment(
                    ApiCredential.TOKEN_ENVIRONMENT to "   ",
                    ApiCredential.TOKEN_FILE_ENVIRONMENT to file.toString(),
                ),
            )
            assertEquals("Bearer from-the-file", credential.authorization())
        }
    }

    @Test
    fun `the environment variable is read the way the server reads it`() {
        val credential = ApiCredential.resolve(
            environment = environment(ApiCredential.TOKEN_ENVIRONMENT to "  the-token  "),
        )
        assertEquals("Bearer the-token", credential.authorization())
    }

    @Test
    fun `a missing token file is a typed failure that names the path`() {
        val directory = Files.createTempDirectory("lmeditor-token")
        try {
            val file = directory.resolve("absent-token")
            val credential = ApiCredential.resolve(
                environment = environment(ApiCredential.TOKEN_FILE_ENVIRONMENT to file.toString()),
            )
            assertTrue(credential is ApiCredential.Unreadable)
            val failure = assertFailsWith<BackendError.Uncredentialed> { credential.authorization() }
            assertEquals(file.toString(), failure.source)
            assertTrue(failure.message.orEmpty().contains(file.toString()))
        } finally {
            directory.toFile().deleteRecursively()
        }
    }

    @Test
    fun `a blank token file is a typed failure that names the path`() {
        withTokenFile("   \n") { file ->
            val credential = ApiCredential.resolve(
                environment = environment(ApiCredential.TOKEN_FILE_ENVIRONMENT to file.toString()),
            )
            assertTrue(credential is ApiCredential.Unreadable)
            val failure = assertFailsWith<BackendError.Uncredentialed> { credential.authorization() }
            assertEquals(file.toString(), failure.source)
        }
    }

    @Test
    fun `a named token file replaces the default rather than being tried after it`() {
        val home = Files.createTempDirectory("lmeditor-home")
        try {
            val named = home.resolve("absent-token")
            val credential = ApiCredential.resolve(
                environment = environment(ApiCredential.TOKEN_FILE_ENVIRONMENT to named.toString()),
                home = home,
            )
            val failure = assertFailsWith<BackendError.Uncredentialed> { credential.authorization() }
            assertEquals(named.toString(), failure.source)
        } finally {
            home.toFile().deleteRecursively()
        }
    }

    @Test
    fun `the token file lives under the operator's home by default`() {
        val home = Files.createTempDirectory("lmeditor-home")
        try {
            val file = ApiCredential.tokenFile(environment = { null }, home = home)
            assertEquals(home.resolve(".lmeditor").resolve("api-token"), file)
        } finally {
            home.toFile().deleteRecursively()
        }
    }

    @Test
    fun `a named token file that begins with a tilde is read from the operator's home`() {
        val home = Files.createTempDirectory("lmeditor-home")
        try {
            val file = ApiCredential.tokenFile(
                environment = environment(ApiCredential.TOKEN_FILE_ENVIRONMENT to "~/api-token"),
                home = home,
            )
            assertEquals(home.resolve("api-token"), file)
        } finally {
            home.toFile().deleteRecursively()
        }
    }

    @Test
    fun `a token with a control character is refused rather than sent`() {
        val credential = ApiCredential.resolve(
            environment = environment(
                ApiCredential.TOKEN_ENVIRONMENT to "token\r\nX-Injected: yes",
            ),
        )
        assertTrue(credential is ApiCredential.Unreadable)
        val failure = assertFailsWith<BackendError.Uncredentialed> { credential.authorization() }
        assertEquals(ApiCredential.TOKEN_ENVIRONMENT, failure.source)
    }

    @Test
    fun `the credential never describes the token`() {
        val credential = ApiCredential.resolve(
            environment = environment(ApiCredential.TOKEN_ENVIRONMENT to "the-secret-token"),
        )
        assertFalse(credential.toString().contains("the-secret-token"))
    }

    /** Write a token file, hand its path over, and clean it up afterwards. */
    private fun withTokenFile(contents: String, block: (Path) -> Unit) {
        val directory = Files.createTempDirectory("lmeditor-token")
        try {
            val file = directory.resolve("api-token")
            Files.writeString(file, contents)
            block(file)
        } finally {
            directory.toFile().deleteRecursively()
        }
    }

    /** An environment that answers only the variables it was given. */
    private fun environment(vararg entries: Pair<String, String>): (String) -> String? {
        val values = entries.toMap()
        return { values[it] }
    }
}
