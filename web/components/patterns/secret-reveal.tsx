"use client";

import type { ReactNode } from "react";

import { CopyButton } from "@/components/patterns/copy-button";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";

/** A secret the API returns once, such as a signing secret or a new key. Only Done closes it. */
export function SecretReveal({
  secret,
  title,
  description = "Copy it now. You won't see it again.",
  copyLabel,
  onClose,
  children,
}: {
  /** Null while there is no secret to show. */
  secret: string | null;
  title: string;
  description?: ReactNode;
  copyLabel: string;
  onClose: () => void;
  /** Shown under the copy button, such as how to use the secret. */
  children?: ReactNode;
}) {
  return (
    <ResponsiveDialog
      alert
      open={secret !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={title}
      description={description}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {secret && (
        <>
          <code className="rounded-md bg-muted px-3 py-2 font-mono text-sm break-all">
            {secret}
          </code>
          <CopyButton text={secret} label={copyLabel} />
          {children}
        </>
      )}
    </ResponsiveDialog>
  );
}
