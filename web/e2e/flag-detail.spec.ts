import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import {
  adminApi,
  createTestFlag,
  type FlagConfig,
  ok,
  putConfig,
  SHOP,
} from "./support/api";
import { noHorizontalScroll, smallTargets } from "./support/checks";

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

function detail(flag: string, query = ""): string {
  return `/projects/${SHOP}/flags/${flag}${query}`;
}

async function enabledIn(flag: string, env: string): Promise<boolean> {
  const response = await ok(
    await api.get(`/api/projects/${SHOP}/flags/${flag}/environments/${env}`),
    `read ${flag} in ${env}`,
  );
  return ((await response.json()) as { enabled: boolean }).enabled;
}

async function toggleViaApi(flag: string, env: string, enabled: boolean) {
  await ok(
    await api.post(
      `/api/projects/${SHOP}/flags/${flag}/environments/${env}/toggle`,
      { data: { enabled } },
    ),
    `toggle ${flag} in ${env}`,
  );
}

async function configIn(flag: string, env: string): Promise<FlagConfig> {
  const response = await ok(
    await api.get(`/api/projects/${SHOP}/flags/${flag}/environments/${env}`),
    `read ${flag} in ${env}`,
  );
  return (await response.json()) as FlagConfig;
}

function card(page: Page, env: string) {
  return page
    .getByRole("region", { name: "Environments" })
    .getByRole("group", { name: env, exact: true });
}

test.describe("flag detail", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("an unknown flag is the not-found page", async ({ page }) => {
    await page.goto(detail("nope"));
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
  });

  test("the strip has a card per environment", async ({ page }) => {
    await page.goto(detail("checkout-v2"));
    await expect(
      page.getByRole("heading", { level: 1, name: "Checkout v2" }),
    ).toBeVisible();
    await expect(page.locator("header code")).toHaveText("checkout-v2");
    const strip = page.getByRole("region", { name: "Environments" });
    await expect(strip.getByRole("group")).toHaveCount(3);
    await expect(
      card(page, "Production").getByText("Needs approval"),
    ).toBeVisible();
    await expect(card(page, "Staging").getByText("Needs approval")).toHaveCount(
      0,
    );
    for (const name of ["Targeting", "Test", "History", "Insights", "Settings"])
      await expect(page.getByRole("tab", { name })).toBeVisible();
  });

  test("toggling on in Staging turns the flag on", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const toggle = page.getByRole("switch", { name: `${flag} in Staging` });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(page.getByText("Turned on in Staging")).toBeVisible();
    await expect(toggle).toBeChecked();
    expect(await enabledIn(flag, "staging")).toBe(true);
  });

  test("toggling in Production requests a change", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const toggle = page.getByRole("switch", { name: `${flag} in Production` });
    await toggle.click();
    const dialog = page.getByRole("dialog", {
      name: "Request change in Production",
    });
    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue(
      `Turn on ${flag} in Production`,
    );
    await dialog
      .getByRole("textbox", { name: "Description" })
      .fill("Launch it");
    await dialog.getByRole("button", { name: "Request change" }).click();
    await expect(page.getByText("Change request created")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "View change requests" }),
    ).toBeVisible();
    await expect(toggle).not.toBeChecked();

    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag, env: "production" },
      }),
      "list change requests",
    );
    const { items } = (await listed.json()) as {
      items: { kind: string; title: string }[];
    };
    expect(items).toEqual([
      expect.objectContaining({
        kind: "toggle",
        title: `Turn on ${flag} in Production`,
      }),
    ]);
    expect(await enabledIn(flag, "production")).toBe(false);
  });

  test("the kill switch asks first", async ({ page }) => {
    const flag = await createTestFlag(api);
    await toggleViaApi(flag, "staging", true);
    await page.goto(detail(flag));
    const kill = page.getByRole("button", { name: "Kill switch in Staging" });
    const title = `Kill ${flag} in Staging?`;

    await kill.click();
    const dialog = page.getByRole("alertdialog", { name: title });
    await expect(dialog).toContainText(
      `This turns ${flag} off in Staging right away, without approval.`,
    );
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByRole("switch", { name: `${flag} in Staging` }),
    ).toBeChecked();
    expect(await enabledIn(flag, "staging")).toBe(true);

    await kill.click();
    await page
      .getByRole("alertdialog", { name: title })
      .getByRole("button", { name: "Kill switch" })
      .click();
    await expect(
      page.getByRole("switch", { name: `${flag} in Staging` }),
    ).not.toBeChecked();
    await expect(kill).toBeDisabled();
    expect(await enabledIn(flag, "staging")).toBe(false);
  });

  test("the kill switch skips approval in Production", async ({ page }) => {
    const flag = await createTestFlag(api);
    const production = `/api/projects/${SHOP}/environments/production`;
    await ok(
      await api.patch(production, { data: { approvals_required: 0 } }),
      "drop approvals",
    );
    try {
      await toggleViaApi(flag, "production", true);
    } finally {
      await ok(
        await api.patch(production, { data: { approvals_required: 1 } }),
        "restore approvals",
      );
    }
    await page.goto(detail(flag));
    await page
      .getByRole("button", { name: "Kill switch in Production" })
      .click();
    await page
      .getByRole("alertdialog", { name: `Kill ${flag} in Production?` })
      .getByRole("button", { name: "Kill switch" })
      .click();
    await expect(
      page.getByRole("switch", { name: `${flag} in Production` }),
    ).not.toBeChecked();
    expect(await enabledIn(flag, "production")).toBe(false);
    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag },
      }),
      "list change requests",
    );
    expect(((await listed.json()) as { items: unknown[] }).items).toEqual([]);
  });

  test("the environment and tab live in the URL", async ({ page }) => {
    await page.goto(detail("checkout-v2"));
    const edit = page.getByRole("button", { name: "Edit Development" });
    await expect(edit).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: "Edit Staging" }).click();
    await expect(page).toHaveURL(/[?&]env=staging/);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Edit Staging" }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(edit).toHaveAttribute("aria-pressed", "false");

    await page.getByRole("tab", { name: "History" }).click();
    await expect(page).toHaveURL(/[?&]tab=history/);
    await expect(page).toHaveURL(/[?&]env=staging/);
    await page.reload();
    await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

