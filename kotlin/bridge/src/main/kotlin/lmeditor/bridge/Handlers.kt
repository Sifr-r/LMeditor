package lmeditor.bridge

import lmeditor.core.Citations
import lmeditor.core.EntailmentStatus
import lmeditor.core.InlineEditRejectedError
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.SectionNotFoundError
import lmeditor.core.Sections
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError
import lmeditor.core.claimFromWire
import lmeditor.core.claimVerdictFromWire
import lmeditor.core.inlineEditRequestFromWire
import lmeditor.core.json.jsonBool
import lmeditor.core.json.jsonDouble
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.core.outlineNodeInputFromWire
import lmeditor.core.perspectiveFromWire
import lmeditor.core.researchMemoryFromWire
import lmeditor.core.sectionDraftFromWire
import lmeditor.core.sourceChunkFromWire
import lmeditor.core.sourceFromWire
import lmeditor.core.toWireMap
import lmeditor.discovery.OutlineBuilder
import lmeditor.discovery.PerspectiveGenerator
import lmeditor.editing.InlineEditor
import lmeditor.editing.RewriteFn
import lmeditor.editing.validateRevision
import lmeditor.factcheck.FactCheckReportBuilder
import lmeditor.library.BookStore
import lmeditor.library.PassageIndex
import lmeditor.library.WorkingSetBuilder
import lmeditor.library.buildBookDocument
import lmeditor.library.buildBrief
import lmeditor.library.importSections
import lmeditor.library.importWordDocument
import lmeditor.library.mergeBrief
import lmeditor.pdf.PdfDocument
import lmeditor.pdf.PdfMetadata
import lmeditor.pdf.PdfText
import lmeditor.text.CitationChecker
import lmeditor.text.CitationLineageEngine
import lmeditor.text.ClaimLineage
import lmeditor.text.DEFAULT_BETA
import lmeditor.text.DEFAULT_F_BETA_THRESHOLD
import lmeditor.text.DEFAULT_OVERLAP_SIZE
import lmeditor.text.DEFAULT_WINDOW_SIZE
import lmeditor.text.Indexer
import lmeditor.text.NamedNliEvaluator
import lmeditor.text.RetrievalChunk
import lmeditor.text.SentenceAwareChunker
import lmeditor.text.SupportingText
import lmeditor.text.computeCitationPrecision
import lmeditor.text.computeCitationRecall
import lmeditor.text.computeGroundednessFBeta
import lmeditor.text.extractCitations
import lmeditor.text.extractClaims
import lmeditor.text.segmentSentences
import lmeditor.workflow.DEFAULT_TARGET_AUDIENCE
import lmeditor.workflow.HarnessState
import lmeditor.workflow.Routing
import lmeditor.workflow.VerificationRoute
import lmeditor.workflow.createInitialHarnessState
import lmeditor.workflow.toWireMap
import lmeditor.word.WordDocument
import lmeditor.word.WordReader
import lmeditor.word.WordWriter
import java.nio.file.Path
import java.util.Base64

/**
 * The method table of the seam.
 *
 * Each entry is a pure function of its parameters. The wire vocabulary is
 * documented in `kotlin/bridge/README.md`; the methods that carry domain
 * models use the converters in `lmeditor.core`.
 *
 * The seam carries *deterministic* work. Where a rule needs a model, a
 * network or a vector index, that side stays in Python and hands its result
 * here, and the arithmetic and the rule-checking happen here.
 */
object Handlers {

