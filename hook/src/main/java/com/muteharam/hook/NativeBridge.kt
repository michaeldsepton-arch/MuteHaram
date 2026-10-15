package com.muteharam.hook

import android.util.Log
import java.io.File
import java.util.zip.ZipFile
import kotlin.concurrent.thread

/**
 * Loads the module's native library (AAudio hook, see src/main/cpp) inside the
 * target app and keeps its settings in sync with the live test settings.
 *
 * Why this is fiddly: under LSPatch the module APK is a copy under the target
 * app's cache dir and is only a dex class loader, so System.loadLibrary()
 * can't find the module's .so files. We load them straight out of the APK
 * (zip path), falling back to extracting them.
 */
object NativeBridge {
    private const val TAG = "MuteHaramHook"
    private const val ABI = "arm64-v8a"

    @Volatile
    private var started = false

    @JvmStatic external fun nativeInit(): Int
    @JvmStatic external fun nativeSetConfig(delayMs: Int, shiftMs: Int, noClock: Int)

    fun start() {
        if (started) return
        started = true
        try {
            if (!loadLibraries()) return
            val failures = nativeInit()
            Log.i(TAG, "native AAudio hook init: $failures hook(s) failed (0 = all good)")
            thread(name = "MuteHaramConfig", isDaemon = true) {
                while (true) {
                    try {
                        nativeSetConfig(
                            DebugProps.getInt("delay_ms", 0).coerceIn(0, 5000),
                            DebugProps.getInt("shift_ms", -1),
                            DebugProps.getInt("noclock", 0)
                        )
                        Thread.sleep(500)
                    } catch (t: Throwable) {
                        Log.w(TAG, "config thread error", t)
                        Thread.sleep(2000)
                    }
                }
            }
        } catch (t: Throwable) {
            Log.w(TAG, "native AAudio hook unavailable", t)
        }
    }

    private fun moduleApkPath(): String? {
        val loader = NativeBridge::class.java.classLoader?.toString() ?: return null
        return Regex("zip file \"([^\"]+\\.apk)\"").find(loader)?.groupValues?.get(1)
    }

    private fun loadLibraries(): Boolean {
        val apk = moduleApkPath()
        if (apk == null) {
            Log.w(TAG, "can't locate module APK for native libs")
            return false
        }
        // shadowhook first: our lib depends on it.
        for (lib in listOf("libshadowhook.so", "libmuteharam_native.so")) {
            try {
                System.load("$apk!/lib/$ABI/$lib")
                Log.i(TAG, "loaded $lib from APK")
            } catch (e: UnsatisfiedLinkError) {
                Log.w(TAG, "load from APK failed for $lib (${e.message}); extracting")
                val dir = File(File(apk).parentFile, "native").apply { mkdirs() }
                val out = File(dir, lib)
                ZipFile(apk).use { zip ->
                    val entry = zip.getEntry("lib/$ABI/$lib") ?: return false
                    zip.getInputStream(entry).use { i -> out.outputStream().use { o -> i.copyTo(o) } }
                }
                System.load(out.absolutePath)
                Log.i(TAG, "loaded $lib from ${out.absolutePath}")
            }
        }
        return true
    }
}
