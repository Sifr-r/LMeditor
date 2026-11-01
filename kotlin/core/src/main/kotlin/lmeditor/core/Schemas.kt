package lmeditor.core

import java.security.MessageDigest

/**
 * The schema models, and how they are built.
 *
 * These models mirror the Python tier's Pydantic models, and they must behave
 * the same way -- down to the whitespace. A Python model declared with
 * `str_strip_whitespace=True` never keeps the whitespace around a string
 * field, and the constraints are checked on the trimmed value. A Kotlin model
 * that kept the whitespace would put the two tiers a character apart, and
 * every offset measured against that character would be a character out.
 *
 * Pydantic has two ways to make a model, and they differ:
 *
 * * the **constructor** validates and strips;
 * * **`model_copy()`** does neither.
 *
 * Kotlin's `data class` has one constructor, and the generated `copy()` calls
 * it. So the models here have a **private** primary constructor, reached only
 * through the companion's `operator fun invoke` -- which trims, then
 * validates -- and a **public** `copy()` -- which reaches the private
 * constructor directly, and therefore neither trims nor strips.
 *
 * `@ExposedCopyVisibility` is what keeps `copy()` public despite the private
 * constructor. The compiler flags every such call with
 * `DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING`, because it will become an error
 * in Kotlin language version 2.5; the files that copy a model suppress it
 * with a comment saying why. The alternative -- a `copy()` that trims -- would
 * quietly differ from `model_copy()` on every value the operator hands the
 * editor, which is exactly the kind of drift this tier exists to remove.
 *
 * The models whose Python counterpart does *not* strip -- `SourceChunk`,
 * `SourceIngestResult`, `DialogueTurn`, `DialogueSession`, `ResearchMemory`,
 * `SectionSaveRequest`, `SectionSaveResult`, `BookSectionSaveRequest` and
 * `BookSectionSaveResult` -- keep an ordinary public constructor, because
 * that is what their Python originals do.
 */

/**
 * Native hashing helpers.
 *
 * The chunk identifier is a SHA-256 hex digest, so the digest must be
 * byte-for-byte identical to the Python tier's `hashlib.sha256(...).hexdigest()`.
 * That is the contract: a chunk id minted on one side must resolve on the other.
 */
object Hashing {

    private val HEX = "0123456789abcdef".toCharArray()

    /** Lowercase hex SHA-256 of [bytes]. */
    fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        val out = CharArray(digest.size * 2)
        for (i in digest.indices) {
            val b = digest[i].toInt() and 0xff
            out[i * 2] = HEX[b ushr 4]
            out[i * 2 + 1] = HEX[b and 0x0f]
        }
        return String(out)
    }

    /** Lowercase hex SHA-256 of the UTF-8 encoding of [text]. */
    fun sha256Hex(text: String): String = sha256Hex(text.toByteArray(Charsets.UTF_8))
}

/**
 * Immutable representation of a text segment anchored by character and byte
 * offsets.
 *
 * The invariants the Python tier enforces through Pydantic are enforced here
 * too, so a malformed chunk cannot be constructed on either side of the seam.
 */
data class SourceChunk(
    val chunkId: String,
    val documentId: String,
    val text: String,
    val charStart: Int = 0,
    val charEnd: Int = 0,
    val byteStart: Int = 0,
    val byteEnd: Int = 0,
    val tokenCount: Int = 0,
    val startChar: Int = charStart,
    val endChar: Int = charEnd,
    val metadata: Map<String, Any?> = emptyMap(),
) {
    init {
        if (charStart < 0) {
            throw ValidationError("char_start must not be negative", field = "char_start")
        }
        if (charEnd < 0) {
            throw ValidationError("char_end must not be negative", field = "char_end")
        }
        if (byteStart < 0) {
            throw ValidationError("byte_start must not be negative", field = "byte_start")
        }
        if (byteEnd < 0) {
            throw ValidationError("byte_end must not be negative", field = "byte_end")
        }
        if (startChar < 0) {
            throw ValidationError("start_char must not be negative", field = "start_char")
        }
        if (endChar < 0) {
            throw ValidationError("end_char must not be negative", field = "end_char")
        }
        if (!CHUNK_ID_REGEX.matches(chunkId)) {
            throw ValidationError(
                "chunk_id must be a 64-character lowercase SHA-256 hex digest",
                field = "chunk_id",
                value = chunkId,
            )
        }
        if (documentId.isEmpty()) {
            throw ValidationError("document_id must not be empty", field = "document_id")
        }
        if (text.isEmpty()) {
            throw ValidationError("text must not be empty", field = "text")
        }
        if (charEnd > 0 || charStart > 0) {
            if (charEnd <= charStart) {
                throw ValidationError(
                    "char_end ($charEnd) must be greater than char_start ($charStart)",
                    field = "char_end",
                )
            }
        }
        if (byteEnd > 0 || byteStart > 0) {
            if (byteEnd <= byteStart) {
                throw ValidationError(
                    "byte_end ($byteEnd) must be greater than byte_start ($byteStart)",
                    field = "byte_end",
                )
            }
        }
        if (tokenCount < 0) {
            throw ValidationError("token_count must not be negative", field = "token_count")
        }
    }

    /** Alias for [documentId]. */
    val docId: String get() = documentId

    /** Alias for [charStart]. */
    val charOffsetStart: Int get() = charStart

    /** Alias for [charEnd]. */
    val charOffsetEnd: Int get() = charEnd

    /** Title of the source document, taken from the chunk metadata. */
    val documentTitle: String get() = metadata["document_title"]?.toString() ?: ""

    companion object {
        internal val CHUNK_ID_REGEX = Regex("^[a-f0-9]{64}$")

        /**
         * Compute the deterministic SHA-256 chunk id and construct the chunk.
         *
         * The payload is `document_id:char_start:char_end:text`, exactly as the
         * Python tier computes it.
         */
        fun create(
            documentId: String,
            text: String,
            charStart: Int,
            charEnd: Int,
            byteStart: Int,
            byteEnd: Int,
            metadata: Map<String, Any?> = emptyMap(),
        ): SourceChunk {
            val payload = "$documentId:$charStart:$charEnd:$text"
            return SourceChunk(
                chunkId = Hashing.sha256Hex(payload),
                documentId = documentId,
                text = text,
                charStart = charStart,
                charEnd = charEnd,
                byteStart = byteStart,
                byteEnd = byteEnd,
                startChar = charStart,
                endChar = charEnd,
                metadata = metadata,
            )
        }
    }
}