    /** Every method the bridge serves. */
    fun all(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> =
        mapOf<String, (Map<String, Any?>) -> Map<String, Any?>>(
        // --- core: the shared grammar -------------------------------------
        "core.citations.extract_chunk_ids" to { params ->
            mapOf("chunk_ids" to Citations.extractChunkIds(params.jsonString("text")))
        },
        "core.citations.iter_spans" to { params ->
            val spans = Citations.iterCitationSpans(params.jsonString("text")).map { span ->
                linkedMapOf<String, Any?>(
                    "chunk_id" to span.chunkId,
                    "start" to span.start,
                    "end" to span.end,
                )
            }
            mapOf("spans" to spans)
        },
        "core.citations.strip" to { params ->
            mapOf("text" to Citations.stripCitations(params.jsonString("text")))
        },
        "core.sections.merge_operator_text" to { params ->
            val merged = Sections.mergeOperatorText(
                draft = sectionDraftFromWire(params.jsonObject("draft")),
                content = params.jsonString("content"),
                title = params["title"]?.toString(),
                order = (params["order"] as? Number)?.toInt(),
            )
            mapOf("draft" to merged.toWireMap())
        },
        "core.schemas.source_chunk_create" to { params ->
            val chunk = SourceChunk.create(
                documentId = params.jsonString("document_id"),
                text = params.jsonString("text"),
                charStart = params.jsonInt("char_start"),
                charEnd = params.jsonInt("char_end"),
                byteStart = params.jsonInt("byte_start"),
                byteEnd = params.jsonInt("byte_end"),
                metadata = params.jsonObject("metadata"),
            )
            mapOf("chunk" to chunk.toWireMap())
        },

        // --- text: chunking, claims, lineage, auditing ---------------------
        "text.chunk" to { params ->
            // The window is a parameter of the chunking rule, not a setting of
            // the caller: a chunker configured with another window must answer
            // with that window's chunks, so it travels across the seam.
            val chunker = SentenceAwareChunker(
                windowSize = params.jsonInt("window_size", DEFAULT_WINDOW_SIZE),
                overlapSize = params.jsonInt("overlap_size", DEFAULT_OVERLAP_SIZE),
            )
            val chunks = chunker.chunk(
                text = params.jsonString("text"),
                documentId = params.jsonString("document_id"),
                metadata = params.jsonObject("metadata"),
            )
            mapOf("chunks" to chunks.map { it.toWireMap() })
        },
        "text.claims.extract" to { params ->
            val claims = extractClaims(
                text = params.jsonString("text"),
                sectionId = params.jsonString("section_id"),
            )
            mapOf("claims" to claims.map { it.toWireMap() })
        },
        "text.claims.segment_sentences" to { params ->
            mapOf("sentences" to segmentSentences(params.jsonString("text")))
        },
        "text.citations.extract" to { params ->
            val citations = extractCitations(params.jsonString("text"))
            mapOf("citations" to citations.map { it.toWireMap() })
        },
        "text.lineage.attribute_span" to { params ->
            // Two lineage rules live here, and they are told apart by the
            // method rather than by a field: a draft whose text is empty is
            // still a span to attribute, and choosing the mode from the text
            // would silently answer the wrong question.
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val store = chunks.associate { chunk -> chunk.chunkId to chunk.text }
            val lineage = CitationLineageEngine().attributeSpan(
                draftText = params.jsonString("draft_text"),
                spanStart = params.jsonInt("span_start"),
                spanEnd = params.jsonInt("span_end"),
                citedChunkId = params.jsonString("chunk_id"),
                claimId = params.jsonString("claim_id"),
                chunkStore = store,
                exactQuote = params["exact_quote"]?.toString(),
                createdAt = params.jsonString("created_at").ifEmpty {
                    java.time.Instant.now().toString()
                },
            )
            mapOf("lineages" to listOf(lineage.toWireMap()))
        },
        "text.lineage.attribute_claim" to { params ->
            // The composition of the claim-lineage rule: rank the corpus for
            // the claim's own text, find what each candidate and the claim say
            // in common, attribute the claim's span to the candidate, and
            // scale the confidence by the share of the claim the quote covers.
            //
            // The dense vectors are not the rule: when the Python tier has a
            // model, it sends the vectors it computed and the tier uses them
            // as they are; when it has none, the tier computes its own
            // term-frequency hash.
            val chunks = params.jsonObjectList("chunks").map { chunk ->
                RetrievalChunk(
                    chunkId = chunk.jsonString("chunk_id"),
                    text = chunk.jsonString("text"),
                )
            }
            val denseVectors = params["dense_vectors"]?.let { raw ->
                denseVectorMap(raw)
            }
            val queryVector = params["query_vector"]?.let { raw ->
                doubleList(raw, "query_vector")
            }
            if ((denseVectors == null) != (queryVector == null)) {
                throw ValidationError(
                    "dense_vectors and query_vector must be given together",
                    field = "dense_vectors",
                )
            }
            val lineages = ClaimLineage.attributeClaim(
                claimId = params.jsonString("claim_id"),
                claimText = params.jsonString("claim_text"),
                chunks = chunks,
                draftText = params["draft_text"]?.toString(),
                spanStart = params.jsonInt("span_start"),
                spanEnd = params["span_end"]?.let { params.jsonInt("span_end") },
                topK = params.jsonInt("top_k", ClaimLineage.DEFAULT_TOP_K),
                rrfK = params.jsonInt("rrf_k", Indexer.DEFAULT_RRF_K),
                alpha = params.jsonDouble("alpha", Indexer.DEFAULT_ALPHA),
                k1 = params.jsonDouble("k1", Indexer.DEFAULT_K1),
                b = params.jsonDouble("b", Indexer.DEFAULT_B),
                embeddingDim = params.jsonInt("embedding_dim", Indexer.DEFAULT_EMBEDDING_DIM),
                denseVectors = denseVectors,
                queryVector = queryVector,
            )
            mapOf("lineages" to lineages.map { it.toWireMap() })
        },
        "text.lineage.extract_from_draft" to { params ->
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val store = chunks.associate { chunk -> chunk.chunkId to chunk.text }
            val lineages = CitationLineageEngine().extractCitationsFromDraft(
                draftText = params.jsonString("draft_text"),
                chunkStore = store,
                createdAt = params.jsonString("created_at").ifEmpty {
                    java.time.Instant.now().toString()
                },
            )
            mapOf("lineages" to lineages.map { it.toWireMap() })
        },
        "text.lineage.supporting_text" to { params ->
            // The text a claim and a passage share is a rule: it decides what
            // a citation is allowed to quote, and how much of the claim the
            // quote accounts for.
            val supporting = SupportingText.supportingText(
                claimText = params.jsonString("claim_text"),
                chunkText = params.jsonString("chunk_text"),
            )
            mapOf(
                "quote" to supporting.quote,
                "coverage" to supporting.coverage,
            )
        },
        "text.retrieval.rank" to { params ->
            // The ranking is a rule. The dense vectors are not: when the
            // Python tier has a model, it sends the vectors it computed and
            // the tier uses them as they are; when it has none, the tier
            // computes its own term-frequency hash.
            val chunks = params.jsonObjectList("chunks").map { chunk ->
                RetrievalChunk(
                    chunkId = chunk.jsonString("chunk_id"),
                    text = chunk.jsonString("text"),
                )
            }
            val denseVectors = params["dense_vectors"]?.let { raw ->
                denseVectorMap(raw)
            }
            val queryVector = params["query_vector"]?.let { raw ->
                doubleList(raw, "query_vector")
            }
            if ((denseVectors == null) != (queryVector == null)) {
                throw ValidationError(
                    "dense_vectors and query_vector must be given together",
                    field = "dense_vectors",
                )
            }
            val ranked = Indexer.rank(
                chunks = chunks,
                query = params.jsonString("query"),
                mode = params.jsonString("mode", "hybrid"),
                topK = params.jsonInt("top_k", Indexer.DEFAULT_TOP_K),
                rrfK = params.jsonInt("rrf_k", Indexer.DEFAULT_RRF_K),
                alpha = params.jsonDouble("alpha", Indexer.DEFAULT_ALPHA),
                k1 = params.jsonDouble("k1", Indexer.DEFAULT_K1),
                b = params.jsonDouble("b", Indexer.DEFAULT_B),
                embeddingDim = params.jsonInt("embedding_dim", Indexer.DEFAULT_EMBEDDING_DIM),
                denseVectors = denseVectors,
                queryVector = queryVector,
            )
            mapOf(
                "results" to ranked.map { entry ->
                    linkedMapOf<String, Any?>(
                        "chunk_id" to entry.chunkId,
                        "score" to entry.score,
                    )
                },
            )
        },
        "text.citation_check.resolve_premises" to { params ->
            // The premise a claim is judged against is a rule, and the rule is
            // here. The model that judges it lives in the Python tier, so the
            // premises travel out and the verdicts travel back.
            val claims = params.jsonObjectList("claims").map { lmeditor.core.claimFromWire(it) }
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val store = chunks.associateBy { it.chunkId }
            val (cited, premises) = CitationChecker(PreEvaluatedClaims(emptyList()))
                .resolvePremises(
                    claims = claims,
                    chunksById = store,
                    strictHallucinationCheck = params.jsonBool("strict_hallucination_check", false),
                    sectionId = params.jsonString("section_id"),
                )
            mapOf(
                "cited" to cited,
                "premises" to premises,
            )
        },
        "text.citation_check.precision" to { params ->
            mapOf(
                "precision" to computeCitationPrecision(
                    supportedCitedClaims = params.jsonInt("supported_cited_claims"),
                    totalCitedClaims = params.jsonInt("total_cited_claims"),
                    totalClaims = params.jsonInt("total_claims"),
                ),
            )
        },
        "text.citation_check.recall" to { params ->
            mapOf(
                "recall" to computeCitationRecall(
                    supportedClaims = params.jsonInt("supported_claims"),
                    totalClaims = params.jsonInt("total_claims"),
                ),
            )
        },
        "text.citation_check.f_beta" to { params ->
            mapOf(
                "f_beta" to computeGroundednessFBeta(
                    precision = params.jsonDouble("precision"),
                    recall = params.jsonDouble("recall"),
                    beta = params.jsonDouble("beta", DEFAULT_BETA),
                ),
            )
        },
        "text.citation_check.audit" to { params ->
            val claims = params.jsonObjectList("claims").map { lmeditor.core.claimFromWire(it) }
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val store = chunks.associateBy { it.chunkId }
            val checker = CitationChecker(
                PreEvaluatedClaims(claims, params.jsonString("evaluator_name")),
                beta = params.jsonDouble("beta", DEFAULT_BETA),
            )
            val (audited, scorecard) = checker.auditClaims(
                claims = claims,
                chunksById = store,
                strictHallucinationCheck = params.jsonBool("strict_hallucination_check", false),
                sectionId = params.jsonString("section_id"),
            )
            val hallucinated = claims
                .filter { claim -> claim.citedChunkIds.any { it !in store } }
                .map { it.claimId }
            mapOf(
                "claims" to audited.map { it.toWireMap() },
                "scorecard" to scorecard.toWireMap(),
                "hallucinated" to hallucinated,
            )
        },
        "text.citation_check.audit_section" to { params ->
            // The section's verdict: the metrics, the status and the critique
            // the operator reads. The claims it is made of have already been
            // judged by the model in the Python tier, so they arrive carrying
            // their verdicts.
            val section = sectionDraftFromWire(params.jsonObject("section"))
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val store = chunks.associateBy { it.chunkId }
            val checker = CitationChecker(
                PreEvaluatedClaims(section.claims, params.jsonString("evaluator_name")),
                beta = params.jsonDouble("beta", DEFAULT_BETA),
                fBetaThreshold = params.jsonDouble(
                    "threshold_f_beta",
                    DEFAULT_F_BETA_THRESHOLD,
                ),
            )
            val (updated, scorecard) = checker.auditSection(
                section = section,
                chunksById = store,
                strictHallucinationCheck = params.jsonBool("strict_hallucination_check", false),
            )
            mapOf(
                "section" to updated.toWireMap(),
                "scorecard" to scorecard.toWireMap(),
            )
        },

        // --- editing: whether a proposed revision may stand ------------------
        "editing.validate_revision" to { params ->
            // The revision itself was written by a model, and the model lives in
            // the Python tier. What crosses the seam is the question the
            // revision has to answer: may this text stand, given the sources
            // the document has?
            val validation = validateRevision(
                revisedText = params.jsonString("revised_text"),
                section = sectionDraftFromWire(params.jsonObject("section")),
                chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) },
            )
            mapOf(
                "accepted" to validation.accepted,
                "reason" to validation.reason,
                "cited_chunk_ids" to validation.citedChunkIds,
                "claims" to validation.claims.map { it.toWireMap() },
            )
        },
        "editing.plan_edit" to { params ->
            // Which span of the section the edit lands on and the instruction
            // the model is given are rules, and the rules live here. The
            // passages the model is shown are the retrieval index's business:
            // the index belongs to the Python tier, so the passages it ranked
            // travel with the request and the editor is handed an index that
            // answers with them.
            //
            // The rewrite itself is a model call, and the model is the Python
            // tier's, so the seam is asked for the plan and the model is
            // called on the other side of it.
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            var plannedInstruction = ""
            var plannedText = ""
            var plannedPassages: List<SourceChunk> = emptyList()
            val editor = InlineEditor(
                checker = CitationChecker(PreEvaluatedClaims(emptyList())),
                rewrite = object : RewriteFn {
                    override fun rewrite(
                        instruction: String,
                        text: String,
                        passages: List<SourceChunk>,
                    ): String {
                        plannedInstruction = instruction
                        plannedText = text
                        plannedPassages = passages
                        return text
                    }
                },
                index = null,
                chunks = chunks,
                maxPassages = params.jsonInt("max_passages", 6),
            )
            // The seam handed the text back unchanged, so the editor stops at
            // the no-op branch. The plan it computed is what travels out.
            editor.edit(
                inlineEditRequestFromWire(params.jsonObject("request")),
                sectionDraftFromWire(params.jsonObject("section")),
            )
            mapOf(
                "instruction" to plannedInstruction,
                "original_text" to plannedText,
                "passages" to plannedPassages.map { it.toWireMap() },
            )
        },
        "editing.apply_edit" to { params ->
            // Whether the revision may stand is a rule, and so is the section
            // that comes back when it does. The entailment model lives in the
            // Python tier, so the claims arrive already judged and the
            // evaluator hands that judgement back unchanged.
            val grounding = groundingFromWire(params)
            val revisedText = params.jsonString("revised_text")
            val refusalReason = params.jsonString("refusal_reason")
            val editor = InlineEditor(
                checker = CitationChecker(
                    PreEvaluatedClaims(
                        params.jsonObjectList("evaluated_claims").map { claimFromWire(it) },
                        params.jsonString("evaluator_name"),
                    ),
                    beta = params.jsonDouble("beta", DEFAULT_BETA),
                    fBetaThreshold = params.jsonDouble(
                        "threshold_f_beta",
                        DEFAULT_F_BETA_THRESHOLD,
                    ),
                ),
                rewrite = object : RewriteFn {
                    override fun rewrite(
                        instruction: String,
                        text: String,
                        passages: List<SourceChunk>,
                    ): String {
                        if (refusalReason.isNotEmpty()) {
                            // The seam refused the revision on the other side of
                            // the seam; the refusal travels back as the reason
                            // the editor reports.
                            throw InlineEditRejectedError(
                                sectionId = "",
                                reason = refusalReason,
                            )
                        }
                        return revisedText
                    }
                },
                index = grounding.index,
                chunks = grounding.chunks,
                maxPassages = grounding.maxPassages,
            )
            val result = editor.edit(
                inlineEditRequestFromWire(params.jsonObject("request")),
                sectionDraftFromWire(params.jsonObject("section")),
            )
            mapOf("result" to result.toWireMap())
        },

