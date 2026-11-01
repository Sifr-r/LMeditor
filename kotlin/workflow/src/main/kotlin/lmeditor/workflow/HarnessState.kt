package lmeditor.workflow

import lmeditor.core.NliEvaluator
import lmeditor.core.OutlineNode
import lmeditor.core.OutlineNodeInput
import lmeditor.core.Perspective
import lmeditor.core.ResearchMemory
import lmeditor.core.SectionDraft
import lmeditor.core.Source
import lmeditor.core.SourceChunk
import lmeditor.core.VerificationReport

/**
 * The central workflow state passed through the cyclic writing harness.
 *
 * Port of `backend/src/lmeditor/workflow/state.py`. The Python original is a
 * `total=False` TypedDict, so every key is optional and a checkpoint written
 * before a key existed simply has no such key. Two consequences shape this
 * port:
 *
 *  * the reducers ([mergeDicts], [appendLists]) are pure functions over maps
 *    and lists rather than LangGraph `Annotated` channels, and
 *  * the two caller-supplied verification options ([maxRetries] and
 *    [thresholdFBeta]) are nullable, where `null` means "the key is absent".
 *    The router reads them through the same `.get(key, <default>)` semantics
 *    the Python tier uses, so a legacy checkpoint still routes on the
 *    defaults.
 *
 * The optional [nliEvaluator] override is typed as the core
 * [NliEvaluator] protocol. The Python original declares it as `Any`; the
 * protocol is the same seam with the type made honest.
 */
data class HarnessState(
    // -- Document metadata ---------------------------------------------------
    val docId: String = "",
    val topic: String = "",
    val targetAudience: String = DEFAULT_TARGET_AUDIENCE,
    val guidelines: List<String> = emptyList(),

    // -- Layer 0: user-supplied Source artifacts -----------------------------
    val sources: List<Source> = emptyList(),

    // -- Layer 1: ingested and indexed source chunks -------------------------
    val rawTexts: List<String> = emptyList(),
    val sourceChunks: Map<String, SourceChunk> = emptyMap(),

    // -- Layer 2: STORM discovery and outlining ------------------------------
    val perspectives: List<Perspective> = emptyList(),
    val outlineRoot: OutlineNode? = null,
    val outlineNodes: List<OutlineNode> = emptyList(),

    // -- Guided research ------------------------------------------------------
    // The corpus questioned by the discovered perspectives before the outline is
    // written. [researchMemory] is the consolidated result of that inquiry and
    // [researchNotes] says what it found (or why it did not run).
    // [guidedResearch] is the toggle a run uses to opt out of the stage; the
    // default is on.
    val guidedResearch: Boolean = true,
    val researchMemory: ResearchMemory? = null,
    val researchNotes: List<String> = emptyList(),

    // -- The outline the caller supplied -------------------------------------
    // The operator's steer: the sections they named are the sections the
    // manuscript is written to. It carries no chunk assignments, because the
    // corpus still decides what may be said about each section.
    val requestedOutline: List<OutlineNodeInput>? = null,

    // -- Layer 3: drafting ----------------------------------------------------
    val sectionDrafts: Map<String, SectionDraft> = emptyMap(),
    val currentSectionId: String? = null,

    // -- Layer 4: verification and citation fidelity --------------------------
    val verificationReports: Map<String, VerificationReport> = emptyMap(),
    val retryCounts: Map<String, Int> = emptyMap(),
    val critiques: Map<String, String> = emptyMap(),

    // -- Layer 5: macro cohesion and polish -----------------------------------
    val fullManuscript: String = "",
    val status: String = "",
    val errors: List<String> = emptyList(),

    // -- Human-in-the-loop review gates ---------------------------------------
    val requiresHitl: Boolean = false,
    val hitlReason: String? = null,
    val hitlApproved: Boolean = false,

    // -- Caller-supplied verification options ---------------------------------
    // `null` means the key is absent, exactly as it is absent from a
    // checkpoint serialised before the options existed.
    val maxRetries: Int? = null,
    val thresholdFBeta: Double? = null,

    // -- Optional NLI evaluator override ---------------------------------------
    // Production never sets it (the default cross-encoder is used); a test
    // suite sets it so a verification run does not need the model weights.
    val nliEvaluator: NliEvaluator? = null,
)

