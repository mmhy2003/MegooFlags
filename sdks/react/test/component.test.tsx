import {
  NOOP_PROVIDER,
  OpenFeature,
  type EvaluationContext,
} from "@openfeature/web-sdk";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider } from "@megooflags/js";
import type { Platform, StorageLike } from "@megooflags/js/platform";
import { webPlatform } from "@megooflags/js/platform";
import {
  MegooFlagsProvider,
  OpenFeatureTestProvider,
  useBooleanFlagValue,
} from "../src/index.js";
import { createMegooFlagsComponent } from "../src/platform.js";
import {
  EVALUATE,
  evaluated,
  FakeServer,
  type Received,
  STREAM,
} from "../../js/test/helpers/fake-server.js";

const KEY = "mf_client_secret123";

const silent = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

const result = (value: boolean) => ({
  key: "f",
  value,
  variant: String(value),
  reason: "TARGETING_MATCH",
  error_code: null,
});

class MemoryStorage implements StorageLike {
  readonly items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
}

/** A web platform with an in-memory cache, so tests never share storage. */
const memoryPlatform = (): Platform => ({
  ...webPlatform(),
  storage: new MemoryStorage(),
});

const Provider = createMegooFlagsComponent(memoryPlatform);

/** contextKey is the `key` of an evaluate request's context. */
const contextKey = (req: Received) =>
  (JSON.parse(req.body) as { context: { key?: string } }).context.key;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function Flag() {
  const value = useBooleanFlagValue("f", false);
  return <span data-testid="flag">{String(value)}</span>;
}

const flag = () => screen.getByTestId("flag").textContent;

/** openStreams counts the stream connections the client has not closed. */
async function openStreams(): Promise<number> {
  let open = 0;
  for (const s of server.streams) {
    const closed = await Promise.race([
      s.closed.then(() => true),
      sleep(10).then(() => false),
    ]);
    if (!closed) {
      open++;
    }
  }
  return open;
}

let server: FakeServer;

beforeEach(async () => {
  OpenFeature.setLogger(silent);
  server = await FakeServer.start();
  server.reply(STREAM, { stream: true });
});

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  await OpenFeature.clearProviders();
  await server.stop();
});

describe("MegooFlagsProvider", () => {
  it("renders the defaults first, then the server value", async () => {
    server.reply(EVALUATE, evaluated([result(true)], { delayMs: 50 }));

    render(
      <MegooFlagsProvider
        sdkKey={KEY}
        baseUrl={server.url}
        options={{ logger: silent, disableCache: true }}
      >
        <Flag />
      </MegooFlagsProvider>,
    );

    expect(flag()).toBe("false");
    await waitFor(() => expect(flag()).toBe("true"));
    expect(OpenFeature.getProvider()).toBeInstanceOf(MegooFlagsWebProvider);
  });

  it("calls setContext once when the context prop changes, not for an equal object", async () => {
    server.reply(EVALUATE, (req) =>
      evaluated([result(contextKey(req) !== "user-2")]),
    );
    const setContext = vi.spyOn(OpenFeature, "setContext");
    const tree = (context: EvaluationContext) => (
      <Provider
        sdkKey={KEY}
        baseUrl={server.url}
        context={context}
        options={{ logger: silent }}
      >
        <Flag />
      </Provider>
    );

    const { rerender } = render(tree({ targetingKey: "user-1", plan: "pro" }));
    await waitFor(() => expect(flag()).toBe("true"));
    expect(setContext).not.toHaveBeenCalled();

    rerender(tree({ targetingKey: "user-2", plan: "pro" }));
    await waitFor(() => expect(flag()).toBe("false"));
    expect(setContext).toHaveBeenCalledTimes(1);

    // An equal context in a new object, keys in another order.
    rerender(tree({ plan: "pro", targetingKey: "user-2" }));
    await sleep(100);
    expect(setContext).toHaveBeenCalledTimes(1);
    expect(
      server.received(EVALUATE).filter((r) => contextKey(r) === "user-2"),
    ).toHaveLength(1);
  });

  it("closes the provider on unmount and sends no further requests", async () => {
    server.reply(EVALUATE, evaluated([result(true)]));
    const { unmount } = render(
      <Provider sdkKey={KEY} baseUrl={server.url} options={{ logger: silent }}>
        <Flag />
      </Provider>,
    );
    await waitFor(() => expect(flag()).toBe("true"));
    const stream = await server.nextStream();

    unmount();

    await stream.closed;
    expect(OpenFeature.getProvider()).toBe(NOOP_PROVIDER);
    await sleep(100); // the last exposures are flushed on close
    const sent = server.requests.length;
    await sleep(300);
    expect(server.requests.length).toBe(sent);
  });

  it("leaves exactly one active provider and one stream under StrictMode", async () => {
    server.reply(EVALUATE, evaluated([result(true)]));
    const created: Platform[] = [];
    const Counted = createMegooFlagsComponent(() => {
      const p = memoryPlatform();
      created.push(p);
      return p;
    });

    render(
      <StrictMode>
        <Counted sdkKey={KEY} baseUrl={server.url} options={{ logger: silent }}>
          <Flag />
        </Counted>
      </StrictMode>,
    );

    await waitFor(() => expect(flag()).toBe("true"));
    await server.nextStream();
    await sleep(200);
    expect(created).toHaveLength(2); // StrictMode runs the effect twice
    expect(await openStreams()).toBe(1);
    expect(OpenFeature.getProvider()).toBeInstanceOf(MegooFlagsWebProvider);
  });
});

describe("re-exports", () => {
  it("OpenFeatureTestProvider serves its flag values", async () => {
    render(
      <OpenFeatureTestProvider flagValueMap={{ f: true }}>
        <Flag />
      </OpenFeatureTestProvider>,
    );
    await waitFor(() => expect(flag()).toBe("true"));
  });
});
