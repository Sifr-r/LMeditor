package lmeditor.core

import lmeditor.core.json.jsonArray
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList

/**
 * The reading half of the wire vocabulary.
 *
 * These are the counterparts of the `toWireMap` functions in `Wire.kt`: the
 * same snake_case keys, read back into the domain models. The Python tier
 * validates a Kotlin result with its own Pydantic model, so a decoder that
 * disagrees with an encoder here shows up as a validation error on the other
 * side of the seam rather than as a silent difference.
 *
 * Every decoder validates: a field that the model requires and the payload
 * does not carry is a `ValidationError`, not a default.
 */

/** Read a [SourceChunk] from the wire. */
fun sourceChunkFromWire(map: Map<String, Any?>): SourceChunk = SourceChunk(
    chunkId = map.jsonString("chunk_id"),
    documentId = map.jsonString("document_id"),
    text = map.jsonString("text"),
    charStart = map.jsonInt("char_start"),
    charEnd = map.jsonInt("char_end"),
    byteStart = map.jsonInt("byte_start"),
    byteEnd = map.jsonInt("byte_end"),
    tokenCount = map.jsonInt("token_count"),
    startChar = map.jsonInt("start_char", map.jsonInt("char_start")),
    endChar = map.jsonInt("end_char", map.jsonInt("char_end")),
    metadata = map.jsonObject("metadata"),
)

/** Read a [Claim] from the wire. */
fun claimFromWire(map: Map<String, Any?>): Claim = Claim(
    claimId = map.jsonString("claim_id"),
    sectionId = map.jsonString("section_id"),
    text = map.jsonString("text"),
    charStart = map.jsonInt("char_start"),
    charEnd = map.jsonInt("char_end"),
    citedChunkIds = map.jsonStringList("cited_chunk_ids"),
    entailmentStatus = EntailmentStatus.fromWire(
        map.jsonString("entailment_status", EntailmentStatus.NEUTRAL.wire),
    ),
    entailmentScore = map.jsonDouble("entailment_score"),
    reasoning = map["reasoning"]?.toString(),
    sentenceIdx = map.jsonInt("sentence_idx"),
    premiseText = map["premise_text"]?.toString(),
)

/** Read a [CitationLineage] from the wire. */
fun citationLineageFromWire(map: Map<String, Any?>): CitationLineage = CitationLineage(
    lineageId = map.jsonString("lineage_id"),
    claimId = map.jsonString("claim_id"),
    chunkId = map.jsonString("chunk_id"),
    spanStart = map.jsonInt("span_start"),
    spanEnd = map.jsonInt("span_end"),
    exactQuote = map["exact_quote"]?.toString(),
    confidenceScore = map.jsonDouble("confidence_score", 1.0),
    createdAt = map.jsonString("created_at"),
)

/** Read a [SectionDraft] from the wire. */
fun sectionDraftFromWire(map: Map<String, Any?>): SectionDraft = SectionDraft(
    sectionId = map.jsonString("section_id"),
    title = map.jsonString("title"),
    content = map.jsonString("content"),
    order = map.jsonInt("order"),
    citations = map.jsonObjectList("citations").map { citationLineageFromWire(it) },
    claims = map.jsonObjectList("claims").map { claimFromWire(it) },
    isVerified = map.jsonBool("is_verified"),
    status = SectionStatus.fromWire(map.jsonString("status", SectionStatus.PENDING.wire)),
    revisionCount = map.jsonInt("revision_count"),
    critique = map["critique"]?.toString(),
    citedChunkIds = map.jsonStringList("cited_chunk_ids"),
)

/** Read a [DocumentDraft] from the wire. */
fun documentDraftFromWire(map: Map<String, Any?>): DocumentDraft = DocumentDraft(
    documentId = map.jsonString("document_id"),
    title = map.jsonString("title"),
    sections = map.jsonObjectList("sections").map { sectionDraftFromWire(it) },
    status = DocumentStatus.fromWire(map.jsonString("status", DocumentStatus.DRAFTING.wire)),
)

/** Read a [VerificationScorecard] from the wire. */
fun verificationScorecardFromWire(map: Map<String, Any?>): VerificationScorecard =
    VerificationScorecard(
        precision = map.jsonDouble("precision"),
        recall = map.jsonDouble("recall"),
        fBeta = map.jsonDouble("f_beta"),
        beta = map.jsonDouble("beta", 0.5),
        totalClaims = map.jsonInt("total_claims"),
        supportedClaims = map.jsonInt("supported_claims"),
        ungroundedClaims = map.jsonInt("ungrounded_claims"),
        contradictedClaims = map.jsonInt("contradicted_claims"),
        neutralClaims = map.jsonInt("neutral_claims"),
        details = map.jsonObjectList("details").map { claimFromWire(it) },
        evaluator = map.jsonString("evaluator"),
    )

