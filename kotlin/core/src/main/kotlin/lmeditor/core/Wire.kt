package lmeditor.core

/**
 * The wire form of the domain models.
 *
 * These converters are the single definition of how a Kotlin model travels
 * across the NDJSON seam: snake_case keys, the same names the Python tier's
 * Pydantic models use, and enums as their `wire` string.
 *
 * The Python tier validates a Kotlin result with its own model, so a key that
 * is wrong here is a validation error there -- which is the point. There is
 * one vocabulary, and it is checked from both ends.
 *
 * The conversions are written out by hand. The tier has no reflection and no
 * serialization framework, because its runtime dependency set is empty; a
 * hand-written converter is also the only one that can be *read*.
 */

/** The wire form of a [SourceChunk]. */
fun SourceChunk.toWireMap(): Map<String, Any?> = linkedMapOf(
    "chunk_id" to chunkId,
    "document_id" to documentId,
    "text" to text,
    "char_start" to charStart,
    "char_end" to charEnd,
    "byte_start" to byteStart,
    "byte_end" to byteEnd,
    "token_count" to tokenCount,
    "start_char" to startChar,
    "end_char" to endChar,
    "metadata" to metadata,
)

/** The wire form of a [Claim]. */
fun Claim.toWireMap(): Map<String, Any?> = linkedMapOf(
    "claim_id" to claimId,
    "section_id" to sectionId,
    "text" to text,
    "char_start" to charStart,
    "char_end" to charEnd,
    "cited_chunk_ids" to citedChunkIds,
    "entailment_status" to entailmentStatus.wire,
    "entailment_score" to entailmentScore,
    "reasoning" to reasoning,
    "sentence_idx" to sentenceIdx,
    "premise_text" to premiseText,
)

/** The wire form of a [CitationLineage]. */
fun CitationLineage.toWireMap(): Map<String, Any?> = linkedMapOf(
    "lineage_id" to lineageId,
    "claim_id" to claimId,
    "chunk_id" to chunkId,
    "span_start" to spanStart,
    "span_end" to spanEnd,
    "exact_quote" to exactQuote,
    "confidence_score" to confidenceScore,
    "created_at" to createdAt,
)

/** The wire form of a [SectionDraft]. */
fun SectionDraft.toWireMap(): Map<String, Any?> = linkedMapOf(
    "section_id" to sectionId,
    "title" to title,
    "content" to content,
    "order" to order,
    "citations" to citations.map { it.toWireMap() },
    "claims" to claims.map { it.toWireMap() },
    "is_verified" to isVerified,
    "status" to status.wire,
    "revision_count" to revisionCount,
    "critique" to critique,
    "cited_chunk_ids" to citedChunkIds,
)

/** The wire form of a [DocumentDraft]. */
fun DocumentDraft.toWireMap(): Map<String, Any?> = linkedMapOf(
    "document_id" to documentId,
    "title" to title,
    "sections" to sections.map { it.toWireMap() },
    "status" to status.wire,
)

/** The wire form of an [InlineCitation]. */
fun InlineCitation.toWireMap(): Map<String, Any?> = linkedMapOf(
    "citation_id" to citationId,
    "chunk_id" to chunkId,
    "source_doc" to sourceDoc,
    "span_start" to spanStart,
    "span_end" to spanEnd,
    "prefix_text" to prefixText,
)

/** The wire form of a [VerificationScorecard]. */
fun VerificationScorecard.toWireMap(): Map<String, Any?> = linkedMapOf(
    "precision" to precision,
    "recall" to recall,
    "f_beta" to fBeta,
    "beta" to beta,
    "total_claims" to totalClaims,
    "supported_claims" to supportedClaims,
    "ungrounded_claims" to ungroundedClaims,
    "contradicted_claims" to contradictedClaims,
    "neutral_claims" to neutralClaims,
    "details" to details.map { it.toWireMap() },
    "evaluator" to evaluator,
)

/** The wire form of a [VerificationReport]. */
fun VerificationReport.toWireMap(): Map<String, Any?> = linkedMapOf(
    "report_id" to reportId,
    "section_id" to sectionId,
    "total_claims" to totalClaims,
    "entailed_claims" to entailedClaims,
    "contradicted_claims" to contradictedClaims,
    "ungrounded_claims" to ungroundedClaims,
    "precision" to precision,
    "claims" to claims.map { it.toWireMap() },
    "status" to status.wire,
    "all_passed" to allPassed,
    "summary" to summary,
    "evaluator" to evaluator,
)

