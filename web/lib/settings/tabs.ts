import type { Permission } from "@/lib/access/permissions";

export const SETTINGS_TABS = [
  "members",
  "teams",
  "roles",
  "tokens",
  "sso",
  "security",
  "audit",
] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

export const TAB_LABELS: Record<SettingsTab, string> = {
  members: "Members",
  teams: "Teams",
  roles: "Roles",
  tokens: "Service tokens",
  sso: "SSO",
  security: "Security",
  audit: "Audit log",
};

/** The org permission a tab needs; null when everyone sees it. */
const NEEDS: Record<SettingsTab, Permission | null> = {
  members: "member.manage",
  teams: null,
  roles: null,
  tokens: "token.manage",
  sso: "org.manage",
  security: "org.manage",
  audit: "audit.read",
};

/** The tabs the caller's org permissions show, in order. */
export function visibleTabs(org: readonly Permission[]): SettingsTab[] {
  return SETTINGS_TABS.filter((tab) => {
    const needed = NEEDS[tab];
    return needed === null || org.includes(needed);
  });
}

/** The tab from the URL, or the first visible one when it is unknown or hidden. */
export function pickTab(
  raw: string | null,
  visible: SettingsTab[],
): SettingsTab {
  return visible.find((tab) => tab === raw) ?? visible[0];
}
