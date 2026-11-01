package lmeditor.vault

import lmeditor.core.ValidationError
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/**
 * The ranked search over a vault.
 */
class VaultSearchTest {

    /** A title hit outranks a body hit. */
    @Test
    fun aTitleHitOutranksABodyHit() {
        val store = VaultStore(newVault())
        store.writeNote("body-hit", "# Other\n\nthe alpha project\n")
        store.writeNote("title-hit", "# Alpha Project\n\nthe body\n")
        val hits = store.search("alpha", 10)
        assertEquals(listOf("title-hit", "body-hit"), hits.map { it.noteId })
        assertEquals(VaultSearch.TITLE_WEIGHT, hits[0].score)
        assertEquals(VaultSearch.BODY_WEIGHT, hits[1].score)
        assertEquals(listOf("alpha"), hits[0].matchedTerms)
    }

    /** A tag hit outranks a body hit. */
    @Test
    fun aTagHitOutranksABodyHit() {
        val store = VaultStore(newVault())
        store.writeNote("body-hit", "# Other\n\nthe alpha project\n")
        store.writeNote("tag-hit", "# Other\n\n#alpha\n")
        val hits = store.search("alpha", 10)
        assertEquals(listOf("tag-hit", "body-hit"), hits.map { it.noteId })
    }

    /** The order of the hits is the same every time. */
    @Test
    fun theOrderOfTheHitsIsTheSameEveryTime() {
        val store = VaultStore(newVault())
        store.writeNote("b-note", "# Note\n\nalpha\n")
        store.writeNote("a-note", "# Note\n\nalpha\n")
        val first = store.search("alpha", 10).map { it.noteId }
        val second = store.search("alpha", 10).map { it.noteId }
        assertEquals(listOf("a-note", "b-note"), first)
        assertEquals(first, second)
    }

    /** A note that carries every term outranks one that carries some. */
    @Test
    fun aNoteThatCarriesEveryTermOutranksOneThatCarriesSome() {
        val store = VaultStore(newVault())
        store.writeNote("some", "# Note\n\nalpha only\n")
        store.writeNote("every", "# Note\n\nalpha and beta\n")
        val hits = store.search("alpha beta", 10)
        assertEquals(listOf("every", "some"), hits.map { it.noteId })
        assertEquals(listOf("alpha", "beta"), hits[0].matchedTerms)
        assertEquals(listOf("alpha"), hits[1].matchedTerms)
    }

    /** The limit bounds the number of hits. */
    @Test
    fun theLimitBoundsTheNumberOfHits() {
        val store = VaultStore(newVault())
        store.writeNote("one", "# One\n\nalpha\n")
        store.writeNote("two", "# Two\n\nalpha\n")
        assertEquals(1, store.search("alpha", 1).size)
    }

    /** A search that matches nothing returns nothing. */
    @Test
    fun aSearchThatMatchesNothingReturnsNothing() {
        val store = VaultStore(newVault())
        store.writeNote("one", "# One\n\nalpha\n")
        assertEquals(emptyList(), store.search("nothing", 10))
    }

    /** A limit that is not a positive number is refused. */
    @Test
    fun aLimitThatIsNotPositiveIsRefused() {
        val store = VaultStore(newVault())
        assertFailsWith<ValidationError> { store.search("alpha", 0) }
    }
}
