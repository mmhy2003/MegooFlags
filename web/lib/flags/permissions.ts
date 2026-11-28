import type { components } from "@/lib/api/schema";
import type { MePermissions } from "@/lib/nav";

/** Whether the caller holds a permission in the project, or in one of its environments. */
export function can(
  perms: MePermissions | null,
  p: components["schemas"]["Permission"],
  env?: string,
): boolean {
  if (!perms) return false;
  const held = env === undefined ? perms.project : perms.environments[env];
  return !!held?.includes(p);
}
