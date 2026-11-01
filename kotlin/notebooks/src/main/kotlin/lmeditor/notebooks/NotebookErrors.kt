package lmeditor.notebooks

import lmeditor.core.LMeditorError

/**
 * The base of every failure the notebook store raises.
 *
 * A notebook has two ways of refusing a caller: the notebook id is not a safe
 * path segment, and the notebook is not there. Each one is typed and carries
 * its own details, so a refusal that crosses the seam says what was refused
 * and why -- and never looks like an empty result that succeeded.
 */
open class NotebookError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

/**
 * Raised when a notebook id is not a safe path segment.
 *
 * The error is a [NotebookError] -- so a caller of the store can catch the
 * domain error -- and it names the offending id and the reason it is unsafe,
 * because an unsafe notebook id is a bad argument value and must not be
 * mistaken for a missing notebook.
 */
class UnsafeNotebookIdError(
    /** The offending notebook id. */
    val notebookId: String,
    /** Why the id is not a safe path segment. */
    val reason: String,
) : NotebookError(
    "unsafe notebook_id '$notebookId': $reason",
    mapOf("notebook_id" to notebookId, "reason" to reason),
)

/**
 * Raised when the notebook is not there.
 *
 * The notebooks root always exists -- the server opens one and keeps it --
 * so a notebook that is not there is a missing notebook and nothing else.
 * The error is a [NotebookError], so a caller can catch the domain error and
 * tell it apart from a refusal of the request itself.
 */
class NotebookNotFoundError(
    /** The notebook that is not there. */
    val notebookId: String,
) : NotebookError(
    "NotebookNotFoundError: the notebook '$notebookId' is not there.",
    mapOf("notebook_id" to notebookId),
)
