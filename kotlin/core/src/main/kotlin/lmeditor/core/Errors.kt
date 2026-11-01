package lmeditor.core

/**
 * Deterministic domain exceptions.
 *
 * The hierarchy mirrors the Python tier one-for-one so that a failure raised
 * on either side of the NDJSON seam carries the same meaning, the same
 * `details` payload and the same message text.
 *
 * Every error is typed and contextual: there are no bare `IllegalStateException`
 * throws and no swallowed failures. `details` is the machine-readable half of
 * the error, and it is what crosses the seam.
 */

open class LMeditorError(
    private val baseMessage: String = "",
    val details: Map<String, Any?> = emptyMap(),
) : RuntimeException(baseMessage) {

    override val message: String
        get() = if (details.isEmpty()) baseMessage else "$baseMessage | Details: $details"
}

// --- Provenance ------------------------------------------------------------

open class ProvenanceError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class ChunkNotFoundError(
    val chunkId: String,
    message: String? = null,
) : ProvenanceError(
    message ?: "ChunkNotFoundError: chunk_id='$chunkId' was not found in index.",
    mapOf("chunk_id" to chunkId),
)

class SpanAlignmentError(
    val spanStart: Int,
    val spanEnd: Int,
    val docLength: Int? = null,
    message: String? = null,
) : ProvenanceError(
    message ?: (
        "SpanAlignmentError: span [$spanStart, $spanEnd) out of bounds" +
            (if (docLength != null) " for doc_length=$docLength." else ".")
        ),
    mapOf("span_start" to spanStart, "span_end" to spanEnd, "doc_length" to docLength),
)

// --- Verification ----------------------------------------------------------

open class VerificationError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class CitationHallucinationError(
    rawChunkId: String = "",
    val quote: String? = null,
    val reason: String? = null,
    val sectionId: String? = null,
    message: String? = null,
    rawHallucinatedIds: List<String>? = null,
) : VerificationError(
    message ?: buildMessage(rawChunkId, rawHallucinatedIds, quote, reason, sectionId),
    buildDetails(rawChunkId, rawHallucinatedIds, quote, reason, sectionId),
) {
    /** The chunk the citation points at, resolved to a concrete value. */
    val chunkId: String = rawChunkId.ifEmpty { rawHallucinatedIds?.firstOrNull() ?: "unknown" }

    /** Every chunk id this error is about; always at least one. */
    val hallucinatedIds: List<String> =
        rawHallucinatedIds?.ifEmpty { listOf(chunkId) } ?: listOf(chunkId)

    private companion object {
        fun resolveChunkId(rawChunkId: String, rawHallucinatedIds: List<String>?): String =
            rawChunkId.ifEmpty { rawHallucinatedIds?.firstOrNull() ?: "unknown" }

        fun buildMessage(
            rawChunkId: String,
            rawHallucinatedIds: List<String>?,
            quote: String?,
            reason: String?,
            sectionId: String?,
        ): String {
            val cid = resolveChunkId(rawChunkId, rawHallucinatedIds)
            return (
                "CitationHallucinationError: chunk_id='$cid' in section='${sectionId ?: "unknown"}'. " +
                    "Quote: '${quote ?: ""}'. Reason: '${reason ?: "ungrounded"}'."
                )
        }

        fun buildDetails(
            rawChunkId: String,
            rawHallucinatedIds: List<String>?,
            quote: String?,
            reason: String?,
            sectionId: String?,
        ): Map<String, Any?> = buildMap {
            put("chunk_id", resolveChunkId(rawChunkId, rawHallucinatedIds))
            put("section_id", sectionId)
            put("quote", quote)
            put("reason", reason)
            if (!rawHallucinatedIds.isNullOrEmpty()) put("hallucinated_ids", rawHallucinatedIds)
        }
    }
}

