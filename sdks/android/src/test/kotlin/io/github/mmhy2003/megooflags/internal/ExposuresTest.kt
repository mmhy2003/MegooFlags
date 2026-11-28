package io.github.mmhy2003.megooflags.internal

import org.junit.Assert.assertEquals
import org.junit.Test

private val USER1 = mapOf("key" to "user-1", "plan" to "pro")

// 2026-11-10T09:15:02Z.
private const val START = 1_794_302_102_000L

class ExposuresTest {
    private var now = START
    private val logger = LineLogger()

    private fun make(
        sendContext: Boolean = false,
        maxKeys: Int = 10_000,
        maxBuffer: Int = 10_000,
    ) = Exposures(sendContext, logger, { now }, maxKeys, maxBuffer)

    private val warnings get() = logger.lines.filter { it.startsWith("warn ") }

    private fun Exposures.flags(max: Int = 10) = drain(max).map { it.flagKey }

    @Test
    fun recordsTheSameUserFlagAndVariantOnceWithinTheHour() {
        val ex = make()

        ex.record("f", "on", "TARGETING_MATCH", USER1)
        now += 30 * 60_000
        ex.record("f", "on", "TARGETING_MATCH", USER1)

        assertEquals(1, ex.size)
    }

    @Test
    fun recordsItAgainInTheNextUtcHour() {
        val ex = make()

        ex.record("f", "on", "TARGETING_MATCH", USER1)
        now += 45 * 60_000
        ex.record("f", "on", "TARGETING_MATCH", USER1)

        assertEquals(2, ex.size)
    }

    @Test
    fun recordsAnotherVariantFlagOrUserAgain() {
        val ex = make()

        ex.record("f", "on", "TARGETING_MATCH", USER1)
        ex.record("f", "off", "TARGETING_MATCH", USER1)
        ex.record("g", "on", "TARGETING_MATCH", USER1)
        ex.record("f", "on", "TARGETING_MATCH", mapOf("key" to "user-2"))

        assertEquals(4, ex.size)
    }

    @Test
    fun treatsContextsWithoutATargetingKeyAsOneUser() {
        val ex = make()

        ex.record("f", "on", "STATIC", emptyMap())
        ex.record("f", "on", "STATIC", mapOf("plan" to "pro"))

        assertEquals(1, ex.size)
    }

    @Test
    fun evictsTheLeastRecentlySeenKey() {
        val ex = make(maxKeys = 2)

        ex.record("a", "on", "STATIC", USER1)
        ex.record("b", "on", "STATIC", USER1)
        ex.record("a", "on", "STATIC", USER1) // a is now the most recent
        ex.record("c", "on", "STATIC", USER1) // evicts b
        ex.drain(10)
        ex.record("a", "on", "STATIC", USER1)
        ex.record("b", "on", "STATIC", USER1)

        assertEquals(listOf("b"), ex.flags())
    }

    @Test
    fun buildsTheExposureWithAnRfc3339UtcTimestampAndNoContext() {
        val ex = make()

        ex.record("f", "on", "TARGETING_MATCH", USER1)

        val items = ex.drain(10)
        assertEquals(listOf(Exposure("f", "on", "TARGETING_MATCH", "2026-11-10T09:15:02.000Z", null)), items)
        assertEquals(
            mapOf(
                "flag_key" to "f",
                "variant" to "on",
                "reason" to "TARGETING_MATCH",
                "timestamp" to "2026-11-10T09:15:02.000Z",
            ),
            items[0].toJson(),
        )
    }

    @Test
    fun addsTheContextOnlyWithSendContext() {
        val ex = make(sendContext = true)

        ex.record("f", "on", "TARGETING_MATCH", USER1)

        val item = ex.drain(10)[0]
        assertEquals(USER1, item.context)
        assertEquals(USER1, item.toJson()["context"])
    }

    @Test
    fun drainsAtMostMaxItemsOldestFirst() {
        val ex = make()
        for (f in listOf("a", "b", "c")) ex.record(f, "on", "STATIC", USER1)

        assertEquals(listOf("a", "b"), ex.flags(2))
        assertEquals(1, ex.size)
        assertEquals(listOf("c"), ex.flags(2))
    }

    @Test
    fun putsItemsBackInFrontOfTheBuffer() {
        val ex = make()
        for (f in listOf("a", "b", "c")) ex.record(f, "on", "STATIC", USER1)
        val batch = ex.drain(2)

        ex.restore(batch)

        assertEquals(listOf("a", "b", "c"), ex.flags())
    }

    @Test
    fun restoreDropsTheNewestItemsOverTheBound() {
        val ex = make(maxBuffer = 2)
        for (f in listOf("a", "b")) ex.record(f, "on", "STATIC", USER1)
        val batch = ex.drain(1)
        ex.record("c", "on", "STATIC", USER1)

        ex.restore(batch)

        assertEquals(listOf("a", "b"), ex.flags())
        assertEquals(1, warnings.size)
    }

    @Test
    fun dropsNewItemsOverTheBoundAndWarnsOnceUntilTheNextDrain() {
        val ex = make(maxBuffer = 2)

        for (f in listOf("a", "b", "c", "d")) ex.record(f, "on", "STATIC", USER1)

        assertEquals(2, ex.size)
        assertEquals(listOf("warn megooflags: the exposure buffer is full (2); dropping new exposures"), warnings)
        assertEquals(listOf("a", "b"), ex.flags())

        for (f in listOf("e", "f", "g")) ex.record(f, "on", "STATIC", USER1)
        assertEquals(2, warnings.size)
    }

    @Test
    fun recordsADroppedItemAgainOnceThereIsRoom() {
        val ex = make(maxBuffer = 1)

        ex.record("a", "on", "STATIC", USER1)
        ex.record("b", "on", "STATIC", USER1)
        ex.drain(10)
        ex.record("b", "on", "STATIC", USER1)

        assertEquals(listOf("b"), ex.flags())
    }

    @Test
    fun callsOnFullOnceWhen1000ItemsAreBufferedUntilTheNextDrain() {
        val ex = make()
        var calls = 0
        ex.onFull = { calls++ }

        for (i in 0 until 999) ex.record("f$i", "on", "STATIC", USER1)
        assertEquals(0, calls)
        ex.record("f999", "on", "STATIC", USER1)
        assertEquals(1, calls)
        ex.record("more", "on", "STATIC", USER1)
        assertEquals(1, calls)

        ex.drain(1)
        ex.record("again", "on", "STATIC", USER1)
        assertEquals(2, calls)
    }
}
