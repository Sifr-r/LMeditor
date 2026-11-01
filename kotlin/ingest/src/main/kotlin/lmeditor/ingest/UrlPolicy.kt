package lmeditor.ingest

import lmeditor.core.LMeditorError
import java.net.InetAddress
import java.net.UnknownHostException
import java.nio.file.Path
import java.nio.file.Paths

/**
 * Raised when a source id, an upload filename or a URL fails a boundary check.
 *
 * Every rejection at the trust boundary carries this one error so that the
 * callers at the HTTP boundary can translate it into the domain error
 * [lmeditor.core.SourceLoadError] without inspecting the reason.
 */
/**
 * Raised when a URL, a source id or an upload name fails a boundary check.
 *
 * It is an [LMeditorError], so it crosses the seam with its own type rather
 * than as an anonymous failure: the Python side raises its own
 * `UnsafeSourceError` in reply, and the code that catches it still catches it.
 */
class UnsafeSourceError(message: String) : LMeditorError(message)

/** The longest a source id may be. */
const val MAX_SOURCE_ID_LENGTH: Int = 128

/** The longest an upload file name may be. */
const val MAX_FILENAME_LENGTH: Int = 255

/** The characters that may not appear in a source id. */
private val FORBIDDEN_ID_CHARS: Set<Char> = setOf('/', '\\', ':', '*', '?', '"', '<', '>', '|')

/** The URL schemes the loader is allowed to fetch. */
private val ALLOWED_URL_SCHEMES: Set<String> = setOf("http", "https")

/**
 * The names Windows reserves for its own devices.
 *
 * A file or a directory cannot carry one of them: `NUL` is the null device,
 * and a name whose part before the first dot is one of these names *is* that
 * device, whatever follows the dot.
 */
private val RESERVED_DEVICE_NAMES: Set<String> = setOf(
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
)

/**
 * True when [character] is a control character: a C0 control or DEL.
 *
 * None of them has a meaning in a URL, a source id or a file name, and every
 * one of them is a way to confuse the text that is written to a socket or
 * handed to the filesystem.
 */
private fun isControlCharacter(character: Char): Boolean =
    character.code <= 0x1f || character.code == 0x7f

/**
 * True when [name] is a name Windows reserves for one of its devices.
 *
 * The part before the first dot decides: `NUL`, `nul.txt` and `NUL.pdf` all
 * name the null device, and none of them can be created.
 */
private fun isReservedDeviceName(name: String): Boolean =
    name.substringBefore('.').uppercase() in RESERVED_DEVICE_NAMES

/**
 * True when [name] ends in a space or a dot.
 *
 * Windows silently strips both, so the file that is written is not the file
 * that was checked -- and the name that was checked is not the name that is
 * later opened.
 */
private fun endsWithStrippedCharacter(name: String): Boolean =
    name.isNotEmpty() && (name.last() == ' ' || name.last() == '.')

/**
 * Resolves a host name to the addresses it points at.
 *
 * The seam exists so that the policy can be exercised against a fixed set of
 * addresses without a network, exactly as the Python tier patches
 * `socket.getaddrinfo` in its tests.
 */
fun interface HostResolver {
    /**
     * Return the addresses [host] resolves to.
     *
     * @throws UnsafeSourceError the host could not be resolved.
     */
    fun resolve(host: String): List<InetAddress>
}

/**
 * The resolver that asks the platform.
 *
 * `InetAddress.getAllByName` is the JDK's equivalent of `socket.getaddrinfo`:
 * a literal address is returned unchanged and never reaches the resolver, and a
 * name is resolved once to every address it points at.
 */
object JdkHostResolver : HostResolver {
    override fun resolve(host: String): List<InetAddress> = try {
        InetAddress.getAllByName(host).toList()
    } catch (exc: UnknownHostException) {
        throw UnsafeSourceError("could not resolve host '$host': ${exc.message}")
    } catch (exc: IllegalArgumentException) {
        throw UnsafeSourceError("could not resolve host '$host': ${exc.message}")
    }
}