/** The audience a run targets unless the caller says otherwise. */
const val DEFAULT_TARGET_AUDIENCE: String = "Technical & Academic Readers"

/**
 * The default retry budget seeded into a fresh state.
 *
 * A literal duplicate of `workflow.edges.MAX_SECTION_RETRIES`, written out
 * rather than imported for the same reason the Python tier writes it out: the
 * state module is a leaf of the package graph and must not depend on the
 * router. `RoutingTest` asserts the two constants are numerically identical,
 * so the duplication cannot drift.
 */
private const val DEFAULT_MAX_RETRIES: Int = 3

/**
 * The default groundedness threshold seeded into a fresh state.
 *
 * A literal duplicate of `verification.citation_checker.DEFAULT_F_BETA_THRESHOLD`
 * for the same reason as [DEFAULT_MAX_RETRIES]: importing the verification
 * stack would drag the NLI dependencies into every consumer of the state.
 */
private const val DEFAULT_THRESHOLD_F_BETA: Double = 0.85

/**
 * Initialise a clean, deterministic [HarnessState].
 *
 * @param docId the document identifier.
 * @param topic the manuscript subject.
 * @param targetAudience the audience the manuscript is written for.
 * @param rawTexts the passages the caller supplied.
 * @param sources the caller-supplied source artifacts (Layer 0).
 * @param guidelines the caller's writing guidelines.
 * @param maxRetries the per-section revision budget.
 * @param thresholdFBeta the groundedness threshold the verification step uses.
 */
fun createInitialHarnessState(
    docId: String,
    topic: String,
    targetAudience: String = DEFAULT_TARGET_AUDIENCE,
    rawTexts: List<String>? = null,
    sources: List<Source>? = null,
    guidelines: List<String>? = null,
    maxRetries: Int = DEFAULT_MAX_RETRIES,
    thresholdFBeta: Double = DEFAULT_THRESHOLD_F_BETA,
): HarnessState = HarnessState(
    docId = docId,
    topic = topic,
    targetAudience = targetAudience,
    guidelines = guidelines ?: emptyList(),
    rawTexts = rawTexts ?: emptyList(),
    sources = sources ?: emptyList(),
    sourceChunks = emptyMap(),
    perspectives = emptyList(),
    outlineRoot = null,
    outlineNodes = emptyList(),
    guidedResearch = true,
    researchMemory = null,
    researchNotes = emptyList(),
    sectionDrafts = emptyMap(),
    currentSectionId = null,
    verificationReports = emptyMap(),
    retryCounts = emptyMap(),
    critiques = emptyMap(),
    fullManuscript = "",
    status = STATUS_INITIALIZED,
    errors = emptyList(),
    requiresHitl = false,
    hitlReason = null,
    hitlApproved = false,
    maxRetries = maxRetries,
    thresholdFBeta = thresholdFBeta,
)

/** The status a freshly initialised run carries. */
const val STATUS_INITIALIZED: String = "initialized"

/**
 * Pure map reducer merging an update into the current value.
 *
 * Port of `workflow.state.merge_dicts`. The update wins on a key that is
 * present in both; a key that is only in [b] is appended.
 */
fun <K, V> mergeDicts(a: Map<K, V>, b: Map<K, V>): Map<K, V> = a + b

/**
 * Pure list reducer concatenating two lists.
 *
 * Port of `workflow.state.append_lists`. Neither input is mutated.
 */
fun <T> appendLists(a: List<T>, b: List<T>): List<T> = a + b
