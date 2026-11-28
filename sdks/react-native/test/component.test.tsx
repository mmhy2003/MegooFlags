// @vitest-environment jsdom
import { OpenFeature } from "@openfeature/web-sdk";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider } from "@megooflags/js";
import { MegooFlagsProvider, useBooleanFlagValue } from "../src/index.js";
import {
  EVALUATE,
  evaluated,
  FakeServer,
  STREAM,
} from "../../js/test/helpers/fake-server.js";
import { FakeAsyncStorage, silent } from "./helpers/memory.js";

function Flag() {
  const value = useBooleanFlagValue("f", false);
  return <span data-testid="flag">{String(value)}</span>;
}

let server: FakeServer;

beforeEach(async () => {
  OpenFeature.setLogger(silent);
  // Node has no AsyncStorage: the platform warns once on the console.
  vi.spyOn(console, "warn").mockImplementation(() => {});
  server = await FakeServer.start();
  server.reply(STREAM, { stream: true });
});

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  await OpenFeature.clearProviders();
  await server.stop();
});

describe("MegooFlagsProvider (React Native)", () => {
  it("renders the server value", async () => {
    server.reply(
      EVALUATE,
      evaluated([
        {
          key: "f",
          value: true,
          variant: "true",
          reason: "TARGETING_MATCH",
          error_code: null,
        },
      ]),
    );

    render(
      <MegooFlagsProvider
        sdkKey="mf_client_secret123"
        baseUrl={server.url}
        context={{ targetingKey: "user-1" }}
        options={{ logger: silent, storage: new FakeAsyncStorage() }}
      >
        <Flag />
      </MegooFlagsProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("flag").textContent).toBe("true"),
    );
    expect(OpenFeature.getProvider()).toBeInstanceOf(MegooFlagsWebProvider);
  });
});
