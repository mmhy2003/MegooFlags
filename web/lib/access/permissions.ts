import type { components } from "@/lib/api/schema";
import type { MePermissions } from "@/lib/nav";

export type Permission = components["schemas"]["Permission"];
/** Where a role is granted: "org" | "project" | "environment". */
export type Level = components["schemas"]["ScopeKind"];
/** A catalogue entry: a permission and the narrowest level it works at. */
export type PermissionInfo = components["schemas"]["PermissionOut"];

/** What each permission lets a person do, in a few words. */
export const PERMISSION_LABELS: Record<Permission, string> = {
  "org.manage": "Manage organization settings and SSO",
  "member.manage": "Manage members and teams",
  "role.manage": "Manage roles",
  "token.manage": "Manage service tokens",
  "project.create": "Create projects",
  "audit.read": "Read the audit log",
  "project.read": "See the project",
  "project.manage": "Manage the project",
  "environment.manage": "Manage environments",
  "flag.create": "Create flags",
  "flag.update": "Edit flags and variations",
  "flag.archive": "Archive, restore and delete flags",
  "segment.write": "Edit segments",
  "flag.config.write": "Edit targeting",
  "flag.toggle": "Turn flags on and off",
  "change.approve": "Approve change requests",
  evaluate: "Evaluate flags",
  "sdk_key.read": "See SDK keys",
  "sdk_key.manage": "Manage SDK keys",
};

/** The name of each level and where its permissions work. */
export const LEVEL_TEXT: Record<Level, { label: string; where: string }> = {
  org: {
    label: "Organization",
    where: "Works only when granted for the whole organization.",
  },
  project: {
    label: "Project",
    where: "Works for the whole organization or for one project.",
  },
  environment: {
    label: "Environment",
    where: "Works for the organization, a project or one environment.",
  },
};

const LEVELS: Level[] = ["org", "project", "environment"];

/** Whether a permission of `level` works when granted at `scope`. */
export function worksAt(level: Level, scope: Level): boolean {
  return LEVELS.indexOf(scope) <= LEVELS.indexOf(level);
}

/** The catalogue grouped by level (org first), in catalogue order, without empty groups. */
export function groupByLevel(
  catalogue: PermissionInfo[],
): { level: Level; permissions: Permission[] }[] {
  return LEVELS.map((level) => ({
    level,
    permissions: catalogue
      .filter((p) => p.level === level)
      .map((p) => p.permission),
  })).filter((g) => g.permissions.length > 0);
}

/** The permissions the caller holds at `scope` (`env` for the environment level). */
export function heldAt(
  perms: MePermissions,
  scope: Level,
  env?: string,
): Permission[] {
  if (scope === "org") return perms.org;
  if (scope === "project") return perms.project ?? [];
  return (env !== undefined && perms.environments[env]) || [];
}

/** The held permissions that work at `scope`, in catalogue order. */
export function grantableAt(
  catalogue: PermissionInfo[],
  held: readonly Permission[],
  scope: Level,
): Permission[] {
  return catalogue
    .filter((p) => held.includes(p.permission) && worksAt(p.level, scope))
    .map((p) => p.permission);
}
