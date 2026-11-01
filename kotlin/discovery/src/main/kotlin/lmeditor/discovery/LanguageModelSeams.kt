package lmeditor.discovery

/**
 * The language-model seams of the discovery layer.
 *
 * The Python tier runs the models (DSPy, an OpenAI-compatible endpoint); the
 * Kotlin tier owns the deterministic domain rules that cross the seam. These
 * three interfaces are where the model call crosses the NDJSON seam.
 *
 * The rules the seam does not carry are named rather than implied:
 * ARCHITECTURE.md §18.2.1 is the catalogue of the drift-pinned Python twins,
 * the two rules that have no Kotlin counterpart, and the model-facing half
 * that is Python by design.
 *
 * Each seam returns the model's raw answer as text. The deterministic half
 * of the discovery layer -- parsing the answer, deciding which sections
 * survive, assigning passages to them -- stays in Kotlin and is the same
 * whether the answer came from a model or from the corpus.
 *
 * A seam that is asked for but not supplied is a failure, not a downgrade:
 * the caller raises `LMNotConfiguredError` rather than silently producing a
 * weaker document.
 */

/**
 * The model that proposes the section headings of an outline.
 *
 * The implementation returns a JSON array of objects with `title`,
 * `description` and `keywords`, exactly as the Python tier's DSPy signature
 * asks for.
 */
interface OutlineModel {
    /**
     * Propose the section headings for a manuscript about [topic].
     *
     * @param topic the manuscript subject.
     * @param passages the corpus the headings must be grounded in, one
     *   passage per block.
     * @return the model's raw answer.
     */
    fun proposeSections(topic: String, passages: String): String
}

/**
 * The model that writes the personas of a discovery run.
 *
 * The implementation returns a JSON array of objects with `role`, `title`,
 * `description`, `focus_areas` and `guiding_questions`.
 */
interface PerspectiveModel {
    /**
     * Write [numPerspectives] distinct personas for [topic].
     *
     * @param topic the research subject or thesis.
     * @param context the background the personas are grounded in.
     * @param numPerspectives how many personas to produce.
     * @return the model's raw answer.
     */
    fun proposePerspectives(topic: String, context: String, numPerspectives: Int): String
}

/**
 * The model that frames the evidence a dialogue turn retrieved.
 *
 * The implementation returns one or two sentences. It never writes the
 * grounded quotations: those are added by the Kotlin side, so a model can
 * add framing but can never introduce an uncited claim.
 */
interface DialogueModel {
    /**
     * Frame [passages] as an answer to [question].
     *
     * @param question the inquiry the turn asked.
     * @param passages the retrieved passages, one per block.
     * @param role the wire name of the perspective that asked.
     * @return the model's framing sentence.
     */
    fun frameAnswer(question: String, passages: String, role: String): String
}
