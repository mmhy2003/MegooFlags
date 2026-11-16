"use client";

import { CopyButton } from "@/components/patterns/copy-button";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";

/** A new webhook's signing secret. The API returns it once, so this is the only chance to copy it. */
export function SecretReveal({
  secret,
  onClose,
}: {
  /** Null while there is no secret to show. */
  secret: string | null;
  onClose: () => void;
}) {
  return (
    <ResponsiveDialog
      alert
      open={secret !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Signing secret"
      description="Copy it now. You won't see it again."
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {secret && (
        <>
          <code className="rounded-md bg-muted px-3 py-2 font-mono text-sm break-all">
            {secret}
          </code>
          <CopyButton text={secret} label="Copy secret" />
        </>
      )}
    </ResponsiveDialog>
  );
}
