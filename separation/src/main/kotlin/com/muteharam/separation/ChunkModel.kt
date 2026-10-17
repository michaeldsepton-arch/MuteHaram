package com.muteharam.separation

/**
 * The AI part, behind an interface so everything around it can be tested
 * without a phone.
 *
 * A model works on fixed-size chunks of mono 44.1 kHz audio: [frames] STFT
 * frames, i.e. [chunkSamples] = (frames - 1) * 512 samples (plus 1024 samples of
 * reflect padding on each side, which the pipeline adds).
 */
interface ChunkModel {
    /** T: number of STFT frames per call (the .tflite is built for exactly this). */
    val frames: Int

    /**
     * @param padded mono audio, length chunkSamples + 2048 (reflect padded)
     * @return the estimated MUSIC as a complex spectrogram (bins = 1025, [frames] frames)
     */
    fun estimateMusic(padded: FloatArray): Spectrogram

    fun close() {}

    val chunkSamples: Int get() = (frames - 1) * HOP

    companion object {
        const val SAMPLE_RATE = 44100
        const val HOP = 512
        const val WIN = 2048
    }
}

/**
 * "Given a chunk of mono 44.1 kHz audio, return the music in it" -- the one thing
 * the pipeline needs from the AI, wherever it runs (this phone's GPU, or a computer
 * across Wi-Fi). Both directions use exactly [chunkSamples] samples.
 */
interface MusicEstimator {
    val chunkSamples: Int
    fun estimate(chunk: FloatArray): FloatArray
    fun close() {}

    /** True if [estimateStereo] works: left and right are cleaned as two separate signals. */
    val stereoCapable: Boolean get() = false

    /** Music estimate for the left and right window (each exactly [chunkSamples] long). */
    fun estimateStereo(left: FloatArray, right: FloatArray): Array<FloatArray> =
        throw UnsupportedOperationException("stereo is not supported by this estimator")
}

/** Runs a fixed-shape on-device [ChunkModel]: reflect pad -> model -> inverse STFT. */
class ChunkModelEstimator(private val model: ChunkModel) : MusicEstimator {
    private val stft = Stft()
    override val chunkSamples: Int get() = model.chunkSamples

    override fun estimate(chunk: FloatArray): FloatArray {
        val spec = model.estimateMusic(stft.reflectPad(chunk))
        return stft.inverse(spec.re, spec.im, spec.frames)
    }

    override fun close() = model.close()
}
