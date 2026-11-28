# MegooFlags React Native SDK

`@megooflags/react-native` is the MegooFlags client SDK for React Native. One component,
`<MegooFlagsProvider>`, registers a [`@megooflags/js`](../js) [OpenFeature](https://openfeature.dev)
provider while it is mounted, and the OpenFeature React hooks read the flags in its children. Values
are evaluated by the server for the context you pass, with a client key, so no targeting rule ever
reaches the device. They follow changes over SSE with a polling fallback, are cached in AsyncStorage,
can start from bootstrap values, and exposures are sent in batches.

Status: 0.1.0, Beta: the API may still change before 1.0.0. Works with React Native 0.76 and later, and with
Expo: the package has no native code of its own.

## Install

```sh
npm i @megooflags/react-native @react-native-async-storage/async-storage
```

`@openfeature/web-sdk` (`^1.10.0`), `@openfeature/react-sdk` (`^1.4.1`), `react` (`>=18`) and
`react-native` (`>=0.76`) are peer dependencies; `@megooflags/js` and `@megooflags/react` are regular
dependencies. npm and pnpm install the peers for you; Yarn users add them by hand:

```sh
yarn add @megooflags/react-native @react-native-async-storage/async-storage @openfeature/web-sdk @openfeature/react-sdk
```

With Expo, install AsyncStorage with `npx expo install @react-native-async-storage/async-storage`.
The package is ESM only.

## Quick start

```jsx
import {
  MegooFlagsProvider,
  useBooleanFlagValue,
} from "@megooflags/react-native";

export function App({ user }) {
  return (
    <MegooFlagsProvider
      sdkKey="mf_client_…" // a client key
      baseUrl="https://flags.example.com"
      context={{ targetingKey: user.id, plan: user.plan }}
    >
      <Checkout />
    </MegooFlagsProvider>
  );
}

function Checkout() {
  const newCheckout = useBooleanFlagValue("new-checkout", false);
  return newCheckout ? <NewCheckout /> : <OldCheckout />;
}
```

The props (`sdkKey`, `baseUrl`, `context`, `options`, `domain`), what is read when, domains and
testing with `OpenFeatureTestProvider` are the same as in [`@megooflags/react`](../react/README.md),
and every hook of `@openfeature/react-sdk` is re-exported from this package too. The options are
documented in the [`@megooflags/js` README](../js/README.md#configuration).

## AsyncStorage (optional)

When `@react-native-async-storage/async-storage` is installed, the last values are cached there, so
the app starts with them after a restart and serves them while it is offline (see
[Cache and offline](../js/README.md#cache-and-offline)). It is optional: without it the values are
kept in memory only, and the SDK logs one warning per process:

```
megooflags: @react-native-async-storage/async-storage is not installed; values are not cached across restarts
```

To use another store, pass `options.storage`: an object with `getItem`, `setItem` and `removeItem`,
synchronous or returning promises.

## Background and foreground

The SDK follows `AppState`. When the app goes to the background (`background` or `inactive`), the
waiting exposures are sent at once; 30 seconds later the stream is closed and polling stops, so a
backgrounded app keeps no connection open. When the app is `active` again, the stream reconnects at
once with the id of the last change it saw, and the values are fetched again if a flag changed in
the meantime. A short trip to the background (under 30 seconds) keeps the stream open.

## Why the stream uses XMLHttpRequest

React Native's `fetch` cannot read a response body while it is still arriving, so it cannot follow a
server-sent events stream. The SDK reads the stream with `XMLHttpRequest` progress events instead,
which needs no native module and no extra library. `XMLHttpRequest` keeps the whole response in
memory, so the SDK closes the stream after 4 MiB and reconnects with the id of the last change, so
no change is missed. Evaluate and events requests use `fetch`.

## Android and `http://`

Android blocks cleartext HTTP by default (API 28 and later), and release builds of a new React
Native app do too. Use an `https://` `baseUrl` in production. For a local server over `http://`
during development, allow cleartext traffic for that host with a
[network security configuration](https://developer.android.com/privacy-and-security/security-config),
or `android:usesCleartextTraffic="true"` in a debug manifest. iOS App Transport Security blocks
`http://` the same way, except for `localhost`.

## OpenFeature and React Native

The OpenFeature project does not list React Native as a supported platform of its web and React
SDKs. They are plain JavaScript and work there; this SDK's tests run them with React Native's
adapters. If something in OpenFeature breaks on React Native, please open an issue with us.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-react-native/vX.Y.Z`, and only maintainers push those tags.
The `VERSION` export holds the version.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/react-native/`, after building `@megooflags/js` and `@megooflags/react` (the local links
resolve to their `dist/`):

```sh
pnpm --dir ../js install --frozen-lockfile && pnpm --dir ../js build
pnpm --dir ../react install --frozen-lockfile && pnpm --dir ../react build
pnpm install --frozen-lockfile
pnpm format:check && pnpm lint && pnpm typecheck
pnpm test
pnpm build && pnpm pack
```

The end-to-end test runs in Node: the provider gets the React Native platform with a fake `AppState`,
an in-memory AsyncStorage and an `XMLHttpRequest` shim over Node's HTTP client. It needs a running
stack and an API token (see the `sdk-react-native-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
pnpm e2e
```

Without both variables it is skipped.
