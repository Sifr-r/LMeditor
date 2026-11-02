package lmeditor.ingest

import java.net.InetAddress
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The SSRF guard, ported from `backend/tests/test_ingestion_security.py`.
 *
 * The addresses and URLs pinned here are the ones the Python tier refuses, and
 * they are the facts the two tiers must agree on.
 */
class UrlPolicyTest {

    /**
     * Loopback, private, link-local, reserved, multicast and non-HTTP targets
     * are all refused.
     *
     * Ported from `test_assert_public_url_rejects_internal_targets`.
     */
    @Test
    fun theInternalTargetsAreRefused() {
        val blocked = listOf(
            "http://127.0.0.1:8080/internal",
            "http://localhost/internal",
            "https://localhost:9000/internal",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/internal",
            "http://0.0.0.0/",
            "http://[fd00::1]/",
            "http://224.0.0.1/",
            "file:///etc/passwd",
            "gopher://127.0.0.1:11211/_stats",
            "ftp://example.org/x",
            "http:///no-host",
        )
        for (url in blocked) {
            val failure = assertFailsWith<UnsafeSourceError>("'$url' must be refused") {
                assertPublicUrl(url)
            }
            assertTrue(
                failure.message!!.isNotEmpty(),
                "the refusal of '$url' must say why",
            )
        }
    }

    /** A host that resolves to a public address is allowed, unchanged. */
    @Test
    fun aPublicHostIsAllowedUnchanged() {
        val resolver = fixedResolver("93.184.216.34")
        assertEquals(
            "https://example.org/paper",
            assertPublicUrl("https://example.org/paper", resolver),
        )
    }

    /**
     * A host that resolves to both a public and a private address is allowed:
     * the loader pins the connection to the vetted addresses, so the private
     * one is never reached.
     */
    @Test
    fun aMixedPublicAndPrivateResolutionIsAllowed() {
        val resolver = fixedResolver("93.184.216.34", "10.0.0.1")
        assertTrue(assertPublicUrl("https://mixed.example/paper", resolver).isNotEmpty())
    }

    /** A host that cannot be resolved is refused. */
    @Test
    fun aFailedResolutionIsRefused() {
        val resolver = HostResolver { host ->
            throw UnsafeSourceError("could not resolve host '$host': no addresses returned")
        }
        val failure = assertFailsWith<UnsafeSourceError> {
            assertPublicUrl("https://does-not-resolve.invalid/paper", resolver)
        }
        assertTrue(failure.message!!.contains("could not resolve"))
    }

    /**
     * A resolution that mixes a public and a private address pins only the
     * public one.
     *
     * Ported from `test_resolve_public_addresses_drops_internal_entries_from_a_mixed_set`:
     * the private address is never connectable, because it is never pinned.
     */
    @Test
    fun aMixedResolutionPinsOnlyThePublicAddress() {
        val resolver = fixedResolver("93.184.216.34", "10.0.0.1")
        val vetted = resolvePublicAddresses("https://mixed.example/paper", resolver)
        assertEquals(listOf("93.184.216.34"), vetted.map { it.ip })
    }

    /**
     * A resolution that is entirely internal yields no pinned address at all.
     *
     * Ported from `test_resolve_public_addresses_rejects_all_internal_resolutions`.
     */
    @Test
    fun anAllInternalResolutionIsRefused() {
        val internal = listOf(
            "127.0.0.1",
            "10.0.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
        )
        for (address in internal) {
            val resolver = fixedResolver(address)
            val failure = assertFailsWith<UnsafeSourceError>("'$address' must be refused") {
                resolvePublicAddresses("https://internal.example/x", resolver)
            }
            assertTrue(failure.message!!.contains("internal address"))
        }
    }

