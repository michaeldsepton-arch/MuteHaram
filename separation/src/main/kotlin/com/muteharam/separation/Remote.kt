package com.muteharam.separation

import java.io.DataInputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Wire format shared with pc/muteharam_pc/stream_server.py (all little-endian):
 *   hello    "MHR1" | u8 tokenLength | token
 *   reply    "MHR1" | u8 status (0 ok)
 *   request  u32 seq | u32 n | n * float32   (mono, 44.1 kHz)
 *   response u32 seq | u32 n | n * float32   (n = 0xFFFFFFFF: failed)
 */
object RemoteProtocol {
    val MAGIC = byteArrayOf('M'.code.toByte(), 'H'.code.toByte(), 'R'.code.toByte(), '1'.code.toByte())
    val MAGIC2 = byteArrayOf('M'.code.toByte(), 'H'.code.toByte(), 'R'.code.toByte(), '2'.code.toByte())
    const val ERROR_N = -1   // 0xFFFFFFFF as a signed int

    fun encodeHello(token: String, magic: ByteArray = MAGIC): ByteArray {
        val t = token.toByteArray(Charsets.UTF_8)
        require(t.size < 256)
        return magic + byteArrayOf(t.size.toByte()) + t
    }

    /** Version 2 request: [channels] are equally long float signals in -1..1, sent as planar int16. */
    fun encodeRequestV2(seq: Int, channels: List<FloatArray>): ByteArray {
        val n = channels[0].size
        val b = ByteBuffer.allocate(12 + channels.size * n * 2).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt(seq).putInt(n).putInt(channels.size)
        for (c in channels) {
            require(c.size == n)
            for (v in c) {
                val q = Math.round(v * 32768f)
                b.putShort((if (q > 32767) 32767 else if (q < -32768) -32768 else q).toShort())
            }
        }
        return b.array()
    }

    /** int16 sample of a version 2 reply back to float (the server scales by 32767). */
    fun fromInt16(v: Short): Float = v / 32767f

    fun encodeRequest(seq: Int, samples: FloatArray): ByteArray {
        val b = ByteBuffer.allocate(8 + samples.size * 4).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt(seq).putInt(samples.size)
        b.asFloatBuffer().put(samples)
        return b.array()
    }
}

/**
 * Runs the AI on another computer (see pc/start_wifi_server.bat). Every failure
 * is reported as an exception; [SeparationPipeline] then plays that block
 * un-cleaned and simply tries again for the next block (after a short pause).
 */
class RemoteEstimator(
    private val host: String,
    private val port: Int,
    private val token: String,
    override val chunkSamples: Int,
    private val connectTimeoutMs: Int = 2000,
    private val readTimeoutMs: Int = 8000,
    private val retryAfterMs: Long = 1500,
    /** Use protocol version 2 (left and right cleaned separately, 16-bit): needs a recent PC server. */
    private val stereo: Boolean = false,
) : MusicEstimator {
    override val stereoCapable: Boolean get() = stereo

    private var sock: Socket? = null
    private var input: DataInputStream? = null
    private var seq = 0
    private var nextTryAt = 0L

    @Volatile var connected = false; private set

    @Synchronized
    override fun estimate(chunk: FloatArray): FloatArray {
        require(chunk.size == chunkSamples)
        try {
            ensureConnected()
            val s = sock!!
            val id = seq++
            s.getOutputStream().apply { write(RemoteProtocol.encodeRequest(id, chunk)); flush() }
            val head = ByteArray(8)
            input!!.readFully(head)
            val hb = ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN)
            val rseq = hb.int
            val n = hb.int
            if (n == RemoteProtocol.ERROR_N) throw IOException("server could not process the window")
            if (rseq != id || n != chunk.size) throw IOException("unexpected reply (seq $rseq, n $n)")
            val body = ByteArray(n * 4)
            input!!.readFully(body)
            val out = FloatArray(n)
            ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(out)
            return out
        } catch (e: IOException) {
            disconnect()
            nextTryAt = System.currentTimeMillis() + retryAfterMs
            throw e
        }
    }

    @Synchronized
    override fun estimateStereo(left: FloatArray, right: FloatArray): Array<FloatArray> {
        require(stereo) { "estimator was created without stereo" }
        require(left.size == chunkSamples && right.size == chunkSamples)
        try {
            ensureConnected()
            val s = sock!!
            val id = seq++
            s.getOutputStream().apply { write(RemoteProtocol.encodeRequestV2(id, listOf(left, right))); flush() }
            val head = ByteArray(12)
            input!!.readFully(head)
            val hb = ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN)
            val rseq = hb.int
            val n = hb.int
            val ch = hb.int
            if (n == RemoteProtocol.ERROR_N) throw IOException("server could not process the window")
            if (rseq != id || n != chunkSamples || ch != 2) throw IOException("unexpected reply (seq $rseq, n $n, ch $ch)")
            val body = ByteArray(n * ch * 2)
            input!!.readFully(body)
            val sb = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
            val out = Array(2) { FloatArray(n) }
            for (c in 0 until 2) for (i in 0 until n) out[c][i] = RemoteProtocol.fromInt16(sb.get())
            return out
        } catch (e: IOException) {
            disconnect()
            nextTryAt = System.currentTimeMillis() + retryAfterMs
            throw e
        }
    }

    private fun ensureConnected() {
        if (sock != null) return
        val now = System.currentTimeMillis()
        if (now < nextTryAt) throw IOException("waiting before reconnecting")
        val s = Socket()
        try {
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(host, port), connectTimeoutMs)
            s.soTimeout = readTimeoutMs
            val magic = if (stereo) RemoteProtocol.MAGIC2 else RemoteProtocol.MAGIC
            s.getOutputStream().apply { write(RemoteProtocol.encodeHello(token, magic)); flush() }
            val ins = DataInputStream(s.getInputStream())
            val reply = ByteArray(5)
            ins.readFully(reply)
            if (!reply.copyOfRange(0, 4).contentEquals(magic)) throw IOException("not a MuteHaram server (or too old for stereo: update the PC)")
            if (reply[4].toInt() != 0) throw IOException("server refused the token")
            sock = s
            input = ins
            connected = true
        } catch (e: IOException) {
            try { s.close() } catch (_: IOException) {}
            throw e
        }
    }

    private fun disconnect() {
        connected = false
        try { sock?.close() } catch (_: IOException) {}
        sock = null
        input = null
    }

    @Synchronized
    override fun close() = disconnect()
}
