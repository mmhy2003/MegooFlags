"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { UsedByList } from "@/components/segments/used-by";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  ApiError,
  type ApiFailure,
  failureText,
  must,
} from "@/lib/flags/errors";
import { projectPath } from "@/lib/routes";

type SegmentUsage = components["schemas"]["SegmentUsage"];

/** The uses listed by a 409 `in_use` failure; null for any other failure. */
function inUseList(f: ApiFailure): SegmentUsage[] | null {
  if (f.code !== "in_use") return null;
  const details = f.details as { used_by?: unknown } | null | undefined;
  const list = Array.isArray(details?.used_by) ? details.used_by : [];
  return list.filter(
    (u): u is SegmentUsage =>
      typeof u === "object" &&
      u !== null &&
      typeof (u as SegmentUsage).flag === "string" &&
      typeof (u as SegmentUsage).environment === "string",
  );
}

/**
 * Deletes a segment, then goes back to the list. While flags use it (as loaded, or as the API
 * answers with a 409) it lists them instead and offers only Close.
 */
export function DeleteSegmentDialog({
  project,
  segment,
  name,
  usedBy,
  open,
  onOpenChange,
}: {
  project: string;
  segment: string;
  name: string;
  usedBy: SegmentUsage[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [sending, setSending] = useState(false);
  // The uses the API named when it refused, which may differ from the loaded ones.
  const [refused, setRefused] = useState<SegmentUsage[] | null>(null);
  const uses = refused ?? usedBy;

  const setOpen = (next: boolean) => {
    onOpenChange(next);
    if (!next) setRefused(null);
  };

  const remove = async () => {
    setSending(true);
    try {
      await must(
        api.DELETE("/api/projects/{project}/segments/{segment}", {
          params: { path: { project, segment } },
        }),
      );
      toast("Segment deleted");
      onOpenChange(false);
      router.push(projectPath(project, "segments"));
    } catch (error) {
      const list = error instanceof ApiError ? inUseList(error.failure) : null;
      if (list && list.length > 0) setRefused(list);
      else {
        if (!(error instanceof ApiError))
          console.error("deleting the segment failed", error);
        toast.error(
          error instanceof ApiError
            ? failureText(error.failure)
            : "Couldn't reach the server",
        );
      }
      setSending(false);
    }
  };

  if (uses.length > 0)
    return (
      <ResponsiveDialog
        open={open}
        onOpenChange={setOpen}
        title={`${name} is used by these flags`}
        description="Remove the segment from these flags' rules before you delete it."
        footer={
          <DialogClose render={<Button variant="outline" />}>Close</DialogClose>
        }
      >
        <UsedByList project={project} usedBy={uses} />
      </ResponsiveDialog>
    );

  return (
    <ResponsiveDialog
      alert
      open={open}
      onOpenChange={setOpen}
      title={`Delete ${name}?`}
      description="This removes the segment with its keys and rules. It can't be undone."
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button
            variant="destructive"
            disabled={sending}
            onClick={() => void remove()}
          >
            Delete segment
          </Button>
        </>
      }
    />
  );
}