    /**
     * A name that does not resolve is refused rather than fetched blindly.
     *
     * Ported from `test_resolve_public_addresses_rejects_unresolvable_hosts`.
     */
    @Test
    fun anUnresolvableHostHasNoPinnedAddress() {
        val resolver = HostResolver { host ->
            throw UnsafeSourceError("could not resolve host '$host': no addresses returned")
        }
        val failure = assertFailsWith<UnsafeSourceError> {
            resolvePublicAddresses("https://does-not-resolve.invalid/x", resolver)
        }
        assertTrue(failure.message!!.contains("could not resolve"))
    }

    /**
     * The vetted addresses of a public URL are the ones the connection may be
     * made to, and the port follows the scheme.
     */
    @Test
    fun theVettedAddressesCarryThePortOfTheScheme() {
        val resolver = fixedResolver("93.184.216.34")
        val https = resolvePublicAddresses("https://example.org/paper", resolver)
        assertEquals(1, https.size)
        assertEquals(443, https[0].port)
        assertEquals("93.184.216.34", https[0].ip)

        val http = resolvePublicAddresses("http://example.org:8080/paper", resolver)
        assertEquals(8080, http[0].port)
    }

    /**
     * A redirect into the private network is refused before it is followed.
     *
     * This is the rule that makes a redirect unable to walk the fetch into the
     * private network: the target goes through the same policy as the first
     * hop.
     */
    @Test
    fun aRedirectToAPrivateTargetIsRefused() {
        val resolver = hostResolver("169.254.169.254" to "169.254.169.254")
        val failure = assertFailsWith<UnsafeSourceError> {
            validatedRedirect(
                current = "https://public.example/start",
                location = "http://169.254.169.254/latest/meta-data/",
                resolver = resolver,
            )
        }
        assertTrue(failure.message!!.contains("internal address"))
    }

    /** A redirect to a public host is accepted, and resolved against the current URL. */
    @Test
    fun aRedirectToAPublicTargetIsAccepted() {
        val resolver = fixedResolver("93.184.216.34")
        assertEquals(
            "https://public.example/final",
            validatedRedirect(
                current = "https://public.example/start",
                location = "/final",
                resolver = resolver,
            ),
        )
        assertEquals(
            "https://other.example/final",
            validatedRedirect(
                current = "https://public.example/start",
                location = "https://other.example/final",
                resolver = resolver,
            ),
        )
    }

    /**
     * The hosts a response declares as redirect targets are listed, so the
     * error message can name them.
     */
    @Test
    fun theRedirectHostsAreListed() {
        val hosts = iterRedirectHosts(
            "https://public.example/start",
            listOf("/one", "https://other.example/two", "/one"),
        )
        assertEquals(listOf("public.example", "other.example"), hosts)
    }

    /**
     * The address rules are the ones Python's `ipaddress` applies, so the two
     * tiers refuse exactly the same addresses.
     *
     * The addresses and the verdicts were taken from the Python runtime.
     */
    @Test
    fun theBlockedAddressesAreTheOnesPythonBlocks() {
        val blocked = listOf(
            "0.0.0.0",
            "10.0.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "172.16.0.1",
            "192.168.1.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "::",
            "::1",
            "::ffff:127.0.0.1",
            "::ffff:10.0.0.1",
            "100::1",
            "2001:db8::1",
            "2002::1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
        )
        for (address in blocked) {
            assertTrue(
                isBlockedAddressText(address),
                "'$address' must be refused",
            )
        }
    }

    /**
     * The public addresses are the ones Python allows.
     *
     * The addresses and the verdicts were taken from the Python runtime.
     */
    @Test
    fun thePublicAddressesAreTheOnesPythonAllows() {
        val allowed = listOf(
            "8.8.8.8",
            "93.184.216.34",
            "100.64.0.1",
            "2606:4700::1111",
            "::ffff:93.184.216.34",
        )
        for (address in allowed) {
            assertTrue(
                !isBlockedAddressText(address),
                "'$address' must be allowed",
            )
        }
    }

    /** Safe ids are returned unchanged; the id becomes the on-disk directory. */
    @Test
    fun safeSourceIdsAreAcceptedUnchanged() {
        for (id in listOf("src_url_api", "a.b.c", "paper-2024", "x".repeat(128))) {
            assertEquals(id, validateSourceId(id))
        }
    }

