package lmeditor.desktop.model

import lmeditor.core.Claim
import lmeditor.core.CitationLineage
import lmeditor.core.OutlineNode
import lmeditor.core.OutlineNodeInput
import lmeditor.core.Perspective
import lmeditor.core.SectionDraft
import lmeditor.core.SourceChunk
import lmeditor.core.VerificationScorecard

/**
 * Everything the studio window shows.
 *
 * The state is immutable, and every transition produces a new value. That is
 * what makes a run reproducible in the window: a frame that arrives twice
 * leaves the same state behind, and a frame that arrives out of order cannot
 * half-apply.
 */
data class StudioState(
    /** The backend this window talks to. */
    val backendUrl: String = DEFAULT_BACKEND_URL,

    /** What the backend last said about itself. */
    val health: HealthSummary? = null,

    /** The sources the server holds. */
    val sources: List<SourceSummary> = emptyList(),

    /** The source the next run is written from. */
    val selectedSourceId: String? = null,

    /** What the next document is about. */
    val topic: String = "",

    /** The operator's own outline: the sections the next draft is written to. */
    val operatorOutline: List<OutlineNodeInput> = emptyList(),

    /** The document the window is following. */
    val docId: String? = null,

    /** The run's state, as the backend reports it. */
    val status: String = "",

    /** The stage the run is in right now. */
    val currentStep: String = "",

    /** The perspectives the discovery stage derived. */
    val perspectives: List<Perspective> = emptyList(),

    /** The findings the guided research stage recorded. */
    val researchNotes: List<String> = emptyList(),

    /** The chunks the guided research stage leaned on. */
    val referencedChunkIds: List<String> = emptyList(),

    /** The outline the run wrote itself. */
    val outline: List<OutlineNode> = emptyList(),

    /** The sections as they stream in, keyed by section id. */
    val liveText: Map<String, String> = emptyMap(),

    /** The finished sections of the document. */
    val sections: List<SectionDraft> = emptyList(),

    /** Every claim the verifier has measured. */
    val claims: List<Claim> = emptyList(),

    /** Every citation the run bound to a passage. */
    val lineages: List<CitationLineage> = emptyList(),

    /** The passages the run read from. */
    val chunks: List<SourceChunk> = emptyList(),

    /** The document-level scorecard. */
    val scorecard: VerificationScorecard? = null,

    /** The review the run is waiting for, when it is waiting for one. */
    val pendingReview: PendingReview? = null,

    /** The run's own numbers. */
    val metrics: RunMetrics? = null,

    /**
     * The word-processing workspace.
     *
     * The documents workspace is a job of its own, separate from the run: it
     * holds the books the server's library keeps, the book the operator has
     * open and the section being edited. It is a value of its own so a screen
     * cannot show half of a document change.
     */
    val documents: DocumentWorkspace = DocumentWorkspace(),

    /**
     * The vault workspace.
     *
     * The vault workspace is a job of its own, separate from the run: it
     * holds the folder of markdown files the server has open, the notes it
     * holds, the note being edited and the search over them. It is a value
     * of its own so a screen cannot show half of a vault change.
     */
    val vault: VaultWorkspace = VaultWorkspace(),

    /**
     * The notebook workspace.
     *
     * The notebook workspace is a job of its own, separate from the run: it
     * holds the folder of notebooks the server has open, the notebooks in it,
     * the notebook being worked on and the answer the notebook gave to the
     * operator's question. It is a value of its own so a screen cannot show
     * half of a notebook change.
     */
    val notebooks: NotebookWorkspace = NotebookWorkspace(),

    /** The claim the operator is inspecting. */
    val selectedClaimId: String? = null,

    /** The passage the operator is inspecting. */
    val selectedChunkId: String? = null,

    /** The last failure worth showing the operator. */
    val error: String? = null,

    /** The last thing worth telling the operator. */
    val notice: String? = null,

    /** Confirmed permanent stream loss, retained until the server acknowledges a new run. */
    val streamLossNotice: String? = null,

    /** True while a request is in flight. */
    val busy: Boolean = false,
) {
    /** Notices remain visible together, including when the state also carries an error. */
    val statusNotices: List<String>
        get() = listOfNotNull(streamLossNotice, notice).distinct()

    companion object {
        /** The backend the client reaches when nothing else is configured. */
        const val FALLBACK_BACKEND_URL: String = "http://127.0.0.1:8000"

        /**
         * The backend the client reaches when nothing else is configured.
         *
         * `LMEDITOR_BACKEND_URL` points the client at a backend that is not
         * on the default port, which is what a second instance does.
         */
        val DEFAULT_BACKEND_URL: String =
            System.getenv("LMEDITOR_BACKEND_URL")
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?.trimEnd('/')
                ?: FALLBACK_BACKEND_URL
    }
}