test.describe("editor", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  function section(page: Page, name: string) {
    return page.getByRole("region", { name });
  }

  function unsaved(page: Page) {
    return page.getByRole("status", { name: "Unsaved changes" });
  }

  test("targets and a rollout are reviewed, then saved", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-9");
    await targets.blur();

    const fallthrough = section(page, "Default rule");
    await fallthrough.getByRole("radio", { name: "Serve a rollout" }).check();
    const percent = fallthrough.getByRole("spinbutton", {
      name: "Rollout percentage",
    });
    await percent.fill("30");
    await percent.press("Enter");

    await unsaved(page).getByRole("button", { name: "Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await expect(dialog.getByRole("region", { name: "After" })).toContainText(
      "user-9",
    );
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Saved", { exact: true })).toBeVisible();
    await expect(dialog).toBeHidden();
    await expect(unsaved(page)).toHaveCount(0);

    const saved = await configIn(flag, "development");
    expect(saved.targets).toEqual([{ variation: "true", values: ["user-9"] }]);
    expect(saved.fallthrough).toEqual({
      variation: null,
      rollout: {
        bucket_by: "key",
        weights: [
          { variation: "true", weight: 30000 },
          { variation: "false", weight: 70000 },
        ],
      },
    });
  });

  test("Discard reverts the draft", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served false",
    });
    await targets.fill("user-3");
    await targets.blur();
    await unsaved(page).getByRole("button", { name: "Discard" }).click();
    await expect(targets).toHaveValue("");
    await expect(unsaved(page)).toHaveCount(0);
  });

  test("a newer version is shown and saved over", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-7");
    await targets.blur();
    await putConfig(api, flag, "development", (c) => {
      c.enabled = true;
    });

    await unsaved(page).getByRole("button", { name: "Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog.getByRole("alert")).toContainText(
      "Someone saved a newer version",
    );
    await expect(dialog.getByRole("region", { name: "After" })).toContainText(
      "user-7",
    );
    await dialog.getByRole("button", { name: "Save over latest" }).click();
    await expect(dialog).toBeHidden();

    const saved = await configIn(flag, "development");
    expect(saved.enabled).toBe(true);
    expect(saved.targets).toEqual([{ variation: "true", values: ["user-7"] }]);
  });

  test("Production requests a config change", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag, "?env=production"));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-5");
    await targets.blur();
    await unsaved(page)
      .getByRole("button", { name: "Review & request change" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue(
      `Update targeting of ${flag} in Production`,
    );
    await dialog
      .getByRole("textbox", { name: "Description" })
      .fill("Let user-5 in");
    await dialog.getByRole("button", { name: "Request change" }).click();
    await expect(page.getByText("Change request created")).toBeVisible();
    await expect(targets).toHaveValue("");

    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag, env: "production" },
      }),
      "list change requests",
    );
    const { items } = (await listed.json()) as {
      items: { kind: string; title: string }[];
    };
    expect(items).toEqual([
      expect.objectContaining({
        kind: "config",
        title: `Update targeting of ${flag} in Production`,
      }),
    ]);
    expect((await configIn(flag, "production")).targets).toEqual([]);
  });

  test("weights must add up to 100 %", async ({ page }) => {
    const flag = await createTestFlag(api, { type: "string" });
    await page.goto(detail(flag));
    const fallthrough = section(page, "Default rule");
    await fallthrough.getByRole("radio", { name: "Serve a rollout" }).check();
    await expect(fallthrough).toContainText("Total: 100 %");
    await fallthrough
      .getByRole("spinbutton", { name: "Weight of a (%)" })
      .fill("40");
    await expect(fallthrough).toContainText("Total: 90 %");
    await unsaved(page).getByRole("button", { name: "Review" }).click();
    await expect(
      fallthrough.getByText("Weights must add up to 100 %"),
    ).toBeVisible();
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "Fix these before review" })
        .getByText("Weights must add up to 100 %"),
    ).toBeVisible();
    await expect(
      page.getByRole("dialog", { name: "Review changes" }),
    ).toHaveCount(0);
  });

  test("leaving with unsaved changes asks first", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-1");
    await targets.blur();
    await expect(unsaved(page)).toBeVisible();

    let message = "";
    page.once("dialog", (dialog) => {
      message = dialog.message();
      void dialog.dismiss();
    });
    await page
      .getByRole("navigation", { name: "Breadcrumb" })
      .getByRole("link", { name: SHOP })
      .click();
    await expect.poll(() => message).toBe("Leave without saving your changes?");
    await expect(page).toHaveURL(new RegExp(`/flags/${flag}$`));
    await expect(targets).toHaveValue("user-1");
  });

  test("a prerequisite is saved", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const prerequisites = section(page, "Prerequisites");
    await prerequisites
      .getByRole("button", { name: "Add prerequisite" })
      .click();
    await prerequisites
      .getByRole("combobox", { name: "Prerequisite 1 flag" })
      .selectOption("checkout-v2");
    const variation = prerequisites.getByRole("combobox", {
      name: "Prerequisite 1 variation",
    });
    await expect(variation.getByRole("option", { name: "true" })).toHaveCount(
      1,
    );
    await variation.selectOption("true");

    await unsaved(page).getByRole("button", { name: "Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
    expect((await configIn(flag, "development")).prerequisites).toEqual([
      { flag: "checkout-v2", variation: "true" },
    ]);
  });
});

