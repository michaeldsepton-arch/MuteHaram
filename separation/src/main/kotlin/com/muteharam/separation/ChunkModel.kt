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
