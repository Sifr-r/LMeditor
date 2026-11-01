package lmeditor.ingest

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import javax.net.ssl.SSLParameters
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** The largest number of redirect hops a fetch will follow. */
const val MAX_RESOLVER_REDIRECT_HOPS: Int = 5

/** The size of one read from a response body. */
private const val BODY_CHUNK_BYTES: Int = 8 * 1024

/** The HTTP status codes that carry a redirect. */
private val REDIRECT_STATUSES: Set<Int> = setOf(301, 302, 303, 307, 308)

/**
 * Raised when a fetch fails below the policy layer.
 *
 * The HTTP boundary turns it into the domain error that fits its layer; the
 * reason carries the context of the failure.
 */
class HttpSafetyError(message: String, cause: Throwable? = null) : RuntimeException(message, cause)

/**
 * One HTTP response, still open.
 *
 * The body is a stream rather than a byte array on purpose: the cap that
 * applies to it is enforced while it is read, so an oversized document is
 * abandoned rather than buffered whole.
 */
class HttpExchange(
    /** The HTTP status code of the response. */
    val statusCode: Int,
    /** The response headers, keyed by their name as the server wrote it. */
    val headers: Map<String, List<String>>,
    /**
     * The address the connection actually reached, or null when the transport
     * cannot tell.
     *
     * A null is *unproven*, not *clean*: the caller must refuse rather than
     * assume the connection went where the policy vetted.
     */
    val peerAddress: String?,
    /** The response body. */
    val body: InputStream,
    private val onClose: () -> Unit = {},
) : Closeable {
    /** Return the first value of the header [name], matched case-insensitively. */
    fun header(name: String): String? = headers.entries
        .firstOrNull { it.key.equals(name, ignoreCase = true) }
        ?.value
        ?.firstOrNull()

    /** True when the status code carries a redirect. */
    val isRedirect: Boolean get() = statusCode in REDIRECT_STATUSES

    override fun close() {
        try {
            body.close()
        } catch (exc: IOException) {
            // The body is being abandoned; a failure to close it changes nothing.
        }
        onClose()
    }
}

/**
 * Issues one HTTP request.
 *
 * The seam exists for two reasons: the tests must be able to exercise the
 * redirect policy without a network, and the transport must be able to report
 * the address it really connected to so that the caller can prove the
 * connection went to a vetted address.
 */
interface HttpTransport {
    /**
     * Issue a `GET` for [url].
     *
     * The transport must connect only to one of the vetted [addresses]; it must
     * not resolve the host itself, because a second resolution is exactly the
     * window the policy closes.
     *
     * @throws HttpSafetyError the request could not be completed.
     */
    fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange
}

/**
 * The transport that speaks HTTP/1.1 over a plain JDK socket.
 *
 * A raw socket is used rather than `java.net.http.HttpClient` for one reason:
 * only a socket the caller owns can report the address it connected to, and
 * that address is what lets the loader prove the connection went to a vetted
 * address instead of to whatever the name resolved to a moment later.
 */
object SocketHttpTransport : HttpTransport {

    override fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange {
        if (addresses.isEmpty()) {
            throw HttpSafetyError("no vetted address to connect to for '$url'")
        }
        val parsed = ParsedUrl.parse(url)
        val host = parsed.host
        val port = addresses.first().port
        val millis = timeout.toMillis().coerceAtLeast(1L).toInt()

        var lastFailure: IOException? = null
        for (vetted in addresses) {
            var socket: Socket? = null
            try {
                val plain = Socket()
                socket = plain
                plain.connect(InetSocketAddress(vetted.address, port), millis)
                plain.soTimeout = millis

                val transport = if (parsed.scheme.equals("https", ignoreCase = true)) {
                    upgradeToTls(plain, host, port)
                } else {
                    plain
                }
                socket = transport

                val peer = transport.inetAddress?.hostAddress
                val request = buildRequest(parsed, host, port)
                transport.getOutputStream().use { out ->
                    out.write(request.toByteArray(Charsets.US_ASCII))
                    out.flush()
                }
                val stream = BufferedInputStream(transport.getInputStream())
                return readHead(stream, transport, peer)
            } catch (exc: IOException) {
                lastFailure = exc
                try {
                    socket?.close()
                } catch (closeFailure: IOException) {
                    // The socket is being abandoned.
                }
            }
        }
        throw HttpSafetyError(
            "could not reach '$url': ${lastFailure?.message ?: "no address could be reached"}",
            lastFailure,
        )
    }

