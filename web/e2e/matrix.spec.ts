import { expect, type Page, test } from "@playwright/test";

import { adminApi, ok } from "./support/api";
import {
  axeViolations,
  noHorizontalScroll,
  smallTargets,
} from "./support/checks";

// UI-3, UI-6, UI-12, UI-16, UI-19: every width from a folded phone to a desktop, in both themes.
const WIDTHS = [280, 344, 390, 880, 1024, 1440];
const THEMES = ["light", "dark"] as const;
const FLAG = "/projects/demo/flags/new-checkout?env=development";
// What audit pages show for every entry's time (see fixTimes).
const FIXED_TIME = "2026-12-28T12:28:28Z";
// `mask` hides <time> elements, whose relative text changes from run to run. demo is never
// changed and has no evaluations, so the flag pages (Insights too) are stable.
// Only visible elements are masked: Playwright masks hidden ones too, and their boxes follow
// text that is not on the page (a <time> inside a closed "More" below 1024 px).
const PAGES: {
  name: string;
  path: string;
  mask?: boolean;
  /** Text that changes from run to run, besides <time>; masked too. */
  maskText?: RegExp[];
  /** Text that shows once the page's data has loaded. */
  ready?: string;
  /**
   * Gives every audit entry the same `occurred_at`. The masked <time> still takes the width of
   * its text, which decides how the cards wrap, so a real timestamp changes the layout.
   */
  fixTimes?: boolean;
  detail?: boolean;
  /** Works out `path` at run time, for pages whose URL holds a generated id. */
  resolve?: () => Promise<string>;
}[] = [
  { name: "flags", path: "/projects/demo/flags", mask: true },
  { name: "flag-new", path: "/projects/demo/flags/new", mask: true },
  ...["targeting", "automation", "test", "history", "insights", "settings"].map(
    (tab) => ({
      name: `flag-${tab}`,
      path: `${FLAG}&tab=${tab}`,
      mask: true,
      detail: true,
      // Targeting is read-only until the permissions load.
      ready: tab === "targeting" ? "Add rule" : undefined,
    }),
  ),
  // global-setup.ts seeds a paused webhook in shop, so the page shows a row that never changes.
  { name: "integrations", path: "/projects/shop/integrations", mask: true },
  // global-setup.ts leaves only matrix-worker, which never connects and stays Pending. Its token
  // prefix is random, the snapshot version grows with every flag change the other specs make,
  // and the control-plane version changes with every release.
  {
    name: "nodes",
    path: "/nodes",
    mask: true,
    maskText: [/^mf_node_/, /^v\d+$/, /^Control plane /],
    ready: "matrix-worker",
  },
  // global-setup.ts seeds project gov and the gov-* org items once and never changes them; no
  // spec touches them. Key and token prefixes and the change request id are random.
  {
    name: "change-requests",
    path: "/projects/gov/change-requests?status=all",
    mask: true,
    ready: "Turn on targeting",
  },
  {
    name: "change-request",
    path: "/projects/gov/change-requests",
    resolve: govRequest,
    mask: true,
    maskText: [/^[0-9a-f-]{36}$/],
    ready: "Turn on targeting",
  },
  {
    name: "audit",
    path: "/projects/gov/audit?action=change_request.create",
    mask: true,
    maskText: [/^[0-9a-f-]{36}$/],
    fixTimes: true,
  },
  {
    name: "keys",
    path: "/projects/gov/keys?env=development",
    mask: true,
    maskText: [/^mf_/],
    ready: "gov server",
  },
  // The rollout is paused and the scheduled disable is 300 days ahead, so neither moves.
  {
    name: "flag-automation-live",
    path: "/projects/gov/flags/gov-rollout?env=development&tab=automation",
    mask: true,
    detail: true,
    ready: "Paused",
  },
  {
    name: "segments",
    path: "/projects/gov/segments",
    mask: true,
    ready: "gov-beta",
  },
  {
    name: "segment",
    path: "/projects/gov/segments/gov-beta",
    mask: true,
    ready: "gov-rollout",
  },
  {
    name: "environments",
    path: "/projects/gov/environments",
    mask: true,
    ready: "Needs 1 approval",
  },
  { name: "project-settings", path: "/projects/gov/settings", mask: true },
  { name: "account-tokens", path: "/account/tokens", mask: true },
  {
    name: "settings-members",
    path: "/settings?tab=members&q=%40e2e.test",
    mask: true,
  },
  { name: "settings-teams", path: "/settings?tab=teams", mask: true },
  { name: "settings-roles", path: "/settings?tab=roles", mask: true },
  {
    name: "settings-tokens",
    path: "/settings?tab=tokens",
    mask: true,
    maskText: [/^mf_api_/],
    ready: "gov-token",
  },
  {
    name: "settings-sso",
    path: "/settings?tab=sso",
    mask: true,
    ready: "gov-oidc",
  },
  { name: "settings-security", path: "/settings?tab=security", mask: true },
  {
    name: "settings-audit",
    path: "/settings?tab=audit&action=sso_provider.create&resource_key=gov-oidc",
    mask: true,
    fixTimes: true,
  },
  { name: "kit", path: "/dev/kit" },
];

