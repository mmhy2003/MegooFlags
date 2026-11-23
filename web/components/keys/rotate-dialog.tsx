"use client";

import { useId, useState } from "react";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { DEFAULT_OVERLAP, OVERLAP_CHOICES } from "@/lib/tokens/expiry";

function Form({
  onRotate,
  onDone,
  submitLabel,
}: {
  onRotate: (overlapSeconds: number) => Promise<void>;
  onDone: () => void;
  submitLabel: string;
}) {
  const id = useId();
  const [overlap, setOverlap] = useState(DEFAULT_OVERLAP);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onRotate(overlap);
      onDone();
    } catch {
      // The caller has already said what went wrong.
      setBusy(false);
    }
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-5">
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">
          The old one keeps working for
        </legend>
        <div role="radiogroup" className="flex flex-col">
          {OVERLAP_CHOICES.map((o) => (
            <label
              key={o.seconds}
              data-hit-area
              className="inline-flex min-h-11 min-w-11 items-center gap-2"
            >
              <input
                type="radio"
                name={`${id}-overlap`}
                className="size-5 shrink-0 accent-primary"
                checked={overlap === o.seconds}
                onChange={() => setOverlap(o.seconds)}
              />
              {o.label}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <DialogClose render={<Button type="button" variant="outline" />}>
          Cancel
        </DialogClose>
        <Button type="submit" disabled={busy}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

/**
 * Asks how long the old secret keeps working, then calls `onRotate`. It closes when `onRotate`
 * resolves; when it rejects, the dialog stays open and the caller reports the error.
 */
export function RotateDialog({
  open,
  onOpenChange,
  name,
  onRotate,
  submitLabel = "Rotate key",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Stays set while the dialog closes, so its title doesn't change mid-animation. */
  name: string;
  onRotate: (overlapSeconds: number) => Promise<void>;
  /** The submit button, such as "Rotate token". */
  submitLabel?: string;
}) {
  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Rotate ${name}`}
      description="You get a new secret. Put it in place before the old one stops working."
    >
      {open && (
        <Form
          onRotate={onRotate}
          onDone={() => onOpenChange(false)}
          submitLabel={submitLabel}
        />
      )}
    </ResponsiveDialog>
  );
}
