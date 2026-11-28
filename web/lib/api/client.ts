import createClient from "openapi-fetch";

import type { paths } from "./schema";

export type { components, paths } from "./schema";

/** Typed client for the MegooFlags API. In the browser, the default "" base URL is same-origin through Caddy. */
export function createApiClient(baseUrl = "") {
  return createClient<paths>({ baseUrl });
}

export const api = createApiClient();