/**
 * An address the policy has vetted, together with the port it may be reached on.
 *
 * The pair is the pinning primitive: a caller opens its connection to one of
 * these addresses rather than re-resolving the name itself, which is what
 * closes the DNS-rebinding window between the vet and the connection.
 */
data class VettedAddress(
    /** The vetted address. */
    val address: InetAddress,
    /** The port the address may be reached on. */
    val port: Int,
) {
    /** The numeric form of the address, as it is compared and reported. */
    val ip: String get() = address.hostAddress
}

/**
 * Return [sourceId] unchanged, or raise if it is not a safe path segment.
 *
 * Rejects: empty (or whitespace-only) ids, ids longer than
 * [MAX_SOURCE_ID_LENGTH], ids containing any of `/ \ : * ? " < > |`, ids
 * containing a `.` or `..` segment, ids starting with `-`, ids containing a
 * control character, ids that end in a space or a dot, and ids that name one
 * of the devices Windows reserves (`NUL`, `CON`, `COM1`, ...).
 *
 * The id becomes a directory under the upload root, so every one of those
 * names is refused *here*, where the refusal is typed, rather than by the
 * filesystem, where it is an untyped `NoSuchFileException`.
 */
fun validateSourceId(sourceId: String): String {
    if (sourceId.isBlank()) {
        throw UnsafeSourceError("source_id must be a non-empty string")
    }
    if (sourceId.length > MAX_SOURCE_ID_LENGTH) {
        throw UnsafeSourceError(
            "source_id exceeds the maximum length of $MAX_SOURCE_ID_LENGTH characters",
        )
    }
    val control = sourceId.firstOrNull { isControlCharacter(it) }
    if (control != null) {
        throw UnsafeSourceError(
            "source_id must not contain a control character (0x${control.code.toString(16)})",
        )
    }
    val forbidden = FORBIDDEN_ID_CHARS.filter { sourceId.contains(it) }.sorted()
    if (forbidden.isNotEmpty()) {
        throw UnsafeSourceError(
            "source_id contains forbidden character(s): ${forbidden.joinToString(", ")}",
        )
    }
    if (sourceId.startsWith("-")) {
        throw UnsafeSourceError("source_id must not start with '-'")
    }
    if (endsWithStrippedCharacter(sourceId)) {
        throw UnsafeSourceError("source_id must not end in a space or a dot")
    }
    if (isReservedDeviceName(sourceId)) {
        throw UnsafeSourceError(
            "source_id must not name a reserved device: '$sourceId'",
        )
    }
    val segments = sourceId.split('/', '\\')
    if (segments.any { it == "." || it == ".." }) {
        throw UnsafeSourceError("source_id must not contain a '.' or '..' segment")
    }
    return sourceId
}

/**
 * Return the directory that holds persisted uploads.
 *
 * Defaults to `<tempdir>/lmeditor_uploads` (portable across Linux, macOS and
 * Windows). `LMEDITOR_UPLOAD_DIR` overrides it so a caller can redirect the root
 * without writing into the real temp directory.
 *
 * [base] is the *configured* root the caller resolved for itself. It wins over
 * the environment variable, because the environment of this process is not
 * necessarily the environment of the caller: the Python tier runs in its own
 * process and may have been given a different `LMEDITOR_UPLOAD_DIR` than the
 * JVM was started with. Passing the configured value keeps the rule -- "which
 * directory is the upload root" -- in one place while letting the two tiers
 * agree on the answer.
 */
fun uploadRoot(base: String? = null): Path {
    if (!base.isNullOrBlank()) {
        return Paths.get(base)
    }
    val override = System.getenv("LMEDITOR_UPLOAD_DIR")
    if (!override.isNullOrBlank()) {
        return Paths.get(override)
    }
    return Paths.get(System.getProperty("java.io.tmpdir"), "lmeditor_uploads")
}

/**
 * Return a proven-contained absolute path for an uploaded source file.
 *
 * [sourceId] is validated, [filename] is reduced to its basename (so a
 * client-supplied absolute path such as `C:\Windows\x.txt` or `/etc/passwd` is
 * neutralized to `x.txt` / `passwd`), truncated to [MAX_FILENAME_LENGTH], joined
 * under [base] (or [uploadRoot]), and finally *proven* to stay inside that root.
 *
 * A file name that Windows cannot create -- one that carries a control
 * character, one that ends in a space or a dot, one that names a reserved
 * device -- is refused here, with a typed error, rather than by the
 * filesystem, with an untyped one.
 */
