using System;
using Xunit;

namespace MegooFlags.Tests.E2E;

/// <summary>A fact that is skipped unless MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are both set.</summary>
[AttributeUsage(AttributeTargets.Method, AllowMultiple = false)]
public sealed class E2EFactAttribute : FactAttribute
{
    public E2EFactAttribute()
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MEGOOFLAGS_E2E_URL"))
            || string.IsNullOrEmpty(Environment.GetEnvironmentVariable("MEGOOFLAGS_API_TOKEN")))
        {
            Skip = "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set";
        }
    }
}
