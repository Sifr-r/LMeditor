package lmeditor.vault

/**
 * The markdown grammar of a vault: frontmatter, wiki-links and tags.
 *
 * The grammar is deliberately small, and it is deliberately *markdown* and
 * not a private format: the notes of a vault are ordinary files, and a tag
 * has to be a tag that `grep` finds.
 *
 * - **Frontmatter** is a `---` fenced block at the very start of the file.
 *   Its `title:` sets the title when the body has no `# heading`, and its
 *   `tags:` list -- inline `[a, b]` or one `- a` per line -- adds tags.
 * - **A wiki-link** is `[[Target]]`, `[[Target|alias]]` or `[[Target#Heading]]`.
 *   An unresolved target is still a link: it is a link to a note that does
 *   not exist yet, which is what a wiki-link is for.
 * - **A tag** is `#` followed by a letter, then letters, digits, `_`, `-` or
 *   `/`. A `#` that starts a markdown heading, that follows a word character,
 *   or that is part of a URL fragment is not a tag. Tags are compared
 *   case-insensitively and are reported lower-cased.
 */
object Markdown {

    /** The fence that opens and closes a frontmatter block. */
    const val FRONTMATTER_FENCE: String = "---"

    /** The wiki-link, as it is written. */
    private val WIKI_LINK: Regex = Regex("""\[\[([^\[\]]+)]]""")

    /** A tag: a `#` and a letter, then letters, digits, `_`, `-` or `/`. */
    private val TAG: Regex = Regex("""#([A-Za-z][A-Za-z0-9_\-/]*)""")

    /** The whitespace a word boundary falls on. */
    private val WHITESPACE_RUN: Regex = Regex("[\\p{IsWhite_Space}]+")

    /**
     * Return the frontmatter of [content].
     *
     * A file whose first line is not the fence has no frontmatter, and a
     * block that is never closed is not a frontmatter either: it is text.
     *
     * @param content the text of a note
     * @return the title and the tags the frontmatter declares
     */
    fun frontmatter(content: String): Frontmatter {
        val lines = content.lines()
        if (lines.isEmpty() || lines[0].trim() != FRONTMATTER_FENCE) {
            return Frontmatter()
        }
        var end = -1
        for (index in 1 until lines.size) {
            if (lines[index].trim() == FRONTMATTER_FENCE) {
                end = index
                break
            }
        }
        if (end < 0) {
            return Frontmatter()
        }
        val block = lines.subList(1, end)
        var title = ""
        val tags = LinkedHashSet<String>()
        var index = 0
        while (index < block.size) {
            val line = block[index]
            val key = line.substringBefore(":").trim()
            val value = line.substringAfter(":", "").trim()
            when (key) {
                "title" -> title = unquote(value)
                "tags" -> {
                    if (value.startsWith("[") && value.endsWith("]")) {
                        for (item in value.substring(1, value.length - 1).split(",")) {
                            addTag(tags, item)
                        }
                    } else if (value.isEmpty()) {
                        // The list form: one `- a` per line under the key.
                        var next = index + 1
                        while (next < block.size) {
                            val item = block[next].trim()
                            if (!item.startsWith("-")) {
                                break
                            }
                            addTag(tags, item.removePrefix("-"))
                            next++
                        }
                        index = next - 1
                    } else {
                        addTag(tags, value)
                    }
                }
            }
            index++
        }
        return Frontmatter(title = title, tags = tags.sorted())
    }

    /**
     * Return the body of [content]: the text after its frontmatter.
     *
     * @param content the text of a note
     * @return the body, with the frontmatter taken off
     */
    fun body(content: String): String {
        val lines = content.lines()
        if (lines.isEmpty() || lines[0].trim() != FRONTMATTER_FENCE) {
            return content
        }
        for (index in 1 until lines.size) {
            if (lines[index].trim() == FRONTMATTER_FENCE) {
                return lines.subList(index + 1, lines.size).joinToString("\n")
            }
        }
        return content
    }

