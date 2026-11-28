import { createHmac } from "node:crypto";

import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, createTestFlag, ok, SHOP } from "./support/api";
import { noHorizontalScroll } from "./support/checks";
import { type Receiver, startReceiver } from "./support/receiver";
import { VIEWER } from "./support/users";

// Webhooks reach the receiver on 127.0.0.1 only because the e2e API runs with
// MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true (AGENTS.md §9).

const PAGE = `/projects/${SHOP}/integrations`;
// Every integration this run creates starts with it, so afterAll removes exactly those.
const RUN = `e2e-${Date.now().toString(36)}`;
const WEBHOOK = `${RUN} webhook`;
const RENAMED = `${RUN} renamed`;
const TEAMS = `${RUN} teams`;

let api: APIRequestContext;
let receiver: Receiver;

test.beforeAll(async () => {
  api = await adminApi();
  receiver = await startReceiver();
});

test.afterAll(async () => {
  const list = await ok(
    await api.get(`/api/projects/${SHOP}/integrations`),
    "list integrations",
  );
  for (const i of (await list.json()) as { id: string; name: string }[]) {
    if (i.name.startsWith(RUN))
      await ok(
        await api.delete(`/api/projects/${SHOP}/integrations/${i.id}`),
        `delete ${i.name}`,
      );
  }
  await api.dispose();
  await receiver.close();
});

/** The table row at desktop widths. */
function row(page: Page, name: string) {
  return page.getByRole("row").filter({ hasText: name });
}

async function rowAction(page: Page, name: string, action: string) {
  await page.getByRole("button", { name: `Actions for ${name}` }).click();
  await page.getByRole("menuitem", { name: action }).click();
}

function openAdd(page: Page) {
  return page.getByRole("button", { name: "Add integration" }).first().click();
}

function verify(secret: string, header: string, body: string): boolean {
  const parts = Object.fromEntries(
    header.split(",").map((p) => p.split("=", 2) as [string, string]),
  );
  const expected = createHmac("sha256", secret)
    .update(`${parts.t}.${body}`)
    .digest("hex");
  return parts.v1 === expected;
}

