package io.github.mmhy2003.megooflags;

import java.net.URI;
import java.net.URISyntaxException;
import java.net.http.HttpClient;
import java.nio.file.Path;
import java.time.Duration;

/**
 * The settings of a {@link MegooFlagsProvider}. Only sdkKey and baseUrl (or, offline, a bootstrap
 * file or a cache directory) are required. Immutable; build one with {@link #builder()}.
 */
public final class MegooFlagsConfig {
    private static final Duration DEFAULT_INIT_TIMEOUT = Duration.ofSeconds(5);
    private static final Duration DEFAULT_POLL_INTERVAL = Duration.ofSeconds(30);
    private static final Duration MIN_POLL_INTERVAL = Duration.ofSeconds(15);
    private static final Duration DEFAULT_FLUSH_INTERVAL = Duration.ofSeconds(30);
    private static final Duration MIN_FLUSH_INTERVAL = Duration.ofSeconds(10);
    private static final Duration MAX_FLUSH_INTERVAL = Duration.ofSeconds(60);

    private final String sdkKey;
    private final String baseUrl;
    private final Path bootstrapFile;
    private final Path cacheDir;
    private final boolean offline;
    private final Duration initTimeout;
    private final boolean disableStream;
    private final Duration pollInterval;
    private final Duration flushInterval;
    private final boolean sendContext;
    private final System.Logger logger;
    private final HttpClient httpClient;

    private MegooFlagsConfig(Builder b) {
        sdkKey = b.sdkKey == null ? "" : b.sdkKey;
        baseUrl = b.baseUrl == null ? "" : b.baseUrl.replaceAll("/+$", "");
        bootstrapFile = b.bootstrapFile;
        cacheDir = b.cacheDir;
        offline = b.offline;
        initTimeout = orDefault(b.initTimeout, DEFAULT_INIT_TIMEOUT);
        disableStream = b.disableStream;
        pollInterval = max(orDefault(b.pollInterval, DEFAULT_POLL_INTERVAL), MIN_POLL_INTERVAL);
        flushInterval =
                min(max(orDefault(b.flushInterval, DEFAULT_FLUSH_INTERVAL), MIN_FLUSH_INTERVAL), MAX_FLUSH_INTERVAL);
        sendContext = b.sendContext;
        logger = b.logger != null ? b.logger : System.getLogger("io.github.mmhy2003.megooflags");
        httpClient = b.httpClient;
    }

    private static Duration orDefault(Duration d, Duration dflt) {
        return d == null || d.isZero() || d.isNegative() ? dflt : d;
    }

    private static Duration max(Duration a, Duration b) {
        return a.compareTo(b) >= 0 ? a : b;
    }

    private static Duration min(Duration a, Duration b) {
        return a.compareTo(b) <= 0 ? a : b;
    }

    public static Builder builder() {
        return new Builder();
    }

    /** The SDK key (mf_server_…). */
    public String sdkKey() {
        return sdkKey;
    }

    /** The base URL without trailing slashes, for example https://flags.example.com. */
    public String baseUrl() {
        return baseUrl;
    }

    /** A ruleset JSON file read at construction, or null. */
    public Path bootstrapFile() {
        return bootstrapFile;
    }

    /** Where the last-known ruleset is kept, or null for no cache. */
    public Path cacheDir() {
        return cacheDir;
    }

    /** No network: the bootstrap file and the cache only. */
    public boolean offline() {
        return offline;
    }

    /** How long initialisation waits for the first ruleset (5 s). */
    public Duration initTimeout() {
        return initTimeout;
    }

    /** Poll only, without the stream. */
    public boolean disableStream() {
        return disableStream;
    }

    /** The time between polls (30 s, at least 15 s). */
    public Duration pollInterval() {
        return pollInterval;
    }

    /** The time between event batches (30 s, from 10 s to 60 s). */
    public Duration flushInterval() {
        return flushInterval;
    }

    /** Whether exposures carry the evaluation context. */
    public boolean sendContext() {
        return sendContext;
    }

    /** The logger (System.getLogger("io.github.mmhy2003.megooflags")). */
    public System.Logger logger() {
        return logger;
    }

    /**
     * The client for every request, or null for a default client. It must not set a request
     * timeout: the stream is long-lived.
     */
    public HttpClient httpClient() {
        return httpClient;
    }

    /** The reason the provider cannot run with this config, or null. Never contains the key. */
    String validate() {
        if (sdkKey.startsWith("mf_client_")) {
            return "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)";
        }
        if (!sdkKey.startsWith("mf_server_")) {
            return "megooflags: invalid SDK key; use a server key (mf_server_…)";
        }
        if (offline) {
            return bootstrapFile == null && cacheDir == null
                    ? "megooflags: offline needs a bootstrapFile or a cacheDir"
                    : null;
        }
        String badUrl = "megooflags: baseUrl must be an http or https URL";
        URI uri;
        try {
            uri = new URI(baseUrl);
        } catch (URISyntaxException e) {
            return badUrl;
        }
        String scheme = uri.getScheme();
        if (!("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) || uri.getHost() == null) {
            return badUrl;
        }
        return null;
    }

    /** Builds a {@link MegooFlagsConfig}; null, zero and negative durations mean the default. */
    public static final class Builder {
        private String sdkKey;
        private String baseUrl;
        private Path bootstrapFile;
        private Path cacheDir;
        private boolean offline;
        private Duration initTimeout;
        private boolean disableStream;
        private Duration pollInterval;
        private Duration flushInterval;
        private boolean sendContext;
        private System.Logger logger;
        private HttpClient httpClient;

        private Builder() {}

        public Builder sdkKey(String key) {
            sdkKey = key;
            return this;
        }

        public Builder baseUrl(String url) {
            baseUrl = url;
            return this;
        }

        public Builder bootstrapFile(Path file) {
            bootstrapFile = file;
            return this;
        }

        public Builder cacheDir(Path dir) {
            cacheDir = dir;
            return this;
        }

        public Builder offline(boolean b) {
            offline = b;
            return this;
        }

        public Builder initTimeout(Duration d) {
            initTimeout = d;
            return this;
        }

        public Builder disableStream(boolean b) {
            disableStream = b;
            return this;
        }

        public Builder pollInterval(Duration d) {
            pollInterval = d;
            return this;
        }

        public Builder flushInterval(Duration d) {
            flushInterval = d;
            return this;
        }

        public Builder sendContext(boolean b) {
            sendContext = b;
            return this;
        }

        public Builder logger(System.Logger l) {
            logger = l;
            return this;
        }

        public Builder httpClient(HttpClient c) {
            httpClient = c;
            return this;
        }

        public MegooFlagsConfig build() {
            return new MegooFlagsConfig(this);
        }
    }
}