    /** Traversal, separators, a leading dash, a NUL and an over-long id are refused. */
    @Test
    fun unsafeSourceIdsAreRefused() {
        val unsafe = listOf(
            "",
            "   ",
            "../evil",
            "a/b",
            "a\\b",
            "a:b",
            "-leading",
            "with\u0000nul",
            "x".repeat(129),
            ".",
            "..",
        )
        for (id in unsafe) {
            assertFailsWith<UnsafeSourceError>("'$id' must be refused") {
                validateSourceId(id)
            }
        }
    }

    /** A filename that is an absolute path is reduced to its basename. */
    @Test
    fun anAbsoluteFilenameIsNeutralized() {
        val base = java.nio.file.Files.createTempDirectory("lmeditor-upload")
        val cases = mapOf(
            "/etc/passwd" to "passwd",
            "C:\\Windows\\x.txt" to "x.txt",
            "../../shadow.txt" to "shadow.txt",
        )
        for ((filename, expected) in cases) {
            val result = safeUploadPath("src_ok", filename, base)
            assertEquals(base.toAbsolutePath().normalize().resolve("src_ok").resolve(expected), result)
            assertTrue(result.startsWith(base.toAbsolutePath().normalize()))
        }
    }

    /** A filename with no usable basename is refused. */
    @Test
    fun aDotOnlyFilenameIsRefused() {
        val base = java.nio.file.Files.createTempDirectory("lmeditor-upload")
        for (filename in listOf("..", ".", "", "   ", "...", "\u0000name.txt")) {
            assertFailsWith<UnsafeSourceError>("'$filename' must be refused") {
                safeUploadPath("src_ok", filename, base)
            }
        }
    }

    /** An over-long filename is truncated to the 255-character cap. */
    @Test
    fun aLongFilenameIsTruncated() {
        val base = java.nio.file.Files.createTempDirectory("lmeditor-upload")
        val result = safeUploadPath("src_ok", "a".repeat(400), base)
        assertEquals(255, result.fileName.toString().length)
        assertTrue(result.startsWith(base.toAbsolutePath().normalize()))
    }

    /** A traversal source id must not produce a path above the root. */
    @Test
    fun aTraversalSourceIdIsRefused() {
        val base = java.nio.file.Files.createTempDirectory("lmeditor-upload")
        assertFailsWith<UnsafeSourceError> {
            safeUploadPath("../../evil", "notes.txt", base)
        }
    }

    /**
     * A URL that carries a control character is refused.
     *
     * The URL is written into the request line of an HTTP request, so a line
     * break inside it ends the line early and turns the rest of the URL into a
     * header of the caller's choosing.
     */
    @Test
    fun aUrlWithAControlCharacterIsRefused() {
        val hostile = listOf(
            "http://example.org/a\r\nX-Injected-Header: pwned",
            "http://example.org/a\nX-Injected-Header: pwned",
            "http://example.org/a\r\n\r\nGET /admin HTTP/1.1",
            "http://exam\r\nple.org/",
            "http://example.org/a\u0000b",
        )
        for (url in hostile) {
            val failure = assertFailsWith<UnsafeSourceError>("'$url' must be refused") {
                assertPublicUrl(url, fixedResolver("93.184.216.34"))
            }
            assertTrue(
                failure.message!!.contains("control character"),
                "the refusal of '$url' must say why: ${failure.message}",
            )
        }
    }

    /** A URL without a control character is still accepted, unchanged. */
    @Test
    fun aUrlWithoutAControlCharacterIsAccepted() {
        val resolver = fixedResolver("93.184.216.34")
        assertEquals(
            "https://example.org/paper",
            assertPublicUrl("https://example.org/paper", resolver),
        )
    }