test.describe.serial("integrations", () => {
  let secret = "";
  let flag = "";

  test("adds a webhook and shows its secret once", async ({ page }) => {
    flag = await createTestFlag(api, { prefix: "hook" });
    await page.goto(PAGE);
    await expect(
      page.getByRole("heading", { level: 1, name: "Integrations" }),
    ).toBeVisible();
    await openAdd(page);
    const form = page.getByRole("dialog", { name: "Add integration" });
    await expect(form.getByRole("radio", { name: "Webhook" })).toBeChecked();
    await form.getByLabel("Name").fill(WEBHOOK);
    await form.getByLabel("URL").fill(receiver.url);
    await form.getByRole("button", { name: "Add integration" }).click();

    const reveal = page.getByRole("alertdialog", { name: "Signing secret" });
    secret = (await reveal.getByRole("code").textContent()) ?? "";
    expect(secret).toMatch(/^mf_whsec_/);
    await expect(
      reveal.getByText("Copy it now. You won't see it again."),
    ).toBeVisible();
    await reveal.getByRole("button", { name: "Done" }).click();
    await expect(reveal).toBeHidden();
    await expect(row(page, WEBHOOK)).toContainText("Active");
  });

  test("a flag toggle reaches the receiver, signed", async () => {
    await ok(
      await api.post(
        `/api/projects/${SHOP}/flags/${flag}/environments/development/toggle`,
        { data: { enabled: true } },
      ),
      `toggle ${flag}`,
    );
    const isToggle = (body: string) => {
      const event = JSON.parse(body) as {
        type: string;
        resource: { key: string };
      };
      return event.type === "flag.toggle" && event.resource.key === flag;
    };
    await expect
      .poll(() => receiver.requests.some((r) => isToggle(r.body)), {
        timeout: 20_000,
      })
      .toBe(true);
    const request = receiver.requests.find((r) => isToggle(r.body))!;
    expect(request.method).toBe("POST");
    expect(request.headers["x-megooflags-event"]).toBe("flag.toggle");
    const signature = request.headers["x-megooflags-signature"] as string;
    expect(signature).toMatch(/^t=\d+,v1=[0-9a-f]{64}$/);
    expect(verify(secret, signature, request.body)).toBe(true);
    expect(verify("mf_whsec_wrong", signature, request.body)).toBe(false);
  });

  test("the delivery log shows the delivery and its body", async ({ page }) => {
    await page.goto(PAGE);
    await rowAction(page, WEBHOOK, "Deliveries");
    const log = page.getByRole("dialog", { name: `Deliveries: ${WEBHOOK}` });
    const delivery = log
      .getByRole("list", { name: "Deliveries" })
      .getByRole("listitem")
      .filter({ hasText: "flag.toggle" });
    await expect(delivery).toContainText("Delivered");
    await expect(delivery).toContainText("HTTP 200");
    const details = delivery.getByRole("button", { name: "Details" });
    await details.click();
    await expect(details).toHaveAttribute("aria-expanded", "true");
    const body = delivery.getByRole("region", { name: "Request body" });
    await expect(body).toContainText('"type": "flag.toggle"');
    await expect(body).toContainText(`"key": "${flag}"`);
  });

  test("a failed test can be sent again", async ({ page }) => {
    receiver.setStatus(404);
    await page.goto(PAGE);
    await rowAction(page, WEBHOOK, "Send test");
    await expect(page.getByText("Test failed: 404")).toBeVisible();
    await expect(row(page, WEBHOOK)).toContainText("Failing");

    await rowAction(page, WEBHOOK, "Deliveries");
    const log = page.getByRole("dialog", { name: `Deliveries: ${WEBHOOK}` });
    const items = log
      .getByRole("list", { name: "Deliveries" })
      .getByRole("listitem");
    const ping = items.filter({ hasText: "ping" }).first();
    await expect(ping).toContainText("Failed");
    await expect(ping).toContainText("HTTP 404");
    const before = await items.count();
    await ping.getByRole("button", { name: "Send again" }).click();
    await expect(page.getByText("Queued again")).toBeVisible();
    await expect(items).toHaveCount(before + 1);
    receiver.setStatus(200);
  });

  test("edits, pauses and deletes the webhook", async ({ page }) => {
    await page.goto(PAGE);
    await rowAction(page, WEBHOOK, "Edit");
    const form = page.getByRole("dialog", { name: "Edit integration" });
    await expect(form.getByRole("radio", { name: "Webhook" })).toBeDisabled();
    await expect(
      form.getByText("Leave empty to keep the current URL"),
    ).toBeVisible();
    await form.getByLabel("Name").fill(RENAMED);
    await form.getByRole("button", { name: "Save" }).click();
    await expect(form).toBeHidden();
    await expect(row(page, RENAMED)).toBeVisible();

    await rowAction(page, RENAMED, "Pause");
    await expect(row(page, RENAMED)).toContainText("Paused");

    await rowAction(page, RENAMED, "Delete");
    await page
      .getByRole("alertdialog", { name: "Delete integration?" })
      .getByRole("button", { name: "Delete integration" })
      .click();
    await expect(row(page, RENAMED)).toHaveCount(0);
  });

  test("Slack needs hooks.slack.com and Teams saves", async ({ page }) => {
    await page.goto(PAGE);
    await openAdd(page);
    const form = page.getByRole("dialog", { name: "Add integration" });
    await form.getByRole("radio", { name: "Slack" }).check();
    await expect(
      form.getByText("Incoming webhook URL (hooks.slack.com)"),
    ).toBeVisible();
    await form.getByLabel("Name").fill(`${RUN} slack`);
    await form.getByLabel("URL").fill("https://example.com/services/T0/B0/x");
    await form.getByRole("button", { name: "Add integration" }).click();
    await expect(
      form.getByText("Use a Slack incoming webhook URL (hooks.slack.com)"),
    ).toBeVisible();
    await expect(form.getByLabel("URL")).toHaveAttribute(
      "aria-invalid",
      "true",
    );

    await form.getByRole("radio", { name: "Microsoft Teams" }).check();
    await form.getByLabel("Name").fill(TEAMS);
    await form.getByLabel("URL").fill(receiver.url);
    await form.getByRole("button", { name: "Add integration" }).click();
    await expect(form).toBeHidden();
    await expect(
      page.getByRole("alertdialog", { name: "Signing secret" }),
    ).toHaveCount(0);
    await expect(row(page, TEAMS)).toContainText("Microsoft Teams");
  });
});

test.describe("as a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("has no Integrations link and no data", async ({ page }) => {
    await page.goto(`/projects/${SHOP}/flags`);
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Flags" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Integrations" })).toHaveCount(
      0,
    );

    await page.goto(PAGE);
    await expect(page.getByText("Couldn't load integrations")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Add integration" }),
    ).toHaveCount(0);
  });
});

test.describe("at 344 px", () => {
  test.use({ viewport: { width: 344, height: 800 } });

  test("shows cards and a full-screen form", async ({ page }) => {
    await page.goto(PAGE);
    const cards = page.getByRole("list", { name: "Integrations" });
    await expect(cards.getByRole("listitem").first()).toBeVisible();
    await expect(page.getByRole("table")).toBeHidden();
    expect(await noHorizontalScroll(page)).toBe(true);

    await openAdd(page);
    const form = page.getByRole("dialog", { name: "Add integration" });
    await expect(form).toBeVisible();
    await expect
      .poll(async () => form.boundingBox())
      .toEqual({ x: 0, y: 0, width: 344, height: 800 });
    expect(await noHorizontalScroll(page)).toBe(true);
  });
});
