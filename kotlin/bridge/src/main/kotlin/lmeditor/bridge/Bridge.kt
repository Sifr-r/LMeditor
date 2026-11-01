package lmeditor.bridge

import lmeditor.core.LMeditorError
import lmeditor.core.json.Json
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonString

/**
 * The NDJSON seam, server half.
 *
 * One JSON document per line, in and out. A request names a method and its
 * parameters; a response carries either a result or a typed error.
 *
 * The rules that cross this seam are the Kotlin tier's, and the Python tier
 * calls them rather than repeating them. The rules the seam does *not* serve
 * are named rather than implied: ARCHITECTURE.md §18.2.1 is the catalogue of
 * the drift-pinned Python twins, the two rules with no Kotlin counterpart, and
 * the model-facing half that is Python by design.
 *
 * Two rules make the seam honest:
 *
 * - **A failure never terminates the bridge.** A malformed request, an
 *   unknown method and a refused operation are all reported as a failed
 *   response, and the service keeps serving. Only a broken pipe ends it.
 * - **The error carries its type.** The simple name of the Kotlin exception,
 *   its message and its `details` map, so the Python side can rebuild the
 *   exception its own callers already catch.
 */
class Bridge {

    private val handlers: Map<String, (Map<String, Any?>) -> Map<String, Any?>> = Handlers.all()

    /**
     * Handle one request line and return the response line.
     *
     * This never throws: every failure becomes a failed response.
     */
    fun handle(line: String): String {
        val request = try {
            Json.parseObject(line)
        } catch (exc: LMeditorError) {
            return failure("", "BridgeError", exc.message, emptyMap())
        } catch (exc: Exception) {
            return failure("", "BridgeError", exc.message ?: exc.toString(), emptyMap())
        }

        val id = request.jsonString("id")
        val method = request.jsonString("method")
        val params = request.jsonObject("params")

        val handler = handlers[method]
            ?: return failure(
                id,
                "BridgeError",
                "unknown method '$method'",
                mapOf("method" to method),
            )

        return try {
            val result = handler(params)
            success(id, result)
        } catch (exc: LMeditorError) {
            failure(
                id,
                exc::class.simpleName ?: "LMeditorError",
                exc.message,
                exc.details,
            )
        } catch (exc: Exception) {
            failure(
                id,
                "BridgeError",
                exc.message ?: exc.toString(),
                mapOf("exception" to (exc::class.simpleName ?: "Exception")),
            )
        }
    }

    private fun success(id: String, result: Map<String, Any?>): String =
        Json.write(
            linkedMapOf<String, Any?>(
                "id" to id,
                "ok" to true,
                "result" to result,
            ),
        )

    private fun failure(
        id: String,
        type: String,
        message: String,
        details: Map<String, Any?>,
    ): String = Json.write(
        linkedMapOf<String, Any?>(
            "id" to id,
            "ok" to false,
            "error" to linkedMapOf<String, Any?>(
                "type" to type,
                "message" to message,
                "details" to details,
            ),
        ),
    )
}