test.describe("rules", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  function rule(page: Page, n: number) {
    return page.getByRole("group", { name: `Rule ${n}`, exact: true });
  }

  async function save(page: Page) {
    await page
      .getByRole("status", { name: "Unsaved changes" })
      .getByRole("button", { name: "Review" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
  }

  async function setValues(page: Page, n: number, text: string) {
    const values = rule(page, n).getByRole("textbox", { name: "Values" });
    await values.fill(text);
    await values.blur();
  }

  const countryRule = {
    description: "",
    clauses: [
      { attribute: "country", operator: "in", values: ["NL"], negate: false },
    ],
    variation: "true",
    rollout: null,
  };
  const betaRule = {
    description: "",
    clauses: [
      {
        attribute: "key",
        operator: "in_segment",
        values: ["beta-testers"],
        negate: false,
      },
    ],
    variation: null,
    rollout: {
      bucket_by: "key",
      weights: [
        { variation: "true", weight: 50000 },
        { variation: "false", weight: 50000 },
      ],
    },
  };

  test("a list rule and a segment rule are saved, then reordered", async ({
    page,
  }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const rules = page.getByRole("region", { name: "Rules" });
    await expect(rules).toContainText(
      "No rules. Everyone who is not targeted gets the default rule.",
    );

    await rules.getByRole("button", { name: "Add rule" }).click();
    await rule(page, 1)
      .getByRole("textbox", { name: "Attribute" })
      .fill("country");
    await setValues(page, 1, "NL\nBE");
    await rule(page, 1)
      .getByRole("combobox", { name: "Variation" })
      .selectOption("true");

    await rules.getByRole("button", { name: "Add rule" }).click();
    const second = rule(page, 2);
    await second
      .getByRole("combobox", { name: "Operator" })
      .selectOption({ label: "is in segment" });
    await second
      .getByRole("checkbox", { name: "Segment beta-testers" })
      .check();
    await second.getByRole("radio", { name: "Serve a rollout" }).check();
    await expect(
      second.getByRole("spinbutton", { name: "Rollout percentage" }),
    ).toHaveValue("50");
    await save(page);

    // Values keep the typed order; only segment keys come back sorted.
    const saved = await configIn(flag, "development");
    expect(saved.rules).toEqual([
      {
        ...countryRule,
        clauses: [{ ...countryRule.clauses[0], values: ["NL", "BE"] }],
      },
      betaRule,
    ]);

    await expect(
      rule(page, 1).getByRole("button", { name: "Move rule 1 up" }),
    ).toBeDisabled();
    await expect(
      rule(page, 2).getByRole("button", { name: "Move rule 2 down" }),
    ).toBeDisabled();
    await rule(page, 2).getByRole("button", { name: "Move rule 2 up" }).click();
    await expect(
      rule(page, 1).getByRole("button", { name: "Move rule 1 down" }),
    ).toBeFocused();
    await expect(
      rule(page, 1).getByRole("checkbox", { name: "Segment beta-testers" }),
    ).toBeChecked();
    await save(page);
    const swapped = await configIn(flag, "development");
    expect(swapped.rules).toEqual([saved.rules[1], saved.rules[0]]);
  });

  test("a bad version shows inline and blocks review", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    await page.getByRole("button", { name: "Add rule" }).click();
    await setValues(page, 1, "NL");
    await rule(page, 1)
      .getByRole("combobox", { name: "Operator" })
      .selectOption({ label: "version >" });
    await expect(
      rule(page, 1).getByRole("textbox", { name: "Values" }),
    ).toHaveValue("");
    await expect(rule(page, 1)).toContainText("Versions like 1.2.3");
    await setValues(page, 1, "1.2");
    const values = rule(page, 1).getByRole("textbox", { name: "Values" });
    await expect(values).toHaveAttribute("aria-invalid", "true");
    await expect(rule(page, 1)).toContainText("Enter versions like 1.2.3");

    await page
      .getByRole("status", { name: "Unsaved changes" })
      .getByRole("button", { name: "Review" })
      .click();
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "Fix these before review" })
        .getByText("Add at least one value"),
    ).toBeVisible();
    await expect(
      page.getByRole("dialog", { name: "Review changes" }),
    ).toHaveCount(0);
  });

  test("Not round-trips through Save", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    await page.getByRole("button", { name: "Add rule" }).click();
    await rule(page, 1)
      .getByRole("textbox", { name: "Attribute" })
      .fill("plan");
    await setValues(page, 1, "free");
    await rule(page, 1).getByRole("checkbox", { name: "Not" }).check();
    await save(page);
    const saved = await configIn(flag, "development");
    expect(saved.rules).toEqual([
      expect.objectContaining({
        clauses: [
          { attribute: "plan", operator: "in", values: ["free"], negate: true },
        ],
      }),
    ]);
    await page.reload();
    await expect(
      rule(page, 1).getByRole("checkbox", { name: "Not" }),
    ).toBeChecked();
  });

  test("removing rule 1 leaves rule 2", async ({ page }) => {
    const flag = await createTestFlag(api);
    await putConfig(api, flag, "development", (c) => {
      c.rules = [countryRule, betaRule];
    });
    await page.goto(detail(flag));
    await expect(
      rule(page, 1).getByRole("button", { name: "Remove clause" }),
    ).toBeDisabled();
    await rule(page, 1).getByRole("button", { name: "Remove rule 1" }).click();
    await expect(rule(page, 2)).toHaveCount(0);
    await save(page);
    expect((await configIn(flag, "development")).rules).toEqual([betaRule]);
  });

  test("rules fit at 344 px", async ({ page }) => {
    const flag = await createTestFlag(api);
    await putConfig(api, flag, "development", (c) => {
      c.rules = [countryRule, betaRule];
    });
    await page.setViewportSize({ width: 344, height: 800 });
    await page.goto(detail(flag));
    await expect(
      rule(page, 2).getByRole("checkbox", { name: "Segment beta-testers" }),
    ).toBeChecked();
    expect(await noHorizontalScroll(page)).toBe(true);
    expect(await smallTargets(page)).toEqual([]);
  });
});