        // --- factcheck: the verdict over a passage ---------------------------
        "factcheck.relevance" to { params ->
            // Whether a passage is evidence at all is a rule: the share of the
            // claim's content words the passage contains. The Python tier asks
            // for it rather than keeping a second copy of the measure.
            mapOf(
                "relevance" to lmeditor.factcheck.relevance(
                    claimText = params.jsonString("claim_text"),
                    evidenceText = params.jsonString("evidence_text"),
                ),
            )
        },
        "factcheck.evidence_limit" to { params ->
            // How many evidence items a claim is given is a rule too: the
            // request is authoritative when it expresses a preference of its
            // own, and the checker's default applies when it does not.
            mapOf(
                "limit" to lmeditor.factcheck.evidenceLimit(
                    maxEvidence = params.jsonInt("max_evidence"),
                    requestedMaxEvidence = params.jsonInt("requested_max_evidence"),
                ),
            )
        },
        "factcheck.validate_max_evidence" to { params ->
            // The range the evidence limit has to fall in is the tier's too.
            lmeditor.factcheck.validateMaxEvidence(params.jsonInt("max_evidence"))
            mapOf("valid" to true)
        },
        "factcheck.aggregate" to { params ->
            // The evidence was retrieved and the claims were judged in the
            // Python tier, because retrieval and the entailment model are its
            // business. What the report says is a rule, and the rule is here.
            val verdicts = params.jsonObjectList("verdicts").map { claimVerdictFromWire(it) }
            val report = FactCheckReportBuilder.build(
                docId = params.jsonString("doc_id"),
                text = params.jsonString("text"),
                claims = params.jsonObjectList("claims").map { claimFromWire(it) },
                verdicts = verdicts,
                evidenceFound = params.jsonInt(
                    "evidence_found",
                    verdicts.sumOf { it.evidence.size },
                ),
                webConsulted = params.jsonBool("web_searched", false),
                notes = params.jsonStringList("notes"),
                beta = params.jsonDouble("beta", DEFAULT_BETA),
                evaluatorName = params.jsonString("evaluator_name"),
            )
            mapOf(
                "report" to report.toWireMap(),
                "scorecard" to report.scorecard?.toWireMap(),
            )
        },

