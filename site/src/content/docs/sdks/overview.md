---
title: SDK overview
description: The 13 MegooFlags SDKs, how to choose one, OpenFeature, SDK keys and offline start.
---

MegooFlags has 13 SDKs, all in the repository under `sdks/<lang>/`. Every one of them is an
[OpenFeature](https://openfeature.dev) provider: your code calls the OpenFeature API of its language, and
the MegooFlags provider answers. Each SDK is versioned on its own, independently of the server.

## The SDKs

| SDK                                            | Package                                                        | Kind   | Evaluation | Status       |
| ---------------------------------------------- | -------------------------------------------------------------- | ------ | ---------- | ------------ |
| [Go](/MegooFlags/sdks/go/)                     | `github.com/mmhy2003/MegooFlags/sdks/go` (Go module)           | Server | Local      | 0.1.0 (Beta) |
| [Node.js](/MegooFlags/sdks/node/)              | `@megooflags/node` (npm)                                       | Server | Local      | 0.1.0 (Beta) |
| [Python](/MegooFlags/sdks/python/)             | `megooflags` (PyPI)                                            | Server | Local      | 0.1.0 (Beta) |
| [Java](/MegooFlags/sdks/java/)                 | `io.github.mmhy2003:megooflags` (Maven Central)                | Server | Local      | 0.1.0 (Beta) |
| [.NET](/MegooFlags/sdks/dotnet/)               | `MegooFlags` (NuGet)                                           | Server | Local      | 0.1.0 (Beta) |
| [Ruby](/MegooFlags/sdks/ruby/)                 | `megooflags` (RubyGems)                                        | Server | Local      | 0.1.0 (Beta) |
| [PHP](/MegooFlags/sdks/php/)                   | `megooflags/megooflags` (Packagist)                            | Server | Local      | 0.1.0 (Beta) |
| [JavaScript](/MegooFlags/sdks/js/) (browser)   | `@megooflags/js` (npm)                                         | Client | Remote     | 0.1.0 (Beta) |
| [React](/MegooFlags/sdks/react/)               | `@megooflags/react` (npm)                                      | Client | Remote     | 0.1.0 (Beta) |
| [React Native](/MegooFlags/sdks/react-native/) | `@megooflags/react-native` (npm)                               | Client | Remote     | 0.1.0 (Beta) |
| [iOS](/MegooFlags/sdks/ios/)                   | `https://github.com/mmhy2003/megooflags-swift` (Swift Package) | Client | Remote     | 0.1.0 (Beta) |
| [Android](/MegooFlags/sdks/android/)           | `io.github.mmhy2003:megooflags-android` (Maven Central)        | Client | Remote     | 0.1.0 (Beta) |
| [Flutter](/MegooFlags/sdks/flutter/)           | `megooflags` (pub.dev)                                         | Client | Remote     | 0.1.0 (Beta) |

Beta means the features are complete and tested, and the API may still change before 1.0.0. Server SDKs pass the shared conformance suite, client SDKs the
shared client cases, both at 100 %.

## How to choose

- **Code that runs on your servers** (APIs, workers, jobs) uses a **server SDK**. It downloads the
  environment's whole ruleset once, evaluates every flag in-process with no network call (p99 under
  1 ms on 10 000 flags), follows changes over SSE with a polling fallback (PHP through its sync
  daemon), and keeps serving the last ruleset when the server is unreachable.
- **Code that runs on your users' devices** (browsers, mobile apps) uses a **client SDK**. It sends the
  evaluation context to `POST /sdk/v1/evaluate` and receives the evaluated values, so no targeting rule
  ever leaves the server. It follows changes over SSE, caches the last values on the device, and can
  start from values your server rendered.
- React apps use the React SDK, which wraps the JavaScript SDK; React Native apps use the React Native
  SDK.

Both kinds give the same answer for the same context: they follow the same
[evaluation spec](/MegooFlags/reference/evaluation/).

## OpenFeature

You install the MegooFlags package and the OpenFeature SDK of your language, register the provider
once at start-up, and evaluate through an OpenFeature client. In Node.js:

```js
import { OpenFeature } from "@openfeature/server-sdk";
import { MegooFlagsProvider } from "@megooflags/node";

await OpenFeature.setProviderAndWait(
  new MegooFlagsProvider({
    sdkKey: process.env.MEGOOFLAGS_SDK_KEY, // a server key, mf_server_…
    baseUrl: "https://flags.example.com",
  }),
);

const client = OpenFeature.getClient();
const enabled = await client.getBooleanValue("new-checkout", false, {
  targetingKey: "user-123",
  plan: "pro",
});
```

The OpenFeature targeting key is the context's `key` attribute in MegooFlags rules, and what percentage
rollouts bucket on by default. The other context fields are attributes your rules can match. Because
your code only calls OpenFeature, moving to MegooFlags from another tool means replacing the provider,
not the calls: see [Migrate](/MegooFlags/migrate/overview/).

## SDK keys

Each SDK key belongs to one environment (see [Concepts](/MegooFlags/start/concepts/#sdk-keys)):

- Server SDKs need a **server key**, `mf_server_…`, since they download the rules. They refuse a client
  key, or any key without the `mf_server_` prefix, when the provider is created. Keep server keys
  secret: never ship one to a browser or a mobile app.
- Client SDKs need a **client key**, `mf_client_…`, which can only ask for evaluated values. They refuse
  a server key.

Create keys on the project's **Keys** page in the web UI, or with the API as in the
[quick start](/MegooFlags/start/quick-start/).

## Offline start from a bootstrap file

A server SDK can start from a ruleset file instead of the network: for tests, for CI without a
MegooFlags server, or so that a service starts with flags even when the server is down. Download an
environment's ruleset, byte for byte what the SDKs receive, with the [CLI](/MegooFlags/use/cli/#ruleset):

```bash
megooflags ruleset pull -p shop --env development -o ruleset.json    # wrote ruleset.json (12 flags)
```

Then pass the file to the provider's bootstrap option: `bootstrapFile` in Node.js, Java and PHP,
`BootstrapFile` in Go and .NET, `bootstrap_file` in Python and Ruby.

```js
new MegooFlagsProvider({
  sdkKey: process.env.MEGOOFLAGS_SDK_KEY,
  bootstrapFile: "ruleset.json",
  offline: true, // no network at all: serve the file only
});
```

At start-up a server SDK looks for a ruleset in the bootstrap file, then in its cache directory, then
on the network. With a bootstrap file and without `offline`, it starts at once from the file and
refreshes from the network in the background. With `offline`, it makes no network calls and sends no
events.

Client SDKs start from values instead of a ruleset: the `bootstrap` option takes an evaluate response,
`{ "results": [...] }`, for example rendered by your server for the first page. See each SDK's page.
