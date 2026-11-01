package lmeditor.factcheck

import lmeditor.core.WebEvidenceError
import java.time.Duration
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The web-evidence seam and the SSRF policy, ported from
 * `backend/src/lmeditor/factcheck/web.py` and
 * `backend/src/lmeditor/ingestion/url_policy.py`.
 *
 * No test in this file touches the network: the addresses are literal, so
 * the policy is exercised without a name server, and the transport is a
 * recording fake, so the provider is exercised without a network.
 */
class WebEvidenceTest {

    // ------------------------------------------------------------------
    // 1. The search request names a constant host
    // ------------------------------------------------------------------

    @Test
    fun theWebDefaultsAreTheNumbersThePythonDoorWritesOut() {
        // The door writes these numbers out rather than reading them across
        // the seam, so the tier holds the same numbers and this test pins
        // them: the two copies cannot drift.
        assertEquals(8.0, DEFAULT_WEB_TIMEOUT_SECONDS, 0.0)
        assertEquals(262_144, DEFAULT_WEB_MAX_BYTES)
        assertEquals(4_000, MAX_PAGE_TEXT_CHARS)
    }

    @Test
    fun theSearchRequestNamesAConstantHostAndCarriesTheQueryEncoded() {
        val url = WebSearchEndpoints.buildSearchUrl("coral reefs & warming")

        assertEquals("https://html.duckduckgo.com/html/?q=coral+reefs+%26+warming", url)
        assertEquals("html.duckduckgo.com", WebSearchEndpoints.HOST)
        assertEquals("/html/", WebSearchEndpoints.SEARCH_PATH)
        assertEquals("https://html.duckduckgo.com/html/", WebSearchEndpoints.SEARCH_BASE)
    }

    @Test
    fun theSearchProviderRefusesAnEmptyQuery() {
        // An empty query is a typed error, not an empty result.
        assertFailsWith<WebEvidenceError> {
            WebSearchEndpoints.buildSearchUrl("   ")
        }
    }

    // ------------------------------------------------------------------
    // 2. The result list is parsed
    // ------------------------------------------------------------------

    @Test
    fun aWellFormedResultListIsParsedIntoResultsWithTheirAddresses() {
        val body = """
            <html><body>
            <div class="result results_links web-result">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a"
                   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus&amp;rut=abc">Global reef census</a>
              </h2>
              <a class="result__snippet"
                 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus">Coral reefs shelter a quarter of all marine species.</a>
            </div>
            </body></html>
        """.trimIndent().toByteArray(Charsets.UTF_8)

        val results = WebSearchEndpoints.parseSearchResults(body)

        assertEquals(1, results.size)
        assertEquals("https://reefs.example.org/census", results[0].url)
        assertEquals("Global reef census", results[0].title)
        assertTrue(results[0].snippet.contains("quarter"))
    }

    @Test
    fun aBodyThatHoldsNothingRecognisableYieldsNoResults() {
        val results = WebSearchEndpoints.parseSearchResults(
            "<html><body><p>no results</p></body></html>".toByteArray(Charsets.UTF_8),
        )

        assertTrue(results.isEmpty())
    }

    @Test
    fun aResultThatIsAlreadyAbsoluteIsReturnedUnchanged() {
        assertEquals(
            "https://reefs.example.org/census",
            WebSearchEndpoints.normaliseResultUrl("https://reefs.example.org/census"),
        )
    }

    @Test
    fun aProtocolRelativeResultIsGivenTheHttpsScheme() {
        assertEquals(
            "https://reefs.example.org/census",
            WebSearchEndpoints.normaliseResultUrl("//reefs.example.org/census"),
        )
    }

    // ------------------------------------------------------------------
    // 3. The null provider
    // ------------------------------------------------------------------

    @Test
    fun theNullProviderConsultsNothing() {
        assertTrue(NullWebSearchProvider().search("coral reefs").isEmpty())
        assertTrue(NullWebSearchProvider().search("coral reefs", limit = 3).isEmpty())
    }

