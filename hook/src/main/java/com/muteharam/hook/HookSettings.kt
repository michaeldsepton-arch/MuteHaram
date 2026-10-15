package com.muteharam.hook

/**
 * Live settings, changed from a PC with adb (no re-patching needed):
 *
 *   adb shell settings put global muteharam_mode  <test|remote|local>
 *       test   (default) -> the delay/sync experiment (muteharam_delay_ms ...)
 *       remote           -> music removal on a PC across Wi-Fi (pc/start_wifi_server.bat)
 *       local            -> music removal on this device's GPU
 *   adb shell settings put global muteharam_server  192.168.1.20:47801   (remote)
 *   adb shell settings put global muteharam_token   abcd1234            (remote)
 *   adb shell settings put global muteharam_chunk_ms  3000     AI window length
 *   adb shell settings put global muteharam_margin_ms 500      context each side of a block
 *   adb shell settings put global muteharam_budget_ms 700      time allowed for the AI per block
 *   adb shell settings put global muteharam_gain 100           % of the music to remove (0 = none)
 *
 * Read about once a second; the audio threads only look at cached values.
 */
object HookSettings {
    @Volatile private var lastNs = 0L

    @Volatile var mode: String = "test"; private set
    @Volatile var server: String = ""; private set
    @Volatile var token: String = ""; private set
    @Volatile var chunkMs: Int = 3000; private set
    @Volatile var marginMs: Int = 500; private set
    @Volatile var budgetMs: Int = 700; private set
    @Volatile var gainPercent: Int = 100; private set

    /** Cheap to call from anywhere; re-reads the settings at most once a second. */
    fun refresh() {
        val now = System.nanoTime()
        if (now - lastNs < 1_000_000_000L) return
        lastNs = now
        val m = DebugProps.getString("mode", "test").lowercase()
        mode = if (m == "remote" || m == "local") m else "test"
        server = DebugProps.getString("server", "")
        token = DebugProps.getString("token", "")
        chunkMs = DebugProps.getInt("chunk_ms", 3000).coerceIn(1000, 12000)
        marginMs = DebugProps.getInt("margin_ms", 500).coerceIn(100, 2000)
        budgetMs = DebugProps.getInt("budget_ms", 700).coerceIn(100, 5000)
        gainPercent = DebugProps.getInt("gain", 100).coerceIn(0, 100)
    }

    val separating: Boolean get() { refresh(); return mode != "test" }

    /** A string that changes whenever a setting that needs a new pipeline changes. */
    fun pipelineKey(): String { refresh(); return "$mode|$server|$token|$chunkMs|$marginMs|$budgetMs" }
}
