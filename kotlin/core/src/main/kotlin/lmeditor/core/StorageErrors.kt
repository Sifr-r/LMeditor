package lmeditor.core

/** A write failed its revision precondition; no implicit merge is performed. */
class RevisionConflictError(
    artifactId: String,
    expectedRevision: Long,
    actualRevision: Long,
    outcome: String,
) : LMeditorError(
    "Revision conflict for '$artifactId'.",
    linkedMapOf(
        "artifact_id" to artifactId,
        "expected_revision" to expectedRevision,
        "actual_revision" to actualRevision,
        "outcome" to outcome,
    ),
)

/** Context for a failed storage mutation, shared across stores and the bridge. */
class StorageWriteError(
    path: String,
    reason: String,
) : LMeditorError(
    "Could not write storage at '$path': $reason.",
    linkedMapOf("path" to path, "reason" to reason),
)

/** A multi-file operation requires recovery before it can be acknowledged. */
class IncompleteOperationError(
    operationId: String,
    operationKind: String,
    affectedPaths: List<String>,
) : LMeditorError(
    "Operation '$operationId' ($operationKind) is incomplete.",
    linkedMapOf(
        "operation_id" to operationId,
        "operation_kind" to operationKind,
        "affected_paths" to affectedPaths,
    ),
)