    // ------------------------------------------------------------------
    // 5. The provider asks the transport for the right address
    // ------------------------------------------------------------------

    @Test
    fun theSearchProviderAsksTheTransportForALiteralHostUrlWithAnEncodedQuery() {
        val fetcher = RecordingFetcher()

        val results = DuckDuckGoSearchProvider(fetcher).search("coral reefs & warming", limit = 3)

        assertEquals(1, fetcher.asked.size)
        val url = fetcher.asked[0]
        assertTrue(url.startsWith("https://html.duckduckgo.com/html/?"), url)
        assertEquals("html.duckduckgo.com", WebSearchEndpoints.HOST)
        assertEquals("/html/", WebSearchEndpoints.SEARCH_PATH)
        assertTrue(url.contains("q=coral+reefs+%26+warming"), url)
        assertEquals(emptyList(), results)
    }

    @Test
    fun theSearchProviderParsesTheResultList() {
        val body = """
            <html><body>
            <div class="result results_links web-result">
              <h2 class="result__title">
                <a rel="nofollow" class="result__a"
                   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus&amp;rut=abc">Global reef census</a>
              </h2>
              <a class="result__snippet"
                 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus">Coral reefs shelter a quarter of all marine species.</a>
            </div>
            </body></html>
        """.trimIndent().toByteArray(Charsets.UTF_8)
        val fetcher = RecordingFetcher(body)

        val results = DuckDuckGoSearchProvider(fetcher).search("coral reefs", limit = 5)

        assertEquals(1, results.size)
        assertEquals("https://reefs.example.org/census", results[0].url)
        assertEquals("Global reef census", results[0].title)
        assertTrue(results[0].snippet.contains("quarter"))
    }

    @Test
    fun theSearchProviderReportsAFailedRequestAsADomainError() {
        val fetcher = RecordingFetcher(failure = WebFailure.FAILED)

        val failure = assertFailsWith<WebEvidenceError> {
            DuckDuckGoSearchProvider(fetcher).search("coral reefs")
        }

        assertTrue(failure.reason.startsWith("the search request failed"), failure.reason)
        assertEquals("coral reefs", failure.query)
    }

    @Test
    fun theSearchProviderRefusesAnEmptyQueryBeforeItReachesTheTransport() {
        // An empty query is a typed error, not an empty result, and it never
        // reaches the transport.
        val fetcher = RecordingFetcher()

        val failure = assertFailsWith<WebEvidenceError> {
            DuckDuckGoSearchProvider(fetcher).search("   ")
        }

        assertEquals("the search query must be a non-empty string", failure.reason)
        assertTrue(fetcher.asked.isEmpty())
    }

    @Test
    fun aLimitOfZeroOrLessAsksTheTransportForNothing() {
        val fetcher = RecordingFetcher()

        assertTrue(DuckDuckGoSearchProvider(fetcher).search("coral reefs", limit = 0).isEmpty())
        assertTrue(fetcher.asked.isEmpty())
    }

    @Test
    fun theSearchProviderReturnsAtMostTheRequestedNumberOfResults() {
        val body = """
            <html><body>
            <a class="result__a" href="https://a.example.org/1">First</a>
            <a class="result__snippet" href="https://a.example.org/1">One.</a>
            <a class="result__a" href="https://b.example.org/2">Second</a>
            <a class="result__snippet" href="https://b.example.org/2">Two.</a>
            </body></html>
        """.trimIndent().toByteArray(Charsets.UTF_8)

        val results = DuckDuckGoSearchProvider(RecordingFetcher(body)).search("reefs", limit = 1)

        assertEquals(1, results.size)
        assertEquals("https://a.example.org/1", results[0].url)
    }

