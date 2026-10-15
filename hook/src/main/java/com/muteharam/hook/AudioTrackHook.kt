package com.muteharam.hook

import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import de.robv.android.xposed.IXposedHookLoadPackage
import de.robv.android.xposed.XC_MethodHook
import de.robv.android.xposed.XposedBridge
import de.robv.android.xposed.XposedHelpers
import de.robv.android.xposed.callbacks.XC_LoadPackage
import java.nio.ByteBuffer
import java.nio.ByteOrder

private const val TAG = "MuteHaramHook"

/**
 * LSPosed/LSPatch call handleLoadPackage once per *package* loaded into a
 * process (e.g. the app, its WebView and microG), but AudioTrack is one class
 * per process. Hooking it each time stacks the callbacks, so every write was
 * processed N times and the clock shifted N times. Install exactly once.
 */
private val hooksInstalled = java.util.concurrent.atomic.AtomicBoolean(false)

/**
 * Entry point LSPosed calls once per app process that starts, for
 * every app on the device (that's how Xposed/LSPosed works -- your
 * module sees every process launch and decides whether to act).
 *
 * Referenced by assets/xposed_init, which must contain this class's
 * fully qualified name.
 */
class AudioTrackHook : IXposedHookLoadPackage {

    private val seen = java.util.concurrent.ConcurrentHashMap<String, Boolean>()

    /**
     * Players (ExoPlayer) call write(ByteBuffer, ...) in NON-BLOCKING mode:
     * Android may accept only part of the chunk, and the player then offers
     * the unaccepted tail again on the next call. We process in place, so
     * without this guard that tail would be processed twice (audibly: smeared
     * overlapping copies of the speech). We remember the unaccepted range
     * and skip processing when exactly that range comes back.
     */
    private object RetryGuard {
        private var buf: ByteBuffer? = null
        private var start = 0
        private var end = 0

        @Synchronized
        fun isRetry(b: ByteBuffer, s: Int, e: Int): Boolean =
            buf === b && start == s && end == e

        @Synchronized
        fun record(b: ByteBuffer, s: Int, size: Int, written: Int) {
            if (written in 0 until size) {
                buf = b; start = s + written; end = s + size
            } else {
                buf = null
            }
        }
    }

    /** Log once per overload so logcat tells us which write() the app uses and in what format. */
    private fun logFirst(kind: String, track: AudioTrack) {
        if (seen.putIfAbsent(kind, true) == null) {
            Log.i(TAG, "write($kind) FIRST CALL: rate=${track.sampleRate} " +
                "ch=${track.channelCount} encoding=${track.audioFormat} (2=PCM16, 4=float)")
        }
    }

    override fun handleLoadPackage(lpparam: XC_LoadPackage.LoadPackageParam) {
        if (!hooksInstalled.compareAndSet(false, true)) {
            Log.i(TAG, "AudioTrack already hooked in this process; skipping ${lpparam.packageName}")
            return
        }

        // No package filter on purpose: scoping is already done by
        // LSPosed's per-app scope (rooted) or by which APK was patched
        // (LSPatch/NPatch, rootless). A hard-coded list would silently
        // skip e.g. ReVanced Extended, whose package name differs from
        // stock YouTube's. TARGET_PACKAGES is kept for documentation.
        Log.i(TAG, "Hooking AudioTrack in ${lpparam.packageName}")

        // AudioTrack.write has several overloads depending on how the
        // calling app buffers audio. We cover the common ones; a given
        // app/player (ExoPlayer, MediaPlayer, custom) may use only one
        // of these, so don't be surprised if only one of the three
        // actually fires in practice -- check logcat for "MuteHaramHook"
        // while the target app plays audio to see which one it is.

        hookShortArrayWrite(lpparam)
        hookByteArrayWrite(lpparam)
        hookByteBufferWrite(lpparam)

        hookClockShift()
        hookStreamReset()
    }