        // --- library: the books on disk -------------------------------------
        "library.import_book" to { params ->
            val manifest = importWordDocument(
                store = bookStoreFor(params),
                bookId = params.jsonString("book_id"),
                title = params.jsonString("title"),
                document = readWordDocument(params),
            )
            mapOf("manifest" to manifest.toWireMap())
        },
        "library.get_manifest" to { params ->
            val manifest = bookStoreFor(params).open(params.jsonString("book_id"))
            mapOf("manifest" to manifest.toWireMap())
        },
        "library.get_section" to { params ->
            val store = bookStoreFor(params)
            val bookId = params.jsonString("book_id")
            val sectionId = params.jsonString("section_id")
            val section = store.open(bookId).sections
                .firstOrNull { it.sectionId == sectionId }
                // A section that is not in the book is a missing section, not a
                // malformed request: the caller asked for a thing that does not
                // exist. The Python side answers that with a 404, and only a
                // `SectionNotFoundError` carries it there.
                ?: throw SectionNotFoundError(bookId, sectionId)
            mapOf(
                "section_id" to section.sectionId,
                "title" to section.title,
                "order" to section.order,
                "content" to store.readSection(bookId, sectionId),
                "word_count" to section.wordCount,
                "char_count" to section.charCount,
                "chunk_ids" to section.chunkIds,
            )
        },
        "library.save_section" to { params ->
            val saved = bookStoreFor(params).writeSection(
                bookId = params.jsonString("book_id"),
                sectionId = params.jsonString("section_id"),
                content = params.jsonString("content"),
            )
            mapOf("result" to saved.toWireMap())
        },
        "library.build_working_set" to { params ->
            // The retrieval itself is model-dependent: the Python tier indexes
            // the corpus with an embedding model, so the passages it ranked
            // travel with the request and the tier assembles the bounded set
            // around them.
            val ranked = params.jsonObjectList("ranked_passages").map { sourceChunkFromWire(it) }
            val builder = WorkingSetBuilder(
                bookStoreFor(params),
                index = if (ranked.isEmpty()) null else PassageIndex { _, _ -> ranked },
            )
            val workingSet = builder.build(
                bookId = params.jsonString("book_id"),
                sectionId = params.jsonString("section_id"),
                neighbours = params.jsonInt("neighbours", 1),
                maxPassages = params.jsonInt("max_passages", 8),
                maxBriefChars = params.jsonInt("max_brief_chars", 2000),
                maxSectionChars = params.jsonInt("max_section_chars", 4000),
            )
            mapOf("working_set" to workingSet.toWireMap())
        },
        "library.read_count" to { params ->
            // The number of section bodies the store has read. It is what
            // makes the bounded-context property measurable rather than merely
            // asserted, so it is answered by the tier that does the reading.
            mapOf("read_count" to bookStoreFor(params).readCount)
        },
        "library.build_brief" to { params ->
            mapOf(
                "brief" to buildBrief(
                    bookStoreFor(params),
                    params.jsonString("book_id"),
                    params.jsonInt("max_chars", 2000),
                ),
            )
        },
        "library.merge_brief" to { params ->
            // The brief is a bounded digest: the tier owns the bound, so the
            // Python side does not get to decide how long it is.
            mapOf(
                "brief" to mergeBrief(
                    params.jsonString("current"),
                    params.jsonString("addition"),
                    params.jsonInt("max_chars", 2000),
                ),
            )
        },
        "library.add_section" to { params ->
            // The tier's upsert: it creates the section when it is not there
            // yet and replaces it when it is. The Python side used to write
            // the index entry itself, which meant two writers for one format.
            val order = params["order"]?.let { value ->
                (value as? Number)?.toInt()
            }
            val saved = bookStoreFor(params).addSection(
                bookId = params.jsonString("book_id"),
                sectionId = params.jsonString("section_id"),
                content = params.jsonString("content"),
                title = params.jsonString("title", ""),
                order = order,
                origin = params.jsonObject("origin"),
            )
            mapOf("result" to saved.toWireMap())
        },
        "library.import_sections" to { params ->
            // The sections travel as `(section_id, title, content)` triples.
            val sections = params.jsonObjectList("sections").map { section ->
                Triple(
                    section["section_id"]?.toString() ?: "",
                    section["title"]?.toString() ?: "",
                    section["content"]?.toString() ?: "",
                )
            }
            val manifest = importSections(
                store = bookStoreFor(params),
                bookId = params.jsonString("book_id"),
                title = params.jsonString("title"),
                sections = sections,
            )
            mapOf("manifest" to manifest.toWireMap())
        },
        "library.export_book" to { params ->
            val document = buildBookDocument(bookStoreFor(params), params.jsonString("book_id"))
            val bytes = WordWriter(deterministic = true).write(document)
            mapOf("bytes_b64" to Base64.getEncoder().encodeToString(bytes))
        },
        "library.create" to { params ->
            val manifest = bookStoreFor(params).create(
                bookId = params.jsonString("book_id"),
                title = params.jsonString("title"),
            )
            mapOf("manifest" to manifest.toWireMap())
        },
        "library.list_books" to { params ->
            mapOf("books" to bookStoreFor(params).listBooks().map { it.toWireMap() })
        },
        "library.delete" to { params ->
            bookStoreFor(params).delete(params.jsonString("book_id"))
            mapOf("ok" to true)
        },
        "library.read_section_record" to { params ->
            val record = bookStoreFor(params).readSectionRecord(
                bookId = params.jsonString("book_id"),
                sectionId = params.jsonString("section_id"),
            )
            mapOf(
                "record" to mapOf(
                    "section_id" to record.sectionId,
                    "title" to record.title,
                    "order" to record.order,
                    "content" to record.content,
                    "origin" to record.origin,
                ),
            )
        },
        "library.set_section_chunks" to { params ->
            val saved = bookStoreFor(params).setSectionChunks(
                bookId = params.jsonString("book_id"),
                sectionId = params.jsonString("section_id"),
                chunkIds = params.jsonStringList("chunk_ids"),
            )
            mapOf("result" to saved.toWireMap())
        },
        "library.put_chunks" to { params ->
            bookStoreFor(params).putChunks(
                bookId = params.jsonString("book_id"),
                chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) },
            )
            mapOf("ok" to true)
        },
        "library.chunks" to { params ->
            mapOf(
                "chunks" to bookStoreFor(params)
                    .chunks(params.jsonString("book_id"))
                    .map { it.toWireMap() },
            )
        },
        "library.read_book_origin" to { params ->
            mapOf("origin" to bookStoreFor(params).readBookOrigin(params.jsonString("book_id")))
        },
        "library.set_book_origin" to { params ->
            @Suppress("UNCHECKED_CAST")
            bookStoreFor(params).setBookOrigin(
                bookId = params.jsonString("book_id"),
                origin = (params["origin"] as? Map<String, Any?>) ?: emptyMap(),
            )
            mapOf("ok" to true)
        },
        "library.update_brief" to { params ->
            bookStoreFor(params).updateBrief(
                bookId = params.jsonString("book_id"),
                brief = params.jsonString("brief"),
            )
            mapOf("ok" to true)
        },

        // --- pdf: native text and metadata extraction -----------------------
        "pdf.text" to { params ->
            val document = pdfDocumentFrom(params)
            mapOf(
                "text" to PdfText.text(document),
                "pages" to document.pageCount,
                "page_texts" to PdfText.pageTexts(document),
                "limitations" to document.issues.map { it.reason },
            )
        },
        "pdf.metadata" to { params ->
            val document = pdfDocumentFrom(params)
            val metadata = PdfMetadata.extract(document)
            mapOf(
                "title" to metadata.title,
                "author" to metadata.author,
                "subject" to metadata.subject,
                "keywords" to metadata.keywords,
                "creator" to metadata.creator,
                "producer" to metadata.producer,
                "creation_date" to metadata.creationDate,
                "doi" to metadata.doi,
                "arxiv_id" to metadata.arxivId,
                "page_count" to document.pageCount,
                "metadata" to metadata.toMap(),
            )
        },

        // --- workflow: the routing rules ------------------------------------
        "workflow.create_initial_state" to { params ->
            val state = createInitialHarnessState(
                docId = params.jsonString("doc_id"),
                topic = params.jsonString("topic"),
                targetAudience = params["target_audience"]?.toString()
                    ?: DEFAULT_TARGET_AUDIENCE,
                rawTexts = params.jsonStringList("raw_texts").ifEmpty { null },
                sources = params.jsonObjectList("sources")
                    .map { sourceFromWire(it) }
                    .ifEmpty { null },
                guidelines = params.jsonStringList("guidelines").ifEmpty { null },
                maxRetries = params.jsonInt("max_retries", Routing.MAX_SECTION_RETRIES),
                thresholdFBeta = params.jsonDouble(
                    "threshold_f_beta",
                    Routing.DEFAULT_F_BETA_THRESHOLD,
                ),
            )
            mapOf("state" to state.toWireMap())
        },
        "workflow.route" to { params ->
            // The routing rules read a handful of fields: which sections are
            // verified, how many times each has been retried, the retry budget
            // and the operator's decision. The seam carries those, because the
            // routing question is what crosses it; the authoritative state
            // stays in the Python tier that owns the graph.
            val state = HarnessState(
                sectionDrafts = params.jsonObject("section_drafts")
                    .mapValues { (_, value) ->
                        sectionDraftFromWire(
                            @Suppress("UNCHECKED_CAST")
                            (value as Map<String, Any?>),
                        )
                    },
                retryCounts = params.jsonObject("retry_counts")
                    .mapValues { (_, value) -> (value as? Number)?.toInt() ?: 0 },
                maxRetries = (params["max_retries"] as? Number)?.toInt(),
                hitlApproved = params.jsonBool("hitl_approved"),
            )
            val route = params.jsonString("route", "after_verification")
            val result = when (route) {
                "after_hitl" -> mapOf(
                    "next_step" to Routing.routeAfterHitl(state).wire,
                    "reason" to "the operator's decision decides whether the run continues",
                )
                else -> {
                    val next = Routing.routeAfterVerification(state)
                    mapOf(
                        "next_step" to next.wire,
                        "reason" to when (next) {
                            VerificationRoute.ALL_VERIFIED ->
                                "every section is verified"
                            VerificationRoute.HITL_REQUIRED ->
                                "a section has spent its retry budget and needs a human"
                            VerificationRoute.NEEDS_REFINEMENT ->
                                "a section is not verified and can be refined again"
                        },
                        "exhausted_sections" to Routing.exhaustedSections(state),
                    )
                }
            }
            result
        },

        // --- discovery: perspectives and outlines ---------------------------
        "discovery.outline" to { params ->
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val memory = params["research_memory"]?.let { raw ->
                @Suppress("UNCHECKED_CAST")
                researchMemoryFromWire(raw as Map<String, Any?>)
            }
            val perspectives = params.jsonObjectList("perspectives").map { perspectiveFromWire(it) }
            val outline = OutlineBuilder(useModel = false).generateOutline(
                topic = params.jsonString("topic"),
                researchMemory = memory,
                availableChunks = chunks,
                perspectives = perspectives.ifEmpty { null },
            )
            mapOf("outline" to outline.toWireMap())
        },
        "discovery.outlineSections" to { params ->
            // The operator's steer: the sections they named are the sections the
            // manuscript is written to. The passages are still assigned from the
            // corpus by the tier's own rules, so the steer decides what the
            // document covers and never what it claims.
            val sections = params.jsonObjectList("sections").map { outlineNodeInputFromWire(it) }
            val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
            val outline = OutlineBuilder(useModel = false).buildOutlineFromSections(
                sections = sections,
                topic = params.jsonString("topic"),
                availableChunks = chunks,
            )
            mapOf("outline" to outline.toWireMap())
        },
        "discovery.perspectives" to { params ->
            val perspectives = PerspectiveGenerator(useModel = false).generatePerspectives(
                topic = params.jsonString("topic"),
                context = params["context"]?.toString(),
                numPerspectives = params.jsonInt("count", 4),
            )
            mapOf("perspectives" to perspectives.map { it.toWireMap() })
        },
    ) + wordHandlers() + serverHandlers() + ingestHandlers() + discoveryHandlers() + factcheckWebHandlers() + vaultHandlers() + notebookHandlers()
}

