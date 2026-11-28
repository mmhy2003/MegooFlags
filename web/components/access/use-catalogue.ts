"use client";

import { useApiData } from "@/hooks/use-api-data";
import type { PermissionInfo } from "@/lib/access/permissions";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { must } from "@/lib/flags/errors";

type RoleOut = components["schemas"]["RoleOut"];

/** Every permission and the level it works at; null while loading or after a failure. */
export function usePermissionCatalogue(): PermissionInfo[] | null {
  return useApiData("permissions", () => must(api.GET("/api/permissions")))
    .data;
}

/** The organization's roles, built-in and custom; null while loading or after a failure. */
export function useRoles(): RoleOut[] | null {
  return useApiData("roles", () =>
    must(api.GET("/api/roles", { params: { query: { limit: 500 } } })).then(
      (page) => page.items,
    ),
  ).data;
}

/** The keys of the projects the caller can see; null while loading or after a failure. */
export function useProjectKeys(): string[] | null {
  return useApiData("projects", () =>
    must(api.GET("/api/projects", { params: { query: { limit: 500 } } })).then(
      (page) => page.items.map((p) => p.key),
    ),
  ).data;
}

/** The environment keys of `project`, in its order; null without a project or while loading. */
export function useEnvironmentKeys(project: string | null): string[] | null {
  return useApiData(project, () =>
    must(
      api.GET("/api/projects/{project}/environments", {
        params: { path: { project: project ?? "" } },
      }),
    ).then((envs) => envs.map((e) => e.key)),
  ).data;
}
