package com.muteharam.hook

import android.util.Log
import com.muteharam.separation.ChunkModel
import com.muteharam.separation.MusicEstimator
import com.muteharam.separation.RemoteEstimator
import com.muteharam.separation.SeparationPipeline
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap

/**
 * Owns one [SeparationPipeline] per audio stream and builds them from the live
 * settings (HookSettings). Used by both the Java AudioTrack hook and the native
 * AAudio hook (through NativeBridge.onAudioFloat).
 *
 * Any failure while creating a pipeline (bad server address, model missing ...)
 * is logged once and the stream simply passes through unchanged.
 */
object SeparationHost {
    private const val TAG = "MuteHaramHook"

    private class Stream(
        val key: String,
        val pipeline: SeparationPipeline?,
        val rate: Int,
        val channels: Int,
    ) {
        var scratch = FloatArray(0)
    }

    private val streams = ConcurrentHashMap<Int, Stream>()
    private val loggedErrors = HashSet<String>()

    private fun chunkSamples(): Int = (HookSettings.chunkMs / 1000.0 * ChunkModel.SAMPLE_RATE).toInt()

    /** Fixed output delay (ms) for the current settings: what the player's clock must be shifted by. */
    fun latencyMs(): Int {
        HookSettings.refresh()
        return SeparationPipeline.plan(
            44100, chunkSamples(), HookSettings.marginMs / 1000.0, HookSettings.budgetMs, 150
        ).latencyMs(44100)
    }

    private fun buildEstimator(): MusicEstimator? {
        when (HookSettings.mode) {
            "remote" -> {
                val hostPort = HookSettings.server
                val i = hostPort.lastIndexOf(':')
                if (i <= 0) {
                    once("remote mode needs: adb shell settings put global muteharam_server <ip>:47801")
                    return null
                }
                val host = hostPort.substring(0, i)
                val port = hostPort.substring(i + 1).toIntOrNull()
                if (port == null) { once("bad muteharam_server value: $hostPort"); return null }
                return RemoteEstimator(host, port, HookSettings.token, chunkSamples())
            }
            "local" -> return LocalEstimators.create(chunkSamples())
        }
        return null
    }

    private fun once(msg: String) {
        if (loggedErrors.add(msg)) Log.w(TAG, msg)
    }

    private fun streamFor(id: Int, rate: Int, channels: Int): Stream? {
        val key = HookSettings.pipelineKey() + "|$rate|$channels"
        val cur = streams[id]
        if (cur != null && cur.key == key) return cur
        synchronized(this) {
            val again = streams[id]
            if (again != null && again.key == key) return again
            again?.pipeline?.close()
            var pipeline: SeparationPipeline? = null
            try {
                val est = buildEstimator()
                if (est != null) {
                    pipeline = SeparationPipeline(
                        rate, channels, est,
                        marginSeconds = HookSettings.marginMs / 1000.0,
                        inferenceBudgetMs = HookSettings.budgetMs,
                        slackMs = 150,
                    )
                    Log.i(TAG, "separation stream $id: ${HookSettings.mode}, rate=$rate ch=$channels, " +
                        "latency ${pipeline.latencyMs} ms")
                }
            } catch (t: Throwable) {
                once("could not start separation (${HookSettings.mode}): $t")
            }
            val s = Stream(key, pipeline, rate, channels)
            streams[id] = s
            return s
        }
    }

    /** AudioTrack 16-bit PCM path. Returns false if the buffer was left untouched. */
    fun processPcm16(streamId: Int, pcm: ShortArray, offset: Int, length: Int, rate: Int, channels: Int): Boolean {
        val s = streamFor(streamId, rate, channels) ?: return false
        val p = s.pipeline ?: return false
        HookSettings.refresh()
        p.musicGain = HookSettings.gainPercent / 100f
        if (s.scratch.size < length) s.scratch = FloatArray(length)
        p.processPcm16(pcm, offset, length / channels, s.scratch)
        logStats(streamId, p)
        return true
    }

    /** AAudio float path; [buf] is the native audio buffer, modified in place. */
    fun processFloat(streamId: Int, buf: ByteBuffer, frames: Int, channels: Int, rate: Int) {
        val s = streamFor(streamId, rate, channels) ?: return
        val p = s.pipeline ?: return
        p.musicGain = HookSettings.gainPercent / 100f
        val n = frames * channels
        if (s.scratch.size < n) s.scratch = FloatArray(n)
        val fb = buf.order(ByteOrder.nativeOrder()).asFloatBuffer()
        fb.get(s.scratch, 0, n)
        p.process(s.scratch, 0, frames)
        fb.position(0)
        fb.put(s.scratch, 0, n)
        logStats(streamId, p)
    }

    fun reset(streamId: Int) {
        streams[streamId]?.pipeline?.reset()
    }

    private var lastLog = 0L
    private fun logStats(streamId: Int, p: SeparationPipeline) {
        val now = System.currentTimeMillis()
        if (now - lastLog < 5000) return
        lastLog = now
        Log.i(TAG, "separation stream $streamId: blocks ok=${p.blocksProcessed} failed=${p.blocksFailed} " +
            "skipped=${p.blocksSkipped} late-frames=${p.lateFrames} last-ai=${"%.0f".format(p.lastInferenceMs)}ms " +
            "max-ai=${"%.0f".format(p.maxInferenceMs)}ms err=${p.lastError ?: "-"}")
    }
}

/** On-device model providers (LiteRT). Filled in by LiteRtLocal.kt when available. */
object LocalEstimators {
    @Volatile var factory: ((Int) -> MusicEstimator?)? = null

    fun create(chunkSamples: Int): MusicEstimator? {
        val f = factory
        if (f == null) {
            Log.w("MuteHaramHook", "local mode: on-device model not available in this build")
            return null
        }
        return f(chunkSamples)
    }
}
