package lmeditor.notebooks

import lmeditor.core.stripModelWhitespace

/**
 * The id rules of a notebook: what a notebook id may be, and how one is
 * derived from a title.
 *
 * A notebook id is the name of the notebook's own directory, and it travels
 * in a URL path segment. It is therefore a **single safe path segment**: one
 * name, with nothing in it that could step outside the notebooks root or
 * that a filesystem would refuse.
 *
 * The rule is the reconciliation of the two rules the tier already owns:
 *
 * - the library's `validateIdentifier`, which guards a book's directory and
 *   a section's file name;
 * - the vault's `VaultPath.validateNoteId`, which guards a note's path
 *   inside a vault.
 *
 * Both are kept where they apply, and the two places they disagree are
 * settled in favour of the stricter rule, because a notebook id has to be
 * safe on every platform the operator may be on:
 *
 * - **Control characters.** The library forbids the NUL character; the vault
 *   forbids every control character. The vault's rule wins: a control
 *   character in a directory name is refused by Windows itself and by every
 *   path parser, so a name that carries one is unsafe everywhere, not just
 *   on one platform.
 * - **A trailing dot or a space.** The library allows both; the vault
 *   refuses a segment that ends with either. The vault's rule wins, and not
 *   out of pedantry: Windows silently strips a trailing dot and a trailing
 *   space from a directory name, so the notebooks `notes` and `notes.` would
 *   be one directory under two names, and the second id would read the first
 *   one's manifest.
 * - **A hidden name.** The vault refuses a name that begins with a dot,
 *   because Obsidian keeps its own `.obsidian` folder inside a vault and a
 *   vault must never read the application's bookkeeping as if it were the
 *   operator's notes. A notebooks root is not a vault and holds no
 *   application folder of its own, so a leading dot is allowed here. The two
 *   relative segments `.` and `..` are refused anyway, so an id can never
 *   name the root itself or its parent.
 *
 * Everything else the two rules agree on: not blank, not `.` or `..`, no path
 * separator, no drive letter, none of the characters a Windows path forbids,
 * not a reserved device name, and at most [MAX_ID_LENGTH] characters.
 */
object NotebookPath {

    /** The longest a notebook id may be. */
    const val MAX_ID_LENGTH: Int = 128

    /** The id a notebook gets when its title yields none. */
    const val FALLBACK_ID: String = "notebook"

    /** The characters a Windows path refuses in a file name. */
    private val FORBIDDEN_CHARS: Set<Char> = setOf('<', '>', ':', '"', '|', '?', '*')

    /** Device names Windows reserves; `NUL.json` is the null device, not a file. */
    private val WINDOWS_RESERVED_NAMES: Set<String> =
        setOf("CON", "PRN", "AUX", "NUL") +
            (1..9).map { "COM$it" } +
            (1..9).map { "LPT$it" }

    /** A run of characters a derived id does not keep. */
    private val NON_SLUG_RUN: Regex = Regex("[^a-z0-9]+")

    /**
     * Return [notebookId] as a safe path segment, or raise.
     *
     * The surrounding whitespace is stripped first, the way every string the
     * core models carry is stripped, so the id that is validated is the id
     * that is written to disk and the one the manifest records.
     *
     * @param notebookId the notebook id the caller named
     * @return the id, stripped of surrounding whitespace
     * @throws UnsafeNotebookIdError the id is not a safe path segment
     */
    fun validateNotebookId(notebookId: String): String {
        val id = notebookId.stripModelWhitespace()
        if (id.isEmpty()) {
            throw UnsafeNotebookIdError(notebookId, "it must not be blank")
        }
        if (id.length > MAX_ID_LENGTH) {
            throw UnsafeNotebookIdError(
                notebookId,
                "it exceeds the maximum length of $MAX_ID_LENGTH characters",
            )
        }
        if (id == "." || id == "..") {
            throw UnsafeNotebookIdError(notebookId, "it is a relative path segment")
        }
        if (id.length >= 2 && id[1] == ':') {
            throw UnsafeNotebookIdError(notebookId, "it carries a drive letter")
        }
        val forbidden = FORBIDDEN_CHARS.filter { id.contains(it) }.sorted()
        if (forbidden.isNotEmpty()) {
            throw UnsafeNotebookIdError(
                notebookId,
                "it contains the forbidden character(s) $forbidden",
            )
        }
        if (id.contains('/') || id.contains('\\')) {
            throw UnsafeNotebookIdError(notebookId, "it contains a path separator")
        }
        if (id.any { it.isISOControl() }) {
            throw UnsafeNotebookIdError(notebookId, "it contains a control character")
        }
        if (id.endsWith(".")) {
            throw UnsafeNotebookIdError(notebookId, "it ends with a dot")
        }
        if (id.substringBefore('.').uppercase() in WINDOWS_RESERVED_NAMES) {
            throw UnsafeNotebookIdError(notebookId, "it is a reserved device name")
        }
        return id
    }

    /**
     * Return the id a title yields.
     *
     * The title is lower-cased, every run of characters that is not a letter
     * or a digit becomes a single `-`, and the leading and trailing dashes
     * are removed. A title that yields nothing -- one that is blank, or one
     * that carries nothing but punctuation -- yields [FALLBACK_ID].
     *
     * The id is derived, so it is derived deterministically: the same title
     * always yields the same id, and the id is always a safe path segment.
     * A title long enough to yield an id longer than [MAX_ID_LENGTH] is
     * cut short, because an id the store would have to refuse is no id at
     * all.
     *
     * @param title the title the caller gave
     * @return the derived id
     */
    fun deriveNotebookId(title: String): String {
        val slug = title.lowercase()
            .replace(NON_SLUG_RUN, "-")
            .trim('-')
        val capped = if (slug.length > MAX_ID_LENGTH) {
            slug.take(MAX_ID_LENGTH).trimEnd('-')
        } else {
            slug
        }
        return capped.ifEmpty { FALLBACK_ID }
    }
}