/**
 * Read the PDF the request names.
 *
 * A request carries either a path or the bytes themselves; the bytes are
 * base64 on the wire, and the sender is the one that encoded them, so the
 * receiver never has to guess.
 */
private fun pdfDocumentFrom(params: Map<String, Any?>): PdfDocument {
    val path = params["path"]?.toString()
    val encoded = params["bytes_b64"]?.toString()
    return when {
        path != null -> PdfDocument.open(Path.of(path))
        encoded != null -> PdfDocument.parse(
            Base64.getDecoder().decode(encoded),
            origin = path ?: "<bytes>",
        )
        else -> throw ValidationError(
            "either 'path' or 'bytes_b64' is required",
            field = "path",
        )
    }
}

/**
 * The book store the library rules use.
 *
 * The tier and the Python side must keep their books in the *same* place, or
 * a book imported by one would be invisible to the other. So the root comes
 * from the same environment variable the Python side reads, and falls back to
 * the same default.
 */
private fun defaultBookStore(): BookStore {
    val configured = System.getenv(LIBRARY_DIR_ENV)
    val root = if (configured.isNullOrBlank()) {
        Path.of(System.getProperty("java.io.tmpdir"), "lmeditor_books")
    } else {
        Path.of(configured)
    }
    return bookStore(root)
}

