package lmeditor.core

/**
 * The contract every domain enumeration keeps with the wire.
 *
 * An enum that carries its wire string is one that can cross the NDJSON seam
 * without a hand-written conversion at every call site: the JSON codec writes
 * `wire` and never the Kotlin constant name, so the string that leaves the
 * tier is the string that travels.
 */
interface WireEnum {
    /** The exact string this value has on the wire. */
    val wire: String
}

/**
 * Domain enumerations and their wire vocabulary.
 *
 * Every enum carries the exact string the Python tier already speaks, so a
 * value can cross the NDJSON seam without translation.
 * The Python tier historically exposed a few aliases (`ENTAILMENT`,
 * `DRAFTED`, `FLAGGED`); those are reproduced as companion constants so
 * existing call sites keep working, without duplicating enum entries.
 */

enum class EntailmentStatus(override val wire: String) : WireEnum {
    ENTAILED("entailed"),
    CONTRADICTED("contradicted"),
    NEUTRAL("neutral"),
    UNGROUNDED("ungrounded");

    companion object {
        /** Historical alias of [ENTAILED]. */
        val ENTAILMENT: EntailmentStatus = ENTAILED

        /** Historical alias of [CONTRADICTED]. */
        val CONTRADICTION: EntailmentStatus = CONTRADICTED

        fun fromWire(value: String): EntailmentStatus =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown entailment status '$value'",
                    field = "entailment_status",
                    value = value,
                )
    }
}

enum class DocumentStatus(override val wire: String) : WireEnum {
    DISCOVERY("discovery"),
    OUTLINING("outlining"),
    DRAFTING("drafting"),
    VERIFYING("verifying"),
    REVISING("revising"),
    READY_FOR_HITL("ready_for_hitl"),
    COMPLETED("completed"),
    FAILED("failed");

    companion object {
        fun fromWire(value: String): DocumentStatus =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown document status '$value'",
                    field = "document_status",
                    value = value,
                )
    }
}

enum class SectionStatus(override val wire: String) : WireEnum {
    PENDING("pending"),
    DRAFTING("drafting"),
    VERIFIED("verified"),
    NEEDS_REVISION("needs_revision"),
    APPROVED("approved");

    companion object {
        /** Historical alias of [DRAFTING]. */
        val DRAFTED: SectionStatus = DRAFTING

        /** Historical alias of [NEEDS_REVISION]. */
        val FLAGGED: SectionStatus = NEEDS_REVISION

        fun fromWire(value: String): SectionStatus =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown section status '$value'",
                    field = "section_status",
                    value = value,
                )
    }
}

enum class WorkflowStep(override val wire: String) : WireEnum {
    INGEST("ingest"),
    DISCOVER("discover"),
    GUIDED_RESEARCH("guided_research"),
    OUTLINE("outline"),
    DRAFT_SECTIONS("draft_sections"),
    VERIFY_CITATIONS("verify_citations"),
    CRITIQUE_REVISION("critique_revision"),
    MACRO_POLISH("macro_polish"),
    HITL_GATE("hitl_gate"),
    COMPLETE("complete");

    companion object {
        fun fromWire(value: String): WorkflowStep =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown workflow step '$value'",
                    field = "workflow_step",
                    value = value,
                )
    }
}

/**
 * Server-Sent Event payload classification.
 *
 * These values are the *whole* wire vocabulary: the Python tier publishes
 * exactly these strings, and a consumer of the stream dispatches on them.
 */
enum class EventType(override val wire: String) : WireEnum {
    NODE_START("node_start"),
    NODE_COMPLETE("node_complete"),
    TOKEN_DELTA("token_delta"),
    SCORECARD_UPDATE("scorecard_update"),
    HITL_REQUEST("hitl_request"),
    WORKFLOW_COMPLETE("workflow_complete"),
    WORKFLOW_ERROR("workflow_error"),

    // Content events: the client parses these by name.
    CHUNKS("chunks"),
    PERSPECTIVES("perspectives"),
    RESEARCH_NOTES("research_notes"),
    OUTLINE("outline"),
    CLAIM_VERIFIED("claim_verified"),
    CITATION_CREATED("citation_created"),
    STREAM_METRICS("stream_metrics");

