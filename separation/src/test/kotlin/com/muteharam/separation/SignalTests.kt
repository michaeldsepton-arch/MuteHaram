package com.muteharam.separation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import java.util.Random
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Test

class SignalTests {

    @Test
    fun fftMatchesNaiveDft() {
        val n = 64
        val r = Random(1)
        val re = DoubleArray(n) { r.nextGaussian() }
        val im = DoubleArray(n) { r.nextGaussian() }
        val fr = re.copyOf()
        val fi = im.copyOf()
        Fft(n).transform(fr, fi)
        for (k in 0 until n) {
            var sr = 0.0
            var si = 0.0
            for (t in 0 until n) {
                val a = -2.0 * PI * k * t / n
                sr += re[t] * cos(a) - im[t] * sin(a)
                si += re[t] * sin(a) + im[t] * cos(a)
            }
            assertEquals(sr, fr[k], 1e-9)
            assertEquals(si, fi[k], 1e-9)
        }
        // inverse (unscaled) restores the input times n
        Fft(n).transform(fr, fi, inverse = true)
        for (k in 0 until n) assertEquals(re[k] * n, fr[k], 1e-8)
    }

    @Test
    fun stftRoundTrip() {
        val stft = Stft()
        val frames = 24
        val len = (frames - 1) * stft.hop
        val r = Random(2)
        val x = FloatArray(len) { r.nextGaussian().toFloat() * 0.3f }
        val spec = stft.forward(stft.reflectPad(x))
        assertEquals(frames, spec.frames)
        assertEquals(1025, spec.bins)
        val y = stft.inverse(spec.re, spec.im, spec.frames)
        assertEquals(len, y.size)
        for (i in 0 until len) assertEquals("sample $i", x[i].toDouble(), y[i].toDouble(), 2e-4)
    }

    @Test
    fun stftMatchesDefinitionForOneFrame() {
        val stft = Stft()
        val frames = 6
        val r = Random(3)
        val x = FloatArray((frames - 1) * stft.hop) { r.nextGaussian().toFloat() }
        val padded = stft.reflectPad(x)
        val spec = stft.forward(padded)
        val t = 2
        for (f in intArrayOf(0, 1, 7, 100, 513, 1024)) {
            var sr = 0.0
            var si = 0.0
            for (i in 0 until 2048) {
                val w = 0.5 - 0.5 * cos(2.0 * PI * i / 2048)
                val a = -2.0 * PI * f * i / 2048
                sr += padded[t * 512 + i] * w * cos(a)
                si += padded[t * 512 + i] * w * sin(a)
            }
            assertEquals(sr, spec.re[f * frames + t].toDouble(), 2e-3)
            assertEquals(si, spec.im[f * frames + t].toDouble(), 2e-3)
        }
    }

    @Test
    fun reflectPadMatchesTorchConvention() {
        val stft = Stft()
        val x = FloatArray(3000) { it.toFloat() }
        val p = stft.reflectPad(x)
        assertEquals(3000 + 2048, p.size)
        assertEquals(x[1].toDouble(), p[1023].toDouble(), 0.0)     // p[1024 - 1] = x[1]
        assertEquals(x[1024].toDouble(), p[0].toDouble(), 0.0)     // p[0] = x[1024]
        assertEquals(x[2998].toDouble(), p[1024 + 3000].toDouble(), 0.0)  // first sample after the signal
    }

    private fun sineError(inRate: Int, outRate: Int, freq: Double): Double {
        val n = inRate  // one second
        val x = FloatArray(n) { sin(2.0 * PI * freq * it / inRate).toFloat() }
        val y = Resampler(inRate, outRate).process(x)
        var err = 0.0
        var cnt = 0
        for (j in 200 until y.size - 200) {
            val ideal = sin(2.0 * PI * freq * j / outRate)
            err += (y[j] - ideal) * (y[j] - ideal)
            cnt++
        }
        return sqrt(err / cnt)
    }

    @Test
    fun resamplerKeepsSinesAccurate() {
        assertTrue("48k->44.1k ${sineError(48000, 44100, 1000.0)}", sineError(48000, 44100, 1000.0) < 2e-3)
        assertTrue("44.1k->48k ${sineError(44100, 48000, 3000.0)}", sineError(44100, 48000, 3000.0) < 2e-3)
        assertTrue("48k->44.1k 8k ${sineError(48000, 44100, 8000.0)}", sineError(48000, 44100, 8000.0) < 6e-3)
    }

    @Test
    fun resamplerLengthsAndPassthrough() {
        val r = Resampler(48000, 44100)
        assertEquals(44100, r.outputLength(48000))
        val x = FloatArray(10) { it.toFloat() }
        val same = Resampler(44100, 44100).process(x)
        for (i in x.indices) assertEquals(x[i], same[i], 0f)
        // impulse stays centred (no time shift)
        val imp = FloatArray(2000)
        imp[1000] = 1f
        val y = Resampler(48000, 44100).process(imp)
        var best = 0
        for (i in y.indices) if (abs(y[i]) > abs(y[best])) best = i
        assertEquals(1000.0 * 44100 / 48000, best.toDouble(), 1.0)
    }
}
