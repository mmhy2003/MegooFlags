using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Transport;

namespace MegooFlags.Internal.Delivery;

/// <summary>
/// Downloads the ruleset from <c>GET /sdk/v1/ruleset</c>. The poll loop and its interval belong to
/// <see cref="Sync"/>.
/// </summary>
internal sealed class Poller
{
    private readonly HttpClient _client;
    private readonly Uri _url;
    private readonly string _sdkKey;
    private readonly Func<string> _etag;
    private readonly Func<string, string, bool> _onRuleset;
    private readonly TimeSpan _timeout;

    /// <summary>
    /// A poller. etag gives the ETag to send as If-None-Match ("" for none); onRuleset receives a downloaded body
    /// and its ETag ("" when the response has none) and returns false for an invalid ruleset.
    /// </summary>
    public Poller(
        HttpClient client,
        Uri url,
        string sdkKey,
        Func<string> etag,
        Func<string, string, bool> onRuleset,
        TimeSpan timeout)
    {
        _client = client;
        _url = url;
        _sdkKey = sdkKey;
        _etag = etag;
        _onRuleset = onRuleset;
        _timeout = timeout;
    }

    /// <summary>
    /// Downloads the ruleset once. A 304 keeps what we have; a 200 hands the body to onRuleset. Throws
    /// <see cref="TransportException"/> for any other status, <see cref="TimeoutException"/> when the poll takes
    /// longer than the timeout, <see cref="IOException"/> for an invalid ruleset, the client's exception for a
    /// network failure, and <see cref="OperationCanceledException"/> when ct is cancelled.
    /// </summary>
    public async Task PollAsync(CancellationToken ct)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(_timeout);
        string raw;
        string etag;
        try
        {
            using var req = Transport.Transport.CreateRequest(HttpMethod.Get, _url, _sdkKey, null);
            var known = _etag();
            if (known.Length > 0)
            {
                req.Headers.TryAddWithoutValidation("If-None-Match", known);
            }
            using var res = await _client
                .SendAsync(req, HttpCompletionOption.ResponseHeadersRead, cts.Token)
                .ConfigureAwait(false);
            var status = (int)res.StatusCode;
            if (status == 304)
            {
                return;
            }
            var err = Transport.Transport.StatusError(status, res.Headers, DateTimeOffset.UtcNow);
            if (err != null)
            {
                throw err;
            }
            if (status != 200)
            {
                throw new HttpStatusException(status);
            }
            raw = await res.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
            etag = res.Headers.TryGetValues("ETag", out var values) ? values.FirstOrDefault() ?? "" : "";
        }
        catch (Exception e) when (e is OperationCanceledException && cts.IsCancellationRequested && !ct.IsCancellationRequested)
        {
            throw new TimeoutException(
                "megooflags: ruleset poll timed out after " + _timeout.TotalSeconds.ToString("0.###", CultureInfo.InvariantCulture) + "s",
                e);
        }
        if (!_onRuleset(raw, etag))
        {
            throw new IOException("megooflags: invalid ruleset");
        }
    }
}
