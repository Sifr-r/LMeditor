package lmeditor.desktop.model

import lmeditor.core.Claim
import lmeditor.core.OutlineNodeInput
import lmeditor.core.Perspective
import lmeditor.core.SectionDraft
import lmeditor.core.SourceChunk
import lmeditor.core.VerificationScorecard

/**
 * The records this client reads from the HTTP surface that the tier's own
 * models do not already cover.
 *
 * They are grouped in one file because they are one thing: the shape of the
 * HTTP surface as this client reads it. Everything the tier *does* own -- a
 * chunk, a claim, an outline node, a scorecard, a workflow event -- is read
 * with the tier's own decoder instead, so the two halves of the system
 * cannot disagree about what a payload means.
 */

/** What the backend reports about itself. */
data class HealthSummary(
    val status: String,
    val version: String,
    val lmConfigured: Boolean,
    val lmStub: Boolean,
) {
    /**
     * The backend can write.
     *
     * The offline stub counts: it drafts, it cites, and it verifies. It is not
     * a language model, and the studio says so separately, but a backend that
     * holds the stub is not a backend that cannot write.
     */
    val canWrite: Boolean get() = lmConfigured
}

/** One source in the server's source library. */
data class SourceSummary(
    val sourceId: String,
    val title: String,
    val kind: String,
    val origin: String,
    val chunkCount: Int,
    val byteSize: Long,
    val addedAt: String,
    val omissions: List<String>,
)

/** What an ingestion produced. */
data class IngestResult(
    val sourceId: String,
    val title: String,
    val chunks: List<SourceChunk>,
    val omissions: List<String>,
)

/** The backend's answer to a request to write a document. */
data class GenerateAck(
    val docId: String,
    val status: String,
    val streamUrl: String,
)

/** The state of a run, as the backend reports it. */
data class WorkflowStatus(
    val docId: String,
    val topic: String,
    val status: String,
    val currentStep: String,
    val hitlPending: Boolean,
    val isCompleted: Boolean,
    val error: String?,
    val rejectionReason: String?,
)

/** The finished document: its sections and the scorecard measured on it. */
data class DocumentPayload(
    val docId: String,
    val topic: String,
    val status: String,
    val isCompleted: Boolean,
    val hitlPending: Boolean,
    val error: String?,
    val sections: List<SectionDraft>,
    val scorecard: VerificationScorecard?,
)

/** The review a run is waiting for. */
data class PendingReview(
    val reason: String,
    val requiresHitl: Boolean,
    val maxSectionRetries: Int,
)

/** The run's own numbers. */
data class RunMetrics(
    val tokenCount: Int,
    val latencyMs: Int,
    val groundingScore: Double,
)

/** Everything the studio knows about one run. */
data class RunSnapshot(
    val docId: String,
    val topic: String,
    val status: String,
    val perspectives: List<Perspective>,
    val outline: List<lmeditor.core.OutlineNode>,
    val sections: List<SectionDraft>,
    val claims: List<Claim>,
    val chunks: List<SourceChunk>,
    val scorecard: VerificationScorecard?,
    val pendingReview: PendingReview?,
)

/**
 * The operator's own outline: the sections the next draft is written to.
 *
 * It is a separate type from the published outline node, and that separation
 * is the point: a section id or a chunk assignment cannot be posted by
 * accident, because the type has nowhere to put one.
 */
typealias OperatorOutline = List<OutlineNodeInput>
