using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using MegooFlags.Internal;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Engine;
using MegooFlags.Internal.Events;
using MegooFlags.Internal.Json;
using MegooFlags.Internal.Transport;
using Microsoft.Extensions.Logging;
using OpenFeature;
using OpenFeature.Constant;
using OpenFeature.Error;
using OpenFeature.Model;

namespace MegooFlags;

/// <summary>
/// The MegooFlags OpenFeature provider. It evaluates flags against the ruleset it holds, with no network call per
/// evaluation, and keeps that ruleset current over the stream (or by polling).
/// </summary>
/// <example>
/// <code>
/// await Api.Instance.SetProviderAsync(new MegooFlagsProvider(new MegooFlagsOptions
/// {
///     SdkKey = "mf_server_…",
///     BaseUrl = "https://flags.example.com",
/// }));
/// </code>
/// </example>
public sealed class MegooFlagsProvider : FeatureProvider
{
    /// <summary>The SDK version.</summary>
    public const string Version = BuildInfo.Version;

    private const string Name = "MegooFlags";

    /// <summary>Bounds the final events flush.</summary>
    private static readonly TimeSpan _shutdownTimeout = TimeSpan.FromSeconds(5);

    private static readonly Metadata _metadata = new(Name);

    private readonly MegooFlagsOptions _options;
    private readonly ILogger _logger;

    /// <summary>Why the options are invalid, or null.</summary>
    private readonly string? _configError;

    private readonly Store _store = new();
    private readonly Sync? _sync;
    private readonly Batcher? _batcher;

    /// <summary>
    /// Events wait here and one pump task writes them to <see cref="FeatureProvider.EventChannel"/>, whose reader runs
    /// the handlers: a slow handler never holds up the sync callbacks.
    /// </summary>
    private readonly Channel<ProviderEventPayload> _events =
        Channel.CreateUnbounded<ProviderEventPayload>(new UnboundedChannelOptions { SingleReader = true });

    private readonly CancellationTokenSource _closed = new();
    private readonly Task _pump;

    /// <summary>Completed by the first ruleset, a refused key or shutdown: initialisation stops waiting.</summary>
    private readonly TaskCompletionSource _wake = new(TaskCreationOptions.RunContinuationsAsynchronously);

    /// <summary>Guards the state below.</summary>
    private readonly object _lock = new();

    private int _fatalOnce;
    private bool _started;

    /// <summary>Set by ShutdownAsync: nothing is emitted from then on.</summary>
    private volatile bool _shutDown;

    private Task? _shutdown;

    /// <summary>The refused-key message, once the key was refused.</summary>
    private volatile string? _fatalMessage;

    /// <summary>How many InitializeAsync calls wait for the first ruleset.</summary>
    private int _waiting;

    /// <summary>InitializeAsync gave up waiting for the first ruleset.</summary>
    private bool _initTimedOut;

    /// <summary>STALE was emitted and not yet followed by READY.</summary>
    private bool _stale;

    /// <summary>
    /// Validates options and loads the bootstrap file, then (when that gave no ruleset) the cache. It never touches
    /// the network. Invalid options are logged, and every evaluation then returns the default with PROVIDER_FATAL.
    /// </summary>
    public MegooFlagsProvider(MegooFlagsOptions options) : this(options, null)
    {
    }