    /**
     * Return the first `# heading` of [content], or an empty string.
     *
     * A heading is a line whose first marks are `#`s followed by a space. A
     * line that begins with `#tag` is not a heading: it is a tag.
     *
     * @param content the text of a note
     * @return the title the heading carries, or an empty string
     */
    fun headingTitle(content: String): String {
        for (line in body(content).lineSequence()) {
            val indent = line.indexOfFirst { !it.isWhitespace() }
            if (indent < 0) {
                continue
            }
            val rest = line.substring(indent)
            if (!rest.startsWith("#")) {
                continue
            }
            val after = rest.trimStart('#')
            if (after.isEmpty() || !after[0].isWhitespace()) {
                continue
            }
            val title = stripClosingMarkers(after.trim())
            if (title.isNotEmpty()) {
                return title
            }
        }
        return ""
    }

    /**
     * Report whether the note opens with a heading.
     *
     * A note that opens with a heading already names itself, so a title must
     * not be put in front of it: the note would carry two headings, and the
     * second one would be a lie about the first.
     *
     * @param content the text of a note
     * @return whether the first non-blank line of the note is a heading
     */
    fun opensWithHeading(content: String): Boolean {
        for (line in body(content).lineSequence()) {
            val indent = line.indexOfFirst { !it.isWhitespace() }
            if (indent < 0) {
                continue
            }
            val rest = line.substring(indent)
            if (!rest.startsWith("#")) {
                return false
            }
            val after = rest.trimStart('#')
            return after.isNotEmpty() && after[0].isWhitespace()
        }
        return false
    }

    /**
     * Return the wiki-links of [content], as they are written.
     *
     * A link is reported by its target: the alias and the heading are the
     * link's decorations, not its name. The order the links were written in
     * is kept, and a target that appears twice is reported once.
     *
     * @param content the text of a note
     * @return the link targets, in the order they were written
     */
    fun links(content: String): List<String> {
        val found = LinkedHashSet<String>()
        for (match in WIKI_LINK.findAll(body(content))) {
            val target = linkTarget(match.groupValues[1])
            if (target.isNotEmpty()) {
                found.add(target)
            }
        }
        return found.toList()
    }

    /**
     * Return the tags of [content]: the frontmatter's and the body's.
     *
     * The tags are lower-cased, because tags are compared case-insensitively,
     * and sorted, so the same note always carries them in the same order.
     *
     * @param content the text of a note
     * @return the tags the note carries
     */
    fun tags(content: String): List<String> {
        val found = LinkedHashSet<String>()
        found.addAll(frontmatter(content).tags)
        found.addAll(bodyTags(content))
        return found.sorted()
    }

    /**
     * Return the `#tag` tokens of the body of [content].
     *
     * @param content the text of a note
     * @return the tags the body carries, lower-cased
     */
    fun bodyTags(content: String): List<String> {
        val found = LinkedHashSet<String>()
        for (line in body(content).lineSequence()) {
            val indent = line.indexOfFirst { !it.isWhitespace() }
            val markers = if (indent < 0) 0 else headingMarkers(line.substring(indent))
            var index = 0
            while (index < line.length) {
                if (line[index] != '#') {
                    index++
                    continue
                }
                if (indent >= 0 && index < indent + markers) {
                    // The `#`s of a heading are the heading's, not a tag's.
                    index++
                    continue
                }
                val previous = if (index > 0) line[index - 1] else ' '
                if (previous.isLetterOrDigit() || previous == '_') {
                    // A `#` that follows a word character is not a tag: it is
                    // a fragment of a word, or of a URL.
                    index++
                    continue
                }
                if (isUrlFragment(line, index)) {
                    index++
                    continue
                }
                val match = TAG.matchAt(line, index)
                if (match == null) {
                    index++
                    continue
                }
                found.add(match.groupValues[1].lowercase())
                index = match.range.last + 1
            }
        }
        return found.toList()
    }

    /**
     * Return the number of words in the body of [content].
     *
     * @param content the text of a note
     * @return the number of whitespace-separated words
     */
    fun wordCount(content: String): Int =
        body(content).split(WHITESPACE_RUN).count { it.isNotEmpty() }

