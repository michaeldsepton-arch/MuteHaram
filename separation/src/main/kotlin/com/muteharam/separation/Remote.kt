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
    const val ERROR_N = -1   // 0xFFFFFFFF as a signed int

    fun encodeHello(token: String): ByteArray {
        val t = token.toByteArray(Charsets.UTF_8)
        require(t.size < 256)
        return MAGIC + byteArrayOf(t.size.toByte()) + t
    }

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
) : MusicEstimator {
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

    private fun ensureConnected() {
        if (sock != null) return
        val now = System.currentTimeMillis()
        if (now < nextTryAt) throw IOException("waiting before reconnecting")
        val s = Socket()
        try {
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(host, port), connectTimeoutMs)
            s.soTimeout = readTimeoutMs
            s.getOutputStream().apply { write(RemoteProtocol.encodeHello(token)); flush() }
            val ins = DataInputStream(s.getInputStream())
            val reply = ByteArray(5)
            ins.readFully(reply)
            if (!reply.copyOfRange(0, 4).contentEquals(RemoteProtocol.MAGIC)) throw IOException("not a MuteHaram server")
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