    /// <summary>A provider with shortened sync timings; only tests pass them.</summary>
    internal MegooFlagsProvider(MegooFlagsOptions options, Sync.Tuning? tuning)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _logger = options.Logger;
        _pump = Task.Run(PumpAsync);
        _configError = options.Validate();
        if (_configError != null)
        {
            _logger.LogError("megooflags: invalid options: {Reason}", _configError);
            return;
        }
        var cache = new Cache(options.CacheDir, options.SdkKey);
        // Offline uses Sync only to apply the bootstrap file and the cache; InitializeAsync never starts it.
        _sync = new Sync(new Sync.Options(options.BaseUrl, options.SdkKey, _store)
        {
            HttpClient = options.HttpClient,
            Warn = Warn,
            Debug = m => _logger.LogDebug("{Message}", m),
            DisableStream = options.DisableStream,
            PollInterval = options.PollInterval,
            Cache = cache,
            OnChange = OnChange,
            OnStatus = OnStatus,
            Tuning = tuning ?? new Sync.Tuning(),
        });
        if (!options.Offline)
        {
            _batcher = new Batcher(new Batcher.Options(Transport.Url(options.BaseUrl, "events"), options.SdkKey)
            {
                HttpClient = options.HttpClient,
                Warn = Warn,
                Debug = m => _logger.LogDebug("{Message}", m),
                FlushInterval = options.FlushInterval,
                SendContext = options.SendContext,
                OnFatal = OnFatal,
            });
        }
        if (options.BootstrapFile != null)
        {
            try
            {
                _sync.Apply(File.ReadAllText(options.BootstrapFile, Encoding.UTF8), "", SnapshotSource.Bootstrap);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                Warn("megooflags: could not read the bootstrap file: " + e.Message);
            }
        }
        if (_store.Load() == null)
        {
            try
            {
                var entry = cache.Load();
                if (entry != null)
                {
                    _sync.Apply(JsonTree.Write(entry.Ruleset), entry.ETag, SnapshotSource.Cache);
                }
            }
            catch (IOException e)
            {
                Warn("megooflags: could not read the ruleset cache: " + e.Message);
            }
        }
    }

    /// <inheritdoc />
    public override Metadata GetMetadata() => _metadata;

    /// <summary>
    /// Starts the event sender and, unless offline, the ruleset sync. With a ruleset from the bootstrap file or the
    /// cache it returns at once and the network refreshes it in the background. Otherwise it waits up to
    /// InitTimeout for the first ruleset; on timeout it throws <see cref="GeneralException"/>, keeps retrying and
    /// emits PROVIDER_READY when a ruleset arrives. Invalid options or a refused key throw
    /// <see cref="ProviderFatalException"/>.
    /// </summary>
    public override async Task InitializeAsync(EvaluationContext context, CancellationToken cancellationToken = default)
    {
        if (_configError != null)
        {
            throw new ProviderFatalException(_configError);
        }
        lock (_lock)
        {
            if (_shutDown)
            {
                throw new GeneralException("megooflags: the provider was shut down");
            }
            if (_fatalMessage is { } fatal)
            {
                throw new ProviderFatalException(fatal);
            }
            if (!_started)
            {
                _started = true;
                _batcher?.Start();
                if (!_options.Offline)
                {
                    _sync!.Start();
                }
            }
        }
        if (_store.Load() != null)
        {
            return;
        }
        if (_options.Offline)
        {
            throw new GeneralException(
                "megooflags: offline, and neither the bootstrap file nor the cache gave a valid ruleset");
        }
        await AwaitFirstRulesetAsync(cancellationToken).ConfigureAwait(false);
    }

    private async Task AwaitFirstRulesetAsync(CancellationToken cancellationToken)
    {
        lock (_lock)
        {
            _waiting++;
        }
        try
        {
            await _wake.Task.WaitAsync(_options.InitTimeout, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception e) when (e is TimeoutException or OperationCanceledException)
        {
            lock (_lock)
            {
                if (_store.Load() == null && _fatalMessage == null && !_shutDown)
                {
                    // OnChange reads this under the lock after the store has the first ruleset, so READY follows.
                    _initTimedOut = true;
                    if (e is OperationCanceledException)
                    {
                        throw;
                    }
                    throw new GeneralException(
                        "megooflags: no ruleset within "
                        + ((long)_options.InitTimeout.TotalMilliseconds).ToString(CultureInfo.InvariantCulture) + " ms");
                }
            }
        }
        finally
        {
            lock (_lock)
            {
                _waiting--;
            }
        }
        if (_store.Load() != null)
        {
            return;
        }
        if (_fatalMessage is { } fatal)
        {
            throw new ProviderFatalException(fatal);
        }
        throw new GeneralException("megooflags: the provider was shut down");
    }

    /// <summary>
    /// Stops the sync and sends the buffered events within 5 s. Idempotent: a later call waits for the same
    /// shutdown. The last ruleset keeps being served.
    /// </summary>
    public override Task ShutdownAsync(CancellationToken cancellationToken = default)
    {
        lock (_lock)
        {
            if (_shutdown == null)
            {
                _shutDown = true;
                _shutdown = ShutdownCoreAsync();
            }
            return _shutdown;
        }
    }

    private async Task ShutdownCoreAsync()
    {
        // Yield so the caller's lock is released first.
        await Task.Yield();
        _wake.TrySetResult();
        _events.Writer.TryComplete();
        _closed.Cancel();
        if (_sync != null)
        {
            await _sync.StopAsync().ConfigureAwait(false);
        }
        if (_batcher != null && !await _batcher.ShutdownAsync(_shutdownTimeout).ConfigureAwait(false))
        {
            Warn("megooflags: could not send all events before shutdown");
        }
        await _pump.ConfigureAwait(false);
    }

    /// <inheritdoc />
    public override Task<ResolutionDetails<bool>> ResolveBooleanValueAsync(
        string flagKey, bool defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default) =>
        Task.FromResult(Resolve(flagKey, defaultValue, context, "boolean", v => (bool)v!));

    /// <inheritdoc />
    public override Task<ResolutionDetails<string>> ResolveStringValueAsync(
        string flagKey, string defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default) =>
        Task.FromResult(Resolve(flagKey, defaultValue, context, "string", v => (string)v!));

    /// <summary>
    /// Resolves a number flag as an int. A served number that is not integral, or is outside the int range, is the
    /// default with TYPE_MISMATCH.
    /// </summary>
    public override Task<ResolutionDetails<int>> ResolveIntegerValueAsync(
        string flagKey, int defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default)
    {
        var res = Evaluate(flagKey, defaultValue, context, "number");
        if (res == null)
        {
            return Task.FromResult(NotReady(flagKey, defaultValue));
        }
        if (res.ErrorCode != null || res.Variant == null)
        {
            return Task.FromResult(Details(flagKey, defaultValue, res));
        }
        if (ToInt(res.Value) is not int v)
        {
            return Task.FromResult(new ResolutionDetails<int>(
                flagKey,
                defaultValue,
                ErrorType.TypeMismatch,
                Reason.Error,
                res.Variant,
                "megooflags: flag '" + flagKey + "' is not an integer in the int range",
                FlagMetadata(res)));
        }
        return Task.FromResult(Details(flagKey, v, res));
    }

    /// <inheritdoc />
    public override Task<ResolutionDetails<double>> ResolveDoubleValueAsync(
        string flagKey, double defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default) =>
        Task.FromResult(Resolve(
            flagKey, defaultValue, context, "number", v => Convert.ToDouble(v, CultureInfo.InvariantCulture)));

    /// <summary>Resolves a flag of any type as a Value; json flags are the intended use. Numbers are doubles.</summary>
    public override Task<ResolutionDetails<Value>> ResolveStructureValueAsync(
        string flagKey, Value defaultValue, EvaluationContext? context = null, CancellationToken cancellationToken = default) =>
        Task.FromResult(Resolve(flagKey, defaultValue, context, "json", ToValue));

    /// <summary>The engine's result mapped to T: the served value converted, else the caller's default.</summary>
    private ResolutionDetails<T> Resolve<T>(
        string flagKey, T defaultValue, EvaluationContext? context, string expectedType, Func<object?, T> convert)
    {
        var res = Evaluate(flagKey, defaultValue, context, expectedType);
        if (res == null)
        {
            return NotReady(flagKey, defaultValue);
        }
        var value = res.ErrorCode == null && res.Variant != null ? convert(res.Value) : defaultValue;
        return Details(flagKey, value, res);
    }

    /// <summary>
    /// Runs the engine on the current ruleset, or returns null when there is none yet. The context is copied, with
    /// the targeting key as the key attribute. No I/O and no lock.
    /// </summary>
    private Result? Evaluate(string flagKey, object? defaultValue, EvaluationContext? context, string expectedType)
    {
        var snap = _store.Load();
        if (snap == null)
        {
            return null;
        }
        var attrs = Attributes(context);
        var res = Evaluator.Evaluate(snap.Ruleset, flagKey, attrs, defaultValue, expectedType);
        Record(flagKey, res, attrs);
        return res;
    }

    /// <summary>Queues the evaluation for the batcher without blocking.</summary>
    private void Record(string flagKey, Result res, Dictionary<string, object?> attrs)
    {
        if (_batcher == null)
        {
            return;
        }
        var served = res.Variant != null && res.ErrorCode == null;
        _batcher.Record(new Evaluation(
            flagKey,
            served ? res.Variant! : "",
            served,
            res.Reason,
            attrs.TryGetValue("key", out var key) && key is string s ? s : "",
            _options.SendContext ? (Dictionary<string, object?>)JsonCopy(attrs)! : null,
            DateTimeOffset.UtcNow));
    }

    /// <summary>The context attributes as the engine's JSON tree; a date stays a DateTime and matches nothing.</summary>
    private static Dictionary<string, object?> Attributes(EvaluationContext? context)
    {
        var attrs = new Dictionary<string, object?>(StringComparer.Ordinal);
        if (context == null)
        {
            return attrs;
        }
        foreach (var kv in context.AsDictionary())
        {
            if (kv.Key != "targetingKey")
            {
                attrs[kv.Key] = Unwrap(kv.Value);
            }
        }
        if (context.TargetingKey is { } targetingKey)
        {
            attrs["key"] = targetingKey;
        }
        return attrs;
    }

    private static object? Unwrap(Value? v)
    {
        if (v == null || v.IsNull)
        {
            return null;
        }
        if (v.IsBoolean)
        {
            return v.AsBoolean;
        }
        if (v.IsString)
        {
            return v.AsString;
        }
        if (v.IsNumber)
        {
            return v.AsDouble;
        }
        if (v.IsList)
        {
            return v.AsList!.Select(Unwrap).ToList();
        }
        if (v.IsStructure)
        {
            var m = new Dictionary<string, object?>(StringComparer.Ordinal);
            foreach (var kv in v.AsStructure!)
            {
                m[kv.Key] = Unwrap(kv.Value);
            }
            return m;
        }
        return v.AsObject;
    }

    /// <summary>A copy of the context the events body can hold: a DateTime becomes its RFC 3339 UTC text.</summary>
    private static object? JsonCopy(object? v) => v switch
    {
        Dictionary<string, object?> m => m.ToDictionary(kv => kv.Key, kv => JsonCopy(kv.Value), StringComparer.Ordinal),
        List<object?> items => items.Select(JsonCopy).ToList(),
        DateTime t => (t.Kind == DateTimeKind.Local ? t.ToUniversalTime() : t)
            .ToString("yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'", CultureInfo.InvariantCulture),
        _ => v,
    };

    /// <summary>The result while there is no ruleset.</summary>
    private ResolutionDetails<T> NotReady<T>(string flagKey, T defaultValue)
    {
        var fatal = _configError ?? _fatalMessage;
        return new ResolutionDetails<T>(
            flagKey,
            defaultValue,
            fatal != null ? ErrorType.ProviderFatal : ErrorType.ProviderNotReady,
            Reason.Error,
            null,
            fatal ?? "megooflags: no ruleset is loaded yet");
    }

    /// <summary>value with the engine's variant, reason (PREREQUISITE_FAILED passes through), error and metadata.</summary>
    private static ResolutionDetails<T> Details<T>(string flagKey, T value, Result res) =>
        new(
            flagKey,
            value,
            res.ErrorCode == null ? ErrorType.None : ToErrorType(res.ErrorCode),
            res.Reason,
            res.Variant,
            res.ErrorCode == null ? null : res.ErrorMessage,
            FlagMetadata(res));

    private static ImmutableMetadata FlagMetadata(Result res)
    {
        var m = new Dictionary<string, object>();
        if (res.RuleIndex is int i)
        {
            m["rule_index"] = i;
        }
        if (res.Prerequisite != null)
        {
            m["prerequisite"] = res.Prerequisite;
        }
        return new ImmutableMetadata(m);
    }

    private static ErrorType ToErrorType(string code) => code switch
    {
        ErrorCodes.FlagNotFound => ErrorType.FlagNotFound,
        ErrorCodes.TypeMismatch => ErrorType.TypeMismatch,
        ErrorCodes.ParseError => ErrorType.ParseError,
        ErrorCodes.InvalidContext => ErrorType.InvalidContext,
        _ => ErrorType.General,
    };

    /// <summary>The int of an integral number in the int range, or null.</summary>
    private static int? ToInt(object? v) => v switch
    {
        long l when l is >= int.MinValue and <= int.MaxValue => (int)l,
        double d when d == Math.Floor(d) && d is >= int.MinValue and <= int.MaxValue => (int)d,
        _ => null,
    };

    /// <summary>
    /// Converts a served value (the ruleset's read-only JSON tree) to a Value. Value holds every number as a double.
    /// </summary>
    private static Value ToValue(object? v) => v switch
    {
        null => new Value(),
        string s => new Value(s),
        bool b => new Value(b),
        long l => new Value((double)l),
        double d => new Value(d),
        IReadOnlyDictionary<string, object?> m =>
            new Value(new Structure(m.ToDictionary(kv => kv.Key, kv => ToValue(kv.Value), StringComparer.Ordinal))),
        IReadOnlyList<object?> items => new Value(items.Select(ToValue).ToList()),
        _ => throw new InvalidOperationException("megooflags: unexpected JSON value " + v.GetType().Name),
    };

    /// <summary>
    /// Runs after each apply. The first ruleset ends the wait in InitializeAsync (and emits READY when that wait had
    /// timed out); a later one is a configuration change.
    /// </summary>
    private void OnChange(Snapshot? old, Snapshot next)
    {
        if (old == null)
        {
            bool timedOut;
            lock (_lock)
            {
                timedOut = _initTimedOut;
            }
            _wake.TrySetResult();
            if (timedOut)
            {
                Emit(ProviderEventTypes.ProviderReady, "megooflags: ruleset loaded");
            }
            return;
        }
        Emit(ProviderEventTypes.ProviderConfigurationChanged, "megooflags: ruleset updated",
            flagsChanged: FlagChanges(old.Ruleset, next.Ruleset));
    }

    /// <summary>Maps the delivery status to events: STALE while disconnected with a ruleset, READY on reconnect.</summary>
    private void OnStatus(Sync.Status status, Exception? err)
    {
        switch (status)
        {
            case Sync.Status.Stale:
                {
                    bool emit;
                    lock (_lock)
                    {
                        emit = !_stale && _store.Load() != null && _fatalMessage == null;
                        _stale |= emit;
                    }
                    if (emit)
                    {
                        Emit(ProviderEventTypes.ProviderStale,
                            "megooflags: disconnected; serving the last ruleset" + (err == null ? "" : ": " + err.Message));
                    }
                    return;
                }
            case Sync.Status.Ready:
                {
                    bool emit;
                    lock (_lock)
                    {
                        emit = _stale;
                        _stale = false;
                    }
                    if (emit)
                    {
                        Emit(ProviderEventTypes.ProviderReady, "megooflags: reconnected");
                    }
                    return;
                }
            default:
                OnFatal(err);
                return;
        }
    }

    /// <summary>
    /// Handles a refused key once: stops event sending and the sync and emits PROVIDER_ERROR with PROVIDER_FATAL. A
    /// waiting InitializeAsync throws ProviderFatalException instead of the event. The last ruleset keeps being
    /// served.
    /// </summary>
    private void OnFatal(Exception? err)
    {
        if (Interlocked.Exchange(ref _fatalOnce, 1) == 1)
        {
            return;
        }
        var message = err?.Message ?? "megooflags: the SDK key was refused";
        bool initWaits;
        lock (_lock)
        {
            _fatalMessage = message;
            initWaits = _waiting > 0;
        }
        _wake.TrySetResult();
        _logger.LogError("megooflags: the server refused the SDK key; serving the last ruleset: {Reason}", message);
        _batcher?.Stop();
        // OnFatal may run on a Sync worker, which StopAsync waits for: start it without waiting.
        _ = _sync?.StopAsync();
        if (!initWaits)
        {
            Emit(ProviderEventTypes.ProviderError, message, ErrorType.ProviderFatal);
        }
    }

    /// <summary>Queues an event for the pump unless the provider was shut down. Never blocks.</summary>
    private void Emit(ProviderEventTypes type, string message, ErrorType? errorType = null, List<string>? flagsChanged = null)
    {
        if (_shutDown)
        {
            return;
        }
        _events.Writer.TryWrite(new ProviderEventPayload
        {
            ProviderName = Name,
            Type = type,
            Message = message,
            ErrorType = errorType,
            FlagsChanged = flagsChanged,
        });
    }

    /// <summary>Moves queued events into the OpenFeature event channel until shutdown.</summary>
    private async Task PumpAsync()
    {
        try
        {
            while (await _events.Reader.WaitToReadAsync(_closed.Token).ConfigureAwait(false))
            {
                while (_events.Reader.TryRead(out var ev))
                {
                    await EventChannel.Writer.WriteAsync(ev, _closed.Token).ConfigureAwait(false);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Shutdown: queued events are dropped.
        }
        catch (ChannelClosedException)
        {
            // The OpenFeature SDK no longer listens to this provider.
        }
    }

    /// <summary>The sorted keys of the flags that were added, removed, or have a different version.</summary>
    private static List<string> FlagChanges(Ruleset old, Ruleset next)
    {
        var keys = new List<string>();
        foreach (var (key, flag) in next.Flags)
        {
            if (!old.Flags.TryGetValue(key, out var before) || before.Version != flag.Version)
            {
                keys.Add(key);
            }
        }
        keys.AddRange(old.Flags.Keys.Where(k => !next.Flags.ContainsKey(k)));
        keys.Sort(StringComparer.Ordinal);
        return keys;
    }

    private void Warn(string message) => _logger.LogWarning("{Message}", message);
}
