package lmeditor.text

import lmeditor.core.Chunker
import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

/**
 * The chunker rules, ported from `backend/tests/test_provenance.py`.
 *
 * The digests and offsets pinned here were produced by the Python tier; they
 * are the facts the two tiers must agree on.
 */
class ChunkerTest {

    @Test
    fun theChunkIdIsDeterministic() {
        val documentId = "doc_01"
        val text = "The Stanford STORM paper presents multi-perspective discovery."
        val first = computeChunkId(documentId, 0, text.length, text)
        val second = computeChunkId(documentId, 0, text.length, text)

        assertEquals(first, second)
        assertEquals(64, first.length)
        assertTrue(first.matches(Regex("^[a-f0-9]{64}$")))

        // Different bounds must yield different chunk ids.
        assertNotEquals(first, computeChunkId(documentId, 1, text.length, text))
    }

    @Test
    fun theChunkIdMatchesThePythonDigestExactly() {
        // Pinned against the Python tier:
        //   hashlib.sha256("doc_01:0:59:The Stanford STORM paper presents
        //   multi-perspective discovery.".encode("utf-8")).hexdigest()
        assertEquals(
            "6525383a9a25e4cbf9d41b7b40fce7bfffd2f322aaba9c4d798f47061d9ae105",
            computeChunkId(
                "doc_01",
                0,
                59,
                "The Stanford STORM paper presents multi-perspective discovery.",
            ),
        )
    }

    @Test
    fun aChunkIdMintedInPythonResolvesInKotlin() {
        // Pinned against the Python tier for the payload "doc_01:0:6:cafe 🚀",
        // whose sixth character is an astral-plane emoji.
        val text = "café 🚀"
        assertEquals(
            "f2855afa64de72415f9cb237b595e4a5e23af401bf6c3a4848d042c024b64cc3",
            computeChunkId("doc_01", 0, 6, text),
        )
    }

    @Test
    fun byteOffsetsAreUtf8LengthsNotCharacterCounts() {
        val asciiText = "Hello world"
        val (asciiStart, asciiEnd) = getByteOffsets(asciiText, 0, 5)
        assertEquals(0, asciiStart)
        assertEquals(5, asciiEnd)
        assertEquals("Hello", asciiText.toByteArray(Charsets.UTF_8).copyOfRange(asciiStart, asciiEnd).decodeToString())

        // Multi-byte UTF-8: 'café' (é is 2 bytes), '🚀' is 4 bytes.
        val multiByteText = "café 🚀 rocket"
        // 'café' chars [0:4] -> bytes: 'c'(1) + 'a'(1) + 'f'(1) + 'é'(2) = 5 bytes.
        val (start, end) = getByteOffsets(multiByteText, 0, 4)
        assertEquals(0, start)
        assertEquals(5, end)

        // The span covering the emoji: character 5 is '🚀', four bytes wide.
        val (emojiStart, emojiEnd) = getByteOffsets(multiByteText, 5, 6)
        assertEquals(6, emojiStart)
        assertEquals(10, emojiEnd)
        assertEquals(
            "🚀",
            multiByteText.toByteArray(Charsets.UTF_8).copyOfRange(emojiStart, emojiEnd).decodeToString(),
        )
    }

    @Test
    fun theSlidingWindowChunkerValidatesItsInput() {
        assertTrue(
            assertFailsWith<ValidationError> { SlidingWindowChunker(windowSize = 0) }
                .message.contains("window_size must be positive, got 0"),
        )
        assertFailsWith<ValidationError> { SlidingWindowChunker(windowSize = -10) }
        assertTrue(
            assertFailsWith<ValidationError> {
                SlidingWindowChunker(windowSize = 100, overlapSize = -1)
            }.message.contains("overlap_size must be non-negative"),
        )
        assertFailsWith<ValidationError> { SlidingWindowChunker(windowSize = 100, overlapSize = 100) }

        val chunker = SlidingWindowChunker(windowSize = 100, overlapSize = 20)
        assertTrue(
            assertFailsWith<ValidationError> { chunker.chunk("", "doc_1") }
                .message.contains("text cannot be empty"),
        )
        assertFailsWith<ValidationError> { chunker.chunk("   ", "doc_1") }
        assertTrue(
            assertFailsWith<ValidationError> { chunker.chunk("Some valid content", "") }
                .message.contains("document_id cannot be empty"),
        )
    }

