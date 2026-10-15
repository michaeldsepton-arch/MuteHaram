package com.muteharam.engine

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.muteharam.separation.ChunkModelEstimator
import com.muteharam.separation.ChunkModel
import kotlin.concurrent.thread
import kotlin.math.sin

/**
 * Controls for the engine. Typical use: open this app, tap "Start engine", wait for
 * "ready", then play a video in the patched YouTube / CineHD with
 *   adb shell settings put global muteharam_mode local
 */
class MainActivity : Activity() {
    private lateinit var text: TextView
    private val ui = Handler(Looper.getMainLooper())
    private var benchmarking = false

    private val tick = object : Runnable {
        override fun run() {
            render()
            ui.postDelayed(this, 500)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }

        fun button(label: String, onClick: () -> Unit) = Button(this).apply {
            this.text = label
            setOnClickListener { onClick() }
        }

        val row1 = LinearLayout(this)
        row1.addView(button("Start engine") {
            startForegroundService(Intent(this, EngineService::class.java))
        })
        row1.addView(button("Stop") {
            startService(Intent(this, EngineService::class.java).setAction(EngineService.ACTION_STOP))
        })
        row1.addView(button("Speed test") { speedTest() })
        root.addView(row1)

        val row2 = LinearLayout(this)
        for (f in LiteRtChunkModel.AVAILABLE_FRAMES) {
            val secs = (f - 1) * 512 / 44100.0
            row2.addView(button("%.1f s".format(secs)) {
                getSharedPreferences("engine", Context.MODE_PRIVATE).edit().putInt("frames", f).apply()
                render("Window length set to %.2f s. Stop and start the engine to apply.".format(secs))
            })
        }
        root.addView(row2)

        text = TextView(this).apply { textSize = 14f; setTextIsSelectable(true) }
        root.addView(ScrollView(this).apply { addView(text) },
            ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)
    }

    override fun onResume() { super.onResume(); ui.post(tick) }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick) }

    private var note = ""
    private var benchLog = ""

    private fun render(newNote: String? = null) {
        if (newNote != null) note = newNote
        val frames = getSharedPreferences("engine", Context.MODE_PRIVATE).getInt("frames", 176)
        val s = EngineState
        text.text = buildString {
            appendLine("Status: ${s.status}")
            appendLine("Window length: %.2f s (T=$frames)".format((frames - 1) * 512 / 44100.0))
            appendLine("Windows done: ${s.windows}   failed: ${s.failures}   hook connections: ${s.clients}")
            if (s.windows > 0) appendLine("AI time per window: last %.0f ms, average %.0f ms".format(s.lastMs, s.avgMs))
            appendLine()
            if (note.isNotEmpty()) { appendLine(note); appendLine() }
            if (benchLog.isNotEmpty()) { appendLine(benchLog); appendLine() }
            appendLine("How to use")
            appendLine("1. Tap Start engine and wait for 'ready' (the first start compiles the model: up to a minute).")
            appendLine("2. On a PC with adb:  adb shell settings put global muteharam_mode local")
            appendLine("3. Play a video in a patched YouTube / CineHD. The picture starts a couple of seconds late; that is the delay the AI needs.")
            appendLine("4. To turn it off:  adb shell settings put global muteharam_mode test")
        }
    }

    /** Runs the AI on a test signal and says whether this device can keep up. */
    private fun speedTest() {
        if (benchmarking) return
        if (EngineState.status.startsWith("ready")) {
            benchLog = "Stop the engine first (the speed test needs the GPU to itself)."
            return
        }
        benchmarking = true
        benchLog = "Speed test: loading the model ..."
        thread(name = "speedtest") {
            try {
                val frames = getSharedPreferences("engine", Context.MODE_PRIVATE).getInt("frames", 176)
                val model = LiteRtChunkModel.load(this, frames)
                val est = ChunkModelEstimator(model)
                val chunk = FloatArray(model.chunkSamples) { (0.1 * sin(it * 0.05) + 0.05 * sin(it * 0.0123)).toFloat() }
                val times = ArrayList<Double>()
                for (i in 0 until 8) {
                    val t0 = System.nanoTime()
                    est.estimate(chunk)
                    times.add((System.nanoTime() - t0) / 1e6)
                    benchLog = "Speed test: ${i + 1}/8 done (%.0f ms)".format(times.last())
                }
                est.close()
                val steady = times.drop(2).sorted()
                val med = steady[steady.size / 2]
                val seconds = model.chunkSamples / 44100.0
                val hop = seconds - 1.0          // with the default 0.5 s of context on each side
                val verdict = when {
                    med < hop * 1000 * 0.6 -> "FAST ENOUGH with room to spare"
                    med < hop * 1000 * 0.9 -> "just fast enough (tight)"
                    else -> "TOO SLOW for real time with this window length: try a longer one"
                }
                benchLog = "Speed test: median %.0f ms for a %.2f s window (needs to finish within %.0f ms).\n=> %s"
                    .format(med, seconds, hop * 1000, verdict)
            } catch (t: Throwable) {
                benchLog = "Speed test failed: $t"
            } finally {
                benchmarking = false
            }
        }
    }
}
