package lmeditor.desktop.net

import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import lmeditor.core.ValidationError
import lmeditor.core.WorkflowEvent
import lmeditor.core.json.Json
import lmeditor.core.workflowEventFromWire

/**
 * One frame of the stream, as the server wrote it.
 *
 * The server writes three kinds of frame: a sequence frame, which carries an
 * `id:` and a `data:` envelope of a workflow event; and two control frames,
 * `overflow` and `gap`, which carry neither an id nor an event envelope.
 * They are told apart by the `event:` name, which is why it is read.
 */
private data class SseFrame(val event: String?, val id: String?, val data: String)

/**
 * The reader for the backend's Server-Sent Event stream.
 *
 * The stream is the run's voice: every sequence frame is a `WorkflowEvent`,
 * decoded by the tier's own decoder, so the client and the server read the
 * same frame with the same rules. The two control frames -- `overflow` and
 * `gap` -- report on the stream rather than carrying an event, and are told
 * apart by their `event:` name; they are never decoded as events.
 *
 * The stream ends when the server closes it, which it does after the
 * terminal frame. That is not a failure: a finished run is a finished stream.
 *
 * The stream is a route under `/api/`, so it carries the credential the API
 * requires, exactly as the REST requests do.
 */
class SseClient(
    private val http: HttpClient,
    baseUrl: String,
    private val credential: ApiCredential = ApiCredential.resolve(),
) {
    private val baseUrl = backendUrl(baseUrl)

    /**
     * The frames of one run, in the order the server published them.
     *
     * The flow throws [BackendError] when the stream cannot be read at all.
     * A frame the client cannot decode is a genuine disagreement between the
     * two halves of the system, and it is reported rather than skipped: a
     * silently dropped frame is a run that looks complete and is not.
     *
     * The server disconnects a subscriber that has fallen behind, and says so
     * in an `overflow` frame before it closes. That is not a failure: the run
     * is still going, so the client reopens the stream with the id of the last
     * frame it received and carries on. It does that once -- a retry loop needs
     * a backoff and a budget, which is a different decision -- so a second
     * overflow ends the stream, and the caller sees a run that stopped short.
     */
    fun events(docId: String): Flow<WorkflowEvent> = channelFlow {
        var lastEventId: String? = null
        var reconnected = false
        // The body of the response being read now. A blocked read is not
        // interrupted by cancellation, so cancellation reaches it by closing
        // the body; the holder is how the closing thread finds it.
        val open = AtomicReference<InputStream?>(null)
        val worker = launch(Dispatchers.IO) {
            try {
                while (true) {
                    val request = buildRequest(docId, lastEventId)
                    var overflowed = false
                    try {
                        val response = http.sendCancellable(request, HttpResponse.BodyHandlers.ofInputStream())
                        val body = response.body()
                        open.set(body)
                        // A cancellation that lands between the response
                        // completing and the body being published would
                        // otherwise leave the body open with nobody to close
                        // it, and the blocked read below would never end.
                        // The worker stops here rather than reading a closed
                        // stream and reporting the result as an unreachable
                        // backend, which is not what happened.
                        if (!isActive) {
                            body.close()
                            return@launch
                        }
                        body.use { stream ->
                            if (response.statusCode() != 200) {
                                throw BackendError.Refused(response.statusCode(), "the event stream was refused")
                            }
                            val reader = BufferedReader(InputStreamReader(stream, StandardCharsets.UTF_8))
                            while (true) {
                                val frame = readFrame(reader) ?: break
                                when (frame.event) {
                                    "overflow" -> {
                                        // The run is still going and this subscriber fell
                                        // behind. The stream is reopened with the last
                                        // frame received, so the frames it missed are
                                        // replayed rather than lost.
                                        overflowed = true
                                        break
                                    }
                                    "gap" -> {
                                        // The server could not give this client the frames it
                                        // asked for, and named the hole. The hole cannot be
                                        // filled from the stream: the document's state is
                                        // the authoritative record, and the stream carries
                                        // on with what comes next.
                                    }
                                    else -> {
                                        frame.id?.let { lastEventId = it }
                                        send(decode(frame.data))
                                    }
                                }
                            }
                        }
                    } catch (cause: CancellationException) {
                        throw cause
                    } catch (cause: IOException) {
                        throw BackendError.Unreachable(request.uri().toString(), cause)
                    } finally {
                        open.set(null)
                    }
                    // One reconnect is what this slice covers. A second overflow
                    // ends the stream: the frames between are gone, and a
                    // retry loop needs a backoff and a budget, which is a
                    // different decision.
                    if (!overflowed || reconnected) {
                        break
                    }
                    reconnected = true
                }
                close()
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: BackendError) {
                close(cause)
            }
        }
        awaitClose {
            worker.cancel()
            open.get()?.close()
        }
    }

    /** Read one frame's lines into a frame, or null at the end of the stream. */
    private fun readFrame(reader: BufferedReader): SseFrame? {
        var event: String? = null
        var id: String? = null
        val data = mutableListOf<String>()
        while (true) {
            val line = reader.readLine() ?: return null
            when {
                line.startsWith("event:") -> event = line.substring(6).trim()
                line.startsWith("id:") -> id = line.substring(3).trim()
                line.startsWith("data:") -> data.add(line.substring(5).removePrefix(" "))
                line.isEmpty() -> {
                    if (data.isNotEmpty()) {
                        return SseFrame(event, id, data.joinToString("\n"))
                    }
                    // A block with no data is a comment, and the SSE
                    // specification dispatches nothing for it. It is read
                    // past rather than turned into an empty frame the caller
                    // would fail to decode.
                    event = null
                    id = null
                }
            }
        }
    }

    /** The request for one stream, resuming after the last frame received. */
    private fun buildRequest(docId: String, lastEventId: String?): HttpRequest {
        val builder = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/api/v1/stream/${pathSegment(docId)}"))
            .header("Authorization", credential.authorization())
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
        if (lastEventId != null) {
            builder.header("Last-Event-ID", lastEventId)
        }
        return builder.GET().build()
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
