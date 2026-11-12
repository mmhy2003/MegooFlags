import { cookies } from "next/headers";
import { unstable_rethrow } from "next/navigation";

import { serverApi } from "@/lib/api/server";
import type { components } from "@/lib/api/schema";
import type { MePermissions } from "@/lib/nav";
import { PROJECT_COOKIE, SIDEBAR_COOKIE, sidebarOpen } from "@/lib/theme";

export type MeOut = components["schemas"]["MeOut"];
export type ProjectSummary = components["schemas"]["ProjectSummary"];

export type ShellData = {
  me: MeOut | null;
  /** Null when the list failed to load. */
  projects: ProjectSummary[] | null;
  /** Null when they failed to load; the shell then shows only the always-visible items. */
  permissions: MePermissions | null;
  /** The project `permissions` were loaded for. */
  permissionsProject: string | null;
  sidebarOpen: boolean;
};

/** The data of an API call, or null after an error, which is logged. */
async function orNull<T>(
  what: string,
  call: Promise<{ data?: T; error?: unknown }>,
): Promise<T | null> {
  try {
    const { data, error } = await call;
    if (error !== undefined) console.error(`shell: ${what} failed`, error);
    return data ?? null;
  } catch (error) {
    // Next's own signals (a prerender ending, a redirect) must propagate.
    unstable_rethrow(error);
    console.error(`shell: ${what} failed`, error);
    return null;
  }
}

/** What the app shell needs, loaded on the server. Each failure becomes null and is logged. */
export async function loadShell(): Promise<ShellData> {
  const api = await serverApi();
  const c = await cookies();
  const me = await orNull("/me", api.GET("/api/auth/me"));
  const open = sidebarOpen(
    c.get(SIDEBAR_COOKIE)?.value,
    me?.preferences?.sidebar_collapsed,
  );
  if (me?.session_state !== "active") {
    return {
      me,
      projects: null,
      permissions: null,
      permissionsProject: null,
      sidebarOpen: open,
    };
  }

  const page = await orNull(
    "projects",
    api.GET("/api/projects", { params: { query: { limit: 500 } } }),
  );
  const projects = page?.items ?? null;
  const keys = projects?.map((p) => p.key) ?? [];
  const last = c.get(PROJECT_COOKIE)?.value;
  const project = last && keys.includes(last) ? last : (keys[0] ?? null);
  const permissions = await orNull(
    "permissions",
    api.GET("/api/auth/me/permissions", {
      params: { query: project ? { project } : {} },
    }),
  );
  return {
    me,
    projects,
    permissions,
    permissionsProject: project,
    sidebarOpen: open,
  };
}
