// The app's background signal (spec/client/README.md, background signal).
package io.github.mmhy2003.megooflags.internal

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
