"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { MePermissions } from "@/lib/nav";

/**
 * The caller's permissions in `project`: the server-loaded ones while the project matches,
 * otherwise fetched in the browser. Null while loading or after a failure, which shows only the
 * always-visible items; the API still enforces every permission.
 */
export function useProjectPermissions(
  project: string | null,
  initial: MePermissions | null,
  initialProject: string | null,
): MePermissions | null {
  const [fetched, setFetched] = useState<{
    project: string;
    permissions: MePermissions | null;
  } | null>(null);
  const needsFetch = project !== null && project !== initialProject;

  useEffect(() => {
    if (!needsFetch) return;
    let cancelled = false;
    api
      .GET("/api/auth/me/permissions", { params: { query: { project } } })
      .then(({ data, error }) => {
        if (error) console.error("permissions failed", error);
        if (!cancelled) setFetched({ project, permissions: data ?? null });
      })
      .catch((error: unknown) => {
        console.error("permissions failed", error);
        if (!cancelled) setFetched({ project, permissions: null });
      });
    return () => {
      cancelled = true;
    };
  }, [needsFetch, project]);

  if (!needsFetch) return initial;
  return fetched?.project === project ? fetched.permissions : null;
}
