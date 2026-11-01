package lmeditor.ingest

import lmeditor.core.BibliographyResolverKind
import lmeditor.core.BibliographyResolutionError
import lmeditor.core.SourceKind
import java.io.ByteArrayInputStream
import java.net.InetAddress
import java.time.Duration
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * The bibliography resolver chain, ported from
 * `backend/tests/test_ingestion_resolvers.py`.
 */
class BibliographyResolversTest {

    /**
     * A DOI keeps every printable character but the five that would change the
     * structure of the request.
     */
    @Test
    fun aDoiKeepsItsPrintableCharacters() {
        assertEquals(
            "10.1234/abc.def-1_2~3",
            validatedDoi(BibliographyResolverKind.CROSSREF, "10.1234/abc.def-1_2~3"),
        )
        // The SICI DOI form carries parentheses, a colon and a semicolon: they
        // are legal in a DOI and must be percent-encoded, not rejected.
        assertEquals(
            "10.1002/(SICI)1099-0844(199912)17:4<290::AID-CBF849>3.0.CO;2-P",
            validatedDoi(
                BibliographyResolverKind.CROSSREF,
                "10.1002/(SICI)1099-0844(199912)17:4<290::AID-CBF849>3.0.CO;2-P",
            ),
        )
    }

    /** A DOI without a separator, with a traversal segment or with an illegal character is refused. */
    @Test
    fun anUnusableDoiIsRefused() {
        val unusable = listOf(
            "",
            "   ",
            "not-a-doi",
            "10.1234/../works/W1",
            "10.1234//suffix",
            "10.1234/with space",
            "10.1234/with#hash",
            "10.1234/with?query",
            "10.1234/with%percent",
            "10.1234/with\\backslash",
            "x".repeat(256),
        )
        for (doi in unusable) {
            assertFailsWith<BibliographyResolutionError>("'$doi' must be refused") {
                validatedDoi(BibliographyResolverKind.CROSSREF, doi)
            }
        }
    }

    /** An arXiv id keeps its narrow character set. */
    @Test
    fun anArxivIdKeepsItsCharacterSet() {
        assertEquals(
            "2101.12345v2",
            validatedArxivId(BibliographyResolverKind.ARXIV, "2101.12345v2"),
        )
        assertEquals(
            "hep-th/9901001",
            validatedArxivId(BibliographyResolverKind.ARXIV, "hep-th/9901001"),
        )
        for (arxivId in listOf("", "  ", "2101.12345 v2", "2101.12345#x", "2101/../x")) {
            assertFailsWith<BibliographyResolutionError>("'$arxivId' must be refused") {
                validatedArxivId(BibliographyResolverKind.ARXIV, arxivId)
            }
        }
    }

    /** A PMID is a bare decimal number of at most eight digits. */
    @Test
    fun aPmidIsABareDecimalNumber() {
        assertEquals("12345678", validatedPmid(BibliographyResolverKind.PUBMED, "12345678"))
        for (pmid in listOf("", "  ", "abc", "123-456", "123456789")) {
            assertFailsWith<BibliographyResolutionError>("'$pmid' must be refused") {
                validatedPmid(BibliographyResolverKind.PUBMED, pmid)
            }
        }
    }

    /** The identifier is percent-encoded, and the characters that carry meaning are kept. */
    @Test
    fun theIdentifierIsPercentEncoded() {
        assertEquals("10.1234/abc", encodeIdentifier("10.1234/abc"))
        assertEquals(
            "10.1002/%28SICI%29x%3Cy%3Ez",
            encodeIdentifier("10.1002/(SICI)x<y>z"),
        )
    }

    /** The CrossRef resolver asks for the work the DOI names. */
    @Test
    fun theCrossRefResolverAsksForTheWork() {
        val resolver = CrossRefBibliographyResolver()
        assertEquals(
            "https://api.crossref.org/works/10.1234/abc",
            resolver.url("10.1234/abc", null, null),
        )
        assertNull(resolver.url(null, null, null))
    }

    /** The arXiv resolver asks for the entry the id names, over Atom. */
    @Test
    fun theArxivResolverAsksForTheEntry() {
        val resolver = ArxivBibliographyResolver()
        assertEquals(
            "http://export.arxiv.org/api/query?id_list=2101.12345v2",
            resolver.url(null, "2101.12345v2", null),
        )
        assertNull(resolver.url("10.1234/abc", null, null))
    }