fun safeUploadPath(sourceId: String, filename: String, base: Path? = null): Path {
    val validatedId = validateSourceId(sourceId)

    val control = filename.firstOrNull { isControlCharacter(it) }
    if (control != null) {
        throw UnsafeSourceError(
            "filename must not contain a control character (0x${control.code.toString(16)})",
        )
    }

    // Backslashes are normalized first so a Windows-style absolute path is
    // reduced to its basename on POSIX hosts too.
    val normalized = filename.replace('\\', '/')
    var name = normalized.substringAfterLast('/')
    if (name.isBlank() || name.all { it == '.' || it == ' ' }) {
        throw UnsafeSourceError("filename has no usable basename: '$filename'")
    }
    name = name.take(MAX_FILENAME_LENGTH)
    if (endsWithStrippedCharacter(name)) {
        throw UnsafeSourceError("filename must not end in a space or a dot: '$name'")
    }
    if (isReservedDeviceName(name)) {
        throw UnsafeSourceError("filename must not name a reserved device: '$name'")
    }

    val root = (base ?: uploadRoot()).toAbsolutePath().normalize()
    val candidate = root.resolve(validatedId).resolve(name).normalize()
    if (!candidate.startsWith(root)) {
        throw UnsafeSourceError("resolved upload path escapes the upload root: $candidate")
    }
    return candidate
}

/**
 * Return the vetted `(address, port)` pairs [url] is allowed to connect to.
 *
 * This is the pinning primitive of the SSRF guard. It validates the scheme, the
 * host and the port, resolves the host **once** through [resolver] and returns
 * only those pairs whose address is not loopback / private / link-local /
 * reserved / multicast / unspecified (see [isBlockedAddress]).
 *
 * @throws UnsafeSourceError the URL is malformed, cannot be resolved, or
 *   resolves only to internal addresses.
 */
fun resolvePublicAddresses(
    url: String,
    resolver: HostResolver = JdkHostResolver,
): List<VettedAddress> {
    if (url.isBlank()) {
        throw UnsafeSourceError("url must be a non-empty string")
    }

    val scheme = schemeOf(url)
    if (scheme !in ALLOWED_URL_SCHEMES) {
        throw UnsafeSourceError(
            "url scheme '$scheme' is not allowed (http/https only): '$url'",
        )
    }

    val parsed = ParsedUrl.parse(url)
    if (parsed.host.isEmpty()) {
        throw UnsafeSourceError("url has no host: '$url'")
    }
    val port = parsed.port ?: if (scheme == "https") 443 else 80

    val resolved = resolver.resolve(parsed.host)
    if (resolved.isEmpty()) {
        throw UnsafeSourceError("could not resolve host '${parsed.host}': no addresses returned")
    }

    val vetted = resolved
        .filter { !isBlockedAddress(it) }
        .map { VettedAddress(it, port) }
    if (vetted.isEmpty()) {
        throw UnsafeSourceError(
            "host '${parsed.host}' resolves only to internal address(es): " +
                resolved.joinToString(", ") { it.hostAddress },
        )
    }
    return vetted
}

/**
 * Return [url] unchanged, or raise if it must not be fetched (SSRF guard).
 *
 * Requires an `http`/`https` scheme with a host, resolves the host and rejects
 * the URL when resolution fails or when **every** resolved address is loopback
 * / private / link-local / reserved / multicast / unspecified.
 *
 * A URL may legitimately resolve to a mix of public and internal addresses
 * (split-horizon DNS, a CDN with an internal origin), so this guard only
 * rejects an all-internal resolution. Callers that go on to open a connection
 * must therefore use [resolvePublicAddresses] and accept only the vetted pairs,
 * rather than re-resolving the name themselves.
 */
fun assertPublicUrl(url: String, resolver: HostResolver = JdkHostResolver): String {
    resolvePublicAddresses(url, resolver)
    return url
}

