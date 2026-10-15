package com.muteharam.engine

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.muteharam.separation.ChunkModelEstimator
import com.muteharam.separation.MusicEstimator
import com.muteharam.separation.RemoteProtocol
import java.io.DataInputStream
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread

/** What the UI shows. */
object EngineState {
    @Volatile var status = "stopped"
    @Volatile var frames = 0
    @Volatile var windows = 0L
    @Volatile var failures = 0L
    @Volatile var lastMs = 0.0
    @Volatile var avgMs = 0.0
    @Volatile var clients = 0
}

/**
 * Foreground service: loads the model once, then answers the MuteHaram hook on
 * 127.0.0.1:47801 (same protocol as the PC's Wi-Fi server, see Remote.kt) so the
 * hook running inside YouTube / CineHD can use this device's GPU.
 */
class EngineService : Service() {
    private var server: ServerSocket? = null
    private var estimator: MusicEstimator? = null
    private var wake: PowerManager.WakeLock? = null
    @Volatile private var running = false
    private val gpuLock = Any()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(1, notification("Starting ..."))
        if (!running) {
            running = true
            thread(name = "engine-main") { runServer() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        try { server?.close() } catch (_: IOException) {}
        estimator?.close()
        wake?.let { if (it.isHeld) it.release() }
        EngineState.status = "stopped"
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("engine", "MuteHaram Engine", NotificationManager.IMPORTANCE_LOW))
        return Notification.Builder(this, "engine")
            .setContentTitle("MuteHaram Engine")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()
    }

    private fun setStatus(s: String) {
        EngineState.status = s
        getSystemService(NotificationManager::class.java).notify(1, notification(s))
    }

    private fun runServer() {
        try {
            val pm = getSystemService(PowerManager::class.java)
            wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "muteharam:engine").apply { acquire() }
            val frames = getSharedPreferences("engine", Context.MODE_PRIVATE).getInt("frames", 176)
            EngineState.frames = frames
            setStatus("loading AI model (first time takes a while) ...")
            val model = LiteRtChunkModel.load(this, frames)
            estimator = ChunkModelEstimator(model)
            val ss = ServerSocket(PORT, 8, InetAddress.getByName("127.0.0.1"))
            server = ss
            setStatus("ready: ${"%.2f".format(model.chunkSamples / 44100.0)} s windows on port $PORT")
            while (running) {
                val s = ss.accept()
                thread(name = "engine-client") { serve(s, estimator!!) }
            }
        } catch (t: Throwable) {
            if (running) {
                Log.e(TAG, "engine failed", t)
                setStatus("failed: $t")
            }
        }
    }

    private fun serve(s: Socket, est: MusicEstimator) {
        EngineState.clients++
        try {
            s.tcpNoDelay = true
            s.soTimeout = 60_000
            val ins = DataInputStream(s.getInputStream())
            val out = s.getOutputStream()
            val magic = ByteArray(4); ins.readFully(magic)
            val tokenLen = ins.readUnsignedByte()
            ins.readFully(ByteArray(tokenLen))              // loopback only: no token needed
            out.write(RemoteProtocol.MAGIC + byteArrayOf(0)); out.flush()
            val chunk = est.chunkSamples
            while (running) {
                val head = ByteArray(8); ins.readFully(head)
                val hb = ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN)
                val seq = hb.int
                val n = hb.int
                if (n <= 0 || n > 44100 * 30) {
                    reply(out, seq, null); continue
                }
                val body = ByteArray(n * 4); ins.readFully(body)
                if (n != chunk) {          // this engine is built for exactly one window length
                    Log.w(TAG, "window of $n samples, engine needs $chunk -> set muteharam_chunk_ms to ${"%.0f".format(chunk * 1000 / 44100.0)}")
                    EngineState.failures++
                    reply(out, seq, null); continue
                }
                val x = FloatArray(n)
                ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer().get(x)
                val t0 = System.nanoTime()
                val y = try { synchronized(gpuLock) { est.estimate(x) } } catch (t: Throwable) {
                    Log.e(TAG, "inference failed", t); EngineState.failures++; null
                }
                val ms = (System.nanoTime() - t0) / 1e6
                if (y != null) {
                    EngineState.windows++
                    EngineState.lastMs = ms
                    EngineState.avgMs = if (EngineState.windows == 1L) ms else EngineState.avgMs * 0.9 + ms * 0.1
                }
                reply(out, seq, y)
            }
        } catch (_: IOException) {
        } finally {
            EngineState.clients--
            try { s.close() } catch (_: IOException) {}
        }
    }

    private fun reply(out: java.io.OutputStream, seq: Int, y: FloatArray?) {
        val b = ByteBuffer.allocate(8 + (y?.size ?: 0) * 4).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt(seq).putInt(y?.size ?: RemoteProtocol.ERROR_N)
        if (y != null) b.asFloatBuffer().put(y)
        out.write(b.array()); out.flush()
    }

    companion object {
        const val TAG = "MuteEngine"
        const val PORT = 47801
        const val ACTION_STOP = "com.muteharam.engine.STOP"
    }
}
