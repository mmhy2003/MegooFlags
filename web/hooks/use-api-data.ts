"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type ApiFailure } from "@/lib/flags/errors";

const NETWORK: ApiFailure = {
  status: 0,
  code: "network",
  message: "Couldn't reach the server",
  path: null,
};

type State<T> = {
  key: string | null;
  data: T | null;
  failure: ApiFailure | null;
};

/**
 * Loads data for `key`, and again whenever it changes or `reload` is called. A null key loads
 * nothing. Responses for an older key or load are ignored.
 */
export function useApiData<T>(
  key: string | null,
  load: () => Promise<T>,
): {
  data: T | null;
  failure: ApiFailure | null;
  loading: boolean;
  reload: () => void;
} {
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });
  const [run, setRun] = useState(0);
  const [state, setState] = useState<State<T> & { run: number }>({
    key: null,
    data: null,
    failure: null,
    run: -1,
  });

  useEffect(() => {
    if (key === null) return;
    let current = true;
    loadRef.current().then(
      (data) => {
        if (current) setState({ key, data, failure: null, run });
      },
      (error: unknown) => {
        if (!current) return;
        if (!(error instanceof ApiError)) console.error("load failed", error);
        const failure = error instanceof ApiError ? error.failure : NETWORK;
        setState({ key, data: null, failure, run });
      },
    );
    return () => {
      current = false;
    };
  }, [key, run]);

  const reload = useCallback(() => setRun((n) => n + 1), []);
  const settled = key !== null && state.key === key && state.run === run;
  return {
    data: settled ? state.data : null,
    failure: settled ? state.failure : null,
    loading: key !== null && !settled,
    reload,
  };
}
