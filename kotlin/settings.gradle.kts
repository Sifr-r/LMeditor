// LMeditor — Kotlin core tier.
//
// The Kotlin tier owns every deterministic domain rule that crosses the NDJSON
// seam. It deliberately has NO third-party runtime dependencies: JSON, XML,
// ZIP, HTTP, hashing and date handling all come from the JDK itself.
//
// The rules the seam does not serve are named, not implied: ARCHITECTURE.md
// §18.2.1 is the catalogue of the drift-pinned Python twins, the two rules
// that have no Kotlin counterpart, and the model-facing half that is Python
// by design. See ARCHITECTURE.md §18.

pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

rootProject.name = "lmeditor-kotlin"

// --- Domain modules -------------------------------------------------------
// Dependency direction is strictly downward: core <- text <- word, etc.
// `core` depends on nothing at all.

include(":core") // errors, enums, schemas, citation grammar, native JSON
include(":text") // chunking, lineage, claim extraction, citation auditing
include(":word") // OOXML (.docx) document model, reader and writer
include(":pdf") // native PDF text and metadata extraction
include(":ingest") // source loaders, SSRF policy, bounded upload store
include(":library") // the on-disk book library, brief and working sets
include(":workflow") // workflow state and conditional routing
include(":editing") // grounded inline editing
include(":factcheck") // claim -> evidence -> verdict
include(":discovery") // perspective generation and outline construction
include(":server") // session broker and wire models for the SSE transport
include(":vault") // the rules of a vault: paths, markdown grammar and store
include(":notebooks") // the rules of a notebook: ids, manifest and store
include(":bridge") // the NDJSON stdio seam the Python tier calls into

// The desktop client. It is not a tier module: it sits above the tier and
// depends downward on :core alone. See build.gradle.kts and CONTRACTS.md.
include(":desktop")
