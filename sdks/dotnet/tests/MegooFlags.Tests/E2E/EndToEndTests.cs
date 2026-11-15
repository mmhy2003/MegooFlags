using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using OpenFeature;
using OpenFeature.Constant;
using OpenFeature.Model;
using Xunit;

namespace MegooFlags.Tests.E2E;

/// <summary>
/// Runs the provider against a running MegooFlags stack. Set MEGOOFLAGS_E2E_URL (for example
/// http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it:
/// <c>dotnet test -c Release --filter "Category=E2E"</c>.
/// </summary>
[Collection(OpenFeatureCollection.Name)]
[Trait("Category", "E2E")]
public sealed class EndToEndTests : IAsyncLifetime
{
    private static readonly string _base =
        (Environment.GetEnvironmentVariable("MEGOOFLAGS_E2E_URL") ?? "").TrimEnd('/');
    private static readonly string _token = Environment.GetEnvironmentVariable("MEGOOFLAGS_API_TOKEN") ?? "";

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly string _dir = Directory.CreateTempSubdirectory("megooflags-e2e-test-").FullName;
    private string? _project;

    public Task InitializeAsync() => Task.CompletedTask;

    public async Task DisposeAsync()
    {
        await Api.Instance.ShutdownAsync();
        if (_project != null)
        {
            await CallAsync(HttpMethod.Delete, "/api/projects/" + _project, null);
        }
        _http.Dispose();
        Directory.Delete(_dir, recursive: true);
    }

    [E2EFact]
    public async Task EndToEnd()
    {
        var project = "sdkdotnet-" + Convert.ToHexString(RandomNumberGenerator.GetBytes(4)).ToLowerInvariant();
        await MustAsync(HttpStatusCode.Created, HttpMethod.Post, "/api/projects", new Dictionary<string, object>
        {
            ["key"] = project,
            ["name"] = project,
            ["environments"] = new[] { new Dictionary<string, object> { ["key"] = "e2e", ["name"] = "E2E" } },
        });
        _project = project;

        var flagPath = "/api/projects/" + project + "/flags/e2e-flag/environments/e2e";
        await MustAsync(HttpStatusCode.Created, HttpMethod.Post, "/api/projects/" + project + "/flags",
            new Dictionary<string, object> { ["key"] = "e2e-flag", ["name"] = "E2E", ["type"] = "boolean" });
        await MustAsync(HttpStatusCode.OK, HttpMethod.Post, flagPath + "/toggle",
            new Dictionary<string, object> { ["enabled"] = true });
        using var created = await MustAsync(HttpStatusCode.Created, HttpMethod.Post,
            "/api/projects/" + project + "/environments/e2e/sdk-keys",
            new Dictionary<string, object> { ["name"] = "dotnet-e2e", ["kind"] = "server" });
        var sdkKey = created.RootElement.TryGetProperty("key", out var k) && k.ValueKind == JsonValueKind.String
            ? k.GetString()!
            : "";
        Assert.False(sdkKey.Length == 0, "POST sdk-keys returned no key");

        var cacheDir = Path.Combine(_dir, "cache");
        var provider = new MegooFlagsProvider(new MegooFlagsOptions
        {
            SdkKey = sdkKey,
            BaseUrl = _base,
            CacheDir = cacheDir,
            FlushInterval = TimeSpan.FromSeconds(10),
        });
        await Api.Instance.SetProviderAsync(project, provider);
        var client = Api.Instance.GetClient(project);
        Assert.Equal(ProviderStatus.Ready, client.ProviderStatus);
        var changes = 0;
        client.AddHandler(ProviderEventTypes.ProviderConfigurationChanged, _ => Interlocked.Increment(ref changes));
        var ctx = EvaluationContext.Builder().SetTargetingKey("user-1").Build();

        // Check 1: the downloaded ruleset is evaluated.
        Assert.True(await client.GetBooleanValueAsync("e2e-flag", false, ctx));

        // Check 2: a toggle arrives over SSE within 5 s.
        await MustAsync(HttpStatusCode.OK, HttpMethod.Post, flagPath + "/toggle",
            new Dictionary<string, object> { ["enabled"] = false });
        await UntilAsync("e2e-flag false after the toggle",
            async () => !await client.GetBooleanValueAsync("e2e-flag", false, ctx),
            TimeSpan.FromSeconds(5), TimeSpan.FromMilliseconds(50));
        await UntilAsync("a configuration-changed event",
            () => Task.FromResult(Volatile.Read(ref changes) > 0),
            TimeSpan.FromSeconds(5), TimeSpan.FromMilliseconds(50));

        // Check 3: evaluation counts are sent at shutdown and accepted.
        for (var i = 0; i < 20; i++)
        {
            await client.GetBooleanValueAsync("e2e-flag", false, ctx);
        }
        await provider.ShutdownAsync();
        await UntilAsync("evaluations total >= 21", async () =>
        {
            using var doc = await MustAsync(HttpStatusCode.OK, HttpMethod.Get, flagPath + "/evaluations?range=24h", null);
            return doc.RootElement.TryGetProperty("total", out var total)
                && total.ValueKind == JsonValueKind.Number
                && total.GetInt64() >= 21;
        }, TimeSpan.FromSeconds(10), TimeSpan.FromMilliseconds(500));

        // Check 4: with the API unreachable, a new provider serves the cache.
        var offline = new MegooFlagsProvider(new MegooFlagsOptions
        {
            SdkKey = sdkKey,
            BaseUrl = "http://127.0.0.1:" + ClosedPort(),
            CacheDir = cacheDir,
            InitTimeout = TimeSpan.FromSeconds(1),
        });
        await Api.Instance.SetProviderAsync(project + "-offline", offline);
        var offlineClient = Api.Instance.GetClient(project + "-offline");
        Assert.Equal(ProviderStatus.Ready, offlineClient.ProviderStatus);
        Assert.False(await offlineClient.GetBooleanValueAsync("e2e-flag", true, ctx));
    }

    /// <summary>Calls the management API; a null body sends none. Returns the status and the body.</summary>
    private async Task<(HttpStatusCode Status, string Body)> CallAsync(HttpMethod method, string path, object? body)
    {
        using var req = new HttpRequestMessage(method, _base + path);
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _token);
        if (body != null)
        {
            req.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        }
        using var res = await _http.SendAsync(req);
        return (res.StatusCode, await res.Content.ReadAsStringAsync());
    }

    /// <summary>Calls the management API and fails unless the status is want.</summary>
    private async Task<JsonDocument> MustAsync(HttpStatusCode want, HttpMethod method, string path, object? body)
    {
        var (status, text) = await CallAsync(method, path, body);
        Assert.True(status == want, method + " " + path + ": " + (int)status + " " + text);
        return JsonDocument.Parse(text.Length == 0 ? "{}" : text);
    }

    /// <summary>Polls check every interval until it holds or timeout passes.</summary>
    private static async Task UntilAsync(string what, Func<Task<bool>> check, TimeSpan timeout, TimeSpan interval)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (!await check())
        {
            if (DateTime.UtcNow > deadline)
            {
                Assert.Fail(what + ": not within " + (long)timeout.TotalMilliseconds + " ms");
            }
            await Task.Delay(interval);
        }
    }

    /// <summary>A local port nothing listens on.</summary>
    private static int ClosedPort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }
}
