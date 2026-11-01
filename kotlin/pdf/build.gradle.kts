description = "Native PDF text and metadata extraction (no third-party parser)"

dependencies {
    api(project(":core"))
    // Test-only. The shared decompression bound is proved against the DOCX
    // reader as well, and `:word` is the module that holds it. The edge is in
    // a test configuration only: the runtime graph is unchanged.
    testImplementation(project(":word"))
}