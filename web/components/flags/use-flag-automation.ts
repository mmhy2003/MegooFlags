"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useVisibleInterval } from "@/hooks/use-visible-interval";
import { api } from "@/lib/api/client";
import { ApiError, type ApiFailure, must } from "@/lib/flags/errors";
import {
  type AutomationSnapshot,
  needsPolling,
  wasApplied,
} from "@/lib/flags/schedules";

const EMPTY: AutomationSnapshot = { rollout: null, schedules: null };
const POLL_MS = 30_000;

const NETWORK: ApiFailure = {
  status: 0,
  code: "network",
  message: "Couldn't reach the server",
  path: null,
};

type State = {
  env: string;
  data: AutomationSnapshot;
  failure: ApiFailure | null;
};

/**
 * The selected environment's rollout and schedules, loaded together. A reload keeps the last
 * data until it answers; another environment starts empty. It reloads every 30 seconds while
 * a rollout runs or a schedule is pending, and calls `onApplied` when a reload shows that one
 * of them changed the flag.
 */
export function useFlagAutomation(
  project: string,
  flag: string,
  env: string,
  onApplied: () => void,
): {
  data: AutomationSnapshot;
  failure: ApiFailure | null;
  reload: () => Promise<void>;
} {
  const [state, setState] = useState<State>({
    env,
    data: EMPTY,
    failure: null,
  });
  // The last settled load, compared with the next one; only this hook's loads change it.
  const last = useRef<State>(state);
  const loads = useRef(0);
  const onAppliedRef = useRef(onApplied);
  useEffect(() => {
    onAppliedRef.current = onApplied;
  });

  const reload = useCallback(async () => {
    if (!env) return;
    const load = ++loads.current;
    const path = { project, flag, env };
    let next: State;
    try {
      const [rollout, schedules] = await Promise.all([
        must(
          api.GET(
            "/api/projects/{project}/flags/{flag}/environments/{env}/rollout",
            { params: { path } },
          ),
        ),
        must(
          api.GET(
            "/api/projects/{project}/flags/{flag}/environments/{env}/schedules",
            { params: { path } },
          ),
        ),
      ]);
      next = { env, data: { rollout, schedules }, failure: null };
    } catch (error) {
      if (!(error instanceof ApiError)) console.error("load failed", error);
      const failure = error instanceof ApiError ? error.failure : NETWORK;
      const data = last.current.env === env ? last.current.data : EMPTY;
      next = { env, data, failure };
    }
    if (load !== loads.current) return;
    const before = last.current.env === env ? last.current.data : EMPTY;
    last.current = next;
    setState(next);
    if (wasApplied(before, next.data)) onAppliedRef.current();
  }, [project, flag, env]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const current =
    state.env === env ? state : { env, data: EMPTY, failure: null };
  useVisibleInterval(() => {
    if (needsPolling(current.data)) void reload();
  }, POLL_MS);

  return { data: current.data, failure: current.failure, reload };
}