    /** Wrap [plain] in TLS, keeping the hostname for SNI and for verification. */
    private fun upgradeToTls(plain: Socket, host: String, port: Int): Socket {
        val factory = SSLSocketFactory.getDefault() as SSLSocketFactory
        val tls = factory.createSocket(plain, host, port, true) as SSLSocket
        val parameters = SSLParameters()
        parameters.endpointIdentificationAlgorithm = "HTTPS"
        tls.sslParameters = parameters
        tls.startHandshake()
        return tls
    }

    /**
     * Build the request line and headers for [parsed].
     *
     * The request is written to a socket, so a line break in the path or in the
     * authority would end the request line early and let the rest of the URL
     * become a header of the caller's choosing. The URL parser already refuses
     * such a URL; this is the second lock on the same door, and it is the last
     * place before the bytes are written.
     */
    internal fun buildRequest(parsed: ParsedUrl, host: String, port: Int): String {
        val defaultPort = if (parsed.scheme.equals("https", ignoreCase = true)) 443 else 80
        val authority = if (port == defaultPort) host else "$host:$port"
        var path = parsed.path
        if (path.isEmpty()) {
            path = "/"
        }
        if (path.any { it == '\r' || it == '\n' } || authority.any { it == '\r' || it == '\n' }) {
            throw HttpSafetyError(
                "the request would contain a line break; the URL was refused",
            )
        }
        return buildString {
            append("GET ").append(path).append(" HTTP/1.1\r\n")
            append("Host: ").append(authority).append("\r\n")
            append("User-Agent: LMeditor-Kotlin/1.0\r\n")
            append("Accept: */*\r\n")
            append("Connection: close\r\n")
            append("\r\n")
        }
    }

    /** Read the status line and the headers, then hand back the body stream. */
    private fun readHead(stream: InputStream, socket: Socket, peer: String?): HttpExchange {
        val statusLine = readLine(stream)
            ?: throw HttpSafetyError("the server closed the connection before it answered")
        val parts = statusLine.split(' ', limit = 3)
        if (parts.size < 2 || !parts[0].startsWith("HTTP/")) {
            throw HttpSafetyError("the server did not answer with an HTTP status line: '$statusLine'")
        }
        val status = parts[1].toIntOrNull()
            ?: throw HttpSafetyError("the server answered with an invalid status code: '$statusLine'")

        val headers = LinkedHashMap<String, MutableList<String>>()
        while (true) {
            val line = readLine(stream) ?: break
            if (line.isEmpty()) {
                break
            }
            val colon = line.indexOf(':')
            if (colon <= 0) {
                continue
            }
            val name = line.substring(0, colon).trim()
            val value = line.substring(colon + 1).trim()
            headers.getOrPut(name) { mutableListOf() }.add(value)
        }

        val frozen = headers.mapValues { it.value.toList() }
        val chunked = frozen.entries
            .firstOrNull { it.key.equals("Transfer-Encoding", ignoreCase = true) }
            ?.value
            ?.any { it.contains("chunked", ignoreCase = true) } == true
        val contentLength = frozen.entries
            .firstOrNull { it.key.equals("Content-Length", ignoreCase = true) }
            ?.value
            ?.firstOrNull()
            ?.trim()
            ?.toIntOrNull()

        val body: InputStream = when {
            chunked -> ChunkedInputStream(stream)
            contentLength != null -> BoundedInputStream(stream, contentLength.toLong())
            else -> stream
        }
        return HttpExchange(status, frozen, peer, body) { socket.close() }
    }

    /** Read one CRLF-terminated line, without the terminator. */
    private fun readLine(stream: InputStream): String? {
        val builder = StringBuilder()
        while (true) {
            val byte = stream.read()
            if (byte < 0) {
                return if (builder.isEmpty()) null else builder.toString()
            }
            if (byte == '\n'.code) {
                return builder.toString().removeSuffix("\r")
            }
            builder.append(byte.toChar())
        }
    }
}

