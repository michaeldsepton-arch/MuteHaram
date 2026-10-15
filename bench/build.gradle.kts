plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.muteharam.bench"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.muteharam.bench"
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
    }
    // The model is copied out of the APK at first run; keep it uncompressed.
    androidResources { noCompress += "tflite" }
}

dependencies {
    implementation("com.google.ai.edge.litert:litert:2.1.5")
}