/**
 * Return the hostnames of every redirect target declared by a response.
 *
 * [locations] are the `Location` headers of a response; each is resolved
 * against [baseUrl] (so relative targets are handled) and the unique hostnames
 * are returned in order of appearance.
 */
fun iterRedirectHosts(baseUrl: String, locations: List<String>): List<String> {
    val hosts = mutableListOf<String>()
    for (location in locations) {
        if (location.isEmpty()) {
            continue
        }
        val target = if (baseUrl.isBlank()) location else resolveUrl(baseUrl, location)
        val host = ParsedUrl.parse(target).host
        if (host.isNotEmpty()) {
            val lowered = host.lowercase()
            if (lowered !in hosts) {
                hosts.add(lowered)
            }
        }
    }
    return hosts
}

/**
 * Return the scheme of [url], lowercased, or the empty string when it has none.
 *
 * The scheme is read leniently so that a URL without one is refused with the
 * same "scheme is not allowed" message the Python tier produces, rather than
 * with a parse error.
 */
internal fun schemeOf(url: String): String {
    val text = url.trim()
    val colon = text.indexOf(':')
    if (colon <= 0) {
        return ""
    }
    val scheme = text.substring(0, colon)
    if (!scheme.all { it.isLetterOrDigit() || it == '+' || it == '-' || it == '.' }) {
        return ""
    }
    return scheme.lowercase()
}

/**
 * Return the next hop's URL, rejecting any target that is not public.
 *
 * This is the rule that makes a redirect unable to walk the fetch into the
 * private network: the target is resolved against the current URL and then put
 * through the same policy as the first hop.
 *
 * @throws UnsafeSourceError the target failed the private-address policy.
 */
fun validatedRedirect(
    current: String,
    location: String,
    resolver: HostResolver = JdkHostResolver,
): String {
    val target = resolveUrl(current, location)
    assertPublicUrl(target, resolver)
    return target
}

/**
 * Resolve [location] against [base] the way `urllib.parse.urljoin` does.
 *
 * An absolute target wins; a scheme-relative target keeps the base's scheme; a
 * relative target replaces the last path segment of the base.
 */
fun resolveUrl(base: String, location: String): String {
    val trimmed = location.trim()
    if (trimmed.isEmpty()) {
        return base
    }
    if (base.isBlank()) {
        return trimmed
    }
    val schemeEnd = trimmed.indexOf(':')
    if (schemeEnd > 0 && trimmed.take(schemeEnd).all { it.isLetterOrDigit() || it == '+' || it == '-' || it == '.' }) {
        return trimmed
    }
    if (trimmed.startsWith("//")) {
        val scheme = ParsedUrl.parse(base).scheme
        return "$scheme:$trimmed"
    }
    val parsed = ParsedUrl.parse(base)
    val authority = parsed.authority
    if (trimmed.startsWith("/")) {
        val fragment = trimmed.substringAfter('#', "")
        val query = trimmed.substringAfter('?', "").substringBefore('#')
        val path = trimmed.substringBefore('?').substringBefore('#')
        return buildString {
            append(parsed.scheme).append("://").append(authority)
            append(path)
            if (query.isNotEmpty()) append('?').append(query)
            if (fragment.isNotEmpty()) append('#').append(fragment)
        }
    }
    val basePath = parsed.path
    val directory = if (basePath.contains('/')) basePath.substringBeforeLast('/') + "/" else "/"
    val path = directory + trimmed.substringBefore('?').substringBefore('#')
    val query = trimmed.substringAfter('?', "").substringBefore('#')
    val fragment = trimmed.substringAfter('#', "")
    return buildString {
        append(parsed.scheme).append("://").append(authority)
        append(path)
        if (query.isNotEmpty()) append('?').append(query)
        if (fragment.isNotEmpty()) append('#').append(fragment)
    }
}

