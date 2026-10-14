package com.muteharam.capturetest

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlin.math.log10
import kotlin.math.sqrt
import kotlin.concurrent.thread

/**
 * Minimal test harness: captures whatever audio the device is currently
 * playing (subject to Android's AudioPlaybackCapture rules) and reports
 * a simple RMS/dB level so you can see, per app, whether capture is
 * getting real signal or silence.
 *
 * This does NOT do any source separation yet -- it's purely the
 * "can I even get the bytes" probe discussed in chat.
 */
class AudioCaptureService : Service() {

    companion object {
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        private const val CHANNEL_ID = "capture_channel"
        private const val NOTIF_ID = 1

        // Simple in-process callback so MainActivity can show live levels
        // without wiring up a full broadcast/LiveData setup for a test app.
        var levelListener: ((rmsDb: Double, hasSignal: Boolean) -> Unit)? = null
    }

    private var mediaProjection: MediaProjection? = null
    private var audioRecord: AudioRecord? = null
    @Volatile private var capturing = false
    private var captureThread: Thread? = null

    // Track the best signal seen all session so a single glance at the
    // notification (e.g. while another app is fullscreen) tells you
    // whether capture ever worked, not just this instant.
    @Volatile private var peakDb: Double = -120.0
    @Volatile private var everSawSignal: Boolean = false
    private var lastNotifyUpdateMs: Long = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, -1) ?: -1
        val resultData: Intent? = intent?.getParcelableExtra(EXTRA_RESULT_DATA)

        startForeground(NOTIF_ID, buildNotification())

        if (resultData == null || resultCode != android.app.Activity.RESULT_OK) {
            stopSelf()
            return START_NOT_STICKY
        }

        val projectionManager =
            getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = projectionManager.getMediaProjection(resultCode, resultData)

        startCapture()
        return START_NOT_STICKY
    }

    @SuppressLint("MissingPermission") // RECORD_AUDIO checked before service is started
    private fun startCapture() {
        val projection = mediaProjection ?: return

        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .build()

        val sampleRate = 44100
        val channelConfig = AudioFormat.CHANNEL_IN_STEREO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT
        val minBufSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        val bufSize = if (minBufSize > 0) minBufSize * 2 else sampleRate * 2

        val format = AudioFormat.Builder()
            .setEncoding(audioFormat)
            .setSampleRate(sampleRate)
            .setChannelMask(channelConfig)
            .build()

        audioRecord = AudioRecord.Builder()
            .setAudioFormat(format)
            .setBufferSizeInBytes(bufSize)
            .setAudioPlaybackCaptureConfig(config)
            .build()

        audioRecord?.startRecording()
        capturing = true

        captureThread = thread(start = true) {
            val buffer = ShortArray(bufSize / 2)
            while (capturing) {
                val read = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (read > 0) {
                    val rmsDb = computeRmsDb(buffer, read)
                    val hasSignal = rmsDb > -60.0 // rough "not silence" threshold
                    if (hasSignal) everSawSignal = true
                    if (rmsDb > peakDb) peakDb = rmsDb
                    levelListener?.invoke(rmsDb, hasSignal)
                    maybeUpdateNotification(rmsDb, hasSignal)
                }
            }
        }
    }

    private fun computeRmsDb(buffer: ShortArray, len: Int): Double {
        var sumSquares = 0.0
        for (i in 0 until len) {
            val sample = buffer[i].toDouble()
            sumSquares += sample * sample
        }
        val rms = sqrt(sumSquares / len)
        if (rms <= 0.0) return -120.0
        // normalize against max 16-bit amplitude
        return 20 * log10(rms / Short.MAX_VALUE)
    }

    private fun buildNotification(
        currentDb: Double? = null,
        hasSignal: Boolean = false
    ): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Audio capture test",
                NotificationManager.IMPORTANCE_LOW
            )
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }

        val contentText = if (currentDb == null) {
            "Waiting for audio... switch to the app you're testing"
        } else {
            val now = if (hasSignal) "SIGNAL (%.0f dB)".format(currentDb) else "silence"
            val everText = if (everSawSignal) "yes, peak %.0f dB".format(peakDb) else "not yet"
            "Now: $now   |   Ever detected signal this session: $everText"
        }

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("MuteHaram capture test running")
            .setContentText(contentText)
            .setStyle(NotificationCompat.BigTextStyle().bigText(contentText))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()
    }

    private fun maybeUpdateNotification(rmsDb: Double, hasSignal: Boolean) {
        val now = System.currentTimeMillis()
        // Throttle to avoid hammering the notification manager — a few
        // updates per second is plenty for a human glancing at the shade.
        if (now - lastNotifyUpdateMs < 700) return
        lastNotifyUpdateMs = now
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification(rmsDb, hasSignal))
    }

    override fun onDestroy() {
        capturing = false
        captureThread?.join(500)
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        mediaProjection?.stop()
        mediaProjection = null
        super.onDestroy()
    }
}