/**
 * Read at most [maxBytes] from [stream], refusing a body that is larger.
 *
 * The cap is enforced *while* the body is read: the moment the stream yields
 * more than [maxBytes] the read is abandoned and the payload refused, so an
 * oversized document is never held in memory in full.
 *
 * @throws PayloadTooLargeError the body is larger than [maxBytes].
 * @throws HttpSafetyError the stream failed.
 */
fun readLimited(stream: InputStream, maxBytes: Int): ByteArray {
    val buffer = ByteArrayOutputStream(minOf(maxBytes, BODY_CHUNK_BYTES).coerceAtLeast(1))
    val chunk = ByteArray(BODY_CHUNK_BYTES)
    var total = 0
    while (true) {
        val read = try {
            stream.read(chunk)
        } catch (exc: IOException) {
            throw HttpSafetyError("the response body could not be read: ${exc.message}", exc)
        }
        if (read < 0) {
            break
        }
        total += read
        if (total > maxBytes) {
            throw PayloadTooLargeError(maxBytes, total)
        }
        buffer.write(chunk, 0, read)
    }
    return buffer.toByteArray()
}

/**
 * Fetch [url], following redirects and reading at most [maxBytes] of body.
 *
 * Every hop is vetted on its own: the host of the hop is resolved and must
 * resolve to a public address before the hop is attempted, so a redirect into
 * the private network is refused rather than followed.
 *
 * @throws UnsafeSourceError a hop resolved to an internal address.
 * @throws PayloadTooLargeError the body is larger than [maxBytes].
 * @throws HttpSafetyError the request failed.
 */
fun fetchLimited(
    url: String,
    maxBytes: Int,
    timeout: Duration = Duration.ofSeconds(10),
    maxRedirects: Int = MAX_RESOLVER_REDIRECT_HOPS,
    transport: HttpTransport = SocketHttpTransport,
    resolver: HostResolver = JdkHostResolver,
): ByteArray {
    var current = url
    var hops = 0
    while (true) {
        val vetted = resolvePublicAddresses(current, resolver)
        val exchange = transport.send(current, vetted, timeout)
        exchange.use {
            if (!exchange.isRedirect) {
                if (exchange.statusCode >= 400) {
                    throw HttpSafetyError("the server answered with HTTP ${exchange.statusCode}")
                }
                return readLimited(exchange.body, maxBytes)
            }
            val location = exchange.header("Location")
            if (location.isNullOrBlank()) {
                throw HttpSafetyError("HTTP ${exchange.statusCode} redirect without a Location header")
            }
            if (hops >= maxRedirects) {
                throw HttpSafetyError("exceeded the maximum of $maxRedirects redirects")
            }
            hops++
            current = resolveUrl(current, location)
        }
    }
}

/**
 * Return the address the connection reached, or null when the transport cannot
 * tell.
 *
 * A null is *unproven*, not *clean*: a caller that cannot prove where it
 * connected must refuse rather than fall back on the pre-flight vet alone.
 */
fun connectedPeerIp(exchange: HttpExchange): String? = exchange.peerAddress

/**
 * Assert that the connection really went to one of the vetted addresses.
 *
 * Fails closed on purpose. The transport resolves the hostname itself when it
 * opens the connection, a moment after the pre-flight vet resolved it, and that
 * gap is the DNS-rebinding window. A hop reporting no peer address is
 * therefore *unproven* rather than *clean*, and the pre-flight vet alone
 * cannot stand in for a check that did not run.
 *
 * @throws UnsafeSourceError the peer address is unknown, or it is not one of the
 *   vetted addresses.
 */
fun assertVettedPeer(exchange: HttpExchange, vetted: List<VettedAddress>, host: String = "") {
    val peer = connectedPeerIp(exchange)
    if (peer == null) {
        throw UnsafeSourceError(
            "host '$host' connected without reporting a peer address, so the connection " +
                "cannot be shown to have used the vetted address(es) ${vetted.map { it.ip }.sorted()}",
        )
    }
    val allowed = vetted.map { it.ip }
    if (peer !in allowed) {
        throw UnsafeSourceError(
            "host '$host' connected to '$peer', which is not one of the vetted address(es) " +
                allowed.sorted(),
        )
    }
}

