"use client";

import { useId, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/patterns/confirm-dialog";
import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { isLastApproval } from "@/lib/change-requests/status";
import { ApiError, failureText, must } from "@/lib/flags/errors";

type CR = components["schemas"]["ChangeRequestOut"];

type Props = {
  project: string;
  cr: CR;
  /** Gets the request as the API returned it after an action. */
  onChanged: (cr: CR) => void;
  /** Loads the request again, when someone else closed it meanwhile. */
  onReload: () => void;
};

const OUTCOMES: Partial<Record<CR["status"], string>> = {
  applied: "Change applied",
  rejected: "Change request rejected",
  cancelled: "Change request cancelled",
};

/** A dialog with a comment box and one submit button, for Approve and Reject. */
function ReviewDialog({
  title,
  description,
  label,
  fieldLabel,
  required,
  variant,
  onSubmit,
}: {
  title: string;
  description?: string;
  label: string;
  fieldLabel: string;
  required: boolean;
  variant: "default" | "destructive";
  onSubmit: (comment: string) => Promise<boolean>;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    const done = await onSubmit(comment.trim());
    setSending(false);
    if (done) {
      setOpen(false);
      setComment("");
    }
  };

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button variant={variant} className="min-h-11 flex-1 sm:flex-none">
          {label}
        </Button>
      }
      title={title}
      description={description}
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-comment`}>{fieldLabel}</Label>
          <Textarea
            id={`${id}-comment`}
            value={comment}
            maxLength={10000}
            required={required}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <DialogClose
            render={
              <Button type="button" variant="outline" className="min-h-11" />
            }
          >
            Cancel
          </DialogClose>
          <Button
            type="submit"
            variant={variant}
            className="min-h-11"
            disabled={sending || (required && comment.trim() === "")}
          >
            {label}
          </Button>
        </div>
      </form>
    </ResponsiveDialog>
  );
}

/**
 * The bar at the bottom of a change request: Approve and Reject for reviewers, Cancel request
 * for its author and project managers. It stays in view while the page scrolls (UI-15).
 */
export function ReviewActions({ project, cr, onChanged, onReload }: Props) {
  if (!cr.can_approve && !cr.can_cancel) return null;
  const path = { params: { path: { project, id: cr.id } } };

  /** Runs an action; whether it went through, so its dialog can close. */
  const run = async (
    call: () => Promise<CR>,
    success: string,
  ): Promise<boolean> => {
    try {
      const next = await call();
      if (next.status === "failed")
        toast.error("The change couldn't be applied");
      else toast(OUTCOMES[next.status] ?? success);
      onChanged(next);
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.failure.code === "invalid_state") {
        toast("Someone else closed this request");
        onReload();
        return true;
      }
      if (!(error instanceof ApiError)) console.error("review failed", error);
      toast.error(
        error instanceof ApiError
          ? failureText(error.failure)
          : "Couldn't reach the server",
      );
      return false;
    }
  };

  return (
    <section
      aria-label="Review actions"
      className="sticky bottom-0 z-10 -mx-4 mt-6 border-t bg-background px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:-mx-6 md:px-6"
    >
      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
        {cr.can_cancel && (
          <ConfirmDialog
            trigger={
              <Button
                variant="outline"
                className="min-h-11 flex-1 sm:flex-none"
              >
                Cancel request
              </Button>
            }
            title="Cancel this change request?"
            description="It closes without changing the flag. This can't be undone."
            confirmLabel="Cancel request"
            destructive
            onConfirm={() =>
              void run(
                () =>
                  must(
                    api.POST(
                      "/api/projects/{project}/change-requests/{id}/cancel",
                      path,
                    ),
                  ),
                "Change request cancelled",
              )
            }
          />
        )}
        {cr.can_approve && (
          <>
            <ReviewDialog
              title="Reject change request"
              description="Rejecting closes the request without changing the flag."
              label="Reject"
              fieldLabel="Reason"
              required
              variant="destructive"
              onSubmit={(comment) =>
                run(
                  () =>
                    must(
                      api.POST(
                        "/api/projects/{project}/change-requests/{id}/reject",
                        { ...path, body: { comment } },
                      ),
                    ),
                  "Change request rejected",
                )
              }
            />
            <ReviewDialog
              title="Approve change request"
              description={
                isLastApproval(cr)
                  ? "This is the last approval needed, so the change is applied at once."
                  : undefined
              }
              label="Approve"
              fieldLabel="Comment (optional)"
              required={false}
              variant="default"
              onSubmit={(comment) =>
                run(
                  () =>
                    must(
                      api.POST(
                        "/api/projects/{project}/change-requests/{id}/approve",
                        { ...path, body: comment ? { comment } : {} },
                      ),
                    ),
                  "Change request approved",
                )
              }
            />
          </>
        )}
      </div>
    </section>
  );
}