    @Test
    fun aRefusedAddressLeavesTheProviderAsATypedError() {
        val fetcher = RecordingFetcher(failure = WebFailure.ADDRESS_REFUSED)

        val failure = assertFailsWith<WebEvidenceError> {
            DuckDuckGoSearchProvider(fetcher).search("coral reefs")
        }

        assertTrue(failure.reason.startsWith("the search URL was refused"), failure.reason)
    }

    // ------------------------------------------------------------------
    // 6. The page-reading path
    // ------------------------------------------------------------------

    @Test
    fun thePageTextIsTheVisibleTextOfTheDocument() {
        val text = extractPageText(
            "<html><body><h1>Reef census</h1><p>Coral reefs shelter.</p></body></html>"
                .toByteArray(Charsets.UTF_8),
        )

        assertEquals("Reef census Coral reefs shelter.", text)
    }

    @Test
    fun thePageTextCollapsesTheWhitespaceThatIsLeft() {
        val text = extractPageText("<p>a   b\n\nc</p>".toByteArray(Charsets.UTF_8))

        assertEquals("a b c", text)
    }

    @Test
    fun thePageReaderReturnsTheVisibleTextOfThePage() {
        val fetcher = RecordingFetcher("<html><body><p>Reef census</p></body></html>".toByteArray())

        val text = HttpWebPageReader(fetcher).readPageText("https://reefs.example.org/census")

        assertEquals("Reef census", text)
        assertEquals(listOf("https://reefs.example.org/census"), fetcher.asked)
    }

    @Test
    fun thePageReaderRefusesAnEmptyUrl() {
        val failure = assertFailsWith<WebEvidenceError> {
            fetchPageText("   ", RecordingFetcher())
        }

        assertEquals("the page URL must be a non-empty string", failure.reason)
    }

    @Test
    fun aRefusedPageLeavesTheReaderAsATypedError() {
        val fetcher = RecordingFetcher(failure = WebFailure.ADDRESS_REFUSED)

        val failure = assertFailsWith<WebEvidenceError> {
            HttpWebPageReader(fetcher).readPageText("http://127.0.0.1/")
        }

        assertTrue(failure.reason.startsWith("the page URL was refused"), failure.reason)
    }

    @Test
    fun anOversizedPageLeavesTheReaderAsATypedError() {
        val fetcher = RecordingFetcher(failure = WebFailure.TOO_LARGE)

        val failure = assertFailsWith<WebEvidenceError> {
            HttpWebPageReader(fetcher).readPageText("https://reefs.example.org/census")
        }

        assertTrue(failure.reason.startsWith("the page was too large"), failure.reason)
    }

    // ------------------------------------------------------------------
    // 7. The result address is normalised the way a join normalises it
    // ------------------------------------------------------------------

    @Test
    fun aRelativeResultAddressIsJoinedAgainstTheSearchBase() {
        assertEquals(
            "https://html.duckduckgo.com/html/census",
            WebSearchEndpoints.normaliseResultUrl("census"),
        )
        assertEquals(
            "https://html.duckduckgo.com/html/census",
            WebSearchEndpoints.normaliseResultUrl("./census"),
        )
        assertEquals(
            "https://html.duckduckgo.com/census",
            WebSearchEndpoints.normaliseResultUrl("/census"),
        )
        assertEquals(
            "https://html.duckduckgo.com/census",
            WebSearchEndpoints.normaliseResultUrl("../census"),
        )
    }

    @Test
    fun aResultAddressThatCarriesOnlyAQueryOrAFragmentKeepsThePath() {
        assertEquals(
            "https://html.duckduckgo.com/html/?q=1",
            WebSearchEndpoints.normaliseResultUrl("?q=1"),
        )
        assertEquals(
            "https://html.duckduckgo.com/html/#frag",
            WebSearchEndpoints.normaliseResultUrl("#frag"),
        )
    }

    @Test
    fun aResultAddressThatNamesASchemeOfItsOwnIsLeftAlone() {
        assertEquals("mailto:a@b", WebSearchEndpoints.normaliseResultUrl("mailto:a@b"))
    }

