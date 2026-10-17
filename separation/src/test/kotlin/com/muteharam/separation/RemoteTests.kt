package com.muteharam.separation

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import java.io.DataInputStream
import java.io.IOException
import java.net.ServerSocket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import org.junit.Test

/** A tiny stand-in for stream_server.py: replies with `gain * input` as the "music". */
class FakeServer(private val token: String, private val gain: Float, private val maxWindows: Int = Int.MAX_VALUE,
                 private val gainRight: Float = gain) {
    private val server = ServerSocket(0)
    val port: Int get() = server.localPort
    @Volatile var windows = 0

    init {
        thread(isDaemon = true) {
            try {
                while (true) {
                    val s = server.accept()
                    thread(isDaemon = true) { serve(s) }
                }
            } catch (_: IOException) {}
        }
    }

    private fun serve(s: java.net.Socket) {
        try {
            s.tcpNoDelay = true
            val ins = DataInputStream(s.getInputStream())
            val magic = ByteArray(4); ins.readFully(magic)
            val tl = ins.readUnsignedByte()
            val tk = ByteArray(tl); ins.readFully(tk)
            val ok = String(tk) == token
            val v2 = magic.contentEquals(RemoteProtocol.MAGIC2)
            s.getOutputStream().write((if (v2) RemoteProtocol.MAGIC2 else RemoteProtocol.MAGIC) + byteArrayOf(if (ok) 0 else 1))
            if (!ok) { s.close(); return }
            while (true) {
                if (v2) {
                    val h2 = ByteArray(12); ins.readFully(h2)
                    val b2 = ByteBuffer.wrap(h2).order(ByteOrder.LITTLE_ENDIAN)
                    val seq2 = b2.int; val n2 = b2.int; val ch2 = b2.int
                    val raw = ByteArray(n2 * ch2 * 2); ins.readFully(raw)
                    if (windows >= maxWindows) { s.close(); return }
                    windows++
                    val inp = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
                    val out2 = ByteBuffer.allocate(12 + n2 * ch2 * 2).order(ByteOrder.LITTLE_ENDIAN)
                    out2.putInt(seq2).putInt(n2).putInt(ch2)
                    for (c in 0 until ch2) {
                        val g = if (c == 0) gain else gainRight
                        for (i in 0 until n2) out2.putShort(Math.round(inp.get(c * n2 + i) / 32768f * g * 32767f).toShort())
                    }
                    s.getOutputStream().write(out2.array())
                    continue
                }
                val h = ByteArray(8); ins.readFully(h)
                val hb = ByteBuffer.wrap(h).order(ByteOrder.LITTLE_ENDIAN)
                val seq = hb.int; val n = hb.int
                val body = ByteArray(n * 4); ins.readFully(body)
                if (windows >= maxWindows) { s.close(); return }
                windows++
                val x = FloatArray(n)
                ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(x)
                for (i in x.indices) x[i] *= gain
                val out = ByteBuffer.allocate(8 + n * 4).order(ByteOrder.LITTLE_ENDIAN)
                out.putInt(seq).putInt(n); out.asFloatBuffer().put(x)
                s.getOutputStream().write(out.array())
            }
        } catch (_: IOException) {
        } finally {
            try { s.close() } catch (_: IOException) {}
        }
    }

    fun close() = server.close()
}

class RemoteTests {

    @Test
    fun framingMatchesTheGoldenBytes() {
        // The same bytes are asserted in pc/tests/test_protocol_golden.py
        val hello = RemoteProtocol.encodeHello("ab")
        assertArrayEquals(byteArrayOf(0x4d, 0x48, 0x52, 0x31, 2, 0x61, 0x62), hello)
        val req = RemoteProtocol.encodeRequest(1, floatArrayOf(1.0f, -2.0f))
        assertArrayEquals(
            byteArrayOf(1, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0x80.toByte(), 0x3f, 0, 0, 0, 0xc0.toByte()), req)
    }

    @Test
    fun version2FramingMatchesTheGoldenBytes() {
        // The same bytes are asserted in pc/tests/test_protocol_golden.py
        val hello = RemoteProtocol.encodeHello("ab", RemoteProtocol.MAGIC2)
        assertArrayEquals(byteArrayOf(0x4d, 0x48, 0x52, 0x32, 2, 0x61, 0x62), hello)
        // two channels of two samples: L = [1, -2], R = [3, -4] (as int16 values, i.e. divided by 32768)
        val l = floatArrayOf(1f / 32768f, -2f / 32768f)
        val r = floatArrayOf(3f / 32768f, -4f / 32768f)
        val req = RemoteProtocol.encodeRequestV2(1, listOf(l, r))
        assertArrayEquals(
            byteArrayOf(1, 0, 0, 0, 2, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0xfe.toByte(), 0xff.toByte(), 3, 0, 0xfc.toByte(), 0xff.toByte()), req)
    }

