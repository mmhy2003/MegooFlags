"use client";

import { AccessEditor } from "@/components/access/access-editor";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";

/** The roles a service token holds, with grants kept in `lockedProject` when it is set. */
export function TokenAccessDialog({
  token,
  open,
  onOpenChange,
  lockedProject,
  onChanged,
}: {
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  token: { id: string; name: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lockedProject?: string;
  onChanged: () => void;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Access of ${token?.name ?? "the token"}`}
      description="Changes apply at once, also to a token that is in use."
    >
      {open && token && (
        <AccessEditor
          subjectKind="service_token"
          subjectId={token.id}
          readOnly={false}
          lockedProject={lockedProject}
          onChanged={onChanged}
        />
      )}
    </ResponsiveDialog>
  );
}
