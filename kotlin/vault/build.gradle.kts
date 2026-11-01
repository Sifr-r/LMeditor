description = "The rules of a vault: the safety of its paths, its markdown grammar and its store"

dependencies {
    api(project(":core"))
    // The vault reads and writes plain files with java.nio.file and reads its
    // markdown with java.util.regex. It takes no third-party library: a vault
    // has to stay a folder of markdown files that any editor can open, and a
    // dependency would only add a way for that folder to stop being one.
}
