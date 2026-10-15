package com.muteharam.separation

import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Stateless windowed-sinc resampler between two integer sample rates.
 *
 * Output sample j sits at input position j * inRate / outRate; samples outside
 * the input are treated as zero, so the first/last few milliseconds (about
 * [HALF_WIDTH] input samples) are not accurate. The separation pipeline always
 * resamples windows with a margin and throws the edges away.
 */
class Resampler(val inRate: Int, val outRate: Int, private val halfWidth: Int = HALF_WIDTH) {
    private val g = gcd(inRate, outRate)
    private val up = outRate / g       // phases
    private val down = inRate / g      // input step per output sample, in 1/up units... see below
    private val cutoff = min(1.0, outRate.toDouble() / inRate)
    private val taps = 2 * ceil(halfWidth / cutoff).toInt()
    private val left = taps / 2        // taps reaching to the left of the centre sample
    private val table: Array<DoubleArray>

    init {
        table = Array(up) { phase ->
            val frac = phase.toDouble() / up
            val h = DoubleArray(taps)
            var sum = 0.0
            for (k in 0 until taps) {
                // tap k reads input sample i0 - left + 1 + k
                val x = (k - left + 1) - frac
                val w = hann(x / (halfWidth / cutoff))
                val v = sinc(x * cutoff) * cutoff * w
                h[k] = v
                sum += v
            }
            for (k in 0 until taps) h[k] /= sum   // unity gain at DC
            h
        }
    }

    fun outputLength(inputLength: Int): Int = (inputLength.toLong() * outRate / inRate).toInt()

    fun process(x: FloatArray, count: Int = x.size): FloatArray {
        if (inRate == outRate) return x.copyOf(count)
        val n = outputLength(count)
        val out = FloatArray(n)
        for (j in 0 until n) {
            val pos = j.toLong() * down
            val i0 = (pos / up).toInt()
            val phase = (pos % up).toInt()
            val h = table[phase]
            val first = i0 - left + 1
            var acc = 0.0
            val kStart = max(0, -first)
            val kEnd = min(taps, count - first)
            for (k in kStart until kEnd) acc += x[first + k] * h[k]
            out[j] = acc.toFloat()
        }
        return out
    }

    companion object {
        const val HALF_WIDTH = 16

        private fun gcd(a: Int, b: Int): Int = if (b == 0) a else gcd(b, a % b)

        private fun sinc(x: Double): Double = if (x == 0.0) 1.0 else sin(PI * x) / (PI * x)

        /** Hann window over [-1, 1]. */
        private fun hann(u: Double): Double = if (u <= -1.0 || u >= 1.0) 0.0 else 0.5 + 0.5 * cos(PI * u)
    }
}
