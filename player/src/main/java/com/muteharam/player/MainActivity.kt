package com.muteharam.player

import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.content.res.ColorStateList
import android.widget.ImageView
import android.widget.ProgressBar
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
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.source.LoadEventInfo
import androidx.media3.exoplayer.source.MediaLoadData
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
// the logo's colours
private const val NAVY_DEEP = 0xFF040E19.toInt()
private const val NAVY = 0xFF0A1F33.toInt()
private const val TEAL_DEEP = 0xFF15524F.toInt()
private const val TEAL = 0xFF4FA89A.toInt()
private const val TEAL_DIM = 0x664FA89A
private const val CREAM = 0xFFF2ECE2.toInt()
private const val MUTED = 0xFFA9B8C2.toInt()
private const val WARN = 0xFFE0A458.toInt()

@UnstableApi
class MainActivity : AppCompatActivity() {
    private lateinit var playerView: PlayerView
    private lateinit var status: TextView
    private lateinit var home: FrameLayout
    private lateinit var homeTitle: TextView
    private lateinit var homeDetail: TextView
    private lateinit var homeDot: View
    private lateinit var homeBar: ProgressBar
    private lateinit var homeCard: LinearLayout
    private lateinit var homeFooter: TextView

    // Lowering the gaze: the parts of the film to cover (from the PC's scene check) and the on/off button
    private lateinit var gazeCover: LinearLayout
    private lateinit var gazeTitle: TextView
    private lateinit var gazeLine: TextView
    private lateinit var gazeButton: TextView
    @Volatile
    private var covers: DoubleArray = DoubleArray(0)       // start, end, start, end ... (seconds)
    @Volatile
    private var scannedTo = 0.0
    @Volatile
    private var scenesDone = true
    @Volatile
    private var hasScenes = false
    private var covering = false
    private var heldForCheck = false
    private var coverEnd = 0.0                              // where the covered part we are in ends (seconds)
    private var coverStartS = 0.0
    private lateinit var gazeSkip: TextView
    private lateinit var gazeSaid: TextView                 // subtitles while covered (people may still be talking)
    private lateinit var gazeSaidBox: LinearLayout
    private val said = ArrayList<String>()                  // the website's subtitles while covered
    @Volatile
    private var madeStart = DoubleArray(0)                  // MuteHaram's own subtitles of the covered parts
    @Volatile
    private var madeText: List<String> = emptyList()
    private var shownMade = -1
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

    // loading problems, sent to the PC with each check so its log shows what the tablet waited for
    @Volatile
    private var diag = ""
    private val loads = HashMap<String, LongArray>()   // "picture" / "PC sound" -> [loads, bytes, ms, errors]
    private var lastLoadError = ""
    private var stalls = 0
    private var stallInfo = ""

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

