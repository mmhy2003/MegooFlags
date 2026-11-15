using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace MegooFlags.Tests;

/// <summary>
/// A scripted HTTP server on 127.0.0.1 for the delivery, events and provider tests. Each path answers from its
/// queue of replies; the last reply of a queue repeats, and a path without replies gets 404.
/// </summary>
public sealed class FakeServer : IDisposable
{
    /// <summary>A reply: a canned response, a held-open event stream, or a function of the request.</summary>
    public abstract class Reply
    {
        private protected Reply()
        {
        }

        /// <summary>A canned response with headers and a body.</summary>
        public static Reply Of(int status, IReadOnlyDictionary<string, string> headers, string body) =>
            new Scripted(status, headers, body);

        /// <summary>A canned response without headers.</summary>
        public static Reply Of(int status, string body) => new Scripted(status, _noHeaders, body);

        /// <summary>An empty response.</summary>
        public static Reply Status(int status) => new Scripted(status, _noHeaders, "");

        /// <summary>
        /// A text/event-stream response held open; the test writes to it. It starts with one comment line
        /// (<c>:</c>): HttpListener sends the status and headers only with the first bytes of the body.
        /// </summary>
        public static Reply Stream() => new StreamReply();

        /// <summary>A response the function picks for each request; it may block to hold the reply back.</summary>
        public static Reply Respond(Func<Received, Reply> fn) => new Responder(fn);
    }

    private static readonly IReadOnlyDictionary<string, string> _noHeaders = new Dictionary<string, string>();

    private sealed class Scripted(int status, IReadOnlyDictionary<string, string> headers, string body) : Reply
    {
        public int StatusCode { get; } = status;

        public IReadOnlyDictionary<string, string> Headers { get; } = headers;

        public string Body { get; } = body;
    }

    private sealed class StreamReply : Reply
    {
    }

    private sealed class Responder(Func<Received, Reply> fn) : Reply
    {
        public Func<Received, Reply> Fn { get; } = fn;
    }

    /// <summary>A request the server received, with its whole body.</summary>
    public sealed class Received(string method, string path, string uri, NameValueCollection headers, string body, DateTimeOffset at)
    {
        public string Method { get; } = method;

        public string Path { get; } = path;

        /// <summary>The raw request URI: path and query.</summary>
        public string Uri { get; } = uri;

        public NameValueCollection Headers { get; } = headers;

        public string Body { get; } = body;

        /// <summary>When the request arrived.</summary>
        public DateTimeOffset At { get; } = at;

        /// <summary>The value of a header (case-insensitive), or null.</summary>
        public string? Header(string name) => Headers[name];
    }

    /// <summary>An open event-stream response.</summary>
    public sealed class SseConnection
    {
        private readonly HttpListenerResponse _response;
        private readonly object _lock = new();

        internal SseConnection(Received request, HttpListenerResponse response)
        {
            Request = request;
            _response = response;
        }

        public Received Request { get; }

        /// <summary>Sends text as is and flushes it.</summary>
        public void Write(string text) => Write(Encoding.UTF8.GetBytes(text));

        /// <summary>Sends bytes as is and flushes them. Throws once the client has gone.</summary>
        public void Write(byte[] bytes)
        {
            lock (_lock)
            {
                _response.OutputStream.Write(bytes, 0, bytes.Length);
                _response.OutputStream.Flush();
            }
        }

        /// <summary>
        /// Sends text as is and flushes it, returning false instead of throwing once the client has gone, so a test
        /// can assert on that rather than fail on a broken pipe.
        /// </summary>
        public bool TryWrite(string text)
        {
            try
            {
                Write(text);
                return true;
            }
            catch (Exception e) when (e is HttpListenerException or IOException or ObjectDisposedException or InvalidOperationException)
            {
                return false;
            }
        }

        /// <summary>Finishes the response cleanly.</summary>
        public void End()
        {
            lock (_lock)
            {
                try
                {
                    _response.Close();
                }
                catch (Exception e) when (e is HttpListenerException or IOException or ObjectDisposedException or InvalidOperationException)
                {
                    // Already gone.
                }
            }
        }

        /// <summary>
        /// Waits until the client has closed the connection, found by writing a comment line every 10 ms until a
        /// write fails, and fails after timeout.
        /// </summary>
        public async Task AwaitClosedAsync(TimeSpan timeout)
        {
            var deadline = DateTime.UtcNow + timeout;
            while (DateTime.UtcNow < deadline)
            {
                try
                {
                    Write(":\n");
                }
                catch (Exception e) when (e is HttpListenerException or IOException or ObjectDisposedException or InvalidOperationException)
                {
                    return;
                }
                await Task.Delay(10);
            }
            throw new TimeoutException("the client kept the stream open for " + timeout);
        }
    }

    private readonly HttpListener _listener;
    private readonly Dictionary<string, Queue<Reply>> _routes = new();
    private readonly List<Received> _requests = new();
    private readonly List<SseConnection> _streams = new();
    private readonly Channel<SseConnection> _newStreams = Channel.CreateUnbounded<SseConnection>();
    private readonly Task _loop;
    private int _stopped;

    private FakeServer(HttpListener listener, int port)
    {
        _listener = listener;
        Url = "http://127.0.0.1:" + port;
        _loop = Task.Run(AcceptLoop);
    }

