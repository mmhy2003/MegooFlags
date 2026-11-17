"use client";

import { useEffect, useRef } from "react";

type VisibilityDocument = Pick<
  Document,
  "visibilityState" | "addEventListener" | "removeEventListener"
>;

/**
 * Calls `callback` every `ms` while `doc` is visible, and once at once when it becomes visible
 * again. Returns a function that stops it.
 */
export function createVisibleInterval(
  callback: () => void,
  ms: number,
  doc: VisibilityDocument,
): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  const start = () => {
    if (timer === null) timer = setInterval(callback, ms);
  };
  const pause = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };
  const onChange = () => {
    if (doc.visibilityState === "visible") {
      if (timer !== null) return;
      callback();
      start();
    } else {
      pause();
    }
  };
  if (doc.visibilityState === "visible") start();
  doc.addEventListener("visibilitychange", onChange);
  return () => {
    pause();
    doc.removeEventListener("visibilitychange", onChange);
  };
}

/** Calls `callback` every `ms` while the page is visible, and once on becoming visible. */
export function useVisibleInterval(callback: () => void, ms: number): void {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });
  useEffect(
    () => createVisibleInterval(() => callbackRef.current(), ms, document),
    [ms],
  );
}
