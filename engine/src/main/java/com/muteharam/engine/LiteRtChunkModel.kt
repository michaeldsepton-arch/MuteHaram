package com.muteharam.engine

import android.content.Context
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.CompiledModel
import com.muteharam.separation.ChunkModel
import com.muteharam.separation.Spectrogram
import java.io.File

/**
 * TIGER-DnR music network on the device GPU (LiteRT). The .tflite files are the
 * Hugging Face "TIGER-DnR-LiteRT" model re-timed to [frames] STFT frames by
 * hook/research/retime/retime.py (done in CI).
 *
 * Input  [1, chunk + 2048] reflect-padded mono audio; outputs (real, imag)
 * [1, 3, 1025, frames] with the music estimate as source 0.
 */
class LiteRtChunkModel private constructor(
    private val compiled: CompiledModel,
    override val frames: Int,
) : ChunkModel {
    private val inB = compiled.createInputBuffers()
    private val outB = compiled.createOutputBuffers()
    private val bins = 1025

    override fun estimateMusic(padded: FloatArray): Spectrogram {
        require(padded.size == chunkSamples + ChunkModel.WIN)
        inB[0].writeFloat(padded)
        compiled.run(inB, outB)
        val re = outB[0].readFloat()
        val im = outB[1].readFloat()
        val n = bins * frames
        val s = Spectrogram(bins, frames)
        System.arraycopy(re, 0, s.re, 0, n)   // source 0 = music comes first
        System.arraycopy(im, 0, s.im, 0, n)
        return s
    }

    override fun close() {
        try { compiled.close() } catch (_: Throwable) {}
    }

    companion object {
        val AVAILABLE_FRAMES = intArrayOf(128, 176, 256)

        fun assetName(frames: Int) = "tiger_music_T$frames.tflite"

        fun load(ctx: Context, frames: Int): LiteRtChunkModel {
            val f = File(ctx.filesDir, assetName(frames))
            if (!f.exists() || f.length() == 0L) {
                ctx.assets.open(assetName(frames)).use { i -> f.outputStream().use { o -> i.copyTo(o) } }
            }
            val m = CompiledModel.create(f.absolutePath, CompiledModel.Options(Accelerator.GPU), null)
            return LiteRtChunkModel(m, frames)
        }
    }
}
