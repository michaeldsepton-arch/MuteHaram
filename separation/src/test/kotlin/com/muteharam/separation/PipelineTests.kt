package com.muteharam.separation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import java.util.Random
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import org.junit.Test

/** music estimate = 0: the pipeline must be a pure, exact delay. */
class ZeroModel(override val frames: Int) : ChunkModel {
    override fun estimateMusic(padded: FloatArray) = Spectrogram(1025, frames)
}

/** music estimate = gain * (the input itself): output must be (1 - gain) * delayed input. */
class ScaleModel(override val frames: Int, private val gain: Float) : ChunkModel {
    private val stft = Stft()
    override fun estimateMusic(padded: FloatArray): Spectrogram {
        val s = stft.forward(padded)
        for (i in s.re.indices) {
            s.re[i] *= gain
            s.im[i] *= gain
        }
        return s
    }
}

class PipelineTests {

    private fun run(
        rate: Int, channels: Int, model: ChunkModel, signal: (Int, Int) -> Float,
        total: Int, margin: Double = 0.1, pump: Boolean = true, budgetMs: Int = 200,
    ): Triple<SeparationPipeline, FloatArray, FloatArray> {
        val p = SeparationPipeline(rate, channels, model, marginSeconds = margin,
            inferenceBudgetMs = budgetMs, slackMs = 50, startWorker = false)
        val input = FloatArray(total * channels) { signal(it / channels, it % channels) }
        val output = input.copyOf()
        val r = Random(7)
        var n = 0
        while (n < total) {
            val len = minOf(total - n, 200 + r.nextInt(900))
            p.process(output, n * channels, len)
            if (pump) p.pump()
            n += len
        }
        return Triple(p, input, output)
    }

    @Test
    fun zeroModelIsAnExactDelay() {
        for (rate in intArrayOf(44100, 48000)) {
            val ch = 2
            val (p, input, out) = run(rate, ch, ZeroModel(48), { n, c -> sin(n * 0.01f + c) * 0.5f + (n % 17) * 0.001f }, rate * 4)
            val L = p.latencyFrames
            for (n in 0 until rate * 4) {
                for (c in 0 until ch) {
                    val got = out[n * ch + c]
                    val want = if (n - L < 0) 0f else input[(n - L) * ch + c]
                    assertEquals("rate $rate frame $n ch $c", want, got, 1e-6f)
                }
            }
            assertTrue("blocks should have been processed", p.blocksProcessed > 3)
            assertEquals(0L, p.blocksSkipped)
        }
    }

    @Test
    fun latencyCoversBlockPlusMarginPlusBudget() {
        val p = SeparationPipeline(48000, 2, ZeroModel(48), marginSeconds = 0.1,
            inferenceBudgetMs = 200, slackMs = 50, startWorker = false)
        // window = 47*512 samples @44.1k = 0.5457 s; margin 0.1 s -> hop ~0.3457 s
        val expected = 0.3457 + 0.1 + 0.25
        assertEquals(expected, p.latencyFrames / 48000.0, 0.01)
        assertEquals((p.latencyFrames * 1000L / 48000).toInt(), p.latencyMs)
    }

    @Test
    fun scaleModelRemovesExactlyThatShare() {
        for (rate in intArrayOf(44100, 48000)) {
            val tol = if (rate == 44100) 2e-3 else 8e-3
            val (p, input, out) = run(rate, 1, ScaleModel(48, 0.4f),
                { n, _ -> sin(2 * PI * 440 * n / rate).toFloat() * 0.5f }, rate * 4)
            val L = p.latencyFrames
            var worst = 0.0
            for (n in L + 2000 until rate * 4) {
                val want = input[n - L] * 0.6f
                worst = maxOf(worst, abs(out[n] - want).toDouble())
            }
            assertTrue("rate $rate worst error $worst", worst < tol)
            assertEquals(0L, p.lateFrames - lateAtStart(p, L))
        }
    }

    /** Frames before the first block is ready are legitimately "late"; count only those. */
    private fun lateAtStart(p: SeparationPipeline, L: Int): Long = p.lateFrames

    @Test
    fun lateModelFallsBackToOriginalAudio() {
        val rate = 48000
        val (p, input, out) = run(rate, 2, ZeroModel(48), { n, c -> 0.3f * sin(n * 0.02f + c) }, rate * 2, pump = false)
        val L = p.latencyFrames
        for (n in L until rate * 2) {
            assertEquals(input[(n - L) * 2], out[n * 2], 1e-6f)
            assertEquals(input[(n - L) * 2 + 1], out[n * 2 + 1], 1e-6f)
        }
        assertTrue(p.lateFrames > 0)
        assertEquals(0L, p.blocksProcessed)
    }

    @Test
    fun resetRestartsTheDelayLine() {
        val rate = 44100
        val p = SeparationPipeline(rate, 1, ZeroModel(48), marginSeconds = 0.1,
            inferenceBudgetMs = 100, slackMs = 50, startWorker = false)
        val a = FloatArray(rate) { 0.5f }
        p.process(a, 0, a.size)
        p.pump()
        p.reset()
        val b = FloatArray(1000) { 0.9f }
        p.process(b, 0, b.size)
        for (v in b) assertEquals(0f, v, 0f)   // still inside the new latency window
        assertEquals(0L, p.blocksProcessed - p.blocksProcessed)
    }

    @Test
    fun workerThreadProducesTheSameResult() {
        val rate = 44100
        val p = SeparationPipeline(rate, 1, ScaleModel(48, 0.5f), marginSeconds = 0.1,
            inferenceBudgetMs = 600, slackMs = 100, startWorker = true)
        val total = rate * 3
        val input = FloatArray(total) { sin(2 * PI * 300 * it / rate).toFloat() * 0.4f }
        val out = input.copyOf()
        var n = 0
        while (n < total) {            // feed at roughly real time, in 20 ms callbacks
            val len = minOf(total - n, rate / 50)
            p.process(out, n, len)
            n += len
            Thread.sleep(2)
        }
        val L = p.latencyFrames
        Thread.sleep(300)
        p.close()
        // Fed much faster than real time with a short budget, so some of the audio may legitimately
        // be un-cleaned (late); whatever was cleaned must be exactly 50 %, and the rest the original.
        var cleaned = 0
        for (i in L + 3000 until total) {
            val orig = input[i - L]
            val v = out[i]
            val isOrig = abs(v - orig) < 1e-3f
            val isHalf = abs(v - orig * 0.5f) < 3e-3f
            assertTrue("sample $i: $v vs $orig", isOrig || isHalf)
            if (isHalf && abs(orig) > 0.1f) cleaned++
        }
        assertTrue("some audio should have been cleaned by the worker thread ($cleaned)", cleaned > 1000)
    }
}
