plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.muteharam.engine"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.muteharam.engine"
        minSdk = 31
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
        ndk { abiFilters += "arm64-v8a" }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
        // LiteRT 2.x ships Kotlin 2.3 metadata; this project builds with 1.9.
        freeCompilerArgs += "-Xskip-metadata-version-check"
    }
    androidResources { noCompress += "tflite" }
}

dependencies {
    implementation("com.google.ai.edge.litert:litert:2.1.5")
    implementation(project(":separation"))
}
