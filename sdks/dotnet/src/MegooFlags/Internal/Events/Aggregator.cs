using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.Json;
using MegooFlags.Internal.Json;

namespace MegooFlags.Internal.Events;

/// <summary>That a context saw a served variant. Timestamp is UTC with milliseconds (2026-11-10T09:15:02.000Z).</summary>
internal sealed record Exposure(
    string FlagKey,
    string Variant,
    string Reason,
    string Timestamp,
    IDictionary<string, object?>? Context);

/// <summary>
/// The number of evaluations of one flag and variant in one UTC hour (HourUnix, in Unix seconds). Variant is null
/// for evaluations that returned the default.
/// </summary>
internal sealed record CountItem(string FlagKey, string? Variant, long HourUnix, long Count)
{
    /// <summary>The hour as the server expects it (2026-11-10T09:00:00Z).</summary>
    public string Hour =>
        DateTimeOffset.FromUnixTimeSeconds(HourUnix).ToString("yyyy-MM-dd'T'HH':'mm':'ss'Z'", CultureInfo.InvariantCulture);
}

/// <summary>The body of <c>POST /sdk/v1/events</c>.</summary>
internal sealed class Batch(List<Exposure> exposures, List<CountItem> counts)
{
    public List<Exposure> Exposures { get; } = exposures;

    public List<CountItem> Counts { get; } = counts;

    public int Items => Exposures.Count + Counts.Count;

    /// <summary>
    /// The body as UTF-8 JSON: both lists always present, a null variant for an unserved count, the context only
    /// when it was kept. Throws <see cref="JsonException"/> when a context holds a value that is not JSON.
    /// </summary>
    public byte[] ToJson()
    {
        using var stream = new MemoryStream();
        try
        {
            using (var w = new Utf8JsonWriter(stream, JsonTree.WriterOptions))
            {
                w.WriteStartObject();
                w.WriteStartArray("exposures");
                foreach (var e in Exposures)
                {
                    w.WriteStartObject();
                    w.WriteString("flag_key", e.FlagKey);
                    w.WriteString("variant", e.Variant);
                    w.WriteString("reason", e.Reason);
                    w.WriteString("timestamp", e.Timestamp);
                    if (e.Context != null)
                    {
                        w.WritePropertyName("context");
                        JsonTree.WriteValue(w, e.Context);
                    }
                    w.WriteEndObject();
                }
                w.WriteEndArray();
                w.WriteStartArray("counts");
                foreach (var c in Counts)
                {
                    w.WriteStartObject();
                    w.WriteString("flag_key", c.FlagKey);
                    w.WriteString("variant", c.Variant);
                    w.WriteString("hour", c.Hour);
                    w.WriteNumber("count", c.Count);
                    w.WriteEndObject();
                }
                w.WriteEndArray();
                w.WriteEndObject();
            }
        }
        catch (InvalidOperationException e)
        {
            throw new JsonException(e.Message, e);
        }
        return stream.ToArray();
    }
}

/// <summary>A fixed-size set that forgets the least recently seen key. Not thread-safe.</summary>
internal sealed class Lru<T>
    where T : notnull
{
    private readonly int _capacity;
    private readonly LinkedList<T> _order = new();
    private readonly Dictionary<T, LinkedListNode<T>> _items = new();

    public Lru(int capacity)
    {
        _capacity = Math.Max(capacity, 1);
    }

    public int Count => _items.Count;

    /// <summary>
    /// Reports whether key is present and marks it as the most recent. A new key is inserted, evicting the oldest
    /// one above the capacity.
    /// </summary>
    public bool Seen(T key)
    {
        if (_items.TryGetValue(key, out var node))
        {
            _order.Remove(node);
            _order.AddFirst(node);
            return true;
        }
        _items[key] = _order.AddFirst(key);
        if (_items.Count > _capacity)
        {
            var oldest = _order.Last!;
            _order.RemoveLast();
            _items.Remove(oldest.Value);
        }
        return false;
    }
}

/// <summary>
/// Buffers exposures and hour-bucketed counts (spec/delivery.md §7). Not thread-safe: the batcher owns it from one
/// task.
/// </summary>
internal sealed class Aggregator
{
    /// <summary>The largest count the server accepts in one item.</summary>
    public const long MaxCount = 1_000_000_000;

    // A context without a string key has CtxKey "", so anonymous contexts share one exposure per flag, variant and
    // hour.
    private readonly record struct DedupKey(string CtxKey, string Flag, string Variant, long Hour);

