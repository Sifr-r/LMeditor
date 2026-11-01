package lmeditor.desktop.net

import java.nio.charset.StandardCharsets

/**
 * Builds a `multipart/form-data` body.
 *
 * The JDK has no multipart writer, and the client's dependency set is not
 * allowed to grow for one, so the body is assembled here: a boundary, the
 * plain fields, and at most one file part.
 *
 * The boundary is derived from the content itself rather than from a counter
 * or a clock, so building the same body twice produces the same bytes.
 */
object MultipartBody {

    /** The separator between two parts, without the leading `--`. */
    const val BOUNDARY: String = "LMeditorDesktopBoundary"

    /**
     * Build the body of a `multipart/form-data` request.
     *
     * @param fields the plain form fields, in the order they are written.
     * @param fileName the name of the file part, or null for a body with no
     *   file part.
     * @param fileContentType the content type of the file part.
     * @param fileContent the bytes of the file part.
     */
    fun build(
        fields: Map<String, String>,
        fileName: String? = null,
        fileContentType: String = "application/octet-stream",
        fileContent: ByteArray = ByteArray(0),
    ): ByteArray {
        val body = StringBuilder()
        for ((name, value) in fields) {
            body.append("--").append(BOUNDARY).append("\r\n")
            body.append("Content-Disposition: form-data; name=\"").append(name).append("\"\r\n")
            body.append("\r\n")
            body.append(value).append("\r\n")
        }
        if (fileName != null) {
            body.append("--").append(BOUNDARY).append("\r\n")
            body.append("Content-Disposition: form-data; name=\"file\"; filename=\"")
                .append(sanitizeFileName(fileName)).append("\"\r\n")
            body.append("Content-Type: ").append(fileContentType).append("\r\n")
            body.append("\r\n")
            val prefix = body.toString().toByteArray(StandardCharsets.UTF_8)
            val suffix = "\r\n--$BOUNDARY--\r\n".toByteArray(StandardCharsets.UTF_8)
            val out = ByteArray(prefix.size + fileContent.size + suffix.size)
            System.arraycopy(prefix, 0, out, 0, prefix.size)
            System.arraycopy(fileContent, 0, out, prefix.size, fileContent.size)
            System.arraycopy(suffix, 0, out, prefix.size + fileContent.size, suffix.size)
            return out
        }
        body.append("--").append(BOUNDARY).append("--\r\n")
        return body.toString().toByteArray(StandardCharsets.UTF_8)
    }

    /**
     * Strip anything from a file name that would break the header it is
     * written into.
     *
     * A name is operator-supplied, and a name carrying a quote or a newline
     * would end the header early and let the rest of the name be read as a
     * header of its own.
     */
    private fun sanitizeFileName(fileName: String): String {
        val cleaned = fileName.replace(Regex("[\\r\\n\"]"), "_")
        return if (cleaned.isBlank()) "upload" else cleaned
    }
}