    companion object {
        fun fromWire(value: String): EventType =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown event type '$value'",
                    field = "event_type",
                    value = value,
                )
    }
}

enum class PerspectiveRole(override val wire: String) : WireEnum {
    DOMAIN_EXPERT("domain_expert"),
    CRITICAL_SKEPTIC("critical_skeptic"),
    SYNTHESIZER("synthesizer"),
    GENERAL_READER("general_reader"),
    FACT_CHECKER("fact_checker"),
    SKEPTIC("skeptic"),
    PRACTITIONER("practitioner"),
    REGULATOR("regulator"),
    COMPARATIVE_ANALYST("comparative_analyst"),
    GENERAL_AUDIENCE("general_audience");

    companion object {
        fun fromWire(value: String): PerspectiveRole =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown perspective role '$value'",
                    field = "perspective_role",
                    value = value,
                )
    }
}

enum class OutlineNodeType(override val wire: String) : WireEnum {
    ROOT("root"),
    SECTION("section"),
    SUBSECTION("subsection"),
    LEAF("leaf");

    companion object {
        fun fromWire(value: String): OutlineNodeType =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown outline node type '$value'",
                    field = "outline_node_type",
                    value = value,
                )
    }
}

enum class CitationFormat(override val wire: String) : WireEnum {
    BRACKETED_ID("bracketed_id"),
    BRACKETED_NUMBER("bracketed_number");

    companion object {
        fun fromWire(value: String): CitationFormat =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown citation format '$value'",
                    field = "citation_format",
                    value = value,
                )
    }
}

enum class SourceKind(override val wire: String) : WireEnum {
    PDF("pdf"),
    DOCX("docx"),
    MARKDOWN("markdown"),
    HTML("html"),
    TEXT("text"),
    URL("url");

    companion object {
        fun fromWire(value: String): SourceKind =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown source kind '$value'",
                    field = "source_kind",
                    value = value,
                )
    }
}

/** Granularity of a targeted edit applied to a manuscript. */
enum class EditScope(override val wire: String) : WireEnum {
    PARAGRAPH("paragraph"),
    SECTION("section");

    companion object {
        fun fromWire(value: String): EditScope =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown edit scope '$value'",
                    field = "edit_scope",
                    value = value,
                )
    }
}

/** Where a piece of supporting evidence was found. */
enum class EvidenceSource(override val wire: String) : WireEnum {
    CORPUS("corpus"),
    WEB("web");

    companion object {
        fun fromWire(value: String): EvidenceSource =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown evidence source '$value'",
                    field = "evidence_source",
                    value = value,
                )
    }
}

/** Aggregate verdict produced by fact-checking a passage. */
enum class FactCheckVerdict(override val wire: String) : WireEnum {
    SUPPORTED("supported"),
    PARTIALLY_SUPPORTED("partially_supported"),
    UNSUPPORTED("unsupported"),
    CONTRADICTED("contradicted"),
    INCONCLUSIVE("inconclusive");

    companion object {
        fun fromWire(value: String): FactCheckVerdict =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown fact-check verdict '$value'",
                    field = "fact_check_verdict",
                    value = value,
                )
    }
}

/** Role a section plays inside a bounded working set. */
enum class WorkingSetRole(override val wire: String) : WireEnum {
    TARGET("target"),
    PREVIOUS("previous"),
    NEXT("next");

    companion object {
        fun fromWire(value: String): WorkingSetRole =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown working-set role '$value'",
                    field = "working_set_role",
                    value = value,
                )
    }
}

/** Sources of bibliographic metadata, in priority order. */
enum class BibliographyResolverKind(override val wire: String) : WireEnum {
    PDF_HEADER("pdf-header"),
    CROSSREF("crossref"),
    ARXIV("arxiv"),
    PUBMED("pubmed"),
    OPENALEX("openalex"),
    USER("user"),
    NONE("none");

    companion object {
        fun fromWire(value: String): BibliographyResolverKind =
            entries.firstOrNull { it.wire == value }
                ?: throw ValidationError(
                    "Unknown bibliography resolver '$value'",
                    field = "bibliography_resolver",
                    value = value,
                )
    }
}
