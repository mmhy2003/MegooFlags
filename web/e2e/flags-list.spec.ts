import { type APIRequestContext, expect, test } from "@playwright/test";

import { adminApi, ok, SHOP } from "./support/api";

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

test("global setup seeds the shop project", async () => {
  const flags = await ok(
    await api.get(`/api/projects/${SHOP}/flags`, { params: { limit: 500 } }),
    "list shop flags",
  );
  const { items } = (await flags.json()) as { items: { key: string }[] };
  const keys = items.map((f) => f.key);
  expect(keys).toContain("checkout-v2");
  expect(keys.filter((k) => k.startsWith("load-"))).toHaveLength(52);

  const production = await ok(
    await api.get(`/api/projects/${SHOP}/environments/production`),
    "read production",
  );
  expect(await production.json()).toMatchObject({ approvals_required: 1 });
});