    /** flush() (seek), stop() and release() end a stream's audio: clear its delay line. */
    private fun hookStreamReset() {
        for (name in listOf("flush", "stop", "release")) {
            try {
                XposedHelpers.findAndHookMethod(
                    AudioTrack::class.java,
                    name,
                    object : XC_MethodHook() {
                        override fun afterHookedMethod(param: MethodHookParam) {
                            HookConfig.processor.reset(System.identityHashCode(param.thisObject))
                        }
                    }
                )
            } catch (t: Throwable) {
                Log.w(TAG, "$name() not hookable", t)
            }
        }
    }

    /**
     * Video re-sync without touching any video class. Players slave video
     * frame release to the audio clock, which they derive from
     * AudioTrack.getTimestamp()/getPlaybackHeadPosition(). Our processor
     * plays audio [AudioProcessor.latencyMs] late, so we report the playback
     * position that much *smaller*: the player concludes audio is behind and
     * releases video frames that much later. Both calls are shifted
     * consistently because ExoPlayer cross-checks one against the other.
     * Side effect: seek bar / subtitles read ~latencyMs behind the picture.
     */
    private fun hookClockShift() {
        try {
            XposedHelpers.findAndHookMethod(
                AudioTrack::class.java,
                "getPlaybackHeadPosition",
                object : XC_MethodHook() {
                    override fun afterHookedMethod(param: MethodHookParam) {
                        val track = param.thisObject as AudioTrack
                        val shift = shiftFrames(track)
                        val raw = param.result as Int
                        logClock("headPos", raw.toLong(), shift)
                        if (shift > 0) {
                            param.result = maxOf(0, raw - shift)
                        }
                    }
                }
            )
            XposedHelpers.findAndHookMethod(
                AudioTrack::class.java,
                "getTimestamp",
                android.media.AudioTimestamp::class.java,
                object : XC_MethodHook() {
                    override fun afterHookedMethod(param: MethodHookParam) {
                        if (param.result != true) return
                        val track = param.thisObject as AudioTrack
                        val ts = param.args[0] as android.media.AudioTimestamp
                        val shift = shiftFrames(track)
                        logClock("timestamp", ts.framePosition, shift)
                        if (shift > 0) {
                            ts.framePosition = maxOf(0L, ts.framePosition - shift)
                        }
                    }
                }
            )
            Log.i(TAG, "Hooked AudioTrack clock (getPlaybackHeadPosition/getTimestamp)")
        } catch (t: Throwable) {
            Log.w(TAG, "clock-shift hooks failed", t)
        }
    }

    private val clockCalls = java.util.concurrent.ConcurrentHashMap<String, Int>()

    /** Shows whether (and how often) the player asks for the clock we shift. */
    private fun logClock(kind: String, raw: Long, shift: Int) {
        val n = clockCalls.merge(kind, 1, Int::plus) ?: 1
        if (n == 1 || n % 300 == 0) {
            Log.i(TAG, "clock $kind call #$n raw=$raw shiftedBy=$shift frames")
        }
    }

    private fun shiftFrames(track: AudioTrack): Int {
        val p = HookConfig.processor
        if (!p.shiftClock) return 0
        return (p.clockShiftMs.toLong() * track.sampleRate / 1000).toInt()
    }

