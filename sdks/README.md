# SDKs

MegooFlags SDKs live in this repository, one directory per language: `sdks/<lang>/`.

Built, in order: the server SDKs `go`, `node`, `python`, `java`, `dotnet`, `ruby`, `php`, then the client
SDKs `js` (browser), `react`, `react-native`, `ios`, `android` and `flutter`.

## Versioning and tags

Each SDK is versioned independently of the server and of the other SDKs, following the X.Y.Z and 0.Y.Z
rules in [AGENTS.md](../AGENTS.md). Release tags are:

| SDK | Tag | Example |
|---|---|---|
| Go | `sdks/go/vX.Y.Z` | `sdks/go/v1.2.0` |
| Every other language | `sdk-<lang>/vX.Y.Z` | `sdk-node/v1.2.0` |
| iOS split repository `mmhy2003/megooflags-swift` | `X.Y.Z` (pushed by the release job) | `1.2.0` |

Go is the exception because Go modules in a repository subdirectory must be tagged with the
subdirectory path. SwiftPM reads versions from plain `X.Y.Z` tags at the root of a repository, so the
`sdk-ios/vX.Y.Z` release pushes `sdks/ios` to a separate read-only repository with that tag. Server
releases keep the plain `vX.Y.Z` tags. As with server releases, only a maintainer pushes SDK tags.

The release steps, the one-time registry setup and the publishing switches are in
[RELEASING.md](RELEASING.md).

## Requirements

Each SDK is an [OpenFeature](https://openfeature.dev) provider and must pass its shared cases in
[`spec/`](../spec/README.md) before it can be released: server SDKs the conformance suite, client SDKs
the cases in [`spec/client`](../spec/client/README.md).

## Status

| SDK | Directory | Version | Kind | Shared cases | CI jobs |
|---|---|---|---|---|---|
| Go | [`sdks/go`](go/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-go`, `sdk-go-e2e` |
| Node.js | [`sdks/node`](node/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-node`, `sdk-node-e2e` |
| Python | [`sdks/python`](python/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-python`, `sdk-python-e2e` |
| Java | [`sdks/java`](java/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-java`, `sdk-java-e2e` |
| .NET | [`sdks/dotnet`](dotnet/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-dotnet`, `sdk-dotnet-e2e` |
| Ruby | [`sdks/ruby`](ruby/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-ruby`, `sdk-ruby-e2e` |
| PHP | [`sdks/php`](php/README.md) | 0.1.0 (Beta) | OpenFeature provider | conformance 100 % | `sdk-php`, `sdk-php-e2e` |
| JavaScript | [`sdks/js`](js/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-js`, `sdk-js-e2e` |
| React | [`sdks/react`](react/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-react`, `sdk-react-e2e` |
| React Native | [`sdks/react-native`](react-native/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-react-native`, `sdk-react-native-e2e` |
| iOS | [`sdks/ios`](ios/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-ios`, `sdk-ios-e2e` |
| Android | [`sdks/android`](android/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-android`, `sdk-android-e2e` |
| Flutter | [`sdks/flutter`](flutter/README.md) | 0.1.0 (Beta) | OpenFeature client provider | spec/client 100 % | `sdk-flutter`, `sdk-flutter-e2e` |
