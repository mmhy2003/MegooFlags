// The app's background signal (spec/client/README.md, background signal): the started-activity counter that tells
// visible from hidden, and the controller that flushes, pauses and resumes the provider's delivery.
package io.github.mmhy2003.megooflags.internal

import android.app.Activity
import android.app.Application
import android.os.Bundle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

/** Whether the app has a started activity. */
internal enum class BackgroundState {
    VISIBLE,
    HIDDEN,
}

/** Tells the provider when the app goes to the background and comes back. */
internal fun interface LifecycleSource {
    /** subscribe calls [listener] on every change and returns the function that unsubscribes it. */
    fun subscribe(listener: (BackgroundState) -> Unit): () -> Unit
}

/**
 * StartedActivities counts the started activities and reports [onChange] when the app becomes visible (the first
 * start) or hidden (the last stop). A stop for a configuration change is not hidden: the recreated activity starts
 * right after. Each state is reported once until the other one. Not thread-safe: call it on the main thread.
 */
internal class StartedActivities(
    private val onChange: (BackgroundState) -> Unit,
) {
    private var started = 0
    private var last: BackgroundState? = null

    fun started() {
        started++
        report(BackgroundState.VISIBLE)
    }

    fun stopped(changingConfigurations: Boolean) {
        // A stop without a start seen: the listener subscribed while that activity was started.
        if (started > 0) started--
        if (started == 0 && !changingConfigurations) report(BackgroundState.HIDDEN)
    }

    private fun report(state: BackgroundState) {
        if (state == last) return
        last = state
        onChange(state)
    }
}

/** ActivityLifecycleSource follows the activities of [application] through its lifecycle callbacks. */
internal class ActivityLifecycleSource(
    private val application: Application,
) : LifecycleSource {
    override fun subscribe(listener: (BackgroundState) -> Unit): () -> Unit {
        val counter = StartedActivities(listener)
        val callbacks =
            object : Application.ActivityLifecycleCallbacks {
                override fun onActivityStarted(activity: Activity) {
                    counter.started()
                }

                override fun onActivityStopped(activity: Activity) {
                    counter.stopped(activity.isChangingConfigurations)
                }

                override fun onActivityCreated(
                    activity: Activity,
                    savedInstanceState: Bundle?,
                ) {}

                override fun onActivityResumed(activity: Activity) {}

                override fun onActivityPaused(activity: Activity) {}

                override fun onActivitySaveInstanceState(
                    activity: Activity,
                    outState: Bundle,
                ) {}

                override fun onActivityDestroyed(activity: Activity) {}
            }
        application.registerActivityLifecycleCallbacks(callbacks)
        return { application.unregisterActivityLifecycleCallbacks(callbacks) }
    }
}

/** How long the app stays hidden before the stream and the poller pause. */
private val HIDDEN_GRACE = 30.seconds

/**
 * BackgroundController follows [source]: hidden flushes the exposures at once and pauses delivery after 30 s
 * hidden; visible cancels that pause, or resumes what was paused. The flush and the timer run on [scope]; a failing
 * flush is ignored. Every method may be called from any thread.
 */
internal class BackgroundController(
    private val scope: CoroutineScope,
    private val source: LifecycleSource?,
    private val flush: suspend () -> Unit,
    private val pause: () -> Unit,
    private val resume: () -> Unit,
    private val grace: Duration = HIDDEN_GRACE,
) {
    private val lock = Any()
    private var unsubscribe: (() -> Unit)? = null
    private var timer: Job? = null
    private var paused = false

    /** start follows the source; it is idempotent. */
    fun start() {
        synchronized(lock) {
            if (source == null || unsubscribe != null) return
            unsubscribe = source.subscribe { on(it) }
        }
    }

    /** stop unsubscribes and cancels a pending pause. */
    fun stop() {
        val unsubscribe =
            synchronized(lock) {
                timer?.cancel()
                timer = null
                unsubscribe.also { unsubscribe = null }
            }
        unsubscribe?.invoke()
    }

    private fun on(state: BackgroundState) {
        when (state) {
            BackgroundState.HIDDEN -> hidden()
            BackgroundState.VISIBLE -> visible()
        }
    }

    private fun hidden() {
        synchronized(lock) {
            if (unsubscribe == null) return
            scope.launch {
                try {
                    flush()
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) {
                    // The batcher keeps what it could not send.
                }
            }
            if (paused || timer != null) return
            val job =
                scope.launch(start = CoroutineStart.LAZY) {
                    delay(grace)
                    synchronized(lock) {
                        if (timer !== coroutineContext[Job]) return@launch
                        timer = null
                        paused = true
                    }
                    pause()
                }
            timer = job
            job.start()
        }
    }

    private fun visible() {
        val wasPaused =
            synchronized(lock) {
                timer?.cancel()
                timer = null
                paused.also { paused = false }
            }
        if (wasPaused) resume()
    }
}
