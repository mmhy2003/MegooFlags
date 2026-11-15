// @vitest-environment node
import { OpenFeature } from "@openfeature/web-sdk";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, expect, it } from "vitest";
import { MegooFlagsProvider, useBooleanFlagValue } from "../src/index.js";
import { FakeServer } from "../../js/test/helpers/fake-server.js";

let server: FakeServer;

beforeEach(async () => {
  server = await FakeServer.start();
});

afterEach(async () => {
  await OpenFeature.clearProviders();
  await server.stop();
});

function Flag() {
  return <span>{String(useBooleanFlagValue("f", false))}</span>;
}

it("renderToString renders the defaults and makes no request", async () => {
  const html = renderToString(
    <MegooFlagsProvider sdkKey="mf_client_secret123" baseUrl={server.url}>
      <Flag />
    </MegooFlagsProvider>,
  );

  expect(html).toBe("<span>false</span>");
  await new Promise((r) => setTimeout(r, 100));
  expect(server.requests).toHaveLength(0);
});
