package lmeditor.bridge

import lmeditor.core.LMeditorError
import lmeditor.core.WordParseError
import lmeditor.core.WordRenderError
import lmeditor.core.bibliographicRecordFromWire
import lmeditor.core.documentDraftFromWire
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.word.WordManuscript
import lmeditor.word.WordReader
import lmeditor.word.WordWriter
import lmeditor.word.wordDocumentFromWire
import lmeditor.word.wordDocumentToWire
import lmeditor.word.wordPartRelationshipToWire
import java.util.Base64

/**
 * The Word methods of the seam.
 *
 * The document model does not have to be modelled twice: it is modelled once,
 * in the Kotlin tier, and travels as the wire form `kotlin/word/WIRE.md`
 * pins. The Python tier validates what arrives here with its own Pydantic
 * model, so a key that is wrong on this side shows up as a validation error
 * on the other side -- which is the point. There is one vocabulary and it is
 * checked from both ends.
 *
 * Each method raises exactly one kind of failure, because the Python tier
 * rebuilds the exception from the error's `details`:
 *
 * - `word.read` raises [WordParseError], whose details are the `origin` and
 *   the `reason`.
 * - `word.write` raises [WordRenderError], whose details are the `reason`.
 *
 * `word.manuscript` answers with the document *and* the package it renders
 * to, because the two callers want different halves of the same answer: the
 * Python door rebuilds the document model from the wire form, and the export
 * route renders the model with `word.write`.
 */
internal fun wordHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    "word.read" to { params ->
        // The origin label names the source in the error messages. The Python
        // door reads the file itself and sends the path as the origin, so the
        // tier never touches the filesystem on Python's behalf and the message
        // says what the operator knows.
        val origin = params.jsonString("origin", DEFAULT_ORIGIN)
        val encoded = params["bytes_b64"]
        if (encoded == null) {
            throw WordParseError(origin, "the request carries no 'bytes_b64' payload")
        }
        if (encoded !is String) {
            throw WordParseError(origin, "the 'bytes_b64' payload must be a base64 string")
        }
        val bytes = try {
            Base64.getDecoder().decode(encoded)
        } catch (_: IllegalArgumentException) {
            throw WordParseError(origin, "the 'bytes_b64' payload is not valid base64")
        }
        val baseUri = when (val value = params["base_uri"]) {
            null -> null
            is String -> value
            else -> throw WordParseError(origin, "the 'base_uri' parameter must be a string")
        }
        val reader = WordReader()
        val document = try {
            reader.readBytes(bytes, baseUri, origin)
        } catch (exc: WordParseError) {
            throw exc
        } catch (exc: LMeditorError) {
            throw WordParseError(origin, exc.message)
        }
        mapOf(
            "document" to wordDocumentToWire(document),
            "omissions" to reader.lastOmissions,
            "package_relationships" to reader.lastPackageRelationships.map {
                wordPartRelationshipToWire(it)
            },
        )
    },

    "word.write" to { params ->
        // The document arrives in its wire form and is rebuilt into the model,
        // which is where the model's own invariants are enforced: a document
        // the model refuses is a document the tier will not render.
        val document = try {
            wordDocumentFromWire(params.jsonObject("document"))
        } catch (exc: LMeditorError) {
            throw WordRenderError("the document could not be read from the wire: ${exc.message}")
        }
        val writer = WordWriter(deterministic = params.jsonBool("deterministic", true))
        val bytes = try {
            writer.write(document)
        } catch (exc: WordRenderError) {
            throw exc
        } catch (exc: LMeditorError) {
            throw WordRenderError("the document could not be rendered: ${exc.message}")
        }
        // The omission register crosses the seam with the package: a write that
        // lost something says so, and the caller is the one who can act on it.
        mapOf(
            "bytes_b64" to Base64.getEncoder().encodeToString(bytes),
            "omissions" to writer.lastOmissions,
        )
    },

    // The manuscript method is served here, not in the base map of
    // `Handlers.kt`: the base map is composed first and this one second, and
    // a map union keeps the right-hand value, so this entry is the one that
    // answers. It shadows the base map's entry of the same name rather than
    // living beside it, because there is only one answer to give.
    "word.manuscript" to { params ->
        // A manuscript is built from a draft and its bibliography, both of
        // which are core schemas, so both cross the seam in their wire form
        // (`kotlin/core/WireDecode.kt` reads them). The document model
        // crosses too: it is the same wire form `word.read` answers with and
        // `word.write` is given, so the Python door rebuilds it with the one
        // model both tiers share.
        val draft = documentDraftFromWire(params.jsonObject("draft"))
        val bibliography = params.jsonObjectList("bibliography")
            .map { bibliographicRecordFromWire(it) }
        val document = WordManuscript.buildManuscriptDocument(
            document = draft,
            bibliography = bibliography,
            verifiedOnly = params.jsonBool("verified_only", false),
        )
        val bytes = WordWriter(deterministic = true).write(document)
        mapOf(
            "bytes_b64" to Base64.getEncoder().encodeToString(bytes),
            "document" to wordDocumentToWire(document),
        )
    },
)

/** The origin label a byte source is known by when the request names none. */
private const val DEFAULT_ORIGIN: String = "<bytes>"