    /**
     * Return [content] with the wiki-links that point at the old note rewritten.
     *
     * A link keeps its alias and its heading: only the target is replaced,
     * with the note's new id. The links that point somewhere else are left
     * exactly as they were written.
     *
     * @param content the text of a note
     * @param to the target the rewritten links point at
     * @param pointsAt whether a link target points at the note being renamed
     * @return the text with the links rewritten
     */
    fun rewriteLinks(content: String, to: String, pointsAt: (String) -> Boolean): String =
        WIKI_LINK.replace(content) { match ->
            val inner = match.groupValues[1]
            val target = linkTarget(inner)
            if (target.isEmpty() || !pointsAt(target)) {
                match.value
            } else {
                "[[" + to + linkSuffix(inner) + "]]"
            }
        }

    /**
     * Return the number of wiki-links in [content] that point at a given note.
     *
     * A link that appears twice is counted twice: the caller wants to know
     * how many links were rewritten, not how many notes hold them.
     *
     * @param content the text of a note
     * @param pointsAt whether a link target points at the note being counted
     * @return the number of links that point at the note
     */
    fun countLinks(content: String, pointsAt: (String) -> Boolean): Int {
        var count = 0
        for (match in WIKI_LINK.findAll(content)) {
            val target = linkTarget(match.groupValues[1])
            if (target.isNotEmpty() && pointsAt(target)) {
                count++
            }
        }
        return count
    }

    /** Return the target of a wiki-link: the part before the alias and the heading. */
    private fun linkTarget(inner: String): String =
        inner.substringBefore("|").substringBefore("#").trim()

    /** Return the alias and the heading of a wiki-link, as they are written. */
    private fun linkSuffix(inner: String): String {
        val aliasSplit = inner.split("|", limit = 2)
        val headingSplit = aliasSplit[0].split("#", limit = 2)
        val suffix = StringBuilder()
        if (headingSplit.size > 1) {
            suffix.append('#').append(headingSplit[1])
        }
        if (aliasSplit.size > 1) {
            suffix.append('|').append(aliasSplit[1])
        }
        return suffix.toString()
    }

    /** Return the number of leading `#`s of a heading line, or zero. */
    private fun headingMarkers(text: String): Int {
        if (!text.startsWith("#")) {
            return 0
        }
        var count = 0
        while (count < text.length && text[count] == '#') {
            count++
        }
        if (count == text.length) {
            return count
        }
        return if (text[count].isWhitespace()) count else 0
    }

    /** Whether the `#` at [index] is the fragment of a URL. */
    private fun isUrlFragment(line: String, index: Int): Boolean {
        var start = index
        while (start > 0) {
            val ch = line[start - 1]
            if (ch.isWhitespace() || ch == '(' || ch == ')' || ch == '[' || ch == ']' || ch == '<' || ch == '>') {
                break
            }
            start--
        }
        return line.substring(start, index).contains("://")
    }

    /** Return the title of a heading without the `#`s that close it. */
    private fun stripClosingMarkers(text: String): String {
        var end = text.length
        while (end > 0 && text[end - 1] == '#') {
            end--
        }
        if (end == text.length || end == 0) {
            return text
        }
        return if (text[end - 1].isWhitespace()) text.substring(0, end).trim() else text
    }

    /** Add a tag to the set, lower-cased, when it carries one. */
    private fun addTag(tags: MutableSet<String>, value: String) {
        val tag = unquote(value)
        if (tag.isNotEmpty()) {
            tags.add(tag.lowercase())
        }
    }

    /** Return a value without the quotes it may be written in. */
    private fun unquote(value: String): String {
        val trimmed = value.trim()
        if (trimmed.length >= 2) {
            val first = trimmed[0]
            val last = trimmed[trimmed.length - 1]
            if ((first == '"' && last == '"') || (first == '\'' && last == '\'')) {
                return trimmed.substring(1, trimmed.length - 1).trim()
            }
        }
        return trimmed
    }
}
