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
 * Speed test for the TIGER music-separation network on this phone's GPU.
 * Runs the ready-made 12 s model (input = 534016 samples @ 44.1 kHz) several
 * times and reports how long each run takes. Everything is also written to
 * logcat under the tag MuteBench.
 */
class MainActivity : Activity() {
    private lateinit var out: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        val row = LinearLayout(this)
        row.addView(Button(this).apply {
            text = "Run on GPU"
            setOnClickListener { run(Accelerator.GPU) }
        })
        row.addView(Button(this).apply {
            text = "Run on CPU"
            setOnClickListener { run(Accelerator.CPU) }
        })
        root.addView(row)
        out = TextView(this).apply { textSize = 13f; setTextIsSelectable(true) }
        root.addView(ScrollView(this).apply { addView(out) },
            ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)
        log("MuteHaram Bench. Tap a button. Takes a minute; keep the screen on.")
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

    private fun run(accel: Accelerator) {
        Thread {
            try {
                log("---- ${accel.name} ----")
                val f = File(filesDir, "tiger_music_fp16.tflite")
                if (!f.exists() || f.length() == 0L) {
                    assets.open("tiger_music_fp16.tflite").use { i -> f.outputStream().use { o -> i.copyTo(o) } }
                }
                val t0 = SystemClock.elapsedRealtime()
                val model = CompiledModel.create(f.absolutePath, CompiledModel.Options(accel), null)
                log("load+compile: ${SystemClock.elapsedRealtime() - t0} ms")
                val inB = model.createInputBuffers()
                val outB = model.createOutputBuffers()
                val x = FloatArray(534016) { (0.1 * sin(it * 0.05) + 0.05 * sin(it * 0.0123)).toFloat() }
                inB[0].writeFloat(x)
                val times = ArrayList<Long>()
                for (i in 0 until 12) {
                    val t = SystemClock.elapsedRealtime()
                    model.run(inB, outB)
                    val dt = SystemClock.elapsedRealtime() - t
                    times.add(dt)
                    log("run ${i + 1}: $dt ms   (thermal: ${thermal()})")
                }
                val re = outB[0].readFloat()
                var bad = 0
                for (v in re) if (v.isNaN() || v.isInfinite()) bad++
                log("output values: ${re.size}, NaN/inf: $bad")
                val steady = times.drop(2).sorted()
                val med = steady[steady.size / 2]
                log("median (after 2 warm-up): $med ms for 12.06 s of audio")
                log("=> ${"%.2f".format(med / 12060.0)} s of compute per 1 s of audio (needs to be well under 1.0)")
                model.close()
            } catch (t: Throwable) {
                log("ERROR: $t")
                Log.e("MuteBench", "failed", t)
            }
        }.start()
    }
}