    /**
     * A source id that names a device Windows reserves is refused.
     *
     * The id becomes a directory under the upload root, and a directory cannot
     * be named after the null device.
     */
    @Test
    fun aReservedDeviceNameIsRefusedAsASourceId() {
        val reserved = listOf("NUL", "nul", "CON", "PRN", "AUX", "COM1", "LPT9", "nul.txt", "COM1.pdf")
        for (id in reserved) {
            assertFailsWith<UnsafeSourceError>("'$id' must be refused") {
                validateSourceId(id)
            }
        }
        // A name that merely begins with a reserved word is a name.
        assertEquals("CONtest", validateSourceId("CONtest"))
        assertEquals("com10", validateSourceId("com10"))
    }

    /**
     * A name that ends in a space or a dot is refused.
     *
     * Windows strips both, so the file that would be written is not the file
     * that was checked -- and the name that was checked is not the name that is
     * later opened.
     */
    @Test
    fun aNameThatEndsInASpaceOrADotIsRefused() {
        for (id in listOf("paper ", "paper.", "paper.txt ")) {
            assertFailsWith<UnsafeSourceError>("'$id' must be refused") {
                validateSourceId(id)
            }
        }
        val base = java.nio.file.Files.createTempDirectory("lmeditor-upload")
        for (filename in listOf("trailing.txt ", "trailing.txt.", "report.txt ")) {
            assertFailsWith<UnsafeSourceError>("'$filename' must be refused") {
                safeUploadPath("src_ok", filename, base)
            }
        }
    }

    /** A control character in a source id is refused. */
    @Test
    fun aControlCharacterInASourceIdIsRefused() {
        for (id in listOf("with\u0000nul", "with\u0001nul", "with\u007fnul")) {
            assertFailsWith<UnsafeSourceError>("'$id' must be refused") {
                validateSourceId(id)
            }
        }
    }

    @Test
    fun c1ControlsAreRefusedAtEverySourceBoundary() {
        val base = java.nio.file.Paths.get(System.getProperty("java.io.tmpdir"), "lmeditor-c1")
        val resolver = fixedResolver("93.184.216.34")
        for (code in 0x80..0x9f) {
            val control = code.toChar()
            val boundaries = listOf<() -> Unit>(
                { validateSourceId("source${control}id") },
                { safeUploadPath("source", "paper${control}.txt", base) },
                { assertPublicUrl("https://example.org/paper${control}text", resolver) },
            )
            for ((index, boundary) in boundaries.withIndex()) {
                val failure = assertFailsWith<UnsafeSourceError>("U+${code.toString(16)} at boundary $index") {
                    boundary()
                }
                assertTrue(failure.message!!.contains("control character"))
                assertTrue(failure.message!!.contains("0x${code.toString(16)}"))
            }
        }
    }

    @Test
    fun printableUnicodeIsPreservedAtEverySourceBoundary() {
        val base = java.nio.file.Paths.get(System.getProperty("java.io.tmpdir"), "lmeditor-unicode")
        val id = "caf\u00e9-\u65e5\u672c"
        val filename = "$id.txt"
        val url = "https://example.org/$id"
        assertEquals(id, validateSourceId(id))
        assertEquals(filename, safeUploadPath(id, filename, base).fileName.toString())
        assertEquals(url, assertPublicUrl(url, fixedResolver("93.184.216.34")))
    }
}

/** Return a resolver that answers with [addresses], whatever the host is. */
internal fun fixedResolver(vararg addresses: String): HostResolver = HostResolver { host ->
    addresses.map { InetAddress.getByName(it) }
}

/**
 * Return a resolver that answers per host.
 *
 * A host that is not named resolves to [defaultAddress], so a test only has to
 * name the hosts whose behaviour it cares about.
 */
internal fun hostResolver(
    vararg entries: Pair<String, String>,
    defaultAddress: String = "93.184.216.34",
): HostResolver = HostResolver { host ->
    val mapped = entries.firstOrNull { it.first == host }?.second ?: defaultAddress
    listOf(InetAddress.getByName(mapped))
}