    /// <summary>The base URL, for example http://127.0.0.1:41234.</summary>
    public string Url { get; }

    /// <summary>Starts a server on 127.0.0.1 on a free port.</summary>
    public static FakeServer Start()
    {
        for (var attempt = 0; ; attempt++)
        {
            var port = FreePort();
            var listener = new HttpListener();
            listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
            try
            {
                listener.Start();
                return new FakeServer(listener, port);
            }
            catch (HttpListenerException) when (attempt < 10)
            {
                // Another process took the port between the probe and the start.
                listener.Close();
            }
        }
    }

    /// <summary>Adds replies to the queue of path.</summary>
    public void Script(string path, params Reply[] replies)
    {
        lock (_routes)
        {
            if (!_routes.TryGetValue(path, out var queue))
            {
                queue = new Queue<Reply>();
                _routes[path] = queue;
            }
            foreach (var r in replies)
            {
                queue.Enqueue(r);
            }
        }
    }

    /// <summary>Every request received so far.</summary>
    public IReadOnlyList<Received> Requests()
    {
        lock (_requests)
        {
            return _requests.ToList();
        }
    }

    /// <summary>The requests received for path.</summary>
    public IReadOnlyList<Received> ReceivedFor(string path) => Requests().Where(r => r.Path == path).ToList();

    /// <summary>The next event-stream connection not yet handed out, waiting up to timeout for it.</summary>
    public async Task<SseConnection> NextStreamAsync(TimeSpan timeout)
    {
        using var cts = new CancellationTokenSource(timeout);
        try
        {
            return await _newStreams.Reader.ReadAsync(cts.Token);
        }
        catch (OperationCanceledException)
        {
            throw new TimeoutException("no event-stream connection within " + timeout);
        }
    }

    /// <summary>Closes every open event stream.</summary>
    public void CloseStreams()
    {
        List<SseConnection> open;
        lock (_streams)
        {
            open = _streams.ToList();
        }
        foreach (var c in open)
        {
            c.End();
        }
    }

    /// <summary>Ends every open stream and closes the listener.</summary>
    public void Stop()
    {
        if (Interlocked.Exchange(ref _stopped, 1) == 1)
        {
            return;
        }
        CloseStreams();
        _listener.Close();
        try
        {
            _loop.Wait(TimeSpan.FromSeconds(5));
        }
        catch (AggregateException)
        {
            // The accept loop ends with the listener.
        }
    }

    public void Dispose() => Stop();

    private static int FreePort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    private async Task AcceptLoop()
    {
        while (Volatile.Read(ref _stopped) == 0)
        {
            HttpListenerContext ctx;
            try
            {
                ctx = await _listener.GetContextAsync();
            }
            catch (Exception e) when (e is HttpListenerException or ObjectDisposedException or InvalidOperationException)
            {
                return;
            }
            _ = Task.Run(() => Handle(ctx));
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        var at = DateTimeOffset.UtcNow;
        var request = ctx.Request;
        string body;
        using (var reader = new StreamReader(request.InputStream, Encoding.UTF8))
        {
            body = reader.ReadToEnd();
        }
        var headers = new NameValueCollection(request.Headers);
        var received = new Received(
            request.HttpMethod, request.Url!.AbsolutePath, request.RawUrl ?? "", headers, body, at);
        lock (_requests)
        {
            _requests.Add(received);
        }
        Reply? reply = null;
        lock (_routes)
        {
            if (_routes.TryGetValue(received.Path, out var queue) && queue.Count > 0)
            {
                reply = queue.Count > 1 ? queue.Dequeue() : queue.Peek();
            }
        }
        try
        {
            Answer(received, ctx.Response, reply);
        }
        catch (Exception e) when (e is HttpListenerException or IOException or ObjectDisposedException or InvalidOperationException)
        {
            // The client went away.
        }
    }

    private void Answer(Received received, HttpListenerResponse response, Reply? reply)
    {
        if (reply is Responder responder)
        {
            reply = responder.Fn(received);
        }
        switch (reply)
        {
            case null:
                response.StatusCode = 404;
                response.Close();
                return;
            case StreamReply:
                response.StatusCode = 200;
                response.ContentType = "text/event-stream";
                response.Headers["Cache-Control"] = "no-cache";
                response.SendChunked = true;
                var c = new SseConnection(received, response);
                // HttpListener sends the status and headers only with the first body bytes.
                c.Write(":\n");
                lock (_streams)
                {
                    _streams.Add(c);
                }
                _newStreams.Writer.TryWrite(c);
                return;
            case Scripted s:
                response.StatusCode = s.StatusCode;
                foreach (var kv in s.Headers)
                {
                    response.Headers[kv.Key] = kv.Value;
                }
                var bytes = Encoding.UTF8.GetBytes(s.Body);
                if (bytes.Length > 0 && s.StatusCode != 204 && s.StatusCode != 304)
                {
                    response.ContentLength64 = bytes.Length;
                    response.OutputStream.Write(bytes, 0, bytes.Length);
                }
                response.Close();
                return;
            default:
                throw new InvalidOperationException("unknown reply " + reply.GetType().Name);
        }
    }
}