/** Read a [VerificationReport] from the wire. */
fun verificationReportFromWire(map: Map<String, Any?>): VerificationReport = VerificationReport(
    reportId = map.jsonString("report_id"),
    sectionId = map.jsonString("section_id"),
    totalClaims = map.jsonInt("total_claims"),
    entailedClaims = map.jsonInt("entailed_claims"),
    contradictedClaims = map.jsonInt("contradicted_claims"),
    ungroundedClaims = map.jsonInt("ungrounded_claims"),
    precision = map.jsonDouble("precision"),
    claims = map.jsonObjectList("claims").map { claimFromWire(it) },
    status = EntailmentStatus.fromWire(map.jsonString("status", EntailmentStatus.NEUTRAL.wire)),
    allPassed = map.jsonBool("all_passed"),
    summary = map.jsonString("summary"),
    evaluator = map.jsonString("evaluator"),
)

/** Read an [OutlineNode] from the wire, children included. */
fun outlineNodeFromWire(map: Map<String, Any?>): OutlineNode = OutlineNode(
    nodeId = map.jsonString("node_id"),
    title = map.jsonString("title"),
    sectionId = map.jsonString("section_id"),
    depth = map.jsonInt("depth"),
    description = map.jsonString("description"),
    assignedChunkIds = map.jsonStringList("assigned_chunk_ids"),
    children = map.jsonObjectList("children").map { outlineNodeFromWire(it) },
    order = map.jsonInt("order"),
    subheadings = map.jsonStringList("subheadings"),
)

/** Read an [OutlineNodeInput] from the wire. */
fun outlineNodeInputFromWire(map: Map<String, Any?>): OutlineNodeInput = OutlineNodeInput(
    title = map.jsonString("title"),
    description = map.jsonString("description"),
    children = map.jsonObjectList("children").map { outlineNodeInputFromWire(it) },
)

/** Read a [TopicOutline] from the wire. */
fun topicOutlineFromWire(map: Map<String, Any?>): TopicOutline = TopicOutline(
    topic = map.jsonString("topic"),
    nodes = map.jsonObjectList("nodes").map { outlineNodeFromWire(it) },
)

/** Read a [Perspective] from the wire. */
fun perspectiveFromWire(map: Map<String, Any?>): Perspective = Perspective(
    perspectiveId = map.jsonString("perspective_id", map.jsonString("id")),
    id = map.jsonString("id", map.jsonString("perspective_id")),
    role = PerspectiveRole.fromWire(map.jsonString("role", PerspectiveRole.DOMAIN_EXPERT.wire)),
    title = map.jsonString("title", map.jsonString("name")),
    name = map.jsonString("name", map.jsonString("title")),
    description = map.jsonString("description"),
    domain = map.jsonString("domain"),
    focusAreas = map.jsonStringList("focus_areas"),
    promptDirectives = map.jsonStringList("prompt_directives"),
    guidingQuestions = map.jsonStringList("guiding_questions"),
    keyInquiries = map.jsonStringList("key_inquiries"),
)

/** Read a [WorkflowEvent] from the wire. */
fun workflowEventFromWire(map: Map<String, Any?>): WorkflowEvent = WorkflowEvent(
    eventType = EventType.fromWire(map.jsonString("event_type")),
    step = WorkflowStep.fromWire(map.jsonString("step")),
    docId = map.jsonString("doc_id"),
    data = map.jsonObject("data"),
)

/** Read a [BibliographicRecord] from the wire. */
fun bibliographicRecordFromWire(map: Map<String, Any?>): BibliographicRecord {
    val year = map["year"]
    return BibliographicRecord(
        title = map.jsonString("title"),
        authors = map.jsonStringList("authors"),
        year = if (year is Number) year.toInt() else null,
        publicationDate = map["publication_date"]?.toString(),
        containerTitle = map["container_title"]?.toString(),
        volume = map["volume"]?.toString(),
        issue = map["issue"]?.toString(),
        pages = map["pages"]?.toString(),
        doi = map["doi"]?.toString(),
        url = map["url"]?.toString(),
        type = map["type"]?.toString(),
        resolver = BibliographyResolverKind.fromWire(
            map.jsonString("resolver", BibliographyResolverKind.NONE.wire),
        ),
    )
}

