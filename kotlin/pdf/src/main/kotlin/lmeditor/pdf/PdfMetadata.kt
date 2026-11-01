package lmeditor.pdf

import java.nio.file.Path

/**
 * The bibliographic metadata of a PDF.
 *
 * A PDF carries an `/Info` dictionary with the title, the author, the subject
 * and the keywords its producer wrote into it, and -- often -- the paper's
 * DOI. That metadata is the only bibliographic evidence available for a PDF
 * whose title the operator never typed, so it is extracted here rather than
 * being discarded inside the loader.
 *
 * The identifiers are *found*, not *known*: the DOI or the arXiv id is looked
 * for in the metadata values and in the first page. A first page is scanned
 * only up to [FIRST_PAGE_SCAN_LIMIT] characters because a DOI that appears
 * further down is more likely to be one the paper *cites* than the paper's
 * own, and a cited DOI must not be mistaken for the document's identity.
 */
data class PdfMetadata(
    /** The `/Title` of the document. */
    val title: String? = null,
    /** The `/Author` of the document. */
    val author: String? = null,
    /** The `/Subject` of the document. */
    val subject: String? = null,
    /** The `/Keywords` of the document. */
    val keywords: String? = null,
    /** The `/Creator` of the document. */
    val creator: String? = null,
    /** The `/Producer` of the document. */
    val producer: String? = null,
    /** The `/CreationDate` of the document. */
    val creationDate: String? = null,
    /** The DOI found in the metadata or on the first page. */
    val doi: String? = null,
    /** The arXiv id found in the metadata or on the first page. */
    val arxivId: String? = null,
) {

    /**
     * The metadata as the plain-keyed mapping the Python tier returns.
     *
     * The keys are `title`, `author`, `subject`, `keywords`, `creator`,
     * `producer`, `creation_date`, `doi` and `arxiv_id`; an entry that the
     * document does not carry is absent rather than empty.
     */
    fun toMap(): Map<String, String> = buildMap {
        title?.let { put("title", it) }
        author?.let { put("author", it) }
        subject?.let { put("subject", it) }
        keywords?.let { put("keywords", it) }
        creator?.let { put("creator", it) }
        producer?.let { put("producer", it) }
        creationDate?.let { put("creation_date", it) }
        doi?.let { put("doi", it) }
        arxivId?.let { put("arxiv_id", it) }
    }

    companion object {

        /** How much of the first page is scanned for an identifier. */
        const val FIRST_PAGE_SCAN_LIMIT: Int = 3000

        /** The metadata of the PDF at [path]. */
        fun extract(path: Path): PdfMetadata = extract(PdfDocument.open(path))

        /** The metadata of the PDF held in [bytes]. */
        fun extract(bytes: ByteArray): PdfMetadata = extract(PdfDocument.parse(bytes))

        /** The metadata of [document]. */
        fun extract(document: PdfDocument): PdfMetadata {
            val metadata = PdfMetadata(
                title = document.info["Title"],
                author = document.info["Author"],
                subject = document.info["Subject"],
                keywords = document.info["Keywords"],
                creator = document.info["Creator"],
                producer = document.info["Producer"],
                creationDate = document.info["CreationDate"],
            )
            val firstPage = document.pages.firstOrNull()?.text ?: ""
            val identifiers = findIdentifiers(metadata, firstPage)
            return metadata.copy(doi = identifiers.first, arxivId = identifiers.second)
        }

        /**
         * The DOI and the arXiv id found in the metadata or on the first page.
         *
         * The metadata values are searched before the first page, and each in
         * the order the `/Info` entries are read, so the identifier of the
         * document itself wins over one that appears in a citation.
         */
        private fun findIdentifiers(metadata: PdfMetadata, firstPage: String): Pair<String?, String?> {
            val haystacks = ArrayList<String>()
            for (value in listOf(
                metadata.title,
                metadata.author,
                metadata.subject,
                metadata.keywords,
                metadata.creator,
                metadata.producer,
                metadata.creationDate,
            )) {
                if (value != null) haystacks.add(value)
            }
            if (firstPage.isNotEmpty()) haystacks.add(firstPage.take(FIRST_PAGE_SCAN_LIMIT))

            var doi: String? = null
            var arxivId: String? = null
            for (haystack in haystacks) {
                if (doi == null) doi = findDoi(haystack)
                if (arxivId == null) arxivId = findArxivId(haystack)
                if (doi != null && arxivId != null) break
            }
            return doi to arxivId
        }

        /**
         * The first DOI in [text], or `null`.
         *
         * A DOI is the `10.` prefix, a registrant code of four to nine digits
         * and a suffix. The suffix charset is deliberately wide -- a legacy
         * SICI suffix uses parentheses, semicolons and angle brackets -- and
         * the trailing punctuation of a sentence is trimmed afterwards.
         */
        private fun findDoi(text: String): String? {
            val match = DOI_PATTERN.find(text) ?: return null
            val doi = match.value.trimEnd('.', ',', ';', ':')
            return doi.ifEmpty { null }
        }

        /** The first arXiv id in [text], or `null`. */
        private fun findArxivId(text: String): String? {
            val match = ARXIV_PATTERN.find(text) ?: return null
            return match.groupValues.getOrNull(1)?.ifEmpty { null }
        }

        // `(?U)` makes `\s` Unicode-aware, which is what Python's `\s` does for
        // a `str` pattern. Without it, a DOI followed by a non-breaking space
        // would swallow the space into the identifier.
        private val DOI_PATTERN = Regex("""(?U)10\.\d{4,9}/[^\s"'<>\]]+""")

        private val ARXIV_PATTERN = Regex(
            """(?U)arXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?|[a-zA-Z-]+(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?)""",
        )
    }
}
