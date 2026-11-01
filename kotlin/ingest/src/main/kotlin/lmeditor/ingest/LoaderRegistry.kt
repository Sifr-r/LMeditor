package lmeditor.ingest

import lmeditor.core.SourceKind
import lmeditor.core.SourceLoadError
import java.nio.file.Path

/**
 * Maps a [SourceKind] to the loader that handles it.
 *
 * The registry is the one place that knows which loader belongs to which kind,
 * so a caller that only knows the kind never has to know the loader.
 */
class SourceLoaderRegistry(
    private val loaders: Map<SourceKind, SourceLoader>,
) {
    /**
     * Construct a registry pre-populated with one of each loader.
     */
    companion object {
        /** Return a registry pre-populated with one of each loader. */
        fun default(): SourceLoaderRegistry = SourceLoaderRegistry(
            mapOf(
                SourceKind.PDF to PdfSourceLoader(),
                SourceKind.DOCX to DocxSourceLoader(),
                SourceKind.MARKDOWN to MarkdownSourceLoader(),
                SourceKind.HTML to HtmlSourceLoader(),
                SourceKind.TEXT to TextSourceLoader(),
                SourceKind.URL to UrlSourceLoader(),
            ),
        )
    }

    /**
     * Return the loader registered for [kind].
     *
     * @throws SourceLoadError no loader is registered for the kind.
     */
    fun resolve(kind: SourceKind): SourceLoader = loaders[kind]
        ?: throw SourceLoadError(
            kind = kind.wire,
            origin = "(unknown)",
            reason = "no loader registered for kind '${kind.wire}'",
        )

    /**
     * Resolve the loader for [kind] and dispatch the load.
     *
     * @throws SourceLoadError the kind has no loader, or the load failed.
     */
    fun load(kind: SourceKind, origin: SourceOrigin): LoadedSource =
        resolve(kind).load(origin)

    /**
     * Load the file at [path] with the loader registered for [kind].
     *
     * @throws SourceLoadError the kind is [SourceKind.URL] -- the URL loader
     *   requires a URL, not a path -- or the load failed.
     */
    fun load(kind: SourceKind, path: Path): LoadedSource {
        if (kind == SourceKind.URL) {
            throw SourceLoadError(
                kind = kind.wire,
                origin = path.toString(),
                reason = "URL loader requires a URL string, not a Path",
            )
        }
        return load(kind, SourceOrigin.File(path))
    }

    /**
     * Load [origin] with the loader registered for [kind].
     *
     * A string is a URL for [SourceKind.URL] and a file path for every other
     * kind, which is what the HTTP boundary hands over.
     *
     * @throws SourceLoadError the load failed.
     */
    fun load(kind: SourceKind, origin: String): LoadedSource =
        if (kind == SourceKind.URL) {
            load(kind, SourceOrigin.Remote(origin))
        } else {
            load(kind, SourceOrigin.File(java.nio.file.Paths.get(origin)))
        }
}
