package com.muteharam.hook

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
 * Entry point LSPosed calls once per app process that starts, for
 * every app on the device (that's how Xposed/LSPosed works -- your
 * module sees every process launch and decides whether to act).
 *
 * Referenced by assets/xposed_init, which must contain this class's
 * fully qualified name.
 */
class AudioTrackHook : IXposedHookLoadPackage {

    override fun handleLoadPackage(lpparam: XC_LoadPackage.LoadPackageParam) {
        if (lpparam.packageName !in HookConfig.TARGET_PACKAGES) return

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
                        val buffer = param.args[0] as ShortArray
                        val offset = param.args[1] as Int
                        val sizeInShorts = param.args[2] as Int
                        HookConfig.processor.process(
                            buffer, offset, sizeInShorts,
                            track.sampleRate, track.channelCount
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
                            track.sampleRate, track.channelCount
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
                        val buf = param.args[0] as ByteBuffer
                        val sizeInBytes = param.args[1] as Int

                        // This is the overload ExoPlayer (what YouTube
                        // and most video apps are built on) typically
                        // uses in its default buffering mode, so this
                        // is the one most likely to actually fire.
                        val startPos = buf.position()
                        val order = buf.order()
                        buf.order(ByteOrder.LITTLE_ENDIAN)

                        val shortCount = sizeInBytes / 2
                        val shorts = ShortArray(shortCount)
                        for (i in 0 until shortCount) {
                            shorts[i] = buf.getShort(startPos + i * 2)
                        }

                        HookConfig.processor.process(
                            shorts, 0, shortCount,
                            track.sampleRate, track.channelCount
                        )

                        for (i in 0 until shortCount) {
                            buf.putShort(startPos + i * 2, shorts[i])
                        }
                        buf.order(order)
                        buf.position(startPos) // restore so write() still reads from the right place
                    }
                }
            )
            Log.i(TAG, "Hooked write(ByteBuffer, int, int)")
        } catch (t: Throwable) {
            Log.w(TAG, "write(ByteBuffer, int, int) not found/hookable", t)
        }
    }
}
