package com.muteharam.hook

/**
 * Bridges the gap between what AudioTrackHook actually receives (tiny,
 * irregularly-sized buffers from AudioTrack.write() -- often just a
 * few milliseconds of audio) and what a real separation model needs
 * (a much larger window of context to separate well).
 *
 * Buffering/overlap math verified in Python first (see
 * scripts/windowing_prototype.py in the project chat history/repo
 * notes) before being ported here -- round-trip tests with an
 * identity "model" and a known-gain "model" both reconstructed the
 * input exactly, with zero error at window boundaries (no clicks/
 * seams from the overlap-discard approach).
 *
 * ## The latency constraint this exists to manage
 *
 * This hook modifies [AudioTrackHook]'s intercepted buffer **in
 * place, synchronously** -- the original AudioTrack.write() call
 * happens immediately after the hook returns, using whatever is in
 * the buffer at that moment. That means whatever delay this
 * introduces is audio-only latency with no matching delay on the
 * video side (we don't control video rendering), so it directly
 * trades off against audio/video sync. A 1-second analysis window
 * (what many offline separation models like Demucs are designed
 * around) would mean ~1.25s of one-way audio lag -- clearly audible
 * desync. A ~120ms window with ~150ms total latency is a much more
 * plausible budget to stay close to imperceptible, but it means the
 * separation model itself must be one designed for short-context,
 * low-latency operation (see the real-time source separation
 * research referenced in the project notes), not a full offline
 * model -- this is now a hard model-selection constraint, not just a
 * nice-to-have.
 *
 * The other hard constraint: whatever model runs inside [modelFn]
 * must complete faster than [hopSamples] worth of audio duration, or
 * the output queue will run dry and this falls back to outputting
 * silence rather than stalling the audio thread (stalling it would
 * hang the app's audio callback entirely, which is worse than a brief
 * silent gap). Benchmark actual model inference time against that
 * budget before wiring in anything beyond the current passthrough.
 */
class StreamingWindowBuffer(
    private val windowSamples: Int,
    private val hopSamples: Int,
    private val modelFn: (FloatArray) -> FloatArray
) {
    init {
        require(hopSamples in 1 until windowSamples) {
            "hopSamples must be > 0 and < windowSamples"
        }
    }

    private val marginSamples = (windowSamples - hopSamples) / 2

    // Growable input accumulator. Simple ArrayDeque-backed approach is
    // fine here -- this runs at most a few times per AudioTrack.write()
    // call, not per-sample.
    private val inBuffer = ArrayDeque<Float>()
    private val outQueue = ArrayDeque<Float>()
    private var warmedUp = false

    /** Feed newly-captured samples in. May trigger 0+ inference calls. */
    fun push(samples: FloatArray, offset: Int, length: Int) {
        if (!warmedUp) {
            // Left-pad with silence so the very first real window still
            // has a margin to trim, instead of losing real audio.
            repeat(marginSamples) { inBuffer.addLast(0f) }
            warmedUp = true
        }
        for (i in 0 until length) inBuffer.addLast(samples[offset + i])
        drainReadyWindows()
    }

    private fun drainReadyWindows() {
        while (inBuffer.size >= windowSamples) {
            val window = FloatArray(windowSamples) { inBuffer[it] }
            val processed = modelFn(window)
            // Keep only the centre hop-sized slice -- the model had
            // less context near the edges of its input window.
            for (i in marginSamples until marginSamples + hopSamples) {
                outQueue.addLast(processed[i])
            }
            repeat(hopSamples) { inBuffer.removeFirst() }
        }
    }

    /**
     * Pull exactly [length] processed samples to satisfy one
     * AudioTrack.write() call. If processing hasn't kept up and fewer
     * than [length] are ready, the remainder is silence -- a brief
     * dropout is preferable to blocking the audio thread.
     */
    fun pull(length: Int): FloatArray {
        val out = FloatArray(length)
        var i = 0
        while (i < length && outQueue.isNotEmpty()) {
            out[i] = outQueue.removeFirst()
            i++
        }
        // remaining entries, if any, stay at the FloatArray default (0f = silence)
        return out
    }
}