class UngroundedClaimError(
    val claimId: String,
    val claimText: String = "",
    rawScore: Double = 0.0,
    rawEntailmentScore: Double? = null,
    val threshold: Double? = null,
    message: String? = null,
) : VerificationError(
    message ?: buildMessage(claimId, claimText, rawScore, rawEntailmentScore, threshold),
    buildDetails(claimId, claimText, rawScore, rawEntailmentScore, threshold),
) {
    /** The final score: the entailment score when one was given, otherwise the raw score. */
    val score: Double = rawEntailmentScore ?: rawScore

    /** The score the entailment measurement produced. */
    val entailmentScore: Double = rawEntailmentScore ?: rawScore

    private companion object {
        fun resolveScore(rawScore: Double, rawEntailmentScore: Double?): Double =
            rawEntailmentScore ?: rawScore

        fun buildMessage(
            claimId: String,
            claimText: String,
            rawScore: Double,
            rawEntailmentScore: Double?,
            threshold: Double?,
        ): String {
            val finalScore = resolveScore(rawScore, rawEntailmentScore)
            val threshStr = threshold?.let { " threshold=${it.format4()}" } ?: ""
            return (
                "UngroundedClaimError for claim_id='$claimId': score=${finalScore.format4()}$threshStr. " +
                    "Claim: '$claimText'"
                )
        }

        fun buildDetails(
            claimId: String,
            claimText: String,
            rawScore: Double,
            rawEntailmentScore: Double?,
            threshold: Double?,
        ): Map<String, Any?> = mapOf(
            "claim_id" to claimId,
            "claim_text" to claimText,
            "score" to resolveScore(rawScore, rawEntailmentScore),
            "threshold" to threshold,
        )
    }
}

class NLIModelError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : VerificationError(message, details)

class ClaimExtractionError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : VerificationError(message, details)

// --- Workflow --------------------------------------------------------------

open class WorkflowError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class WorkflowDeadlockError(
    val currentNode: String = "",
    val stateSummary: String = "",
    val docId: String = "",
    val retryCount: Int = 0,
    val maxRetries: Int = 0,
    message: String? = null,
) : WorkflowError(
    message ?: (
        "WorkflowDeadlockError in current_node='$currentNode': $stateSummary. " +
            "doc_id='$docId' retries=$retryCount/$maxRetries"
        ),
    mapOf(
        "current_node" to currentNode,
        "state_summary" to stateSummary,
        "doc_id" to docId,
        "retry_count" to retryCount,
        "max_retries" to maxRetries,
    ),
)

class InvalidStateTransitionError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : WorkflowError(message, details)

// --- Generic ---------------------------------------------------------------

class ValidationError(
    message: String,
    val field: String? = null,
    val value: Any? = null,
) : LMeditorError(
    message,
    buildMap {
        if (field != null) put("field", field)
        if (value != null) put("value", value.toString())
    },
)

