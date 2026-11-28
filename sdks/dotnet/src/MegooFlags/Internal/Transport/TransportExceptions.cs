using System;
using System.Globalization;

namespace MegooFlags.Internal.Transport;

/// <summary>A failed exchange with the MegooFlags server, as <see cref="Transport.StatusError"/> maps it.</summary>
internal abstract class TransportException : Exception
{
    private protected TransportException(string message) : base(message)
    {
    }
}

/// <summary>
/// The server refused the SDK key (401, 403, or the stream's revoked event). Retrying with the same key cannot
/// succeed.
/// </summary>
internal sealed class FatalException : TransportException
{
    private FatalException(int status, string reason)
        : base("megooflags: SDK key refused (" + (status != 0 ? status.ToString(CultureInfo.InvariantCulture) : reason) + ")")
    {
        Status = status;
        Reason = reason;
    }

    /// <summary>A refusal by HTTP status (401 or 403).</summary>
    public FatalException(int status) : this(status, "")
    {
    }

    /// <summary>The HTTP status, or 0 when the refusal came as a reason.</summary>
    public int Status { get; }

    /// <summary>The reason (<c>revoked</c>), or "" for a refusal by status.</summary>
    public string Reason { get; }

    /// <summary>The stream's revoked event.</summary>
    public static FatalException Revoked() => new(0, "revoked");
}

/// <summary>A 429. <see cref="After"/> is the server's Retry-After, or zero.</summary>
internal sealed class RetryAfterException : TransportException
{
    public RetryAfterException(TimeSpan after)
        : base("megooflags: rate limited, retry after " + after.TotalSeconds.ToString("0.###", CultureInfo.InvariantCulture) + "s")
    {
        After = after;
    }

    /// <summary>The wait the server asked for; zero when it gave none.</summary>
    public TimeSpan After { get; }
}

/// <summary>Any other unexpected HTTP status.</summary>
internal sealed class HttpStatusException : TransportException
{
    public HttpStatusException(int status) : base("megooflags: HTTP " + status.ToString(CultureInfo.InvariantCulture))
    {
        Status = status;
    }

    public int Status { get; }
}
