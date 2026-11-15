using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Extensions.Logging;

namespace MegooFlags.Tests;

/// <summary>An ILogger that keeps every formatted line with its level.</summary>
internal sealed class RecordingLogger : ILogger
{
    private readonly List<(LogLevel Level, string Message)> _lines = new();

    public IDisposable? BeginScope<TState>(TState state)
        where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(
        LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
    {
        lock (_lines)
        {
            _lines.Add((logLevel, formatter(state, exception)));
        }
    }

    /// <summary>Every line so far.</summary>
    public List<string> Lines()
    {
        lock (_lines)
        {
            return _lines.Select(l => l.Message).ToList();
        }
    }

    /// <summary>The lines logged at level.</summary>
    public List<string> At(LogLevel level)
    {
        lock (_lines)
        {
            return _lines.Where(l => l.Level == level).Select(l => l.Message).ToList();
        }
    }
}
