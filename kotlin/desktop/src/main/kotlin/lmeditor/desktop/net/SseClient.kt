package lmeditor.desktop.net

import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.launch
import lmeditor.core.ValidationError
import lmeditor.core.WorkflowEvent
import lmeditor.core.json.Json
import lmeditor.core.workflowEventFromWire

/**
 * The reader for the backend's Server-Sent Event stream.
 *
 * The stream is the run's voice: every frame is a `WorkflowEvent`, decoded by
 * the tier's own decoder, so the client and the server read the same frame
 * with the same rules.
 *
 * The stream ends when the server closes it, which it does after the
 * terminal frame. That is not a failure: a finished run is a finished stream.
 */
class SseClient(
    private val http: HttpClient,
    baseUrl: String,
) {
    private val baseUrl = backendUrl(baseUrl)

    /**
     * The frames of one run, in the order the server published them.
     *
     * The flow throws [BackendError] when the stream cannot be read at all.
     * A frame the client cannot decode is a genuine disagreement between the
     * two halves of the system, and it is reported rather than skipped: a
     * silently dropped frame is a run that looks complete and is not.
     */
    fun events(docId: String): Flow<WorkflowEvent> = channelFlow {
        val request = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/api/v1/stream/${pathSegment(docId)}"))
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .GET()
            .build()
        val response = http.sendCancellable(request, HttpResponse.BodyHandlers.ofInputStream())
        val body = response.body()
        val worker = launch(Dispatchers.IO) {
            try {
                body.use {
                    if (response.statusCode() != 200) {
                        throw BackendError.Refused(response.statusCode(), "the event stream was refused")
                    }
                    val reader = BufferedReader(InputStreamReader(it, StandardCharsets.UTF_8))
                    val data = mutableListOf<String>()
                    while (true) {
                        val line = reader.readLine() ?: break
                        when {
                            line.startsWith("data:") -> data.add(line.substring(5).removePrefix(" "))
                            line.isEmpty() && data.isNotEmpty() -> {
                                send(decode(data.joinToString("\n")))
                                data.clear()
                            }
                        }
                    }
                }
                close()
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: IOException) {
                close(BackendError.Unreachable(request.uri().toString(), cause))
            } catch (cause: BackendError) {
                close(cause)
            }
        }
        awaitClose {
            worker.cancel()
            body.close()
        }
    }

    /** Decode one frame's payload into the event it carries. */
    private fun decode(payload: String): WorkflowEvent {
        val map = try {
            Json.parseObject(payload)
        } catch (cause: ValidationError) {
            throw BackendError.Malformed("event", cause)
        }
        return try {
            workflowEventFromWire(map)
        } catch (cause: ValidationError) {
            throw BackendError.Malformed("event", cause)
        }
    }
}
