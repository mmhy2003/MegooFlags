import {
  type APIRequestContext,
  type APIResponse,
  request,
} from "@playwright/test";

import { ADMIN } from "./users";

export const APP = "http://localhost:3000";
export const API = process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000";

/** The project the flag specs use; global setup recreates it on every run. */
export const SHOP = "shop";

export async function ok(
  response: APIResponse,
  what: string,
): Promise<APIResponse> {
  if (!response.ok())
    throw new Error(
      `${what}: HTTP ${response.status()} ${await response.text()}`,
    );
  return response;
}

/** The admin, through the Next rewrites, with the Origin the API's CSRF check expects. Dispose it. */
export function adminApi(): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: APP,
    storageState: ADMIN.storageState,
    extraHTTPHeaders: { Origin: APP },
  });
}

type TestFlag = {
  prefix?: string;
  type?: "boolean" | "string";
  tags?: string[];
};

/** Creates a flag with a unique key in shop, so a retried test never sees an earlier run's changes. */
export async function createTestFlag(
  api: APIRequestContext,
  { prefix = "t", type = "boolean", tags = [] }: TestFlag = {},
): Promise<string> {
  const key = `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const strings =
    type === "string"
      ? {
          variations: [
            { key: "a", value: "a" },
            { key: "b", value: "b" },
          ],
          off_variation: "a",
        }
      : {};
  await ok(
    await api.post(`/api/projects/${SHOP}/flags`, {
      data: { key, name: key, type, tags, ...strings },
    }),
    `create flag ${key}`,
  );
  return key;
}

/** The body of PUT …/environments/{env}, as GET returns it. */
export type FlagConfig = {
  version: number;
  enabled: boolean;
  targets: { variation: string; values: string[] }[];
  rules: object[];
  fallthrough: object;
  prerequisites: { flag: string; variation: string }[];
};

/** GETs the flag's config in env, lets `mutate` change it, then PUTs it with the loaded version. */
export async function putConfig(
  api: APIRequestContext,
  flag: string,
  env: string,
  mutate: (config: FlagConfig) => void,
): Promise<FlagConfig> {
  const path = `/api/projects/${SHOP}/flags/${flag}/environments/${env}`;
  const current = await ok(await api.get(path), `GET ${path}`);
  const { version, enabled, targets, rules, fallthrough, prerequisites } =
    (await current.json()) as FlagConfig;
  const config = {
    version,
    enabled,
    targets,
    rules,
    fallthrough,
    prerequisites,
  };
  mutate(config);
  const saved = await ok(await api.put(path, { data: config }), `PUT ${path}`);
  return (await saved.json()) as FlagConfig;
}