/**
 * The store for the root a request names, or the configured one.
 *
 * The root is a parameter of the store, not a setting of the process: a
 * caller that opens a book in its own directory must get *that* book, so the
 * root travels with the request. A request that names no root gets the
 * configured one.
 */
private fun bookStoreFor(params: Map<String, Any?>): BookStore {
    val root = params["root"]?.toString()
    return if (root.isNullOrBlank()) defaultBookStore() else bookStore(Path.of(root))
}

/** The store for one root, so the books are opened once and kept. */
private fun bookStore(root: Path): BookStore =
    stores.getOrPut(root) { BookStore(root) }

/** The open stores, keyed by their root. */
private val stores: MutableMap<Path, BookStore> = LinkedHashMap()

/**
 * The environment variable that redirects the library root.
 *
 * The Python side reads the same variable, so both tiers keep their books in
 * the same directory.
 */
private const val LIBRARY_DIR_ENV: String = "LMEDITOR_LIBRARY_DIR"

/**
 * The Word document the request names.
 *
 * A request carries either a path or the bytes themselves; the bytes are
 * base64 on the wire.
 */
private fun readWordDocument(params: Map<String, Any?>): WordDocument {
    val path = params["path"]?.toString()
    val encoded = params["bytes_b64"]?.toString()
    val reader = WordReader()
    return when {
        path != null -> reader.read(Path.of(path))
        encoded != null -> reader.readBytes(Base64.getDecoder().decode(encoded))
        else -> throw ValidationError(
            "either 'path' or 'bytes_b64' is required",
            field = "path",
        )
    }
}

