import type { Level } from "@/lib/access/permissions";
import { type ApiFailure, failureText } from "@/lib/flags/errors";

/** Where a role binding applies. */
export type ScopeValue = {
  scope: Level;
  project: string | null;
  env: string | null;
};
/** A role at a scope, as edited in a form. */
export type AccessRow = ScopeValue & { role: string };

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** "Organization", "Project shop" or "shop / production". */
export function scopeText(s: ScopeValue): string {
  if (s.scope === "org") return "Organization";
  if (s.scope === "project") return `Project ${s.project}`;
  return `${s.project} / ${s.env}`;
}

/** "viewer · Organization". */
export function bindingText(b: { role: string } & ScopeValue): string {
  return `${b.role} · ${scopeText(b)}`;
}

/** The scope as a request body, without the null fields. */
export function scopeBody(s: ScopeValue): {
  scope: Level;
  project?: string;
  env?: string;
} {
  return {
    scope: s.scope,
    ...(s.project !== null && { project: s.project }),
    ...(s.env !== null && { env: s.env }),
  };
}

/** Whether a binding was made by SSO sign-in rather than by hand. */
export function isFromSso(b: { source: string }): boolean {
  return b.source !== "manual";
}

/** Whether a binding applies in `project`. */
export function reachesProject(b: ScopeValue, project: string): boolean {
  return b.scope === "org" || b.project === project;
}

/** What still uses a role, from an `in_use` failure; its message without a list. */
export function usedByText(f: ApiFailure): string {
  const list = isObj(f.details) ? f.details.used_by : null;
  if (!Array.isArray(list) || list.length === 0) return f.message;
  const users = list.filter(isObj);
  const parts: string[] = [];
  const bindings = users.filter((u) => u.kind === "binding").length;
  if (bindings > 0)
    parts.push(`${bindings} ${bindings === 1 ? "binding" : "bindings"}`);
  for (const u of users) {
    if (u.kind === "sso_provider") parts.push(`SSO provider ${u.provider}`);
    if (u.kind === "sso_mapping")
      parts.push(`group ${u.group} of ${u.provider}`);
  }
  if (parts.length === 0) return f.message;
  return `This role is still used by: ${parts.join(", ")}`;
}

/** A failure of a role, binding or token form, with the API's reason where it gives one. */
export function formFailureText(f: ApiFailure): string {
  if (f.status === 403 || f.code === "exceeds_owner_rights") {
    const held = isObj(f.details) ? f.details.permissions : null;
    if (Array.isArray(held) && held.length > 0)
      return `${f.message}: ${held.join(", ")}`;
    return f.message;
  }
  if (f.code === "in_use") return usedByText(f);
  return failureText(f);
}