/** Read a [Source] from the wire. */
fun sourceFromWire(map: Map<String, Any?>): Source {
    val pageCount = map["page_count"]
    val byteSize = map["byte_size"]
    val bibliography = map["bibliography"]
    return Source(
        id = map.jsonString("id"),
        kind = SourceKind.fromWire(map.jsonString("kind")),
        origin = map.jsonString("origin"),
        title = map.jsonString("title"),
        bibliography = if (bibliography is Map<*, *>) {
            @Suppress("UNCHECKED_CAST")
            bibliographicRecordFromWire(bibliography as Map<String, Any?>)
        } else {
            null
        },
        pageCount = if (pageCount is Number) pageCount.toInt() else null,
        byteSize = if (byteSize is Number) byteSize.toInt() else null,
        addedAt = map.jsonString("added_at"),
        omissions = map.jsonStringList("omissions"),
    )
}

/** Read a [BookSection] from the wire. */
fun bookSectionFromWire(map: Map<String, Any?>): BookSection = BookSection(
    sectionId = map.jsonString("section_id"),
    title = map.jsonString("title"),
    order = map.jsonInt("order"),
    wordCount = map.jsonInt("word_count"),
    charCount = map.jsonInt("char_count"),
    chunkIds = map.jsonStringList("chunk_ids"),
)

/** Read a [BookManifest] from the wire. */
fun bookManifestFromWire(map: Map<String, Any?>): BookManifest = BookManifest(
    bookId = map.jsonString("book_id"),
    title = map.jsonString("title"),
    sectionCount = map.jsonInt("section_count"),
    chunkCount = map.jsonInt("chunk_count"),
    createdAt = map.jsonString("created_at"),
    modifiedAt = map.jsonString("modified_at"),
    sections = map.jsonObjectList("sections").map { bookSectionFromWire(it) },
    brief = map.jsonString("brief"),
)

/** Read a [BookSectionSaveResult] from the wire. */
fun bookSectionSaveResultFromWire(map: Map<String, Any?>): BookSectionSaveResult = BookSectionSaveResult(
    sectionId = map.jsonString("section_id"),
    title = map.jsonString("title"),
    content = map.jsonString("content"),
    wordCount = map.jsonInt("word_count"),
    charCount = map.jsonInt("char_count"),
    chunkCount = map.jsonInt("chunk_count"),
)

/** Read a [VaultRenameResult] from the wire. */
fun vaultRenameResultFromWire(map: Map<String, Any?>): VaultRenameResult = VaultRenameResult(
    fromId = map.jsonString("from_id"),
    toId = map.jsonString("to_id"),
    updatedLinks = map.jsonInt("updated_links"),
)

/** Read a [VaultSummary] from the wire. */
fun vaultSummaryFromWire(map: Map<String, Any?>): VaultSummary = VaultSummary(
    root = map.jsonString("root"),
    name = map.jsonString("name"),
    noteCount = map.jsonInt("note_count"),
)

/** Read a [VaultNote] from the wire. */
fun vaultNoteFromWire(map: Map<String, Any?>): VaultNote = VaultNote(
    noteId = map.jsonString("note_id"),
    title = map.jsonString("title"),
    path = map.jsonString("path"),
    content = map.jsonString("content"),
    tags = map.jsonStringList("tags"),
    links = map.jsonStringList("links"),
    backlinks = map.jsonStringList("backlinks"),
    createdAt = map.jsonString("created_at"),
    modifiedAt = map.jsonString("modified_at"),
    wordCount = map.jsonInt("word_count"),
)

/** Read a [VaultNoteSummary] from the wire. */
fun vaultNoteSummaryFromWire(map: Map<String, Any?>): VaultNoteSummary = VaultNoteSummary(
    noteId = map.jsonString("note_id"),
    title = map.jsonString("title"),
    path = map.jsonString("path"),
    tags = map.jsonStringList("tags"),
    wordCount = map.jsonInt("word_count"),
    modifiedAt = map.jsonString("modified_at"),
)

/** Read a [VaultSearchHit] from the wire. */
fun vaultSearchHitFromWire(map: Map<String, Any?>): VaultSearchHit = VaultSearchHit(
    noteId = map.jsonString("note_id"),
    title = map.jsonString("title"),
    path = map.jsonString("path"),
    score = map.jsonInt("score"),
    matchedTerms = map.jsonStringList("matched_terms"),
)

/** Read a [VaultTag] from the wire. */
fun vaultTagFromWire(map: Map<String, Any?>): VaultTag = VaultTag(
    tag = map.jsonString("tag"),
    count = map.jsonInt("count"),
)

