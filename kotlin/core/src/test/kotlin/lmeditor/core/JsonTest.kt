package lmeditor.core

import lmeditor.core.json.Json
import lmeditor.core.json.jsonArray
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonString
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/**
 * The JSON codec is hand-written, so its behaviour is pinned here rather than
 * left to the reader of the code.
 */
class JsonTest {

    @Test
    fun `an object round-trips through the codec`() {
        val original = mapOf(
            "name" to "LMeditor",
            "count" to 3L,
            "ratio" to 0.5,
            "ready" to true,
            "missing" to null,
            "items" to listOf(1L, 2L, 3L),
            "nested" to mapOf("key" to "value"),
        )
        val parsed = Json.parse(Json.write(original))
        assertEquals(original["name"], (parsed as Map<*, *>)["name"])
        assertEquals(3L, (parsed as Map<*, *>)["count"])
        assertEquals(0.5, (parsed as Map<*, *>)["ratio"])
        assertEquals(true, (parsed as Map<*, *>)["ready"])
        assertEquals(null, (parsed as Map<*, *>)["missing"])
        assertEquals(listOf(1L, 2L, 3L), (parsed as Map<*, *>)["items"])
    }

    @Test
    fun `an integral number stays integral`() {
        val parsed = Json.parse("{\"n\": 42}")
        assertEquals(42L, (parsed as Map<*, *>)["n"])
    }

    @Test
    fun `an enum is written as its wire string and not as its Kotlin name`() {
        val written = Json.write(mapOf("status" to EntailmentStatus.ENTAILED))
        assertEquals("{\"status\":\"entailed\"}", written)
    }

    @Test
    fun `a string is escaped and unescaped faithfully`() {
        val original = "quote \" backslash \\ newline \n tab \t unicode é"
        val parsed = Json.parse(Json.write(mapOf("v" to original)))
        assertEquals(original, (parsed as Map<*, *>)["v"])
    }

    @Test
    fun `a control character is written as an escape`() {
        assertEquals("{\"v\":\"\\u0001\"}", Json.write(mapOf("v" to "\u0001")))
    }

    @Test
    fun `a malformed document is refused with a typed error`() {
        assertFailsWith<ValidationError> { Json.parse("{not json}") }
        assertFailsWith<ValidationError> { Json.parse("") }
        assertFailsWith<ValidationError> { Json.parse("{} trailing") }
    }

    @Test
    fun `an object builder keeps insertion order and skips absent values`() {
        val built = jsonObject {
            put("first", "1")
            putIfPresent("second", null)
            put("third", 3L)
        }
        assertEquals(listOf("first", "third"), built.keys.toList())
        assertEquals("""{"first":"1","third":3}""", Json.write(built))
    }

    @Test
    fun `an array builder produces a list`() {
        assertEquals("[1,2,3]", Json.write(jsonArray { add(1L); add(2L); add(3L) }))
    }

    @Test
    fun `a non-finite number is written as null rather than as invalid JSON`() {
        assertEquals("{\"v\":null}", Json.write(mapOf("v" to Double.NaN)))
    }

    @Test
    fun `the typed accessors read the shapes the seam produces`() {
        val parsed = Json.parseObject(
            """{"s":"text","i":7,"d":1.5,"b":true,"o":{"k":1},"a":[1,2]}""",
        )
        assertEquals("text", parsed.jsonString("s"))
        assertEquals(7, parsed.jsonInt("i"))
        assertEquals(1.5, parsed.jsonDouble("d"))
        assertTrue(parsed.jsonBool("b"))
        assertEquals(1, parsed.jsonObject("o").jsonInt("k"))
        assertEquals(2, parsed.jsonArray("a").size)
        assertEquals("fallback", parsed.jsonString("missing", "fallback"))
    }
}
