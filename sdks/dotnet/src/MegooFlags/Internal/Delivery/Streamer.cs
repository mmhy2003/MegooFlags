using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Transport;

namespace MegooFlags.Internal.Delivery;

/// <summary>A stream that sent nothing, not even a ping, within the dead-connection timeout.</summary>
internal sealed class DeadConnectionException : IOException
{
    public DeadConnectionException() : base("megooflags: stream sent nothing within the dead-connection timeout")
    {
    }
}

/// <summary>
/// The SSE client for <c>GET /sdk/v1/stream</c>. It reconnects with backoff until stopped or until the server
/// refuses the key.
/// </summary>
internal sealed class Streamer
{
    /// <summary>What the streamer reports. The callbacks run on the streamer's task, one at a time.</summary>
    public interface ICallbacks
    {
        /// <summary>Receives a put event; false means an invalid ruleset (the previous one stays).</summary>
        bool OnPut(string raw, string id);

        /// <summary>The server answered 200.</summary>
        void OnConnected();

        /// <summary>A connection attempt or a connection ended; failures counts them since the last stable one.</summary>
        void OnDisconnected(Exception error, int failures);
    }

    private readonly HttpClient _client;
    private readonly Uri _url;
    private readonly string _sdkKey;
    private readonly Func<string> _lastEventId;
    private readonly ICallbacks _cb;
    private readonly Action<string> _warn;
    private readonly TimeSpan _deadTimeout;
    private readonly TimeSpan _stableAfter;
    private readonly Backoff _backoff;

    /// <summary>
    /// A streamer. client must not have a timeout (the stream is long-lived); lastEventId gives the ETag to resume
    /// from ("" for none); warn receives messages worth a warning.
    /// </summary>
    public Streamer(
        HttpClient client,
        Uri url,
        string sdkKey,
        Func<string> lastEventId,
        ICallbacks callbacks,
        Action<string> warn,
        TimeSpan deadTimeout,
        TimeSpan stableAfter,
        Backoff backoff)
    {
        _client = client;
        _url = url;
        _sdkKey = sdkKey;
        _lastEventId = lastEventId;
        _cb = callbacks;
        _warn = warn;
        _deadTimeout = deadTimeout;
        _stableAfter = stableAfter;
        _backoff = backoff;
    }

    /// <summary>
    /// Streams until stop is cancelled, returning null, or until the server refuses the key, returning the
    /// <see cref="FatalException"/>. Throws only what OnConnected or OnDisconnected throws. Must not run
    /// concurrently with itself.
    /// </summary>
    public async Task<FatalException?> RunAsync(CancellationToken stop)
    {
        var failures = 0;
        while (true)
        {
            var (connectedAt, err) = await ConnectAsync(stop).ConfigureAwait(false);
            if (stop.IsCancellationRequested)
            {
                return null;
            }
            if (err is FatalException fatal)
            {
                return fatal;
            }
            if (connectedAt is long at && Stopwatch.GetElapsedTime(at) >= _stableAfter)
            {
                _backoff.Reset();
                failures = 0;
            }
            failures++;
            var wait = _backoff.Next();
            if (err is RetryAfterException ra && ra.After > wait)
            {
                wait = ra.After;
            }
            _cb.OnDisconnected(err, failures);
            if (!await Backoff.SleepAsync(wait, stop).ConfigureAwait(false))
            {
                return null;
            }
        }
    }

    /// <summary>
    /// Opens one stream and reads it until it ends. connectedAt (a <see cref="Stopwatch"/> timestamp) is null when
    /// the server did not answer 200; err is why the connection ended.
    /// </summary>
    private async Task<(long? ConnectedAt, Exception Err)> ConnectAsync(CancellationToken stop)
    {
        // The dead timer: cancelled after the dead timeout unless a read pushes it back.
        using var conn = CancellationTokenSource.CreateLinkedTokenSource(stop);
        long? connectedAt = null;
        try
        {
            using var req = Transport.Transport.CreateStreamRequest(_url, _sdkKey);
            var id = _lastEventId();
            if (id.Length > 0)
            {
                req.Headers.TryAddWithoutValidation("Last-Event-ID", id);
            }
            using var res = await _client
                .SendAsync(req, HttpCompletionOption.ResponseHeadersRead, conn.Token)
                .ConfigureAwait(false);
            var status = (int)res.StatusCode;
            var statusErr = Transport.Transport.StatusError(status, res.Headers, DateTimeOffset.UtcNow);
            if (statusErr != null)
            {
                return (null, statusErr);
            }
            if (status != 200)
            {
                return (null, new HttpStatusException(status));
            }
            connectedAt = Stopwatch.GetTimestamp();
            _cb.OnConnected();
            conn.CancelAfter(_deadTimeout);
            var body = await res.Content.ReadAsStreamAsync(conn.Token).ConfigureAwait(false);
            await using (body.ConfigureAwait(false))
            {
                var timed = new DeadTimerStream(body, conn, _deadTimeout);
                await SseReader.ReadAsync(timed, new Events(this), conn.Token).ConfigureAwait(false);
            }
            return (connectedAt, new IOException("megooflags: the server closed the stream"));
        }
        catch (FatalException e)
        {
            return (connectedAt, e);
        }
        catch (Exception e) when (!stop.IsCancellationRequested)
        {
            if (conn.IsCancellationRequested)
            {
                return (connectedAt, new DeadConnectionException());
            }
            return (connectedAt, e);
        }
        catch (Exception)
        {
            // Stopped: RunAsync returns.
            return (connectedAt, new OperationCanceledException(stop));
        }
    }

    /// <summary>Routes the reader's callbacks: lines are seen by the dead timer through the stream reads.</summary>
    private sealed class Events(Streamer s) : SseReader.ICallbacks
    {
        public void OnLine()
        {
        }

        public void OnRetry(long millis) => s._backoff.Base = TimeSpan.FromMilliseconds(millis);

        public void OnEvent(SseReader.Event e)
        {
            switch (e.Name)
            {
                case "put":
                    if (!s._cb.OnPut(e.Data, e.Id))
                    {
                        s._warn("megooflags: stream sent an invalid ruleset; keeping the previous one (event id " + e.Id + ")");
                    }
                    break;
                case "revoked":
                    throw FatalException.Revoked();
            }
        }
    }

    /// <summary>A read-only view of the response body that pushes the dead timer back before each read.</summary>
    private sealed class DeadTimerStream(Stream inner, CancellationTokenSource timer, TimeSpan timeout) : Stream
    {
        public override bool CanRead => true;

        public override bool CanSeek => false;

        public override bool CanWrite => false;

        public override long Length => throw new NotSupportedException();

        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }

        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            timer.CancelAfter(timeout);
            return inner.ReadAsync(buffer, cancellationToken);
        }

        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken) =>
            ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();

        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();

        public override void Flush()
        {
        }

        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

        public override void SetLength(long value) => throw new NotSupportedException();

        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
}