    private readonly record struct CountKey(string Flag, string Variant, bool Served, long Hour);

    private readonly Lru<DedupKey> _dedup;
    private readonly Dictionary<CountKey, long> _counts = new();
    private readonly List<CountKey> _countOrder = new();
    private readonly List<Exposure> _exposures = new();
    private readonly int _maxPending;
    private readonly bool _sendContext;

    public Aggregator(int dedupSize, int maxPending, bool sendContext)
    {
        _dedup = new Lru<DedupKey>(dedupSize);
        _maxPending = maxPending;
        _sendContext = sendContext;
    }

    /// <summary>Exposures dropped because maxPending were already waiting.</summary>
    public long Dropped { get; private set; }

    public int PendingExposures => _exposures.Count;

    /// <summary>The count buckets waiting (one bucket may become several items).</summary>
    public int PendingCounts => _countOrder.Count;

    /// <summary>
    /// Counts ev and, the first time a served variant is seen for its context key, flag and hour, queues an
    /// exposure. An exposure beyond maxPending is dropped and counted; counts are never dropped.
    /// </summary>
    public void Add(in Evaluation ev)
    {
        var hour = HourOf(ev.At);
        AddCount(new CountKey(ev.FlagKey, ev.Served ? ev.Variant : "", ev.Served, hour), 1);
        if (!ev.Served || _dedup.Seen(new DedupKey(ev.ContextKey, ev.FlagKey, ev.Variant, hour)))
        {
            return;
        }
        if (_exposures.Count >= _maxPending)
        {
            Dropped++;
            return;
        }
        var timestamp = ev.At.UtcDateTime.ToString("yyyy-MM-dd'T'HH':'mm':'ss'.'fff'Z'", CultureInfo.InvariantCulture);
        _exposures.Add(new Exposure(ev.FlagKey, ev.Variant, ev.Reason, timestamp, _sendContext ? ev.Context : null));
    }

    /// <summary>
    /// Removes and returns up to limit exposures (oldest first) and up to limit count items (in the order the
    /// buckets were first seen). A bucket above <see cref="MaxCount"/> becomes several items; what does not fit stays
    /// for the next batch.
    /// </summary>
    public Batch Take(int limit)
    {
        var n = Math.Min(limit, _exposures.Count);
        var exposures = _exposures.GetRange(0, n);
        _exposures.RemoveRange(0, n);

        var counts = new List<CountItem>();
        var done = 0;
        while (done < _countOrder.Count && counts.Count < limit)
        {
            var k = _countOrder[done];
            var left = _counts[k];
            while (left > 0 && counts.Count < limit)
            {
                var c = Math.Min(left, MaxCount);
                left -= c;
                counts.Add(new CountItem(k.Flag, k.Served ? k.Variant : null, k.Hour, c));
            }
            if (left > 0)
            {
                _counts[k] = left;
                break;
            }
            _counts.Remove(k);
            done++;
        }
        _countOrder.RemoveRange(0, done);
        return new Batch(exposures, counts);
    }

    /// <summary>
    /// Puts an unsent batch back in front of what is pending, so a retry sends it first. Its counts add to any newer
    /// count for the same bucket.
    /// </summary>
    public void Restore(Batch b)
    {
        _exposures.InsertRange(0, b.Exposures);
        var order = new List<CountKey>();
        var restored = new HashSet<CountKey>();
        foreach (var c in b.Counts)
        {
            var k = new CountKey(c.FlagKey, c.Variant ?? "", c.Variant != null, c.HourUnix);
            if (restored.Add(k))
            {
                order.Add(k);
            }
            _counts[k] = _counts.GetValueOrDefault(k) + c.Count;
        }
        foreach (var k in _countOrder)
        {
            if (!restored.Contains(k))
            {
                order.Add(k);
            }
        }
        _countOrder.Clear();
        _countOrder.AddRange(order);
    }

    private void AddCount(CountKey k, long n)
    {
        if (_counts.TryGetValue(k, out var have))
        {
            _counts[k] = have + n;
            return;
        }
        _counts[k] = n;
        _countOrder.Add(k);
    }

    /// <summary>The start of the UTC hour of at, in Unix seconds.</summary>
    private static long HourOf(DateTimeOffset at)
    {
        var s = at.ToUnixTimeSeconds();
        return s - (((s % 3600) + 3600) % 3600);
    }
}
