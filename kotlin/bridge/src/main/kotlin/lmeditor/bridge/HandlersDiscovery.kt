package lmeditor.bridge

import lmeditor.core.DialogueSession
import lmeditor.core.Perspective
import lmeditor.core.json.jsonInt
import lmeditor.core.json.jsonObject
import lmeditor.core.json.jsonObjectList
import lmeditor.core.json.jsonString
import lmeditor.core.json.jsonStringList
import lmeditor.core.perspectiveFromWire
import lmeditor.core.sourceChunkFromWire
import lmeditor.core.toWireMap
import lmeditor.discovery.DialogueLoop
import lmeditor.discovery.SourceAnalysis

/**
 * The discovery methods of the seam.
 *
 * The lexical analysis of the corpus -- its vocabulary, the findings its
 * sentences state, the headings it already carries, the way its passages
 * cluster -- is a rule, and the rule is Kotlin's: it is implemented once, in
 * `lmeditor.discovery`, and the tier serves it across the NDJSON seam. The
 * Python modules that used to carry it are doors: they keep the Python API
 * and answer with the tier's answers.
 *
 * The dialogue loop is served in pieces, because the retrieval is Python's:
 * the vector index lives in the Python tier, so the passages a turn saw
 * travel across the seam as the passages themselves. The plan of the
 * questions, the grounded answer and the takeaways are the tier's.
 *
 * The language model is Python's too. A model writes the framing sentence of
 * an answer; the tier quotes the passages underneath it, so a model can add
 * framing but can never introduce an uncited claim. The `framing` field of a
 * turn carries the model's sentence when there was one, and the tier uses
 * its own deterministic framing when there was not.
 */
internal fun discoveryHandlers(): Map<String, (Map<String, Any?>) -> Map<String, Any?>> = mapOf(
    // --- the lexical analysis of the corpus ------------------------------
    "discovery.source.tokenize" to { params ->
        mapOf("tokens" to SourceAnalysis.tokenize(params.jsonString("text")))
    },
    "discovery.source.contentTokens" to { params ->
        mapOf("tokens" to SourceAnalysis.contentTokens(params.jsonString("text")))
    },
    "discovery.source.salientTerms" to { params ->
        mapOf(
            "terms" to SourceAnalysis.salientTerms(
                text = params.jsonString("text"),
                limit = params.jsonInt("limit", 12),
            ),
        )
    },
    "discovery.source.salientTermsAcross" to { params ->
        mapOf(
            "terms" to SourceAnalysis.salientTermsAcross(
                texts = params.jsonStringList("texts"),
                limit = params.jsonInt("limit", 12),
            ),
        )
    },
    "discovery.source.splitSentences" to { params ->
        mapOf("sentences" to SourceAnalysis.splitSentences(params.jsonString("text")))
    },
    "discovery.source.extractFindings" to { params ->
        mapOf(
            "findings" to SourceAnalysis.extractFindings(
                text = params.jsonString("text"),
                limit = params.jsonInt("limit", 6),
            ),
        )
    },
    "discovery.source.detectHeadings" to { params ->
        mapOf("headings" to SourceAnalysis.detectHeadings(params.jsonString("text")))
    },
    "discovery.source.titleCaseTerms" to { params ->
        mapOf("title" to SourceAnalysis.titleCaseTerms(params.jsonStringList("terms")))
    },
    "discovery.source.clusterByTerms" to { params ->
        mapOf(
            "clusters" to SourceAnalysis.clusterByTerms(
                texts = params.jsonStringList("texts"),
                numClusters = params.jsonInt("num_clusters", 1),
            ),
        )
    },

    // --- the dialogue loop ------------------------------------------------
    "discovery.dialogue.questions" to { params ->
        val perspective = perspectiveFromWire(params.jsonObject("perspective"))
        mapOf(
            "questions" to DialogueLoop().planQuestions(
                perspective = perspective,
                numTurns = params.jsonInt("num_turns", 3),
            ),
        )
    },
    "discovery.dialogue.memory" to { params ->
        val loop = DialogueLoop()
        val planned = params.jsonObjectList("perspectives").map { entry ->
            val perspective = perspectiveFromWire(entry.jsonObject("perspective"))
            val turns = entry.jsonObjectList("turns").mapIndexed { index, turn ->
                loop.synthesizeTurn(
                    perspective = perspective,
                    turnIndex = index,
                    question = turn.jsonString("question"),
                    retrievedChunks = turn.jsonObjectList("chunks").map { sourceChunkFromWire(it) },
                    // An absent framing means the tier writes its own.
                    framing = turn.jsonString("framing").ifEmpty { null },
                )
            }
            PlannedSession(perspective, turns, loop.extractTakeaways(perspective, turns))
        }
        val perspectives = planned.map { it.perspective }
        val sessions = planned.map { entry ->
            DialogueSession(
                perspectiveId = entry.perspective.id,
                turns = entry.turns,
                synthesizedTakeaways = entry.takeaways,
            )
        }
        mapOf("memory" to loop.buildMemory(perspectives, sessions).toWireMap())
    },
)

/** One persona, the turns it held, and the takeaways that session produced. */
private class PlannedSession(
    val perspective: Perspective,
    val turns: List<lmeditor.core.DialogueTurn>,
    val takeaways: List<String>,
)