    // ------------------------------------------------------------------
    // 8. The SSRF policy
    // ------------------------------------------------------------------

    @Test
    fun aLoopbackAddressIsRefused() {
        assertRefused("http://127.0.0.1/")
        assertRefused("http://127.1.2.3:8080/path")
    }

    @Test
    fun aPrivateAddressIsRefused() {
        assertRefused("http://10.0.0.1/")
        assertRefused("http://172.16.0.1/")
        assertRefused("http://192.168.1.1/")
    }

    @Test
    fun aLinkLocalAddressIsRefused() {
        // The cloud metadata endpoint: the reason the policy exists.
        assertRefused("http://169.254.169.254/")
    }

    @Test
    fun anUnspecifiedOrMulticastAddressIsRefused() {
        assertRefused("http://0.0.0.0/")
        assertRefused("http://224.0.0.1/")
    }

    @Test
    fun anIpv6LoopbackOrUniqueLocalAddressIsRefused() {
        assertRefused("http://[::1]/")
        assertRefused("http://[fd00::1]/")
        assertRefused("http://[fe80::1]/")
    }

    @Test
    fun anIpv4MappedAddressIsJudgedAsTheIpv4AddressItCarries() {
        assertRefused("http://[::ffff:127.0.0.1]/")
    }

    @Test
    fun aSchemeThatIsNotHttpIsRefused() {
        assertRefused("ftp://example.com/")
        assertRefused("file:///etc/passwd")
    }

    @Test
    fun anAddressWithoutAHostIsRefused() {
        assertRefused("")
        assertRefused("   ")
        assertRefused("http://")
    }

    @Test
    fun aPublicAddressIsAllowed() {
        // A literal address needs no name server, so the check is
        // deterministic and offline.
        assertEquals("http://93.184.216.34/", UrlPolicy.assertPublicUrl("http://93.184.216.34/"))
        assertEquals(
            "http://[2606:2800:220:1:248:1893:25c8:1946]/",
            UrlPolicy.assertPublicUrl("http://[2606:2800:220:1:248:1893:25c8:1946]/"),
        )
    }

    @Test
    fun theVettedAddressesAreTheOnesThatWereInspected() {
        val vetted = UrlPolicy.resolvePublicAddresses("http://93.184.216.34:8080/x")

        assertEquals(1, vetted.size)
        assertEquals("93.184.216.34", vetted[0].address.hostAddress)
        assertEquals(8080, vetted[0].port)
    }

    @Test
    fun theRefusalNamesTheAddressThatWasInternal() {
        val failure = assertFailsWith<WebEvidenceError> {
            UrlPolicy.assertPublicUrl("http://127.0.0.1/")
        }
        assertTrue(failure.reason.contains("internal address"))
    }

    /** The URL must be refused with a typed web error. */
    private fun assertRefused(url: String) {
        assertFailsWith<WebEvidenceError>("expected '$url' to be refused") {
            UrlPolicy.assertPublicUrl(url)
        }
    }
}

/**
 * A transport that answers from a fixed body and records what it was asked for.
 *
 * The tier's rule is what is being tested, so the transport is a seam: it
 * answers with a body, or fails in one of the four ways a real transport can
 * fail, and it keeps the addresses it was asked for so that a test can prove
 * which one the provider built.
 */
private class RecordingFetcher(
    private val body: ByteArray = "<html><body><p>no results</p></body></html>".toByteArray(),
    private val failure: WebFailure? = null,
) : WebFetcher {

    /** The addresses the transport was asked for, in order. */
    val asked: MutableList<String> = mutableListOf()

    override fun fetch(url: String, timeout: Duration, maxBytes: Int): ByteArray {
        asked.add(url)
        val reason = failure
        if (reason != null) {
            throw WebRequestError(reason, "the network is down")
        }
        return body
    }
}