    @Test
    fun theSlidingWindowChunksCarryTheirOwnSourceText() {
        val text = (
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu " +
                "nu xi omicron pi rho sigma tau upsilon phi chi psi omega. " +
                "A second sentence exploring LLM citation mechanisms and verifiable provenance."
            )
        val chunker = SlidingWindowChunker(windowSize = 50, overlapSize = 15)
        val chunks = chunker.chunk(text, "doc_integrity", mapOf("source" to "test"))

        assertTrue(chunks.size > 1)
        for (chunk in chunks) {
            // The crucial invariant: the chunk is the slice it claims to be.
            assertEquals(TextOffsets.slice(text, chunk.charStart, chunk.charEnd), chunk.text)
            // The byte boundary invariant.
            val encoded = text.toByteArray(Charsets.UTF_8)
            assertEquals(
                chunk.text.toByteArray(Charsets.UTF_8).decodeToString(),
                encoded.copyOfRange(chunk.byteStart, chunk.byteEnd).decodeToString(),
            )
            assertEquals("doc_integrity", chunk.documentId)
            assertEquals(mapOf<String, Any?>("source" to "test"), chunk.metadata)
        }
    }

    @Test
    fun theSlidingWindowChunkBoundariesMatchThePythonTier() {
        val text = (
            "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu " +
                "nu xi omicron pi rho sigma tau upsilon phi chi psi omega. " +
                "A second sentence exploring LLM citation mechanisms and verifiable provenance."
            )
        val chunker = SlidingWindowChunker(windowSize = 50, overlapSize = 15)
        val chunks = chunker.chunk(text, "doc_integrity")

        // Pinned against the Python tier.
        assertEquals(
            listOf(0, 35, 70, 105, 140, 175),
            chunks.map { it.charStart },
        )
        assertEquals(
            listOf(50, 85, 120, 155, 190, 203),
            chunks.map { it.charEnd },
        )
        assertEquals(
            listOf(13, 13, 13, 13, 13, 7),
            chunks.map { it.tokenCount },
        )
        assertEquals(
            "5997124d2de537d7fcca84b8f215cd094f8f5a477e61892f9c46ac74521bb565",
            chunks.first().chunkId,
        )
    }

    @Test
    fun theSemanticChunkerValidatesItsInput() {
        assertTrue(
            assertFailsWith<ValidationError> { SemanticChunker(maxChunkSize = 100, minChunkSize = 0) }
                .message.contains("min_chunk_size must be positive"),
        )
        assertTrue(
            assertFailsWith<ValidationError> { SemanticChunker(maxChunkSize = 50, minChunkSize = 100) }
                .message.contains("greater than or equal to min_chunk_size"),
        )

        val chunker = SemanticChunker(maxChunkSize = 200, minChunkSize = 50)
        assertFailsWith<ValidationError> { chunker.chunk("", "doc_1") }
        assertFailsWith<ValidationError> { chunker.chunk("Valid text", "  ") }
    }