    // ------------------------------------------------------------------ look (the logo's colours)

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun pill(fill: Int, stroke: Int, radiusDp: Int = 22) = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(radiusDp).toFloat()
        setStroke(dp(1), stroke)
    }

    /** The screen while nothing plays: logo, name, and a card saying what is happening (the loading screen). */
    private fun buildHome(): FrameLayout {
        val bg = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(NAVY_DEEP, NAVY, TEAL_DEEP))
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.mh_logo)
            alpha = 0f
            scaleX = 0.85f
            scaleY = 0.85f
            animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(700).start()
        }
        col.addView(logo, LinearLayout.LayoutParams(dp(132), dp(132)))
        col.addView(TextView(this).apply {
            text = "MuteHaram"
            setTextColor(CREAM)
            textSize = 30f
            typeface = Typeface.create(Typeface.SERIF, Typeface.BOLD)
            letterSpacing = 0.04f
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(14) })
        col.addView(TextView(this).apply {
            text = "Films and shows, without the music"
            setTextColor(TEAL)
            textSize = 15f
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(4) })

        homeCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = pill(0xB3081A2B.toInt(), TEAL_DIM, 18)
            setPadding(dp(22), dp(18), dp(22), dp(18))
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        homeDot = View(this).apply {
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(TEAL) }
        }
        row.addView(homeDot, LinearLayout.LayoutParams(dp(10), dp(10)).apply { rightMargin = dp(12) })
        homeTitle = TextView(this).apply {
            setTextColor(CREAM)
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            text = "Connecting to your PC …"
        }
        row.addView(homeTitle)
        homeCard.addView(row)
        homeDetail = TextView(this).apply {
            setTextColor(MUTED)
            textSize = 14f
            setLineSpacing(0f, 1.15f)
            visibility = View.GONE
        }
        homeCard.addView(homeDetail, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        homeBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true
            indeterminateTintList = ColorStateList.valueOf(TEAL)
        }
        homeCard.addView(homeBar, LinearLayout.LayoutParams(-1, dp(6)).apply { topMargin = dp(14) })
        col.addView(homeCard, LinearLayout.LayoutParams(dp(520), -2).apply { topMargin = dp(30) })
        // the dot breathes while the screen is up
        homeDot.animate().alpha(0.35f).setDuration(900).withEndAction(object : Runnable {
            override fun run() {
                homeDot.animate().alpha(if (homeDot.alpha < 0.5f) 1f else 0.35f).setDuration(900).withEndAction(this).start()
            }
        }).start()

        homeFooter = TextView(this).apply {
            setTextColor(MUTED)
            textSize = 13f
            gravity = Gravity.CENTER
        }
        return FrameLayout(this).apply {
            background = bg
            addView(col, FrameLayout.LayoutParams(-2, -2, Gravity.CENTER))
            addView(homeFooter, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM).apply { bottomMargin = dp(18) })
        }
    }

    private fun gazeOn() = prefs.getBoolean("gaze", true)

    private fun showGazeButton() {
        val on = gazeOn()
        gazeButton.text = if (on) "◉  Lowering the gaze: ON" else "○  Lowering the gaze: OFF"
        gazeButton.background = pill(if (on) 0xCC15524F.toInt() else 0xCC081A2B.toInt(), if (on) TEAL else TEAL_DIM)
    }

    private val gazeTimer = object : Runnable {
        override fun run() {
            checkGaze()
            ui.postDelayed(this, 250)
        }
    }

    /** Cover the picture and mute in the parts the PC's scene check found; while a live check is still behind
     *  playback, wait for it ("Checking the next scenes"). G-button OFF: nothing is covered. */
    private fun checkGaze() {
        val p = player
        if (p == null || !hasScenes || !gazeOn()) {
            if (covering || heldForCheck) {
                covering = false
                gazeCover.visibility = View.GONE
                gazeSaidBox.visibility = View.GONE
                gazeSkip.visibility = View.GONE
                p?.volume = 1f
                if (heldForCheck) {
                    heldForCheck = false
                    p?.play()
                }
            }
            return
        }
        val t = p.currentPosition / 1000.0
        // playback stays well behind the check: a scene is found a few seconds after it starts and covered from
        // before it (looking back + 5 s), so with less room the start of a scene could already have been seen
        if (!scenesDone && t > scannedTo - 15) {
            if (!heldForCheck && p.isPlaying) {
                heldForCheck = true
                gazeSkip.visibility = View.GONE
                gazeSaidBox.visibility = View.GONE
                p.pause()
                gazeTitle.text = "Checking the next scenes …"
                gazeLine.text = "It carries on by itself in a moment"
                gazeCover.visibility = View.VISIBLE
            }
            return
        }
        if (heldForCheck && (scenesDone || scannedTo > t + 40)) {
            heldForCheck = false
            gazeCover.visibility = View.GONE
            p.play()
        }
        val c = covers
        var inside = false
        var i = 0
        while (i + 1 < c.size) {
            if (t >= c[i] && t <= c[i + 1]) { inside = true; break }
            i += 2
        }
        if (inside) {
            coverEnd = c[i + 1]
            coverStartS = c[i]
        }
        if (inside && !covering) {
            covering = true
            said.clear()
            showSaid()
            gazeTitle.text = "Lowering the gaze"
            gazeLine.text = "A scene is covered · the sound comes back by itself"
            gazeCover.visibility = View.VISIBLE
            gazeSaidBox.visibility = View.VISIBLE
            gazeSkip.visibility = View.VISIBLE
            p.volume = 0f
        } else if (!inside && covering) {
            covering = false
            gazeCover.visibility = View.GONE
            gazeSaidBox.visibility = View.GONE
            gazeSkip.visibility = View.GONE
            p.volume = 1f
        }
        if (covering) {
            var due = 0
            val st = madeStart
            for (k in st.indices) if (st[k] >= coverStartS - 1 && st[k] <= t + 0.3) due++
            if (due != shownMade) showSaid()
            val left = (coverEnd - t).toInt()
            gazeSkip.text = "Skip  ▶   (" + (left / 60) + ":" + String.format("%02d", left % 60) + ")"
        }
    }

    /** The panel: MuteHaram's own subtitles spoken so far in this covered part (the website's if not ready). */
    private fun showSaid() {
        val t = (player?.currentPosition ?: 0L) / 1000.0
        val coverStart = coverStartS
        val mine = ArrayList<String>()
        val st = madeStart
        val tx = madeText
        for (k in st.indices) if (st[k] >= coverStart - 1 && st[k] <= t + 0.3 && k < tx.size) mine.add(tx[k])
        shownMade = mine.size
        val lines = if (mine.isNotEmpty()) mine else said
        gazeSaid.text = if (lines.isEmpty()) "(nothing said yet)" else lines.takeLast(10).joinToString("\n\n")
    }

    /** The PC's scene check, sent with every check-in. */
    private fun readScenes(j: org.json.JSONObject) {
        val sc = j.optJSONObject("scenes")
        if (sc == null) {
            hasScenes = false
            return
        }
        val arr = sc.optJSONArray("covers")
        val out = DoubleArray((arr?.length() ?: 0) * 2)
        if (arr != null) for (k in 0 until arr.length()) {
            val r = arr.optJSONArray(k) ?: continue
            out[2 * k] = r.optDouble(0)
            out[2 * k + 1] = r.optDouble(1)
        }
        covers = out
        val sd = sc.optJSONArray("said")
        if (sd != null) {
            val st = DoubleArray(sd.length())
            val tx = ArrayList<String>()
            for (k in 0 until sd.length()) {
                val r = sd.optJSONArray(k)
                st[k] = r?.optDouble(0) ?: 0.0
                tx.add(r?.optString(2) ?: "")
            }
            madeText = tx
            madeStart = st
        }
        scannedTo = sc.optDouble("scanned_to", 0.0)
        scenesDone = sc.optBoolean("done", false) || sc.optString("error").isNotEmpty()
        if (!hasScenes) {
            hasScenes = true
            ui.post { refreshOverlay() }
        }
    }

    /** What the home screen card says: the first line as its title, the rest as the explanation. */
    private fun showOnHome(text: String, tappable: Boolean) {
        val lines = text.trim().split("\n", limit = 2)
        homeTitle.text = lines[0]
        homeDetail.text = if (lines.size > 1) lines[1] else ""
        homeDetail.visibility = if (lines.size > 1) View.VISIBLE else View.GONE
        val problem = Regex("(problem|could not|failed|stopped|error)", RegexOption.IGNORE_CASE).containsMatchIn(lines[0])
        val busy = !problem && !tappable &&
            Regex("(connecting|preparing|loading|starting|trying|cleaning|waiting|opening)", RegexOption.IGNORE_CASE)
                .containsMatchIn(text)
        homeBar.visibility = if (busy) View.VISIBLE else View.GONE
        (homeDot.background as GradientDrawable).setColor(if (problem) WARN else TEAL)
        homeCard.background = pill(0xB3081A2B.toInt(), if (tappable) TEAL else TEAL_DIM, 18)
        val addr = prefs.getString("addr", "") ?: ""
        homeFooter.text = if (addr.isBlank()) "Not connected yet: tap Settings" else "PC  $addr"
    }
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

        // the home screen (nothing playing yet): logo, name and a card saying what is happening
        home = buildHome()
        root.addView(home, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        gazeCover = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR, intArrayOf(NAVY_DEEP, NAVY, TEAL_DEEP))
            visibility = View.GONE
            isClickable = false
            addView(ImageView(context).apply { setImageResource(R.drawable.mh_logo) }, LinearLayout.LayoutParams(dp(84), dp(84)))
            addView(TextView(context).apply {
                text = "غضّ البصر"
                setTextColor(TEAL)
                textSize = 26f
                gravity = Gravity.CENTER
            }, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(18) })
            gazeTitle = TextView(context).apply {
                setTextColor(CREAM)
                textSize = 32f
                typeface = Typeface.create(Typeface.SERIF, Typeface.BOLD)
                gravity = Gravity.CENTER
            }
            addView(gazeTitle, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(4) })
            gazeLine = TextView(context).apply {
                setTextColor(MUTED)
                textSize = 15f
                gravity = Gravity.CENTER
            }
            addView(gazeLine, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(8) })
            // skip to where the covered part ends
            gazeSkip = TextView(context).apply {
                setTextColor(CREAM)
                textSize = 16f
                typeface = Typeface.DEFAULT_BOLD
                setPadding(dp(22), dp(11), dp(22), dp(11))
                background = pill(0xCC15524F.toInt(), TEAL)
                visibility = View.GONE
                setOnClickListener { player?.seekTo(((coverEnd + 0.2) * 1000).toLong()) }
            }
            addView(gazeSkip, LinearLayout.LayoutParams(-2, -2).apply { topMargin = dp(22) })
        }
        root.addView(gazeCover, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        // what is being said while the picture is covered: the subtitles, in a panel on the right
        gazeSaidBox = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = pill(0xE6081A2B.toInt(), TEAL_DIM, 16)
            setPadding(dp(18), dp(16), dp(18), dp(16))
            visibility = View.GONE
            addView(TextView(context).apply {
                text = "What is said"
                setTextColor(TEAL)
                textSize = 15f
                typeface = Typeface.DEFAULT_BOLD
            })
            gazeSaid = TextView(context).apply {
                setTextColor(CREAM)
                textSize = 15f
                setLineSpacing(0f, 1.2f)
                gravity = Gravity.BOTTOM
            }
            addView(gazeSaid, LinearLayout.LayoutParams(-1, 0, 1f).apply { topMargin = dp(10) })
        }
        root.addView(gazeSaidBox, FrameLayout.LayoutParams(dp(330), ViewGroup.LayoutParams.MATCH_PARENT,
            Gravity.END).apply { setMargins(dp(16), dp(70), dp(16), dp(70)) })

        // messages while a film plays: a rounded panel at the top, out of the way of the play/pause button
        status = TextView(this).apply {
            setTextColor(CREAM)
            background = pill(0xE6081A2B.toInt(), TEAL_DIM)
            textSize = 15f
            setPadding(dp(20), dp(12), dp(20), dp(12))
            gravity = Gravity.CENTER
            visibility = View.GONE
            isClickable = false             // a plain message never catches a tap meant for the player (pause ...)
            isFocusable = false
        }
        root.addView(
            status,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.CENTER_HORIZONTAL
            ).apply { topMargin = dp(20) }
        )

        val gear = TextView(this).also { gearButton = it }.apply {
            text = "⚙  Settings"
            setTextColor(CREAM)
            background = pill(0xCC081A2B.toInt(), TEAL_DIM)
            textSize = 14f
            setPadding(dp(16), dp(9), dp(16), dp(9))
            setOnClickListener { showSettings() }
        }
        root.addView(
            gear,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END
            ).apply { setMargins(dp(16), dp(16), dp(16), dp(16)) }
        )
        modeButton = TextView(this).apply {
            setTextColor(CREAM)
            background = pill(0xCC081A2B.toInt(), TEAL_DIM)
            textSize = 14f
            setPadding(dp(16), dp(9), dp(16), dp(9))
            visibility = View.GONE
        }
        root.addView(
            modeButton,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START
            ).apply { setMargins(dp(16), dp(16), dp(16), dp(16)) }
        )
        gazeButton = TextView(this).apply {
            setTextColor(CREAM)
            textSize = 14f
            setPadding(dp(16), dp(9), dp(16), dp(9))
            visibility = View.GONE
            setOnClickListener {
                prefs.edit().putBoolean("gaze", !gazeOn()).apply()
                showGazeButton()
                checkGaze()
            }
        }
        root.addView(
            gazeButton,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START
            ).apply { setMargins(dp(16), dp(68), dp(16), dp(16)) }
        )
        showGazeButton()
        ui.post(gazeTimer)
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
            AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert).setTitle("The player stopped unexpectedly last time")
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
        AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Connect to your PC")
            .setIcon(R.drawable.mh_logo)
            .setMessage("Type the PC address and token shown at the bottom of the MuteHaram library window on the PC.")
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
                // nothing playing: the home screen's card says it; while a film plays: the panel at the top
                showOnHome(text, tappable)
                status.text = text
                status.visibility = if (player != null) View.VISIBLE else View.GONE
                val target = if (player != null) status else homeCard
                if (tappable) {
                    target.setOnClickListener {
                        pendingOld?.let { o -> acceptedOldId = o.id; pendingOld = null }
                        stoppedId = -1L
                        autoRetries = 0
                        currentId = -1L
                        setStatus("Trying again ...")
                    }
                } else {
                    status.setOnClickListener(null)
                    status.isClickable = false
                    homeCard.setOnClickListener(null)
                    homeCard.isClickable = false
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
                    val url = "http://" + host + ":47804/session.json?t=" + URLEncoder.encode(tok, "UTF-8") +
                        (if (diag.isEmpty()) "" else "&diag=" + URLEncoder.encode(diag, "UTF-8")) +
                        (if (resumeId == currentId && resumeMs > 0) "&pos=" + resumeMs else "")
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
                    readScenes(j)
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
        home.visibility = if (player == null) View.VISIBLE else View.GONE
        val show = controlsVisible || player == null
        gearButton.visibility = if (show) View.VISIBLE else View.GONE
        modeButton.visibility = if (show && current != null) View.VISIBLE else View.GONE
        gazeButton.visibility = if (show && current != null && hasScenes) View.VISIBLE else View.GONE
    }

    private fun noteLoad(info: LoadEventInfo, error: IOException?) {
        val which = if (info.uri.port == 47804) "PC sound" else "picture"
        val a = loads.getOrPut(which) { LongArray(4) }
        if (error == null) {
            a[0]++; a[1] += info.bytesLoaded; a[2] += info.loadDurationMs
        } else {
            a[3]++
            lastLoadError = which + ": " + (error.message ?: error.javaClass.simpleName).take(120)
        }
        updateDiag()
    }

    private fun updateDiag() {
        val parts = ArrayList<String>()
        parts.add("stalls " + stalls + (if (stallInfo.isEmpty()) "" else " (" + stallInfo + ")"))
        for ((k, a) in loads) {
            val mbit = if (a[2] > 0) a[1] * 8.0 / a[2] / 1000.0 else 0.0
            parts.add(k + ": " + a[0] + " pieces at " + String.format("%.1f", mbit) + " Mbit/s, " + a[3] + " failed")
        }
        if (lastLoadError.isNotEmpty()) parts.add("last failure " + lastLoadError)
        diag = parts.joinToString(" | ")
    }

    private fun play(s: SessionInfo, host: String, token: String, withSubs: Boolean = true) {
        current = s
        curHost = host
        curToken = token
        showMode(s.mode)
        setStatus(("Starting " + s.title).trim())
        player?.release()
        playingNow = false
        loads.clear(); lastLoadError = ""; stalls = 0; stallInfo = ""; diag = ""

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

        val audioUrl = "http://" + host + ":47804" + s.audio + "?t=" + URLEncoder.encode(token, "UTF-8")
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
        p.addAnalyticsListener(object : AnalyticsListener {
            override fun onLoadCompleted(t: AnalyticsListener.EventTime, info: LoadEventInfo, data: MediaLoadData) {
                noteLoad(info, null)
            }

            override fun onLoadError(t: AnalyticsListener.EventTime, info: LoadEventInfo, data: MediaLoadData,
                                     error: IOException, wasCanceled: Boolean) {
                noteLoad(info, error)
            }
        })
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

            override fun onCues(cueGroup: androidx.media3.common.text.CueGroup) {
                if (!covering) return
                val text = cueGroup.cues.mapNotNull { it.text?.toString()?.trim() }.filter { it.isNotEmpty() }.joinToString(" ")
                if (text.isNotEmpty() && said.lastOrNull() != text) {
                    said.add(text)
                    showSaid()
                }
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
                        g.isSelected && (0 until g.length).any { g.getTrackFormat(it).sampleMimeType == s.pcAudioMime }
                    }
                    setStatus(if (cleaned) "Sound: cleaned by the PC" else "Sound: ORIGINAL (the PC's cleaned sound is not playing)")
                    ui.postDelayed({ if (player === p) setStatus(null) }, 4000)
                }
                else if (state == Player.STATE_BUFFERING) {
                    setStatus("Loading ...")
                    if (p.currentPosition > 1000) {
                        stalls++
                        val pos = p.currentPosition / 1000
                        stallInfo = "last at " + (pos / 60) + ":" + String.format("%02d", pos % 60)
                        updateDiag()
                    }
                }
            }
        })
        // pick the PC's cleaned sound from the very first moment (otherwise the film's own sound, WITH the music,
        // can play for a few seconds until the choice below switches over)
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setPreferredAudioMimeType(s.pcAudioMime)
            .build()
        p.setMediaSource(MergingMediaSource(video, audio), (s.startSeconds * 1000).toLong())
        p.prepare()
        p.playWhenReady = true
        playerView.player = p
        player = p
        refreshOverlay()
        controlsVisible = false
        refreshOverlay()
    }

    /** The picture's own sound (with the music) is switched off: the PC's cleaned audio plays instead. */
    private fun preferPcAudio(p: ExoPlayer, tracks: Tracks) {
        val groups = tracks.groups.filter { it.type == C.TRACK_TYPE_AUDIO }
        if (groups.size < 2) return
        val pc = groups.firstOrNull { g ->
            (0 until g.length).any { g.getTrackFormat(it).sampleMimeType == (current?.pcAudioMime ?: MimeTypes.AUDIO_RAW) }
        } ?: return
        if (pc.isSelected) return
        p.trackSelectionParameters = p.trackSelectionParameters.buildUpon()
            .setOverrideForType(TrackSelectionOverride(pc.mediaTrackGroup, 0))
            .build()
    }
}
