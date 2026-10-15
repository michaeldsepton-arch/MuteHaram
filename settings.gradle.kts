pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // Hosts the classic Xposed API artifact (de.robv.android.xposed:api)
        // that LSPosed maintains compatibility with.
        maven(url = "https://api.xposed.info/")
    }
}

rootProject.name = "MuteHaram"
include(":app")
include(":hook")
include(":bench")
include(":separation")
include(":engine")