/** The wire form of an [OutlineNode], children included. */
fun OutlineNode.toWireMap(): Map<String, Any?> = linkedMapOf(
    "node_id" to nodeId,
    "title" to title,
    "section_id" to resolvedSectionId,
    "depth" to depth,
    "description" to description,
    "assigned_chunk_ids" to assignedChunkIds,
    "children" to children.map { it.toWireMap() },
    "order" to order,
    "subheadings" to subheadings,
)

/** The wire form of an [OutlineNodeInput]. */
fun OutlineNodeInput.toWireMap(): Map<String, Any?> = linkedMapOf(
    "title" to title,
    "description" to description,
    "children" to children.map { it.toWireMap() },
)

/** The wire form of a [TopicOutline]. */
fun TopicOutline.toWireMap(): Map<String, Any?> = linkedMapOf(
    "topic" to topic,
    "nodes" to nodes.map { it.toWireMap() },
)

/** The wire form of a [Perspective]. */
fun Perspective.toWireMap(): Map<String, Any?> = linkedMapOf(
    "perspective_id" to resolvedId,
    "id" to resolvedId,
    "role" to role.wire,
    "title" to resolvedTitle,
    "name" to resolvedTitle,
    "description" to description,
    "domain" to domain,
    "focus_areas" to focusAreas,
    "prompt_directives" to promptDirectives,
    "guiding_questions" to guidingQuestions,
    "key_inquiries" to keyInquiries,
)

/** The wire form of a [WorkflowEvent]. */
fun WorkflowEvent.toWireMap(): Map<String, Any?> = linkedMapOf(
    "event_type" to eventType.wire,
    "step" to step.wire,
    "doc_id" to docId,
    "data" to data,
)

/** The wire form of a [BibliographicRecord]. */
fun BibliographicRecord.toWireMap(): Map<String, Any?> = linkedMapOf(
    "title" to title,
    "authors" to authors,
    "year" to year,
    "publication_date" to publicationDate,
    "container_title" to containerTitle,
    "volume" to volume,
    "issue" to issue,
    "pages" to pages,
    "doi" to doi,
    "url" to url,
    "type" to type,
    "resolver" to resolver.wire,
)

/** The wire form of a [Source]. */
fun Source.toWireMap(): Map<String, Any?> = linkedMapOf(
    "id" to id,
    "kind" to kind.wire,
    "origin" to origin,
    "title" to title,
    "bibliography" to bibliography?.toWireMap(),
    "page_count" to pageCount,
    "byte_size" to byteSize,
    "added_at" to addedAt,
    "omissions" to omissions,
)

/** The wire form of a [SourceIngestResult]. */
fun SourceIngestResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "source" to source.toWireMap(),
    "chunks" to chunks.map { it.toWireMap() },
    "omissions" to omissions,
)

/** The wire form of a [BookSection]. */
fun BookSection.toWireMap(): Map<String, Any?> = linkedMapOf(
    "section_id" to sectionId,
    "title" to title,
    "order" to order,
    "word_count" to wordCount,
    "char_count" to charCount,
    "chunk_ids" to chunkIds,
)

/** The wire form of a [BookManifest]. */
fun BookManifest.toWireMap(): Map<String, Any?> = linkedMapOf(
    "book_id" to bookId,
    "title" to title,
    "section_count" to sectionCount,
    "chunk_count" to chunkCount,
    "created_at" to createdAt,
    "modified_at" to modifiedAt,
    "sections" to sections.map { it.toWireMap() },
    "brief" to brief,
)

/** The wire form of a [WorkingSetSection]. */
fun WorkingSetSection.toWireMap(): Map<String, Any?> = linkedMapOf(
    "section_id" to sectionId,
    "title" to title,
    "order" to order,
    "role" to role.wire,
    "content" to content,
)

/** The wire form of a [WorkingSet]. */
fun WorkingSet.toWireMap(): Map<String, Any?> = linkedMapOf(
    "book_id" to bookId,
    "target_section_id" to targetSectionId,
    "sections" to sections.map { it.toWireMap() },
    "passages" to passages.map { it.toWireMap() },
    "brief" to brief,
    "context_chars" to contextChars,
)

/** The wire form of an [Evidence]. */
fun Evidence.toWireMap(): Map<String, Any?> = linkedMapOf(
    "source" to source.wire,
    "chunk_id" to chunkId,
    "url" to url,
    "title" to title,
    "excerpt" to excerpt,
    "score" to score,
)

