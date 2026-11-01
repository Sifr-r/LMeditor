package lmeditor.ingest

import lmeditor.core.SourceKind
import lmeditor.core.SourceLoadError
import java.io.ByteArrayInputStream
import java.net.InetAddress
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The source loaders, ported from `backend/tests/test_ingestion_loaders.py`
 * and `backend/tests/test_ingestion_registry.py`.
 */
class LoadersTest {

    /** A plain text file is read as it stands. */
    @Test
    fun aTextFileIsReadAsItStands() {
        val path = writeFile("notes.txt", "First line.\n\nSecond line.\n")
        val loaded = TextSourceLoader().load(SourceOrigin.File(path))
        assertEquals("First line.\n\nSecond line.\n", loaded.text)
        assertEquals(null, loaded.pageCount)
        assertTrue(loaded.byteSize!! > 0)
    }

    /** A file that is not valid UTF-8 is refused as a load error. */
    @Test
    fun anInvalidUtf8FileIsRefused() {
        val path = writeBytes("bad.txt", byteArrayOf(0x41, 0xff.toByte(), 0x42))
        val failure = assertFailsWith<SourceLoadError> {
            TextSourceLoader().load(SourceOrigin.File(path))
        }
        assertTrue(failure.reason.contains("UTF-8"))
    }

    /** A directory is refused as a load error, not as an I/O failure. */
    @Test
    fun aDirectoryIsRefusedAsALoadError() {
        val directory = Files.createTempDirectory("lmeditor-source")
        assertFailsWith<SourceLoadError> {
            TextSourceLoader().load(SourceOrigin.File(directory))
        }
    }

    /**
     * The Markdown syntax is removed and the text it wrapped is kept.
     *
     * The Python tier renders Markdown to HTML and then strips the tags; the
     * result is the text of the document, with the syntax gone.
     */
    @Test
    fun theMarkdownSyntaxIsRemoved() {
        val path = writeFile(
            "notes.md",
            """
            # Title

            Some *emphasised* and **strong** text.

            - first item
            - second item

            A [link](https://example.org) and a ![image](pic.png).

            ```kotlin
            val x = 1
            ```
            """.trimIndent() + "\n",
        )
        val loaded = MarkdownSourceLoader().load(SourceOrigin.File(path))
        assertTrue(loaded.text.contains("Title"))
        assertTrue(loaded.text.contains("emphasised"))
        assertTrue(loaded.text.contains("strong"))
        assertTrue(loaded.text.contains("first item"))
        assertTrue(loaded.text.contains("link"))
        assertTrue(loaded.text.contains("image"))
        assertTrue(loaded.text.contains("val x = 1"))
        assertTrue(!loaded.text.contains("#"))
        assertTrue(!loaded.text.contains("**"))
        assertTrue(!loaded.text.contains("]("))
    }

    /** The visible text of an HTML file is extracted. */
    @Test
    fun theVisibleTextOfHtmlIsExtracted() {
        val path = writeFile(
            "page.html",
            "<html><head><style>p { color: red; }</style></head>" +
                "<body><p>Hello &amp; welcome</p><script>var x = 1;</script></body></html>",
        )
        val loaded = HtmlSourceLoader().load(SourceOrigin.File(path))
        assertEquals("Hello & welcome", loaded.text)
    }

    /** The registry dispatches a kind to its loader. */
    @Test
    fun theRegistryDispatchesAKindToItsLoader() {
        val registry = SourceLoaderRegistry.default()
        assertEquals(SourceKind.TEXT, registry.resolve(SourceKind.TEXT).kind)
        assertEquals(SourceKind.URL, registry.resolve(SourceKind.URL).kind)

        val path = writeFile("notes.txt", "body")
        assertEquals("body", registry.load(SourceKind.TEXT, path).text)
    }

    /** A kind the registry does not know is refused. */
    @Test
    fun anUnknownKindIsRefused() {
        val registry = SourceLoaderRegistry(emptyMap())
        val failure = assertFailsWith<SourceLoadError> {
            registry.resolve(SourceKind.TEXT)
        }
        assertTrue(failure.reason.contains("no loader registered"))
    }