    @Test
    fun theSemanticChunkerSplitsOnParagraphsAndKeepsItsSlices() {
        val text = (
            "First paragraph sentence one. First paragraph sentence two with more details.\n\n" +
                "Second paragraph begins here. It contains another factual claim about DSPy.\n\n" +
                "Third paragraph discusses LangGraph orchestration and human in the loop checkpoints."
            )
        val chunker = SemanticChunker(maxChunkSize = 120, minChunkSize = 30)
        val chunks = chunker.chunk(text, "doc_semantic")

        assertTrue(chunks.size >= 2)
        for (chunk in chunks) {
            assertEquals(TextOffsets.slice(text, chunk.charStart, chunk.charEnd), chunk.text)
            val encoded = text.toByteArray(Charsets.UTF_8)
            assertEquals(
                chunk.text.toByteArray(Charsets.UTF_8).decodeToString(),
                encoded.copyOfRange(chunk.byteStart, chunk.byteEnd).decodeToString(),
            )
        }

        // Pinned against the Python tier.
        assertEquals(listOf(0, 109, 156), chunks.map { it.charStart })
        assertEquals(listOf(108, 154, 240), chunks.map { it.charEnd })
    }

    @Test
    fun aChunkCarriesATokenEstimate() {
        val chunker = SentenceAwareChunker(chunkSize = 200, overlapSize = 20)
        val text = "A sentence with a handful of words in it. Another sentence follows it."
        val chunks = chunker.chunk(text, "doc_tokens")

        assertTrue(chunks.isNotEmpty())
        for (chunk in chunks) {
            assertTrue(chunk.tokenCount > 0)
            assertEquals(estimateTokenCount(chunk.text), chunk.tokenCount)
        }
    }

    @Test
    fun theTokenEstimateIsAnEstimate() {
        // The estimate is a character-based approximation, not a tokenizer count.
        assertEquals(0, estimateTokenCount(""))
        assertEquals(1, estimateTokenCount("four"))
        assertEquals(100, estimateTokenCount("x".repeat(400)))
    }

    @Test
    fun theSourceChunkerNeverCutsASentenceInHalf() {
        val sentences = (1..8).map { "Sentence number $it carries a little weight." }
        val text = sentences.joinToString(" ")

        val chunker = SentenceAwareChunker(chunkSize = 60, overlapSize = 0)
        val chunks = chunker.chunk(text, "doc_sentences")

        assertTrue(chunks.isNotEmpty())
        for (chunk in chunks) {
            assertTrue(chunk.text.trim() in sentences)
        }
    }

    @Test
    fun theSourceChunkerBoundariesMatchThePythonTier() {
        val sentences = (1..8).map { "Sentence number $it carries a little weight." }
        val text = sentences.joinToString(" ")

        val chunker = SentenceAwareChunker(chunkSize = 60, overlapSize = 0)
        val chunks = chunker.chunk(text, "doc_sentences")

        // Pinned against the Python tier.
        assertEquals(listOf(0, 43, 86, 129, 172, 215, 258, 301), chunks.map { it.charStart })
        assertEquals(listOf(43, 86, 129, 172, 215, 258, 301, 343), chunks.map { it.charEnd })
        assertEquals(
            "8823ab399beac53e13f197ef23ad1b5f6c89d48ac1f51f4b666c290f03d6ddc9",
            chunks.first().chunkId,
        )
    }

    @Test
    fun theOverlapRepeatsWholeSentences() {
        val sentences = (1..8).map { "Sentence number $it carries a little weight." }
        val text = sentences.joinToString(" ")

        val chunker = SentenceAwareChunker(chunkSize = 60, overlapSize = 35)
        val chunks = chunker.chunk(text, "doc_overlap")

        assertTrue(chunks.size > 1)
        for (chunk in chunks) {
            for (sentence in sentences) {
                if (sentence in chunk.text) {
                    // A sentence that appears must appear whole.
                    assertEquals(1, Regex(Regex.escape(sentence)).findAll(chunk.text).count())
                }
            }
        }
    }

    @Test
    fun theSourceChunkerAliasPointsAtTheSourceAwareChunker() {
        // The old name no longer promises more than the chunker delivers.
        val chunker: SourceChunker = SentenceAwareChunker(chunkSize = 200, overlapSize = 20)
        assertTrue(chunker is SentenceAwareChunker)
    }

