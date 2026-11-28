using System;
using System.Globalization;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using MegooFlags.Internal.Transport;

namespace MegooFlags.Internal.Events;

/// <summary>
/// Aggregates evaluations and posts them to <c>POST /sdk/v1/events</c> from one background task, which alone owns
/// the aggregator and the sender. <see cref="Record"/> never blocks.
/// </summary>
internal sealed class Batcher
{
    /// <summary>The largest events body the server accepts.</summary>
    public const int MaxBodyBytes = 1_000_000;

    /// <summary>The settings of a Batcher. FlushInterval is used as given (the provider clamps it).</summary>
    public sealed class Options(Uri url, string sdkKey)
    {
        /// <summary>The events URL (<c>&lt;base&gt;/sdk/v1/events</c>).</summary>
        public Uri Url { get; } = url;

        public string SdkKey { get; } = sdkKey;

        /// <summary>The client for every request. Without one, the batcher creates a client without a timeout.</summary>
        public HttpClient? HttpClient { get; set; }

        /// <summary>Receives messages worth a warning.</summary>
        public Action<string>? Warn { get; set; }

        /// <summary>Receives messages worth a debug line.</summary>
        public Action<string>? Debug { get; set; }

        public TimeSpan FlushInterval { get; set; } = TimeSpan.FromSeconds(30);

        public int QueueSize { get; set; } = 10_000;

        public int DedupSize { get; set; } = 10_000;

        public int MaxPending { get; set; } = 10_000;

        /// <summary>The most exposures, and the most count items, in one batch; reaching it in either list flushes.</summary>
        public int MaxItems { get; set; } = 1000;

        public bool SendContext { get; set; }

        /// <summary>
        /// The key was refused (401 or 403). Called once, on its own task, after sending stopped; it may call
        /// <see cref="Stop"/> and <see cref="ShutdownAsync"/>.
        /// </summary>
        public Action<FatalException>? OnFatal { get; set; }

        /// <summary>The clock and timers of the flush interval, the retry delays and the send timeout.</summary>
        public TimeProvider Time { get; set; } = TimeProvider.System;

        public TimeSpan RetryBase { get; set; } = TimeSpan.FromSeconds(2);

        public TimeSpan RetryMax { get; set; } = TimeSpan.FromSeconds(60);
    }

    private readonly Uri _url;
    private readonly string _sdkKey;
    private readonly HttpClient _client;
    private readonly bool _ownsClient;
    private readonly Action<string> _warn;
    private readonly Action<string> _debug;
    private readonly Action<FatalException> _onFatal;
    private readonly TimeProvider _time;
    private readonly TimeSpan _flushInterval;
    private readonly int _maxItems;
    private readonly Channel<Evaluation> _queue;
    private readonly Aggregator _agg;
    private readonly Backoff _backoff;

    /// <summary>Cancelled by Stop: aborts a send or a wait in flight.</summary>
    private readonly CancellationTokenSource _stop = new();

    /// <summary>Completed by the first ShutdownAsync: the loop drains, then ends.</summary>
    private readonly TaskCompletionSource _shutdown = new(TaskCreationOptions.RunContinuationsAsynchronously);

    private readonly object _lock = new();
    private long _dropped;
    private long _aggDropped;
    private bool _started;
    private bool _closed;
    private Task _loop = Task.CompletedTask;

    /// <summary>Cancelled at the shutdown deadline; set before <see cref="_shutdown"/> completes.</summary>
    private CancellationTokenSource? _deadline;

    // Owned by the loop.
    private DateTimeOffset _nextAttempt = DateTimeOffset.MinValue;
    private bool _halted;
    private bool _drainedAll = true;

