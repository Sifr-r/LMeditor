package lmeditor.pdf

import java.io.ByteArrayOutputStream
import java.util.zip.Deflater

/**
 * Builds the PDF files the tests read.
 *
 * The files are built here rather than checked in as opaque bytes so that
 * every byte a test depends on is visible in the test itself: the content
 * stream, the information dictionary and the cross-reference table are all
 * written out in front of the reader.
 */
internal object PdfFixtures {

    /** The `/Info` dictionary the fixtures carry unless a test says otherwise. */
    const val DEFAULT_INFO: String =
        "/Producer (lmeditor test fixture) /Title (Municipal Waste Audit) " +
            "/Author (LMeditor) /Subject (Collection rates) " +
            "/Keywords (waste, composting, municipal)"

    /** The content of a page that draws two lines of text. */
    const val TWO_LINES: String =
        "BT\n/F1 12 Tf\n72 720 Td\n14 TL\n" +
            "(Municipal waste collection rates fell by 30 percent between 2019 and 2023.) Tj\nT*\n" +
            "(The decline was driven by a citywide composting programme introduced in 2020.) Tj\nT*\nET\n"

    /** One indirect object of a PDF under construction. */
    class Obj(val dictionary: String, val stream: ByteArray? = null)

    /**
     * Build a PDF whose objects are the given ones, numbered from one.
     *
     * The cross-reference table is written out correctly, so the file is a
     * well-formed PDF and not a bundle of bytes that happens to start with a
     * header.
     */
    fun build(
        objects: List<Obj>,
        root: Int = 3,
        info: Int? = 1,
        extraTrailer: String = "",
    ): ByteArray {
        val out = ByteArrayOutputStream()
        out.write("%PDF-1.4\n".toByteArray(Charsets.ISO_8859_1))
        val offsets = IntArray(objects.size + 1)
        for (index in objects.indices) {
            val number = index + 1
            offsets[number] = out.size()
            val entry = objects[index]
            out.write("$number 0 obj\n".toByteArray(Charsets.ISO_8859_1))
            if (entry.stream == null) {
                out.write(entry.dictionary.toByteArray(Charsets.ISO_8859_1))
            } else {
                out.write(
                    "<< ${entry.dictionary} /Length ${entry.stream.size} >>\n"
                        .toByteArray(Charsets.ISO_8859_1),
                )
                out.write("stream\n".toByteArray(Charsets.ISO_8859_1))
                out.write(entry.stream)
                out.write("\nendstream\n".toByteArray(Charsets.ISO_8859_1))
            }
            out.write("endobj\n".toByteArray(Charsets.ISO_8859_1))
        }
        val xrefOffset = out.size()
        val count = objects.size + 1
        out.write("xref\n0 $count\n".toByteArray(Charsets.ISO_8859_1))
        out.write("0000000000 65535 f \n".toByteArray(Charsets.ISO_8859_1))
        for (number in 1..objects.size) {
            out.write("%010d 00000 n \n".format(offsets[number]).toByteArray(Charsets.ISO_8859_1))
        }
        val trailer = buildString {
            append("<< /Size $count /Root $root 0 R")
            if (info != null) append(" /Info $info 0 R")
            append(extraTrailer)
            append(" >>")
        }
        out.write("trailer\n$trailer\n".toByteArray(Charsets.ISO_8859_1))
        out.write("startxref\n$xrefOffset\n%%EOF\n".toByteArray(Charsets.ISO_8859_1))
        return out.toByteArray()
    }

    /** A one-page PDF whose page draws [content]. */
    fun onePage(
        content: String = TWO_LINES,
        info: String = DEFAULT_INFO,
        compress: Boolean = false,
        extraTrailer: String = "",
    ): ByteArray {
        val data = content.toByteArray(Charsets.ISO_8859_1)
        val stream: ByteArray
        val streamDictionary: String
        if (compress) {
            stream = deflate(data)
            streamDictionary = "/Filter /FlateDecode"
        } else {
            stream = data
            streamDictionary = ""
        }
        return build(
            listOf(
                Obj("<< $info >>"),
                Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
                Obj("<< /Type /Catalog /Pages 2 0 R >>"),
                Obj(pageDictionary(5)),
                Obj(streamDictionary, stream),
            ),
            extraTrailer = extraTrailer,
        )
    }