/**
 * Report whether an address must not be fetched by the URL loader.
 *
 * The rules are the ones Python's `ipaddress` module applies, so the two tiers
 * refuse exactly the same addresses:
 *
 * * loopback, private, link-local, reserved, multicast and unspecified;
 * * an address that cannot be parsed at all, because an address the policy
 *   cannot even read is not one the policy should connect to;
 * * an IPv4-mapped IPv6 address (`::ffff:10.0.0.1`) is classified as the IPv4
 *   address it carries, so the mapping cannot be used to smuggle a private
 *   address past the guard.
 */
fun isBlockedAddress(address: InetAddress): Boolean = isBlockedAddressText(address.hostAddress)

/**
 * Report whether the literal [rawAddress] must not be fetched.
 *
 * @see isBlockedAddress
 */
fun isBlockedAddressText(rawAddress: String): Boolean {
    val bytes = parseIpAddress(rawAddress) ?: return true
    val effective = if (bytes.size == 16) ipv4Mapped(bytes) ?: bytes else bytes
    return when (effective.size) {
        4 -> isBlockedIpv4(effective)
        else -> isBlockedIpv6(effective)
    }
}

// --- Address classification --------------------------------------------------

private fun parseIpAddress(raw: String): ByteArray? {
    val text = raw.trim()
    if (text.isEmpty()) {
        return null
    }
    if (text.contains(':')) {
        return parseIpv6(text)
    }
    return parseIpv4(text)
}

private fun parseIpv4(text: String): ByteArray? {
    val parts = text.split('.')
    if (parts.size != 4) {
        return null
    }
    val bytes = ByteArray(4)
    for (index in parts.indices) {
        val part = parts[index]
        if (part.isEmpty() || part.length > 3 || !part.all { it.isDigit() }) {
            return null
        }
        // Python's `ipaddress` refuses a leading zero: "010.1.1.1" is ambiguous
        // between decimal and octal, so it is not an address.
        if (part.length > 1 && part[0] == '0') {
            return null
        }
        val value = part.toInt()
        if (value > 255) {
            return null
        }
        bytes[index] = value.toByte()
    }
    return bytes
}

private fun parseIpv6(text: String): ByteArray? {
    val scope = text.indexOf('%')
    val body = if (scope >= 0) text.substring(0, scope) else text
    if (body.isEmpty()) {
        return null
    }
    val doubleColon = body.indexOf("::")
    if (doubleColon != body.lastIndexOf("::")) {
        return null
    }
    val headText = if (doubleColon >= 0) body.substring(0, doubleColon) else body
    val tailText = if (doubleColon >= 0) body.substring(doubleColon + 2) else ""
    val head = splitIpv6Groups(headText)
    val tail = splitIpv6Groups(tailText)
    if (head == null || tail == null) {
        return null
    }
    val filled = if (doubleColon >= 0) 8 - (head.size + tail.size) else 0
    // A "::" must stand for at least one group of zeros.
    if (filled < 0 || (doubleColon >= 0 && filled == 0)) {
        return null
    }
    val all = mutableListOf<ByteArray>()
    all.addAll(head)
    repeat(filled) { all.add(byteArrayOf(0, 0)) }
    all.addAll(tail)
    if (all.size != 8) {
        return null
    }
    return ByteArray(16).also { out ->
        for (index in all.indices) {
            val group = all[index]
            out[index * 2] = group[0]
            out[index * 2 + 1] = group[1]
        }
    }
}

/**
 * Split the colon-separated groups of one half of an IPv6 address.
 *
 * The last group may be an embedded IPv4 address, which contributes two groups.
 */
private fun splitIpv6Groups(text: String): List<ByteArray>? {
    if (text.isEmpty()) {
        return emptyList()
    }
    val parts = text.split(':')
    val groups = mutableListOf<ByteArray>()
    for (index in parts.indices) {
        val part = parts[index]
        if (part.isEmpty()) {
            return null
        }
        if (part.contains('.')) {
            if (index != parts.size - 1) {
                return null
            }
            val v4 = parseIpv4(part) ?: return null
            groups.add(byteArrayOf(v4[0], v4[1]))
            groups.add(byteArrayOf(v4[2], v4[3]))
            continue
        }
        if (part.length > 4 || !part.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' }) {
            return null
        }
        val value = part.toInt(16)
        groups.add(byteArrayOf((value ushr 8).toByte(), (value and 0xff).toByte()))
    }
    return groups
}

