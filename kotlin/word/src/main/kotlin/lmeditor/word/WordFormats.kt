package lmeditor.word

/**
 * Format detection for the Word subsystem.
 *
 * The reader only speaks OOXML (`.docx`). A legacy Word 97-2003 document (`.doc`)
 * is a *different* format entirely -- an OLE2 compound-file binary -- and feeding
 * one to the OOXML reader produces a confusing "not a valid zip container" error
 * that tells the operator nothing about what to do next.
 *
 * This object recognises the legacy format by its magic bytes and states, in one
 * place, what the operator has to do about it. The reader and the HTTP route both
 * use it, so the two can never disagree about what a file is.
 */
object WordFormats {

    /**
     * The OLE2 / Compound File Binary magic number every legacy `.doc` starts with
     * (`D0 CF 11 E0 A1 B1 1A E1`).
     */
    val LEGACY_DOC_MAGIC: ByteArray = byteArrayOf(
        0xD0.toByte(), 0xCF.toByte(), 0x11.toByte(), 0xE0.toByte(),
        0xA1.toByte(), 0xB1.toByte(), 0x1A.toByte(), 0xE1.toByte(),
    )

    /** The ZIP local-file-header magic number every OOXML package starts with. */
    val ZIP_MAGIC: ByteArray = byteArrayOf(0x50, 0x4B, 0x03, 0x04)

    /** The reason a legacy document is refused, in the words the operator needs. */
    const val LEGACY_DOC_REASON: String = "legacy .doc (OLE2) is not supported; convert to .docx"

    /** The reason a file that is neither format at all is refused. */
    const val UNKNOWN_FORMAT_REASON: String =
        "the file is neither an OOXML (.docx) package nor a legacy .doc document"

    /**
     * Report whether [data] is a legacy binary Word document.
     *
     * The check is on the magic number only: an OLE2 compound file is what a `.doc`
     * document is, whatever its extension claims.
     */
    fun isLegacyDoc(data: ByteArray): Boolean = data.startsWith(LEGACY_DOC_MAGIC)

    /** Report whether [data] starts like an OOXML (zip) package. */
    fun isZipPackage(data: ByteArray): Boolean = data.startsWith(ZIP_MAGIC)

    /**
     * Return the reason a file cannot be parsed as OOXML.
     *
     * @param data the first bytes of the file
     */
    fun describeUnsupportedFormat(data: ByteArray): String {
        if (isLegacyDoc(data)) return LEGACY_DOC_REASON
        if (isZipPackage(data)) return "the file is not a valid .docx package (bad zip container)"
        return UNKNOWN_FORMAT_REASON
    }

    private fun ByteArray.startsWith(prefix: ByteArray): Boolean {
        if (size < prefix.size) return false
        for (index in prefix.indices) {
            if (this[index] != prefix[index]) return false
        }
        return true
    }
}