/** Structured bibliographic metadata attached to a [Source]. */
@kotlin.ExposedCopyVisibility
data class BibliographicRecord private constructor(
    val title: String,
    val authors: List<String> = emptyList(),
    val year: Int? = null,
    val publicationDate: String? = null,
    val containerTitle: String? = null,
    val volume: String? = null,
    val issue: String? = null,
    val pages: String? = null,
    val doi: String? = null,
    val url: String? = null,
    val type: String? = null,
    val resolver: BibliographyResolverKind = BibliographyResolverKind.NONE,
) {
    init {
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
        if (year != null && (year < 1700 || year > 2100)) {
            throw ValidationError("year must be between 1700 and 2100", field = "year", value = year)
        }
    }

    companion object {
        /**
         * Construct a [BibliographicRecord], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            title: String,
            authors: List<String> = emptyList(),
            year: Int? = null,
            publicationDate: String? = null,
            containerTitle: String? = null,
            volume: String? = null,
            issue: String? = null,
            pages: String? = null,
            doi: String? = null,
            url: String? = null,
            type: String? = null,
            resolver: BibliographyResolverKind = BibliographyResolverKind.NONE,
        ): BibliographicRecord = BibliographicRecord(
            title = title.stripModelWhitespace(),
            authors = authors.map { it.stripModelWhitespace() },
            year = year,
            publicationDate = publicationDate?.stripModelWhitespace(),
            containerTitle = containerTitle?.stripModelWhitespace(),
            volume = volume?.stripModelWhitespace(),
            issue = issue?.stripModelWhitespace(),
            pages = pages?.stripModelWhitespace(),
            doi = doi?.stripModelWhitespace(),
            url = url?.stripModelWhitespace(),
            type = type?.stripModelWhitespace(),
            resolver = resolver,
            )
    }
}

/** A user-supplied artifact (file or URL) plus its resolved bibliographic record. */
@kotlin.ExposedCopyVisibility
data class Source private constructor(
    val id: String,
    val kind: SourceKind,
    val origin: String,
    val title: String,
    val bibliography: BibliographicRecord? = null,
    val pageCount: Int? = null,
    val byteSize: Int? = null,
    val addedAt: String = "",
    val omissions: List<String> = emptyList(),
) {
    init {
        if (!SOURCE_ID_REGEX.matches(id)) {
            throw ValidationError(
                "id must match ^[a-zA-Z0-9_\\-\\.]+$",
                field = "id",
                value = id,
            )
        }
        if (origin.isEmpty()) {
            throw ValidationError("origin must not be empty", field = "origin")
        }
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
        if (pageCount != null && pageCount < 0) {
            throw ValidationError("page_count must not be negative", field = "page_count")
        }
        if (byteSize != null && byteSize < 0) {
            throw ValidationError("byte_size must not be negative", field = "byte_size")
        }
    }

    companion object {

        /**
         * Construct a [Source], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            id: String,
            kind: SourceKind,
            origin: String,
            title: String,
            bibliography: BibliographicRecord? = null,
            pageCount: Int? = null,
            byteSize: Int? = null,
            addedAt: String = "",
            omissions: List<String> = emptyList(),
        ): Source = Source(
            id = id.stripModelWhitespace(),
            kind = kind,
            origin = origin.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            bibliography = bibliography,
            pageCount = pageCount,
            byteSize = byteSize,
            addedAt = addedAt.stripModelWhitespace(),
            omissions = omissions.map { it.stripModelWhitespace() },
            )

        internal val SOURCE_ID_REGEX = Regex("^[a-zA-Z0-9_\\-\\.]+$")
    }
}

/** Return shape of the source-ingest endpoint. */
data class SourceIngestResult(
    val source: Source,
    val chunks: List<SourceChunk>,
    val omissions: List<String> = emptyList(),
)

/** Atomic proposition extracted from draft text with entailment verification state. */
@kotlin.ExposedCopyVisibility
data class Claim private constructor(
    val claimId: String,
    val sectionId: String,
    val text: String,
    val charStart: Int = 0,
    val charEnd: Int = 0,
    val citedChunkIds: List<String> = emptyList(),
    val entailmentStatus: EntailmentStatus = EntailmentStatus.NEUTRAL,
    val entailmentScore: Double = 0.0,
    val reasoning: String? = null,
    val sentenceIdx: Int = 0,
    val premiseText: String? = null,
) {
    init {
        if (charStart < 0) {
            throw ValidationError("char_start must not be negative", field = "char_start")
        }
        if (charEnd < 0) {
            throw ValidationError("char_end must not be negative", field = "char_end")
        }
        if (claimId.isEmpty()) {
            throw ValidationError("claim_id must not be empty", field = "claim_id")
        }
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
        if (text.isEmpty()) {
            throw ValidationError("text must not be empty", field = "text")
        }
        if (charEnd > 0 || charStart > 0) {
            if (charEnd <= charStart) {
                throw ValidationError(
                    "char_end ($charEnd) must be greater than char_start ($charStart)",
                    field = "char_end",
                )
            }
        }
        requireScore(entailmentScore, "entailment_score")
        if (sentenceIdx < 0) {
            throw ValidationError("sentence_idx must not be negative", field = "sentence_idx")
        }
    }

    /** Alias for [reasoning]. */
    val rationale: String? get() = reasoning

    companion object {
        /**
         * Construct a [Claim], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            claimId: String,
            sectionId: String,
            text: String,
            charStart: Int = 0,
            charEnd: Int = 0,
            citedChunkIds: List<String> = emptyList(),
            entailmentStatus: EntailmentStatus = EntailmentStatus.NEUTRAL,
            entailmentScore: Double = 0.0,
            reasoning: String? = null,
            sentenceIdx: Int = 0,
            premiseText: String? = null,
        ): Claim = Claim(
            claimId = claimId.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            text = text.stripModelWhitespace(),
            charStart = charStart,
            charEnd = charEnd,
            citedChunkIds = citedChunkIds.map { it.stripModelWhitespace() },
            entailmentStatus = entailmentStatus,
            entailmentScore = entailmentScore,
            reasoning = reasoning?.stripModelWhitespace(),
            sentenceIdx = sentenceIdx,
            premiseText = premiseText?.stripModelWhitespace(),
            )
    }
}

/** Fine-grained token-span attribution binding a claim to an exact source passage. */
@kotlin.ExposedCopyVisibility
data class CitationLineage private constructor(
    val lineageId: String,
    val claimId: String,
    val chunkId: String,
    val spanStart: Int,
    val spanEnd: Int,
    val exactQuote: String? = null,
    val confidenceScore: Double = 1.0,
    val createdAt: String = "",
) {
    init {
        if (lineageId.isEmpty()) {
            throw ValidationError("lineage_id must not be empty", field = "lineage_id")
        }
        if (claimId.isEmpty()) {
            throw ValidationError("claim_id must not be empty", field = "claim_id")
        }
        if (chunkId.isEmpty()) {
            throw ValidationError("chunk_id must not be empty", field = "chunk_id")
        }
        if (spanStart < 0) {
            throw ValidationError("span_start must not be negative", field = "span_start")
        }
        if (spanEnd <= spanStart) {
            throw ValidationError(
                "span_end ($spanEnd) must be greater than span_start ($spanStart)",
                field = "span_end",
            )
        }
        requireScore(confidenceScore, "confidence_score")
    }

    companion object {
        /**
         * Construct a [CitationLineage], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            lineageId: String,
            claimId: String,
            chunkId: String,
            spanStart: Int,
            spanEnd: Int,
            exactQuote: String? = null,
            confidenceScore: Double = 1.0,
            createdAt: String = "",
        ): CitationLineage = CitationLineage(
            lineageId = lineageId.stripModelWhitespace(),
            claimId = claimId.stripModelWhitespace(),
            chunkId = chunkId.stripModelWhitespace(),
            spanStart = spanStart,
            spanEnd = spanEnd,
            exactQuote = exactQuote?.stripModelWhitespace(),
            confidenceScore = confidenceScore,
            createdAt = createdAt.stripModelWhitespace(),
            )
    }
}

/** An inline citation token mapping to an indexed [SourceChunk]. */
@kotlin.ExposedCopyVisibility
data class InlineCitation private constructor(
    val citationId: String,
    val chunkId: String,
    val sourceDoc: String = "",
    val spanStart: Int = 0,
    val spanEnd: Int = 0,
    val prefixText: String = "",
) {
    init {
        if (spanEnd < 0) {
            throw ValidationError("span_end must not be negative", field = "span_end")
        }
        if (spanStart < 0) {
            throw ValidationError("span_start must not be negative", field = "span_start")
        }
    }

    companion object {
        /**
         * Construct a [InlineCitation], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            citationId: String,
            chunkId: String,
            sourceDoc: String = "",
            spanStart: Int = 0,
            spanEnd: Int = 0,
            prefixText: String = "",
        ): InlineCitation = InlineCitation(
            citationId = citationId.stripModelWhitespace(),
            chunkId = chunkId.stripModelWhitespace(),
            sourceDoc = sourceDoc.stripModelWhitespace(),
            spanStart = spanStart,
            spanEnd = spanEnd,
            prefixText = prefixText.stripModelWhitespace(),
            )
    }
}

/** STORM-style perspective persona guiding inquiry, discovery and critique. */
@kotlin.ExposedCopyVisibility
data class Perspective private constructor(
    val perspectiveId: String = "",
    val id: String = "",
    val role: PerspectiveRole,
    val title: String = "",
    val name: String = "",
    val description: String = "",
    val domain: String = "",
    val focusAreas: List<String> = emptyList(),
    val promptDirectives: List<String> = emptyList(),
    val guidingQuestions: List<String> = emptyList(),
    val keyInquiries: List<String> = emptyList(),
) {
    /** The resolved persona id: whichever of [perspectiveId]/[id] was supplied. */
    val resolvedId: String = perspectiveId.ifEmpty { id }.ifEmpty { "perspective_default" }

    /** The resolved display title: whichever of [title]/[name] was supplied. */
    val resolvedTitle: String = title.ifEmpty { name }.ifEmpty { "Perspective Title" }

    companion object {
        /**
         * Construct a [Perspective], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            perspectiveId: String = "",
            id: String = "",
            role: PerspectiveRole,
            title: String = "",
            name: String = "",
            description: String = "",
            domain: String = "",
            focusAreas: List<String> = emptyList(),
            promptDirectives: List<String> = emptyList(),
            guidingQuestions: List<String> = emptyList(),
            keyInquiries: List<String> = emptyList(),
        ): Perspective = Perspective(
            perspectiveId = perspectiveId.stripModelWhitespace(),
            id = id.stripModelWhitespace(),
            role = role,
            title = title.stripModelWhitespace(),
            name = name.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            domain = domain.stripModelWhitespace(),
            focusAreas = focusAreas.map { it.stripModelWhitespace() },
            promptDirectives = promptDirectives.map { it.stripModelWhitespace() },
            guidingQuestions = guidingQuestions.map { it.stripModelWhitespace() },
            keyInquiries = keyInquiries.map { it.stripModelWhitespace() },
            )
    }
}

/** Single turn in a conversational research loop. */
data class DialogueTurn(
    val turnId: String,
    val question: String,
    val answer: String,
    val referencedChunkIds: List<String> = emptyList(),
) {
    init {
        if (question.isEmpty()) {
            throw ValidationError("question must not be empty", field = "question")
        }
        if (answer.isEmpty()) {
            throw ValidationError("answer must not be empty", field = "answer")
        }
    }
}

/** Full conversational research record for a perspective. */
data class DialogueSession(
    val perspectiveId: String,
    val turns: List<DialogueTurn> = emptyList(),
    val synthesizedTakeaways: List<String> = emptyList(),
)

/** Aggregated knowledge repository from multi-perspective research. */
data class ResearchMemory(
    val topic: String,
    val sessions: List<DialogueSession> = emptyList(),
    val allReferencedChunkIds: List<String> = emptyList(),
    val collectedTakeaways: List<String> = emptyList(),
) {
    init {
        if (topic.isEmpty()) {
            throw ValidationError("topic must not be empty", field = "topic")
        }
    }
}

/** Hierarchical section node in the document outline tree. */
@kotlin.ExposedCopyVisibility
data class OutlineNode private constructor(
    val nodeId: String,
    val title: String,
    val sectionId: String = "",
    val depth: Int = 0,
    val description: String = "",
    val assignedChunkIds: List<String> = emptyList(),
    val children: List<OutlineNode> = emptyList(),
    val order: Int = 0,
    val subheadings: List<String> = emptyList(),
) {
    init {
        if (nodeId.isEmpty()) {
            throw ValidationError("node_id must not be empty", field = "node_id")
        }
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
        if (depth < 0) {
            throw ValidationError("depth must not be negative", field = "depth")
        }
        if (order < 0) {
            throw ValidationError("order must not be negative", field = "order")
        }
    }

    /** The section this node links to; defaults to the node id. */
    val resolvedSectionId: String get() = sectionId.ifEmpty { nodeId }

    /** A node is a leaf if it has no children. */
    val isLeaf: Boolean get() = children.isEmpty()

    /** Ids of the immediate child nodes. */
    val childrenIds: List<String> get() = children.map { it.nodeId }

    /** Every leaf node in this subtree. */
    fun getLeaves(): List<OutlineNode> {
        if (isLeaf) return listOf(this)
        return children.flatMap { it.getLeaves() }
    }

    companion object {
        /**
         * Construct a [OutlineNode], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            nodeId: String,
            title: String,
            sectionId: String = "",
            depth: Int = 0,
            description: String = "",
            assignedChunkIds: List<String> = emptyList(),
            children: List<OutlineNode> = emptyList(),
            order: Int = 0,
            subheadings: List<String> = emptyList(),
        ): OutlineNode = OutlineNode(
            nodeId = nodeId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            depth = depth,
            description = description.stripModelWhitespace(),
            assignedChunkIds = assignedChunkIds.map { it.stripModelWhitespace() },
            children = children,
            order = order,
            subheadings = subheadings.map { it.stripModelWhitespace() },
            )
    }
}

/**
 * One section of a caller-supplied outline.
 *
 * It carries no chunk assignments and no ids: the operator says *what* the
 * document should cover, and the corpus decides *what may be said* about it.
 */
@kotlin.ExposedCopyVisibility
data class OutlineNodeInput private constructor(
    val title: String,
    val description: String = "",
    val children: List<OutlineNodeInput> = emptyList(),
) {
    init {
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
    }

    companion object {
        /**
         * Construct a [OutlineNodeInput], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            title: String,
            description: String = "",
            children: List<OutlineNodeInput> = emptyList(),
        ): OutlineNodeInput = OutlineNodeInput(
            title = title.stripModelWhitespace(),
            description = description.stripModelWhitespace(),
            children = children,
            )
    }
}

/** Hierarchical outline of topics and sections forming a directed acyclic graph. */
@kotlin.ExposedCopyVisibility
data class TopicOutline private constructor(
    val topic: String,
    val nodes: List<OutlineNode> = emptyList(),
) {
    init {
        if (topic.isEmpty()) {
            throw ValidationError("topic must not be empty", field = "topic")
        }
    }

    /** All leaf nodes across the entire hierarchy. */
    fun getLeaves(): List<OutlineNode> = nodes.flatMap { it.getLeaves() }

    /** All distinct chunk ids assigned across all outline nodes. */
    fun getAllAssignedChunkIds(): List<String> {
        val seen = LinkedHashSet<String>()
        for (leaf in getLeaves()) seen.addAll(leaf.assignedChunkIds)
        return seen.toList()
    }

    /** Find a node by id anywhere in the hierarchy. */
    fun findNode(nodeId: String): OutlineNode? {
        fun search(nodes: List<OutlineNode>): OutlineNode? {
            for (n in nodes) {
                if (n.nodeId == nodeId) return n
                search(n.children)?.let { return it }
            }
            return null
        }
        return search(nodes)
    }

    /**
     * Validate DAG acyclicity, node uniqueness and integrity.
     *
     * @throws OutlineGenerationError when a cycle or a duplicate id is found.
     */
    fun validateDag() {
        val seenIds = mutableSetOf<String>()
        val visiting = mutableSetOf<String>()

        fun traverse(node: OutlineNode) {
            if (node.nodeId in visiting) {
                throw OutlineGenerationError(
                    "Cycle detected in outline DAG at node '${node.nodeId}'.",
                    nodeId = node.nodeId,
                    cycleDetected = true,
                )
            }
            if (!seenIds.add(node.nodeId)) {
                throw OutlineGenerationError(
                    "Duplicate node_id '${node.nodeId}' detected in outline DAG.",
                    nodeId = node.nodeId,
                )
            }
            visiting.add(node.nodeId)
            for (child in node.children) traverse(child)
            visiting.remove(node.nodeId)
        }

        for (root in nodes) traverse(root)
    }

    companion object {
        /**
         * Construct a [TopicOutline], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            topic: String,
            nodes: List<OutlineNode> = emptyList(),
        ): TopicOutline = TopicOutline(
            topic = topic.stripModelWhitespace(),
            nodes = nodes,
            )
    }
}

/** Complete draft content for an outline section with associated claims and citations. */
@kotlin.ExposedCopyVisibility
data class SectionDraft private constructor(
    val sectionId: String,
    val title: String,
    val content: String = "",
    val order: Int = 0,
    val citations: List<CitationLineage> = emptyList(),
    val claims: List<Claim> = emptyList(),
    val isVerified: Boolean = false,
    val status: SectionStatus = SectionStatus.PENDING,
    val revisionCount: Int = 0,
    val critique: String? = null,
    val citedChunkIds: List<String> = emptyList(),
) {
    init {
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
        if (order < 0) {
            throw ValidationError("order must not be negative", field = "order")
        }
        if (revisionCount < 0) {
            throw ValidationError("revision_count must not be negative", field = "revision_count")
        }
    }

    companion object {
        /**
         * Construct a [SectionDraft], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            sectionId: String,
            title: String,
            content: String = "",
            order: Int = 0,
            citations: List<CitationLineage> = emptyList(),
            claims: List<Claim> = emptyList(),
            isVerified: Boolean = false,
            status: SectionStatus = SectionStatus.PENDING,
            revisionCount: Int = 0,
            critique: String? = null,
            citedChunkIds: List<String> = emptyList(),
        ): SectionDraft = SectionDraft(
            sectionId = sectionId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            content = content.stripModelWhitespace(),
            order = order,
            citations = citations,
            claims = claims,
            isVerified = isVerified,
            status = status,
            revisionCount = revisionCount,
            critique = critique?.stripModelWhitespace(),
            citedChunkIds = citedChunkIds.map { it.stripModelWhitespace() },
            )
    }
}

/** Complete document draft with structured sections and provenance. */
@kotlin.ExposedCopyVisibility
data class DocumentDraft private constructor(
    val documentId: String,
    val title: String,
    val sections: List<SectionDraft> = emptyList(),
    val status: DocumentStatus = DocumentStatus.DRAFTING,
) {
    init {
        if (documentId.isEmpty()) {
            throw ValidationError("document_id must not be empty", field = "document_id")
        }
        if (title.isEmpty()) {
            throw ValidationError("title must not be empty", field = "title")
        }
    }

    companion object {
        /**
         * Construct a [DocumentDraft], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            documentId: String,
            title: String,
            sections: List<SectionDraft> = emptyList(),
            status: DocumentStatus = DocumentStatus.DRAFTING,
        ): DocumentDraft = DocumentDraft(
            documentId = documentId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            sections = sections,
            status = status,
            )
    }
}

/** Formal audit scorecard summarizing claim verification outcomes. */
@kotlin.ExposedCopyVisibility
data class VerificationReport private constructor(
    val reportId: String,
    val sectionId: String,
    val totalClaims: Int,
    val entailedClaims: Int,
    val contradictedClaims: Int,
    val ungroundedClaims: Int,
    val precision: Double,
    val claims: List<Claim> = emptyList(),
    val status: EntailmentStatus,
    val allPassed: Boolean,
    val summary: String,
    val evaluator: String = "",
) {
    init {
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
        if (entailedClaims < 0) {
            throw ValidationError("entailed_claims must not be negative", field = "entailed_claims")
        }
        if (contradictedClaims < 0) {
            throw ValidationError("contradicted_claims must not be negative", field = "contradicted_claims")
        }
        if (ungroundedClaims < 0) {
            throw ValidationError("ungrounded_claims must not be negative", field = "ungrounded_claims")
        }
        if (reportId.isEmpty()) {
            throw ValidationError("report_id must not be empty", field = "report_id")
        }
        if (totalClaims < 0) {
            throw ValidationError("total_claims must not be negative", field = "total_claims")
        }
        requireScore(precision, "precision")
    }

    /** Alias for the precision/groundedness metric score. */
    val fBeta: Double get() = precision

    /** Alias for [ungroundedClaims]. */
    val ungroundedCount: Int get() = ungroundedClaims

    /** Alias for [contradictedClaims]. */
    val contradictionCount: Int get() = contradictedClaims

    companion object {
        /**
         * Construct a [VerificationReport], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            reportId: String,
            sectionId: String,
            totalClaims: Int,
            entailedClaims: Int,
            contradictedClaims: Int,
            ungroundedClaims: Int,
            precision: Double,
            claims: List<Claim> = emptyList(),
            status: EntailmentStatus,
            allPassed: Boolean,
            summary: String,
            evaluator: String = "",
        ): VerificationReport = VerificationReport(
            reportId = reportId.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            totalClaims = totalClaims,
            entailedClaims = entailedClaims,
            contradictedClaims = contradictedClaims,
            ungroundedClaims = ungroundedClaims,
            precision = precision,
            claims = claims,
            status = status,
            allPassed = allPassed,
            summary = summary.stripModelWhitespace(),
            evaluator = evaluator.stripModelWhitespace(),
            )
    }
}

/** Mathematical citation audit metrics and claim breakdowns. */
@kotlin.ExposedCopyVisibility
data class VerificationScorecard private constructor(
    val precision: Double,
    val recall: Double,
    val fBeta: Double,
    val beta: Double = 0.5,
    val totalClaims: Int = 0,
    val supportedClaims: Int = 0,
    val ungroundedClaims: Int = 0,
    val contradictedClaims: Int = 0,
    val neutralClaims: Int = 0,
    val details: List<Claim> = emptyList(),
    val evaluator: String = "",
) {
    init {
        if (supportedClaims < 0) {
            throw ValidationError("supported_claims must not be negative", field = "supported_claims")
        }
        if (ungroundedClaims < 0) {
            throw ValidationError("ungrounded_claims must not be negative", field = "ungrounded_claims")
        }
        if (contradictedClaims < 0) {
            throw ValidationError("contradicted_claims must not be negative", field = "contradicted_claims")
        }
        if (neutralClaims < 0) {
            throw ValidationError("neutral_claims must not be negative", field = "neutral_claims")
        }
        requireScore(precision, "precision")
        requireScore(recall, "recall")
        requireScore(fBeta, "f_beta")
        if (beta <= 0.0) {
            throw ValidationError("beta must be greater than zero", field = "beta", value = beta)
        }
        if (totalClaims < 0) {
            throw ValidationError("total_claims must not be negative", field = "total_claims")
        }
    }

    companion object {
        /**
         * Construct a [VerificationScorecard], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            precision: Double,
            recall: Double,
            fBeta: Double,
            beta: Double = 0.5,
            totalClaims: Int = 0,
            supportedClaims: Int = 0,
            ungroundedClaims: Int = 0,
            contradictedClaims: Int = 0,
            neutralClaims: Int = 0,
            details: List<Claim> = emptyList(),
            evaluator: String = "",
        ): VerificationScorecard = VerificationScorecard(
            precision = precision,
            recall = recall,
            fBeta = fBeta,
            beta = beta,
            totalClaims = totalClaims,
            supportedClaims = supportedClaims,
            ungroundedClaims = ungroundedClaims,
            contradictedClaims = contradictedClaims,
            neutralClaims = neutralClaims,
            details = details,
            evaluator = evaluator.stripModelWhitespace(),
            )
    }
}

/** Human-in-the-loop review decision payload. */
@kotlin.ExposedCopyVisibility
data class HITLReviewPayload private constructor(
    val approved: Boolean,
    val docId: String = "",
    val feedback: String? = null,
    val modifiedSections: List<SectionDraft>? = null,
) {
    companion object {
        /**
         * Construct a [HITLReviewPayload], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            approved: Boolean,
            docId: String = "",
            feedback: String? = null,
            modifiedSections: List<SectionDraft>? = null,
        ): HITLReviewPayload = HITLReviewPayload(
            approved = approved,
            docId = docId.stripModelWhitespace(),
            feedback = feedback?.stripModelWhitespace(),
            modifiedSections = modifiedSections,
            )
    }
}

/** The operator's own text for one section of a document. */
data class SectionSaveRequest(
    val content: String,
    val title: String? = null,
    val order: Int? = null,
) {
    init {
        if (title != null && title.isEmpty()) {
            throw ValidationError("title must not be empty when supplied", field = "title")
        }
        if (order != null && order < 0) {
            throw ValidationError("order must not be negative", field = "order", value = order)
        }
    }
}

/** What the server stored for one section, and what it measured on it. */
data class SectionSaveResult(
    val sectionId: String,
    val title: String,
    val content: String,
    val status: SectionStatus,
    val isVerified: Boolean,
    val scorecard: VerificationScorecard? = null,
)

/** SSE broadcast payload for frontend synchronisation. */
@kotlin.ExposedCopyVisibility
data class WorkflowEvent private constructor(
    val eventType: EventType,
    val step: WorkflowStep,
    val docId: String,
    val data: Map<String, Any?> = emptyMap(),
) {
    companion object {
        /**
         * Construct a [WorkflowEvent], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            eventType: EventType,
            step: WorkflowStep,
            docId: String,
            data: Map<String, Any?> = emptyMap(),
        ): WorkflowEvent = WorkflowEvent(
            eventType = eventType,
            step = step,
            docId = docId.stripModelWhitespace(),
            data = data,
            )
    }
}

/** A targeted edit to one paragraph or section of a manuscript. */
@kotlin.ExposedCopyVisibility
data class InlineEditRequest private constructor(
    val sectionId: String,
    val instruction: String,
    val docId: String = "",
    val selectedText: String = "",
    val scope: EditScope = EditScope.PARAGRAPH,
    val paragraphIndex: Int = 0,
    val persist: Boolean = true,
) {
    init {
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
        if (instruction.isEmpty()) {
            throw ValidationError("instruction must not be empty", field = "instruction")
        }
        if (paragraphIndex < 0) {
            throw ValidationError("paragraph_index must not be negative", field = "paragraph_index")
        }
    }

    companion object {
        /**
         * Construct a [InlineEditRequest], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            sectionId: String,
            instruction: String,
            docId: String = "",
            selectedText: String = "",
            scope: EditScope = EditScope.PARAGRAPH,
            paragraphIndex: Int = 0,
            persist: Boolean = true,
        ): InlineEditRequest = InlineEditRequest(
            sectionId = sectionId.stripModelWhitespace(),
            instruction = instruction.stripModelWhitespace(),
            docId = docId.stripModelWhitespace(),
            selectedText = selectedText.stripModelWhitespace(),
            scope = scope,
            paragraphIndex = paragraphIndex,
            persist = persist,
            )
    }
}

/** The outcome of a targeted edit. */
@kotlin.ExposedCopyVisibility
data class InlineEditResult private constructor(
    val docId: String,
    val sectionId: String,
    val scope: EditScope = EditScope.PARAGRAPH,
    val originalText: String = "",
    val revisedText: String = "",
    val applied: Boolean = false,
    val reason: String = "",
    val citedChunkIds: List<String> = emptyList(),
    val claims: List<Claim> = emptyList(),
    val scorecard: VerificationScorecard? = null,
    val section: SectionDraft? = null,
) {
    init {
        if (docId.isEmpty()) {
            throw ValidationError("doc_id must not be empty", field = "doc_id")
        }
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
    }

    companion object {
        /**
         * Construct a [InlineEditResult], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            docId: String,
            sectionId: String,
            scope: EditScope = EditScope.PARAGRAPH,
            originalText: String = "",
            revisedText: String = "",
            applied: Boolean = false,
            reason: String = "",
            citedChunkIds: List<String> = emptyList(),
            claims: List<Claim> = emptyList(),
            scorecard: VerificationScorecard? = null,
            section: SectionDraft? = null,
        ): InlineEditResult = InlineEditResult(
            docId = docId.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            scope = scope,
            originalText = originalText.stripModelWhitespace(),
            revisedText = revisedText.stripModelWhitespace(),
            applied = applied,
            reason = reason.stripModelWhitespace(),
            citedChunkIds = citedChunkIds.map { it.stripModelWhitespace() },
            claims = claims,
            scorecard = scorecard,
            section = section,
            )
    }
}

/** A single piece of evidence used to judge a claim. */
@kotlin.ExposedCopyVisibility
data class Evidence private constructor(
    val source: EvidenceSource,
    val title: String,
    val excerpt: String,
    val chunkId: String = "",
    val url: String = "",
    val score: Double = 0.0,
) {
    init {
        requireScore(score, "score")
    }

    companion object {
        /**
         * Construct a [Evidence], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            source: EvidenceSource,
            title: String,
            excerpt: String,
            chunkId: String = "",
            url: String = "",
            score: Double = 0.0,
        ): Evidence = Evidence(
            source = source,
            title = title.stripModelWhitespace(),
            excerpt = excerpt.stripModelWhitespace(),
            chunkId = chunkId.stripModelWhitespace(),
            url = url.stripModelWhitespace(),
            score = score,
            )
    }
}

/** The verdict on one claim together with the evidence behind it. */
@kotlin.ExposedCopyVisibility
data class ClaimVerdict private constructor(
    val claimId: String,
    val status: EntailmentStatus,
    val claimText: String = "",
    val score: Double = 0.0,
    val evidence: List<Evidence> = emptyList(),
    val reasoning: String = "",
) {
    init {
        if (claimId.isEmpty()) {
            throw ValidationError("claim_id must not be empty", field = "claim_id")
        }
        requireScore(score, "score")
    }

    companion object {
        /**
         * Construct a [ClaimVerdict], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            claimId: String,
            status: EntailmentStatus,
            claimText: String = "",
            score: Double = 0.0,
            evidence: List<Evidence> = emptyList(),
            reasoning: String = "",
        ): ClaimVerdict = ClaimVerdict(
            claimId = claimId.stripModelWhitespace(),
            status = status,
            claimText = claimText.stripModelWhitespace(),
            score = score,
            evidence = evidence,
            reasoning = reasoning.stripModelWhitespace(),
            )
    }
}

/** A request to fact-check a passage against the corpus, the web, or both. */
@kotlin.ExposedCopyVisibility
data class FactCheckRequest private constructor(
    val text: String,
    val docId: String = "",
    val sectionId: String = "",
    val includeWeb: Boolean = false,
    val maxEvidence: Int = 5,
) {
    init {
        if (text.isEmpty()) {
            throw ValidationError("text must not be empty", field = "text")
        }
        if (maxEvidence < 1 || maxEvidence > 25) {
            throw ValidationError(
                "max_evidence must be between 1 and 25",
                field = "max_evidence",
                value = maxEvidence,
            )
        }
    }

    companion object {
        /**
         * Construct a [FactCheckRequest], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            text: String,
            docId: String = "",
            sectionId: String = "",
            includeWeb: Boolean = false,
            maxEvidence: Int = 5,
        ): FactCheckRequest = FactCheckRequest(
            text = text.stripModelWhitespace(),
            docId = docId.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            includeWeb = includeWeb,
            maxEvidence = maxEvidence,
            )
    }
}

/** The result of fact-checking a passage. */
@kotlin.ExposedCopyVisibility
data class FactCheckReport private constructor(
    val docId: String,
    val verdict: FactCheckVerdict = FactCheckVerdict.INCONCLUSIVE,
    val text: String = "",
    val claims: List<ClaimVerdict> = emptyList(),
    val scorecard: VerificationScorecard? = null,
    val webSearched: Boolean = false,
    val summary: String = "",
) {
    init {
        if (docId.isEmpty()) {
            throw ValidationError("doc_id must not be empty", field = "doc_id")
        }
    }

    companion object {
        /**
         * Construct a [FactCheckReport], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            docId: String,
            verdict: FactCheckVerdict = FactCheckVerdict.INCONCLUSIVE,
            text: String = "",
            claims: List<ClaimVerdict> = emptyList(),
            scorecard: VerificationScorecard? = null,
            webSearched: Boolean = false,
            summary: String = "",
        ): FactCheckReport = FactCheckReport(
            docId = docId.stripModelWhitespace(),
            verdict = verdict,
            text = text.stripModelWhitespace(),
            claims = claims,
            scorecard = scorecard,
            webSearched = webSearched,
            summary = summary.stripModelWhitespace(),
            )
    }
}

/** One section of a book, stored and edited in isolation. */
@kotlin.ExposedCopyVisibility
data class BookSection private constructor(
    val sectionId: String,
    val title: String = "",
    val order: Int = 0,
    val wordCount: Int = 0,
    val charCount: Int = 0,
    val chunkIds: List<String> = emptyList(),
) {
    init {
        if (wordCount < 0) {
            throw ValidationError("word_count must not be negative", field = "word_count")
        }
        if (charCount < 0) {
            throw ValidationError("char_count must not be negative", field = "char_count")
        }
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
        if (order < 0) {
            throw ValidationError("order must not be negative", field = "order")
        }
    }

    companion object {
        /**
         * Construct a [BookSection], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            sectionId: String,
            title: String = "",
            order: Int = 0,
            wordCount: Int = 0,
            charCount: Int = 0,
            chunkIds: List<String> = emptyList(),
        ): BookSection = BookSection(
            sectionId = sectionId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            order = order,
            wordCount = wordCount,
            charCount = charCount,
            chunkIds = chunkIds.map { it.stripModelWhitespace() },
            )
    }
}

/**
 * The index of a book: enough to navigate it without loading it.
 *
 * The manifest holds metadata and a section index, never the body text of the
 * book. That is what lets a book of any size be opened, navigated and edited
 * one section at a time.
 */
@kotlin.ExposedCopyVisibility
data class BookManifest private constructor(
    val bookId: String,
    val title: String = "",
    val sectionCount: Int = 0,
    val chunkCount: Int = 0,
    val createdAt: String,
    val modifiedAt: String,
    val sections: List<BookSection> = emptyList(),
    val brief: String = "",
) {
    init {
        if (bookId.isEmpty()) {
            throw ValidationError("book_id must not be empty", field = "book_id")
        }
        if (sectionCount < 0) {
            throw ValidationError("section_count must not be negative", field = "section_count")
        }
        if (chunkCount < 0) {
            throw ValidationError("chunk_count must not be negative", field = "chunk_count")
        }
    }

    companion object {
        /**
         * Construct a [BookManifest], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            bookId: String,
            title: String = "",
            sectionCount: Int = 0,
            chunkCount: Int = 0,
            createdAt: String,
            modifiedAt: String,
            sections: List<BookSection> = emptyList(),
            brief: String = "",
        ): BookManifest = BookManifest(
            bookId = bookId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            sectionCount = sectionCount,
            chunkCount = chunkCount,
            createdAt = createdAt.stripModelWhitespace(),
            modifiedAt = modifiedAt.stripModelWhitespace(),
            sections = sections,
            brief = brief.stripModelWhitespace(),
            )
    }
}

/** A section as it appears inside a bounded working set. */
@kotlin.ExposedCopyVisibility
data class WorkingSetSection private constructor(
    val sectionId: String,
    val title: String = "",
    val order: Int = 0,
    val role: WorkingSetRole = WorkingSetRole.TARGET,
    val content: String = "",
) {
    init {
        if (order < 0) {
            throw ValidationError("order must not be negative", field = "order")
        }
        if (sectionId.isEmpty()) {
            throw ValidationError("section_id must not be empty", field = "section_id")
        }
    }

    companion object {
        /**
         * Construct a [WorkingSetSection], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            sectionId: String,
            title: String = "",
            order: Int = 0,
            role: WorkingSetRole = WorkingSetRole.TARGET,
            content: String = "",
        ): WorkingSetSection = WorkingSetSection(
            sectionId = sectionId.stripModelWhitespace(),
            title = title.stripModelWhitespace(),
            order = order,
            role = role,
            content = content.stripModelWhitespace(),
            )
    }
}

/**
 * The bounded context used to edit one section of a book.
 *
 * A working set is the whole of the book that the language model ever sees:
 * the target section, its immediate neighbours, the passages retrieval
 * considers relevant, and the running brief. Everything else stays on disk.
 */
@kotlin.ExposedCopyVisibility
data class WorkingSet private constructor(
    val bookId: String,
    val targetSectionId: String,
    val sections: List<WorkingSetSection> = emptyList(),
    val passages: List<SourceChunk> = emptyList(),
    val brief: String = "",
    val contextChars: Int = 0,
) {
    init {
        if (bookId.isEmpty()) {
            throw ValidationError("book_id must not be empty", field = "book_id")
        }
        if (targetSectionId.isEmpty()) {
            throw ValidationError("target_section_id must not be empty", field = "target_section_id")
        }
        if (contextChars < 0) {
            throw ValidationError("context_chars must not be negative", field = "context_chars")
        }
    }

    companion object {
        /**
         * Construct a [WorkingSet], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            bookId: String,
            targetSectionId: String,
            sections: List<WorkingSetSection> = emptyList(),
            passages: List<SourceChunk> = emptyList(),
            brief: String = "",
            contextChars: Int = 0,
        ): WorkingSet = WorkingSet(
            bookId = bookId.stripModelWhitespace(),
            targetSectionId = targetSectionId.stripModelWhitespace(),
            sections = sections,
            passages = passages,
            brief = brief.stripModelWhitespace(),
            contextChars = contextChars,
            )
    }
}

/** A request to edit one section of a book in place. */
@kotlin.ExposedCopyVisibility
data class BookSectionEditRequest private constructor(
    val instruction: String,
    val bookId: String = "",
    val sectionId: String = "",
    val selectedText: String = "",
    val scope: EditScope = EditScope.PARAGRAPH,
    val paragraphIndex: Int = 0,
    val persist: Boolean = true,
) {
    init {
        if (instruction.isEmpty()) {
            throw ValidationError("instruction must not be empty", field = "instruction")
        }
        if (paragraphIndex < 0) {
            throw ValidationError("paragraph_index must not be negative", field = "paragraph_index")
        }
    }

    companion object {
        /**
         * Construct a [BookSectionEditRequest], the way Pydantic constructs the Python model.
         *
         * The string fields are stripped of their surrounding whitespace
         * before the constraints are checked, so a field that is nothing but
         * whitespace is empty -- and empty is refused.
         *
         * `copy()` deliberately does not strip: Pydantic's `model_copy()` does
         * not re-validate either.
         */
        operator fun invoke(
            instruction: String,
            bookId: String = "",
            sectionId: String = "",
            selectedText: String = "",
            scope: EditScope = EditScope.PARAGRAPH,
            paragraphIndex: Int = 0,
            persist: Boolean = true,
        ): BookSectionEditRequest = BookSectionEditRequest(
            instruction = instruction.stripModelWhitespace(),
            bookId = bookId.stripModelWhitespace(),
            sectionId = sectionId.stripModelWhitespace(),
            selectedText = selectedText.stripModelWhitespace(),
            scope = scope,
            paragraphIndex = paragraphIndex,
            persist = persist,
            )
    }
}

/**
 * The operator's own text for one section of a book.
 *
 * The whitespace is deliberately not stripped: the text is the operator's, and
 * a word processor stores what was typed.
 */
data class BookSectionSaveRequest(
    val content: String,
)

/** What the server stored for one section of a book. */
data class BookSectionSaveResult(
    val sectionId: String,
    val title: String = "",
    val content: String = "",
    val wordCount: Int = 0,
    val charCount: Int = 0,
    val chunkCount: Int = 0,
) {
    init {
        if (wordCount < 0) {
            throw ValidationError("word_count must not be negative", field = "word_count")
        }
        if (charCount < 0) {
            throw ValidationError("char_count must not be negative", field = "char_count")
        }
        if (chunkCount < 0) {
            throw ValidationError("chunk_count must not be negative", field = "chunk_count")
        }
    }
}

private fun requireScore(value: Double, field: String) {
    if (value.isNaN() || value < 0.0 || value > 1.0) {
        throw ValidationError("$field must be between 0.0 and 1.0", field = field, value = value)
    }
}
