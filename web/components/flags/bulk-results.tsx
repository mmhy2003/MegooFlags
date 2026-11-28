"use client";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import type { BulkResult } from "@/lib/flags/bulk";

/** What a bulk action did to each flag, in the order they were selected. */
export function BulkResults({
  results,
  onClose,
}: {
  results: BulkResult[] | null;
  onClose: () => void;
}) {
  const failed = results?.filter((r) => !r.ok).length ?? 0;
  const done = (results?.length ?? 0) - failed;
  return (
    <ResponsiveDialog
      open={results !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="Bulk results"
      description={`${done} done, ${failed} failed`}
      footer={<DialogClose render={<Button />}>Close</DialogClose>}
    >
      <ul className="flex flex-col gap-1 text-sm">
        {results?.map((r) => (
          <li
            key={r.key}
            className={r.ok ? "break-words" : "break-words text-destructive"}
          >
            {r.ok ? `${r.key}: Done` : `${r.key}: Failed, ${r.reason}`}
          </li>
        ))}
      </ul>
    </ResponsiveDialog>
  );
}