/** Return the IPv4 address an IPv4-mapped IPv6 address carries, or null. */
private fun ipv4Mapped(bytes: ByteArray): ByteArray? {
    for (index in 0..9) {
        if (bytes[index] != 0.toByte()) {
            return null
        }
    }
    if (bytes[10] != 0xff.toByte() || bytes[11] != 0xff.toByte()) {
        return null
    }
    return byteArrayOf(bytes[12], bytes[13], bytes[14], bytes[15])
}

private fun isBlockedIpv4(bytes: ByteArray): Boolean {
    val b0 = bytes[0].toInt() and 0xff
    val b1 = bytes[1].toInt() and 0xff
    val b2 = bytes[2].toInt() and 0xff
    val b3 = bytes[3].toInt() and 0xff

    // Loopback: 127.0.0.0/8.
    if (b0 == 127) {
        return true
    }
    // Link-local: 169.254.0.0/16.
    if (b0 == 169 && b1 == 254) {
        return true
    }
    // Multicast: 224.0.0.0/4.
    if (b0 in 224..239) {
        return true
    }
    // Unspecified: 0.0.0.0/32.
    if (b0 == 0 && b1 == 0 && b2 == 0 && b3 == 0) {
        return true
    }
    // Reserved: 240.0.0.0/4.
    if (b0 >= 240) {
        return true
    }
    // Private, as Python's `ipaddress` defines it.
    if (b0 == 0) {
        return true // 0.0.0.0/8
    }
    if (b0 == 10) {
        return true // 10.0.0.0/8
    }
    if (b0 == 172 && b1 in 16..31) {
        return true // 172.16.0.0/12
    }
    if (b0 == 192 && b1 == 0 && b2 == 0) {
        // 192.0.0.0/24 is private, except the two anycast addresses the IANA
        // registry calls globally reachable: 192.0.0.9 (PCP anycast) and
        // 192.0.0.10 (TURN anycast). Python's `ipaddress` carves out the same
        // two, so a URL that resolves to one of them is fetched on both sides.
        return b3 != 9 && b3 != 10
    }
    if (b0 == 192 && b1 == 0 && b2 == 0 && (b3 == 170 || b3 == 171)) {
        return true // 192.0.0.170/31
    }
    if (b0 == 192 && b1 == 0 && b2 == 2) {
        return true // 192.0.2.0/24
    }
    if (b0 == 192 && b1 == 168) {
        return true // 192.168.0.0/16
    }
    if (b0 == 198 && (b1 == 18 || b1 == 19)) {
        return true // 198.18.0.0/15
    }
    if (b0 == 198 && b1 == 51 && b2 == 100) {
        return true // 198.51.100.0/24
    }
    if (b0 == 203 && b1 == 0 && b2 == 113) {
        return true // 203.0.113.0/24
    }
    if (b0 == 255 && b1 == 255 && b2 == 255 && b3 == 255) {
        return true // 255.255.255.255/32
    }
    return false
}

