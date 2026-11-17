import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

// The Nodes API answers only on a control plane, so the e2e API runs with
// MEGOOFLAGS_ROLE=control-plane (AGENTS.md §9).

const PAGE = "/nodes";
// Unique per run, so afterAll removes exactly this run's worker.
const WORKER = `e2e-${Date.now()}`;

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  const list = await ok(await api.get("/api/nodes"), "list nodes");
  const { nodes } = (await list.json()) as {
    nodes: { id: string; name: string; status: string }[];
  };
  for (const node of nodes) {
    if (node.name !== WORKER) continue;
    if (node.status !== "revoked")
      await ok(
        await api.post(`/api/nodes/${node.id}/revoke`),
        `revoke ${node.name}`,
      );
    await ok(await api.delete(`/api/nodes/${node.id}`), `delete ${node.name}`);
  }
  await api.dispose();
});

/** The table row at desktop widths. */
function row(page: Page, name: string) {
  return page.getByRole("row").filter({ hasText: name });
}

async function openActions(page: Page, name: string) {
  await page.getByRole("button", { name: `Actions for ${name}` }).click();
}

async function confirmAction(page: Page, action: string, label: string) {
  await openActions(page, WORKER);
  await page.getByRole("menuitem", { name: action }).click();
  await page
    .getByRole("alertdialog", { name: `${label} ${WORKER}?` })
    .getByRole("button", { name: label })
    .click();
}

/** The token step of the add and rotate dialogs: checks it and returns the token. */
async function readToken(page: Page, dialog: string): Promise<string> {
  const reveal = page.getByRole("alertdialog", { name: dialog });
  const token = (await reveal.getByRole("code").textContent()) ?? "";
  expect(token).toMatch(/^mf_node_/);
  await expect(
    reveal.getByText("Copy it now. You won't see it again."),
  ).toBeVisible();
  const lines = await reveal.locator("pre").textContent();
  expect(lines?.split("\n")).toEqual([
    "MEGOOFLAGS_ROLE=worker",
    `MEGOOFLAGS_CONTROL_PLANE_URL=${new URL(page.url()).origin}`,
    `MEGOOFLAGS_NODE_TOKEN=${token}`,
  ]);
  await expect(
    reveal.getByRole("button", { name: "Copy .env lines" }),
  ).toBeVisible();
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(reveal).toBeHidden();
  return token;
}

test.describe.serial("nodes", () => {
  let token = "";

  test("adds a worker and shows its token once", async ({ page }) => {
    await page.goto(`/projects/${SHOP}/flags`);
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Nodes" })
      .click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Nodes" }),
    ).toBeVisible();

    await page.getByRole("button", { name: "Add worker" }).click();
    const form = page.getByRole("dialog", { name: "Add worker" });
    await form.getByLabel("Name").fill(WORKER);
    await form.getByRole("button", { name: "Add worker" }).click();
    token = await readToken(page, "Add worker");
    await expect(row(page, WORKER)).toContainText("Pending");

    await page.reload();
    await expect(row(page, WORKER)).toContainText("Pending");
    await expect(row(page, WORKER)).toContainText(token.slice(0, 12));
    await expect(page.getByText(token)).toHaveCount(0);
  });

  test("drains and resumes", async ({ page }) => {
    await page.goto(PAGE);
    await confirmAction(page, "Drain", "Drain");
    await expect(page.getByText(`${WORKER} is draining`)).toBeVisible();
    await expect(row(page, WORKER)).toContainText("Draining");

    await confirmAction(page, "Resume", "Resume");
    await expect(row(page, WORKER)).toContainText("Pending");
  });

  test("rotates the token", async ({ page }) => {
    await page.goto(PAGE);
    await openActions(page, WORKER);
    await page.getByRole("menuitem", { name: "Rotate token" }).click();
    const form = page.getByRole("dialog", {
      name: `Rotate the token of ${WORKER}`,
    });
    await form.getByRole("button", { name: "Rotate token" }).click();
    const rotated = await readToken(page, `Rotate the token of ${WORKER}`);
    expect(rotated).not.toBe(token);

    await page.reload();
    await expect(row(page, WORKER)).toContainText(rotated.slice(0, 12));
    await expect(page.getByText(rotated)).toHaveCount(0);
  });

  test("revokes and removes", async ({ page }) => {
    await page.goto(PAGE);
    await openActions(page, WORKER);
    await expect(page.getByRole("menuitem", { name: "Remove" })).toBeDisabled();
    await page.keyboard.press("Escape");

    await confirmAction(page, "Revoke", "Revoke");
    await expect(row(page, WORKER)).toContainText("Revoked");

    await openActions(page, WORKER);
    await expect(page.getByRole("menuitem", { name: "Revoke" })).toBeDisabled();
    await expect(page.getByRole("menuitem", { name: "Remove" })).toBeEnabled();
    await page.keyboard.press("Escape");

    await confirmAction(page, "Remove", "Remove");
    await expect(page.getByText(`${WORKER} removed`)).toBeVisible();
    await expect(row(page, WORKER)).toHaveCount(0);
  });
});

test.describe("as a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("a viewer has no Nodes link", async ({ page }) => {
    await page.goto(`/projects/${SHOP}/flags`);
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Flags" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Nodes" })).toHaveCount(0);

    await page.goto(PAGE);
    await expect(page.getByRole("button", { name: "Add worker" })).toHaveCount(
      0,
    );
  });
});