/** Read a [NotebookLibrary] from the wire. */
fun notebookLibraryFromWire(map: Map<String, Any?>): NotebookLibrary = NotebookLibrary(
    root = map.jsonString("root"),
    name = map.jsonString("name"),
    notebookCount = map.jsonInt("notebook_count"),
    notebooks = map.jsonObjectList("notebooks").map { notebookSummaryFromWire(it) },
)

/** Read a [Notebook] from the wire. */
fun notebookFromWire(map: Map<String, Any?>): Notebook = Notebook(
    notebookId = map.jsonString("notebook_id"),
    title = map.jsonString("title"),
    description = map.jsonString("description"),
    sourceIds = map.jsonStringList("source_ids"),
    noteIds = map.jsonStringList("note_ids"),
    createdAt = map.jsonString("created_at"),
    modifiedAt = map.jsonString("modified_at"),
)

/** Read a [NotebookSummary] from the wire. */
fun notebookSummaryFromWire(map: Map<String, Any?>): NotebookSummary = NotebookSummary(
    notebookId = map.jsonString("notebook_id"),
    title = map.jsonString("title"),
    description = map.jsonString("description"),
    sourceCount = map.jsonInt("source_count"),
    noteCount = map.jsonInt("note_count"),
    modifiedAt = map.jsonString("modified_at"),
)

/** Read a [NotebookCitation] from the wire. */
fun notebookCitationFromWire(map: Map<String, Any?>): NotebookCitation = NotebookCitation(
    chunkId = map.jsonString("chunk_id"),
    sourceId = map.jsonString("source_id"),
    title = map.jsonString("title"),
    quote = map.jsonString("quote"),
)

/** Read a [NotebookAnswer] from the wire. */
fun notebookAnswerFromWire(map: Map<String, Any?>): NotebookAnswer = NotebookAnswer(
    answer = map.jsonString("answer"),
    answered = map.jsonBool("answered"),
    citations = map.jsonObjectList("citations").map { notebookCitationFromWire(it) },
)

/** Read a [WorkingSetSection] from the wire. */fun workingSetSectionFromWire(map: Map<String, Any?>): WorkingSetSection = WorkingSetSection(
    sectionId = map.jsonString("section_id"),
    title = map.jsonString("title"),
    order = map.jsonInt("order"),
    role = WorkingSetRole.fromWire(map.jsonString("role", WorkingSetRole.TARGET.wire)),
    content = map.jsonString("content"),
)

/** Read a [WorkingSet] from the wire. */
fun workingSetFromWire(map: Map<String, Any?>): WorkingSet = WorkingSet(
    bookId = map.jsonString("book_id"),
    targetSectionId = map.jsonString("target_section_id"),
    sections = map.jsonObjectList("sections").map { workingSetSectionFromWire(it) },
    passages = map.jsonObjectList("passages").map { sourceChunkFromWire(it) },
    brief = map.jsonString("brief"),
    contextChars = map.jsonInt("context_chars"),
)

/** Read an [Evidence] from the wire. */
fun evidenceFromWire(map: Map<String, Any?>): Evidence = Evidence(
    source = EvidenceSource.fromWire(map.jsonString("source", EvidenceSource.CORPUS.wire)),
    chunkId = map.jsonString("chunk_id"),
    url = map.jsonString("url"),
    title = map.jsonString("title"),
    excerpt = map.jsonString("excerpt"),
    score = map.jsonDouble("score"),
)

/** Read a [ClaimVerdict] from the wire. */
fun claimVerdictFromWire(map: Map<String, Any?>): ClaimVerdict = ClaimVerdict(
    claimId = map.jsonString("claim_id"),
    status = EntailmentStatus.fromWire(map.jsonString("status", EntailmentStatus.NEUTRAL.wire)),
    claimText = map.jsonString("claim_text"),
    score = map.jsonDouble("score"),
    evidence = map.jsonObjectList("evidence").map { evidenceFromWire(it) },
    reasoning = map.jsonString("reasoning"),
)

/** Read a [HITLReviewPayload] from the wire. */
fun hitlReviewPayloadFromWire(map: Map<String, Any?>): HITLReviewPayload {
    val modified = map["modified_sections"]
    return HITLReviewPayload(
        approved = map.jsonBool("approved"),
        docId = map.jsonString("doc_id"),
        feedback = map["feedback"]?.toString(),
        modifiedSections = if (modified is List<*>) {
            modified.filterIsInstance<Map<String, Any?>>().map { sectionDraftFromWire(it) }
        } else {
            null
        },
    )
}