    @Test
    fun theChunkersSatisfyTheCoreChunkerInterface() {
        val chunker: Chunker = SentenceAwareChunker(chunkSize = 200, overlapSize = 20)
        val chunks = chunker.chunk("A sentence about grounding.", "doc_interface")
        assertEquals(1, chunks.size)
        assertEquals("A sentence about grounding.", chunks.first().text)
    }

    @Test
    fun theSourceAwareChunkerRejectsAnImpossibleWindow() {
        assertFailsWith<ValidationError> { SentenceAwareChunker(chunkSize = 0) }
        assertFailsWith<ValidationError> { SentenceAwareChunker(chunkSize = 100, overlapSize = 100) }
    }

    @Test
    fun theChunkIdsOfAnAstralTextMatchThePythonTier() {
        // The text holds a two-byte character and an astral-plane emoji, so
        // the character offsets of the two tiers only agree when they are
        // counted in code points. The offsets and the digests below were
        // produced by the Python tier.
        val text = "Café 🚀 launches. A second sentence follows here. And a third one."

        val sentenceAware = SentenceAwareChunker(chunkSize = 40, overlapSize = 10)
            .chunk(text, "doc_astral")
        assertEquals(listOf(0, 17, 49), sentenceAware.map { it.charStart })
        assertEquals(listOf(17, 49, 65), sentenceAware.map { it.charEnd })
        assertEquals(listOf(0, 21, 53), sentenceAware.map { it.byteStart })
        assertEquals(listOf(21, 53, 69), sentenceAware.map { it.byteEnd })
        assertEquals(
            listOf(
                "d371793d76fe009be4fd406fb8a3f3e0cd7a9e1273bd4b8cc1915626df24d535",
                "b157fca6525507539f33dd1eb3d8e93597ed9bc0e7f3a49c8b832442158b6d1c",
                "ad6b856559bec7c0b0f0b784c81d3ddc97594bacdb99162a4d669564e193872c",
            ),
            sentenceAware.map { it.chunkId },
        )

        val sliding = SlidingWindowChunker(windowSize = 20, overlapSize = 5)
            .chunk(text, "doc_astral")
        assertEquals(listOf(0, 15, 30, 45), sliding.map { it.charStart })
        assertEquals(listOf(20, 35, 50, 65), sliding.map { it.charEnd })
        assertEquals(listOf(0, 19, 34, 49), sliding.map { it.byteStart })
        assertEquals(listOf(24, 39, 54, 69), sliding.map { it.byteEnd })
        assertEquals(
            listOf(
                "9e170f833c9729e7f21fb8414954dad54a220a85a4ea2379a3165fb78dd221d6",
                "f26b666f47315b5c5e630392b454ede01eafdf60551a8bace0b8c4ce389210b6",
                "b663917b0a167d1e8362583d2de7f148a44782c5debc38bcc5d1d03e92cd7ef2",
                "f7b312c6f03629c51c1f80cb4d0645e9f9c482ccff7b8221093d56222b3bff9a",
            ),
            sliding.map { it.chunkId },
        )

        val semantic = SemanticChunker(maxChunkSize = 45, minChunkSize = 10)
            .chunk(text, "doc_astral")
        assertEquals(listOf(0, 17, 49), semantic.map { it.charStart })
        assertEquals(listOf(16, 48, 65), semantic.map { it.charEnd })
        assertEquals(
            listOf(
                "bebccdee4cb3b8725329ffa18a07879c3f17cbafb9c59c0c36046886128992cf",
                "3b45f856d4dd3b5fcbbe04c5aa0042e83b6805fccb34b8991ffb8e4633eb8b57",
                "ad6b856559bec7c0b0f0b784c81d3ddc97594bacdb99162a4d669564e193872c",
            ),
            semantic.map { it.chunkId },
        )
    }
}
