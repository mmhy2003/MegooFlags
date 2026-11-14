using System;
using System.Net.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace MegooFlags;

/// <summary>
/// The settings of a <see cref="MegooFlagsProvider"/>. Only <see cref="SdkKey"/> and <see cref="BaseUrl"/> (or,
/// offline, a bootstrap file or a cache directory) are required. A zero or negative duration means the default,
/// and the intervals are clamped, so each property reads back the value the provider uses.
/// </summary>
public sealed class MegooFlagsOptions
{
    private static readonly TimeSpan _defaultInitTimeout = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan _defaultPollInterval = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan _minPollInterval = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan _defaultFlushInterval = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan _minFlushInterval = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan _maxFlushInterval = TimeSpan.FromSeconds(60);

    private readonly string _sdkKey = "";
    private readonly string _baseUrl = "";
    private readonly TimeSpan _initTimeout = _defaultInitTimeout;
    private readonly TimeSpan _pollInterval = _defaultPollInterval;
    private readonly TimeSpan _flushInterval = _defaultFlushInterval;
    private readonly ILogger _logger = NullLogger.Instance;

    /// <summary>The server SDK key (mf_server_…).</summary>
    public string SdkKey
    {
        get => _sdkKey;
        init => _sdkKey = value ?? "";
    }

    /// <summary>The base URL, for example https://flags.example.com; trailing slashes are removed.</summary>
    public string BaseUrl
    {
        get => _baseUrl;
        init => _baseUrl = (value ?? "").TrimEnd('/');
    }

    /// <summary>A ruleset JSON file read at construction, or null.</summary>
    public string? BootstrapFile { get; init; }

    /// <summary>Where the last-known ruleset is kept, or null for no cache.</summary>
    public string? CacheDir { get; init; }

    /// <summary>No network: the bootstrap file and the cache only.</summary>
    public bool Offline { get; init; }

    /// <summary>How long initialisation waits for the first ruleset (5 s).</summary>
    public TimeSpan InitTimeout
    {
        get => _initTimeout;
        init => _initTimeout = value > TimeSpan.Zero ? value : _defaultInitTimeout;
    }

    /// <summary>Poll only, without the stream.</summary>
    public bool DisableStream { get; init; }

    /// <summary>The time between polls (30 s, at least 15 s).</summary>
    public TimeSpan PollInterval
    {
        get => _pollInterval;
        init => _pollInterval = value <= TimeSpan.Zero ? _defaultPollInterval
            : value < _minPollInterval ? _minPollInterval
            : value;
    }

    /// <summary>The time between event batches (30 s, from 10 s to 60 s).</summary>
    public TimeSpan FlushInterval
    {
        get => _flushInterval;
        init => _flushInterval = value <= TimeSpan.Zero ? _defaultFlushInterval
            : value < _minFlushInterval ? _minFlushInterval
            : value > _maxFlushInterval ? _maxFlushInterval
            : value;
    }

    /// <summary>Whether exposures carry the evaluation context.</summary>
    public bool SendContext { get; init; }

    /// <summary>The logger (<see cref="NullLogger.Instance"/>).</summary>
    public ILogger Logger
    {
        get => _logger;
        init => _logger = value ?? NullLogger.Instance;
    }

    /// <summary>
    /// The client for every request, or null for clients the provider creates. It must not set a timeout: the
    /// stream is long-lived.
    /// </summary>
    public HttpClient? HttpClient { get; init; }

    /// <summary>The reason the provider cannot run with these options, or null. Never contains the key.</summary>
    internal string? Validate()
    {
        if (SdkKey.StartsWith("mf_client_", StringComparison.Ordinal))
        {
            return "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)";
        }
        if (!SdkKey.StartsWith("mf_server_", StringComparison.Ordinal))
        {
            return "megooflags: invalid SDK key; use a server key (mf_server_…)";
        }
        if (Offline)
        {
            return BootstrapFile == null && CacheDir == null
                ? "megooflags: Offline needs a BootstrapFile or a CacheDir"
                : null;
        }
        if (!Uri.TryCreate(BaseUrl, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
            || string.IsNullOrEmpty(uri.Host)
            || BaseUrl.Contains(' ', StringComparison.Ordinal))
        {
            return "megooflags: BaseUrl must be an http or https URL";
        }
        return null;
    }
}
