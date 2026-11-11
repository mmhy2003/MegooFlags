import type { components } from "./schema";

// Fails to compile if the generated schema stops matching the API's health contract.
export const healthExample: components["schemas"]["HealthResponse"] = {
  status: "ok",
  version: "0.1.0",
};
export const readyExample: components["schemas"]["ReadyResponse"] = {
  status: "ready",
};
export const signInExample: components["schemas"]["SignInOut"] = {
  status: "mfa_required",
  mfa_methods: ["totp", "webauthn", "recovery"],
};
export const meExample: components["schemas"]["MeOut"] = {
  kind: "user",
  id: "00000000-0000-0000-0000-000000000001",
  label: "admin@example.com",
  email: "admin@example.com",
  name: "Admin",
  auth_method: "password",
  session_state: "active",
  mfa: { totp: false, passkeys: 0, recovery_codes_left: 0 },
  require_mfa: false,
};
export const errorExample: components["schemas"]["ErrorBody"] = {
  code: "not_found",
  message: "flag 'x' not found",
  details: null,
};
