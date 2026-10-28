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
import androidx.media3.exoplayer.DefaultLoadControl
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
    private lateinit var modeButton: TextView
    private lateinit var gearButton: TextView
    private var controlsVisible = true
    private var current: SessionInfo? = null
    private var curHost = ""
    private var curToken = ""

    @Volatile
    private var currentId = -1L

    // a session the PC prepared a while ago is only played when asked for (tap the message)
    @Volatile
    private var pendingOld: SessionInfo? = null

    @Volatile
    private var acceptedOldId = -1L

    @Volatile
    private var running = false

    // whether the film is playing, for the background check (the player itself may only be asked on the main thread)
    @Volatile
    private var playingNow = false

    // where the film was, so that after a playback problem it carries on from there (not from the beginning)
    @Volatile
    private var resumeId = -1L

    @Volatile
    private var resumeMs = 0L
    private var autoRetries = 0

    // a film that failed to play after the automatic retries: not started again by itself (the message stays up)
    @Volatile
    private var stoppedId = -1L
    private var pollFailures = 0
    private var lastRetryAt = 0L
    private val remember = object : Runnable {
        override fun run() {
            val p = player
            val s = current
            if (p != null && s != null && p.playbackState == Player.STATE_READY) {
                resumeId = s.id
                resumeMs = p.currentPosition
                prefs.edit().putLong("resumeId", resumeId).putLong("resumeMs", resumeMs).apply()
            }
            ui.postDelayed(this, 2000)
        }
    }
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
            isClickable = false             // a plain message never catches a tap meant for the player (pause ...)
            isFocusable = false
        }
        root.addView(
            status,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER
            )
        )

        val gear = TextView(this).also { gearButton = it }.apply {
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
        modeButton = TextView(this).apply {
            setTextColor(Color.WHITE)
            setBackgroundColor(0x66000000)
            textSize = 15f
            setPadding(28, 16, 28, 16)
            visibility = View.GONE
        }
        root.addView(
            modeButton,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START
            )
        )
        setContentView(root)
        // Settings and the sound-mode button appear together with the player's controls (tap the picture) and fade with them.
        playerView.setControllerVisibilityListener(PlayerView.ControllerVisibilityListener { v ->
            controlsVisible = v == View.VISIBLE
            refreshOverlay()
        })

        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, root).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        if (prefs.getString("addr", "").isNullOrBlank()) showSettings()
        // closing the app (or the system closing it while the screen was off) must not lose the place in the film
        resumeId = prefs.getLong("resumeId", -1L)
        resumeMs = prefs.getLong("resumeMs", 0L)
        // if the app ever crashes, say why the next time it opens (so it can be reported and fixed)
        prefs.getString("lastCrash", null)?.let {
            prefs.edit().remove("lastCrash").apply()
            AlertDialog.Builder(this).setTitle("The player stopped unexpectedly last time")
                .setMessage("Please send this to whoever set up MuteHaram:\n\n" + it.take(1500))
                .setPositiveButton("OK", null).show()
        }
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                prefs.edit().putString("lastCrash", android.util.Log.getStackTraceString(e)).commit()
            } catch (_: Throwable) {
            }
            previous?.uncaughtException(t, e)
        }
    }

    private var poller: Thread? = null

    override fun onStart() {
        super.onStart()
        running = true
        ui.removeCallbacks(remember)
        ui.post(remember)
        if (poller?.isAlive != true) {          // coming back quickly: the earlier loop is still running
            poller = Thread { pollLoop() }.apply { isDaemon = true; start() }
        }
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

    /** A message. While the film plays it sits at the top, out of the way of the play/pause button.
     *  Only messages that say "tap here" react to a tap (retry, or play the old session that is waiting). */
    private fun setStatus(text: String?, tappable: Boolean = false) {
        ui.post {
            if (text == null) {
                status.visibility = View.GONE
                status.setOnClickListener(null)
                status.isClickable = false
            } else {
                status.text = text
                status.visibility = View.VISIBLE
                (status.layoutParams as FrameLayout.LayoutParams).let {
                    it.gravity = if (player != null) (Gravity.TOP or Gravity.CENTER_HORIZONTAL) else Gravity.CENTER
                    it.topMargin = if (player != null) 24 else 0
                    status.layoutParams = it
                }
                if (tappable) {
                    status.setOnClickListener {
                        pendingOld?.let { o -> acceptedOldId = o.id; pendingOld = null }
                        stoppedId = -1L
                        autoRetries = 0
                        currentId = -1L
                        setStatus("Trying again ...")
                    }
                } else {
                    status.setOnClickListener(null)
                    status.isClickable = false
                }
            }
        }
    }

    private fun httpGet(url: String, readTimeoutMs: Int = 4000): String {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 3000
        c.readTimeout = readTimeoutMs
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
                    val text = httpGet(url)
                    pollFailures = 0
                    val j = org.json.JSONObject(text)
                    if (j.optBoolean("preparing")) {
                        // "Send to the tablet" was pressed: the PC opens the film and cleans its first seconds
                        if (player == null || !playingNow) {
                            setStatus(
                                if (j.optBoolean("failed")) j.optString("status") + "\n(details in the window on the PC)"
                                else "The PC is preparing the film ... " + j.optLong("elapsed_s") + " s\n" +
                                    j.optString("status") + "\n(this usually takes 1 to 2 minutes; it starts by itself)"
                            )
                        }
                        Thread.sleep(1000)
                        continue
                    }
                    val s = SessionInfo.parse(text)
                    if (s.id == stoppedId) {
                        // this film failed to play: leave the error message up until it is tapped or a new film is sent
                    } else if (s.id == resumeId && resumeMs > 0 && s.id != currentId) {
                        // the same film again after a problem: carry on where it was, never from the beginning
                        pendingOld = null
                        currentId = s.id
                        val pos = resumeMs / 1000.0
                        ui.post { play(s.copy(startSeconds = pos), host, tok) }
                    } else if (s.id != currentId) {
                        if (s.ageSeconds > 180 && s.id != acceptedOldId) {
                            pendingOld = s
                            if (player == null) {
                                setStatus(
                                    "The PC still has a session from " + (s.ageSeconds / 60) + " minutes ago." + "\n" +
                                        "For a fresh one: on the PC use the MuteHaram icon's menu, Send to the tablet." + "\n" +
                                        "Or tap here to play the old one anyway.", tappable = true
                                )
                            }
                        } else {
                            pendingOld = null
                            currentId = s.id
                            ui.post { play(s, host, tok) }
                        }
                    } else if (player == null) {
                        setStatus("Connected to the PC")
                    }
                } catch (e: Exception) {
                    pollFailures++
                    if (player != null && pollFailures >= 3 &&
                        (e is java.net.ConnectException || e is java.net.SocketTimeoutException)) {
                        // the PC stopped sending (cancelled there, or switched off): stop here too, and say so
                        ui.post {
                            player?.release()
                            player = null
                            playerView.player = null
                            current = null
                            refreshOverlay()
                        }
                        stoppedId = -1L
                        currentId = -1L
                        setStatus("The PC stopped sending the film.\nSend it again from the PC when you want to carry on (it continues where it was).")
                    } else if (stoppedId == -1L && (player == null || !playingNow)) {
                        val why = when (e) {
                            is java.net.ConnectException, is java.net.SocketTimeoutException ->
                                "Nothing has been sent from the PC yet (or the PC is not on this Wi-Fi)."
                            else -> e.message ?: e.javaClass.simpleName
                        }
                        setStatus(
                            "Waiting for the PC (" + host + ")\n" +
                                "On the PC: open the video, then use the MuteHaram icon's menu: Send to the tablet.\n" + why
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

    private fun showMode(mode: String) {
        modeButton.text = when (mode) {
            "precleaned" -> "Sound: cleaned in advance on the PC (library)"
            else -> "Sound: music removed by the PC"
        }
        refreshOverlay()
    }

    /** The two buttons are visible with the controls, or whenever nothing is playing yet. */
    private fun refreshOverlay() {
        val show = controlsVisible || player == null
        gearButton.visibility = if (show) View.VISIBLE else View.GONE
        modeButton.visibility = if (show && current != null) View.VISIBLE else View.GONE
    }

    private fun play(s: SessionInfo, host: String, token: String, withSubs: Boolean = true) {
        current = s
        curHost = host
        curToken = token
        showMode(s.mode)
        setStatus(("Starting " + s.title).trim())
        player?.release()
        playingNow = false

        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(true)
            .setConnectTimeoutMs(15000)
            .setReadTimeoutMs(30000)
        if (s.headers.isNotEmpty()) http.setDefaultRequestProperties(s.headers)
        s.headers.entries.firstOrNull { it.key.equals("user-agent", ignoreCase = true) }
            ?.let { http.setUserAgent(it.value) }
        val dataFactory = DefaultDataSource.Factory(this, http)

        // Captions go through the player library's own loader (the only way it can read them). If anything
        // fails while captions are on, onPlayerError below plays again WITHOUT them.
        val subs = if (withSubs) s.subs.mapIndexed { i, pair ->
            MediaItem.SubtitleConfiguration.Builder(Uri.parse(pair.first))
                .setMimeType(SessionInfo.subtitleMime(pair.first))
                .setLanguage(if (pair.second.isEmpty()) null else pair.second)
                .setSelectionFlags(if (i == 0) C.SELECTION_FLAG_DEFAULT else 0)
                .build()
        } else emptyList()
        val itemBuilder = MediaItem.Builder().setUri(s.video).setSubtitleConfigurations(subs)
        if (s.kind == "hls") itemBuilder.setMimeType(MimeTypes.APPLICATION_M3U8)
        if (s.kind == "dash") itemBuilder.setMimeType(MimeTypes.APPLICATION_MPD)
        val video = DefaultMediaSourceFactory(dataFactory).createMediaSource(itemBuilder.build())

        val audioUrl = "http://" + host + ":47804/audio.wav?t=" + URLEncoder.encode(token, "UTF-8")
        // the PC may need ~15 s to open the film after a switch or a skip: wait for it instead of giving up
        val audio = ProgressiveMediaSource.Factory(
            DefaultHttpDataSource.Factory().setConnectTimeoutMs(15000).setReadTimeoutMs(90000)
        )
            .createMediaSource(MediaItem.fromUri(audioUrl))

        // keep ~5 minutes of film ready: website servers and home internet slow down now and then, and a
        // small reserve turns every dip into a freeze. After a stall, wait for 10 s of film so it does not stutter.
        val load = DefaultLoadControl.Builder()
            .setBufferDurationsMs(120_000, 300_000, 2_500, 10_000)
            .setTargetBufferBytes(220 * 1024 * 1024)
            .setPrioritizeTimeOverSizeThresholds(true)
            .build()
        val p = ExoPlayer.Builder(this)
            .setLoadControl(load)
            .setSeekBackIncrementMs(10_000)
            .setSeekForwardIncrementMs(30_000)
            .build()
        p.addListener(object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                if (withSubs && s.subs.isNotEmpty()) {
                    android.util.Log.w("MuteHaramPlayer", "playback failed with captions; retrying without them", error)
                    ui.post { play(s, host, token, false) }
                    return
                }
                // carry on from where it was, never from the beginning: a few automatic retries, then ask
                val pos = maxOf(p.currentPosition, if (resumeId == s.id) resumeMs else 0L)
                resumeId = s.id
                resumeMs = pos
                val now = System.currentTimeMillis()
                if (now - lastRetryAt > 120_000) autoRetries = 0
                if (autoRetries < 3) {
                    autoRetries++
                    lastRetryAt = now
                    android.util.Log.w("MuteHaramPlayer", "playback problem; retry " + autoRetries + " from " + pos / 1000 + " s", error)
                    setStatus("Playback hiccup, carrying on from " + (pos / 60000) + ":" + String.format("%02d", (pos / 1000) % 60) + " ...")
                    ui.postDelayed({ if (player === p) play(s.copy(startSeconds = pos / 1000.0), host, token, withSubs) }, 2000)
                    return
                }
                // give up on this film until the message is tapped (no endless restarting, the message stays up)
                stoppedId = s.id
                currentId = s.id
                ui.post { if (player === p) p.stop() }
                var why = ""
                var c: Throwable? = error.cause
                var depth = 0
                while (c != null && depth < 4) {
                    why += "\n  caused by " + c.javaClass.simpleName + ": " + (c.message ?: "")
                    c = c.cause
                    depth++
                }
                android.util.Log.e("MuteHaramPlayer", "playback error", error)
                setStatus("Playback problem: " + error.errorCodeName + "\n" + (error.message ?: "") + why + "\n(tap here to try again)", tappable = true)
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (player === p) playingNow = isPlaying
            }

            override fun onTracksChanged(tracks: Tracks) {
                preferPcAudio(p, tracks)
            }

            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) {
                    // say which sound is playing for a few seconds, so it is obvious if the PC's audio is not used
                    val audioGroups = p.currentTracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
                    val cleaned = audioGroups.any { g ->
                        g.isSelected && (0 until g.length).any { g.getTrackFormat(it).sampleMimeType == MimeTypes.AUDIO_RAW }
                    }
                    setStatus(if (cleaned) "Sound: cleaned by the PC" else "Sound: ORIGINAL (the PC's cleaned sound is not playing)")
                    ui.postDelayed({ if (player === p) setStatus(null) }, 4000)
                }
                else if (state == Player.STATE_BUFFERING) setStatus("Loading ...")
            }
        })
        // pick the PC's cleaned sound from the very first moment (otherwise the film's own sound, WITH the music,
        // can play for a few seconds until the choice below switches over)
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setPreferredAudioMimeType(MimeTypes.AUDIO_RAW)
            .build()
        p.setMediaSource(MergingMediaSource(video, audio), (s.startSeconds * 1000).toLong())
        p.prepare()
        p.playWhenReady = true
        playerView.player = p
        player = p
        controlsVisible = false
        refreshOverlay()
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
