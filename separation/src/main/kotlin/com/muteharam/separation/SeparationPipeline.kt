package com.muteharam.separation

import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.locks.LockSupport
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Real-time music removal for ONE audio stream (what an app hands to AudioTrack /
 * AAudio), built around a fixed-size [ChunkModel].
 *
 * Principle: delay the audio by a FIXED latency L, so the AI has time to look a
 * little into the future and to finish. Sample n of the output is
 * `original(n - L) - music(n - L)`. The music estimate for a block is computed on
 * a worker thread from a window that extends `margin` before and after the block.
 * If the AI is late for some sample, the ORIGINAL audio (delayed by the same L, so
 * sync is kept) is played instead of waiting.
 *
 * The caller (hook) must make the player's clock run [latencyFrames] behind, which
 * delays the picture by the same amount (see AudioTrackHook / aaudio_hook).
 *
 * Threading: [process] is meant for the audio thread: it only copies samples and
 * never blocks or allocates (after construction). The model runs on the worker.
 */
class SeparationPipeline(
    val sampleRate: Int,
    val channels: Int,
    private val estimator: MusicEstimator,
    marginSeconds: Double = 0.4,
    inferenceBudgetMs: Int = 900,
    slackMs: Int = 150,
    startWorker: Boolean = true,
    var musicGain: Float = 1f,
) {
    /** Convenience: use an on-device [ChunkModel]. */
    constructor(
        sampleRate: Int, channels: Int, model: ChunkModel,
        marginSeconds: Double = 0.4, inferenceBudgetMs: Int = 900, slackMs: Int = 150,
        startWorker: Boolean = true, musicGain: Float = 1f,
    ) : this(sampleRate, channels, ChunkModelEstimator(model), marginSeconds, inferenceBudgetMs,
        slackMs, startWorker, musicGain)

    private val modelRate = ChunkModel.SAMPLE_RATE
    private val chunk = estimator.chunkSamples                 // model domain samples

    // ---- block geometry in the STREAM domain
    private val plan = plan(sampleRate, chunk, marginSeconds, inferenceBudgetMs, slackMs)
    private val windowFrames = plan.windowFrames               // Ws
    private val marginFrames = plan.marginFrames               // Ms
    private val hopFrames = plan.hopFrames                     // Hs
    val latencyFrames: Int = plan.latencyFrames
    val latencyMs: Int get() = (latencyFrames * 1000L / sampleRate).toInt()

    // ---- rings (indices are absolute stream frame numbers, stored modulo cap)
    private val cap = latencyFrames + 2 * windowFrames + sampleRate
    private val orig = FloatArray(cap * channels)
    private val music = FloatArray(cap)
    private val blockOk = BooleanArray(cap / max(1, hopFrames) + 8)

    private val inCount = AtomicLong(0)        // frames received
    @Volatile private var blocksDone = 0L      // blocks finished or skipped, in order
    @Volatile private var generation = 0

    private val toModel = Resampler(sampleRate, modelRate)
    private val fromModel = Resampler(modelRate, sampleRate)
    private val mixBuf = FloatArray(windowFrames)

    // stats
    @Volatile var blocksProcessed = 0L; private set
    @Volatile var blocksSkipped = 0L; private set
    @Volatile var blocksFailed = 0L; private set
    @Volatile var lastError: String? = null; private set
    @Volatile var lateFrames = 0L; private set
    @Volatile var lastInferenceMs = 0.0; private set
    @Volatile var maxInferenceMs = 0.0; private set

    private var worker: Thread? = null
    @Volatile private var stopped = false

    init {
        require(hopFrames > 0) { "margin too large for this chunk size" }
        if (startWorker) {
            worker = Thread({ workLoop() }, "MuteHaram-separation").apply { isDaemon = true; start() }
        }
    }

    // ------------------------------------------------------------ audio thread

    /** Interleaved float samples; replaced in place with the delayed, cleaned audio. */
    fun process(x: FloatArray, offset: Int, frames: Int) {
        val n0 = inCount.get()
        val ch = channels
        // 1. store the new input
        for (i in 0 until frames) {
            val slot = ((n0 + i) % cap).toInt()
            for (c in 0 until ch) orig[slot * ch + c] = x[offset + (i * ch) + c]
        }
        inCount.set(n0 + frames)
        worker?.let { LockSupport.unpark(it) }
        // 2. produce the delayed output
        val done = blocksDone
        val doneFrames = done * hopFrames
        val gain = musicGain
        var late = 0
        for (i in 0 until frames) {
            val idx = n0 + i - latencyFrames
            val o = offset + i * ch
            if (idx < 0) {
                for (c in 0 until ch) x[o + c] = 0f
                continue
            }
            val slot = (idx % cap).toInt()
            var m = 0f
            if (idx < doneFrames && blockOk[((idx / hopFrames) % blockOk.size).toInt()]) {
                m = music[slot] * gain
            } else {
                late++
            }
            for (c in 0 until ch) x[o + c] = orig[slot * ch + c] - m
        }
        if (late > 0) lateFrames += late
    }

    /** Shorts variant for AudioTrack's 16-bit paths. */
    fun processPcm16(x: ShortArray, offset: Int, frames: Int, scratch: FloatArray) {
        val n = frames * channels
        for (i in 0 until n) scratch[i] = x[offset + i] / 32768f
        process(scratch, 0, frames)
        for (i in 0 until n) {
            val v = scratch[i] * 32767f
            x[offset + i] = (if (v > 32767f) 32767f else if (v < -32768f) -32768f else v).toInt().toShort()
        }
    }

    /** Forget everything (seek / stop / new stream). */
    fun reset() {
        generation++
        inCount.set(0)
        blocksDone = 0
        java.util.Arrays.fill(blockOk, false)
    }

    fun close() {
        stopped = true
        worker?.let { LockSupport.unpark(it) }
        estimator.close()
    }

    // ------------------------------------------------------------ worker

    private fun workLoop() {
        while (!stopped) {
            if (!pumpOne()) LockSupport.parkNanos(3_000_000)
        }
    }

    /** Process every block that is ready (for tests that run without a worker thread). */
    fun pump(): Int {
        var n = 0
        while (pumpOne()) n++
        return n
    }

    private fun pumpOne(): Boolean {
        val gen = generation
        val k = blocksDone
        val have = inCount.get()
        val needEnd = (k + 1) * hopFrames + marginFrames           // last frame the window needs
        if (have < needEnd) return false
        val ws = k * hopFrames - marginFrames                      // window start (may be < 0)
        val oldest = have - cap + sampleRate / 10                  // oldest frame still safe in the ring
        if (ws < oldest) {                                         // worker too far behind: skip ahead
            blocksSkipped++
            finishBlock(gen, k, ok = false)
            return true
        }
        // mono mix of the window (zero before the start of the stream)
        for (i in 0 until windowFrames) {
            val idx = ws + i
            if (idx < 0) { mixBuf[i] = 0f; continue }
            val slot = (idx % cap).toInt()
            var s = 0f
            for (c in 0 until channels) s += orig[slot * channels + c]
            mixBuf[i] = s / channels
        }
        val t0 = System.nanoTime()
        val back: FloatArray
        try {
            val atModel = toModel.process(mixBuf, windowFrames)
            val chunkIn = FloatArray(chunk)
            System.arraycopy(atModel, 0, chunkIn, 0, min(atModel.size, chunk))
            val musicModel = estimator.estimate(chunkIn)
            back = fromModel.process(musicModel, musicModel.size)
        } catch (t: Throwable) {
            // AI unavailable (network down, GPU error ...): this block plays un-cleaned, the next one retries.
            blocksFailed++
            lastError = t.toString()
            finishBlock(gen, k, ok = false)
            return true
        }
        val ms = (System.nanoTime() - t0) / 1e6
        lastInferenceMs = ms
        if (ms > maxInferenceMs) maxInferenceMs = ms
        if (generation != gen) return true                         // reset while we were busy
        for (j in 0 until hopFrames) {
            val src = marginFrames + j
            music[((k * hopFrames + j) % cap).toInt()] = if (src < back.size) back[src] else 0f
        }
        blocksProcessed++
        finishBlock(gen, k, ok = true)
        return true
    }

    private fun finishBlock(gen: Int, k: Long, ok: Boolean) {
        if (generation != gen) return
        blockOk[(k % blockOk.size).toInt()] = ok
        blocksDone = k + 1
    }

    /** Block geometry and the fixed output delay, without creating a pipeline. */
    data class Plan(val windowFrames: Int, val marginFrames: Int, val hopFrames: Int, val latencyFrames: Int) {
        fun latencyMs(sampleRate: Int): Int = (latencyFrames * 1000L / sampleRate).toInt()
    }

    companion object {
        fun plan(sampleRate: Int, chunkSamples: Int, marginSeconds: Double, inferenceBudgetMs: Int, slackMs: Int): Plan {
            val ws = (chunkSamples.toLong() * sampleRate / ChunkModel.SAMPLE_RATE).toInt()
            val ms = ceil(marginSeconds * sampleRate).toInt()
            val hs = ws - 2 * ms
            val lat = hs + ms + ((inferenceBudgetMs + slackMs) / 1000.0 * sampleRate).roundToInt()
            return Plan(ws, ms, hs, lat)
        }
    }
}