/**
 * An evaluator that returns the verdict a claim already carries.
 *
 * The entailment model lives in the Python tier; by the time the claims
 * arrive here they have been judged. This evaluator hands that judgement
 * back unchanged, so the audit runs exactly the code path it would run with a
 * model behind it -- the arithmetic is identical either way, and no model is
 * needed on this side of the seam.
 *
 * A claim is identified by its text *and* the passage it was judged against,
 * and not by its text alone. Two claims may read the same and rest on
 * different passages; they are then two claims with two verdicts, and letting
 * one of them answer for the other would let a contradicted claim be counted
 * as an entailed one.
 */
private class PreEvaluatedClaims(
    claims: List<lmeditor.core.Claim>,
    override val evaluatorName: String = "",
) : NamedNliEvaluator {

    /**
     * The claims that read alike, in the order they arrived.
     *
     * The entailment interface carries the hypothesis and the premise, and the
     * premise is the passage the claim was judged against -- so it is what
     * tells two claims that read alike apart.
     */
    private val byText: Map<String, List<lmeditor.core.Claim>> =
        claims.groupBy { it.text }

    override fun evaluate(premise: String, hypothesis: String): Pair<EntailmentStatus, Double> {
        val alike = byText[hypothesis].orEmpty()
        if (alike.isEmpty()) return EntailmentStatus.NEUTRAL to 0.0
        if (alike.size == 1) return verdictOf(alike[0])
        val onThisPremise = alike.filter { it.premiseText == premise }
        val candidates = if (onThisPremise.isEmpty()) alike else onThisPremise
        return candidates
            .map { verdictOf(it) }
            .maxByOrNull { verdict -> severity(verdict.first) }
            ?: (EntailmentStatus.NEUTRAL to 0.0)
    }

    /** The verdict the claim carries. */
    private fun verdictOf(claim: lmeditor.core.Claim): Pair<EntailmentStatus, Double> =
        claim.entailmentStatus to claim.entailmentScore
}

