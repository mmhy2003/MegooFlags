package io.github.mmhy2003.megooflags

import android.util.Log

/** Receives the SDK's log lines. Messages start with "megooflags: " and never contain the SDK key. */
public interface MegooFlagsLogger {
    public fun debug(message: String)

    public fun info(message: String)

    public fun warn(message: String)

    public fun error(message: String)
}

/** The default logger: Logcat under the tag MegooFlags. */
internal object LogcatLogger : MegooFlagsLogger {
    const val TAG: String = "MegooFlags"

    override fun debug(message: String) {
        Log.d(TAG, message)
    }

    override fun info(message: String) {
        Log.i(TAG, message)
    }

    override fun warn(message: String) {
        Log.w(TAG, message)
    }

    override fun error(message: String) {
        Log.e(TAG, message)
    }
}
