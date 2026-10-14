plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.muteharam.hook"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.muteharam.hook"
        minSdk = 27
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Provided by the LSPosed framework at runtime on the device --
    // never bundled into the APK, hence compileOnly.
    compileOnly("de.robv.android.xposed:api:82")
}