/**
 * How badly a verdict reads, the worst last.
 *
 * The gate refuses a section that holds anything but an entailed claim, and a
 * contradiction is the loudest of the verdicts that refuse it: it says the
 * claim is not merely unproven but wrong. The order is stated here rather
 * than taken from the enum, because the enum's order is the wire order and
 * not a ranking.
 */
private fun severity(status: EntailmentStatus): Int = when (status) {
    EntailmentStatus.ENTAILED -> 0
    EntailmentStatus.UNGROUNDED -> 1
    EntailmentStatus.NEUTRAL -> 2
    EntailmentStatus.CONTRADICTED -> 3
}

/**
 * The retrieval context of one edit.
 *
 * The chunks are the ones the editor may fall back to when there is no index.
 * The index is the Python tier's, so the passages it ranked travel across the
 * seam as the passages themselves.
 */
private class Grounding(
    val chunks: List<SourceChunk>,
    val index: ProvenanceIndexer?,
    val maxPassages: Int,
)

/**
 * Read the retrieval context of one edit from the wire.
 *
 * `use_index` says whether the edit is grounded in an index at all: an index
 * with nothing in it is still an index, and the editor asks it for the
 * passages rather than falling back to the overlapping ones.
 */
private fun groundingFromWire(params: Map<String, Any?>): Grounding {
    val chunks = params.jsonObjectList("chunks").map { sourceChunkFromWire(it) }
    val indexChunks = params.jsonObjectList("index_chunks").map { sourceChunkFromWire(it) }
    val ranked = params.jsonObjectList("ranked_passages").map { sourceChunkFromWire(it) }
    val useIndex = params.jsonBool("use_index", false)
    val maxPassages = params.jsonInt("max_passages", 6)
    val index = if (useIndex) {
        RankedPassages(ranked, indexChunks + chunks)
    } else {
        null
    }
    return Grounding(chunks, index, maxPassages)
}

/**
 * An index that answers with the passages the Python tier ranked.
 *
 * The retrieval belongs to the Python tier -- it owns the vector index and the
 * ranking -- so the ranking travels across the seam as the passages
 * themselves. The corpus travels with them, because a chunk the revision
 * cites has to be resolvable to its text even when it was not among the
 * passages the model was shown.
 */
private class RankedPassages(
    private val ranked: List<SourceChunk>,
    private val corpus: List<SourceChunk>,
) : ProvenanceIndexer {
    override fun query(query: String, topK: Int): List<SourceChunk> = ranked.take(topK)
    override fun getChunk(chunkId: String): SourceChunk? =
        corpus.firstOrNull { it.chunkId == chunkId }
            ?: ranked.firstOrNull { it.chunkId == chunkId }
    override fun hasChunk(chunkId: String): Boolean = getChunk(chunkId) != null
    override fun getAllChunkIds(): List<String> = (corpus + ranked).map { it.chunkId }
}

/**
 * The dense vectors of a corpus, as the wire carries them.
 *
 * The sender normalised them already, so they are read as they are: the
 * tier's own term-frequency hash is only used when the sender sends nothing.
 */
private fun denseVectorMap(raw: Any?): Map<String, List<Double>> {
    val entries = raw as? Map<*, *> ?: throw ValidationError(
        "dense_vectors must be an object of chunk id to vector",
        field = "dense_vectors",
    )
    val vectors = LinkedHashMap<String, List<Double>>()
    for ((key, value) in entries) {
        vectors[key.toString()] = doubleList(value, "dense_vectors")
    }
    return vectors
}

/** An array of doubles, as the wire carries it. */
private fun doubleList(raw: Any?, field: String): List<Double> {
    val items = raw as? List<*> ?: throw ValidationError(
        "$field must be an array of numbers",
        field = field,
    )
    return items.map { item ->
        (item as? Number)?.toDouble() ?: throw ValidationError(
            "$field must be an array of numbers",
            field = field,
        )
    }
}

