import {
  type APIRequestContext,
  type APIResponse,
  request,
} from "@playwright/test";

import {
  ADMIN,
  type E2EUser,
  PASSWORD,
  PREFS,
  USERS,
  VIEWER,
} from "./support/users";

const APP = "http://localhost:3000";
const API = process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000";

/** Through the Next rewrites, with the Origin the API's CSRF check expects. */
function appContext(): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: APP,
    extraHTTPHeaders: { Origin: APP },
  });
}

async function ok(response: APIResponse, what: string): Promise<APIResponse> {
  if (!response.ok())
    throw new Error(
      `${what}: HTTP ${response.status()} ${await response.text()}`,
    );
  return response;
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
