import {
  type APIRequestContext,
  type APIResponse,
  request,
} from "@playwright/test";

import { API, APP, ok, putConfig, SHOP } from "./support/api";
import {
  ADMIN,
  type E2EUser,
  PASSWORD,
  PREFS,
  USERS,
  VIEWER,
} from "./support/users";

/** Through the Next rewrites, with the Origin the API's CSRF check expects. */
function appContext(): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: APP,
    extraHTTPHeaders: { Origin: APP },
  });
}

async function waitForApi(): Promise<void> {
  const ctx = await request.newContext();
  const deadline = Date.now() + 90_000;
  try {
    for (;;) {
      const r = await ctx.get(`${API}/readyz`).catch(() => null);
      if (r?.ok()) return;
      if (Date.now() > deadline)
        throw new Error(`API at ${API} not ready after 90 s`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } finally {
    await ctx.dispose();
  }
}

async function signIn(
  ctx: APIRequestContext,
  email: string,
): Promise<APIResponse> {
  return ok(
    await ctx.post("/api/auth/sign-in", {
      data: { email, password: PASSWORD },
    }),
    `sign in ${email}`,
  );
}

async function ensure(
  ctx: APIRequestContext,
  path: string,
  create: string,
  data: object,
): Promise<void> {
  const r = await ctx.get(path);
  if (r.ok()) return;
  if (r.status() !== 404) await ok(r, `GET ${path}`);
  await ok(await ctx.post(create, { data }), `POST ${create}`);
}

async function seed(admin: APIRequestContext): Promise<void> {
  await ensure(admin, "/api/projects/demo", "/api/projects", {
    key: "demo",
    name: "Demo",
    environments: [
      { key: "development", name: "Development" },
      { key: "staging", name: "Staging" },
      { key: "production", name: "Production" },
    ],
  });
  await ensure(admin, "/api/projects/other", "/api/projects", {
    key: "other",
    name: "Other",
  });
  const flags = "/api/projects/demo/flags";
  await ensure(admin, `${flags}/new-checkout`, flags, {
    key: "new-checkout",
    name: "New checkout",
    type: "boolean",
  });
  await ensure(admin, `${flags}/dark-mode`, flags, {
    key: "dark-mode",
    name: "Dark mode",
    type: "boolean",
  });
  await ensure(admin, `${flags}/pricing-copy`, flags, {
    key: "pricing-copy",
    name: "Pricing copy",
    type: "string",
    variations: [
      { key: "control", value: "Start your free trial" },
      { key: "short", value: "Try it free" },
    ],
    off_variation: "control",
  });
}

/** Runs `task` on every item, `limit` at a time. */
async function inBatches<T>(
  items: T[],
  limit: number,
  task: (item: T) => Promise<unknown>,
): Promise<void> {
  for (let i = 0; i < items.length; i += limit)
    await Promise.all(items.slice(i, i + limit).map(task));
}

/**
 * Removes every prerequisite in shop. The database refuses to delete a project whose flags are
 * prerequisites of each other (the FK has no ON DELETE), and the bulk archive spec leaves one.
 */
async function clearShopPrerequisites(admin: APIRequestContext): Promise<void> {
  const keys: string[] = [];
  for (const lifecycle of [undefined, "archived"]) {
    const page = await ok(
      await admin.get(`/api/projects/${SHOP}/flags`, {
        params: { limit: 500, ...(lifecycle && { lifecycle }) },
      }),
      "list shop flags",
    );
    const { items } = (await page.json()) as { items: { key: string }[] };
    keys.push(...items.map((f) => f.key));
  }
  const envs = ["development", "staging", "production"];
  await inBatches(keys, 4, async (key) => {
    for (const env of envs) {
      const path = `/api/projects/${SHOP}/flags/${key}/environments/${env}`;
      const current = await ok(await admin.get(path), `GET ${path}`);
      const { prerequisites } = (await current.json()) as {
        prerequisites: unknown[];
      };
      if (prerequisites.length > 0)
        await putConfig(admin, key, env, (config) => {
          config.prerequisites = [];
        });
    }
  });
}

/** Recreates the shop project the flag specs use, so every run starts from the same data. */
async function seedShop(admin: APIRequestContext): Promise<void> {
  const project = `/api/projects/${SHOP}`;
  let gone = await admin.delete(project);
  if (gone.status() === 409) {
    await clearShopPrerequisites(admin);
    gone = await admin.delete(project);
  }
  if (gone.status() !== 404) await ok(gone, `DELETE ${project}`);
  await ok(
    await admin.post("/api/projects", {
      data: {
        key: SHOP,
        name: "Shop",
        environments: [
          { key: "development", name: "Development" },
          { key: "staging", name: "Staging" },
          { key: "production", name: "Production" },
        ],
      },
    }),
    "create shop",
  );
  await ok(
    await admin.patch(`${project}/environments/production`, {
      data: { approvals_required: 1 },
    }),
    "require approvals in production",
  );

  const segments = `${project}/segments`;
  await ok(
    await admin.post(segments, {
      data: { key: "beta-testers", name: "Beta testers" },
    }),
    "create beta-testers",
  );
  await ok(
    await admin.put(`${segments}/beta-testers`, {
      data: { included: ["user-beta"] },
    }),
    "configure beta-testers",
  );

  const flags = `${project}/flags`;
  const create = async (data: object) =>
    ok(
      await admin.post(flags, { data }),
      `create flag ${JSON.stringify(data)}`,
    );
  await create({
    key: "checkout-v2",
    name: "Checkout v2",
    type: "boolean",
    tags: ["web", "checkout"],
    owner: "team-web",
  });
  await create({
    key: "search-ranking",
    name: "Search ranking",
    type: "string",
    tags: ["search"],
    owner: "team-search",
    variations: [
      { key: "classic", value: "classic" },
      { key: "ml", value: "ml" },
    ],
    off_variation: "classic",
  });
  await create({
    key: "banner-text",
    name: "Banner text",
    type: "string",
    variations: [
      { key: "a", value: "a" },
      { key: "b", value: "b" },
    ],
    off_variation: "a",
  });
  await create({
    key: "legacy-header",
    name: "Legacy header",
    type: "boolean",
  });
  await ok(
    await admin.post(`${flags}/legacy-header/archive`),
    "archive legacy-header",
  );
  const load = Array.from(
    { length: 52 },
    (_, i) => `load-${String(i + 1).padStart(2, "0")}`,
  );
  await inBatches(load, 4, (key) =>
    create({ key, name: key, type: "boolean", tags: ["load"] }),
  );

  const checkout = `${flags}/checkout-v2/environments`;
  const dev = await ok(await admin.get(`${checkout}/development`), "GET dev");
  const { version } = (await dev.json()) as { version: number };
  await ok(
    await admin.put(`${checkout}/development`, {
      data: {
        version,
        enabled: true,
        targets: [{ variation: "true", values: ["user-1"] }],
        rules: [
          {
            clauses: [{ attribute: "country", operator: "in", values: ["NL"] }],
            variation: "true",
          },
        ],
        fallthrough: { variation: "false" },
      },
    }),
    "configure checkout-v2 in development",
  );
  await ok(
    await admin.post(`${checkout}/staging/toggle`, { data: { enabled: true } }),
    "turn checkout-v2 on in staging",
  );

  const created = await ok(
    await admin.post(`${project}/environments/development/sdk-keys`, {
      data: { name: "e2e server", kind: "server" },
    }),
    "create SDK key",
  );
  const { key } = (await created.json()) as { key: string };
  // Straight to the API: the Next rewrite covers only /api.
  const sdk = await request.newContext({
    baseURL: API,
    extraHTTPHeaders: { Authorization: `Bearer ${key}` },
  });
  try {
    for (let i = 0; i < 20; i++)
      await ok(
        await sdk.post("/ofrep/v1/evaluate/flags/checkout-v2", {
          data: {
            context: {
              targetingKey: `user-${i}`,
              country: i % 2 ? "NL" : "DE",
            },
          },
        }),
        `evaluate checkout-v2 for user-${i}`,
      );
  } finally {
    await sdk.dispose();
  }
}

/** Invites the user, accepts with PASSWORD and binds their org role, unless they exist. */
async function ensureUser(
  admin: APIRequestContext,
  user: E2EUser,
): Promise<void> {
  const found = await ok(
    await admin.get("/api/users", { params: { q: user.email } }),
    "list users",
  );
  const { items } = (await found.json()) as { items: { email: string }[] };
  if (items.some((u) => u.email === user.email)) return;

  const created = await ok(
    await admin.post("/api/users", {
      data: { email: user.email, name: user.name },
    }),
    `create ${user.email}`,
  );
  const body = (await created.json()) as {
    user: { id: string };
    invite: { url: string };
  };
  const token = body.invite.url.split("/").pop();
  await ok(
    await admin.post(`/api/invitations/${token}`, {
      data: { password: PASSWORD },
    }),
    `accept invite ${user.email}`,
  );
  await ok(
    await admin.post("/api/bindings", {
      data: {
        subject_kind: "user",
        subject_id: body.user.id,
        role: user.role,
        scope: "org",
      },
    }),
    `bind ${user.email}`,
  );
}

/** Idempotent: safe to run against a database that an earlier run already seeded. */
export default async function globalSetup(): Promise<void> {
  await waitForApi();

  const admin = await appContext();
  try {
    const status = await ok(await admin.get("/api/setup/status"), "status");
    const { needs_setup } = (await status.json()) as { needs_setup: boolean };
    if (needs_setup) {
      await ok(
        await admin.post("/api/setup", {
          data: { email: ADMIN.email, name: ADMIN.name, password: PASSWORD },
        }),
        "setup",
      );
    } else {
      await signIn(admin, ADMIN.email);
    }
    await seed(admin);
    await seedShop(admin);
    await ensureUser(admin, VIEWER);
    await ensureUser(admin, PREFS);
  } finally {
    await admin.dispose();
  }

  for (const user of USERS) {
    const ctx = await appContext();
    try {
      await signIn(ctx, user.email);
      await ok(
        await ctx.patch("/api/account/preferences", {
          data: { theme: "system", sidebar_collapsed: false },
        }),
        `reset preferences ${user.email}`,
      );
      await ctx.storageState({ path: user.storageState });
    } finally {
      await ctx.dispose();
    }
  }
}