class PerspectiveGenerationError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class DialogueError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class OutlineGenerationError(
    message: String = "",
    val nodeId: String? = null,
    val cycleDetected: Boolean = false,
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(
    message,
    buildMap {
        putAll(details)
        if (nodeId != null) put("node_id", nodeId)
        if (cycleDetected) put("cycle_detected", true)
    },
)

class PassageBoundsError(
    message: String = "",
    val sectionId: String? = null,
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(
    message,
    buildMap {
        putAll(details)
        if (sectionId != null) put("section_id", sectionId)
    },
)

class SourceLoadError(
    val kind: String,
    val origin: String,
    val reason: String,
) : LMeditorError(
    "Failed to load $kind source '$origin': $reason",
    mapOf("kind" to kind, "origin" to origin, "reason" to reason),
)

// --- Word processing -------------------------------------------------------

open class WordProcessingError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class WordParseError(
    val origin: String,
    val reason: String,
) : WordProcessingError(
    "Failed to parse Word document '$origin': $reason",
    mapOf("origin" to origin, "reason" to reason),
)

class WordRenderError(
    val reason: String,
) : WordProcessingError(
    "Failed to render Word document: $reason",
    mapOf("reason" to reason),
)

// --- Ingestion, drafting, export -------------------------------------------

class BibliographyResolutionError(
    val resolver: String,
    val origin: String,
    val reason: String,
) : LMeditorError("Resolver '$resolver' failed for '$origin': $reason")

class LMNotConfiguredError(
    val operation: String = "",
    message: String? = null,
) : LMeditorError(
    message ?: (
        "No language model is configured, so '${operation.ifEmpty { "generation" }}' cannot run. " +
            "Set LMEDITOR_LM_PROVIDER, LMEDITOR_LM_MODEL and LMEDITOR_LM_API_KEY " +
            "(or LMEDITOR_LM_BASE_URL for a local OpenAI-compatible server) and restart the app."
        ),
    mapOf("operation" to operation),
)

class DraftingError(
    val sectionId: String = "",
    val reason: String = "",
    message: String? = null,
) : LMeditorError(
    message ?: "Drafting failed for section '$sectionId': $reason",
    mapOf("section_id" to sectionId, "reason" to reason),
)

class ExportError(
    val docId: String = "",
    val reason: String = "",
    message: String? = null,
) : LMeditorError(
    message ?: "Export failed for document '$docId': $reason",
    mapOf("doc_id" to docId, "reason" to reason),
)

class SourceUploadTooLargeError(
    message: String,
    val limitBytes: Int = 0,
) : LMeditorError(message, mapOf("limit_bytes" to limitBytes))

// --- Editing ---------------------------------------------------------------

open class EditingError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class InlineEditRejectedError(
    val sectionId: String = "",
    val reason: String = "",
    message: String? = null,
) : EditingError(
    message ?: "Inline edit rejected for section '$sectionId': $reason",
    mapOf("section_id" to sectionId, "reason" to reason),
)

class EditTargetNotFoundError(
    val sectionId: String = "",
    val paragraphIndex: Int? = null,
    message: String? = null,
) : EditingError(
    message ?: "Edit target not found: section='$sectionId' paragraph=$paragraphIndex",
    mapOf("section_id" to sectionId, "paragraph_index" to paragraphIndex),
)

// --- Fact checking ---------------------------------------------------------

open class FactCheckError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class WebEvidenceError(
    val reason: String = "",
    val query: String = "",
    message: String? = null,
) : FactCheckError(
    message ?: "Web evidence unavailable for query '$query': $reason",
    mapOf("reason" to reason, "query" to query),
)

// --- Library ---------------------------------------------------------------

open class LibraryError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

class BookNotFoundError(
    val bookId: String = "",
    message: String? = null,
) : LibraryError(
    message ?: "BookNotFoundError: book_id='$bookId' was not found.",
    mapOf("book_id" to bookId),
)

class SectionNotFoundError(
    val bookId: String = "",
    val sectionId: String = "",
    message: String? = null,
) : LibraryError(
    message ?: "SectionNotFoundError: section_id='$sectionId' was not found in book '$bookId'.",
    mapOf("book_id" to bookId, "section_id" to sectionId),
)

// --- Session and human-in-the-loop ----------------------------------------

/** Raised when a decision arrives for a review that is not awaiting one. */
class HITLReviewNotPendingError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

/** Raised when a review is released without a recorded decision payload. */
class HITLDecisionMissingError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

/**
 * Raised when a live (non-terminal) session already owns a document id.
 *
 * Creating a second session for the same document would orphan the first
 * one's background worker, which keeps running and publishing into an object
 * nothing can reach any more. Replacing a session that already reached a
 * terminal outcome stays allowed.
 */
class ActiveSessionExistsError(
    val docId: String,
    val status: String,
) : LMeditorError(
    "document '$docId' already has a live workflow session (status='$status')",
    mapOf("doc_id" to docId, "status" to status),
)

/** Formats a double the way Python's `:.4f` does, so messages match. */
internal fun Double.format4(): String = String.format(java.util.Locale.ROOT, "%.4f", this)
