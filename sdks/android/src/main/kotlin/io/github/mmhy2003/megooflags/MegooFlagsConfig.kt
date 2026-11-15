// The provider's configuration, its validation and defaults. Messages never contain the SDK key.
package io.github.mmhy2003.megooflags

import android.app.Application
import io.github.mmhy2003.megooflags.internal.ActivityLifecycleSource
import io.github.mmhy2003.megooflags.internal.EvaluateResult
import io.github.mmhy2003.megooflags.internal.LifecycleSource
import io.github.mmhy2003.megooflags.internal.parseResults
import java.io.File
import java.net.URI
import java.net.URISyntaxException
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

private val MIN_POLL_INTERVAL = 15.seconds
private val MIN_FLUSH_INTERVAL = 10.seconds
private val MAX_FLUSH_INTERVAL = 60.seconds

/** How a MegooFlagsProvider connects and behaves; build it with [Builder]. */
public class MegooFlagsConfig private constructor(
    internal val sdkKey: String,
    /** Without trailing slashes; null when offline without a base URL. */
    internal val baseUrl: String?,
    internal val bootstrap: List<EvaluateResult>?,
    internal val disableCache: Boolean,
    internal val offline: Boolean,
    internal val initTimeout: Duration,
    internal val disableStream: Boolean,
    internal val pollInterval: Duration,
    internal val flushInterval: Duration,
    internal val sendContext: Boolean,
    internal val logger: MegooFlagsLogger,
    /** Where the cache file lives; null: no cache. */
    internal val cacheDir: File?,
    internal val lifecycle: LifecycleSource?,
) {
    /** Collects the options; [build] checks them and throws IllegalArgumentException when one is invalid. */
    public class Builder internal constructor(
        private val sdkKey: String,
        private val cacheDir: File?,
        private val lifecycle: LifecycleSource?,
    ) {
        private var baseUrl: String? = null
        private var bootstrap: String? = null
        private var disableCache = false
        private var offline = false
        private var initTimeout = 5.seconds
        private var disableStream = false
        private var pollInterval = 30.seconds
        private var flushInterval = 30.seconds
        private var sendContext = false
        private var logger: MegooFlagsLogger = LogcatLogger

        /**
         * A client SDK key (mf_client_…); the cache lives in the app's files directory under megooflags, and the
         * app's activities tell when it is in the background.
         */
        public constructor(context: android.content.Context, sdkKey: String) :
            this(
                sdkKey,
                File(context.applicationContext.filesDir, "megooflags"),
                (context.applicationContext as? Application)?.let(::ActivityLifecycleSource),
            )

        /** The MegooFlags server, an http or https URL; required unless offline. */
        public fun baseUrl(url: String): Builder = apply { baseUrl = url }

        /** The body of an evaluate response ({"results": [...]}) as JSON text, served until there are newer values. */
        public fun bootstrap(json: String): Builder = apply { bootstrap = json }

        public fun disableCache(v: Boolean = true): Builder = apply { disableCache = v }

        /** No network and no events; needs bootstrap or a cache entry. */
        public fun offline(v: Boolean = true): Builder = apply { offline = v }

        /** How long initialisation waits for the first values; 5 seconds by default. */
        public fun initTimeout(d: Duration): Builder = apply { initTimeout = d }

        /** Poll instead of following the stream. */
        public fun disableStream(v: Boolean = true): Builder = apply { disableStream = v }

        /** 30 seconds by default; raised to 15 seconds when smaller. */
        public fun pollInterval(d: Duration): Builder = apply { pollInterval = d }

        /** How often exposures are sent; 30 seconds by default, clamped to 10…60 seconds. */
        public fun flushInterval(d: Duration): Builder = apply { flushInterval = d }

        /** Send the context with exposure events. */
        public fun sendContext(v: Boolean = true): Builder = apply { sendContext = v }

        /** Logcat by default. */
        public fun logger(l: MegooFlagsLogger): Builder = apply { logger = l }

        public fun build(): MegooFlagsConfig {
            require(!sdkKey.startsWith("mf_server_")) {
                "megooflags: a server key must never ship in an app; use a client key (mf_client_…)"
            }
            require(sdkKey.startsWith("mf_client_")) { "megooflags: sdkKey must be a client key (mf_client_…)" }
            require(initTimeout.isPositive()) { "megooflags: initTimeout must be greater than 0" }
            return MegooFlagsConfig(
                sdkKey = sdkKey,
                baseUrl = checkBaseUrl(baseUrl, offline),
                bootstrap = bootstrap?.let { parseResults(it, "megooflags: invalid bootstrap") },
                disableCache = disableCache,
                offline = offline,
                initTimeout = initTimeout,
                disableStream = disableStream,
                pollInterval = maxOf(pollInterval, MIN_POLL_INTERVAL),
                flushInterval = flushInterval.coerceIn(MIN_FLUSH_INTERVAL, MAX_FLUSH_INTERVAL),
                sendContext = sendContext,
                logger = logger,
                cacheDir = cacheDir,
                lifecycle = lifecycle,
            )
        }
    }
}

/** checkBaseUrl requires an http or https URL with a host unless offline, and removes trailing slashes. */
private fun checkBaseUrl(
    url: String?,
    offline: Boolean,
): String? {
    if (url == null) {
        require(offline) { "megooflags: baseUrl is required unless offline" }
        return null
    }
    val uri =
        try {
            URI(url)
        } catch (_: URISyntaxException) {
            null
        }
    val scheme = uri?.scheme?.lowercase()
    require((scheme == "http" || scheme == "https") && !uri?.host.isNullOrEmpty()) {
        "megooflags: baseUrl must be an http or https URL"
    }
    return url.trimEnd('/')
}
