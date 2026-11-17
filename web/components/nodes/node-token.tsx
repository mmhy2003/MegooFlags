"use client";

import { CopyButton } from "@/components/patterns/copy-button";
import { envLines } from "@/lib/nodes/format";

export const TOKEN_WARNING = "Copy it now. You won't see it again.";

/** A worker's new token and the `.env` lines that join it to this control plane. Shown once. */
export function NodeToken({ token }: { token: string }) {
  const lines = envLines(window.location.origin, token);
  return (
    <>
      <code className="rounded-md bg-muted px-3 py-2 font-mono text-sm break-all">
        {token}
      </code>
      <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-sm">
        {lines}
      </pre>
      <CopyButton text={lines} label="Copy .env lines" />
    </>
  );
}
