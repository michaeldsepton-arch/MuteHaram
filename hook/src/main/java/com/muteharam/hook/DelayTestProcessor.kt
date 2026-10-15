package com.muteharam.hook

/**
 * Test settings that can be changed from a PC without re-patching the app.
 * Tries two channels, because apps can't always read system properties:
 *   1. Global settings:  adb shell settings put global muteharam_delay_ms 1000
 *   2. System property:  adb shell setprop debug.muteharam.delay_ms 1000
 * [name] is e.g. "delay_ms".
 */
internal object DebugProps {
    private const val TAG = "MuteHaramHook"
    private var warnedSettings = false
    private var warnedProp = false

    fun getInt(name: String, default: Int): Int =
        fromSettings(name) ?: fromProp(name) ?: default

    private fun fromSettings(name: String): Int? = try {
        val app = Class.forName("android.app.ActivityThread")
            .getMethod("currentApplication").invoke(null) as? android.app.Application
        app?.let {
            android.provider.Settings.Global
                .getString(it.contentResolver, "muteharam_$name")?.toIntOrNull()
        }
    } catch (t: Throwable) {
        if (!warnedSettings) {
            warnedSettings = true
            android.util.Log.w(TAG, "can't read Settings.Global", t)
        }
        null
    }

    private fun fromProp(name: String): Int? = try {
        val c = Class.forName("android.os.SystemProperties")
        val m = c.getMethod("get", String::class.java, String::class.java)
        (m.invoke(null, "debug.muteharam.$name", "") as String).toIntOrNull()
    } catch (t: Throwable) {
        if (!warnedProp) {
            warnedProp = true
            android.util.Log.w(TAG, "can't read SystemProperties", t)
        }
        null
    }
}

/**
 * Test stand-in for the real separation model: delays audio by a fixed
 * amount and reports that amount as [latencyMs], so we can verify the
 * "shift the audio clock so video follows" trick on a real device before
 * the model exists. If it works, picture and lips stay in sync even though
 * the sound is [delayMs] late.
 *
 * Controlled live from a PC (re-read about once a second):
 *   adb shell settings put global muteharam_delay_ms 1000   (0 = off)
 *   adb shell settings put global muteharam_noclock 1       (1 = DON'T shift
 *       the clock, i.e. the "control" case where sound should visibly lag)
 * (the debug.muteharam.* system properties work too where readable)
 *
 * Set the delay BEFORE starting a video; changing it mid-playback causes a
 * glitch (the audio clock jumps).
 */
class DelayTestProcessor : AudioProcessor {
    @Volatile private var delayMs = 0
    @Volatile private var noClock = false
    @Volatile private var lastPollNs = 0L

    private var ring = ShortArray(0)
    private var pos = 0
    private var ringRate = 0
    private var ringCh = 0

    private fun poll() {
        val now = System.nanoTime()
        if (now - lastPollNs < 1_000_000_000L) return
        lastPollNs = now
        val newDelay = DebugProps.getInt("delay_ms", 0).coerceIn(0, 5000)
        val newNoClock = DebugProps.getInt("noclock", 0) == 1
        if (newDelay != delayMs || newNoClock != noClock) {
            android.util.Log.i("MuteHaramHook", "test settings: delay_ms=$newDelay noclock=$newNoClock")
        }
        delayMs = newDelay
        noClock = newNoClock
    }

    override val latencyMs: Int
        get() {
            poll()
            return delayMs
        }

    override val shiftClock: Boolean
        get() {
            poll()
            return !noClock
        }

    override fun process(
        pcm16: ShortArray,
        offset: Int,
        length: Int,
        sampleRateHz: Int,
        channelCount: Int
    ) {
        poll()
        val d = delayMs
        measureIn(pcm16, offset, length)
        if (d <= 0) {
            if (ring.isNotEmpty()) ring = ShortArray(0)
            measureOut(pcm16, offset, length, sampleRateHz, channelCount, d)
            return
        }
        val needed = (sampleRateHz.toLong() * d / 1000).toInt() * channelCount
        if (needed <= 0) return
        if (ring.size != needed || ringRate != sampleRateHz || ringCh != channelCount) {
            ring = ShortArray(needed)
            pos = 0
            ringRate = sampleRateHz
            ringCh = channelCount
        }
        val r = ring
        var p = if (pos < r.size) pos else 0
        for (i in offset until offset + length) {
            val old = r[p]
            r[p] = pcm16[i]
            pcm16[i] = old
            p++
            if (p == r.size) p = 0
        }
        pos = p
        measureOut(pcm16, offset, length, sampleRateHz, channelCount, d)
    }

    // --- diagnostics: loudness in vs out, logged every ~250 ms. If the delay
    // works, "out" at time t equals "in" at time t - delay. ---
    private var inSq = 0.0
    private var outSq = 0.0
    private var cnt = 0L

    private fun measureIn(a: ShortArray, off: Int, len: Int) {
        var s = 0.0
        for (i in off until off + len) { val v = a[i] / 32768.0; s += v * v }
        inSq += s
    }

    private fun measureOut(a: ShortArray, off: Int, len: Int, rate: Int, ch: Int, d: Int) {
        var s = 0.0
        for (i in off until off + len) { val v = a[i] / 32768.0; s += v * v }
        outSq += s
        cnt += len
        if (cnt >= rate.toLong() * ch / 4) {
            fun rms(x: Double) = Math.sqrt(x / cnt)
            android.util.Log.i(
                "MuteHaramHook",
                "lvl in=%.4f out=%.4f delay=%d t=%d".format(rms(inSq), rms(outSq), d, System.currentTimeMillis() % 100000)
            )
            inSq = 0.0; outSq = 0.0; cnt = 0
        }
    }
}
