package lmeditor.ingest

import lmeditor.core.SourceLoadError
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.time.Duration
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The bounded read and the redirect handling, ported from
 * `backend/tests/test_ingestion_security.py`.
 */
class HttpSafetyTest {

    /** A body that fits is returned whole. */
    @Test
    fun aBodyThatFitsIsReturnedWhole() {
        val payload = "the quick brown fox".toByteArray()
        assertTrue(payload.contentEquals(readLimited(ByteArrayInputStream(payload), 1024)))
    }

    /**
     * A body that is larger than the cap is refused while it is read.
     *
     * The refusal happens on the way in, not on the way out: the reader never
     * holds the whole body, so an oversized document costs the cap and not its
     * size.
     */
    @Test
    fun anOversizedBodyIsRefusedWhileItIsRead() {
        val payload = ByteArray(64 * 1024) { 'a'.code.toByte() }
        var bytesRead = 0
        val counting = object : InputStream() {
            override fun read(): Int {
                bytesRead++
                return payload[bytesRead - 1].toInt() and 0xff
            }

            override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                if (bytesRead >= payload.size) {
                    return -1
                }
                val count = minOf(length, payload.size - bytesRead)
                System.arraycopy(payload, bytesRead, buffer, offset, count)
                bytesRead += count
                return count
            }
        }
        val failure = assertFailsWith<PayloadTooLargeError> {
            readLimited(counting, 1024)
        }
        assertEquals(1024, failure.limit)
        assertTrue(bytesRead <= 1024 + 8192, "the reader must stop near the cap, not read the body whole")
    }

    /**
     * A redirect into the private network is refused, and the second hop is
     * never requested.
     */
    @Test
    fun aRedirectIntoThePrivateNetworkIsRefused() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { url ->
            if (url.endsWith("/start")) {
                redirectResponse(url, "http://169.254.169.254/latest/meta-data/")
            } else {
                textResponse(url, "should never be read")
            }
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(
                transport = transport,
                resolver = hostResolver("169.254.169.254" to "169.254.169.254"),
            )
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
        assertEquals(listOf("https://public.example/start"), calls)
    }

    /** Redirects between public hosts are still followed, one hop at a time. */
    @Test
    fun aPublicRedirectIsFollowed() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { url ->
            if (url.endsWith("/start")) {
                redirectResponse(url, "/final")
            } else {
                textResponse(url, "<html><body><p>Fetched</p></body></html>")
            }
        }
        val loaded = UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
            .load(SourceOrigin.Remote("https://public.example/start"))
        assertEquals(
            listOf("https://public.example/start", "https://public.example/final"),
            calls,
        )
        assertEquals("Fetched", loaded.text)
        assertEquals(null, loaded.pageCount)
    }

    /** A redirect chain is capped, and the chain that is refused names the cap. */
    @Test
    fun aRedirectChainIsCapped() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { url ->
            redirectResponse(url, "/next/0")
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/next/0"))
        }
        assertTrue(failure.reason.contains("maximum of 5 redirects"))
        assertEquals(MAX_RESOLVER_REDIRECT_HOPS + 1, calls.size)
    }

    /** A redirect without a Location header is refused. */
    @Test
    fun aRedirectWithoutALocationIsRefused() {
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 302,
                headers = emptyMap(),
                peerAddress = "93.184.216.34",
                body = ByteArrayInputStream(ByteArray(0)),
            )
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("Location"))
    }

    /**
     * A connection whose peer address is unknown is refused: "cannot tell" is
     * unproven, not clean.
     */
    @Test
    fun anUnprovenPeerIsRefused() {
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = emptyMap(),
                peerAddress = null,
                body = ByteArrayInputStream("hello".toByteArray()),
            )
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
    }

    /** A connection that reached an address the policy did not vet is refused. */
    @Test
    fun anUnvettedPeerIsRefused() {
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = emptyMap(),
                peerAddress = "127.0.0.1",
                body = ByteArrayInputStream("hello".toByteArray()),
            )
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
    }

    /**
     * The transport is handed the vetted addresses, not the host name.
     *
     * This is the pin primitive in action: the transport must connect to one
     * of the addresses the policy vetted, because a second resolution is
     * exactly the window the policy closes.
     *
     * Ported from `test_the_hop_is_issued_as_a_stream`, whose Python form
     * asserted the call shape that made the peer check possible at all.
     */
    @Test
    fun theTransportIsGivenTheVettedAddresses() {
        val transport = recordingTransport(mutableListOf()) { url ->
            textResponse(url, "<html><body><p>Fetched</p></body></html>")
        }
        UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
            .load(SourceOrigin.Remote("https://public.example/start"))
        assertEquals(1, transport.addresses.size)
        assertEquals(listOf("93.184.216.34"), transport.addresses[0].map { it.ip })
        assertEquals(listOf(443), transport.addresses[0].map { it.port })
    }

    /**
     * A hop that lands on a rebound internal address is refused, and its body
     * is never even read.
     *
     * Ported from `test_hop_landing_on_a_rebound_internal_address_is_refused`:
     * the internal content must not reach the caller, and it must not be read
     * at all.
     */
    @Test
    fun aReboundInternalAddressIsRefusedAndItsBodyIsNeverRead() {
        val rebound = listOf("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1")
        for (address in rebound) {
            val body = countingBody(INTERNAL_BODY)
            val transport = recordingTransport(mutableListOf()) { url ->
                HttpExchange(
                    statusCode = 200,
                    headers = emptyMap(),
                    peerAddress = address,
                    body = body,
                )
            }
            val failure = assertFailsWith<SourceLoadError>("'$address' must be refused") {
                UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                    .load(SourceOrigin.Remote("https://public.example/secret"))
            }
            assertTrue(failure.reason.contains("blocked URL"))
            assertTrue(failure.reason.contains("not one of the vetted address(es)"))
            assertTrue(failure.reason.contains(address))
            assertTrue(!failure.reason.contains("hunter2"), "the internal body must not surface")
            assertEquals(0, body.reads, "the internal body must never be read")
        }
    }

    /**
     * A hop that lands on a *different public* address is refused: the pin is
     * stricter than a blocklist.
     *
     * Ported from `test_hop_landing_on_a_different_public_address_is_refused`.
     */
    @Test
    fun aDifferentPublicAddressIsRefused() {
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = emptyMap(),
                peerAddress = "198.51.100.7",
                body = ByteArrayInputStream("hello".toByteArray()),
            )
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
    }

    /**
     * A mixed resolution whose connection uses the private address is refused.
     *
     * Ported from `test_mixed_resolution_refused_when_the_connection_uses_the_private_address`:
     * the guard allows the host, but only the public pair is pinned.
     */
    @Test
    fun aMixedResolutionUsedOnThePrivateAddressIsRefused() {
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = emptyMap(),
                peerAddress = "10.0.0.1",
                body = ByteArrayInputStream("hello".toByteArray()),
            )
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(
                transport = transport,
                resolver = fixedResolver("93.184.216.34", "10.0.0.1"),
            )
                .load(SourceOrigin.Remote("https://mixed.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
    }

    /**
     * The control for the refusals above: a hop that landed on the pinned
     * address *is* served.
     *
     * Ported from `test_a_hop_that_landed_on_the_pinned_address_is_served`:
     * without it, the refusals would not show that the peer comparison --
     * rather than the body or the status code -- is what rejects a rebound hop.
     */
    @Test
    fun aHopOnThePinnedAddressIsServed() {
        val body = countingBody("<html><body><p>Fetched</p></body></html>")
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("text/html")),
                peerAddress = "93.184.216.34",
                body = body,
            )
        }
        val loaded = UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
            .load(SourceOrigin.Remote("https://public.example/start"))
        assertEquals("Fetched", loaded.text)
        assertEquals(null, loaded.pageCount)
        assertTrue(body.reads > 0, "the accepted body must be read")
        assertTrue(body.closed, "the accepted exchange must be released")
    }

    /**
     * Every hop is pinned on its own: a first hop that is fine may not carry
     * a second hop to an address the policy did not vet.
     *
     * Ported from `test_redirect_hops_are_pinned_independently`.
     */
    @Test
    fun everyHopIsPinnedIndependently() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { url ->
            if (url.endsWith("/start")) {
                redirectResponse(url, "https://next.example/final")
            } else {
                HttpExchange(
                    statusCode = 200,
                    headers = emptyMap(),
                    peerAddress = "127.0.0.1",
                    body = ByteArrayInputStream("secret".toByteArray()),
                )
            }
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(
                transport = transport,
                resolver = fixedResolver("93.184.216.34"),
            )
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
        assertEquals(listOf("https://public.example/start", "https://next.example/final"), calls)
    }

    /**
     * A refused hop releases its connection.
     *
     * Ported from `test_a_refused_hop_releases_its_connection`: the exchange
     * is closed on the refusal path too, so no socket is leaked.
     */
    @Test
    fun aRefusedHopReleasesItsConnection() {
        val body = countingBody(INTERNAL_BODY)
        val transport = recordingTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 200,
                headers = emptyMap(),
                peerAddress = "127.0.0.1",
                body = body,
            )
        }
        assertFailsWith<SourceLoadError> {
            UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
                .load(SourceOrigin.Remote("https://public.example/start"))
        }
        assertTrue(body.closed, "a refused exchange must be closed")
    }

    /**
     * An internal target is refused before any request is made.
     *
     * Ported from `test_internal_origins_are_still_refused_before_any_request`:
     * the pre-flight guard rejects internal targets without touching the
     * transport at all.
     */
    @Test
    fun anInternalTargetIsRefusedBeforeAnyRequest() {
        val blocked = listOf(
            "http://127.0.0.1:8080/internal",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/internal",
            "http://0.0.0.0/",
            "http://[fd00::1]/",
            "http://224.0.0.1/",
            "file:///etc/passwd",
        )
        for (url in blocked) {
            val calls = mutableListOf<String>()
            val transport = recordingTransport(calls) { requested ->
                textResponse(requested, "must never be read")
            }
            val failure = assertFailsWith<SourceLoadError>("'$url' must be refused") {
                UrlSourceLoader(transport = transport, resolver = literalResolver())
                    .load(SourceOrigin.Remote(url))
            }
            assertTrue(failure.reason.contains("blocked URL"))
            assertEquals(emptyList(), calls, "no request may be attempted for '$url'")
        }
    }

    /**
     * A name that resolves only to a private address never reaches the
     * transport.
     *
     * Ported from `test_internal_hostname_is_still_refused_before_any_request`.
     */
    @Test
    fun anInternalHostnameIsRefusedBeforeAnyRequest() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { requested ->
            textResponse(requested, "must never be read")
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(
                transport = transport,
                resolver = fixedResolver("10.1.2.3"),
            )
                .load(SourceOrigin.Remote("https://internal.example/internal"))
        }
        assertTrue(failure.reason.contains("blocked URL"))
        assertEquals(emptyList(), calls)
    }

    /**
     * A URL that would split the request is refused before a request is made.
     *
     * The path of the URL is written into the request line, so a line break in
     * it would end the line early and turn the rest of the URL into a header
     * of the caller's choosing.
     */
    @Test
    fun aUrlThatWouldSplitTheRequestIsRefusedBeforeAnyRequest() {
        val calls = mutableListOf<String>()
        val transport = recordingTransport(calls) { requested ->
            textResponse(requested, "must never be read")
        }
        val failure = assertFailsWith<SourceLoadError> {
            UrlSourceLoader(
                transport = transport,
                resolver = fixedResolver("93.184.216.34"),
            ).load(SourceOrigin.Remote("https://public.example/a\r\nX-Injected-Header: pwned"))
        }
        assertTrue(failure.reason.contains("control character"), failure.reason)
        assertEquals(emptyList(), calls, "no request may be attempted for a split URL")
    }

    /**
     * The request builder refuses to write a line break into the request.
     *
     * The URL parser already refuses such a URL; this is the second lock, on
     * the last place before the bytes are written to the socket.
     */
    @Test
    fun theRequestBuilderRefusesToWriteALineBreak() {
        val parsed = ParsedUrl(
            scheme = "http",
            authority = "public.example",
            host = "public.example",
            port = null,
            path = "/a\r\nX-Injected-Header: pwned",
        )

        assertFailsWith<HttpSafetyError> {
            SocketHttpTransport.buildRequest(parsed, parsed.host, 80)
        }
    }

    /** A request for an ordinary URL is written as one request line and its headers. */
    @Test
    fun anOrdinaryRequestIsWrittenAsOneRequestLine() {
        val parsed = ParsedUrl.parse("https://public.example/paper?q=1")

        val request = SocketHttpTransport.buildRequest(parsed, parsed.host, 443)

        assertTrue(request.startsWith("GET /paper?q=1 HTTP/1.1\r\n"), request)
        assertEquals(1, request.split("\r\n").count { it.startsWith("GET ") })
        assertTrue(request.contains("\r\nHost: public.example\r\n"), request)
    }
}

