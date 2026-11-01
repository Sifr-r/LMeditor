package lmeditor.pdf

/**
 * A PDF feature this parser cannot honour.
 *
 * The tier's rule is that an unsupported feature is *reported*, never
 * silently dropped: a caller that receives an empty text must be able to tell
 * an empty document from a document this parser could not read.
 */
enum class PdfIssueKind(val wire: String) {
    /** The PDF is encrypted; its objects are not readable without decryption. */
    ENCRYPTED("encrypted"),

    /** The PDF uses object streams, whose compressed objects are not read. */
    OBJECT_STREAMS("object_streams"),

    /** A stream uses a filter this parser does not implement. */
    UNSUPPORTED_FILTER("unsupported_filter"),

    /** A font cannot be decoded, so the text it draws is not recovered. */
    UNDECODABLE_FONT("undecodable_font"),

    /** The PDF carries no text at all; it is most likely a scanned image. */
    NO_TEXT("no_text"),

    /** The cross-reference table was missing or unusable and the file was scanned instead. */
    BROKEN_CROSS_REFERENCE("broken_cross_reference"),
}

/**
 * One detected limitation, with the reason the caller needs to see.
 *
 * @property kind the class of the limitation.
 * @property reason a human-readable statement of what could not be honoured.
 */
data class PdfIssue(val kind: PdfIssueKind, val reason: String)
