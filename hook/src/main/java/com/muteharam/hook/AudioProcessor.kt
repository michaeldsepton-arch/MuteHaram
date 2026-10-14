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
