package com.muteharam.separation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import org.junit.Test

/**
 * Cross-language check against the REAL Python server (pc/muteharam_pc/stream_server.py
 * with the real TIGER model). Skipped unless MUTEHARAM_SERVER=host:port is set; CI
 * starts the server and sets it (see the "integration" job).
 */
class LiveServerTest {

    @Test
    fun stereoPipelineAgainstTheRealPythonServer() {
        val hp = System.getenv("MUTEHARAM_SERVER")
        if (hp.isNullOrBlank()) return
        val host = hp.substringBeforeLast(':')
        val port = hp.substringAfterLast(':').toInt()
        val rate = 48000
        val chunk = 88 * 512
        val est = RemoteEstimator(host, port, "", chunk, readTimeoutMs = 120_000, stereo = true)
        val p = SeparationPipeline(rate, 2, est, marginSeconds = 0.2, inferenceBudgetMs = 100,
            slackMs = 50, startWorker = false)
        assertTrue(p.isStereo)
        val total = rate * 5
        val input = FloatArray(total * 2) { i ->
            val n = i / 2
            val f = if (i % 2 == 0) 1318.0 else 1760.0
            (0.25 * sin(2 * PI * 220 * n / rate) + 0.2 * sin(2 * PI * f * n / rate)).toFloat()
        }
        val out = input.copyOf()
        var n = 0
        while (n < total) {
            val len = minOf(total - n, 960)
            p.process(out, n * 2, len)
            p.pump()
            n += len
        }
        assertEquals("blocks failed: ${p.lastError}", 0L, p.blocksFailed)
        assertTrue("blocks processed: ${p.blocksProcessed}", p.blocksProcessed >= 3)
        for (v in out) assertTrue(v.isFinite() && abs(v) < 4f)
        p.close()
    }

    @Test
    fun pipelineAgainstTheRealPythonServer() {
        val hp = System.getenv("MUTEHARAM_SERVER")
        if (hp.isNullOrBlank()) return
        val host = hp.substringBeforeLast(':')
        val port = hp.substringAfterLast(':').toInt()

        val rate = 48000
        val chunk = 88 * 512                               // ~1.0 s: keeps the CPU server quick
        val est = RemoteEstimator(host, port, "", chunk, readTimeoutMs = 120_000)
        val p = SeparationPipeline(rate, 2, est, marginSeconds = 0.2, inferenceBudgetMs = 100,
            slackMs = 50, startWorker = false)

        val total = rate * 5
        val input = FloatArray(total * 2) { i ->
            val n = i / 2
            // "speech-like" low tone plus a brighter "music" chord
            (0.25 * sin(2 * PI * 220 * n / rate) + 0.2 * sin(2 * PI * 1318 * n / rate) +
                0.15 * sin(2 * PI * 1568 * n / rate)).toFloat()
        }
        val out = input.copyOf()
        var n = 0
        while (n < total) {
            val len = minOf(total - n, 960)
            p.process(out, n * 2, len)
            p.pump()
            n += len
        }
        assertEquals("blocks failed: ${p.lastError}", 0L, p.blocksFailed)
        assertTrue("blocks processed: ${p.blocksProcessed}", p.blocksProcessed >= 3)
        for (v in out) assertTrue(v.isFinite() && abs(v) < 4f)
        // the real model must have changed something relative to the plain delayed original
        val L = p.latencyFrames
        var diff = 0.0
        for (i in (L + 8000) * 2 until total * 2) diff += abs(out[i] - input[i - L * 2]).toDouble()
        assertTrue("the AI should have altered the audio (diff=$diff)", diff > 1.0)
        p.close()
    }
}
