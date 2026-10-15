package com.muteharam.separation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.sqrt
import org.junit.Test

/**
 * Renders a real clip through the exact Android pipeline (48 kHz stream, windowing, fixed
 * delay, resampling, network round trip to the real Python server) and writes WAVs you can
 * listen to. Skipped unless MUTEHARAM_SERVER and MUTEHARAM_CLIP are set (CI integration job).
 */
class RenderDemoTest {

    private class Wav(val rate: Int, val channels: Int, val data: FloatArray)

    private fun readWav(f: File): Wav {
        val b = ByteBuffer.wrap(f.readBytes()).order(ByteOrder.LITTLE_ENDIAN)
        var pos = 12
        var fmt = 1; var ch = 1; var rate = 44100; var bits = 16
        var dataOff = -1; var dataLen = 0
        while (pos + 8 <= b.limit()) {
            val id = String(ByteArray(4) { b.get(pos + it) })
            val len = b.getInt(pos + 4)
            if (id == "fmt ") {
                fmt = b.getShort(pos + 8).toInt(); ch = b.getShort(pos + 10).toInt()
                rate = b.getInt(pos + 12); bits = b.getShort(pos + 22).toInt()
            } else if (id == "data") {
                dataOff = pos + 8; dataLen = minOf(len, b.limit() - dataOff); break
            }
            pos += 8 + len + (len and 1)
        }
        require(dataOff >= 0)
        val n = dataLen / (bits / 8)
        val out = FloatArray(n)
        for (i in 0 until n) {
            out[i] = when {
                fmt == 3 && bits == 32 -> b.getFloat(dataOff + i * 4)
                bits == 16 -> b.getShort(dataOff + i * 2) / 32768f
                else -> throw IllegalArgumentException("unsupported wav format $fmt/$bits")
            }
        }
        return Wav(rate, ch, out)
    }

    private fun writeWav(f: File, rate: Int, channels: Int, x: FloatArray) {
        val data = ByteBuffer.allocate(x.size * 2).order(ByteOrder.LITTLE_ENDIAN)
        for (v in x) data.putShort((v.coerceIn(-1f, 1f) * 32767f).toInt().toShort())
        val h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        h.put("RIFF".toByteArray()).putInt(36 + data.capacity()).put("WAVEfmt ".toByteArray())
        h.putInt(16).putShort(1).putShort(channels.toShort()).putInt(rate)
        h.putInt(rate * channels * 2).putShort((channels * 2).toShort()).putShort(16)
        h.put("data".toByteArray()).putInt(data.capacity())
        f.parentFile.mkdirs()
        f.writeBytes(h.array() + data.array())
    }

    private fun rms(x: FloatArray, from: Int, to: Int): Double {
        var s = 0.0
        for (i in from until to) s += x[i] * x[i]
        return sqrt(s / maxOf(1, to - from))
    }

    @Test
    fun renderRealClipThroughTheAndroidPipeline() {
        val hp = System.getenv("MUTEHARAM_SERVER")
        val clip = System.getenv("MUTEHARAM_CLIP")
        if (hp.isNullOrBlank() || clip.isNullOrBlank()) return
        val outDir = File(System.getenv("MUTEHARAM_OUT") ?: "build/android_pipeline_demo")
        val seconds = (System.getenv("MUTEHARAM_SECONDS") ?: "20").toInt()

        // the clip is mono 44.1 kHz; make it a 48 kHz stereo stream like YouTube's
        val w = readWav(File(clip))
        val mono = w.data.copyOf(minOf(w.data.size, w.rate * seconds * w.channels))
        val at48 = Resampler(w.rate, 48000).process(mono)
        val rate = 48000
        val frames = at48.size
        val stream = FloatArray(frames * 2) { at48[it / 2] }

        val host = hp.substringBeforeLast(':')
        val port = hp.substringAfterLast(':').toInt()
        val chunk = 131 * 512                        // ~1.52 s windows: quick on the CI's CPU
        val est = RemoteEstimator(host, port, "", chunk, readTimeoutMs = 300_000)
        val p = SeparationPipeline(rate, 2, est, marginSeconds = 0.35, inferenceBudgetMs = 100,
            slackMs = 50, startWorker = false)

        val out = stream.copyOf()
        var n = 0
        while (n < frames) {
            val len = minOf(frames - n, 1024)
            p.process(out, n * 2, len)
            p.pump()
            n += len
        }
        assertEquals("pipeline errors: ${p.lastError}", 0L, p.blocksFailed)
        assertTrue(p.blocksProcessed >= 5)

        val L = p.latencyFrames
        // for listening: the original delayed by the same L, and the cleaned result
        val delayedOrig = FloatArray(frames * 2)
        for (i in L * 2 until frames * 2) delayedOrig[i] = stream[i - L * 2]
        writeWav(File(outDir, "1_original_delayed.wav"), rate, 2, delayedOrig.copyOfRange(L * 2, frames * 2))
        writeWav(File(outDir, "2_android_pipeline_cleaned.wav"), rate, 2, out.copyOfRange(L * 2, frames * 2))
        val e0 = rms(delayedOrig, L * 2 + 40000, frames * 2)
        val e1 = rms(out, L * 2 + 40000, frames * 2)
        println("RENDER: latency=${p.latencyMs} ms, blocks ok=${p.blocksProcessed}, " +
            "late frames=${p.lateFrames}, RMS original=$e0 cleaned=$e1")
        File(outDir, "info.txt").writeText(
            "latency ${p.latencyMs} ms, blocks ${p.blocksProcessed}, late frames ${p.lateFrames}\n" +
                "RMS original $e0 -> cleaned $e1 (${"%.1f".format(20 * Math.log10(e1 / e0))} dB)\n")
        assertTrue("cleaned audio must not be louder than the original", e1 <= e0 * 1.05)
    }
}
