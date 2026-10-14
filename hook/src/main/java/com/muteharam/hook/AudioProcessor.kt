package com.muteharam.hook

/**
 * The one thing this whole project exists to do well: take a chunk of
 * raw PCM audio and return the same chunk with music suppressed and
 * speech/other sound preserved.
 *
 * This interface is intentionally separate from the hook plumbing in
 * [AudioTrackHook] so the actual separation model can be developed,
 * benchmarked, and swapped independently of the Xposed/LSPosed
 * integration -- the hook doesn't need to know or care whether this is
 * a passthrough stub or a full ONNX Runtime session.
 *
 * Contract: `process` is called synchronously on the audio thread that
 * is about to write this buffer to the speaker (we are literally
 * intercepting AudioTrack.write(...) before it happens). Whatever this
 * function returns IS what gets played. That means:
 *   - It must return a buffer of the exact same sample format/length
 *     semantics the caller expects (same array, modified in place, is
 *     the simplest contract and what the stub below does).
 *   - It must complete fast enough that the audio pipeline doesn't
 *     starve. As a rule of thumb: processing a buffer representing N
 *     milliseconds of audio must reliably take meaningfully less than
 *     N milliseconds, or you'll hear underruns/stutter. This is the
 *     real-time constraint referenced throughout the project chat --
 *     benchmark against this before plugging in a real model.
 */
interface AudioProcessor {
    /**
     * @param pcm16 interleaved 16-bit PCM samples, modified in place.
     * @param offset start index into pcm16 for this buffer.
     * @param length number of shorts (not bytes) valid starting at offset.
     * @param sampleRateHz sample rate of the stream being written.
     * @param channelCount channels in the stream (1 = mono, 2 = stereo, etc).
     */
    fun process(
        pcm16: ShortArray,
        offset: Int,
        length: Int,
        sampleRateHz: Int,
        channelCount: Int
    )
}

/**
 * Does nothing -- plays audio completely unmodified. This is the
 * current wiring. Its only job right now is to prove the hook fires
 * and that intercepting + re-writing the buffer doesn't break playback
 * or introduce audible glitches on its own, before any real processing
 * cost is added. Swap [HookConfig.processor] for a real implementation
 * once that's benchmarked.
 */
class PassthroughProcessor : AudioProcessor {
    override fun process(
        pcm16: ShortArray,
        offset: Int,
        length: Int,
        sampleRateHz: Int,
        channelCount: Int
    ) {
        // Intentionally a no-op.
    }
}

/**
 * Wraps a [StreamingWindowBuffer] to give real-model processors the
 * larger context window they need, while still satisfying
 * [AudioProcessor]'s "modify this exact buffer in place" contract that
 * [AudioTrackHook] relies on. This is the class a real separation
 * model should plug into -- implement `modelFn` to run inference on a
 * [windowMs]-length chunk and return the same-length processed chunk.
 *
 * Defaults (120ms window / 60ms hop) were chosen and verified for
 * correctness in prototyping specifically to keep total added latency
 * (~150ms) low enough to stay close to audio/video sync -- see
 * [StreamingWindowBuffer]'s doc comment for why that number matters.
 * Don't increase windowMs without re-checking that tradeoff.
 *
 * Note: operates per-channel-interleaved-as-mono for now, i.e. it
 * treats the interleaved stereo stream as one stream. That's fine for
 * a model whose job is "pass through non-music, suppress music" where
 * stereo-correctness isn't critical, but revisit if the chosen model
 * needs true per-channel processing.
 */
class StreamingAudioProcessor(
    sampleRateHz: Int,
    windowMs: Int = 120,
    hopMs: Int = 60,
    private val modelFn: (FloatArray) -> FloatArray
) : AudioProcessor {

    private val buffer = StreamingWindowBuffer(
        windowSamples = sampleRateHz * windowMs / 1000,
        hopSamples = sampleRateHz * hopMs / 1000,
        modelFn = modelFn
    )

    override fun process(
        pcm16: ShortArray,
        offset: Int,
        length: Int,
        sampleRateHz: Int,
        channelCount: Int
    ) {
        val floats = FloatArray(length) { pcm16[offset + it] / 32768f }
        buffer.push(floats, 0, length)
        val processed = buffer.pull(length)
        for (i in 0 until length) {
            val clamped = processed[i].coerceIn(-1f, 1f)
            pcm16[offset + i] = (clamped * 32767).toInt().toShort()
        }
    }
}
