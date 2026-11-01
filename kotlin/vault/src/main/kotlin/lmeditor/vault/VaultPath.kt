package lmeditor.vault

import lmeditor.core.LMeditorError
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path

/** The extension every note of a vault carries. */
const val NOTE_EXTENSION: String = ".md"

/**
 * The base of every failure the vault raises.
 *
 * A vault has three ways of refusing a caller: the note id is not a safe path
 * inside the vault, the note is not there, and the vault folder is not there.
 * Each one is typed and carries its own details, so a refusal that crosses
 * the seam says what was refused and why -- and never looks like an empty
 * result that succeeded.
 */
open class VaultError(
    message: String = "",
    details: Map<String, Any?> = emptyMap(),
) : LMeditorError(message, details)

/**
 * Raised when a note id is not a safe path inside the vault.
 *
 * The error is a [VaultError] -- so a caller of the vault can catch the
 * domain error -- and it names the offending id and the reason it is unsafe,
 * because an unsafe note id is a bad argument value and must not be mistaken
 * for a missing note.
 */
class UnsafeNoteIdError(
    /** The offending note id. */
    val noteId: String,
    /** Why the id is not a safe path inside a vault. */
    val reason: String,
) : VaultError(
    "unsafe note_id '$noteId': $reason",
    mapOf("note_id" to noteId, "reason" to reason),
)

/**
 * Raised when the vault folder is not there.
 *
 * A vault is a folder the operator chose. When the folder is not there there
 * is nothing to read and nothing to write, and the caller has to be told so
 * rather than handed an empty vault that looks like an empty one.
 */
class VaultNotFoundError(
    /** The vault folder that is not there. */
    val root: String,
) : VaultError(
    "VaultNotFoundError: the vault '$root' was not found.",
    mapOf("root" to root),
)

/**
 * The path rules of a vault: what a note id may be, and how it resolves.
 *
 * A note id is a relative path inside the vault, with `/` separators and no
 * `.md`. It is refused unless it
 *
 * - is not blank, and is not `.` or `..`;
 * - carries no segment that is `.` or `..`;
 * - carries no absolute prefix, no drive letter and no UNC prefix;
 * - carries no control character, and no character a Windows path forbids
 *   (`<>:"|?*`, and a trailing dot or space in a segment);
 * - names no hidden file or folder, so Obsidian's own `.obsidian` is never
 *   written;
 * - stays inside the vault after resolution -- the resolved real path must
 *   still begin with the resolved real root, after symbolic links are
 *   resolved.
 *
 * Symlinked entries are refused when a note is written, so a vault cannot be
 * walked out of through a link.
 */
object VaultPath {

    /** The characters a Windows path refuses in a file name. */
    private val FORBIDDEN_CHARS: Set<Char> = setOf('<', '>', ':', '"', '|', '?', '*')

    /**
     * Return [noteId] as a safe relative path inside a vault, or raise.
     *
     * @param noteId the note id the caller named
     * @return the note id, unchanged: a note id is a path, and a path the
     *   operator typed is the one that is written to disk
     * @throws UnsafeNoteIdError the id is not a safe path inside a vault
     */
    fun validateNoteId(noteId: String): String {
        if (noteId.isBlank()) {
            throw UnsafeNoteIdError(noteId, "it must not be blank")
        }
        if (noteId == "." || noteId == "..") {
            throw UnsafeNoteIdError(noteId, "it is a relative path segment")
        }
        if (noteId.startsWith("\\\\")) {
            throw UnsafeNoteIdError(noteId, "it is a UNC path")
        }
        if (noteId.startsWith("/")) {
            throw UnsafeNoteIdError(noteId, "it is an absolute path")
        }
        if (noteId.length >= 2 && noteId[1] == ':') {
            throw UnsafeNoteIdError(noteId, "it carries a drive letter")
        }
        for (segment in noteId.split("/")) {
            if (segment.isEmpty()) {
                throw UnsafeNoteIdError(noteId, "it carries an empty path segment")
            }
            if (segment == "." || segment == "..") {
                throw UnsafeNoteIdError(noteId, "it carries the '$segment' path segment")
            }
            if (segment.startsWith(".")) {
                throw UnsafeNoteIdError(
                    noteId,
                    "the segment '$segment' names a hidden file or folder",
                )
            }
            if (segment.endsWith(".") || segment.endsWith(" ")) {
                throw UnsafeNoteIdError(
                    noteId,
                    "the segment '$segment' ends with a dot or a space",
                )
            }
            if (segment.any { it.isISOControl() }) {
                throw UnsafeNoteIdError(
                    noteId,
                    "the segment '$segment' carries a control character",
                )
            }
            if (segment.contains('\\')) {
                throw UnsafeNoteIdError(
                    noteId,
                    "the segment '$segment' carries a backslash; a note id is separated by '/'",
                )
            }
            val forbidden = FORBIDDEN_CHARS.filter { segment.contains(it) }.sorted()
            if (forbidden.isNotEmpty()) {
                throw UnsafeNoteIdError(
                    noteId,
                    "the segment '$segment' carries the forbidden character(s) $forbidden",
                )
            }
        }
        return noteId
    }

