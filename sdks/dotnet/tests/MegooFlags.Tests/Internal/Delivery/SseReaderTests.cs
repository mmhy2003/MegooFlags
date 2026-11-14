using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Delivery;
using Xunit;

namespace MegooFlags.Tests.Internal.Delivery;

public class SseReaderTests
{
    private sealed class Seen : SseReader.ICallbacks
    {
        public int Lines { get; private set; }

        public List<long> Retries { get; } = new();

        public List<SseReader.Event> Events { get; } = new();

        public void OnLine() => Lines++;

        public void OnRetry(long millis) => Retries.Add(millis);

        public void OnEvent(SseReader.Event e) => Events.Add(e);
    }

    /// <summary>A read-only stream that hands out each chunk in its own read call.</summary>
    private sealed class ChunkStream(IEnumerable<byte[]> chunks, Exception? end = null) : Stream
    {
        private readonly Queue<byte[]> _chunks = new(chunks);

        public override bool CanRead => true;

        public override bool CanSeek => false;

        public override bool CanWrite => false;

        public override long Length => throw new NotSupportedException();

        public override long Position
        {
            get => throw new NotSupportedException();
            set => throw new NotSupportedException();
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (!_chunks.TryDequeue(out var chunk))
            {
                return end == null ? 0 : throw end;
            }
            if (chunk.Length > count)
            {
                throw new InvalidOperationException("the test chunk is larger than the read buffer");
            }
            Array.Copy(chunk, 0, buffer, offset, chunk.Length);
            return chunk.Length;
        }

        public override void Flush()
        {
        }

        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

        public override void SetLength(long value) => throw new NotSupportedException();

        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    private static byte[] Utf8(string s) => Encoding.UTF8.GetBytes(s);

    private static async Task<Seen> ReadAsync(params byte[][] parts)
    {
        var seen = new Seen();
        await SseReader.ReadAsync(new ChunkStream(parts), seen, CancellationToken.None);
        return seen;
    }

    private static Task<Seen> ReadAsync(string text) => ReadAsync(Utf8(text));

    private static SseReader.Event Ev(string name, string data, string id) => new(name, data, id);

