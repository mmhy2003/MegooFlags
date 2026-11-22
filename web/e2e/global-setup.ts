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
  REVIEWER,
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
 * Removes every prerequisite and segment rule in shop. The database refuses to delete a project
 * whose flags are prerequisites of each other or whose rules use its segments (those FKs have no
 * ON DELETE), and the bulk archive and rules specs leave some.
 */
async function clearShopReferences(admin: APIRequestContext): Promise<void> {
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
      const { prerequisites, rules } = (await current.json()) as {
        prerequisites: unknown[];
        rules: { clauses: { operator: string }[] }[];
      };
      const usesSegment = (rule: (typeof rules)[number]) =>
        rule.clauses.some((c) => c.operator === "in_segment");
      if (prerequisites.length > 0 || rules.some(usesSegment))
        await putConfig(admin, key, env, (config) => {
          config.prerequisites = [];
          config.rules = rules.filter((r) => !usesSegment(r));
        });
    }
  });
}

/**
 * The integration the visual matrix shows. Paused, so it never sends and its row never changes.
 * Its 127.0.0.1 URL needs MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true, as the e2e API runs.
 */
async function ensureMatrixWebhook(admin: APIRequestContext): Promise<void> {
  const path = `/api/projects/${SHOP}/integrations`;
  const list = await ok(await admin.get(path), "list shop integrations");
  const items = (await list.json()) as { name: string }[];
  if (items.some((i) => i.name === "matrix-webhook")) return;
  await ok(
    await admin.post(path, {
      data: {
        kind: "webhook",
        name: "matrix-webhook",
        url: "http://127.0.0.1:9/matrix",
        event_groups: ["flags", "targeting", "segments", "change_requests"],
        all_environments: true,
        enabled: false,
      },
    }),
    "create matrix-webhook",
  );
}

/**
 * Leaves exactly one node, matrix-worker, for the visual matrix. It never connects, so it stays
 * Pending and its row never changes. Needs the e2e API on MEGOOFLAGS_ROLE=control-plane.
 */
async function seedNodes(admin: APIRequestContext): Promise<void> {
  const list = await ok(
    await admin.get("/api/nodes"),
    "list nodes (run the API with MEGOOFLAGS_ROLE=control-plane)",
  );
  const { nodes } = (await list.json()) as {
    nodes: { id: string; name: string; status: string }[];
  };
  for (const node of nodes) {
    if (node.name === "matrix-worker") continue;
    if (node.status !== "revoked")
      await ok(
        await admin.post(`/api/nodes/${node.id}/revoke`),
        `revoke ${node.name}`,
      );
    await ok(
      await admin.delete(`/api/nodes/${node.id}`),
      `delete ${node.name}`,
    );
  }
  if (!nodes.some((n) => n.name === "matrix-worker"))
    await ok(
      await admin.post("/api/nodes", { data: { name: "matrix-worker" } }),
      "create matrix-worker",
    );
}

