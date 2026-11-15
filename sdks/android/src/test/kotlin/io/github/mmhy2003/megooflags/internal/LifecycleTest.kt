package io.github.mmhy2003.megooflags.internal

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.time.Duration.Companion.seconds

/** A LifecycleSource the test drives with [send]. */
private class FakeLifecycle : LifecycleSource {
    val listeners: MutableList<(BackgroundState) -> Unit> = CopyOnWriteArrayList()

    override fun subscribe(listener: (BackgroundState) -> Unit): () -> Unit {
        listeners.add(listener)
        return { listeners.remove(listener) }
    }

    fun send(state: BackgroundState) {
        listeners.forEach { it(state) }
    }
}

/** What one controller test works with; [calls] are "flush", "pause" and "resume". */
@OptIn(ExperimentalCoroutinesApi::class)
private class ControllerHarness(
    val test: TestScope,
) {
    val lifecycle = FakeLifecycle()
    val calls: MutableList<String> = CopyOnWriteArrayList()
    val scope = CoroutineScope(test.coroutineContext + SupervisorJob())
    val controller =
        BackgroundController(
            scope = scope,
            source = lifecycle,
            flush = { calls.add("flush") },
            pause = { calls.add("pause") },
            resume = { calls.add("resume") },
        )

    fun send(state: BackgroundState) {
        lifecycle.send(state)
        test.runCurrent()
    }

    fun advance(seconds: Int) {
        test.advanceTimeBy(seconds * 1_000L)
        test.runCurrent()
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
private fun controllerTest(body: ControllerHarness.() -> Unit) =
    runTest {
        val h = ControllerHarness(this)
        try {
            h.controller.start()
            h.body()
        } finally {
            h.scope.cancel()
        }
    }

class StartedActivitiesTest {
    private val states = mutableListOf<BackgroundState>()
    private val counter = StartedActivities { states.add(it) }

    @Test
    fun firstStartedActivityIsVisibleAndTheLastStoppedIsHidden() {
        counter.started()
        counter.started()
        counter.stopped(changingConfigurations = false)
        assertEquals(listOf(BackgroundState.VISIBLE), states)
        counter.stopped(changingConfigurations = false)
        assertEquals(listOf(BackgroundState.VISIBLE, BackgroundState.HIDDEN), states)
        counter.started()
        assertEquals(listOf(BackgroundState.VISIBLE, BackgroundState.HIDDEN, BackgroundState.VISIBLE), states)
    }

    @Test
    fun aConfigurationChangeIsNotHidden() {
        counter.started()
        counter.stopped(changingConfigurations = true)
        counter.started()
        assertEquals(listOf(BackgroundState.VISIBLE), states)
        // The recreated activity is the only one: its stop hides the app.
        counter.stopped(changingConfigurations = false)
        assertEquals(listOf(BackgroundState.VISIBLE, BackgroundState.HIDDEN), states)
    }

    @Test
    fun aStopWithoutAStartSeenIsHidden() {
        // Subscribed while an activity was already started.
        counter.stopped(changingConfigurations = false)
        counter.stopped(changingConfigurations = false)
        assertEquals(listOf(BackgroundState.HIDDEN), states)
    }
}

class BackgroundControllerTest {
    @Test
    fun hiddenFlushesAtOnceAndPausesAfter30Seconds() =
        controllerTest {
            send(BackgroundState.HIDDEN)
            assertEquals(listOf("flush"), calls)
            advance(29)
            assertEquals(listOf("flush"), calls)
            advance(1)
            assertEquals(listOf("flush", "pause"), calls)
            send(BackgroundState.VISIBLE)
            assertEquals(listOf("flush", "pause", "resume"), calls)
        }

    @Test
    fun visibleWithin30SecondsCancelsThePause() =
        controllerTest {
            send(BackgroundState.HIDDEN)
            advance(10)
            send(BackgroundState.VISIBLE)
            advance(60)
            assertEquals(listOf("flush"), calls)
        }

    @Test
    fun hiddenAgainDoesNotRestartTheTimer() =
        controllerTest {
            send(BackgroundState.HIDDEN)
            advance(20)
            send(BackgroundState.HIDDEN)
            advance(10)
            assertEquals(listOf("flush", "flush", "pause"), calls)
            send(BackgroundState.HIDDEN)
            advance(60)
            assertEquals(listOf("flush", "flush", "pause", "flush"), calls)
        }

    @Test
    fun visibleWithoutAPauseResumesNothing() =
        controllerTest {
            send(BackgroundState.VISIBLE)
            assertTrue(calls.isEmpty())
        }

    @Test
    fun stopUnsubscribesAndCancelsThePause() =
        controllerTest {
            send(BackgroundState.HIDDEN)
            controller.stop()
            assertTrue(lifecycle.listeners.isEmpty())
            advance(60)
            send(BackgroundState.HIDDEN)
            assertEquals(listOf("flush"), calls)
        }

    @Test
    fun startIsIdempotent() =
        controllerTest {
            controller.start()
            assertEquals(1, lifecycle.listeners.size)
        }

    @Test
    fun aFailingFlushIsIgnored() =
        runTest {
            val lifecycle = FakeLifecycle()
            val scope = CoroutineScope(coroutineContext + SupervisorJob())
            val calls = mutableListOf<String>()
            val controller =
                BackgroundController(scope, lifecycle, { error("boom") }, { calls.add("pause") }, {})
            try {
                controller.start()
                lifecycle.send(BackgroundState.HIDDEN)
                advanceTimeBy(30.seconds.inWholeMilliseconds)
                runCurrent()
                assertEquals(listOf("pause"), calls)
            } finally {
                scope.cancel()
            }
        }
}
