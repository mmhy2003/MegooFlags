// The polling fallback: refreshes the values on an interval while the stream is down, or always with
// disableStream.
package io.github.mmhy2003.megooflags.internal

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

/** The shortest poll interval. */
private val MIN_INTERVAL = 15.seconds

/** Poller calls [refresh] on [scope] every [interval] (at least 15 s); an exception it throws is ignored. */
internal class Poller(
    private val scope: CoroutineScope,
    interval: Duration,
    private val refresh: () -> Unit,
) {
    private val interval = maxOf(interval, MIN_INTERVAL)
    private val lock = Any()
    private var job: Job? = null

    val running: Boolean get() = synchronized(lock) { job != null }

    /** start schedules the first refresh one interval from now; it is idempotent. */
    fun start() {
        synchronized(lock) {
            if (job != null) return
            job =
                scope.launch {
                    while (true) {
                        delay(interval)
                        try {
                            refresh()
                        } catch (e: CancellationException) {
                            throw e
                        } catch (_: Exception) {
                            // The next tick tries again.
                        }
                    }
                }
        }
    }

    fun stop() {
        synchronized(lock) {
            job?.cancel()
            job = null
        }
    }
}
