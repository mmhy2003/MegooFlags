using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Engine;
using MegooFlags.Internal.Transport;

namespace MegooFlags.Internal.Delivery;

/// <summary>
/// Keeps the store current: the stream, with polling while the stream keeps failing (or polling only, with
/// DisableStream). The callbacks run on the worker tasks, one at a time, and must not await
/// <see cref="StopAsync"/>. No callback runs after StopAsync or after a fatal status.
/// </summary>
internal sealed class Sync
{
    /// <summary>
    /// The delivery state: Ready (the stream is connected, or with DisableStream the last poll succeeded), Stale
    /// (the stream or the poll failed; the last ruleset is still served) or Fatal (the key was refused; Sync has
    /// stopped).
    /// </summary>
    public enum Status
    {
        Ready,
        Stale,
        Fatal,
    }

    /// <summary>Shortened timings; only tests set them.</summary>
    public sealed class Tuning
    {
        public TimeSpan DeadTimeout { get; set; } = TimeSpan.FromSeconds(45);

        public TimeSpan StableAfter { get; set; } = TimeSpan.FromSeconds(60);

        public TimeSpan RetryBase { get; set; } = TimeSpan.FromSeconds(3);

        public TimeSpan RetryMax { get; set; } = TimeSpan.FromSeconds(60);

        /// <summary>The stream failures in a row before polling starts.</summary>
        public int PollAfterFailures { get; set; } = 2;

        /// <summary>Replaces <see cref="Options.PollInterval"/> without any clamp.</summary>
        public TimeSpan? PollInterval { get; set; }
    }

    /// <summary>The settings of a Sync.</summary>
    public sealed class Options(string baseUrl, string sdkKey, Store store)
    {
        public string BaseUrl { get; } = baseUrl;

        public string SdkKey { get; } = sdkKey;

        public Store Store { get; } = store;

        /// <summary>The client for every request. Without one, Sync creates a client without a timeout.</summary>
        public HttpClient? HttpClient { get; init; }

        /// <summary>Receives messages worth a warning.</summary>
        public Action<string>? Warn { get; init; }

        /// <summary>Receives messages worth a debug line.</summary>
        public Action<string>? Debug { get; init; }

        public bool DisableStream { get; init; }

        /// <summary>Used as given (the provider clamps it).</summary>
        public TimeSpan PollInterval { get; init; } = TimeSpan.FromSeconds(30);

        /// <summary>Receives each ruleset from the stream or a poll; none when null.</summary>
        public Cache? Cache { get; init; }

        /// <summary>A ruleset was installed; old is null the first time.</summary>
        public Action<Snapshot?, Snapshot>? OnChange { get; init; }

        /// <summary>The status changed; the error is the cause for Stale and Fatal, else null.</summary>
        public Action<Status, Exception?>? OnStatus { get; init; }

        public Tuning Tuning { get; init; } = new();
    }

    private readonly Store _store;
    private readonly Cache? _cache;
    private readonly Action<string> _warn;
    private readonly Action<string> _debug;
    private readonly Action<Snapshot?, Snapshot> _onChange;
    private readonly Action<Status, Exception?> _onStatus;
    private readonly bool _disableStream;
    private readonly TimeSpan _pollInterval;
    private readonly int _pollAfterFailures;
    private readonly HttpClient _client;
    private readonly bool _ownsClient;
    private readonly Streamer _streamer;
    private readonly Poller _poller;

    /// <summary>Cancelled by StopAsync and by a fatal status: every worker ends.</summary>
    private readonly CancellationTokenSource _halt = new();

    /// <summary>Guards the state below and runs the callbacks one at a time.</summary>
    private readonly object _lock = new();

    private readonly HashSet<string> _logged = new(StringComparer.Ordinal);
    private readonly List<Task> _workers = new();
    private bool _started;
    private bool _stopped;
    private bool _fatal;
    private Task? _stopping;

    /// <summary>Set while a poll loop runs; cancelling it ends that loop.</summary>
    private CancellationTokenSource? _pollCancel;

    /// <summary>The earliest next poll (a <see cref="Stopwatch"/> timestamp), kept across poll-loop restarts.</summary>
    private long _nextPoll = Stopwatch.GetTimestamp();

