package io.github.mmhy2003.megooflags.internal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** A Random whose nextDouble is always [value]. */
private class FixedRandom(
    private val value: Double,
) : Random() {
    override fun nextBits(bitCount: Int): Int = 0

    override fun nextDouble(): Double = value
}

class BackoffTest {
    @Test
    fun firstWaitIsBase() {
        assertEquals(1_000L, Backoff(1_000, 60_000, FixedRandom(0.0)).next())
    }

    @Test
    fun doublesFromBaseUpToMax() {
        val b = Backoff(3_000, 60_000, FixedRandom(1.0))
        assertEquals(listOf(3_000L, 6_000L, 12_000L, 24_000L, 48_000L, 60_000L, 60_000L), List(7) { b.next() })
    }

    @Test
    fun resetStartsOver() {
        val b = Backoff(3_000, 60_000, FixedRandom(1.0))
        b.next()
        b.next()
        b.reset()
        assertEquals(3_000L, b.next())
    }

    @Test
    fun jittersFromTheSecondWait() {
        val b = Backoff(3_000, 60_000, FixedRandom(0.0))
        assertEquals(listOf(3_000L, 4_800L, 9_600L), List(3) { b.next() })
    }

    @Test
    fun keepsJitterWithinBounds() {
        val b = Backoff(1_000, 60_000)
        b.next()
        for (n in 1 until 6) {
            val w = b.next()
            val full = minOf(60_000L, 1_000L shl n)
            assertTrue("$w", w >= full * 8 / 10 && w <= full)
        }
    }

    @Test
    fun firstWaitIsNeverAboveMax() {
        assertEquals(60_000L, Backoff(90_000, 60_000).next())
    }

    @Test
    fun neverGoesAboveMax() {
        val b = Backoff(3_000, 60_000)
        repeat(200) {
            val w = b.next()
            assertTrue("$w", w in 1..60_000)
        }
    }

    @Test
    fun baseCanBeReplaced() {
        val b = Backoff(3_000, 60_000, FixedRandom(1.0))
        b.base = 1_000
        assertEquals(listOf(1_000L, 2_000L), List(2) { b.next() })
    }
}
