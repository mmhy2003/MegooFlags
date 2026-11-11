import {
  startAuthentication,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

import { api, type components } from "@/lib/api/client";

export type SignInOut = components["schemas"]["SignInOut"];
export type MeOut = components["schemas"]["MeOut"];
export type SetupIn = components["schemas"]["SetupIn"];
export type PublicProvider = components["schemas"]["PublicProvider"];
export type ErrorBody = components["schemas"]["ErrorBody"];

export function signIn(email: string, password: string) {
  return api.POST("/api/auth/sign-in", { body: { email, password } });
}

export function setup(body: SetupIn) {
  return api.POST("/api/setup", { body });
}

export function signOut() {
  return api.POST("/api/auth/sign-out");
}

export function me() {
  return api.GET("/api/auth/me");
}

export function providers() {
  return api.GET("/api/auth/providers");
}

/** Passwordless sign-in, or the second factor after a password: options, the authenticator, verify.
 * Throws when the browser or the user cancels the ceremony. */
export async function passkeySignIn(): Promise<{
  data?: SignInOut;
  error?: ErrorBody;
  response: Response;
}> {
  const options = await api.POST("/api/auth/webauthn/options");
  if (options.data === undefined) {
    return { error: options.error, response: options.response };
  }
  const credential = await startAuthentication({
    optionsJSON: options.data
      .options as unknown as PublicKeyCredentialRequestOptionsJSON,
  });
  return api.POST("/api/auth/webauthn/verify", {
    body: {
      challenge_id: options.data.challenge_id,
      credential: { ...credential },
    },
  });
}

/** `next` when it is a path on this site, else null. Rejects `//host` and `/\host`, which browsers
 * treat as another origin. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return null;
  if (next.startsWith("/\\")) return null;
  return next;
}

/** Where to go after a sign-in step succeeds. */
export function destinationFor(
  status: SignInOut["status"],
  next: string | null | undefined,
): string {
  switch (status) {
    case "mfa_required":
      return "/sign-in/mfa";
    case "enrol_mfa":
      return "/account/security?enrol=1";
    default:
      return safeNext(next) ?? "/";
  }
}

/** Seconds from a 429's Retry-After header, or null. */
export function retryAfter(response: Response): number | null {
  const seconds = Number(response.headers.get("retry-after"));
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : null;
}