    /** The PubMed resolver asks for the summary the PMID names. */
    @Test
    fun thePubmedResolverAsksForTheSummary() {
        val resolver = PubMedBibliographyResolver()
        assertEquals(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi" +
                "?db=pubmed&id=12345678&retmode=json",
            resolver.url(null, null, "12345678"),
        )
        assertNull(resolver.url(null, null, null))
    }

    /** The OpenAlex resolver asks for the work the DOI names. */
    @Test
    fun theOpenAlexResolverAsksForTheWork() {
        val resolver = OpenAlexBibliographyResolver()
        assertEquals(
            "https://api.openalex.org/works/doi:10.1234/abc",
            resolver.url("10.1234/abc", null, null),
        )
    }

    /** A CrossRef payload is turned into a record. */
    @Test
    fun aCrossRefPayloadBecomesARecord() {
        val resolver = CrossRefBibliographyResolver()
        val payload = mapOf(
            "message" to mapOf(
                "title" to listOf("A study of things"),
                "author" to listOf(
                    mapOf("given" to "Ada", "family" to "Lovelace"),
                    mapOf("given" to "", "family" to "Babbage"),
                ),
                "issued" to mapOf("date-parts" to listOf(listOf(1843L))),
                "container-title" to listOf("The Journal"),
                "DOI" to "10.1234/abc",
                "URL" to "https://doi.org/10.1234/abc",
                "type" to "journal-article",
            ),
        )
        val record = resolver.parseJson(payload, null)
        assertEquals("A study of things", record!!.title)
        assertEquals(listOf("Ada Lovelace", "Babbage"), record.authors)
        assertEquals(1843, record.year)
        assertEquals("The Journal", record.containerTitle)
        assertEquals(BibliographyResolverKind.CROSSREF, record.resolver)
    }

    /** A CrossRef payload without a title resolves to nothing. */
    @Test
    fun aCrossRefPayloadWithoutATitleResolvesToNothing() {
        val resolver = CrossRefBibliographyResolver()
        assertNull(resolver.parseJson(mapOf("message" to mapOf<String, Any?>()), null))
    }

    /** A PubMed payload is turned into a record. */
    @Test
    fun aPubmedPayloadBecomesARecord() {
        val resolver = PubMedBibliographyResolver()
        val payload = mapOf(
            "result" to mapOf(
                "uids" to listOf("12345678"),
                "12345678" to mapOf(
                    "title" to "  A study of things  ",
                    "authors" to listOf(mapOf("name" to "Lovelace A")),
                    "pubdate" to "1843 Jan",
                    "source" to "The Journal",
                ),
            ),
        )
        val record = resolver.parseJson(payload, "12345678")
        assertEquals("A study of things", record!!.title)
        assertEquals(listOf("Lovelace A"), record.authors)
        assertEquals(1843, record.year)
        assertEquals("The Journal", record.containerTitle)
    }

    /** An OpenAlex payload is turned into a record. */
    @Test
    fun anOpenAlexPayloadBecomesARecord() {
        val resolver = OpenAlexBibliographyResolver()
        val payload = mapOf(
            "title" to "A study of things",
            "authorships" to listOf(
                mapOf("author" to mapOf("display_name" to "Ada Lovelace")),
            ),
            "publication_year" to 1843L,
            "primary_location" to mapOf("source" to mapOf("display_name" to "The Journal")),
            "doi" to "https://doi.org/10.1234/abc",
            "type" to "article",
        )
        val record = resolver.parseJson(payload, null)
        assertEquals("A study of things", record!!.title)
        assertEquals(listOf("Ada Lovelace"), record.authors)
        assertEquals(1843, record.year)
        assertEquals("The Journal", record.containerTitle)
    }

    /** An arXiv Atom feed is turned into a record. */
    @Test
    fun anArxivAtomFeedBecomesARecord() {
        val resolver = ArxivBibliographyResolver()
        val feed = """
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/2101.12345v2</id>
                <published>2021-01-30T18:00:00Z</published>
                <title>A study of things</title>
                <author><name>Ada Lovelace</name></author>
                <author><name>Charles Babbage</name></author>
              </entry>
            </feed>
        """.trimIndent()
        val record = resolver.parseAtom(feed)
        assertEquals("A study of things", record!!.title)
        assertEquals(listOf("Ada Lovelace", "Charles Babbage"), record.authors)
        assertEquals(2021, record.year)
        assertEquals("http://arxiv.org/abs/2101.12345v2", record.url)
    }