    /** The URL loader refuses a file path: the mistake the SSRF guard exists to prevent. */
    @Test
    fun theUrlLoaderRefusesAFilePath() {
        val registry = SourceLoaderRegistry.default()
        val path = writeFile("notes.txt", "body")
        val failure = assertFailsWith<SourceLoadError> {
            registry.load(SourceKind.URL, path)
        }
        assertTrue(failure.reason.contains("URL loader requires a URL"))
    }

    /**
     * The URL loader fetches the page and extracts its text.
     *
     * Ported from `test_url_loader_fetches_and_extracts_text`.
     */
    @Test
    fun theUrlLoaderFetchesAndExtractsText() {
        val calls = mutableListOf<String>()
        val transport = object : HttpTransport {
            override fun send(
                url: String,
                addresses: List<VettedAddress>,
                timeout: Duration,
            ): HttpExchange {
                calls.add(url)
                return HttpExchange(
                    statusCode = 200,
                    headers = mapOf("Content-Type" to listOf("text/html")),
                    peerAddress = addresses.first().ip,
                    body = ByteArrayInputStream(
                        "<html><body><h1>Fetched</h1><p>Body.</p></body></html>".toByteArray(),
                    ),
                )
            }
        }
        val loaded = UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
            .load(SourceOrigin.Remote("https://example.org/paper"))
        assertTrue(loaded.text.contains("Fetched"))
        assertTrue(loaded.text.contains("Body."))
        assertEquals(null, loaded.pageCount)
        assertEquals(listOf("https://example.org/paper"), calls)
    }

    /**
     * The size of the body that was read is reported.
     *
     * Ported from `test_url_loader_reports_the_size_of_the_body_it_read`.
     */
    @Test
    fun theUrlLoaderReportsTheSizeOfTheBodyItRead() {
        val payload = "<html><body><p>Fetched</p></body></html>"
        val transport = object : HttpTransport {
            override fun send(
                url: String,
                addresses: List<VettedAddress>,
                timeout: Duration,
            ): HttpExchange = HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("text/html")),
                peerAddress = addresses.first().ip,
                body = ByteArrayInputStream(payload.toByteArray()),
            )
        }
        val loaded = UrlSourceLoader(transport = transport, resolver = fixedResolver("93.184.216.34"))
            .load(SourceOrigin.Remote("https://example.org/paper"))
        assertEquals(payload.length.toLong(), loaded.byteSize)
    }

    /**
     * A body over the loader's cap is refused, and the rest is never read.
     *
     * Ported from `test_url_loader_refuses_an_oversized_response`.
     */
    @Test
    fun theUrlLoaderRefusesAnOversizedResponse() {
        val body = countingBody("x".repeat(100_000))
        val transport = object : HttpTransport {
            override fun send(
                url: String,
                addresses: List<VettedAddress>,
                timeout: Duration,
            ): HttpExchange = HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("text/html")),
                peerAddress = addresses.first().ip,
                body = body,
            )
        }
        val loader = UrlSourceLoader(
            transport = transport,
            resolver = fixedResolver("93.184.216.34"),
            maxResponseBytes = 1024,
        )
        val failure = assertFailsWith<SourceLoadError> {
            loader.load(SourceOrigin.Remote("https://example.org/huge"))
        }
        assertTrue(failure.reason.contains("response too large"))
        assertTrue(body.reads <= 1024 + 64, "the read must be abandoned near the cap")
    }
}

/** Write [content] into a temporary file and return its path. */
internal fun writeFile(name: String, content: String): Path {
    val directory = Files.createTempDirectory("lmeditor-source")
    val path = directory.resolve(name)
    Files.write(path, content.toByteArray(StandardCharsets.UTF_8))
    return path
}

/** Write [bytes] into a temporary file and return its path. */
internal fun writeBytes(name: String, bytes: ByteArray): Path {
    val directory = Files.createTempDirectory("lmeditor-source")
    val path = directory.resolve(name)
    Files.write(path, bytes)
    return path
}
