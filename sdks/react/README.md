# MegooFlags React SDK

`@megooflags/react` is the MegooFlags client SDK for React. One component, `<MegooFlagsProvider>`,
registers a [`@megooflags/js`](../js) [OpenFeature](https://openfeature.dev) web provider while it is
mounted, and the OpenFeature React hooks (re-exported from `@openfeature/react-sdk`) read the flags in
its children. Values are evaluated by the server for the context you pass, with a client key, so no
targeting rule ever reaches the browser.

Status: 0.1.0, Alpha: the API may still change. Works with React 18 and 19.

## Install

```sh
npm i @megooflags/react
```

`@openfeature/web-sdk` (`^1.10.0`), `@openfeature/react-sdk` (`^1.4.1`) and `react` (`>=18`) are peer
dependencies, and `@megooflags/js` is a regular dependency. npm and pnpm install the peers for you;
Yarn users add them by hand:

```sh
yarn add @megooflags/react @openfeature/web-sdk @openfeature/react-sdk
```

The package is ESM only.

## Quick start

```jsx
import { MegooFlagsProvider, useBooleanFlagValue } from "@megooflags/react";

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

Components re-render when the values change (a flag edited in MegooFlags, a new context). Until the
first values arrive the hooks return your default. Every hook of `@openfeature/react-sdk` is
re-exported: `useFlag`, `useSuspenseFlag`, `useBooleanFlagValue`, `useBooleanFlagDetails` and their
string, number and object versions, `useOpenFeatureClient`, `useOpenFeatureClientStatus`,
`useWhenProviderReady` and `OpenFeatureTestProvider`.

## Props

| Prop       | Default              | Notes                                                                                                         |
| ---------- | -------------------- | ------------------------------------------------------------------------------------------------------------- |
| `sdkKey`   | (required)           | A client key, `mf_client_…`. A server key is refused.                                                         |
| `baseUrl`  | none                 | The MegooFlags URL. Required unless `options.offline` is true.                                                |
| `context`  | `{}`                 | The evaluation context. A change, compared by value, calls `OpenFeature.setContext` and fetches new values.   |
| `options`  | `{}`                 | Every other `@megooflags/js` option: `bootstrap`, `initTimeoutMs`, `flushIntervalMs`, `logger`, `storage`, …. |
| `domain`   | the default provider | The OpenFeature domain to register the provider for.                                                          |
| `children` | none                 | Rendered inside an `OpenFeatureProvider` for the same domain.                                                 |

The options are documented in the [`@megooflags/js` README](../js/README.md#configuration), with the
cache, updates, background tabs, exposures and errors.

### What is read when

The provider is created on mount and again when `sdkKey`, `baseUrl` or `domain` change; the old one
is closed first. `options` are read only then: changing `options` alone has no effect, so you can
pass an object literal without memoising it. `context` is the one prop that is applied while the
provider lives. On unmount the component closes the provider, which sends the waiting exposures, and
registers OpenFeature's no-op provider again.

Mount one `<MegooFlagsProvider>` near the root of your app, not one per component: every instance
opens its own stream.

## Domains

Without `domain`, the component sets the default OpenFeature provider. With `domain="checkout"` it
sets the provider of that domain only, and its children's hooks read that domain. Two components with
different domains can live side by side, for example with two MegooFlags environments.

## Server rendering

Nothing runs during server rendering: the provider is registered in an effect, on the client. On the
server, and on the client until the provider is registered, children render with the defaults you
pass to the hooks. To render real values on the first paint, evaluate on your server and pass them as
`options.bootstrap` (see [Bootstrap](../js/README.md#bootstrap)).

## StrictMode

Under `<StrictMode>` React runs effects twice in development. The component is safe there: React's
cleanup closes the first provider before the second is created and registered, so one provider stays
open. Production builds run the effect once.

## Testing

Render components under `OpenFeatureTestProvider` instead of `<MegooFlagsProvider>`: it serves fixed
values with no network.

```jsx
import { OpenFeatureTestProvider } from "@megooflags/react";
import { render, screen } from "@testing-library/react";

render(
  <OpenFeatureTestProvider flagValueMap={{ "new-checkout": true }}>
    <Checkout />
  </OpenFeatureTestProvider>,
);
```

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-react/vX.Y.Z`, and only maintainers push those tags. The
`VERSION` export holds the version. `@megooflags/react/platform` is internal (it is what
`@megooflags/react-native` builds on) and may change in any release.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/react/`, after building `@megooflags/js` (the local link resolves to its `dist/`):

```sh
pnpm --dir ../js install --frozen-lockfile && pnpm --dir ../js build
pnpm install --frozen-lockfile
pnpm format:check && pnpm lint && pnpm typecheck
pnpm test
pnpm build && pnpm pack
```

The end-to-end test renders the component in Chromium, on a page served from
`http://127.0.0.1:4174` (another origin than the API). It needs a running stack and an API token
(see the `sdk-react-e2e` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
pnpm exec playwright install chromium
pnpm e2e
```

Without both variables it is skipped.
