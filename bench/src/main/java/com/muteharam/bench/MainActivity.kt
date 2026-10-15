package com.muteharam.bench

import android.app.Activity
import android.os.Bundle
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.google.ai.edge.litert.Accelerator
import com.google.ai.edge.litert.CompiledModel
import java.io.File
import kotlin.math.sin

/**
 * Speed test for the TIGER music-separation network on this device's GPU.
 * Each button runs one chunk length: T STFT frames = (T-1)*512 samples of
 * 44.1 kHz audio. T=1040 is the original 12 s model; the others are the same
 * model re-timed by hook/research/retime/retime.py (built in CI).
 * Everything is also written to logcat under the tag MuteBench.
 */
class MainActivity : Activity() {
    private lateinit var out: TextView

    // (frames, asset file)
    private val variants = listOf(
        128 to "tiger_music_T128.tflite",
        176 to "tiger_music_T176.tflite",
        256 to "tiger_music_T256.tflite",
        1040 to "tiger_music_fp16.tflite",
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        val row = LinearLayout(this)
        for ((t, asset) in variants) {
            val secs = (t - 1) * 512 / 44100.0
            row.addView(Button(this).apply {
                text = "%.1f s".format(secs)
                setOnClickListener { run(t, asset) }
            })
        }
        root.addView(row)
        out = TextView(this).apply { textSize = 13f; setTextIsSelectable(true) }
        root.addView(ScrollView(this).apply { addView(out) },
            ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)
        log("MuteHaram Bench. Tap a length to time that model on the GPU (one at a time).")
        log("Device: ${android.os.Build.MODEL}, Android ${android.os.Build.VERSION.RELEASE}")
    }

    private fun log(s: String) {
        Log.i("MuteBench", s)
        runOnUiThread { out.append(s + "\n") }
    }

    private fun thermal(): String {
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        return when (pm.currentThermalStatus) {
            PowerManager.THERMAL_STATUS_NONE -> "none"
            PowerManager.THERMAL_STATUS_LIGHT -> "light"
            PowerManager.THERMAL_STATUS_MODERATE -> "moderate"
            PowerManager.THERMAL_STATUS_SEVERE -> "SEVERE"
            else -> "critical"
        }
    }

    private fun run(frames: Int, asset: String) {
        Thread {
            try {
                val chunk = (frames - 1) * 512
                val inputLen = chunk + 2048
                val seconds = chunk / 44100.0
                log("---- T=$frames  (%.2f s of audio per call) ----".format(seconds))
                val f = File(filesDir, asset)
                if (!f.exists() || f.length() == 0L) {
                    assets.open(asset).use { i -> f.outputStream().use { o -> i.copyTo(o) } }
                }
                val t0 = SystemClock.elapsedRealtime()
                val model = CompiledModel.create(f.absolutePath, CompiledModel.Options(Accelerator.GPU), null)
                log("load+compile: ${SystemClock.elapsedRealtime() - t0} ms")
                val inB = model.createInputBuffers()
                val outB = model.createOutputBuffers()
                inB[0].writeFloat(FloatArray(inputLen) { (0.1 * sin(it * 0.05) + 0.05 * sin(it * 0.0123)).toFloat() })
                val times = ArrayList<Long>()
                val runs = if (frames > 500) 8 else 20
                for (i in 0 until runs) {
                    val t = SystemClock.elapsedRealtime()
                    model.run(inB, outB)
                    val dt = SystemClock.elapsedRealtime() - t
                    times.add(dt)
                    if (i < 4 || i % 5 == 4) log("run ${i + 1}: $dt ms   (thermal: ${thermal()})")
                }
                val re = outB[0].readFloat()
                var bad = 0
                for (v in re) if (v.isNaN() || v.isInfinite()) bad++
                log("output values: ${re.size}, NaN/inf: $bad")
                val steady = times.drop(2).sorted()
                val med = steady[steady.size / 2]
                val perSec = med / 1000.0 / seconds
                log("median (after 2 warm-up): $med ms   (slowest ${steady.last()} ms)")
                log("=> %.2f s of compute per 1 s of audio if every second were processed once".format(perSec))
                log("=> window 2x overlap (hop = half): %.2f   hop = 3/4 window: %.2f   (must stay under ~0.7)"
                    .format(perSec * 2, perSec * 4 / 3))
                model.close()
            } catch (t: Throwable) {
                log("ERROR: $t")
                Log.e("MuteBench", "failed", t)
            }
        }.start()
    }
}
