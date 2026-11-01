description = "The on-disk book library, its brief and its bounded working sets"

dependencies {
    api(project(":core"))
    api(project(":text"))
    // The importer and the exporter rebuild a Word document: the section
    // origin of a book holds the blocks it was imported from, and an export
    // puts them back into the file. The dependency points strictly downward
    // (:word depends on :core and :text), so the direction rule holds.
    api(project(":word"))
}