// The desktop client.
//
// This module is the one part of the repository that is *not* a tier module.
// The tier's rule is that a tier module's runtime dependency set is empty;
// a user interface cannot live inside that rule, because a user interface is
// made of dependencies. The client therefore sits ABOVE the tier: it depends
// downward on `:core` alone, and the tier still depends on nothing at all.
//
// What the client borrows from `:core` is the part that must not be written
// twice: the schema models, the wire vocabulary, the JSON codec, the wire
// decoders and the inline-citation grammar. The client therefore decodes the
// server's payloads with the same functions the server's own models were
// written against, so the two halves cannot disagree about what a payload
// means.
//
// See ARCHITECTURE.md §18 and §20.

plugins {
    alias(libs.plugins.compose)
    alias(libs.plugins.compose.compiler)
}

// Compose Multiplatform's desktop artifacts depend on a handful of AndroidX
// artifacts (the runtime, the lifecycle and the saved-state libraries), and
// those live on Google's Maven repository rather than on Maven Central.
//
// The repository is declared here, on the one module that needs it, so the
// tier's own build keeps resolving against Maven Central alone.
repositories {
    google()
    mavenCentral()
}

dependencies {
    // The only tier module the client needs.
    implementation(project(":core"))

    implementation(compose.desktop.currentOs)
    implementation(compose.material3)

    testImplementation(kotlin("test"))
}

compose.desktop {
    application {
        mainClass = "lmeditor.desktop.MainKt"

        nativeDistributions {
            targetFormats(
                org.jetbrains.compose.desktop.application.dsl.TargetFormat.Dmg,
                org.jetbrains.compose.desktop.application.dsl.TargetFormat.Msi,
                org.jetbrains.compose.desktop.application.dsl.TargetFormat.Deb,
            )
            packageName = "LMeditor"
            packageVersion = "0.1.0"
            description = "Source-grounded long-form writing"
        }
    }
}