private fun isBlockedIpv6(bytes: ByteArray): Boolean {
    val b0 = bytes[0].toInt() and 0xff
    val b1 = bytes[1].toInt() and 0xff
    val b2 = bytes[2].toInt() and 0xff
    val b3 = bytes[3].toInt() and 0xff

    var allZero = true
    for (byte in bytes) {
        if (byte != 0.toByte()) {
            allZero = false
            break
        }
    }
    // Unspecified: ::/128.
    if (allZero) {
        return true
    }
    // Loopback: ::1/128.
    if (b0 == 0 && b1 == 0 && b2 == 0 && b3 == 0 &&
        bytes[15] == 1.toByte() &&
        bytes.copyOfRange(4, 15).all { it == 0.toByte() }
    ) {
        return true
    }
    // Link-local: fe80::/10.
    if (b0 == 0xfe && (b1 and 0xc0) == 0x80) {
        return true
    }
    // Multicast: ff00::/8.
    if (b0 == 0xff) {
        return true
    }
    // Reserved, as Python's `ipaddress` defines it.
    if (b0 == 0x00) {
        return true // ::/8
    }
    if (b0 == 0x01 && b1 == 0x00) {
        return true // 100::/8
    }
    if (b0 in 0x02..0x03) {
        return true // 200::/7
    }
    if (b0 in 0x04..0x07) {
        return true // 400::/6
    }
    if (b0 in 0x08..0x0f) {
        return true // 800::/5
    }
    if (b0 in 0x10..0x1f) {
        return true // 1000::/4
    }
    if (b0 in 0x40..0x5f) {
        return true // 4000::/3
    }
    if (b0 in 0x60..0x7f) {
        return true // 6000::/3
    }
    if (b0 in 0x80..0x9f) {
        return true // 8000::/3
    }
    if (b0 in 0xa0..0xbf) {
        return true // a000::/3
    }
    if (b0 in 0xc0..0xdf) {
        return true // c000::/3
    }
    if (b0 in 0xe0..0xef) {
        return true // e000::/4
    }
    if (b0 in 0xf0..0xf7) {
        return true // f000::/5
    }
    if (b0 in 0xf8..0xfb) {
        return true // f800::/6
    }
    if (b0 == 0xfe && (b1 and 0x80) == 0) {
        return true // fe00::/9
    }
    // Private, as Python's `ipaddress` defines it.
    if (b0 == 0x00 && b1 == 0x00 && b2 == 0x00 && b3 == 0x00) {
        return true // ::1/128 and ::/128
    }
    if (b0 == 0x00 && b1 == 0x64 && b2 == 0xff && b3 == 0x9b &&
        bytes[4] == 0x01.toByte() && bytes[5] == 0x00.toByte()
    ) {
        return true // 64:ff9b:1::/48
    }
    if (b0 == 0x01 && b1 == 0x00 && bytes.copyOfRange(2, 8).all { it == 0.toByte() }) {
        return true // 100::/64
    }
    if (b0 == 0x20 && b1 == 0x01 && (b2 and 0xf8) == 0x00) {
        // 2001::/23 is private, except the ranges the IANA registry calls
        // globally reachable. Python's `ipaddress` carves out the same ones.
        return !isGloballyReachable2001(bytes)
    }
    if (b0 == 0x20 && b1 == 0x01 && b2 == 0x0d && b3 == 0xb8) {
        return true // 2001:db8::/32
    }
    if (b0 == 0x20 && b1 == 0x02) {
        return true // 2002::/16
    }
    if (b0 == 0x3f && b1 == 0xff && (b2 and 0xf0) == 0x00) {
        return true // 3fff::/20
    }
    if (b0 in 0xfc..0xfd) {
        return true // fc00::/7
    }
    if (b0 == 0xfe && (b1 and 0xc0) == 0x80) {
        return true // fe80::/10
    }
    return false
}

// --- URL parsing -------------------------------------------------------------

/**
 * Whether the address falls in one of the globally-reachable exceptions to
 * `2001::/23`.
 *
 * `2001::/23` is the IETF protocol-assignment block, and most of it is not
 * reachable from the open internet. A handful of ranges inside it are:
 *
 * | Range | What it is |
 * | :--- | :--- |
 * | `2001:1::1/128` | Port Control Protocol anycast |
 * | `2001:1::2/128` | Traversal Using Relays around NAT anycast |
 * | `2001:3::/32` | AMT |
 * | `2001:4:112::/48` | AS112-v6 |
 * | `2001:20::/28` | ORCHIDv2 |
 * | `2001:30::/28` | Drone Remote ID Protocol Entity Tags |
 *
 * Python's `ipaddress` draws exactly these exceptions, so the Kotlin policy
 * has to draw them too -- otherwise the two tiers disagree about whether a
 * URL may be fetched, which is the one question the SSRF guard exists to
 * answer.
 */