    private fun hookShortArrayWrite(lpparam: XC_LoadPackage.LoadPackageParam) {
        try {
            XposedHelpers.findAndHookMethod(
                AudioTrack::class.java,
                "write",
                ShortArray::class.java,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        val track = param.thisObject as AudioTrack
                        logFirst("short[]", track)
                        val buffer = param.args[0] as ShortArray
                        val offset = param.args[1] as Int
                        val sizeInShorts = param.args[2] as Int
                        HookConfig.processor.process(
                            buffer, offset, sizeInShorts,
                            track.sampleRate, track.channelCount,
                            System.identityHashCode(track)
                        )
                        // buffer was modified in place; param.args[0]
                        // already points at it, nothing further needed.
                    }
                }
            )
            Log.i(TAG, "Hooked write(short[], int, int)")
        } catch (t: Throwable) {
            Log.w(TAG, "write(short[], int, int) not found/hookable", t)
        }
    }

    private fun hookByteArrayWrite(lpparam: XC_LoadPackage.LoadPackageParam) {
        try {
            XposedHelpers.findAndHookMethod(
                AudioTrack::class.java,
                "write",
                ByteArray::class.java,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        val track = param.thisObject as AudioTrack
                        logFirst("byte[]", track)
                        if (track.audioFormat != AudioFormat.ENCODING_PCM_16BIT) return
                        val bytes = param.args[0] as ByteArray
                        val offset = param.args[1] as Int
                        val sizeInBytes = param.args[2] as Int

                        // Assume 16-bit PCM (by far the common case for
                        // these legacy write() overloads). Convert to
                        // shorts, process, convert back in place.
                        val shortCount = sizeInBytes / 2
                        val shorts = ShortArray(shortCount)
                        val bb = ByteBuffer.wrap(bytes, offset, sizeInBytes)
                            .order(ByteOrder.LITTLE_ENDIAN)
                        for (i in 0 until shortCount) shorts[i] = bb.short

                        HookConfig.processor.process(
                            shorts, 0, shortCount,
                            track.sampleRate, track.channelCount,
                            System.identityHashCode(track)
                        )

                        val outBb = ByteBuffer.wrap(bytes, offset, sizeInBytes)
                            .order(ByteOrder.LITTLE_ENDIAN)
                        for (i in 0 until shortCount) outBb.putShort(shorts[i])
                    }
                }
            )
            Log.i(TAG, "Hooked write(byte[], int, int)")
        } catch (t: Throwable) {
            Log.w(TAG, "write(byte[], int, int) not found/hookable", t)
        }
    }

    private fun hookByteBufferWrite(lpparam: XC_LoadPackage.LoadPackageParam) {
        try {
            XposedHelpers.findAndHookMethod(
                AudioTrack::class.java,
                "write",
                ByteBuffer::class.java,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        val track = param.thisObject as AudioTrack
                        logFirst("ByteBuffer", track)
                        if (track.audioFormat != AudioFormat.ENCODING_PCM_16BIT) return
                        val buf = param.args[0] as ByteBuffer
                        val sizeInBytes = param.args[1] as Int

                        // This is the overload ExoPlayer (what YouTube
                        // and most video apps are built on) typically
                        // uses in its default buffering mode, so this
                        // is the one most likely to actually fire.
                        val startPos = buf.position()
                        param.setObjectExtra("start", startPos)
                        if (RetryGuard.isRetry(buf, startPos, startPos + sizeInBytes)) return
                        val order = buf.order()
                        buf.order(ByteOrder.LITTLE_ENDIAN)

                        val shortCount = sizeInBytes / 2
                        val shorts = ShortArray(shortCount)
                        for (i in 0 until shortCount) {
                            shorts[i] = buf.getShort(startPos + i * 2)
                        }

                        HookConfig.processor.process(
                            shorts, 0, shortCount,
                            track.sampleRate, track.channelCount,
                            System.identityHashCode(track)
                        )

                        for (i in 0 until shortCount) {
                            buf.putShort(startPos + i * 2, shorts[i])
                        }
                        buf.order(order)
                        buf.position(startPos) // restore so write() still reads from the right place
                    }

                    override fun afterHookedMethod(param: MethodHookParam) {
                        val buf = param.args[0] as? ByteBuffer ?: return
                        val start = param.getObjectExtra("start") as? Int ?: return
                        val written = param.result as? Int ?: return
                        RetryGuard.record(buf, start, param.args[1] as Int, written)
                    }
                }
            )
            Log.i(TAG, "Hooked write(ByteBuffer, int, int)")
        } catch (t: Throwable) {
            Log.w(TAG, "write(ByteBuffer, int, int) not found/hookable", t)
        }
    }
}
