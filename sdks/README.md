# SDKs

MegooFlags SDKs live in this repository, one directory per language: `sdks/<lang>/`.

Planned, in order: `go`, `node`, `python`, `java`, `dotnet`, `ruby`, `php`, `js` (browser), `react`, then mobile.

## Versioning and tags

Each SDK is versioned independently of the server and of the other SDKs, following the X.Y.Z and 0.Y.Z
rules in [AGENTS.md](../AGENTS.md). Release tags are:

| SDK | Tag | Example |
|---|---|---|
| Go | `sdks/go/vX.Y.Z` | `sdks/go/v1.2.0` |
| Every other language | `sdk-<lang>/vX.Y.Z` | `sdk-node/v1.2.0` |

Go is the exception because Go modules in a repository subdirectory must be tagged with the
subdirectory path. Server releases keep the plain `vX.Y.Z` tags. As with server releases, only a
maintainer pushes SDK tags.

## Requirements

Each SDK is an [OpenFeature](https://openfeature.dev) provider and must pass the conformance suite in
[`spec/`](../spec/README.md) before it can be released.

## Status

| SDK | Directory | Version | Kind | Conformance | CI jobs |
|---|---|---|---|---|---|
| Go | [`sdks/go`](go/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-go`, `sdk-go-e2e` |
| Node.js | [`sdks/node`](node/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-node`, `sdk-node-e2e` |
| Python | [`sdks/python`](python/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-python`, `sdk-python-e2e` |
| Java | [`sdks/java`](java/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-java`, `sdk-java-e2e` |
| .NET | [`sdks/dotnet`](dotnet/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-dotnet`, `sdk-dotnet-e2e` |
| Ruby | [`sdks/ruby`](ruby/README.md) | 0.1.0 (Alpha) | OpenFeature provider | 100 % | `sdk-ruby`, `sdk-ruby-e2e` |
| PHP | `sdks/php` | planned | | | |