test.describe("copy", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  async function devWithTarget(): Promise<string> {
    const flag = await createTestFlag(api);
    await putConfig(api, flag, "development", (c) => {
      c.enabled = true;
      c.targets = [{ variation: "true", values: ["user-5"] }];
    });
    return flag;
  }

  async function openCopy(page: Page, flag: string) {
    await page.goto(detail(flag));
    await page.getByRole("button", { name: "Copy to…" }).click();
    return page.getByRole("dialog", { name: "Copy configuration" });
  }

  test("copies targeting and the on/off state to Staging", async ({ page }) => {
    const flag = await devWithTarget();
    const dialog = await openCopy(page, flag);
    await expect(dialog.getByRole("combobox", { name: "From" })).toHaveValue(
      "development",
    );
    const copy = dialog.getByRole("button", { name: "Copy", exact: true });
    await expect(copy).toBeDisabled();
    await dialog.getByRole("checkbox", { name: "To Staging" }).check();
    await dialog
      .getByRole("checkbox", { name: "Include on/off state" })
      .check();
    await copy.click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("Copied to Staging")).toBeVisible();

    const staging = await configIn(flag, "staging");
    expect(staging.targets).toEqual([
      { variation: "true", values: ["user-5"] },
    ]);
    expect(staging.enabled).toBe(true);
    await expect(card(page, "Staging")).toContainText("On");
  });

  test("Production needs approval, so it can't be a target", async ({
    page,
  }) => {
    const flag = await devWithTarget();
    const dialog = await openCopy(page, flag);
    await expect(
      dialog.getByRole("checkbox", { name: "To Production" }),
    ).toBeDisabled();
    await expect(dialog).toContainText(
      "Needs approval: copy is not allowed here",
    );
  });

  test("without the on/off state, Staging stays off", async ({ page }) => {
    const flag = await devWithTarget();
    const dialog = await openCopy(page, flag);
    await dialog.getByRole("checkbox", { name: "To Staging" }).check();
    await dialog.getByRole("button", { name: "Copy", exact: true }).click();
    await expect(dialog).toBeHidden();

    const staging = await configIn(flag, "staging");
    expect(staging.targets).toEqual([
      { variation: "true", values: ["user-5"] },
    ]);
    expect(staging.enabled).toBe(false);
    await expect(card(page, "Staging")).toContainText("Off");
  });
});