    /** An arXiv feed with no entry resolves to nothing. */
    @Test
    fun anArxivFeedWithoutAnEntryResolvesToNothing() {
        val resolver = ArxivBibliographyResolver()
        val feed = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
        assertNull(resolver.parseAtom(feed))
    }

    /** The PDF header resolver reads the metadata the document carries. */
    @Test
    fun thePdfHeaderResolverReadsTheMetadata() {
        val resolver = PdfHeaderBibliographyResolver()
        val record = resolver.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                pdfMetadata = mapOf(
                    "title" to "Municipal Waste Audit",
                    "author" to "LMeditor; A. Auditor",
                ),
            ),
        )
        assertEquals("Municipal Waste Audit", record!!.title)
        assertEquals(listOf("LMeditor", "A. Auditor"), record.authors)
        assertEquals(BibliographyResolverKind.PDF_HEADER, record.resolver)
    }

    /** A PDF without metadata resolves to nothing. */
    @Test
    fun aPdfWithoutMetadataResolvesToNothing() {
        val resolver = PdfHeaderBibliographyResolver()
        assertNull(
            resolver.resolve(
                ResolutionRequest(sourceId = "doc_01", sourceKind = SourceKind.PDF),
            ),
        )
    }

    /**
     * When an identifier is known, the resolver that can verify it is tried
     * before the one that can only report what the document says.
     */
    @Test
    fun anIdentifierResolverIsTriedBeforeTheHeaderResolver() {
        val header = PdfHeaderBibliographyResolver()
        val crossRef = CrossRefBibliographyResolver(
            transport = stubTransport(
                """
                {"message": {"title": ["Verified by CrossRef"], "DOI": "10.1234/abc"}}
                """.trimIndent(),
            ),
        )
        val chain = BibliographyResolverChain(listOf(header, crossRef))
        val record = chain.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "10.1234/abc",
                pdfMetadata = mapOf("title" to "Title from the PDF"),
            ),
        )
        assertEquals("Verified by CrossRef", record!!.title)
        assertEquals(BibliographyResolverKind.CROSSREF, record.resolver)
    }

    /** A resolver that fails does not stop the chain. */
    @Test
    fun aFailingResolverDoesNotStopTheChain() {
        val failing = CrossRefBibliographyResolver(
            transport = stubTransport("this is not json"),
        )
        val header = PdfHeaderBibliographyResolver()
        val chain = BibliographyResolverChain(listOf(failing, header))
        val record = chain.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "10.1234/abc",
                pdfMetadata = mapOf("title" to "Title from the PDF"),
            ),
        )
        assertEquals("Title from the PDF", record!!.title)
        assertEquals(BibliographyResolverKind.PDF_HEADER, record.resolver)
    }

    /** The DOI a PDF's metadata carries is put on the record. */
    @Test
    fun theDoiOfTheMetadataIsPutOnTheRecord() {
        val resolver = PdfHeaderBibliographyResolver()
        val record = resolver.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                pdfMetadata = mapOf(
                    "title" to "A study of things",
                    "subject" to "doi:10.1234/abc",
                ),
            ),
        )
        assertEquals("10.1234/abc", record!!.doi)
    }

    /**
     * The requested PMID wins over the first one the envelope lists.
     *
     * Ported from `test_pubmed_uses_the_requested_pmid_over_the_first_uid`.
     */
    @Test
    fun aPubmedPayloadUsesTheRequestedUid() {
        val resolver = PubMedBibliographyResolver()
        val payload = mapOf(
            "result" to mapOf(
                "uids" to listOf("11111111", "22222222"),
                "11111111" to mapOf("title" to "The first one"),
                "22222222" to mapOf("title" to "The second one"),
            ),
        )
        val record = resolver.parseJson(payload, "22222222")
        assertEquals("The second one", record!!.title)
        assertEquals("https://pubmed.ncbi.nlm.nih.gov/22222222/", record.url)
    }

    /**
     * A requested PMID the envelope does not list falls back to the first one.
     *
     * Ported from `test_pubmed_falls_back_to_the_first_uid_when_requested_is_absent`.
     */
    @Test
    fun aPubmedPayloadFallsBackToTheFirstUid() {
        val resolver = PubMedBibliographyResolver()
        val payload = mapOf(
            "result" to mapOf(
                "uids" to listOf("11111111", "22222222"),
                "11111111" to mapOf("title" to "The first one"),
                "22222222" to mapOf("title" to "The second one"),
            ),
        )
        val record = resolver.parseJson(payload, "33333333")
        assertEquals("The first one", record!!.title)
    }

    /**
     * An envelope the resolver cannot read resolves to nothing.
     *
     * Ported from `test_pubmed_returns_none_for_unusable_envelopes`.
     */
    @Test
    fun anUnusablePubmedEnvelopeResolvesToNothing() {
        val resolver = PubMedBibliographyResolver()
        val unusable = listOf(
            mapOf<String, Any?>(),
            mapOf("result" to null),
            mapOf("result" to listOf("not", "a", "mapping")),
            mapOf("result" to mapOf<String, Any?>()),
            mapOf("result" to mapOf("uids" to listOf("11111111"))),
            mapOf("result" to mapOf("uids" to listOf("11111111"), "11111111" to "not a mapping")),
            mapOf("result" to mapOf("uids" to listOf("11111111"), "11111111" to mapOf<String, Any?>())),
            mapOf(
                "result" to mapOf(
                    "uids" to listOf("11111111"),
                    "11111111" to mapOf("title" to "   "),
                ),
            ),
        )
        for (payload in unusable) {
            assertNull(resolver.parseJson(payload, "11111111"), "'$payload' must resolve to nothing")
        }
    }

    /**
     * An arXiv entry without a title resolves to nothing.
     *
     * Ported from `test_arxiv_returns_none_when_the_entry_has_no_title`.
     */
    @Test
    fun anArxivEntryWithoutATitleResolvesToNothing() {
        val resolver = ArxivBibliographyResolver()
        val feed = """
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>http://arxiv.org/abs/2101.12345v2</id>
                <title>   </title>
              </entry>
            </feed>
        """.trimIndent()
        assertNull(resolver.parseAtom(feed))
    }

    /**
     * A feed that does not declare the Atom namespace is still read.
     *
     * Ported from `test_arxiv_tolerates_a_feed_without_the_atom_namespace`.
     */
    @Test
    fun anArxivFeedWithoutTheAtomNamespaceIsTolerated() {
        val resolver = ArxivBibliographyResolver()
        val feed = """
            <?xml version="1.0" encoding="UTF-8"?>
            <feed>
              <entry>
                <id>http://arxiv.org/abs/2101.12345v2</id>
                <published>2021-01-30T18:00:00Z</published>
                <title>A study of things</title>
                <author><name>Ada Lovelace</name></author>
              </entry>
            </feed>
        """.trimIndent()
        val record = resolver.parseAtom(feed)
        assertEquals("A study of things", record!!.title)
        assertEquals(listOf("Ada Lovelace"), record.authors)
    }

    /**
     * A chain falls through to the next resolver when one fails.
     *
     * Ported from `test_chain_falls_through_to_pubmed_after_arxiv_fails`.
     */
    @Test
    fun theChainFallsThroughToPubmedAfterArxivFails() {
        val arxiv = ArxivBibliographyResolver(transport = stubTransport("not xml at all"))
        val pubmed = PubMedBibliographyResolver(
            transport = stubTransport(
                """
                {"result": {"uids": ["11111111"], "11111111": {"title": "Verified by PubMed"}}}
                """.trimIndent(),
            ),
        )
        val chain = BibliographyResolverChain(listOf(arxiv, pubmed))
        val record = chain.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                pmid = "11111111",
            ),
        )
        assertEquals("Verified by PubMed", record!!.title)
        assertEquals(BibliographyResolverKind.PUBMED, record.resolver)
    }

    /**
     * A chain falls through when the identifier itself is refused.
     *
     * Ported from `test_chain_falls_through_after_a_rejected_identifier`.
     */
    @Test
    fun theChainFallsThroughAfterARejectedIdentifier() {
        val crossRef = CrossRefBibliographyResolver(transport = stubTransport("{}"))
        val header = PdfHeaderBibliographyResolver()
        val chain = BibliographyResolverChain(listOf(crossRef, header))
        val record = chain.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "not-a-doi",
                pdfMetadata = mapOf("title" to "Title from the PDF"),
            ),
        )
        assertEquals("Title from the PDF", record!!.title)
        assertEquals(BibliographyResolverKind.PDF_HEADER, record.resolver)
    }

    /**
     * A well-formed request resolves end to end.
     *
     * Ported from `test_well_formed_resolve_is_unchanged_end_to_end`: the
     * request is issued, the payload is read and the record is stamped with
     * the resolver that answered.
     */
    @Test
    fun aWellFormedResolveIsUnchangedEndToEnd() {
        val calls = mutableListOf<String>()
        val transport = scriptedTransport(calls) { url ->
            HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("application/json")),
                peerAddress = "93.184.216.34",
                body = ByteArrayInputStream(
                    """
                    {"message": {"title": ["A study of things"], "DOI": "10.1234/abc"}}
                    """.trimIndent().toByteArray(),
                ),
            )
        }
        val resolver = CrossRefBibliographyResolver(
            transport = transport,
            hostResolver = literalHostResolver(),
        )
        val record = resolver.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "10.1234/abc",
            ),
        )
        assertEquals("A study of things", record!!.title)
        assertEquals("10.1234/abc", record.doi)
        assertEquals(BibliographyResolverKind.CROSSREF, record.resolver)
        assertEquals(listOf("https://api.crossref.org/works/10.1234/abc"), calls)
    }

    /**
     * A DOI in the SICI form reaches the request and resolves.
     *
     * Ported from `test_wiley_sici_doi_reaches_a_request_and_resolves`: the
     * parentheses, the colon and the semicolon are legal in a DOI and are
     * percent-encoded rather than rejected.
     */
    @Test
    fun aSiciDoiReachesTheRequestAndResolves() {
        val calls = mutableListOf<String>()
        val transport = scriptedTransport(calls) { url ->
            HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("application/json")),
                peerAddress = "93.184.216.34",
                body = ByteArrayInputStream(
                    """{"message": {"title": ["A study of things"]}}""".toByteArray(),
                ),
            )
        }
        val resolver = CrossRefBibliographyResolver(
            transport = transport,
            hostResolver = literalHostResolver(),
        )
        val record = resolver.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "10.1002/(SICI)1099-0844(199912)17:4<290::AID-CBF849>3.0.CO;2-P",
            ),
        )
        assertEquals("A study of things", record!!.title)
        assertEquals(1, calls.size)
        assertTrue(
            calls[0].startsWith("https://api.crossref.org/works/10.1002/%28SICI%29"),
            "the SICI suffix must be percent-encoded, not dropped: ${calls[0]}",
        )
    }

    /**
     * A redirect into the private network is refused by the resolver.
     *
     * Ported from `test_resolver_refuses_a_redirect_to_a_private_address`.
     */
    @Test
    fun aResolverRefusesARedirectToAPrivateAddress() {
        val transport = scriptedTransport(mutableListOf()) { url ->
            HttpExchange(
                statusCode = 302,
                headers = mapOf("Location" to listOf("http://169.254.169.254/latest/meta-data/")),
                peerAddress = "93.184.216.34",
                body = ByteArrayInputStream(ByteArray(0)),
            )
        }
        val resolver = CrossRefBibliographyResolver(
            transport = transport,
            hostResolver = literalHostResolver(),
        )
        val failure = assertFailsWith<BibliographyResolutionError> {
            resolver.resolve(
                ResolutionRequest(
                    sourceId = "doc_01",
                    sourceKind = SourceKind.PDF,
                    doi = "10.1234/abc",
                ),
            )
        }
        assertTrue(failure.reason.contains("blocked URL"))
    }

    /**
     * A redirect to a public address is followed.
     *
     * Ported from `test_resolver_follows_a_redirect_to_a_public_address`.
     */
    @Test
    fun aResolverFollowsARedirectToAPublicAddress() {
        val calls = mutableListOf<String>()
        val transport = scriptedTransport(calls) { url ->
            if (url.endsWith("/works/10.1234/abc")) {
                HttpExchange(
                    statusCode = 302,
                    headers = mapOf("Location" to listOf("https://api.crossref.org/works/redirected")),
                    peerAddress = "93.184.216.34",
                    body = ByteArrayInputStream(ByteArray(0)),
                )
            } else {
                HttpExchange(
                    statusCode = 200,
                    headers = mapOf("Content-Type" to listOf("application/json")),
                    peerAddress = "93.184.216.34",
                    body = ByteArrayInputStream(
                        """{"message": {"title": ["A study of things"]}}""".toByteArray(),
                    ),
                )
            }
        }
        val resolver = CrossRefBibliographyResolver(
            transport = transport,
            hostResolver = literalHostResolver(),
        )
        val record = resolver.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                doi = "10.1234/abc",
            ),
        )
        assertEquals("A study of things", record!!.title)
        assertEquals(
            listOf(
                "https://api.crossref.org/works/10.1234/abc",
                "https://api.crossref.org/works/redirected",
            ),
            calls,
        )
    }

    /**
     * The chain derives the DOI from the PDF's own metadata.
     *
     * Ported from `test_chain_derives_the_doi_from_the_pdf_metadata`: the
     * identifier the document carries is the one the chain asks about.
     */
    @Test
    fun theChainDerivesTheDoiFromThePdfMetadata() {
        val calls = mutableListOf<String>()
        val transport = scriptedTransport(calls) { url ->
            HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("application/json")),
                peerAddress = "93.184.216.34",
                body = ByteArrayInputStream(
                    """{"message": {"title": ["Verified by CrossRef"], "DOI": "10.1234/abc"}}""".toByteArray(),
                ),
            )
        }
        val chain = BibliographyResolverChain(
            listOf(PdfHeaderBibliographyResolver(), CrossRefBibliographyResolver(transport = transport)),
        )
        val record = chain.resolve(
            ResolutionRequest(
                sourceId = "doc_01",
                sourceKind = SourceKind.PDF,
                pdfMetadata = mapOf("title" to "Title from the PDF", "subject" to "doi:10.1234/abc"),
            ),
        )
        assertEquals("Verified by CrossRef", record!!.title)
        assertEquals(BibliographyResolverKind.CROSSREF, record.resolver)
        assertEquals(listOf("https://api.crossref.org/works/10.1234/abc"), calls)
    }

    /**
     * A resolver answer over the cap is refused instead of being buffered.
     *
     * Ported from `test_resolver_refuses_an_oversized_response`.
     */
    @Test
    fun aResolverRefusesAnOversizedResponse() {
        val body = countingBody("x".repeat(100_000))
        val transport = object : HttpTransport {
            override fun send(
                url: String,
                addresses: List<VettedAddress>,
                timeout: Duration,
            ): HttpExchange = HttpExchange(
                statusCode = 200,
                headers = mapOf("Content-Type" to listOf("application/json")),
                peerAddress = addresses.firstOrNull()?.ip,
                body = body,
            )
        }
        val resolver = CrossRefBibliographyResolver(
            transport = transport,
            hostResolver = literalHostResolver(),
            maxResponseBytes = 1024,
        )
        val failure = assertFailsWith<BibliographyResolutionError> {
            resolver.resolve(
                ResolutionRequest(
                    sourceId = "doc_01",
                    sourceKind = SourceKind.PDF,
                    doi = "10.1234/big",
                ),
            )
        }
        assertTrue(failure.reason.contains("exceeds the maximum"))
        assertTrue(body.reads <= 1024 + 64, "the read must be abandoned near the cap")
    }
}

/** A transport that answers every request with [body]. */
private fun stubTransport(body: String): HttpTransport = object : HttpTransport {
    override fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange =
        HttpExchange(
            statusCode = 200,
            headers = mapOf("Content-Type" to listOf("application/json")),
            peerAddress = addresses.firstOrNull()?.ip,
            body = ByteArrayInputStream(body.toByteArray()),
        )
}

/** A transport that records the URLs it is asked for and answers with [respond]. */
private class scriptedTransport(
    private val calls: MutableList<String>,
    private val respond: (String) -> HttpExchange,
) : HttpTransport {
    override fun send(url: String, addresses: List<VettedAddress>, timeout: Duration): HttpExchange {
        calls.add(url)
        return respond(url)
    }
}

/**
 * A resolver that answers with the address the host *is*.
 *
 * A literal address resolves to itself, so the policy sees the address the
 * URL named; a name is resolved by the JDK, which is what the default
 * resolver does.
 */
private fun literalHostResolver(): HostResolver = HostResolver { host ->
    listOf(InetAddress.getByName(host))
}