    // Expected events are [name, data, id] triples: theory data must be public types.
    public static TheoryData<string, string, int, string[][]> Cases() => new()
    {
        { "named event with id", "event: put\nid: 42\ndata: {\"flags\":{}}\n\n", 4, [["put", "{\"flags\":{}}", "42"]] },
        { "default name", "data: x\n\n", 2, [["message", "x", ""]] },
        { "multi-line data", "data: a\ndata: b\ndata:c\n\n", 4, [["message", "a\nb\nc", ""]] },
        { "CRLF endings", "event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4, [["put", "x", "7"]] },
        { "unknown event name passed through", "event: mystery\ndata: 1\n\n", 3, [["mystery", "1", ""]] },
        { "unterminated last event discarded", "data: a\n\nevent: put\ndata: b\n", 4, [["message", "a", ""]] },
        { "unterminated last line discarded", "data: a\n\ndata: b", 2, [["message", "a", ""]] },
        {
            "state resets after dispatch", "event: put\nid: 1\ndata: a\n\ndata: b\n\n", 6,
            [["put", "a", "1"], ["message", "b", ""]]
        },
        { "empty line without data dispatches nothing", "event: put\n\ndata: a\n\n", 4, [["message", "a", ""]] },
        { "id with NUL ignored", "id: a\0b\ndata: x\n\n", 3, [["message", "x", ""]] },
        { "field without colon has an empty value", "data\ndata\n\n", 3, [["message", "\n", ""]] },
        {
            "only one leading space removed, unknown field ignored", "foo: bar\ndata:  two\n\n", 3,
            [["message", " two", ""]]
        },
        { "colon inside the value kept", "data: a: b\n\n", 2, [["message", "a: b", ""]] },
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public async Task Parses(string name, string input, int lines, string[][] events)
    {
        _ = name;
        var seen = await ReadAsync(input);
        Assert.Equal(events.Select(e => Ev(e[0], e[1], e[2])), seen.Events);
        Assert.Equal(lines, seen.Lines);
        Assert.Empty(seen.Retries);
    }

    [Fact]
    public async Task CommentsCallOnLineOnly()
    {
        var seen = await ReadAsync(": ping\n:\n\n");
        Assert.Equal(3, seen.Lines);
        Assert.Empty(seen.Retries);
        Assert.Empty(seen.Events);
    }

    [Fact]
    public async Task ValidRetryCallsOnRetryInMilliseconds()
    {
        var seen = await ReadAsync("retry: 3000\n\n");
        Assert.Equal(2, seen.Lines);
        Assert.Equal([3000L], seen.Retries);
        Assert.Empty(seen.Events);
    }

    [Theory]
    [InlineData("retry: x\n")]
    [InlineData("retry: -1\n")]
    [InlineData("retry: 1.5\n")]
    [InlineData("retry:\n")]
    [InlineData("retry: \u0663\n")]
    [InlineData("retry: 99999999999999999999\n")]
    public async Task InvalidRetryIsIgnored(string input)
    {
        Assert.Empty((await ReadAsync(input)).Retries);
    }

    [Fact]
    public async Task ReadsA1MiBDataLine()
    {
        var big = new string('x', 1 << 20);
        var parts = new List<byte[]> { Utf8("event: put\ndata: ") };
        for (var i = 0; i < big.Length; i += 16_384)
        {
            parts.Add(Utf8(big.Substring(i, 16_384)));
        }
        parts.Add(Utf8("\n\n"));
        var seen = await ReadAsync(parts.ToArray());
        Assert.Equal(big, Assert.Single(seen.Events).Data);
    }

    [Fact]
    public async Task DecodesAMultiByteCharacterSplitAcrossTwoChunks()
    {
        var bytes = Utf8("data: \u00e9\u20ac\U0001F600\n\n");
        var at = Array.IndexOf(bytes, (byte)0xe2) + 1;
        var seen = await ReadAsync(bytes[..at], bytes[at..]);
        Assert.Equal([Ev("message", "\u00e9\u20ac\U0001F600", "")], seen.Events);
    }

    [Fact]
    public async Task SplitsLinesThatArriveInOneChunkOrByteByByte()
    {
        const string text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n";
        var one = await ReadAsync(text);
        var split = await ReadAsync(Utf8(text).Select(b => new[] { b }).ToArray());
        Assert.Equal(one.Events, split.Events);
        Assert.Equal(one.Lines, split.Lines);
        Assert.Equal(["a", "b"], one.Events.Select(e => e.Data));
    }

    private sealed class Throwing(Exception stop, List<string> names) : SseReader.ICallbacks
    {
        public void OnLine()
        {
        }

        public void OnRetry(long millis)
        {
        }

        public void OnEvent(SseReader.Event e)
        {
            names.Add(e.Name);
            throw stop;
        }
    }

    [Fact]
    public async Task AnExceptionThrownByOnEventStopsReading()
    {
        var names = new List<string>();
        var stop = new IOException("stop");
        var stream = new ChunkStream([Utf8("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n")]);
        var got = await Assert.ThrowsAsync<IOException>(
            () => SseReader.ReadAsync(stream, new Throwing(stop, names), CancellationToken.None));
        Assert.Same(stop, got);
        Assert.Equal(["revoked"], names);
    }

    [Fact]
    public async Task AReadErrorIsPassedThrough()
    {
        var boom = new IOException("boom");
        var seen = new Seen();
        var got = await Assert.ThrowsAsync<IOException>(
            () => SseReader.ReadAsync(new ChunkStream([Utf8(": ping\n")], boom), seen, CancellationToken.None));
        Assert.Same(boom, got);
        Assert.Equal(1, seen.Lines);
    }

    [Fact]
    public async Task CancellationStopsReading()
    {
        var server = FakeServer.Start();
        try
        {
            server.Script("/sdk/v1/stream", FakeServer.Reply.Stream());
            using var client = new HttpClient();
            using var res = await client.GetAsync(server.Url + "/sdk/v1/stream", HttpCompletionOption.ResponseHeadersRead);
            await server.NextStreamAsync(TimeSpan.FromSeconds(5));
            using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(50));
            await using var body = await res.Content.ReadAsStreamAsync();
            await Assert.ThrowsAnyAsync<OperationCanceledException>(
                () => SseReader.ReadAsync(body, new Seen(), cts.Token));
        }
        finally
        {
            server.Stop();
        }
    }

    private sealed class Answering(FakeServer.SseConnection conn, List<SseReader.Event> events) : SseReader.ICallbacks
    {
        public void OnLine()
        {
        }

        public void OnRetry(long millis)
        {
        }

        public void OnEvent(SseReader.Event e)
        {
            events.Add(e);
            if (events.Count == 1)
            {
                conn.Write("event: put\nid: 2\ndata: b\n\n");
                conn.End();
            }
        }
    }

    [Fact]
    public async Task ReadsAnHttpResponseBodyAsItArrives()
    {
        var server = FakeServer.Start();
        try
        {
            server.Script("/sdk/v1/stream", FakeServer.Reply.Stream());
            using var client = new HttpClient();
            using var res = await client.GetAsync(server.Url + "/sdk/v1/stream", HttpCompletionOption.ResponseHeadersRead);
            Assert.Equal("text/event-stream", res.Content.Headers.ContentType?.MediaType);
            var conn = await server.NextStreamAsync(TimeSpan.FromSeconds(5));
            var events = new List<SseReader.Event>();
            conn.Write(": ping\n\nevent: put\nid: 1\ndata: a\n\n");
            await using (var body = await res.Content.ReadAsStreamAsync())
            {
                await SseReader.ReadAsync(body, new Answering(conn, events), CancellationToken.None);
            }
            Assert.Equal([Ev("put", "a", "1"), Ev("put", "b", "2")], events);
            Assert.Single(server.ReceivedFor("/sdk/v1/stream"));
        }
        finally
        {
            server.Stop();
        }
    }
}