test.describe("test panel", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  async function evaluate(page: Page, context: string) {
    await page.getByRole("textbox", { name: "Context (JSON)" }).fill(context);
    await page.getByRole("button", { name: "Evaluate" }).click();
    return page.getByRole("region", { name: "Result", exact: true });
  }

  test("explains a target, a rule and the default rule", async ({ page }) => {
    await page.goto(detail("checkout-v2", "?env=development&tab=test"));
    await expect(
      page.getByRole("heading", { name: "Test in Development" }),
    ).toBeVisible();

    let result = await evaluate(page, '{"key":"user-1"}');
    await expect(result).toContainText("true");
    await expect(result).toContainText("Individual target");

    result = await evaluate(page, '{"key":"u2","country":"NL"}');
    await expect(result).toContainText("Rule 1");

    result = await evaluate(page, '{"key":"u3","country":"DE"}');
    await expect(result).toContainText("false");
    await expect(result).toContainText("Default rule");
  });

  test("Show rule opens the rule in Targeting", async ({ page }) => {
    await page.goto(detail("checkout-v2", "?env=development&tab=test"));
    const result = await evaluate(page, '{"key":"u2","country":"NL"}');
    await result.getByRole("button", { name: "Show rule 1" }).click();
    await expect(page).toHaveURL(/tab=targeting/);
    await expect(page.locator("#rule-1")).toBeInViewport();
  });

  test("a flag that is off says so", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag, "?tab=test"));
    const result = await evaluate(page, '{"key":"user-1"}');
    await expect(result).toContainText("Flag is off");
  });

  test("invalid JSON asks for an object", async ({ page }) => {
    await page.goto(detail("checkout-v2", "?env=development&tab=test"));
    await evaluate(page, "{");
    await expect(page.getByText("Enter a JSON object")).toBeVisible();
  });

  test("the context survives a reload", async ({ page }) => {
    await page.goto(detail("checkout-v2", "?env=development&tab=test"));
    const context = page.getByRole("textbox", { name: "Context (JSON)" });
    await context.fill('{"key":"kept"}');
    await page.reload();
    await expect(context).toHaveValue('{"key":"kept"}');
  });

  test("a dirty draft notes that the saved config is used", async ({
    page,
  }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-9");
    await targets.blur();
    await page.getByRole("tab", { name: "Test" }).click();
    await expect(
      page.getByText("Results use the saved config", { exact: false }),
    ).toBeVisible();
  });
});
