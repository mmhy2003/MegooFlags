import { cookies, headers } from "next/headers";
import createClient from "openapi-fetch";

import { apiBaseUrl } from "./base";
import type { paths } from "./schema";

/**
 * Typed API client for server components. It forwards the visitor's cookie and scheme, as
 * proxy.ts does, because the API names the session cookie after the scheme.
 */
export async function serverApi() {
  const c = await cookies();
  const h = await headers();
  return createClient<paths>({
    baseUrl: apiBaseUrl(),
    headers: {
      cookie: c.toString(),
      "x-forwarded-proto": h.get("x-forwarded-proto") ?? "http",
    },
    fetch: (request) => fetch(request, { cache: "no-store" }),
  });
}
