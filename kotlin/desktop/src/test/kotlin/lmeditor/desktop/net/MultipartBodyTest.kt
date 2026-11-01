package lmeditor.desktop.net

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The rules the multipart body follows. */
class MultipartBodyTest {

    @Test
    fun `the body carries every field`() {
        val body = MultipartBody.build(
            fields = mapOf("source_id" to "src_1", "title" to "A Paper"),
        ).toString(Charsets.UTF_8)

        assertTrue(body.contains("""name="source_id""""))
        assertTrue(body.contains("src_1"))
        assertTrue(body.contains("""name="title""""))
        assertTrue(body.contains("A Paper"))
    }

    @Test
    fun `the body ends with the closing boundary`() {
        val body = MultipartBody.build(fields = mapOf("a" to "b")).toString(Charsets.UTF_8)
        assertTrue(body.endsWith("--${MultipartBody.BOUNDARY}--\r\n"))
    }

    @Test
    fun `a file part carries its name and its bytes`() {
        val body = MultipartBody.build(
            fields = mapOf("source_id" to "src_1"),
            fileName = "report.txt",
            fileContentType = "text/plain",
            fileContent = "hello".toByteArray(Charsets.UTF_8),
        )

        val text = String(body, Charsets.UTF_8)
        assertTrue(text.contains("""filename="report.txt""""))
        assertTrue(text.contains("Content-Type: text/plain"))
        assertTrue(text.contains("hello"))
    }

    @Test
    fun `a file name cannot break out of its header`() {
        val body = MultipartBody.build(
            fields = mapOf("a" to "b"),
            fileName = "evil\" \r\nX-Injected: yes",
        ).toString(Charsets.UTF_8)

        assertTrue(!body.contains("X-Injected: yes\r\n"))
        assertTrue(body.contains("""filename="evil_ __X-Injected: yes""""))
    }

    @Test
    fun `the same body is built twice`() {
        val first = MultipartBody.build(fields = mapOf("a" to "b"), fileName = "x.txt")
        val second = MultipartBody.build(fields = mapOf("a" to "b"), fileName = "x.txt")
        assertEquals(first.toList(), second.toList())
    }
}