    /// <summary>A batcher; nothing runs until <see cref="Start"/>.</summary>
    public Batcher(Options o)
    {
        _url = o.Url;
        _sdkKey = o.SdkKey;
        _ownsClient = o.HttpClient == null;
        _client = o.HttpClient ?? new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        _warn = o.Warn ?? (_ => { });
        _debug = o.Debug ?? (_ => { });
        _onFatal = o.OnFatal ?? (_ => { });
        _time = o.Time;
        _flushInterval = o.FlushInterval;
        _maxItems = o.MaxItems;
        _queue = Channel.CreateBounded<Evaluation>(new BoundedChannelOptions(o.QueueSize)
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
        });
        _agg = new Aggregator(o.DedupSize, o.MaxPending, o.SendContext);
        _backoff = new Backoff(o.RetryBase, o.RetryMax);
    }

    /// <summary>Evaluations dropped: queue overflows, records after Stop or ShutdownAsync, and exposures over the limit.</summary>
    public long Dropped => Interlocked.Read(ref _dropped) + Interlocked.Read(ref _aggDropped);

    /// <summary>Starts the background task. Only the first call does anything, and none after Stop or ShutdownAsync.</summary>
    public void Start()
    {
        lock (_lock)
        {
            if (_started || _closed)
            {
                return;
            }
            _started = true;
            _loop = Task.Run(LoopAsync);
        }
    }

    /// <summary>
    /// Queues ev without blocking. A full queue, or a batcher that was stopped or shut down (its queue is completed),
    /// drops ev and counts the drop.
    /// </summary>
    public void Record(in Evaluation ev)
    {
        if (!_queue.Writer.TryWrite(ev))
        {
            Interlocked.Increment(ref _dropped);
        }
    }

    /// <summary>
    /// Stops accepting, then sends what is buffered until everything is sent, a fatal status, or the timeout. Returns
    /// false when the timeout came first. Idempotent: a later call waits for the same drain with its own timeout.
    /// </summary>
    public async Task<bool> ShutdownAsync(TimeSpan timeout)
    {
        lock (_lock)
        {
            if (!_closed)
            {
                Close();
            }
            if (!_started)
            {
                return true;
            }
            if (_deadline == null)
            {
                _deadline = new CancellationTokenSource(timeout, _time);
                _shutdown.TrySetResult();
            }
        }
        try
        {
            await _loop.WaitAsync(timeout, _time).ConfigureAwait(false);
        }
        catch (TimeoutException)
        {
            return false;
        }
        return Volatile.Read(ref _drainedAll);
    }

    /// <summary>Stops without sending what is buffered and waits for the background task. Idempotent.</summary>
    public void Stop()
    {
        Task loop;
        lock (_lock)
        {
            SignalStop();
            loop = _loop;
        }
        // The loop catches everything, so the wait does not throw.
        loop.Wait();
    }

    /// <summary>Completes the queue so Record drops from now on. Called with the lock held.</summary>
    private void Close()
    {
        _closed = true;
        _queue.Writer.TryComplete();
    }

    /// <summary>Stop without the wait, so the loop can call it. Called with the lock held.</summary>
    private void SignalStop()
    {
        if (!_closed)
        {
            Close();
        }
        if (!_stop.IsCancellationRequested)
        {
            _stop.Cancel();
        }
    }

    private async Task LoopAsync()
    {
        try
        {
            var nextFlush = _time.GetUtcNow() + _flushInterval;
            while (!_halted && !_stop.IsCancellationRequested)
            {
                while (!_halted && _queue.Reader.TryRead(out var ev))
                {
                    Add(ev);
                    if (_agg.PendingExposures >= _maxItems || _agg.PendingCounts >= _maxItems)
                    {
                        await FlushAsync(_stop.Token).ConfigureAwait(false);
                    }
                }
                if (_halted || _stop.IsCancellationRequested)
                {
                    return;
                }
                if (_shutdown.Task.IsCompleted)
                {
                    await DrainAsync().ConfigureAwait(false);
                    return;
                }
                var wait = nextFlush - _time.GetUtcNow();
                if (wait <= TimeSpan.Zero)
                {
                    await FlushAsync(_stop.Token).ConfigureAwait(false);
                    nextFlush = _time.GetUtcNow() + _flushInterval;
                    continue;
                }
                await WaitAsync(wait).ConfigureAwait(false);
            }
        }
        catch (Exception e)
        {
            Log(_debug, "megooflags: events worker failed: " + e);
        }
        finally
        {
            if (_ownsClient)
            {
                _client.Dispose();
            }
        }
    }

    /// <summary>Waits until an evaluation is queued, the flush is due, shutdown begins or Stop is called.</summary>
    private async Task WaitAsync(TimeSpan wait)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(_stop.Token);
        var read = _queue.Reader.WaitToReadAsync(cts.Token).AsTask();
        var delay = Task.Delay(wait, _time, cts.Token);
        await Task.WhenAny(read, delay, _shutdown.Task).ConfigureAwait(false);
        cts.Cancel();
        // Observe the losers: they end cancelled, or read completes false once the queue is closed.
        try
        {
            await Task.WhenAll(read, delay).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Expected for every wait that did not finish first.
        }
    }

    private void Add(in Evaluation ev)
    {
        _agg.Add(ev);
        Interlocked.Exchange(ref _aggDropped, _agg.Dropped);
    }

    /// <summary>
    /// Empties the queue, then sends everything until the deadline, waiting out a retry delay when it fits.
    /// </summary>
    private async Task DrainAsync()
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(_deadline!.Token, _stop.Token);
        while (_queue.Reader.TryRead(out var ev))
        {
            Add(ev);
        }
        while (!_halted)
        {
            if (_agg.PendingExposures == 0 && _agg.PendingCounts == 0)
            {
                return;
            }
            var wait = _nextAttempt - _time.GetUtcNow();
            if (cts.IsCancellationRequested
                || (wait > TimeSpan.Zero && !await SleepAsync(wait, cts.Token).ConfigureAwait(false)))
            {
                Volatile.Write(ref _drainedAll, false);
                return;
            }
            await FlushAsync(cts.Token).ConfigureAwait(false);
        }
    }

    private async Task<bool> SleepAsync(TimeSpan d, CancellationToken ct)
    {
        try
        {
            await Task.Delay(d, _time, ct).ConfigureAwait(false);
            return true;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
    }

    /// <summary>
    /// Sends pending items in batches of at most MaxItems per list. Does nothing before the next attempt is due and
    /// stops at the first failure.
    /// </summary>
    private async Task FlushAsync(CancellationToken ct)
    {
        if (_time.GetUtcNow() < _nextAttempt)
        {
            return;
        }
        while (!_halted)
        {
            if (_agg.PendingExposures == 0 && _agg.PendingCounts == 0)
            {
                return;
            }
            var unsent = await SendAsync(_agg.Take(_maxItems), ct).ConfigureAwait(false);
            if (unsent != null)
            {
                _agg.Restore(unsent);
                return;
            }
        }
    }

    /// <summary>
    /// Posts batch, splitting it when it is too large. Returns the part that was not delivered and must be retried,
    /// or null when nothing is to be retried (sent, dropped, or sending stopped for good).
    /// </summary>
    private async Task<Batch?> SendAsync(Batch batch, CancellationToken ct)
    {
        byte[] body;
        try
        {
            body = batch.ToJson();
        }
        catch (JsonException e)
        {
            Log(_warn, "megooflags: cannot encode events; dropping the batch: " + e.Message);
            return null;
        }
        if (body.Length > MaxBodyBytes)
        {
            return await SplitAsync(batch, "the body is over 1 MB", ct).ConfigureAwait(false);
        }

        TransportException? err;
        using (var timer = new CancellationTokenSource(Transport.Transport.EventsTimeout, _time))
        using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct, timer.Token))
        {
            try
            {
                using var req = Transport.Transport.CreateRequest(HttpMethod.Post, _url, _sdkKey, body);
                using var res = await _client
                    .SendAsync(req, HttpCompletionOption.ResponseHeadersRead, timeout.Token)
                    .ConfigureAwait(false);
                err = Transport.Transport.StatusError((int)res.StatusCode, res.Headers, _time.GetUtcNow());
            }
            catch (Exception e) when (e is HttpRequestException or OperationCanceledException or System.IO.IOException)
            {
                RetryIn(_backoff.Next());
                Log(_debug, "megooflags: sending events failed; will retry: " + e.Message);
                return batch;
            }
        }

        switch (err)
        {
            case null:
                _backoff.Reset();
                return null;
            case FatalException fatal:
                _halted = true;
                lock (_lock)
                {
                    SignalStop();
                }
                // On its own task, so it may call Stop or ShutdownAsync, which wait for this loop.
                _ = Task.Run(() =>
                {
                    try
                    {
                        _onFatal(fatal);
                    }
                    catch (Exception e)
                    {
                        Log(_debug, "megooflags: events OnFatal failed: " + e);
                    }
                });
                return null;
            case RetryAfterException limited:
                var next = _backoff.Next();
                RetryIn(limited.After > next ? limited.After : next);
                Log(_debug, "megooflags: events rate limited; will retry after "
                    + limited.After.TotalSeconds.ToString("0.###", CultureInfo.InvariantCulture) + "s");
                return batch;
            case HttpStatusException { Status: 413 }:
                return await SplitAsync(batch, "the server refused the body as too large", ct).ConfigureAwait(false);
            case HttpStatusException { Status: >= 500 } http:
                RetryIn(_backoff.Next());
                Log(_debug, "megooflags: sending events failed with HTTP " + http.Status + "; will retry");
                return batch;
            default:
                Log(_warn, "megooflags: the server refused an events batch; dropping it (" + err.Message + ", "
                    + batch.Exposures.Count + " exposures, " + batch.Counts.Count + " counts)");
                return null;
        }
    }

    /// <summary>
    /// Sends batch as two halves, exposures first. A single item cannot be split and is dropped. When the first half
    /// must be retried, the second is kept unsent too.
    /// </summary>
    private async Task<Batch?> SplitAsync(Batch batch, string why, CancellationToken ct)
    {
        if (batch.Items <= 1)
        {
            Log(_warn, "megooflags: an events item is too large; dropping it (" + why + ")");
            return null;
        }
        var half = batch.Items / 2;
        var ne = Math.Min(half, batch.Exposures.Count);
        var nc = half - ne;
        var first = new Batch(batch.Exposures.GetRange(0, ne), batch.Counts.GetRange(0, nc));
        var second = new Batch(
            batch.Exposures.GetRange(ne, batch.Exposures.Count - ne),
            batch.Counts.GetRange(nc, batch.Counts.Count - nc));
        var unsent = await SendAsync(first, ct).ConfigureAwait(false);
        if (_halted)
        {
            return null;
        }
        if (unsent != null)
        {
            unsent.Exposures.AddRange(second.Exposures);
            unsent.Counts.AddRange(second.Counts);
            return unsent;
        }
        return await SendAsync(second, ct).ConfigureAwait(false);
    }

    private void RetryIn(TimeSpan d) => _nextAttempt = _time.GetUtcNow() + d;

    private static void Log(Action<string> sink, string message)
    {
        try
        {
            sink(message);
        }
        catch (Exception)
        {
            // The log sink failed; nothing is left to tell.
        }
    }
}
