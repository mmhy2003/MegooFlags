"use client";

import { Button } from "@/components/ui/button";

/**
 * A bar stuck to the bottom of the page while the draft has unsaved changes, with Discard and
 * Review. In an environment that needs approval, Review leads to a change request.
 */
export function UnsavedBar({
  isDirty,
  needsApproval,
  onDiscard,
  onReview,
}: {
  isDirty: boolean;
  needsApproval: boolean;
  onDiscard: () => void;
  onReview: () => void;
}) {
  if (!isDirty) return null;
  return (
    <div
      role="status"
      aria-label="Unsaved changes"
      className="sticky bottom-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-lg"
    >
      <span className="font-medium">Unsaved changes</span>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" className="min-h-11" onClick={onDiscard}>
          Discard
        </Button>
        <Button className="min-h-11" onClick={onReview}>
          {needsApproval ? "Review & request change" : "Review"}
        </Button>
      </div>
    </div>
  );
}