/** A stream that yields exactly [length] bytes and no more. */
private class BoundedInputStream(
    private val source: InputStream,
    private val length: Long,
) : InputStream() {
    private var remaining = length

    override fun read(): Int {
        if (remaining <= 0) {
            return -1
        }
        val byte = source.read()
        if (byte >= 0) {
            remaining--
        }
        return byte
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (remaining <= 0) {
            return -1
        }
        val want = minOf(length.toLong(), remaining).toInt()
        val read = source.read(buffer, offset, want)
        if (read > 0) {
            remaining -= read
        }
        return read
    }
}

/** A stream that decodes `Transfer-Encoding: chunked` on the fly. */
private class ChunkedInputStream(private val source: InputStream) : InputStream() {
    private var remainingInChunk: Long = 0
    private var finished = false

    override fun read(): Int {
        val single = ByteArray(1)
        val read = read(single, 0, 1)
        return if (read < 0) -1 else single[0].toInt() and 0xff
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (finished) {
            return -1
        }
        if (remainingInChunk == 0L) {
            if (!startChunk()) {
                finished = true
                return -1
            }
        }
        val want = minOf(length.toLong(), remainingInChunk).toInt()
        val read = source.read(buffer, offset, want)
        if (read < 0) {
            throw EOFException("the chunked body ended in the middle of a chunk")
        }
        remainingInChunk -= read
        return read
    }

    /** Read the next chunk header; return false when the body is finished. */
    private fun startChunk(): Boolean {
        val line = readLine() ?: return false
        val sizeText = line.substringBefore(';').trim()
        val size = sizeText.toLongOrNull()
            ?: throw HttpSafetyError("the server sent an invalid chunk header: '$line'")
        if (size == 0L) {
            // Consume the trailer section up to the final blank line.
            while (true) {
                val trailer = readLine() ?: break
                if (trailer.isEmpty()) {
                    break
                }
            }
            return false
        }
        remainingInChunk = size
        return true
    }

    /** Read one CRLF-terminated line from the source. */
    private fun readLine(): String? {
        val builder = StringBuilder()
        while (true) {
            val byte = source.read()
            if (byte < 0) {
                return if (builder.isEmpty()) null else builder.toString()
            }
            if (byte == '\n'.code) {
                return builder.toString().removeSuffix("\r")
            }
            builder.append(byte.toChar())
        }
    }
}

/**
 * The transport built on `java.net.http.HttpClient`.
 *
 * It is the simpler of the two: the client handles the protocol, the body is
 * streamed rather than buffered, and the byte cap is enforced while the stream
 * is read.
 *
 * It **cannot** report the address it connected to, so a caller that needs the
 * post-connect peer check (the URL loader does) must use [SocketHttpTransport]
 * instead. The bibliography resolvers talk to fixed, public upstreams and do
 * not need the check, so they use this one.
 */
class JdkHttpClientTransport(
    private val client: HttpClient = HttpClient.newBuilder()
        .followRedirects(HttpClient.Redirect.NEVER)
        .connectTimeout(Duration.ofSeconds(10))
        .build(),
) : HttpTransport {

    override fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange {
        if (addresses.isEmpty()) {
            throw HttpSafetyError("no vetted address to connect to for '$url'")
        }
        val request = HttpRequest.newBuilder()
            .uri(URI.create(url))
            .timeout(timeout)
            .GET()
            .build()
        val response = try {
            client.send(request, HttpResponse.BodyHandlers.ofInputStream())
        } catch (exc: IOException) {
            throw HttpSafetyError("could not reach '$url': ${exc.message}", exc)
        } catch (exc: InterruptedException) {
            Thread.currentThread().interrupt()
            throw HttpSafetyError("the request to '$url' was interrupted", exc)
        }
        val headers = LinkedHashMap<String, List<String>>()
        for ((name, values) in response.headers().map()) {
            headers[name] = values
        }
        return HttpExchange(
            statusCode = response.statusCode(),
            headers = headers,
            peerAddress = null,
            body = response.body(),
        )
    }
}

