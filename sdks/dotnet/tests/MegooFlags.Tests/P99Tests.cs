using System;
using System.Diagnostics;
using System.Globalization;
using MegooFlags.Internal.Engine;
using MegooFlags.Internal.Json;
using Xunit;

namespace MegooFlags.Tests;

/// <summary>
/// p99 guard: evaluation stays under 1 ms on 10 000 flags. It runs alone (dotnet test --filter "Category=P99") so
/// other tests do not compete for the CPU.
/// </summary>
[Trait("Category", "P99")]
public class P99Tests
{
    private const int Flags = 10000;
    private const int WarmUp = 10000;
    private const int Samples = 100000;

    [Fact]
    public void Evaluates10000FlagsWithP99Under1Ms()
    {
        var rs = RulesetParser.Parse(JsonTree.Write(RulesetGen.GenRuleset(Flags)));
        var keys = RulesetGen.FlagKeys(Flags);
        var ctxs = RulesetGen.BenchContexts();
        var random = new Random(12345);
        for (var i = 0; i < WarmUp; i++)
        {
            Evaluator.Evaluate(rs, keys[random.Next(Flags)], ctxs[random.Next(ctxs.Count)], null, null);
        }
        var samples = new long[Samples];
        for (var i = 0; i < Samples; i++)
        {
            var key = keys[random.Next(Flags)];
            var ctx = ctxs[random.Next(ctxs.Count)];
            var start = Stopwatch.GetTimestamp();
            Evaluator.Evaluate(rs, key, ctx, null, null);
            samples[i] = Stopwatch.GetTimestamp() - start;
        }
        Array.Sort(samples);
        double Ms(long ticks) => ticks * 1000.0 / Stopwatch.Frequency;
        var p50 = Ms(samples[Samples / 2]);
        var p99 = Ms(samples[Samples * 99 / 100]);
        var max = Ms(samples[Samples - 1]);
        var line = string.Format(
            CultureInfo.InvariantCulture,
            "p50 {0:F4} ms, p99 {1:F4} ms, max {2:F4} ms",
            p50,
            p99,
            max);
        // VSTest shows it with --logger "console;verbosity=normal".
        Console.WriteLine(line);
        Assert.True(p99 < 1, "p99 is " + p99.ToString(CultureInfo.InvariantCulture) + " ms");
    }
}
