package lmeditor.discovery

import lmeditor.core.CodePoints
import lmeditor.core.DialogueError
import lmeditor.core.DialogueSession
import lmeditor.core.DialogueTurn
import lmeditor.core.LMNotConfiguredError
import lmeditor.core.Perspective
import lmeditor.core.ProvenanceIndexer
import lmeditor.core.ResearchMemory
import lmeditor.core.SourceChunk
import lmeditor.core.ValidationError

/**
 * The conversational research loop between perspective agents and a
 * provenance indexer.
 *
 * Port of `backend/src/lmeditor/discovery/dialogue.py`.
 *
 * The loop answers a question with the passages it retrieved and nothing
 * else. Every answer quotes the sentence of a retrieved chunk that actually
 * bears on the question and names the chunk it came from, so a reader can
 * walk from the answer back to the source. The takeaways of a session are
 * derived from the turns that were actually held: which passages carried the
 * weight, what they said, and which inquiries found nothing at all.
 *
 * When a language model is configured and [useModel] is enabled, the model
 * writes the synthesis sentence that frames the retrieved evidence. The
 * grounded quotations themselves are always added by this module, so a model
 * can add framing but can never introduce an uncited claim.
 */
class DialogueLoop(
    private val useModel: Boolean = false,
    private val topKPerTurn: Int = 3,
    private val model: DialogueModel? = null,
) {
    /**
     * Validate input boundaries for a single perspective dialogue session.
     *
     * @throws ValidationError if an argument violates a structural contract.
     */
    fun validateDialogueInputs(
        perspective: Perspective,
        indexer: ProvenanceIndexer,
        numTurns: Int,
    ) {
        if (numTurns < 1 || numTurns > 10) {
            throw ValidationError(
                "num_turns must be between 1 and 10, got $numTurns",
                field = "num_turns",
                value = numTurns,
            )
        }
    }

    /**
     * The questions a session asks, in order.
     *
     * The persona's own guiding questions come first, then one question per
     * focus area, and a session that runs past them asks the persona's
     * standing question.
     *
     * The plan is the tier's, because the plan is a rule: a persona asks the
     * same questions however the passages for them are retrieved.
     */
    fun planQuestions(perspective: Perspective, numTurns: Int): List<String> {
        val questionsPool = perspective.guidingQuestions +
            perspective.focusAreas.map { "What empirical evidence addresses $it?" }
        return (0 until numTurns).map { turnIdx ->
            if (turnIdx < questionsPool.size) {
                questionsPool[turnIdx]
            } else {
                "From the perspective of a ${perspective.role.wire}, what key trade-offs " +
                    "and operational nuances in ${perspective.domain} require deeper inquiry?"
            }
        }
    }

    /**
     * Conduct a multi-turn interactive inquiry for a specific persona.
     *
     * @param perspective the persona guiding the investigation.
     * @param indexer the provenance search indexer.
     * @param numTurns the number of Q&A conversational turns.
     * @return the structured session, with its turns and takeaways.
     * @throws ValidationError if the inputs are malformed.
     * @throws DialogueError if retrieval or synthesis fails.
     */
    fun conductDialogue(
        perspective: Perspective,
        indexer: ProvenanceIndexer,
        numTurns: Int = 3,
    ): DialogueSession {
        validateDialogueInputs(perspective, indexer, numTurns)

        val turns = mutableListOf<DialogueTurn>()

        for ((turnIdx, question) in planQuestions(perspective, numTurns).withIndex()) {
            // 2. Retrieve chunks from the indexer.
            val retrievedChunks = try {
                indexer.query(question, topK = topKPerTurn)
            } catch (exc: Exception) {
                throw DialogueError(
                    "Provenance indexer query failed at turn ${turnIdx + 1}: $exc",
                    details = mapOf(
                        "error" to exc.toString(),
                        "query" to question,
                        "perspective_id" to perspective.id,
                        "turn_index" to turnIdx,
                    ),
                )
            }

            // 3. Grounded answer synthesis.
            turns.add(synthesizeTurn(perspective, turnIdx, question, retrievedChunks))
        }

        // 4. Synthesize overarching session takeaways.
        val takeaways = extractTakeaways(perspective, turns)

        return DialogueSession(
            perspectiveId = perspective.id,
            turns = turns,
            synthesizedTakeaways = takeaways,
        )
    }

    /**
     * Build one grounded turn of a session.
     *
     * [framing] is the sentence a language model wrote for the retrieved
     * evidence. When it is absent the deterministic framing is used, so the
     * answer is the same whether the model was asked or not.
     *
     * @param perspective the persona that asked.
     * @param turnIndex the turn's position in the session, counting from zero.
     * @param question the inquiry the turn asked.
     * @param retrievedChunks the passages the index returned for it.
     * @param framing the model's framing sentence, when there was one.
     * @return the grounded turn.
     */
    fun synthesizeTurn(
        perspective: Perspective,
        turnIndex: Int,
        question: String,
        retrievedChunks: List<SourceChunk>,
        framing: String? = null,
    ): DialogueTurn {
        val synthesis = synthesizeAnswer(perspective, question, retrievedChunks, framing)
        return DialogueTurn(
            turnId = "turn_${perspective.id}_${turnIndex + 1}",
            question = question,
            answer = synthesis.text,
            referencedChunkIds = synthesis.citedIds,
        )
    }

    /**
     * Run research dialogue sessions across all discovered perspectives.
     *
     * @param perspectives the personas to interrogate.
     * @param indexer the provenance indexer.
     * @param turnsPerSession the turns per individual perspective.
     * @return the consolidated research memory.
     * @throws ValidationError if the perspectives are empty or a parameter is
     *   invalid.
     * @throws DialogueError if a dialogue fails.
     */
    fun conductAllDialogues(
        perspectives: List<Perspective>,
        indexer: ProvenanceIndexer,
        turnsPerSession: Int = 3,
    ): ResearchMemory {
        if (perspectives.isEmpty()) {
            throw ValidationError(
                "perspectives sequence cannot be empty.",
                field = "perspectives",
                value = perspectives,
            )
        }

        val sessions = mutableListOf<DialogueSession>()
        val allChunkIds = mutableListOf<String>()
        val seenChunkIds = mutableSetOf<String>()
        val collectedTakeaways = mutableListOf<String>()

        val topic = perspectives[0].domain.ifEmpty { "Document Research Topic" }

        for (persp in perspectives) {
            val session = conductDialogue(persp, indexer, numTurns = turnsPerSession)
            sessions.add(session)

            for (turn in session.turns) {
                for (cid in turn.referencedChunkIds) {
                    if (seenChunkIds.add(cid)) allChunkIds.add(cid)
                }
            }

            collectedTakeaways.addAll(session.synthesizedTakeaways)
        }

        return ResearchMemory(
            topic = topic,
            sessions = sessions,
            allReferencedChunkIds = allChunkIds,
            collectedTakeaways = collectedTakeaways,
        )
    }

    /**
     * Consolidate the sessions of a research run into one memory.
     *
     * The retrieval belongs to the Python tier -- it owns the vector index --
     * so the sessions arrive here already built, and the rule that says how
     * they are consolidated lives here: the passages are named once, in the
     * order they were first cited, and the takeaways are collected in the
     * order the sessions produced them.
     *
     * @param perspectives the personas that were interrogated, in order.
     * @param sessions the session each persona held, in the same order.
     * @return the consolidated research memory.
     */
    fun buildMemory(
        perspectives: List<Perspective>,
        sessions: List<DialogueSession>,
    ): ResearchMemory {
        val allChunkIds = mutableListOf<String>()
        val seenChunkIds = mutableSetOf<String>()
        val collectedTakeaways = mutableListOf<String>()

        for (session in sessions) {
            for (turn in session.turns) {
                for (cid in turn.referencedChunkIds) {
                    if (seenChunkIds.add(cid)) allChunkIds.add(cid)
                }
            }
            collectedTakeaways.addAll(session.synthesizedTakeaways)
        }

        val topic = perspectives.firstOrNull()?.domain?.ifEmpty { "Document Research Topic" }
            ?: "Document Research Topic"

        return ResearchMemory(
            topic = topic,
            sessions = sessions,
            allReferencedChunkIds = allChunkIds,
            collectedTakeaways = collectedTakeaways,
        )
    }

    // ----------------------------------------------------------------------
    // Answer synthesis
    // ----------------------------------------------------------------------

    /**
     * Build an answer from the retrieved passages.
     *
     * The answer quotes the passage sentences that bear on the question and
     * names the chunk each quotation came from.
     *
     * [framing] is the sentence a language model wrote for the retrieved
     * evidence. When it is absent the framing is the deterministic one, and
     * the model is asked only when this loop was built to use one.
     */
    private fun synthesizeAnswer(
        perspective: Perspective,
        question: String,
        retrievedChunks: List<SourceChunk>,
        framing: String? = null,
    ): Synthesis {
        if (retrievedChunks.isEmpty()) {
            return Synthesis(
                text = "No direct source chunks were matched in the provenance index for " +
                    "'$question'. This represents an ungrounded exploratory boundary requiring " +
                    "further factual documentation.",
                citedIds = emptyList(),
            )
        }

        val citedIds = mutableListOf<String>()
        val answerParts = mutableListOf<String>()
        val evidenceTerms = mutableListOf<String>()

        for (chunk in retrievedChunks) {
            val cid = chunk.chunkId
            if (!citedIds.contains(cid)) citedIds.add(cid)
            val quote = selectQuote(chunk, question)
            answerParts.add("According to [$cid]: \"$quote\"")
            evidenceTerms.addAll(SourceAnalysis.contentTokens(chunk.text))
        }

        val grounding = describeEvidence(evidenceTerms)

        val resolvedFraming = framing?.takeIf { it.isNotEmpty() }
            ?: if (useModel) {
                synthesizeWithModel(perspective, question, retrievedChunks)
            } else {
                "In response to the inquiry regarding '$question', the retrieved evidence $grounding:"
            }

        return Synthesis(
            text = "$resolvedFraming " + answerParts.joinToString(" "),
            citedIds = citedIds,
        )
    }

    /** Pick the sentence of [chunk] that bears most on [question]. */
    private fun selectQuote(chunk: SourceChunk, question: String): String {
        val sentences = SourceAnalysis.splitSentences(chunk.text)
            .ifEmpty { listOf(SourceAnalysis.strip(chunk.text)) }

        val questionTokens = SourceAnalysis.contentTokens(question).toSet()
        var bestSentence = sentences[0]
        var bestScore = -1
        for (sentence in sentences) {
            val sentenceTokens = SourceAnalysis.contentTokens(sentence).toSet()
            if (sentenceTokens.isEmpty()) continue
            val score = questionTokens.intersect(sentenceTokens).size
            if (score > bestScore) {
                bestScore = score
                bestSentence = sentence
            }
        }

        var quote = SourceAnalysis.collapseWhitespace(bestSentence)
        if (CodePoints.length(quote) > 240) quote = CodePoints.slice(quote, 0, 237) + "..."
        return quote
    }

    /** Describe what the retrieved passages collectively cover. */
    private fun describeEvidence(tokens: List<String>): String {
        val counts = LinkedHashMap<String, Int>()
        for (token in tokens) {
            if (token in SourceAnalysis.GENERIC_TERMS) continue
            counts[token] = (counts[token] ?: 0) + 1
        }
        if (counts.isEmpty()) return "reports the following statements"

        val top = counts.entries
            .sortedByDescending { it.value }
            .take(3)
            .map { it.key }
        return "centres on ${top.joinToString(", ")} and reports the following statements"
    }

    /**
     * Frame the retrieved evidence with the configured language model.
     *
     * The model only writes the framing sentence. The grounded quotations
     * are appended by the caller, so the model cannot introduce an uncited
     * claim.
     */
    private fun synthesizeWithModel(
        perspective: Perspective,
        question: String,
        retrievedChunks: List<SourceChunk>,
    ): String {
        val lm = model ?: throw LMNotConfiguredError("dialogue answer synthesis")

        val passages = retrievedChunks.mapIndexed { index, chunk ->
            "--- PASSAGE ${index + 1} [${chunk.chunkId}] ---\n${chunk.text}"
        }.joinToString("\n\n")

        val framing = try {
            lm.frameAnswer(question, passages, perspective.role.wire).trim()
        } catch (exc: Exception) {
            throw DialogueError(
                "Language model failed to synthesise the answer: $exc",
                details = mapOf(
                    "error" to exc.toString(),
                    "query" to question,
                    "perspective_id" to perspective.id,
                ),
            )
        }

        if (framing.isEmpty()) {
            throw DialogueError(
                "Language model returned an empty synthesis for the retrieved evidence.",
                details = mapOf("query" to question, "perspective_id" to perspective.id),
            )
        }
        return framing
    }

    // ----------------------------------------------------------------------
    // Takeaway synthesis
    // ----------------------------------------------------------------------

    /**
     * Derive the session takeaways from the turns that were actually held.
     *
     * A takeaway names the evidence that carried the session: the passage
     * that was cited most, what it said, what the retrieved passages had in
     * common, and which inquiries came back empty.
     */
    fun extractTakeaways(
        perspective: Perspective,
        turns: List<DialogueTurn>,
    ): List<String> {
        val takeaways = mutableListOf<String>()
        val roleLabel = pythonTitle(perspective.role.wire.replace("_", " "))

        val evidence = mutableListOf<Pair<String, String>>()
        for (turn in turns) {
            for (cid in turn.referencedChunkIds) {
                val quote = quoteForCid(turn.answer, cid)
                if (quote.isNotEmpty()) evidence.add(cid to quote)
            }
        }

        if (evidence.isEmpty()) {
            takeaways.add(
                "[$roleLabel] Found no source passage for ${perspective.domain}; " +
                    "the inquiries in this session remain empirically unsupported.",
            )
            return takeaways
        }

        val counts = LinkedHashMap<String, Int>()
        for ((cid, _) in evidence) counts[cid] = (counts[cid] ?: 0) + 1

        // The most cited passage. Python's `max` over `(count, chunk id)`
        // keeps the *greatest* chunk id when two passages were cited equally
        // often, so the tie-break here is the same one.
        val dominantCid = counts.entries
            .sortedWith(
                compareByDescending<Map.Entry<String, Int>> { it.value }
                    .thenByDescending { it.key },
            )
            .first()
            .key
        val dominantQuote = evidence.first { (cid, _) -> cid == dominantCid }.second

        takeaways.add(
            "[$roleLabel] The passage that carried this session was [$dominantCid], " +
                "which states: \"$dominantQuote\"",
        )

        val terms = SourceAnalysis.salientTerms(
            evidence.joinToString(" ") { (_, quote) -> quote },
            limit = 4,
        )
        if (terms.isNotEmpty()) {
            takeaways.add(
                "[$roleLabel] Across the retrieved evidence the recurring substance is " +
                    "${terms.joinToString(", ")}, which bounds what ${perspective.domain} " +
                    "can be claimed to show.",
            )
        }

        val groundedTurns = turns.count { it.referencedChunkIds.isNotEmpty() }
        if (groundedTurns < turns.size) {
            takeaways.add(
                "[$roleLabel] ${turns.size - groundedTurns} of ${turns.size} inquiries returned no " +
                    "matching passage and remain open.",
            )
        } else {
            takeaways.add(
                "[$roleLabel] Every one of the ${turns.size} inquiries was answered from a " +
                    "retrieved passage.",
            )
        }

        if (perspective.focusAreas.isNotEmpty()) {
            takeaways.add(
                "[$roleLabel] Prioritised analytical focus on: ${perspective.focusAreas[0]}.",
            )
        }

        return takeaways
    }

    /** The quotation the answer attributes to [chunkId]. */
    private fun quoteForCid(answer: String, chunkId: String): String {
        val marker = "[$chunkId]"
        val index = answer.indexOf(marker)
        if (index == -1) return ""
        val start = answer.indexOf('"', index)
        if (start == -1) return ""
        val end = answer.indexOf('"', start + 1)
        if (end == -1) return ""
        return SourceAnalysis.strip(answer.substring(start + 1, end))
    }

    /** Python's `str.title()`: capitalise the first letter of every word. */
    private fun pythonTitle(text: String): String {
        val result = StringBuilder(text.length)
        var previousWasLetter = false
        for (ch in text) {
            if (ch.isLetter()) {
                result.append(if (previousWasLetter) ch.lowercaseChar() else ch.uppercaseChar())
                previousWasLetter = true
            } else {
                result.append(ch)
                previousWasLetter = false
            }
        }
        return result.toString()
    }
}

/** An answer and the chunks it cites. */
private data class Synthesis(val text: String, val citedIds: List<String>)