    public Sync(Options o)
    {
        _store = o.Store;
        _cache = o.Cache;
        _warn = o.Warn ?? (_ => { });
        _debug = o.Debug ?? (_ => { });
        _onChange = o.OnChange ?? ((_, _) => { });
        _onStatus = o.OnStatus ?? ((_, _) => { });
        _disableStream = o.DisableStream;
        _pollInterval = o.Tuning.PollInterval ?? o.PollInterval;
        _pollAfterFailures = o.Tuning.PollAfterFailures;
        _ownsClient = o.HttpClient == null;
        _client = o.HttpClient ?? new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        _poller = new Poller(
            _client,
            Transport.Transport.Url(o.BaseUrl, "ruleset"),
            o.SdkKey,
            ETag,
            (raw, etag) => Apply(raw, etag, SnapshotSource.Poll) == null,
            Transport.Transport.PollTimeout);
        _streamer = new Streamer(
            _client,
            Transport.Transport.Url(o.BaseUrl, "stream"),
            o.SdkKey,
            ETag,
            new StreamEvents(this),
            _warn,
            o.Tuning.DeadTimeout,
            o.Tuning.StableAfter,
            new Backoff(o.Tuning.RetryBase, o.Tuning.RetryMax));
    }

    private bool Halted => _stopped || _fatal;

    private string ETag() => _store.Load()?.ETag ?? "";

    /// <summary>
    /// Parses raw and installs it. An invalid ruleset is logged and returned, and the store keeps the previous one.
    /// A ruleset from the stream or a poll is also saved to the cache; a save error is only logged. Each ruleset
    /// warning is logged once. A stream or poll ruleset arriving after stop or a fatal status is ignored.
    /// </summary>
    public ParseException? Apply(string raw, string etag, SnapshotSource source)
    {
        var live = source is SnapshotSource.Stream or SnapshotSource.Poll;
        lock (_lock)
        {
            if (live && Halted)
            {
                return null;
            }
        }
        // Parsed outside the lock: a large ruleset must not hold up the status callbacks.
        Ruleset ruleset;
        try
        {
            ruleset = RulesetParser.Parse(raw);
        }
        catch (ParseException e)
        {
            Call(() => _warn("megooflags: invalid ruleset from " + source.Name() + "; keeping the previous one: " + e.Message));
            return e;
        }
        lock (_lock)
        {
            if (live && Halted)
            {
                return null;
            }
            var next = new Snapshot(ruleset, etag, source);
            var old = _store.Swap(next);
            if (live && _cache != null)
            {
                try
                {
                    _cache.Save(raw, etag);
                }
                catch (IOException e)
                {
                    Call(() => _warn("megooflags: could not save the ruleset cache: " + e.Message));
                }
            }
            foreach (var w in ruleset.Warnings)
            {
                if (_logged.Add(w))
                {
                    Call(() => _warn("megooflags: ruleset warning: " + w));
                }
            }
            Call(() => _onChange(old, next));
            return null;
        }
    }

    /// <summary>
    /// Starts the stream, or only the poll loop with DisableStream, and returns at once. Only the first call does
    /// anything, and none after StopAsync.
    /// </summary>
    public void Start()
    {
        lock (_lock)
        {
            if (_started || Halted)
            {
                return;
            }
            _started = true;
            if (_disableStream)
            {
                StartPolling();
                return;
            }
            Spawn(async () =>
            {
                var err = await _streamer.RunAsync(_halt.Token).ConfigureAwait(false);
                if (err != null)
                {
                    Fail(err);
                }
            });
        }
    }

    /// <summary>
    /// Cancels the stream, any poll in flight and the waits, and completes when every worker has ended. Idempotent
    /// and safe before Start.
    /// </summary>
    public Task StopAsync()
    {
        lock (_lock)
        {
            _stopped = true;
            _stopping ??= StopCoreAsync();
            return _stopping;
        }
    }

    private async Task StopCoreAsync()
    {
        // Yield so the caller's lock is released before the workers run their last steps.
        await Task.Yield();
        _halt.Cancel();
        Task[] workers;
        lock (_lock)
        {
            workers = _workers.ToArray();
        }
        // The workers catch everything, so WhenAll does not throw.
        await Task.WhenAll(workers).ConfigureAwait(false);
        if (_ownsClient)
        {
            _client.Dispose();
        }
    }

    private void OnConnected()
    {
        StopPolling();
        SetStatus(Status.Ready, null);
    }

