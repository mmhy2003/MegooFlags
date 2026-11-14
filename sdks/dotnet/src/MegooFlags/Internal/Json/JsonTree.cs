using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace MegooFlags.Internal.Json;

/// <summary>
/// JSON as plain .NET values: <c>Dictionary&lt;string, object?&gt;</c>, <c>List&lt;object?&gt;</c>, <c>string</c>,
/// <c>long</c> (an integer literal that fits), <c>double</c> (any other number), <c>bool</c> and <c>null</c>.
/// </summary>
internal static class JsonTree
{
    /// <summary>The deepest nesting <see cref="Parse"/> and <see cref="Write"/> accept.</summary>
    public const int MaxDepth = 512;

    private static readonly JsonDocumentOptions _parseOptions = new() { MaxDepth = MaxDepth };

    /// <summary>The writer settings of <see cref="Write"/>, for callers that write a larger document.</summary>
    public static readonly JsonWriterOptions WriterOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        MaxDepth = MaxDepth,
        SkipValidation = false,
    };

    /// <summary>
    /// Parse reads one strict RFC 8259 document. Duplicate keys: the last one wins. Throws
    /// <see cref="JsonException"/> for invalid JSON, a number out of the double range and a lone surrogate escape.
    /// </summary>
    public static object? Parse(string text)
    {
        using var doc = JsonDocument.Parse(text, _parseOptions);
        return Convert(doc.RootElement);
    }

    /// <summary>Write writes a tree built of the types <see cref="Parse"/> returns, without whitespace.</summary>
    public static string Write(object? value)
    {
        using var stream = new MemoryStream();
        try
        {
            using (var writer = new Utf8JsonWriter(stream, WriterOptions))
            {
                WriteValue(writer, value);
            }
        }
        catch (InvalidOperationException e)
        {
            throw new JsonException(e.Message, e);
        }
        return Encoding.UTF8.GetString(stream.ToArray());
    }

    private static object? Convert(JsonElement e)
    {
        switch (e.ValueKind)
        {
            case JsonValueKind.Object:
                var obj = new Dictionary<string, object?>();
                foreach (var p in e.EnumerateObject())
                {
                    obj[Name(p)] = Convert(p.Value);
                }
                return obj;
            case JsonValueKind.Array:
                var list = new List<object?>(e.GetArrayLength());
                foreach (var item in e.EnumerateArray())
                {
                    list.Add(Convert(item));
                }
                return list;
            case JsonValueKind.String:
                return Text(e);
            case JsonValueKind.Number:
                return Number(e);
            case JsonValueKind.True:
                return true;
            case JsonValueKind.False:
                return false;
            default:
                return null;
        }
    }

    private static object Number(JsonElement e)
    {
        var raw = e.GetRawText();
        if (raw.IndexOfAny(['.', 'e', 'E']) < 0 && e.TryGetInt64(out var l))
        {
            return l;
        }
        if (!e.TryGetDouble(out var d) || !double.IsFinite(d))
        {
            throw new JsonException("number out of range: " + raw);
        }
        return d;
    }

    // GetString and Name throw InvalidOperationException for a lone surrogate escape.
    private static string Text(JsonElement e)
    {
        try
        {
            return e.GetString()!;
        }
        catch (InvalidOperationException ex)
        {
            throw new JsonException(ex.Message, ex);
        }
    }

    private static string Name(JsonProperty p)
    {
        try
        {
            return p.Name;
        }
        catch (InvalidOperationException ex)
        {
            throw new JsonException(ex.Message, ex);
        }
    }

    /// <summary>
    /// Writes a tree built of the types <see cref="Parse"/> returns. Throws <see cref="JsonException"/> for any
    /// other type and a non-finite number, and the writer's InvalidOperationException past <see cref="MaxDepth"/>.
    /// </summary>
    public static void WriteValue(Utf8JsonWriter w, object? v)
    {
        switch (v)
        {
            case null:
                w.WriteNullValue();
                break;
            case string s:
                w.WriteStringValue(s);
                break;
            case bool b:
                w.WriteBooleanValue(b);
                break;
            case long or int or short or sbyte or byte or ushort or uint:
                w.WriteNumberValue(System.Convert.ToInt64(v, CultureInfo.InvariantCulture));
                break;
            case double or float:
                WriteDouble(w, System.Convert.ToDouble(v, CultureInfo.InvariantCulture));
                break;
            case IDictionary<string, object?> map:
                w.WriteStartObject();
                foreach (var kv in map)
                {
                    w.WritePropertyName(kv.Key);
                    WriteValue(w, kv.Value);
                }
                w.WriteEndObject();
                break;
            case IEnumerable<object?> items:
                w.WriteStartArray();
                foreach (var item in items)
                {
                    WriteValue(w, item);
                }
                w.WriteEndArray();
                break;
            default:
                throw new JsonException("cannot write a " + v.GetType().Name + " as JSON");
        }
    }

    // A double keeps a fraction or an exponent, so it reads back as a double ("1.0", not "1").
    private static void WriteDouble(Utf8JsonWriter w, double d)
    {
        if (!double.IsFinite(d))
        {
            throw new JsonException("non-finite number");
        }
        var text = d.ToString("R", CultureInfo.InvariantCulture);
        if (text.IndexOfAny(['.', 'E']) < 0)
        {
            text += ".0";
        }
        w.WriteRawValue(text, skipInputValidation: true);
    }
}
