using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace MegooFlags.Internal.Engine;

/// <summary>One variation's share of a rollout, out of <see cref="Bucketing.Buckets"/>.</summary>
internal readonly record struct WeightedVariation(string Variation, int Weight);

/// <summary>Percentage rollout bucketing, identical in every MegooFlags SDK.</summary>
internal static class Bucketing
{
    /// <summary>The number of rollout buckets; weights add up to it.</summary>
    public const int Buckets = 100000;

    private const long MaxSafeInteger = (1L << 53) - 1;

    /// <summary>
    /// BucketText is the text hashed for v, or null when v can't be bucketed (which counts as a missing attribute).
    /// A string is used as is, an integral number with |n| &lt;= 2^53-1 as its base-10 text; anything else is
    /// refused.
    /// </summary>
    public static string? BucketText(object? v)
    {
        if (v is string s)
        {
            return s;
        }
        if (Values.IsInteger(v))
        {
            var n = Values.ToLong(v!);
            return n >= -MaxSafeInteger && n <= MaxSafeInteger ? n.ToString(CultureInfo.InvariantCulture) : null;
        }
        if (v is double or float)
        {
            var d = Values.ToDouble(v);
            if (d != Math.Round(d) || Math.Abs(d) > MaxSafeInteger)
            {
                return null; // NaN fails the first test, infinities the second
            }
            return ((long)d).ToString(CultureInfo.InvariantCulture); // (long)-0.0 is 0
        }
        return null;
    }

    /// <summary>Bucket is uint64_be(sha256(flagKey + "." + text)[0:8]) mod <see cref="Buckets"/>.</summary>
    public static int Bucket(string flagKey, string text)
    {
        var sum = SHA256.HashData(Encoding.UTF8.GetBytes(flagKey + "." + text));
        var r = 0;
        for (var i = 0; i < 8; i++)
        {
            r = (r * 256 + sum[i]) % Buckets;
        }
        return r;
    }

    /// <summary>
    /// PickVariation returns the first variation whose running total of weights is greater than bucket, or null
    /// when there is none. The parser guarantees the weights add up to <see cref="Buckets"/>.
    /// </summary>
    public static string? PickVariation(IReadOnlyList<WeightedVariation> weights, int bucket)
    {
        long total = 0;
        foreach (var w in weights)
        {
            total += w.Weight;
            if (total > bucket)
            {
                return w.Variation;
            }
        }
        return null;
    }
}
