plugins {
    id("org.jetbrains.kotlin.jvm")
}

// Pure Kotlin/JVM (no Android classes) so the signal processing can be unit-tested
// on a normal computer / CI, then used by the Android modules.
kotlin {
    jvmToolchain(17)
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    useJUnit()
    testLogging {
        events("passed", "failed", "skipped")
        showStandardStreams = false
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
