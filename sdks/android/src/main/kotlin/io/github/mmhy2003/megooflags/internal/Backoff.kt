package io.github.mmhy2003.megooflags.internal

import kotlin.random.Random

/**
 * Backoff computes waits in milliseconds. The first wait is min(base, max); the n-th is min(max, base * 2^n)
 * scaled by a factor in [0.8, 1.0]. base can be replaced, for the stream's retry field.
 */
internal class Backoff(
    var base: Long,
    private val max: Long,
    private val random: Random = Random.Default,
) {
    private var n = 0

    /** next returns the next wait and advances the attempt counter. */
    fun next(): Long {
        if (n == 0) {
            n++
            return minOf(base, max)
        }
        val full = minOf(max.toDouble(), base.toDouble() * Math.scalb(1.0, n))
        if (n < 64) {
            n++
        }
        return (full * (0.8 + 0.2 * random.nextDouble())).toLong()
    }

    /** reset makes the next wait base again. */
    fun reset() {
        n = 0
    }
}
