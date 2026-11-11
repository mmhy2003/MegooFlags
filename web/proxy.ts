import { NextResponse, type NextRequest } from "next/server";

import type { components } from "@/lib/api/schema";

type MeOut = components["schemas"]["MeOut"];
type SetupStatus = components["schemas"]["SetupStatus"];

const PUBLIC_PATHS = new Set(["/sign-in", "/sign-in/mfa", "/setup"]);
// Where an enrol_mfa session adds its second factor; redirecting it there again would loop.
const ENROL_PATH = "/account/security";

function redirect(request: NextRequest, pathname: string, search = "") {
  const url = request.nextUrl.clone();
  url.pathname = pathname;
  url.search = search;
  return NextResponse.redirect(url);
}

/** Sends visitors without an active session to sign-in, MFA, enrolment or first-run setup. */
export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (PUBLIC_PATHS.has(pathname) || pathname.startsWith("/invite/")) {
    return NextResponse.next();
  }
  const apiUrl = process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000";
  // The API names the session cookie after the scheme, so it needs the visitor's one.
  const headers = {
    cookie: request.headers.get("cookie") ?? "",
    "x-forwarded-proto":
      request.headers.get("x-forwarded-proto") ??
      request.nextUrl.protocol.replace(/:$/, ""),
  };
  try {
    const res = await fetch(`${apiUrl}/api/auth/me`, {
      headers,
      cache: "no-store",
    });
    if (res.ok) {
      const me = (await res.json()) as MeOut;
      if (me.session_state === "pending_mfa") {
        return redirect(request, "/sign-in/mfa");
      }
      if (me.session_state === "enrol_mfa" && pathname !== ENROL_PATH) {
        return redirect(request, ENROL_PATH, "?enrol=1");
      }
      return NextResponse.next();
    }
    if (res.status === 401) {
      const status = await fetch(`${apiUrl}/api/setup/status`, {
        headers,
        cache: "no-store",
      });
      if (status.ok && ((await status.json()) as SetupStatus).needs_setup) {
        return redirect(request, "/setup");
      }
    }
    const next = new URLSearchParams({ next: `${pathname}${search}` });
    return redirect(request, "/sign-in", `?${next}`);
  } catch {
    return redirect(request, "/sign-in");
  }
}

export const config = {
  matcher: ["/((?!api|sdk|ofrep|_next|favicon.ico|healthz|readyz).*)"],
};
