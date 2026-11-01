package lmeditor.pdf

import lmeditor.core.SourceLoadError
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path

/**
 * A parsed PDF: its pages, the metadata it carries, and the limitations found.
 *
 * The document is read eagerly -- the text of every page is extracted when the
 * file is opened -- so that a caller sees one consistent picture of the file:
 * the page count, the text and the list of features that could not be honoured
 * all describe the same read.
 */
class PdfDocument internal constructor(
    /** Where the document came from: a path, or a label for an in-memory file. */
    val origin: String,
    /** The pages of the document, in reading order. */
    val pages: List<PdfPage>,
    /** The entries of the document's `/Info` dictionary, keyed by their plain names. */
    val info: Map<String, String>,
    /** The features of the file that could not be honoured. */
    val issues: List<PdfIssue>,
) {

    /** The number of pages the document has. */
    val pageCount: Int get() = pages.size

    /** True when at least one page carries text. */
    val hasText: Boolean get() = pages.any { it.text.isNotBlank() }

    companion object {

        /**
         * Parse the PDF held in [bytes].
         *
         * @param origin the label used in an error message when the bytes are not a PDF.
         * @throws SourceLoadError when the bytes are not a readable PDF.
         */
        fun parse(bytes: ByteArray, origin: String = "<bytes>"): PdfDocument = readDocument(bytes, origin)

        /**
         * Parse the PDF at [path].
         *
         * @throws SourceLoadError when the file is missing, unreadable or not a PDF.
         */
        fun open(path: Path): PdfDocument {
            if (!Files.isRegularFile(path)) {
                throw pdfError(
                    path.toString(),
                    "file does not exist or is not a regular file: $path",
                )
            }
            val bytes = try {
                Files.readAllBytes(path)
            } catch (e: IOException) {
                throw pdfError(path.toString(), "file could not be read: ${e.message}")
            }
            return readDocument(bytes, path.toString())
        }
    }
}

/**
 * One page of a PDF.
 *
 * @property number the one-based position of the page in the document.
 * @property dictionary the page's own dictionary.
 * @property resources the resources the page draws with, including its fonts.
 * @property text the text the page carries.
 */
class PdfPage internal constructor(
    val number: Int,
    val dictionary: PdfDictionary,
    val resources: PdfDictionary?,
    val text: String,
)

/** The typed error every failure to read a PDF is reported as. */
internal fun pdfError(origin: String, reason: String): SourceLoadError =
    SourceLoadError(kind = "pdf", origin = origin, reason = reason)

/** The `/Info` entries that are copied into the extracted metadata, in the order they are read. */
private val INFO_KEYS = listOf(
    "Title",
    "Author",
    "Subject",
    "Keywords",
    "Creator",
    "Producer",
    "CreationDate",
)

/** How deep the page tree is followed before a file is treated as malformed. */
private const val MAX_PAGE_TREE_DEPTH = 64

/** How far into a file the `%PDF-` header is looked for. */
private const val MAX_HEADER_SEARCH = 1024

private fun readDocument(bytes: ByteArray, origin: String): PdfDocument = try {
    readDocumentBytes(bytes, origin)
} catch (exc: PdfStreamTooLargeError) {
    // A stream that expands beyond the cap is a refused document, not a
    // document with a missing page: the cap is the tier's rule about how much
    // a file may make the tier allocate.
    throw pdfError(origin, exc.reason)
}

private fun readDocumentBytes(bytes: ByteArray, origin: String): PdfDocument {
    if (bytes.isEmpty()) throw pdfError(origin, "the file is empty")
    if (findPdfHeader(bytes) < 0) {
        throw pdfError(origin, "the file does not start with %PDF-; it is not a PDF")
    }
    val issues = ArrayList<PdfIssue>()
    val xref = PdfXrefParser.parse(bytes)
    val resolver = PdfObjectResolver(bytes, xref?.offsets ?: emptyMap())
    val trailer = xref?.trailer
    if (trailer == null) {
        issues.add(
            PdfIssue(
                PdfIssueKind.BROKEN_CROSS_REFERENCE,
                "the cross-reference table could not be read; the file was scanned for its objects instead",
            ),
        )
    }
    // The trailer names the encryption dictionary. When the trailer itself
    // could not be read, the marker is looked for in the file, because a
    // document whose objects are encrypted must be refused rather than read
    // as an empty one.
    val encrypted = trailer?.get("Encrypt") != null ||
        (trailer == null && indexOfToken(bytes, "/Encrypt", 0) >= 0)
    if (encrypted) {
        throw pdfError(origin, "the PDF is encrypted; encrypted PDFs are not supported")
    }
    if (xref?.usesObjectStreams == true || indexOfToken(bytes, "/ObjStm", 0) >= 0) {
        issues.add(
            PdfIssue(
                PdfIssueKind.OBJECT_STREAMS,
                "the PDF stores objects in compressed object streams; the objects they hold are not read",
            ),
        )
    }

    val catalog = resolver.dictionary(trailer?.get("Root")) ?: resolver.findByType("Catalog")
        ?: throw pdfError(origin, "the PDF has no document catalog")
    val pages = readPages(catalog, resolver, origin, issues)
    val info = readInfo(trailer, resolver, issues)
    if (pages.isEmpty()) {
        issues.add(
            PdfIssue(PdfIssueKind.NO_TEXT, "the PDF has no pages"),
        )
    } else if (pages.all { it.text.isBlank() }) {
        issues.add(
            PdfIssue(
                PdfIssueKind.NO_TEXT,
                "the PDF carries no text; it is most likely a scanned image",
            ),
        )
    }
    return PdfDocument(origin, pages, info, issues)
}