private fun isGloballyReachable2001(bytes: ByteArray): Boolean {
    // The second group is 0x0001 for the two anycast addresses.
    if (bytes[2] == 0x00.toByte() && bytes[3] == 0x01.toByte() &&
        bytes.copyOfRange(4, 15).all { it == 0.toByte() }
    ) {
        val last = bytes[15].toInt() and 0xff
        if (last == 1 || last == 2) {
            return true
        }
    }
    // 2001:3::/32
    if (bytes[2] == 0x00.toByte() && bytes[3] == 0x03.toByte() &&
        bytes.copyOfRange(4, 8).all { it == 0.toByte() }
    ) {
        return true
    }
    // 2001:4:112::/48
    if (bytes[2] == 0x00.toByte() && bytes[3] == 0x04.toByte() &&
        bytes[4] == 0x01.toByte() && bytes[5] == 0x12.toByte() &&
        bytes.copyOfRange(6, 8).all { it == 0.toByte() }
    ) {
        return true
    }
    // 2001:20::/28 and 2001:30::/28
    if (bytes[2] == 0x00.toByte() &&
        (bytes[3].toInt() and 0xf0) == 0x20 &&
        bytes.copyOfRange(4, 8).all { it == 0.toByte() }
    ) {
        return true
    }
    return false
}

/**
 * The parts of a URL, parsed the way `urllib.parse.urlsplit` parses them.
 *
 * The authority is kept whole so that a relative target can be resolved
 * against it, and the host is lowercased because that is what `urlsplit`
 * returns.
 */
internal data class ParsedUrl(
    val scheme: String,
    val authority: String,
    val host: String,
    val port: Int?,
    val path: String,
) {
    companion object {
        /**
         * Parse [url], raising [UnsafeSourceError] when it carries an invalid
         * port or a control character.
         *
         * A control character is refused here, at the boundary every URL passes
         * through, because the URL is written into the request line of an HTTP
         * request: a carriage return or a line feed inside it would end the
         * line early and turn the rest of the URL into a header of the
         * caller's choosing.
         */
        fun parse(url: String): ParsedUrl {
            val text = url.trim()
            val control = text.firstOrNull { isControlCharacter(it) }
            if (control != null) {
                throw UnsafeSourceError(
                    "url must not contain a control character (0x${control.code.toString(16)})",
                )
            }
            val schemeEnd = text.indexOf(':')
            if (schemeEnd <= 0) {
                throw UnsafeSourceError("url has no scheme: '$url'")
            }
            val scheme = text.substring(0, schemeEnd)
            var rest = text.substring(schemeEnd + 1)
            if (rest.startsWith("//")) {
                rest = rest.substring(2)
            }
            val authorityEnd = rest.indexOfFirst { it == '/' || it == '?' || it == '#' }
            val authority = if (authorityEnd < 0) rest else rest.substring(0, authorityEnd)
            val path = if (authorityEnd < 0) "" else rest.substring(authorityEnd)

            val afterUserInfo = authority.substringAfterLast('@')
            val host: String
            val portText: String?
            if (afterUserInfo.startsWith("[")) {
                val close = afterUserInfo.indexOf(']')
                if (close < 0) {
                    throw UnsafeSourceError("url has an invalid host: '$url'")
                }
                host = afterUserInfo.substring(1, close)
                val remainder = afterUserInfo.substring(close + 1)
                portText = if (remainder.startsWith(":")) remainder.substring(1) else null
            } else {
                val colon = afterUserInfo.lastIndexOf(':')
                if (colon >= 0 && afterUserInfo.indexOf(':') == colon) {
                    host = afterUserInfo.substring(0, colon)
                    portText = afterUserInfo.substring(colon + 1)
                } else {
                    host = afterUserInfo
                    portText = null
                }
            }

            var port: Int? = null
            if (portText != null && portText.isNotEmpty()) {
                if (!portText.all { it.isDigit() }) {
                    throw UnsafeSourceError("url has an invalid port: '$url'")
                }
                val value = portText.toIntOrNull()
                    ?: throw UnsafeSourceError("url has an invalid port: '$url'")
                if (value < 1 || value > 65535) {
                    throw UnsafeSourceError("url has an invalid port: '$url'")
                }
                port = value
            }
            return ParsedUrl(
                scheme = scheme,
                authority = authority,
                host = host.lowercase(),
                port = port,
                path = path,
            )
        }
    }
}
