// The schema models strip whitespace when they are constructed and not when
// they are copied, the way Pydantic's constructor and `model_copy()` do. The
// generated `copy()` of a model with a private constructor is flagged by the
// compiler; the pattern is deliberate, and it is documented in
// `lmeditor.core.Schemas`.
@file:Suppress("DATA_CLASS_INVISIBLE_COPY_USAGE_WARNING")

package lmeditor.discovery

import lmeditor.core.LMNotConfiguredError
import lmeditor.core.OutlineGenerationError
import lmeditor.core.OutlineNode
import lmeditor.core.OutlineNodeInput
import lmeditor.core.Perspective
import lmeditor.core.ResearchMemory
import lmeditor.core.SourceChunk
import lmeditor.core.TopicOutline
import lmeditor.core.ValidationError
import lmeditor.core.json.Json

/**
 * A hierarchical topic outline whose structure comes from the corpus.
 *
 * Port of `backend/src/lmeditor/discovery/outliner.py`.
 *
 * The outline is *derived*, never assumed: the sections of a manuscript are
 * named after the material that will actually fill them. Two paths produce
 * that structure and both are real:
 *
 *  * with a language model configured and [useModel] enabled, the model reads
 *    the chunk text and proposes the section headings, and
 *  * without one, the headings come from the chunks themselves -- either the
 *    headings the document already carries, or the salient vocabulary of
 *    topical clusters of the passages.
 *
 * The result is that two different corpora produce two different outlines,
 * and an outline for a corpus about municipal waste never mentions
 * production telemetry.
 *
 * The model path is a seam ([OutlineModel]). A model that is asked for but
 * not supplied raises `LMNotConfiguredError`: the caller asked for prose from
 * a model, and quietly returning a weaker document instead would be a lie
 * about what the document is.
 */
