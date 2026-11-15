using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace MegooFlags.Internal.Delivery;

/// <summary>A text/event-stream parser over a byte stream (the response body of the stream request).</summary>
internal sealed class SseReader
{
    /// <summary>One dispatched server-sent event. The name is "message" when the stream sent none; Id may be "".</summary>
    public sealed record Event(string Name, string Data, string Id);

    /// <summary>What the reader reports. An exception thrown by a callback stops reading and is passed through.</summary>
    public interface ICallbacks
    {
        /// <summary>Runs for every line, comments included (the dead-connection timer).</summary>
        void OnLine();

        /// <summary>Receives a valid retry field, in milliseconds.</summary>
        void OnRetry(long millis);

        /// <summary>Receives each event.</summary>
        void OnEvent(Event e);
    }

    private const byte Lf = (byte)'\n';
    private const byte Cr = (byte)'\r';

    private readonly ICallbacks _cb;
    private readonly List<string> _data = new();
    private string _name = "";
    private string _id = "";
    private bool _seen;

    private SseReader(ICallbacks cb)
    {
        _cb = cb;
    }

    /// <summary>
    /// Parses stream until it ends. Lines are split on the LF byte before decoding UTF-8, so a character split
    /// across reads decodes whole, and a line of any length is accepted. An unterminated last line or event is
    /// discarded. A read error, a cancellation of ct, or an exception thrown by a callback is passed through.
    /// </summary>
    public static async Task ReadAsync(Stream stream, ICallbacks callbacks, CancellationToken ct)
    {
        var r = new SseReader(callbacks);
        var buf = new byte[64 * 1024];
        var pending = new MemoryStream();
        int n;
        while ((n = await stream.ReadAsync(buf.AsMemory(), ct).ConfigureAwait(false)) > 0)
        {
            var start = 0;
            for (var i = Array.IndexOf(buf, Lf, 0, n); i >= 0; i = Array.IndexOf(buf, Lf, start, n - start))
            {
                if (pending.Length > 0)
                {
                    pending.Write(buf, start, i - start);
                    r.Line(pending.GetBuffer(), 0, (int)pending.Length);
                    pending.SetLength(0);
                }
                else
                {
                    r.Line(buf, start, i - start);
                }
                start = i + 1;
            }
            if (start < n)
            {
                pending.Write(buf, start, n - start);
            }
        }
    }

    private void Line(byte[] bytes, int offset, int length)
    {
        if (length > 0 && bytes[offset + length - 1] == Cr)
        {
            length--;
        }
        var line = Encoding.UTF8.GetString(bytes, offset, length);
        _cb.OnLine();
        if (line.Length == 0)
        {
            Dispatch();
            return;
        }
        if (line[0] == ':')
        {
            return;
        }
        var field = line;
        var value = "";
        var colon = line.IndexOf(':');
        if (colon >= 0)
        {
            field = line[..colon];
            value = line[(colon + 1)..];
            if (value.StartsWith(' '))
            {
                value = value[1..];
            }
        }
        switch (field)
        {
            case "event":
                _name = value;
                break;
            case "data":
                _data.Add(value);
                _seen = true;
                break;
            case "id":
                if (!value.Contains('\0'))
                {
                    _id = value;
                }
                break;
            case "retry":
                if (Digits(value) is long ms)
                {
                    _cb.OnRetry(ms);
                }
                break;
        }
    }

    private void Dispatch()
    {
        var e = _seen ? new Event(_name.Length == 0 ? "message" : _name, string.Join('\n', _data), _id) : null;
        _name = "";
        _id = "";
        _data.Clear();
        _seen = false;
        if (e != null)
        {
            _cb.OnEvent(e);
        }
    }

    /// <summary>A non-empty string of ASCII digits that fits a long, else null.</summary>
    private static long? Digits(string s)
    {
        if (s.Length == 0)
        {
            return null;
        }
        long v = 0;
        foreach (var c in s)
        {
            if (c is < '0' or > '9')
            {
                return null;
            }
            if (v > (long.MaxValue - (c - '0')) / 10)
            {
                return null;
            }
            v = v * 10 + (c - '0');
        }
        return v;
    }
}
