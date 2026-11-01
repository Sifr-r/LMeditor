description = "The rules of a notebook: the safety of its ids, its manifest and its store"

dependencies {
    api(project(":core"))
    // A notebook is a directory with a JSON manifest in it. It reads and
    // writes plain files with java.nio.file and reads its manifest with the
    // core tier's own JSON. It takes no third-party library: a notebook has to
    // stay a folder an operator can open, and a dependency would only add a
    // way for that folder to stop being one.
}
