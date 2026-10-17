package com.muteharam.player

import android.app.AlertDialog
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.PlayerView
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * MuteHaram Player: plays what the PC offers (on the PC: "Send to the tablet"). The picture comes
 * straight from the website; the sound is the music-free audio the PC serves over Wi-Fi. Pause,
 * seek and captions work here on the tablet.
 */
@UnstableApi
class MainActivity : AppCompatActivity() {
    private lateinit var playerView: PlayerView
    private lateinit var status: TextView
    private var player: ExoPlayer? = null

    @Volatile
    private var currentId = -1L

    @Volatile
    private var running = false
    private val ui = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("mh", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)
        playerView = PlayerView(this).apply {
            setShowSubtitleButton(true)
            controllerShowTimeoutMs = 3500
        }
        root.addView(
            playerView,
            ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        )

        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            setBackgroundColor(0xAA000000.toInt())
            textSize = 16f
            setPadding(32, 20, 32, 20)
            gravity = Gravity.CENTER
            setOnClickListener { currentId = -1L }       // tap to try again
        }
        root.addView(
            status,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER
            )
        )

        val gear = TextView(this).apply {
            text = "⚙ Settings"
            setTextColor(Color.WHITE)
            setBackgroundColor(0x66000000)
            textSize = 15f
            setPadding(28, 16, 28, 16)
            setOnClickListener { showSettings() }
        }
        root.addView(
            gear,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END
            )
        )
        setContentView(root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, root).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        if (prefs.getString("addr", "").isNullOrBlank()) showSettings()
    }

    override fun onStart() {
        super.onStart()
        running = true
        Thread { pollLoop() }.apply { isDaemon = true; start() }
    }

    override fun onStop() {
        super.onStop()
        running = false
        player?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        running = false
        player?.release()
        player = null
    }

    // ------------------------------------------------------------------ settings

    private fun showSettings() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val addr = EditText(this).apply {
            hint = "PC address, e.g. 192.168.137.1"
            setText(prefs.getString("addr", ""))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        val tok = EditText(this).apply {
            hint = "Token (printed by the PC helper window)"
            setText(prefs.getString("tok", ""))
        }
        box.addView(addr)
        box.addView(tok)
        AlertDialog.Builder(this)
            .setTitle("MuteHaram Player")
            .setMessage("Type what the PC's helper window prints (address and token).")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                prefs.edit()
                    .putString("addr", addr.text.toString().trim())
                    .putString("tok", tok.text.toString().trim())
                    .apply()
                currentId = -1L
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ------------------------------------------------------------------ talking to the PC

    private fun setStatus(text: String?) {
        ui.post {
            if (text == null) {
                status.visibility = View.GONE
            } else {
                status.text = text
                status.visibility = View.VISIBLE
            }
        }
    }

    private fun httpGet(url: String): String {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 3000
        c.readTimeout = 4000
        try {
            when (c.responseCode) {
                200 -> return c.inputStream.bufferedReader().readText()
                401 -> throw IOException("the token is wrong (tap Settings)")
                else -> throw IOException("the PC answered " + c.responseCode)
            }
        } finally {
            c.disconnect()
        }
    }

    private fun pollLoop() {
        while (running) {
            val host = (prefs.getString("addr", "") ?: "").trim().substringBefore(':')
            val tok = (prefs.getString("tok", "") ?: "").trim()
            if (host.isEmpty()) {
                if (player == null) setStatus("Tap Settings and type the PC's address and token")
            } else {
                try {
                    val url = "http://" + host + ":47804/session.json?t=" + URLEncoder.encode(tok, "UTF-8")
                    val s = SessionInfo.parse(httpGet(url))
                    if (s.id != currentId) {
                        currentId = s.id
                        ui.post { play(s, host, tok) }
                    } else if (player == null) {
                        setStatus("Connected to the PC")
                    }
                } catch (e: Exception) {
                    if (player == null || player?.isPlaying != true) {
                        setStatus(
                            "Waiting for the PC (" + host + ")\n" +
                                "On the PC: open the video, then use the MuteHaram icon's menu: Send to the tablet.\n" +
                                (e.message ?: "")
                        )
                    }
                }
            }
            try {
                Thread.sleep(2000)
            } catch (_: InterruptedException) {
            }
        }
    }

    // ------------------------------------------------------------------ playing

    private fun play(s: SessionInfo, host: String, token: String) {
        setStatus(("Starting " + s.title).trim())
        player?.release()

        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(15000)
            .setReadTimeoutMs(30000)
        if (s.headers.isNotEmpty()) http.setDefaultRequestProperties(s.headers)
        s.headers.entries.firstOrNull { it.key.equals("user-agent", ignoreCase = true) }
            ?.let { http.setUserAgent(it.value) }
        val dataFactory = DefaultDataSource.Factory(this, http)

        val subs = s.subs.mapIndexed { i, pair ->
            MediaItem.SubtitleConfiguration.Builder(Uri.parse(pair.first))
                .setMimeType(SessionInfo.subtitleMime(pair.first))
                .setLanguage(if (pair.second.isEmpty()) null else pair.second)
                .setSelectionFlags(if (i == 0) C.SELECTION_FLAG_DEFAULT else 0)
                .build()
        }
        val itemBuilder = MediaItem.Builder().setUri(s.video).setSubtitleConfigurations(subs)
        if (s.kind == "hls") itemBuilder.setMimeType(MimeTypes.APPLICATION_M3U8)
        if (s.kind == "dash") itemBuilder.setMimeType(MimeTypes.APPLICATION_MPD)
        val video = DefaultMediaSourceFactory(dataFactory).createMediaSource(itemBuilder.build())

        val audioUrl = "http://" + host + ":47804/audio.wav?t=" + URLEncoder.encode(token, "UTF-8")
        val audio = ProgressiveMediaSource.Factory(DefaultHttpDataSource.Factory())
            .createMediaSource(MediaItem.fromUri(audioUrl))

        val p = ExoPlayer.Builder(this)
            .setSeekBackIncrementMs(10_000)
            .setSeekForwardIncrementMs(30_000)
            .build()
        p.addListener(object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                currentId = -1L        // the next poll offers the same session again; tapping the message retries
                setStatus("Playback problem: " + error.errorCodeName + "\n" + (error.message ?: "") + "\n(tap here to try again)")
            }

            override fun onTracksChanged(tracks: Tracks) {
                preferPcAudio(p, tracks)
            }

            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) setStatus(null)
                else if (state == Player.STATE_BUFFERING) setStatus("Loading ...")
            }
        })
        p.setMediaSource(MergingMediaSource(video, audio), (s.startSeconds * 1000).toLong())
        p.prepare()
        p.playWhenReady = true
        playerView.player = p
        player = p
    }

    /** The picture's own sound (with the music) is switched off: the PC's cleaned audio plays instead. */
    private fun preferPcAudio(p: ExoPlayer, tracks: Tracks) {
        val groups = tracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
        if (groups.size < 2) return
        val pc = groups.firstOrNull { g ->
            (0 until g.length).any { g.getTrackFormat(it).sampleMimeType == MimeTypes.AUDIO_RAW }
        } ?: return
        if (pc.isSelected) return
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setOverrideForType(TrackSelectionOverride(pc.mediaTrackGroup, 0))
            .build()
    }
}