    /**
     * Return the file a note id names inside [root], or raise.
     *
     * The file is resolved against the *real* root -- the root with its
     * symbolic links resolved -- and every segment of the note id is checked
     * for being a link. A note is therefore never written through a path that
     * leaves the vault, not even through a link that points outside it.
     *
     * @param root the vault folder
     * @param noteId the note id the caller named
     * @return the file the note id names, which need not exist yet
     * @throws UnsafeNoteIdError the id is not a safe path inside a vault, or
     *   it passes through a symbolic link
     * @throws VaultNotFoundError the vault folder is not there
     */
    fun resolve(root: Path, noteId: String): Path {
        val id = validateNoteId(noteId)
        val realRoot = realRoot(root)
        val segments = id.split("/")
        var walked = realRoot
        for (index in 0 until segments.size - 1) {
            walked = walked.resolve(segments[index])
            if (Files.isSymbolicLink(walked)) {
                throw UnsafeNoteIdError(
                    id,
                    "it passes through the symbolic link '${segments[index]}'",
                )
            }
        }
        val target = walked.resolve(segments.last() + NOTE_EXTENSION)
        if (Files.isSymbolicLink(target)) {
            throw UnsafeNoteIdError(
                id,
                "it names the symbolic link '${segments.last()}'",
            )
        }
        if (!realPathOf(target).startsWith(realRoot)) {
            throw UnsafeNoteIdError(id, "it resolves outside the vault")
        }
        return target
    }

    /**
     * Whether a file or folder name is hidden, and so is never read.
     *
     * A name that begins with `.` is hidden: that is how Obsidian keeps its
     * own `.obsidian` folder, and a vault that read it would show the
     * operator the application's own bookkeeping as if it were their notes.
     *
     * @param name the name of a file or folder
     * @return true when the entry is hidden
     */
    fun isHiddenName(name: String): Boolean = name.startsWith(".")

    /**
     * Return the real path of the vault folder, or raise.
     *
     * @param root the vault folder
     * @return the folder with its symbolic links resolved
     * @throws VaultNotFoundError the folder is not there, or is not a folder
     */
    fun realRoot(root: Path): Path {
        val real = try {
            root.toRealPath()
        } catch (exc: IOException) {
            throw VaultNotFoundError(root.toString())
        }
        if (!Files.isDirectory(real)) {
            throw VaultNotFoundError(root.toString())
        }
        return real
    }

    /**
     * Return the real path of [path], resolving what is already there.
     *
     * The deepest part of the path that exists is resolved with
     * `toRealPath()`, and the part that does not exist yet is appended to it.
     * A junction or a link that points outside the vault therefore shows up
     * in the result, and the containment check sees it.
     */
    private fun realPathOf(path: Path): Path {
        var existing = path
        val missing = ArrayDeque<String>()
        while (!Files.exists(existing) && existing.parent != null) {
            val name = existing.fileName ?: break
            missing.addFirst(name.toString())
            existing = existing.parent
        }
        val real = try {
            existing.toRealPath()
        } catch (exc: IOException) {
            throw VaultNotFoundError(path.toString())
        }
        var resolved = real
        for (segment in missing) {
            resolved = resolved.resolve(segment)
        }
        return resolved
    }
}