/** The body a rebound hop would have carried; it must never be read. */
private const val INTERNAL_BODY: String = "<html><body><h1>internal</h1><p>db_password=hunter2</p></body></html>"

/**
 * A resolver that answers with the address the host *is*.
 *
 * A literal address resolves to itself, so the policy sees the address the
 * URL named; a name would be resolved by the JDK, which is what the default
 * resolver does.
 */
private fun literalResolver(): HostResolver = HostResolver { host ->
    listOf(InetAddress.getByName(host))
}

/** A transport that records the URLs it is asked for and answers with [respond]. */
private class recordingTransport(
    private val calls: MutableList<String>,
    private val respond: (String) -> HttpExchange,
) : HttpTransport {
    /** The vetted addresses each hop was given. */
    val addresses: MutableList<List<VettedAddress>> = mutableListOf()

    override fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange {
        calls.add(url)
        this.addresses.add(addresses)
        return respond(url)
    }
}

/**
 * A body that counts how often it was read and whether it was closed.
 *
 * The two counters are the evidence for two properties: a refused hop's body
 * is never read, and every hop releases its exchange whether it was accepted
 * or refused.
 */
internal class countingBody(private val payload: String) : InputStream() {
    /** How often the body was read. */
    var reads: Int = 0
        private set

    /** Whether the body was closed. */
    var closed: Boolean = false
        private set

    private val bytes: ByteArray = payload.toByteArray()
    private var position: Int = 0

    override fun read(): Int {
        reads++
        if (position >= bytes.size) {
            return -1
        }
        val value = bytes[position].toInt() and 0xff
        position++
        return value
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        reads++
        if (position >= bytes.size) {
            return -1
        }
        val count = minOf(length, bytes.size - position)
        System.arraycopy(bytes, position, buffer, offset, count)
        position += count
        return count
    }

    override fun close() {
        closed = true
    }
}

/** Return a 302 response pointing at [location]. */
private fun redirectResponse(url: String, location: String): HttpExchange = HttpExchange(
    statusCode = 302,
    headers = mapOf("Location" to listOf(location)),
    peerAddress = "93.184.216.34",
    body = ByteArrayInputStream(ByteArray(0)),
)

/** Return a 200 response carrying [body]. */
private fun textResponse(url: String, body: String): HttpExchange = HttpExchange(
    statusCode = 200,
    headers = mapOf("Content-Type" to listOf("text/html")),
    peerAddress = "93.184.216.34",
    body = ByteArrayInputStream(body.toByteArray()),
)