    /** A two-page PDF whose pages draw [first] and [second]. */
    fun twoPages(first: String, second: String): ByteArray = build(
        listOf(
            Obj("<< $DEFAULT_INFO >>"),
            Obj("<< /Type /Pages /Count 2 /Kids [ 4 0 R 5 0 R ] >>"),
            Obj("<< /Type /Catalog /Pages 2 0 R >>"),
            Obj(pageDictionary(6)),
            Obj(pageDictionary(7)),
            Obj("", first.toByteArray(Charsets.ISO_8859_1)),
            Obj("", second.toByteArray(Charsets.ISO_8859_1)),
        ),
    )

    /** A PDF whose trailer says the document is encrypted. */
    fun encrypted(): ByteArray = build(
        listOf(
            Obj("<< $DEFAULT_INFO >>"),
            Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
            Obj("<< /Type /Catalog /Pages 2 0 R >>"),
            Obj(pageDictionary(5)),
            Obj("", TWO_LINES.toByteArray(Charsets.ISO_8859_1)),
            Obj("<< /Filter /Standard /V 1 /R 2 /O <00> /U <00> /P -1 >>"),
        ),
        extraTrailer = " /Encrypt 6 0 R",
    )

    /** A PDF that keeps some of its objects in a compressed object stream. */
    fun withObjectStream(): ByteArray = build(
        listOf(
            Obj("<< $DEFAULT_INFO >>"),
            Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
            Obj("<< /Type /Catalog /Pages 2 0 R >>"),
            Obj(pageDictionary(5)),
            Obj("", TWO_LINES.toByteArray(Charsets.ISO_8859_1)),
            Obj("<< /Type /ObjStm /N 2 /First 4 /Length 20 >>", ByteArray(20)),
        ),
    )

    /** A PDF whose only font is a subsetted font that carries a `/ToUnicode` CMap. */
    fun withToUnicodeFont(): ByteArray {
        val cmap = """
            /CIDInit /ProcSet findresource begin
            12 dict begin
            begincmap
            /CMapName /Custom def
            /CMapType 2 def
            1 begincodespacerange
            <0000> <FFFF>
            endcodespacerange
            2 beginbfchar
            <0001> <0048>
            <0002> <0069>
            endbfchar
            endcmap
            end
            end
        """.trimIndent()
        return build(
            listOf(
                Obj("<< $DEFAULT_INFO >>"),
                Obj("<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>"),
                Obj("<< /Type /Catalog /Pages 2 0 R >>"),
                Obj(
                    "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] /Resources " +
                        "<< /Font << /F1 << /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Custom " +
                        "/Encoding /Identity-H /DescendantFonts [ 6 0 R ] /ToUnicode 7 0 R >> >> >> " +
                        "/Contents 5 0 R >>",
                ),
                Obj(
                    "",
                    "BT\n/F1 12 Tf\n72 720 Td\n14 TL\n<00010002> Tj\nT*\nET\n"
                        .toByteArray(Charsets.ISO_8859_1),
                ),
                Obj("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+Custom /DW 1000 >>"),
                Obj("", cmap.toByteArray(Charsets.ISO_8859_1)),
            ),
        )
    }

    private fun pageDictionary(contents: Int): String =
        "<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] " +
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 " +
            "/BaseFont /Helvetica >> >> >> /Contents $contents 0 R >>"

    /** Compress [data] the way a PDF producer compresses a content stream. */
    fun deflate(data: ByteArray): ByteArray {
        val deflater = Deflater()
        deflater.setInput(data)
        deflater.finish()
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (!deflater.finished()) {
            val read = deflater.deflate(buffer)
            out.write(buffer, 0, read)
        }
        deflater.end()
        return out.toByteArray()
    }
}