open class OutlineBuilder(
    private val useModel: Boolean = false,
    private val model: OutlineModel? = null,
) {
    companion object {
        /** Maximum number of top-level sections derived from a corpus. */
        const val MAX_ROOT_SECTIONS: Int = 5

        /** Maximum number of leaves under a single root section. */
        const val MAX_LEAVES_PER_ROOT: Int = 2
    }

    /**
     * One leaf of the outline specification tree.
     *
     * The leaf is what the passages are assigned to: it carries the words
     * the assignment is matched on and the passages the derivation already
     * claimed for it.
     */
    data class LeafSpec(
        val id: String,
        val title: String,
        val description: String,
        val keywords: List<String>,
        val chunkIds: List<String>,
    )

    /** A derived section: a title, a description and the passages it covers. */
    private data class SectionSpec(
        val title: String,
        val description: String,
        val keywords: List<String>,
        val chunks: List<SourceChunk>,
    )

    /** One root section of the outline specification tree. */
    private data class RootSpec(
        val id: String,
        val title: String,
        val description: String,
        val subsections: List<LeafSpec>,
    )

    /** The zero-padded two-digit numbering the Python format spec produces. */
    private fun pad2(value: Int): String = value.toString().padStart(2, '0')

    /**
     * Validate input parameters against boundary conditions.
     *
     * @return the sanitized topic.
     * @throws ValidationError if the topic is empty or the corpus is.
     */
    fun validateInputs(
        topic: String,
        researchMemory: ResearchMemory?,
        availableChunks: List<SourceChunk>,
    ): String {
        val cleanTopic = SourceAnalysis.strip(topic)
        if (cleanTopic.isEmpty()) {
            throw ValidationError(
                "topic cannot be empty or whitespace.",
                field = "topic",
                value = topic,
            )
        }

        if (researchMemory == null) {
            throw ValidationError(
                "research_memory must be an instance of ResearchMemory, got NoneType",
                field = "research_memory",
                value = null,
            )
        }

        if (availableChunks.isEmpty()) {
            throw ValidationError(
                "available_chunks sequence cannot be empty.",
                field = "available_chunks",
                value = availableChunks,
            )
        }

        return cleanTopic
    }

    /**
     * Build a grounded outline from sections the caller named.
     *
     * This is the operator's steer: the sections they asked for are the
     * sections the manuscript is written to. What they *cannot* steer is
     * what the document says -- the passages assigned to each section are
     * chosen from the corpus by the same keyword-relevance and balancing
     * rules the synthesised outline uses, so a caller-supplied outline
     * cannot smuggle in a claim the sources do not support.
     *
     * @param sections the sections the caller named, in order.
     * @param topic the document's subject, used for the root description.
     * @param availableChunks the corpus the sections are grounded in.
     */
    fun buildOutlineFromSections(
        sections: List<OutlineNodeInput>,
        topic: String,
        availableChunks: List<SourceChunk>? = null,
    ): TopicOutline {
        val chunks = availableChunks ?: emptyList()
        val leaves = mutableListOf<LeafSpec>()

        fun collect(nodes: List<OutlineNodeInput>, depth: Int): List<OutlineNode> {
            val built = mutableListOf<OutlineNode>()
            for ((order, node) in nodes.withIndex()) {
                val nodeId = "sec_${pad2(order + 1)}_${pad2(leaves.size + 1)}"
                if (node.children.isNotEmpty()) {
                    val children = collect(node.children, depth + 1)
                    built.add(
                        OutlineNode(
                            nodeId = nodeId,
                            title = node.title,
                            sectionId = nodeId,
                            depth = depth,
                            description = node.description,
                            assignedChunkIds = emptyList(),
                            children = children,
                            order = order,
                            subheadings = children.map { it.title },
                        ),
                    )
                    continue
                }

                val keywords = SourceAnalysis
                    .salientTerms("${node.title} ${node.description}", limit = 4)
                    .map { it.lowercase() }
                leaves.add(
                    LeafSpec(
                        id = nodeId,
                        title = node.title,
                        description = node.description,
                        keywords = keywords,
                        chunkIds = emptyList(),
                    ),
                )
                built.add(
                    OutlineNode(
                        nodeId = nodeId,
                        title = node.title,
                        sectionId = nodeId,
                        depth = depth,
                        description = node.description,
                        assignedChunkIds = emptyList(),
                        children = emptyList(),
                        order = order,
                        subheadings = emptyList(),
                    ),
                )
            }
            return built
        }

        val rootNodes = collect(sections, 0)
        if (rootNodes.isEmpty()) return TopicOutline(topic = topic, nodes = emptyList())

        // With no corpus there is nothing to ground the sections in, so the
        // sections are kept and left unassigned rather than assigned a passage
        // that does not exist.
        if (chunks.isEmpty()) return TopicOutline(topic = topic, nodes = rootNodes)

        val assignments = assignChunksToLeaves(chunks, leaves)

        fun withChunks(nodes: List<OutlineNode>): List<OutlineNode> = nodes.map { node ->
            if (node.children.isNotEmpty()) {
                node.copy(children = withChunks(node.children))
            } else {
                node.copy(assignedChunkIds = assignments[node.nodeId] ?: emptyList())
            }
        }

        return TopicOutline(topic = topic, nodes = withChunks(rootNodes))
    }

    /**
     * Generate a hierarchical topic outline with leaf chunk assignments.
     *
     * The section titles come from the material that will fill them: either
     * from a language model that read the passages, or from the passages
     * themselves.
     *
     * @param topic the manuscript subject.
     * @param researchMemory the consolidated discovery result.
     * @param availableChunks the corpus the outline is grounded in.
     * @param perspectives the discovered personas, used to build a
     *   [ResearchMemory] when [researchMemory] is not supplied.
     * @throws ValidationError if the inputs are out of bounds.
     * @throws OutlineGenerationError if the outline cannot be built.
     */
    fun generateOutline(
        topic: String,
        researchMemory: ResearchMemory? = null,
        availableChunks: List<SourceChunk> = emptyList(),
        perspectives: List<Perspective>? = null,
    ): TopicOutline {
        val memory = researchMemory ?: perspectives?.let { named ->
            ResearchMemory(
                topic = topic,
                sessions = emptyList(),
                allReferencedChunkIds = availableChunks.map { it.chunkId },
                collectedTakeaways = named.map { it.title },
            )
        }

        val cleanTopic = validateInputs(topic, memory, availableChunks)

        // Chunk lookup index for strict boundary verification.
        val validChunkMap = LinkedHashMap<String, SourceChunk>()
        for (chunk in availableChunks) validChunkMap[chunk.chunkId] = chunk

        // 1. Derive the top-level sections from the source material.
        val outlineSpecs = deriveSectionSpecs(cleanTopic, availableChunks)

        // 2. Extract leaf specs and assign chunks to them.
        val leafSpecs = outlineSpecs.flatMap { it.subsections }
        val leafChunkAssignments = assignChunksToLeaves(availableChunks, leafSpecs)

        // 3. Construct the OutlineNode tree DAG.
        val rootNodes = outlineSpecs.mapIndexed { rootIdx, rootSpec ->
            val childNodes = rootSpec.subsections.mapIndexed { subIdx, subSpec ->
                val assignedCids = leafChunkAssignments[subSpec.id] ?: emptyList()

                // Verify every assigned chunk strictly exists in availableChunks.
                for (cid in assignedCids) {
                    if (!validChunkMap.containsKey(cid)) {
                        throw OutlineGenerationError(
                            "Assigned chunk '$cid' is not in available_chunks.",
                            nodeId = subSpec.id,
                            details = mapOf(
                                "assigned_chunk_id" to cid,
                                "valid_chunk_ids" to validChunkMap.keys.toList(),
                            ),
                        )
                    }
                }

                OutlineNode(
                    nodeId = subSpec.id,
                    title = subSpec.title,
                    sectionId = subSpec.id,
                    depth = 1,
                    description = subSpec.description,
                    assignedChunkIds = assignedCids,
                    children = emptyList(),
                    order = subIdx,
                    subheadings = listOf("Sub-aspect of ${subSpec.title}"),
                )
            }

            OutlineNode(
                nodeId = rootSpec.id,
                title = rootSpec.title,
                sectionId = rootSpec.id,
                depth = 0,
                description = rootSpec.description,
                // Root nodes are non-leaves; assigned chunks reside in leaves.
                assignedChunkIds = emptyList(),
                children = childNodes,
                order = rootIdx,
                subheadings = childNodes.map { it.title },
            )
        }

        val outline = TopicOutline(topic = cleanTopic, nodes = rootNodes)

        // 4. Verify DAG acyclicity and structural constraints.
        outline.validateDag()

        return outline
    }

    // ----------------------------------------------------------------------
    // Structure derivation
    // ----------------------------------------------------------------------

    private fun deriveSectionSpecs(
        cleanTopic: String,
        chunks: List<SourceChunk>,
    ): List<RootSpec> =
        if (useModel) {
            deriveSectionSpecsWithModel(cleanTopic, chunks)
        } else {
            deriveSectionSpecsFromChunks(cleanTopic, chunks)
        }

    /**
     * Ask the configured language model for section headings.
     *
     * A model that is not supplied is a configuration failure, not a
     * downgrade: [LMNotConfiguredError] is raised rather than quietly
     * producing a weaker outline.
     */
    private fun deriveSectionSpecsWithModel(
        cleanTopic: String,
        chunks: List<SourceChunk>,
    ): List<RootSpec> {
        val lm = model ?: throw LMNotConfiguredError("outline synthesis")

        val passages = chunks.mapIndexed { index, chunk ->
            "--- PASSAGE ${index + 1} ---\n${chunk.text}"
        }.joinToString("\n\n")

        val raw = try {
            lm.proposeSections(cleanTopic, passages)
        } catch (exc: Exception) {
            throw OutlineGenerationError(
                "Language model failed to propose section headings: $exc",
                details = mapOf("topic" to cleanTopic, "chunk_count" to chunks.size),
            )
        }

        val sections = parseModelSections(raw, cleanTopic, chunks)
        if (sections.isEmpty()) {
            throw OutlineGenerationError(
                "Language model returned no usable section headings.",
                details = mapOf("topic" to cleanTopic, "raw_output" to raw.take(500)),
            )
        }

        return buildRootSpecs(cleanTopic, chunks, sections)
    }

    /**
     * Parse the model's JSON answer into section specifications.
     *
     * The answer is scanned for the first `[` and the last `]`, so a model
     * that wraps its JSON in prose still parses. An answer that carries no
     * usable section is not an error here: the caller decides.
     */
    private fun parseModelSections(
        raw: String,
        cleanTopic: String,
        chunks: List<SourceChunk>,
    ): List<SectionSpec> {
        val payload = SourceAnalysis.strip(raw)
        val start = payload.indexOf('[')
        val end = payload.lastIndexOf(']')
        if (start == -1 || end == -1 || end <= start) return emptyList()

        val parsed = try {
            Json.parse(payload.substring(start, end + 1))
        } catch (exc: Exception) {
            return emptyList()
        }
        if (parsed !is List<*>) return emptyList()

        val sections = mutableListOf<SectionSpec>()
        for (entry in parsed) {
            if (entry !is Map<*, *>) continue
            val title = SourceAnalysis.strip(entry["title"]?.toString() ?: "")
            if (title.isEmpty()) continue
            val description = SourceAnalysis.strip(entry["description"]?.toString() ?: "")
            val keywordsRaw = entry["keywords"]
            val keywords = if (keywordsRaw is List<*>) {
                keywordsRaw
                    .map { SourceAnalysis.strip(it?.toString() ?: "") }
                    .filter { it.isNotEmpty() }
                    .map { it.lowercase() }
            } else {
                emptyList()
            }
            sections.add(SectionSpec(title, description, keywords, emptyList()))
        }

        if (sections.isEmpty()) return emptyList()

        // Assign the chunks to the section whose keywords they match best.
        val assignments = List(sections.size) { mutableListOf<SourceChunk>() }
        for (chunk in chunks) {
            val lowered = chunk.text.lowercase()
            var bestIndex = -1
            var bestScore = 0
            for ((index, section) in sections.withIndex()) {
                val score = section.keywords.count { it in lowered }
                if (score > bestScore) {
                    bestScore = score
                    bestIndex = index
                }
            }
            if (bestIndex >= 0) assignments[bestIndex].add(chunk) else assignments[0].add(chunk)
        }

        // A section with no chunk of its own is dropped rather than shipped empty.
        val kept = mutableListOf<SectionSpec>()
        val keptAssignments = mutableListOf<List<SourceChunk>>()
        for ((index, section) in sections.withIndex()) {
            val members = assignments[index]
            if (members.isEmpty()) continue
            kept.add(section.copy(chunks = members))
            keptAssignments.add(members)
        }

        if (kept.isEmpty()) {
            return listOf(SectionSpec(cleanTopic, "", emptyList(), chunks))
        }

        return kept
    }

    /**
     * Derive the section structure from the chunk content itself.
     *
     * Headings that the document already carries win; otherwise the
     * passages are clustered by their shared vocabulary and each cluster is
     * named after its dominant terms.
     */
    private fun deriveSectionSpecsFromChunks(
        cleanTopic: String,
        chunks: List<SourceChunk>,
    ): List<RootSpec> {
        val groups = groupChunks(chunks)
        val sections = groups.map { (label, members) ->
            SectionSpec(label, describe(members), emptyList(), members)
        }
        return buildRootSpecs(cleanTopic, chunks, sections)
    }

    /**
     * Group the chunks into topical sections.
     *
     * Headings that the passages themselves carry are preferred, because
     * they are the author's own organisation of the material. Passages
     * without a heading are clustered by vocabulary.
     */
    private fun groupChunks(chunks: List<SourceChunk>): List<Pair<String, List<SourceChunk>>> {
        val headingsPerChunk = chunks.map { SourceAnalysis.detectHeadings(it.text) }
        if (headingsPerChunk.any { it.isNotEmpty() }) {
            val groups = LinkedHashMap<String, MutableList<SourceChunk>>()
            val order = mutableListOf<String>()
            val loose = mutableListOf<SourceChunk>()

            for ((index, chunk) in chunks.withIndex()) {
                val headings = headingsPerChunk[index]
                if (headings.isNotEmpty()) {
                    val heading = headings[0]
                    groups.getOrPut(heading) { order.add(heading); mutableListOf() }.add(chunk)
                } else {
                    loose.add(chunk)
                }
            }

            val result = order.map { heading -> heading to groups.getValue(heading).toList() }.toMutableList()
            if (loose.isNotEmpty()) result.addAll(clusterGroups(loose))
            return result
        }

        return clusterGroups(chunks)
    }

    /** Cluster chunks by shared vocabulary and name each cluster. */
    private fun clusterGroups(chunks: List<SourceChunk>): List<Pair<String, List<SourceChunk>>> {
        if (chunks.isEmpty()) return emptyList()
        if (chunks.size == 1) {
            val chunk = chunks[0]
            val terms = SourceAnalysis.salientTerms(chunk.text, limit = 2)
            val label = if (terms.isNotEmpty()) {
                SourceAnalysis.titleCaseTerms(terms)
            } else {
                "Source Material"
            }
            return listOf(label to listOf(chunk))
        }

        val numClusters = maxOf(1, minOf(MAX_ROOT_SECTIONS, (chunks.size + 1) / 2))
        val clusters = SourceAnalysis.clusterByTerms(chunks.map { it.text }, numClusters)

        return clusters.map { indices ->
            val members = indices.map { chunks[it] }
            val terms = SourceAnalysis
                .salientTerms(members.joinToString(" ") { it.text }, limit = 2)
                .filter { it !in SourceAnalysis.GENERIC_TERMS }
            val label = if (terms.isNotEmpty()) {
                SourceAnalysis.titleCaseTerms(terms)
            } else {
                "Source Material"
            }
            label to members
        }
    }

    /** Describe a group of chunks with the findings they actually state. */
    private fun describe(chunks: List<SourceChunk>): String {
        val findings = chunks.flatMap { SourceAnalysis.extractFindings(it.text, limit = 2) }
        if (findings.isNotEmpty()) {
            return "Source passages reporting: ${findings.take(3).joinToString("; ")}"
        }
        val terms = SourceAnalysis.salientTerms(chunks.joinToString(" ") { it.text }, limit = 4)
        if (terms.isNotEmpty()) {
            return "Source passages covering ${terms.joinToString(", ")}."
        }
        return "Source passages grouped by shared subject matter."
    }

    /** Turn derived sections into the root/leaf specification tree. */
    private fun buildRootSpecs(
        cleanTopic: String,
        chunks: List<SourceChunk>,
        sections: List<SectionSpec>,
    ): List<RootSpec> = sections.take(MAX_ROOT_SECTIONS).mapIndexed { rootIdx, section ->
        val members = section.chunks
        val title = SourceAnalysis.strip(section.title).ifEmpty { cleanTopic }
        val description = SourceAnalysis.strip(section.description).ifEmpty { describe(members) }

        RootSpec(
            id = "sec_${pad2(rootIdx + 1)}",
            title = "${rootIdx + 1}. $title",
            description = description,
            subsections = deriveLeafSpecs(rootIdx, title, members),
        )
    }

    /** Split a section's chunks into at most two leaves. */
    private fun deriveLeafSpecs(
        rootIdx: Int,
        rootTitle: String,
        members: List<SourceChunk>,
    ): List<LeafSpec> {
        if (members.isEmpty()) {
            return listOf(
                LeafSpec(
                    id = "sec_${pad2(rootIdx + 1)}_01",
                    title = "${rootIdx + 1}.1 $rootTitle",
                    description = "No passage was assigned to this section.",
                    keywords = emptyList(),
                    chunkIds = emptyList(),
                ),
            )
        }

        val groups: List<List<SourceChunk>> = if (members.size == 1) {
            listOf(members)
        } else {
            val clusters = SourceAnalysis.clusterByTerms(
                members.map { it.text },
                minOf(MAX_LEAVES_PER_ROOT, members.size),
            )
            clusters
                .map { indices -> indices.map { members[it] } }
                .filter { it.isNotEmpty() }
        }

        return groups.mapIndexed { leafIdx, group ->
            val terms = SourceAnalysis
                .salientTerms(group.joinToString(" ") { it.text }, limit = 2)
                .filter { it !in SourceAnalysis.GENERIC_TERMS }
            val label = if (terms.isNotEmpty()) {
                SourceAnalysis.titleCaseTerms(terms)
            } else {
                rootTitle
            }
            LeafSpec(
                id = "sec_${pad2(rootIdx + 1)}_${pad2(leafIdx + 1)}",
                title = "${rootIdx + 1}.${leafIdx + 1} $label",
                description = describe(group),
                keywords = terms.map { it.lowercase() },
                chunkIds = group.map { it.chunkId },
            )
        }
    }

    /**
     * Map candidate chunks to leaf sections by keyword relevance and balanced
     * distribution.
     *
     * The method is open so a caller that needs a different placement rule
     * can supply one; whatever it returns is still held to the boundary
     * check, so a leaf may never be given a chunk the corpus does not carry.
     */
    protected open fun assignChunksToLeaves(
        chunks: List<SourceChunk>,
        leafSpecs: List<LeafSpec>,
    ): Map<String, List<String>> {
        val assignments = LinkedHashMap<String, MutableList<String>>()
        for (spec in leafSpecs) assignments[spec.id] = mutableListOf()

        val numLeaves = leafSpecs.size
        if (numLeaves == 0) return emptyMap()

        val chunkIds = chunks.map { it.chunkId }
        val knownIds = chunkIds.toSet()

        // 1. Honour the assignment the derivation already made.
        val assignedChunkIds = mutableSetOf<String>()
        for (spec in leafSpecs) {
            for (chunkId in spec.chunkIds) {
                if (chunkId in knownIds && assignedChunkIds.add(chunkId)) {
                    assignments.getValue(spec.id).add(chunkId)
                }
            }
        }

        // 2. Any chunk that no leaf claimed is placed by keyword relevance.
        for (chunk in chunks) {
            if (chunk.chunkId in assignedChunkIds) continue
            val chunkTextLower = chunk.text.lowercase()
            var bestLeafId: String? = null
            var maxScore = 0
            for (spec in leafSpecs) {
                val score = spec.keywords.count { it in chunkTextLower }
                if (score > maxScore) {
                    maxScore = score
                    bestLeafId = spec.id
                }
            }
            if (bestLeafId != null && maxScore > 0) {
                assignments.getValue(bestLeafId).add(chunk.chunkId)
                assignedChunkIds.add(chunk.chunkId)
            }
        }

        // 3. Ensure all chunks are assigned (round-robin for any unassigned chunks).
        val leftovers = chunks.filter { it.chunkId !in assignedChunkIds }
        for ((index, chunk) in leftovers.withIndex()) {
            assignments.getValue(leafSpecs[index % numLeaves].id).add(chunk.chunkId)
        }

        // 4. If any leaf has 0 chunks, ensure it receives at least one candidate chunk.
        for ((index, spec) in leafSpecs.withIndex()) {
            if (assignments.getValue(spec.id).isNotEmpty()) continue
            // Prefer a chunk that no other leaf holds, so that the normal case
            // keeps every chunk in exactly one leaf.
            val elsewhere = assignments.values.flatten().toSet()
            val spare = chunkIds.filter { it !in elsewhere }
            val pool = spare.ifEmpty { chunkIds }
            assignments.getValue(spec.id).add(pool[index % pool.size])
        }

        return assignments.mapValues { (_, ids) -> ids.toList() }
    }
}
