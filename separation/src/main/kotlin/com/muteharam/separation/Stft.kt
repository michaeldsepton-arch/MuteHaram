package com.muteharam.separation

import kotlin.math.PI
import kotlin.math.cos

/**
 * One-sided complex spectrogram laid out exactly like the TIGER model output:
 * bin f, frame t is at index f * frames + t.
 */
class Spectrogram(val bins: Int, val frames: Int) {
    val re = FloatArray(bins * frames)
    val im = FloatArray(bins * frames)
}

/**
 * STFT / inverse STFT matching torch.stft / torch.istft with n_fft = win = 2048,
 * hop = 512, periodic Hann window, center = true (the caller does the reflect
 * padding for the forward direction; the inverse trims it).
 */
class Stft(val win: Int = 2048, val hop: Int = 512) {
    val bins = win / 2 + 1
    private val fft = Fft(win)
    private val window = DoubleArray(win) { 0.5 - 0.5 * cos(2.0 * PI * it / win) }

    /** Reflect-pad by win/2 on both sides (what torch.stft(center=True) does). */
    fun reflectPad(x: FloatArray, count: Int = x.size): FloatArray {
        val p = win / 2
        val out = FloatArray(count + 2 * p)
        for (i in 0 until count) out[p + i] = x[i]
        for (i in 1..p) {
            out[p - i] = x[reflect(i, count)]
            out[p + count - 1 + i] = x[reflect(count - 1 - i, count)]
        }
        return out
    }

    private fun reflect(i: Int, n: Int): Int {
        if (n == 1) return 0
        var k = i
        val period = 2 * (n - 1)
        k %= period
        if (k < 0) k += period
        return if (k < n) k else period - k
    }

    /** [padded] must have length (frames - 1) * hop + win. */
    fun forward(padded: FloatArray): Spectrogram {
        require((padded.size - win) % hop == 0 && padded.size >= win)
        val frames = (padded.size - win) / hop + 1
        val s = Spectrogram(bins, frames)
        val re = DoubleArray(win)
        val im = DoubleArray(win)
        for (t in 0 until frames) {
            for (i in 0 until win) {
                re[i] = padded[t * hop + i] * window[i]
                im[i] = 0.0
            }
            fft.transform(re, im)
            for (f in 0 until bins) {
                s.re[f * frames + t] = re[f].toFloat()
                s.im[f * frames + t] = im[f].toFloat()
            }
        }
        return s
    }

    /**
     * Inverse STFT with overlap-add and window-envelope normalisation, trimming
     * win/2 from each end: returns (frames - 1) * hop samples.
     */
    fun inverse(re: FloatArray, im: FloatArray, frames: Int, reOffset: Int = 0): FloatArray {
        val total = (frames - 1) * hop + win
        val y = DoubleArray(total)
        val env = DoubleArray(total)
        val fr = DoubleArray(win)
        val fi = DoubleArray(win)
        for (t in 0 until frames) {
            for (f in 0 until bins) {
                fr[f] = re[reOffset + f * frames + t].toDouble()
                fi[f] = im[reOffset + f * frames + t].toDouble()
            }
            // DC and Nyquist are real for a real signal
            fi[0] = 0.0
            fi[bins - 1] = 0.0
            for (f in 1 until bins - 1) {
                fr[win - f] = fr[f]
                fi[win - f] = -fi[f]
            }
            fft.transform(fr, fi, inverse = true)
            for (i in 0 until win) {
                val v = fr[i] / win * window[i]
                y[t * hop + i] += v
                env[t * hop + i] += window[i] * window[i]
            }
        }
        val p = win / 2
        val out = FloatArray(total - 2 * p)
        for (i in out.indices) {
            val e = env[p + i]
            out[i] = if (e > 1e-11) (y[p + i] / e).toFloat() else 0f
        }
        return out
    }
}