/** The wire form of a [ClaimVerdict]. */
fun ClaimVerdict.toWireMap(): Map<String, Any?> = linkedMapOf(
    "claim_id" to claimId,
    "claim_text" to claimText,
    "status" to status.wire,
    "score" to score,
    "evidence" to evidence.map { it.toWireMap() },
    "reasoning" to reasoning,
)

/** The wire form of a [FactCheckReport]. */
fun FactCheckReport.toWireMap(): Map<String, Any?> = linkedMapOf(
    "doc_id" to docId,
    "text" to text,
    "verdict" to verdict.wire,
    "claims" to claims.map { it.toWireMap() },
    "scorecard" to scorecard?.toWireMap(),
    "web_searched" to webSearched,
    "summary" to summary,
)

/** The wire form of a [DialogueTurn]. */
fun DialogueTurn.toWireMap(): Map<String, Any?> = linkedMapOf(
    "turn_id" to turnId,
    "question" to question,
    "answer" to answer,
    "referenced_chunk_ids" to referencedChunkIds,
)

/** The wire form of a [DialogueSession]. */
fun DialogueSession.toWireMap(): Map<String, Any?> = linkedMapOf(
    "perspective_id" to perspectiveId,
    "turns" to turns.map { it.toWireMap() },
    "synthesized_takeaways" to synthesizedTakeaways,
)

/** The wire form of a [ResearchMemory]. */
fun ResearchMemory.toWireMap(): Map<String, Any?> = linkedMapOf(
    "topic" to topic,
    "sessions" to sessions.map { it.toWireMap() },
    "all_referenced_chunk_ids" to allReferencedChunkIds,
    "collected_takeaways" to collectedTakeaways,
)

/** The wire form of a [HITLReviewPayload]. */
fun HITLReviewPayload.toWireMap(): Map<String, Any?> = linkedMapOf(
    "doc_id" to docId,
    "approved" to approved,
    "feedback" to feedback,
    "modified_sections" to modifiedSections?.map { it.toWireMap() },
)

/** The wire form of an [InlineEditRequest]. */
fun InlineEditRequest.toWireMap(): Map<String, Any?> = linkedMapOf(
    "doc_id" to docId,
    "section_id" to sectionId,
    "instruction" to instruction,
    "selected_text" to selectedText,
    "scope" to scope.wire,
    "paragraph_index" to paragraphIndex,
    "persist" to persist,
)

/** The wire form of an [InlineEditResult]. */
fun InlineEditResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "doc_id" to docId,
    "section_id" to sectionId,
    "scope" to scope.wire,
    "original_text" to originalText,
    "revised_text" to revisedText,
    "applied" to applied,
    "reason" to reason,
    "cited_chunk_ids" to citedChunkIds,
    "claims" to claims.map { it.toWireMap() },
    "scorecard" to scorecard?.toWireMap(),
    "section" to section?.toWireMap(),
)

/** The wire form of a [SectionSaveRequest]. */
fun SectionSaveRequest.toWireMap(): Map<String, Any?> = linkedMapOf(
    "content" to content,
    "title" to title,
    "order" to order,
)

/** The wire form of a [SectionSaveResult]. */
fun SectionSaveResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "section_id" to sectionId,
    "title" to title,
    "content" to content,
    "status" to status.wire,
    "is_verified" to isVerified,
    "scorecard" to scorecard?.toWireMap(),
)

/** The wire form of a [BookSectionSaveRequest]. */
fun BookSectionSaveRequest.toWireMap(): Map<String, Any?> = linkedMapOf(
    "content" to content,
)

/** The wire form of a [BookSectionSaveResult]. */
fun BookSectionSaveResult.toWireMap(): Map<String, Any?> = linkedMapOf(
    "section_id" to sectionId,
    "title" to title,
    "content" to content,
    "word_count" to wordCount,
    "char_count" to charCount,
    "chunk_count" to chunkCount,
)

/** The wire form of a [BookSectionEditRequest]. */
fun BookSectionEditRequest.toWireMap(): Map<String, Any?> = linkedMapOf(
    "book_id" to bookId,
    "section_id" to sectionId,
    "instruction" to instruction,
    "selected_text" to selectedText,
    "scope" to scope.wire,
    "paragraph_index" to paragraphIndex,
    "persist" to persist,
)
