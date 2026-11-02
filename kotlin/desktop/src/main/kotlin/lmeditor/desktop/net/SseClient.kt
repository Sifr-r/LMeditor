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
 * One thing the stream carried.
 *
 * The stream carries two kinds of thing: the run's events, and the server's
 * word about the stream itself. They are told apart here so that a caller is
 * never left to guess which it holds. The control frames exist to tell the
 * caller that frames were missed, and a caller that cannot see them cannot
 * tell a complete run from a short one.
 */
sealed interface SseMessage {
    /** A workflow event, exactly as the backend published it. */
    data class Event(val event: WorkflowEvent) : SseMessage

    /**
     * The frames a resume asked for are not all available.
     *
     * `requestedAfter` is the frame the client named and `oldestRetained` the
     * oldest frame the server still holds. When the frames between them have
     * left the server's memory they are gone, and the client will never be
     * given them. The same two numbers also describe a client that resumed
     * *ahead* of the session, and there they mean the opposite thing:
     * nothing is missing and the stream is waiting for a frame the session
     * has not published yet. The caller tells the two apart by comparing
     * them: frames are gone when `oldestRetained` is more than one beyond
     * `requestedAfter`.
     */
    data class Gap(val requestedAfter: Int, val oldestRetained: Int) : SseMessage

    /**
     * The subscriber fell behind and the server ended the stream.
     *
     * `behind` is the number of frames the server published that this
     * subscriber was never given, and `oldestRetained` is the oldest frame
     * the server still holds. The client reopens the stream with the id of
     * the last frame it received, so the frames it missed are replayed
     * rather than lost.
     */
    data class Overflow(val behind: Int, val oldestRetained: Int) : SseMessage
}

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
     * The messages of one run, in the order the server published them.
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
     *
     * The two control frames are handed to the caller as messages of their
     * own, carrying what they say. They are the server's word about the
     * stream, and a caller that could not see them could not tell a run that
     * lost frames from one that did not.
     */
    fun events(docId: String): Flow<SseMessage> = channelFlow {
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
                                        // replayed rather than lost. The caller is told
                                        // how many frames it was never given: a run that
                                        // lost frames and one that did not must not look
                                        // the same to it.
                                        send(decodeOverflow(frame.data))
                                        overflowed = true
                                        break
                                    }
                                    "gap" -> {
                                        // The server could not give this client the frames it
                                        // asked for, and named the hole. The hole cannot be
                                        // filled from the stream: the document's state is
                                        // the authoritative record, and the stream carries
                                        // on with what comes next. The caller is told which
                                        // frames are gone, so that it can say the run is
                                        // short rather than show a stream that looks whole.
                                        send(decodeGap(frame.data))
                                    }
                                    else -> {
                                        frame.id?.let { lastEventId = it }
                                        send(SseMessage.Event(decode(frame.data)))
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

    /** Decode an overflow frame's payload into the frames it was never given. */
    private fun decodeOverflow(payload: String): SseMessage.Overflow {
        val map = controlPayload(payload, "overflow")
        return SseMessage.Overflow(
            behind = requiredCount(map, "behind", "overflow"),
            oldestRetained = requiredCount(map, "oldest_retained", "overflow"),
        )
    }

    /** Decode a gap frame's payload into the ids that describe the hole. */
    private fun decodeGap(payload: String): SseMessage.Gap {
        val map = controlPayload(payload, "gap")
        return SseMessage.Gap(
            requestedAfter = requiredCount(map, "requested_after", "gap"),
            oldestRetained = requiredCount(map, "oldest_retained", "gap"),
        )
    }

    /**
     * The payload of a control frame, read as an object.
     *
     * A control frame the client cannot read is a genuine disagreement
     * between the two halves of the system, and it is reported rather than
     * skipped: a silently dropped control frame is a run that looks complete
     * and is not.
     */
    private fun controlPayload(payload: String, what: String): Map<String, Any?> {
        try {
            return Json.parseObject(payload)
        } catch (cause: ValidationError) {
            throw BackendError.Malformed("$what frame", cause)
        }
    }

    /**
     * One count from a control frame's payload.
     *
     * The count must be a whole number the client can hold: a field that is
     * missing, or that holds something else, means the client and the server
     * disagree about what the frame says, and that is reported rather than
     * guessed at.
     */
    private fun requiredCount(map: Map<String, Any?>, name: String, what: String): Int {
        val value = map[name]
        if (value !is Long || value < Int.MIN_VALUE || value > Int.MAX_VALUE) {
            throw BackendError.Malformed(
                "$what frame",
                ValidationError("the '$name' field is not a whole number", field = name),
            )
        }
        return value.toInt()
    }
}
