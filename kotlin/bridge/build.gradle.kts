// The NDJSON stdio seam the Python tier calls into.
//
// The rules that cross this seam are the Kotlin tier's, and the Python tier
// calls them rather than repeating them. The rules the seam does *not* serve
// are named rather than implied: ARCHITECTURE.md §18.2.1 is the catalogue of
// the drift-pinned Python twins, the two rules with no Kotlin counterpart, and
// the model-facing half that is Python by design.

description = "The NDJSON stdio seam the Python tier calls into"

dependencies {
    api(project(":core"))
    api(project(":text"))
    api(project(":word"))
    api(project(":pdf"))
    api(project(":ingest"))
    api(project(":library"))
    api(project(":workflow"))
    api(project(":editing"))
    api(project(":factcheck"))
    api(project(":discovery"))
    api(project(":server"))
    // The vault's rules are the tier's, and the seam serves them: the vault
    // depends downward on :core alone, so the direction rule holds.
    api(project(":vault"))
    // The notebook's rules are the tier's too, for the same reason.
    api(project(":notebooks"))
}

/**
 * A self-contained jar of the whole Kotlin tier.
 *
 * The Python tier starts this jar as a process and speaks NDJSON to it over
 * its standard input and output. It needs one file and one classpath entry,
 * so the jar carries the tier's modules and the Kotlin runtime with it.
 *
 * The standard library is the Kotlin *language's* own runtime, not a
 * third-party dependency of the tier: every Kotlin program needs it, and the
 * dependency policy in `CONTRACTS.md` §1 is about the libraries the tier
 * chooses, not about the language it is written in. Everything else -- JSON,
 * XML, ZIP, HTTP, hashing, dates -- comes from the JDK.
 */
val tierJar = tasks.register<Jar>("tierJar") {
    group = "distribution"
    description = "A self-contained, runnable jar of the Kotlin tier"
    archiveClassifier.set("tier")
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE

    manifest {
        attributes("Main-Class" to "lmeditor.bridge.BridgeMain")
    }

    from(sourceSets.main.get().output)
    from({
        configurations.runtimeClasspath.get()
            .filter { it.name.endsWith(".jar") }
            .map { zipTree(it) }
    })

    // The tier's modules are packaged from their jars, so those jars have to
    // be rebuilt before this task runs. Without this dependency Gradle resolves
    // the classpath at execution time and finds whatever jar happens to be on
    // disk: the tier jar then carries a stale module and the seam fails in a
    // way that looks like a logic bug rather than a packaging bug.
    dependsOn(configurations.runtimeClasspath)

    exclude("META-INF/*.SF", "META-INF/*.DSA", "META-INF/*.RSA")
    exclude("META-INF/versions/9/module-info.class")
    exclude("module-info.class")
    exclude("META-INF/*.kotlin_module")
}

tasks.named("assemble") {
    dependsOn(tierJar)
}
