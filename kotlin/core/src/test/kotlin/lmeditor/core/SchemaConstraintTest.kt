package lmeditor.core

import kotlin.test.Test
import kotlin.test.assertFailsWith

/**
 * The Kotlin models must refuse what the Python models refuse.
 *
 * The Python tier's models are Pydantic models, and Pydantic refuses a value
 * that breaks a constraint: a negative offset, an empty id, a score outside
 * `[0, 1]`. The Kotlin tier has to refuse the same values. A model that one
 * tier accepts and the other refuses is a model that reaches the audit trail
 * in one form and not in the other, and the tier exists to make that
 * impossible.
 *
 * Every check below has a counterpart in the Python model, on the same field.
 */
class SchemaConstraintTest {

    @Test
    fun `a chunk refuses a negative offset`() {
        assertFailsWith<ValidationError> {
            SourceChunk(
                chunkId = "a".repeat(64),
                documentId = "doc-1",
                text = "A passage.",
                charStart = -1,
                charEnd = 10,
            )
        }
        assertFailsWith<ValidationError> {
            SourceChunk(
                chunkId = "a".repeat(64),
                documentId = "doc-1",
                text = "A passage.",
                byteEnd = -1,
            )
        }
    }

    @Test
    fun `a claim refuses a negative offset`() {
        assertFailsWith<ValidationError> {
            Claim(claimId = "claim-1", sectionId = "sec-1", text = "A claim.", charStart = -1)
        }
        assertFailsWith<ValidationError> {
            Claim(claimId = "claim-1", sectionId = "sec-1", text = "A claim.", charEnd = -1)
        }
    }

    @Test
    fun `an inline citation refuses a negative end`() {
        assertFailsWith<ValidationError> {
            InlineCitation(citationId = "cit_0001", chunkId = "a".repeat(64), spanEnd = -1)
        }
    }

    @Test
    fun `a section draft refuses an empty title`() {
        assertFailsWith<ValidationError> {
            SectionDraft(sectionId = "sec-1", title = "")
        }
    }

    @Test
    fun `a research memory refuses an empty topic`() {
        assertFailsWith<ValidationError> {
            ResearchMemory(topic = "")
        }
    }

    @Test
    fun `a verification report refuses an empty section and a negative count`() {
        assertFailsWith<ValidationError> {
            VerificationReport(
                reportId = "report-1",
                sectionId = "",
                totalClaims = 0,
                entailedClaims = 0,
                contradictedClaims = 0,
                ungroundedClaims = 0,
                precision = 1.0,
                status = EntailmentStatus.ENTAILED,
                allPassed = true,
                summary = "All good.",
            )
        }
        assertFailsWith<ValidationError> {
            VerificationReport(
                reportId = "report-1",
                sectionId = "sec-1",
                totalClaims = 0,
                entailedClaims = -1,
                contradictedClaims = 0,
                ungroundedClaims = 0,
                precision = 1.0,
                status = EntailmentStatus.ENTAILED,
                allPassed = true,
                summary = "All good.",
            )
        }
    }

    @Test
    fun `a scorecard refuses a negative count`() {
        assertFailsWith<ValidationError> {
            VerificationScorecard(
                precision = 1.0,
                recall = 1.0,
                fBeta = 1.0,
                supportedClaims = -1,
            )
        }
    }

    @Test
    fun `an inline edit result refuses an empty document or section`() {
        assertFailsWith<ValidationError> {
            InlineEditResult(docId = "", sectionId = "sec-1")
        }
        assertFailsWith<ValidationError> {
            InlineEditResult(docId = "doc-1", sectionId = "")
        }
    }

    @Test
    fun `a claim verdict refuses an empty claim id`() {
        assertFailsWith<ValidationError> {
            ClaimVerdict(claimId = "", status = EntailmentStatus.ENTAILED)
        }
    }

    @Test
    fun `a fact check report refuses an empty document id`() {
        assertFailsWith<ValidationError> {
            FactCheckReport(docId = "")
        }
    }

    @Test
    fun `a book section refuses a negative count`() {
        assertFailsWith<ValidationError> {
            BookSection(sectionId = "sec-1", wordCount = -1)
        }
        assertFailsWith<ValidationError> {
            BookSection(sectionId = "sec-1", charCount = -1)
        }
    }

    @Test
    fun `a working set section refuses a negative order`() {
        assertFailsWith<ValidationError> {
            WorkingSetSection(sectionId = "sec-1", order = -1)
        }
    }

    @Test
    fun `a book section save result refuses a negative count`() {
        assertFailsWith<ValidationError> {
            BookSectionSaveResult(sectionId = "sec-1", wordCount = -1)
        }
    }
}