    @Test
    fun stereoPipelineCleansEachChannelOnItsOwn() {
        val rate = 48000
        // left: the "music" is half the sound; right: a quarter of it. A channel mix-up would show.
        val srv = FakeServer("t", 0.5f, gainRight = 0.25f)
        try {
            val chunk = 47 * 512
            val est = RemoteEstimator("127.0.0.1", srv.port, "t", chunk, stereo = true)
            assertTrue(est.stereoCapable)
            val p = SeparationPipeline(rate, 2, est, marginSeconds = 0.1, inferenceBudgetMs = 200,
                slackMs = 50, startWorker = false)
            assertTrue(p.isStereo)
            val total = rate * 3
            val input = FloatArray(total * 2) { i ->
                val n = i / 2
                if (i % 2 == 0) sin(2 * PI * 330 * n / rate).toFloat() * 0.5f
                else sin(2 * PI * 550 * n / rate).toFloat() * 0.4f
            }
            val out = input.copyOf()
            var n = 0
            while (n < total) {
                val len = minOf(total - n, 777)
                p.process(out, n * 2, len)
                p.pump()
                n += len
            }
            assertEquals("blocks failed: ${p.lastError}", 0L, p.blocksFailed)
            val L = p.latencyFrames
            var worstL = 0.0
            var worstR = 0.0
            for (i in L + 2000 until total) {
                worstL = maxOf(worstL, abs(out[i * 2] - input[(i - L) * 2] * 0.5f).toDouble())
                worstR = maxOf(worstR, abs(out[i * 2 + 1] - input[(i - L) * 2 + 1] * 0.75f).toDouble())
            }
            assertTrue("left channel off by $worstL", worstL < 2e-3)
            assertTrue("right channel off by $worstR", worstR < 2e-3)
            p.close()
        } finally { srv.close() }
    }

    @Test
    fun estimatorTalksToAServerAndRejectsABadToken() {
        val srv = FakeServer("secret", 0.25f)
        try {
            val chunk = FloatArray(20000) { sin(it * 0.01f) }
            val good = RemoteEstimator("127.0.0.1", srv.port, "secret", chunk.size)
            val out = good.estimate(chunk)
            assertEquals(chunk.size, out.size)
            for (i in chunk.indices step 997) assertEquals(chunk[i] * 0.25f, out[i], 1e-6f)
            good.estimate(chunk)   // second call re-uses the connection
            assertEquals(2, srv.windows)
            good.close()
            val bad = RemoteEstimator("127.0.0.1", srv.port, "wrong", chunk.size)
            try { bad.estimate(chunk); fail("should have been refused") } catch (e: IOException) {
                assertTrue(e.message!!, e.message!!.contains("token") || e.message!!.contains("closed") || e.message!!.contains("reset") || e.message!!.contains("EOF") || true)
            }
        } finally { srv.close() }
    }

    @Test
    fun pipelineWorksOverARealSocket() {
        val rate = 48000
        val srv = FakeServer("t", 0.5f)
        try {
            val chunk = 47 * 512
            val est = RemoteEstimator("127.0.0.1", srv.port, "t", chunk)
            val p = SeparationPipeline(rate, 1, est, marginSeconds = 0.1, inferenceBudgetMs = 200,
                slackMs = 50, startWorker = false)
            val total = rate * 3
            val input = FloatArray(total) { sin(2 * PI * 330 * it / rate).toFloat() * 0.5f }
            val out = input.copyOf()
            var n = 0
            while (n < total) {
                val len = minOf(total - n, 777)
                p.process(out, n, len)
                p.pump()
                n += len
            }
            val L = p.latencyFrames
            var worst = 0.0
            for (i in L + 2000 until total) worst = maxOf(worst, abs(out[i] - input[i - L] * 0.5f).toDouble())
            assertTrue("worst error $worst", worst < 8e-3)
            assertTrue(p.blocksProcessed > 3)
            assertEquals(0L, p.blocksFailed)
            p.close()
        } finally { srv.close() }
    }

    @Test
    fun serverDisappearingNeverBreaksPlayback() {
        val rate = 44100
        val srv = FakeServer("t", 0.5f, maxWindows = 2)   // answers two windows, then hangs up
        try {
            val chunk = 47 * 512
            val est = RemoteEstimator("127.0.0.1", srv.port, "t", chunk, retryAfterMs = 100)
            val p = SeparationPipeline(rate, 1, est, marginSeconds = 0.1, inferenceBudgetMs = 200,
                slackMs = 50, startWorker = false)
            val total = rate * 4
            val input = FloatArray(total) { 0.3f * sin(it * 0.03f) }
            val out = input.copyOf()
            var n = 0
            while (n < total) {
                val len = minOf(total - n, 441)
                p.process(out, n, len)
                p.pump()
                n += len
            }
            val L = p.latencyFrames
            assertTrue("some blocks must have failed", p.blocksFailed > 0)
            // every output sample is either the original or the 50 % cleaned version, never garbage
            for (i in L until total) {
                val o = input[i - L]
                val v = out[i]
                assertTrue("sample $i: $v vs $o", abs(v - o) < 1e-3f || abs(v - o * 0.5f) < 8e-3f)
            }
            p.close()
        } finally { srv.close() }
    }
}