    private void OnDisconnected(Exception err, int failures)
    {
        SetStatus(Status.Stale, err);
        if (failures >= _pollAfterFailures)
        {
            lock (_lock)
            {
                StartPolling();
            }
        }
    }

    /// <summary>Reports status unless Sync is stopping or has failed.</summary>
    private void SetStatus(Status status, Exception? err)
    {
        lock (_lock)
        {
            if (!Halted)
            {
                Call(() => _onStatus(status, err));
            }
        }
    }

    /// <summary>Reports a refused key once and stops every worker.</summary>
    private void Fail(FatalException err)
    {
        lock (_lock)
        {
            if (Halted)
            {
                return;
            }
            _fatal = true;
            Call(() => _onStatus(Status.Fatal, err));
        }
        _halt.Cancel();
    }

    /// <summary>Runs a callback and logs what it throws.</summary>
    private void Call(Action fn)
    {
        try
        {
            fn();
        }
        catch (Exception e)
        {
            try
            {
                _debug("megooflags: sync callback failed: " + e);
            }
            catch (Exception)
            {
                // The log sink failed too; nothing is left to tell.
            }
        }
    }

    /// <summary>Starts a poll loop unless one runs. Called with the lock held.</summary>
    private void StartPolling()
    {
        if (_pollCancel != null || Halted)
        {
            return;
        }
        var loop = CancellationTokenSource.CreateLinkedTokenSource(_halt.Token);
        _pollCancel = loop;
        Spawn(() => PollLoopAsync(loop));
    }

    private void StopPolling()
    {
        lock (_lock)
        {
            _pollCancel?.Cancel();
            _pollCancel = null;
        }
    }

    private async Task PollLoopAsync(CancellationTokenSource loop)
    {
        try
        {
            while (true)
            {
                long started;
                TimeSpan wait;
                lock (_lock)
                {
                    if (loop.IsCancellationRequested || Halted)
                    {
                        return;
                    }
                    started = Stopwatch.GetTimestamp();
                    wait = Stopwatch.GetElapsedTime(started, _nextPoll);
                    if (wait <= TimeSpan.Zero)
                    {
                        _nextPoll = started + Ticks(_pollInterval);
                    }
                }
                if (wait > TimeSpan.Zero)
                {
                    if (!await Backoff.SleepAsync(wait, loop.Token).ConfigureAwait(false))
                    {
                        return;
                    }
                    continue;
                }
                Exception? err = null;
                try
                {
                    await _poller.PollAsync(loop.Token).ConfigureAwait(false);
                }
                catch (Exception e)
                {
                    err = e;
                }
                if (loop.IsCancellationRequested)
                {
                    return;
                }
                if (err is FatalException fatal)
                {
                    Fail(fatal);
                    return;
                }
                if (err is RetryAfterException ra)
                {
                    lock (_lock)
                    {
                        _nextPoll = started + Ticks(ra.After > _pollInterval ? ra.After : _pollInterval);
                    }
                }
                if (err != null)
                {
                    Call(() => _debug("megooflags: poll failed: " + err.Message));
                }
                if (_disableStream)
                {
                    SetStatus(err == null ? Status.Ready : Status.Stale, err);
                }
            }
        }
        finally
        {
            lock (_lock)
            {
                if (_pollCancel == loop)
                {
                    _pollCancel = null;
                }
                loop.Dispose();
            }
        }
    }

    /// <summary>Starts a worker that logs anything it throws. Called with the lock held.</summary>
    private void Spawn(Func<Task> body)
    {
        _workers.RemoveAll(t => t.IsCompleted);
        _workers.Add(Task.Run(async () =>
        {
            try
            {
                await body().ConfigureAwait(false);
            }
            catch (Exception e)
            {
                Call(() => _debug("megooflags: sync worker failed: " + e));
            }
        }));
    }

    /// <summary>A duration in <see cref="Stopwatch"/> ticks.</summary>
    private static long Ticks(TimeSpan d) => (long)(d.TotalSeconds * Stopwatch.Frequency);

    private sealed class StreamEvents(Sync s) : Streamer.ICallbacks
    {
        public bool OnPut(string raw, string id) => s.Apply(raw, id, SnapshotSource.Stream) == null;

        public void OnConnected() => s.OnConnected();

        public void OnDisconnected(Exception error, int failures) => s.OnDisconnected(error, failures);
    }
}
