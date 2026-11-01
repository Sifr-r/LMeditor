// Root build for the LMeditor Kotlin core tier.
//
// Dependency policy: the runtime dependency set of every *tier* module is
// EMPTY. The JDK supplies JSON parsing (hand-written in :core), XML
// (javax.xml), ZIP (java.util.zip), HTTP (java.net.http), hashing
// (java.security) and dates (java.time). The only declared dependency is the
// test framework, which never ships with the application.
//
// There is exactly one module that is not a tier module: `:desktop`, the
// Compose Multiplatform client. A user interface cannot live inside the rule
// above -- a user interface *is* dependencies -- so the client sits above the
// tier instead of inside it. It depends downward on `:core` and on nothing
// else, and the tier still depends on nothing at all. The rule is scoped, not
// broken.
//
// See ARCHITECTURE.md §18 for the dependency-direction rules.

plugins {
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.compose) apply false
    alias(libs.plugins.compose.compiler) apply false
}

allprojects {
    group = "lmeditor"
    version = "0.1.0"
}

subprojects {
    apply(plugin = "org.jetbrains.kotlin.jvm")
    apply(plugin = "java-library")

    extensions.configure<JavaPluginExtension>("java") {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    tasks.withType<JavaCompile>().configureEach {
        options.release.set(17)
        options.encoding = "UTF-8"
    }

    extensions.configure<org.jetbrains.kotlin.gradle.dsl.KotlinJvmProjectExtension>("kotlin") {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
            allWarningsAsErrors.set(false)
        }
    }

    dependencies {
        // Test-only. Never on the runtime classpath.
        "testImplementation"(kotlin("test"))
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
        testLogging {
            events("passed", "failed", "skipped")
            showStandardStreams = false
        }
    }
}
