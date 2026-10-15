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

        ndk { abiFilters += "arm64-v8a" }
        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
                arguments += "-DANDROID_STL=c++_static"
            }
        }
    }

    ndkVersion = "27.3.13750724"
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }
    buildFeatures { prefab = true }
    packaging { jniLibs { pickFirsts += "**/libshadowhook.so" } }

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

    // Inline-hooking library for the native AAudio hook (bundled in the APK).
    implementation("com.bytedance.android:shadowhook:2.0.0")

    // Pure-Kotlin signal processing + real-time pipeline (unit-tested in CI).
    implementation(project(":separation"))
}