/** Read an [InlineEditRequest] from the wire. */
fun inlineEditRequestFromWire(map: Map<String, Any?>): InlineEditRequest = InlineEditRequest(
    docId = map.jsonString("doc_id"),
    sectionId = map.jsonString("section_id"),
    instruction = map.jsonString("instruction"),
    selectedText = map.jsonString("selected_text"),
    scope = EditScope.fromWire(map.jsonString("scope", EditScope.PARAGRAPH.wire)),
    paragraphIndex = map.jsonInt("paragraph_index"),
    persist = map.jsonBool("persist", true),
)

/** Read an [InlineEditResult] from the wire. */
fun inlineEditResultFromWire(map: Map<String, Any?>): InlineEditResult = InlineEditResult(
    docId = map.jsonString("doc_id"),
    sectionId = map.jsonString("section_id"),
    scope = EditScope.fromWire(map.jsonString("scope", EditScope.PARAGRAPH.wire)),
    originalText = map.jsonString("original_text"),
    revisedText = map.jsonString("revised_text"),
    applied = map.jsonBool("applied"),
    reason = map.jsonString("reason"),
    citedChunkIds = map.jsonStringList("cited_chunk_ids"),
    claims = map.jsonObjectList("claims").map { claimFromWire(it) },
    scorecard = map["scorecard"]?.let {
        @Suppress("UNCHECKED_CAST")
        verificationScorecardFromWire(it as Map<String, Any?>)
    },
    section = map["section"]?.let {
        @Suppress("UNCHECKED_CAST")
        sectionDraftFromWire(it as Map<String, Any?>)
    },
)

/** Read a [SectionSaveRequest] from the wire. */
fun sectionSaveRequestFromWire(map: Map<String, Any?>): SectionSaveRequest = SectionSaveRequest(
    content = map.jsonString("content"),
    title = map["title"]?.toString(),
    order = (map["order"] as? Number)?.toInt(),
)

/** Read a [FactCheckRequest] from the wire. */
fun factCheckRequestFromWire(map: Map<String, Any?>): FactCheckRequest = FactCheckRequest(
    docId = map.jsonString("doc_id"),
    text = map.jsonString("text"),
    sectionId = map.jsonString("section_id"),
    includeWeb = map.jsonBool("include_web"),
    maxEvidence = map.jsonInt("max_evidence", 5),
)

/** Read a [BookSectionEditRequest] from the wire. */
fun bookSectionEditRequestFromWire(map: Map<String, Any?>): BookSectionEditRequest =
    BookSectionEditRequest(
        bookId = map.jsonString("book_id"),
        sectionId = map.jsonString("section_id"),
        instruction = map.jsonString("instruction"),
        selectedText = map.jsonString("selected_text"),
        scope = EditScope.fromWire(map.jsonString("scope", EditScope.PARAGRAPH.wire)),
        paragraphIndex = map.jsonInt("paragraph_index"),
        persist = map.jsonBool("persist", true),
    )

/** Read an [InlineCitation] from the wire. */
fun inlineCitationFromWire(map: Map<String, Any?>): InlineCitation = InlineCitation(
    citationId = map.jsonString("citation_id"),
    chunkId = map.jsonString("chunk_id"),
    sourceDoc = map.jsonString("source_doc"),
    spanStart = map.jsonInt("span_start"),
    spanEnd = map.jsonInt("span_end"),
    prefixText = map.jsonString("prefix_text"),
)

/** Read a [DialogueTurn] from the wire. */
fun dialogueTurnFromWire(map: Map<String, Any?>): DialogueTurn = DialogueTurn(
    turnId = map.jsonString("turn_id"),
    question = map.jsonString("question"),
    answer = map.jsonString("answer"),
    referencedChunkIds = map.jsonStringList("referenced_chunk_ids"),
)

/** Read a [DialogueSession] from the wire. */
fun dialogueSessionFromWire(map: Map<String, Any?>): DialogueSession = DialogueSession(
    perspectiveId = map.jsonString("perspective_id"),
    turns = map.jsonObjectList("turns").map { dialogueTurnFromWire(it) },
    synthesizedTakeaways = map.jsonStringList("synthesized_takeaways"),
)

/** Read a [ResearchMemory] from the wire. */
fun researchMemoryFromWire(map: Map<String, Any?>): ResearchMemory = ResearchMemory(
    topic = map.jsonString("topic"),
    sessions = map.jsonObjectList("sessions").map { dialogueSessionFromWire(it) },
    allReferencedChunkIds = map.jsonStringList("all_referenced_chunk_ids"),
    collectedTakeaways = map.jsonStringList("collected_takeaways"),
)

/** Read a list of strings from a wire array field. */
fun stringListFromWire(map: Map<String, Any?>, key: String): List<String> =
    map.jsonArray(key).mapNotNull { it?.toString() }
