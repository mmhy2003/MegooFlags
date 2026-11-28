import type { components } from "@/lib/api/schema";
import { projectPath, type ProjectSection } from "@/lib/routes";

export type MePermissions = components["schemas"]["MePermissions"];
export type InstanceRole = components["schemas"]["InstanceRole"];
type Permission = MePermissions["org"][number];

export type NavKey =
  | Exclude<ProjectSection, "settings">
  | "project-settings"
  | "nodes"
  | "settings";
export type NavItem = { key: NavKey; label: string; href: string };

const SETTINGS_PERMISSIONS: Permission[] = [
  "org.manage",
  "member.manage",
  "role.manage",
  "token.manage",
];

const PROJECT_ITEMS: {
  key: NavKey;
  section: ProjectSection;
  label: string;
  /** Omitted for items everyone sees. */
  visible?: (perms: MePermissions) => boolean;
}[] = [
  { key: "flags", section: "flags", label: "Flags" },
  { key: "segments", section: "segments", label: "Segments" },
  { key: "environments", section: "environments", label: "Environments" },
  {
    key: "change-requests",
    section: "change-requests",
    label: "Change requests",
  },
  {
    key: "audit",
    section: "audit",
    label: "Audit log",
    visible: (p) => !!p.project?.includes("audit.read"),
  },
  {
    key: "keys",
    section: "keys",
    label: "API & SDK keys",
    visible: (p) =>
      Object.values(p.environments).some((env) =>
        env.includes("sdk_key.read"),
      ) || p.org.includes("token.manage"),
  },
  {
    key: "integrations",
    section: "integrations",
    label: "Integrations",
    visible: (p) => !!p.project?.includes("project.manage"),
  },
  {
    key: "project-settings",
    section: "settings",
    label: "Project settings",
    visible: (p) => !!p.project?.includes("project.manage"),
  },
];

/** Sidebar items the caller may use. Nodes exists only on a control plane. */
export function navItems(
  project: string | null,
  perms: MePermissions | null,
  role: InstanceRole | null,
): NavItem[] {
  const items: NavItem[] = [];
  if (project !== null) {
    for (const item of PROJECT_ITEMS) {
      if (!item.visible || (perms && item.visible(perms))) {
        items.push({
          key: item.key,
          label: item.label,
          href: projectPath(project, item.section),
        });
      }
    }
  }
  if (role === "control-plane" && perms?.org.includes("org.manage")) {
    items.push({ key: "nodes", label: "Nodes", href: "/nodes" });
  }
  // Org-wide audit readers get Settings for its Audit log tab.
  if (
    perms?.org.some(
      (p) => SETTINGS_PERMISSIONS.includes(p) || p === "audit.read",
    )
  ) {
    items.push({ key: "settings", label: "Settings", href: "/settings" });
  }
  return items;
}
