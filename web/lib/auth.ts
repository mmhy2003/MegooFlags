import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

import { api, type components } from "@/lib/api/client";

export type SignInOut = components["schemas"]["SignInOut"];
export type MeOut = components["schemas"]["MeOut"];
export type SetupIn = components["schemas"]["SetupIn"];
export type PublicProvider = components["schemas"]["PublicProvider"];
export type ErrorBody = components["schemas"]["ErrorBody"];
export type InvitationOut = components["schemas"]["InvitationOut"];
export type PasskeyOut = components["schemas"]["PasskeyOut"];
export type PasskeyRegistered = components["schemas"]["PasskeyRegistered"];
export type SessionOut = components["schemas"]["SessionOut"];
export type TotpEnrolment = components["schemas"]["TotpEnrolment"];

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

export function mfaTotp(code: string) {
  return api.POST("/api/auth/mfa/totp", { body: { code } });
}

export function mfaRecovery(code: string) {
  return api.POST("/api/auth/mfa/recovery", { body: { code } });
}

export function invitation(token: string) {
  return api.GET("/api/invitations/{token}", {
    params: { path: { token } },
  });
}

export function acceptInvitation(token: string, password: string) {
  return api.POST("/api/invitations/{token}", {
    params: { path: { token } },
    body: { password },
  });
}

export function enrolTotp() {
  return api.POST("/api/account/totp");
}

export function confirmTotp(code: string) {
  return api.POST("/api/account/totp/confirm", { body: { code } });
}

export function removeTotp() {
  return api.DELETE("/api/account/totp");
}

export function listPasskeys() {
  return api.GET("/api/account/passkeys");
}

export function renamePasskey(id: string, name: string) {
  return api.PATCH("/api/account/passkeys/{id}", {
    params: { path: { id } },
    body: { name },
  });
}

export function deletePasskey(id: string) {
  return api.DELETE("/api/account/passkeys/{id}", {
    params: { path: { id } },
  });
}

export function regenerateRecoveryCodes() {
  return api.POST("/api/account/recovery-codes");
}

export function changePassword(current_password: string, new_password: string) {
  return api.PUT("/api/account/password", {
    body: { current_password, new_password },
  });
}

export function listSessions() {
  return api.GET("/api/account/sessions");
}

export function revokeSession(id: string) {
  return api.DELETE("/api/account/sessions/{id}", {
    params: { path: { id } },
  });
}

export function revokeOtherSessions() {
  return api.DELETE("/api/account/sessions/others");
}

/** Register a passkey on this account: options, the authenticator, then save it under `name`.
 * Throws when the browser or the user cancels the ceremony. */
export async function registerPasskey(name: string): Promise<{
  data?: PasskeyRegistered;
  error?: ErrorBody;
  response: Response;
}> {
  const options = await api.POST("/api/account/passkeys/options");
  if (options.data === undefined) {
    return { error: options.error, response: options.response };
  }
  const credential = await startRegistration({
    optionsJSON: options.data
      .options as unknown as PublicKeyCredentialCreationOptionsJSON,
  });
  return api.POST("/api/account/passkeys", {
    body: {
      challenge_id: options.data.challenge_id,
      name,
      credential: { ...credential },
    },
  });
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

/** `next` when it is a path on this site, else null. Rejects `//host`, any backslash (`/\host`)
 * and any control character, space or DEL: browsers strip tab, CR and LF before parsing, so
 * `/\t/host` would become `//host`. Stricter than the API's `safe_next`, never looser. */
export function safeNext(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return null;
  for (const char of next) {
    const code = char.charCodeAt(0);
    if (code < 0x21 || code === 0x7f || char === "\\") return null;
  }
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

/** The message for a failed MFA code: 401 is a wrong code, 429 says how long to wait. */
export function codeFailure(response: Response, fallback: string): string {
  if (response.status === 401) return "Invalid code";
  if (response.status === 429) {
    const seconds = retryAfter(response);
    return seconds === null
      ? "Too many attempts, try again later"
      : `Too many attempts, try again in ${seconds} s`;
  }
  return fallback;
}

/** Seconds from a 429's Retry-After header, or null. */
export function retryAfter(response: Response): number | null {
  const seconds = Number(response.headers.get("retry-after"));
  return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : null;
}