private fun readPages(
    catalog: PdfDictionary,
    resolver: PdfObjectResolver,
    origin: String,
    issues: MutableList<PdfIssue>,
): List<PdfPage> {
    val root = resolver.dictionary(catalog.get("Pages"))
        ?: throw pdfError(origin, "the PDF catalog has no page tree")
    val pages = ArrayList<PdfPage>()
    walkPages(root, null, 0, pages, resolver, issues)
    return pages
}

private fun walkPages(
    node: PdfDictionary,
    inheritedResources: PdfDictionary?,
    depth: Int,
    pages: MutableList<PdfPage>,
    resolver: PdfObjectResolver,
    issues: MutableList<PdfIssue>,
) {
    if (depth > MAX_PAGE_TREE_DEPTH) return
    val resources = node.dictionary("Resources") ?: inheritedResources
    val kids = node.array("Kids")?.items ?: emptyList()
    if (kids.isEmpty()) {
        pages.add(buildPage(node, resources, pages.size + 1, resolver, issues))
        return
    }
    for (kid in kids) {
        val child = resolver.dictionary(kid) ?: continue
        val isPage = child.name("Type") == "Page" || (child.name("Type") != "Pages" && child["Kids"] == null)
        if (isPage) {
            val pageResources = child.dictionary("Resources") ?: resources
            pages.add(buildPage(child, pageResources, pages.size + 1, resolver, issues))
        } else {
            walkPages(child, resources, depth + 1, pages, resolver, issues)
        }
    }
}

private fun buildPage(
    dictionary: PdfDictionary,
    resources: PdfDictionary?,
    number: Int,
    resolver: PdfObjectResolver,
    issues: MutableList<PdfIssue>,
): PdfPage {
    val text = PageText.extract(resolver, dictionary, resources, issues)
    return PdfPage(number, dictionary, resources, text)
}

private fun readInfo(
    trailer: PdfDictionary?,
    resolver: PdfObjectResolver,
    issues: MutableList<PdfIssue>,
): Map<String, String> {
    // The information dictionary is named by the trailer, so a file whose
    // trailer could not be read has no way to name it. It is found by the keys
    // it holds instead -- the same fallback the catalog has -- so a file whose
    // offsets no longer resolve still carries its metadata.
    val info = resolver.dictionary(trailer?.get("Info"))
        ?: resolver.findByKeys(INFO_KEYS)
    if (info == null) {
        if (trailer == null) {
            issues.add(
                PdfIssue(
                    PdfIssueKind.BROKEN_CROSS_REFERENCE,
                    "the information dictionary could not be found; the document carries no metadata",
                ),
            )
        }
        return emptyMap()
    }
    val result = LinkedHashMap<String, String>()
    for (key in INFO_KEYS) {
        val value = info[key] as? PdfString ?: continue
        val text = value.text.trim()
        if (text.isNotEmpty()) result[key] = text
    }
    return result
}

/**
 * The offset of the `%PDF-` header, or `-1` when the file does not carry one.
 *
 * A producer that writes something in front of the header is tolerated: the
 * cross-reference offsets are counted from the start of the file either way.
 */
private fun findPdfHeader(bytes: ByteArray): Int {
    val header = "%PDF-"
    // A file shorter than the header cannot carry one, and the bound is
    // checked before the loop: reading past the end of a short file is an
    // `ArrayIndexOutOfBoundsException`, not a refused document.
    val last = minOf(bytes.size - header.length, MAX_HEADER_SEARCH)
    if (last < 0) return -1
    for (offset in 0..last) {
        var matched = true
        for (index in header.indices) {
            if (bytes[offset + index].toInt() != header[index].code) {
                matched = false
                break
            }
        }
        if (matched) return offset
    }
    return -1
}
