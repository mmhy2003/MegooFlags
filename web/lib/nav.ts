import type { components } from "@/lib/api/schema";
import { projectPath, type ProjectSection } from "@/lib/routes";

export type MePermissions = components["schemas"]["MePermissions"];
type Permission = MePermissions["org"][number];

export type NavKey = ProjectSection | "settings";
export type NavItem = { key: NavKey; label: string; href: string };

const SETTINGS_PERMISSIONS: Permission[] = [
  "org.manage",
  "member.manage",
  "role.manage",
  "token.manage",
];

const PROJECT_ITEMS: {
  key: ProjectSection;
  label: string;
  /** Omitted for items everyone sees. */
  visible?: (perms: MePermissions) => boolean;
}[] = [
  { key: "flags", label: "Flags" },
  { key: "segments", label: "Segments" },
  { key: "environments", label: "Environments" },
  { key: "change-requests", label: "Change requests" },
  {
    key: "audit",
    label: "Audit log",
    visible: (p) => !!p.project?.includes("audit.read"),
  },
  {
    key: "keys",
    label: "API & SDK keys",
    visible: (p) =>
      Object.values(p.environments).some((env) => env.includes("sdk_key.read")),
  },
  {
    key: "integrations",
    label: "Integrations",
    visible: (p) => !!p.project?.includes("project.manage"),
  },
];

/** Sidebar items the caller may use. Nodes stays hidden until multi-node support exists. */
export function navItems(
  project: string | null,
  perms: MePermissions | null,
): NavItem[] {
  const items: NavItem[] = [];
  if (project !== null) {
    for (const item of PROJECT_ITEMS) {
      if (!item.visible || (perms && item.visible(perms))) {
        items.push({
          key: item.key,
          label: item.label,
          href: projectPath(project, item.key),
        });
      }
    }
  }
  if (perms?.org.some((p) => SETTINGS_PERMISSIONS.includes(p))) {
    items.push({ key: "settings", label: "Settings", href: "/settings" });
  }
  return items;
}
