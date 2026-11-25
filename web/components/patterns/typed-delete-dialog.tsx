"use client";

import { useId, useState } from "react";

import { ResponsiveDialog } from "@/components/patterns/responsive-dialog";
import { Button } from "@/components/ui/button";
import { DialogClose } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * An alertdialog that deletes once `typedKey` is typed. When `onDelete` rejects it stays open
 * and can be confirmed again; the caller shows the error. Closing it clears what was typed.
 */
export function TypedDeleteDialog({
  open,
  onOpenChange,
  title,
  description,
  typedKey,
  confirmLabel,
  onDelete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  typedKey: string;
  confirmLabel: string;
  onDelete: () => Promise<void>;
}) {
  const id = useId();
  const [typed, setTyped] = useState("");
  const [sending, setSending] = useState(false);

  const setOpen = (next: boolean) => {
    onOpenChange(next);
    if (!next) setTyped("");
  };

  const confirm = async () => {
    setSending(true);
    try {
      await onDelete();
      setOpen(false);
    } catch {
      // The caller has shown why; the dialog stays open for another try.
    } finally {
      setSending(false);
    }
  };

  return (
    <ResponsiveDialog
      alert
      open={open}
      onOpenChange={setOpen}
      title={title}
      description={description}
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button
            variant="destructive"
            disabled={sending || typed !== typedKey}
            onClick={() => void confirm()}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-confirm`}>Type {typedKey} to confirm</Label>
        <Input
          id={`${id}-confirm`}
          value={typed}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setTyped(e.target.value)}
        />
      </div>
    </ResponsiveDialog>
  );
}
