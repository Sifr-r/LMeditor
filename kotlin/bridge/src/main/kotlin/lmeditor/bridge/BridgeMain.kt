package lmeditor.bridge

import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.io.PrintWriter
import java.nio.charset.StandardCharsets

/**
 * The entry point of the Kotlin tier's NDJSON seam.
 *
 * The process reads one request per line from standard input and writes one
 * response per line to standard output. Standard error carries diagnostics
 * only: a diagnostic written to standard output would corrupt the stream.
 *
 * The loop ends when standard input closes, which is how the Python tier
 * says goodbye.
 */
object BridgeMain {

    @JvmStatic
    fun main(args: Array<String>) {
        val reader = BufferedReader(InputStreamReader(System.`in`, StandardCharsets.UTF_8))
        // The writer is UTF-8 whatever the console's encoding is: the pipe
        // carries the wire vocabulary, and a platform-encoded response would
        // mangle every non-ASCII character in a document.
        val writer = PrintWriter(OutputStreamWriter(System.out, StandardCharsets.UTF_8), true)
        val bridge = Bridge()

        while (true) {
            val line = reader.readLine() ?: break
            if (line.isBlank()) continue
            writer.println(bridge.handle(line))
            writer.flush()
        }
    }
}