/** Recreates the shop project the flag specs use, so every run starts from the same data. */
async function seedShop(admin: APIRequestContext): Promise<void> {
  const project = `/api/projects/${SHOP}`;
  let gone = await admin.delete(project);
  if (gone.status() === 409) {
    await clearShopReferences(admin);
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

  await ensureMatrixWebhook(admin);

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

/** The user's id; ensureUser has created them. */
async function userId(
  admin: APIRequestContext,
  user: E2EUser,
): Promise<string> {
  const found = await ok(
    await admin.get("/api/users", { params: { q: user.email } }),
    "list users",
  );
  const { items } = (await found.json()) as {
    items: { id: string; email: string }[];
  };
  const match = items.find((u) => u.email === user.email);
  if (!match) throw new Error(`no user ${user.email}`);
  return match.id;
}

/**
 * The governance data the visual matrix shows: project gov with two change requests (one open, one
 * rejected), two SDK keys, and the gov-token, gov-team, gov-role and gov-oidc org items. Each item
 * is created only when missing and never changed after that, so the pages look the same on every
 * run. No spec touches them.
 */
async function seedGov(
  admin: APIRequestContext,
  reviewer: APIRequestContext,
): Promise<void> {
  const project = "/api/projects/gov";
  const fresh = (await admin.get(project)).status() === 404;
  await ensure(admin, project, "/api/projects", {
    key: "gov",
    name: "Gov",
    environments: [
      { key: "development", name: "Development" },
      { key: "production", name: "Production" },
    ],
  });
  if (fresh)
    await ok(
      await admin.patch(`${project}/environments/production`, {
        data: { approvals_required: 1 },
      }),
      "require approvals in gov production",
    );
  const flags = `${project}/flags`;
  await ensure(admin, `${flags}/gov-flag`, flags, {
    key: "gov-flag",
    name: "Gov flag",
    type: "boolean",
  });

  const requests = `${project}/change-requests`;
  const listed = await ok(
    // Without a status, every request.
    await admin.get(requests, { params: { limit: 1 } }),
    "list gov change requests",
  );
  if (((await listed.json()) as { items: unknown[] }).items.length === 0) {
    const config = await ok(
      await admin.get(`${flags}/gov-flag/environments/production`),
      "GET gov-flag in production",
    );
    const { version } = (await config.json()) as { version: number };
    await ok(
      await admin.post(requests, {
        data: {
          environment: "production",
          flag: "gov-flag",
          kind: "config",
          title: "Turn on targeting",
          payload: {
            version,
            enabled: true,
            fallthrough: { variation: "false" },
          },
        },
      }),
      "open Turn on targeting",
    );
    const toggle = await ok(
      await admin.post(requests, {
        data: {
          environment: "production",
          flag: "gov-flag",
          kind: "toggle",
          title: "Turn it on",
          payload: { enabled: true },
        },
      }),
      "open Turn it on",
    );
    const { id } = (await toggle.json()) as { id: string };
    await ok(
      await reviewer.post(`${requests}/${id}/reject`, {
        data: { comment: "Not now" },
      }),
      "reject Turn it on",
    );
  }

  const keys = `${project}/environments/development/sdk-keys`;
  const keyList = await ok(await admin.get(keys), "list gov SDK keys");
  const keyNames = ((await keyList.json()) as { name: string }[]).map(
    (k) => k.name,
  );
  for (const [name, kind] of [
    ["gov server", "server"],
    ["gov client", "client"],
  ])
    if (!keyNames.includes(name))
      await ok(
        await admin.post(keys, { data: { name, kind } }),
        `create ${name}`,
      );

  const tokens = await ok(
    await admin.get("/api/tokens", { params: { project: "gov", limit: 500 } }),
    "list gov tokens",
  );
  const { items: tokenItems } = (await tokens.json()) as {
    items: { name: string }[];
  };
  if (!tokenItems.some((t) => t.name === "gov-token"))
    await ok(
      await admin.post("/api/tokens", {
        data: {
          name: "gov-token",
          bindings: [{ role: "viewer", scope: "project", project: "gov" }],
        },
      }),
      "create gov-token",
    );

  if ((await admin.get("/api/teams/gov-team")).status() === 404) {
    const team = await ok(
      await admin.post("/api/teams", {
        data: { key: "gov-team", name: "Gov team" },
      }),
      "create gov-team",
    );
    const { id } = (await team.json()) as { id: string };
    await ok(
      await admin.post("/api/teams/gov-team/members", {
        data: { user_id: await userId(admin, VIEWER) },
      }),
      "add the viewer to gov-team",
    );
    await ok(
      await admin.post("/api/bindings", {
        data: {
          subject_kind: "team",
          subject_id: id,
          role: "viewer",
          scope: "project",
          project: "gov",
        },
      }),
      "bind gov-team",
    );
  }

  await ensure(admin, "/api/roles/gov-role", "/api/roles", {
    key: "gov-role",
    name: "Gov role",
    permissions: ["project.read", "flag.toggle"],
  });

  await ensure(admin, "/api/sso/providers/gov-oidc", "/api/sso/providers", {
    key: "gov-oidc",
    name: "Gov OIDC",
    protocol: "oidc",
    enabled: false,
    oidc: { issuer: "https://idp.example.test", client_id: "megooflags" },
    client_secret: "e2e-secret",
  });
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
    await seedNodes(admin);
    await ensureUser(admin, VIEWER);
    await ensureUser(admin, PREFS);
    await ensureUser(admin, REVIEWER);
    const reviewer = await appContext();
    try {
      await signIn(reviewer, REVIEWER.email);
      await seedGov(admin, reviewer);
    } finally {
      await reviewer.dispose();
    }
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