/** Answers the audit list with every entry's `occurred_at` set to one fixed instant. */
async function fixAuditTimes(page: Page): Promise<void> {
  await page.route(/\/api\/audit(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      items: { occurred_at: string }[];
    };
    for (const entry of body.items) entry.occurred_at = FIXED_TIME;
    await route.fulfill({ response, json: body });
  });
}

/** The detail page of gov's "Turn on targeting" request. */
async function govRequest(): Promise<string> {
  const api = await adminApi();
  try {
    const list = await ok(
      await api.get("/api/projects/gov/change-requests", {
        params: { status: "open" },
      }),
      "list gov change requests",
    );
    const { items } = (await list.json()) as {
      items: { id: string; title: string }[];
    };
    const found = items.find((cr) => cr.title === "Turn on targeting");
    if (!found) throw new Error("global setup seeds Turn on targeting in gov");
    return `/projects/gov/change-requests/${found.id}`;
  } finally {
    await api.dispose();
  }
}

for (const theme of THEMES) {
  test.describe(theme, () => {
    // The admin's stored theme is "system", so the page follows the colour scheme.
    test.use({ colorScheme: theme });

    for (const {
      name,
      path,
      mask,
      maskText = [],
      ready,
      detail,
      resolve,
      fixTimes,
    } of PAGES) {
      for (const width of WIDTHS) {
        test(`${name} at ${width} px`, async ({ page }) => {
          if (fixTimes) await fixAuditTimes(page);
          await page.setViewportSize({ width, height: 900 });
          await page.goto(resolve ? await resolve() : path);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
          if (ready) {
            // The table or the cards, whichever the width shows.
            await expect(
              page.getByText(ready).filter({ visible: true }).first(),
            ).toBeVisible();
          }
          if (detail) {
            await expect(
              page.getByRole("region", { name: "Environments" }),
            ).toBeVisible();
          }
          await page.evaluate(() => document.fonts.ready);

          await expect(page.locator("html")).toHaveClass(
            new RegExp(`\\b${theme}\\b`),
          );
          expect(await axeViolations(page)).toEqual([]);
          expect(await noHorizontalScroll(page)).toBe(true);
          expect(await smallTargets(page)).toEqual([]);
          await expect(page).toHaveScreenshot(`${name}-${theme}-${width}.png`, {
            fullPage: true,
            // The running version changes with every release.
            mask: [
              page.getByTestId("app-version"),
              ...(mask ? [page.locator("time")] : []),
              ...maskText.map((text) => page.getByText(text)),
            ].map((locator) => locator.filter({ visible: true })),
          });
        });
      }
    }
  });
}

for (const width of [280, 1440]) {
  test(`account security has no horizontal scroll at ${width} px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/account/security");
    await expect(page.getByRole("main")).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}
