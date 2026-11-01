package lmeditor.desktop.net

import java.io.InputStream
import java.io.IOException
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.util.concurrent.CompletionException
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import lmeditor.core.ValidationError

/** Validate a backend address before it becomes connection state. */
internal fun backendUrl(value: String): String {
    val normalized = value.trim().trimEnd('/')
    val uri = try { URI(normalized) } catch (cause: java.net.URISyntaxException) {
        throw ValidationError("The backend URL is invalid.", field = "backend_url", value = value)
    }
    if (uri.scheme !in listOf("http", "https") || uri.host.isNullOrBlank() ||
        uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null ||
        uri.port !in -1..65535 || uri.port == 0
    ) throw ValidationError("The backend URL must be an HTTP(S) address without credentials, query or fragment.", field = "backend_url", value = value)
    return normalized
}

/** Encode an identifier as exactly one path segment, never as URL syntax. */
internal fun pathSegment(value: String): String {
    if (value.isBlank() || value == "." || value == ".." || value.any { it.isISOControl() }) {
        throw ValidationError(
            "A resource identifier must be nonblank and contain no control characters.",
            field = "identifier",
            value = value,
        )
    }
    return URLEncoder.encode(value, Charsets.UTF_8).replace("+", "%20")
}

/** Await JDK HTTP without blocking a coroutine; cancellation owns late stream bodies. */
internal suspend fun <T> HttpClient.sendCancellable(
    request: HttpRequest,
    handler: HttpResponse.BodyHandler<T>,
): HttpResponse<T> = suspendCancellableCoroutine { continuation ->
    val future = try {
        sendAsync(request, handler)
    } catch (cause: IOException) {
        continuation.resumeWithException(BackendError.Unreachable(request.uri().toString(), cause))
        return@suspendCancellableCoroutine
    }
    continuation.invokeOnCancellation { future.cancel(true) }
    future.whenComplete { response, failure ->
        if (failure != null) {
            val cause = if (failure is CompletionException) failure.cause ?: failure else failure
            continuation.resumeWithException(
                if (cause is IOException) BackendError.Unreachable(request.uri().toString(), cause) else cause,
            )
        } else {
            continuation.resume(response, onCancellation = { _, value, _ ->
                (value.body() as? InputStream)?.close()
            })
        }
    }
}
