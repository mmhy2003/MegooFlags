using System;
using System.Threading.Tasks;

namespace MegooFlags.Tests;

/// <summary>Waits for a condition that background work makes true.</summary>
internal static class Await
{
    /// <summary>The longest any test waits for something that should happen.</summary>
    public static readonly TimeSpan Timeout = TimeSpan.FromSeconds(5);

    /// <summary>Checks cond every 5 ms and fails after <see cref="Timeout"/>.</summary>
    public static async Task UntilAsync(Func<bool> cond, string what = "the condition")
    {
        var deadline = DateTime.UtcNow + Timeout;
        while (!cond())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException(what + " did not happen within " + Timeout);
            }
            await Task.Delay(5);
        }
    }
}
